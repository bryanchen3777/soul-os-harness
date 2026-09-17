# tests/soul/test_life_thread_dissolution_exec.py
# LIFE-THREAD-M2-EXEC — 沉澱執行層（Dissolution Execution）測試矩陣 T1~T40。
#
# 受測模組（唯讀）：`src/soul/life_thread_dissolution_exec.py`
#   - pure stdlib、0 `src.*` import、0 接線、0 真實 LLM、0 網路。
#   - 所有外部依賴（LLM / SAGE 寫入 / 時鐘 / 預算）**一律注入 fake**。
#
# 本檔紅線自證：
#   * 不啟停任何服務、不綁任何埠、不對任何 URL 發請求。
#   * 唯一真實 I/O 是 §I 的 SAGE 整合測試，且 db 落在 pytest `tmp_path`（
#     `tests/conftest.py` 的 autouse 資料根隔離另把 `SOUL_OS_DATA_DIR` 指向 tmp）。
#   * 不使用 `Stop-Process` / `taskkill` 等任何行程操作（本檔 0 行程命令）。
#
# 慣例對齊 `tests/soul/test_life_thread_wake_gate_m3.py`：`tests/soul/` 無 `__init__.py`，
# 本檔自行 `sys.path.insert(0, REPO_ROOT)`。
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.soul.life_thread_dissolution_exec as ex  # noqa: E402
from src.soul.life_thread_dissolution import (  # noqa: E402
    DissolutionEvaluation,
    DissolutionReason,
    ThreadStatus,
    evaluate_thread_dissolution,
)

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_dissolution_exec.py"
MODULE_NAME = "life_thread_dissolution_exec"
TEST_FILE_NAME = Path(__file__).name

AGENT = "agent_m2exec"
AGENT_LONG = "agent_" + ("x" * 300)
THREAD_ID = "th-m2exec-1"
PROMPT = "你剛完成一條生活線頭：「完成一份簡報」，歷時 7 天。\n以第一人稱回答：這段經歷，對「我」意味著什麼？"
PROMPT_MARKER = "zzq-prompt-marker-zzq"
NARRATIVE_MARKER = "zzq-narrative-marker-zzq"
NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
NOW_EPOCH = NOW.timestamp()

THREAD: Dict[str, Any] = {
    "thread_id": THREAD_ID,
    "status": "completed",
    "dissolved_at": None,
    "title": "完成一份簡報",
    "narrative_content": "我把簡報做完了",
    "origin_type": "goal_driven",
    "created_at": "2026-09-10T00:00:00Z",
    "updated_at": "2026-09-16T00:00:00Z",
}

GOOD_PAYLOAD = json.dumps(
    {"dissolution": "我把簡報做完了，這讓我覺得自己能扛事。", "meaning_kind": "competence"},
    ensure_ascii=False,
)


# ──────────────────────────────────────────────────────────────
# §0 夾具與 fake（0 I/O、0 外呼、0 網路）
# ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_exec_state():
    """每個測試前後清空行程內狀態：預算單例 ＋ 冪等 sentinel（測試間零耦合）。"""
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    try:
        yield
    finally:
        ex._clear_consolidated_registry()
        ex._reset_default_budget()


def _run(coro):
    """跑單一 coroutine（不依賴 pytest-asyncio 的 strict 模式設定）。"""
    return asyncio.run(coro)


def _evaluation(**overrides: Any) -> Dict[str, Any]:
    """M2 `DissolutionEvaluation` 的 dict 等價形狀（duck-typing 用；0 依賴 M2）。"""
    base: Dict[str, Any] = {
        "thread_id": THREAD_ID,
        "target_status": "completed",
        "reason": "goal_achieved",
        "should_mutate": True,
        "reflection_prompt": PROMPT,
        "extra_metadata": {
            "advisory": False,
            "sedimentation_context": {
                "agent_id": AGENT,
                "title": THREAD["title"],
                "narrative_content": THREAD["narrative_content"],
                "origin_type": THREAD["origin_type"],
                "terminal_status": "completed",
                "soul_context": None,
                "created_at": THREAD["created_at"],
                "updated_at": THREAD["updated_at"],
            },
            "skipped": None,
        },
    }
    base.update(overrides)
    return base


def _m2_evaluation(
    *,
    target_status: ThreadStatus = ThreadStatus.COMPLETED,
    should_mutate: bool = True,
    reflection_prompt: Optional[str] = PROMPT,
    thread_id: str = THREAD_ID,
) -> DissolutionEvaluation:
    """**真實** M2 frozen dataclass（跨模組 duck-typing 相容性實證）。"""
    return DissolutionEvaluation(
        thread_id=thread_id,
        target_status=target_status,
        reason=DissolutionReason.GOAL_ACHIEVED,
        should_mutate=should_mutate,
        reflection_prompt=reflection_prompt,
        extra_metadata={"advisory": False, "sedimentation_context": None, "skipped": None},
    )


class FakeLLM:
    """同步 fake LLM（記錄每次呼叫的 prompt 與 max_tokens）。"""

    def __init__(self, response: Any = GOOD_PAYLOAD, exc: Optional[BaseException] = None):
        self.response = response
        self.exc = exc
        self.calls: list = []

    def __call__(self, prompt: str, max_tokens: Any = None) -> Any:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if self.exc is not None:
            raise self.exc
        return self.response

    @property
    def count(self) -> int:
        return len(self.calls)


class AsyncFakeLLM(FakeLLM):
    """非同步 fake LLM（回傳 awaitable，驗證 `await` 分支與逾時）。"""

    def __init__(self, response: Any = GOOD_PAYLOAD, *, delay: float = 0.0,
                 exc: Optional[BaseException] = None):
        super().__init__(response, exc)
        self.delay = delay

    async def __call__(self, prompt: str, max_tokens: Any = None) -> Any:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.response


