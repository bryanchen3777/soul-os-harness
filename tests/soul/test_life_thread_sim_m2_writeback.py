# tests/soul/test_life_thread_sim_m2_writeback.py
# M2-WRITEBACK-SIM-1 — 把「開」（`LIFE_THREAD_CONSOLIDATION_ENABLED`）接到
# SIM-TIME-001 的**假時鐘金樣平面**，釘死四條既有 wiring 測試沒鎖到的寫回行為。
"""M2-WRITEBACK-SIM-1 — 沉澱寫回 × 假時鐘 × 金樣（**薄票：0 `src/**` 改動、0 生產寫入**）。

受測模組（**全部唯讀呼叫，本票不改一行**）
──────────────────────────────────────────
- `src/soul/life_thread_orchestrator.py`（假時鐘入口 `run_slot_pipeline`）
- `src/soul/life_thread_consolidation_wiring.py`（旗標 ＋ 同步 hook ＋ 背景沉澱）
- `src/soul/life_thread_dissolution{,_exec}.py`（M2 裁決 ＋ 執行層）
- `src/soul/life_threads.py`（M1 寫回語意）

平面不變量（沿用 SIM-TIME-001，**同一套 primitive**）
──────────────────────────────────────────────────
1. **假時鐘**：`now` 由測試直接注入（local tz `America/New_York`，slot 08:00／22:00）。
   連續 slot ＝ 直接換 `now` 再呼叫 ⇒ **0 `time.sleep`／0 `asyncio.sleep(秒)`／0 真實等待**。
   唯一的 `await asyncio.sleep(0)` 是**讓 done-callback 跑完的 event-loop 讓渡**（0 秒 wall-clock）。
2. **記憶 fork**：金樣 → `SOUL_OS_DATA_DIR` 隔離目錄（`sim_root`）＋ `reset_data_root()`；
   生產 `data/**` 全程唯讀（本檔另以 `lt.life_threads_path` 斷言落點不在 repo `data/`）。
3. **零 LLM**：M4 一律注入預錄 JSON stub（`ScriptedLLM`）；沉澱 LLM 走
   `w._resolve_llm_proxy` 的替身（`_RecordingProxy`，記憶體內回應）；
   另裝「真實 proxy 探針」（`lt_origins._find_llm_proxy` ⇒ 命中即紅燈）＋
   `socket.create_connection` 攔阻 ⇒ **0 真實外呼**（死代理 `HTTP(S)_PROXY=127.0.0.1:1` 下亦全綠）。
4. **SAGE 一律 stub**：`w._write_fact` 以替身取代 ⇒ **不建 store、不寫真記憶庫**
   （`w._WRITERS` 全程為空）；M1 回填（`w._append_dissolved`）走**真實** `lt.append_dissolved`
   —— 那正是本票要觀測的寫回路徑。

四條斷言（本票唯一範圍）
────────────────────────
- **WB-1 候選 → 恰好一次寫回**：金樣 B ＋ 假時鐘一輪 ⇒ M1 檔內該線頭**恰 1 筆**
  `dissolved` 事件（`dissolved_at` 非 null、`sage_fact_id` 非空字串回填）、沉澱 LLM 恰 1 次。
- **WB-2 下一 slot 同一 id ⇒ `already_dissolved`**：跳到下一個 slot 再跑 ⇒ M2 對該線頭回
  `extra_metadata["skipped"] == "already_dissolved"`、`should_mutate is False`，
  **第二次 0 次 LLM**、M1 檔**逐位元不變**。
- **WB-3 同日第 4 次 ⇒ 不呼叫 LLM**：`DISSOLVE_MAX_PER_DAY = 3`；同日累計第 4 次溶解請求
  ⇒ 執行層回 `skipped_budget`（`llm_calls == 0`）、**stub 計數不變**、**不寫入**（fail-closed 至安靜）。
- **WB-4 軟封存預設關 ⇒ 終態前不溶**：非終態（`active`／`dormant`）線頭即使已達
  advisory 門檻（`allow_soft_archive` 預設 `False`）⇒ **不溶解**、
  M1 檔內 `dissolved_at` 保持 `null`、0 LLM。

🔴 票面前提的一處**可執行校正**（WB-1 內已斷言，見 `test_wb1_*`）
──────────────────────────────────────────────────────────────
票面寫「金樣 B 內 akane 的 `2917e901`（`status=completed`、`dissolved_at=null`）即**現成的溶解候選**」。
事實：它是 M2 的**裁決候選**（`should_mutate=True`），但**不是生產可達的寫回標的**——
`life_thread_origins.apply_actions` 只在 `lt.append_transition()` **成功之後**才觸發
`dissolve_hook`，而 M1 `allowed_transition()` 對終態來源**恆為 `False`**
（`completed` / `abandoned` → 無任何目標狀態）⇒ 對一個**已經**是終態的線頭，
生產路徑**永遠不會**呼叫 hook（本檔以真動作斷言：`skipped == ["complete:rejected"]`）。
因此 WB-1 的寫回標的＝**該輪剛進入終態**的那條 akane 線頭（同一份金樣、同一個 slot、
同一條「讀線頭 → M2 → LLM → SAGE → `append_dissolved`」寫回路徑）。
「既有終態線頭的沉澱」屬**新契約問題**（本票不改 `src/**`，故只登記不修）。

跨檔 import 的取捨
──────────────────
`tests/soul/` 無 `__init__.py`（見 `test_life_thread_consolidation_wiring.py` 的同註記），
故**不**跨檔 import 另一個測試模組（模組名解析依賴 pytest 的 rootdir 插入順序，脆弱且會與
SIM-TIME-001 的 autouse 平面不變量糾纏——它的 `_no_live_llm` 把旗標釘在關）。
本檔改為**複製最小必要 primitive**（金樣讀取／落地、due 標記、假時鐘 runner、
`ScriptedLLM`、proxy 探針、socket 攔阻），並把旗標改成**預設關、各測試顯式開**。

執行層詞彙的取用（**0 新 importer**）
────────────────────────────────────
`test_life_thread_dissolution_exec.py` 的 T53／T54 把「全庫 import 執行層的檔案」釘成
**精確集合等值**（1 生產檔 ＋ 2 測試檔）。本檔**不**直接 import 執行層，改取接線模組
已持有的別名（`ex = w.lt_exec`）⇒ 既有護欄逐字不動、**0 新 importer**（見檔內註解）。
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
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

#: 執行層的**觀測詞彙**（狀態碼／預算類／結果型別）。
#:
#: 🔴 本檔**刻意不**寫 `import src.soul.life_thread_dissolution_exec`：`test_life_thread_dissolution_exec.py`
#: 的 T53／T54 把「全庫 import 執行層的檔案」釘成**精確集合等值**（1 個生產檔
#: `life_thread_consolidation_wiring.py` ＋ 2 個測試檔）。多一個 importer 就紅 ⇒ 直接 import
#: 會**動到既有護欄**。改由接線模組**已持有**的模組別名取用（該檔本來就是唯一允許 import
#: 執行層的生產檔）⇒ 0 新 importer、既有護欄逐字不動。
ex = w.lt_exec
assert ex.__name__ == "src.soul.life_thread_dissolution_exec"

# ══════════════════════════════════════════════════════════════
# 常數：金樣、假時鐘、觀測標的
# ══════════════════════════════════════════════════════════════

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"
FIXTURE_B = FIXTURES_DIR / "life_thread_snapshot_20260920_0800.json"
LIFE_THREADS_FILENAME = "life_threads.jsonl"

AGENT = "agent_akane"
#: 金樣 B 內 akane 的**現成終態候選**（`status=completed`、`dissolved_at=null`）。
AKANE_TERMINAL_CANDIDATE = "2917e901-757b-4d4c-9b2e-8188413e5449"
#: 金樣 B 內 akane 的 **active** 線頭（本檔以真動作把它推進終態，再觀測寫回）。
AKANE_ACTIVE_THREAD = "0ce7e589-eebc-4203-9c48-626f65819433"

#: 假時鐘：09/20 08:00（morning）與同日的下一個 slot 09/20 22:00（night）。
NOW_B = datetime(2026, 9, 20, 8, 0, 0, tzinfo=LOCAL_TZ)
NOW_B_NEXT = datetime(2026, 9, 20, 22, 0, 0, tzinfo=LOCAL_TZ)
SLOT_B = "morning"
SLOT_B_NEXT = "night"

#: 執行層「日」預算鍵的釘死時刻（**假時鐘**；生產預設是 wall clock）。
#: WB-3 的四次請求必須落在**同一個預算日** ⇒ 釘死才具決定性（否則跨 UTC 午夜會 flake）。
BUDGET_DAY_UTC = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

_SOUL_STUB = "（模擬人格語境；M2-WRITEBACK-SIM-1 固定字串，不讀生產 persona）"
_NARRATIVE = "她把這件事收好了：不是勝利，是終於可以放下。"

#: 沉澱 LLM 的預錄回覆（契約 §3.2：`{"dissolution", "meaning_kind"}`）。
DISSOLUTION_JSON = json.dumps({"dissolution": _NARRATIVE, "meaning_kind": "relief"}, ensure_ascii=False)

#: 日預算上限的**票面值**（WB-3 逐字釘死：執行層與決策層必須同值）。
DISSOLVE_MAX_PER_DAY = 3


def _actions_json(*thread_ids: str) -> str:
    """§5.1 動作 JSON：對每個 thread_id 發一個 `complete`（op ⇒ status 由 M4 映射）。"""
    return json.dumps(
        {"actions": [{"op": "complete", "thread_id": tid} for tid in thread_ids]},
        ensure_ascii=False,
    )


# ══════════════════════════════════════════════════════════════
# 金樣：讀取、落地（記憶 fork）、觀測輔助
# ══════════════════════════════════════════════════════════════


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


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


def local_lines(data_root: Path, agent_id: str = AGENT) -> List[Dict[str, Any]]:
    path = _agent_file(data_root, agent_id)
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def dissolved_rows(data_root: Path, agent_id: str = AGENT) -> List[Dict[str, Any]]:
    return [r for r in local_lines(data_root, agent_id) if r.get("event_type") == "dissolved"]


def fold_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """M1 `fold()` 規則 2 的等價實作（`(updated_at, event_seq)` 字典序最大者勝出）。"""
    states: Dict[str, Any] = {}
    keys: Dict[str, tuple] = {}
    for row in rows:
        thread_id = row.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        key = (str(row.get("updated_at") or ""), int(row.get("event_seq") or 0))
        if thread_id not in keys or key >= keys[thread_id]:
            keys[thread_id] = key
            states[thread_id] = row
    return states


def folded_due_thread_ids(data_root: Path, now: datetime) -> List[str]:
    """M3 判定 1 的同口徑到期集合（**fold 後** `active` 且 `check_after_ts <= now`）。

    必須先 fold：一條線頭的歷史列裡有它「曾經 active」的舊列，
    直接用原始列判定會把**已終態**的線頭誤判成到期。
    """
    ts = now.timestamp()
    out: List[str] = []
    for thread_id, row in fold_rows(local_lines(data_root)).items():
        if row.get("status") != "active":
            continue
        parsed = _parse_iso(row.get("check_after_ts"))
        if parsed is not None and parsed.timestamp() <= ts:
            out.append(thread_id)
    return sorted(out)


def _rewrite_winner(data_root: Path, thread_id: str, mutate) -> None:
    """只改 `fold()` 會選中的那一列（`(updated_at, event_seq)` 字典序最大者）。

    改其他列不影響 fold 後的當前狀態 ⇒ 這是「只動隔離副本」的最小寫入面。
    """
    rows = local_lines(data_root)
    winner = max(
        (r for r in rows if r.get("thread_id") == thread_id),
        key=lambda r: (str(r.get("updated_at") or ""), int(r.get("event_seq") or 0)),
    )
    mutate(winner)
    _agent_file(data_root).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def make_thread_due(data_root: Path, thread_id: str, now: datetime) -> None:
    """把該線頭的 `check_after_ts` 設為過去（**只動隔離副本**）⇒ 該 slot 到期。"""
    past = (now - timedelta(hours=1)).astimezone(timezone.utc).isoformat()

    def mutate(row: Dict[str, Any]) -> None:
        row["check_after_ts"] = past

    _rewrite_winner(data_root, thread_id, mutate)
    assert thread_id in folded_due_thread_ids(data_root, now), "到期標記未生效"


def make_thread_over_age(data_root: Path, thread_id: str, now: datetime) -> None:
    """把該 active 線頭推過 advisory 門檻（age 19 天、idle 8 天 ⇒ 兩軸皆超限）。

    **必須改「該線頭的所有列」**：只改當下的 fold 勝者會把它的 `updated_at` 推舊，
    反而讓另一列（帶著原本的 `check_after_ts`）變成勝者 ⇒ 該線頭又變成到期、M3 就會 WAKE。
    全部列一致化後，勝者（`event_seq` 最大者）仍是原勝者，只是時間軸被推舊。

    **單調安全**：時間只會往前 ⇒ 這條線頭對「假時鐘」與「wall clock」都恆為超限，
    故不隨真實日期漂移而改變裁決（WB-4 的決定性來源）。
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
        row["check_after_ts"] = None  # 只留 advisory 軸，不讓它到期喚醒
        touched += 1
    assert touched > 0, f"線頭不存在：{thread_id}"
    _agent_file(data_root).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    state = lt.get_state(AGENT, thread_id)
    assert state["status"] == "active", state.get("status")
    assert state["check_after_ts"] is None
    assert folded_due_thread_ids(data_root, now) == [], "不該有到期線頭"


