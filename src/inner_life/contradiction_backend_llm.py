"""src/inner_life/contradiction_backend_llm.py — 真實 LLM 矛盾判定 backend（注入式）。

工單：FEAT-G3-PRODUCTION-BACKEND-WIRING（2026-10-28；G-3 生產 Backend 接線與排程掛載）

這個模組回答什麼問題
--------------------
G-3 已經把「矛盾預濾器（＝挑候選）＋語意判定（＝裁決）」做成離線可驗證的兩層，
但 ``elevate_matured_patterns(contradiction_backend=...)`` 的 ``contradiction_backend``
**缺省 None** ⇒ 生產槽**從不**跑矛盾判定 ⇒ ``superseded`` 節點永遠不會誕生。

本模組提供那個「**真實**」backend：把判定 prompt 送進**呼叫端注入**的既有
``LLMProxy``，取回**二元**判定（``CONTRADICT`` / ``COMPATIBLE``）。

防禦合約（Owner 2026-10-28 核准；逐條可被守衛 G1～G6 驗證）
----------------------------------------------------------
1. **≤1 次／agent／日**：以**跨實例共享**的預算計數器（``{(agent_id, date): n}``）
   計數；超額 ⇒ **不呼叫** LLM、回 ``COMPATIBLE``（fail-closed）。
2. **單次 ≤300 tokens**：以 ``max_tokens=300`` **在呼叫點**傳入（不寫死在別處）。
3. **retries = 0**：**顯式**傳 ``max_retries=0``（repo 既有預設是 3 ⇒ 不得依賴預設）。
4. **二元輸出**：``CONTRADICT`` / ``COMPATIBLE``；無法解析 ⇒ ``COMPATIBLE``。
5. **fail-closed**：例外／逾時／格式不符 ⇒ 一律 ``COMPATIBLE``
   （不動作、不寫引擎、不擋主路徑）。
6. **延遲 import**：模組層**不** import 任何具網路能力的模組（守衛 G6 以 AST 釘死）
   ⇒ 連線只可能發生在**呼叫端注入**的既有 proxy 內部。

本模組**刻意不做**的事
----------------------
- **不建立連線**：不 import ``src.llm``／``httpx``／``requests``；proxy 由呼叫端注入
  （與 ``elevate_matured_patterns(llm=...)`` 的既有注入風格一致）。
- **不寫引擎**：本模組只回傳判定字串；寫累積器／``supersede()`` 是
  ``ContradictionPipeline`` 的事（呼叫端）。
- **不改判準**：矛盾預濾（哪一對命題互斥）**不在**本模組；本模組只負責
  「把已挑好的候選送去問一次 LLM，並誠實回報二元答案」。

🔺 同步／非同步的落差（為什麼本模組有一座小橋接器）
---------------------------------------------------
``LLMProxy.generate_text`` 是 **async**，但 ``contradiction_backend`` 的契約是
**同步** ``Callable[[str], str]``（G-3 契約）⇒ 需要一座橋。
生產的呼叫點（``_elevate_check``）跑在 **event loop 執行緒**內 ⇒
``asyncio.run()`` 會直接拋 ``RuntimeError`` ⇒ 橋接器在「已有 running loop」時
**另起執行緒**跑自己的 loop（並帶逾時）⇒ 兩種執行環境都安全。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("soul_os.inner_life.contradiction_backend")

#: 二元判定的兩個（也是僅有的）可能值。
VERDICT_CONTRADICT = "CONTRADICT"
VERDICT_COMPATIBLE = "COMPATIBLE"

#: 每個 agent 每日至多幾次真實 LLM 呼叫（Owner 2026-10-28 核准的預算硬上限）。
MAX_CALLS_PER_AGENT_PER_DAY = 1

#: 單次呼叫的輸出 token 上限（**在呼叫點**以 ``max_tokens`` 傳入）。
MAX_TOKENS = 300

#: 重試次數（**顯式**傳 ``max_retries=0``；repo 既有預設是 3 ⇒ 不得依賴預設）。
MAX_RETRIES = 0

#: 單次呼叫的逾時秒數（逾時 ⇒ fail-closed）。
DEFAULT_TIMEOUT_SECONDS = 20.0

#: 執行緒橋接的寬限期（逾時後再多等這麼久才放棄等結果）。
_THREAD_JOIN_GRACE_SECONDS = 5.0

# 預算計數器的欄位名（**兩層介面並存**，見 :func:`new_budget`）。
_BUDGET_BY_AGENT_DAY = "by_agent_day"
_BUDGET_AGENTS = "agents"
_BUDGET_DATE = "date"

#: 判定 prompt 的 system 段（**只**要求單一 token 回覆；不含模型／供應商／URL／key）。
_JUDGE_SYSTEM_PROMPT = (
    "你是一個嚴格的二元判定器。只回覆 CONTRADICT 或 COMPATIBLE 其中一個 token，"
    "不得輸出任何其他文字、標點、說明或引號。"
)


def _utc_today() -> str:
    """今天的 UTC 日期字串（``YYYY-MM-DD``）——「每日」的邊界一律以 **UTC** 為準。

    🔺 與 ``contradiction_prefilter`` 的跨日口徑一致（UTC）⇒ 兩層不會各算各的。
    """
    return datetime.now(timezone.utc).date().isoformat()


def new_budget() -> Dict[str, Any]:
    """建立一份**行程級共享**的預算計數器（形狀見下）。

    兩層介面並存（**同一個 dict**，因為 G-3 的判定器與本模組共用它）：

    - ``by_agent_day``：``{(agent_id, "YYYY-MM-DD"): n}`` —— 工單 D1.2 的形如；
      **本模組（backend 層）的權威依據**（key 裡帶日期 ⇒ 天然跨日分界，無需重置）。
    - ``agents``：``{agent_id: {"attempted": n}}`` —— G-3 ``ContradictionJudge``
      的介面（它只認這個形狀）；**當日**有效 ⇒ 跨日由 :func:`ensure_current_day` 重置。
    - ``attempted``／``succeeded``／``failed``／``budget_exhausted``：觀測用總計。

    🔺 「當日」＝ UTC 日；**跨實例共用不等於跨日共用**（G-3 契約 §三）。
    """
    return {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "budget_exhausted": 0,
        _BUDGET_AGENTS: {},
        _BUDGET_BY_AGENT_DAY: {},
        _BUDGET_DATE: _utc_today(),
    }


def ensure_current_day(budget: Dict[str, Any]) -> bool:
    """把計數器滾動到「今天」（UTC）；跨日 ⇒ 重置**當日**的判定器介面計數。

    🔺 為什麼需要它：``by_agent_day`` 因為 key 帶日期而天然跨日分界，但 G-3 判定器
    只看 ``agents[agent_id]["attempted"]`` ⇒ 若不滾動，**第二天起判定器會永遠
    以為預算已用盡**（功能死鎖）。故**呼叫端必須在每次判定前**呼叫本函式。

    Args:
        budget: 計數器（就地修改）。

    Returns:
        True 若本次發生跨日滾動。
    """
    today = _utc_today()
    if budget.get(_BUDGET_DATE) == today:
        return False
    budget[_BUDGET_DATE] = today
    budget[_BUDGET_AGENTS] = {}
    for _key in ("attempted", "succeeded", "failed", "budget_exhausted"):
        budget[_key] = 0
    logger.info("[contradiction] ↻ budget day rollover date=%s", today)
    return True


def budget_by_agent_day(budget: Dict[str, Any]) -> Dict[Tuple[str, str], int]:
    """計數器的 ``{(agent_id, date): n}`` 視圖（工單 D1.2 的形如；唯讀）。

    合併兩層介面：``by_agent_day`` 的歷史 ＋ 當日 ``agents`` 的即時計數。
    """
    view: Dict[Tuple[str, str], int] = {}
    raw = budget.get(_BUDGET_BY_AGENT_DAY) or {}
    for key, value in raw.items():
        view[key] = int(value)
    current = budget.get(_BUDGET_DATE)
    agents = budget.get(_BUDGET_AGENTS) or {}
    for agent_id, entry in agents.items():
        key = (str(agent_id), str(current))
        view[key] = max(view.get(key, 0), int((entry or {}).get("attempted", 0)))
    return view


def parse_verdict_strict(response: Any) -> str:
    """把回應解析成二元判定（**嚴格**：只做字面比對，不做任何語意推測）。

    規則（與 G-3 ``contradiction_judge_prototype.parse_verdict`` **逐條同義**）：

    1. 非字串 ⇒ ``COMPATIBLE``；
    2. 去首尾空白後（大小寫不敏感）完全等於 ``CONTRADICT`` ⇒ ``CONTRADICT``；
    3. 同樣完全等於 ``COMPATIBLE`` ⇒ ``COMPATIBLE``；
    4. **其餘一切** ⇒ ``COMPATIBLE``（fail-closed：寧可漏記矛盾，不可誤記矛盾）。

    🔺 守衛會逐筆比對本函式與 G-3 解析器的輸出 ⇒ 兩份語意**不得漂移**。
    """
    if not isinstance(response, str):
        return VERDICT_COMPATIBLE
    token = response.strip().upper()
    if token == VERDICT_CONTRADICT:
        return VERDICT_CONTRADICT
    return VERDICT_COMPATIBLE


async def _with_timeout(coro: Any, timeout: float) -> Any:
    """在 async 端加上逾時（逾時 ⇒ ``asyncio.TimeoutError``）。"""
    return await asyncio.wait_for(coro, timeout)


def _run_coroutine_blocking(coro: Any, timeout: float) -> Any:
    """在**同步**呼叫端把一個 coroutine 跑完（逾時 ⇒ ``TimeoutError``）。

    🔺 生產的呼叫點跑在 event loop 執行緒內 ⇒ ``asyncio.run()`` 會拋
    ``RuntimeError`` ⇒ 此時**另起執行緒**跑自己的 loop（逾時仍由
    ``asyncio.wait_for`` 負責）。
    """
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        return asyncio.run(_with_timeout(coro, timeout))

    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(_with_timeout(coro, timeout))
        except BaseException as exc:  # noqa: BLE001 — 錯誤原樣交回同步端
            box["error"] = exc

    thread = threading.Thread(
        target=_worker, name="soul-os-contradiction-llm", daemon=True
    )
    thread.start()
    thread.join(timeout + _THREAD_JOIN_GRACE_SECONDS)
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise TimeoutError(f"contradiction LLM call exceeded {timeout}s")
    return box["value"]


class VerdictBackend:
    """真實 LLM 矛盾判定 backend（``Callable[[str], str]``；注入式；fail-closed）。

    典型用法::

        budget = new_budget()                       # 行程級共享（跨實例）
        backend = make_verdict_backend(proxy=llm, budget=budget)
        verdict = backend(judge_prompt)             # ⇒ "CONTRADICT" / "COMPATIBLE"

    🔺 **預算的歸屬**：``≤1 次／agent／日`` 需要知道「這次是替哪個 agent 問的」。
    呼叫端（``elevation_adapter`` 的 hook）知道 ⇒ 它會呼叫 :meth:`bind_agent`
    取得**綁定 agent** 的 backend（共用同一份計數器）⇒ backend 層自己就能
    拒絕超額的呼叫（回 ``COMPATIBLE``、**不**打 LLM）。
    未綁定（agent 未知）時，backend **不做**自己的預算判定 ⇒ 由**上游**的
    G-3 判定器預算（``agents[agent_id]``，同一份計數器）把關 ⇒ 兩層都不會
    讓「每日至多 1 次」失守。
    """

    def __init__(
        self,
        proxy: Any,
        *,
        budget: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
        max_tokens: int = MAX_TOKENS,
        max_retries: int = MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        #: 注入的 proxy（**唯一的外部互動點**；本模組不建立連線）。
        self._proxy = proxy
        #: 預算計數器（由呼叫端注入 ⇒ 跨實例共用）。
        self._budget = budget if budget is not None else new_budget()
        #: 本 backend 綁定的 agent（None ⇒ 未知；由上游判定器把關）。
        self._agent_id = str(agent_id) if agent_id is not None else None
        #: 單次呼叫的 token 上限（**在呼叫點**傳入）。
        self.max_tokens = int(max_tokens)
        #: 重試次數（**顯式** 0 ⇒ 恰好 1 次嘗試）。
        self.max_retries = int(max_retries)
        #: 單次呼叫的逾時秒數。
        self.timeout = float(timeout)

    # ── 對外契約：``Callable[[str], str]`` ────────────────────────────
    def __call__(self, prompt: str) -> str:
        """問一次 LLM，回**二元**判定（任何不確定都落到 ``COMPATIBLE``）。"""
        agent_id = self._agent_id
        today = _utc_today()
        ensure_current_day(self._budget)

        # ① 預算防禦線（≤1 次／agent／日）：超額 ⇒ **不呼叫**、fail-closed。
        if agent_id is not None:
            by_day = self._budget.setdefault(_BUDGET_BY_AGENT_DAY, {})
            used = int(by_day.get((agent_id, today), 0))
            if used >= MAX_CALLS_PER_AGENT_PER_DAY:
                self._budget["budget_exhausted"] = (
                    int(self._budget.get("budget_exhausted", 0)) + 1
                )
                logger.info(
                    "[contradiction] ↻ budget exhausted agent=%s date=%s used=%s "
                    "⇒ 不呼叫 LLM（fail-closed）",
                    agent_id,
                    today,
                    used,
                )
                return VERDICT_COMPATIBLE
            # ② 計次（**先**計 ⇒ 中途失敗也不會超額）。
            by_day[(agent_id, today)] = used + 1

        # ③ 呼叫（**只**經由注入的 proxy；禁重試、token 上限、逾時）。
        try:
            raw = self._call_llm(prompt)
        except Exception as exc:  # noqa: BLE001 — fail-closed（不讓例外冒泡）
            self._budget["failed"] = int(self._budget.get("failed", 0)) + 1
            logger.warning(
                "[contradiction] ↻ llm failed agent=%s: %s: %s",
                agent_id,
                type(exc).__name__,
                exc,
            )
            return VERDICT_COMPATIBLE

        # ④ 嚴格二元解析（無法解析 ⇒ COMPATIBLE）。
        verdict = parse_verdict_strict(raw)
        if verdict == VERDICT_CONTRADICT:
            self._budget["succeeded"] = int(self._budget.get("succeeded", 0)) + 1
        else:
            self._budget["failed"] = int(self._budget.get("failed", 0)) + 1
        logger.info(
            "[contradiction] ↻ verdict agent=%s date=%s verdict=%s called_llm=%s",
            agent_id,
            today,
            verdict,
            True,
        )
        return verdict

    # ── 內部 ──────────────────────────────────────────────────────────
    def _call_llm(self, prompt: str) -> Any:
        """把判定 prompt 送進**注入的** proxy（禁重試、token 上限、逾時）。"""
        messages = [
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": str(prompt)},
        ]
        coro = self._proxy.generate_text(
            messages,
            agent_id=self._agent_id or "system",
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
        )
        return _run_coroutine_blocking(coro, self.timeout)

    # ── agent 綁定（讓 backend 層自己也有「每 agent 每日 1 次」的權威依據）──
    def bind_agent(self, agent_id: str) -> "VerdictBackend":
        """回傳**綁定 agent** 的新 backend（**共用同一份預算計數器**）。"""
        return VerdictBackend(
            self._proxy,
            budget=self._budget,
            agent_id=agent_id,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )


def make_verdict_backend(proxy: Any, **kwargs: Any) -> VerdictBackend:
    """組一支真實 LLM 矛盾判定 backend（**薄接線**；本模組不建立連線）。

    Args:
        proxy: 注入的 ``LLMProxy``（任何有 ``generate_text`` 的物件皆可）。
        **kwargs: 透傳給 :class:`VerdictBackend`（``budget``／``agent_id``／
            ``max_tokens``／``max_retries``／``timeout``）。
    """
    return VerdictBackend(proxy, **kwargs)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CALLS_PER_AGENT_PER_DAY",
    "MAX_RETRIES",
    "MAX_TOKENS",
    "VERDICT_COMPATIBLE",
    "VERDICT_CONTRADICT",
    "VerdictBackend",
    "budget_by_agent_day",
    "ensure_current_day",
    "make_verdict_backend",
    "new_budget",
    "parse_verdict_strict",
]
