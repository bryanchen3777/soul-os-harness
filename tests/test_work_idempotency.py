"""
tests/test_work_idempotency.py
Soul OS — MA-4-R1：Resume idempotency（consume_handoff dedup / effectively-once）。

驗收（對照 logs/DSH-MA-4-R1-WORK-ORDER.md P1）：
- idempotency_key = hash(work_id + role + result_type + artifact_refs/evidence_refs/decision)
- consume_handoff 在 Domain Core 做 dedup：key 已存在於 durable log → skip
  （回傳既有 event），不重複 append
- duplicate / crash-after-write / retry 都有 deterministic behavior（dedup by key）
- resume_state.idempotency_keys 從 durable log 推導（R1：不再是 dead field）
- dedup 只吞 identical retry，不吞不同結果（不同內容 → 都記錄）
- 零 DSH coupling（kernel / store 不 import DSH）

執行：pytest tests/test_work_idempotency.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.work import kernel as kernel_mod
from src.work import store as store_mod
from src.work.bridge import derive_idempotency_key
from src.work.kernel import WorkKernel
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    ResultType,
    WorkEventType,
)
from src.work.store import (
    WorkStore,
    derive_idempotency_key_from_event,
    derive_idempotency_key_from_handoff,
)
from src.work.workflow import WorkflowOrchestrator

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)


def _source_of(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _artifact_handoff(work_id, refs=("sha256:abc",), role="developer", **kw) -> HandoffResult:
    return HandoffResult(
        work_id=work_id, role=role, result_type=ResultType.ARTIFACT,
        artifact_refs=list(refs), status=HandoffStatus.DONE, **kw,
    )


def _evidence_handoff(work_id, refs=("sha256:def",), role="tester", **kw) -> HandoffResult:
    return HandoffResult(
        work_id=work_id, role=role, result_type=ResultType.EVIDENCE,
        evidence_refs=list(refs), status=HandoffStatus.DONE, **kw,
    )


def _decision_handoff(work_id, decision, role="developer", **kw) -> HandoffResult:
    return HandoffResult(
        work_id=work_id, role=role, result_type=ResultType.DECISION,
        decision=decision, status=HandoffStatus.DONE, **kw,
    )


def _orch(tmp_path) -> WorkflowOrchestrator:
    """建 orchestrator（同 data_dir 隔離），create 一個 work 回傳 (orch, work_id)。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")
    return orch, work_id


def _count_event_type(data_dir, event_type: WorkEventType) -> int:
    """直接數 durable log 中某 event_type 的筆數（驗證不重複 append）。"""
    path = Path(data_dir) / "work_events.jsonl"
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if json.loads(line).get("event_type") == event_type.value:
                count += 1
    return count


# ─────────────────────────────────────────────
# 1. idempotency_key 推導（hash(work_id + role + result_type + refs/decision)）
# ─────────────────────────────────────────────

def test_idempotency_key_is_deterministic_hash():
    """相同 handoff → 同 key；key 是 sha256 hex（64 chars）。"""
    k1 = derive_idempotency_key_from_handoff(_artifact_handoff("w1"))
    k2 = derive_idempotency_key_from_handoff(_artifact_handoff("w1"))
    assert k1 == k2
    assert len(k1) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", k1)


def test_idempotency_key_distinguishes_work_id():
    assert derive_idempotency_key_from_handoff(_artifact_handoff("w1")) != \
        derive_idempotency_key_from_handoff(_artifact_handoff("w2"))


def test_idempotency_key_distinguishes_role():
    assert derive_idempotency_key_from_handoff(_artifact_handoff("w1", role="developer")) != \
        derive_idempotency_key_from_handoff(_artifact_handoff("w1", role="tester"))


def test_idempotency_key_distinguishes_result_type():
    assert derive_idempotency_key_from_handoff(_artifact_handoff("w1")) != \
        derive_idempotency_key_from_handoff(_evidence_handoff("w1"))