def new_active_thread(now: datetime, origin_type: str = "necessity_driven") -> str:
    """用**真 M1 API**（不手寫 JSONL）建一條 active 且已到期的線頭（WB-3 的合成請求來源）。

    `origin_type="necessity_driven"`：M3 的 `origin_type` 取自**最早到期**的線頭，
    而 M4 的 prompt 組裝對 `necessity_driven` 只要求合法 slot（金樣 akane 亦為此型）
    ⇒ 不需偽造 goal seeds。落點仍是 `sim_root`（`SOUL_OS_DATA_DIR` 隔離）。
    """
    past = (now - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    thread_id = lt.create_thread(
        AGENT,
        "合成的待完成線頭",
        "她打算把這件事收尾。",
        origin_type,
        check_after_ts=past,
    )
    assert thread_id, "合成線頭失敗（M1 容量或參數拒絕）"
    return thread_id


# ══════════════════════════════════════════════════════════════
# fixtures：隔離資料根、平面護欄、行程級狀態
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
    m5.reset_state()
    yield
    assert w.pending_task_count() == 0, "測試結束仍有未清理的背景沉澱任務"
    w._BACKGROUND_TASKS.clear()
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()
    m5.reset_state()


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
        raise AssertionError("M2-WRITEBACK-SIM-1：平面內不得連外（socket.create_connection 已攔）")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect, raising=True)

    yield probe
    assert probe.calls == [], f"路徑碰到真實 LLM proxy：{probe.calls}"


