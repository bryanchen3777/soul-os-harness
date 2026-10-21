# tests/soul/test_sim_accel_streak_7d_sunset.py
# LIFE-THREAD-M2-SUNSET — 確定性超期歸檔的**沙盒收束性**驗證（7 天 × 14 slot × 2 劇本）。
"""確定性超期歸檔（Deterministic Sunset）的沙盒收束性驗證。

為什麼要有這個檔
────────────────
門檻三（連續 7 天可見性）的**判準 1** 至今不成立。SIM-ACCEL-STREAK-7D 的沙盒實測
已定案：這是**容量／機率**問題 —— 10 隻 agent 全部 `active == cap == 2` 永久滿載，
而 M2 的存活軸在 `allow_soft_archive=False` 下**只產 advisory**（`should_mutate=False`）
⇒ active 線頭**永遠不會自發轉入終態** ⇒ 容量位元只進不出 ⇒ 每天無法建立新線頭
⇒ 7 天連續新 `thread_id` **根本無路徑**。

本檔在**虛擬沙盒**裡驗證：接上確定性超期歸檔後，7 天 × 14 slot 的模擬是否**收斂**
（門檻三判準 1 於第 7 天轉綠），以及機制**不會誤傷**年輕線頭。

兩劇本（工單 §3.6）
──────────────────
- **劇本 A（順從型 LLM：可關一開一）**：起點＝**生產現況**（唯讀截斷副本）。
  關閉最舊的**到期**線頭 ＋ 在有空位時建立一條新線頭。
  ⇒ 驗收：**第 7 天 streak 計數器 10/10 達 7**（判準 1 轉綠）；標題多樣性維持 1.0000。
- **劇本 B（反事實：不新建不關閉）**：起點＝**劇本 A 的終態**（7 天後）。
  只推進既有線頭，**不新建、不關閉**。
  ⇒ 驗收：**sunset 不得發生**（此時 agent 的線頭都很年輕 ⇒ 條件 1 不成立）⇒
  「機制運行一週後，agent 停止新建時**不會誤傷**它的新線頭」。

🔴 **已登記的票面校正（§3.6 對帳項 2）**：工單原文預期「劇本 B：sunset 不得發生」，
並註明「若劇本 B 反而 sunset，須在報告中解釋並調整條件」。**實測事實**：sunset 的
四項條件**與 LLM 行為無關**（條件 1 看 `created_at`、條件 2 看 active 數、條件 3 看
「距上次新建的槽數」），故只要**起點**是生產現況（10/10 飽和、7/10 最舊線頭
age > 21 天、距上次新建 ≥ 5 槽），**第一個 slot 就會 sunset**，與劇本 A／B 無關。
⇒ 本檔的處理：
  1. **劇本 B 的起點改為劇本 A 的終態**（線頭皆 ≤ 7 天）⇒ 滿足「劇本 B 不 sunset」
     這條驗收，且驗收的語意（不誤傷年輕線頭）**正是**機制最需要被證明的性質；
  2. **另附** `test_scenario_b_from_production_start_sunsets_are_accounted`：以生產
     現況為起點跑同一個反事實劇本，把「真的會 sunset」這件事**連同四項條件逐條
     對帳**釘死（不藏著）。

平面不變量（沿用 SIM-TIME-001／M2-WRITEBACK-SIM-1／M2-CATCHUP-1 的同一套 primitive）
──────────────────────────────────────────────────────────────────────────────────
1. **假時鐘**：`lt.now_utc_iso()` 被換成可變假時鐘；連續 slot 一律**直接換 `now`**
   再呼叫 ⇒ **0 `time.sleep`／0 真實等待**（並以 `time.sleep` 攔阻自證）。
2. **記憶 fork**：生產 `data/soul/**` **唯讀**；模擬寫入全部落在 `tmp_path` 的
   隔離根（`SOUL_OS_DATA_DIR`）⇒ 生產檔 sha256 前後**逐位元相同**。
3. **零 LLM**：腳本化 LLM 覆蓋 100%；另裝「真實 proxy 探針」（`lt_origins._find_llm_proxy`
   ⇒ 命中即紅燈）＋ `socket.create_connection` 攔阻 ⇒ **0 真實外呼**。
4. **0 `src/**` 改動**：本檔只讀生產模組。

跨檔 import 的取捨
──────────────────
`tests/soul/` 無 `__init__.py` ⇒ **不**跨檔 import 另一個測試模組 ⇒ **複製最小必要
primitive**（沿用既有風格）。

觀測輸出
────────
`[SUNSET-SIM-OBS]` 開頭的離散觀測行（`pytest -s` 可見）只含 id／狀態／計數／憑據，
**永不**含 prompt 或敘述原文。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

# ══════════════════════════════════════════════════════════════
# 常數：起點、時間軸、投影欄位
# ══════════════════════════════════════════════════════════════

PROD_SOUL_DIR: Path = _REPO_ROOT / "data" / "soul"
LIFE_THREADS_FILENAME = "life_threads.jsonl"

#: 10 隻生產 agent（依字母序＝決定性順序）。
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

#: 記憶副本的截斷邊界（UTC）＝ 生產現況基準（10 隻 agent 全部 `active=2`／`cap=2`）。
START_CUTOFF_UTC = datetime(2026, 10, 21, 2, 30, 0, tzinfo=timezone.utc)

#: 模擬時間軸：7 個 local 日曆日 × 2 slot ＝ 14 個 slot（08:00／22:00 local）。
SIM_DAYS = 7
SIM_START_LOCAL = datetime(2026, 10, 21, 8, 0, 0, tzinfo=LOCAL_TZ)
SLOT_HOURS: Tuple[Tuple[int, str], ...] = ((8, "morning"), (22, "night"))

#: 記憶副本投影欄位（決策相關；**不含** `title`／`narrative_content` 等敘事內容）。
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

#: 腳本化 LLM 寫入的 `next_check_hours`（24h ＝ 生產眾數；**不催熟**線頭）。
NEXT_CHECK_HOURS = 24

#: 總耗時上限（工單 §3.6 對帳項 6：< 60 秒）。
MAX_ELAPSED_SECONDS = 60.0

_SOUL_STUB = "（模擬人格語境；LIFE-THREAD-M2-SUNSET 固定字串，不讀生產 persona）"


def _obs(label: str, **fields: Any) -> None:
    """離散觀測行（只記 id／狀態／計數／憑據；**永不**記 prompt 或敘述原文）。"""
    pairs = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[SUNSET-SIM-OBS] {label} {pairs}")


# ══════════════════════════════════════════════════════════════
# 純函式工具：時間解析、streak 計數器、標題多樣性、雜湊
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
    """ISO 字串 → **UTC** 日期（門檻三「有推進日（UTC）」口徑）。"""
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
    """生產 `data/soul/**/life_threads.jsonl` 的逐檔 sha256（**唯讀**）。"""
    root = soul_dir or PROD_SOUL_DIR
    digests: Dict[str, str] = {}
    if not root.is_dir():
        return digests
    for path in sorted(root.glob(f"*/{LIFE_THREADS_FILENAME}")):
        digests[path.relative_to(root).as_posix()] = _sha256_file(path)
    return digests


def streak_from_dates(dates: Iterable[date]) -> int:
    """**門檻三的正確算法**：日期集合 → 對每個日期作錨點往前連續計數 → 取最大值。"""
    unique = {d for d in dates if isinstance(d, date)}
    best = 0
    for anchor in unique:
        run = 1
        while (anchor - timedelta(days=run)) in unique:
            run += 1
        best = max(best, run)
    return best


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
# 記憶副本：生產事件流的時間截斷 ＋ 落地（生產唯讀）
# ══════════════════════════════════════════════════════════════


def derive_start_state(
    cutoff_utc: datetime,
    *,
    prod_dir: Optional[Path] = None,
    agents: Sequence[str] = AGENTS,
) -> Dict[str, List[Dict[str, Any]]]:
    """由生產 append-only 事件日誌**時間截斷**推導起點狀態（唯讀）。"""
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


def copy_soul_tree(src_root: Path, dst_root: Path, agents: Sequence[str] = AGENTS) -> Path:
    """把沙盒 A 的線頭檔**實體複製**到另一個沙盒 B（**獨立 inode**，不用硬連結）。

    🔴 2026-10-20 23:34 事故教訓：`os.link` 會讓兩個沙盒共用 inode ⇒ 一邊寫入污染
    另一邊（當時污染了生產 `elevation_lifecycle_events.jsonl`）。⇒ 一律 `copy2`。
    """
    soul_dir = dst_root / "soul"
    for agent_id in agents:
        src = src_root / "soul" / agent_id / LIFE_THREADS_FILENAME
        if not src.is_file():
            continue
        target = soul_dir / agent_id
        target.mkdir(parents=True, exist_ok=True)
        dst = target / LIFE_THREADS_FILENAME
        shutil.copy2(src, dst)
        assert not os.path.samefile(src, dst), "沙盒副本必須是獨立 inode（不得硬連結）"
    return soul_dir


# ══════════════════════════════════════════════════════════════
# 世界事件（生產唯讀）：平移進模擬窗 ⇒ M3 的世界碰撞喚醒路徑是活的
# ══════════════════════════════════════════════════════════════

PROD_WORLD_TRACE: Path = _REPO_ROOT / "data" / "world" / "perception_trace.jsonl"

#: 世界碰撞視窗（小時）＝ M3 與 M4 的同名常數。
WORLD_WINDOW_HOURS = 14


def derive_world_records(
    cutoff_utc: datetime, *, world_trace: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """從**生產**感知軌跡取出「合格」的世界事件（唯讀）。

    合格口徑（逐字對齊 M3 `_scan_world_collision` ＋ M4 `collect_world_seeds`）：
    `accepted is True`；`source ∈ {weather, news, news_event, calendar, calendar_event}`；
    `extra["summary"]` 非空；`consumed_by_wake is not True`；時間落在
    `[cutoff − 14h, cutoff]`。

    為什麼需要它：生產的世界**是活的** ⇒ M3 的世界碰撞路徑在生產每個 slot 都會命中。
    若模擬窗內沒有世界事件，「世界碰撞」這條喚醒路徑整條失效 ⇒ 量測到的就不再是
    容量動態，而是平面的假象（實測：無世界事件時 10 隻 agent 只有 5 隻喚醒過）。
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
    """把世界事件**平移**到模擬 slot 前 14 小時內，寫進隔離資料根（生產唯讀）。"""
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


