# tests/soul/test_sim_accel_streak_7d.py
# SIM-ACCEL-STREAK-7D — 7 天連續可見性狀態機的**模擬時鐘**驗證（容量動態 ＋ streak 計數器）。
#
# 為什麼存在
# ─────────
# 門檻三判準 1（「連續 7 天，每天都有主動推進」）在生產現況**不成立**（最高 mahiru 5 天）。
# 本檔把「再等 2 天就會轉綠嗎？」這個**時序問題**換成一個**可判定的結構問題**：
# 在 `cap=2`、且「連續 7 天」需要**每天不同的 thread_id** 的限制下，
# 到底需要發生什麼、那件事**由誰決定**、**有沒有保證到達路徑**。
#
# 本檔**只量測現狀，不改政策**：
#   - 受測對象 ＝ `src/soul/life_thread_orchestrator.py`（M5 接線）＋ M1/M2/M3/M4 全鏈路；
#   - **0 `src/**` 改動**（本檔是純測試票：改動被測對象會讓結論失去意義）；
#   - **0 真實 LLM**（腳本化 LLM 100% 覆蓋 ＋ 真 proxy 探針 ＋ socket 攔阻）；
#   - **0 生產寫入**（記憶副本落在 pytest 的 `tmp_path`；生產 `data/**` 全程唯讀）。
#
# 平面不變量（沿用 SIM-TIME-001 的風格）
# ────────────────────────────────
#   1. **假時鐘**：`now` 由測試直接注入（local tz `America/New_York`；slot 08:00／22:00）。
#      連續 slot ＝ 直接換 `now` 再呼叫 ⇒ **0 `time.sleep`／0 真實等待**。
#   2. **記憶副本**：生產 `data/soul/<agent>/life_threads.jsonl` 的事件流**時間截斷**到起點
#      （`updated_at <= cutoff`、保留檔案原順序、投影到 `FIELDS_KEPT`）⇒ 起點狀態；
#      所有後續寫入落在 `tmp_path`（生產唯讀，見 `test_production_untouched`）。
#   3. **零真實 LLM**：所有呼叫一律走注入的 `ScriptedLLM`；真實 proxy 探針命中即紅燈。
#
# 🔴 假時鐘的**寫入面接縫**（本票唯一必要的平面注入，必須寫清楚）
# ─────────────────────────────────────────────
#   M1（`src/soul/life_threads.py`）在落盤時用 `lt.now_utc_iso()` 取「現在」；
#   `apply_actions` 不接受 `updated_at` 參數 ⇒ 若不注入假時鐘，14 個 slot 的寫入會全部
#   落在**真實牆鐘的同一個日期**，streak 計數器將無法定義（日期集合恆為 1 天）。
#   故本檔把 `lt.now_utc_iso` 換成「回傳模擬 slot 時刻」的閉包（**測試平面**注入，
#   0 `src/**` 改動、0 生產影響）。這是 SIM-TIME-001「假時鐘」不變量的延伸，
#   不是對被測邏輯的改寫。
#
# 世界事件（**生產真實事件，時間平移**）
# ─────────────────────────────
#   生產的世界**是活的**（`data/world/perception_trace.jsonl` 每隔數分鐘就有天氣／新聞）
#   ⇒ M3 的世界碰撞路徑在生產每個 slot 都會命中，`world_collision` 起源的線頭也才有
#   可用的 prompt。若模擬窗內沒有世界事件，那條喚醒路徑整條失效 ⇒ 量到的會是
#   **平面的假象**而不是容量動態。故本檔把生產感知軌跡中「合格」的真實事件
#   （accepted ＋ 合格來源 ＋ 有 summary ＋ 未被消費 ＋ cutoff 前 14h）**平移**到
#   每個模擬 slot 前 14 小時，寫進隔離資料根（生產唯讀）。
#
# 唯一的效能注入
# ─────────────
#   `lt._load_default_config` 換成 memo 版本（避免每輪重讀 `configs/default.yaml`
#   ＋ dotenv）。**回傳值逐位元相同**（同一份 YAML），0 決策差異。
#
# 沙盒紀律（2026-10-20 23:34 RECALL-30D 污染事故的教訓，**本檔已逐條對照**）
# ────────────────────────────────────────────────────────
#   🔴 那次事故的根因是「沙盒＝來源」的 **inode 共享**（硬連結）⇒ append 落在生產 inode。
#   本檔**沒有任何 `os.link`／硬連結**；三條複製路徑全部產生**獨立 inode**：
#     1. 記憶副本：`read_text` → `json.loads` → **重新序列化** `write_text`（全新 inode）；
#     2. 世界事件：同上（讀進記憶體 → `write_text`）；
#     3. 變異 4 的沙盒副本：`dst.write_bytes(src.read_bytes())`（位元組複製、**非硬連結**）。
#   所有引擎寫入只經由 `SOUL_OS_DATA_DIR` 導向 `tmp_path`（或變異 4 的 tmp 沙盒）。
#   `test_production_untouched` 另含 **inode 共享斷言**（`os.path.samefile`），
#   把「沙盒與生產不是同一個 inode」寫成會紅的斷言，而不是只寫在註解裡。
#
# 跨檔 import 的取捨
# ────────────────
#   `tests/soul/` 無 `__init__.py` ⇒ **不**跨檔 import 另一個測試模組（模組名解析依賴
#   pytest rootdir 插入順序，且會與 SIM-TIME-001 的 autouse 平面不變量糾纏——它的
#   `_no_live_llm` 把旗標釘在關）。本檔改為**複製最小必要 primitive**（見
#   `tests/soul/test_life_thread_sim_m2_writeback.py` 的同註記）。
#
# 起點狀態與時間邊界（**本檔自行復現，不採信工單轉錄數字**）
# ─────────────────────────────────────────────
#   `START_CUTOFF_UTC = 2026-10-21T02:30:00Z`（＝ 10/20 22:30 local）＝ 生產 10/20 22:00
#   槽跑完後的狀態。14 個模擬 slot ＝ 10/21 08:00 起、local 日曆日連續 7 天 × 2 slot。
#   🔴 **streak 計數器用 UTC 日期**（§0.1.22 的「有推進日（UTC）」）：22:00 local ＝ 次日
#   02:00Z ⇒ 夜槽的 UTC 日期是**次日**。報告同時給 local 日期與 UTC 日期。
#
# 變異實驗（`SIM_ACCEL_STREAK_7D_MUTATION` 環境變數；見 `logs/SIM-ACCEL-STREAK-7D-IMPLEMENTATION.md`）
# ─────────────────────────────────────────────
#   1 → streak 計數器改成「距今天數」      2 → 容量檢查失效（cap 視為無限）
#   3 → 腳本化 LLM 漏接某個 slot          4 → memory fork 寫回生產（隔離失效）
#   5 → 標題多樣性統計漏掉重複計數
#   🔴 凡「移除某道閘門」型變異，被解除防護的程式碼**不可能真的執行**：
#      變異 3 的真 proxy 探針在命中當場 `raise`（連外不可能發生）；
#      變異 4 的「生產」被換成 `tmp_path` 內的沙盒副本（真生產零寫入）。
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_dissolution as lt_diss  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

# ══════════════════════════════════════════════════════════════
# 常數：起點、時間軸、容量、投影欄位
# ══════════════════════════════════════════════════════════════

#: 生產 log 根（`data/soul/`）。變異 4 會把它**指向 tmp 沙盒副本**（真生產零寫入）。
PROD_SOUL_DIR: Path = _REPO_ROOT / "data" / "soul"
#: 真生產 log 根（**永不被變異改寫**）：記憶副本的**唯讀**來源、資料存在性判定都用它。
PROD_SOUL_DIR_REAL: Path = _REPO_ROOT / "data" / "soul"
LIFE_THREADS_FILENAME = "life_threads.jsonl"

#: 生產感知軌跡（M3 世界碰撞防線 ＋ M4 `world_collision` 種子的唯一來源）。
PROD_WORLD_TRACE: Path = _REPO_ROOT / "data" / "world" / "perception_trace.jsonl"

#: 世界碰撞視窗（小時）＝ M3 與 M4 的同名常數（`WORLD_COLLISION_WINDOW_HOURS`）。
WORLD_WINDOW_HOURS = 14

