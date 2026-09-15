"""LIFE-THREAD-M5：生活線頭引擎的排程器介接層（orchestrator）。

**唯一職責**：在既有 wake 的 slot 窗內，把 M1（`life_threads`）／M2
（`life_thread_dissolution`）／M3（`life_thread_wake_gate`）／M4
（`life_thread_origins`）串成一條管線。本模組**不是**新的引擎，只是接線。

🔴 硬性邊界（契約 `docs/LIFE-THREAD-ENGINE-CONTRACT.md`）：

- **0 新定時器**：不新增任何 timer／thread／`asyncio.sleep`／`call_later`。
  本模組只被 `src/soul/scheduler.py` 既有的 morning/night slot 觸發窗呼叫。
- **0 新事件型別**：不新增 `trigger_type`、不 `bus.publish`。
- **0 進入 Agency 觸發鏈**（§12 F1 `docs:773` 逐字：「本引擎不進入 Agency 觸發鏈、
  不新增 trigger_type」）。喚醒的唯一動作 ＝ `life_thread_origins.run_origin_round(...)`
  （§5 注入範式的實作），副作用僅 `data/soul/<agent>/life_threads.jsonl`（經 M1
  `apply_actions`）—— **0 TG、0 memory、0 SAGE、0 bus、0 InnerLifeEvent**。
- **M2 僅評估不寫入**：M2 的 `append_dissolved()` 會寫入 `dissolved_at`，而該欄位是
  §3.3 L1 的「**沉澱已完成**」哨兵（§3.2：LLM 失敗時 `dissolved_at` **不寫**，
  下一輪可重試）。M2 的**執行層尚未落地**（本票 out of scope），若在此先寫
  `dissolved_at`，等 M2 執行層日後上線時這些線頭會被 L1 永久跳過 ⇒ **沉澱永遠不會
  發生**。故本模組對 M2 **只做評估與記錄，0 M1 寫入**：`should_mutate` 只計數與記 log，
  絕不呼叫 `append_dissolved` / `append_transition`。
- **張力訊號未供給**：契約 §4.2 的 `should_wake` **只有**
  `check_points_due OR world_collision_detected` 兩項；張力是 M3 的**已宣告偏離**
  第三訊號，且 M4↔M3 的張力形狀對齊尚未做 ⇒ 本模組傳 `unresolved_tensions=None`
  （契約純淨），**張力供給為後續票**。附帶好處：「張力與線頭不得重複計數」自動成立。
- **due 集合交還 M4（單一 predicate 來源）**：WAKE 時傳 `due_threads=None`，讓 M4
  `_render_due_threads` 走它**自己**那條 `lt.list_active()` ＋ `check_after_ts <= now`
  的 due 過濾（與契約 §4.2 判定 1 同口徑，且該路徑已被 M4 既有測試覆蓋）。理由：
  **單一 predicate 來源 > 省一次整檔讀** —— 若在 orchestrator 內自行先過濾再傳入，
  等於在本層再造**第三份** due predicate（M1 契約 §4.2／M3 判定 1／本層），語意漂移
  風險大於省下的 I/O。代價：WAKE 時多 1 次 `lt.list_active()` 整檔讀
  （≤ 2 次/日/agent，可接受）。故本模組對 §4.2 的 **due 集合不再有偏離**；
  唯一的**已宣告偏離**仍是張力訊號未供給（見上）。
- **0 LLM 直接呼叫**：所有 LLM 一律經 `run_origin_round`（§8.1 路徑 A）。
- **寫入面＝0 檔案寫入**：唯一 I/O 是每輪 1 次感知檔（`perception_trace.jsonl`）唯讀；
  M1 讀取一律經 `life_threads` 的公開 API。

at-most-once 冪等：見 `run_slot_pipeline` 與 `_run_agent` 的說明。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.soul import life_thread_dissolution as lt_diss
from src.soul import life_thread_origins as lt_origins
from src.soul import life_thread_wake_gate as lt_gate
from src.soul import life_threads as lt

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 常數（具名、可測）
# ══════════════════════════════════════════════════════════════

#: 契約 §4.1：只准兩個評估點（與 M4 `SLOT_VALUES` / M3 `REFLECTION_SLOTS` 一致）。
#: **絕不接受 `daytime` / `evening`**。
MODULE_SLOT_VALUES = ("morning", "night")

#: 容量政策旋鈕（Owner 裁定 A）：`True` ⇒ 活躍池飽和即 `SLEEP /
#: ACTIVE_POOL_SATURATED`（容量防線最高優先）。
POLICY_ENFORCE_STRICT_CAPACITY = True

#: 軟性歸檔政策（Owner 裁定 B）：**不得**走軟性歸檔路徑。
POLICY_ALLOW_SOFT_ARCHIVE = False

#: M2 判「活躍過久」的天數門檻。
POLICY_MAX_ACTIVE_DURATION_DAYS = 14

#: M2 判 `check_after_ts` 過期（stale）的天數門檻。
POLICY_STALE_CHECK_THRESHOLD_DAYS = 7

#: 觀測行內文上限（避免日誌被單一長字串撐爆）。
_LOG_MAX_CHARS = 200

#: 行程內冪等鍵：`f"{agent_id}:{slot}:{date}"`（at-most-once；見 `_run_agent`）。
_LAST_PROCESSED: Dict[str, int] = {}


# ══════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════


def reset_state() -> None:
    """清空行程內冪等鍵 `_LAST_PROCESSED`。

    ⚠️ **僅供測試呼叫**。生產路徑**不得**呼叫本函式：清空會使 at-most-once 失效，
    同一 slot 觸發窗（`scheduler._slot_for_time` 的 `0 <= diff < 60`）在 30s tick 下會命中
    兩次，導致重複喚醒與重複 LLM 花費（突破「每日 2 評估點」）。
    """
    _LAST_PROCESSED.clear()


def _clip(value: Any) -> str:
    """把任意值收斂成單行、≤ `_LOG_MAX_CHARS` 的觀測字串。"""
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    return text[:_LOG_MAX_CHARS]


def _perception_records_path():
    """感知 trace 檔路徑 —— **逐字沿用** M4 的 `_perception_trace_path()`。

    直接呼叫 M4 的私有函式而非自行組路徑，保證兩者**永不漂移**
    （測試另有釘死兩者一致）。
    """
    return lt_origins._perception_trace_path()


def _read_perception_records() -> List[Any]:
    """唯讀 `data/world/perception_trace.jsonl`（**每輪只呼叫一次**、全體 agent 共用）。

    fail-silent：檔案不存在／不可讀 ⇒ 回 `[]`；壞行（非 JSON）逐行跳過；**永不 raise**。
    """
    try:
        path = _perception_records_path()
    except Exception as e:
        logger.warning(f"[LifeThreadOrchestrator] 感知檔路徑取得失敗 (fail-silent): {_clip(e)}")
        return []
    try:
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[LifeThreadOrchestrator] 感知檔讀取失敗 (fail-silent): {_clip(e)}")
        return []

    records: List[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue  # 壞行跳過（fail-silent）
    return records


# ══════════════════════════════════════════════════════════════
# 對外入口：一個 slot 窗內的一輪管線
# ══════════════════════════════════════════════════════════════


async def run_slot_pipeline(
    agent_ids: Sequence[str],
    now: datetime,
    slot: str,
    *,
    llm_caller: Optional[Callable[..., Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """在既有 wake 的 slot 窗內跑一輪 M1→M2→M3→M4 管線（**永不 raise**）。

    回傳 `{agent_id: summary}`；`summary` 依路徑可能是：

    - `{"skipped": "duplicate_slot"}` —— 同 `(agent, slot, date)` 已處理過。
    - `{"woke": False, "reason": ..., "dissolved_candidates": N}` —— M3 判 SLEEP。
    - `{"woke": True, "wake_blocked": "empty_soul_context", "reason": ...}` ——
      人格上下文為空 ⇒ 提前跳過（`build_origin_prompt` 對 falsy 回 `None` ⇒ 0 LLM）。
    - `{"woke": True, "reason": ..., "origin_type": ..., "origin_round": {...}}` —— 真喚醒。
    - `{"error": "..."}` —— 該 agent fail-closed（不影響其他 agent）。

    **at-most-once（先蓋章再執行）**：LLM 花費與喚醒副作用**不可重複**。30s tick
    （`src/soul/scheduler.py` 的 `_run_loop`：`await asyncio.sleep(30)`；**以函式＋呼叫
    原文引用，不寫行號**以免再次漂移）對上 60s slot 觸發窗
    （同檔 `_slot_for_time` 的 `0 <= diff < 60`）必然命中**兩次**；若不做 at-most-once
    就會在同一評估點重複喚醒、突破「每日 2 評估點」的契約上限。故在**執行之前**
    就寫入 `_LAST_PROCESSED[key]`。權衡：該輪若失敗（LLM 失敗／例外）**不重試**
    （fail-quiet）—— 寧可漏一次，不可重複花費；下一輪（下一個 slot 或隔日）自然補上。
    """
    # ── 閘門 1：slot 白名單（契約 §4.1）──────────────────────
    if slot not in MODULE_SLOT_VALUES:
        logger.warning(
            f"[LifeThreadOrchestrator] 非法 slot={_clip(slot)!r}"
            f"（僅 {MODULE_SLOT_VALUES}）⇒ 0 動作"
        )
        return {}

    # ── 閘門 2：agent_ids 必須是可迭代序列（永不 raise）──────
    if agent_ids is None or isinstance(agent_ids, (str, bytes)) or not isinstance(
        agent_ids, Sequence
    ):
        logger.warning(
            f"[LifeThreadOrchestrator] agent_ids 非序列（{type(agent_ids).__name__}）⇒ 0 動作"
        )
        return {}

    # 全體 agent 共用、每輪只讀一次（避免 N 次重複 I/O）。
    perceptions = _read_perception_records()

    summaries: Dict[str, Dict[str, Any]] = {}
    # 順序＝輸入順序（**不排序**，決定性由呼叫端決定）。
    for agent_id in agent_ids:
        try:
            summaries[agent_id] = await _run_agent(
                agent_id, now, slot, perceptions, llm_caller
            )
        except asyncio.CancelledError:
            # 不得吞取消：讓它往上冒（否則主循環無法收斂）。
            raise
        except Exception as e:
            # 每個 agent 各自 fail-closed，**不得中斷整批**。
            logger.warning(
                f"[LifeThreadOrchestrator] agent={_clip(agent_id)} 失敗 (fail-closed): "
                f"{type(e).__name__}: {_clip(e)}"
            )
            summaries[agent_id] = {"error": _clip(e)}
    return summaries


async def _run_agent(
    agent_id: str,
    now: datetime,
    slot: str,
    perceptions: Sequence[Any],
    llm_caller: Optional[Callable[..., Any]],
) -> Dict[str, Any]:
    """單一 agent 的一輪：M1 fold → M2（唯讀評估）→ M3 →（WAKE 才）M4。"""
    # ── at-most-once：**先蓋章再執行**（見 `run_slot_pipeline` docstring）──
    key = f"{agent_id}:{slot}:{now.date().isoformat()}"
    if key in _LAST_PROCESSED:
        logger.info(
            f"[LifeThreadOrchestrator] agent={_clip(agent_id)} slot={_clip(slot)} "
            f"key={_clip(key)} 已處理 ⇒ 跳過（at-most-once）"
        )
        return {"skipped": "duplicate_slot"}
    _LAST_PROCESSED[key] = 1

    # ── M1：整檔只 fold 一次，再派生兩份視圖 ────────────────
    states = lt.fold(agent_id)
    all_threads = [dict(v) for v in states.values()]
    active_threads = [t for t in all_threads if t.get("status") == "active"]
    cap = lt.capacity(agent_id)

    # ── M2（唯讀）：輸入必須是**全部線頭** ───────────────────
    # M2 的步驟 2（唯一觸發點 §3.1）只對**終態**線頭生效，故傳 `all_threads`
    # 而非 `active_threads`（後者會使 M2 在此管線中永遠是 no-op）。
    # 🔴 只評估與記錄：**0 M1 寫入**（理由見模組 docstring）。
    evals = lt_diss.evaluate_batch_dissolution(
        all_threads,
        now,
        max_active_duration_days=POLICY_MAX_ACTIVE_DURATION_DAYS,
        stale_check_threshold_days=POLICY_STALE_CHECK_THRESHOLD_DAYS,
        allow_soft_archive=POLICY_ALLOW_SOFT_ARCHIVE,
    )
    dissolved_candidates = sum(1 for e in evals if e.should_mutate)
    logger.info(
        f"[LifeThreadOrchestrator] agent={_clip(agent_id)} slot={_clip(slot)} "
        f"threads={len(all_threads)} active={len(active_threads)} cap={_clip(cap)} "
        f"dissolved_candidates={dissolved_candidates}（M2 唯讀，0 M1 寫入）"
    )

    # ── M3：Salience Gate（`current_time` 是 float epoch 秒）──
    decision = lt_gate.evaluate_wake_gate(
        agent_id,
        now.timestamp(),
        slot,
        active_threads,
        cap,
        recent_perceptions=list(perceptions),
        unresolved_tensions=None,  # 張力供給為後續票（契約 §4.2 純淨）
        enforce_strict_capacity=POLICY_ENFORCE_STRICT_CAPACITY,
    )

    if not decision.should_wake:
        return {
            "woke": False,
            "reason": decision.reason,
            "dissolved_candidates": dissolved_candidates,
        }

    # ── M4 前置：人格上下文（空 ⇒ 0 LLM 花費，提前跳過）────
    # 用 M4 的 `load_soul_context`（失敗回 `""`、**永不回 None**、已套上限、
    # 且避開 germ/seeded 分歧 —— 直接呼叫 `proxy.load_persona()` 預設 `seeded`，
    # 對 germ agent 會取錯人格）。
    soul_context = lt_origins.load_soul_context(agent_id)
    if not soul_context:
        logger.warning(
            f"[LifeThreadOrchestrator] agent={_clip(agent_id)} slot={_clip(slot)} "
            f"wake_blocked=empty_soul_context（0 LLM 花費）"
        )
        return {
            "woke": True,
            "wake_blocked": "empty_soul_context",
            "reason": decision.reason,
            "dissolved_candidates": dissolved_candidates,
        }

    # ── M4：唯一喚醒動作（§5 注入範式）───────────────────────
    # `dissolve_hook=None` **必須**：M2 執行層 out of scope ⇒ 天然 0 SAGE。
    # `soul_context` / `world_records` **顯式傳**：否則 `build_origin_prompt`
    # 會自己再讀一次 persona、`collect_world_seeds` 會二次讀同一檔（重複 I/O）。
    # 🔴 `due_threads=None`（F2 裁定）：due 過濾**交還 M4 自己**的 `_render_due_threads`
    # （`lt.list_active()` ＋ `check_after_ts <= now`，與契約 §4.2 判定 1 **同口徑**，
    # 且該路徑已被 M4 既有測試覆蓋）。**不得**改傳 `active_threads` ——
    # `life_thread_origins._render_due_threads`（`due_threads is not None` 分支）
    # **完全不做** due 過濾，未到期的 active 線頭會被 over-include 進 prompt。
    # 取捨（見模組 docstring）：**單一 predicate 來源 > 省一次整檔讀**；
    # 代價 ＝ WAKE 時多 1 次 `lt.list_active()`（≤2 次/日/agent）。
    result = await lt_origins.run_origin_round(
        agent_id,
        decision.origin_type,
        now=now,
        due_threads=None,
        llm_caller=llm_caller,
        dissolve_hook=None,
        build_kwargs={"soul_context": soul_context, "world_records": list(perceptions)},
    )
    return {
        "woke": True,
        "reason": decision.reason,
        "origin_type": decision.origin_type,
        "origin_round": result,
        "dissolved_candidates": dissolved_candidates,
    }
