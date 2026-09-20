# tests/soul/test_life_thread_sim_m2_catchup.py
# M2-CATCHUP-1 / Phase 1 — 「存量終態線頭」的沉澱追趕：**只有 harness，零 `src/**`**。
"""M2-CATCHUP-1 Phase 1 — 存量終態（`status ∈ {completed, abandoned}` 且
`dissolved_at is null`）的寫回語意，以**假時鐘金樣平面**釘死。

契約新條款（本票要釘死的唯一一條）
──────────────────────────────────
掃 `status ∈ {completed, abandoned}` 且 `dissolved_at is null` 的**存量終態**線頭，
走**既有** `append_dissolved`，仍守 **at-most-once** 與 **`DISSOLVE_MAX_PER_DAY` (=3)**；
軟封存預設關。

為何存量終態在生產上「不會」被沉澱（背景事實，本票只釘行為不改 `src`）
────────────────────────────────────────────────────────────────────
`life_thread_origins.apply_actions` 只在該輪有線頭**轉入終態**時才呼叫 `dissolve_hook`，
而 M1 `allowed_transition()` 對終態來源**恆為 `False`**（終態不可再轉移）⇒ 金樣 B 內
既有的終態線頭（akane `2917e901`，`status=completed`、`dissolved_at=null`）**永遠不會**
觸發 hook。缺的只是「把候選交給 hook」——決策層**已經**吃存量（orchestrator 對 M2 傳
`all_threads`）。本票以**直接呼叫真正的 `build_dissolve_hook()`** 來量測「一旦交棒，
既有寫回路徑會做什麼」。

受測模組（**全部唯讀呼叫，本票不改一行**）
─────────────────────────────────────────
- `src/soul/life_thread_consolidation_wiring.py`（旗標 ＋ 同步 hook ＋ 背景沉澱）
- `src/soul/life_thread_dissolution{,_exec}.py`（M2 裁決 ＋ 執行層）
- `src/soul/life_threads.py`（M1 寫回語意）

平面不變量（沿用 SIM-TIME-001／M2-WRITEBACK-SIM-1 的同一套 primitive）
────────────────────────────────────────────────────────────────────
1. **假時鐘**：hook 內部的時鐘接縫 `w._now` 由測試換成可變 `FakeClock`；連續 slot
   **一律直接換 `now` 再呼叫** ⇒ **0 `time.sleep`／0 真實等待**。唯一的
   `await asyncio.sleep(0)` 是**讓 done-callback 跑完的 event-loop 讓渡**（0 秒 wall-clock）。
2. **記憶 fork**：金樣 → `SOUL_OS_DATA_DIR` 隔離目錄（`sim_root`）＋ `reset_data_root()`；
   生產 `data/**` 全程唯讀（本檔以 `lt.life_threads_path` 斷言落點不在 repo `data/`）。
3. **零 LLM**：沉澱 LLM 走 `w._resolve_llm_proxy` 的替身（`_RecordingProxy`，記憶體內回應）；
   另裝「真實 proxy 探針」（`lt_origins._find_llm_proxy` ⇒ 命中即紅燈）＋
   `socket.create_connection` 攔阻 ⇒ **0 真實外呼**（死代理 `HTTP(S)_PROXY=127.0.0.1:1` 下亦全綠）。
4. **SAGE 一律 stub**：`w._write_fact` 以替身取代 ⇒ **不建 store、不寫真記憶庫**
   （`w._WRITERS` 全程為空）；M1 回填（`w._append_dissolved`）走**真實**
   `lt.append_dissolved` —— 那正是本票要觀測的寫回路徑。

四條斷言（本票唯一範圍）
────────────────────────
- **M2C-1 存量終態 → 恰一次寫回**：對金樣 B 內既有的終態線頭直接呼叫 hook ⇒ M1 檔內該線頭
  出現**恰好一次**非 null `dissolved_at` ＋ **非空** `sage_fact_id`、**恰一筆** `dissolved`
  事件；沉澱 LLM 恰 1 次、SAGE 恰 1 次。
- **M2C-2 下一 slot 再打一次 ⇒ `already_dissolved`、0 LLM**：`should_mutate=False`、
  `skipped == "already_dissolved"`、stub 呼叫數不變、M1 檔**逐位元不變**。
- **M2C-3 同日預算用盡即停**：同日先做滿 3 次成功溶解（`DISSOLVE_MAX_PER_DAY = 3`），
  第 4 條存量終態 ⇒ `skipped_budget`（`reason=budget_exhausted`）、**0 LLM**、**0 寫入**
  （`dissolved_at` 保持 null）；budget 3/3。
- **M2C-4 軟封存預設關**：對**非終態**（`active`／`dormant`）線頭 ⇒ 不溶解（0 LLM／0 寫入）；
  `allow_soft_archive=True` **對照組**證明擋下它的正是「預設關」這個旗標。

🔴 兩處**已查證的票面校正**（不改 `src`，只登記）
──────────────────────────────────────────────────
- **校正 A（M2C-3 的 3 條「存量終態」來源）**：金樣 B 內**只有 1 條**存量終態線頭
  （akane `2917e901`；實測全樣 10 個 agent／39 列，`status ∈ {completed, abandoned}` 者恰 1 列），
  金樣 A（`20260919_0800`）為 **0 條** ⇒ 「同日先做滿 3 次」無法只用金樣既有終態湊齊。
  本檔改以**真 M1 API**（`create_thread` ＋ `append_transition(→completed)`）合成另外 3 條
  **同構的存量終態**（`status=completed`、`dissolved_at=null`、**非本輪轉入**），
  再以金樣那條當**第 4 條**——四條都是同一種「存量終態」，語意一致。
- **校正 B（M2C-4 的 `allow_soft_archive` 對照組）**：該 seam **可注入**
  （`w._evaluate` 是模組 docstring 明列的接縫），但**注入後仍不會沉澱**：
  M2 對超限 active 線頭回 `should_mutate=True`、`target_status=dormant`，而執行層的終態閘門
  `TERMINAL_TARGET_STATUSES = ("completed", "abandoned")` **不含 `dormant`** ⇒
  執行層回 `skipped_not_terminal`（`reason="target_status=dormant"`）、**仍然 0 LLM／0 寫入**。
  故本檔的對照組斷言「裁決翻轉」＋「執行層照樣擋下」，**不**宣稱「才溶」——
  這是實測值，不是預測值。

跨檔 import 的取捨（**沿用 M2-WRITEBACK-SIM-1 的決定**）
────────────────────────────────────────────────────────
`tests/soul/` 無 `__init__.py`，故**不**跨檔 import 另一個測試模組（模組名解析依賴 pytest
的 rootdir 插入順序，且會與 SIM-TIME-001 的 autouse 平面不變量糾纏）。本檔改為
**複製最小必要 primitive**（金樣讀取／落地、假時鐘、`ScriptedLLM` 不需要、proxy 探針、
socket 攔阻），旗標**預設關、各測試顯式開**。

執行層詞彙的取用（**0 新 importer**）
────────────────────────────────────
`test_life_thread_dissolution_exec.py` 的 T53／T54 把「全庫 import 執行層的檔案」釘成
**精確集合等值**（1 生產檔 ＋ 2 測試檔）。本檔**不**直接 import 執行層，改取接線模組
已持有的別名（`ex = w.lt_exec`）⇒ 既有護欄逐字不動、**0 新 importer**。

觀測輸出
────────
`[M2-CATCHUP-OBS]` 開頭的離散觀測行（`pytest -s` 可見）只含 id／狀態／計數／憑據，
**永不**含 prompt 或敘述原文（沿用執行層規則 9 的隱私口徑）。
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_consolidation_wiring as w  # noqa: E402
from src.soul import life_thread_dissolution as lt_diss  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

#: 執行層的**觀測詞彙**（狀態碼／結果型別）。
#:
#: 🔴 本檔**刻意不**寫 `import src.soul.life_thread_dissolution_exec`：T53／T54 把
#: 「全庫 import 執行層的檔案」釘成**精確集合等值**（1 個生產檔
#: `life_thread_consolidation_wiring.py` ＋ 2 個測試檔）⇒ 多一個 importer 就紅。
#: 改由接線模組**已持有**的模組別名取用 ⇒ 0 新 importer、既有護欄逐字不動。
ex = w.lt_exec
assert ex.__name__ == "src.soul.life_thread_dissolution_exec"

# ══════════════════════════════════════════════════════════════
# 常數：金樣、假時鐘、觀測標的
# ══════════════════════════════════════════════════════════════

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"
FIXTURE_B = FIXTURES_DIR / "life_thread_snapshot_20260920_0800.json"
LIFE_THREADS_FILENAME = "life_threads.jsonl"

AGENT = "agent_akane"
#: 金樣 B 內 akane 的**現成存量終態**（`status=completed`、`dissolved_at=null`、
#: **非本輪轉入**）—— 本票 M2C-1／M2C-2／M2C-3 的唯一寫回標的。
AKANE_TERMINAL_CANDIDATE = "2917e901-757b-4d4c-9b2e-8188413e5449"
#: 金樣 B 內 akane 的 **active** 線頭（M2C-4 的非終態樣本）。
AKANE_ACTIVE_THREAD = "0ce7e589-eebc-4203-9c48-626f65819433"

#: 假時鐘：09/20 08:00（morning）與同日的下一個 slot 09/20 22:00（night）。
NOW_B = datetime(2026, 9, 20, 8, 0, 0, tzinfo=LOCAL_TZ)
NOW_B_NEXT = datetime(2026, 9, 20, 22, 0, 0, tzinfo=LOCAL_TZ)

#: 執行層「日」預算鍵的釘死時刻（**假時鐘**；生產預設是 wall clock）。
#: M2C-3 的四次請求必須落在**同一個預算日** ⇒ 釘死才具決定性（否則跨 UTC 午夜會 flake）。
BUDGET_DAY_UTC = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

_SOUL_STUB = "（模擬人格語境；M2-CATCHUP-1 固定字串，不讀生產 persona）"
_NARRATIVE = "她把這件事收好了：不是勝利，是終於可以放下。"

#: 沉澱 LLM 的預錄回覆（契約 §3.2：`{"dissolution", "meaning_kind"}`）。
DISSOLUTION_JSON = json.dumps(
    {"dissolution": _NARRATIVE, "meaning_kind": "relief"}, ensure_ascii=False
)

#: 日預算上限的**票面值**（M2C-3 逐字釘死：執行層與決策層必須同值）。
DISSOLVE_MAX_PER_DAY = 3

#: 合成線頭的固定字串（逐字沿用 M2-WRITEBACK-SIM-1 已驗收的合法值）。
_SYNTH_TITLE = "合成的待完成線頭"
_SYNTH_NARRATIVE = "她打算把這件事收尾。"


def _obs(label: str, **fields: Any) -> None:
    """離散觀測行（只記 id／狀態／計數／憑據；**永不記 prompt 或敘述原文**）。"""
    pairs = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[M2-CATCHUP-OBS] {label} {pairs}")


def _short(thread_id: str) -> str:
    return thread_id[:8]


# ══════════════════════════════════════════════════════════════
# 金樣：讀取、落地（記憶 fork）、觀測輔助
# ══════════════════════════════════════════════════════════════


def load_fixture(path: Path = FIXTURE_B) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def materialize(snapshot: Dict[str, Any], data_root: Path) -> Path:
    """把金樣複製到隔離資料根（`<root>/soul/<agent>/life_threads.jsonl`）。"""
    soul_dir = data_root / "soul"
    for agent_id, rows in snapshot["agents"].items():
        target = soul_dir / agent_id
        target.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        (target / LIFE_THREADS_FILENAME).write_text(text, encoding="utf-8")
    return soul_dir


def _agent_file(data_root: Path, agent_id: str = AGENT) -> Path:
    return data_root / "soul" / agent_id / LIFE_THREADS_FILENAME


def _read_text(data_root: Path, agent_id: str = AGENT) -> str:
    return _agent_file(data_root, agent_id).read_text(encoding="utf-8")


def local_lines(data_root: Path, agent_id: str = AGENT) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in _read_text(data_root, agent_id).splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def rows_for(data_root: Path, thread_id: str, agent_id: str = AGENT) -> List[Dict[str, Any]]:
    return [r for r in local_lines(data_root, agent_id) if r.get("thread_id") == thread_id]


def dissolved_rows(data_root: Path, agent_id: str = AGENT) -> List[Dict[str, Any]]:
    return [r for r in local_lines(data_root, agent_id) if r.get("event_type") == "dissolved"]


def rows_with_dissolved_at(data_root: Path, thread_id: str) -> List[Dict[str, Any]]:
    """該線頭所有**帶非 null `dissolved_at`** 的列（本票「恰好一次」的觀測面）。"""
    return [r for r in rows_for(data_root, thread_id) if r.get("dissolved_at") is not None]


def new_terminal_thread(now: datetime, to_status: str = "completed") -> str:
    """用**真 M1 API** 合成一條**存量終態**線頭（`dissolved_at=null`，非本輪轉入）。

    `create_thread`（`active`）→ `append_transition(→已完成/已放棄)`：兩步都是生產 API，
    **不手寫 JSONL**、**不偽造 fold 勝者**。容量上可行：終態不佔 `active` 額度，
    故可依序建立多條（每次 `create` 時 `active_count` 皆未達上限）。
    """
    thread_id = lt.create_thread(
        AGENT,
        _SYNTH_TITLE,
        _SYNTH_NARRATIVE,
        "necessity_driven",
        check_after_ts=(now - timedelta(hours=1)).astimezone(timezone.utc).isoformat(),
    )
    assert thread_id, "合成線頭失敗（M1 容量或參數拒絕）"
    assert lt.append_transition(AGENT, thread_id, to_status) is True, "轉入終態被拒"
    state = lt.get_state(AGENT, thread_id)
    assert state is not None and state["status"] == to_status, state
    assert state["dissolved_at"] is None and state["sage_fact_id"] is None
    return thread_id


def new_dormant_thread(now: datetime) -> str:
    """用**真 M1 API** 合成一條 `dormant` 線頭（M2C-4 的第二個非終態樣本）。"""
    thread_id = lt.create_thread(
        AGENT,
        _SYNTH_TITLE,
        _SYNTH_NARRATIVE,
        "necessity_driven",
        check_after_ts=(now - timedelta(hours=1)).astimezone(timezone.utc).isoformat(),
    )
    assert thread_id, "合成線頭失敗（M1 容量或參數拒絕）"
    assert lt.append_transition(AGENT, thread_id, "dormant") is True, "轉入 dormant 被拒"
    assert lt.get_state(AGENT, thread_id)["status"] == "dormant"
    return thread_id


def make_thread_over_age(data_root: Path, thread_id: str, now: datetime) -> None:
    """把該 active 線頭推過 advisory **兩軸**門檻（age 19 天、idle 8 天）。

    **只動隔離副本**。**必須改「該線頭的所有列」**（理由同 M2-WRITEBACK-SIM-1：只改
    fold 勝者會把它的 `updated_at` 推舊，反而讓另一列變成勝者）。**單調安全**：時間只會
    往前 ⇒ 對「假時鐘」與 wall clock 都恆為超限，裁決不隨真實日期漂移。
    """
    created = (now - timedelta(days=19)).astimezone(timezone.utc).isoformat()
    updated = (now - timedelta(days=8)).astimezone(timezone.utc).isoformat()

    rows = local_lines(data_root)
    touched = 0
    for row in rows:
        if row.get("thread_id") != thread_id:
            continue
        row["created_at"] = created
        row["updated_at"] = updated
        row["check_after_ts"] = None  # 只留 advisory 軸
        touched += 1
    assert touched > 0, f"線頭不存在：{thread_id}"
    _agent_file(data_root).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    state = lt.get_state(AGENT, thread_id)
    assert state["status"] == "active"
    # 前置自證：**真的**已越過 advisory 門檻（由真 M2 判定，不是測試端假設）。
    view = lt_diss.evaluate_thread_dissolution(state, now, agent_id=AGENT)
    assert view.should_mutate is False
    assert view.reason.value == "time_horizon_exceeded", view.reason
    assert view.extra_metadata["advisory"] is True


# ══════════════════════════════════════════════════════════════
# fixtures：隔離資料根、平面護欄、行程級狀態、假時鐘、M2 監看
# ══════════════════════════════════════════════════════════════


@pytest.fixture()
def sim_root(tmp_path, monkeypatch):
    """模擬資料根：金樣副本的唯一落點（生產 `data/**` 全程唯讀）。"""
    root = tmp_path / "sim_root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(root))
    reset_data_root()
    try:
        yield root
    finally:
        reset_data_root()


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """清空**行程級**狀態 ＋ 斷言 0 背景任務殘留（跨測試不得互相污染）。"""
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()
    yield
    assert w.pending_task_count() == 0, "測試結束仍有未清理的背景沉澱任務"
    w._BACKGROUND_TASKS.clear()
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()


class LiveLLMProbe:
    """真實 proxy 探針：命中即紅燈（本檔任何路徑都不得取得真 proxy）。"""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def __call__(self) -> Any:
        self.calls.append("_find_llm_proxy")
        return None


@pytest.fixture(autouse=True)
def _sim_plane_guards(monkeypatch):
    """平面護欄：0 真實 LLM、0 連外、人格語境固定、旗標**預設關**（各測試顯式開）。

    `socket.create_connection` 攔成 AssertionError（httpx／httpcore／urllib 的實際連外入口）；
    **不攔** `socket.socket.connect`（Windows 的 Proactor loop 以 `socketpair()` 建 self-pipe）。
    """
    probe = LiveLLMProbe()
    monkeypatch.setattr(lt_origins, "_find_llm_proxy", probe, raising=True)
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: _SOUL_STUB)
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "")
    monkeypatch.setenv("LIFE_THREAD_BOOTSTRAP_ENABLED", "")

    def _blocked_connect(*args, **kwargs):
        raise AssertionError("M2-CATCHUP-1：平面內不得連外（socket.create_connection 已攔）")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect, raising=True)

    yield probe
    assert probe.calls == [], f"路徑碰到真實 LLM proxy：{probe.calls}"


class FakeClock:
    """hook 內部時鐘（`w._now`）的替身：**直接換 `moment`** ⇒ 0 sleep、0 真實等待。"""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture()
def fake_clock(monkeypatch):
    """把 `w._now` 換成可變假時鐘（連續 slot ＝ 換 `moment` 再呼叫）。"""
    clock = FakeClock(NOW_B)
    monkeypatch.setattr(w, "_now", clock, raising=True)
    yield clock


class _RecordingProxy:
    """沉澱 LLM 的 `LLMProxy` 替身（**0 真實 LLM**）：記錄每次 `generate_text` 的 kwargs。"""

    def __init__(self, text: str = DISSOLUTION_JSON) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.text = text

    async def generate_text(self, **kwargs) -> Optional[str]:
        self.calls.append(dict(kwargs))
        return self.text

    @property
    def call_count(self) -> int:
        return len(self.calls)


class SageStub:
    """SAGE 落地替身（**不得寫真記憶庫**）：只記錄 fact 並回遞增的 fact_id。"""

    def __init__(self) -> None:
        self.facts: List[Dict[str, Any]] = []
        self.agents: List[str] = []

    def __call__(self, fact: Dict[str, Any], agent_id: str) -> str:
        self.facts.append(dict(fact))
        self.agents.append(agent_id)
        return f"fact-sim-{len(self.facts):04d}"

    @property
    def call_count(self) -> int:
        return len(self.facts)

    @property
    def fact_ids(self) -> List[str]:
        return [f"fact-sim-{i:04d}" for i in range(1, len(self.facts) + 1)]


@pytest.fixture()
def writeback_seams(monkeypatch):
    """把兩條 seam 換成零 LLM／零 SAGE 替身，回傳 `(proxy, sage)`。

    - `w._resolve_llm_proxy` ⇒ 沉澱 LLM 替身（**真實** `w._make_llm_call` adapter 仍被走過）。
    - `w._write_fact` ⇒ SAGE 替身（不建 store、不寫真記憶庫）。
    - `w._append_dissolved` **不換**：M1 回填走真實 `lt.append_dissolved`（本票的觀測面）。
    """
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy, raising=True)
    sage = SageStub()
    monkeypatch.setattr(w, "_write_fact", sage, raising=True)
    return proxy, sage


class M2Spy:
    """M2 單筆入口監看器：包住**真的** `w._evaluate`（hook 用的那一條），記下離散裁決值。

    **不是**測試端模型：`spy` 原封不動轉呼叫真 `w._evaluate`（其內部再呼叫真
    `evaluate_thread_dissolution`），只做觀測（比照 M2-WRITEBACK-SIM-1 的 `M2Spy`）。
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rounds: List[Dict[str, Any]] = []
        real = w._evaluate

        def spy(thread, now, agent_id):
            evaluation = real(thread, now, agent_id)
            extra = evaluation.extra_metadata or {}
            target = getattr(evaluation, "target_status", None)
            reason = getattr(evaluation, "reason", None)
            self.rounds.append(
                {
                    "thread_id": evaluation.thread_id,
                    "should_mutate": evaluation.should_mutate,
                    "target_status": getattr(target, "value", target),
                    "reason": getattr(reason, "value", reason),
                    "skipped": extra.get("skipped"),
                    "advisory": extra.get("advisory"),
                    "prompt_chars": len(evaluation.reflection_prompt)
                    if isinstance(evaluation.reflection_prompt, str)
                    else 0,
                }
            )
            return evaluation

        monkeypatch.setattr(w, "_evaluate", spy, raising=True)

    def last(self, thread_id: str) -> Dict[str, Any]:
        assert self.rounds, "M2 接縫未被呼叫"
        for record in reversed(self.rounds):
            if record["thread_id"] == thread_id:
                return record
        raise AssertionError(f"M2 未評估 {thread_id}：{[r['thread_id'] for r in self.rounds]}")


