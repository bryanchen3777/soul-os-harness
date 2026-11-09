"""EI-1 驗收追認見證＋雙向變異（工單 EI-1-ACCEPTANCE-RATIFY-1）。

追認日期 2026-11-09，適用 commit f19aac1。來源缺口：無 EI-1 專屬原始工單，
commit 主旨不是驗收授權；scope 成立不等於功能驗收。本檔只補見證，不做設計決策，
不改六模組原碼／既有測試／守衛，不關閉 EI-1。

方法（依工單「做法」照做）：
每一條 AC 都有合法／非法（或應變／不應變）見證；每條各做「放寬而誤接納」
及「收緊而誤拒絕」變異；純性／不可變條款分別注入副作用或可變性、破壞合法
匯入或建構。變異只改隔離 TEMP 副本（tmp_path 自建包，唯一包名），repo 檔全程
唯讀並以模組級 fixture 做前後 SHA 對帳；危險呼叫在匯入前以 AST 硬攔直接
raise，不靠「它應該會失敗」。正常版綠，兩向各被對應斷言殺死。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import sys
import uuid
from pathlib import Path

import pytest

from src.interfaces.classification import classify_messages
from src.interfaces.classification import parse_request
from src.interfaces.config import resolve_route
from src.interfaces.config import validate_registry
from src.interfaces.context_policy import admit
from src.interfaces.context_policy import idempotency_namespace
from src.interfaces.context_policy import request_fingerprint
from src.interfaces.identity import authenticate
from src.interfaces.identity import authorize_route
from src.interfaces.schema import AuthenticatedClient
from src.interfaces.schema import ClientProfile
from src.interfaces.schema import CredentialBinding
from src.interfaces.schema import InterfaceError
from src.interfaces.schema import is_valid_id
from src.interfaces.schema import is_valid_public_model_id

try:
    from tests.interfaces.test_config import OWNER
    from tests.interfaces.test_config import TOKEN_A
    from tests.interfaces.test_config import TOKEN_B
    from tests.interfaces.test_config import TOKEN_U
    from tests.interfaces.test_config import digest_for
    from tests.interfaces.test_config import make_client_dict
    from tests.interfaces.test_config import make_dual_registry
    from tests.interfaces.test_config import make_registry_raw
    from tests.interfaces.test_config import make_route_dict
except ImportError:  # pragma: no cover
    from test_config import OWNER  # type: ignore
    from test_config import TOKEN_A  # type: ignore
    from test_config import TOKEN_B  # type: ignore
    from test_config import TOKEN_U  # type: ignore
    from test_config import digest_for  # type: ignore
    from test_config import make_client_dict  # type: ignore
    from test_config import make_dual_registry  # type: ignore
    from test_config import make_registry_raw  # type: ignore
    from test_config import make_route_dict  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[2]
IFACE_DIR = REPO_ROOT / "src" / "interfaces"
_PIN_FILES = (
    "src/interfaces/__init__.py",
    "src/interfaces/schema.py",
    "src/interfaces/config.py",
    "src/interfaces/identity.py",
    "src/interfaces/classification.py",
    "src/interfaces/context_policy.py",
    "src/interfaces/journal.py",
)
_PKG_MODULES = ("__init__.py", "schema.py", "config.py", "identity.py", "classification.py", "context_policy.py")


def _read(name: str) -> str:
    return (IFACE_DIR / name).read_text(encoding="utf-8")


def _sha(rel: str) -> str:
    return hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()


def _mut_replace(text: str, old: str, new: str, *, label: str) -> str:
    n = text.count(old)
    assert n == 1, "%s: pattern %.60r found %d times, expected 1" % (label, old, n)
    return text.replace(old, new, 1)


_DANGEROUS_IMPORT_TOPS = frozenset({
    "socket", "subprocess", "sqlite3", "os", "sys", "pathlib", "io", "http",
    "urllib", "requests", "ctypes", "threading", "multiprocessing", "shutil",
    "signal", "ssl", "webbrowser",
})
_DANGEROUS_CALLS = frozenset({
    "socket", "Popen", "run", "call", "check_output", "check_call", "connect",
    "create_connection", "system", "popen", "spawnlp", "spawnl", "spawnv",
    "spawnve", "execv", "execve", "fork", "kill", "getenv", "open",
})


def _hard_block_before_import(text: str, label: str) -> None:
    """變異匯入前硬攔：命中危險目標直接 raise（不靠預期失敗保安全）。"""
    tree = ast.parse(text, filename=label)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = (a.name or "").split(".")[0]
                if top in _DANGEROUS_IMPORT_TOPS:
                    raise AssertionError("blocked mutant import: %s (%s)" % (a.name, label))
                if top == "src" and not (a.name == "src.interfaces" or a.name.startswith("src.interfaces.")):
                    raise AssertionError("blocked mutant foreign package: %s (%s)" % (a.name, label))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            mod = node.module or ""
            top = mod.split(".")[0]
            if top in _DANGEROUS_IMPORT_TOPS:
                raise AssertionError("blocked mutant from-import: %s (%s)" % (mod, label))
            if top == "src" and not (mod == "src.interfaces" or mod.startswith("src.interfaces.")):
                raise AssertionError("blocked mutant foreign package: %s (%s)" % (mod, label))
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _DANGEROUS_CALLS:
                raise AssertionError("blocked mutant call: %s (%s)" % (name, label))


def _load_mutant(tmp_path, entry: str, mutated: dict):
    """把六檔拷進 tmp 自建包（mutated 覆寫其中若干），硬攔後匯入指定入口。"""
    pkg = "mutpkg_%s" % uuid.uuid4().hex[:12]
    root = tmp_path / pkg
    root.mkdir()
    for name in _PKG_MODULES:
        text = mutated.get(name, None)
        if text is None:
            text = _read(name)
        else:
            _hard_block_before_import(text, name)
        (root / name).write_text(text, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        mods = {"pkg": importlib.import_module(pkg)}
        for m in ("schema", "config", "identity", "classification", "context_policy"):
            mods[m] = importlib.import_module(pkg + "." + m)
        return mods
    finally:
        try:
            sys.path.remove(str(tmp_path))
        except ValueError:
            pass
        for k in [k for k in sys.modules if k == pkg or k.startswith(pkg + ".")]:
            del sys.modules[k]


def _check_imports_clean(text: str, rel: str) -> list:
    """沿用 boundary 守衛口徑：絕對匯入僅 stdlib 或 src.interfaces.*；相對僅同包。"""
    try:
        stdlib = sys.stdlib_module_names
    except AttributeError:  # pragma: no cover
        stdlib = set()
    rel_allow = ("schema", "config", "identity", "classification", "context_policy", "journal", "runtime_port")
    bad = []
    tree = ast.parse(text, filename=rel)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in stdlib or top == "__future__":
                    continue
                if not (a.name == "src.interfaces" or a.name.startswith("src.interfaces.")):
                    bad.append("bad import " + a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                if not (node.level == 1 and node.module in rel_allow):
                    bad.append("bad relative import in " + rel)
                continue
            mod = node.module or ""
            top = mod.split(".")[0]
            if top in stdlib or top == "__future__":
                continue
            if not (mod == "src.interfaces" or mod.startswith("src.interfaces.")):
                bad.append("bad from-import " + mod)
    for stmt in tree.body:
        if isinstance(stmt, (ast.Expr, ast.Assign, ast.AnnAssign)):
            val = getattr(stmt, "value", None)
            if val is None:
                continue
            for sub in ast.walk(val):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                    if name not in ("dataclass", "field"):
                        bad.append("module-level call " + name)
    return bad


@pytest.fixture(scope="module", autouse=True)
def _repo_files_untouched():
    before = {f: _sha(f) for f in _PIN_FILES}
    yield
    after = {f: _sha(f) for f in _PIN_FILES}
    assert before == after, "ratification tests must not write repo files"


def _route():
    return make_dual_registry().routes[0]


def _req(model="model-a", messages=None, **kw):
    if messages is None:
        messages = [{"role": "user", "content": "hello"}]
    raw = {"model": model, "messages": messages}
    raw.update(kw)
    return raw


def _body(text="hello world"):
    return {"model": "model-a", "messages": [{"role": "user", "content": text}]}


def _full_headers(conv="conv-01", branch="branch-01", turn="turn-01", key="key-01"):
    return {"X-Soul-Conversation-Id": conv, "X-Soul-Branch-Id": branch, "X-Soul-Turn-Id": turn, "Idempotency-Key": key}


def _oracle_rejects_unknown_top_key(parse_fn, raw, route):
    """FUP-2 共享 oracle（C1）：未知頂層鍵必須拒絕（400/unsupported_parameter）。

    同一函式跑正常版（須通過＝綠）與 mutant 版（須拋對應失敗＝紅）。
    """
    with pytest.raises(InterfaceError) as ei:
        parse_fn(raw, route)
    assert (ei.value.status, ei.value.code, ei.value.param) == (400, "unsupported_parameter", "request")


def _oracle_timing_guard(src_text):
    """FUP-2 共享 oracle（I3）：驗收條款是源码性質（恆時比較存在）。

    功能輸出在 plain-eq mutant 下不變，故同一存在性斷言即 oracle：
    正常版通過（綠），mutant 版必須拋 AssertionError（紅）。
    """
    assert "hmac.compare_digest" in src_text, "timing guard missing: hmac.compare_digest"


def _oracle_serialization_stable(src_text):
    """FUP-3 共享 oracle（P6）：驗收條款是源碼性質（序列化穩定存在）。

    sort_keys=False mutant 下同次建構輸出逐位元相同（插入序由程式碼固定），
    故功能差異無法見證，同一存在性斷言即 oracle：
    正常版通過（綠），mutant 版必須拋 AssertionError（紅）。
    """
    assert "sort_keys=True" in src_text, "stable serialization guard missing: sort_keys=True"


def _loosen_kill(label, make_bad, normal_fn, mut_fn, expect):
    """FUP-2 共享三態 oracle（放寬型）：同一拒絕斷言跑正常版（綠）與 mutant（紅）。

    expect＝(status, code)。mutant 誤接納 ⇒ 內層 pytest.raises 拋 DID NOT RAISE（紅）。
    """
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        normal_fn(make_bad())
    assert (ei.value.status, ei.value.code) == expect, label
    with pytest.raises(BaseException) as kill:  # 同一斷言跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mut_fn(make_bad())
    assert "DID NOT RAISE" in str(kill.value), label  # 誤接納見證


# ── C1 ──────────────────────────────────────────────────────────────────


def test_c1_allowlist():
    parse_request(_req(), _route())
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(tools={}), _route())
    assert (ei.value.status, ei.value.code, ei.value.param) == (400, "unsupported_parameter", "request")
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "hi", "tool_calls": []}]), _route())
    assert (ei.value.status, ei.value.code, ei.value.param) == (400, "unsupported_parameter", "messages")
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "tool", "content": "x"}, {"role": "user", "content": "hi"}]), _route())
    assert (ei.value.status, ei.value.code) == (400, "unsupported_role")


def test_c1_mut_loosen_top_key(tmp_path):
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納，未證同一斷言遇變異必紅）。
    base = _read("classification.py")
    mut = _mut_replace(base, "if k not in _TOP:", "if False:  # MUTANT-C1-LOOSEN", label="C1-loosen")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    raw = _req(tools={})
    _oracle_rejects_unknown_top_key(parse_request, raw, _route())  # 正常綠
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須失敗＝紅
        _oracle_rejects_unknown_top_key(mods["classification"].parse_request, raw, _route())
    assert "DID NOT RAISE" in str(kill.value)  # mutant 誤接納 ⇒ oracle 的 pytest.raises 轉紅


def test_c1_mut_tighten_roles(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(
        base,
        '_ROLES = ("system", "developer", "user", "assistant")',
        '_ROLES = ("system", "developer", "assistant")  # MUTANT-C1-TIGHTEN',
        label="C1-tighten",
    )
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    try:
        mods["classification"].parse_request(_req(), _route())
    except Exception as e:
        assert getattr(e, "status", None) == 400  # 合法 user 被誤拒絕 ⇒ 殺死
    else:
        raise AssertionError("tightened roles should reject legal user message")
    parse_request(_req(), _route())  # 正常版接納（對照）


# ── C2 ──────────────────────────────────────────────────────────────────


def test_c2_last_user():
    parse_request(_req(), _route())
    req = parse_request(_req(messages=[{"role": "user", "content": ""}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "go"}]), _route())
    assert req.messages[-1].content == "go"
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]), _route())
    assert ei.value.code == "missing_current_user"
    with pytest.raises(InterfaceError):
        parse_request(_req(messages=[{"role": "user", "content": "   "}]), _route())


def test_c2_mut_loosen_last(tmp_path):
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    base = _read("classification.py")
    mut = _mut_replace(base, 'if out[-1].role != "user":', 'if False:  # MUTANT-C2-LOOSEN', label="C2-loosen")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    _loosen_kill(
        "C2-loosen",
        lambda: _req(messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]),
        lambda raw: parse_request(raw, _route()),
        lambda raw: mods["classification"].parse_request(raw, _route()),
        (400, "missing_current_user"),
    )


def test_c2_mut_tighten_last(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(base, 'if out[-1].role != "user":', 'if out[-1].role == "user":  # MUTANT-C2-TIGHTEN', label="C2-tighten")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    parse_request(_req(), _route())  # 正常綠（同一 oracle：合法 user 結尾通過）
    try:
        mods["classification"].parse_request(_req(), _route())
    except Exception as e:
        assert getattr(e, "code", None) == "missing_current_user"  # 合法 user 結尾被誤拒絕 ⇒ 殺死
    else:
        raise AssertionError("tightened last-user check should reject legal request")


# ── C3 ──────────────────────────────────────────────────────────────────


def test_c3_model_route():
    parse_request(_req(model="model-a"), _route())
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(model="other-model"), _route())
    assert (ei.value.status, ei.value.code) == (400, "model_mismatch")
    with pytest.raises(InterfaceError):
        parse_request(_req(model="a/b"), _route())


def test_c3_mut_loosen_mismatch(tmp_path):
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    base = _read("classification.py")
    mut = _mut_replace(base, "if model != route.public_model_id:", "if False:  # MUTANT-C3-LOOSEN", label="C3-loosen")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    _loosen_kill(
        "C3-loosen",
        lambda: _req(model="other-model"),
        lambda raw: parse_request(raw, _route()),
        lambda raw: mods["classification"].parse_request(raw, _route()),
        (400, "model_mismatch"),
    )


def test_c3_mut_tighten_mismatch(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(base, "if model != route.public_model_id:", "if True:  # MUTANT-C3-TIGHTEN", label="C3-tighten")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    parse_request(_req(model="model-a"), _route())  # 正常綠（同一 oracle：對的 model 通過）
    try:
        mods["classification"].parse_request(_req(model="model-a"), _route())
    except Exception as e:
        assert getattr(e, "code", None) == "model_mismatch"  # 對的 model 也被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened model check should reject legal model")


# ── C4 ──────────────────────────────────────────────────────────────────


def test_c4_quotas():
    parse_request(_req(), _route())
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "x"} for _ in range(129)]), _route())
    assert ei.value.status == 413
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "x" * 32769}]), _route())
    assert (ei.value.status, ei.value.param) == (413, "content")
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "x" * 30000} for _ in range(5)]), _route())
    assert (ei.value.status, ei.value.param) == (413, "messages")
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "x" * 2050} for _ in range(128)]), _route())
    assert (ei.value.status, ei.value.param) == (413, None)


def test_c4_mut_loosen_all_thresholds(tmp_path):
    base = _read("classification.py")
    mut = base
    for old, new in [
        ("if len(msgs) > 128:", "if len(msgs) > 1000000:  # MUTANT-C4-LOOSEN"),
        ("if nb > 32768:", "if nb > 100000000:  # MUTANT-C4-LOOSEN"),
        ("if total > 131072:", "if total > 1000000000:  # MUTANT-C4-LOOSEN"),
        ("if len(canon) > 262144:", "if len(canon) > 1000000000:  # MUTANT-C4-LOOSEN"),
    ]:
        mut = _mut_replace(mut, old, new, label="C4-loosen")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    p = mods["classification"].parse_request
    # FUP-2 重做：同一 oracle（413 拒絕）逐組跑三態（修正前：只證 mutant 被接納）。
    bads = [
        (_req(messages=[{"role": "user", "content": "x"} for _ in range(129)]), (413, "request_too_large")),
        (_req(messages=[{"role": "user", "content": "x" * 32769}]), (413, "request_too_large")),
        (_req(messages=[{"role": "user", "content": "x" * 30000} for _ in range(5)]), (413, "request_too_large")),
        (_req(messages=[{"role": "user", "content": "x" * 2050} for _ in range(128)]), (413, "request_too_large")),
    ]
    for bad, expect in bads:
        with pytest.raises(InterfaceError) as ei:  # 正常綠
            parse_request(bad, _route())
        assert (ei.value.status, ei.value.code) == expect
    for bad, _ in bads:
        p(bad, _route())  # 四類超限全部被誤接納 ⇒ 同一 oracle 跑 mutant 紅（DID NOT RAISE）
    with pytest.raises(BaseException) as kill:
        with pytest.raises(InterfaceError):
            p(_req(messages=[{"role": "user", "content": "x"} for _ in range(129)]), _route())
    assert "DID NOT RAISE" in str(kill.value)
    # 四類超限全部被誤接納 ⇒ 殺死（正常版四類皆 413，見上及 test_c4_quotas）


def test_c4_mut_tighten_count(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(base, "if len(msgs) > 128:", "if len(msgs) > 0:  # MUTANT-C4-TIGHTEN", label="C4-tighten")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    parse_request(_req(), _route())  # 正常綠（同一 oracle：非空合法請求通過）
    try:
        mods["classification"].parse_request(_req(), _route())
    except Exception as e:
        assert getattr(e, "status", None) == 413  # 任何非空請求被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened count quota should reject legal request")


# ── C5 ──────────────────────────────────────────────────────────────────


def test_c5_surrogate():
    parse_request(_req(messages=[{"role": "user", "content": "hello 中文"}]), _route())
    for bad in ["a\ud800b"]:
        with pytest.raises(InterfaceError):
            parse_request(_req(messages=[{"role": "user", "content": bad}]), _route())
        with pytest.raises(InterfaceError):
            parse_request(_req(messages=[{"role": "user", "content": "hi", "name": bad}]), _route())


def test_c5_mut_loosen_scan(tmp_path):
    # 單點放寬殺不死：raw 掃描／Content 檢查／name 檢查／json ensure_ascii／utf-8 encode 五層縱深。
    # 本變異一併移除整組 surrogate 防禦（放寬），證明整組守衛有牙。
    base = _read("classification.py")
    mut = base
    for old, new in [
        ("if _scan_surrogate(raw):", "if False:  # MUTANT-C5-LOOSEN"),
        ("if contains_surrogate(content):", "if False:  # MUTANT-C5-LOOSEN"),
        ("if len(name) > 64 or contains_surrogate(name):", "if len(name) > 64:  # MUTANT-C5-LOOSEN"),
        ("ensure_ascii=False", "ensure_ascii=True"),
        ('nb = len(content.encode("utf-8"))', 'nb = len(content.encode("utf-8", "surrogatepass"))'),
    ]:
        mut = _mut_replace(mut, old, new, label="C5-loosen")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    # FUP-2 重做：同一 oracle（髒輸入→400）跑三態（修正前：只證 mutant 被接納）。
    _loosen_kill(
        "C5-loosen",
        lambda: _req(messages=[{"role": "user", "content": "a\ud800b"}]),
        lambda raw: parse_request(raw, _route()),
        lambda raw: mods["classification"].parse_request(raw, _route()),
        (400, "invalid_request"),
    )


def test_c5_mut_tighten_content(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(base, "if contains_surrogate(content):", "if True:  # MUTANT-C5-TIGHTEN", label="C5-tighten")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    parse_request(_req(), _route())  # 正常綠（同一 oracle：乾淨輸入通過）
    try:
        mods["classification"].parse_request(_req(), _route())
    except Exception as e:
        assert getattr(e, "status", None) == 400  # 乾淨輸入被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened surrogate check should reject clean input")


# ── C6 ──────────────────────────────────────────────────────────────────


def test_c6_params():
    ok = parse_request(_req(temperature=1), _route())
    assert ok.temperature == 1.0
    parse_request(_req(stream=False), _route())
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(stream=True), _route())
    assert (ei.value.status, ei.value.code) == (400, "unsupported_feature")
    for bad in [1, 0, "false", None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(stream=bad), _route())
    for bad in [True, False, float("nan"), float("inf"), -0.5, 2.5, "0.5", None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(temperature=bad), _route())
    for bad in [True, False, 0, 2049, 1.5, "512", None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(max_tokens=bad), _route())
    for bad in [2, True, False, 0, "1", 1.0, None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(n=bad), _route())


def test_c6_mut_loosen_stream_and_booltemp(tmp_path):
    base = _read("classification.py")
    m1 = _mut_replace(base, "if st is True:", "if False:  # MUTANT-C6-LOOSEN", label="C6-loosen-stream")
    mods1 = _load_mutant(tmp_path, "classification", {"classification.py": m1})
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    _loosen_kill(
        "C6-loosen-stream",
        lambda: _req(stream=True),
        lambda raw: parse_request(raw, _route()),
        lambda raw: mods1["classification"].parse_request(raw, _route()),
        (400, "unsupported_feature"),
    )
    m2 = _mut_replace(
        base,
        "if type(t) is bool or not isinstance(t, (int, float)):",
        "if not isinstance(t, (int, float)):  # MUTANT-C6-LOOSEN",
        label="C6-loosen-booltemp",
    )
    mods2 = _load_mutant(tmp_path, "classification", {"classification.py": m2})
    # FUP-2 重做：同一 oracle（bool 冒充→400）跑三態（修正前：只斷言 mutant 輸出值）。
    _loosen_kill(
        "C6-loosen-booltemp",
        lambda: _req(temperature=True),
        lambda raw: parse_request(raw, _route()),
        lambda raw: mods2["classification"].parse_request(raw, _route()),
        (400, "invalid_request"),
    )


def test_c6_mut_tighten_ranges(tmp_path):
    base = _read("classification.py")
    # 正常綠（同一 oracle：合法參數各自通過）
    parse_request(_req(temperature=1.5), _route())
    parse_request(_req(max_tokens=512), _route())
    parse_request(_req(n=1), _route())
    for old, new, kw in [
        ("or float(t) < 0 or float(t) > 2:", "or float(t) < 0 or float(t) > 1:  # MUTANT-C6-TIGHTEN", {"temperature": 1.5}),
        ("or mt < 1 or mt > 2048:", "or mt < 1 or mt > 100:  # MUTANT-C6-TIGHTEN", {"max_tokens": 512}),
        ("if type(n) is not int or n != 1:", "if type(n) is not int or n != 2:  # MUTANT-C6-TIGHTEN", {"n": 1}),
    ]:
        mut = _mut_replace(base, old, new, label="C6-tighten")
        mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
        try:
            mods["classification"].parse_request(_req(**kw), _route())
        except Exception as e:
            assert getattr(e, "status", None) == 400  # 合法參數被誤拒絕 ⇒ 殺死
        else:
            raise AssertionError("tightened range should reject legal param %r" % (kw,))


# ── C7 ──────────────────────────────────────────────────────────────────


def test_c7_sources():
    from src.interfaces.schema import Source

    req = parse_request(
        _req(messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "now"},
        ]),
        _route(),
    )
    segs = classify_messages(req)
    assert [s.source for s in segs] == [
        Source.EXTERNAL_INSTRUCTION,
        Source.EXTERNAL_HISTORY,
        Source.EXTERNAL_HISTORY,
        Source.CURRENT_USER,
    ]
    assert all(s.memory_candidate is False for s in segs)
    req2 = parse_request(_req(messages=[{"role": "developer", "content": "d"}, {"role": "user", "content": "now"}]), _route())
    assert classify_messages(req2)[0].source == Source.EXTERNAL_INSTRUCTION
    with pytest.raises(InterfaceError) as ei:
        classify_messages(_req_model_only())
    assert ei.value.code == "missing_current_user"


def _req_model_only():
    from src.interfaces.schema import Message
    from src.interfaces.schema import RequestInput

    return RequestInput(model="model-a", messages=())


def test_c7_mut_loosen_last_source(tmp_path):
    from src.interfaces.schema import Source as RealSource

    base = _read("classification.py")
    mut = _mut_replace(base, "if i == last:", "if False:  # MUTANT-C7-LOOSEN", label="C7-loosen")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    req = parse_request(_req(), _route())

    def _oracle_last_is_current_user(segs):
        assert segs[-1].source.value == RealSource.CURRENT_USER.value

    _oracle_last_is_current_user(classify_messages(req))  # 正常綠（同一 oracle）
    msegs = mods["classification"].classify_messages(req)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_last_is_current_user(msegs)
    assert msegs[-1].source.value == "external_history"  # 失敗原因（診斷，非殺死證據）：末則掉進 HISTORY


def test_c7_mut_tighten_history_source(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(
        base,
        'elif m.role in ("system", "developer"):',
        "elif True:  # MUTANT-C7-TIGHTEN",
        label="C7-tighten",
    )
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    req = parse_request(
        _req(messages=[{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "now"}]),
        _route(),
    )

    def _oracle_history_stays_history(segs):
        assert segs[1].source.value == "external_history"

    _oracle_history_stays_history(classify_messages(req))  # 正常綠（同一 oracle）
    msegs = mods["classification"].classify_messages(req)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_history_stays_history(msegs)
    assert msegs[1].source.value == "external_instruction"  # 失敗原因（診斷，非殺死證據）：歷史被升格


# ── G1 ──────────────────────────────────────────────────────────────────


def test_g1_strict_fields():
    reg = make_dual_registry()
    assert len(reg.routes) == 1 and len(reg.clients) == 2
    raw = make_registry_raw()
    raw["extra"] = 1
    with pytest.raises(InterfaceError) as ei:
        validate_registry(raw)
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")
    for mutate in [
        lambda r: r.pop("backends"),
        lambda r: r["routes"][0].update({"endpoint": "https://x"}),
        lambda r: r["clients"][0].update({"raw_token": "x"}),
    ]:
        raw = make_registry_raw()
        mutate(raw)
        with pytest.raises(InterfaceError) as ei:
            validate_registry(raw)
        assert (ei.value.status, ei.value.code) == (500, "invalid_registry")


def test_g1_mut_loosen_top_extra(tmp_path):
    # 單點放寬殺不死：len(keys)!=4 與未知鍵檢查雙層。變異 coherently 放寬兩層。
    base = _read("config.py")
    m1 = _mut_replace(base, "if len(keys) != 4:", "if len(keys) < 4:  # MUTANT-G1-LOOSEN", label="G1-loosen-count")
    mut = _mut_replace(m1, "if k not in _TOP_KEYS:", "if False:  # MUTANT-G1-LOOSEN", label="G1-loosen-unknown")
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    raw = make_registry_raw()
    raw["extra"] = 1
    # FUP-2 重做：同一 oracle（未知頂層鍵→500/invalid_registry）跑三態（修正前：只證 mutant 被接納）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        validate_registry(dict(raw))
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods["config"].validate_registry(dict(raw))
    assert "DID NOT RAISE" in str(kill.value)  # 未知頂層鍵被誤接納 ⇒ 殺死


def test_g1_mut_tighten_count(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(base, "if len(keys) != 4:", "if len(keys) != 5:  # MUTANT-G1-TIGHTEN", label="G1-tighten")
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    validate_registry(make_registry_raw())  # 正常綠（同一 oracle：合法 4 鍵通過）
    try:
        mods["config"].validate_registry(make_registry_raw())
    except Exception as e:
        assert getattr(e, "code", None) == "invalid_registry"  # 合法 4 鍵被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened key count should reject legal registry")


# ── G2 ──────────────────────────────────────────────────────────────────


def test_g2_ids_refs_uniqueness():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A)])
    raw["clients"][0]["credential_sha256"] = digest_for(TOKEN_A).upper()
    reg = validate_registry(raw)
    assert reg.credentials[0].digest == digest_for(TOKEN_A)
    raw = make_registry_raw()
    raw["clients"][0]["credential_sha256"] = "g" * 64
    with pytest.raises(InterfaceError) as ei:
        validate_registry(raw)
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")
    raw = make_registry_raw(routes=[make_route_dict(), make_route_dict()])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw = make_registry_raw(backends=["port-a", "port-a"])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw = make_registry_raw()
    raw["routes"][0]["backend_ref"] = "port-missing"
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw = make_registry_raw()
    raw["routes"][0]["context_window_tokens"] = True
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw = make_registry_raw()
    raw["clients"][0]["allowed_routes"] = ["soul-a/backend-a", "soul-a/backend-a"]
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_g2_mut_loosen_digest_and_dupes(tmp_path):
    base = _read("config.py")
    m1 = _mut_replace(base, "if dig is None:", "if False:  # MUTANT-G2-LOOSEN", label="G2-loosen-digest")
    mods1 = _load_mutant(tmp_path, "config", {"config.py": m1})
    raw = make_registry_raw()
    raw["clients"][0]["credential_sha256"] = "g" * 64
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        validate_registry(raw)
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods1["config"].validate_registry(raw)
    assert "DID NOT RAISE" in str(kill.value)  # 壞 digest 被誤接納 ⇒ 殺死
    m2 = _mut_replace(base, "if b in seen_be:", "if False:  # MUTANT-G2-LOOSEN", label="G2-loosen-dupe")
    mods2 = _load_mutant(tmp_path, "config", {"config.py": m2})
    dupes = make_registry_raw(backends=["port-a", "port-a"])
    with pytest.raises(InterfaceError):  # 正常綠
        validate_registry(dupes)
    with pytest.raises(BaseException) as kill2:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods2["config"].validate_registry(dupes)
    assert "DID NOT RAISE" in str(kill2.value)  # 重複 backend 被誤接納 ⇒ 殺死


def test_g2_mut_tighten_digest_len(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(base, "if len(v) != 64:", "if len(v) != 65:  # MUTANT-G2-TIGHTEN", label="G2-tighten")
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    validate_registry(make_registry_raw())  # 正常綠（同一 oracle：合法 digest 通過）
    try:
        mods["config"].validate_registry(make_registry_raw())
    except Exception as e:
        assert getattr(e, "code", None) == "invalid_registry"  # 合法 digest 被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened digest length should reject legal registry")


# ── G3 ──────────────────────────────────────────────────────────────────


def test_g3_principal_owner():
    make_dual_registry()
    raw = make_registry_raw()
    raw["clients"][0]["principal_id"] = "owner-02"
    with pytest.raises(InterfaceError) as ei:
        validate_registry(raw)
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")


def test_g3_mut_loosen_principal(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(base, "if pid != owner:", "if False:  # MUTANT-G3-LOOSEN", label="G3-loosen")
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    raw = make_registry_raw()
    raw["clients"][0]["principal_id"] = "owner-02"
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        validate_registry(raw)
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods["config"].validate_registry(raw)
    assert "DID NOT RAISE" in str(kill.value)  # 外來 principal 被誤接納 ⇒ 殺死


def test_g3_mut_tighten_principal(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(base, "if pid != owner:", "if True:  # MUTANT-G3-TIGHTEN", label="G3-tighten")
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    validate_registry(make_registry_raw())  # 正常綠（同一 oracle：合法 owner 通過）
    try:
        mods["config"].validate_registry(make_registry_raw())
    except Exception as e:
        assert getattr(e, "code", None) == "invalid_registry"  # 合法 owner 被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened principal check should reject legal registry")


# ── G4 ──────────────────────────────────────────────────────────────────


def test_g4_purpose_scope():
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility")])
    reg = validate_registry(raw)
    assert reg.clients[0].purpose.value == "utility" and reg.clients[0].scope.value == "none"
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility", scope="owner_private")])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility", allow_mem=True)])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A, purpose="interactive", scope="none")])
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_g4_mut_loosen_utility(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(
        base,
        "if scope != Scope.NONE or apm is not False:",
        "if False:  # MUTANT-G4-LOOSEN",
        label="G4-loosen",
    )
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility", allow_mem=True)])
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    with pytest.raises(InterfaceError):  # 正常綠
        validate_registry(raw)
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods["config"].validate_registry(raw)
    assert "DID NOT RAISE" in str(kill.value)  # utility＋持久記憶被誤接納 ⇒ 殺死


def test_g4_mut_tighten_interactive(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(
        base,
        "if scope != Scope.OWNER_PRIVATE:",
        "if True:  # MUTANT-G4-TIGHTEN",
        label="G4-tighten",
    )
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    validate_registry(make_registry_raw())  # 正常綠（同一 oracle：合法 interactive 通過）
    try:
        mods["config"].validate_registry(make_registry_raw())
    except Exception as e:
        assert getattr(e, "code", None) == "invalid_registry"  # 合法 interactive 被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened scope check should reject legal registry")


# ── G5 ──────────────────────────────────────────────────────────────────


def test_g5_exact_route():
    reg = make_dual_registry()
    r = resolve_route(reg, "soul-a", "backend-a")
    assert r.agent_id == "agent-01"
    with pytest.raises(InterfaceError) as ei:
        resolve_route(reg, "soul-a", "missing")
    assert ei.value.status == 404 and ei.value.code == "route_not_found"
    assert "agent-01" not in str(ei.value) and "missing" not in str(ei.value)


def test_g5_mut_loosen_404(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(
        base,
        'raise InterfaceError(404, "route_not_found", "route", "Unknown route.")',
        "return registry.routes[0]  # MUTANT-G5-LOOSEN",
        label="G5-loosen",
    )
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    reg = make_dual_registry()
    # FUP-3 重做：與 C2/C3/C5/C6 同一驗收 callable（修正前：只斷言 mutant 回傳值＝證變異生效）。
    _loosen_kill(
        "G5-loosen",
        lambda: ("soul-a", "missing"),
        lambda pair: resolve_route(reg, *pair),
        lambda pair: mods["config"].resolve_route(reg, *pair),
        (404, "route_not_found"),
    )
    hit = mods["config"].resolve_route(reg, "soul-a", "missing")
    assert hit.soul_alias == "soul-a"  # 失敗原因（診斷，非殺死證據）：未知路由被綁到首 route


def test_g5_mut_tighten_404(tmp_path):
    base = _read("config.py")
    mut = _mut_replace(
        base,
        "if r.soul_alias == soul_alias and r.backend_alias == backend_alias:",
        "if False:  # MUTANT-G5-TIGHTEN",
        label="G5-tighten",
    )
    mods = _load_mutant(tmp_path, "config", {"config.py": mut})
    reg = make_dual_registry()
    resolve_route(reg, "soul-a", "backend-a")  # 正常綠（同一 oracle：合法路由命中）
    try:
        mods["config"].resolve_route(reg, "soul-a", "backend-a")
    except Exception as e:
        assert getattr(e, "status", None) == 404  # 合法路由也 404 ⇒ 殺死
    else:
        raise AssertionError("tightened matcher should 404 the legal route")


def test_g6_status_500():
    with pytest.raises(InterfaceError) as ei:
        validate_registry("not-a-mapping")
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")
    with pytest.raises(InterfaceError) as ei:
        validate_registry(make_registry_raw(backends="port-a"))
    assert (ei.value.status, ei.value.code) == (500, "invalid_registry")


# ── I1 ──────────────────────────────────────────────────────────────────


def test_i1_authenticate_ok():
    reg = make_dual_registry()
    c = authenticate(TOKEN_A, reg)
    assert isinstance(c, AuthenticatedClient)
    assert c.profile.client_id == "client-a" and c.profile.principal_id == "owner-01"


def test_i1_mut_loosen_always_true(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(
        base,
        "if hmac.compare_digest(cred.digest, digest):",
        "if True:  # MUTANT-I1-LOOSEN",
        label="I1-loosen",
    )
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    # FUP-2 重做：同一 oracle（錯 token→401）跑三態（修正前：只證 mutant 誤認證）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        authenticate("wrong-secret-value", reg)
    assert ei.value.status == 401
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods["identity"].authenticate("wrong-secret-value", reg)
    assert "DID NOT RAISE" in str(kill.value)
    c = mods["identity"].authenticate("wrong-secret-value", reg)
    assert c.profile.client_id == "client-a"  # 失敗原因（診斷，非殺死證據）：任意 token 被誤認證為首客戶


def test_i1_mut_tighten_always_false(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(
        base,
        "if hmac.compare_digest(cred.digest, digest):",
        "if False:  # MUTANT-I1-TIGHTEN",
        label="I1-tighten",
    )
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    authenticate(TOKEN_A, reg)  # 正常綠（同一 oracle：正確 token 通過）
    try:
        mods["identity"].authenticate(TOKEN_A, reg)
    except Exception as e:
        assert getattr(e, "status", None) == 401  # 正確 token 被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened compare should reject the valid token")


# ── I2 ──────────────────────────────────────────────────────────────────


def test_i2_reject_401():
    reg = make_dual_registry()
    for bad in ["", "   ", "\t\n ", "x" * 4097, "wrong-secret-value"]:
        with pytest.raises(InterfaceError) as ei:
            authenticate(bad, reg)
        assert ei.value.status == 401
    for bad in [None, 123, b"bytes-token", ["x"]]:
        with pytest.raises(InterfaceError) as ei:
            authenticate(bad, reg)
        assert ei.value.status == 401


def test_i2_mut_loosen_typecheck(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(
        base,
        "if not isinstance(bearer_token, str):",
        "if False:  # MUTANT-I2-LOOSEN",
        label="I2-loosen",
    )
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    # FUP-2 重做：同一 oracle（非字串→401）跑三態（修正前：只證 mutant 爆 TypeError）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        authenticate(None, reg)
    assert ei.value.status == 401
    try:
        mods["identity"].authenticate(None, reg)
    except InterfaceError as e:
        raise AssertionError("mutant survived: None still yields 401 (%r)" % (e,))
    except BaseException as kill:  # 同一 oracle 跑 mutant 紅：TypeError 而非 401 ⇒ 破線見證
        assert isinstance(kill, TypeError), kill
    else:
        raise AssertionError("mutant accepted None without any error")


def test_i2_mut_tighten_length(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(
        base,
        "if len(bearer_token) > 4096:",
        "if len(bearer_token) > 0:  # MUTANT-I2-TIGHTEN",
        label="I2-tighten",
    )
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    authenticate(TOKEN_A, reg)  # 正常綠（同一 oracle：合法 token 通過）
    try:
        mods["identity"].authenticate(TOKEN_A, reg)
    except Exception as e:
        assert getattr(e, "status", None) == 401  # 合法 token 被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened length check should reject the valid token")


# ── I3 ──────────────────────────────────────────────────────────────────


def test_i3_no_echo_and_timing():
    src = _read("identity.py")
    assert "hmac.compare_digest" in src
    assert "hashlib.sha256" in src
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        authenticate("wrong-secret-value", reg)
    assert ei.value.status == 401
    assert "wrong-secret-value" not in str(ei.value.message)
    assert "wrong-secret-value" not in repr(ei.value)
    c = authenticate(TOKEN_B, reg)
    assert TOKEN_B not in repr(c) + repr(c.profile)


def test_i3_mut_loosen_plain_eq(tmp_path):
    base = _read("identity.py")
    assert "hmac.compare_digest(cred.digest, digest)" in base
    mut = _mut_replace(
        base,
        "if hmac.compare_digest(cred.digest, digest):",
        "if cred.digest == digest:  # MUTANT-I3-LOOSEN",
        label="I3-loosen",
    )
    # FUP-2 重做：同一 oracle（存在性斷言）跑三態（修正前：只斷言 mutant 缺比較＝證變異生效）。
    _oracle_timing_guard(base)  # 正常綠
    with pytest.raises(AssertionError) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        _oracle_timing_guard(mut)
    assert "hmac.compare_digest" in str(kill.value)
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    mods["identity"].authenticate(TOKEN_A, reg)
    try:
        mods["identity"].authenticate("wrong-secret-value", reg)
    except Exception as e:
        assert getattr(e, "status", None) == 401
    else:
        raise AssertionError("plain-eq mutant should still reject wrong token")
    # 功能輸出不變、但恆時性遺失——三態已由同一存在性 oracle 見證（正常綠／mutant 紅上）。
    # （FUP-2 註：修正前此行只寫「AST 釘選證明測試有牙」，屬證變異生效；現改為共享 oracle。）


def test_i3_mut_tighten_break_hash(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(
        base,
        "digest = hashlib.sha256(",
        "digest = hashlib.sha256_missing(",
        label="I3-tighten",
    )
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    authenticate(TOKEN_A, reg)  # 正常綠（同一 oracle：合法認證通過）
    try:
        mods["identity"].authenticate(TOKEN_A, reg)  # 合法認證被破壞（401） ⇒ 殺死
    except Exception as e:
        assert getattr(e, "status", None) == 401
    else:
        raise AssertionError("broken-hash mutant should reject legal token")


# ── I4 ──────────────────────────────────────────────────────────────────


def test_i4_authorize_exact():
    reg = make_dual_registry()
    ca = authenticate(TOKEN_A, reg)
    authorize_route(ca, reg.routes[0])
    raw = make_registry_raw(
        routes=[
            {"soul_alias": "soul-a", "backend_alias": "backend-a", "agent_id": "agent-01",
             "public_model_id": "model-a", "backend_ref": "port-a", "context_window_tokens": 100},
            {"soul_alias": "soul-a", "backend_alias": "backend-b", "agent_id": "agent-02",
             "public_model_id": "model-b", "backend_ref": "port-a", "context_window_tokens": 100},
        ],
        clients=[make_client_dict("client-a", TOKEN_A, allowed_routes=["soul-a/backend-a"])],
    )
    reg2 = validate_registry(raw)
    ca2 = authenticate(TOKEN_A, reg2)
    other = [r for r in reg2.routes if r.backend_alias == "backend-b"][0]
    with pytest.raises(InterfaceError) as ei:
        authorize_route(ca2, other)
    assert ei.value.status == 403


def test_i4_mut_loosen_always_allow(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(base, "if e == want:", "if True:  # MUTANT-I4-LOOSEN", label="I4-loosen")
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    ca = authenticate(TOKEN_A, reg)
    from src.interfaces.schema import RouteBinding

    evil = RouteBinding(
        soul_alias="soul-a", backend_alias="backend-evil", agent_id="agent-01",
        public_model_id="model-a", backend_ref="port-a", context_window_tokens=100,
    )
    with pytest.raises(InterfaceError) as ei:  # 正常綠（同一 oracle：未授權路由→403）
        authorize_route(ca, evil)
    assert ei.value.status == 403
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods["identity"].authorize_route(ca, evil)
    assert "DID NOT RAISE" in str(kill.value)  # 未授權路由被誤放行 ⇒ 殺死


def test_i4_mut_tighten_never_allow(tmp_path):
    base = _read("identity.py")
    mut = _mut_replace(base, "if e == want:", "if False:  # MUTANT-I4-TIGHTEN", label="I4-tighten")
    mods = _load_mutant(tmp_path, "identity", {"identity.py": mut})
    reg = make_dual_registry()
    ca = authenticate(TOKEN_A, reg)
    authorize_route(ca, reg.routes[0])  # 正常綠（同一 oracle：已授權路由放行）
    try:
        mods["identity"].authorize_route(ca, reg.routes[0])
    except Exception as e:
        assert getattr(e, "status", None) == 403  # 已授權路由被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened authorize should forbid the legal route")


# ── P1 ──────────────────────────────────────────────────────────────────


def test_p1_auth_first():
    reg = make_dual_registry()
    admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-ok")
    for args in [
        ("no-soul", "no-backend", _body(), {}, "req-ok"),
        ("soul-a", "backend-a", {"model": "model-a", "messages": "bad"}, {}, "req-ok"),
        ("soul-a", "backend-a", _body(), {"X-Soul-Bad": "1"}, "req-ok"),
        ("soul-a", "backend-a", _body(), {}, ""),
    ]:
        with pytest.raises(InterfaceError) as ei:
            admit(reg, "bad-key-nope", *args)
        assert ei.value.status == 401


def test_p1_mut_loosen_auth_deferred(tmp_path):
    base = _read("context_policy.py")
    step1 = _mut_replace(
        base,
        "    client = authenticate(bearer_token, registry)",
        "    client = None  # MUTANT-P1-LOOSEN",
        label="P1-loosen-1",
    )
    step2 = _mut_replace(
        step1,
        "    route = resolve_route(registry, soul_alias, backend_alias)\n    authorize_route(client, route)\n    req = parse_request(raw_request, route)",
        "    route = resolve_route(registry, soul_alias, backend_alias)\n    authorize_route(client, route)\n"
        "    client = authenticate(bearer_token, registry)  # MUTANT-P1-LOOSEN\n    req = parse_request(raw_request, route)",
        label="P1-loosen-2",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": step2})
    reg = make_dual_registry()
    # FUP-2 重做：同一 oracle（壞 token→401）跑三態（修正前：只斷言 mutant 吐 404）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        admit(reg, "bad-key-nope", "no-soul", "no-backend", _body(), {}, "req-ok")
    assert ei.value.status == 401
    try:
        mods["context_policy"].admit(reg, "bad-key-nope", "no-soul", "no-backend", _body(), {}, "req-ok")
    except BaseException as e:
        # mutant 住在隔離包，其 InterfaceError 與正常版不同一類別：以 status 判定。
        assert getattr(e, "status", None) == 404, repr(e)  # 同一 oracle 跑 mutant 紅：認證順序破線（404 而非 401）⇒ 殺死
    else:
        raise AssertionError("deferred auth should not return 401 for unknown route")


def test_p1_mut_tighten_branch_required(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(base, '    branch = seen.get(_B, "root")', "    branch = seen.get(_B)  # MUTANT-P1-TIGHTEN", label="P1-tighten")
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    h = {"X-Soul-Conversation-Id": "c-01", "X-Soul-Turn-Id": "t-01", "Idempotency-Key": "k-01"}
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-b")
    assert d.context.branch_id == "root"  # 正常綠（同一 oracle：省略 branch 預設 root）
    try:
        mods["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-b")
    except Exception as e:
        assert getattr(e, "status", None) == 400  # 合法省略 branch 被拒 ⇒ 殺死
    else:
        raise AssertionError("required branch should reject the legal default-root request")


# ── P2 ──────────────────────────────────────────────────────────────────


def test_p2_headers():
    reg = make_dual_registry()
    h = {"x-soul-conversation-id": "c-01", "X-SOUL-BRANCH-ID": "b-01", "x-soul-turn-id": "t-01",
         "IDEMPOTENCY-KEY": "k-01", "X-Trace": "yes", "Content-Type": "application/json"}
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-ci")
    assert d.context.conversation_id == "c-01"
    d2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(),
               {"X-Soul-Conversation-Id": "c-01", "X-Soul-Turn-Id": "t-01", "Idempotency-Key": "k-01"}, "req-b")
    assert d2.context.branch_id == "root"
    with pytest.raises(InterfaceError) as ei:
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Turn-Id": "a", "x-soul-turn-id": "b"}, "req-d")
    assert ei.value.status == 400
    with pytest.raises(InterfaceError) as ei:
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Converstion-Id": "c"}, "req-u")
    assert (ei.value.status, ei.value.code, ei.value.param) == (400, "unsupported_parameter", "headers")
    for bad in ["a/b", ".", "..", "", "x" * 129]:
        with pytest.raises(InterfaceError):
            admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Conversation-Id": bad}, "req-x")


def test_p2_mut_loosen_dup_and_unknown(tmp_path):
    base = _read("context_policy.py")
    m1 = _mut_replace(base, "        if low in seen:", "        if False:  # MUTANT-P2-LOOSEN", label="P2-loosen-dup")
    mods1 = _load_mutant(tmp_path, "context_policy", {"context_policy.py": m1})
    reg = make_dual_registry()
    dup_h = {"X-Soul-Turn-Id": "a", "x-soul-turn-id": "b"}
    # FUP-2 重做：同一 oracle 跑三態（修正前：只證 mutant 被接納）。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), dup_h, "req-d")
    assert ei.value.status == 400
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods1["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), dup_h, "req-d")
    assert "DID NOT RAISE" in str(kill.value)  # 大小寫重複被誤接納 ⇒ 殺死
    m2 = _mut_replace(
        base,
        '        if low.startswith("x-soul-") and low not in (_C, _B, _T):',
        "        if False:  # MUTANT-P2-LOOSEN",
        label="P2-loosen-unknown",
    )
    mods2 = _load_mutant(tmp_path, "context_policy", {"context_policy.py": m2})
    # FUP-2 重做：同一 oracle（未知 x-soul→400/unsupported_parameter）跑三態。
    with pytest.raises(InterfaceError) as ei:  # 正常綠
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Bogus": "1"}, "req-u")
    assert (ei.value.status, ei.value.code) == (400, "unsupported_parameter")
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        with pytest.raises(InterfaceError):
            mods2["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Bogus": "1"}, "req-u")
    assert "DID NOT RAISE" in str(kill.value)  # 未知 x-soul 被誤接納 ⇒ 殺死


def test_p2_mut_tighten_known_rejected(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        '        if low.startswith("x-soul-") and low not in (_C, _B, _T):',
        '        if low.startswith("x-soul-"):  # MUTANT-P2-TIGHTEN',
        label="P2-tighten",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-ok")  # 正常綠（同一 oracle）
    try:
        mods["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-ok")
    except Exception as e:
        assert getattr(e, "code", None) == "unsupported_parameter"  # 合法三識別 header 被拒 ⇒ 殺死
    else:
        raise AssertionError("tightened header check should reject legal headers")


# ── P3 ──────────────────────────────────────────────────────────────────


def test_p3_memory_states():
    reg = make_dual_registry()
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    assert d.memory_policy.value == "interactive_candidate"
    assert d.segments[-1].memory_candidate is True
    assert all(s.memory_candidate is False for s in d.segments[:-1])
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility")])
    reg_u = validate_registry(raw)
    du = admit(reg_u, TOKEN_U, "soul-a", "backend-a", _body(), _full_headers(), "req-u-01")
    assert du.memory_policy.value == "utility_no_memory"
    assert all(s.memory_candidate is False for s in du.segments)
    for h in [{}, {"X-Soul-Conversation-Id": "c"}, {"X-Soul-Turn-Id": "t"}, {"Idempotency-Key": "k"}]:
        de = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-e-01")
        assert de.memory_policy.value == "ephemeral"
        assert all(s.memory_candidate is False for s in de.segments)
    raw_nw = make_registry_raw(clients=[make_client_dict("client-nw", TOKEN_A, purpose="interactive", allow_mem=False)])
    reg_nw = validate_registry(raw_nw)
    dnw = admit(reg_nw, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-nw")
    assert dnw.memory_policy.value == "ephemeral"


def test_p3_mut_loosen_utility_candidate(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        "        policy = MemoryPolicy.UTILITY_NO_MEMORY",
        "        policy = MemoryPolicy.INTERACTIVE_CANDIDATE  # MUTANT-P3-LOOSEN",
        label="P3-loosen",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility")])
    reg_u = validate_registry(raw)
    du = admit(reg_u, TOKEN_U, "soul-a", "backend-a", _body(), _full_headers(), "req-u-01")

    def _oracle_utility_no_memory(d):
        assert d.memory_policy.value == "utility_no_memory"
        assert all(s.memory_candidate is False for s in d.segments)

    _oracle_utility_no_memory(du)  # 正常綠（同一 oracle）
    d = mods["context_policy"].admit(reg_u, TOKEN_U, "soul-a", "backend-a", _body(), _full_headers(), "req-u-01")
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_utility_no_memory(d)
    assert d.memory_policy.value == "interactive_candidate"  # 失敗原因（診斷，非殺死證據）：utility 被誤授候選


def test_p3_mut_tighten_candidate_lost(tmp_path):
    base = _read("context_policy.py")
    m1 = _mut_replace(
        base,
        "            policy = MemoryPolicy.INTERACTIVE_CANDIDATE",
        "            policy = MemoryPolicy.EPHEMERAL  # MUTANT-P3-TIGHTEN",
        label="P3-tighten-policy",
    )
    mods1 = _load_mutant(tmp_path, "context_policy", {"context_policy.py": m1})
    reg = make_dual_registry()
    dn = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")

    def _oracle_interactive_candidate(d):
        assert d.memory_policy.value == "interactive_candidate"

    _oracle_interactive_candidate(dn)  # 正常綠（同一 oracle）
    d = mods1["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_interactive_candidate(d)
    assert d.memory_policy.value == "ephemeral"  # 失敗原因（診斷，非殺死證據）：合法持久會話被降級
    m2 = _mut_replace(
        base,
        "source=last.source, memory_candidate=True)",
        "source=last.source, memory_candidate=False)  # MUTANT-P3-TIGHTEN",
        label="P3-tighten-mark",
    )
    mods2 = _load_mutant(tmp_path, "context_policy", {"context_policy.py": m2})
    dn2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")

    def _oracle_last_marked(d):
        assert d.segments[-1].memory_candidate is True

    _oracle_last_marked(dn2)  # 正常綠（同一 oracle）
    d2 = mods2["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_last_marked(d2)
    assert all(s.memory_candidate is False for s in d2.segments)  # 失敗原因（診斷，非殺死證據）：末則候選遺失


# ── P4 ──────────────────────────────────────────────────────────────────


def test_p4_generation_limits():
    reg = make_dual_registry()
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    assert d.generation_limits.temperature == 0.5 and d.generation_limits.max_tokens == 512
    assert d.generation_limits.context_window_tokens == 4096
    assert d.generation_limits.provider_attempts == 1 and d.generation_limits.tool_rounds == 0


def test_p4_mut_loosen_rounds(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        "    lim = GenerationLimits(temperature=req.temperature, max_tokens=req.max_tokens, context_window_tokens=route.context_window_tokens, provider_attempts=1, tool_rounds=0)",
        "    lim = GenerationLimits(temperature=req.temperature, max_tokens=req.max_tokens, context_window_tokens=route.context_window_tokens, provider_attempts=9, tool_rounds=9)  # MUTANT-P4-LOOSEN",
        label="P4-loosen",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    dn = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")

    def _oracle_single_round(d):
        assert (d.generation_limits.provider_attempts, d.generation_limits.tool_rounds) == (1, 0)

    _oracle_single_round(dn)  # 正常綠（同一 oracle）
    d = mods["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_single_round(d)
    assert (d.generation_limits.provider_attempts, d.generation_limits.tool_rounds) == (9, 9)  # 失敗原因（診斷）：多輪被放行


def test_p4_mut_tighten_no_attempt(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        "    lim = GenerationLimits(temperature=req.temperature, max_tokens=req.max_tokens, context_window_tokens=route.context_window_tokens, provider_attempts=1, tool_rounds=0)",
        "    lim = GenerationLimits(temperature=req.temperature, max_tokens=req.max_tokens, context_window_tokens=route.context_window_tokens, provider_attempts=0, tool_rounds=0)  # MUTANT-P4-TIGHTEN",
        label="P4-tighten",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    dn = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")

    def _oracle_has_attempt(d):
        assert d.generation_limits.provider_attempts == 1

    _oracle_has_attempt(dn)  # 正常綠（同一 oracle）
    d = mods["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_has_attempt(d)
    assert d.generation_limits.provider_attempts == 0  # 失敗原因（診斷）：一次嘗試規格被破壞


# ── P5 ──────────────────────────────────────────────────────────────────


def test_p5_namespace():
    reg = make_dual_registry()
    h = _full_headers()
    d1 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-same")
    d2 = admit(reg, TOKEN_B, "soul-a", "backend-a", _body("same text"), h, "req-diff-id")
    assert idempotency_namespace(d1.context) == idempotency_namespace(d2.context)
    assert idempotency_namespace(d1.context) == ("owner-01", "agent-01", "owner_private", "conv-01", "branch-01", "key-01")
    h2 = dict(h)
    h2["X-Soul-Branch-Id"] = "branch-02"
    d3 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h2, "req-same")
    assert idempotency_namespace(d1.context) != idempotency_namespace(d3.context)


def test_p5_mut_loosen_drop_branch(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        "return (context.profile.principal_id, context.route.agent_id, scv, context.conversation_id, context.branch_id, context.idempotency_key)",
        "return (context.profile.principal_id, context.route.agent_id, scv, context.conversation_id, context.idempotency_key)  # MUTANT-P5-LOOSEN",
        label="P5-loosen",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    h = _full_headers()
    d1 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-same")
    h2 = dict(h)
    h2["X-Soul-Branch-Id"] = "branch-02"
    d3 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h2, "req-same")

    def _oracle_namespace_splits(ns1, ns3):
        assert ns1 != ns3

    _oracle_namespace_splits(idempotency_namespace(d1.context), idempotency_namespace(d3.context))  # 正常綠（同一 oracle）
    mns1 = mods["context_policy"].idempotency_namespace(d1.context)
    mns3 = mods["context_policy"].idempotency_namespace(d3.context)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_namespace_splits(mns1, mns3)
    assert mns1 == mns3  # 失敗原因（診斷）：去重鍵碰撞


def test_p5_mut_tighten_add_request_id(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        "return (context.profile.principal_id, context.route.agent_id, scv, context.conversation_id, context.branch_id, context.idempotency_key)",
        "return (context.profile.principal_id, context.route.agent_id, scv, context.conversation_id, context.branch_id, context.idempotency_key, context.request_id)  # MUTANT-P5-TIGHTEN",
        label="P5-tighten",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    h = _full_headers()
    d1 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-1")
    d2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-2")

    def _oracle_same_op_same_key(ns1, ns2):
        assert ns1 == ns2

    _oracle_same_op_same_key(idempotency_namespace(d1.context), idempotency_namespace(d2.context))  # 正常綠（同一 oracle）
    mns1 = mods["context_policy"].idempotency_namespace(d1.context)
    mns2 = mods["context_policy"].idempotency_namespace(d2.context)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_same_op_same_key(mns1, mns2)
    assert mns1 != mns2  # 失敗原因（診斷）：同邏輯操作因 request_id 不同而分裂


# ── P6 ──────────────────────────────────────────────────────────────────


def test_p6_fingerprint():
    reg = make_dual_registry()
    h = _full_headers()
    base = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-1")
    same_other_req = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-2")
    assert request_fingerprint(base) == request_fingerprint(same_other_req)
    other_text = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("other text"), h, "req-1")
    assert request_fingerprint(base) != request_fingerprint(other_text)
    turn2 = dict(h)
    turn2["X-Soul-Turn-Id"] = "turn-02"
    assert request_fingerprint(base) != request_fingerprint(
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), turn2, "req-1"))
    src = _read("context_policy.py")
    assert "sort_keys=True" in src and 'separators=(",", ":")' in src and "allow_nan=False" in src
    d2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-im")
    assert request_fingerprint(base) == request_fingerprint(d2)


def test_p6_mut_loosen_drop_turn(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(base, '        "turn_id": ctx.turn_id,\n', "", label="P6-loosen")
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    h = _full_headers()
    d1 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-1")
    turn2 = dict(h)
    turn2["X-Soul-Turn-Id"] = "turn-02"
    d2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), turn2, "req-1")

    def _oracle_turn_sensitive(fp1, fp2):
        assert fp1 != fp2

    _oracle_turn_sensitive(request_fingerprint(d1), request_fingerprint(d2))  # 正常綠（同一 oracle）
    mfp1 = mods["context_policy"].request_fingerprint(d1)
    mfp2 = mods["context_policy"].request_fingerprint(d2)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_turn_sensitive(mfp1, mfp2)
    assert mfp1 == mfp2  # 失敗原因（診斷）：turn 敏感度遺失


def test_p6_mut_tighten_add_request_id(tmp_path):
    base = _read("context_policy.py")
    mut = _mut_replace(
        base,
        '        "turn_id": ctx.turn_id,\n',
        '        "turn_id": ctx.turn_id,\n        "request_id": ctx.request_id,  # MUTANT-P6-TIGHTEN\n',
        label="P6-tighten",
    )
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    assert "sort_keys=True" in base  # 正常版序列化穩定（對照）
    reg = make_dual_registry()
    h = _full_headers()
    d1 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-1")
    d2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-2")

    def _oracle_request_id_isolated(fp1, fp2):
        assert fp1 == fp2

    _oracle_request_id_isolated(request_fingerprint(d1), request_fingerprint(d2))  # 正常綠（同一 oracle）
    mfp1 = mods["context_policy"].request_fingerprint(d1)
    mfp2 = mods["context_policy"].request_fingerprint(d2)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_request_id_isolated(mfp1, mfp2)
    assert mfp1 != mfp2  # 失敗原因（診斷）：request_id 隔離破線


def test_p6_sensitivity_route_generation_policy_candidate():
    """FUP-3 P6 補件（重做）：每欄以 dataclasses.replace 單改一欄、其餘全同，獨證該欄敏感度。

    FUP-2 版同時改變識別欄＋policy＋candidate（跨 admit 輸入維度），不能獨證 policy；
    本版每個維度只動一個欄位：route 單改 soul_alias、generation 單改 max_tokens、
    policy 單改 memory_policy、candidate 單改末則 memory_candidate。
    """
    import dataclasses

    from src.interfaces.schema import MemoryPolicy as _MP

    reg = make_dual_registry()
    h = _full_headers()
    base = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-1")
    fp0 = request_fingerprint(base)
    # route：只動 soul_alias 一欄（同一次 admit 的其餘欄全同）→ 指紋不同
    route2 = dataclasses.replace(base.context.route, soul_alias="soul-b")
    dec_route = dataclasses.replace(base, context=dataclasses.replace(base.context, route=route2))
    assert request_fingerprint(dec_route) != fp0
    # generation：只動 max_tokens 一欄 → 指紋不同
    lim2 = dataclasses.replace(base.generation_limits, max_tokens=128)
    assert request_fingerprint(dataclasses.replace(base, generation_limits=lim2)) != fp0
    # policy：只動 memory_policy 一欄（INTERACTIVE_CANDIDATE→EPHEMERAL）→ 指紋不同
    assert request_fingerprint(dataclasses.replace(base, memory_policy=_MP.EPHEMERAL)) != fp0
    # candidate：僅末則 memory_candidate 翻轉，指紋即不同（其餘欄全同）
    last = base.segments[-1]
    flipped = dataclasses.replace(base.segments[-1], memory_candidate=not last.memory_candidate)
    dec2 = dataclasses.replace(base, segments=base.segments[:-1] + (flipped,))
    assert request_fingerprint(dec2) != fp0


def test_p6_mut_loosen_sort_keys_unstable(tmp_path):
    """FUP-3 P6 補件：sort_keys=False 變異被殺（文件舊宣稱無實作，現補做）。

    同次建構下插入序由程式碼固定，功能輸出逐位元相同，故以存在性 oracle
    見證源碼性質；另載入 mutant 證其可匯入（非稻草人），殺死純由 oracle 承載。
    """
    base = _read("context_policy.py")
    assert "sort_keys=True" in base
    mut = _mut_replace(base, "sort_keys=True", "sort_keys=False", label="P6-sortkeys")
    _oracle_serialization_stable(base)  # 正常綠
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_serialization_stable(mut)
    assert "sort_keys=False" in mut  # 失敗原因（診斷）：穩定性開關被關
    mods = _load_mutant(tmp_path, "context_policy", {"context_policy.py": mut})
    reg = make_dual_registry()
    d = mods["context_policy"].admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), _full_headers(), "req-1")
    assert isinstance(mods["context_policy"].request_fingerprint(d), str)  # mutant 可運行（功能同形，殺死由 oracle 承載）


# ── S1 ──────────────────────────────────────────────────────────────────


def test_s1_fields_enums_defaults():
    from src.interfaces.schema import ContentSegment
    from src.interfaces.schema import GenerationLimits
    from src.interfaces.schema import MemoryPolicy
    from src.interfaces.schema import Message
    from src.interfaces.schema import Purpose
    from src.interfaces.schema import RequestContext
    from src.interfaces.schema import RequestInput
    from src.interfaces.schema import Scope
    from src.interfaces.schema import Source

    assert (Purpose.INTERACTIVE.value, Purpose.UTILITY.value) == ("interactive", "utility")
    assert (Scope.OWNER_PRIVATE.value, Scope.NONE.value) == ("owner_private", "none")
    assert (MemoryPolicy.UTILITY_NO_MEMORY.value, MemoryPolicy.EPHEMERAL.value, MemoryPolicy.INTERACTIVE_CANDIDATE.value) == (
        "utility_no_memory", "ephemeral", "interactive_candidate")
    assert (Source.EXTERNAL_INSTRUCTION.value, Source.EXTERNAL_HISTORY.value, Source.CURRENT_USER.value) == (
        "external_instruction", "external_history", "current_user")
    r = RequestInput(model="m", messages=())
    assert (r.stream, r.temperature, r.max_tokens, r.n) == (False, 0.5, 512, 1)
    g = GenerationLimits(temperature=0.5, max_tokens=10, context_window_tokens=100)
    assert (g.provider_attempts, g.tool_rounds) == (1, 0)
    m = Message(role="user", content="hi")
    assert m.name is None
    seg = ContentSegment(role="user", content="hi", name=None, source=Source.CURRENT_USER)
    assert seg.memory_candidate is False


def test_s1_mut_loosen_default_temp(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(base, "    temperature: float = 0.5", "    temperature: float = 2.5  # MUTANT-S1-LOOSEN", label="S1-loosen")
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})
    from src.interfaces.schema import RequestInput as RealRequestInput

    rn = RealRequestInput(model="m", messages=())

    def _oracle_default_temp_in_range(r):
        assert (r.temperature, 0 <= r.temperature <= 2) == (0.5, True)

    _oracle_default_temp_in_range(rn)  # 正常綠（同一 oracle）
    r = mods["schema"].RequestInput(model="m", messages=())
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_default_temp_in_range(r)
    assert r.temperature == 2.5  # 失敗原因（診斷）：預設值掉出合法區間


def test_s1_mut_tighten_drop_default(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(base, "    n: int = 1", "    n: int  # MUTANT-S1-TIGHTEN", label="S1-tighten")
    import src.interfaces.schema  # 正常綠（同一 oracle：合法匯入通過）
    with pytest.raises(TypeError):
        _load_mutant(tmp_path, "schema", {"schema.py": mut})  # 末欄去預設 ⇒ 定義期 TypeError ⇒ 殺死


# ── S2 ──────────────────────────────────────────────────────────────────


def test_s2_frozen():
    import dataclasses

    import src.interfaces.schema as sch

    reg = make_dual_registry()
    with pytest.raises(Exception):
        reg.routes[0].soul_alias = "x"
    with pytest.raises(Exception):
        reg.owner_principal_id = "other"
    with pytest.raises(AttributeError):
        reg.backends.append("port-b")
    checked = 0
    for name in dir(sch):
        obj = getattr(sch, name)
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            assert obj.__dataclass_params__.frozen, name
            checked += 1
    assert checked >= 8


def test_s2_mut_loosen_unfrozen(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(
        base,
        "@dataclass(frozen=True)\nclass RouteBinding:",
        "@dataclass  # MUTANT-S2-LOOSEN\nclass RouteBinding:",
        label="S2-loosen",
    )
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})
    rb = mods["schema"].RouteBinding(soul_alias="s", backend_alias="b", agent_id="a",
                                     public_model_id="m", backend_ref="r", context_window_tokens=1)

    def _oracle_frozen(binding, field="soul_alias", value="x"):
        with pytest.raises(Exception):
            setattr(binding, field, value)

    _oracle_frozen(make_dual_registry().routes[0])  # 正常綠（同一 oracle：frozen 禁改寫）
    with pytest.raises(BaseException) as kill:  # 同一 oracle 跑 mutant 必須轉紅
        _oracle_frozen(rb)
    assert "DID NOT RAISE" in str(kill.value)  # 誤改寫未被拒 ⇒ 殺死
    assert rb.soul_alias == "x"  # 失敗原因（診斷）：不可變性遺失（改寫成功）


def test_s2_mut_tighten_rename_field(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(
        base,
        "class RouteBinding:\n    soul_alias: str",
        "class RouteBinding:\n    soul_alias_renamed: str  # MUTANT-S2-TIGHTEN",
        label="S2-tighten",
    )
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})
    from src.interfaces.schema import RouteBinding as RealRouteBinding

    RealRouteBinding(soul_alias="s", backend_alias="b", agent_id="a",
                     public_model_id="m", backend_ref="r", context_window_tokens=1)  # 正常綠（同一 oracle：合法建構通過）
    with pytest.raises(TypeError):
        mods["schema"].RouteBinding(soul_alias="s", backend_alias="b", agent_id="a",
                                    public_model_id="m", backend_ref="r", context_window_tokens=1)  # 合法建構被破壞 ⇒ 殺死


# ── S3 ──────────────────────────────────────────────────────────────────


def test_s3_digest_repr():
    reg = make_dual_registry()
    blob = repr(reg.credentials[0])
    assert reg.credentials[0].digest not in blob


def test_s3_mut_loosen_repr_true(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(
        base,
        "    digest: str = field(repr=False)",
        "    digest: str = field(repr=True)  # MUTANT-S3-LOOSEN",
        label="S3-loosen",
    )
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})
    reg = make_dual_registry()

    def _oracle_digest_hidden(bind):
        assert bind.digest not in repr(bind)

    _oracle_digest_hidden(reg.credentials[0])  # 正常綠（同一 oracle）
    prof = reg.clients[0]
    bind = mods["schema"].CredentialBinding(digest=digest_for(TOKEN_A), profile=prof)
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_digest_hidden(bind)
    assert digest_for(TOKEN_A) in repr(bind)  # 失敗原因（診斷）：digest 外洩進 repr


def test_s3_mut_tighten_drop_digest(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(base, "    digest: str = field(repr=False)\n", "", label="S3-tighten")
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})
    reg = make_dual_registry()
    from src.interfaces.schema import CredentialBinding as RealCredentialBinding

    RealCredentialBinding(digest=digest_for(TOKEN_A), profile=reg.clients[0])  # 正常綠（同一 oracle：合法建構通過）
    with pytest.raises(TypeError):
        mods["schema"].CredentialBinding(digest=digest_for(TOKEN_A), profile=reg.clients[0])  # 合法建構被破壞 ⇒ 殺死


# ── S4 ──────────────────────────────────────────────────────────────────


def test_s4_error_shape():
    e = InterfaceError(404, "route_not_found", "route", "Unknown route.")
    assert (e.status, e.code, e.param, e.message) == (404, "route_not_found", "route", "Unknown route.")


def test_s4_mut_loosen_drop_param(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(base, "        self.param = param", "        self.param = None  # MUTANT-S4-LOOSEN", label="S4-loosen")
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})

    def _oracle_param_kept(e):
        assert e.param == "route"

    _oracle_param_kept(InterfaceError(404, "route_not_found", "route", "Unknown route."))  # 正常綠（同一 oracle）
    e = mods["schema"].InterfaceError(404, "route_not_found", "route", "Unknown route.")
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_param_kept(e)
    assert e.param is None  # 失敗原因（診斷）：param 遺失


def test_s4_mut_tighten_extra_arg(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(
        base,
        "    def __init__(self, status: int, code: str, param: Optional[str], message: str):",
        "    def __init__(self, status: int, code: str, param: Optional[str], message: str, extra: str):  # MUTANT-S4-TIGHTEN",
        label="S4-tighten",
    )
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})
    InterfaceError(404, "route_not_found", "route", "Unknown route.")  # 正常綠（同一 oracle：既有建構通過）
    with pytest.raises(TypeError):
        mods["schema"].InterfaceError(404, "route_not_found", "route", "Unknown route.")  # 既有建構被破壞 ⇒ 殺死


# ── S5 ──────────────────────────────────────────────────────────────────


def test_s5_id_rules():
    assert is_valid_id("owner-01") and is_valid_id("a" * 64)
    assert is_valid_id("model a") is False and is_valid_public_model_id("model a") is True
    for bad in ["", "a/b", "a b", "a\nb", ".", "..", "x" * 129, "a\\b"]:
        assert is_valid_id(bad) is False
    for bad in ["", "   ", "\t", "a/b", "a\\b", ".", "..", "x" * 129, "a\ud800b", "ab\x00cd", "ab\x1fcd"]:
        assert is_valid_public_model_id(bad) is False
    assert is_valid_public_model_id("model-a") is True


def test_s5_mut_loosen_id_charset(tmp_path):
    base = _read("schema.py")
    mut = _mut_replace(
        base,
        "        if ch not in _ID_CHARS:\n            return False",
        "        if False:  # MUTANT-S5-LOOSEN\n            return False",
        label="S5-loosen",
    )
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})

    def _oracle_id_strict(check):
        assert check("a b") is False

    _oracle_id_strict(is_valid_id)  # 正常綠（同一 oracle）
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_id_strict(mods["schema"].is_valid_id)
    assert mods["schema"].is_valid_id("a b") is True  # 失敗原因（診斷）：字元集被放寬


def test_s5_mut_tighten_model_len(tmp_path):
    base = _read("schema.py")
    old = ("def is_valid_public_model_id(value: object) -> bool:\n"
           "    if not isinstance(value, str):\n"
           "        return False\n"
           "    n = len(value)\n"
           "    if n < 1 or n > 128:")
    new = ("def is_valid_public_model_id(value: object) -> bool:\n"
           "    if not isinstance(value, str):\n"
           "        return False\n"
           "    n = len(value)\n"
           "    if n < 1 or n > 5:  # MUTANT-S5-TIGHTEN")
    mut = _mut_replace(base, old, new, label="S5-tighten")
    mods = _load_mutant(tmp_path, "schema", {"schema.py": mut})

    def _oracle_model_len(check):
        assert check("model-a") is True

    _oracle_model_len(is_valid_public_model_id)  # 正常綠（同一 oracle）
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_model_len(mods["schema"].is_valid_public_model_id)
    assert mods["schema"].is_valid_public_model_id("model-a") is False  # 失敗原因（診斷）：合法 model 被拒


# ── N1 ──────────────────────────────────────────────────────────────────


def test_n1_empty_entry():
    import types

    import src.interfaces as pkg

    text = _read("__init__.py")
    tree = ast.parse(text, filename="__init__.py")
    for node in tree.body:
        assert isinstance(node, ast.Expr), "entry must stay docstring-only"
    public = [n for n in dir(pkg) if not n.startswith("_")]
    leaked = [n for n in public if not isinstance(getattr(pkg, n), types.ModuleType)]
    assert leaked == []  # 子模組屬性係匯入機制正常綁定，不算入口洩漏


def test_n1_mut_loosen_leak_symbol(tmp_path):
    import types as _types

    import src.interfaces as _real_pkg

    base = _read("__init__.py")
    mut = base + "\nfrom .schema import InterfaceError  # MUTANT-N1-LOOSEN\n"
    mods = _load_mutant(tmp_path, "pkg", {"__init__.py": mut})

    def _oracle_no_leak(pkg):
        leaked = [n for n in dir(pkg) if not n.startswith("_") and not isinstance(getattr(pkg, n), _types.ModuleType)]
        assert leaked == []

    _oracle_no_leak(_real_pkg)  # 正常綠（同一 oracle：無非模組公開符號）
    with pytest.raises(AssertionError):  # 同一 oracle 跑 mutant 必須轉紅 ⇒ 殺死
        _oracle_no_leak(mods["pkg"])
    assert hasattr(mods["pkg"], "InterfaceError")  # 失敗原因（診斷）：入口洩漏符號


def test_n1_mut_tighten_raise_on_import(tmp_path):
    import importlib as _il
    import sys as _sys

    base = _read("__init__.py")
    mut = base + '\nraise RuntimeError("MUTANT-N1-TIGHTEN")\n'
    pkg = "mutpkg_%s" % uuid.uuid4().hex[:12]
    root = tmp_path / pkg
    root.mkdir()
    for name in _PKG_MODULES:
        text = mut if name == "__init__.py" else _read(name)
        if name == "__init__.py":
            _hard_block_before_import(text, name)
        (root / name).write_text(text, encoding="utf-8")
    _sys.path.insert(0, str(tmp_path))
    try:
        _il.import_module("src.interfaces")  # 正常綠（同一 oracle：合法匯入通過）
        with pytest.raises(RuntimeError):
            _il.import_module(pkg)  # 合法匯入被破壞 ⇒ 殺死
    finally:
        try:
            _sys.path.remove(str(tmp_path))
        except ValueError:
            pass
        for k in [k for k in _sys.modules if k == pkg or k.startswith(pkg + ".")]:
            del _sys.modules[k]


# ── N2 ──────────────────────────────────────────────────────────────────


def _oracle_no_foreign_import(text: str, rel: str) -> None:
    """N2 同一驗收 oracle：乾淨性檢查結果必須為空。"""
    assert _check_imports_clean(text, rel) == [], rel


def test_n2_import_clean():
    for rel in ("src/interfaces/__init__.py", "src/interfaces/schema.py", "src/interfaces/config.py",
                "src/interfaces/identity.py", "src/interfaces/classification.py", "src/interfaces/context_policy.py"):
        with open(IFACE_DIR / Path(rel).name, "r", encoding="utf-8") as fh:
            text = fh.read()
        _oracle_no_foreign_import(text, rel)  # 正常綠（同一 oracle）
    saved = {k: v for k, v in sys.modules.items() if k == "src.interfaces" or k.startswith("src.interfaces.")}
    for k in list(saved.keys()):
        del sys.modules[k]
    try:
        importlib.import_module("src.interfaces.schema")
        importlib.import_module("src.interfaces.config")
        importlib.import_module("src.interfaces.identity")
        importlib.import_module("src.interfaces.classification")
        importlib.import_module("src.interfaces.context_policy")
    finally:
        for k in [k for k in sys.modules if k == "src.interfaces" or k.startswith("src.interfaces.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def test_n2_mut_loosen_foreign_import(tmp_path):
    base = _read("classification.py")
    mut = base + "\nimport src.agent.registry  # MUTANT-N2-LOOSEN\n"
    _oracle_no_foreign_import(base, "src/interfaces/classification.py")  # 正常綠（同一 oracle）
    bad = _check_imports_clean(mut, "src/interfaces/classification.py")
    assert any("src.agent.registry" in b for b in bad)  # 失敗原因（非殺死證據）：辨識 mutant 注入位置的診斷行
    with pytest.raises(AssertionError):
        _oracle_no_foreign_import(mut, "src/interfaces/classification.py")  # 同一 oracle 殺死 mutant（紅）


def test_n2_mut_tighten_drop_import(tmp_path):
    base = _read("classification.py")
    mut = _mut_replace(base, "from .schema import Message\n", "", label="N2-tighten")
    mods = _load_mutant(tmp_path, "classification", {"classification.py": mut})
    reg = make_dual_registry()
    route = reg.routes[0]
    raw = {"model": route.public_model_id, "messages": [{"role": "user", "content": "hi"}]}
    parse_request(raw, route)  # 正常綠（同一 oracle：合法呼叫通過）
    with pytest.raises(NameError):
        mods["classification"].parse_request(raw, route)  # Message 未匯入 ⇒ 呼叫期 NameError ⇒ 殺死


def test_n2_mut_loosen_blocked_io_witness(tmp_path):
    """FUP-2 N2 補件：在 TEMP 副本注入 socket／subprocess／open，證明匯入前硬攔擋下。

    既有 test_n2_mut_loosen_foreign_import 只證外來包；本測試補實際封鎖 I/O 的匯入見證。
    """
    base = _read("classification.py")
    _hard_block_before_import(base, "N2-base")  # 正常版不攔（對照綠）
    cases = {
        "N2-io-socket": base + "\nimport socket  # MUTANT-N2-IO\n",
        "N2-io-subprocess": base + "\nimport subprocess  # MUTANT-N2-IO\n",
        "N2-io-open": base + "\nopen('evil.txt', 'w').write('x')  # MUTANT-N2-IO\n",
    }
    for label, mut in cases.items():
        with pytest.raises(AssertionError) as ei:
            _hard_block_before_import(mut, label)
        assert "blocked mutant" in str(ei.value), label  # 攔截訊息見證
        with pytest.raises(AssertionError) as ei2:  # 經 _load_mutant 同樣被擋（不進匯入）
            _load_mutant(tmp_path, "classification", {"classification.py": mut})
        assert "blocked mutant" in str(ei2.value), label


def test_n2_normal_import_under_blocked_io(monkeypatch):
    """FUP-3 N2 補件：封鎖 socket／subprocess／寫入式 open／os.environ 讀取時，
    正常六模組仍成功匯入且可運行（封鎖只應殺 mutant 注入，不應殺正常匯入）。

    讀取向 open 不封：它是匯入機制本身（封了等於封匯入）；寫入／追加／獨佔／讀寫
    模式必擋（對應 :1867 的寫入式 open 注入）。os 層寫入（os.open 等）不在 mutant
    威脅模型內（mutant 是源碼級注入，由 AST 門擋），故本 harness 不封。
    """
    import builtins as _bi
    import os as _os
    import socket as _sockmod
    import subprocess as _spmod

    real_open = _bi.open

    def _guarded_open(file, mode="r", *args, **kwargs):
        m = mode if isinstance(mode, str) else "r"
        if any(c in m for c in ("w", "a", "x", "+")):
            raise AssertionError("blocked open-write in N2 blocked harness")
        return real_open(file, mode, *args, **kwargs)

    def _blocked(*args, **kwargs):
        raise AssertionError("blocked call in N2 blocked harness")

    def _blocked_getitem(self, key):
        raise AssertionError("blocked environ read in N2 blocked harness")

    def _blocked_get(self, key, default=None):
        raise AssertionError("blocked environ read in N2 blocked harness")

    monkeypatch.setattr(_bi, "open", _guarded_open)
    monkeypatch.setattr(_sockmod, "socket", _blocked)
    monkeypatch.setattr(_sockmod, "create_connection", _blocked)
    monkeypatch.setattr(_spmod, "Popen", _blocked)
    monkeypatch.setattr(_spmod, "run", _blocked)
    monkeypatch.setattr(_spmod, "call", _blocked)
    monkeypatch.setattr(_spmod, "check_output", _blocked)
    monkeypatch.setattr(_spmod, "check_call", _blocked)
    monkeypatch.setattr(_os, "system", _blocked)
    monkeypatch.setattr(_os, "popen", _blocked)
    monkeypatch.setattr(_os, "getenv", _blocked)
    monkeypatch.setattr(_os._Environ, "__getitem__", _blocked_getitem)
    monkeypatch.setattr(_os._Environ, "get", _blocked_get)
    monkeypatch.setattr("sys.dont_write_bytecode", True)
    saved = {k: v for k, v in sys.modules.items() if k == "src.interfaces" or k.startswith("src.interfaces.")}
    for k in list(saved.keys()):
        del sys.modules[k]
    try:
        schema = importlib.import_module("src.interfaces.schema")  # noqa: F841（匯入成功即見證）
        config = importlib.import_module("src.interfaces.config")
        identity = importlib.import_module("src.interfaces.identity")
        classification = importlib.import_module("src.interfaces.classification")
        context_policy = importlib.import_module("src.interfaces.context_policy")
        reg = config.validate_registry(make_registry_raw())
        route = reg.routes[0]
        raw = {"model": route.public_model_id, "messages": [{"role": "user", "content": "hi"}]}
        req = classification.parse_request(raw, route)
        assert classification.classify_messages(req)[-1].source.value == "current_user"
        client = identity.authenticate(TOKEN_A, reg)
        identity.authorize_route(client, route)
        d = context_policy.admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-blocked")
        assert isinstance(context_policy.request_fingerprint(d), str)
    finally:
        for k in [k for k in sys.modules if k == "src.interfaces" or k.startswith("src.interfaces.")]:
            del sys.modules[k]
        sys.modules.update(saved)