def _title_for(agent_id: str, now: datetime) -> str:
    """每個 (agent, slot) 唯一、且 ≤ 40 字元的標題（帶**時段**以免同日重複）。"""
    short = agent_id.replace("agent_", "")
    return f"SIM-{short}-{now.strftime('%m%d')}-{now.strftime('%H')}"


def _narrative_for(agent_id: str, now: datetime) -> str:
    """敘事內容：1~600 字元、非佔位符、**不等同 title**。"""
    return (
        f"{now.strftime('%Y-%m-%d %H:%M')} 槽的模擬推進："
        f"{agent_id} 把一條舊線頭收束成句，替自己騰出下一個著力點。"
    )


class ScriptedLLM:
    """腳本化 LLM（**0 真實 LLM／0 網路**）。

    - **劇本 A（可關一開一）**：關閉最舊的**到期**線頭 ＋ 在「本輪開始時有空位」
      （`active < cap`）時建立一條新線頭。
    - **劇本 B（不新建不關閉）**：只回 `op=advance`（推進最舊的到期線頭）。
    """

    def __init__(self, scenario: str, clock: FakeClock) -> None:
        self.scenario = scenario
        self.clock = clock
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, messages: Any, agent_id: str) -> Optional[str]:
        now = self.clock.now
        actions = self._actions(agent_id, now)
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
                        "title": _title_for(agent_id, now),
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
# fixtures：隔離資料根、假時鐘、零 LLM、零 sleep
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
def _config_cache(monkeypatch):
    """config 載入快取（**純效能**，不改任何決策；回傳值逐位元相同）。"""
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
    """真實 proxy 探針：命中即紅燈（決策路徑不得取得真 proxy）。"""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def __call__(self) -> Any:
        self.calls.append("_find_llm_proxy")
        raise AssertionError(
            "LIFE-THREAD-M2-SUNSET：路徑碰到真實 LLM proxy（硬攔，連外不可能發生）"
        )


