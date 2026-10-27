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

  R1（ws 目標由 env 決定 ⇒ 必須無預設目標 ＋ 必須有 skipif 引用同一 env）
      任何模組若把 **websocket 目標**交給環境變數決定：
        · 該 env 讀取**不得帶非空預設**（帶了 ⇒ 環境未設定時仍有目標 ⇒ 有能力）
        · 該模組必須有 `pytest.mark.skipif`，且其條件引用**同一個** env 變數
          ⇒ 環境未設定 ⇒ 測試 skip ⇒ 預設關閉
  R2 `tests/test_identity.py` 的**程式碼字串常數**不得含生產服務位址
      （docstring 與註解不算 —— 那些是在記錄「原本錯在哪」）
  R3 掃描器自測：合成的違規樣本必須被 R1 抓到（護欄自己不是盲腸）

🔺 本檔自身不得出現生產位址的**字面**（會被 tests/infra/test_no_production_spawn_guard.py
 的位址規則命中）⇒ 目標字串以片段拼接產生。

執行：
  .venv\Scripts\python.exe -m pytest tests\infra\test_live_service_probes_are_opt_in.py -q
"""
import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent

#: 生產服務位址（以片段拼接 ⇒ 本檔不含該字面，避免自我命中位址規則）。
PRODUCTION_ADDR = "localhost" + ":" + "8000"

#: 會對外連線的 websocket 進入點（AST 形狀：`websockets.connect(...)`）。
WS_CONNECT_FUNCS = {("websockets", "connect")}

#: 視為「env 讀取」的形狀。
ENV_GET = {("os", "environ", "get"), ("os", "getenv")}


def _modules():
    """掃描目標：tests/ 下所有 .py（排除本檔自身，避免自我指涉）。"""
    here = Path(__file__).resolve()
    return [p for p in sorted(TESTS_DIR.rglob("*.py")) if p.resolve() != here]


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
    """模組層級常數 → 其值的 AST 節點（供「目標由常數指派」時追一次）。"""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _ws_target_env_reads(tree):
    """找出所有 `websockets.connect(...)` 呼叫，其目標引數裡的 env 讀取。

    目標可能是直接運算式，也可能是**模組層級常數**（`URL = os.environ.get(...)`
    後 `websockets.connect(URL)`）⇒ 兩種都要追。
    """
    consts = _module_constants(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted(node.func) not in WS_CONNECT_FUNCS:
            continue
        args = list(node.args) + [kw.value for kw in node.keywords]
        for a in args:
            reads = _env_reads(a)
            if reads:
                out.extend(reads)
                continue
            if isinstance(a, ast.Name) and a.id in consts:
                out.extend(_env_reads(consts[a.id]))
    return out


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


def _code_string_constants(tree):
    """模組裡所有**非 docstring** 的字串常數（程式碼裡實際會用到的位址）。"""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def _offenders(tree, label):
    """R1 的**判斷邏輯**（獨立成函式，才能被 R3 直接測）。

    回傳此模組的違規描述清單：
      · env 讀取帶了非空預設 ⇒ 環境未設定時仍有目標 ⇒ 有能力
      · 決定 ws 目標的 env 沒有被任何 pytest.mark.skipif 引用 ⇒ 預設會跑
    """
    offenders = []
    reads = _ws_target_env_reads(tree)
    if not reads:
        return offenders
    gated = _skipif_env_names(tree)
    for name, has_default, default in reads:
        if has_default and default not in (None, ""):
            offenders.append(
                f"{label}: env {name!r} 的讀取帶了非空預設 {default!r}"
                f" ⇒ 環境未設定時仍有目標（有能力）"
            )
        if name not in gated:
            offenders.append(
                f"{label}: env {name!r} 決定 ws 目標，"
                f"但沒有 pytest.mark.skipif 引用它 ⇒ 預設會跑"
            )
    return offenders


def test_ws_target_from_env_must_have_no_default_and_must_be_opt_in():
    """R1：ws 目標由 env 決定 ⇒ 無預設目標 ＋ 有 skipif 引用同一 env。"""
    offenders = []
    for path in _modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        offenders.extend(_offenders(tree, str(path.relative_to(TESTS_DIR.parent))))
    assert not offenders, (
        "以下模組把 websocket 目標交給環境變數決定，但沒有做到"
        "「無預設目標 ＋ 明確 skipif 閘門」：\n  " + "\n  ".join(offenders)
    )


def test_identity_probe_code_has_no_hardcoded_production_address():
    """R2：tests/test_identity.py 的程式碼字串常數不得含生產服務位址。"""
    path = TESTS_DIR / "test_identity.py"
    assert path.exists(), "tests/test_identity.py 不存在"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = [v for v in _code_string_constants(tree) if PRODUCTION_ADDR in v]
    assert not bad, (
        "tests/test_identity.py 的程式碼裡又出現硬寫的生產服務位址："
        f"{bad} ⇒ 目標必須由環境變數明確指定（能力移除）"
    )


def test_scanner_detects_a_synthetic_offender():
    """R3：掃描器自測 —— 合成的違規樣本必須被 R1 抓到（護欄不是盲腸）。"""
    addr = "http://localhost" + ":" + "8000"
    sample = (
        "import os\n"
        "import websockets\n"
        "URL = os.environ.get('SOUL_OS_LIVE_SERVICE_BASE_URL', '%s')\n"
        "async def go():\n"
        "    async with websockets.connect(URL) as ws:\n"
        "        pass\n"
    ) % addr
    tree = ast.parse(sample)
    reads = _ws_target_env_reads(tree)
    assert reads, "掃描器漏掉了 websocket 目標的 env 讀取 —— 護欄已失效"
    name, has_default, default = reads[0]
    assert name == "SOUL_OS_LIVE_SERVICE_BASE_URL", name
    assert has_default and default not in (None, ""), (
        "掃描器沒抓到『帶非空預設的 env 目標』—— 護欄已失效"
    )
    assert not _skipif_env_names(tree), (
        "掃描器沒抓到『沒有 skipif 閘門』—— 護欄已失效"
    )
    # ★ 判斷層自測：合成樣本（帶非空預設 ＋ 沒有 skipif）必須被**判為違規**。
    #   只測抽取不夠 —— 抽取正常但判斷被移除 ⇒ 護欄會變成盲腸。
    offenders = _offenders(tree, "synthetic")
    assert len(offenders) == 2, (
        f"合成樣本應被判出 2 條違規（帶非空預設 ＋ 缺 skipif），實得 {offenders}"
    )
    assert any("非空預設" in o for o in offenders), offenders
    assert any("skipif" in o for o in offenders), offenders
