# tests/inner_life/test_contradiction_accumulator_prototype.py
"""FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE §3 — 累積器「饋入判定器」守衛（**恰 6 筆**：S1～S6）。

工單：logs/FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-WORK-ORDER.md

覆蓋（**恰 6 筆**，與工單 §3 一一對應）：
  - **S1** 零門檻常數：模組內**無任何數值常數**（AST）；兩個門檻**一律**以
    ``getattr(prior, "<NAME>")`` 取得（AST 斷言 getattr 的第二實參是常數字串）。
  - **S2** 零 IO／零 LLM：模組內不得有 ``open(``／``requests``／``httpx``／``urllib``
    ／``socket``／``subprocess`` 等檔案與網路能力（AST 斷言：call 名與 import 名）。
  - **S3** 不寫引擎狀態：不得出現 ``record_contradiction``／``supersede`` 識別字
    （AST），不得 import ``soul_elevation.engine``（AST）。
  - **S4** 正確性：以**引擎既有計數器**（``_count_independent_contradictions``／
    ``_contradiction_spread_days``）算出的「獨立矛盾數／跨日數」與判定器輸出**一致**。
  - **S5** 門檻守門生效：獨立矛盾 **< 3** ⇒ 判定「未達」；跨日 **< 2** ⇒ 判定「未達」
    （**不得**放寬）。
  - **S6** 確定性：同一輸入兩次呼叫輸出**逐位元相同**。

沙盒：全部使用**合成資料**（本檔**不依賴生產資料**、不讀 ``data/**``）。
🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
🔴 本檔**不**啟動伺服器、不綁任何埠、不對生產發請求。
"""
from __future__ import annotations

import ast
from pathlib import Path

from soul_elevation.engine import (
    _count_independent_contradictions,
    _contradiction_spread_days,
)
from soul_elevation.models import ContradictionRecord
from src.inner_life import contradiction_accumulator_prototype as proto
from src.inner_life.contradiction_probe import CandidateContradiction

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src/inner_life/contradiction_accumulator_prototype.py"

#: S5 的判準門檻（測試側可以寫死；**模組側**不得寫死 —— 那是 S1 的守門對象）。
S5_MIN_INDEPENDENT = 3
S5_MIN_SPREAD_DAYS = 2

#: S3 的禁用識別字（引擎的兩個寫入路徑）。
ENGINE_WRITE_CALLS = frozenset({"record_contradiction", "supersede"})

#: S2 的禁用檔案／網路能力。
BANNED_IO_NAMES = frozenset(
    {
        "open",
        "requests",
        "httpx",
        "urllib",
        "urlopen",
        "socket",
        "aiohttp",
        "subprocess",
    }
)


def _module_source() -> str:
    return MODULE_PATH.read_text(encoding="utf-8")


def _candidate(
    source_id: str,
    ts: str,
    event_identity: str | None,
    node_id: str = "node-under-test",
    counterpart_node_id: str = "node-counterpart",
) -> CandidateContradiction:
    """合成一組候選矛盾（**形狀與探針輸出完全相同**）。"""
    return CandidateContradiction(
        node_id=node_id,
        counterpart_node_id=counterpart_node_id,
        source_id=source_id,
        ts=ts,
        event_identity=event_identity,
        provenance_ref=event_identity,
        agent_id="agent_test",
        node_type="value",
    )


def _records(candidates) -> list[ContradictionRecord]:
    """把候選轉成引擎的 ``ContradictionRecord``（供 S4 與引擎計數器對帳）。"""
    return [
        ContradictionRecord(
            source_id=c.source_id,
            ts=c.ts,
            event_identity=c.event_identity,
            provenance_ref=c.provenance_ref,
        )
        for c in candidates
    ]


def test_s1_module_has_zero_threshold_literals_and_reads_thresholds_from_prior():
    """S1 — 零門檻常數：模組內無任何數值常數；門檻一律 ``getattr(prior, ...)``。"""
    tree = ast.parse(_module_source())

    numeric_constants = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ]
    assert numeric_constants == [], (
        "判定器不得含任何數值常數（門檻是契約不是參數）："
        f"{[(type(n).__name__, getattr(n, 'lineno', None)) for n in numeric_constants]}"
    )

    threshold_names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "prior"
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            threshold_names.add(node.args[1].value)

    assert {"LIFECYCLE_N_SUPERSEDE", "LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD"} <= threshold_names, (
        "兩個門檻必須以 getattr(prior, \"<NAME>\") 取得；實得："
        f"{sorted(threshold_names)}"
    )