def test_idempotency_key_distinguishes_artifact_refs():
    assert derive_idempotency_key_from_handoff(_artifact_handoff("w1", refs=("sha256:a",))) != \
        derive_idempotency_key_from_handoff(_artifact_handoff("w1", refs=("sha256:b",)))


def test_idempotency_key_refs_order_independent():
    """ref 順序不影響 key（同內容同 key）。"""
    assert derive_idempotency_key_from_handoff(_artifact_handoff("w1", refs=("a", "b"))) == \
        derive_idempotency_key_from_handoff(_artifact_handoff("w1", refs=("b", "a")))


def test_idempotency_key_decision_dict_order_independent():
    """decision dict 鍵順序不影響 key。"""
    assert derive_idempotency_key_from_handoff(_decision_handoff("w1", {"a": 1, "b": 2})) == \
        derive_idempotency_key_from_handoff(_decision_handoff("w1", {"b": 2, "a": 1}))


def test_idempotency_key_decision_content_matters():
    assert derive_idempotency_key_from_handoff(_decision_handoff("w1", {"choice": "x"})) != \
        derive_idempotency_key_from_handoff(_decision_handoff("w1", {"choice": "y"}))


def test_idempotency_key_from_event_matches_handoff(tmp_path):
    """event 側推導與 handoff 側推導一致（dedup 命中的前提）。"""
    orch, work_id = _orch(tmp_path)
    handoff = _artifact_handoff(work_id)
    event = orch.consume_handoff(handoff)
    assert derive_idempotency_key_from_event(event) == derive_idempotency_key_from_handoff(handoff)
    assert derive_idempotency_key_from_event(event) == derive_idempotency_key(
        work_id=work_id, role="developer", result_type="artifact",
        refs=["sha256:abc"],
    )


def test_bridge_derive_is_pure_function():
    """bridge.derive_idempotency_key 是純函式（language-neutral canonical formula）。"""
    k = derive_idempotency_key(
        work_id="w", role="developer", result_type="artifact", refs=["r1", "r2"],
    )
    assert len(k) == 64
    assert k == derive_idempotency_key(
        work_id="w", role="developer", result_type="artifact", refs=["r2", "r1"],
    )
    assert k != derive_idempotency_key(
        work_id="w", role="developer", result_type="artifact", refs=["r1"],
    )


# ─────────────────────────────────────────────
# 2. consume_handoff dedup（duplicate / retry）
# ─────────────────────────────────────────────

def test_consume_handoff_dedups_duplicate(tmp_path):
    """相同 handoff 兩次 → 只 append 一筆；第二次 skip 回傳既有 event。"""
    orch, work_id = _orch(tmp_path)
    handoff = _artifact_handoff(work_id)
    e1 = orch.consume_handoff(handoff)
    e2 = orch.consume_handoff(handoff)

    assert e1.event_type == WorkEventType.ARTIFACT_PRODUCED
    # 回傳既有 event（同內容，非新 append）
    assert e1.model_dump() == e2.model_dump()
    # durable log 只有一筆 artifact_produced
    assert _count_event_type(tmp_path, WorkEventType.ARTIFACT_PRODUCED) == 1
    # fold 只累積一筆 artifact
    work = orch.synthesize(work_id)
    assert work.artifacts == [{"refs": ["sha256:abc"]}]


def test_consume_handoff_dedup_evidence(tmp_path):
    """evidence handoff 重複 → dedup，不重複 append。"""
    orch, work_id = _orch(tmp_path)
    handoff = _evidence_handoff(work_id)
    e1 = orch.consume_handoff(handoff)
    e2 = orch.consume_handoff(handoff)
    assert e1.model_dump() == e2.model_dump()
    assert _count_event_type(tmp_path, WorkEventType.EVIDENCE_PRODUCED) == 1
    assert orch.synthesize(work_id).evidence == [{"refs": ["sha256:def"]}]