class SyncOnlyLLM:
    """只吃位置參數的 fake（驗證呼叫形式固定為 `max_tokens=` 關鍵字）。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, prompt: str) -> Any:  # noqa: D401 - 故意不吃 max_tokens
        self.calls += 1
        return GOOD_PAYLOAD


class FakeWriter:
    """fake SAGE 寫入器（記錄寫入的 fact；可注入例外／空 fact_id）。"""

    def __init__(self, ret: Any = "fact-0001", exc: Optional[BaseException] = None):
        self.ret = ret
        self.exc = exc
        self.calls: list = []

    def __call__(self, fact: Any) -> Any:
        self.calls.append(dict(fact))
        if self.exc is not None:
            raise self.exc
        return self.ret

    @property
    def count(self) -> int:
        return len(self.calls)


class AsyncFakeWriter(FakeWriter):
    async def __call__(self, fact: Any) -> Any:
        self.calls.append(dict(fact))
        if self.exc is not None:
            raise self.exc
        return self.ret


def _thread_at(tid: str) -> Dict[str, Any]:
    """指定 thread_id 的一組 `(thread, evaluation)` kwargs（避開冪等 sentinel 串擾）。"""
    return {
        "thread": {**THREAD, "thread_id": tid},
        "evaluation": _evaluation(thread_id=tid),
    }


def _exec(**overrides: Any) -> ex.ConsolidationResult:
    """以預設參數跑一次執行層（預設：無 LLM、無 writer，須由測試顯式注入）。"""
    params: Dict[str, Any] = {
        "agent_id": AGENT,
        "thread": dict(THREAD),
        "evaluation": _evaluation(),
        "llm_call": None,
        "fact_writer": None,
        "budget": None,
        "now": NOW,
    }
    params.update(overrides)
    return _run(ex.consolidate_terminal_thread(**params))


def _parse_ast(path: Path) -> Optional[ast.Module]:
    """讀檔並 parse AST；無法解析（SyntaxError）回 `None`。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def _imported_module_names(tree: ast.Module) -> set:
    """AST 抽取所有被 import 的模組名（含 `from x import y` 的 `x.y`）。"""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _importers_of(target: str, roots) -> list:
    """在 `roots` 下找出「有 import `target`」的檔案（AST 級，非字串比對）。"""
    found = []
    for root in roots:
        if not root.exists():
            continue
        for path in _iter_py_files(root):
            tree = _parse_ast(path)
            if tree is None:
                continue
            for name in _imported_module_names(tree):
                if name == target or name.endswith("." + target):
                    found.append(path)
                    break
    return found


# ──────────────────────────────────────────────────────────────
# §A 契約常數／簽名／靜態純度
# ──────────────────────────────────────────────────────────────


def test_t01_module_declares_contract_constants() -> None:
    """T1：四個票面常數逐字落地（成本與逾時的單一真相來源）。"""
    assert ex.CONSOLIDATION_MAX_TOKENS == 300
    assert ex.CONSOLIDATION_TIMEOUT_SECONDS == 20
    assert ex.MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY == 24
    assert ex.MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY == 200
    assert ex.FACT_CONFIDENCE == 1.0
    assert ex.FACT_PREDICATE == "life_thread_dissolved"
    assert ex.FACT_SOURCE == "inference"
    assert ex.FACT_ORIGIN == "lived_experience"
    # D-1：終態值域以 M1/M2 實際 `TERMINAL_STATUSES` 為準（`dissolved` 不是狀態值）。
    assert ex.TERMINAL_TARGET_STATUSES == ("completed", "abandoned")
    assert "dissolved" not in ex.TERMINAL_TARGET_STATUSES


def test_t02_signature_is_keyword_only_and_frozen_result() -> None:
    """T2：主函式簽名逐字固定（全 keyword-only）＋ 結果 dataclass frozen。"""
    sig = inspect.signature(ex.consolidate_terminal_thread)
    assert list(sig.parameters) == [
        "agent_id",
        "thread",
        "evaluation",
        "llm_call",
        "fact_writer",
        "budget",
        "now",
    ]
    for name, param in sig.parameters.items():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
    assert sig.parameters["llm_call"].default is None
    assert sig.parameters["fact_writer"].default is None
    assert sig.parameters["budget"].default is None
    assert sig.parameters["now"].default is None
    assert inspect.iscoroutinefunction(ex.consolidate_terminal_thread)

    assert is_dataclass(ex.ConsolidationResult)
    assert [f.name for f in fields(ex.ConsolidationResult)] == [
        "thread_id",
        "status",
        "fact",
        "llm_calls",
        "reason",
    ]
    result = ex.ConsolidationResult("t", "s", None, 0, "r")
    with pytest.raises(FrozenInstanceError):
        result.status = "other"  # type: ignore[misc]


def test_t03_module_is_pure_stdlib_without_src_imports() -> None:
    """T3：受測模組 0 `src.*` import（執行層靠注入，不硬依賴生產模組）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = _imported_module_names(tree)
    offenders = [n for n in imported if n == "src" or n.startswith("src.")]
    assert offenders == [], f"執行層不得 import 任何 src.* 模組：{offenders}"


def test_t04_module_has_exactly_one_llm_call_site() -> None:
    """T4（硬上限）：全模組 `llm_call(...)` 呼叫點**恰一個**（無重試、無迴圈）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "llm_call"
    ]
    assert len(call_sites) == 1
    keywords = {kw.arg for kw in call_sites[0].keywords}
    assert "max_tokens" in keywords
    assert isinstance(call_sites[0].keywords[0].value, ast.Name)
    assert call_sites[0].keywords[0].value.id == "CONSOLIDATION_MAX_TOKENS"


def test_t05_module_never_touches_m1_write_api() -> None:
    """T5：本模組不寫 M1（無 `append_*` 呼叫、無 `life_threads` import）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    append_calls = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr.startswith("append_")
    ]
    assert append_calls == []
    imported = _imported_module_names(tree)
    assert not any("life_threads" in name for name in imported)


def test_t06_module_imports_no_network_library() -> None:
    """T6（成本自證）：0 網路 client import ⇒ 本模組不存在自行外呼的路徑。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = _imported_module_names(tree)
    forbidden_roots = {
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "urllib3",
        "socket",
        "http",
        "ssl",
        "ftplib",
        "smtplib",
        "websockets",
        "subprocess",
        "importlib",
    }
    offenders = sorted(
        {name.split(".")[0] for name in imported} & forbidden_roots
    )
    assert offenders == []
    # 動態執行/匯入 API（AST 級；`ast.literal_eval` 是受限字面值解析，不在列）。
    dynamic_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } & {"eval", "exec", "__import__", "compile"}
    assert dynamic_calls == set(), dynamic_calls


# ──────────────────────────────────────────────────────────────
# §B 成功路徑
# ──────────────────────────────────────────────────────────────


def test_t07_success_with_real_m2_evaluation() -> None:
    """T7：真實 M2 `DissolutionEvaluation` ⇒ `consolidated`、恰 1 次呼叫、fact 逐欄正確。"""
    llm = FakeLLM()
    writer = FakeWriter()
    result = _exec(
        evaluation=_m2_evaluation(), llm_call=llm, fact_writer=writer
    )

    assert result.status == ex.STATUS_CONSOLIDATED
    assert result.reason == "ok"
    assert result.llm_calls == 1
    assert result.thread_id == THREAD_ID
    assert llm.count == 1
    assert writer.count == 1

    # 呼叫形式固定 `llm_call(prompt, max_tokens=300)`，且 prompt **逐字**取自 M2。
    assert llm.calls[0]["max_tokens"] == ex.CONSOLIDATION_MAX_TOKENS == 300
    assert llm.calls[0]["prompt"] == PROMPT

    fact = result.fact
    assert fact is not None
    assert fact["subject"] == AGENT
    assert fact["predicate"] == "life_thread_dissolved"
    assert fact["object"] == "我把簡報做完了，這讓我覺得自己能扛事。"
    assert fact["source"] == "inference"
    assert fact["origin"] == "lived_experience"
    assert fact["confidence"] == 1.0
    assert isinstance(fact["confidence"], float)
    assert fact["timestamp"] == NOW_EPOCH
    assert fact["valid_from"] == NOW_EPOCH
    assert fact["invalidated_at"] is None
    # 寫入器拿到的內容與回傳 fact 一致（同一份正規化結果）。
    assert writer.calls[0] == fact