# ══════════════════════════════════════════════════════════════
# 零 LLM 接縫替身 ＋ 假時鐘 runner
# ══════════════════════════════════════════════════════════════


class ScriptedLLM:
    """M4 的預錄 LLM stub（**0 真實 LLM／0 網路**）：逐 agent 回預錄 JSON 並累計呼叫。"""

    def __init__(self, responses: Optional[Dict[str, str]] = None, default: Optional[str] = None):
        self.responses = dict(responses or {})
        self.default = default
        self.calls: List[str] = []

    def __call__(self, messages: Any, agent_id: str) -> Optional[str]:
        self.calls.append(agent_id)
        return self.responses.get(agent_id, self.default)

    @property
    def call_count(self) -> int:
        return len(self.calls)


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


class M2Spy:
    """M2 批量入口監看器（orchestrator 的唯讀評估；記下每筆裁決的離散觀測值）。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rounds: List[Dict[str, Dict[str, Any]]] = []
        real = lt_diss.evaluate_batch_dissolution

        def spy(threads, current_time, **kwargs):
            evaluations = real(threads, current_time, **kwargs)
            self.rounds.append(
                {
                    e.thread_id: {
                        "should_mutate": e.should_mutate,
                        "target_status": e.target_status.value,
                        "reason": e.reason.value,
                        "advisory": (e.extra_metadata or {}).get("advisory"),
                        "skipped": (e.extra_metadata or {}).get("skipped"),
                    }
                    for e in evaluations
                }
            )
            return evaluations

        monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy, raising=True)

    def last(self, thread_id: str) -> Dict[str, Any]:
        assert self.rounds, "M2 入口未被呼叫"
        assert thread_id in self.rounds[-1], f"本輪未評估 {thread_id}：{sorted(self.rounds[-1])}"
        return self.rounds[-1][thread_id]


def run_slot(
    agent_ids: Sequence[str],
    now: datetime,
    slot: str,
    *,
    llm_caller: Any,
) -> Dict[str, Any]:
    """**假時鐘**一輪：直接注入 `now`（0 sleep、0 真實等待）＋ 收斂 hook 丟出的背景任務。

    回傳 `{"summaries", "tasks", "results"}`；`results` 是背景沉澱任務的
    `ConsolidationResult`（或例外物件），供逐條斷言實際觀測值。
    """
    out: Dict[str, Any] = {"summaries": {}, "tasks": [], "results": []}

    async def _driver() -> None:
        out["summaries"] = await m5.run_slot_pipeline(agent_ids, now, slot, llm_caller=llm_caller)
        tasks = list(w._BACKGROUND_TASKS)
        out["tasks"] = tasks
        if tasks:
            out["results"] = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # 只讓 done-callback 跑完的 event-loop 讓渡（0 秒 wall-clock）

    asyncio.run(_driver())
    assert w.pending_task_count() == 0
    return out


def drive_hook(agent_id: str, thread_id: str, status: str) -> List[Any]:
    """在 running loop 內呼叫**同步 hook**（M4 的唯一接縫）並把背景任務 `await` 完。"""
    results: List[Any] = []

    async def _driver() -> None:
        w.build_dissolve_hook()(agent_id, thread_id, status)
        tasks = list(w._BACKGROUND_TASKS)
        if tasks:
            results.extend(await asyncio.gather(*tasks, return_exceptions=True))
        await asyncio.sleep(0)

    asyncio.run(_driver())
    assert w.pending_task_count() == 0
    return results


def _statuses(results: Sequence[Any]) -> List[Any]:
    return [getattr(r, "status", r) for r in results]


def _llm_calls(results: Sequence[Any]) -> List[Any]:
    return [getattr(r, "llm_calls", None) for r in results]


@pytest.fixture()
def writeback_seams(monkeypatch):
    """把三條 seam 換成零 LLM／零 SAGE 替身，回傳 `(m4_stub, proxy, sage)`。

    - `w._resolve_llm_proxy` ⇒ 沉澱 LLM 替身（**真實** `w._make_llm_call` adapter 仍被走過）。
    - `w._write_fact` ⇒ SAGE 替身（不建 store、不寫真記憶庫）。
    - `w._append_dissolved` **不換**：M1 回填走真實 `lt.append_dissolved`（本票的觀測面）。
    """
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy, raising=True)
    sage = SageStub()
    monkeypatch.setattr(w, "_write_fact", sage, raising=True)
    return proxy, sage


def enable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """per-test 開啟「開」（`LIFE_THREAD_CONSOLIDATION_ENABLED`）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    assert w.consolidation_enabled() is True


