# -*- coding: utf-8 -*-
"""test_live_service_probes_are_opt_in.py — 線上服務探針的 opt-in 護欄

2026-10-27 實測發現（三方交叉確認，不是推測）
──────────────────────────────────────────────
`tests/test_identity.py` 原本硬寫 `ws://localhost:8000/ws`、**完全沒有任何 opt-in
閘門**；pytest 會收集它的 `async def test_identity` ⇒ 每次全庫回歸都直連生產
服務、送出 `USER_MESSAGE` ⇒ 生產服務寫入對話／記憶、產生 TTS、跑**真實 LLM
推理**（成本非 0）。2026-09-17 事故同形。

真正危險的不是那一個檔案，是它讓任何人可以合理地說
「我只是跑了 pytest 全庫，我沒有特別呼叫生產」。

這支護欄把規則變成可擋的東西：

  R1（ws 目標由 env 決定 ⇒ 必須無預設目標 ＋ 必須有 skip 閘門引用同一 env）
      任何模組若把 **websocket 目標**交給環境變數決定：
        · 該 env 讀取**不得帶非空預設**（帶了 ⇒ 環境未設定時仍有目標 ⇒ 有能力）
        · 該模組必須有跳過閘門，且其條件／訊息引用**同一個** env 變數
          ⇒ 環境未設定 ⇒ 測試 skip ⇒ 預設關閉
      ★ **遞迴**追蹤模組常數（Name → 常數 → 常數裡的 Name → …，含 visited 防環）。
        舊版只追一層 ⇒ `WS_URL = (LIVE_BASE.replace(...))` 追不到 `LIVE_BASE` 的
        env 讀取 ⇒ `tests/test_identity.py` 是「**因為掃不到而通過**」，
        不是因為它真的合規（**綠燈不算證據**）。現在它必須被真正掃到。
  R2 `tests/**` **全部** .py 檔的**程式碼字串常數**不得含生產服務位址
      （docstring 與註解不算 —— 那些是在記錄「原本錯在哪」）；
      例外僅限 spawn guard 保留的 3 筆（明確列舉、附理由、且不得是死規則）。
  R3 掃描器自測：合成的違規樣本必須被 R1 抓到（護欄自己不是盲腸），
      且樣本必須**穿透遞迴追蹤**（A = env 讀取、B = A.replace(...)、呼叫端用 B）。
  R4（新增）http(s) 目標（`httpx.get` / `httpx.Client().get` /
      `urllib.request.urlopen`）若由 env 決定，同樣要求
      「無非空預設 ＋ 同一 env 被跳過閘門引用」⇒ 涵蓋兩個 `/health` 探針。

🔺 本檔自身不得出現生產位址的**字面**（會被 tests/infra/test_no_production_spawn_guard.py
 的位址規則命中）⇒ 目標字串以片段拼接產生。

執行：
  .venv\Scripts\python.exe -m pytest tests\infra\test_live_service_probes_are_opt_in.py -q
"""
import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent

#: 生產服務位址（以片段拼接 ⇒ 本檔不含該字面，避免自我命中位址規則）。
PRODUCTION_ADDR = "localhost" + ":" + "8000"

#: 生產服務位址的**迴圈主機**與**埠**（同樣以片段拼接 ⇒ 本檔不含該字面）。
ADDR_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
ADDR_PORT = "8000"


def _address_re():
    """生產服務位址樣式（與 spawn guard 的 ADDRESS_RE 同語意）。

    以片段拼接 ⇒ 本檔自身不含該字面（護欄不能是位址規則的例外）。
    """
    hosts = "|".join(re.escape(h) for h in ADDR_HOSTS)
    return re.compile(
        r"(?:https?|wss?)://[^\s'\"<>()]*:" + ADDR_PORT + r"(?!\d)"
        r"|(?:" + hosts + r"):" + ADDR_PORT + r"(?!\d)",
        re.IGNORECASE,
    )


#: 生產服務位址樣式（R2 用）。
ADDRESS_RE = _address_re()

#: 會對外連線的 websocket 進入點（AST 形狀：`websockets.connect(...)`）。
WS_CONNECT_FUNCS = {("websockets", "connect")}

#: 會對外連線的 http(s) 進入點（AST 形狀：R4）。
HTTP_TARGET_FUNCS = {("httpx", "get"), ("urllib", "request", "urlopen")}

#: 視為「env 讀取」的形狀。
ENV_GET = {("os", "environ", "get"), ("os", "getenv")}