def test_t08_success_with_real_m2_decision_function() -> None:
    """T8：與 M2 決策函式端到端（真實 prompt 由 M2 組出，執行層只消耗）。"""
    evaluation = evaluate_thread_dissolution(THREAD, NOW, agent_id=AGENT)
    assert evaluation.target_status is ThreadStatus.COMPLETED
    assert evaluation.should_mutate is True
    assert isinstance(evaluation.reflection_prompt, str)

    llm = FakeLLM()
    writer = FakeWriter()
    result = _exec(evaluation=evaluation, llm_call=llm, fact_writer=writer)
    assert result.status == ex.STATUS_CONSOLIDATED
    assert llm.calls[0]["prompt"] == evaluation.reflection_prompt
    assert writer.count == 1


def test_t09_prompt_is_consumed_verbatim_not_rebuilt() -> None:
    """T9：注入任意 prompt 逐字送出（執行層**不自行拼 prompt**）。"""
    exotic = "  " + PROMPT_MARKER + "\n\n 請只回 JSON：{} " + "  "
    llm = FakeLLM()
    _exec(
        evaluation=_evaluation(reflection_prompt=exotic),
        llm_call=llm,
        fact_writer=FakeWriter(),
    )
    assert llm.calls[0]["prompt"] == exotic


def test_t10_fact_omits_forbidden_keys() -> None:
    """T10：契約 §3.4 禁填欄位（weight／引擎狀態）**不得**出現在 fact。"""
    writer = FakeWriter()
    result = _exec(llm_call=FakeLLM(), fact_writer=writer)
    fact = result.fact or {}
    for forbidden in ("weight", "check_after_ts", "origin_type", "status", "agent_id", "soul_context"):
        assert forbidden not in fact
    assert set(fact) == {
        "subject",
        "predicate",
        "object",
        "source",
        "origin",
        "confidence",
        "timestamp",
        "valid_from",
        "invalidated_at",
    }


def test_t11_fields_are_normalized_and_truncated() -> None:
    """T11：三欄正規化空白；`object` ≤200、`subject` ≤120（對齊 SAGE schema gate）。"""
    long_narrative = "這" * 500
    payload = json.dumps({"dissolution": long_narrative}, ensure_ascii=False)
    writer = FakeWriter()
    result = _exec(
        agent_id=AGENT_LONG,
        llm_call=FakeLLM(payload),
        fact_writer=writer,
    )
    fact = result.fact or {}
    assert len(fact["subject"]) == ex.SUBJECT_MAX_CHARS == 120
    assert len(fact["object"]) == ex.MAX_FIELD_CHARS == 200

    messy = "  第一句。\n\n\t第二句。\u3000 第三句。  "
    payload2 = json.dumps({"dissolution": messy}, ensure_ascii=False)
    result2 = _exec(**_thread_at("th-t11-b"), llm_call=FakeLLM(payload2), fact_writer=FakeWriter())
    assert (result2.fact or {})["object"] == "第一句。 第二句。 第三句。"


def test_t12_confidence_is_forced_and_llm_fields_ignored() -> None:
    """T12：`confidence` 恆為 1.0；LLM 自帶的 subject/predicate/confidence 一律忽略。"""
    payload = json.dumps(
        {
            "dissolution": "這是我的詮釋。",
            "subject": "llm-invented-subject",
            "predicate": "llm-invented-predicate",
            "confidence": 0.2,
            "weight": 9.9,
        },
        ensure_ascii=False,
    )
    result = _exec(llm_call=FakeLLM(payload), fact_writer=FakeWriter())
    fact = result.fact or {}
    assert fact["subject"] == AGENT
    assert fact["predicate"] == "life_thread_dissolved"
    assert fact["confidence"] == 1.0
    assert "weight" not in fact


@pytest.mark.parametrize(
    "meaning_kind,expected_log",
    [
        ("competence", "meaning_kind=competence"),
        ("relief", "meaning_kind=relief"),
        ("COMPETENCE", "meaning_kind=competence"),
        ("not_a_kind", "meaning_kind=none"),
        (None, "meaning_kind=none"),
        (7, "meaning_kind=none"),
    ],
)
def test_t13_meaning_kind_degrades_but_never_blocks(
    meaning_kind: Any, expected_log: str, caplog: pytest.LogCaptureFixture
) -> None:
    """T13：`meaning_kind` 非法值降級 `none`，其餘照寫（契約 §3.2 不因枚舉錯誤丟整段）。"""
    payload = json.dumps({"dissolution": "寫下來。", "meaning_kind": meaning_kind}, ensure_ascii=False)
    with caplog.at_level(logging.INFO, logger=ex.logger.name):
        result = _exec(llm_call=FakeLLM(payload), fact_writer=FakeWriter())
    assert result.status == ex.STATUS_CONSOLIDATED
    assert expected_log in caplog.text


def test_t14_object_key_is_a_tolerated_alias() -> None:
    """T14：`dissolution` 缺席時退化接受 `object`（解析容忍，見 docstring D-3）。"""
    payload = json.dumps({"object": "換個欄位名也收。", "meaning_kind": "loss"}, ensure_ascii=False)
    result = _exec(llm_call=FakeLLM(payload), fact_writer=FakeWriter())
    assert result.status == ex.STATUS_CONSOLIDATED
    assert (result.fact or {})["object"] == "換個欄位名也收。"


def test_narrative_prefers_dissolution_over_object_alias() -> None:
    """敘述欄位優先權：兩鍵同時存在時必須採納 `dissolution`，不得誤用退化別名 `object`。

    T14 只證明「`dissolution` 缺席時 `object` 可退化接受」；本測試補上審計指出的盲區：
    把 `NARRATIVE_KEYS` 的優先序寫反（`("object", "dissolution")`）時必須變紅。
    """
    payload = json.dumps(
        {"dissolution": "PRIMARY_VALUE", "object": "ALIAS_VALUE"}, ensure_ascii=False
    )
    result = _exec(llm_call=FakeLLM(payload), fact_writer=FakeWriter())
    assert result.status == ex.STATUS_CONSOLIDATED
    assert (result.fact or {})["object"] == "PRIMARY_VALUE"
    assert (result.fact or {})["object"] != "ALIAS_VALUE"