def test_consume_handoff_dedup_decision(tmp_path):
    """decision handoff 重複 → dedup，不重複 append。"""
    orch, work_id = _orch(tmp_path)
    handoff = _decision_handoff(work_id, {"choice": "use sqlite"})
    e1 = orch.consume_handoff(handoff)
    e2 = orch.consume_handoff(handoff)
    assert e1.model_dump() == e2.model_dump()
    assert _count_event_type(tmp_path, WorkEventType.DECISION_MADE) == 1
    assert orch.synthesize(work_id).decisions == [{"choice": "use sqlite"}]


def test_consume_handoff_distinct_results_both_recorded(tmp_path):
    """不同內容 → 不同 key → 都記錄（dedup 只吞 identical retry）。"""
    orch, work_id = _orch(tmp_path)
    orch.consume_handoff(_artifact_handoff(work_id, refs=("sha256:a",)))
    orch.consume_handoff(_artifact_handoff(work_id, refs=("sha256:b",)))
    orch.consume_handoff(_evidence_handoff(work_id, refs=("sha256:e",)))
    assert _count_event_type(tmp_path, WorkEventType.ARTIFACT_PRODUCED) == 2
    assert _count_event_type(tmp_path, WorkEventType.EVIDENCE_PRODUCED) == 1
    work = orch.synthesize(work_id)
    assert work.artifacts == [{"refs": ["sha256:a"]}, {"refs": ["sha256:b"]}]
    assert work.evidence == [{"refs": ["sha256:e"]}]


