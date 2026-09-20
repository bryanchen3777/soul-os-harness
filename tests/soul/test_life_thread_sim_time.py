# tests/soul/test_life_thread_sim_time.py
# SIM-TIME-001 — 生活線頭引擎「模擬取證平面」：假時鐘 ＋ 記憶副本 ＋ 零 LLM 的可重放 harness。
#
# 受測模組：`src/soul/life_thread_orchestrator.py`（既有入口，簽名不得改）
#           ＋ `src/soul/life_thread_wake_gate.py`（M3）
#           ＋ `src/soul/life_thread_dissolution.py`（M2，唯讀評估）
#           ＋ `src/soul/life_threads.py`（M1，記憶副本）
#
# 🔴 取證平面＝模擬；生產 slot 只報錯、不開新驗證項。
#
# 本檔的三個不變量（矩陣 S1–S5 全數共用）：
#   1. **假時鐘**：`now` 由測試直接注入（`datetime`，local tz `America/New_York`），
#      slot 邊界 08:00／22:00。連續 slot ＝ 直接換 `now` 再呼叫，**0 sleep、0 等真實時間**。
#   2. **記憶副本**：金樣 → `SOUL_OS_DATA_DIR` 隔離目錄（沿用 `tests/conftest.py` 的
#      全域 data-root 隔離 fixture，**未關閉**）。失敗即丟副本，生產 `data/**` 全程唯讀。
#   3. **零 LLM**：所有呼叫一律傳 `llm_caller=`（預錄 JSON stub，內部累計呼叫次數）；
#      另裝「真實 proxy 探針」攔 `life_thread_origins._find_llm_proxy`——矩陣內若決策路徑
#      碰到真 proxy，探針計數 > 0 ⇒ 測試失敗。
#
# ── 金樣（frozen golden）──────────────────────────────────────
#   tests/fixtures/life_thread_snapshot_20260919_0800.json  （as-of 09/19 08:00 local）
#   tests/fixtures/life_thread_snapshot_20260920_0800.json  （as-of 09/20 08:00 local）
# 由生產 append-only 事件日誌 `data/soul/<agent>/life_threads.jsonl` 以**時間截斷**推導：
# 只保留 `updated_at <= cutoff_utc` 的事件，且只投影決策相關欄位（見 `SNAPSHOT_FIELDS`；
# **不含** `title` / `narrative_content` 等敘事內容）。provenance（來源路徑、逐檔 sha256、
# 截斷時刻、推導方式、邊界註記）隨金樣凍結在 fixture 內；本檔的 provenance 測試在有生產
# log 時**重推導並斷言一致**，沒有時 `pytest.skip` 並附明確理由（本機有 ⇒ 會真的跑）。
#
# ── S1 的「修法前世界」＝載入**真實舊碼**（git blob 釘死身分）────────────
# 工單 S1 要求：金樣 A ＋ `now=2026-09-19 08:00` ⇒ **10/10 SLEEP reason=ACTIVE_POOL_SATURATED
# （＝修法前那個世界）**。那個世界已不存在於現行 `src/**`（求值序已由 `73d8c2d` 改為
# 「世界碰撞 → 線頭到期 → 容量飽和 → 留白」），而本票禁止改 src ⇒ S1 取出**修法前閘門的真實
# 位元組**原地載入來重放，**不使用任何手寫的「舊碼模型」**：
#   `git show 73d8c2d^:src/soul/life_thread_wake_gate.py` → 寫到 `tmp_path`（**不進 repo**）
#   → `importlib.util.spec_from_file_location` 載入 → 直接呼叫它的 `evaluate_wake_gate(...)`
#   （該版多一個有預設值的參數 `unresolved_tensions` ⇒ 不傳即可）。該版為 pure stdlib、
#   **0 `src.*` import**（測試另以文字斷言把這一點釘死），故可脫離套件原地載入。
#
# **身分釘死（雙重，逐次斷言）**：
#   - `git rev-parse 73d8c2d^:src/soul/life_thread_wake_gate.py` ＝ blob
#     `670891d2113ce59b9cc7025768707ef48ada289d`；
#   - 該 blob 位元組 sha256 ＝ `387de1edcf2937af33dc070f129f42fbe4407201a990925470b60c80a26d2a59`
#     （30851 bytes）；
#   - 求值序文字標記：含「步驟 1：容量防線」、不含修法後才出現的「訊號先於容量」。
#
# **為何這就是 09/19 08:00 生產行程實際在跑的那份碼**（逐項可查，非推論）：
#   - 生產 PID 18984 於 **2026-09-17 20:26:23** 啟動（`logs/ENGINEERING_STATE.md` 的
#     COLDSTART-ENABLE-1 登記逐字）；Python 在啟動時載入模組 ⇒ 它載入的閘門版本就是當時磁碟上
#     的那一份。該檔的前一次變更是 `d34c333`（09/15 00:54），下一次變更即修法 `73d8c2d`
#     （09/19 02:00）⇒ **09/17 20:26:23 載入的位元組 = 本 blob（`73d8c2d^`）**。
#   - 修法 `73d8c2d` 於 09/19 02:00 提交但「**需重啟才生效**」，實際部署（Owner 逐字授權、
#     經提權一次性排程任務）是 **09/19 09:39:32**（`:8000` PID 18984 → 24220，同一登記）⇒
#     09/19 08:00 該槽仍由 09/17 20:26 啟動的舊行程（＝舊模組）評估；09/19 22:00 才是修法後
#     首個 slot（登記逐字：「今晚 22:00 slot 為修法首次全員實測」）。
#   - ⚠️ 與轉述的出入（以檔案事實為準）：09/19 02:00 的 `73d8c2d` **確實改動了該檔案本身**
#     （不是「期間僅 docs／registry 提交」）。舊碼之所以還在跑，是因為**行程自 09/17 20:26 起
#     未重啟**（記憶體裡是啟動時載入的版本），而不是磁碟檔案期間未變動。結論不變、歸因更精確。
#   - 交叉佐證（同一份舊碼，另一個時點）：舊碼在金樣 A 的狀態上必得 10×`ACTIVE_POOL_SATURATED`
#     （10 位皆 2 active＝cap 2）；而 ENGINEERING_STATE 的 GATE-ORDER DEFECT 登記記載 09/18 08:00
#     為 `7×ACTIVE_POOL_SATURATED＋3×REFLECTION_SLOT_CLEAR`＝10/10 SLEEP —— 一致的解釋是當時
#     anna/mahiru/miku 各只有 1 條 active（未滿 cap ⇒ 走步驟 4 留白），到 09/19 08:00 三者已各
#     2 條 ⇒ 全員飽和。（09/18 的狀態不在本票兩份金樣內，故此處僅作登記層級的旁證，不另設情境。）
#
# S1b ＝ S1 的**對照組（control／contrast）**：同一份真實狀態（金樣 A）、同一 `now`，唯一差異是
# 閘門版本（現行 `src/` 碼）⇒ 結果由 10 SLEEP 變 7 WAKE／3 SLEEP。這是本票最有價值的單一證據：
# **同一份真實狀態、僅差求值序**，就是修法（due/world 先於 capacity）的語意差異。
# 全程不改 `src/**`、不寫 `data/**`、不引入生產碼的測試分支、**0 手寫舊碼模型**。
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_dissolution as lt_diss  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_wake_gate as lt_gate  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

