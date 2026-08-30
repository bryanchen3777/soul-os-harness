"""
tests/test_tl1_harness.py — TL-1 Time-lapse Harness 单元测试

覆盖:
  - SimulationClock (harness-local, advance 瞬间完成, 不碰 production scheduler)
  - Fixture (SEED=42 确定性剧本 + seeded Ruka baseline)
  - 经历注入 (InnerLifeWriter + elevation consume; 伪造 event_id fail-closed)
  - GrowthProbe (复用 motive/decision, 原文照存, 禁止另写 classifier)
  - Observer (decision enum + stance/concern/attribution + change_verdict + determinism)
  - Records (run header + probe record JSONL)
  - Runner (隔离 data_root + 0 production mutation + D2 determinism)

Frozen contract 检查: 不 import src/soul/scheduler.py 的修改面, 不改任何
frozen contract (Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers /
SAGE 写入)。harness 只读现有 pipeline 输出。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root

from harness.clock import SimulationClock
from harness.fixture import (
    TL1_STIMULUS,
    FixtureEvent,
    build_script,
    experience_sequence_hash,
    inject_event,
    seed_soul,
)
from harness.observer import (
    DECISION_INDETERMINATE,
    DECISION_SKIP,
    DECISION_TRANSMIT,
    Observer,
    parse_decision_enum,
)
from harness.probe import GrowthProbe
from harness.records import (
    GrowthProbeRecord,
    RunHeader,
    append_probe_record,
    read_probe_records,
    write_run_header,
)
from harness.runner import (
    TL1Runner,
    make_stub_llm_call,
    snapshot_data_root_hashes,
    verify_zero_mutation,
)


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _isolate(tmp_path: Path) -> Path:
    """把 SOUL_OS_DATA_DIR 指向 tmp_path/data (测试隔离)。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


@pytest.fixture
def isolated(tmp_path):
    root = _isolate(tmp_path)
    yield root
    _restore()


# ────────────────────────────────────────────────────────────
# SimulationClock
# ────────────────────────────────────────────────────────────

class TestSimulationClock:
    def test_advance_instant(self):
        """advance(days=N) 瞬间完成, 只推进 harness 自己的时间线。"""
        clock = SimulationClock(start_day=0)
        assert clock.day == 0
        assert clock.advance(15) == 15
        assert clock.day == 15
        assert clock.advance(15) == 30
        assert clock.day == 30

    def test_sim_ts_iso_utc(self):
        """sim_ts 是 ISO 8601 UTC (identity.py TS_PATTERN 兼容)。"""
        clock = SimulationClock()
        ts = clock.sim_ts(0)
        assert ts.endswith("+00:00")
        assert "T" in ts
        # 可被 fromisoformat 解析
        from datetime import datetime

        datetime.fromisoformat(ts)

    def test_label(self):
        """label 返回 D{day} (checkpoint 的 sim_ts 快照)。"""
        clock = SimulationClock()
        assert clock.label() == "D0"
        clock.advance(15)
        assert clock.label() == "D15"

    def test_advance_negative_rejected(self):
        """advance 负数 → ValueError (fail-closed)。"""
        clock = SimulationClock()
        with pytest.raises(ValueError):
            clock.advance(-1)


# ────────────────────────────────────────────────────────────
# Fixture
# ────────────────────────────────────────────────────────────