#: 執行期跳過訊息裡的 env 名樣式（`self.skipTest("... SOUL_OS_XXX ...")`）。
ENV_NAME_RE = re.compile(r"\b[A-Z][A-Z0-9_]{5,}\b")

#: R2 的**唯一**例外（與 tests/infra/test_no_production_spawn_guard.py 的
#: ADDRESS_ALLOWLIST 保留的 3 筆一致）。三筆**都不是線上連線**：
ADDRESS_EXCEPTIONS = {
    ("tests/clients/test_vc_perf_opt_1.py", 303):
        "OpenAI 相容 base URL 交給 adapter（從不撥號）",
    ("tests/test_infra_no_pattern_process_kill.py", 430):
        "合成 classifier fixture（樣本原始碼字串，不是真實位址）",
    ("tests/test_websocket_e2e.py", 69):
        "PRODUCTION_PORT 紅線：整數常數，只用來**拒絕**撥號"
        "（字串常數規則不會命中它；列在此處是為了與 spawn guard 的 R3b 對齊）",
}


def _all_modules():
    """掃描目標：tests/ 下**所有** .py（**含本檔自身**）。

    R2 刻意含本檔：護衛自己的位址字面若被塞回來，也必須紅。
    （本檔的生產位址一律以片段拼接產生 ⇒ AST 上不是單一字串常數。）
    """
    return sorted(TESTS_DIR.rglob("*.py"))


def _modules():
    """R1/R4 的掃描目標：tests/ 下所有 .py（排除本檔自身，避免自我指涉）。

    本檔自身沒有任何目標進入點（合成樣本是**字串**，不是呼叫）⇒ 排除只是為了
    讓「本檔談論的形狀」不被自己判為違規者。
    """
    here = Path(__file__).resolve()
    return [p for p in _all_modules() if p.resolve() != here]


def _dotted(node):
    """把 `os.environ.get` / `os.getenv` / `websockets.connect` 拆成點號元組。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return ()


def _env_reads(node):
    """回傳此 AST 子樹內所有 env 讀取：(env_name, has_default, default_value)。"""
    out = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if _dotted(sub.func) not in ENV_GET:
            continue
        if not sub.args or not isinstance(sub.args[0], ast.Constant):
            continue
        name = sub.args[0].value
        if not isinstance(name, str):
            continue
        has_default = len(sub.args) >= 2
        default = sub.args[1].value if (has_default and isinstance(sub.args[1], ast.Constant)) else None
        out.append((name, has_default, default))
    return out


def _module_constants(tree):
    """模組層級常數 → 其值的 AST 節點（供「目標由常數指派」時遞迴追蹤）。"""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _resolve_env_reads(expr, consts, visited=None):
    """**遞迴**解析 `expr` 裡（經由模組層級常數）的所有 env 讀取。

    為什麼必須遞迴：目標常常是「常數加工後」的結果 ——
        LIVE_BASE = os.environ.get("SOUL_OS_LIVE_SERVICE_BASE_URL", "")
        WS_URL    = LIVE_BASE.replace("http://", "ws://") + "/ws"
        websockets.connect(WS_URL)
    只追一層（`WS_URL` → 它的值裡沒有 env 讀取）⇒ **掃不到** `LIVE_BASE` 的 env
    讀取 ⇒ 該模組「因為掃不到而通過」，不是因為它真的合規（綠燈不算證據）。

    `visited` 防環：常數互相引用時不會無限遞迴；同一常數只解析一次。
    """
    if visited is None:
        visited = set()
    out = list(_env_reads(expr))
    for node in ast.walk(expr):
        if not isinstance(node, ast.Name):
            continue
        if node.id in visited or node.id not in consts:
            continue
        visited.add(node.id)
        out.extend(_resolve_env_reads(consts[node.id], consts, visited))
    return out


def _is_ws_call(call):
    """`websockets.connect(...)` 的 AST 形狀。"""
    return _dotted(call.func) in WS_CONNECT_FUNCS


def _is_http_call(call):
    """http(s) 目標進入點的 AST 形狀。

    `httpx.get(...)` / `urllib.request.urlopen(...)` / `httpx.Client().get(...)`
    """
    if _dotted(call.func) in HTTP_TARGET_FUNCS:
        return True
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Call)
        and _dotted(func.value.func) == ("httpx", "Client")
    )


def _target_env_reads(tree, is_target):
    """所有目標進入點的呼叫，其目標引數裡（含遞迴）的 env 讀取。"""
    consts = _module_constants(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not is_target(node):
            continue
        for arg in node.args:
            out.extend(_resolve_env_reads(arg, consts))
    return out


def _ws_target_env_reads(tree):
    """ws 目標（`websockets.connect(...)`）的 env 讀取（**遞迴**追模組常數）。"""
    return _target_env_reads(tree, _is_ws_call)


def _http_target_env_reads(tree):
    """http(s) 目標的 env 讀取（**遞迴**追模組常數）。"""
    return _target_env_reads(tree, _is_http_call)


def _skipif_env_names(tree):
    """回傳所有 `pytest.mark.skipif(cond, ...)` 的 cond 裡出現的 env 名稱。"""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted(node.func)[-3:] != ("pytest", "mark", "skipif"):
            continue
        if not node.args:
            continue
        for name, _hd, _d in _env_reads(node.args[0]):
            names.add(name)
    return names


def _runtime_skip_env_names(tree):
    """回傳 `self.skipTest(...)` / `pytest.skip(...)` 訊息裡出現的 env 名稱。

    unittest 風格的測試沒有 `pytest.mark.skipif`；它們在測試體內用
    `self.skipTest(...)` 跳過。訊息必須點名同一個 env，否則「沒有目標就跳過」
    這件事無法被機械驗證（跳過了，但跳過的是哪個 env 會脫鉤）。
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_skip_test = isinstance(func, ast.Attribute) and func.attr == "skipTest"
        is_pytest_skip = _dotted(func) == ("pytest", "skip")
        if not (is_skip_test or is_pytest_skip):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    names.update(ENV_NAME_RE.findall(sub.value))
    return names


