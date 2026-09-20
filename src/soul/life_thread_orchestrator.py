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
- **M2 僅評估不寫入**（**旗標 OFF（預設）時**；catch-up 旗標 ON 時的交棒例外見本條末）：
  M2 的 `append_dissolved()` 會寫入 `dissolved_at`，而
  該欄位是 §3.3 L1 的「**沉澱已完成**」哨兵（§3.2：LLM 失敗時 `dissolved_at` **不寫**，
  下一輪可重試）。M2 的**執行層尚未落地**（本票 out of scope），若在此先寫
  `dissolved_at`，等 M2 執行層日後上線時這些線頭會被 L1 永久跳過 ⇒ **沉澱永遠不會
  發生**。故本模組**永不直接**呼叫 `append_dissolved` / `append_transition`。
  🔴 **後續票更新（LIFE-THREAD-M2-WIRING-1）**：M2 執行層**已落地並已接線**，但
  **預設關**（旗標 `LIFE_THREAD_CONSOLIDATION_ENABLED`）。**旗標 OFF（預設）時**，本模組
  對 M2 **只做評估與記錄，0 M1 寫入**：`should_mutate` 只計數與記 log。
  🔴 **後續票更新（M2-CATCHUP-1）— 兩支旗標分工**：
    - `LIFE_THREAD_CONSOLIDATION_ENABLED`（接線層）＝ **同輪轉入終態**的沉澱：
      由 M4 `apply_actions` 在 `append_transition` **成功之後**觸發（`dissolve_hook` 接縫）。
    - `LIFE_THREAD_CATCHUP_ENABLED`（本檔，見 `catchup_enabled()`）＝ **存量終態清掃**：
      對「**已經**是終態」且 `dissolved_at is None` 的線頭交棒。這類線頭**永遠**不會由
      M4 觸發（M1 終態不可再轉移）⇒ 只能由本旗標管。本檔在 M2 之後、M3 之前，對
      `evals` 中 `should_mutate is True` 的每個候選呼叫**同一個** hook 實例
      （`(agent_id, evaluation.thread_id, evaluation.target_status.value)`）。
    - 兩支**獨立開關／獨立回滾**；**任一 OFF ⇒ 該路徑不做任何額外工作**。catch-up ON 時
      **僅對 M2 候選做沉澱**，且 **at-most-once 與日預算（每 agent 3／全域 200／UTC 日）
      仍全由執行層守**；寫入（`append_dissolved` ＋ SAGE fact）全部發生在
      `life_thread_consolidation_wiring` 的背景任務內。
- **喚醒訊號二元（契約 §4.2 純淨）**：契約 §4.2 的 `should_wake` **只有**
  `check_points_due OR world_collision_detected` 兩項。本模組只供給這兩項
  （`active_threads` ＋ `recent_perceptions`）；M3 閘門亦只實作這兩項
  （FIX-A1-C9-1：第三個喚醒訊號已拔除），**無第三態**。
  本模組只實作契約 §4.2 的兩個訊號；任何第三訊號須先修契約。
- **due 集合交還 M4（單一 predicate 來源）**：WAKE 時傳 `due_threads=None`，讓 M4
  `_render_due_threads` 走它**自己**那條 `lt.list_active()` ＋ `check_after_ts <= now`
  的 due 過濾（與契約 §4.2 判定 1 同口徑，且該路徑已被 M4 既有測試覆蓋）。理由：
  **單一 predicate 來源 > 省一次整檔讀** —— 若在 orchestrator 內自行先過濾再傳入，
  等於在本層再造**第三份** due predicate（M1 契約 §4.2／M3 判定 1／本層），語意漂移
  風險大於省下的 I/O。代價：WAKE 時多 1 次 `lt.list_active()` 整檔讀
  （≤ 2 次/日/agent，可接受）。故本模組對 §4.2 的 **due 集合不再有偏離**；
  本層對 §4.2 **無任何已宣告偏離**。
- **0 LLM 直接呼叫**：所有 LLM 一律經 `run_origin_round`（§8.1 路徑 A）。
- **第三條喚醒路徑（契約 §4.4，`LIFE-THREAD-BOOTSTRAP-1`）**：M3 判 SLEEP
  **且非容量理由**、且該 agent **零 active 線頭**、`capacity > 0`、bootstrap 標記
  **未設**時，本層可**恰一次**改以 `bootstrap` 路徑喚醒（`origin_type`
  ＝ `necessity_driven`，§2.3 四值之一，**非第五個**）。M3 閘門**0 改動**；
  旗標 `LIFE_THREAD_BOOTSTRAP_ENABLED` **預設關**（缺席 ⇒ 本分支整段不執行）；
  at-most-once 由 `life_thread_bootstrap.json` 標記界定（**先蓋章再執行**）。
  判定與標記全在 `src/soul/life_thread_bootstrap.py`（純標準庫、永不 raise）。