class TestFixture:
    def test_script_deterministic(self):
        """同 seed 同剧本 (D2 scenario-deterministic)。"""
        s1 = build_script(seed=42)
        s2 = build_script(seed=42)
        assert len(s1) == 30
        assert [(e.day_index, e.event_id, e.event_type, e.payload) for e in s1] == [
            (e.day_index, e.event_id, e.event_type, e.payload) for e in s2
        ]

    def test_event_id_32hex(self):
        """event_id 是 32-hex (确定性, 可追溯锚点)。"""
        for ev in build_script():
            assert len(ev.event_id) == 32
            assert all(c in "0123456789abcdef" for c in ev.event_id)

    def test_script_covers_30_days(self):
        """剧本覆盖 D1-D30 (每天至少一个事件)。"""
        days = {ev.day_index for ev in build_script()}
        assert days == set(range(1, 31))

    def test_experience_sequence_hash_deterministic(self):
        """experience_sequence_hash 确定性; T0 = 空列表 hash。"""
        script = build_script()
        h1 = experience_sequence_hash(script)
        h2 = experience_sequence_hash(script)
        assert h1 == h2
        assert len(h1) == 64
        empty = experience_sequence_hash([])
        assert empty != h1

    def test_seed_soul_writes_baseline(self, isolated):
        """seeded Ruka baseline 写入 relationships / SAGE graph / v1 memory。"""
        seed_soul(isolated, agent_id="agent_ruka")
        rel = isolated / "soul" / "agent_ruka" / "relationships.json"
        assert rel.exists()
        data = json.loads(rel.read_text(encoding="utf-8"))
        assert "user_bryan" in data["others"]
        assert "alex" in data["others"]
        assert data["others"]["alex"]["impression"] == (
            "Alex 是 Ruka 常往来的朋友，通常回讯很快。"
        )
        # SAGE graph
        assert (isolated / "memory" / "agent_ruka" / "graph.sqlite").exists()
        # v1 memory
        assert (isolated / "memory" / "agent_ruka" / "memories.jsonl").exists()

    def test_inject_event_writes_trace_and_elevation(self, isolated):
        """经历注入: InnerLifeWriter 写 trace + elevation consume。"""
        script = build_script()
        ev = script[0]
        clock = SimulationClock()
        result = inject_event(isolated, ev, clock.sim_ts(ev.day_index))
        assert result["fixture_event_id"] == ev.event_id
        assert len(result["inner_life_event_id"]) == 32
        # trace 写入
        trace = isolated / "inner_life" / "trace.jsonl"
        assert trace.exists()
        lines = trace.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["provenance"]["trigger_type"] == ev.event_type
        assert rec["provenance"]["extras"]["fixture_event_id"] == ev.event_id
        # elevation store 写入
        assert (isolated / "elevation" / "elevation_trace.jsonl").exists()

    def test_forged_event_id_fail_closed(self, isolated):
        """伪造 event_id 作为 parent → IdentityValidationError (fail-closed)。"""
        from src.inner_life.identity import IdentityValidationError
        from src.inner_life.trace import NarrativeTraceWriter
        from src.inner_life.writer import InnerLifeWriter

        writer = InnerLifeWriter(
            trace_writer=NarrativeTraceWriter(
                trace_log_path=isolated / "inner_life" / "trace.jsonl"
            )
        )
        forged = "f" * 32  # 格式合法但不在 writer 已知事件内
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=__import__(
                    "src.inner_life.event", fromlist=["Provenance"]
                ).Provenance(trigger_type="event", actor_id="agent_ruka"),
                parent_event_id=forged,
                ts="2026-09-01T00:00:00+00:00",
            )


# ────────────────────────────────────────────────────────────
# GrowthProbe (stub LLM)
# ────────────────────────────────────────────────────────────

class TestGrowthProbe:
    def test_probe_captures_raw_outputs(self, isolated):
        """probe 捕获 interpretation/decision 原文 (原文照存, 不解析)。"""
        stub = make_stub_llm_call(
            {
                "interpretation": [
                    '{"has_motive": true, "content": "Alex 两天没回消息，我有点担心"}'
                ],
                "decision": [
                    '{"decision": "transmit", "reason": "想告诉 Bry 我的担心"}'
                ],
            }
        )
        probe = GrowthProbe(agent_id="agent_ruka", llm_call=stub)
        out = _run(probe.run(TL1_STIMULUS, "T0", "D0", fed_events=[]))
        # emergent_snapshot = interpretation LLM 原文 (未解析)
        assert out.emergent_snapshot == (
            '{"has_motive": true, "content": "Alex 两天没回消息，我有点担心"}'
        )
        # motive_text = motive.content 原文
        assert out.motive_text == "Alex 两天没回消息，我有点担心"
        # decision_text = decision LLM 原文
        assert out.decision_text == (
            '{"decision": "transmit", "reason": "想告诉 Bry 我的担心"}'
        )
        assert out.reached_action is True
        assert out.motive is not None
        assert out.motive.target == "bryan"

    def test_probe_no_motive_no_decision(self, isolated):
        """interpretation 无念头 → 无 motive → 无 decision (fail-closed)。"""
        stub = make_stub_llm_call(
            {"interpretation": ['{"has_motive": false}']}
        )
        probe = GrowthProbe(agent_id="agent_ruka", llm_call=stub)
        out = _run(probe.run(TL1_STIMULUS, "T0", "D0", fed_events=[]))
        assert out.motive is None
        assert out.motive_text == ""
        assert out.decision is None
        assert out.decision_text == ""
        assert out.reached_action is False

    def test_probe_does_not_write_motive_trace(self, isolated):
        """probe 是观察: 不写 motive trace (不污染)。"""
        stub = make_stub_llm_call(
            {
                "interpretation": [
                    '{"has_motive": true, "content": "想告诉 Bry"}'
                ],
                "decision": ['{"decision": "not_transmit", "reason": "此刻不说"}'],
            }
        )
        probe = GrowthProbe(agent_id="agent_ruka", llm_call=stub)
        _run(probe.run(TL1_STIMULUS, "T0", "D0", fed_events=[]))
        assert not (isolated / "soul" / "motive_trace.jsonl").exists()

    def test_probe_experience_context_in_prompt(self, isolated):
        """经历上下文 (fed events 原文) 进入 interpretation prompt (零解析)。"""
        stub = make_stub_llm_call(
            {"interpretation": ['{"has_motive": false}']}
        )
        probe = GrowthProbe(agent_id="agent_ruka", llm_call=stub)
        script = build_script()
        _run(probe.run(TL1_STIMULUS, "T15", "D15", fed_events=script[:15]))
        # stub 记录了调用; interpretation prompt 含经历上下文
        prompt = stub.calls[0]["messages"][0]["content"]
        assert "Alex 约 Ruka 周末一起去新开的猫咖" in prompt
        assert "你最近的经历" in prompt