def _gate_env_names(tree):
    """所有跳過閘門引用的 env 名稱（`pytest.mark.skipif` ＋ 執行期 skip）。"""
    return _skipif_env_names(tree) | _runtime_skip_env_names(tree)


def _docstring_ids(tree):
    """``id()`` of every docstring constant, so prose is not read as code."""
    ids = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, holders):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _code_string_constants_with_lines(tree):
    """模組裡所有**非 docstring** 的字串常數 → (lineno, value)（依行號排序）。

    註解在 AST 上不存在 ⇒ 自動不算；docstring 是 `ast.Constant` 但語意是散文
    ⇒ 明確排除。
    """
    docstrings = _docstring_ids(tree)
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out.append((node.lineno, node.value))
    return sorted(out)


def _exception_still_applies(tree, lineno):
    """例外條目是否仍然指向同一件事（死規則＝悄悄失效的護欄）。

    兩種形狀都算：
      · 該行仍有含生產位址的**程式碼字串常數**；
      · 該行仍是整數的服務埠常數（`PRODUCTION_PORT` 紅線）。
    """
    for node_line, value in _code_string_constants_with_lines(tree):
        if node_line == lineno and ADDRESS_RE.search(value):
            return True
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and node.lineno == lineno
            and isinstance(node.value, ast.Constant)
            and node.value.value == int(ADDR_PORT)
        ):
            return True
    return False


def _offenders(tree, label=""):
    """R1/R4 的**判斷邏輯**（獨立成函式，才能被 R3 直接測）。

    回傳此模組的違規描述清單：
      · env 讀取帶了非空預設 ⇒ 環境未設定時仍有目標 ⇒ 有能力
      · 決定目標的 env 沒有被任何跳過閘門引用 ⇒ 預設會跑
    """
    offenders = []
    reads = _ws_target_env_reads(tree) + _http_target_env_reads(tree)
    if not reads:
        return offenders
    gated = _gate_env_names(tree)
    seen = set()
    for name, has_default, default in reads:
        key = (name, has_default, default)
        if key in seen:
            continue
        seen.add(key)
        if has_default and default not in (None, ""):
            offenders.append(
                f"{label}: env {name!r} 的讀取帶了非空預設 {default!r}"
                f" ⇒ 環境未設定時仍有目標（有能力）"
            )
        if name not in gated:
            offenders.append(
                f"{label}: env {name!r} 決定服務目標，"
                f"但沒有跳過閘門引用它 ⇒ 預設會跑"
            )
    return offenders


def test_ws_target_from_env_must_have_no_default_and_must_be_opt_in():
    """R1：ws 目標由 env 決定 ⇒ 無預設目標 ＋ 有跳過閘門引用同一 env。"""
    offenders = []
    for path in _modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:  # pragma: no cover
            offenders.append(f"{path}: 檔案無法解析（不得靜默跳過）: {exc}")
            continue
        offenders.extend(_offenders(tree, str(path.relative_to(TESTS_DIR.parent))))
    assert not offenders, (
        "以下模組把 websocket 目標交給環境變數決定，但沒有做到"
        "「無預設目標 ＋ 明確 skip 閘門」：\n  " + "\n  ".join(offenders)
    )


