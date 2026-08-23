"""
tests/test_work_p1a_execution_shape.py
Soul OS — DSH P1-A：Execution Target Contract（Domain Core 側）驗收測試。

對照 logs/DSH-P1-A-WORK-ORDER.md：
1. ExecutionShape enum 三值 + value 字串（single_shot / multi_stage / continuous）
2. derive_execution_shape 無 dependencies → single_shot
3. derive_execution_shape 有 dependencies → multi_stage（手工構造 WorkObject）
4. derive_execution_shape 模組零 DSH import（schema.py / workflow.py）
5. build_execution_request payload 含 execution_shape 且等於推導值
6. execution_shape 是 capability-neutral 字串，不含 DSH primitive 名
   （subagent / workflow / goal）

執行：pytest tests/test_work_p1a_execution_shape.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.work.schema import (
    ExecutionShape,
    Provenance,
    ResumeState,
    WorkObject,
    WorkState,
)
from src.work.workflow import derive_execution_shape
from src.work_adapter.execution import build_execution_request

REPO_ROOT = Path(__file__).resolve().parent.parent

# 只匹配「實際 import 陳述」，不匹配 docstring 中的文檔引用
# （與 tests/test_work_adapter.py 的 _DSH_IMPORT_RE 同款模式）。
_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)


def _work(dependencies: list[str] | None = None) -> WorkObject:
    """手工構造 WorkObject（WorkObject 必填：objective / state / owner /
    provenance / resume_state）。dependencies 預設 []，可傳入非空清單。"""
    return WorkObject(
        objective="build feature X",
        state=WorkState.IN_PROGRESS,
        owner="chief",
        provenance=Provenance(role="chief", capability="orchestration"),
        resume_state=ResumeState(current_phase=WorkState.IN_PROGRESS),
        dependencies=dependencies or [],
    )


# ─────────────────────────────────────────────
# 1. ExecutionShape enum 三值 + value 字串
# ─────────────────────────────────────────────

class TestExecutionShapeEnum:
    def test_three_members_exist(self):
        """ExecutionShape 恰有三個成員：SINGLE_SHOT / MULTI_STAGE / CONTINUOUS。"""
        assert len(ExecutionShape) == 3
        assert {m.name for m in ExecutionShape} == {
            "SINGLE_SHOT",
            "MULTI_STAGE",
            "CONTINUOUS",
        }

    def test_value_strings(self):
        """value 字串為 single_shot / multi_stage / continuous（capability-neutral）。"""
        assert ExecutionShape.SINGLE_SHOT.value == "single_shot"
        assert ExecutionShape.MULTI_STAGE.value == "multi_stage"
        assert ExecutionShape.CONTINUOUS.value == "continuous"

    def test_is_str_enum(self):
        """str, Enum：serialize 為純字串，payload 可直接承載。"""
        assert isinstance(ExecutionShape.SINGLE_SHOT, str)
        assert ExecutionShape.SINGLE_SHOT == "single_shot"


# ─────────────────────────────────────────────
# 2/3. derive_execution_shape 推導規則
# ─────────────────────────────────────────────

class TestDeriveExecutionShape:
    def test_no_dependencies_single_shot(self):
        """無 dependencies 的 WorkObject → single_shot。"""
        work = _work()  # dependencies 預設 []
        assert work.dependencies == []
        assert derive_execution_shape(work) == ExecutionShape.SINGLE_SHOT

    def test_with_dependencies_multi_stage(self):
        """有 dependencies（["work-2"]）的 WorkObject → multi_stage。

        第一期 dependencies 永為 []（create_work 硬編 []），此分支無觸發路徑，
        用單元測試覆蓋（P1 decomposition §3.3 / work order §2 要求）。
        """
        work = _work(dependencies=["work-2"])
        assert work.dependencies == ["work-2"]
        assert derive_execution_shape(work) == ExecutionShape.MULTI_STAGE

    def test_single_specialist_resume_is_single_shot(self):
        """resume discriminator：blocked 後單一 specialist 再 handoff 一輪完成
        （無 dependencies）→ single_shot，不是 continuous。"""
        work = _work()  # blocked → resume 回 in_progress 的單輪 resume，無 dependencies
        assert derive_execution_shape(work) == ExecutionShape.SINGLE_SHOT


# ─────────────────────────────────────────────
# 4. 零 DSH import
# ─────────────────────────────────────────────

class TestZeroDshImport:
    def test_schema_and_workflow_zero_dsh_import(self):
        """schema.py / workflow.py 的 import 語句零 DSH / Cordis 引用。

        ExecutionShape 三值非 DSH primitive 名（single_shot / multi_stage /
        continuous），Domain Core 零 DSH coupling 永久不變（2A §8.1）。
        """
        offenders = []
        for name in ("schema.py", "workflow.py"):
            src = (REPO_ROOT / "src" / "work" / name).read_text(encoding="utf-8")
            if _DSH_IMPORT_RE.search(src):
                offenders.append(name)
        assert offenders == [], f"src/work/ 出現 DSH import: {offenders}"


# ─────────────────────────────────────────────
# 5/6. build_execution_request payload 承載 execution_shape
# ─────────────────────────────────────────────

class TestBuildExecutionRequestPayload:
    def test_payload_has_execution_shape(self):
        """payload["execution_shape"] 存在且等於 derive_execution_shape(work).value。"""
        work = _work()
        msg = build_execution_request(work, "developer", "artifact.create")
        assert "execution_shape" in msg.payload
        assert msg.payload["execution_shape"] == derive_execution_shape(work).value

    def test_execution_shape_present_in_multi_stage_work(self):
        """有 dependencies 的 Work → payload["execution_shape"] == "multi_stage"。"""
        work = _work(dependencies=["work-2"])
        msg = build_execution_request(work, "developer", "artifact.create")
        assert msg.payload["execution_shape"] == ExecutionShape.MULTI_STAGE.value

    def test_capability_neutral_values(self):
        """execution_shape 三值字串不含 DSH primitive 名（subagent / workflow / goal）。

        adapter 才把 shape 映射到 DSH primitive；Domain Core contract 本身
        capability-neutral（P1-A §3.2）。
        """
        for shape in ExecutionShape:
            value = shape.value
            assert "subagent" not in value, f"{value!r} 含 DSH primitive 名 subagent"
            assert "workflow" not in value, f"{value!r} 含 DSH primitive 名 workflow"
            assert "goal" not in value, f"{value!r} 含 DSH primitive 名 goal"
