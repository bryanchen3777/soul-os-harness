# tests/soul/test_life_thread_m2_sunset.py
# LIFE-THREAD-M2-SUNSET — 確定性超期歸檔（Deterministic Sunset）守衛測試 ＋ 變異實驗。
"""確定性超期歸檔（Deterministic Sunset）的守衛測試矩陣 G1~G7 ＋ 變異實驗 M1~M5。

受測模組
──────────────────────────────────────────────────────────────
- `src/soul/life_thread_sunset.py`：**純決策層**（0 I/O、0 `src.*` import）。
- `src/soul/life_thread_orchestrator.py`：**接線層**（算出旗標 → 走既有溶解路徑）。

為什麼要有這個機制（一句話）
──────────────────────────────
活躍池有硬上限，而 M2 的存活軸在 `allow_soft_archive=False` 下**只產 advisory**
（`should_mutate=False`）⇒ active 線頭**永遠不會自發轉入終態** ⇒ 容量位元只進不出
⇒ 每天無法建立新線頭 ⇒ 門檻三（連續 7 天可見性）**沒有路徑**可達。
本機制補上**確定性**（不依賴 LLM 決策／機率）的釋放路徑。

G1~G7（逐條對應工單 §3.4）
───────────────────────────
- **G1 槽索引邊界**：`07:59:59`／`08:00:00`／`08:00:01`／`21:59:59`／`22:00:00`
  的槽序號**寫死期望值**（槽＝從 08:00 起算的時間區間；14h／10h 間隔不可用
  「天數 × 2」或「小時 / 12」估算）。
- **G2 `slots_since_last_creation`**：正確值 ＋ 「從未建立」的情形（回傳極大值，
  **不是 0** —— 0 會把「從未建立」誤讀成「剛剛建立」而**放行** sunset）。
- **G3 複合條件的四項邊界**（逐項）：age 恰好 21 天 → **False**（嚴格大於）；
  age 21 天 +1 秒 → 其餘三項全成立時 → True；`active_count < cap` → False；
  `slots == 2` → False、`slots == 3` → True；**非最舊**線頭 → False。
- **G4 決定性**：兩條線頭 `created_at` 相同 ⇒ 恆選 `thread_id` 字典序較小者；
  **跑 10 次結果一致**（且輸入順序刻意**不是**字典序 ⇒ 「挑任意一條」會被識破）。
- **G5 一次評估最多釋放一條**：多條同時合格時仍只回傳**一條**。
- **G6 走完整溶解路徑**：sunset 觸發時**有** reflection ＋ fact ＋ `status=completed`
  （**不是**靜默改狀態）；未觸發時 M1 檔**逐位元不變**。
- **G7 M2 純決策層零改動**：`src/soul/life_thread_dissolution.py` 的 `git diff` 為空。

M1~M5（變異實驗；逐條必須轉紅，工單 §3.5）
───────────────────────────────────────────
| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| M1 | age 邊界 `>` → `>=` | G3 |
| M2 | 移除「容量飽和」條件 | G3 |
| M3 | 「≥3 槽」改成「≥1 槽」 | G3 |
| M4 | 移除「只挑最舊」（挑輸入順序第一條） | G4 |
| M5 | sunset 路徑跳過 `dissolve_hook`（靜默改狀態） | G6 |

變異怎麼做（**不**碰 repo 原始檔、**不**起子行程）
──────────────────────────────────────────────────
1. **機械式**改寫**生產原始碼文字**（單一 token 替換；替換必須命中恰一次）。
2. 把改寫後的文字寫到 `tmp_path`，以 `importlib` 載入成**獨立模組物件**
   （⇒ repo 的檔案**零改動**、無需 git 還原）。
3. 用**同一段守衛斷言**（守衛測試與變異實驗**共用同一個函式**）去跑那份變異碼
   ⇒ 必須**拋 AssertionError**（＝ 守衛在變異下轉紅）。

🔴 **為什麼不用子行程跑 pytest**：repo 的行程護欄
`tests/infra/test_no_production_spawn_guard.py` 把 `tests/**` 內**每一個**
`subprocess.*` 呼叫點釘成 `(檔案, 行號)` 白名單（且**大小鎖定**）⇒ 本票**不得**
新增子行程 ⇒ 變異實驗改成**行程內**「同一段斷言 × 變異碼模組」。
（既有測試檔 `tests/soul/test_life_thread_origins_m4.py` 已有同型別的
`subprocess` 白名單條目；本檔**不新增**。）

平面不變量（沿用 SIM-TIME-001／M2-WRITEBACK-SIM-1／M2-CATCHUP-1 的同一套 primitive）
──────────────────────────────────────────────────────────────────────────────────
1. **假時鐘**：`w._now` 被換成可變 `FakeClock`；連續 slot 一律**直接換 `now`**
   再呼叫 ⇒ **0 `time.sleep`／0 真實等待**。唯一的 `await asyncio.sleep(0)` 是
   **讓 done-callback 跑完的 event-loop 讓渡**（0 秒 wall-clock）。
2. **記憶 fork**：資料根 → `SOUL_OS_DATA_DIR` 隔離目錄（`sim_root`）＋
   `reset_data_root()`；生產 `data/**` 全程唯讀（以 `lt.life_threads_path` 斷言
   落點不在 repo `data/`）。
3. **零 LLM**：沉澱 LLM 走 `w._resolve_llm_proxy` 的替身；另裝「真實 proxy 探針」
   （`lt_origins._find_llm_proxy` ⇒ 命中即紅燈）＋ `socket.create_connection`
   攔阻 ⇒ **0 真實外呼**。
4. **SAGE 一律 stub**：`w._write_fact` 以替身取代 ⇒ **不建 store、不寫真記憶庫**；
   M1 回填走**真實** `lt.append_dissolved` —— 那正是本票要觀測的寫回路徑。

跨檔 import 的取捨
──────────────────
`tests/soul/` 無 `__init__.py` ⇒ **不**跨檔 import 另一個測試模組 ⇒ 複製最小必要
primitive（沿用既有風格）。另：執行層（`life_thread_dissolution_exec`）的 importer
集合被 T53／T54 釘成**精確等值**（1 生產檔 ＋ 2 測試檔）⇒ 本檔**不**直接 import
它，改取接線模組已持有的別名（`ex = w.lt_exec`）⇒ **0 新 importer**。

觀測輸出
────────
`[SUNSET-OBS]` 開頭的離散觀測行（`pytest -s` 可見）只含 id／狀態／計數／憑據，
**永不**含 prompt 或敘述原文。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
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
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_sunset as sunset  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

#: 執行層別名（**不** import 該模組本身：T53／T54 釘死 importer 集合為精確等值）。
ex = w.lt_exec
assert ex.__name__ == "src.soul.life_thread_dissolution_exec"

# ══════════════════════════════════════════════════════════════
# 常數：檔案、假時鐘、觀測標的
# ══════════════════════════════════════════════════════════════

SUNSET_MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_sunset.py"
ORCHESTRATOR_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
M2_MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_dissolution.py"

LIFE_THREADS_FILENAME = "life_threads.jsonl"

#: 假時鐘：2026-10-21 08:00（morning 槽）。
NOW = datetime(2026, 10, 21, 8, 0, 0, tzinfo=LOCAL_TZ)
SLOT = "morning"

#: **寫死**的槽序號期望值（`date.toordinal()` 實測值；見 G1）。
#: 2026-10-21 → 739910；2026-10-22 → 739911；2026-09-18 → 739877。
ORD_2026_10_21 = 739910
ORD_2026_10_22 = 739911
ORD_2026_09_18 = 739877

#: G6 用的 agent（**假造的 id** ⇒ `lt.capacity()` 回預設 2 ⇒ 2 條 active 即飽和）。
AGENT_G6 = "agent_sunset_g6"
AGENT_G6_YOUNG = "agent_sunset_g6_young"

_SOUL_STUB = "（模擬人格語境；LIFE-THREAD-M2-SUNSET 固定字串，不讀生產 persona）"
_NARRATIVE = "她把這件事收好了：不是勝利，是終於可以放下。"

#: 沉澱 LLM 的預錄回覆（契約 §3.2：`{"dissolution", "meaning_kind"}`）。
DISSOLUTION_JSON = json.dumps(
    {"dissolution": _NARRATIVE, "meaning_kind": "relief"}, ensure_ascii=False
)

#: M4 的**空動作**預錄回應（e2e 用）：0 `transitioned` ⇒ M4 的 hook 接縫不被觸發。
_EMPTY_ACTIONS_JSON = json.dumps({"actions": []}, ensure_ascii=False)

_SYNTH_TITLE = "合成的待完成線頭"
_SYNTH_NARRATIVE = "她打算把這件事收尾。"

#: G6 用的「過期」跨度（**大於** `SUNSET_MAX_LIFETIME_DAYS`＝21 天）。
AGED_DAYS = 30


def _obs(label: str, **fields: Any) -> None:
    """離散觀測行（只記 id／狀態／計數／憑據；**永不**記 prompt 或敘述原文）。"""
    pairs = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[SUNSET-OBS] {label} {pairs}")


def _short(thread_id: str) -> str:
    return str(thread_id)[:8]


# ══════════════════════════════════════════════════════════════
# fixtures：隔離資料根、平面護欄、行程級狀態、假時鐘、寫回接縫
# ══════════════════════════════════════════════════════════════


@pytest.fixture()
def sim_root(tmp_path, monkeypatch):
    """模擬資料根：所有寫入的唯一落點（生產 `data/**` 全程唯讀）。"""
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

    `socket.create_connection` 攔成 AssertionError（httpx／httpcore／urllib 的實際連外
    入口）；**不攔** `socket.socket.connect`（Windows 的 Proactor loop 以
    `socketpair()` 建 self-pipe）。
    """
    probe = LiveLLMProbe()
    monkeypatch.setattr(lt_origins, "_find_llm_proxy", probe, raising=True)
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: _SOUL_STUB)
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "")
    monkeypatch.setenv(m5.CATCHUP_ENABLED_ENV, "")
    monkeypatch.setenv("LIFE_THREAD_BOOTSTRAP_ENABLED", "")
    monkeypatch.setenv("LIFE_THREAD_WHIM_ENABLED", "")

    def _blocked_connect(*args, **kwargs):
        raise AssertionError("LIFE-THREAD-M2-SUNSET：平面內不得連外（socket.create_connection 已攔）")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect, raising=True)

    yield probe
    assert probe.calls == [], f"路徑碰到真實 LLM proxy：{probe.calls}"