#: 10 隻生產 agent（依字母序＝決定性順序；與 SIM-TIME-001 的金樣 agent 集合一致）。
AGENTS: Tuple[str, ...] = (
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

#: 記憶副本的截斷邊界（UTC）＝ 10/20 22:00 槽跑完後。此邊界複現工單 §0.1.2 的現況基準
#: （10 隻 agent 全部 `active=2`／`cap=2`、active 總數 20）——由本檔**自行重推**。
START_CUTOFF_UTC = datetime(2026, 10, 21, 2, 30, 0, tzinfo=timezone.utc)

#: 模擬時間軸：7 個 local 日曆日 × 2 slot ＝ 14 個 slot（slot 時間 08:00／22:00 local）。
SIM_DAYS = 7
SIM_START_LOCAL = datetime(2026, 10, 21, 8, 0, 0, tzinfo=LOCAL_TZ)
SLOT_HOURS: Tuple[Tuple[int, str], ...] = ((8, "morning"), (22, "night"))

#: 記憶副本投影欄位（決策相關；**不含** `title`／`narrative_content` 等敘事內容）
#: —— 與 SIM-TIME-001 金樣的 `fields_kept` 逐字同集合。
FIELDS_KEPT: Tuple[str, ...] = (
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

#: 腳本化 LLM 寫入的 `next_check_hours`（生產實測區間 20~28 小時；取 24 ＝ 生產眾數，
#: **不催熟**線頭：新建線頭要滿 24 小時才到期 ⇒ 每 local 日 1 條新線頭的節奏）。
NEXT_CHECK_HOURS = 24

#: 容量死鎖門檻（工單 §3.3.1 訂定）：連續 ≥ 3 個 slot 無法建立新線頭。
DEADLOCK_MIN_SLOTS = 3

#: 變異 4（隔離失效）會把它指向 tmp 沙盒根；`None` ＝ 正常隔離（tmp_path/sim_root）。
SIM_DATA_ROOT_OVERRIDE: Optional[Path] = None

#: 變異開關（環境變數）。非空時只套用**該條**變異（見模組 docstring）。
MUTATION_ENV = "SIM_ACCEL_STREAK_7D_MUTATION"

_SOUL_STUB = "（模擬人格語境；SIM-ACCEL-STREAK-7D 固定字串，不讀生產 persona）"


def _mutation() -> str:
    return (os.environ.get(MUTATION_ENV) or "").strip()


# ══════════════════════════════════════════════════════════════
# 純函式工具：時間解析、streak 計數器、標題多樣性、摘要
# ══════════════════════════════════════════════════════════════


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _utc_date(value: Any) -> Optional[date]:
    """ISO 字串 → **UTC** 日期（§0.1.22 的「有推進日（UTC）」口徑）。"""
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).date()


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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prod_life_threads_digest(soul_dir: Optional[Path] = None) -> Dict[str, str]:
    """生產 `data/soul/**/life_threads.jsonl` 的逐檔 sha256（唯讀）。"""
    root = soul_dir or PROD_SOUL_DIR
    digests: Dict[str, str] = {}
    if not root.is_dir():
        return digests
    for path in sorted(root.glob(f"*/{LIFE_THREADS_FILENAME}")):
        digests[path.relative_to(root).as_posix()] = _sha256_file(path)
    return digests


def prod_tree_digest(soul_dir: Optional[Path] = None) -> Dict[str, str]:
    """生產 `data/soul/**` 全樹的逐檔 sha256（唯讀；供報告用，不作為硬斷言）。"""
    root = soul_dir or PROD_SOUL_DIR
    digests: Dict[str, str] = {}
    if not root.is_dir():
        return digests
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digests[path.relative_to(root).as_posix()] = _sha256_file(path)
    return digests


def streak_from_dates(dates: Iterable[date]) -> int:
    """**門檻三的正確算法**（§0.1.22）：日期集合 → 對每個日期作錨點往前連續計數 → 取最大值。

    🔴 **不是**「距今天數」（該口徑已被證偽並作廢，見 `days_since_last_update`）。
    """
    unique = {d for d in dates if isinstance(d, date)}
    best = 0
    for anchor in unique:
        run = 1
        while (anchor - timedelta(days=run)) in unique:
            run += 1
        best = max(best, run)
    return best


def days_since_last_update(dates: Iterable[date], today: date) -> int:
    """**已作廢**的口徑（§0.1.22 證偽）：距最後一次更新的天數。僅供變異實驗使用。"""
    unique = {d for d in dates if isinstance(d, date)}
    if not unique:
        return 0
    return (today - max(unique)).days


def measure_streak(dates: Iterable[date], today: Optional[date] = None) -> int:
    """模擬量測路徑**唯一**使用的 streak 入口（變異 1 的作用點）。"""
    return streak_from_dates(dates)


def title_diversity(titles: Sequence[Any]) -> Dict[str, Any]:
    """標題多樣性：集合大小 ÷ 總數 ＋ 重複清單（分子／分母／單位見報告）。"""
    cleaned = [t for t in titles if isinstance(t, str) and t.strip()]
    distinct = sorted(set(cleaned))
    counts: Dict[str, int] = {}
    for title in cleaned:
        counts[title] = counts.get(title, 0) + 1
    duplicates = sorted(t for t, n in counts.items() if n > 1)
    return {
        "distinct": len(distinct),
        "total": len(cleaned),
        "ratio": (len(distinct) / len(cleaned)) if cleaned else 0.0,
        "duplicates": duplicates,
    }


# ══════════════════════════════════════════════════════════════
# 記憶副本：生產事件流的時間截斷 ＋ 落地
# ══════════════════════════════════════════════════════════════


def derive_start_state(
    cutoff_utc: datetime,
    *,
    prod_dir: Optional[Path] = None,
    agents: Sequence[str] = AGENTS,
) -> Dict[str, List[Dict[str, Any]]]:
    """由生產 append-only 事件日誌**時間截斷**推導起點狀態（唯讀）。

    規則（與 SIM-TIME-001 金樣的 `provenance.derivation` 同語意）：保留 `updated_at`
    可解析且 `<= cutoff_utc` 的事件（依檔案原順序），投影到 `FIELDS_KEPT`；
    `updated_at` 缺失／不可解析者一律丟棄（不猜時間）。
    """
    root = prod_dir or PROD_SOUL_DIR
    out: Dict[str, List[Dict[str, Any]]] = {}
    for agent_id in agents:
        path = root / agent_id / LIFE_THREADS_FILENAME
        kept: List[Dict[str, Any]] = []
        for entry in _read_jsonl(path):
            ts = _parse_iso(entry.get("updated_at"))
            if ts is None or ts > cutoff_utc:
                continue
            kept.append({field: entry.get(field) for field in FIELDS_KEPT})
        out[agent_id] = kept
    return out


def materialize(rows_by_agent: Dict[str, List[Dict[str, Any]]], data_root: Path) -> Path:
    """把起點狀態寫進隔離資料根（`<root>/soul/<agent>/life_threads.jsonl`）。"""
    soul_dir = data_root / "soul"
    for agent_id, rows in rows_by_agent.items():
        target = soul_dir / agent_id
        target.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        (target / LIFE_THREADS_FILENAME).write_text(text, encoding="utf-8")
    return soul_dir


def derive_world_records(
    cutoff_utc: datetime, *, world_trace: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """從**生產**感知軌跡取出「合格」的世界事件（唯讀）。

    合格口徑（逐字對齊 M3 `_scan_world_collision` ＋ M4 `collect_world_seeds`）：
      - `accepted is True`；
      - `source ∈ {weather, news, news_event, calendar, calendar_event}`；
      - `extra["summary"]` 非空（否則不構成種子）；
      - `consumed_by_wake is not True`（已消費的碰撞不再喚醒）；
      - `timestamp` 落在 `[cutoff − 14h, cutoff]`（與生產 slot 同視窗）。

    為什麼需要它：生產的世界**是活的**（`data/world/perception_trace.jsonl` 每隔數分鐘
    就有天氣／新聞事件）⇒ M3 的世界碰撞路徑在生產每個 slot 都會命中。若模擬窗內沒有
    世界事件，則「世界碰撞」這條喚醒路徑整條失效、`world_collision` 起源的線頭會讓
    agent **完全無法行動**（prompt 不可得 ⇒ 0 LLM）⇒ 量測到的就不再是容量動態，
    而是平面的假象。故本檔把**生產真實事件**平移進模擬窗（見 `write_world_window`）。
    """
    path = world_trace or PROD_WORLD_TRACE
    floor = cutoff_utc - timedelta(hours=WORLD_WINDOW_HOURS)
    qualifying = {"weather", "news", "news_event", "calendar", "calendar_event"}
    out: List[Dict[str, Any]] = []
    for record in _read_jsonl(path):
        if record.get("accepted") is not True:
            continue
        source = record.get("source")
        if not isinstance(source, str) or source.strip() not in qualifying:
            continue
        extra = record.get("extra")
        if not isinstance(extra, dict):
            continue
        summary = extra.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        if record.get("consumed_by_wake") is True:
            continue
        ts = _parse_iso(record.get("timestamp"))
        if ts is None or ts < floor or ts > cutoff_utc:
            continue
        out.append(record)
    return out


def write_world_window(
    sim_root: Path,
    records: Sequence[Dict[str, Any]],
    slot_now: datetime,
    cutoff_utc: datetime,
) -> Optional[Path]:
    """把世界事件**平移**到模擬 slot 前 14 小時內，寫進隔離資料根（生產唯讀）。

    平移量 ＝ `slot_now − cutoff` ⇒ 每個 slot 都拿到與生產同構的「近 14 小時世界」。
    寫入位置 ＝ `<sim_root>/world/perception_trace.jsonl`（M3／M4 的真實解析路徑）。
    """
    if not records:
        return None
    shift = slot_now.astimezone(timezone.utc) - cutoff_utc
    lines: List[str] = []
    for record in records:
        moved = dict(record)
        ts = _parse_iso(record.get("timestamp"))
        if ts is not None:
            moved["timestamp"] = (ts + shift).isoformat()
        lines.append(json.dumps(moved, ensure_ascii=False))
    path = sim_root / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ══════════════════════════════════════════════════════════════
# 假時鐘 ＋ 腳本化 LLM
# ══════════════════════════════════════════════════════════════


class FakeClock:
    """模擬時鐘的唯一真相：`now` 由 runner 逐 slot 注入。"""

    def __init__(self, now: Optional[datetime] = None) -> None:
        self.now = now or SIM_START_LOCAL

    def set(self, now: datetime) -> None:
        self.now = now

    @property
    def now_utc(self) -> datetime:
        return self.now.astimezone(timezone.utc)

    def iso_utc(self) -> str:
        return self.now_utc.isoformat()


def build_slots() -> List[Tuple[str, datetime]]:
    """14 個 slot：7 個 local 日曆日 × {08:00 morning, 22:00 night}。"""
    slots: List[Tuple[str, datetime]] = []
    for offset in range(SIM_DAYS):
        day = SIM_START_LOCAL.date() + timedelta(days=offset)
        for hour, name in SLOT_HOURS:
            slots.append(
                (name, datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=LOCAL_TZ))
            )
    return slots


def _is_due(state: Dict[str, Any], now: datetime) -> bool:
    """到期判定（M3 判定 1 同口徑）：`check_after_ts <= now`。"""
    ts = _parse_iso(state.get("check_after_ts"))
    return ts is not None and ts.timestamp() <= now.timestamp()


def _title_for(agent_id: str, now: datetime, slot_name: str) -> str:
    """每個 (agent, slot) 唯一、且 ≤ 40 字元的標題（M1 `TITLE_MAX_LEN`）。

    🔴 標題必須帶上**時段**（08／22）：同一個 local 日有兩個 slot，若標題只帶日期，
    同一天建立的兩條線頭會拿到**同一個標題** ⇒ 標題多樣性量測會被污染。
    """
    short = agent_id.replace("agent_", "")
    return f"SIM-{short}-{now.strftime('%m%d')}-{now.strftime('%H')}"


def _narrative_for(agent_id: str, now: datetime) -> str:
    """敘事內容：1~600 字元、非佔位符、**不等同 title**（M1 §2.2 驗證）。"""
    return (
        f"{now.strftime('%Y-%m-%d %H:%M')} 槽的模擬推進："
        f"{agent_id} 把一條舊線頭收束成句，替自己騰出下一個著力點。"
    )


class ScriptedLLM:
    """腳本化 LLM（**0 真實 LLM／0 網路**）。

    兩條劇本（工單 §3.2）：

    - **A（每天新建一條線頭）**：
      1. 若有**到期**的 active 線頭 ⇒ 關閉其中**最舊**的一條（`op=complete`）；
         「最舊」＝ `(created_at, thread_id)` 最小，且限於本輪到期集合
         （M4 輸出契約規則 4：`thread_id` 必須是到期線頭的 uuid）。
      2. **僅在「本輪開始時有容量空位」**（`active < cap`）時建立一條新線頭
         —— 與 M4 prompt 的容量區塊同口徑（「只有 active < cap 時才可以 op=create」）。
    - **B（每天只推進既有線頭）**：只回 `op=advance`（關最舊的到期線頭），
      **不新建、不關閉**。

    產出的 `next_check_hours` ＝ `NEXT_CHECK_HOURS`（24h，生產實測區間內）。
    """

    def __init__(self, scenario: str, clock: FakeClock) -> None:
        self.scenario = scenario
        self.clock = clock
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, messages: Any, agent_id: str) -> Optional[str]:
        now = self.clock.now
        actions = self._actions(agent_id, now)
        # 記帳：每一次腳本化回覆的動作內容（供「關閉只來自 LLM 決策」的對帳）。
        self.calls.append(
            {
                "agent_id": agent_id,
                "now": now.isoformat(),
                "actions": [
                    {"op": a.get("op"), "thread_id": a.get("thread_id")} for a in actions
                ],
            }
        )
        return json.dumps({"actions": actions}, ensure_ascii=False)

    # ── 劇本決策 ──────────────────────────────────────────────
    def _actions(self, agent_id: str, now: datetime) -> List[Dict[str, Any]]:
        states = lt.fold(agent_id)
        active = [s for s in states.values() if s.get("status") == "active"]
        due = [s for s in active if _is_due(s, now)]
        oldest = (
            min(due, key=lambda s: (str(s.get("created_at") or ""), str(s.get("thread_id"))))
            if due
            else None
        )
        actions: List[Dict[str, Any]] = []
        if self.scenario == "A":
            if oldest is not None:
                actions.append(
                    {
                        "op": "complete",
                        "thread_id": oldest.get("thread_id"),
                        "narrative_content": _narrative_for(agent_id, now),
                        "next_check_hours": NEXT_CHECK_HOURS,
                    }
                )
            if len(active) < lt.capacity(agent_id):
                actions.append(
                    {
                        "op": "create",
                        "title": _title_for(agent_id, now, "create"),
                        "narrative_content": _narrative_for(agent_id, now),
                        "next_check_hours": NEXT_CHECK_HOURS,
                        "share_target": "none",
                    }
                )
        elif self.scenario == "B":
            if oldest is not None:
                actions.append(
                    {
                        "op": "advance",
                        "thread_id": oldest.get("thread_id"),
                        "narrative_content": _narrative_for(agent_id, now),
                        "next_check_hours": NEXT_CHECK_HOURS,
                    }
                )
        else:  # pragma: no cover - 防呆
            raise ValueError(f"未知劇本：{self.scenario!r}")
        return actions