# ══════════════════════════════════════════════════════════════
# 常數：金樣、截斷、假時鐘
# ══════════════════════════════════════════════════════════════

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"
FIXTURE_A = FIXTURES_DIR / "life_thread_snapshot_20260919_0800.json"
FIXTURE_B = FIXTURES_DIR / "life_thread_snapshot_20260920_0800.json"

PROD_SOUL_DIR = _REPO_ROOT / "data" / "soul"
LIFE_THREADS_FILENAME = "life_threads.jsonl"

#: 生產 log 在 2026-09-18/19/20 期間有生活線頭事件的 10 位 agent（依字母序＝決定性順序）。
SNAPSHOT_AGENTS: Tuple[str, ...] = (
    "agent_akane",
    "agent_anna",
    "agent_aoi",
    "agent_mahiru",
    "agent_mai",
    "agent_miku",
    "agent_ram",
    "agent_rem",
    "agent_ruka",
    "agent_yua",
)

#: 金樣投影欄位（決策相關；**不含**敘事內容）。
#: `updated_at` ＝ M1 `fold()` 的排序鍵（`_is_usable_entry` 亦要求它存在，缺則整列被丟棄）；
#: `created_at` ＝ M2 `evaluate_thread_dissolution` 步驟 0 的必填驗證欄位（缺 ⇒ invalid、0 候選）；
#: `origin_type` ＝ M3 到期判定回傳的 `origin_type` 來源；
#: `dissolved_at` ＝ M2 步驟 1「已溶解」冪等哨兵（金樣內恆為 null，S5 唯讀斷言用）。
SNAPSHOT_FIELDS: Tuple[str, ...] = (
    "thread_id",
    "event_seq",
    "event_type",
    "origin_type",
    "status",
    "check_after_ts",
    "created_at",
    "updated_at",
    "dissolved_at",
)

#: 工單指定的兩個「真實狀態」截斷邊界（UTC；local tz ＝ America/New_York／EDT −04:00）。
CUTOFF_A_UTC = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)  # ＝ 09/19 08:00 local
CUTOFF_B_UTC = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)  # ＝ 09/20 08:00 local

#: 假時鐘（slot 邊界）。`NOW_B` 必須是 **08:00:00 整**（不是 08:00:10.88）——
#: 生產該槽的推進事件落在 `08:00:1x` 之後，用整點重放才與金樣 B 的截斷一致（見 fixture 邊界註記）。
NOW_A = datetime(2026, 9, 19, 8, 0, 0, tzinfo=LOCAL_TZ)
NOW_B = datetime(2026, 9, 20, 8, 0, 0, tzinfo=LOCAL_TZ)
NOW_B_NEXT = datetime(2026, 9, 20, 22, 0, 0, tzinfo=LOCAL_TZ)  # 09/20 的下一個 slot（night）

#: 09/20 08:00 槽的生產記錄（逐項對帳基準）：恰 ruka／rem WAKE，其餘 8 位 SLEEP。
S2_WAKE_AGENTS = ("agent_rem", "agent_ruka")
S2_SLEEP_REASONS = {
    "agent_akane": "REFLECTION_SLOT_CLEAR",  # active=1 < cap=2、檢驗點未到期
    "agent_anna": "ACTIVE_POOL_SATURATED",
    "agent_aoi": "ACTIVE_POOL_SATURATED",
    "agent_mahiru": "ACTIVE_POOL_SATURATED",
    "agent_mai": "ACTIVE_POOL_SATURATED",
    "agent_miku": "ACTIVE_POOL_SATURATED",
    "agent_ram": "ACTIVE_POOL_SATURATED",
    "agent_yua": "ACTIVE_POOL_SATURATED",
}

REASON_SATURATED = lt_gate.REASON_ACTIVE_POOL_SATURATED
REASON_DUE = lt_gate.REASON_CHECKPOINT_DUE_WAKE

# ── 修法前閘門（S1 用；身分以 git blob 釘死）──────────────────────
#: 修法 commit（求值序＝碰撞 → 到期 → 容量 → 留白；生產於 09/19 09:39:32 重啟後才生效）。
OLD_GATE_FIX_COMMIT = "73d8c2d"
#: 修法前的 revision（`73d8c2d^`）：09/19 08:00 該槽生產行程實際在跑的版本。
OLD_GATE_REV = "73d8c2d^"
OLD_GATE_PATH = "src/soul/life_thread_wake_gate.py"
#: `git rev-parse 73d8c2d^:src/soul/life_thread_wake_gate.py`（blob id，測試逐次斷言）。
OLD_GATE_BLOB_ID = "670891d2113ce59b9cc7025768707ef48ada289d"
#: 該 blob 的位元組 sha256 與長度（測試逐次斷言）。
OLD_GATE_SHA256 = "387de1edcf2937af33dc070f129f42fbe4407201a990925470b60c80a26d2a59"
OLD_GATE_BYTES = 30851
#: 原地載入用的模組名（避免與 `src.soul.*` 撞名）。
PREFIX_GATE_MODULE_NAME = "sim_time_001_prefix_wake_gate"