class FakeClock:
    """接線層內部時鐘（`w._now`）的替身：**直接換 `moment`** ⇒ 0 sleep、0 真實等待。"""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


@pytest.fixture()
def fake_clock(monkeypatch):
    """把 `w._now` 換成可變假時鐘（連續 slot ＝ 換 `moment` 再呼叫）。"""
    clock = FakeClock(NOW)
    monkeypatch.setattr(w, "_now", clock, raising=True)
    return clock


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


@pytest.fixture()
def writeback_seams(monkeypatch):
    """把兩條 seam 換成零 LLM／零 SAGE 替身，回傳 `(proxy, sage)`。

    - `w._resolve_llm_proxy` ⇒ 沉澱 LLM 替身（**真實** adapter 仍被走過）。
    - `w._write_fact` ⇒ SAGE 替身（不建 store、不寫真記憶庫）。
    - `w._append_dissolved` **不換**：M1 回填走真實 `lt.append_dissolved`（觀測面）。
    """
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy, raising=True)
    sage = SageStub()
    monkeypatch.setattr(w, "_write_fact", sage, raising=True)
    return proxy, sage


class ScriptedLLM:
    """M4 的預錄 LLM stub（**0 真實 LLM／0 網路**）：回固定 JSON。"""

    def __init__(self, response: str = _EMPTY_ACTIONS_JSON) -> None:
        self.response = response
        self.calls: List[str] = []

    def __call__(self, messages: Any, agent_id: str) -> Optional[str]:
        self.calls.append(agent_id)
        return self.response

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ══════════════════════════════════════════════════════════════
# 沙盒輔助：M1 真 API、隔離副本改寫、觀測
# ══════════════════════════════════════════════════════════════


