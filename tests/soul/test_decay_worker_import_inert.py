"""tests/soul/test_decay_worker_import_inert.py — 證明 1：接縫匯入惰性。

## 被證明的命題

**匯入 `src.agent.decay_worker` 不得產生任何副作用。**

具體地說，在一個**全新子直譯器**中，於**設定好拋棄式資料根之後**匯入該模組，
必須觀察到：

  1. `run_server` **未**被連帶匯入（`scripts/run_server.py` 匯入時會開
     faulthandler 檔、`data_root()`、mkdir…）。
  2. `src.agent.emotion` **未**被連帶匯入 —— 這是本證明最關鍵的一條：
     `import emotion` 在**匯入當下**就做真實檔案 I/O
     （`src/agent/emotion.py:1190` → `:363` mkdir / `:364` sqlite3.connect /
     `:374-381` CREATE TABLE / `:398` commit）。
  3. 拋棄式資料根下**零檔案**被建立。
  4. **零** SQLite 連線、**零** DDL。
  5. **零** `.env` 載入。
  6. **零**背景 task（沒有 event loop 被啟動）。
  7. **零**網路 socket。

## 🔴 為什麼必須是「全新子直譯器」而不是行程內 `sys.modules` 檢查

行程內檢查會被**測試自身的 import 歷史**污染：pytest 收集階段早就把
`src.agent.emotion` 之類的模組載進 `sys.modules` 了。在那個行程裡斷言
「emotion 不在 sys.modules」是**必然假紅**；反過來說，若某個實作真的在
匯入接縫時連帶匯入了 emotion，只要**別的測試**先 import 過，行程內檢查就會
**必然假綠**。只有「全新子直譯器 + 乾淨的 import 紀錄」能同時避開這兩種錯誤。

## 隔離紀律

  - 子行程的 `SOUL_OS_DATA_DIR` **在子行程啟動前**就由父行程環境設定指向
    `tmp_path` 底下 —— 這樣即使真有東西呼叫 `data_root()`
    （`src/paths.py:26,40-48` 的模組層級快取），落點也是 tmp，
    **不可能**是 repo 的 `data/`。
  - 子行程環境**剝除生產憑證**（provider API keys / Telegram / VC token）。
  - 全程：**不啟動服務、不綁任何埠、不殺任何行程、不碰 `data/**`**。

跑法：

    .venv\\Scripts\\python.exe -m pytest -q tests/soul/test_decay_worker_import_inert.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: 子行程必須**看不到**的環境變數（生產憑證 / 金鑰 / 端點）。
_CREDENTIAL_PREFIXES = (
    "OPENAI_",
    "ANTHROPIC_",
    "DEEPSEEK_",
    "GEMINI_",
    "GOOGLE_",
    "OLLAMA_",
    "OPENROUTER_",
    "VC_INBOUND_",
    "INTERNAL_VC_",
    "TELEGRAM_",
    "TG_",
    "SMTP_",
)


#: 子行程探針。全部邏輯在子行程內執行，結果以 JSON 從 stdout 取回。
#:
#: 🔴 關鍵順序：`SOUL_OS_DATA_DIR` 由**父行程**設定在 `env` 裡（不是在此
#: 設定）—— `src/paths.py` 的 `_DATA_ROOT` 是**模組層級快取**，一旦任何模組
#: 在設定前先呼叫過 `data_root()`，快取就定型了。由父行程設定環境變數可以
#: 保證「子行程的第一行 Python 之前，根就已經指向 tmp」。
_PROBE = r'''
import json, os, socket, sys, sqlite3, builtins, asyncio
from pathlib import Path

ROOT = Path(sys.argv[1])
DATA = Path(sys.argv[2])
sys.path.insert(0, str(ROOT))

result = {}
before = set(sys.modules)

# 記錄 sqlite3.connect（DDL 的必要前置）
connects = []
_real_connect = sqlite3.connect
def _spy_connect(*a, **kw):
    connects.append(str(a[0]) if a else "<kwargs>")
    return _real_connect(*a, **kw)
sqlite3.connect = _spy_connect

# 記錄 socket 建立
sockets = []
_real_socket = socket.socket
class _SpySocket(_real_socket):
    def __init__(self, *a, **kw):
        sockets.append(1)
        super().__init__(*a, **kw)
socket.socket = _SpySocket

# 記錄寫入模式開檔
CREATED = []
_real_open = builtins.open
def _spy_open(file, mode="r", *a, **kw):
    try:
        if any(c in str(mode) for c in ("w", "a", "x", "+")):
            CREATED.append(str(file))
    except Exception:
        pass
    return _real_open(file, mode, *a, **kw)
builtins.open = _spy_open

# 記錄 event loop 建立
loop_created = []
_real_new_loop = asyncio.new_event_loop
def _spy_new_loop(*a, **kw):
    loop_created.append(1)
    return _real_new_loop(*a, **kw)
asyncio.new_event_loop = _spy_new_loop
_real_run = asyncio.run
def _spy_run(*a, **kw):
    loop_created.append(1)
    return _real_run(*a, **kw)
asyncio.run = _spy_run

# 記錄 dotenv 載入
dotenv_loads = []
try:
    import dotenv as _dotenv
    _real_load = _dotenv.load_dotenv
    def _spy_load(*a, **kw):
        dotenv_loads.append(1)
        return _real_load(*a, **kw)
    _dotenv.load_dotenv = _spy_load
except Exception:
    pass

# ── 被測動作：匯入接縫 ────────────────────────────────────
import src.agent.decay_worker as dw

after = set(sys.modules)

result["data_root_env"] = os.environ.get("SOUL_OS_DATA_DIR")
result["data_root_is_tmp"] = (
    result["data_root_env"] is not None
    and Path(result["data_root_env"]).resolve() == DATA.resolve()
)

result["run_server_imported"] = any("run_server" in m for m in after)
result["emotion_imported"] = "src.agent.emotion" in after

if DATA.exists():
    result["files_under_root"] = sorted(
        str(p.relative_to(DATA)) for p in DATA.rglob("*") if p.is_file()
    )
else:
    result["files_under_root"] = []

result["sqlite_connects"] = connects
result["dotenv_loads"] = dotenv_loads
result["loop_created"] = loop_created
result["sockets"] = len(sockets)

# 正向控制：接縫真的被載入了
result["seam_loaded"] = "src.agent.decay_worker" in after
result["seam_has_cancel"] = hasattr(dw, "_DECAY_WORKER_CANCEL")
result["seam_task_is_none"] = getattr(dw, "_DECAY_WORKER_TASK", "MISSING") is None
result["seam_cancel_not_set"] = not dw._DECAY_WORKER_CANCEL.is_set()

# 結構性鎖：模組層級 import 集合
import ast as _ast
_seam_src = (ROOT / "src" / "agent" / "decay_worker.py").read_text(encoding="utf-8")
_seam_tree = _ast.parse(_seam_src)
_top_imports = []
for _n in _seam_tree.body:
    if isinstance(_n, _ast.Import):
        _top_imports.extend(a.name for a in _n.names)
    elif isinstance(_n, _ast.ImportFrom):
        _top_imports.append(_n.module or "")
result["seam_top_level_imports"] = sorted(set(_top_imports))

result["created_by_open"] = CREATED
result["new_modules"] = sorted(after - before)

print("INERT_PROBE " + json.dumps(result), flush=True)
'''


def _child_env(data_root: Path) -> dict:
    """子行程環境：拋棄式根 + 剝除生產憑證。"""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(_CREDENTIAL_PREFIXES):
            env.pop(key, None)
    env["SOUL_OS_DATA_DIR"] = str(data_root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["INTIMACY_DECAY_ENABLED"] = ""
    return env


def _run_probe(tmp_path: Path) -> dict:
    """跑一次全新子直譯器探針，回傳解析後的 JSON。"""
    data_root = tmp_path / "disposable_root"
    data_root.mkdir(parents=True, exist_ok=True)

    probe = tmp_path / "_inert_probe.py"
    probe.write_text(_PROBE, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(probe), str(REPO_ROOT), str(data_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=_child_env(data_root), cwd=str(REPO_ROOT),
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        "子行程探針失敗（匯入接縫時就已拋錯 ⇒ 匯入不惰性）:\n" + combined[-4000:]
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith("INERT_PROBE "):
            return json.loads(line[len("INERT_PROBE "):])
    raise AssertionError("子行程沒有輸出 INERT_PROBE 結果:\n" + combined[-4000:])


@pytest.fixture(scope="module")
def probe_result(tmp_path_factory) -> dict:
    """整個模組跑**一次**子行程（昂貴；結果唯讀，可安全共用）。"""
    return _run_probe(tmp_path_factory.mktemp("inert_probe"))


# ─────────────────────────────────────────────────────────────
# 正向控制
# ─────────────────────────────────────────────────────────────


def test_probe_actually_loaded_the_seam(probe_result: dict) -> None:
    """🔴 前提：接縫**真的**被載入了，且資料根在匯入前就是 tmp。

    沒有這一條，下面每一條「沒有副作用」的斷言都可能只是因為
    「什麼都沒做」而空洞地通過。
    """
    assert probe_result["seam_loaded"] is True, (
        "子行程沒有載入 src.agent.decay_worker —— 探針失效（假綠）"
    )
    assert probe_result["seam_has_cancel"] is True
    assert probe_result["seam_task_is_none"] is True, (
        "模組層級的 _DECAY_WORKER_TASK 必須是 None（未被排入）"
    )
    assert probe_result["seam_cancel_not_set"] is True, (
        "模組層級取消旗標不得在匯入時就被 set"
    )
    assert probe_result["data_root_is_tmp"] is True, (
        "拋棄式資料根未生效 —— 後續『零檔案』斷言不成立（可能落到 repo data/）"
    )


# ─────────────────────────────────────────────────────────────
# 證明 1 主體
# ─────────────────────────────────────────────────────────────


def test_import_does_not_pull_in_run_server(probe_result: dict) -> None:
    """(1) 匯入接縫不得連帶匯入 `run_server`。"""
    assert probe_result["run_server_imported"] is False, (
        "🔴 匯入 src.agent.decay_worker 竟連帶匯入了 run_server "
        "（服務入口檔的模組層級副作用就跟著進來了）"
    )


def test_import_does_not_pull_in_emotion(probe_result: dict) -> None:
    """(2) 🔴 最關鍵：匯入接縫不得連帶匯入 `src.agent.emotion`。"""
    assert probe_result["emotion_imported"] is False, (
        "🔴 匯入 src.agent.decay_worker 竟連帶匯入了 src.agent.emotion —— "
        "這會在 import 當下做真實檔案 I/O（mkdir / connect / DDL / commit）"
    )


def test_import_creates_no_files_under_root(probe_result: dict) -> None:
    """(3) 拋棄式資料根下必須**零檔案**。"""
    assert probe_result["files_under_root"] == [], (
        "🔴 匯入接縫竟在資料根下建立了檔案："
        f"{probe_result['files_under_root']}"
    )
    assert probe_result["created_by_open"] == [], (
        "🔴 匯入接縫期間有檔案被以寫入模式開啟："
        f"{probe_result['created_by_open']}"
    )


def test_import_opens_no_sqlite_and_runs_no_ddl(probe_result: dict) -> None:
    """(4) 零 SQLite 連線（DDL 必然需要連線 ⇒ 連線為零即 DDL 為零）。"""
    assert probe_result["sqlite_connects"] == [], (
        "🔴 匯入接縫竟建立了 SQLite 連線："
        f"{probe_result['sqlite_connects']}"
    )


def test_import_loads_no_dotenv(probe_result: dict) -> None:
    """(5) 零 `.env` 載入。"""
    assert probe_result["dotenv_loads"] == [], "🔴 匯入接縫竟載入了 .env"


def test_import_creates_no_background_task(probe_result: dict) -> None:
    """(6) 零背景 task / 零 event loop。"""
    assert probe_result["loop_created"] == [], (
        "🔴 匯入接縫竟建立了 event loop（背景 task 的前置）"
    )


def test_import_opens_no_network_socket(probe_result: dict) -> None:
    """(7) 零網路 socket。"""
    assert probe_result["sockets"] == 0, (
        f"🔴 匯入接縫竟建立了 {probe_result['sockets']} 個 socket"
    )


def test_seam_top_level_imports_are_allowlisted(probe_result: dict) -> None:
    """(8) 模組層級 import 集合必須落在白名單內（結構性鎖）。

    行為斷言可能被「這次剛好沒觸發」蒙混；本條直接鎖住原始碼的 import 集合。
    允許：`__future__` / `asyncio` / `logging` / `threading`。
    """
    allowed = {"__future__", "asyncio", "logging", "threading"}
    actual = set(probe_result["seam_top_level_imports"])
    extra = actual - allowed
    assert not extra, (
        f"🔴 decay_worker.py 模組層級出現白名單外的 import：{sorted(extra)}"
        "（工單 §4.1 只允許 asyncio / logging / threading）"
    )
    assert {"asyncio", "logging", "threading"} <= actual, (
        f"decay_worker.py 缺少必要的模組層級 import：實得 {sorted(actual)}"
    )