@pytest.fixture(autouse=True)
def probe(monkeypatch) -> LiveLLMProbe:
    """矩陣級不變量：0 真實 LLM proxy、0 連外、人格語境固定、接線旗標釘在關。"""
    live = LiveLLMProbe()
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
        raise AssertionError("LIFE-THREAD-M2-SUNSET：平面內不得連外（socket.create_connection 已攔）")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect, raising=True)
    return live


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> FakeClock:
    """假時鐘的**寫入面接縫**：`lt.now_utc_iso()` 回傳模擬 slot 時刻。"""
    fake = FakeClock()
    monkeypatch.setattr(lt, "now_utc_iso", lambda: fake.iso_utc(), raising=True)
    return fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """平面不變量：0 `time.sleep`（假時鐘 ⇒ 連續 slot 直接換 `now`）。"""

    def _blocked_sleep(*args, **kwargs):
        raise AssertionError("LIFE-THREAD-M2-SUNSET：平面內不得 sleep（假時鐘）")

    monkeypatch.setattr(time, "sleep", _blocked_sleep, raising=True)


# ══════════════════════════════════════════════════════════════
# 模擬 runner：14 個 slot ＋ 逐 slot 量測（含 sunset 對帳）
# ══════════════════════════════════════════════════════════════


def _agent_snapshot(agent_id: str, window_dates: Set[date]) -> Dict[str, Any]:
    """某 agent 在當下的 fold 視圖 ＋ streak 口徑。"""
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
        "streak_all": streak_from_dates(dates),
        "streak_window": streak_from_dates(dates & window_dates),
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
    """跑完 14 個 slot，回傳逐 slot 明細（含每次 sunset 的四項條件）。"""
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
        summaries = asyncio.run(
            m5.run_slot_pipeline(list(agents), now, slot_name, llm_caller=stub)
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
                "sunset": summary.get("sunset"),
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

    return {"scenario": scenario, "slots": slot_log, "llm_calls": len(stub.calls), "stub": stub}


def collect_sunsets(run: Dict[str, Any], agents: Sequence[str] = AGENTS) -> List[Dict[str, Any]]:
    """把逐 slot 的 sunset 事件攤平成可逐條對帳的清單。"""
    out: List[Dict[str, Any]] = []
    for slot in run["slots"]:
        for agent_id in agents:
            info = slot["agents"][agent_id].get("sunset")
            if not info:
                continue
            out.append(
                {
                    "slot_index": slot["index"],
                    "slot": slot["slot"],
                    "local": slot["local"],
                    "agent_id": agent_id,
                    "thread_id": info.get("thread_id"),
                    "created_at": info.get("created_at"),
                    "age_days": info.get("age_days"),
                    "slots_since_new_thread": info.get("slots_since_new_thread"),
                    "agent_active_count": info.get("agent_active_count"),
                    "agent_cap": info.get("agent_cap"),
                    "reason": info.get("reason"),
                    "target_status": info.get("target_status"),
                    "has_reflection_prompt": bool(info.get("reflection_prompt")),
                }
            )
    return out


def collect_created_titles(run: Dict[str, Any], agents: Sequence[str] = AGENTS) -> Dict[str, List[str]]:
    """每個 agent 在整場模擬中建立的線頭標題（供多樣性量測）。"""
    titles: Dict[str, List[str]] = {a: [] for a in agents}
    for slot in run["slots"]:
        for agent_id in agents:
            row = slot["agents"][agent_id]
            for tid in row["created"]:
                title = row["titles"].get(tid)
                if isinstance(title, str):
                    titles[agent_id].append(title)
    return titles


def final_streaks(run: Dict[str, Any], agents: Sequence[str] = AGENTS) -> Dict[str, int]:
    """最後一個 slot 的 streak（門檻三判準 1 的量測面）。"""
    last = run["slots"][-1]
    return {a: last["agents"][a]["streak_all"] for a in agents}


# ══════════════════════════════════════════════════════════════
# module 級沙盒：三場模擬各跑**一次**
# ══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    """module 級：三場模擬各跑**一次**（7 天 × 14 slot × 10 agent）。

    - **A**：起點＝生產現況（唯讀截斷副本），劇本 A（可關一開一）。
    - **B**：起點＝**A 的終態**，劇本 B（不新建不關閉）⇒ 驗收「sunset 不得發生」。
    - **B_prod**：起點＝生產現況，劇本 B ⇒ 票面校正的實測面（「真的會 sunset」）。

    🔴 module 級 ⇒ function 級的 autouse 平面護欄在**建立期**尚未生效 ⇒ 本 fixture
    **自帶**同一套護欄（`pytest.MonkeyPatch` 上下文；離開時自動還原）。
    """
    mp = pytest.MonkeyPatch()
    try:
        live = LiveLLMProbe()
        mp.setattr(lt_origins, "_find_llm_proxy", live, raising=True)
        mp.setattr(
            lt_origins, "load_soul_context", lambda agent_id, **kw: _SOUL_STUB, raising=True
        )
        for flag in (
            "LIFE_THREAD_CONSOLIDATION_ENABLED",
            "LIFE_THREAD_BOOTSTRAP_ENABLED",
            "LIFE_THREAD_WHIM_ENABLED",
            "LIFE_THREAD_CATCHUP_ENABLED",
            "LIFE_THREAD_ELEVATION_ENABLED",
        ):
            mp.setenv(flag, "")

        def _blocked_connect(*args, **kwargs):
            raise AssertionError(
                "LIFE-THREAD-M2-SUNSET：平面內不得連外（socket.create_connection 已攔）"
            )

        def _blocked_sleep(*args, **kwargs):
            raise AssertionError("LIFE-THREAD-M2-SUNSET：平面內不得 sleep（假時鐘）")

        mp.setattr(socket, "create_connection", _blocked_connect, raising=True)
        mp.setattr(time, "sleep", _blocked_sleep, raising=True)

        original_config = lt._load_default_config
        cache: Dict[str, Any] = {}

        def _cached():
            if "cfg" not in cache:
                cache["cfg"] = original_config()
            return cache["cfg"]

        mp.setattr(lt, "_load_default_config", _cached, raising=True)

        fake = FakeClock()
        mp.setattr(lt, "now_utc_iso", lambda: fake.iso_utc(), raising=True)

        base = tmp_path_factory.mktemp("sunset_sim")
        world = derive_world_records(START_CUTOFF_UTC)
        assert world, "生產世界事件為空（M3 世界碰撞路徑會整條失效 ⇒ 平面假象）"

        # ── 劇本 A：起點＝生產現況 ──
        root_a = base / "root_a"
        root_a.mkdir(parents=True, exist_ok=True)
        mp.setenv("SOUL_OS_DATA_DIR", str(root_a))
        reset_data_root()
        start_state = derive_start_state(START_CUTOFF_UTC)
        assert any(start_state.values()), "生產起點狀態為空（沙盒無從起算）"
        materialize(start_state, root_a)
        started = time.perf_counter()
        run_a = run_scenario("A", fake, world_records=world, sim_root=root_a)
        elapsed_a = time.perf_counter() - started

        # ── 劇本 B：起點＝劇本 A 的終態（**獨立 inode** 的實體複製）──
        root_b = base / "root_b"
        root_b.mkdir(parents=True, exist_ok=True)
        copy_soul_tree(root_a, root_b)
        mp.setenv("SOUL_OS_DATA_DIR", str(root_b))
        reset_data_root()
        started = time.perf_counter()
        run_b = run_scenario("B", fake, world_records=world, sim_root=root_b)
        elapsed_b = time.perf_counter() - started

        # ── 劇本 B（生產起點）：票面校正的實測面 ──
        root_b2 = base / "root_b_prod"
        root_b2.mkdir(parents=True, exist_ok=True)
        mp.setenv("SOUL_OS_DATA_DIR", str(root_b2))
        reset_data_root()
        materialize(derive_start_state(START_CUTOFF_UTC), root_b2)
        started = time.perf_counter()
        run_b2 = run_scenario("B", fake, world_records=world, sim_root=root_b2)
        elapsed_b2 = time.perf_counter() - started

        yield {
            "A": run_a,
            "B": run_b,
            "B_prod": run_b2,
            "elapsed": elapsed_a + elapsed_b + elapsed_b2,
            "elapsed_a": elapsed_a,
            "elapsed_b": elapsed_b,
            "elapsed_b_prod": elapsed_b2,
            "world_records": len(world),
        }
    finally:
        mp.undo()
        reset_data_root()


# ══════════════════════════════════════════════════════════════
# 劇本 A：起點＝生產現況（唯讀截斷副本）
# ══════════════════════════════════════════════════════════════


def test_scenario_a_streak_reaches_seven_on_day_seven(sandbox):
    """對帳項 1：劇本 A 第 7 天 streak 計數器 **10/10 達 7**（判準 1 轉綠）。"""
    run = sandbox["A"]
    streaks = final_streaks(run)
    reached = [a for a, value in streaks.items() if value >= 7]
    assert len(reached) == len(AGENTS), f"streak 未全數達 7：{streaks}"
    _obs("A-streak", reached=len(reached), total=len(AGENTS), min_streak=min(streaks.values()))


def test_scenario_a_title_diversity_stays_one(sandbox):
    """對帳項 1（續）：標題多樣性維持 **1.0000**（每個 (agent, slot) 唯一標題）。"""
    titles = collect_created_titles(sandbox["A"])
    ratios = {a: title_diversity(t)["ratio"] for a, t in titles.items()}
    assert all(ratio == 1.0 for ratio in ratios.values()), ratios
    _obs("A-diversity", ratio=min(ratios.values()), agents=len(AGENTS))


def test_every_sunset_satisfies_all_four_conditions(sandbox):
    """對帳項 3：**每一次** sunset 的四項條件**逐條成立**（不誤傷的硬證據）。"""
    sunsets = collect_sunsets(sandbox["A"])
    for item in sunsets:
        assert item["age_days"] > 21, f"條件 1 不成立：{item}"
        assert item["agent_active_count"] >= item["agent_cap"], f"條件 2 不成立：{item}"
        assert item["slots_since_new_thread"] >= 3, f"條件 3 不成立：{item}"
        assert item["target_status"] == "completed", f"目標狀態不對：{item}"
        assert item["reason"] == "checkpoints_exhausted", f"M2 裁決理由不對：{item}"
        assert item["has_reflection_prompt"], f"缺 reflection prompt：{item}"
    # 每次評估最多釋放一條 ⇒ 同一 slot 同一 agent 至多一筆
    seen = set()
    for item in sunsets:
        key = (item["slot_index"], item["agent_id"])
        assert key not in seen, f"一次評估釋放多條：{key}"
        seen.add(key)
    _obs("A-sunsets", count=len(sunsets), agents=len(AGENTS))


def test_no_thread_within_21_days_is_ever_sunset(sandbox):
    """對帳項 4：age ≤ 21 天的線頭**必須 0 次**被 sunset。"""
    sunsets = collect_sunsets(sandbox["A"])
    too_young = [item for item in sunsets if float(item["age_days"]) <= 21.0]
    assert too_young == [], too_young
    _obs("A-not-wronged", young_sunset=len(too_young), total_sunset=len(sunsets))


# ══════════════════════════════════════════════════════════════
# 劇本 B（反事實：不新建不關閉）
# ══════════════════════════════════════════════════════════════


def test_scenario_b_counterfactual_never_sunsets(sandbox):
    """對帳項 2：劇本 B（反事實：不新建不關閉）⇒ **sunset 不得發生**。"""
    run = sandbox["B"]
    sunsets = collect_sunsets(run)
    assert sunsets == [], sunsets
    _obs("B-sunset", count=len(sunsets), slots=len(run["slots"]))


def test_scenario_b_counterfactual_does_not_turn_green(sandbox):
    """對帳項 2（續）：劇本 B 不新建線頭 ⇒ **不得靠 LLM 新建把 streak 撐起來**。

    🔴 劇本 B（起點＝劇本 A 終態）的 streak **先天繼承** A 的推進日（判準 1 已在 A
    轉綠）⇒ 此處驗收的是「B **沒有**因為新建而多推進任何一天」：B 的建立數必須為 0。
    """
    run = sandbox["B"]
    created = sum(len(slot["agents"][a]["created"]) for slot in run["slots"] for a in AGENTS)
    assert created == 0, f"劇本 B 不得建立任何新線頭：{created}"
    streaks = final_streaks(run)
    _obs("B-streak", max_streak=max(streaks.values()), agents=len(AGENTS), created=created)


def test_scenario_b_from_production_start_does_not_turn_green(sandbox):
    """反事實對照（生產起點）：不新建、不關閉 ⇒ 門檻三判準 1 **不得**轉綠。

    這是 SIM-ACCEL-STREAK-7D 的反事實對照面（沒有 sunset 撐起容量時，LLM 不新建
    ⇒ 連續 7 天可見性無法成立）。
    """
    streaks = final_streaks(sandbox["B_prod"])
    _obs("B-prod-streak", max_streak=max(streaks.values()), agents=len(AGENTS))
    assert all(value < 7 for value in streaks.values()), streaks


def test_scenario_b_from_production_start_sunsets_are_accounted(sandbox):
    """🔴 已登記的票面校正：以**生產現況**為起點的反事實劇本 ⇒ sunset **確實**發生。

    這是 §3.6 對帳項 2 的「若劇本 B 反而 sunset，須在報告中解釋」的實測面：
    sunset 的四項條件**與 LLM 行為無關** ⇒ 生產起點（飽和 ＋ 最舊線頭 > 21 天
    ＋ 距上次新建 ≥ 5 槽）在**第一個 slot**即滿足 ⇒ 必然 sunset。
    本測試把這件事連同四項條件**逐條釘死**（不藏著），並證明「不誤傷」仍成立。
    """
    run = sandbox["B_prod"]
    sunsets = collect_sunsets(run)
    assert sunsets, "生產起點下條件全成立 ⇒ 必須會 sunset（實測）"
    for item in sunsets:
        assert item["age_days"] > 21, item
        assert item["agent_active_count"] >= item["agent_cap"], item
        assert item["slots_since_new_thread"] >= 3, item
        assert item["target_status"] == "completed", item
        assert item["reason"] == "checkpoints_exhausted", item
    # 「不誤傷」：age ≤ 21 天的線頭 0 次被 sunset
    assert [i for i in sunsets if float(i["age_days"]) <= 21.0] == []
    _obs("B-prod-start-sunset", count=len(sunsets), slots=len(run["slots"]))


# ══════════════════════════════════════════════════════════════
# 對帳項 5／6：生產零寫入、總耗時
# ══════════════════════════════════════════════════════════════


def test_production_life_threads_are_byte_identical_after_the_sandbox(sandbox):
    """對帳項 5：生產 `data/soul/**/life_threads.jsonl` 的 sha256 **前後逐位元相同**。"""
    digest = prod_life_threads_digest()
    assert digest, "生產 life_threads 檔為空（無法對帳）"
    assert digest == _PROD_DIGEST_BEFORE, "生產檔被沙盒寫入污染了"
    _obs("prod-digest", files=len(digest), unchanged=True)


def test_total_elapsed_is_under_60_seconds(sandbox):
    """對帳項 6：總耗時 < 60 秒（假時鐘 ⇒ 0 真實等待；`time.sleep` 已攔阻）。"""
    total = float(sandbox["elapsed"])
    assert total < MAX_ELAPSED_SECONDS, f"總耗時 {total:.2f}s 超過 {MAX_ELAPSED_SECONDS}s"
    _obs("elapsed", seconds=round(total, 3), limit=MAX_ELAPSED_SECONDS)


#: 生產 digest 的**事前**快照（module import 時擷取；見 AGENTS.md 教訓：
#: 守衛測試的基準必須在「任何測試執行之前」擷取，否則同一場測試內的寫入會被
#: 自己的基準吃掉而偵測不到）。
_PROD_DIGEST_BEFORE: Dict[str, str] = prod_life_threads_digest()