- **寫入面＝0 檔案寫入**：唯一 I/O 是每輪 1 次感知檔（`perception_trace.jsonl`）唯讀；
  M1 讀取一律經 `life_threads` 的公開 API。（bootstrap 標記檔是本層**唯一**的寫入，
  且**僅在旗標 ON 且全條件成立**時發生。）

at-most-once 冪等：見 `run_slot_pipeline` 與 `_run_agent` 的說明。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.soul import life_thread_bootstrap as lt_boot
from src.soul import life_thread_consolidation_wiring as lt_wiring
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

#: Capacity flag still True for M3. Owner 2026-09-19 A: due/world before capacity.
#: Capacity still blocks create_thread only. Quiet+saturated still SLEEP SATURATED.
POLICY_ENFORCE_STRICT_CAPACITY = True

#: 軟性歸檔政策（Owner 裁定 B）：**不得**走軟性歸檔路徑。
POLICY_ALLOW_SOFT_ARCHIVE = False

#: M2 判「活躍過久」的天數門檻。
POLICY_MAX_ACTIVE_DURATION_DAYS = 14

#: M2 判 `check_after_ts` 過期（stale）的天數門檻。
POLICY_STALE_CHECK_THRESHOLD_DAYS = 7

#: **存量終態 catch-up** 旗標的環境變數名（**呼叫時即時讀取，不得快取**）。
#:
#: 🔴 **兩支旗標分工**（M2-CATCHUP-1）：
#:   - `LIFE_THREAD_CONSOLIDATION_ENABLED`（`life_thread_consolidation_wiring`）＝
#:     **同輪轉入終態**的沉澱：由 M4 `apply_actions` 在 `append_transition` 成功後觸發。
#:   - `LIFE_THREAD_CATCHUP_ENABLED`（本檔）＝ **存量終態清掃**：對「已經」是終態
#:     （`status ∈ {completed, abandoned}` ∧ `dissolved_at is None`）的線頭交棒。
#:     這類線頭**永遠**不會由 M4 觸發（M1 終態不可再轉移）⇒ 只能由本旗標管。
#: 兩者**獨立開關／獨立回滾**；本旗標 OFF（預設）⇒ 本檔逐位元回到 catch-up 之前的行為。
CATCHUP_ENABLED_ENV = "LIFE_THREAD_CATCHUP_ENABLED"

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