def test_t15_async_llm_and_async_writer_are_awaited() -> None:
    """T15：注入 awaitable ⇒ await（同步 fake 與 async fake 皆支援）。"""
    llm = AsyncFakeLLM(delay=0.0)
    writer = AsyncFakeWriter()
    result = _exec(llm_call=llm, fact_writer=writer)
    assert result.status == ex.STATUS_CONSOLIDATED
    assert llm.count == 1
    assert writer.count == 1


def test_t16_inputs_are_not_mutated() -> None:
    """T16：永不 mutate 入參（線頭／裁決皆為唯讀）。"""
    thread = dict(THREAD)
    evaluation = _evaluation()
    thread_snapshot = json.loads(json.dumps(thread, ensure_ascii=False))
    evaluation_snapshot = json.loads(json.dumps(evaluation, ensure_ascii=False))

    _exec(thread=thread, evaluation=evaluation, llm_call=FakeLLM(), fact_writer=FakeWriter())

    assert thread == thread_snapshot
    assert evaluation == evaluation_snapshot


# ──────────────────────────────────────────────────────────────
# §C 呼叫次數硬上限（只有 0 或 1）
# ──────────────────────────────────────────────────────────────


def _report(llm: FakeLLM, writer: FakeWriter, result: ex.ConsolidationResult) -> None:
    assert llm.count == 0, "本情境必須 0 次 LLM 呼叫"
    assert writer.count == 0, "本情境必須 0 次寫入"
    assert result.llm_calls == 0
    assert result.fact is None


def test_t17_terminal_gate_blocks_active_thread() -> None:
    """T17：`active` 一律擋在呼叫路徑之外（fail-safe）。"""
    llm, writer = FakeLLM(), FakeWriter()
    result = _exec(
        evaluation=_evaluation(target_status="active", should_mutate=False),
        llm_call=llm,
        fact_writer=writer,
    )
    assert result.status == ex.STATUS_SKIPPED_NOT_TERMINAL
    _report(llm, writer, result)


def test_t18_terminal_gate_requires_strict_should_mutate() -> None:
    """T18：`should_mutate` 非嚴格 `True`（1／'yes'／None）一律擋下。"""
    for value in (1, "yes", None, False):
        llm, writer = FakeLLM(), FakeWriter()
        result = _exec(
            evaluation=_evaluation(should_mutate=value), llm_call=llm, fact_writer=writer
        )
        assert result.status == ex.STATUS_SKIPPED_NOT_TERMINAL, value
        assert result.reason == "should_mutate_not_true"
        _report(llm, writer, result)


def test_t19_terminal_gate_accepts_enum_and_case_insensitive_status() -> None:
    """T19：容忍 `Enum` 與大小寫（真實 M2 是 `ThreadStatus.COMPLETED`）。"""
    for index, status in enumerate((ThreadStatus.COMPLETED, "completed", "COMPLETED", " Completed ")):
        tid = f"th-t19-{index}"
        llm, writer = FakeLLM(), FakeWriter()
        result = _exec(
            **{
                **_thread_at(tid),
                "evaluation": _evaluation(target_status=status, thread_id=tid),
            },
            llm_call=llm,
            fact_writer=writer,
        )
        assert result.status == ex.STATUS_CONSOLIDATED, status
        assert llm.count == 1


def test_t20_abandoned_is_terminal_and_dissolved_is_not() -> None:
    """T20（D-1 差異釘死）：`abandoned` 是終態；`dissolved` **不是** M1 狀態值。"""
    llm, writer = FakeLLM(), FakeWriter()
    ok = _exec(
        evaluation=_evaluation(target_status="abandoned"), llm_call=llm, fact_writer=writer
    )
    assert ok.status == ex.STATUS_CONSOLIDATED
    assert llm.count == 1

    llm2, writer2 = FakeLLM(), FakeWriter()
    rejected = _exec(
        evaluation=_evaluation(target_status="dissolved"), llm_call=llm2, fact_writer=writer2
    )
    assert rejected.status == ex.STATUS_SKIPPED_NOT_TERMINAL
    _report(llm2, writer2, rejected)


def test_t21_missing_or_odd_evaluation_is_fail_safe() -> None:
    """T21：無法判定（None／缺欄／非 Mapping）⇒ 不得進入呼叫路徑。"""
    for evaluation in (
        None,
        {},
        {"should_mutate": True},
        {"target_status": "completed"},
        "completed",
        42,
        {"target_status": None, "should_mutate": True},
        {"target_status": 3, "should_mutate": True},
    ):
        llm, writer = FakeLLM(), FakeWriter()
        result = _exec(evaluation=evaluation, llm_call=llm, fact_writer=writer)
        assert result.status == ex.STATUS_SKIPPED_NOT_TERMINAL, evaluation
        _report(llm, writer, result)


def test_t22_no_llm_is_fail_closed() -> None:
    """T22：`llm_call is None` ⇒ `skipped_no_llm`（0 呼叫）。"""
    writer = FakeWriter()
    result = _exec(llm_call=None, fact_writer=writer)
    assert result.status == ex.STATUS_SKIPPED_NO_LLM
    assert result.reason == "llm_call_not_injected"
    assert writer.count == 0


def test_t23_no_writer_is_checked_before_llm_call() -> None:
    """T23：`fact_writer is None` ⇒ `skipped_no_writer`，且**不白花一次推理**。"""
    llm = FakeLLM()
    result = _exec(llm_call=llm, fact_writer=None)
    assert result.status == ex.STATUS_SKIPPED_NO_WRITER
    assert result.reason == "fact_writer_not_injected"
    assert llm.count == 0
    assert result.llm_calls == 0


@pytest.mark.parametrize(
    "prompt", [None, "", "   ", "\n\t ", 42, ["not", "a", "prompt"]]
)
def test_t24_missing_prompt_is_zero_call(prompt: Any) -> None:
    """T24：`reflection_prompt` 缺失／空白／非字串 ⇒ `skipped_no_prompt`（0 呼叫）。"""
    llm, writer = FakeLLM(), FakeWriter()
    result = _exec(
        evaluation=_evaluation(reflection_prompt=prompt), llm_call=llm, fact_writer=writer
    )
    assert result.status == ex.STATUS_SKIPPED_NO_PROMPT
    assert result.reason == "reflection_prompt_missing"
    _report(llm, writer, result)


def test_t25_invalid_agent_or_thread_id_is_zero_call() -> None:
    """T25：`agent_id` / `thread_id` 無法識別 ⇒ `invalid_input`（無法履行冪等與 subject 契約）。"""
    llm, writer = FakeLLM(), FakeWriter()
    for bad_agent in ("", "   ", 123, None, b"agent"):
        result = _exec(agent_id=bad_agent, llm_call=llm, fact_writer=writer)
        assert result.status == ex.STATUS_INVALID_INPUT, bad_agent
        assert result.reason == "agent_id_not_non_empty_str"
        _report(llm, writer, result)

    no_id = _evaluation()
    no_id.pop("thread_id")
    result = _exec(thread={}, evaluation=no_id, llm_call=llm, fact_writer=writer)
    assert result.status == ex.STATUS_INVALID_INPUT
    assert result.reason == "thread_id_not_non_empty_str"
    _report(llm, writer, result)


