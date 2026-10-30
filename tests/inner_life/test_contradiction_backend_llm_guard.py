"""tests/inner_life/test_contradiction_backend_llm_guard.py — 真實 LLM 矛盾判定 backend 守衛（G1～G7）。

工單：FEAT-G3-PRODUCTION-BACKEND-WIRING（2026-10-28；G-3 生產 Backend 接線與排程掛載）

這支守衛釘死 Owner 2026-10-28 核准的**防禦合約**（逐條一個專屬變異實驗）：

| # | 不變量 | 變異方向 |
|---|---|---|
| G1 | 同一 ``(agent, date)`` 第二次呼叫**不觸發** LLM（≤1 次／agent／日；跨實例共享計數器） | 把預算上限放大 ⇒ 必紅 |
| G2 | 單次呼叫的 token 上限 ≤300 | 把上限調大 ⇒ 必紅 |
| G3 | ``retries = 0``（**顯式**傳參，不依賴 repo 預設 3） | 改成重試 1 次 ⇒ 必紅 |
| G4 | **fail-closed**：例外／逾時／無法解析 ⇒ ``COMPATIBLE`` 且**不寫引擎** | 改成 fail-open ⇒ 必紅 |
| G5 | 回傳值**只可能**是 ``CONTRADICT`` / ``COMPATIBLE`` 二元 | 讓它回第三種值 ⇒ 必紅 |
| G6 | 模組層**無** LLM 連線建立（延遲 import；不 import 任何網路面） | 改成模組層建立 ⇒ 必紅 |
| G7 | 生產接線點存在：``run_server.py`` 的 ``_elevate_check()`` 注入 backend ＋ **共享**計數器 | 拿掉注入 ⇒ 必紅 |

🔺 **零真實 LLM**：所有呼叫一律注入 mock proxy；本檔**不連網、不讀寫生產
``data/**``、不啟動任何服務行程** ⇒ **零 LLM 成本**。
🔺 **零 repo 寫入**：變異實驗在 ``%TEMP%`` 的副本上跑（repo 檔案全程零寫入）。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.inner_life import contradiction_backend_llm as backend_mod
from src.inner_life.contradiction_backend_llm import (
    MAX_CALLS_PER_AGENT_PER_DAY,
    MAX_RETRIES,
    MAX_TOKENS,
    VERDICT_COMPATIBLE,
    VERDICT_CONTRADICT,
    budget_by_agent_day,
    ensure_current_day,
    make_verdict_backend,
    new_budget,
    parse_verdict_strict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src/inner_life/contradiction_backend_llm.py"
RUN_SERVER_PATH = REPO_ROOT / "scripts/run_server.py"

#: G6 —— 禁止 import 的模組（完全等於或以其為前綴皆算）；皆具網路／連線能力。
BANNED_IMPORT_MODULES = (
    "src.llm",
    "httpx",
    "http",
    "requests",
    "aiohttp",
    "urllib3",
    "urllib.request",
    "openai",
    "anthropic",
)

#: G6 —— 禁止在模組層呼叫的連線建立者（client 構造器名）。
BANNED_CLIENT_CONSTRUCTORS = (
    "AsyncClient",
    "Client",
    "OpenAI",
    "AsyncOpenAI",
    "Anthropic",
)


# ─────────────────────────────────────────────────────────────────────
# 測試用 stub（**不依賴**真實 soul_elevation / 網路 / 磁碟）
# ─────────────────────────────────────────────────────────────────────


class _MockProxy:
    """注入式 mock proxy（**零真實 LLM**）：記錄每次呼叫與**實際收到的 kwargs**。

    🔺 ``generate_text`` 用 ``**kwargs`` 收尾 ⇒ 沒被**顯式**傳入的參數不會出現在
    ``kwargs`` 裡 ⇒ 守衛 G3 可以精準分辨「顯式傳 0」與「依賴預設」。
    """

    def __init__(
        self,
        response: str = "CONTRADICT",
        error: Optional[BaseException] = None,
        delay: float = 0.0,
    ) -> None:
        self.response = response
        self.error = error
        self.delay = delay
        self.calls: List[Dict[str, Any]] = []

    async def generate_text(
        self,
        messages: List[Dict[str, str]],
        agent_id: str = "system",
        max_tokens: int = 200,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "messages": list(messages),
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "kwargs": dict(kwargs),
            }
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response


class _StubEngine:
    """stub 引擎：只記錄 ``record_contradiction``／``supersede`` 的呼叫。"""

    def __init__(self) -> None:
        self._nodes: Dict[str, Any] = {}
        self.record_calls: List[Dict[str, Any]] = []
        self.supersede_calls: List[Dict[str, Any]] = []

    def record_contradiction(
        self,
        node_id: str,
        *,
        source_id: str,
        ts: Optional[str] = None,
        event_identity: Optional[str] = None,
        **_kwargs: Any,
    ) -> None:
        self.record_calls.append(
            {
                "node_id": node_id,
                "source_id": source_id,
                "ts": ts,
                "event_identity": event_identity,
            }
        )

    def supersede(
        self,
        node_id: str,
        *,
        source_ids: List[str],
        new_content: str,
        new_confidence: float,
        valence: str,
        ts: Optional[str] = None,
        **_kwargs: Any,
    ) -> None:
        self.supersede_calls.append({"node_id": node_id, "ts": ts})


def _soul(node_id: str = "soul-1", agent_id: str = "agent-a") -> Dict[str, Any]:
    """靈魂節點（形狀與生產 ``elevation_nodes.jsonl`` 的列同款）。"""
    return {
        "node_id": node_id,
        "agent_id": agent_id,
        "node_type": "value",
        "candidate_node_type": None,
        "content": "他喜歡喝咖啡",
        "created_ts": "2026-10-01T10:00:00Z",
        "confidence": 0.7,
        "valence": "neutral",
        "source_ids": ["src-old"],
    }


def _pattern(node_id: str = "pat-1", agent_id: str = "agent-a") -> Dict[str, Any]:
    """成熟 pattern 節點。"""
    return {
        "node_id": node_id,
        "agent_id": agent_id,
        "node_type": "pattern",
        "candidate_node_type": "value",
        "content": "他其實不喜歡喝咖啡",
        "created_ts": "2026-10-05T10:00:00Z",
        "source_ids": ["src-new"],
    }


def _module_source() -> str:
    """模組**實際**被載入的來源（變異實驗下＝``%TEMP%`` 底下的副本）。"""
    source = inspect.getsourcefile(backend_mod)
    assert source, "backend 模組沒有來源檔"
    return Path(source).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# G1 —— ≤1 次／agent／日（跨實例共享計數器）
# ─────────────────────────────────────────────────────────────────────


def test_g1_second_call_same_agent_same_day_never_calls_llm():
    """G1：同一 ``(agent, date)`` 第二次呼叫**不觸發** LLM（超額 ⇒ fail-closed）。

    🔺 本測試**只**斷言「有沒有真的打 LLM」與計數器（**不**斷言判定字串）⇒
    判定值的變異不會連帶打紅這條 ⇒ 變異歸因得以精準。
    """
    budget = new_budget()
    proxy = _MockProxy(response="CONTRADICT")
    backend = make_verdict_backend(proxy=proxy, budget=budget).bind_agent("agent-a")

    backend("prompt-1")
    backend("prompt-2")

    assert len(proxy.calls) == 1, "第二次呼叫**不得**真的打 LLM"
    assert budget["budget_exhausted"] == 1


def test_g1_budget_is_shared_across_backend_instances():
    """G1：預算計數器**跨實例共享** ⇒ 換一支 backend 也不會突破每日 1 次。"""
    budget = new_budget()
    proxy = _MockProxy(response="CONTRADICT")

    backend_a = make_verdict_backend(proxy=proxy, budget=budget).bind_agent("agent-a")
    backend_b = make_verdict_backend(proxy=proxy, budget=budget).bind_agent("agent-a")

    backend_a("prompt-1")
    backend_b("prompt-2")

    assert len(proxy.calls) == 1, "計數器未共享 ⇒ 每日上限會被重置而失守"


def test_g1_other_agent_and_next_day_get_their_own_budget():
    """G1：預算是**每 agent 每日**一次，不是全域一次，也不是終身一次。"""
    budget = new_budget()
    proxy = _MockProxy(response="CONTRADICT")
    backend = make_verdict_backend(proxy=proxy, budget=budget)

    backend.bind_agent("agent-a")("prompt-1")
    backend.bind_agent("agent-b")("prompt-2")
    assert len(proxy.calls) == 2, "不同 agent 各自有 1 次額度"

    # 跨日 ⇒ 額度重生（UTC 日界；日期由模組的 ``_utc_today`` 決定）。
    #
    # 🔴 曆法炸彈拔除（Owner 2026-10-29 裁定「核准開立修復工單，立即排工拔除」）：
    #    原本把「明天」**寫死**成 ``"2026-10-29"`` ⇒ 當執行當日恰好是 2026-10-29
    #    時，「明天」＝「今天」⇒ 跨日不成立 ⇒ 額度不會重生 ⇒ 本測試**必然紅**
    #    （寫死日期的自我否定；實測：2026-10-29 當日 FAIL、模擬成 10-28 PASS）。
    # ⇒ 改為**相對於執行當下**的動態隔日推導：以模組自己的 ``_utc_today()``
    #    為基準日 ＋ 1 天 ⇒ 任何執行日都成立，且與模組的 UTC 日界口徑一致
    #    （不另立一套日期權威）。
    original_today = backend_mod._utc_today
    today = original_today()
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    assert tomorrow != today, "隔日推導失敗：今天與明天相同（%s）" % today
    backend_mod._utc_today = lambda: tomorrow  # type: ignore[assignment]
    try:
        backend.bind_agent("agent-a")("prompt-3")
    finally:
        backend_mod._utc_today = original_today  # type: ignore[assignment]
    assert len(proxy.calls) == 3, "跨日 ⇒ 額度重生"

    view = budget_by_agent_day(budget)
    assert view[("agent-a", tomorrow)] == 1, (
        "跨日後的計數必須落在隔日（%s）" % tomorrow)
    # 🔺 agent-b 的計數落在**當日**；日鍵由計數器本身決定（不依賴外部捕捉的
    #    日期字串）⇒ 測試恰好跨過 UTC 日界時也不會誤判。
    agent_b_days = sorted(d for (a, d) in view if a == "agent-b")
    assert len(agent_b_days) == 1, "agent-b 必須只有單一日的計數：%s" % agent_b_days
    assert view[("agent-b", agent_b_days[0])] == 1


def test_g1_counter_shape_is_agent_day_keyed():
    """G1：計數器形如 ``{(agent_id, date): n}``（工單 D1.2）。"""
    budget = new_budget()
    proxy = _MockProxy(response="CONTRADICT")
    backend = make_verdict_backend(proxy=proxy, budget=budget).bind_agent("agent-a")
    backend("prompt-1")

    view = budget_by_agent_day(budget)
    today = backend_mod._utc_today()
    assert view[("agent-a", today)] == 1
    assert set(view.keys()) == {("agent-a", today)}


# ─────────────────────────────────────────────────────────────────────
# G2 —— 單次呼叫 token 上限 ≤300
# ─────────────────────────────────────────────────────────────────────


def test_g2_single_call_token_cap_is_at_most_300():
    """G2：單次呼叫的 ``max_tokens`` ≤300（**在呼叫點**傳入）。"""
    proxy = _MockProxy(response="CONTRADICT")
    backend = make_verdict_backend(proxy=proxy, budget=new_budget()).bind_agent("agent-a")
    backend("prompt-1")

    assert len(proxy.calls) == 1
    assert proxy.calls[0]["max_tokens"] <= 300, (
        f"token 上限超出契約：{proxy.calls[0]['max_tokens']}"
    )
    assert MAX_TOKENS == 300


# ─────────────────────────────────────────────────────────────────────
# G3 —— retries = 0（顯式傳參，不依賴 repo 預設 3）
# ─────────────────────────────────────────────────────────────────────


def test_g3_max_retries_zero_is_passed_explicitly():
    """G3：``max_retries=0`` **顯式**傳入（repo 預設是 3 ⇒ 不得依賴預設）。"""
    proxy = _MockProxy(response="CONTRADICT")
    backend = make_verdict_backend(proxy=proxy, budget=new_budget()).bind_agent("agent-a")
    backend("prompt-1")

    assert len(proxy.calls) == 1
    kwargs = proxy.calls[0]["kwargs"]
    assert "max_retries" in kwargs, (
        "max_retries 必須**顯式**傳入；缺席＝依賴預設（repo 預設 3）"
    )
    assert kwargs["max_retries"] == 0
    assert MAX_RETRIES == 0


def test_g3_repo_llm_contract_pins_max_retries_default():
    """G3：repo 既有契約錨點 —— ``LLMBackend.complete`` 的 ``max_retries`` 預設是 3。

    🔺 這條是「**不得**依賴預設」的前提錨點：若哪天 repo 把預設改成 0，
    本模組的「顯式傳 0」仍然成立（防禦合約不變），但這個錨點會提醒維護者
    預設值變了。
    """
    proxy_src = (REPO_ROOT / "src/llm/proxy.py").read_text(encoding="utf-8")
    assert 'kwargs.pop("max_retries", 3)' in proxy_src, (
        "repo 的 max_retries 預設已變動 ⇒ 請重新確認本模組的顯式傳 0 仍然必要"
    )


# ─────────────────────────────────────────────────────────────────────
# G4 —— fail-closed（例外／逾時／無法解析 ⇒ COMPATIBLE 且不寫引擎）
# ─────────────────────────────────────────────────────────────────────


def _run_pipeline(backend: Any, engine: _StubEngine, budget: Dict[str, Any]) -> Any:
    """用真實 backend 跑一次接線層（判定 → 累積器 → supersede）。"""
    from src.inner_life.contradiction_judge_prototype import ContradictionPipeline

    pipeline = ContradictionPipeline(
        engine=engine, backend=backend, calls=budget, agent_id="agent-a"
    )
    return pipeline.run(
        soul_node=_soul(),
        candidate_pattern=_pattern(),
        ts="2026-10-05T09:30:00Z",
    )


def test_g4_backend_exception_is_fail_closed_and_writes_nothing():
    """G4：backend 拋例外 ⇒ ``COMPATIBLE`` ＋ **0 次引擎寫入**。"""
    proxy = _MockProxy(error=RuntimeError("boom"))
    backend = make_verdict_backend(proxy=proxy, budget=new_budget()).bind_agent("agent-a")
    engine = _StubEngine()

    outcome = _run_pipeline(backend, engine, backend._budget)

    assert outcome.should_record is False
    assert outcome.recorded is False
    assert outcome.superseded is False
    assert engine.record_calls == [], "失敗 ⇒ 0 次寫入引擎"
    assert engine.supersede_calls == []


def test_g4_timeout_is_fail_closed():
    """G4：逾時 ⇒ ``COMPATIBLE``（不動作、不寫引擎）。"""
    proxy = _MockProxy(response="CONTRADICT", delay=1.0)
    backend = make_verdict_backend(
        proxy=proxy, budget=new_budget(), timeout=0.05
    ).bind_agent("agent-a")
    engine = _StubEngine()

    outcome = _run_pipeline(backend, engine, backend._budget)

    assert outcome.should_record is False
    assert outcome.recorded is False
    assert engine.record_calls == []
    assert engine.supersede_calls == []


def test_g4_unparsable_response_is_fail_closed():
    """G4：無法解析的回應 ⇒ ``COMPATIBLE`` ＋ **0 次引擎寫入**。"""
    proxy = _MockProxy(response="我想想看……兩者好像都有點道理")
    backend = make_verdict_backend(proxy=proxy, budget=new_budget()).bind_agent("agent-a")
    engine = _StubEngine()

    outcome = _run_pipeline(backend, engine, backend._budget)

    assert outcome.should_record is False
    assert outcome.recorded is False
    assert outcome.verdict in (VERDICT_COMPATIBLE, "UNDECIDED")
    assert engine.record_calls == []
    assert engine.supersede_calls == []


# ─────────────────────────────────────────────────────────────────────
# G5 —— 回傳值只可能二元
# ─────────────────────────────────────────────────────────────────────


def test_g5_return_value_is_always_binary():
    """G5：無論回應長什麼樣，backend 的回傳值**只可能**是二元判定。"""
    responses = [
        "CONTRADICT",
        "COMPATIBLE",
        "contradict",
        "compatible",
        "  CONTRADICT  ",
        "",
        "   ",
        "CONTRADICT and COMPATIBLE",
        "MAYBE",
        "我不知道",
        "CONTRADICT。",
    ]
    for response in responses:
        proxy = _MockProxy(response=response)
        backend = make_verdict_backend(
            proxy=proxy, budget=new_budget()
        ).bind_agent("agent-a")
        verdict = backend("prompt")
        assert verdict in (VERDICT_CONTRADICT, VERDICT_COMPATIBLE), (
            f"回傳值必須是二元判定，實際：{verdict!r}（回應 {response!r}）"
        )

    # 非字串回應 ⇒ 仍是二元。
    proxy = _MockProxy(response=None)  # type: ignore[arg-type]
    backend = make_verdict_backend(proxy=proxy, budget=new_budget()).bind_agent("agent-a")
    assert backend("prompt") == VERDICT_COMPATIBLE


def test_g5_verdict_values_are_parsed_from_the_llm_response():
    """G5：backend 的回傳值**逐字**對應 LLM 的二元回答（不摻雜、不改寫）。"""
    cases = (
        ("CONTRADICT", VERDICT_CONTRADICT),
        ("COMPATIBLE", VERDICT_COMPATIBLE),
        ("contradict", VERDICT_CONTRADICT),
        ("compatible", VERDICT_COMPATIBLE),
        ("  CONTRADICT  ", VERDICT_CONTRADICT),
    )
    for response, expected in cases:
        proxy = _MockProxy(response=response)
        backend = make_verdict_backend(
            proxy=proxy, budget=new_budget()
        ).bind_agent("agent-a")
        assert backend("prompt") == expected, (
            f"回應 {response!r} 應解析成 {expected!r}"
        )


def test_g5_parser_is_bitwise_aligned_with_g3_judge_parser():
    """G5：本模組的解析器與 G-3 ``parse_verdict`` **逐筆同義**（不得漂移）。"""
    from src.inner_life.contradiction_judge_prototype import (
        VERDICT_UNDECIDED,
        parse_verdict,
    )

    samples = [
        "CONTRADICT",
        "COMPATIBLE",
        "contradict",
        "compatible",
        "  CONTRADICT  ",
        "",
        "MAYBE",
        "CONTRADICT and COMPATIBLE",
        None,
        123,
    ]
    for sample in samples:
        expected = parse_verdict(sample)
        actual = parse_verdict_strict(sample)
        if expected == VERDICT_UNDECIDED:
            expected = VERDICT_COMPATIBLE  # fail-closed：UNDECIDED ≡ COMPATIBLE
        assert actual == expected, (
            f"兩份解析語意漂移：input={sample!r} judge={expected!r} backend={actual!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# G6 —— 模組層無 LLM 連線建立（延遲 import）
# ─────────────────────────────────────────────────────────────────────


def _import_offenders(name: str) -> bool:
    for banned in BANNED_IMPORT_MODULES:
        if name == banned or name.startswith(banned + "."):
            return True
    return False


def test_g6_module_layer_imports_no_llm_network_surface():
    """G6：模組層**不得** import 任何具網路／連線能力的模組（延遲 import）。"""
    tree = ast.parse(_module_source())
    offenders: List[str] = []
    for node in tree.body:  # ← 只看**模組層**的 import
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _import_offenders(alias.name):
                    offenders.append(f"import:{alias.name}@line{node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _import_offenders(module):
                offenders.append(f"from:{module}@line{node.lineno}")
            for alias in node.names:
                combined = f"{module}.{alias.name}" if module else alias.name
                if _import_offenders(combined):
                    offenders.append(f"from:{combined}@line{node.lineno}")
    assert offenders == [], f"模組層不得具備任何 LLM／網路能力面：{offenders}"


def test_g6_module_never_imports_llm_network_surface_anywhere():
    """G6：整支檔案**不得** import 任何 LLM／網路面（backend 只用注入的 proxy）。"""
    tree = ast.parse(_module_source())
    offenders: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _import_offenders(alias.name):
                    offenders.append(f"import:{alias.name}@line{node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _import_offenders(module):
                offenders.append(f"from:{module}@line{node.lineno}")
            for alias in node.names:
                combined = f"{module}.{alias.name}" if module else alias.name
                if _import_offenders(combined):
                    offenders.append(f"from:{combined}@line{node.lineno}")
    assert offenders == [], f"本模組不得自行建立連線路徑：{offenders}"


def test_g6_module_layer_constructs_no_llm_client():
    """G6：模組層**不得**構造任何 LLM client（連線只可能在注入的 proxy 內部）。"""
    tree = ast.parse(_module_source())
    offenders: List[str] = []
    for node in tree.body:  # ← 只看**模組層**的陳述
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in BANNED_CLIENT_CONSTRUCTORS:
                    offenders.append(f"call:{name}@line{inner.lineno}")
    assert offenders == [], f"模組層不得建立 LLM 連線：{offenders}"


# ─────────────────────────────────────────────────────────────────────
# G7 —— 生產接線點（run_server.py 確實注入 backend ＋ 共享計數器）
# ─────────────────────────────────────────────────────────────────────


def _run_server_tree() -> ast.Module:
    return ast.parse(RUN_SERVER_PATH.read_text(encoding="utf-8"))


def _elevate_check_with_elevate_call() -> ast.FunctionDef:
    """找到**真的**會呼叫 ``elevate_matured_patterns`` 的那支 ``_elevate_check``。"""
    tree = _run_server_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_elevate_check":
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "elevate_matured_patterns"
                ):
                    return node
    raise AssertionError("run_server.py 找不到會呼叫 elevate_matured_patterns 的 _elevate_check")


def test_g7_production_wiring_injects_backend_and_shared_counter():
    """G7：``_elevate_check()`` 必須注入 backend ＋ **共享**計數器。"""
    fn = _elevate_check_with_elevate_call()
    call = None
    for inner in ast.walk(fn):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "elevate_matured_patterns"
        ):
            call = inner
            break
    assert call is not None
    kwargs = {kw.arg for kw in call.keywords}
    assert "contradiction_backend" in kwargs, "生產槽必須注入真實矛盾判定 backend"
    assert "contradiction_calls" in kwargs, "生產槽必須注入**共享**預算計數器"


def test_g7_shared_counter_is_created_once_outside_the_check():
    """G7：計數器在**呼叫端**建立一次（行程級單例），不得每次呼叫重置。"""
    fn = _elevate_check_with_elevate_call()
    for inner in ast.walk(fn):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "new_budget"
        ):
            raise AssertionError(
                "預算計數器不得在每次 _elevate_check 內新建（會讓『每日 1 次』失守）"
            )


def test_g7_trigger_point_is_unchanged():
    """G7：觸發點不動 —— ``handle_event`` 仍以 fire-and-forget 呼叫 ``_elevate_check``。"""
    tree = _run_server_tree()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_event":
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "_elevate_check"
                ):
                    found = True
    assert found, "觸發點被動到 ⇒ 排程掛載語意變了（工單 D2.3 禁止）"


# ─────────────────────────────────────────────────────────────────────
# 輔助：不變量的完整性（anti-vacuity）
# ─────────────────────────────────────────────────────────────────────


def test_g1_budget_constant_is_one():
    """G1：預算常數本身必須是 1（避免有人把它調大／調 0 讓守衛失去牙齒）。"""
    assert MAX_CALLS_PER_AGENT_PER_DAY == 1


def test_g2_token_constant_is_300():
    """G2：token 上限常數本身必須是 300。"""
    assert MAX_TOKENS == 300


def test_g3_retry_constant_is_zero():
    """G3：重試次數常數本身必須是 0。"""
    assert MAX_RETRIES == 0


def test_g5_verdict_constants_are_binary():
    """G5：判定字串常數本身必須是那兩個（二元）。"""
    assert (VERDICT_CONTRADICT, VERDICT_COMPATIBLE) == ("CONTRADICT", "COMPATIBLE")


def test_ensure_current_day_resets_the_judge_facing_counter():
    """跨日滾動：G-3 判定器介面（``agents``）必須被重置（否則第二天起永久停擺）。"""
    budget = new_budget()
    budget["agents"]["agent-a"] = {"attempted": 1}
    budget["attempted"] = 5
    original_today = backend_mod._utc_today
    # 🔴 不可把「明天」寫死成某個日期字串：一旦真實 UTC 日追上那個字串，
    #    這個 mock 就不再構成「跨日」⇒ ensure_current_day 不滾動 ⇒ 測試恆紅
    #    （2026-10-30 00:00 UTC 實測引爆）。一律以模組自己的 _utc_today() 為
    #    基準日 +1 天推導（不另立日期權威）；與同檔 G1 的既有做法一致。
    today = original_today()
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    assert tomorrow != today, "隔日推導失敗：今天與明天相同（%s）" % today
    backend_mod._utc_today = lambda: tomorrow  # type: ignore[assignment]
    try:
        rolled = ensure_current_day(budget)
        again = ensure_current_day(budget)
    finally:
        backend_mod._utc_today = original_today  # type: ignore[assignment]
    assert rolled is True
    assert budget["agents"] == {}
    assert budget["attempted"] == 0
    assert again is False, "同一天不得重複滾動"