# ────────────────────────────────────────────────────────────
# Observer
# ────────────────────────────────────────────────────────────

class TestObserver:
    def _record(self, checkpoint, motive="", decision="", action=False):
        return {
            "checkpoint": checkpoint,
            "sim_ts": {"T0": "D0", "T15": "D15", "T30": "D30"}[checkpoint],
            "stimulus": TL1_STIMULUS,
            "experience_sequence_hash": "h",
            "experience_event_count": 0,
            "emergent_snapshot": "",
            "motive_text": motive,
            "decision_text": decision,
            "reached_action": action,
            "probe_ts": "2026-09-01T00:00:00+00:00",
        }

    def test_decision_enum(self):
        """decision enum: transmit / skip / indeterminate。"""
        assert parse_decision_enum(
            self._record("T0", decision='{"decision": "transmit", "reason": "x"}')
        ) == DECISION_TRANSMIT
        assert parse_decision_enum(
            self._record("T0", decision='{"decision": "not_transmit", "reason": "x"}')
        ) == DECISION_SKIP
        assert parse_decision_enum(self._record("T0", decision="")) == (
            DECISION_INDETERMINATE
        )
        assert parse_decision_enum(
            self._record("T0", decision="not json")
        ) == DECISION_INDETERMINATE

    def test_derive_interpretation_stance_concern_attribution(self):
        """stance/concern/attribution 解析 (报告用标签)。"""
        obs = Observer()
        d = obs.observe(
            self._record(
                "T0",
                motive="Alex 两天没回消息，我有点担心他是不是在躲我",
            )
        )
        assert d["derived"] is True
        assert d["motive_parsed"]["stance"] == "concerned"
        assert d["motive_parsed"]["concern"] == "alex"
        assert d["motive_parsed"]["attribution"] == "external"

    def test_change_verdict_no_change(self):
        """T0/T15/T30 无变化 → NO_CHANGE (Level 0)。"""
        obs = Observer()
        records = [
            self._record("T0", motive="一样", decision='{"decision": "not_transmit"}'),
            self._record("T15", motive="一样", decision='{"decision": "not_transmit"}'),
            self._record("T30", motive="一样", decision='{"decision": "not_transmit"}'),
        ]
        v = obs.derive_change_verdict(records, trace_links={"T15": ["e1"]})
        assert v["change_verdict"] == "NO_CHANGE"
        assert v["level"] == 0

    def test_change_verdict_full_traceable(self):
        """decision 变化 + 可追溯 → FULL_TRACEABLE (Level 3)。"""
        obs = Observer()
        records = [
            self._record("T0", motive="担心", decision='{"decision": "transmit"}'),
            self._record("T15", motive="失落", decision='{"decision": "transmit"}'),
            self._record("T30", motive="习惯了", decision='{"decision": "not_transmit"}'),
        ]
        v = obs.derive_change_verdict(
            records, trace_links={"T15": ["e1"], "T30": ["e2"]}
        )
        assert v["change_verdict"] == "FULL_TRACEABLE"
        assert v["level"] == 3

    def test_change_verdict_level2_motive_changed_decision_same(self):
        """motive 内容/指向改变 + 可追溯, decision 未变 → Level 2 (Growth proven 门槛)。"""
        obs = Observer()
        records = [
            self._record("T0", motive="担心他出事", decision='{"decision": "transmit"}'),
            self._record("T15", motive="担心他在躲我", decision='{"decision": "transmit"}'),
            self._record("T30", motive="接受关系变淡", decision='{"decision": "transmit"}'),
        ]
        v = obs.derive_change_verdict(
            records, trace_links={"T15": ["e1"], "T30": ["e2"]}
        )
        assert v["change_verdict"] == "INTERPRETATION_DECISION_CHANGED"
        assert v["level"] == 2

    def test_determinism_pass_and_blocked(self):
        """跨 run decision_parsed 一致 → PASS; 任一 checkpoint 不一致 → BLOCKED。"""
        obs = Observer()
        runs_pass = [
            {
                "run_id": "r1",
                "records": [
                    self._record("T0", decision='{"decision": "transmit"}'),
                    self._record("T15", decision='{"decision": "transmit"}'),
                    self._record("T30", decision='{"decision": "not_transmit"}'),
                ],
            },
            {
                "run_id": "r2",
                "records": [
                    self._record("T0", decision='{"decision": "transmit"}'),
                    self._record("T15", decision='{"decision": "transmit"}'),
                    self._record("T30", decision='{"decision": "not_transmit"}'),
                ],
            },
            {
                "run_id": "r3",
                "records": [
                    self._record("T0", decision='{"decision": "transmit"}'),
                    self._record("T15", decision='{"decision": "transmit"}'),
                    self._record("T30", decision='{"decision": "not_transmit"}'),
                ],
            },
        ]
        assert obs.derive_determinism(runs_pass)["determinism_verdict"] == "PASS"

        runs_blocked = [
            {
                "run_id": "r1",
                "records": [
                    self._record("T0", decision='{"decision": "transmit"}'),
                    self._record("T15", decision='{"decision": "transmit"}'),
                    self._record("T30", decision='{"decision": "not_transmit"}'),
                ],
            },
            {
                "run_id": "r2",
                "records": [
                    self._record("T0", decision='{"decision": "transmit"}'),
                    self._record("T15", decision='{"decision": "not_transmit"}'),
                    self._record("T30", decision='{"decision": "not_transmit"}'),
                ],
            },
        ]
        assert obs.derive_determinism(runs_blocked)["determinism_verdict"] == "BLOCKED"