def _agent_file(data_root: Path, agent_id: str) -> Path:
    return data_root / "soul" / agent_id / LIFE_THREADS_FILENAME


def _read_text(data_root: Path, agent_id: str) -> str:
    return _agent_file(data_root, agent_id).read_text(encoding="utf-8")


def local_lines(data_root: Path, agent_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in _read_text(data_root, agent_id).splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _assert_in_sim_root(sim_root: Path, agent_id: str) -> None:
    """真 M1 落點自證：線頭檔必須在隔離根內（生產 `data/**` 零接觸）。"""
    resolved = str(lt.life_threads_path(agent_id).resolve())
    assert resolved.startswith(str(sim_root.resolve())), resolved


def _make_active_threads(agent_id: str, count: int, *, due: bool) -> List[str]:
    """用**真 M1 API** 建立 `count` 條 active 線頭（回傳 thread_id 依建立順序）。

    `due=False` ⇒ `check_after_ts` 推到未來 ⇒ 該輪 M3 判 **SLEEP** ⇒ **M4 不執行**
    ⇒ 觀測到的背景任務**只可能**來自 sunset 的溶解路徑（0 M4 混淆項）。
    """
    ids: List[str] = []
    for index in range(count):
        check_after = (
            NOW + timedelta(days=365)
            if not due
            else NOW - timedelta(hours=1)
        )
        thread_id = lt.create_thread(
            agent_id,
            f"{_SYNTH_TITLE}{index}",
            _SYNTH_NARRATIVE,
            "necessity_driven",
            check_after_ts=check_after.astimezone(timezone.utc).isoformat(),
        )
        assert thread_id, "建立線頭失敗（M1 容量或參數拒絕）"
        ids.append(thread_id)
    return ids


def _backdate_threads(data_root: Path, agent_id: str, thread_ids: Sequence[str], days: int) -> None:
    """把這些線頭的 `created_at` 往前推 `days` 天（**只動隔離副本**）。

    🔴 必須改「該線頭的**所有列**」：只改 fold 勝者會讓另一列變成勝者，其
    `created_at` 仍是原值 ⇒ sunset 的 age 會被算錯。
    """
    created = (NOW - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    rows = local_lines(data_root, agent_id)
    touched = 0
    for row in rows:
        if row.get("thread_id") not in thread_ids:
            continue
        row["created_at"] = created
        touched += 1
    assert touched > 0, "線頭不存在"
    _agent_file(data_root, agent_id).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _run_pipeline_once(
    orchestrator_module: Any,
    agent_ids: Sequence[str],
    now: datetime,
    slot: str,
    *,
    llm_caller: Any,
) -> Dict[str, Any]:
    """**假時鐘**一輪：直接注入 `now` 走 `run_slot_pipeline` ＋ 收斂背景沉澱任務。"""
    out: Dict[str, Any] = {"summaries": {}, "tasks": [], "results": []}

    async def _driver() -> None:
        out["summaries"] = await orchestrator_module.run_slot_pipeline(
            list(agent_ids), now, slot, llm_caller=llm_caller
        )
        tasks = list(w._BACKGROUND_TASKS)
        out["tasks"] = tasks
        if tasks:
            out["results"] = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # 只讓 done-callback 跑完的 event-loop 讓渡（0 秒 wall-clock）

    asyncio.run(_driver())
    assert w.pending_task_count() == 0, "背景沉澱任務未收斂"
    return out


# ══════════════════════════════════════════════════════════════
# G1：槽索引邊界（寫死期望值）
# ══════════════════════════════════════════════════════════════


def _assert_g1_slot_boundaries(mod: Any) -> None:
    """G1 的完整斷言段（守衛測試與變異實驗**共用同一段**）。"""
    # 07:59:59 ⇒ **前一日**第 1 槽（night 槽尚未結束）
    assert (
        mod.slot_index(datetime(2026, 10, 21, 7, 59, 59, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == (ORD_2026_10_21 - 1) * 2 + 1
    )
    # 08:00:00 整點本身 ⇒ 當日第 0 槽（morning）
    assert (
        mod.slot_index(datetime(2026, 10, 21, 8, 0, 0, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == ORD_2026_10_21 * 2
    )
    # 08:00:01 ⇒ 同樣是當日第 0 槽
    assert (
        mod.slot_index(datetime(2026, 10, 21, 8, 0, 1, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == ORD_2026_10_21 * 2
    )
    # 21:59:59 ⇒ **仍是**當日第 0 槽（morning；槽間隔 14h／10h 不均）
    assert (
        mod.slot_index(datetime(2026, 10, 21, 21, 59, 59, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == ORD_2026_10_21 * 2
    )
    # 22:00:00 ⇒ 進當日第 1 槽（night）
    assert (
        mod.slot_index(datetime(2026, 10, 21, 22, 0, 0, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == ORD_2026_10_21 * 2 + 1
    )
    # 次日 08:00 ⇒ 次日第 0 槽（與前一日 night 槽相隔恰 1 槽）
    assert (
        mod.slot_index(datetime(2026, 10, 22, 8, 0, 0, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == ORD_2026_10_22 * 2
    )
    # 前一日 22:00 ⇒ 前一日第 1 槽
    assert (
        mod.slot_index(datetime(2026, 9, 18, 22, 0, 0, tzinfo=LOCAL_TZ), tz=LOCAL_TZ)
        == ORD_2026_09_18 * 2 + 1
    )
    # UTC 輸入 ⇒ 一律換算到本地時區（08:00Z ＝ 04:00 EDT ⇒ 屬前一日 night 槽）
    assert (
        mod.slot_index(datetime(2026, 10, 21, 8, 0, 0, tzinfo=timezone.utc), tz=LOCAL_TZ)
        == (ORD_2026_10_21 - 1) * 2 + 1
    )


def test_g1_slot_index_boundaries_are_pinned():
    """G1：槽索引的五個邊界時刻（＋兩組延伸）**寫死期望值**。"""
    _assert_g1_slot_boundaries(sunset)
    _obs(
        "G1",
        morning_slot_080000=ORD_2026_10_21 * 2,
        night_slot_220000=ORD_2026_10_21 * 2 + 1,
        before_morning=(ORD_2026_10_21 - 1) * 2 + 1,
        still_morning_215959=ORD_2026_10_21 * 2,
    )


# ══════════════════════════════════════════════════════════════
# G2：slots_since_last_creation（含「從未建立」）
# ══════════════════════════════════════════════════════════════


def _assert_g2_slots_since(mod: Any) -> None:
    """G2 的完整斷言段。"""
    # 從未建立過 ⇒ **極大值**（不是 0：0 會被誤讀成「剛剛建立」而放行 sunset）
    assert mod.slots_since_last_creation([], now=NOW, tz=LOCAL_TZ) == mod.SUNSET_NO_CREATION_SLOTS
    assert mod.slots_since_last_creation(
        [{"created_at": None}], now=NOW, tz=LOCAL_TZ
    ) == mod.SUNSET_NO_CREATION_SLOTS

    # 「最後一次建立」＝ `created_at` **最大**值（不是最小值）
    events = [
        {"created_at": "2026-10-21T06:00:00+00:00"},   # 02:00 EDT ⇒ 前一日 night 槽（最新）
        {"created_at": "2026-10-20T12:00:00+00:00"},   # 08:00 EDT ⇒ 10/20 morning 槽
        {"created_at": "2026-10-18T12:00:00+00:00"},   # 更舊
    ]
    # 最後一次建立 ＝ 10/21 02:00 EDT（落在 10/20 的 night 槽）；now ＝ 10/21 morning 槽
    # ⇒ 槽序號差 = (739910*2) − (739909*2 + 1) = 1
    assert mod.slots_since_last_creation(events, now=NOW, tz=LOCAL_TZ) == 1

    # 同日相鄰兩槽 ⇒ 2 槽（10/20 morning 槽 → 10/21 morning 槽）
    same_day = [{"created_at": "2026-10-20T12:00:00+00:00"}]
    assert mod.slots_since_last_creation(same_day, now=NOW, tz=LOCAL_TZ) == 2

    # 下限 0（now 早於最後一次建立 ⇒ 不得為負）
    future = [{"created_at": "2026-10-22T12:00:00+00:00"}]
    assert mod.slots_since_last_creation(future, now=NOW, tz=LOCAL_TZ) == 0


def test_g2_slots_since_last_creation_values():
    """G2：槽數正確值 ＋ 「從未建立」回傳極大值。"""
    _assert_g2_slots_since(sunset)
    _obs(
        "G2",
        never_created=sunset.SUNSET_NO_CREATION_SLOTS,
        two_slots=sunset.slots_since_last_creation(
            [{"created_at": "2026-10-20T12:00:00+00:00"}], now=NOW, tz=LOCAL_TZ
        ),
    )


# ══════════════════════════════════════════════════════════════
# G3：複合條件的四項邊界（逐項）
# ══════════════════════════════════════════════════════════════


def _thread(thread_id: str, created_at: datetime, status: str = "active") -> Dict[str, Any]:
    return {
        "thread_id": thread_id,
        "status": status,
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": created_at.astimezone(timezone.utc).isoformat(),
    }


def _assert_g3_conditions(mod: Any) -> None:
    """G3 的完整斷言段（守衛 3 的四項邊界逐項）。

    🔴 門檻值**寫死**（21 天／3 槽），**不**從模組常數推導 ⇒ 連「把常數改掉」
    這種變異也會被識破（否則守衛會跟著變異一起漂移 ⇒ 盲腸守衛）。
    """
    cap = 2
    count = 2
    max_days = 21
    min_slots = 3
    slots_ok = min_slots

    # ── 常數本身必須等於規格值（工單 §3.1）──
    assert mod.SUNSET_MAX_LIFETIME_DAYS == max_days, mod.SUNSET_MAX_LIFETIME_DAYS
    assert mod.SUNSET_MIN_NO_NEW_THREAD_SLOTS == min_slots, mod.SUNSET_MIN_NO_NEW_THREAD_SLOTS

    # ── 條件 1：age **嚴格大於** 21 天 ──
    exactly = _thread("t-age-eq", NOW - timedelta(days=max_days))
    assert (
        mod.should_sunset(
            exactly, now=NOW, agent_active_count=count, agent_cap=cap, slots_since_new_thread=slots_ok
        )
        is False
    ), "age 恰好 21 天必須 False（嚴格大於）"
    plus_one_second = _thread("t-age-plus", NOW - timedelta(days=max_days, seconds=1))
    assert (
        mod.should_sunset(
            plus_one_second,
            now=NOW,
            agent_active_count=count,
            agent_cap=cap,
            slots_since_new_thread=slots_ok,
        )
        is True
    ), "age 21 天 +1 秒且其餘三項全成立 ⇒ 必須 True"

    # ── 條件 2：容量飽和（`active_count >= cap`）──
    aged = _thread("t-aged", NOW - timedelta(days=30))
    assert (
        mod.should_sunset(
            aged, now=NOW, agent_active_count=1, agent_cap=cap, slots_since_new_thread=slots_ok
        )
        is False
    ), "active_count < cap ⇒ 必須 False（沒有容量壓力就沒有釋放的必要）"
    assert (
        mod.should_sunset(
            aged, now=NOW, agent_active_count=cap, agent_cap=cap, slots_since_new_thread=slots_ok
        )
        is True
    )

    # ── 條件 3：連續槽數門檻（≥3 槽；2 槽不足、3 槽剛好）──
    assert (
        mod.should_sunset(
            aged,
            now=NOW,
            agent_active_count=count,
            agent_cap=cap,
            slots_since_new_thread=min_slots - 1,
        )
        is False
    ), "槽數門檻 -1（=2 槽）⇒ 必須 False"
    assert (
        mod.should_sunset(
            aged,
            now=NOW,
            agent_active_count=count,
            agent_cap=cap,
            slots_since_new_thread=min_slots,
        )
        is True
    )

    # ── 條件 4：必須是該 agent 的**最舊** active 線頭 ──
    older = _thread("t-oldest", NOW - timedelta(days=40))
    younger = _thread("t-younger", NOW - timedelta(days=30))
    assert (
        mod.should_sunset(
            younger,
            now=NOW,
            agent_active_count=count,
            agent_cap=cap,
            slots_since_new_thread=slots_ok,
            oldest_thread_id="t-oldest",
        )
        is False
    ), "非最舊線頭 ⇒ 必須 False"

    # ── 非 active 一律 False（`dormant` 永不 sunset：INV-1）──
    for status in ("dormant", "completed", "abandoned"):
        assert (
            mod.should_sunset(
                _thread(f"t-{status}", NOW - timedelta(days=40), status=status),
                now=NOW,
                agent_active_count=count,
                agent_cap=cap,
                slots_since_new_thread=slots_ok,
            )
            is False
        ), f"status={status} 不得 sunset"


def test_g3_composite_condition_boundaries():
    """G3：複合條件的四項邊界（逐項）。"""
    _assert_g3_conditions(sunset)
    _obs(
        "G3",
        max_lifetime_days=sunset.SUNSET_MAX_LIFETIME_DAYS,
        min_slots=sunset.SUNSET_MIN_NO_NEW_THREAD_SLOTS,
        age_exactly_21=False,
        age_plus_1s=True,
        not_saturated=False,
        slots_minus_1=False,
        slots_at_threshold=True,
        not_oldest=False,
    )


# ══════════════════════════════════════════════════════════════
# G4：決定性（同 `created_at` ⇒ 恆選 thread_id 字典序較小者）
# ══════════════════════════════════════════════════════════════


def _assert_g4_determinism(mod: Any) -> None:
    """G4 的完整斷言段。

    🔴 輸入順序**刻意不是** `thread_id` 字典序（先把 `zzz` 放前面）⇒ 若實作改成
    「挑輸入順序第一條」（變異 M4），本斷言必須轉紅。
    """
    created = NOW - timedelta(days=30)
    threads = [
        _thread("zzz-tie", created),
        _thread("aaa-tie", created),
    ]
    for _ in range(10):
        target = mod.pick_sunset_target(
            threads,
            now=NOW,
            agent_active_count=2,
            agent_cap=2,
            slots_since_new_thread=mod.SUNSET_MIN_NO_NEW_THREAD_SLOTS,
        )
        assert target is not None, "兩條都合格時必須挑到一條"
        assert target["thread_id"] == "aaa-tie", (
            f"同 created_at 必須恆選 thread_id 字典序較小者：{target['thread_id']}"
        )

    # 不同 `created_at` ⇒ 挑**最舊**者
    mixed = [
        _thread("b-newer", NOW - timedelta(days=30)),
        _thread("a-older", NOW - timedelta(days=40)),
    ]
    for _ in range(10):
        target = mod.pick_sunset_target(
            mixed,
            now=NOW,
            agent_active_count=2,
            agent_cap=2,
            slots_since_new_thread=mod.SUNSET_MIN_NO_NEW_THREAD_SLOTS,
        )
        assert target is not None
        assert target["thread_id"] == "a-older", target["thread_id"]


def test_g4_target_selection_is_deterministic():
    """G4：同 `created_at` ⇒ 恆選 thread_id 字典序較小者；跑 10 次一致。"""
    _assert_g4_determinism(sunset)
    _obs("G4", runs=10, tie_pick="aaa-tie", oldest_pick="a-older")


# ══════════════════════════════════════════════════════════════
# G5：一次評估最多釋放一條
# ══════════════════════════════════════════════════════════════


def _assert_g5_single_release(mod: Any) -> None:
    """G5 的完整斷言段。"""
    threads = [
        _thread("t-30", NOW - timedelta(days=30)),
        _thread("t-40", NOW - timedelta(days=40)),
        _thread("t-50", NOW - timedelta(days=50)),
    ]
    target = mod.pick_sunset_target(
        threads,
        now=NOW,
        agent_active_count=3,
        agent_cap=2,
        slots_since_new_thread=mod.SUNSET_MIN_NO_NEW_THREAD_SLOTS,
    )
    assert target is not None
    assert isinstance(target, dict), "必須回傳**單一** dict（不是清單）"
    assert target["thread_id"] == "t-50", target["thread_id"]
    # 再挑一次 ⇒ 仍是**同一條**（機制本身不記「已處理」；釋放由 M1 的狀態改變表達）
    again = mod.pick_sunset_target(
        threads,
        now=NOW,
        agent_active_count=3,
        agent_cap=2,
        slots_since_new_thread=mod.SUNSET_MIN_NO_NEW_THREAD_SLOTS,
    )
    assert again is not None and again["thread_id"] == target["thread_id"]


def test_g5_at_most_one_release_per_evaluation():
    """G5：一次評估最多釋放**一條**（不破壞蔡戈尼效應的精緻感）。"""
    _assert_g5_single_release(sunset)
    _obs("G5", eligible=3, released=1)


# ══════════════════════════════════════════════════════════════
# G6：走完整溶解路徑（reflection ＋ fact ＋ status=completed）
# ══════════════════════════════════════════════════════════════


def _assert_g6_dissolution_path(
    orchestrator_module: Any,
    sim_root: Path,
    agent_id: str,
    *,
    aged: bool,
    proxy: Any,
    sage: Any,
) -> Dict[str, Any]:
    """G6 的完整斷言段（**守衛測試與變異實驗共用**）。

    `aged=True` ⇒ 過期線頭 ⇒ **必須** sunset 並走完整溶解路徑。
    `aged=False` ⇒ 年輕線頭 ⇒ **不得** sunset，M1 檔**逐位元不變**。
    """
    _assert_in_sim_root(sim_root, agent_id)
    thread_ids = _make_active_threads(agent_id, 2, due=False)
    if aged:
        _backdate_threads(sim_root, agent_id, thread_ids, AGED_DAYS)
    before_text = _read_text(sim_root, agent_id)
    before_rows = len(local_lines(sim_root, agent_id))

    llm = ScriptedLLM()
    out = _run_pipeline_once(
        orchestrator_module, [agent_id], NOW, SLOT, llm_caller=llm
    )
    summary = out["summaries"][agent_id]

    if not aged:
        assert "sunset" not in summary, f"年輕線頭不得 sunset：{summary.get('sunset')}"
        assert _read_text(sim_root, agent_id) == before_text, "未觸發時 M1 檔必須逐位元不變"
        assert proxy.call_count == 0 and sage.call_count == 0, "未觸發時 0 LLM／0 SAGE"
        assert not out["tasks"], "未觸發時 0 背景沉澱任務"
        _obs("G6-young", agent=agent_id, sunset=0, llm=0, sage=0)
        return summary

    # ── 觸發時：必須走**完整**溶解路徑 ──
    assert "sunset" in summary, f"過期線頭必須被 sunset：{summary}"
    info = summary["sunset"]
    assert info["target_status"] == "completed", info
    assert info["reason"] == "checkpoints_exhausted", info
    assert info["thread_id"] in thread_ids, info
    assert info["age_days"] > sunset.SUNSET_MAX_LIFETIME_DAYS, info
    assert info["slots_since_new_thread"] >= sunset.SUNSET_MIN_NO_NEW_THREAD_SLOTS, info

    # M1：轉入終態（`status=completed`）
    state = lt.get_state(agent_id, info["thread_id"])
    assert state is not None and state["status"] == "completed", state

    # M1：溶解回填（`dissolved_at` ＋ `sage_fact_id`）⇒ 證明**不是**靜默改狀態
    assert state["dissolved_at"], "必須留下非 null dissolved_at（沉澱已完成）"
    assert state["sage_fact_id"], "必須留下非空 sage_fact_id（fact 已落地）"

    # reflection ＋ fact：各恰 1 次
    assert proxy.call_count == 1, f"沉澱 LLM 只能恰 1 次：{proxy.call_count}"
    assert sage.call_count == 1, f"SAGE 落地只能恰 1 次：{sage.call_count}"
    assert sage.agents == [agent_id]
    assert sage.facts[0]["predicate"] == ex.FACT_PREDICATE
    assert sage.facts[0]["object"] == _NARRATIVE

    # M1 事件：恰 1 筆 `dissolved`
    dissolved = [r for r in local_lines(sim_root, agent_id) if r.get("event_type") == lt.EVENT_DISSOLVED]
    assert len(dissolved) == 1, dissolved
    assert len(local_lines(sim_root, agent_id)) == before_rows + 2, "只准新增 transition ＋ dissolved 兩列"

    _obs(
        "G6-aged",
        agent=agent_id,
        thread=_short(info["thread_id"]),
        age_days=round(float(info["age_days"]), 2),
        slots=info["slots_since_new_thread"],
        status=state["status"],
        llm=proxy.call_count,
        sage=sage.call_count,
        dissolved_events=len(dissolved),
    )
    return summary


def test_g6_sunset_goes_through_the_full_dissolution_path(
    sim_root, monkeypatch, writeback_seams, fake_clock
):
    """G6：過期線頭 ⇒ sunset ⇒ **reflection ＋ fact ＋ `status=completed`**。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    assert w.consolidation_enabled() is True
    proxy, sage = writeback_seams
    fake_clock.moment = NOW
    _assert_g6_dissolution_path(
        m5, sim_root, AGENT_G6, aged=True, proxy=proxy, sage=sage
    )


def test_g6b_young_thread_is_never_sunset_and_writes_nothing(
    sim_root, monkeypatch, writeback_seams, fake_clock
):
    """G6b：年輕線頭（age ≤ 21 天）⇒ **不** sunset，M1 檔**逐位元不變**、0 LLM／0 SAGE。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    proxy, sage = writeback_seams
    fake_clock.moment = NOW
    _assert_g6_dissolution_path(
        m5, sim_root, AGENT_G6_YOUNG, aged=False, proxy=proxy, sage=sage
    )


# ══════════════════════════════════════════════════════════════
# G7：M2 純決策層零改動
# ══════════════════════════════════════════════════════════════


#: `src/soul/life_thread_dissolution.py`（M2 純決策層）的**凍結雜湊**。
#:
#: 🔴 本票的前提（工單 §0.4／§3.2）：M2 **純決策層零改動**。
#:    本值＝ HEAD（`86c60ee`）版該檔的 sha256，**並已用 `git show HEAD:<path>` 的
#:    blob 雜湊交叉驗證相等** ⇒ 「檔案雜湊未變」⟺「`git diff` 為空」。
#:    （工單 §3.4 守衛 7 允許「AST 斷言**或**檔案雜湊斷言」；本檔**刻意不用
#:    `subprocess` 跑 `git diff` —— repo 的行程護欄把 `tests/**` 內**每一個**
#:    `subprocess.*` 呼叫點釘成 `(檔案, 行號)` 白名單且大小鎖定 ⇒ 本票不得新增。）
M2_FROZEN_SHA256 = "cc397fe37b1f61aa1740fdcab65821555516396e2b422202903fe5de50c75ed9"


def test_g7_m2_decision_layer_is_untouched():
    """G7：`src/soul/life_thread_dissolution.py` 必須**未經改動**（凍結雜湊）。

    M2 純決策層**零改動**是本票的前提（工單 §0.4／§3.2）⇒ 一旦有人動了它，
    本測試立刻轉紅。雜湊值已與 HEAD（`86c60ee`）的 blob 交叉驗證相等。
    """
    import hashlib

    digest = hashlib.sha256(M2_MODULE_PATH.read_bytes()).hexdigest()
    assert digest == M2_FROZEN_SHA256, (
        f"M2 純決策層必須零改動（凍結 sha256 {M2_FROZEN_SHA256}）：實得 {digest}"
    )
    _obs("G7", m2_sha256=digest, matches_head=True)


# ══════════════════════════════════════════════════════════════
# 變異實驗 M1~M5（逐條必須轉紅）
# ══════════════════════════════════════════════════════════════


def _load_variant(source_text: str, tmp_path: Path, module_name: str) -> Any:
    """把（變異後的）原始碼文字載入成**獨立模組物件**（repo 檔案零改動）。"""
    path = tmp_path / f"{module_name}.py"
    path.write_text(source_text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _mutate(source_text: str, old: str, new: str) -> str:
    """機械式單點替換（**必須命中恰一次**；命中 0 次或 >1 次 ⇒ 直接失敗）。"""
    count = source_text.count(old)
    assert count == 1, f"變異錨點必須命中恰一次（實得 {count}）：{old!r}"
    return source_text.replace(old, new)


#: 變異 1：age 邊界由 `>` 改成 `>=`（守衛 3 必須轉紅）。
_M1_OLD = "    if not age > SUNSET_MAX_LIFETIME_DAYS:"
_M1_NEW = "    if not age >= SUNSET_MAX_LIFETIME_DAYS:"

#: 變異 2：移除「容量飽和」條件（守衛 3 必須轉紅）。
_M2_OLD = "    if agent_active_count < agent_cap:"
_M2_NEW = "    if False:  # MUTATION: 容量飽和條件被移除"

#: 變異 3：「≥3 槽」改成「≥1 槽」（守衛 3 必須轉紅）。
_M3_OLD = "SUNSET_MIN_NO_NEW_THREAD_SLOTS: int = 3"
_M3_NEW = "SUNSET_MIN_NO_NEW_THREAD_SLOTS: int = 1"

#: 變異 4：移除「只挑最舊」（挑輸入順序第一條）（守衛 4 必須轉紅）。
_M4_OLD = "    oldest = min(active, key=_oldest_key)"
_M4_NEW = "    oldest = active[0]  # MUTATION: 不再只挑最舊"

#: 變異 5：sunset 路徑跳過 `dissolve_hook`（靜默改狀態）（守衛 6 必須轉紅）。
_M5_OLD = "                    origin_type=SUNSET_ORIGIN_TYPE,\n                    now=now,\n                    dissolve_hook=dissolve_hook,"
_M5_NEW = "                    origin_type=SUNSET_ORIGIN_TYPE,\n                    now=now,\n                    dissolve_hook=None,  # MUTATION: 靜默改狀態"


def test_m1_mutation_age_boundary_turns_guard3_red(tmp_path):
    """變異 1：age 邊界 `>` → `>=` ⇒ **守衛 3 必須轉紅**。"""
    source = SUNSET_MODULE_PATH.read_text(encoding="utf-8")
    mutated = _load_variant(_mutate(source, _M1_OLD, _M1_NEW), tmp_path, "sunset_mut_m1")
    with pytest.raises(AssertionError) as excinfo:
        _assert_g3_conditions(mutated)
    assert "age 恰好 21 天必須 False" in str(excinfo.value), str(excinfo.value)
    _obs("M1", mutation="age > → age >=", guard="G3", turned_red=True)


def test_m2_mutation_capacity_condition_turns_guard3_red(tmp_path):
    """變異 2：移除「容量飽和」⇒ **守衛 3 必須轉紅**。"""
    source = SUNSET_MODULE_PATH.read_text(encoding="utf-8")
    mutated = _load_variant(_mutate(source, _M2_OLD, _M2_NEW), tmp_path, "sunset_mut_m2")
    with pytest.raises(AssertionError) as excinfo:
        _assert_g3_conditions(mutated)
    assert "active_count < cap" in str(excinfo.value), str(excinfo.value)
    _obs("M2", mutation="移除容量飽和條件", guard="G3", turned_red=True)


def test_m3_mutation_slot_threshold_turns_guard3_red(tmp_path):
    """變異 3：「≥3 槽」改成「≥1 槽」⇒ **守衛 3 必須轉紅**。"""
    source = SUNSET_MODULE_PATH.read_text(encoding="utf-8")
    mutated = _load_variant(_mutate(source, _M3_OLD, _M3_NEW), tmp_path, "sunset_mut_m3")
    with pytest.raises(AssertionError) as excinfo:
        _assert_g3_conditions(mutated)
    # 門檻常數被改 ⇒ 先被 G3 的「常數必須等於規格值」斷言攔下（早於槽數邊界段）。
    assert "SUNSET_MIN_NO_NEW_THREAD_SLOTS" in str(excinfo.value), str(excinfo.value)
    _obs("M3", mutation="門檻 3 → 1", guard="G3", turned_red=True)


def test_m4_mutation_oldest_only_turns_guard4_red(tmp_path):
    """變異 4：移除「只挑最舊」⇒ **守衛 4 必須轉紅**。"""
    source = SUNSET_MODULE_PATH.read_text(encoding="utf-8")
    mutated = _load_variant(_mutate(source, _M4_OLD, _M4_NEW), tmp_path, "sunset_mut_m4")
    with pytest.raises(AssertionError) as excinfo:
        _assert_g4_determinism(mutated)
    assert "字典序較小者" in str(excinfo.value), str(excinfo.value)
    _obs("M4", mutation="挑輸入順序第一條", guard="G4", turned_red=True)


def test_m5_mutation_skipping_dissolve_hook_turns_guard6_red(
    tmp_path, sim_root, monkeypatch, writeback_seams, fake_clock
):
    """變異 5：sunset 路徑跳過 `dissolve_hook`（靜默改狀態）⇒ **守衛 6 必須轉紅**。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    proxy, sage = writeback_seams
    fake_clock.moment = NOW
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    mutated = _load_variant(
        _mutate(source, _M5_OLD, _M5_NEW), tmp_path, "orchestrator_mut_m5"
    )
    with pytest.raises(AssertionError) as excinfo:
        _assert_g6_dissolution_path(
            mutated, sim_root, AGENT_G6, aged=True, proxy=proxy, sage=sage
        )
    assert "dissolved_at" in str(excinfo.value), str(excinfo.value)
    _obs("M5", mutation="跳過 dissolve_hook", guard="G6", turned_red=True)