# ══════════════════════════════════════════════════════════════
# fixtures：隔離資料根、假時鐘、零 LLM、零 sleep、變異套用
# ══════════════════════════════════════════════════════════════


@pytest.fixture()
def sim_root(tmp_path, monkeypatch):
    """模擬資料根：起點狀態副本的唯一落點（生產 `data/**` 全程唯讀）。

    變異 4（隔離失效）時，資料根被換成 tmp 內的**生產沙盒副本** ⇒ 模擬的寫入會落到
    那份副本上（真生產仍然零寫入）⇒ 生產守衛測試必須轉紅。
    """
    root = SIM_DATA_ROOT_OVERRIDE or (tmp_path / "sim_root")
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(root))
    reset_data_root()
    try:
        yield root
    finally:
        reset_data_root()


@pytest.fixture()
def world_records() -> List[Dict[str, Any]]:
    """生產真實世界事件（唯讀；平移進模擬窗用）。生產無此檔 ⇒ 空（安靜世界）。"""
    return derive_world_records(START_CUTOFF_UTC)


@pytest.fixture(autouse=True)
def _config_cache(monkeypatch):
    """config 載入快取（**純效能**，不改任何決策）。

    `lt.capacity()` 每次呼叫都會重讀並重解析 `configs/default.yaml`（＋ dotenv）⇒
    14 slot × 10 agent × 數次呼叫會把單輪模擬拖到 10 秒量級。本檔把
    `lt._load_default_config` 換成 memo 版本 ⇒ **回傳值逐位元相同**（同一份 YAML），
    只是一份檔案只解析一次。0 `src/**` 改動。
    """
    original = lt._load_default_config
    cache: Dict[str, Any] = {}

    def _cached():
        if "cfg" not in cache:
            cache["cfg"] = original()
        return cache["cfg"]

    monkeypatch.setattr(lt, "_load_default_config", _cached, raising=True)


@pytest.fixture(autouse=True)
def _clean_idempotency():
    """`_LAST_PROCESSED` 是 process-global 冪等鍵：每個測試前後清空。"""
    m5.reset_state()
    yield
    m5.reset_state()