# ══════════════════════════════════════════════════════════════
# WB-1：候選 → 恰好一次寫回
# ══════════════════════════════════════════════════════════════


def test_wb1_terminal_candidate_written_back_exactly_once(sim_root, monkeypatch, writeback_seams):
    """金樣 B ＋ 假時鐘一輪（09/20 08:00）⇒ 該輪進入終態的 akane 線頭**恰好一次**寫回。

    寫回路徑逐段是真碼：M1 fold → M2 裁決 → 真 adapter → SAGE(stub) →
    **真 `lt.append_dissolved`**（`dissolved_at` ＋ `sage_fact_id`）。
    """
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    materialize(load_fixture(), sim_root)
    make_thread_due(sim_root, AKANE_ACTIVE_THREAD, NOW_B)

    # 真 M1 落點自證：線頭檔必須在隔離根內（生產 `data/**` 零接觸）。
    assert str(lt.life_threads_path(AGENT).resolve()).startswith(str(sim_root.resolve()))

    stub = ScriptedLLM(
        {AGENT: _actions_json(AKANE_TERMINAL_CANDIDATE, AKANE_ACTIVE_THREAD)}, default=None
    )
    out = run_slot([AGENT], NOW_B, SLOT_B, llm_caller=stub)

    summary = out["summaries"][AGENT]
    assert summary["woke"] is True
    assert summary["reason"] == "CHECKPOINT_DUE_WAKE"
    round_info = summary["origin_round"]
    assert round_info["transitioned"] == [AKANE_ACTIVE_THREAD]
    # 🔴 票面前提校正：金樣內**已然終態**的候選在生產路徑上不可達（M1 終態不可再轉移）
    #    ⇒ 不會觸發 hook、不會被沉澱（登記為新契約問題，本票不改 src）。
    assert round_info["skipped"] == ["complete:rejected"], round_info["skipped"]
    assert lt.get_state(AGENT, AKANE_TERMINAL_CANDIDATE)["dissolved_at"] is None

    # ── 恰好一次：一個背景任務、一次 LLM、一次 SAGE、一筆 M1 溶解事件 ──
    assert len(out["tasks"]) == 1
    result = out["results"][0]
    assert isinstance(result, ex.ConsolidationResult), result
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason
    assert result.llm_calls == 1
    assert result.thread_id == AKANE_ACTIVE_THREAD
    assert proxy.call_count == 1, "沉澱 LLM 只能呼叫恰 1 次"
    assert proxy.calls[0]["max_retries"] == 0
    assert proxy.calls[0]["reasoning_effort"] == w.CONSOLIDATION_REASONING_EFFORT
    assert sage.call_count == 1, "SAGE 落地只能恰 1 次"
    assert sage.agents == [AGENT]

    rows = dissolved_rows(sim_root)
    assert len(rows) == 1, f"M1 溶解事件必須恰 1 筆：{rows}"
    assert rows[0]["thread_id"] == AKANE_ACTIVE_THREAD

    state = lt.get_state(AGENT, AKANE_ACTIVE_THREAD)
    assert state["status"] == "completed"
    assert state["dissolved_at"], "dissolved_at 必須回填（非 null）"
    assert state["dissolved_at"] == rows[0]["dissolved_at"]
    assert isinstance(state["sage_fact_id"], str) and state["sage_fact_id"].strip()
    assert state["sage_fact_id"] == sage.fact_ids[0]
    assert state["sage_fact_id"] == rows[0]["sage_fact_id"]