def test_t26_gate_precedes_missing_dependencies() -> None:
    """T26：閘門優先於依賴檢查（非終態 ＋ 無 llm/writer ⇒ 仍報 `skipped_not_terminal`）。"""
    result = _exec(
        evaluation=_evaluation(target_status="dormant", should_mutate=False),
        llm_call=None,
        fact_writer=None,
    )
    assert result.status == ex.STATUS_SKIPPED_NOT_TERMINAL


def test_t27_llm_calls_field_is_never_above_one() -> None:
    """T27：所有情境的 `llm_calls` 只可能是 0 或 1（契約 §3.6 硬上限）。"""
    scenarios = [
        _exec(**_thread_at("th-t27-1"), llm_call=FakeLLM(), fact_writer=FakeWriter()),
        _exec(**_thread_at("th-t27-2"), llm_call=None, fact_writer=FakeWriter()),
        _exec(**_thread_at("th-t27-3"), llm_call=FakeLLM(), fact_writer=None),
        _exec(
            **{
                **_thread_at("th-t27-4"),
                "evaluation": _evaluation(target_status="active", thread_id="th-t27-4"),
            },
            llm_call=FakeLLM(),
            fact_writer=FakeWriter(),
        ),
        _exec(**_thread_at("th-t27-5"), llm_call=FakeLLM("不是 JSON"), fact_writer=FakeWriter()),
        _exec(**_thread_at("th-t27-6"), llm_call=FakeLLM(exc=RuntimeError("boom")), fact_writer=FakeWriter()),
        _exec(
            **_thread_at("th-t27-7"),
            llm_call=FakeLLM(),
            fact_writer=FakeWriter(exc=sqlite3.OperationalError("locked")),
        ),
    ]
    assert {r.llm_calls for r in scenarios} <= {0, 1}


def test_t28_call_count_by_scenario() -> None:
    """T28（硬上限對照表）：成功恰 1 次；其餘 0 呼叫情境逐項驗證。"""
    # 成功
    llm, writer = FakeLLM(), FakeWriter()
    assert _exec(llm_call=llm, fact_writer=writer).llm_calls == 1
    assert llm.count == 1

    # 重複（同 thread 第二次）⇒ 0 呼叫
    assert _exec(llm_call=llm, fact_writer=writer).llm_calls == 0
    assert llm.count == 1

    # 預算耗盡 ⇒ 0 呼叫（用不同 thread_id，避免被冪等 sentinel 先攔）
    budget = ex.ConsolidationBudget(per_agent_limit=0)
    llm2, writer2 = FakeLLM(), FakeWriter()
    result = _exec(**_thread_at("th-t28-budget"), llm_call=llm2, fact_writer=writer2, budget=budget)
    assert result.status == ex.STATUS_SKIPPED_BUDGET
    assert llm2.count == 0


# ──────────────────────────────────────────────────────────────
# §D 格式損毀族（全部 `parse_failed`、0 寫入、不拋例外）
# ──────────────────────────────────────────────────────────────

BROKEN_PAYLOADS = [
    "這不是 JSON",
    "",
    "   ",
    "{}",
    '{"meaning_kind": "loss"}',
    '{"dissolution": ""}',
    '{"dissolution": "   \n  "}',
    '{"dissolution": null}',
    '{"dissolution": 42}',
    '{"dissolution": ["a"]}',
    '{"dissolution": {"nested": "x"}}',
    '{"dissolution": "x"',  # 未閉合
    "```json\n{broken\n```",
    '{"object": ""}',
    "null",
    "[]",
    "[1, 2, 3]",
    "true",
    "```json\n```",
    'not json {"dissolution": } trailing',
]


@pytest.mark.parametrize("payload", BROKEN_PAYLOADS)
def test_t29_broken_payloads_are_parse_failed(payload: Any) -> None:
    """T29：一切解析失敗 ⇒ `parse_failed`、0 寫入、`llm_calls=1`、**不拋例外**。"""
    llm, writer = FakeLLM(payload), FakeWriter()
    result = _exec(llm_call=llm, fact_writer=writer)
    assert result.status == ex.STATUS_PARSE_FAILED
    assert result.fact is None
    assert result.llm_calls == 1
    assert llm.count == 1
    assert writer.count == 0


@pytest.mark.parametrize(
    "payload", [None, 42, 3.14, object(), b"\xff\xfe\x00", ["{}"], {"meaning_kind": "loss"}]
)
def test_t30_non_json_types_are_parse_failed(payload: Any) -> None:
    """T30：非字串／非 Mapping 的回應 ⇒ `parse_failed`（不 raise）。"""
    result = _exec(llm_call=FakeLLM(payload), fact_writer=FakeWriter())
    assert result.status == ex.STATUS_PARSE_FAILED


@pytest.mark.parametrize(
    "payload",
    [
        '```json\n{"dissolution": "甲"}\n```',
        '```\n{"dissolution": "乙"}\n```',
        '```json\n{"dissolution": "丙"}',
        '好的，以下是 JSON：{"dissolution": "丁"} 希望有幫助。',
        "{'dissolution': '戊'}",
        '{"dissolution": "己", "note": "含 } 右括號與 { 左括號"}',
        '{"dissolution": "庚\\n換行"}',
        '{"dissolution": "辛\n裸換行"}',
        '前言 ```json {"dissolution": "壬"} ``` 後語',
        '{"dissolution": "癸", "meaning_kind": "relief",}',
    ],
)
def test_t31_parser_tolerates_common_malformations(payload: str) -> None:
    """T31：圍籬／前後贅字／單引號／裸換行／尾逗號等常見畸形仍能抽出敘述。"""
    writer = FakeWriter()
    result = _exec(llm_call=FakeLLM(payload), fact_writer=writer)
    assert result.status == ex.STATUS_CONSOLIDATED, payload
    assert writer.count == 1
    assert (result.fact or {})["object"]


def test_t32_mapping_response_is_accepted_directly() -> None:
    """T32：fake 直接回 Mapping 也可（無需字串中介）。"""
    result = _exec(
        llm_call=FakeLLM({"dissolution": "直接給 dict。"}), fact_writer=FakeWriter()
    )
    assert result.status == ex.STATUS_CONSOLIDATED
    assert (result.fact or {})["object"] == "直接給 dict。"