def test_http_target_from_env_must_have_no_default_and_must_be_opt_in():
    """R4：http(s) 目標由 env 決定 ⇒ 無預設目標 ＋ 有跳過閘門引用同一 env。

    涵蓋兩個 `/health` 探針（`httpx.get`）與 `urllib.request.urlopen`。
    """
    offenders = []
    scanned = 0
    for path in _modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:  # pragma: no cover
            offenders.append(f"{path}: 檔案無法解析（不得靜默跳過）: {exc}")
            continue
        reads = _http_target_env_reads(tree)
        if not reads:
            continue
        scanned += 1
        offenders.extend(_offenders(tree, str(path.relative_to(TESTS_DIR.parent))))
    assert scanned > 0, (
        "掃描器一個 http 目標都沒看到 —— 掃描範圍已損壞"
        "（負例型護欄不得靜默放行）"
    )
    assert not offenders, (
        "以下模組把 http(s) 目標交給環境變數決定，但沒有做到"
        "「無預設目標 ＋ 明確 skip 閘門」：\n  " + "\n  ".join(offenders)
    )


def test_identity_probe_is_genuinely_scanned_and_compliant():
    """R1 的證據：`tests/test_identity.py` 必須被**真正掃到**（綠燈不算證據）。

    舊版只追一層模組常數 ⇒ `WS_URL = (LIVE_BASE.replace(...))` 追不到
    `LIVE_BASE` 的 env 讀取 ⇒ 該檔是「**因為掃不到而通過**」，不是因為它真的
    合規。這裡釘住兩件事：
      · 掃描器**確實**看得到它的 env 讀取（遞迴生效）；
      · 它的 env 讀取**確實**合規（無非空預設 ＋ 有閘門引用同一 env）。
    """
    path = TESTS_DIR / "test_identity.py"
    assert path.exists(), "tests/test_identity.py 不存在"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reads = _ws_target_env_reads(tree) + _http_target_env_reads(tree)
    assert reads, (
        "掃描器**掃不到** tests/test_identity.py 的 env 讀取 —— "
        "它不是因為合規而通過，是因為掃不到而通過（綠燈不算證據）"
    )
    names = {name for name, _hd, _d in reads}
    assert "SOUL_OS_LIVE_SERVICE_BASE_URL" in names, names
    offenders = _offenders(tree, "tests/test_identity.py")
    assert not offenders, (
        "tests/test_identity.py 不合規:\n  " + "\n  ".join(offenders)
    )


def test_no_code_string_constant_contains_production_address():
    """R2：`tests/**` **全部** .py 檔的程式碼字串常數不得含生產服務位址。

    docstring 與註解不算（AST 上只取 `ast.Constant` 的 str，且排除 docstring 節點）；
    註解在 AST 上根本不存在。
    """
    offenders = []
    unparseable = []
    scanned = 0
    for path in _all_modules():
        rel = path.relative_to(TESTS_DIR.parent).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            unparseable.append(f"{rel}: {exc}")
            continue
        scanned += 1
        for lineno, value in _code_string_constants_with_lines(tree):
            match = ADDRESS_RE.search(value)
            if not match:
                continue
            if (rel, lineno) in ADDRESS_EXCEPTIONS:
                continue
            offenders.append(
                f"{rel}:{lineno}: 程式碼字串常數含生產服務位址 {match.group(0)!r}"
            )
    assert not unparseable, (
        "掃描器無法解析以下檔案（不得靜默跳過）:\n  " + "\n  ".join(unparseable)
    )
    assert scanned >= 100, (
        f"R2 只掃到 {scanned} 個檔（< 100）；掃描範圍可能已損壞"
        "（負例型護欄不得靜默放行）"
    )
    assert not offenders, (
        "tests/** 的程式碼字串常數不得含生產服務位址"
        "（目標必須由環境變數明確指定 ⇒ 能力移除，不是加條件比較）:\n  "
        + "\n  ".join(offenders)
    )


def test_address_pattern_detects_production_addresses_only():
    """位址樣式本身的牙齒（合成樣本）—— 護欄不能是盲腸。

    （位址以片段拼接 ⇒ 本檔不含該字面。）
    """
    assert ADDRESS_RE.search(PRODUCTION_ADDR), (
        "位址樣式抓不到生產位址 —— R2 已失效"
    )
    assert ADDRESS_RE.search("http://" + "127.0.0.1" + ":" + "8000/health"), (
        "位址樣式抓不到 127.0.0.1 形式的生產位址 —— R2 已失效"
    )
    assert not ADDRESS_RE.search("http://localhost" + ":" + "8765/health"), (
        "位址樣式誤判非生產埠 —— R2 會產生假紅"
    )


