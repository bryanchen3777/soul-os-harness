"""張力自主觸發的**排程 tick**（SIM-EPIS-ACTIVATE-READY）。

為什麼需要這個模組
------------------
戰役 A 把「自主求知行動 ＋ 話頭掛載隊列」建好了，但背景行動**只掛在 proxy 的
訊息路徑上** ⇒ 沒有人跟某 agent 講話 ⇒ 沒有訊息組裝 ⇒ 背景行動**永遠不會觸發**
（「自己想通一件事」在結構上不可能發生）。

本模組提供**排程自主觸發**：

- :func:`tick` ＝ **純函式**：對每隻生產 agent 判定「是否落在張力窗內且有未閉合
  張力」⇒ 回傳本輪應觸發背景行動的 agent 清單。**只讀、零 LLM、零網路**。
- :func:`run_scheduled_pass` ＝ tick ＋ 逐 agent 觸發背景行動（經 proxy 的**共用
  節流入口** ⇒ 與訊息路徑不會對同一 agent 重複觸發）。
- :func:`start_scheduler_thread` ／ :func:`ensure_scheduler_started` ＝ 排程執行緒
  （**旗標缺省關閉 ⇒ 不啟動任何執行緒 ⇒ 生產零行為變化**）。

節流常數**沿用** ``src.llm.proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS``（逐 agent）
⇒ 本模組**不另立**一套節流。

🔴 本模組**不**產生任何對用戶的發訊：不呼叫 ``_publish_agency_trigger``、不新增
任何發訊分支、不觸碰事件總線的使用者面向事件。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

from src.inner_life import epistemic_action as _epistemic_action
from src.inner_life import piggyback_queue as _piggyback_queue
from src.soul.temporal_phenomenology import STATE_TENSION

logger = logging.getLogger("soul_os.inner_life.epistemic_tension_scheduler")

#: 排程執行緒名稱（測試可據此確認「旗標關閉 ⇒ 沒有執行緒」）。
THREAD_NAME = "epistemic-tension-scheduler"

#: 三態張力窗的**下界**（秒）：距上次互動 ≥ 24 小時 ⇒ 進入「張力窗」。
#:
#: ⚠️ 鏡像 ``src.soul.temporal_phenomenology`` 的私有常數（該模組的常數是私有的，
#: 不可 import）。漂移由測試 ``test_tension_window_constants_match_upstream`` 釘死。
CALM_MAX_ELAPSED_SEC = 24 * 3600

#: 三態張力窗的**上界**（秒）：距上次互動 ≥ 7 天 ⇒ 已過窗（不觸發）。
TENSION_MAX_ELAPSED_SEC = 7 * 24 * 3600

#: 三態的「張力」狀態名（**直接引用上游常數** ⇒ 不會漂移）。

_THREAD: Optional[threading.Thread] = None
_THREAD_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()


# ── 旗標 ───────────────────────────────────────────────────────────────────
def feature_enabled() -> bool:
    """本機制的 opt-in 旗標（與話頭槽**同一個** ⇒ 單一開關）。"""
    try:
        return bool(_piggyback_queue.feature_enabled())
    except Exception as exc:  # noqa: BLE001 — fail-silent（關閉＝最安全）
        logger.debug(
            "[epistemic-tension-scheduler] 旗標讀取失敗（視為關閉）：%s: %s",
            type(exc).__name__,
            exc,
        )
        return False


def min_interval_seconds() -> int:
    """節流常數**沿用** ``proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS``。

    🔴 **不得**在本模組另立一套節流常數（逐 agent 計時 ⇒ 多角色時不會互相排隊）。
    """
    from src.llm.proxy import BACKGROUND_ACTION_MIN_INTERVAL_SECONDS

    return int(BACKGROUND_ACTION_MIN_INTERVAL_SECONDS)


# ── agent 清單 ─────────────────────────────────────────────────────────────
def resolve_agents(agents: Optional[Sequence[str]] = None) -> List[str]:
    """本輪要判定的 agent 清單。

    順序：① 呼叫端明確指定 ② 既有註冊表（``src.soul.motive``）③ 生產資料面
    （``<data_root>/soul/*/relationships.json`` 的目錄名）。
    """
    if agents is not None:
        return [agent for agent in agents if isinstance(agent, str) and agent]
    try:
        from src.soul.motive import get_agent_ids

        registered = sorted(get_agent_ids())
        if registered:
            return registered
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[epistemic-tension-scheduler] 註冊表讀取失敗：%s: %s", type(exc).__name__, exc
        )
    try:
        from src.paths import data_root

        soul_dir = Path(data_root()) / "soul"
        return sorted(
            child.name
            for child in soul_dir.iterdir()
            if child.is_dir() and (child / "relationships.json").is_file()
        )
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[epistemic-tension-scheduler] 生產 agent 清單讀取失敗：%s: %s",
            type(exc).__name__,
            exc,
        )
        return []


# ── 判定 ───────────────────────────────────────────────────────────────────
def unclosed_tension_ids(
    agent_id: str,
    now: int,
    last_interaction_ts: int,
    tension_store_dir: Optional[Any] = None,
) -> List[str]:
    """該 agent 當下的**未閉合張力** id 清單。

    ＝ 承載裡已開啟但未閉合的張力 ＋ **當下的關係張力**（落在張力窗內 ＋ 有羈絆
    ⇒ 這條關係張力此時尚未被閉合，它本身就是一條未閉合張力）。
    """
    ids: List[str] = [
        tension.tension_id
        for tension in _epistemic_action.load_open_tensions(agent_id, tension_store_dir)
    ]
    try:
        snapshot = _epistemic_action.read_bond_snapshot(agent_id)
        if snapshot is not None and snapshot.eligible:
            state = _classify_state(last_interaction_ts, now, snapshot)
            if state == STATE_TENSION:
                ids.append("relational:current")
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[epistemic-tension-scheduler] 張力判定失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
    return ids


def _classify_state(last_interaction_ts: int, now: int, snapshot: Any) -> str:
    """三態判定（沿用上游 ``classify_temporal_state``；**不**另立一套規則）。"""
    from src.soul.temporal_phenomenology import classify_temporal_state

    return str(classify_temporal_state(last_interaction_ts, now, snapshot.to_bond_evidence()))


def evaluate(
    now: int,
    *,
    agents: Optional[Sequence[str]] = None,
    tension_store_dir: Optional[Any] = None,
) -> List[Tuple[str, int]]:
    """本輪應觸發的 ``(agent_id, last_interaction_ts)`` 清單（**只讀**）。

    判定條件（兩者皆須成立）：

    1. **落在張力窗內**：距上次互動的 elapsed ∈ ``[CALM_MAX, TENSION_MAX)``；
    2. **有未閉合張力**：承載裡已開啟未閉合的張力，或當下的關係張力（窗內 ＋ 有
       羈絆證據 ⇒ 這條關係張力此刻尚未閉合）。
    """
    out: List[Tuple[str, int]] = []
    for agent_id in resolve_agents(agents):
        try:
            last_ts = _epistemic_action.read_last_interaction_ts(agent_id)
            if last_ts <= 0:
                continue
            elapsed = int(now) - int(last_ts)
            if not (CALM_MAX_ELAPSED_SEC <= elapsed < TENSION_MAX_ELAPSED_SEC):
                continue
            if not unclosed_tension_ids(agent_id, int(now), int(last_ts), tension_store_dir):
                continue
            out.append((agent_id, int(last_ts)))
        except Exception as exc:  # noqa: BLE001 — fail-silent（單隻失敗不影響整輪）
            logger.debug(
                "[epistemic-tension-scheduler] %s 判定失敗：%s: %s",
                agent_id,
                type(exc).__name__,
                exc,
            )
    return out


def tick(
    now: Optional[int] = None,
    *,
    agents: Optional[Sequence[str]] = None,
    tension_store_dir: Optional[Any] = None,
) -> List[str]:
    """**純函式**：回傳本輪應觸發背景行動的 agent 清單。

    - **零 LLM、零網路、零寫入**：只做讀取與判定。
    - **不觸發**任何東西（觸發由 :func:`run_scheduled_pass` 負責）⇒ 純函式。
    """
    now_ts = int(now if now is not None else time.time())
    return [
        agent_id
        for agent_id, _ in evaluate(
            now_ts, agents=agents, tension_store_dir=tension_store_dir
        )
    ]


# ── 觸發 ───────────────────────────────────────────────────────────────────
def _trigger_agent(agent_id: str, last_interaction_ts: int) -> bool:
    """經 proxy 的**共用節流入口**觸發背景行動（與訊息路徑同一份節流狀態）。"""
    from src.llm.proxy import _maybe_trigger_background_epistemic_action_scheduled

    return bool(
        _maybe_trigger_background_epistemic_action_scheduled(
            agent_id, last_interaction_ts=int(last_interaction_ts)
        )
    )


def run_scheduled_pass(
    now: Optional[int] = None,
    *,
    agents: Optional[Sequence[str]] = None,
    tension_store_dir: Optional[Any] = None,
    trigger: Optional[Callable[[str, int], bool]] = None,
) -> List[str]:
    """跑一輪排程自主觸發：tick → 逐 agent 觸發背景行動。

    - **旗標關閉 ⇒ 直接返回空清單**（不判定、不觸發 ⇒ 生產零行為變化）。
    - 回傳「本輪實際觸發了」的 agent 清單。
    """
    if not feature_enabled():
        return []
    now_ts = int(now if now is not None else time.time())
    fired: List[str] = []
    for agent_id, last_ts in evaluate(
        now_ts, agents=agents, tension_store_dir=tension_store_dir
    ):
        try:
            ok = trigger(agent_id, last_ts) if trigger else _trigger_agent(agent_id, last_ts)
        except Exception as exc:  # noqa: BLE001 — fail-silent
            logger.debug(
                "[epistemic-tension-scheduler] %s 觸發失敗：%s: %s",
                agent_id,
                type(exc).__name__,
                exc,
            )
            continue
        if ok:
            fired.append(agent_id)
    return fired


# ── 排程執行緒 ─────────────────────────────────────────────────────────────
def _run_loop(
    *,
    agents: Optional[Sequence[str]],
    tension_store_dir: Optional[Any],
    interval_seconds: int,
) -> None:
    """排程迴圈：每 ``interval_seconds`` 醒一次 → 跑一輪排程自主觸發。

    ⚠️ 節奏常數與 per-agent 節流**同源**（``proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS``）
    ⇒ 不會出現「tick 比節流窗密」而空轉。
    """
    while not _STOP_EVENT.is_set():
        try:
            run_scheduled_pass(agents=agents, tension_store_dir=tension_store_dir)
        except Exception as exc:  # noqa: BLE001 — fail-silent（continue，不中止迴圈）
            logger.warning(
                "[epistemic-tension-scheduler] tick 失敗（continue）：%s: %s",
                type(exc).__name__,
                exc,
            )
        _STOP_EVENT.wait(interval_seconds)


def start_scheduler_thread(
    *,
    agents: Optional[Sequence[str]] = None,
    tension_store_dir: Optional[Any] = None,
    interval_seconds: Optional[int] = None,
) -> bool:
    """啟動排程 tick 執行緒（**旗標關閉 ⇒ 不啟動 ⇒ 生產零行為變化**）。

    回傳 ``True`` 表示執行緒已在跑（或本次啟動成功）。
    """
    if not feature_enabled():
        return False
    global _THREAD
    try:
        interval = int(interval_seconds) if interval_seconds else min_interval_seconds()
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.warning(
            "[epistemic-tension-scheduler] 節流常數取得失敗，不啟動：%s: %s",
            type(exc).__name__,
            exc,
        )
        return False
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_run_loop,
            kwargs={
                "agents": agents,
                "tension_store_dir": tension_store_dir,
                "interval_seconds": interval,
            },
            name=THREAD_NAME,
            daemon=True,
        )
        _THREAD.start()
        return True


def stop_scheduler_thread(timeout: float = 5.0) -> None:
    """停止排程執行緒（測試用；不動任何其他行程）。"""
    global _THREAD
    _STOP_EVENT.set()
    with _THREAD_LOCK:
        thread = _THREAD
        _THREAD = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


def scheduler_thread_running() -> bool:
    """排程執行緒是否在跑（測試用）。"""
    thread = _THREAD
    return bool(thread is not None and thread.is_alive())


def ensure_scheduler_started() -> bool:
    """**掛點入口**：旗標開啟才啟動排程執行緒。

    缺省關閉 ⇒ 回傳 ``False`` 且**不啟動任何執行緒**（生產零行為變化）。
    """
    if not feature_enabled():
        return False
    return start_scheduler_thread()


__all__ = [
    "CALM_MAX_ELAPSED_SEC",
    "STATE_TENSION",
    "TENSION_MAX_ELAPSED_SEC",
    "THREAD_NAME",
    "ensure_scheduler_started",
    "evaluate",
    "feature_enabled",
    "min_interval_seconds",
    "resolve_agents",
    "run_scheduled_pass",
    "scheduler_thread_running",
    "start_scheduler_thread",
    "stop_scheduler_thread",
    "tick",
    "unclosed_tension_ids",
]