class LiveLLMProbe:
    """真實 proxy 探針：記錄「決策路徑是否試圖取得真實 LLM proxy」。

    變異 3 時（腳本化 LLM 漏接某 slot）探針在命中當場 `raise` ⇒ 被解除防護的程式碼
    **不可能真的執行**（連外不可能發生，AGENTS.md 鐵律 7）。
    """

    def __init__(self, raise_on_hit: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.raise_on_hit = raise_on_hit

    def __call__(self) -> Any:
        self.calls.append({"via": "_find_llm_proxy"})
        if self.raise_on_hit:
            raise AssertionError(
                "SIM-ACCEL-STREAK-7D 變異 3：真實 LLM proxy 路徑被觸及（硬攔，連外不可能發生）"
            )
        return None


@pytest.fixture(autouse=True)
def probe(monkeypatch) -> LiveLLMProbe:
    """矩陣級不變量：0 真實 LLM proxy、0 連外、人格語境固定、所有接線旗標釘在關。"""
    live = LiveLLMProbe(raise_on_hit=(_mutation() == "3"))
    monkeypatch.setattr(lt_origins, "_find_llm_proxy", live, raising=True)
    monkeypatch.setattr(
        lt_origins, "load_soul_context", lambda agent_id, **kw: _SOUL_STUB, raising=True
    )
    for flag in (
        "LIFE_THREAD_CONSOLIDATION_ENABLED",
        "LIFE_THREAD_BOOTSTRAP_ENABLED",
        "LIFE_THREAD_WHIM_ENABLED",
        "LIFE_THREAD_CATCHUP_ENABLED",
        "LIFE_THREAD_ELEVATION_ENABLED",
    ):
        monkeypatch.setenv(flag, "")

    def _blocked_connect(*args, **kwargs):
        raise AssertionError("SIM-ACCEL-STREAK-7D：平面內不得連外（socket.create_connection 已攔）")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect, raising=True)
    return live


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> FakeClock:
    """假時鐘的**寫入面接縫**：`lt.now_utc_iso()` 回傳模擬 slot 時刻（見模組 docstring）。"""
    fake = FakeClock()
    monkeypatch.setattr(lt, "now_utc_iso", lambda: fake.iso_utc(), raising=True)
    return fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """平面不變量：0 `time.sleep`（假時鐘 ⇒ 連續 slot 直接換 `now`）。"""

    def _blocked_sleep(*args, **kwargs):
        raise AssertionError("SIM-ACCEL-STREAK-7D：平面內不得 sleep（假時鐘）")

    monkeypatch.setattr(time, "sleep", _blocked_sleep, raising=True)


@pytest.fixture(autouse=True)
def _apply_mutation(monkeypatch, tmp_path):
    """變異開關（`SIM_ACCEL_STREAK_7D_MUTATION`）：每條變異都必須讓對應守衛測試轉紅。"""
    mutation = _mutation()
    if not mutation:
        return
    if mutation == "1":
        # streak 計數器改成「距今天數」（§0.1.22 已作廢的口徑）。
        monkeypatch.setattr(
            sys.modules[__name__],
            "measure_streak",
            lambda dates, today=None: days_since_last_update(dates, today or date(2026, 10, 20)),
            raising=True,
        )
    elif mutation == "2":
        # 容量檢查失效：cap 視為無限（M1 強制點與 M3 飽和判定同時失效）。
        monkeypatch.setattr(lt, "capacity", lambda agent_id, config_path=None: 99, raising=True)
    elif mutation == "3":
        # 腳本化 LLM 漏接某個 slot ⇒ 由 runner 注入 `llm_caller=None`（真 proxy 路徑）。
        pass  # 實際行為在 `run_scenario`（漏接第 4 個 slot）。
    elif mutation == "4":
        # memory fork 寫回「生產」：把**寫入目標**從隔離 tmp 根換成 tmp 內的生產沙盒副本
        # ⇒ 等同「去掉 tmp_path 隔離」。真生產零寫入（沙盒在 tmp_path 內）。
        sandbox = tmp_path / "prod_sandbox"
        (sandbox / "soul").mkdir(parents=True, exist_ok=True)
        if PROD_SOUL_DIR_REAL.is_dir():
            for src in PROD_SOUL_DIR_REAL.rglob("*"):
                if not src.is_file():
                    continue
                dst = sandbox / "soul" / src.relative_to(PROD_SOUL_DIR_REAL)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
        # 生產守衛的摘要目標也換成沙盒（模擬「生產被寫到」的可觀測面）。
        monkeypatch.setattr(sys.modules[__name__], "PROD_SOUL_DIR", sandbox / "soul", raising=True)
        monkeypatch.setattr(
            sys.modules[__name__], "SIM_DATA_ROOT_OVERRIDE", sandbox, raising=True
        )
        os.environ["SOUL_OS_DATA_DIR"] = str(sandbox)
        reset_data_root()
    elif mutation == "5":
        # 標題多樣性統計漏掉重複計數：分母被換成「集合大小」⇒ 比率恆為 1.0。
        original_diversity = title_diversity

        def _broken_diversity(titles):
            base = original_diversity(titles)
            base["ratio"] = 1.0 if base["total"] else 0.0
            return base

        monkeypatch.setattr(
            sys.modules[__name__], "title_diversity", _broken_diversity, raising=True
        )
    else:  # pragma: no cover - 防呆
        raise ValueError(f"未知變異：{mutation!r}")





# ══════════════════════════════════════════════════════════════
# 模擬 runner：14 個 slot ＋ 逐 slot 量測
# ══════════════════════════════════════════════════════════════


def _agent_snapshot(agent_id: str, window_dates: Set[date]) -> Dict[str, Any]:
    """某 agent 在當下的 fold 視圖 ＋ 兩個 streak 口徑。"""
    states = lt.fold(agent_id)
    dates = {
        d
        for d in (_utc_date(s.get("updated_at")) for s in states.values())
        if d is not None
    }
    active = [s for s in states.values() if s.get("status") == "active"]
    return {
        "active": len(active),
        "cap": lt.capacity(agent_id),
        "dates": dates,
        "streak_all": measure_streak(dates),
        "streak_window": measure_streak(dates & window_dates),
        "titles": {tid: s.get("title") for tid, s in states.items()},
    }