#: S3 的溶解候選（akane 的 completed 線頭；金樣 B 內 status=completed 且 dissolved_at=null）。
AKANE_CANDIDATE_THREAD_ID = "2917e901-757b-4d4c-9b2e-8188413e5449"
AKANE_ACTIVE_THREAD_ID = "0ce7e589-eebc-4203-9c48-626f65819433"
RUKA_DUE_THREAD_ID = "57361d43-da92-4bfa-8077-1514682b2e9e"
REM_DUE_THREAD_ID = "40d980e2-4c00-491f-a6ad-fc8715539cdd"

_SOUL_STUB = "（模擬人格語境；SIM-TIME-001 固定字串，不讀生產 persona）"
_NARRATIVE = "清晨的模擬推進：她想起昨夜的雨，決定今天去散步。"
_TITLE = "模擬新線頭"

#: 預錄 LLM 回覆（§5.1 契約：`{"actions":[{"op": ...}]}`）。
JSON_CREATE = json.dumps(
    {
        "actions": [
            {
                "op": "create",
                "title": _TITLE,
                "narrative_content": _NARRATIVE,
                "next_check_hours": 6,
                "origin_type": "necessity_driven",
                "share_target": "none",
            }
        ]
    },
    ensure_ascii=False,
)


def _advance_json(thread_id: str, hours: int = 6) -> str:
    return json.dumps(
        {
            "actions": [
                {
                    "op": "advance",
                    "thread_id": thread_id,
                    "narrative_content": _NARRATIVE,
                    "next_check_hours": hours,
                }
            ]
        },
        ensure_ascii=False,
    )


# ══════════════════════════════════════════════════════════════
# 金樣：讀取、推導、投影、落地（記憶副本）
# ══════════════════════════════════════════════════════════════


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def derive_snapshot(
    cutoff_utc: datetime,
    *,
    prod_dir: Path = PROD_SOUL_DIR,
    agents: Sequence[str] = SNAPSHOT_AGENTS,
) -> Dict[str, Any]:
    """由生產 append-only 事件日誌**時間截斷**推導快照（唯讀）。

    規則（逐字）：保留 `updated_at` 可解析且 `<= cutoff_utc` 的事件（依檔案原順序），
    投影至 `SNAPSHOT_FIELDS`；`updated_at` 缺失／不可解析的事件一律丟棄（不猜時間）。
    """
    rows_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    shas: Dict[str, str] = {}
    missing: List[str] = []
    for agent_id in agents:
        path = prod_dir / agent_id / LIFE_THREADS_FILENAME
        if not path.is_file():
            missing.append(agent_id)
            rows_by_agent[agent_id] = []
            continue
        shas[agent_id] = _sha256_file(path)
        kept: List[Dict[str, Any]] = []
        for entry in _read_jsonl(path):
            ts = _parse_iso(entry.get("updated_at"))
            if ts is None or ts > cutoff_utc:
                continue
            kept.append({field: entry.get(field) for field in SNAPSHOT_FIELDS})
        rows_by_agent[agent_id] = kept
    return {"agents": rows_by_agent, "source_sha256": shas, "missing": missing}