def test_consume_handoff_dedup_cross_work_no_leak(tmp_path):
    """不同 work_id → 不同 key → 不互相 dedup。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    w1 = orch.create_work("work A", "chief")
    w2 = orch.create_work("work B", "chief")
    h1 = _artifact_handoff(w1)
    h2 = _artifact_handoff(w2)  # 相同 refs / role / result_type，但不同 work
    orch.consume_handoff(h1)
    e2 = orch.consume_handoff(h2)
    assert e2.event_type == WorkEventType.ARTIFACT_PRODUCED  # w2 不是 dedup
    assert orch.synthesize(w1).artifacts == [{"refs": ["sha256:abc"]}]
    assert orch.synthesize(w2).artifacts == [{"refs": ["sha256:abc"]}]


# ─────────────────────────────────────────────
# 3. crash-after-write / retry（restart 後 dedup 仍生效）
# ─────────────────────────────────────────────

def test_crash_after_write_retry_is_effectively_once(tmp_path):
    """crash-after-write：restart 後重試相同 handoff → dedup，不重複 append。"""
    orch1, work_id = _orch(tmp_path)
    handoff = _artifact_handoff(work_id)
    orch1.consume_handoff(handoff)
    # 寫入已完成（durable log 一筆）
    assert _count_event_type(tmp_path, WorkEventType.ARTIFACT_PRODUCED) == 1

    # restart：新 kernel / orchestrator，同 data_dir（in-process 狀態不存活）
    orch2 = WorkflowOrchestrator(data_dir=tmp_path)
    e2 = orch2.consume_handoff(handoff)
    assert e2.event_type == WorkEventType.ARTIFACT_PRODUCED
    # 不重複 append（effectively-once）
    assert _count_event_type(tmp_path, WorkEventType.ARTIFACT_PRODUCED) == 1
    work = orch2.synthesize(work_id)
    assert work.artifacts == [{"refs": ["sha256:abc"]}]


def test_retry_after_restart_returns_same_event_content(tmp_path):
    """restart 後 retry 回傳的既有 event 與首次 append 內容一致。"""
    orch1, work_id = _orch(tmp_path)
    handoff = _evidence_handoff(work_id)
    e1 = orch1.consume_handoff(handoff)

    orch2 = WorkflowOrchestrator(data_dir=tmp_path)
    e2 = orch2.consume_handoff(handoff)
    assert e2.model_dump() == e1.model_dump()
    assert _count_event_type(tmp_path, WorkEventType.EVIDENCE_PRODUCED) == 1


def test_dedup_deterministic_across_repeated_retries(tmp_path):
    """重複 retry 多次 → durable log 始終只有一筆（deterministic）。"""
    orch, work_id = _orch(tmp_path)
    handoff = _artifact_handoff(work_id)
    for _ in range(5):
        orch.consume_handoff(handoff)
    assert _count_event_type(tmp_path, WorkEventType.ARTIFACT_PRODUCED) == 1


# ─────────────────────────────────────────────
# 4. resume_state.idempotency_keys（R1：從 durable log 推導）
# ─────────────────────────────────────────────

def test_resume_state_idempotency_keys_derived(tmp_path):
    """fold 從 durable log 推導 idempotency_keys（不再是 dead field）。"""
    orch, work_id = _orch(tmp_path)
    orch.consume_handoff(_artifact_handoff(work_id, refs=("sha256:a",)))
    orch.consume_handoff(_evidence_handoff(work_id, refs=("sha256:e",)))
    work = orch.synthesize(work_id)

    keys = work.resume_state.idempotency_keys
    assert derive_idempotency_key_from_handoff(_artifact_handoff(work_id, refs=("sha256:a",))) in keys
    assert derive_idempotency_key_from_handoff(_evidence_handoff(work_id, refs=("sha256:e",))) in keys
    assert len(keys) == 2


def test_resume_state_idempotency_keys_deduped(tmp_path):
    """dedup 後 fold 的 idempotency_keys 不重複（durable log 只有一筆）。"""
    orch, work_id = _orch(tmp_path)
    handoff = _artifact_handoff(work_id)
    orch.consume_handoff(handoff)
    orch.consume_handoff(handoff)  # dedup
    work = orch.synthesize(work_id)
    assert len(work.resume_state.idempotency_keys) == 1


def test_resume_state_idempotency_keys_survive_restart(tmp_path):
    """restart 後 fold 仍推導出同一組 idempotency_keys（durable truth）。"""
    orch1, work_id = _orch(tmp_path)
    handoff = _artifact_handoff(work_id)
    orch1.consume_handoff(handoff)

    orch2 = WorkflowOrchestrator(data_dir=tmp_path)
    work2 = orch2.synthesize(work_id)
    assert work2.resume_state.idempotency_keys == [
        derive_idempotency_key_from_handoff(handoff)
    ]


def test_no_handoff_no_idempotency_keys(tmp_path):
    """無 handoff event → idempotency_keys 為空（不誤報）。"""
    orch, work_id = _orch(tmp_path)
    work = orch.synthesize(work_id)
    assert work.resume_state.idempotency_keys == []


# ─────────────────────────────────────────────
# 5. Domain Core 強制（Adapter 不參與）+ 零 DSH coupling
# ─────────────────────────────────────────────

def test_dedup_enforced_in_kernel_via_durable_log(tmp_path):
    """dedup 查詢直接走 durable log（WorkStore），不依賴 in-process 狀態。"""
    kernel = WorkKernel(data_dir=tmp_path)
    orch = WorkflowOrchestrator(kernel=kernel)
    work_id = orch.create_work("x", "chief")
    handoff = _artifact_handoff(work_id)
    orch.consume_handoff(handoff)

    # 直接對 kernel 的 store 查 key：durable log 已可命中（不需重放）
    key = derive_idempotency_key_from_handoff(handoff)
    found = kernel._store.handoff_event_by_key(work_id, key)
    assert found is not None
    assert found.event_type == WorkEventType.ARTIFACT_PRODUCED


def test_kernel_and_store_do_not_import_dsh():
    """kernel.py / store.py 不得 import 任何 DSH / Cordis type。"""
    for mod in (kernel_mod, store_mod):
        assert not _DSH_IMPORT_RE.search(_source_of(mod)), (
            f"{mod.__name__} 不得 import DSH / Cordis type"
        )