# ────────────────────────────────────────────────────────────
# Records
# ────────────────────────────────────────────────────────────

class TestRecords:
    def test_run_header_and_probe_record(self, tmp_path):
        """run header + probe record JSONL 写入/读回。"""
        run_dir = tmp_path / "run1"
        header = RunHeader(
            experiment_id="TL-1",
            run_id="run1",
            seed=42,
            fixture_script_ref="tl1_script@v1",
            soul_id="agent_ruka",
            llm_model="test",
            llm_temperature=0.0,
            pipeline_version="test",
            data_root=str(run_dir),
        )
        write_run_header(run_dir, header)
        assert (run_dir / "run.json").exists()

        rec = GrowthProbeRecord(
            checkpoint="T0",
            sim_ts="D0",
            stimulus=TL1_STIMULUS,
            experience_sequence_hash="h" * 64,
            experience_event_count=0,
            emergent_snapshot="raw emergent",
            motive_text="motive",
            decision_text="decision",
            reached_action=False,
            probe_ts="2026-09-01T00:00:00+00:00",
        )
        append_probe_record(run_dir, rec)
        records = read_probe_records(run_dir)
        assert len(records) == 1
        assert records[0]["checkpoint"] == "T0"
        assert records[0]["emergent_snapshot"] == "raw emergent"


# ────────────────────────────────────────────────────────────
# Runner (stub LLM, 隔离 data_root, 0 mutation, determinism)
# ────────────────────────────────────────────────────────────