# ══════════════════════════════════════════════════════════════
# WB-2：下一 slot 同一 id ⇒ already_dissolved，不再呼叫
# ══════════════════════════════════════════════════════════════


def test_wb2_next_slot_same_id_is_already_dissolved_with_zero_llm(
    sim_root, monkeypatch, writeback_seams
):
    """把 `now` 跳到下一個 slot（09/20 22:00，night）再跑 ⇒ 同一條線頭不再被溶解。"""
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    spy = M2Spy(monkeypatch)
    materialize(load_fixture(), sim_root)
    make_thread_due(sim_root, AKANE_ACTIVE_THREAD, NOW_B)

    stub = ScriptedLLM({AGENT: _actions_json(AKANE_ACTIVE_THREAD)}, default=None)

    # ── 第一輪：真寫回（前置狀態）──
    first = run_slot([AGENT], NOW_B, SLOT_B, llm_caller=stub)
    assert _statuses(first["results"]) == [ex.STATUS_CONSOLIDATED]
    assert _llm_calls(first["results"]) == [1]
    after_first = lt.get_state(AGENT, AKANE_ACTIVE_THREAD)
    assert after_first["dissolved_at"] is not None
    assert len(dissolved_rows(sim_root)) == 1
    file_after_first = _agent_file(sim_root).read_text(encoding="utf-8")
    # 第一輪的 M2 評估**發生在轉移之前** ⇒ 當時它還是 active（非候選）；
    # 寫回的候選身分來自 hook 在 `append_transition` **之後**重讀 M1 的那一次裁決
    # （＝上面那筆 `STATUS_CONSOLIDATED` 的可執行證據）。
    assert spy.last(AKANE_ACTIVE_THREAD)["should_mutate"] is False
    assert spy.last(AKANE_ACTIVE_THREAD)["target_status"] == "active"

    # ── 第二輪：同一 id、下一個 slot ⇒ `already_dissolved` 哨兵 ──
    llm_before_second = (stub.call_count, proxy.call_count)  # (1, 1)
    second = run_slot([AGENT], NOW_B_NEXT, SLOT_B_NEXT, llm_caller=stub)

    summary2 = second["summaries"][AGENT]
    assert summary2["woke"] is False, "該輪 akane 無到期線頭 ⇒ 不進 M4（0 LLM 花費）"
    assert summary2["reason"] == "REFLECTION_SLOT_CLEAR"
    assert second["tasks"] == [], "不得再建任何背景沉澱任務"

    sentinel = spy.last(AKANE_ACTIVE_THREAD)
    assert sentinel["skipped"] == lt_diss.SKIPPED_ALREADY_DISSOLVED == "already_dissolved"
    assert sentinel["should_mutate"] is False
    assert sentinel["target_status"] == "completed"

    # 第二次 0 次 LLM：M4 stub 與沉澱 stub 皆不增加（Δ＝0）。
    assert (stub.call_count, proxy.call_count) == llm_before_second == (1, 1)
    assert sage.call_count == 1, "第二輪不得再寫 SAGE"

    # M1 檔逐位元不變（0 重複寫回）。
    assert _agent_file(sim_root).read_text(encoding="utf-8") == file_after_first
    assert len(dissolved_rows(sim_root)) == 1
    final = lt.get_state(AGENT, AKANE_ACTIVE_THREAD)
    assert final["dissolved_at"] == after_first["dissolved_at"]
    assert final["sage_fact_id"] == after_first["sage_fact_id"]