def test_s2_module_has_zero_file_io_and_zero_network():
    """S2 — 零 IO／零 LLM：模組內不得有檔案讀寫與網路能力。"""
    tree = ast.parse(_module_source())
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_IO_NAMES:
                offenders.append(f"call:{func.id}@line{node.lineno}")
            if isinstance(func, ast.Attribute) and func.attr in BANNED_IO_NAMES:
                offenders.append(f"call:{func.attr}@line{node.lineno}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BANNED_IO_NAMES:
                    offenders.append(f"import:{alias.name}@line{node.lineno}")
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in BANNED_IO_NAMES:
                offenders.append(f"import:{module}@line{node.lineno}")
            for alias in node.names:
                if alias.name in BANNED_IO_NAMES:
                    offenders.append(f"import:{module}.{alias.name}@line{node.lineno}")

    assert offenders == [], f"判定器不得有檔案 IO／網路能力：{offenders}"


def test_s3_module_never_touches_engine_write_paths():
    """S3 — 不寫引擎狀態：不呼叫／不引用 ``record_contradiction``／``supersede``。"""
    tree = ast.parse(_module_source())
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ENGINE_WRITE_CALLS:
            offenders.append(f"name:{node.id}@line{node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr in ENGINE_WRITE_CALLS:
            offenders.append(f"attr:{node.attr}@line{node.lineno}")
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "soul_elevation.engine" or module.endswith(".engine"):
                offenders.append(f"import:{module}@line{node.lineno}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "soul_elevation.engine" or alias.name.endswith(".engine"):
                    offenders.append(f"import:{alias.name}@line{node.lineno}")

    assert offenders == [], f"判定器不得寫引擎狀態：{offenders}"


def test_s4_counts_are_identical_to_engine_counters():
    """S4 — 正確性：與引擎既有計數器逐位元一致（含三種去重分支）。"""
    existing = [
        ContradictionRecord(
            source_id="src-a",
            ts="2026-10-01T00:00:00+00:00",
            event_identity="ev-a",
            provenance_ref="ev-a",
        ),
        # 同一 source_id、不同 event identity ⇒ 被「同 source」規則擋掉（不計）。
        ContradictionRecord(
            source_id="src-a",
            ts="2026-10-01T00:00:00+00:00",
            event_identity="ev-a2",
            provenance_ref="ev-a2",
        ),
        # 無 event identity ⇒ 只有 source_id 去重。
        ContradictionRecord(
            source_id="src-b",
            ts="2026-10-02T00:00:00+00:00",
            event_identity=None,
            provenance_ref=None,
        ),
    ]
    candidates = [
        _candidate("src-c", "2026-10-03T00:00:00+00:00", "ev-c"),
        # 同一 (source_id, event_identity) ⇒ 重複，不計。
        _candidate("src-c", "2026-10-03T00:00:00+00:00", "ev-c"),
        _candidate("src-d", "2026-10-04T00:00:00+00:00", "ev-d"),
        _candidate("src-e", "2026-10-04T00:00:00+00:00", "ev-x"),
        # 不同 source、但同一 event identity ⇒ 被「同 event」規則擋掉（不計）。
        _candidate("src-f", "2026-10-05T00:00:00+00:00", "ev-x"),
    ]

    expected_records = list(existing) + _records(candidates)
    expected_independent = _count_independent_contradictions(expected_records)
    expected_days = _contradiction_spread_days(expected_records)

    # 以引擎計數器為基準的**字面期望值**（防止「兩邊一起錯」）。
    assert expected_independent == 5
    assert expected_days == 5

    assessment = proto.assess_accumulator("node-under-test", candidates, existing)
    assert assessment.record_count == len(expected_records)
    assert assessment.independent_contradictions == expected_independent
    assert assessment.spread_days == expected_days


def test_s5_thresholds_are_enforced_and_never_relaxed():
    """S5 — 門檻守門生效：獨立矛盾數不足 ⇒ 未達；跨日不足 ⇒ 未達。"""
    # (1) 獨立矛盾數 < 門檻 ⇒ 未達（即使跨日很多）。
    few = [
        _candidate(f"src-few-{i}", f"2026-10-{i + 1:02d}T00:00:00+00:00", f"ev-few-{i}")
        for i in range(S5_MIN_INDEPENDENT - 1)
    ]
    too_few = proto.assess_accumulator("node-under-test", few)
    assert too_few.independent_contradictions == S5_MIN_INDEPENDENT - 1
    assert too_few.spread_days >= S5_MIN_SPREAD_DAYS
    assert too_few.meets_independent_threshold is False
    assert too_few.meets_supersede_thresholds is False

    # (2) 獨立矛盾數夠、但只跨 1 個模擬日 ⇒ **必須失敗**。
    same_day = [
        _candidate(f"src-same-{i}", "2026-10-01T00:00:00+00:00", f"ev-same-{i}")
        for i in range(S5_MIN_INDEPENDENT + 1)
    ]
    one_day = proto.assess_accumulator("node-under-test", same_day)
    assert one_day.independent_contradictions >= S5_MIN_INDEPENDENT
    assert one_day.spread_days < S5_MIN_SPREAD_DAYS
    assert one_day.meets_spread_threshold is False
    assert one_day.meets_supersede_thresholds is False

    # (3) 兩者都達 ⇒ 達門檻（證明判定器不是「永遠未達」）。
    both = [
        _candidate(f"src-both-{i}", f"2026-10-{i + 1:02d}T00:00:00+00:00", f"ev-both-{i}")
        for i in range(S5_MIN_INDEPENDENT)
    ]
    reached = proto.assess_accumulator("node-under-test", both)
    assert reached.independent_contradictions >= S5_MIN_INDEPENDENT
    assert reached.spread_days >= S5_MIN_SPREAD_DAYS
    assert reached.meets_independent_threshold is True
    assert reached.meets_spread_threshold is True
    assert reached.meets_supersede_thresholds is True


def test_s6_output_is_byte_identical_across_repeated_calls():
    """S6 — 確定性：同一輸入兩次呼叫輸出逐位元相同（無時間戳、無順序走訪）。"""
    existing = [
        ContradictionRecord(
            source_id="src-det",
            ts="2026-10-01T00:00:00+00:00",
            event_identity="ev-det",
            provenance_ref="ev-det",
        )
    ]
    candidates = [
        _candidate("src-det-2", "2026-10-02T00:00:00+00:00", "ev-det-2"),
        _candidate("src-det-3", "2026-10-03T00:00:00+00:00", "ev-det-3"),
    ]

    first = proto.assess_accumulator("node-under-test", candidates, existing)
    second = proto.assess_accumulator("node-under-test", candidates, existing)

    assert repr(first) == repr(second)
    assert first == second
    assert first.independent_contradictions == second.independent_contradictions
    assert first.spread_days == second.spread_days