# ──────────────────────────────────────────────────────────────
# §E LLM 例外／逾時（降級，絕不中斷主循環）
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc,expected_reason",
    [
        (RuntimeError("llm exploded"), "RuntimeError"),
        (ValueError("bad gateway"), "ValueError"),
        # `asyncio.TimeoutError` 在 3.11 就是 `builtins.TimeoutError` ⇒ 走逾時分支。
        (TimeoutError("upstream timeout"), "timeout"),
        (sqlite3.OperationalError("database is locked"), "OperationalError"),
        (OSError("[Errno 111] refused"), "OSError"),
        (KeyError("missing"), "KeyError"),
    ],
)
def test_t33_llm_exception_degrades_to_llm_failed(
    exc: BaseException, expected_reason: str
) -> None:
    """T33：LLM 例外 ⇒ `llm_failed`（reason=例外類名／逾時）、0 寫入、**不拋例外**。"""
    llm, writer = FakeLLM(exc=exc), FakeWriter()
    result = _exec(llm_call=llm, fact_writer=writer)
    assert result.status == ex.STATUS_LLM_FAILED
    assert result.reason == expected_reason
    assert result.llm_calls == 1
    assert writer.count == 0


def test_t34_llm_timeout_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """T34：逾時 ⇒ `llm_failed`（reason=timeout），且真的在短逾時內收手。"""
    monkeypatch.setattr(ex, "CONSOLIDATION_TIMEOUT_SECONDS", 0.05)
    llm = AsyncFakeLLM(delay=5.0)
    writer = FakeWriter()
    started = time.monotonic()
    result = _exec(llm_call=llm, fact_writer=writer)
    elapsed = time.monotonic() - started
    assert result.status == ex.STATUS_LLM_FAILED
    assert result.reason == "timeout"
    assert result.llm_calls == 1
    assert writer.count == 0
    assert elapsed < 2.0, "逾時未生效（不得等到 fake 的 5 秒）"


def test_t35_sync_llm_without_max_tokens_kwarg_degrades() -> None:
    """T35：呼叫形式固定為 `max_tokens=`（不吃此關鍵字的物件 ⇒ 降級，不炸）。"""
    llm = SyncOnlyLLM()
    result = _exec(llm_call=llm, fact_writer=FakeWriter())
    assert result.status == ex.STATUS_LLM_FAILED
    assert result.reason == "TypeError"
    # 關鍵字不符 ⇒ 參數綁定在進入 `__call__` **之前**就失敗（本體未被執行）。
    assert llm.calls == 0
    # 但仍然算「已嘗試一次呼叫」（預算已扣、llm_calls=1）。
    assert result.llm_calls == 1


def test_t36_cancelled_error_is_not_swallowed() -> None:
    """T36：`asyncio.CancelledError` 不屬 `Exception` ⇒ 取消必須照常傳播（不吞）。"""
    llm = FakeLLM(exc=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _exec(llm_call=llm, fact_writer=FakeWriter())


# ──────────────────────────────────────────────────────────────
# §F SAGE 寫入失敗隔離
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.IntegrityError("UNIQUE constraint failed"),
        RuntimeError("writer blew up"),
        OSError("disk full"),
    ],
)
def test_t37_write_failure_is_isolated_and_never_retried(exc: BaseException) -> None:
    """T37：寫入例外 ⇒ `write_failed`、**不重試**（恰 1 次嘗試）、不拋例外。"""
    llm, writer = FakeLLM(), FakeWriter(exc=exc)
    result = _exec(llm_call=llm, fact_writer=writer)
    assert result.status == ex.STATUS_WRITE_FAILED
    assert result.reason == type(exc).__name__
    assert result.fact is None
    assert result.llm_calls == 1
    assert llm.count == 1
    assert writer.count == 1, "寫入失敗不得重試"


def test_t38_empty_fact_id_is_a_write_failure() -> None:
    """T38：契約 §3.4「失敗回 `''`」⇒ 空字串／空白視為寫入失敗。"""
    for ret in ("", "   "):
        llm, writer = FakeLLM(), FakeWriter(ret=ret)
        result = _exec(llm_call=llm, fact_writer=writer)
        assert result.status == ex.STATUS_WRITE_FAILED
        assert result.reason == "empty_fact_id"
        assert writer.count == 1


def test_t39_async_writer_exception_is_isolated() -> None:
    """T39：async writer 例外同樣隔離。"""
    result = _exec(
        llm_call=AsyncFakeLLM(),
        fact_writer=AsyncFakeWriter(exc=sqlite3.OperationalError("database is locked")),
    )
    assert result.status == ex.STATUS_WRITE_FAILED


def test_t40_failure_does_not_register_sentinel_so_retry_works() -> None:
    """T40（契約 §3.2「下一輪可重試」）：失敗**不**登記 sentinel ⇒ 下一輪仍可成功。"""
    llm = FakeLLM("壞掉的回應")
    writer = FakeWriter()
    first = _exec(llm_call=llm, fact_writer=writer)
    assert first.status == ex.STATUS_PARSE_FAILED
    assert llm.count == 1

    llm.response = GOOD_PAYLOAD
    second = _exec(llm_call=llm, fact_writer=writer)
    assert second.status == ex.STATUS_CONSOLIDATED
    assert llm.count == 2
    assert writer.count == 1

    third = _exec(llm_call=llm, fact_writer=writer)
    assert third.status == ex.STATUS_SKIPPED_DUPLICATE
    assert llm.count == 2


# ──────────────────────────────────────────────────────────────
# §G 預算
# ──────────────────────────────────────────────────────────────


def _run_n(n: int, budget: ex.ConsolidationBudget, llm: FakeLLM, writer: FakeWriter,
           agent_id: str = AGENT, offset: int = 0) -> list:
    results = []
    for index in range(offset, offset + n):
        results.append(
            _exec(
                agent_id=agent_id,
                thread={**THREAD, "thread_id": f"th-budget-{offset}-{index}"},
                evaluation=_evaluation(thread_id=f"th-budget-{offset}-{index}"),
                llm_call=llm,
                fact_writer=writer,
                budget=budget,
            )
        )
    return results


def test_t41_per_agent_budget_refuses_the_25th_call() -> None:
    """T41：per-agent 上限 24 ⇒ 第 25 次 `skipped_budget`（0 呼叫），前 24 次全成功。"""
    budget = ex.ConsolidationBudget(
        now=lambda: NOW, per_agent_limit=24, global_limit=1000
    )
    llm, writer = FakeLLM(), FakeWriter()
    results = _run_n(24, budget, llm, writer)
    assert all(r.status == ex.STATUS_CONSOLIDATED for r in results)
    assert llm.count == 24
    assert budget.count_for(AGENT) == 24
    assert budget.global_count == 24

    overflow = _run_n(1, budget, llm, writer, offset=100)
    assert overflow[0].status == ex.STATUS_SKIPPED_BUDGET
    assert overflow[0].llm_calls == 0
    assert overflow[0].fact is None
    assert llm.count == 24
    assert writer.count == 24
    assert budget.count_for(AGENT) == 24