# ══════════════════════════════════════════════════════════════
# WB-3：同日第 4 次 ⇒ 不呼叫 LLM（fail-closed 至安靜）
# ══════════════════════════════════════════════════════════════


def test_wb3_fourth_same_day_dissolution_request_is_budget_blocked(
    sim_root, monkeypatch, writeback_seams
):
    """`DISSOLVE_MAX_PER_DAY = 3`：同日累計第 4 次溶解請求 ⇒ 不呼叫 LLM、不寫入。"""
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    materialize(load_fixture(), sim_root)

    # 日預算釘死在**假時鐘**的同一天（生產預設＝wall clock）⇒ 四次請求必屬同一預算日。
    monkeypatch.setattr(ex, "_DEFAULT_BUDGET", ex.ConsolidationBudget(now=lambda: BUDGET_DAY_UTC))
    assert ex.MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY == DISSOLVE_MAX_PER_DAY
    assert lt_diss.DISSOLVE_MAX_PER_DAY == DISSOLVE_MAX_PER_DAY

    # ── 同日第 1、2 次請求：兩條 active 且在 08:00 到期的線頭 ──
    make_thread_due(sim_root, AKANE_ACTIVE_THREAD, NOW_B)
    second_thread = new_active_thread(NOW_B)
    assert lt.active_count(AGENT) == lt.capacity(AGENT) == 2
    stub = ScriptedLLM(
        {AGENT: _actions_json(AKANE_ACTIVE_THREAD, second_thread)}, default=None
    )
    first = run_slot([AGENT], NOW_B, SLOT_B, llm_caller=stub)
    assert _statuses(first["results"]) == [ex.STATUS_CONSOLIDATED] * 2
    assert proxy.call_count == 2 and sage.call_count == 2

    # ── 同日第 3、4 次請求：下一 slot 前再建兩條（此刻 active=0 ⇒ 容量允許）──
    third_thread = new_active_thread(NOW_B_NEXT)
    fourth_thread = new_active_thread(NOW_B_NEXT)
    assert lt.active_count(AGENT) == 2
    stub2 = ScriptedLLM({AGENT: _actions_json(third_thread, fourth_thread)}, default=None)
    proxy_before_second = proxy.call_count  # ＝2（前兩次請求）
    second = run_slot([AGENT], NOW_B_NEXT, SLOT_B_NEXT, llm_caller=stub2)

    assert second["summaries"][AGENT]["woke"] is True
    assert len(second["tasks"]) == 2
    # `_BACKGROUND_TASKS` 是 **set**（迭代序非建立序）⇒ 一律以 `thread_id` 認人，不用位置。
    blocked_by_thread = {r.thread_id: r for r in second["results"]}
    assert set(blocked_by_thread) == {third_thread, fourth_thread}
    assert blocked_by_thread[third_thread].status == ex.STATUS_CONSOLIDATED
    assert blocked_by_thread[third_thread].llm_calls == 1
    blocked = blocked_by_thread[fourth_thread]
    assert blocked.status == ex.STATUS_SKIPPED_BUDGET, blocked.status
    assert blocked.llm_calls == 0, "第 4 次必須 0 次 LLM"
    assert blocked.reason == "budget_exhausted"

    # stub 計數不變：總共恰 3 次沉澱 LLM／3 次 SAGE（第 4 次既沒呼叫也沒落地）。
    assert proxy.call_count == proxy_before_second + 1, "第 4 次請求不得增加 stub 計數"
    assert proxy.call_count == 3
    assert sage.call_count == 3
    assert ex._DEFAULT_BUDGET.count_for(AGENT) == 3
    assert ex._DEFAULT_BUDGET.global_count == 3

    # 不寫入：第 4 條線頭 M1 內 0 溶解事件、`dissolved_at` 保持 null；其餘三條已寫回。
    assert fourth_thread not in [r["thread_id"] for r in dissolved_rows(sim_root)]
    blocked_state = lt.get_state(AGENT, fourth_thread)
    assert blocked_state["status"] == "completed"
    assert blocked_state["dissolved_at"] is None
    assert blocked_state["sage_fact_id"] is None
    assert len(dissolved_rows(sim_root)) == 3
    for thread_id in (AKANE_ACTIVE_THREAD, second_thread, third_thread):
        state = lt.get_state(AGENT, thread_id)
        assert state["dissolved_at"], thread_id
        assert state["sage_fact_id"]