def run_scenario(
    scenario: str,
    fake_clock: FakeClock,
    *,
    agents: Sequence[str] = AGENTS,
    slots: Optional[Sequence[Tuple[str, datetime]]] = None,
    world_records: Optional[Sequence[Dict[str, Any]]] = None,
    sim_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """跑完 14 個 slot，回傳逐 slot 明細 ＋ 每 agent 的量測。"""
    slot_list = list(slots if slots is not None else build_slots())
    stub = ScriptedLLM(scenario, fake_clock)
    m5.reset_state()

    window_dates: Set[date] = set()
    slot_log: List[Dict[str, Any]] = []
    for index, (slot_name, now) in enumerate(slot_list):
        fake_clock.set(now)
        window_dates.add(now.astimezone(timezone.utc).date())
        if sim_root is not None and world_records is not None:
            # 每個 slot 都把生產真實世界事件平移進模擬窗（生產唯讀）。
            write_world_window(sim_root, world_records, now, START_CUTOFF_UTC)
        llm_caller: Any = stub
        if _mutation() == "3" and index == 3:
            # 變異 3：腳本化 LLM 漏接這個 slot ⇒ 決策路徑會去找真實 proxy。
            llm_caller = None
        summaries = asyncio.run(
            m5.run_slot_pipeline(list(agents), now, slot_name, llm_caller=llm_caller)
        )
        per_agent: Dict[str, Any] = {}
        for agent_id in agents:
            summary = summaries.get(agent_id) or {}
            round_result = summary.get("origin_round") or {}
            snap = _agent_snapshot(agent_id, window_dates)
            per_agent[agent_id] = {
                "woke": summary.get("woke"),
                "reason": summary.get("reason"),
                "created": list(round_result.get("created") or []),
                "advanced": list(round_result.get("advanced") or []),
                "transitioned": list(round_result.get("transitioned") or []),
                "skipped": list(round_result.get("skipped") or []),
                "dissolved_candidates": summary.get("dissolved_candidates"),
                "active": snap["active"],
                "cap": snap["cap"],
                "streak_all": snap["streak_all"],
                "streak_window": snap["streak_window"],
                "titles": snap["titles"],
            }
        slot_log.append(
            {
                "index": index,
                "slot": slot_name,
                "local": now.isoformat(),
                "utc": now.astimezone(timezone.utc).isoformat(),
                "local_date": now.date().isoformat(),
                "utc_date": now.astimezone(timezone.utc).date().isoformat(),
                "agents": per_agent,
            }
        )

    return {
        "scenario": scenario,
        "slots": slot_log,
        "llm_calls": len(stub.calls),
        "stub": stub,
    }


def summarize_scenario(run: Dict[str, Any], agents: Sequence[str] = AGENTS) -> Dict[str, Any]:
    """把逐 slot 明細收斂成 4 項量測（每個數字都帶分子／分母／單位）。"""
    created_titles: Dict[str, List[str]] = {a: [] for a in agents}
    created_ids: Dict[str, List[str]] = {a: [] for a in agents}
    closed_ids: Dict[str, List[str]] = {a: [] for a in agents}
    create_rejected_slots: Dict[str, int] = {a: 0 for a in agents}
    slots_without_create: Dict[str, List[int]] = {a: [] for a in agents}
    day_rows: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for slot in run["slots"]:
        day = slot["local_date"]
        day_rows.setdefault(day, {})
        for agent_id in agents:
            row = slot["agents"][agent_id]
            created = row["created"]
            transitioned = row["transitioned"]
            if created:
                for tid in created:
                    created_ids[agent_id].append(tid)
                    title = row["titles"].get(tid)
                    if isinstance(title, str):
                        created_titles[agent_id].append(title)
                day_rows[day].setdefault(agent_id, []).append(
                    {
                        "slot": slot["slot"],
                        "utc_date": slot["utc_date"],
                        "thread_ids": list(created),
                        "titles": [
                            row["titles"].get(tid) for tid in created
                        ],
                    }
                )
            else:
                slots_without_create[agent_id].append(slot["index"])
            closed_ids[agent_id].extend(transitioned)
            if any(s.startswith("create:rejected") for s in row["skipped"]):
                create_rejected_slots[agent_id] += 1

    # 容量死鎖：連續 ≥ DEADLOCK_MIN_SLOTS 個 slot 沒有建立新線頭。
    deadlock_runs: Dict[str, List[Tuple[int, int]]] = {}
    for agent_id in agents:
        gaps = sorted(slots_without_create[agent_id])
        runs: List[Tuple[int, int]] = []
        start = None
        prev = None
        for index in gaps:
            if prev is None or index != prev + 1:
                if start is not None:
                    runs.append((start, prev))
                start = index
            prev = index
        if start is not None:
            runs.append((start, prev))
        deadlock_runs[agent_id] = [r for r in runs if (r[1] - r[0] + 1) >= DEADLOCK_MIN_SLOTS]

    return {
        "created_ids": created_ids,
        "created_titles": created_titles,
        "closed_ids": closed_ids,
        "create_rejected_slots": create_rejected_slots,
        "slots_without_create": slots_without_create,
        "deadlock_runs": deadlock_runs,
        "day_rows": day_rows,
    }


# ══════════════════════════════════════════════════════════════
# 容量動態模型 ＋ 機率表（純計算；§3.4）
# ══════════════════════════════════════════════════════════════


def capacity_shortfall_model(
    cap: int, start_active: int, days: int = SIM_DAYS
) -> Dict[str, Any]:
    """解析模型：cap=2、每天新建 1 條、每天關閉 1 條 ⇒ 第一次「空位不足」何時出現。

    兩種動作次序（決定性說明，不是猜測）：

    - **create-first**（M4 prompt 的容量區塊口徑：LLM 依**本輪開始前**的 active 數判斷
      能不能建立）⇒ 池滿時的建立會被 M1 強制點拒絕 ⇒ 第一次空位不足＝**第 1 個 slot**。
    - **close-first**（同一輪先關後建）⇒ 關閉騰出空間 ⇒ 建立永遠成功（0 次空位不足），
      但**代價是每個 agent 每天都要有一次 LLM 關閉決定**。
    """
    timelines = {}
    for order in ("create-first", "close-first"):
        active = start_active
        first_shortfall = None
        rows = []
        for day in range(days):
            for _hour, slot_name in SLOT_HOURS:
                created = 0
                closed = 0
                if order == "create-first":
                    if active < cap:
                        active += 1
                        created = 1
                    elif first_shortfall is None:
                        first_shortfall = (day, slot_name)
                    active -= 1 if active > 0 else 0
                    closed = 1
                else:
                    active -= 1 if active > 0 else 0
                    closed = 1
                    if active < cap:
                        active += 1
                        created = 1
                rows.append(
                    {
                        "day": day,
                        "slot": slot_name,
                        "created": created,
                        "closed": closed,
                        "active_after": active,
                    }
                )
        timelines[order] = {"first_shortfall": first_shortfall, "rows": rows}
    return timelines


def close_probability(p: float, days: int = SIM_DAYS, needed: int = 6) -> Dict[str, float]:
    """機率表：每天 1 次關閉嘗試、成功率 p ⇒ 7 天內至少關閉 1 次／至少 6 次的機率。"""
    at_least_one = 1.0 - (1.0 - p) ** days
    at_least_k = sum(
        math.comb(days, k) * (p ** k) * ((1.0 - p) ** (days - k))
        for k in range(needed, days + 1)
    )
    return {
        "p": p,
        "p_at_least_one_close": at_least_one,
        "p_at_least_k_closes": at_least_k,
        "k": needed,
        "days": days,
    }


def production_round_base_rate(prod_dir: Optional[Path] = None) -> Dict[str, Any]:
    """生產實測的「每輪關閉率」：把生產事件按 (agent, local 日, slot) 分輪統計。

    分輪口徑：M4 的 `current_slot()`（`05:00 <= hour < 12:00` 為 morning，其餘 night）。
    這是**唯讀**量測，不寫任何檔案。
    """
    root = prod_dir or PROD_SOUL_DIR
    rounds: Dict[Tuple[str, str, str], Dict[str, int]] = {}
    for agent_id in AGENTS:
        path = root / agent_id / LIFE_THREADS_FILENAME
        for entry in _read_jsonl(path):
            ts = _parse_iso(entry.get("updated_at"))
            if ts is None:
                continue
            local = ts.astimezone(LOCAL_TZ)
            slot_name = "morning" if 5 <= local.hour < 12 else "night"
            key = (agent_id, local.date().isoformat(), slot_name)
            bucket = rounds.setdefault(key, {"rounds": 1, "created": 0, "advanced": 0, "closed": 0})
            event_type = entry.get("event_type")
            status = entry.get("status")
            if event_type == "created":
                bucket["created"] += 1
            elif event_type == "updated":
                bucket["advanced"] += 1
            elif event_type == "transitioned" and status in ("completed", "abandoned"):
                bucket["closed"] += 1
    action_rounds = [b for b in rounds.values() if b["created"] + b["advanced"] + b["closed"] > 0]
    closed_rounds = [b for b in action_rounds if b["closed"] > 0]
    created_rounds = [b for b in action_rounds if b["created"] > 0]
    total_actions = sum(b["created"] + b["advanced"] + b["closed"] for b in action_rounds)
    total_closed = sum(b["closed"] for b in action_rounds)
    return {
        "rounds": len(action_rounds),
        "closed_rounds": len(closed_rounds),
        "created_rounds": len(created_rounds),
        "p_close_per_round": (len(closed_rounds) / len(action_rounds)) if action_rounds else 0.0,
        "p_create_per_round": (len(created_rounds) / len(action_rounds)) if action_rounds else 0.0,
        "closed_events": total_closed,
        "action_events": total_actions,
        "p_close_per_event": (total_closed / total_actions) if total_actions else 0.0,
    }


# ══════════════════════════════════════════════════════════════
# 報告渲染
# ══════════════════════════════════════════════════════════════


def render_report(
    run_a: Dict[str, Any],
    run_b: Dict[str, Any],
    summary_a: Dict[str, Any],
    summary_b: Dict[str, Any],
    baseline: Dict[str, Any],
) -> str:
    lines: List[str] = []
    lines.append("=" * 100)
    lines.append("SIM-ACCEL-STREAK-7D 量測報告（假時鐘平面；0 真實 LLM；生產唯讀）")
    lines.append("=" * 100)
    lines.append(
        f"起點狀態：生產事件流時間截斷 cutoff={START_CUTOFF_UTC.isoformat()}"
        f"（10/20 22:30 local）"
    )
    lines.append(
        "模擬時間軸：7 個 local 日 × 2 slot ＝ 14 slot（08:00／22:00 local）；"
        "streak 計數器用 **UTC 日期**（夜槽 22:00 local ＝ 次日 02:00Z）。"
    )
    lines.append(
        "streak_window 的日期集合＝模擬 14 個 slot 觸及的全部 **UTC 日期**"
        "（7 個 local 日 ＝ 8 個 UTC 日期；故 streak_window 的上限是 8，不是 7）。"
    )
    lines.append("")

    for run, summary, label in (
        (run_a, summary_a, "劇本 A（每天新建一條線頭 ＋ 關閉最舊的到期線頭）"),
        (run_b, summary_b, "劇本 B（每天只推進既有線頭；反事實對照）"),
    ):
        lines.append("-" * 100)
        lines.append(f"{label}")
        lines.append("-" * 100)
        for slot in run["slots"]:
            lines.append(
                f"[slot {slot['index'] + 1:02d}] {slot['slot']:<7} "
                f"local={slot['local']}  UTC={slot['utc']}  UTC日期={slot['utc_date']}"
            )
            for agent_id in AGENTS:
                row = slot["agents"][agent_id]
                created = row["created"]
                titles = [row["titles"].get(t) for t in created]
                lines.append(
                    f"    {agent_id:<13} woke={str(row['woke']):<5} reason={str(row['reason']):<24} "
                    f"建立={len(created)} 關閉={len(row['transitioned'])} 推進={len(row['advanced'])} "
                    f"失敗={row['skipped']} active={row['active']}/{row['cap']} "
                    f"streak_all={row['streak_all']} streak_window={row['streak_window']} "
                    f"新thread={created} 標題={titles}"
                )
            lines.append("")

        lines.append("【量測 1】容量動態（分子／分母／單位）")
        for agent_id in AGENTS:
            created_n = len(summary["created_ids"][agent_id])
            closed_n = len(summary["closed_ids"][agent_id])
            rejected_n = summary["create_rejected_slots"][agent_id]
            gaps = summary["slots_without_create"][agent_id]
            deadlocks = summary["deadlock_runs"][agent_id]
            per_day = []
            for slot in run["slots"]:
                day = slot["local_date"]
                n = len(slot["agents"][agent_id]["created"])
                if per_day and per_day[-1].split("=")[0] == day:
                    head, count = per_day[-1].split("=")
                    per_day[-1] = f"{head}={int(count) + n}"
                else:
                    per_day.append(f"{day}={n}")
            lines.append(
                f"    {agent_id:<13} 新建={created_n} 條／14 slot；關閉={closed_n} 條／14 slot；"
                f"建立失敗（容量飽和）={rejected_n} slot／14 slot；"
                f"無新建的 slot={len(gaps)} 個；容量死鎖（連續≥{DEADLOCK_MIN_SLOTS} slot）={len(deadlocks)} 段"
            )
            lines.append(
                f"    {'':<13} 每日新建數（local 日，分子＝當日新建線頭數、單位＝條）＝{' '.join(per_day)}"
            )
        lines.append("")

        lines.append("【量測 2】標題多樣性（集合大小 ÷ 新建總數）")
        for agent_id in AGENTS:
            div = title_diversity(summary["created_titles"][agent_id])
            if div["total"] == 0:
                lines.append(
                    f"    {agent_id:<13} N/A（本劇本 0 條新建線頭 ⇒ 分子分母皆為 0）"
                )
                continue
            lines.append(
                f"    {agent_id:<13} {div['distinct']}／{div['total']} ＝ {div['ratio']:.4f}"
                f"（分子＝不同標題數、分子單位＝個；分母＝新建線頭數、單位＝條）；"
                f"重複標題={div['duplicates']}"
            )
        all_titles = [t for a in AGENTS for t in summary["created_titles"][a]]
        overall = title_diversity(all_titles)
        if overall["total"] == 0:
            lines.append(f"    {'（全體）':<13} N/A（0 條新建線頭）")
        else:
            lines.append(
                f"    {'（全體）':<13} {overall['distinct']}／{overall['total']} ＝ {overall['ratio']:.4f}"
            )
        lines.append("")

        lines.append("【量測 3】streak 計數器（門檻三正確算法；逐 slot ＋ 逐 local 日）")
        for agent_id in AGENTS:
            per_slot = []
            for slot in run["slots"]:
                row = slot["agents"][agent_id]
                per_slot.append(f"{slot['local_date']}{slot['slot'][0]}:{row['streak_window']}")
            # 每個 local 日的最後一個 slot ＝ 該日的收尾值（第 1~7 天）。
            per_day: List[Tuple[str, int]] = []
            for slot in run["slots"]:
                day = slot["local_date"]
                if per_day and per_day[-1][0] == day:
                    per_day[-1] = (day, slot["agents"][agent_id]["streak_window"])
                else:
                    per_day.append((day, slot["agents"][agent_id]["streak_window"]))
            last = run["slots"][-1]["agents"][agent_id]
            lines.append(
                f"    {agent_id:<13} 逐 slot＝{' '.join(per_slot)}"
            )
            lines.append(
                f"    {'':<13} 逐 local 日（1~7 天收尾）＝"
                f"{' '.join(f'D{i + 1}:{v}' for i, (_d, v) in enumerate(per_day))}；"
                f"第 7 天 streak_window={last['streak_window']}、streak_all={last['streak_all']}"
            )
        lines.append("")

        lines.append("【量測 4】反事實對照（劇本 B 不轉綠 ⇒ 判準 2 的「不同 thread_id」必要）")
        for agent_id in AGENTS:
            last_a = run_a["slots"][-1]["agents"][agent_id]
            last_b = run_b["slots"][-1]["agents"][agent_id]
            lines.append(
                f"    {agent_id:<13} 劇本A 第7天 streak_window={last_a['streak_window']}／"
                f"streak_all={last_a['streak_all']}；劇本B 第7天 streak_window={last_b['streak_window']}／"
                f"streak_all={last_b['streak_all']}（起點 streak_all={baseline[agent_id]['streak_all']}）"
            )
        lines.append("")

    lines.append("-" * 100)
    lines.append("【§3.4】容量動態模型 ＋ 機率表")
    lines.append("-" * 100)
    for order, payload in capacity_shortfall_model(cap=2, start_active=2).items():
        first = payload["first_shortfall"]
        lines.append(
            f"    動作次序 {order:<13}：第一次空位不足＝"
            f"{'第 %d 天 %s slot' % (first[0] + 1, first[1]) if first else '從未發生'}"
        )
    base = production_round_base_rate()
    lines.append(
        f"    生產實測每輪關閉率 p_close＝{base['p_close_per_round']:.4f}"
        f"（分子＝有閉閉的輪數 {base['closed_rounds']}／分母＝有動作的輪數 {base['rounds']}）；"
        f"每輪新建率 p_create＝{base['p_create_per_round']:.4f}；"
        f"事件級關閉率＝{base['p_close_per_event']:.4f}"
        f"（{base['closed_events']}／{base['action_events']}）"
    )
    lines.append("    機率表：每天 1 次關閉嘗試、成功率 p、7 天")
    for p in (0.2, 0.4, 0.6, 0.8, 1.0):
        row = close_probability(p)
        lines.append(
            f"        p={p:.1f} ⇒ P(7 天內至少 1 次關閉)={row['p_at_least_one_close']:.4f}；"
            f"P(至少 {row['k']} 次)={row['p_at_least_k_closes']:.4f}"
        )
    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 測試
# ══════════════════════════════════════════════════════════════


def _require_production() -> None:
    if not (PROD_SOUL_DIR_REAL / AGENTS[0] / LIFE_THREADS_FILENAME).is_file():
        pytest.skip(
            "本機無生產生活線頭日誌（data/soul/*/life_threads.jsonl）⇒ 無法推導起點狀態。"
            "本測試只在有生產資料的機器上有意義（缺席即明確 skip，不靜默通過）。"
        )


def _effective_root(sim_root: Path) -> Path:
    """模擬的**寫入**根（變異 4 會把它換成 tmp 內的生產沙盒副本）。"""
    return SIM_DATA_ROOT_OVERRIDE or sim_root


def _prepare(sim_root: Path) -> Dict[str, Any]:
    """推導起點狀態 ＋ 落地到隔離資料根 ＋ 回傳每 agent 的起始量測。

    🔴 記憶副本的**來源**永遠是真生產（`PROD_SOUL_DIR_REAL`，唯讀）；
    **寫入目標**才是隔離根（正常）或生產沙盒副本（變異 4）。
    """
    _require_production()
    root = _effective_root(sim_root)
    os.environ["SOUL_OS_DATA_DIR"] = str(root)
    reset_data_root()
    rows = derive_start_state(START_CUTOFF_UTC, prod_dir=PROD_SOUL_DIR_REAL)
    materialize(rows, root)
    baseline: Dict[str, Any] = {}
    for agent_id in AGENTS:
        snap = _agent_snapshot(agent_id, set())
        aged = 0
        for state in lt.fold(agent_id).values():
            if state.get("status") != "active":
                continue
            created = _parse_iso(state.get("created_at"))
            if created is None:
                continue
            if (START_CUTOFF_UTC - created).total_seconds() / 86400.0 > 14:
                aged += 1
        baseline[agent_id] = {
            "active": snap["active"],
            "cap": snap["cap"],
            "streak_all": snap["streak_all"],
            "dates": sorted(d.isoformat() for d in snap["dates"]),
            "aged_active": aged,
        }
    return baseline


def _reset_to_start(sim_root: Path) -> Dict[str, Any]:
    """把寫入根**重置**回起點狀態（劇本之間的隔離；避免 B 從 A 的尾狀態起步）。"""
    root = _effective_root(sim_root)
    for agent_id in AGENTS:
        target = root / "soul" / agent_id / LIFE_THREADS_FILENAME
        if target.is_file():
            target.unlink()
    return _prepare(sim_root)


def test_start_state_is_all_saturated(sim_root, clock):
    """起點狀態＝工單 §0.1.2 的現況基準，且**由本測試自行重推**（不採信轉錄數字）。"""
    baseline = _prepare(sim_root)
    saturated = [a for a in AGENTS if baseline[a]["active"] >= baseline[a]["cap"]]
    assert len(saturated) == len(AGENTS), (
        f"起點未全部滿載：{[(a, baseline[a]['active'], baseline[a]['cap']) for a in AGENTS]}"
    )
    total_active = sum(baseline[a]["active"] for a in AGENTS)
    assert total_active == 20, f"起點 active 總數＝{total_active}（預期 20）"
    for agent_id in AGENTS:
        assert baseline[agent_id]["cap"] == 2, (
            f"{agent_id} 的 cap＝{baseline[agent_id]['cap']}（預期 2）"
        )
    # 工單 §0.1.22 的交叉對照：mahiru 的 5 天連續（最高值）。
    assert baseline["agent_mahiru"]["streak_all"] == 5, (
        f"mahiru 起點 streak_all＝{baseline['agent_mahiru']['streak_all']}（預期 5）"
    )


def test_scenario_a_creates_and_streak_reaches_seven(sim_root, clock, world_records):
    """劇本 A：7 天 × 2 slot ⇒ 容量動態、標題多樣性、streak 逐日值。"""
    _prepare(sim_root)
    run = run_scenario("A", clock, world_records=world_records, sim_root=sim_root)
    summary = summarize_scenario(run)

    assert len(run["slots"]) == 14, f"slot 數＝{len(run['slots'])}（預期 14）"
    assert run["llm_calls"] > 0, "劇本 A 未觸發任何 LLM 呼叫（腳本化 LLM 未被走到）"

    created_per_agent = {a: len(summary["created_ids"][a]) for a in AGENTS}
    for agent_id in AGENTS:
        count = created_per_agent[agent_id]
        assert 7 <= count <= 10, (
            f"{agent_id} 的新建線頭數＝{count} 條（預期 7~10 條／7 天）"
        )
        # 標題多樣性：每個 agent 自己的標題互不相同。
        div = title_diversity(summary["created_titles"][agent_id])
        assert div["ratio"] == 1.0, (
            f"{agent_id} 的標題多樣性＝{div['distinct']}／{div['total']}（預期 1.0）；"
            f"重複＝{div['duplicates']}"
        )
        # 建立失敗（容量飽和）＝ 0：腳本化 LLM 只在有空位時才要求建立。
        assert summary["create_rejected_slots"][agent_id] == 0, (
            f"{agent_id} 出現 {summary['create_rejected_slots'][agent_id]} 個容量飽和的建立失敗"
        )
        # 容量死鎖：連續 ≥3 個 slot 無法建立新線頭。
        assert not summary["deadlock_runs"][agent_id], (
            f"{agent_id} 出現容量死鎖：{summary['deadlock_runs'][agent_id]}"
        )
        # 每日不同 thread_id（門檻三判準 2）。
        for day, per_agent_rows in summary["day_rows"].items():
            ids = [
                tid
                for row in per_agent_rows.get(agent_id, [])
                for tid in row["thread_ids"]
            ]
            assert len(ids) == len(set(ids)), f"{agent_id} 在 {day} 出現重複 thread_id：{ids}"

    # 容量上限確實是「活的約束」：模擬期間必須真的出現過滿載（active == cap）。
    # （否則這條容量量測就是空轉；把 cap 調成無限 ⇒ 此斷言轉紅。）
    saturated_slots = sum(
        1
        for slot in run["slots"]
        for row in slot["agents"].values()
        if row["active"] >= row["cap"]
    )
    assert saturated_slots > 0, "模擬期間從未滿載（active == cap）⇒ 容量約束未被觸及"

    # streak：第 7 天（7 個 local 日走完）每個 agent 都應達 7。
    reaching = [
        a for a in AGENTS if run["slots"][-1]["agents"][a]["streak_window"] >= 7
    ]
    assert len(reaching) == len(AGENTS), (
        f"第 7 天 streak_window 達 7 的 agent＝{len(reaching)}／{len(AGENTS)}：{reaching}"
    )
    # 窗口上限＝8（7 個 local 日 ＝ 8 個 UTC 日期）；多數 agent 應頂到上限。
    window_max = max(run["slots"][-1]["agents"][a]["streak_window"] for a in AGENTS)
    assert window_max == 8, f"窗口 streak 上限＝{window_max}（預期 8）"
    at_max = [
        a for a in AGENTS if run["slots"][-1]["agents"][a]["streak_window"] == window_max
    ]
    assert len(at_max) >= 9, (
        f"第 7 天 streak_window 頂到 {window_max} 的 agent＝{len(at_max)}／{len(AGENTS)}：{at_max}"
    )
    for agent_id in AGENTS:
        last = run["slots"][-1]["agents"][agent_id]
        assert last["streak_all"] >= 7, (
            f"{agent_id} 第 7 天 streak_all＝{last['streak_all']}（預期 ≥7）"
        )


def test_scenario_b_counterfactual_does_not_turn_green(sim_root, clock, world_records):
    """劇本 B（反事實）：只推進既有線頭 ⇒ streak 不轉綠 ⇒ 判準 2 的 thread_id 條件必要。"""
    _prepare(sim_root)
    run = run_scenario("B", clock, world_records=world_records, sim_root=sim_root)
    summary = summarize_scenario(run)

    for agent_id in AGENTS:
        assert not summary["created_ids"][agent_id], (
            f"{agent_id} 在劇本 B 竟建立了線頭：{summary['created_ids'][agent_id]}"
        )
        assert not summary["closed_ids"][agent_id], (
            f"{agent_id} 在劇本 B 竟關閉了線頭：{summary['closed_ids'][agent_id]}"
        )
        last = run["slots"][-1]["agents"][agent_id]
        assert last["streak_all"] < 7, (
            f"{agent_id} 的 streak_all＝{last['streak_all']}（劇本 B 必須 < 7）"
        )
        assert last["streak_window"] < 7, (
            f"{agent_id} 的 streak_window＝{last['streak_window']}（劇本 B 必須 < 7）"
        )
    # 劇本 B 仍須有推進（否則整場是空跑，結論沒意義）。
    advanced = sum(
        len(run["slots"][i]["agents"][a]["advanced"])
        for i in range(len(run["slots"]))
        for a in AGENTS
    )
    assert advanced > 0, "劇本 B 沒有任何推進（反事實無效）"


def test_streak_counter_hand_computed(sim_root, clock):
    """守衛 4：streak 計數器與**手算**一致（寫死的對照案例）。"""
    # 案例 1：{10-01, 10-02, 10-03, 10-05, 10-06} ⇒ 最長連續＝3（10-01~10-03）。
    case1 = {
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
        date(2026, 10, 5),
        date(2026, 10, 6),
    }
    assert measure_streak(case1) == 3, f"案例 1 的 streak＝{measure_streak(case1)}（預期 3）"
    # 案例 2：mahiru 的生產日期集合 ⇒ 最長連續＝5（10-03~10-07）。
    case2 = {
        date(2026, 9, 24),
        date(2026, 9, 30),
        date(2026, 10, 3),
        date(2026, 10, 4),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
        date(2026, 10, 13),
        date(2026, 10, 15),
        date(2026, 10, 20),
    }
    assert measure_streak(case2) == 5, f"案例 2 的 streak＝{measure_streak(case2)}（預期 5）"
    # 案例 3：空集合 ⇒ 0。
    assert measure_streak(set()) == 0
    # 標題多樣性計數器的手算對照：4 個標題、其中 1 個重複 ⇒ 3／4。
    div = title_diversity(["a", "b", "c", "a"])
    assert div["distinct"] == 3 and div["total"] == 4 and div["ratio"] == 0.75
    assert div["duplicates"] == ["a"]


def test_streak_counter_is_not_days_since_last_update(sim_root, clock):
    """守衛 5：streak 計數器**不是**「距今天數」（§0.1.22 已作廢的口徑）。"""
    # 最後一次更新距今 30 天，但**只有 1 天**有推進 ⇒ 答案必須是 1，不是 30。
    today = date(2026, 10, 20)
    dates = {today - timedelta(days=30)}
    assert days_since_last_update(dates, today) == 30  # 已作廢口徑（供對照）
    assert measure_streak(dates) == 1, (
        f"streak 計數器回傳 {measure_streak(dates)}（預期 1；得到 30 ⇒ 復辟了已作廢口徑）"
    )


def test_no_real_llm_and_no_sleep(sim_root, clock, probe, world_records):
    """守衛 1 ＋ 2：0 真實 LLM、0 連外、0 sleep、總耗時 < 30 秒。"""
    _prepare(sim_root)
    started = time.monotonic()
    run_a = run_scenario("A", clock, world_records=world_records, sim_root=sim_root)
    _reset_to_start(sim_root)
    run_b = run_scenario("B", clock, world_records=world_records, sim_root=sim_root)
    elapsed = time.monotonic() - started

    assert probe.calls == [], f"決策路徑碰到真實 LLM proxy：{probe.calls}"
    assert elapsed < 30.0, f"模擬總耗時＝{elapsed:.2f}s（預期 < 30s）"
    # 腳本化 LLM 100% 覆蓋：每一輪喚醒都必須由腳本化 stub 回答。
    woke = sum(
        1
        for run in (run_a, run_b)
        for slot in run["slots"]
        for row in slot["agents"].values()
        if row["woke"] is True
    )
    assert run_a["llm_calls"] + run_b["llm_calls"] == woke, (
        f"腳本化 LLM 呼叫數={run_a['llm_calls'] + run_b['llm_calls']} ≠ 喚醒數={woke}"
    )


def test_production_untouched(sim_root, clock, world_records):
    """守衛 3：生產 `data/soul/**/life_threads.jsonl` 的 sha256 前後逐位元相同。

    另含 **inode 共享防護**（2026-10-20 23:34 RECALL-30D 事故的教訓）：
    沙盒副本必須是**獨立 inode**的實體複製。若沙盒與生產共用 inode（硬連結），
    append 會直接寫進生產 inode ⇒ 污染是「結構上可能」的。本檔的 fork 是
    「讀進記憶體 → 重新序列化寫檔」⇒ 全新 inode；此斷言把該不變量**寫成斷言**。
    """
    before = prod_life_threads_digest()
    _prepare(sim_root)

    for agent_id in AGENTS:
        prod_file = PROD_SOUL_DIR_REAL / agent_id / LIFE_THREADS_FILENAME
        fork_file = _effective_root(sim_root) / "soul" / agent_id / LIFE_THREADS_FILENAME
        assert prod_file.is_file(), f"生產檔不存在：{prod_file}"
        assert fork_file.is_file(), f"沙盒副本不存在：{fork_file}"
        assert not os.path.samefile(prod_file, fork_file), (
            f"{agent_id} 的沙盒副本與生產檔**共用 inode** ⇒ append 會寫進生產"
            "（硬連結事故的根因；本檔的 fork 必須是獨立 inode 的實體複製）"
        )

    run_scenario("A", clock, world_records=world_records, sim_root=sim_root)
    _reset_to_start(sim_root)
    run_scenario("B", clock, world_records=world_records, sim_root=sim_root)
    after = prod_life_threads_digest()
    assert before == after, (
        "生產生活線頭日誌被改動："
        f"新增={sorted(set(after) - set(before))} 消失={sorted(set(before) - set(after))} "
        f"變動={[k for k in before if after.get(k) != before.get(k)]}"
    )


def test_capacity_model_and_probability_table(sim_root, clock):
    """§3.4：容量動態模型 ＋ 機率表（純計算；不含任何政策調整）。"""
    model = capacity_shortfall_model(cap=2, start_active=2, days=SIM_DAYS)
    # create-first：池一開始就滿 ⇒ 第 1 個 slot 就出現空位不足。
    assert model["create-first"]["first_shortfall"] == (0, "morning"), (
        f"create-first 的第一次空位不足＝{model['create-first']['first_shortfall']}"
    )
    # close-first：只要 LLM 每輪都關閉，就永遠不會空位不足（代價＝每天一次關閉決定）。
    assert model["close-first"]["first_shortfall"] is None
    # 機率表的端點：p=0 ⇒ 0；p=1 ⇒ 1。
    assert close_probability(0.0)["p_at_least_one_close"] == 0.0
    assert close_probability(1.0)["p_at_least_one_close"] == 1.0
    assert close_probability(1.0)["p_at_least_k_closes"] == 1.0
    mid = close_probability(0.5)
    assert 0.0 < mid["p_at_least_one_close"] < 1.0
    # 生產實測的每輪關閉率（唯讀量測）。
    base = production_round_base_rate()
    assert base["rounds"] > 0, "生產實測未取得任何分輪（分母＝0）"


def test_conclusion_is_capacity_and_probability(sim_root, clock, world_records):
    """結論支撐：門檻三判準 1 的不穩定性來源＝容量／機率（不是純時序）。

    三條**可量測**的證據（不靠推論）：

    1. **容量**：起點 10/10 滿載（`active=cap=2`）⇒ 任何新線頭都必須先有一個被關閉的槽位。
    2. **時間軸不會收束**：對起點狀態直接跑 M2 決策層（生產政策值）⇒ age>14 天的
       active 線頭只得到 **advisory**（`should_mutate=False`、`target_status=active`），
       也就是說**時間流逝不會讓它們完成**。
    3. **關閉是決策**：模擬期間每一次狀態轉移，都對應到腳本化 LLM 的一個 `complete`
       動作 ⇒ 沒有任何「自動」路徑會關閉線頭。
    """
    baseline = _prepare(sim_root)

    # ── 證據 1：滿載 ────────────────────────────────────────────
    assert all(baseline[a]["active"] >= baseline[a]["cap"] for a in AGENTS)
    assert sum(baseline[a]["active"] for a in AGENTS) == 20

    # ── 證據 2：M2 advisory 不變更狀態（時間軸無自動收束）────────
    aged_threads: List[Dict[str, Any]] = []
    for agent_id in AGENTS:
        for state in lt.fold(agent_id).values():
            if state.get("status") != "active":
                continue
            created = _parse_iso(state.get("created_at"))
            if created is None:
                continue
            if (START_CUTOFF_UTC - created).total_seconds() / 86400.0 > 14:
                aged_threads.append(state)
    assert aged_threads, "起點沒有任何 age>14 天的 active 線頭（前提不成立）"
    evals = lt_diss.evaluate_batch_dissolution(
        aged_threads,
        START_CUTOFF_UTC,
        max_active_duration_days=m5.POLICY_MAX_ACTIVE_DURATION_DAYS,
        stale_check_threshold_days=m5.POLICY_STALE_CHECK_THRESHOLD_DAYS,
        allow_soft_archive=m5.POLICY_ALLOW_SOFT_ARCHIVE,
    )
    assert evals, "M2 對 age>14 天的 active 線頭竟無裁決"
    for evaluation in evals:
        assert evaluation.should_mutate is False, (
            f"{evaluation.thread_id} 的 M2 裁決竟然要變更狀態（{evaluation}）"
        )
        assert evaluation.target_status.value == "active", (
            f"{evaluation.thread_id} 的 M2 目標狀態＝{evaluation.target_status}"
        )
        assert (evaluation.extra_metadata or {}).get("advisory") is True, (
            f"{evaluation.thread_id} 的 M2 裁決缺 advisory 標記"
        )

    # ── 證據 3：每一次關閉都來自 LLM 決策 ───────────────────────
    run = run_scenario("A", clock, world_records=world_records, sim_root=sim_root)
    stub_completes = {
        (call["agent_id"], action.get("thread_id"))
        for call in run["stub"].calls
        for action in call["actions"]
        if action.get("op") == "complete"
    }
    transitioned = [
        (agent_id, tid)
        for slot in run["slots"]
        for agent_id, row in slot["agents"].items()
        for tid in row["transitioned"]
    ]
    assert transitioned, "劇本 A 沒有任何關閉（證據 3 無從成立）"
    assert set(transitioned) <= stub_completes, (
        f"有線頭不是被 LLM 的 complete 動作關閉的：{sorted(set(transitioned) - stub_completes)}"
    )


def test_report(sim_root, clock, capsys, world_records):
    """報告：兩劇本的 14 個 slot 逐 slot 明細 ＋ 4 項量測 ＋ 模型與機率表。"""
    baseline = _prepare(sim_root)
    run_a = run_scenario("A", clock, world_records=world_records, sim_root=sim_root)
    summary_a = summarize_scenario(run_a)
    # 劇本 B 需從**乾淨**起點重跑（同一資料根已被劇本 A 寫過）。
    baseline_b = _reset_to_start(sim_root)
    run_b = run_scenario("B", clock, world_records=world_records, sim_root=sim_root)
    summary_b = summarize_scenario(run_b)
    text = render_report(run_a, run_b, summary_a, summary_b, baseline_b)
    with capsys.disabled():
        print(text)
    assert "劇本 A" in text and "劇本 B" in text
    assert "容量動態模型" in text and "機率表" in text
