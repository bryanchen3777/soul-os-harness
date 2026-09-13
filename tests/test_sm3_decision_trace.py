"""
tests/test_sm3_decision_trace.py — P1-A-1 四元決策標籤落盤（純 additive 觀測）

P1-A-1 (2026-09-12, ADDITIVE OBSERVABILITY): 把 SM-4 四元決策標籤
(transmit / observe / reflect / do_nothing) 落盤成獨立 append-only JSONL
(data/soul/decision_trace.jsonl)，讓決策分佈從「結構性不可觀測」變成可統計。

驗收錨點（本工單四組）:
  1. schema 斷言 —— append() 後出現一行可 json.loads 的 JSONL，欄位齊全、
     decision ∈ DECISION_ACTIONS、ts 可解析為 ISO 8601 UTC、agent_id 正確。
  2. 四元整合 —— 對 4 個決策值各跑一次 _decision_check：
     (a) trace 檔 decision 標籤正確
     (b) gate 回傳值不變 (transmit→True，其餘三者→False)
     (c) mark_transmitted / mark_rejected 呼叫行為與改動前一致。
  3. fail-closed —— 寫入失敗時 _decision_check 不拋例外且回傳值仍正確。
  4. AST 防線 —— decision_trace.py 與 scheduler 新增區塊：
     0 publish / 0 新定時器 / 0 對 result 的屬性賦值或變更。

Frozen contract 檢查（本測試一併釘住）:
  - DECISION_ACTIONS 四元順序與值域不變。
  - _decision_check 分支條件仍為唯一的 `if result.transmit:`，回傳 True/False 不變。
  - 0 服務重啟 / 0 生產資料寫入（全部落在 SOUL_OS_DATA_DIR 隔離目錄）。
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import json
import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root
from src.soul.decision import DECISION_ACTIONS, DecisionResult
from src.soul.decision_trace import DECISION_TRACE_FILENAME, DecisionTraceStore
from src.soul.motive import (
    MOTIVE_STATUS_REJECTED,
    MOTIVE_STATUS_TRANSMITTED,
    Motive,
    MotiveEngine,
    MotiveTraceStore,
    new_motive_id,
    now_utc_iso,
)
from src.soul.scheduler import SoulScheduler

ROOT = Path(__file__).resolve().parent.parent
TRACE_REL = Path("soul") / DECISION_TRACE_FILENAME

# P1-A-1 §3 凍結 schema（欄位名固定，不得改名）
EXPECTED_FIELDS = {
    "ts", "agent_id", "decision", "transmit", "reason", "motive_id",
    "motive_content", "provenance_ref", "motive_kind", "motive_created_at",
}


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


@pytest.fixture
def isolated_root(tmp_path: Path):
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


class _FakeMotive:
    """帶 kind 的假 motive（驗證 motive_kind 取自 motive.kind，不猜測替代欄位）。"""

    def __init__(self, motive_id: str, kind: str, created_at: str):
        self.motive_id = motive_id
        self.kind = kind
        self.created_at = created_at


class _FakeMotiveNoKind:
    """沒有 kind 屬性的假 motive → motive_kind 必須寫空字串。"""

    def __init__(self, motive_id: str, created_at: str):
        self.motive_id = motive_id
        self.created_at = created_at


def _seed_pending_motive(
    motive_trace_path: Path,
    agent_id: str,
    content: str = "我想告訴你今天的事",
    provenance_ref: str = "evt_p1a1",
) -> str:
    """直接寫一條 pending motive trace 記錄（比照既有 SM-3 測試手法）。"""
    motive_trace_path.parent.mkdir(parents=True, exist_ok=True)
    mid = new_motive_id()
    record = {
        "motive_id": mid,
        "agent_id": agent_id,
        "status": "pending",
        "content": content,
        "target": "bryan",
        "provenance_ref": provenance_ref,
        "created_at": now_utc_iso(),
        "updated_at": now_utc_iso(),
    }
    with open(motive_trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return mid


def _read_trace_lines(path: Path) -> List[Dict[str, Any]]:
    assert path.exists(), f"trace 檔不存在: {path}"
    raw = path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in raw.splitlines() if ln.strip()]


# ────────────────────────────────────────────────────────────
# 1. schema 斷言
# ────────────────────────────────────────────────────────────

class TestSchema:
    """1. append() 落盤 schema（欄位齊全 / 值域 / ISO UTC / agent_id）。"""

    def test_default_path_is_global_single_file_under_data_root(self, isolated_root):
        store = DecisionTraceStore()
        assert store.trace_path == isolated_root / TRACE_REL

    def test_schema_fields_and_iso_utc_ts(self, isolated_root):
        store = DecisionTraceStore()
        motive = _FakeMotive("mot_schema", "goal", "2026-09-12T00:00:00+00:00")
        result = DecisionResult(
            decision="observe",
            transmit=False,
            reason="此刻想先看看",
            motive_id="mot_schema",
            motive_content="<redacted>",
            provenance_ref="evt_p1a1",
        )
        assert store.append(agent_id="agent_yua", motive=motive, result=result) is True

        records = _read_trace_lines(store.trace_path)
        assert len(records) == 1
        rec = records[0]
        # 欄位齊全且 0 多餘欄位（不得寫入 prompt / 系統提示等未列出欄位）
        assert set(rec.keys()) == EXPECTED_FIELDS
        assert rec["decision"] in DECISION_ACTIONS
        assert rec["transmit"] is False
        assert rec["agent_id"] == "agent_yua"
        assert rec["motive_id"] == "mot_schema"
        assert rec["provenance_ref"] == "evt_p1a1"
        assert rec["motive_kind"] == "goal"
        assert rec["motive_created_at"] == "2026-09-12T00:00:00+00:00"
        # ts 可被解析為 ISO 8601 且為 UTC (offset 0)
        ts = datetime.fromisoformat(rec["ts"])
        assert ts.tzinfo is not None
        assert ts.utcoffset() == timedelta(0)

    def test_missing_kind_attribute_writes_empty_string(self, isolated_root):
        """Motive dataclass 無 kind → motive_kind 寫空字串（不猜測替代欄位）。"""
        store = DecisionTraceStore()
        motive = _FakeMotiveNoKind("mot_nokind", "2026-09-12T01:00:00+00:00")
        result = DecisionResult(
            decision="do_nothing", transmit=False, reason="r", motive_id="mot_nokind",
        )
        assert store.append(agent_id="agent_yua", motive=motive, result=result) is True
        rec = _read_trace_lines(store.trace_path)[0]
        assert rec["motive_kind"] == ""
        assert rec["motive_created_at"] == "2026-09-12T01:00:00+00:00"

    def test_append_only_two_rows(self, isolated_root):
        """append-only: 兩次 append → 兩行，既有行不被改寫。"""
        store = DecisionTraceStore()
        motive = _FakeMotiveNoKind("mot_a", now_utc_iso())
        for action in ("transmit", "do_nothing"):
            store.append(
                agent_id="agent_yua",
                motive=motive,
                result=DecisionResult(
                    decision=action,
                    transmit=(action == "transmit"),
                    reason="r",
                    motive_id="mot_a",
                ),
            )
        records = _read_trace_lines(store.trace_path)
        assert len(records) == 2
        assert [r["decision"] for r in records] == ["transmit", "do_nothing"]

    def test_real_motive_dataclass_roundtrip(self, isolated_root):
        """真實 Motive dataclass（無 kind 欄位）可被落盤，不 raise。"""
        store = DecisionTraceStore()
        motive = Motive(
            motive_id="mot_real",
            content="<redacted>",
            target="bryan",
            provenance_ref="evt_real",
            created_at="2026-09-12T02:00:00+00:00",
        )
        result = DecisionResult(
            decision="reflect",
            transmit=False,
            reason="r",
            motive_id=motive.motive_id,
            motive_content=motive.content,
            provenance_ref=motive.provenance_ref,
        )
        assert store.append(agent_id="agent_ruka", motive=motive, result=result) is True
        rec = _read_trace_lines(store.trace_path)[0]
        assert rec["decision"] == "reflect"
        assert rec["agent_id"] == "agent_ruka"
        assert rec["motive_created_at"] == motive.created_at


# ────────────────────────────────────────────────────────────
# 2. 四元整合（_decision_check 真實路徑）
# ────────────────────────────────────────────────────────────

class TestFourWayIntegration:
    """2. 四元決策各跑一次 _decision_check：標籤正確 + 回傳值不變 + mark_* 行為一致。"""

    @pytest.mark.parametrize("decision", DECISION_ACTIONS)
    def test_decision_label_and_gate_return_unchanged(
        self, isolated_root, monkeypatch, decision
    ):
        expected_return = decision == "transmit"
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        mid = _seed_pending_motive(motive_trace_path, "agent_yua")

        calls: Dict[str, List[str]] = {"transmitted": [], "rejected": []}
        orig_transmitted = MotiveEngine.mark_transmitted
        orig_rejected = MotiveEngine.mark_rejected

        def _rec_transmitted(self, motive_id, *a, **kw):
            calls["transmitted"].append(motive_id)
            return orig_transmitted(self, motive_id, *a, **kw)

        def _rec_rejected(self, motive_id, *a, **kw):
            calls["rejected"].append(motive_id)
            return orig_rejected(self, motive_id, *a, **kw)

        async def _fake_decide(self, motive, agent_id, *a, **kw):
            return DecisionResult(
                decision=decision,
                transmit=(decision == "transmit"),
                reason="p1a1-test-reason",
                motive_id=motive.motive_id,
                motive_content=motive.content,
                provenance_ref=motive.provenance_ref,
            )

        monkeypatch.setattr(MotiveEngine, "decide", _fake_decide)
        monkeypatch.setattr(MotiveEngine, "mark_transmitted", _rec_transmitted)
        monkeypatch.setattr(MotiveEngine, "mark_rejected", _rec_rejected)

        scheduler = SoulScheduler(bus=None)
        returned = asyncio.run(scheduler._decision_check("agent_yua"))

        # (b) gate 回傳值不變: transmit → True, observe/reflect/do_nothing → False
        assert returned is expected_return

        # (a) trace 檔 decision 標籤正確（唯一生產呼叫點確實落盤）
        store = DecisionTraceStore()
        records = _read_trace_lines(store.trace_path)
        assert len(records) == 1
        rec = records[0]
        assert set(rec.keys()) == EXPECTED_FIELDS
        assert rec["decision"] == decision
        assert rec["transmit"] is expected_return
        assert rec["agent_id"] == "agent_yua"
        assert rec["motive_id"] == mid
        assert rec["reason"] == "p1a1-test-reason"
        assert rec["motive_kind"] == ""  # 真實 Motive 無 kind

        # (c) mark_transmitted / mark_rejected 呼叫行為與改動前一致
        if decision == "transmit":
            assert calls["transmitted"] == [mid]
            assert calls["rejected"] == []
        else:
            assert calls["transmitted"] == []
            assert calls["rejected"] == [mid]

        # motive 生命週期照常收斂（transmit → transmitted；其餘 → rejected）
        mstore = MotiveTraceStore(trace_path=motive_trace_path)
        latest = mstore._latest_by_motive_id()[mid]
        assert latest["status"] == (
            MOTIVE_STATUS_TRANSMITTED if decision == "transmit"
            else MOTIVE_STATUS_REJECTED
        )

    def test_decision_actions_value_domain_frozen(self):
        """SM-4 四元值域與順序不變（frozen）。"""
        assert tuple(DECISION_ACTIONS) == (
            "transmit", "observe", "reflect", "do_nothing",
        )

    def test_decision_result_frozen_six_fields(self):
        """DecisionResult 仍為 frozen 6 欄（P1-A-1 未擴欄、未加 agent_id / ts）。"""
        import dataclasses
        fields = [f.name for f in dataclasses.fields(DecisionResult)]
        assert fields == [
            "decision", "transmit", "reason", "motive_id",
            "motive_content", "provenance_ref",
        ]
        r = DecisionResult(decision="do_nothing", transmit=False, reason="r", motive_id="m")
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.decision = "transmit"  # type: ignore[misc]


# ────────────────────────────────────────────────────────────
# 3. fail-closed（best-effort，永不 raise）
# ────────────────────────────────────────────────────────────

class TestFailClosed:
    """3. 寫入失敗 → 不拋例外、gate 回傳值仍正確。"""

    def test_store_append_returns_false_when_open_raises(
        self, isolated_root, monkeypatch
    ):
        store = DecisionTraceStore()
        real_open = builtins.open

        def _exploding_open(file, *a, **kw):
            if str(file).endswith(DECISION_TRACE_FILENAME):
                raise OSError("simulated disk failure")
            return real_open(file, *a, **kw)

        monkeypatch.setattr(builtins, "open", _exploding_open)
        motive = _FakeMotiveNoKind("mot_fail", now_utc_iso())
        result = DecisionResult(
            decision="transmit", transmit=True, reason="r", motive_id="mot_fail",
        )
        # 不 raise，回傳 False
        assert store.append(agent_id="agent_yua", motive=motive, result=result) is False
        assert not store.trace_path.exists()

    def test_store_append_returns_false_on_unwritable_path(self, isolated_root):
        """trace_path 的父層是不可建目錄的既有檔案 → False，不 raise。"""
        blocker = isolated_root / "not_a_dir"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("blocker", encoding="utf-8")
        bad = blocker / "sub" / DECISION_TRACE_FILENAME
        store = DecisionTraceStore(trace_path=bad)
        motive = _FakeMotiveNoKind("mot_bad", now_utc_iso())
        result = DecisionResult(
            decision="do_nothing", transmit=False, reason="r", motive_id="mot_bad",
        )
        assert store.append(agent_id="agent_yua", motive=motive, result=result) is False
        assert not bad.exists()

    @pytest.mark.parametrize("decision", ["transmit", "do_nothing"])
    def test_gate_survives_store_exception(
        self, isolated_root, monkeypatch, decision
    ):
        """即使 store.append 本身 raise，_decision_check 仍不拋例外且回傳值正確。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_pending_motive(motive_trace_path, "agent_yua")

        async def _fake_decide(self, motive, agent_id, *a, **kw):
            return DecisionResult(
                decision=decision,
                transmit=(decision == "transmit"),
                reason="r",
                motive_id=motive.motive_id,
                motive_content=motive.content,
                provenance_ref=motive.provenance_ref,
            )

        def _exploding_append(self, **kw):
            raise RuntimeError("simulated trace writer crash")

        monkeypatch.setattr(MotiveEngine, "decide", _fake_decide)
        monkeypatch.setattr(DecisionTraceStore, "append", _exploding_append)

        scheduler = SoulScheduler(bus=None)
        returned = asyncio.run(scheduler._decision_check("agent_yua"))
        assert returned is (decision == "transmit")

    def test_gate_survives_missing_trace_module_symbol(self, isolated_root, monkeypatch):
        """import 失敗（DecisionTraceStore 不可用）也不中斷 gate。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_pending_motive(motive_trace_path, "agent_yua")

        async def _fake_decide(self, motive, agent_id, *a, **kw):
            return DecisionResult(
                decision="transmit", transmit=True, reason="r",
                motive_id=motive.motive_id, motive_content=motive.content,
                provenance_ref=motive.provenance_ref,
            )

        monkeypatch.setattr(MotiveEngine, "decide", _fake_decide)
        monkeypatch.setattr(
            DecisionTraceStore,
            "__init__",
            lambda self, trace_path=None: (_ for _ in ()).throw(
                RuntimeError("simulated constructor failure")
            ),
        )
        scheduler = SoulScheduler(bus=None)
        assert asyncio.run(scheduler._decision_check("agent_yua")) is True


# ────────────────────────────────────────────────────────────
# 4. AST 防線（比照 tests/social/test_sg2_guardrails.py 手法）
# ────────────────────────────────────────────────────────────

_FORBIDDEN_CALL_TOKENS = (
    "publish", "AGENCY_TRIGGER", "handler", "tool_registry", "actuator",
    "call_tool", "send_message", "_fire",
)
# P1-A-1 新增區塊額外禁止碰 motive 生命週期標記（那屬於 gate 既有分支）
_BLOCK_FORBIDDEN_TOKENS = _FORBIDDEN_CALL_TOKENS + ("mark_transmitted", "mark_rejected")
_TIMER_TOKENS = (
    "create_task", "ensure_future", "call_later", "call_soon", "call_at",
    "Timer", "sleep",
)
_MUTATOR_TOKENS = ("setattr", "delattr")


def _call_name(node: ast.Call) -> Optional[str]:
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _audit_no_publish(tree: ast.AST, tokens: tuple = _FORBIDDEN_CALL_TOKENS) -> List[str]:
    issues: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if isinstance(name, str) and any(t in name for t in tokens):
                issues.append(f"禁止调用 {name}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            for nm in names:
                if any(t in nm for t in tokens):
                    issues.append(f"禁止导入 {nm}")
    return issues


def _audit_no_timers(tree: ast.AST) -> List[str]:
    issues: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if isinstance(name, str) and name in _TIMER_TOKENS:
                issues.append(f"禁止定时器/后台任务调用 {name}")
        mod = None
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "asyncio" or a.name.startswith("asyncio."):
                    issues.append("禁止导入异步调度模块")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "asyncio" or mod.startswith("asyncio."):
                issues.append(f"禁止导入异步调度模块 {mod}")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in ("threading", "asyncio"):
                if node.attr in ("Timer", "Thread", "create_task", "ensure_future"):
                    issues.append(f"禁止线程/异步定时器 {node.value.id}.{node.attr}")
    return issues


def _audit_no_result_mutation(tree: ast.AST) -> List[str]:
    """0 個對 result / motive 的屬性賦值或變更（含 subscript / del / setattr）。"""
    issues: List[str] = []
    guarded = ("result", "motive")

    def _targets_names(target: ast.AST) -> bool:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name) and sub.id in guarded:
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, (ast.Attribute, ast.Subscript)) and _targets_names(t):
                    issues.append(f"禁止對 {ast.dump(t)[:60]} 賦值")
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                if _targets_names(t):
                    issues.append("禁止 del result/motive")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if isinstance(name, str) and name in _MUTATOR_TOKENS:
                args = node.args
                if args and isinstance(args[0], ast.Name) and args[0].id in guarded:
                    issues.append(f"禁止 {name}({args[0].id}, ...)")
    return issues


def _audit_no_self_mutation(tree: ast.AST) -> List[str]:
    """0 個 `self.append = ...` 之類的自我篡改（DecisionTraceStore 不重寫 append）。"""
    issues: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                    if t.value.id == "self" and t.attr in ("append", "build_record"):
                        issues.append(f"禁止自我覆寫 self.{t.attr}")
    return issues


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _decision_check_segment() -> str:
    """抽出 scheduler._decision_check 函式本體（縮排已 dedent，可獨立 parse）。"""
    src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    start = src.find("async def _decision_check")
    end = src.find("async def _execute_internal_action")
    assert start != -1 and end != -1 and end > start, "scheduler 定位失敗"
    return textwrap.dedent(src[start:end])


def _p1a1_block_segment() -> str:
    """抽出 P1-A-1 新增區塊（trace 落盤 try/except）。"""
    src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    marker = src.find("# P1-A-1 (2026-09-12, ADDITIVE OBSERVABILITY)")
    assert marker != -1, "P1-A-1 區塊定位失敗"
    start = src.rfind("\n", 0, marker) + 1  # 從行首起算，保留縮排供 dedent
    end = src.find("if result.transmit:", start)
    assert end != -1 and end > start, "P1-A-1 區塊結尾定位失敗"
    return textwrap.dedent(src[start:end])


class TestStaticGuardrails:
    """4. AST 防線：0 publish / 0 新定時器 / 0 對 result 的變更。"""

    def test_decision_trace_no_publish(self):
        assert _audit_no_publish(_parse(ROOT / "src" / "soul" / "decision_trace.py")) == []

    def test_decision_trace_no_timers(self):
        assert _audit_no_timers(_parse(ROOT / "src" / "soul" / "decision_trace.py")) == []

    def test_decision_trace_no_result_mutation(self):
        tree = _parse(ROOT / "src" / "soul" / "decision_trace.py")
        assert _audit_no_result_mutation(tree) == []
        assert _audit_no_self_mutation(tree) == []

    def test_decision_trace_imports_are_read_only(self):
        """decision_trace.py 只 import 標準庫 + data_root（0 生產模組耦合）。"""
        tree = _parse(ROOT / "src" / "soul" / "decision_trace.py")
        allowed = {"__future__", "json", "logging", "datetime", "pathlib", "typing", "src.paths"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name in allowed, f"未預期 import: {a.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert mod in allowed, f"未預期 import: {mod}"

    def test_scheduler_p1a1_block_static(self):
        seg = _p1a1_block_segment()
        tree = ast.parse(seg)
        assert _audit_no_publish(tree, _BLOCK_FORBIDDEN_TOKENS) == []
        assert _audit_no_timers(tree) == []
        assert _audit_no_result_mutation(tree) == []
        # 區塊內確實呼叫 DecisionTraceStore().append(...)
        assert "DecisionTraceStore().append(" in seg
        assert "except Exception" in seg

    def test_scheduler_decision_check_branch_unchanged(self):
        """_decision_check 分支結構仍為唯一 `if result.transmit:` + return True/False。"""
        seg = _decision_check_segment()
        assert seg.count("if result.transmit:") == 1
        assert seg.count("return True") == 1
        assert seg.count("return False") == 3  # F1 無 pending motive + not_transmit + fail-closed except
        assert "engine.mark_transmitted(motive.motive_id)" in seg
        assert "engine.mark_rejected(motive.motive_id)" in seg
        # 新增的 P1-A-1 區塊確實在 `if result.transmit:` 之前（gate 判定後、分支前）
        assert seg.find("# P1-A-1") < seg.find("if result.transmit:")

    def test_scheduler_no_new_loop_or_timer(self):
        """0 新增定時器：既有 30s wake 基線不變（asyncio.sleep(30) 仍為 2）。"""
        src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
        assert src.count("asyncio.sleep(30)") == 2

    def test_scheduler_decision_check_no_publish_or_mutation(self):
        """_decision_check 本體 0 publish / 0 定時器 / 0 對 result·motive 的變更。"""
        tree = ast.parse(_decision_check_segment())
        assert _audit_no_publish(tree) == []
        assert _audit_no_timers(tree) == []
        assert _audit_no_result_mutation(tree) == []

    def test_scheduler_log_lines_keep_legacy_tokens(self):
        """log 行增強必須保留 transmit / not_transmit 字面 token（相容性）。"""
        src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
        assert "] {agent_id} transmit motive=" in src
        assert "] {agent_id} not_transmit motive=" in src
        assert src.count("decision={result.decision} reason={result.reason!r}") == 2

    def test_no_threshold_constant_touched(self):
        """0 門檻常數變更（僅觀測，不碰判定門檻）。"""
        src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
        assert "TENSION_ELIGIBILITY_MIN_CONFIDENCE" not in src
        assert "relational_bands" not in src