# ══════════════════════════════════════════════════════════════
# WB-4：軟封存預設關 ⇒ 終態前不溶
# ══════════════════════════════════════════════════════════════


def test_wb4_soft_archive_default_off_keeps_advisory_thread_undissolved(
    sim_root, monkeypatch, writeback_seams
):
    """非終態（`active`）線頭即使已達 advisory 門檻 ⇒ **不溶解**（`allow_soft_archive` 預設 `False`）。"""
    enable_flag(monkeypatch)
    proxy, sage = writeback_seams
    spy = M2Spy(monkeypatch)
    materialize(load_fixture(), sim_root)
    make_thread_over_age(sim_root, AKANE_ACTIVE_THREAD, NOW_B)

    stub = ScriptedLLM({AGENT: _actions_json(AKANE_ACTIVE_THREAD)}, default=None)
    before = _agent_file(sim_root).read_text(encoding="utf-8")

    # ── 假時鐘一輪：M2 仍評估它，但只給 advisory 訊號（不變更、0 LLM）──
    out = run_slot([AGENT], NOW_B, SLOT_B, llm_caller=stub)
    assert out["summaries"][AGENT]["woke"] is False
    assert out["tasks"] == []
    assert stub.call_count == 0
    evaluation = spy.last(AKANE_ACTIVE_THREAD)
    assert evaluation["advisory"] is True, "超限線頭必須留下 advisory 痕跡（不得靜默）"
    assert evaluation["reason"] == "time_horizon_exceeded"
    assert evaluation["should_mutate"] is False
    assert evaluation["target_status"] == "active"
    assert evaluation["skipped"] is None

    # ── hook 被直接呼叫（M4 接縫的呼叫形式）⇒ 執行層 0 呼叫、0 寫入 ──
    results = drive_hook(AGENT, AKANE_ACTIVE_THREAD, "active")
    assert len(results) == 1
    assert results[0].status == ex.STATUS_SKIPPED_NOT_TERMINAL
    assert results[0].llm_calls == 0
    assert proxy.call_count == 0, "非終態線頭不得進入付費路徑"
    assert sage.call_count == 0
    assert dissolved_rows(sim_root) == []
    state = lt.get_state(AGENT, AKANE_ACTIVE_THREAD)
    assert state["status"] == "active"
    assert state["dissolved_at"] is None
    assert state["sage_fact_id"] is None
    assert _agent_file(sim_root).read_text(encoding="utf-8") == before, "本輪不得有任何 M1 寫入"

    # ── control：同一條線頭、唯一差異是 `allow_soft_archive=True` ⇒ 才會有溶解意圖 ──
    #    （證明擋下它的**就是**「軟封存預設關」，不是別的條件；純函式、0 LLM）
    wiring_view = w._evaluate(state, NOW_B, AGENT)
    assert wiring_view.should_mutate is False  # 接線層逐字傳 `allow_soft_archive=False`
    assert wiring_view.target_status.value == "active"
    control = lt_diss.evaluate_thread_dissolution(
        state, NOW_B, allow_soft_archive=True, agent_id=AGENT
    )
    assert control.should_mutate is True
    assert control.target_status.value == "dormant"
    assert control.reason.value == "time_horizon_exceeded"