def load_fixture(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_agents(snapshot: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    return snapshot["agents"]


def materialize(snapshot: Dict[str, Any], data_root: Path) -> Path:
    """把金樣複製到隔離資料根（`<root>/soul/<agent>/life_threads.jsonl`）。"""
    soul_dir = data_root / "soul"
    for agent_id, rows in fixture_agents(snapshot).items():
        target = soul_dir / agent_id
        target.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        (target / LIFE_THREADS_FILENAME).write_text(text, encoding="utf-8")
    return soul_dir


def local_lines(data_root: Path, agent_id: str) -> List[Dict[str, Any]]:
    return _read_jsonl(data_root / "soul" / agent_id / LIFE_THREADS_FILENAME)


def fold_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """M1 `fold()` 規則 2 的等價實作（`(updated_at, event_seq)` 字典序最大者勝出）。"""
    states: Dict[str, Tuple[Tuple[str, int], Dict[str, Any]]] = {}
    for row in rows:
        thread_id = row.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        key = (str(row.get("updated_at") or ""), int(row.get("event_seq") or 0))
        prev = states.get(thread_id)
        if prev is None or key >= prev[0]:
            states[thread_id] = (key, row)
    return {tid: payload[1] for tid, payload in states.items()}


def status_map(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return {tid: row.get("status") for tid, row in fold_rows(rows).items()}


def due_thread_ids(rows: Iterable[Dict[str, Any]], now: datetime) -> List[str]:
    """金樣在 `now` 的到期集合（M3 判定 1 的同口徑：active 且 `check_after_ts <= now`）。"""
    ts = now.timestamp()
    out: List[str] = []
    for tid, row in fold_rows(rows).items():
        if row.get("status") != "active":
            continue
        parsed = _parse_iso(row.get("check_after_ts"))
        if parsed is not None and parsed.timestamp() <= ts:
            out.append(tid)
    return sorted(out)


# ══════════════════════════════════════════════════════════════
# fixtures：隔離資料根、零 LLM 探針、零連外、零溶解寫回
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
def _clean_idempotency():
    """`_LAST_PROCESSED` 是 process-global 冪等鍵：每個測試前後清空。"""
    m5.reset_state()
    yield
    m5.reset_state()


class LiveLLMProbe:
    """真實 proxy 探針：記錄「決策路徑是否試圖取得真實 LLM proxy」。

    `src/soul/life_thread_origins.py` 的 LLM 接縫是 `_find_llm_proxy()`；
    本探針替換它並**回 `None`**（fail-quiet，不 raise、不連外），矩陣內任何一次命中
    都會被 `_no_live_llm` 的 teardown 斷言抓成紅燈。
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self) -> Any:
        self.calls.append({"via": "_find_llm_proxy"})
        return None


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    """矩陣級不變量：0 真實 LLM proxy、0 連外、人格語境固定、M2 寫回接線釘在關。

    1. 真實 proxy 探針（見 `LiveLLMProbe`）→ 命中即紅燈。
    2. `socket.create_connection` 攔成 AssertionError → 0 連外（httpx／httpcore／urllib
       的實際連外入口）。**不攔** `socket.socket.connect`：Windows 的 asyncio
       ProactorEventLoop 以 `socket.socketpair()`（Python 層 connect）建立 self-pipe，
       攔下去會連 event loop 都建不起來。
    3. `lt_origins.load_soul_context` 固定字串 → 不讀生產 persona、不引入外部狀態。
    4. `LIFE_THREAD_CONSOLIDATION_ENABLED=""` → M2 溶解寫回接線**證明為關**
       （本票 0 溶解寫回；S5 據此斷言 M1 唯讀）。`LIFE_THREAD_BOOTSTRAP_ENABLED=""`
       → 第三條喚醒路徑（§4.4）整段不執行，矩陣只驗 S1–S5。
    """
    probe = LiveLLMProbe()
    monkeypatch.setattr(lt_origins, "_find_llm_proxy", probe, raising=True)
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: _SOUL_STUB)
    monkeypatch.setenv("LIFE_THREAD_CONSOLIDATION_ENABLED", "")
    monkeypatch.setenv("LIFE_THREAD_BOOTSTRAP_ENABLED", "")

    def _blocked_connect(*args, **kwargs):
        raise AssertionError("SIM-TIME-001：矩陣內不得連外（socket.create_connection 已攔）")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect, raising=True)

    yield probe
    assert probe.calls == [], f"決策路徑碰到真實 LLM proxy：{probe.calls}"


# ══════════════════════════════════════════════════════════════
# 假時鐘 runner ＋ 預錄 LLM stub
# ══════════════════════════════════════════════════════════════


class ScriptedLLM:
    """預錄 LLM stub：**0 真實 LLM／0 網路**，逐 agent 回預錄 JSON 字串並累計呼叫。"""

    def __init__(
        self,
        responses: Optional[Dict[str, str]] = None,
        default: Optional[str] = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.default = default
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, messages: Any, agent_id: str) -> Optional[str]:
        self.calls.append({"agent_id": agent_id})
        return self.responses.get(agent_id, self.default)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def called_agents(self) -> List[str]:
        return [c["agent_id"] for c in self.calls]


def run_slot(
    agent_ids: Sequence[str],
    now: datetime,
    slot: str,
    *,
    llm_caller: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """**假時鐘**：直接注入 `now`，一個 slot 一輪（0 sleep、0 真實時間等待）。"""
    return asyncio.run(m5.run_slot_pipeline(agent_ids, now, slot, llm_caller=llm_caller))


def created_from_summaries(summaries: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    return {
        agent_id: len((summary.get("origin_round") or {}).get("created") or [])
        for agent_id, summary in summaries.items()
    }


def total_created(summaries: Dict[str, Dict[str, Any]]) -> int:
    return sum(created_from_summaries(summaries).values())


def advanced_total(summaries: Dict[str, Dict[str, Any]]) -> int:
    return sum(
        len((summary.get("origin_round") or {}).get("advanced") or [])
        for summary in summaries.values()
    )


def wake_agents(summaries: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(a for a, s in summaries.items() if s.get("woke") is True)


def sleep_agents(summaries: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(a for a, s in summaries.items() if s.get("woke") is False)


def reason_map(summaries: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {a: s.get("reason") for a, s in summaries.items()}


def _git_bytes(*args: str) -> bytes:
    """唯讀取 git 物件位元組（**不寫 repo**、不改索引）。失敗即紅燈（不吞）。"""
    proc = subprocess.run(
        ["git", *args], cwd=str(_REPO_ROOT), capture_output=True, check=False
    )
    assert proc.returncode == 0, (
        f"git {' '.join(args)} 失敗（rc={proc.returncode}）："
        f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
    )
    return proc.stdout


@pytest.fixture()
def prefix_wake_gate(tmp_path):
    """S1 用：載入**修法前閘門的真實位元組**（`73d8c2d^`），並把身分釘死。

    流程（全部唯讀，落地只在 `tmp_path`）：
      1. `git rev-parse 73d8c2d^:src/soul/life_thread_wake_gate.py` ⇒ 斷言 blob id；
      2. `git show 73d8c2d^:src/soul/life_thread_wake_gate.py` ⇒ 取**原始位元組**
         ⇒ 斷言 sha256 與長度；
      3. 位元組寫到 `tmp_path`（**不進 repo**）⇒ `importlib.util.spec_from_file_location` 載入；
      4. 文字斷言該版為 pure stdlib（0 `src.*` import）且求值序為**修法前**：
         含「步驟 1：容量防線」、不含「訊號先於容量」。

    `.git` 不存在 ⇒ `pytest.skip` 並附明確理由（本機有 `.git` ⇒ 本測試必須真的跑，不得 skip）。
    """
    if not (_REPO_ROOT / ".git").exists():
        pytest.skip(
            "本機無 `.git`（例如原始碼封存／sdist 環境）：無法取出 `73d8c2d^` 的閘門 blob，"
            "故無法重放「修法前那個世界」。此測試只在有 git 歷史的機器上有意義。"
        )
    revision = f"{OLD_GATE_REV}:{OLD_GATE_PATH}"
    has_object = subprocess.run(
        ["git", "cat-file", "-e", revision], cwd=str(_REPO_ROOT), capture_output=True
    ).returncode == 0
    if not has_object:
        pytest.skip(
            f"本機 git 物件庫不含 `{revision}`（淺層複製或物件缺失）⇒ 取不到修法前閘門的"
            "真實位元組。本機（完整 clone）必須真的跑、不得 skip。"
        )

    blob_id = _git_bytes("rev-parse", revision).decode("utf-8").strip()
    assert blob_id == OLD_GATE_BLOB_ID, f"修法前閘門 blob id 不符：{blob_id}"

    source_bytes = _git_bytes("show", revision)
    assert len(source_bytes) == OLD_GATE_BYTES, f"位元組長度不符：{len(source_bytes)}"
    assert hashlib.sha256(source_bytes).hexdigest() == OLD_GATE_SHA256, "修法前閘門 sha256 不符"

    source_text = source_bytes.decode("utf-8")
    assert "from src" not in source_text and "import src" not in source_text, (
        "修法前閘門出現 `src.*` import ⇒ 不得脫離套件原地載入"
    )
    assert "步驟 1：容量防線" in source_text, "修法前閘門缺『步驟 1：容量防線』標記"
    assert "訊號先於容量" not in source_text, "取到的不是修法前版本（含修法後標記）"

    target = tmp_path / f"prefix_wake_gate_{OLD_GATE_FIX_COMMIT}_parent.py"
    target.write_bytes(source_bytes)  # 原始位元組，不做任何行尾／編碼轉換

    spec = importlib.util.spec_from_file_location(PREFIX_GATE_MODULE_NAME, target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclasses` 在解析 `from __future__ import annotations` 的字串註解時會查
    # `sys.modules[cls.__module__]` ⇒ 載入前先註冊，載入後移除（不殘留 process-global 狀態）。
    sys.modules[PREFIX_GATE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(PREFIX_GATE_MODULE_NAME, None)


def _pre_fix_gate_decision(prefix_gate, agent_id: str, now: datetime, slot: str):
    """用**真舊碼**對金樣副本做一次閘門判定（真舊碼的 `active_threads` 由 M1 `fold()` 取得）。"""
    folded = lt.fold(agent_id)
    active = [r for r in folded.values() if r.get("status") == "active"]
    cap = lt.capacity(agent_id)
    return prefix_gate.evaluate_wake_gate(agent_id, now.timestamp(), slot, active, cap), active, cap


# ══════════════════════════════════════════════════════════════
# S0：金樣 provenance（有生產 log 時重推導並斷言一致）
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "fixture_path, cutoff",
    [(FIXTURE_A, CUTOFF_A_UTC), (FIXTURE_B, CUTOFF_B_UTC)],
    ids=["as_of_20260919_0800", "as_of_20260920_0800"],
)
def test_s0_fixture_matches_production_time_truncation(fixture_path, cutoff):
    """金樣 ＝ 生產 log 的時間截斷投影；本機有 log ⇒ 重推導並逐項比對（無則 skip）。"""
    if not PROD_SOUL_DIR.is_dir():
        pytest.skip(
            "本機無生產 `data/soul/**` 生活線頭日誌：無法重推導金樣 provenance"
            "（此測試只在有生產記憶的機器上有效；矩陣其餘項目不依賴它）"
        )
    snapshot = load_fixture(fixture_path)
    derived = derive_snapshot(cutoff)

    assert derived["missing"] == [], f"金樣來源 agent 缺檔：{derived['missing']}"
    assert snapshot["agents"] == derived["agents"], "金樣內容與生產時間截斷推導不一致"
    assert snapshot["provenance"]["source_sha256"] == derived["source_sha256"], (
        "金樣 provenance 的來源 sha256 與現行生產 log 不一致（生產 log 已被追加 ⇒ 需重凍結金樣）"
    )
    assert snapshot["provenance"]["cutoff_utc"] == cutoff.isoformat()
    assert snapshot["provenance"]["fields_kept"] == list(SNAPSHOT_FIELDS)
    # 敘事欄位不得入金樣（金樣＝決策平面，不是敘事平面）。
    for rows in snapshot["agents"].values():
        for row in rows:
            assert set(row) == set(SNAPSHOT_FIELDS)
            assert "title" not in row and "narrative_content" not in row


def test_s0_golden_b_boundary_reproduces_s2_expectation():
    """邊界自我驗證：金樣 B 在 09/20 08:00 的到期集合**恰** {ruka, rem}。

    這是「用能重現 S2 期望值的方式選邊界」的可執行證據：
      - 截斷邊界 `updated_at <= 2026-09-20T12:00:00Z`（＝ 09/20 08:00 local 整點）；
      - 09/20 08:00 槽自身的推進事件落盤於 08:00:1x 之後 ⇒ 自然被截掉，不會混進金樣；
      - `now` 用 **08:00:00 整**：anna/aoi/mahiru/ram 等線頭的 `check_after_ts`
        恰落在 `08:00:10.884004Z`（＝生產該槽注入的 now 微秒），若改用 `08:00:10.88`
        重放，它們會一併到期 ⇒ 與 09/20 08:00 生產記錄（僅 ruka／rem 落盤）不符。
    """
    snapshot = load_fixture(FIXTURE_B)
    due = {
        agent_id: due_thread_ids(rows, NOW_B)
        for agent_id, rows in fixture_agents(snapshot).items()
    }
    assert sorted(a for a, ids in due.items() if ids) == list(S2_WAKE_AGENTS)
    assert due["agent_ruka"] == [RUKA_DUE_THREAD_ID]
    assert due["agent_rem"] == [REM_DUE_THREAD_ID]


# ══════════════════════════════════════════════════════════════
# S1：金樣 A ＋ now=09/19 08:00 ⇒ 10/10 SLEEP ACTIVE_POOL_SATURATED
# ══════════════════════════════════════════════════════════════


def test_s1_golden_a_pre_fix_gate_all_ten_saturated(sim_root, monkeypatch, prefix_wake_gate):
    """S1：**真舊碼（`73d8c2d^`，blob `670891d2…`／sha256 `387de1ed…`）** ＋ **真金樣 A**、
    `now=2026-09-19 08:00` ⇒ **10/10 SLEEP `ACTIVE_POOL_SATURATED`、created=0**
    （＝ 09/19 08:00 生產記錄逐字一致；該槽由 09/17 20:26 啟動、載入舊模組的行程評估）。

    (a) **逐 agent 直接呼叫真舊碼** `evaluate_wake_gate(...)`：判定與 reason 逐字斷言；
    (b) **整輪管線**：把 orchestrator 的 M3 接縫綁到**同一份真舊碼** ⇒ 量測 `woke/created/LLM`
        （09/19 08:00 生產該槽 0 落盤、0 LLM 成本）。

    兩者用的都是**真舊碼位元組**（`prefix_wake_gate` fixture 已釘死 blob id 與 sha256），
    沒有任何手寫的「舊碼模型」。
    """
    snapshot = load_fixture(FIXTURE_A)
    materialize(snapshot, sim_root)

    # ── (a) 真舊碼 × 真金樣：逐 agent 閘門判定 ─────────────────
    decisions: Dict[str, str] = {}
    expected_reason = "ACTIVE_POOL_SATURATED"  # 逐字（不引用模組常數，避免同義反覆）
    for agent_id in SNAPSHOT_AGENTS:
        decision, active, cap = _pre_fix_gate_decision(
            prefix_wake_gate, agent_id, NOW_A, "morning"
        )
        assert lt.capacity(agent_id) == 2
        assert len(active) == 2  # 結構條件：2 active == cap 2 ⇒ 修法前容量防線（步驟 1）命中
        assert decision.should_wake is False
        decisions[agent_id] = decision.reason
    assert decisions == {a: expected_reason for a in SNAPSHOT_AGENTS}

    # ── (b) 同一份真舊碼 × 整輪管線：0 WAKE / 0 create / 0 LLM ──
    monkeypatch.setattr(m5, "lt_gate", prefix_wake_gate, raising=True)
    stub = ScriptedLLM(default=None)
    summaries = run_slot(SNAPSHOT_AGENTS, NOW_A, "morning", llm_caller=stub)

    assert set(summaries) == set(SNAPSHOT_AGENTS)
    assert wake_agents(summaries) == []
    assert reason_map(summaries) == {a: expected_reason for a in SNAPSHOT_AGENTS}
    assert all(summaries[a]["woke"] is False for a in SNAPSHOT_AGENTS)
    # created=0：無任何 create 落盤，且副本檔內連一列都沒新增（該槽 0 落盤）。
    assert total_created(summaries) == 0
    for agent_id, rows in fixture_agents(snapshot).items():
        assert len(local_lines(sim_root, agent_id)) == len(rows)
    # 0 LLM：修法前世界裡沒有任何 agent 進到 M4。
    assert stub.call_count == 0


def test_s1b_control_current_gate_golden_a_yields_seven_wakes(sim_root):
    """**S1 的對照組（control／contrast）**：同一份真實狀態（金樣 A）、同一 `now`，
    唯一差異是閘門版本 —— 換成**現行 `src/soul/life_thread_wake_gate.py`** ⇒ 7 WAKE／3 SLEEP。

    ⇒ 同一份真實狀態、**僅差求值序**，結果由 S1 的「10 SLEEP」變成「7 WAKE／3 SLEEP」：
    這就是修法（`73d8c2d`：due/world 先於 capacity）的**語意差異**本身。
    akane/aoi/mai/ram/rem/ruka/yua（檢驗點早已過期）在修法前被容量防線短路吃掉、修法後 WAKE；
    anna/mahiru/miku 兩版皆 SLEEP（其檢驗點在 09/19 08:00 之後 ⇒ 新碼亦不 WAKE，
    舊碼則因飽和先於訊號而 SLEEP）。
    """
    snapshot = load_fixture(FIXTURE_A)
    materialize(snapshot, sim_root)
    stub = ScriptedLLM(default=None)

    summaries = run_slot(SNAPSHOT_AGENTS, NOW_A, "morning", llm_caller=stub)

    assert wake_agents(summaries) == [
        "agent_akane",
        "agent_aoi",
        "agent_mai",
        "agent_ram",
        "agent_rem",
        "agent_ruka",
        "agent_yua",
    ]
    assert sleep_agents(summaries) == ["agent_anna", "agent_mahiru", "agent_miku"]
    assert all(summaries[a]["reason"] == REASON_DUE for a in wake_agents(summaries))
    assert all(summaries[a]["reason"] == REASON_SATURATED for a in sleep_agents(summaries))
    assert total_created(summaries) == 0  # 7 位 WAKE 者皆已滿 cap ⇒ create 全被 M1 拒


# ══════════════════════════════════════════════════════════════
# S2：金樣 B ＋ now=09/20 08:00 ⇒ 與生產記錄逐項對帳
# ══════════════════════════════════════════════════════════════


def test_s2_golden_b_replay_matches_production_0920_0800(sim_root):
    """S2：金樣 B、`now=2026-09-20 08:00` ⇒ 恰 ruka／rem WAKE `CHECKPOINT_DUE_WAKE`，
    其餘 SLEEP；WAKE=2／SLEEP=8／created=0（與當時生產記錄逐項對得上）。

    生產登記（09/20 08:00 槽）逐字：`gate=10`、`WAKE=2 / SLEEP=8`；8 個 SLEEP ＝
    7×`ACTIVE_POOL_SATURATED` ＋ `agent_akane` `REFLECTION_SLOT_CLEAR`；該槽輪次
    `llm=2 / created=0 / advanced=2 / transitioned=0 / skipped=0`；akane `active=1 cap=2`、
    `dissolved_candidates=1`。本測試把這些逐項釘死（stub 依當時落盤形狀回 `advance`）。
    """
    snapshot = load_fixture(FIXTURE_B)
    materialize(snapshot, sim_root)
    stub = ScriptedLLM(
        {
            "agent_ruka": _advance_json(RUKA_DUE_THREAD_ID, hours=6),
            "agent_rem": _advance_json(REM_DUE_THREAD_ID, hours=6),
        },
        default=None,
    )
    baseline = {
        agent_id: len(local_lines(sim_root, agent_id))
        for agent_id in fixture_agents(snapshot)
    }

    summaries = run_slot(SNAPSHOT_AGENTS, NOW_B, "morning", llm_caller=stub)

    assert wake_agents(summaries) == list(S2_WAKE_AGENTS)
    assert sleep_agents(summaries) == sorted(S2_SLEEP_REASONS)
    assert len(wake_agents(summaries)) == 2 and len(sleep_agents(summaries)) == 8
    assert summaries["agent_ruka"]["reason"] == REASON_DUE
    assert summaries["agent_rem"]["reason"] == REASON_DUE
    for agent_id, reason in S2_SLEEP_REASONS.items():
        assert summaries[agent_id]["reason"] == reason
        assert summaries[agent_id]["woke"] is False

    # created=0：無 create 落盤，副本檔內 created 事件數不變；ruka/rem 各新增**恰 1** 筆事件。
    assert total_created(summaries) == 0
    assert advanced_total(summaries) == 2
    for agent_id in SNAPSHOT_AGENTS:
        rows = local_lines(sim_root, agent_id)
        created_lines = [r for r in rows if r.get("event_type") == "created"]
        expected_created = len(
            [
                r
                for r in fixture_agents(snapshot)[agent_id]
                if r.get("event_type") == "created"
            ]
        )
        assert len(created_lines) == expected_created
        if agent_id in S2_WAKE_AGENTS:
            assert len(rows) == baseline[agent_id] + 1
            assert rows[-1].get("event_type") == "updated"
        else:
            assert len(rows) == baseline[agent_id]

    # 零 LLM 洩漏：恰 2 次 stub 呼叫（ruka／rem），真實 proxy 探針 0 命中（teardown 再驗一次）。
    assert stub.call_count == 2
    assert sorted(stub.called_agents) == list(S2_WAKE_AGENTS)


# ══════════════════════════════════════════════════════════════
# S3：金樣 B ⇒ akane active=1／cap=2（空位仍在）＋ 溶解候選 ≥ 1
# ══════════════════════════════════════════════════════════════


def test_s3_golden_b_akane_free_slot_and_dissolution_candidate(sim_root, monkeypatch):
    """S3：金樣 B、`now=2026-09-20 08:00` ⇒ akane active=1／cap=2（空位仍在）、溶解候選 ≥ 1。

    M2（`life_thread_dissolution`）**唯讀**：另裝入口監看器記錄每次評估的
    `thread_id / should_mutate`，證明候選數不是憑空斷言（**唯讀、不改 src**）。
    """
    snapshot = load_fixture(FIXTURE_B)
    materialize(snapshot, sim_root)

    seen: List[Dict[str, Any]] = []
    real_batch = lt_diss.evaluate_batch_dissolution

    def spy(threads, current_time, **kwargs):
        evaluations = real_batch(threads, current_time, **kwargs)
        seen.append(
            {
                "thread_ids": [t.get("thread_id") for t in threads if isinstance(t, dict)],
                "candidates": [e.thread_id for e in evaluations if e.should_mutate],
            }
        )
        return evaluations

    monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy, raising=True)
    stub = ScriptedLLM({"agent_ruka": _advance_json(RUKA_DUE_THREAD_ID)}, default=None)

    summaries = run_slot(SNAPSHOT_AGENTS, NOW_B, "morning", llm_caller=stub)

    folded = lt.fold("agent_akane")
    active = [r for r in folded.values() if r.get("status") == "active"]
    assert len(active) == 1
    assert active[0]["thread_id"] == AKANE_ACTIVE_THREAD_ID
    assert lt.capacity("agent_akane") == 2
    assert len(active) < lt.capacity("agent_akane")  # 空位仍在（未被容量防線擋住）

    assert summaries["agent_akane"]["woke"] is False
    assert summaries["agent_akane"]["reason"] == "REFLECTION_SLOT_CLEAR"
    assert summaries["agent_akane"]["dissolved_candidates"] >= 1

    akane_view = [rec for rec in seen if AKANE_ACTIVE_THREAD_ID in rec["thread_ids"]]
    assert len(akane_view) == 1, "M2 入口未被呼叫（或呼叫次數不符）"
    assert akane_view[0]["candidates"] == [AKANE_CANDIDATE_THREAD_ID]


# ══════════════════════════════════════════════════════════════
# S4：空位存在 ⇒ 允許 create；滿 cap ⇒ 不得 create
# ══════════════════════════════════════════════════════════════


def _make_akane_due_now(sim_root, now: datetime) -> None:
    """把 akane 剩餘 active 線頭的 `check_after_ts` 設為過去（**只動隔離副本**）。

    只改 `fold()` 會選中的那一列（`(updated_at, event_seq)` 字典序最大者）——
    改其他列不會影響 fold 後的當前狀態。
    """
    rows = local_lines(sim_root, "agent_akane")
    past = (now - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    winner = max(
        (r for r in rows if r.get("thread_id") == AKANE_ACTIVE_THREAD_ID),
        key=lambda r: (str(r.get("updated_at") or ""), int(r.get("event_seq") or 0)),
    )
    winner["check_after_ts"] = past
    target = sim_root / "soul" / "agent_akane" / LIFE_THREADS_FILENAME
    target.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    assert due_thread_ids(rows, now) == [AKANE_ACTIVE_THREAD_ID]


def test_s4_golden_b_free_slot_allows_create(sim_root):
    """S4：金樣 B ＋ akane 檢驗點設為過去、`now=` 下一個 slot（09/20 22:00，night）
    ⇒ **空位存在時允許 create（akane created=1）**。"""
    snapshot = load_fixture(FIXTURE_B)
    materialize(snapshot, sim_root)
    _make_akane_due_now(sim_root, NOW_B_NEXT)
    stub = ScriptedLLM(default=JSON_CREATE)

    summaries = run_slot(SNAPSHOT_AGENTS, NOW_B_NEXT, "night", llm_caller=stub)

    assert summaries["agent_akane"]["woke"] is True
    assert summaries["agent_akane"]["reason"] == REASON_DUE
    created = created_from_summaries(summaries)
    assert created["agent_akane"] == 1
    assert (summaries["agent_akane"]["origin_round"]["created"]) != []
    # create 之後 akane 剛好填滿 cap（1 + 1 == 2），且副本內新增 1 筆 created 事件。
    assert lt.active_count("agent_akane") == 2
    assert lt.capacity("agent_akane") == 2
    created_lines = [
        r for r in local_lines(sim_root, "agent_akane") if r.get("event_type") == "created"
    ]
    assert len(created_lines) == 3  # 金樣 2 筆 + 本輪 1 筆


def test_s4b_golden_b_full_cap_rejects_create(sim_root):
    """S4 對照：**滿 cap 時不得 create**。同一輪（09/20 22:00）中 anna 已 `active=2=cap`
    且檢驗點到期 ⇒ 仍會 WAKE，但 M1 唯一容量強制點拒絕 create（`created=0`）。"""
    snapshot = load_fixture(FIXTURE_B)
    materialize(snapshot, sim_root)
    _make_akane_due_now(sim_root, NOW_B_NEXT)
    stub = ScriptedLLM(default=JSON_CREATE)

    summaries = run_slot(SNAPSHOT_AGENTS, NOW_B_NEXT, "night", llm_caller=stub)

    folded = lt.fold("agent_anna")
    assert len([r for r in folded.values() if r.get("status") == "active"]) == 2
    assert lt.capacity("agent_anna") == 2
    assert summaries["agent_anna"]["woke"] is True
    assert summaries["agent_anna"]["reason"] == REASON_DUE

    created = created_from_summaries(summaries)
    assert created["agent_anna"] == 0
    assert summaries["agent_anna"]["origin_round"]["created"] == []
    assert "create:rejected" in summaries["agent_anna"]["origin_round"]["skipped"]
    # 全部「滿 cap 且到期」的 agent 都不得 create（只有 akane 有空位 ⇒ 全輪 created 恰 1）。
    for agent_id in ("agent_anna", "agent_mai", "agent_miku", "agent_yua"):
        assert created[agent_id] == 0
    assert total_created(summaries) == 1


# ══════════════════════════════════════════════════════════════
# S5：同 slot 重複 ⇒ 候選可重複出現，但 M1 內 0 溶解寫回
# ══════════════════════════════════════════════════════════════


def test_s5_candidates_recur_but_no_dissolution_writeback(sim_root, monkeypatch):
    """S5：同一份副本連跑兩輪（09/20 08:00 → 09/20 22:00，**直接換 `now`**、0 sleep）
    ⇒ 溶解候選**可重複出現**；但 M1 檔內**沒有任何溶解寫回**：`dissolved_at` 未寫、
    `status` 未變、0 溶解事件 ⇒ 鎖住「M2 還沒接線時的唯讀行為」。
    """
    snapshot = load_fixture(FIXTURE_B)
    materialize(snapshot, sim_root)

    seen: List[Dict[str, Any]] = []
    real_batch = lt_diss.evaluate_batch_dissolution

    def spy(threads, current_time, **kwargs):
        evaluations = real_batch(threads, current_time, **kwargs)
        seen.append(
            {
                "thread_ids": [t.get("thread_id") for t in threads if isinstance(t, dict)],
                "candidates": sorted(e.thread_id for e in evaluations if e.should_mutate),
            }
        )
        return evaluations

    monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy, raising=True)

    baseline_status = {
        agent_id: status_map(rows) for agent_id, rows in fixture_agents(snapshot).items()
    }
    stub = ScriptedLLM({"agent_ruka": _advance_json(RUKA_DUE_THREAD_ID)}, default=None)

    first = run_slot(SNAPSHOT_AGENTS, NOW_B, "morning", llm_caller=stub)
    second = run_slot(SNAPSHOT_AGENTS, NOW_B_NEXT, "night", llm_caller=stub)

    # 兩輪都真的跑了（不是被 at-most-once 擋掉的 duplicate_slot）。
    assert "skipped" not in first["agent_akane"]
    assert "skipped" not in second["agent_akane"]
    assert first["agent_akane"]["dissolved_candidates"] >= 1
    assert second["agent_akane"]["dissolved_candidates"] >= 1

    akane_views = [rec for rec in seen if AKANE_ACTIVE_THREAD_ID in rec["thread_ids"]]
    assert len(akane_views) == 2, "兩輪各應評估一次 akane 的全部線頭"
    assert all(AKANE_CANDIDATE_THREAD_ID in rec["candidates"] for rec in akane_views)

    # 非空泛：第一輪確實對 ruka 落盤了一筆推進（M1 有寫入能力，只是不寫溶解）。
    assert advanced_total(first) == 1

    for agent_id in SNAPSHOT_AGENTS:
        rows = local_lines(sim_root, agent_id)
        assert [r for r in rows if r.get("event_type") == "dissolved"] == []
        assert all(r.get("dissolved_at") is None for r in rows)
        current = status_map(rows)
        for thread_id, status in baseline_status[agent_id].items():
            assert current[thread_id] == status
    assert lt.get_state("agent_akane", AKANE_CANDIDATE_THREAD_ID)["status"] == "completed"
    assert lt.get_state("agent_akane", AKANE_CANDIDATE_THREAD_ID)["dissolved_at"] is None


# ══════════════════════════════════════════════════════════════
# 平面自我護欄：入口簽名、wall-clock 讀取
# ══════════════════════════════════════════════════════════════


def test_plane_entry_signature_is_unchanged():
    """既有入口簽名不得改（SIM-TIME-001 只新增測試面）。"""
    import inspect

    sig = inspect.signature(m5.run_slot_pipeline)
    assert list(sig.parameters) == ["agent_ids", "now", "slot", "llm_caller"]
    assert sig.parameters["now"].annotation is not inspect.Parameter.empty
    assert sig.parameters["llm_caller"].default is None


def test_plane_orchestrator_has_zero_wall_clock_reads():
    """受測模組仍只讀**注入的 now**：原始碼 0 筆 wall-clock 讀取。"""
    source = Path(m5.__file__).read_text(encoding="utf-8")
    for needle in ("datetime.now", "utcnow", "time.time", "now()"):
        assert needle not in source, f"orchestrator 出現 wall-clock 讀取：{needle}"