def test_t42_global_budget_is_independent_of_per_agent() -> None:
    """T42：全域 200 上限獨立可測（注入 `global_limit=3`，不必跑 200 次真呼叫）。"""
    budget = ex.ConsolidationBudget(
        now=lambda: NOW, per_agent_limit=1000, global_limit=3
    )
    llm, writer = FakeLLM(), FakeWriter()
    ok = _run_n(3, budget, llm, writer, agent_id="agent-a")
    assert [r.status for r in ok] == [ex.STATUS_CONSOLIDATED] * 3
    assert budget.global_count == 3
    assert budget.count_for("agent-a") == 3

    # 全域滿了 ⇒ 換一個全新的 agent 也被擋（per-agent 額度還很多）。
    blocked = _run_n(1, budget, llm, writer, agent_id="agent-b", offset=50)
    assert blocked[0].status == ex.STATUS_SKIPPED_BUDGET
    assert budget.count_for("agent-b") == 0
    assert llm.count == 3


def test_t43_budget_none_falls_back_to_process_default() -> None:
    """T43：`budget=None` ⇒ 使用行程級預設預算（成本控制預設開啟）。"""
    default = ex._default_budget()
    assert default.global_count == 0
    result = _exec(llm_call=FakeLLM(), fact_writer=FakeWriter(), budget=None)
    assert result.status == ex.STATUS_CONSOLIDATED
    assert ex._default_budget() is default
    assert default.global_count == 1
    assert default.count_for(AGENT) == 1


def test_t44_budget_day_rollover_resets_counters() -> None:
    """T44：跨日自動歸零（時鐘可注入 ⇒ 決定性可測）。"""
    clock = {"now": NOW}
    budget = ex.ConsolidationBudget(
        now=lambda: clock["now"], per_agent_limit=1, global_limit=1
    )
    llm, writer = FakeLLM(), FakeWriter()
    assert _run_n(1, budget, llm, writer)[0].status == ex.STATUS_CONSOLIDATED
    assert budget.day == "2026-09-17"
    assert _run_n(1, budget, llm, writer, offset=10)[0].status == ex.STATUS_SKIPPED_BUDGET

    clock["now"] = NOW + timedelta(days=1)
    later = _run_n(1, budget, llm, writer, offset=20)
    assert later[0].status == ex.STATUS_CONSOLIDATED
    assert budget.day == "2026-09-18"
    assert budget.global_count == 1


def test_t45_budget_unit_semantics() -> None:
    """T45：`try_consume` 語意（bool、per-agent 隔離、拒絕不計數、上限 0）。"""
    budget = ex.ConsolidationBudget(
        now=lambda: NOW, per_agent_limit=2, global_limit=10
    )
    assert budget.try_consume("a") is True
    assert budget.try_consume("a") is True
    assert budget.try_consume("a") is False  # per-agent 滿
    assert budget.count_for("a") == 2
    assert budget.global_count == 2
    assert budget.try_consume("b") is True  # 不同 agent 不受影響
    assert budget.count_for("b") == 1

    zero = ex.ConsolidationBudget(now=lambda: NOW, per_agent_limit=0, global_limit=10)
    assert zero.try_consume("a") is False
    assert zero.global_count == 0

    # 畸形上限回預設；畸形 agent_id 收斂為空字串鍵（不 raise）。
    weird = ex.ConsolidationBudget(now=lambda: NOW, per_agent_limit=None, global_limit="200")
    assert weird.per_agent_limit == 24
    assert weird.global_limit == 200
    assert weird.try_consume(123) is True  # type: ignore[arg-type]
    assert weird.count_for("") == 1


def test_t46_budget_exhaustion_warns_without_raising(caplog: pytest.LogCaptureFixture) -> None:
    """T46：預算耗盡 ⇒ `logger.warning`（不拋例外），且日誌不含 prompt 內容。"""
    budget = ex.ConsolidationBudget(per_agent_limit=0)
    llm, writer = FakeLLM(), FakeWriter()
    with caplog.at_level(logging.WARNING, logger=ex.logger.name):
        result = _exec(
            evaluation=_evaluation(reflection_prompt=PROMPT_MARKER),
            llm_call=llm,
            fact_writer=writer,
            budget=budget,
        )
    assert result.status == ex.STATUS_SKIPPED_BUDGET
    assert "budget exhausted" in caplog.text
    assert "WARNING" in caplog.text or "skipped_budget" in caplog.text
    assert PROMPT_MARKER not in caplog.text


# ──────────────────────────────────────────────────────────────
# §H 冪等
# ──────────────────────────────────────────────────────────────


def test_t47_duplicate_same_thread_and_status_is_zero_call() -> None:
    """T47：同 `(thread_id, terminal_status)` 第二次 ⇒ `skipped_duplicate`（0 呼叫）。"""
    llm, writer = FakeLLM(), FakeWriter()
    first = _exec(llm_call=llm, fact_writer=writer)
    second = _exec(llm_call=llm, fact_writer=writer)
    assert first.status == ex.STATUS_CONSOLIDATED
    assert second.status == ex.STATUS_SKIPPED_DUPLICATE
    assert second.reason == "already_consolidated"
    assert second.llm_calls == 0
    assert second.fact is None
    assert llm.count == 1
    assert writer.count == 1


def test_t48_dedupe_key_is_scoped_to_thread_and_status() -> None:
    """T48：sentinel 以 `(thread_id, terminal_status)` 為鍵（不同線頭互不影響）。"""
    llm, writer = FakeLLM(), FakeWriter()
    assert _exec(llm_call=llm, fact_writer=writer).status == ex.STATUS_CONSOLIDATED
    other = _exec(
        thread={**THREAD, "thread_id": "th-m2exec-2"},
        evaluation=_evaluation(thread_id="th-m2exec-2"),
        llm_call=llm,
        fact_writer=writer,
    )
    assert other.status == ex.STATUS_CONSOLIDATED
    assert llm.count == 2

    # M1 禁止終態→終態轉移，故「同 thread 換終態」在生產不可達；此處只釘死鍵的形狀。
    assert ex._dedupe_key(THREAD_ID, "completed") != ex._dedupe_key(THREAD_ID, "abandoned")


def test_t49_duplicate_does_not_consume_budget() -> None:
    """T49：重複不消耗預算（先判重、後扣額度）。"""
    budget = ex.ConsolidationBudget(now=lambda: NOW, per_agent_limit=5, global_limit=50)
    llm, writer = FakeLLM(), FakeWriter()
    assert _exec(llm_call=llm, fact_writer=writer, budget=budget).status == ex.STATUS_CONSOLIDATED
    assert _exec(llm_call=llm, fact_writer=writer, budget=budget).status == ex.STATUS_SKIPPED_DUPLICATE
    assert budget.global_count == 1


# ──────────────────────────────────────────────────────────────
# §I 真實 SAGE writer 相容性（唯一真實 I/O；db 只在 tmp）
# ──────────────────────────────────────────────────────────────