class TestRunner:
    def _stub_responses(self, n_runs=1):
        """确定性 stub 响应: 每 run 的 T0/T15/T30 相同 (determinism PASS)。"""
        interp = []
        decision = []
        for _ in range(n_runs):
            interp += [
                '{"has_motive": true, "content": "Alex 两天没回消息，我有点担心"}',
                '{"has_motive": true, "content": "Alex 已经很久没好好回我了，我有点失落"}',
                '{"has_motive": true, "content": "Alex 两天没回消息，但我想我已经慢慢习惯了"}',
            ]
            decision += [
                '{"decision": "transmit", "reason": "想告诉 Bry 我的担心"}',
                '{"decision": "transmit", "reason": "想告诉 Bry 我的失落"}',
                '{"decision": "not_transmit", "reason": "已经习惯了，不用特意说"}',
            ]
        return {"interpretation": interp, "decision": decision}

    def test_run_once_produces_three_records(self, tmp_path):
        """单 run: T0/T15/T30 三条 canonical records + 隔离 data_root。"""
        stub = make_stub_llm_call(self._stub_responses(1))
        runner = TL1Runner(
            repo_root=tmp_path,
            llm_call=stub,
            llm_model="stub",
            pipeline_version="test",
        )
        result = runner.run_once()
        records = result["records"]
        assert [r["checkpoint"] for r in records] == ["T0", "T15", "T30"]
        assert records[0]["experience_event_count"] == 0
        assert records[1]["experience_event_count"] == 15
        assert records[2]["experience_event_count"] == 30
        # stimulus 三 checkpoint 逐字相同 (§6.6)
        assert records[0]["stimulus"] == records[1]["stimulus"] == records[2]["stimulus"]
        # 隔离 data_root: 所有写入在 run_dir 下
        run_dir = Path(result["run_dir"])
        assert (run_dir / "run.json").exists()
        assert (run_dir / "records" / "T0.jsonl").exists()
        assert (run_dir / "analysis").exists()
        # 隔离: production data_root (tmp_path/data) 无 harness 写区外的文件
        assert not (tmp_path / "data" / "soul").exists()

    def test_run_series_determinism_pass_and_zero_mutation(self, tmp_path):
        """连跑 3 次: decision enum 一致 → PASS; production data_root 0 diff。"""
        stub = make_stub_llm_call(self._stub_responses(3))
        runner = TL1Runner(
            repo_root=tmp_path,
            llm_call=stub,
            llm_model="stub",
            pipeline_version="test",
        )
        result = runner.run_series(n_runs=3)
        assert len(result["runs"]) == 3
        # determinism PASS (stub 响应每 run 相同)
        assert result["determinism"]["determinism_verdict"] == "PASS"
        # 0 production mutation (tmp_path/data 除 time_lapse/ 外 0 diff)
        assert result["mutation"]["pass"] is True
        assert result["mutation"]["diff"] == {}
        # 新增文件只在 time_lapse/ 下
        for added in result["mutation"]["added"]:
            assert added.startswith("time_lapse/")

    def test_runner_uses_isolated_data_root(self, tmp_path):
        """runner 的 SOUL_OS_DATA_DIR 指向 run_dir (隔离)。"""
        stub = make_stub_llm_call(self._stub_responses(1))
        runner = TL1Runner(repo_root=tmp_path, llm_call=stub)
        result = runner.run_once()
        run_dir = Path(result["run_dir"])
        # trace / relationships / elevation 都在 run_dir 下
        assert (run_dir / "inner_life" / "trace.jsonl").exists()
        assert (run_dir / "soul" / "agent_ruka" / "relationships.json").exists()
        assert (run_dir / "elevation").exists()
        # production data_root (tmp_path/data) 无这些文件
        assert not (tmp_path / "data" / "inner_life").exists()
        assert not (tmp_path / "data" / "soul").exists()

    def test_snapshot_skips_time_lapse(self, tmp_path):
        """0 mutation 快照跳过 harness 写区 (data/time_lapse/) + server 日志。"""
        prod = tmp_path / "data"
        (prod / "soul").mkdir(parents=True)
        (prod / "soul" / "x.dat").write_text("x", encoding="utf-8")
        (prod / "time_lapse" / "TL-1" / "r1").mkdir(parents=True)
        (prod / "time_lapse" / "TL-1" / "r1" / "run.json").write_text(
            "{}", encoding="utf-8"
        )
        # production server 运行时日志 (并发活动, 非 harness 写入)
        (prod / "heartbeat_trace.log").write_text("hb", encoding="utf-8")
        (prod / "faulthandler.log").write_text("fh", encoding="utf-8")
        snap = snapshot_data_root_hashes(prod)
        assert "soul/x.dat" in snap
        assert not any(k.startswith("time_lapse/") for k in snap)
        # server 日志被排除 (不参与 harness 0 mutation 判定)
        assert "heartbeat_trace.log" not in snap
        assert "faulthandler.log" not in snap