def test_address_exceptions_are_not_dead_rules():
    """R2 的例外必須仍然指向同一件事（死規則＝悄悄失效的護欄）。"""
    stale = []
    for (rel, lineno), reason in sorted(ADDRESS_EXCEPTIONS.items()):
        path = TESTS_DIR.parent / rel
        assert path.exists(), f"例外條目指向不存在的檔案: {rel}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _exception_still_applies(tree, lineno):
            stale.append(f"{rel}:{lineno} — {reason}")
    assert not stale, (
        "R2 例外表出現死規則（該 (檔案, 行號) 上已經沒有對應的東西；"
        "多半是行號漂移，必須重新對齊）:\n  " + "\n  ".join(stale)
    )


def _synthetic_ws_sample() -> str:
    """合成的違規樣本：**穿透遞迴追蹤**才抓得到。

    `BASE` 是 env 讀取；`URL = BASE.replace(...)` 是加工後的常數；呼叫端用 `URL`。
    只追一層的掃描器會**掃不到** env 讀取 ⇒ 這個樣本就是 R1 遞迴性的證據。
    （位址以片段拼接 ⇒ 本檔不含該字面。）
    """
    addr = "http://localhost" + ":" + "8000"
    return (
        "import os\n"
        "import websockets\n"
        "BASE = os.environ.get('SOUL_OS_LIVE_SERVICE_BASE_URL', '%s')\n"
        "URL = BASE.replace('http://', 'ws://') + '/ws'\n"
        "async def go():\n"
        "    async with websockets.connect(URL) as ws:\n"
        "        pass\n"
    ) % addr


def test_scanner_detects_a_synthetic_offender():
    """R3：掃描器自測 —— 合成的違規樣本必須被 R1 抓到（護欄不是盲腸）。"""
    sample = _synthetic_ws_sample()
    tree = ast.parse(sample)
    reads = _ws_target_env_reads(tree)
    assert reads, (
        "掃描器漏掉了 websocket 目標的 env 讀取 —— 護欄已失效"
        "（樣本必須穿透**遞迴**追蹤：URL → BASE → env 讀取）"
    )
    name, has_default, default = reads[0]
    assert name == "SOUL_OS_LIVE_SERVICE_BASE_URL", name
    assert has_default and default not in (None, ""), (
        "掃描器沒抓到『帶非空預設的 env 目標』—— 護欄已失效"
    )
    assert not _gate_env_names(tree), (
        "掃描器沒抓到『沒有跳過閘門』—— 護欄已失效"
    )
    # ★ 判斷層自測：合成樣本（帶非空預設 ＋ 沒有閘門）必須被**判為違規**。
    #   只測抽取不夠 —— 抽取正常但判斷被移除 ⇒ 護欄會變成盲腸。
    offenders = _offenders(tree, "synthetic")
    assert len(offenders) == 2, (
        f"合成樣本應被判出 2 條違規（帶非空預設 ＋ 缺閘門），實得 {offenders}"
    )
    assert any("非空預設" in o for o in offenders), offenders
    assert any("閘門" in o for o in offenders), offenders


def test_http_scanner_detects_a_synthetic_offender():
    """R4 的掃描器自測：http 目標的 env 讀取同樣抓得到（含遞迴）。"""
    addr = "http://localhost" + ":" + "8000"
    sample = (
        "import os\n"
        "import httpx\n"
        "BASE = os.environ.get('SOUL_OS_LIVE_SERVICE_BASE_URL', '%s')\n"
        "URL = BASE.rstrip('/')\n"
        "def go():\n"
        "    return httpx.get(URL + '/health', timeout=5)\n"
    ) % addr
    tree = ast.parse(sample)
    reads = _http_target_env_reads(tree)
    assert reads, "掃描器漏掉了 http 目標的 env 讀取 —— R4 已失效"
    name, has_default, default = reads[0]
    assert name == "SOUL_OS_LIVE_SERVICE_BASE_URL", name
    assert has_default and default not in (None, ""), (
        "掃描器沒抓到『帶非空預設的 http env 目標』—— R4 已失效"
    )
    offenders = _offenders(tree, "synthetic-http")
    assert len(offenders) == 2, (
        f"http 合成樣本應被判出 2 條違規，實得 {offenders}"
    )