@pytest.fixture()
def m2_spy(monkeypatch):
    return M2Spy(monkeypatch)


def enable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """per-test 開啟「開」（`LIFE_THREAD_CONSOLIDATION_ENABLED`）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    assert w.consolidation_enabled() is True


def drive_hook(agent_id: str, thread_id: str, status: str) -> List[Any]:
    """在 running loop 內呼叫**同步 hook**（`build_dissolve_hook()` 的唯一接縫）並收斂背景任務。

    **假時鐘**：hook 內部的 `w._now` 已被 `fake_clock` 換掉 ⇒ 本函式不碰真實時間、0 sleep。
    回傳背景沉澱任務的 `ConsolidationResult`（或例外物件）。
    """
    results: List[Any] = []

    async def _driver() -> None:
        w.build_dissolve_hook()(agent_id, thread_id, status)
        tasks = list(w._BACKGROUND_TASKS)
        if tasks:
            results.extend(await asyncio.gather(*tasks, return_exceptions=True))
        await asyncio.sleep(0)  # 只讓 done-callback 跑完的 event-loop 讓渡（0 秒 wall-clock）

    asyncio.run(_driver())
    assert w.pending_task_count() == 0, "背景沉澱任務未收斂"
    return results


def _assert_in_sim_root(sim_root: Path) -> None:
    """真 M1 落點自證：線頭檔必須在隔離根內（生產 `data/**` 零接觸）。"""
    resolved = str(lt.life_threads_path(AGENT).resolve())
    assert resolved.startswith(str(sim_root.resolve())), resolved


# ══════════════════════════════════════════════════════════════
# M2C-1：存量終態 → 恰一次寫回
# ══════════════════════════════════════════════════════════════


def test_m2c1_existing_terminal_written_back_exactly_once(
    sim_root, monkeypatch, writeback_seams, fake_clock, m2_spy
):
    """金樣 B ＋ 假時鐘（09/20 08:00）⇒ 對**既有**終態線頭直接呼叫 hook ⇒ 恰好一次寫回。

    寫回路徑逐段是真碼：M1 fold → M2 裁決 → 真 adapter → SAGE(stub) →
    **真 `lt.append_dissolved`**（`dissolved_at` ＋ `sage_fact_id`）。
    """
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    materialize(load_fixture(), sim_root)
    fake_clock.moment = NOW_B
    _assert_in_sim_root(sim_root)

    # ── 前置：金樣 B 內該線頭**已經是**終態、且 0 溶解憑據（存量終態的定義）──
    before = lt.get_state(AGENT, AKANE_TERMINAL_CANDIDATE)
    assert before["status"] == "completed", before["status"]
    assert before["dissolved_at"] is None
    assert before["sage_fact_id"] is None
    assert ex._is_consolidated(AKANE_TERMINAL_CANDIDATE, "completed") is False
    rows_before = len(local_lines(sim_root))

    # ── 真 hook（M4 接縫的呼叫形式）：直接把這條存量終態交給它 ──
    results = drive_hook(AGENT, AKANE_TERMINAL_CANDIDATE, "completed")

    assert len(results) == 1, results
    result = results[0]
    assert isinstance(result, ex.ConsolidationResult), result
    assert result.thread_id == AKANE_TERMINAL_CANDIDATE
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason
    assert result.llm_calls == 1
    assert result.reason == "ok"

    # 沉澱 LLM 恰 1 次、SAGE 恰 1 次（且 adapter 契約未被繞過）。
    assert proxy.call_count == 1, "沉澱 LLM 只能呼叫恰 1 次"
    assert proxy.calls[0]["max_retries"] == 0
    assert proxy.calls[0]["reasoning_effort"] == w.CONSOLIDATION_REASONING_EFFORT
    assert sage.call_count == 1, "SAGE 落地只能恰 1 次"
    assert sage.agents == [AGENT]
    assert sage.facts[0]["predicate"] == ex.FACT_PREDICATE
    assert sage.facts[0]["object"] == _NARRATIVE

    # hook 內部**實算**的 M2 裁決：存量終態被視為真實溶解候選。
    verdict = m2_spy.last(AKANE_TERMINAL_CANDIDATE)
    assert verdict["should_mutate"] is True
    assert verdict["target_status"] == "completed"
    assert verdict["reason"] == lt_diss.DissolutionReason.GOAL_ACHIEVED.value == "goal_achieved"
    assert verdict["skipped"] is None
    assert verdict["advisory"] is False
    assert verdict["prompt_chars"] > 0, "終態候選必須帶 reflection_prompt"

    # ── 恰好一次：**一筆** `dissolved` 事件、該線頭**恰一列**非 null `dissolved_at` ──
    all_dissolved = dissolved_rows(sim_root)
    assert len(all_dissolved) == 1, f"M1 溶解事件必須恰 1 筆：{all_dissolved}"
    assert all_dissolved[0]["thread_id"] == AKANE_TERMINAL_CANDIDATE
    credited = rows_with_dissolved_at(sim_root, AKANE_TERMINAL_CANDIDATE)
    assert len(credited) == 1, f"該線頭的非 null dissolved_at 列必須恰 1 列：{credited}"
    assert credited[0]["event_type"] == lt.EVENT_DISSOLVED
    assert len(rows_for(sim_root, AKANE_TERMINAL_CANDIDATE)) == 3  # created/transitioned/dissolved
    assert len(local_lines(sim_root)) == rows_before + 1, "只准新增一列"

    state = lt.get_state(AGENT, AKANE_TERMINAL_CANDIDATE)
    assert state["status"] == "completed"
    assert isinstance(state["dissolved_at"], str) and state["dissolved_at"].strip()
    assert state["dissolved_at"] == credited[0]["dissolved_at"]
    assert isinstance(state["sage_fact_id"], str) and state["sage_fact_id"].strip()
    assert state["sage_fact_id"] == sage.fact_ids[0] == "fact-sim-0001"
    assert state["sage_fact_id"] == credited[0]["sage_fact_id"]
    assert ex._is_consolidated(AKANE_TERMINAL_CANDIDATE, "completed") is True

    _obs(
        "M2C-1",
        thread=_short(AKANE_TERMINAL_CANDIDATE),
        status=result.status,
        llm_calls=result.llm_calls,
        dissolved_at=state["dissolved_at"],
        sage_fact_id=state["sage_fact_id"],
        dissolved_events=len(all_dissolved),
        credited_rows=len(credited),
        proxy_calls=proxy.call_count,
        sage_calls=sage.call_count,
    )


# ══════════════════════════════════════════════════════════════
# M2C-2：下一 slot 再打一次 ⇒ already_dissolved、0 LLM
# ══════════════════════════════════════════════════════════════


def test_m2c2_next_slot_same_existing_terminal_is_already_dissolved_with_zero_llm(
    sim_root, monkeypatch, writeback_seams, fake_clock, m2_spy
):
    """第一輪寫回後，把 `now` 跳到下一 slot（09/20 22:00）再打同一條 ⇒ 不再溶解。"""
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    materialize(load_fixture(), sim_root)

    # ── 第一輪：真寫回（前置狀態）──
    fake_clock.moment = NOW_B
    first = drive_hook(AGENT, AKANE_TERMINAL_CANDIDATE, "completed")
    assert len(first) == 1
    assert first[0].status == ex.STATUS_CONSOLIDATED, first[0].reason
    assert proxy.call_count == 1 and sage.call_count == 1
    after_first = lt.get_state(AGENT, AKANE_TERMINAL_CANDIDATE)
    assert after_first["dissolved_at"], "第一輪必須留下非 null dissolved_at"
    assert after_first["sage_fact_id"], "第一輪必須留下非空 sage_fact_id"
    file_after_first = _read_text(sim_root)

    # ── 第二輪：**下一 slot**（直接換 `now`，0 sleep）；stub 計數起點 ──
    fake_clock.moment = NOW_B_NEXT
    counts_before = (proxy.call_count, sage.call_count)
    second = drive_hook(AGENT, AKANE_TERMINAL_CANDIDATE, "completed")

    assert len(second) == 1, second
    assert second[0].llm_calls == 0, "第二次必須 0 次 LLM"
    # 執行層終態閘門：`should_mutate` 非嚴格 True ⇒ 在付費路徑之前擋下。
    assert second[0].status == ex.STATUS_SKIPPED_NOT_TERMINAL, second[0].status
    assert second[0].reason == "should_mutate_not_true"

    # hook 內部**實算**的 M2 裁決：`already_dissolved`（§3.3 L1 冪等）。
    sentinel = m2_spy.last(AKANE_TERMINAL_CANDIDATE)
    assert sentinel["should_mutate"] is False
    assert sentinel["skipped"] == lt_diss.SKIPPED_ALREADY_DISSOLVED == "already_dissolved"
    assert sentinel["target_status"] == "completed"
    assert sentinel["reason"] == lt_diss.DissolutionReason.NONE.value == "none"
    assert sentinel["prompt_chars"] == 0, "已溶解者不得再產生 prompt"

    # 0 LLM／0 SAGE：stub 呼叫數**不變**。
    assert (proxy.call_count, sage.call_count) == counts_before == (1, 1)
    assert proxy.call_count == 1 and sage.call_count == 1

    # M1 檔**逐位元不變**（0 重複寫回）。
    assert _read_text(sim_root) == file_after_first
    assert len(dissolved_rows(sim_root)) == 1
    assert len(rows_with_dissolved_at(sim_root, AKANE_TERMINAL_CANDIDATE)) == 1
    final = lt.get_state(AGENT, AKANE_TERMINAL_CANDIDATE)
    assert final["dissolved_at"] == after_first["dissolved_at"]
    assert final["sage_fact_id"] == after_first["sage_fact_id"]

    _obs(
        "M2C-2",
        thread=_short(AKANE_TERMINAL_CANDIDATE),
        should_mutate=sentinel["should_mutate"],
        skipped=sentinel["skipped"],
        second_status=second[0].status,
        second_llm_calls=second[0].llm_calls,
        proxy_calls=proxy.call_count,
        sage_calls=sage.call_count,
        dissolved_at=final["dissolved_at"],
        sage_fact_id=final["sage_fact_id"],
        file_unchanged=(_read_text(sim_root) == file_after_first),
    )


# ══════════════════════════════════════════════════════════════
# M2C-3：同日預算用盡即停（第 4 條存量終態 ⇒ skipped_budget）
# ══════════════════════════════════════════════════════════════


def test_m2c3_fourth_existing_terminal_same_day_is_budget_blocked(
    sim_root, monkeypatch, writeback_seams, fake_clock, m2_spy
):
    """`DISSOLVE_MAX_PER_DAY = 3`：同日 3 次成功溶解後，第 4 條存量終態 ⇒ 0 LLM／0 寫入。"""
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    materialize(load_fixture(), sim_root)
    fake_clock.moment = NOW_B

    # 日預算釘死在**假時鐘**的同一天（生產預設＝wall clock）⇒ 四次請求必屬同一預算日。
    monkeypatch.setattr(ex, "_DEFAULT_BUDGET", ex.ConsolidationBudget(now=lambda: BUDGET_DAY_UTC))
    assert ex.MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY == DISSOLVE_MAX_PER_DAY
    assert lt_diss.DISSOLVE_MAX_PER_DAY == DISSOLVE_MAX_PER_DAY

    # ── 3 條**同構的存量終態**（真 M1 API 合成；見 docstring 校正 A）──
    synth = [new_terminal_thread(NOW_B) for _ in range(3)]
    for thread_id in synth:
        state = lt.get_state(AGENT, thread_id)
        assert state["status"] == "completed" and state["dissolved_at"] is None

    # ── 前 3 次：全部成功（各 1 次 LLM／1 次 SAGE／1 筆寫回）──
    for thread_id in synth:
        result = drive_hook(AGENT, thread_id, "completed")[0]
        assert result.status == ex.STATUS_CONSOLIDATED, result.reason
        assert result.llm_calls == 1
    assert proxy.call_count == 3 and sage.call_count == 3
    assert len(dissolved_rows(sim_root)) == 3

    # ── 第 4 條：金樣 B 內**既有**的存量終態（與前三條同一預算日）──
    fake_clock.moment = NOW_B_NEXT
    file_before_fourth = _read_text(sim_root)
    llm_before_fourth = proxy.call_count  # ＝3
    fourth = drive_hook(AGENT, AKANE_TERMINAL_CANDIDATE, "completed")

    assert len(fourth) == 1, fourth
    blocked = fourth[0]
    assert blocked.thread_id == AKANE_TERMINAL_CANDIDATE
    assert blocked.status == ex.STATUS_SKIPPED_BUDGET, blocked.status
    assert blocked.reason == "budget_exhausted"
    assert blocked.llm_calls == 0, "第 4 條必須 0 次 LLM"
    # 擋下它的是**預算**，不是決策：M2 對它仍裁定為真實溶解候選。
    verdict = m2_spy.last(AKANE_TERMINAL_CANDIDATE)
    assert verdict["should_mutate"] is True, verdict
    assert verdict["target_status"] == "completed"

    # stub 呼叫數不變 ＋ budget 3/3。
    assert proxy.call_count == llm_before_fourth == 3, "第 4 條不得增加 stub 計數"
    assert sage.call_count == 3
    assert ex._DEFAULT_BUDGET.count_for(AGENT) == 3
    assert ex._DEFAULT_BUDGET.global_count == 3
    assert ex._DEFAULT_BUDGET.per_agent_limit == DISSOLVE_MAX_PER_DAY
    assert ex._DEFAULT_BUDGET.day == BUDGET_DAY_UTC.date().isoformat()

    # **0 寫入**：M1 檔逐位元不變；第 4 條 `dissolved_at`／`sage_fact_id` 保持 null。
    assert _read_text(sim_root) == file_before_fourth
    assert len(dissolved_rows(sim_root)) == 3
    assert AKANE_TERMINAL_CANDIDATE not in [r["thread_id"] for r in dissolved_rows(sim_root)]
    state = lt.get_state(AGENT, AKANE_TERMINAL_CANDIDATE)
    assert state["status"] == "completed"
    assert state["dissolved_at"] is None
    assert state["sage_fact_id"] is None
    assert rows_with_dissolved_at(sim_root, AKANE_TERMINAL_CANDIDATE) == []
    assert ex._is_consolidated(AKANE_TERMINAL_CANDIDATE, "completed") is False

    _obs(
        "M2C-3",
        fourth_thread=_short(AKANE_TERMINAL_CANDIDATE),
        fourth_status=blocked.status,
        reason=blocked.reason,
        fourth_llm_calls=blocked.llm_calls,
        dissolved_at=state["dissolved_at"],
        sage_fact_id=state["sage_fact_id"],
        budget=f"{ex._DEFAULT_BUDGET.count_for(AGENT)}/{DISSOLVE_MAX_PER_DAY}",
        proxy_calls=proxy.call_count,
        sage_calls=sage.call_count,
        dissolved_events=len(dissolved_rows(sim_root)),
    )


# ══════════════════════════════════════════════════════════════
# M2C-4：軟封存預設關 ⇒ 非終態不溶（＋對照組）
# ══════════════════════════════════════════════════════════════


def test_m2c4_soft_archive_default_off_keeps_non_terminal_undissolved(
    sim_root, monkeypatch, writeback_seams, fake_clock, m2_spy
):
    """非終態（`active`／`dormant`）線頭 ⇒ **不溶解**（0 LLM／0 寫入）；對照組見 docstring 校正 B。"""
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    materialize(load_fixture(), sim_root)
    fake_clock.moment = NOW_B

    dormant_id = new_dormant_thread(NOW_B)
    file_before = _read_text(sim_root)

    # ── (a) active 線頭（金樣 B 內既有）：0 LLM／0 寫入 ──
    active_result = drive_hook(AGENT, AKANE_ACTIVE_THREAD, "active")[0]
    assert active_result.status == ex.STATUS_SKIPPED_NOT_TERMINAL, active_result.status
    assert active_result.llm_calls == 0
    active_verdict = m2_spy.last(AKANE_ACTIVE_THREAD)
    assert active_verdict["should_mutate"] is False
    assert active_verdict["target_status"] == "active"
    assert active_verdict["skipped"] is None
    assert active_verdict["prompt_chars"] == 0

    # ── (b) dormant 線頭（真 M1 合成）：同樣 0 LLM／0 寫入（步驟 5：其餘保持活躍）──
    dormant_result = drive_hook(AGENT, dormant_id, "dormant")[0]
    assert dormant_result.status == ex.STATUS_SKIPPED_NOT_TERMINAL, dormant_result.status
    assert dormant_result.reason == "should_mutate_not_true"
    assert dormant_result.llm_calls == 0
    dormant_verdict = m2_spy.last(dormant_id)
    assert dormant_verdict["should_mutate"] is False
    assert dormant_verdict["target_status"] == "active"
    assert dormant_verdict["reason"] == "none"
    assert dormant_verdict["skipped"] is None

    assert proxy.call_count == 0, "非終態線頭不得進入付費路徑"
    assert sage.call_count == 0
    assert dissolved_rows(sim_root) == []
    assert _read_text(sim_root) == file_before, "非終態輪不得有任何 M1 寫入"
    for thread_id, expected in ((AKANE_ACTIVE_THREAD, "active"), (dormant_id, "dormant")):
        state = lt.get_state(AGENT, thread_id)
        assert state["status"] == expected
        assert state["dissolved_at"] is None
        assert state["sage_fact_id"] is None

    # ── (c) 對照組：把 active 線頭推過 advisory 門檻，再以 `_evaluate` seam 注入
    #         `allow_soft_archive=True`（模組 docstring 明列的注入點，無需改 src）──
    make_thread_over_age(sim_root, AKANE_ACTIVE_THREAD, NOW_B)
    over_age_state = lt.get_state(AGENT, AKANE_ACTIVE_THREAD)

    def soft_archive_evaluate(thread, now, agent_id):
        """與 `w._evaluate` **逐字相同**，唯一差異是 `allow_soft_archive=True`。"""
        return lt_diss.evaluate_thread_dissolution(
            thread,
            now,
            max_active_duration_days=w.POLICY_MAX_ACTIVE_DURATION_DAYS,
            stale_check_threshold_days=w.POLICY_STALE_CHECK_THRESHOLD_DAYS,
            allow_soft_archive=True,
            agent_id=agent_id,
            terminal_reason=None,
        )

    monkeypatch.setattr(w, "_evaluate", soft_archive_evaluate, raising=True)
    file_before_control = _read_text(sim_root)
    control_result = drive_hook(AGENT, AKANE_ACTIVE_THREAD, "active")[0]

    # 裁決**確實翻轉**（證明擋下它的就是「軟封存預設關」這個旗標）：
    control_view = lt_diss.evaluate_thread_dissolution(
        over_age_state, NOW_B, allow_soft_archive=True, agent_id=AGENT
    )
    assert control_view.should_mutate is True
    assert control_view.target_status.value == "dormant"

    # 但執行層的**終態閘門**照樣擋下 `dormant`（`TERMINAL_TARGET_STATUSES` 不含它）：
    assert ex.TERMINAL_TARGET_STATUSES == ("completed", "abandoned")
    assert control_result.status == ex.STATUS_SKIPPED_NOT_TERMINAL, control_result.status
    assert control_result.reason == "target_status=dormant", control_result.reason
    assert control_result.llm_calls == 0
    assert proxy.call_count == 0 and sage.call_count == 0
    assert dissolved_rows(sim_root) == []
    assert _read_text(sim_root) == file_before_control

    _obs(
        "M2C-4",
        active_status=active_result.status,
        active_llm_calls=active_result.llm_calls,
        dormant_status=dormant_result.status,
        dormant_llm_calls=dormant_result.llm_calls,
        control_target=control_view.target_status.value,
        control_should_mutate=control_view.should_mutate,
        control_exec_status=control_result.status,
        control_reason=control_result.reason,
        proxy_calls=proxy.call_count,
        sage_calls=sage.call_count,
    )