def test_t50_real_sage_writer_accepts_produced_fact(tmp_path: Path) -> None:
    """T50：產出的 fact 能被**真實** `MemoryWriter.add_fact` 接受並落地（tmp 內）。"""
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.models import Fact
    from src.memory.sage.writer import MemoryWriter

    db_path = tmp_path / "graph.sqlite"
    store = GraphStore(db_path=db_path)
    writer = MemoryWriter(store, default_session_id="m2exec-test", agent_id=AGENT)
    seen: list = []

    def real_fact_writer(fact: Mapping[str, Any]) -> str:
        seen.append(dict(fact))
        return writer.add_fact(Fact(**dict(fact)))

    result = _exec(llm_call=FakeLLM(), fact_writer=real_fact_writer)
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason
    assert len(seen) == 1

    fact_id = writer.add_fact(Fact(**dict(result.fact or {})))
    assert fact_id, "真實 writer 必須回非空 fact_id（契約 §3.4）"

    stored = store.get_fact(fact_id)
    assert stored is not None
    assert stored.subject == AGENT
    assert stored.predicate == "life_thread_dissolved"
    assert stored.object == "我把簡報做完了，這讓我覺得自己能扛事。"
    assert stored.source == "inference"
    assert stored.origin == "lived_experience"
    assert stored.confidence == 1.0
    assert stored.invalidated_at is None

    # 落地位置必須在 pytest tmp 內（**不得**觸碰生產 `data/**`）。
    assert db_path.exists()
    assert tmp_path in db_path.resolve().parents
    repo_data = (_REPO_ROOT / "data").resolve()
    assert repo_data not in db_path.resolve().parents


def test_t51_fact_payload_is_fact_dataclass_compatible() -> None:
    """T51：fact 的鍵與 `Fact` dataclass 欄位名**完全相容**（`Fact(**fact)` 不炸）。"""
    from src.memory.sage.models import Fact

    result = _exec(llm_call=FakeLLM(), fact_writer=FakeWriter())
    fact = dict(result.fact or {})
    assert fact, "成功路徑必須有 fact"
    valid_fields = {f.name for f in fields(Fact)}
    assert set(fact) <= valid_fields
    built = Fact(**fact)
    assert built.validate() == [], "產出的 fact 必須通過 SAGE schema gate"


def test_t52_data_root_is_isolated_to_tmp() -> None:
    """T52：測試行程的資料根已由 conftest 隔離（0 生產 `data/**` 寫入的前提）。"""
    from src.paths import data_root

    resolved = Path(data_root()).resolve()
    repo_data = (_REPO_ROOT / "data").resolve()
    assert os.environ.get("SOUL_OS_DATA_DIR"), "conftest 必須設定 tmp 資料根"
    assert resolved != repo_data
    assert repo_data not in resolved.parents


# ──────────────────────────────────────────────────────────────
# §J 護欄：0 接線／隱私／離線
# ──────────────────────────────────────────────────────────────


def test_t53_no_production_wiring_in_src_or_scripts() -> None:
    """T53（0 接線護欄）：`src/**` 與 `scripts/**` **沒有任何檔案 import** 本模組。"""
    roots = [_REPO_ROOT / "src", _REPO_ROOT / "scripts"]
    scanned = sum(1 for root in roots if root.exists() for _ in _iter_py_files(root))
    assert scanned > 100, f"掃描覆蓋率過低（只掃到 {scanned} 檔）"
    offenders = _importers_of(MODULE_NAME, roots)
    assert offenders == [], f"本模組不得被生產路徑 import：{offenders}"


def test_t54_only_the_test_file_imports_the_module() -> None:
    """T54：全庫（src/scripts/tests/clients）唯一引用者是本測試檔。"""
    roots = [
        _REPO_ROOT / "src",
        _REPO_ROOT / "scripts",
        _REPO_ROOT / "tests",
        _REPO_ROOT / "clients",
    ]
    importers = [p.name for p in _importers_of(MODULE_NAME, roots)]
    assert importers == [TEST_FILE_NAME], importers


def test_t55_logs_never_leak_prompt_or_narrative(caplog: pytest.LogCaptureFixture) -> None:
    """T55（隱私，規則 9）：日誌只記長度／狀態；prompt 與敘述原文不得進 log。"""
    payload = json.dumps({"dissolution": NARRATIVE_MARKER}, ensure_ascii=False)
    with caplog.at_level(logging.DEBUG, logger=ex.logger.name):
        result = _exec(
            evaluation=_evaluation(reflection_prompt=PROMPT_MARKER),
            llm_call=FakeLLM(payload),
            fact_writer=FakeWriter(),
        )
    assert result.status == ex.STATUS_CONSOLIDATED
    assert PROMPT_MARKER not in caplog.text
    assert NARRATIVE_MARKER not in caplog.text
    assert "prompt_chars=" in caplog.text


def test_t56_failure_logs_never_leak_prompt_or_payload(caplog: pytest.LogCaptureFixture) -> None:
    """T56（隱私，規則 9）：失敗路徑（LLM 例外／解析失敗）同樣不得洩漏原文。"""
    leak = "zzq-leak-marker-zzq"
    with caplog.at_level(logging.DEBUG, logger=ex.logger.name):
        result = _exec(
            evaluation=_evaluation(reflection_prompt=PROMPT_MARKER),
            llm_call=FakeLLM(exc=RuntimeError(leak)),
            fact_writer=FakeWriter(),
        )
        assert result.status == ex.STATUS_LLM_FAILED
        result2 = _exec(
            evaluation=_evaluation(reflection_prompt=PROMPT_MARKER),
            llm_call=FakeLLM(leak),
            fact_writer=FakeWriter(),
        )
        assert result2.status == ex.STATUS_PARSE_FAILED
    assert PROMPT_MARKER not in caplog.text
    assert leak not in caplog.text


def test_t57_module_never_opens_sockets_or_files() -> None:
    """T57（離線安全）：受測模組無任何檔案／socket 開啟 API（純計算 ＋ logging）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    forbidden_attrs = {"open", "urlopen", "connect", "create_connection", "socket"}
    offenders = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs
    ]
    assert offenders == []
    imported = {name.split(".")[0] for name in _imported_module_names(tree)}
    assert imported <= {
        "ast",
        "asyncio",
        "inspect",
        "json",
        "logging",
        "re",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "__future__",
    }, imported


def test_t58_module_uses_only_injected_dependencies() -> None:
    """T58：模組內不存在模組級的 LLM／writer 實例化（一切由參數注入）。"""
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"MemoryWriter", "GraphStore", "set_llm_proxy", "load_persona"})
    assert not (attrs & {"set_llm_proxy", "load_persona", "add_fact", "post_reply_commit"})
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "ConsolidationBudget" in calls  # 只有預設預算會被自行建立
    assert "open" not in calls
