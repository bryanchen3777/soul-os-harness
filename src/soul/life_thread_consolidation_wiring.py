# src/soul/life_thread_consolidation_wiring.py
# Soul OS — LIFE-THREAD-M2-WIRING-1：沉澱**執行層**與生產的接線（唯一允許 import 執行層的生產檔）
"""LIFE-THREAD-M2-WIRING-1 — 把已驗收的沉澱執行層（`life_thread_dissolution_exec`）
接上生產，並提供 `reasoning_effort` 透傳的 adapter。

**唯一職責**：提供 M4（`life_thread_origins`）的 `dissolve_hook` **同步接縫**的實作，
把「線頭進入終態」這件事轉成一個**背景非同步任務**去跑執行層的沉澱管線。

🔴 紅線與不變量
──────────────
- **預設關**：環境變數 `LIFE_THREAD_CONSOLIDATION_ENABLED`（見 `CONSOLIDATION_ENABLED_ENV`）
  **缺席或非真值 ⇒ OFF**。OFF 時 hook **立刻 return**：0 讀檔、0 建任務、0 LLM、0 SAGE
  ⇒ **生產行為與接線前逐位元相同**。
- **不得 inline await**：校準實測單次沉澱延遲 **26.34 s**（`reasoning_effort="none"` 後
  **1.18 s**，見 `docs/LIFE-THREAD-M2-EXEC-COST-CALIBRATION.md`），而 hook 由
  `apply_origin_actions` **同步**呼叫、位於排程器 30s tick 的同一條呼叫鏈上
  （`scheduler._fire_life_thread_slot` → `run_slot_pipeline` → `_run_agent` →
  `run_origin_round`）⇒ **必須**用 `loop.create_task` 丟到背景，**絕不可** await。
- **hook 本身永不拋例外**：整支函式包在 try/except（含 `asyncio.get_running_loop()`
  取不到 loop 的情形）⇒ 只寫 warning。M4 的接縫本身也是 fail-silent，本模組是**第二層**。
- **本檔是唯一允許 import 執行層的生產檔**：0 接線不變量已由
  `tests/soul/test_life_thread_dissolution_exec.py`（T53/T54）逐檔指名釘死。
- **不修改執行層**（`life_thread_dissolution_exec.py` 已驗收凍結，0 行改動）。

模組契約（供測試注入的 seams）
────────────────────────────
所有外部依賴都以**模組級函式**為接縫，測試可 `monkeypatch.setattr` 直接替換
（0 真實 LLM、0 真實 SAGE、0 網路）：

======================  ==========================================================
`_read_thread_state`    讀 M1 fold 後的線頭狀態（`life_threads.get_state`）
`_evaluate`             M2 決策（`evaluate_thread_dissolution`）
`_llm_call`             生產 LLM 通道（`LLMProxy.generate_text` ＋ 零額外重試）
`_write_fact`           SAGE 落地（`MemoryWriter.add_fact(Fact(**fact))` -> fact_id）
`_append_dissolved`     M1 回填（`life_threads.append_dissolved(..., sage_fact_id=...)`）
`_consolidate`          執行層主函式（`consolidate_terminal_thread`）
======================  ==========================================================

D3（`sage_fact_id` 回填）
────────────────────────
執行層 `ConsolidationResult` **不暴露** writer 回傳的 fact_id（其 `fact` 欄位是**入參**
fact 的 dict，不是 fact_id）⇒ 本模組以 **fact_writer wrapper** 捕捉真實 writer 的回傳值，
成功（非空 `str`）後才 `append_dissolved(..., sage_fact_id=<id>)`。
`""` ＝ 寫入失敗 ⇒ 交由執行層既有語意判 `write_failed`，**不回填**（因未落地）。

成本與預算
──────────
- 執行層的 `budget=None` ⇒ 使用其**行程級預設預算**（每 agent 3／全域 200／UTC 日）。
- adapter 走 `generate_text(..., max_retries=0)` ⇒ **恰好 1 次 HTTP 嘗試**
  （本層 1 次 × backend `range(1)`），避開最壞 8 次的重試放大。
- `reasoning_effort="none"`（`CONSOLIDATION_REASONING_EFFORT`）：校準實測
  completion 1790→75（−95.8%）、延遲 26.34s→1.18s。

與票面描述的差異
────────────────
- **本檔不 import `src.llm.proxy`**：`reasoning_effort`／`max_retries` 是既有
  `LLMProxy.generate_text` 的方法參數（本票 D1 打通），呼叫端不需要類別物件；
  proxy **實例**沿用 `life_thread_origins._find_llm_proxy()`（＝ `scripts/run_server.py`
  `:1069` 已注入的那一顆，model／endpoint／金鑰全部沿用生產設定，**0 硬編碼**）。
- **不新增 `set_llm_proxy` 注入點**：`scripts/run_server.py` 為禁改檔；既有 M4 注入點
  已提供同一顆 `LLMProxy`，重用即可（0 新 provider／0 新通道／0 新金鑰）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from src.soul import life_thread_dissolution as lt_diss
from src.soul import life_thread_dissolution_exec as lt_exec
from src.soul import life_thread_origins as lt_origins
from src.soul import life_threads as lt

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# §1 常數（全部具名）
# ──────────────────────────────────────────────────────────────

#: 旗標環境變數名（**缺席或非真值 ⇒ OFF**；**呼叫時即時讀取**）。
CONSOLIDATION_ENABLED_ENV = "LIFE_THREAD_CONSOLIDATION_ENABLED"

#: 真值集合（不分大小寫）。其餘（含 `"0"`／`"false"`／`""`／未設）一律 OFF。
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

#: 沉澱呼叫的推理預算：`"none"` 關閉 extended thinking。
#:
#: 依據 `docs/LIFE-THREAD-M2-EXEC-COST-CALIBRATION.md` §A2（2026-09-17 實測，
#: 模型 `deepseek-v4.1-flash`）：`reasoning_effort="none"` ⇒ completion
#: **1790 → 75**（−95.8%）、延遲 **26.34 s → 1.18 s**，輸出格式仍合法。
CONSOLIDATION_REASONING_EFFORT = "none"

#: 沉澱呼叫的採樣溫度。**逐字沿用校準 receipt 的實測值**
#: （`docs/LIFE-THREAD-M2-EXEC-COST-CALIBRATION.md` §A2.3.1：`temperature: 0.85`），
#: 該組合已驗證能產出執行層可解析的 JSON。
CONSOLIDATION_TEMPERATURE = 0.85

#: 背景任務的行程級集合（強引用，避免 task 被 GC）；done-callback 負責移除。
_BACKGROUND_TASKS: Set[asyncio.Task] = set()

#: M2 政策門檻 —— **逐字對齊** orchestrator 的 Owner 裁定值
#: （`life_thread_orchestrator.POLICY_MAX_ACTIVE_DURATION_DAYS` /
#: `POLICY_STALE_CHECK_THRESHOLD_DAYS`）。本模組**不得** import orchestrator
#: （orchestrator 反向 import 本模組 ⇒ 循環），故以常數宣告 ＋ 由測試釘死等值
#: （`test_wiring_policy_constants_match_orchestrator`）。
#:
#: 註：這兩個門檻只影響 M2 步驟 4（**active** 線頭的諮詢軸）；本接縫的線頭恆為
#: 終態（`completed` / `abandoned`）⇒ 走 M2 步驟 2，門檻值不影響裁決結果。
POLICY_MAX_ACTIVE_DURATION_DAYS = 14

#: 見上（「活躍過久」／「stale」兩軸的天數門檻）。
POLICY_STALE_CHECK_THRESHOLD_DAYS = 7

#: 日誌前綴。
_LOG_PREFIX = "[LifeThreadConsolidationWiring] "


__all__ = [
    "CONSOLIDATION_ENABLED_ENV",
    "TRUTHY_VALUES",
    "CONSOLIDATION_REASONING_EFFORT",
    "CONSOLIDATION_TEMPERATURE",
    "POLICY_MAX_ACTIVE_DURATION_DAYS",
    "POLICY_STALE_CHECK_THRESHOLD_DAYS",
    "consolidation_enabled",
    "build_dissolve_hook",
    "pending_task_count",
]


# ──────────────────────────────────────────────────────────────
# §2 旗標（呼叫時即時讀取）
# ──────────────────────────────────────────────────────────────


def consolidation_enabled() -> bool:
    """旗標是否開啟（**每次呼叫都重新讀 `os.environ`**，讓測試能 monkeypatch）。

    真值集合 `TRUTHY_VALUES`（不分大小寫）；**缺席或非真值 ⇒ `False`**。
    讀取失敗（理論上不可能）⇒ `False`（fail-safe 方向 ＝ 不花錢）。
    """
    try:
        raw = os.environ.get(CONSOLIDATION_ENABLED_ENV)
    except Exception:  # pragma: no cover - defensive
        return False
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() in TRUTHY_VALUES


def pending_task_count() -> int:
    """目前仍在跑的背景沉澱任務數（**唯讀觀測面**，測試用來證明 0 殘留）。"""
    return len(_BACKGROUND_TASKS)


# ──────────────────────────────────────────────────────────────
# §3 生產接縫（全部可 monkeypatch；每個都是最小 wrapper）
# ──────────────────────────────────────────────────────────────


def _read_thread_state(agent_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
    """M1 讀取：fold 後該線頭的狀態（未知線頭 ⇒ `None`）。"""
    return lt.get_state(agent_id, thread_id)


def _evaluate(thread: Dict[str, Any], now: datetime, agent_id: str) -> Any:
    """M2 決策：單筆溶解裁決（**純決策、不寫入**）。

    參數逐字沿用 orchestrator 的政策常數（`allow_soft_archive=False` ⇒ 不走軟性歸檔），
    `terminal_reason=None` ⇒ 終態原因由 M2 自行判定（`completed` ⇒ `GOAL_ACHIEVED`、
    `abandoned` ⇒ `MANUAL_CLOSE`）。
    """
    return lt_diss.evaluate_thread_dissolution(
        thread,
        now,
        max_active_duration_days=POLICY_MAX_ACTIVE_DURATION_DAYS,
        stale_check_threshold_days=POLICY_STALE_CHECK_THRESHOLD_DAYS,
        allow_soft_archive=False,
        agent_id=agent_id,
        terminal_reason=None,
    )


def _resolve_llm_proxy() -> Any:
    """生產 LLM proxy（**沿用** M4 已注入的那一顆；未注入 ⇒ `None`）。

    `scripts/run_server.py:1069` 在 lifespan 內 `_lt_origins.set_llm_proxy(llm)`
    ⇒ 本模組直接重用同一物件：model／endpoint／api_key 全部沿用生產設定，
    **0 硬編碼、0 新 provider、0 新通道**。
    """
    return lt_origins._find_llm_proxy()


def _make_llm_call(agent_id: str) -> Callable[..., Any]:
    """建立執行層要的 `llm_call(prompt, *, max_tokens) -> str` adapter。

    - 走**生產既有** `LLMProxy.generate_text`（0 新通道）。
    - `reasoning_effort="none"` ＋ **`max_retries=0`（恰好 1 次 HTTP 嘗試）**。
    - proxy 缺席 ⇒ `RuntimeError`（呼叫端在背景任務內，由 done-callback 記 warning）。
    - `generate_text` 失敗回 `None` / 空字串 ⇒ **raise**（讓執行層記 `llm_failed`
      而不是誤記 `parse_failed`；兩者皆為 fail-open 降級，下一輪可重試）。
    """

    async def _llm_call(prompt: str, *, max_tokens: int) -> str:
        proxy = _resolve_llm_proxy()
        if proxy is None:
            raise RuntimeError("llm_proxy_not_injected")
        text = await proxy.generate_text(
            messages=[{"role": "user", "content": prompt}],
            agent_id=agent_id,
            max_tokens=max_tokens,
            temperature=CONSOLIDATION_TEMPERATURE,
            reasoning_effort=CONSOLIDATION_REASONING_EFFORT,
            max_retries=0,  # 恰好 1 次 HTTP 嘗試（無重試放大）
        )
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("consolidation_llm_returned_no_text")
        return text

    return _llm_call


def _write_fact(fact: Dict[str, Any], agent_id: str) -> str:
    """SAGE 落地：`MemoryWriter.add_fact(Fact(**fact))` ⇒ 非空 fact_id／`""`（失敗）。

    **lazy import**（`src.memory.sage.*`）＋ **per-agent `MemoryWriter` 快取**：
    - 路徑逐字沿用 `life_thread_origins._goal_db_path`（＝
      `data_root()/memory/<agent>/graph.sqlite`，與 `decision.py`／`motive_provider.py`
      同一路徑模式）⇒ 與該 agent 既有的 SAGE 記憶同庫，**0 新 store**。
    - 建構失敗 ⇒ raise（由執行層的寫入隔離轉 `write_failed`）。
    """
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.models import Fact
    from src.memory.sage.writer import MemoryWriter

    writer = _get_writer(GraphStore, MemoryWriter, agent_id)
    return writer.add_fact(Fact(**dict(fact)))


#: per-agent `MemoryWriter` 快取（行程級；避免每次沉澱重建 store／連線）。
_WRITERS: Dict[str, Any] = {}


def _get_writer(graph_store_cls: Any, writer_cls: Any, agent_id: str) -> Any:
    """per-agent `MemoryWriter`（快取；與該 agent 的 SAGE graph 同庫）。"""
    writer = _WRITERS.get(agent_id)
    if writer is None:
        db_path = lt_origins._goal_db_path(agent_id)
        writer = writer_cls(
            graph_store_cls(db_path=db_path),
            default_session_id="life_thread_consolidation",
            agent_id=agent_id,
        )
        _WRITERS[agent_id] = writer
    return writer


def _reset_writers() -> None:
    """**測試專用**：丟棄 writer 快取（生產路徑不呼叫）。"""
    _WRITERS.clear()


def _append_dissolved(agent_id: str, thread_id: str, fact_id: str) -> bool:
    """M1 回填：把 `sage_fact_id` 寫進 `dissolved_at` 事件（**只在成功後**）。"""
    return bool(lt.append_dissolved(agent_id, thread_id, sage_fact_id=fact_id))


def _now() -> datetime:
    """溶解時刻（UTC aware）。"""
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────
# §4 背景任務：一次沉澱（async；**只在背景任務內執行**）
# ──────────────────────────────────────────────────────────────


async def _consolidate_once(agent_id: str, thread_id: str) -> Any:
    """讀線頭 → M2 裁決 → 執行層沉澱（含 `sage_fact_id` 回填）。

    執行層的 `fact_writer` ＝ **wrapper**：內部呼叫真實 `_write_fact`，
    捕捉其回傳的 fact_id，成功（非空）後回填 M1（D3）。
    `""` ＝ 寫入失敗 ⇒ **不回填**（交執行層判 `write_failed`）。
    """
    thread = _read_thread_state(agent_id, thread_id)
    if not isinstance(thread, dict) or not thread:
        logger.warning(
            f"{_LOG_PREFIX}skip thread_id={thread_id!r} reason=unknown_thread"
        )
        return None

    evaluation = _evaluate(thread, _now(), agent_id)

    def fact_writer(fact: Dict[str, Any]) -> str:
        fact_id = _write_fact(fact, agent_id)
        if isinstance(fact_id, str) and fact_id.strip():
            try:
                _append_dissolved(agent_id, thread_id, fact_id)
            except Exception as exc:
                # 回填失敗**不得**影響已落地的 fact（fail-silent，只記 warning）。
                logger.warning(
                    f"{_LOG_PREFIX}sage_fact_id 回填失敗 thread_id={thread_id!r} "
                    f"type={type(exc).__name__}"
                )
        return fact_id

    result = await lt_exec.consolidate_terminal_thread(
        agent_id=agent_id,
        thread=thread,
        evaluation=evaluation,
        llm_call=_make_llm_call(agent_id),
        fact_writer=fact_writer,
        budget=None,  # 執行層行程級預設預算（每 agent 3／全域 200）
        now=_now(),
    )
    logger.info(
        f"{_LOG_PREFIX}consolidate done thread_id={thread_id!r} "
        f"status={_clip(result.status)} llm_calls={result.llm_calls}"
    )
    return result


def _clip(value: Any) -> str:
    """單行、≤120 字的觀測字串（日誌隱私：永不記 prompt／敘述原文）。"""
    text = value if isinstance(value, str) else str(value)
    return text.replace("\r", " ").replace("\n", " ")[:120]


def _on_task_done(task: "asyncio.Task") -> None:
    """done-callback：**必須**讀 `task.exception()`（否則會被靜默吞掉）＋ 移除強引用。

    **永不拋例外**：M4 的接縫與本 callback 都在排程器呼叫鏈上。
    """
    _BACKGROUND_TASKS.discard(task)
    try:
        if task.cancelled():
            logger.warning(f"{_LOG_PREFIX}consolidation task cancelled")
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                f"{_LOG_PREFIX}consolidation task failed (fail-silent): "
                f"{type(exc).__name__}: {_clip(exc)}"
            )
    except Exception as cb_exc:  # pragma: no cover - defensive
        logger.warning(f"{_LOG_PREFIX}done-callback 失敗: {type(cb_exc).__name__}")


# ──────────────────────────────────────────────────────────────
# §5 對外：同步 hook（M4 `dissolve_hook` 的唯一實作）
# ──────────────────────────────────────────────────────────────


def build_dissolve_hook() -> Callable[[str, str, str], None]:
    """回傳 M4 `dissolve_hook` 的同步實作：`hook(agent_id, thread_id, status)`。

    行為（逐條，順序即成本順序）
    ──────────────────────────
    1. **旗標 OFF ⇒ 立刻 return**（0 讀檔／0 建任務／0 LLM／0 SAGE）。
    2. 取 running loop（例外 ⇒ warning ＋ return，**不拋**）。
    3. `loop.create_task(...)` 建**背景任務**（**不得 inline await**；延遲可達數秒）
       ＋ 放進模組級 set ＋ `add_done_callback(_on_task_done)`。
    4. **整支函式包 try/except** ⇒ 本 hook **絕不拋例外**（M4 接縫的第二層防線）。

    `status` 只用於日誌：真正的終態判據仍由 M2 重新裁決（**不信任 hook 入參**，
    避免把非終態誤送進付費路徑）。
    """

    def hook(agent_id: str, thread_id: str, status: str) -> None:
        try:
            # 1. 旗標（**呼叫時**即時讀取 ⇒ 測試可 monkeypatch）
            if not consolidation_enabled():
                return
            # 2. running loop（sync hook 位於 async 呼叫鏈內 ⇒ 正常必得）
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError as exc:
                logger.warning(
                    f"{_LOG_PREFIX}無 running loop（{type(exc).__name__}）⇒ 不沉澱 "
                    f"thread_id={_clip(thread_id)}"
                )
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    f"{_LOG_PREFIX}取得 loop 失敗（{type(exc).__name__}）⇒ 不沉澱"
                )
                return
            # 3. 背景任務（**不 await**）
            task = loop.create_task(_consolidate_once(agent_id, thread_id))
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_on_task_done)
            logger.info(
                f"{_LOG_PREFIX}scheduled agent={_clip(agent_id)} "
                f"thread_id={_clip(thread_id)} status={_clip(status)}"
            )
        except Exception as exc:
            # 4. hook 本身永不拋（M4 接縫的第二層）
            logger.warning(
                f"{_LOG_PREFIX}dissolve_hook 失敗 (fail-silent): {type(exc).__name__}"
            )

    return hook