def catchup_enabled() -> bool:
    """存量終態 catch-up 旗標是否開啟（**每次呼叫都重新讀 `os.environ`**，讓測試能 monkeypatch）。

    真值語意**刻意與既有兩支旗標逐字相同**（`life_thread_bootstrap.bootstrap_enabled()`
    與 `life_thread_consolidation_wiring.consolidation_enabled()`）：
    去首尾空白、不分大小寫，落在真值集合 `{"1","true","yes","on"}` 才為 `True`；
    **缺席**／非字串／其餘值（含 `""` / `"0"` / `"off"` / `"false"`）⇒ `False`
    （fail-safe 方向 ＝ 不做額外工作、不花錢）。
    真值集合**直接沿用**接線模組既有的 `TRUTHY_VALUES` 常數，不另立一份。

    🔴 **0 新增 import**：本檔的 import 集合已被獨立審計釘住，故 `os` 取自**已匯入**的
    接線模組所繫結的同一個行程環境物件（`lt_wiring.os.environ`）——與
    `lt_wiring.consolidation_enabled()` 讀的是同一個環境、語意完全一致。
    """
    try:
        raw = lt_wiring.os.environ.get(CATCHUP_ENABLED_ENV)
    except Exception:  # pragma: no cover - defensive（fail-safe：不做額外工作）
        return False
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() in lt_wiring.TRUTHY_VALUES


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
    - `{"woke": False, "reason": ..., "dissolved_candidates": N}` —— M3 判 SLEEP
      （含 bootstrap 不成立／不該觸發時的**逐字相同**返回）。
    - `{"woke": True, "reason": ..., "origin_type": "necessity_driven",
       "bootstrap": True, "origin_round": {...}, "dissolved_candidates": N}` ——
      **契約 §4.4 第三條喚醒路徑**（冷啟動引導；旗標 `LIFE_THREAD_BOOTSTRAP_ENABLED`
      **預設關**、每 agent 一次性）。
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
    """單一 agent 的一輪：M1 fold → M2（評估；catch-up 旗標 ON 時交棒存量終態）→ M3 →（WAKE 才）M4。"""
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

    # ── M2（評估）：輸入必須是**全部線頭** ───────────────────
    # M2 的步驟 2（唯一觸發點 §3.1）只對**終態**線頭生效，故傳 `all_threads`
    # 而非 `active_threads`（後者會使 M2 在此管線中永遠是 no-op）。
    # 🔴 本層只評估與記錄；**catch-up 旗標 ON 時**才把 M2 候選交給接線層的 hook
    #    （見下方 catch-up 區塊）。理由見模組 docstring。
    # 🔴 本輪的**唯一** hook 實例：同時供下方 catch-up 與 M4 round（`dissolve_hook=`）
    #    使用 ⇒ 同一輪同一 agent 的狀態一致。旗標 OFF 時它只是個立刻 return 的閉包。
    dissolve_hook = lt_wiring.build_dissolve_hook()
    evals = lt_diss.evaluate_batch_dissolution(
        all_threads,
        now,
        max_active_duration_days=POLICY_MAX_ACTIVE_DURATION_DAYS,
        stale_check_threshold_days=POLICY_STALE_CHECK_THRESHOLD_DAYS,
        allow_soft_archive=POLICY_ALLOW_SOFT_ARCHIVE,
    )
    dissolved_candidates = sum(1 for e in evals if e.should_mutate)

    # ── 契約新條款：**存量終態** catch-up（M2 候選 ⇒ 既有 hook）──────────
    # M2 步驟 2 對 `status ∈ {completed, abandoned}` ∧ `dissolved_at is None` 的線頭回
    # `should_mutate=True`。這類線頭**不會**再由 M4 觸發 hook（M1 終態不可再轉移 ⇒
    # `apply_actions` 永遠拿不到 `transitioned`）⇒ 若不在這裡交棒，「存量終態」永遠不會
    # 被沉澱。故在 M2 之後、M3 之前，對每個候選呼叫**同一個** hook 實例。
    # 🔴 **整段只在 `LIFE_THREAD_CATCHUP_ENABLED` ON 時執行**：旗標 OFF（預設）⇒
    #    **不迭代候選、不呼叫 hook、不建任務** ⇒ 本檔逐位元回到 catch-up 之前。
    # 🔴 at-most-once 與日預算**都在執行層**（wiring `budget=None` ⇒ 行程級預設
    #    每 agent 3／全域 200／UTC 日）＋ M2 步驟 1 的 `already_dissolved` 冪等
    #    ⇒ 本層**不**自建預算或冪等，也不因配額抑制任何呼叫。
    # 🔴 hook 是**同步**且內部 `loop.create_task`（不在本 tick inline await）⇒ 呼叫便宜。
    # 🔴 fail-quiet：本模組「永不 raise」⇒ 單一候選失敗只記 warning，不影響 M3／M4。
    catchup_on = catchup_enabled()
    catchup_invoked = 0
    if catchup_on:
        for evaluation in evals:
            if evaluation.should_mutate is not True:
                continue
            try:
                dissolve_hook(
                    agent_id, evaluation.thread_id, evaluation.target_status.value
                )
                catchup_invoked += 1
            except Exception as exc:
                logger.warning(
                    f"[LifeThreadOrchestrator] agent={_clip(agent_id)} "
                    f"catch-up hook 失敗 (fail-quiet): {type(exc).__name__}"
                )
    logger.info(
        f"[LifeThreadOrchestrator] agent={_clip(agent_id)} slot={_clip(slot)} "
        f"threads={len(all_threads)} active={len(active_threads)} cap={_clip(cap)} "
        f"dissolved_candidates={dissolved_candidates} "
        f"catchup_enabled={catchup_on} catchup_invoked={catchup_invoked}"
    )

    # ── M3：Salience Gate（`current_time` 是 float epoch 秒）──
    decision = lt_gate.evaluate_wake_gate(
        agent_id,
        now.timestamp(),
        slot,
        active_threads,
        cap,
        recent_perceptions=list(perceptions),
        enforce_strict_capacity=POLICY_ENFORCE_STRICT_CAPACITY,
    )

    # ── 契約 §4.4：第三條喚醒路徑（bootstrap wake）───────────
    # 🔴 順序**逐字固定**：M3 判定之後、既有 SLEEP 返回之前。M3 閘門 0 改動
    # （呼叫參數／回傳／語意／日誌逐位元不變；本分支只在 `should_wake is False`
    #  之後接手）。**旗標缺席／OFF（預設）⇒ 整段不執行** ⇒ 生產行為與本節引入前相同。
    # `bootstrap_mode` 只在**蓋章成功**時為 `True`（at-most-once；先蓋章再執行）。
    bootstrap_mode = False
    if not decision.should_wake:
        boot_marker = None
        if lt_boot.bootstrap_enabled():
            try:
                boot_marker = lt_boot.bootstrap_marker_path(lt.life_threads_path(agent_id))
            except Exception:
                boot_marker = None  # fail-quiet：不安全 agent_id ⇒ 不 bootstrap、不 raise、不改變既有行為
        if boot_marker is not None and lt_boot.bootstrap_should_fire(
            enabled=lt_boot.bootstrap_enabled(),
            m3_should_wake=decision.should_wake,
            m3_reason=decision.reason,
            active_count=len(active_threads),
            capacity=cap,
            marker_path=boot_marker,
        ):
            soul_context = lt_origins.load_soul_context(agent_id)      # 既有 0-LLM 前置檢查
            if soul_context:
                if lt_boot.claim_bootstrap(boot_marker, now_iso=now.isoformat()):
                    logger.info(f"[LifeThreadOrchestrator] agent={_clip(agent_id)} slot={_clip(slot)} "
                                f"bootstrap wake（第三條路徑 §4.4；reason={_clip(decision.reason)}；一次為限）")
                    bootstrap_mode = True
        if not bootstrap_mode:
            return {
                "woke": False,
                "reason": decision.reason,
                "dissolved_candidates": dissolved_candidates,
            }

    # ── M4 前置：人格上下文（空 ⇒ 0 LLM 花費，提前跳過）────
    # 用 M4 的 `load_soul_context`（失敗回 `""`、**永不回 None**、已套上限、
    # 且避開 germ/seeded 分歧 —— 直接呼叫 `proxy.load_persona()` 預設 `seeded`，
    # 對 germ agent 會取錯人格）。
    # 🔴 bootstrap 模式**重用**上面已載入的值（同一輪同一 agent，且該值必為非空，
    #    否則蓋不了章）；**非** bootstrap 路徑的呼叫次數與行為**逐字不變**。
    if not bootstrap_mode:
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
    # 🔴 `dissolve_hook` **已接線（LIFE-THREAD-M2-WIRING-1）但預設關**：注入
    # `life_thread_consolidation_wiring.build_dissolve_hook()`（同步 hook；內部以
    # `loop.create_task` 跑背景沉澱，**不在本 tick inline await**）。旗標
    # `LIFE_THREAD_CONSOLIDATION_ENABLED` **缺席或非真值 ⇒ hook 立刻 return**
    # ⇒ 0 讀檔／0 任務／0 LLM／0 SAGE，生產行為與接線前逐位元相同。
    # （舊註解「M2 執行層 out of scope ⇒ 天然 0 SAGE」已失真，見 ENGINEERING_STATE
    #  的 LIFE-THREAD-M2-WIRING-1 列。）
    # `soul_context` / `world_records` **顯式傳**：否則 `build_origin_prompt`
    # 會自己再讀一次 persona、`collect_world_seeds` 會二次讀同一檔（重複 I/O）。
    # 🔴 `due_threads=None`（F2 裁定）：due 過濾**交還 M4 自己**的 `_render_due_threads`
    # （`lt.list_active()` ＋ `check_after_ts <= now`，與契約 §4.2 判定 1 **同口徑**，
    # 且該路徑已被 M4 既有測試覆蓋）。**不得**改傳 `active_threads` ——
    # `life_thread_origins._render_due_threads`（`due_threads is not None` 分支）
    # **完全不做** due 過濾，未到期的 active 線頭會被 over-include 進 prompt。
    # 取捨（見模組 docstring）：**單一 predicate 來源 > 省一次整檔讀**；
    # 代價 ＝ WAKE 時多 1 次 `lt.list_active()`（≤2 次/日/agent）。
    # 契約 §4.4「`origin_type` 的選定」：bootstrap 模式**改以**
    # `lt_boot.BOOTSTRAP_ORIGIN_TYPE`（`"necessity_driven"`，§2.3 四值之一，
    # **不發明第五個**）呼叫；非 bootstrap 模式**逐字沿用** `decision.origin_type`
    # （M3 的判定值，行為不變）。§5.2.2 的模板與語意仍全部屬 M4。
    wake_origin_type = (
        lt_boot.BOOTSTRAP_ORIGIN_TYPE if bootstrap_mode else decision.origin_type
    )
    result = await lt_origins.run_origin_round(
        agent_id,
        wake_origin_type,
        now=now,
        due_threads=None,
        llm_caller=llm_caller,
        dissolve_hook=dissolve_hook,
        build_kwargs={"soul_context": soul_context, "world_records": list(perceptions)},
    )
    if bootstrap_mode:
        logger.info(
            f"[LifeThreadOrchestrator] agent={_clip(agent_id)} slot={_clip(slot)} "
            f"bootstrap wake 完成（origin_type={_clip(wake_origin_type)}）"
        )
    summary: Dict[str, Any] = {
        "woke": True,
        "reason": decision.reason,
        "origin_type": wake_origin_type,
        "origin_round": result,
        "dissolved_candidates": dissolved_candidates,
    }
    if bootstrap_mode:
        # 契約 §4.4：bootstrap 模式成功喚醒時可觀測（非 bootstrap 路徑鍵集合不變）。
        summary["bootstrap"] = True
    return summary
