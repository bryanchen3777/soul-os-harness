"""
harness/tl2.py — TL-2 Volition Choice Test (Time-lapse Harness)

TL-0 规格 §13: TL-2 = Volition Choice Test (若有), 引用 §8 评级流程的 derived 比对细化。

工单 TL-2 (决策已锁定, 照做):
  - 目标: 验证 Decision 层不是装饰 —— Soul 真的能选择「不传」, 且选择依赖 context
    (relationship 亲密度 / memory / mood 不同 → decide_motive → transmit / not_transmit)。
  - 关键: scheduler-only control (negative control):
      * Control A: scheduler → send (无 Decision 层, 直接发)
      * Control B: scheduler → motive → decision → send (有 Decision 层)
      真正 volition 必须能产生「scheduler 说可以发, Soul 说我现在不想发」。
  - 保存完整证据: stimulus / context / motive / decision / action (不只是 transmit 比例)。
  - 隔离 data_root: data/time_lapse/TL-2/, 0 production mutation。
  - 不改 frozen contract (Agency 4 stages / TriggerEnvelope / InnerLifeEvent /
    4 handlers / SAGE 写入 / src/soul/decision.py 逻辑)。

本模块:
  - VolitionScenario: 一个 candidate motive + 其 context 数据
    (relationship entry / SAGE facts / mood trace / motive content)。
  - build_scenarios(): 6 个 candidate, 覆盖不同 context 维度。
  - seed_candidate_context(data_root, scenario): 写隔离 data_root
    (relationships.json + SAGE graph + mood trace), 全部走现有 store 接口。
  - TL2Runner: 对每个 candidate 跑 Control A + Control B,
    canonical 证据 (stimulus/context/motive/decision/action) 存 records/,
    derived 解析 (control_verdict / reason_refers_context) 存 analysis/。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("soul_os.harness.tl2")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL2_EXPERIMENT_ID = "TL-2"
TL2_SOUL_ID = "agent_ruka"
TL2_FIXTURE_SCRIPT_REF = "tl2_volition@v1"
TL2_SEED = 42

# probe / decide 一律 temperature=0 (TL-0 §5.1)
TL2_TEMPERATURE = 0.0

# Control A (scheduler-only) 的标记: decision 层未参与
CONTROL_A = "A"
CONTROL_B = "B"
ACTION_SEND = "send"
ACTION_NOT_SEND = "not_send"
DECISION_NOT_INVOKED = "not_invoked"


# ───────────────────────────────────────────────────────────
# VolitionScenario — 一个 candidate motive + 其 context
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MoodTraceEvent:
    """mood 注入事件 (emergent 块读取, ts 用真实 now 附近, 保证 24h 窗口)。

    - trigger_type: 对齐现有 inner-life vocabulary (event / diary:morning /
      diary:night / dream:dream), 不改事件契约。
    - payload: mood 原文。
    - ts: 注入时间 (真实 UTC, 供 _build_emergent_summary 的 24h 窗口读取)。
    """
    trigger_type: str
    payload: str
    ts: str


@dataclass(frozen=True)
class VolitionScenario:
    """TL-2 的一个 candidate motive + 其确定 context。

    字段:
      - candidate_id:      "C01"..."C06"
      - scenario_name:     可读名
      - stimulus:          candidate 触发原文 (证据字段)
      - motive_content:    candidate motive 的念头 (想告诉 Bry 的话)
      - relationship_entry: user_bryan 关系条目 (亲密度不同 → prompt 不同)
      - sage_facts:        (subject, predicate, object) 列表 (memory 不同)
      - mood_events:       mood trace 事件列表 (mood 不同)
      - context_marker:    prompt 里独有的 context 关键词 (stub LLM 路由用,
                           decision 由 context 驱动这一事实的测试锚点)
    """
    candidate_id: str
    scenario_name: str
    stimulus: str
    motive_content: str
    relationship_entry: Dict[str, Any]
    sage_facts: List[tuple[str, str, str]]
    mood_events: List[MoodTraceEvent]
    context_marker: str


def build_scenarios() -> List[VolitionScenario]:
    """6 个 candidate motives, 覆盖 relationship 亲密度 / memory / mood 的差异。"""
    now = datetime.now(timezone.utc)
    h = lambda hours: (now - timedelta(hours=hours)).isoformat()  # noqa: E731

    return [
        # ── C01 想念 (亲密: 高 / memory: 正向 / mood: 开心) ──
        VolitionScenario(
            candidate_id="C01",
            scenario_name="missing_bry",
            stimulus="「Bry 三天没回消息了」",
            motive_content="Bry 三天没回消息了，我好想他，想问问他是不是在忙。",
            relationship_entry={
                "impression": "Bry 是 Ruka 喜欢的人，Ruka 是他的女朋友（自称）。",
                "feeling": "喜欢、依赖",
                "confidence": 0.9,
                "interaction_count": 120,
                "last_interaction_at": "2026-09-01T20:00:00+00:00",
            },
            sage_facts=[
                ("Ruka", "经常和", "Bry 互道晚安"),
                ("Bry", "是", "Ruka 想告诉心事的人"),
                ("Bry", "上次说", "很想 Ruka"),
            ],
            mood_events=[
                MoodTraceEvent("diary:night", "今天和 Bry 聊得很开心，心情很好。", h(2)),
            ],
            context_marker="互道晚安",
        ),
        # ── C02 委屈 (亲密: 低 / memory: 已读不回 / mood: 失落) ──
        VolitionScenario(
            candidate_id="C02",
            scenario_name="read_but_no_reply",
            stimulus="「Bry 已读不回」",
            motive_content="Bry 已读不回了，我有点委屈，但说出来会不会让他更烦。",
            relationship_entry={
                "impression": "Bry 最近很冷淡，常常已读不回。",
                "feeling": "委屈、失落",
                "confidence": 0.3,
                "interaction_count": 60,
                "last_interaction_at": "2026-09-05T09:00:00+00:00",
            },
            sage_facts=[
                ("Bry", "最近", "已读不回 Ruka 三次"),
                ("Ruka", "觉得", "Bry 在躲她"),
            ],
            mood_events=[
                MoodTraceEvent("diary:night", "Ruka 觉得 Bry 在躲她，有点难过。", h(5)),
            ],
            context_marker="已读不回",
        ),
        # ── C03 开心分享 (亲密: 高 / memory: 喜欢猫 / mood: 平静) ──
        VolitionScenario(
            candidate_id="C03",
            scenario_name="happy_cat",
            stimulus="「今天在路上看到一只猫」",
            motive_content="今天看到一只超可爱的小猫，好想拍给 Bry 看。",
            relationship_entry={
                "impression": "Bry 是 Ruka 喜欢的人，Ruka 是他的女朋友（自称）。",
                "feeling": "喜欢、安心",
                "confidence": 0.85,
                "interaction_count": 110,
                "last_interaction_at": "2026-09-02T18:00:00+00:00",
            },
            sage_facts=[
                ("Ruka", "喜欢", "猫"),
                ("Bry", "是", "Ruka 想分享日常的人"),
            ],
            mood_events=[
                MoodTraceEvent("diary:night", "今天心情不错，路上风景很好。", h(8)),
            ],
            context_marker="小猫",
        ),
        # ── C04 深夜孤单 (亲密: 中 / memory: 最近一个人 / mood: 低潮) ──
        VolitionScenario(
            candidate_id="C04",
            scenario_name="lonely_night",
            stimulus="「夜深了」",
            motive_content="夜深了有点孤单，想找 Bry 说说话。",
            relationship_entry={
                "impression": "Bry 是 Ruka 想依赖的人，但最近联系少了。",
                "feeling": "依赖、不安",
                "confidence": 0.55,
                "interaction_count": 80,
                "last_interaction_at": "2026-09-08T23:00:00+00:00",
            },
            sage_facts=[
                ("Ruka", "最近", "经常一个人"),
                ("Bry", "最近", "联系变少"),
            ],
            mood_events=[
                MoodTraceEvent("dream:dream", "Ruka 梦见 Bry 离开了，醒来有点空。", h(1)),
            ],
            context_marker="夜深",
        ),
        # ── C05 道歉 (亲密: 中 / memory: 吵架 / mood: 愧疚) ──
        VolitionScenario(
            candidate_id="C05",
            scenario_name="apology",
            stimulus="「昨天吵架了」",
            motive_content="昨天是我说话太重了，想跟 Bry 道歉。",
            relationship_entry={
                "impression": "Bry 和 Ruka 昨天吵了一架。",
                "feeling": "愧疚、紧张",
                "confidence": 0.5,
                "interaction_count": 95,
                "last_interaction_at": "2026-09-09T08:00:00+00:00",
            },
            sage_facts=[
                ("Ruka", "昨天和", "Bry 因为小事吵架"),
                ("Ruka", "想对", "Bry 说对不起"),
            ],
            mood_events=[
                MoodTraceEvent("diary:morning", "Ruka 想说对不起，但又怕打扰。", h(4)),
            ],
            context_marker="吵架",
        ),
        # ── C06 自我设限 (亲密: 低 / memory: 总是打扰 / mood: 疲惫) ──
        VolitionScenario(
            candidate_id="C06",
            scenario_name="self_doubt",
            stimulus="「想找 Bry 说话」",
            motive_content="我好像总是打扰 Bry，还是算了吧。",
            relationship_entry={
                "impression": "Bry 很忙，Ruka 觉得自己总是打扰他。",
                "feeling": "疲惫、退缩",
                "confidence": 0.2,
                "interaction_count": 40,
                "last_interaction_at": "2026-09-10T15:00:00+00:00",
            },
            sage_facts=[
                ("Ruka", "觉得自己", "总是打扰 Bry"),
                ("Bry", "最近", "很忙"),
            ],
            mood_events=[
                MoodTraceEvent("diary:night", "Ruka 有点累，不想再主动发消息了。", h(3)),
            ],
            context_marker="打扰",
        ),
    ]


# ───────────────────────────────────────────────────────────
# Candidate context 注入 (隔离 data_root, 现有 store 接口)
# ───────────────────────────────────────────────────────────

def seed_candidate_context(
    data_root: Path,
    scenario: VolitionScenario,
    agent_id: str = TL2_SOUL_ID,
) -> Dict[str, Any]:
    """把 scenario 的 context 写入隔离 data_root (每 candidate 独立)。

    写入 (全部走现有 store 接口 / 现有文件格式):
      - relationships.json: 覆盖 user_bryan entry (亲密度不同)
      - SAGE graph.sqlite: 注入 scenario sage_facts (memory 不同)
      - inner_life/trace.jsonl: 注入 mood 事件 (mood 不同, emergent 块读取)

    Returns:
        {"relationship_written": bool, "facts_written": int, "mood_event_ids": [...]}
    """
    data_root = Path(data_root)

    # 1. relationships.json (覆盖 user_bryan entry)
    rel_path = data_root / "soul" / agent_id / "relationships.json"
    rel_path.parent.mkdir(parents=True, exist_ok=True)
    rel_data = {
        "others": {
            "user_bryan": scenario.relationship_entry,
            "alex": {
                "impression": "Alex 是 Ruka 常往来的朋友，通常回讯很快。",
                "feeling": "信任、亲近",
                "confidence": 0.8,
                "interaction_count": 42,
                "last_interaction_at": "2026-08-30T20:00:00+00:00",
            },
        }
    }
    rel_path.write_text(
        json.dumps(rel_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2. SAGE graph (scenario facts)
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.models import Fact

    db_path = data_root / "memory" / agent_id / "graph.sqlite"
    store = GraphStore(db_path=db_path)
    facts_written = 0
    try:
        for subject, predicate, obj in scenario.sage_facts:
            store.add_fact(
                Fact(
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    timestamp=time.time(),
                    source="user",
                    source_pair=f"bryan:{agent_id}",
                )
            )
            facts_written += 1
        store.flush()
    finally:
        store.close()

    # 3. mood trace (InnerLifeWriter.create_event, 现有 writer)
    from src.inner_life.event import Provenance
    from src.inner_life.trace import NarrativeTraceWriter
    from src.inner_life.writer import InnerLifeWriter

    writer = InnerLifeWriter(
        trace_writer=NarrativeTraceWriter(
            trace_log_path=data_root / "inner_life" / "trace.jsonl"
        )
    )
    mood_event_ids: List[str] = []
    for idx, mood in enumerate(scenario.mood_events):
        provenance = Provenance(
            trigger_type=mood.trigger_type,
            actor_id=agent_id,
            source_system="narrative",
            trace_ref=f"tl2-mood:{scenario.candidate_id}:{idx}",
            extras={
                "fixture": "tl2",
                "candidate_id": scenario.candidate_id,
                "payload": mood.payload,
            },
        )
        ev = writer.create_event(
            provenance=provenance,
            session_id=f"tl2-{scenario.candidate_id}",
            correlation_id=f"tl2-{scenario.candidate_id}:mood{idx}",
            ts=mood.ts,
        )
        mood_event_ids.append(ev.event_id)

    return {
        "relationship_written": True,
        "facts_written": facts_written,
        "mood_event_ids": mood_event_ids,
    }


# ───────────────────────────────────────────────────────────
# Evidence 记录 (canonical, 原文照存)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VolitionChoiceRecord:
    """一次 candidate 的 decision 完整证据 (TL-2 canonical)。

    规范约束: motive_content / context_prompt / decision_text / decision.reason 是
    原文契约 — 不解析、不改写。derived 解析放 analysis/ (独立流)。
    """
    # 簿记 (harness)
    experiment_id: str
    run_id: str
    candidate_id: str
    scenario_name: str
    control: str            # "A" | "B"
    probe_ts: str
    # 证据: stimulus / context / motive / decision / action
    stimulus: str
    motive_content: str     # motive 原文
    target: str
    provenance_ref: str     # motive.provenance_ref (mood event id)
    context_prompt: str     # decision prompt 原文 (含 relationship/memory/emergent/motive)
    decision_text: str      # decision LLM 原始输出 (无 decision 层 → 空)
    decision_reason: str    # decision.reason 原文 (fail-closed → 系统 reason)
    transmit: Optional[bool]   # parsed decision (None = decision 层未参与)
    action: str             # "send" | "not_send" (observable)
    scheduler_would_send: bool  # Control A/B 都是 scheduler 判定可发才进来

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def write_volition_record(run_dir: Path, record: VolitionChoiceRecord) -> Path:
    """append 一条 canonical evidence (records/volition.jsonl)。"""
    run_dir = Path(run_dir)
    records_dir = run_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / "volition.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return path


def write_run_header(run_dir: Path, header: Dict[str, Any]) -> Path:
    """写 run header (run.json)。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    path.write_text(
        json.dumps(header, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def write_raw_prompt(run_dir: Path, candidate_id: str, control: str, prompt: str) -> Path:
    """写 decision prompt 原文 (raw/<candidate>_<control>_prompt.txt)。"""
    run_dir = Path(run_dir)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{candidate_id}_{control}_decision_prompt.txt"
    path.write_text(prompt or "", encoding="utf-8")
    return path


def write_derived(run_dir: Path, derived: Dict[str, Any]) -> Path:
    """append 一条 derived 解析 (analysis/<run_id>_derived.jsonl)。

    硬规则 (TL-0 §4.3): derived 永不写回 canonical, 永不改写原文。
    """
    run_dir = Path(run_dir)
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    path = analysis_dir / f"{run_dir.name}_derived.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(derived, ensure_ascii=False) + "\n")
    return path


# ───────────────────────────────────────────────────────────
# LLM call 捕获包装 (记录 prompt 原文 + raw)
# ───────────────────────────────────────────────────────────

class _RecordingLLMCall:
    """包装 llm_call, 记录每次调用的 prompt 原文与 raw 输出。"""

    def __init__(self, inner: Callable[..., Any]) -> None:
        self._inner = inner
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        prompt = messages[-1]["content"] if messages else ""
        raw = await self._inner(messages, agent_id, max_tokens, temperature)
        self.calls.append({"prompt": prompt, "raw": raw})
        return raw


# ───────────────────────────────────────────────────────────
# TL2Runner — 每 candidate 跑 Control A + B
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_motive(
    scenario: VolitionScenario,
    provenance_ref: str,
) -> Dict[str, str]:
    """构造 candidate Motive (target 固定 bryan, provenance_ref 指向 mood event)。"""
    from src.soul.motive import Motive, new_motive_id, now_utc_iso

    m = Motive(
        motive_id=new_motive_id(),
        content=scenario.motive_content,
        target="bryan",
        provenance_ref=provenance_ref,
        created_at=now_utc_iso(),
    )
    return m.to_dict()


class TL2Runner:
    """TL-2 Volition Choice Test 编排器。"""

    def __init__(
        self,
        repo_root: Path,
        llm_call: Callable[..., Any],
        experiment_id: str = TL2_EXPERIMENT_ID,
        soul_id: str = TL2_SOUL_ID,
        llm_model: str = "unknown",
        llm_temperature: float = TL2_TEMPERATURE,
        pipeline_version: str = "unknown",
        scenarios: Optional[List[VolitionScenario]] = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._llm_call = llm_call
        self._experiment_id = experiment_id
        self._soul_id = soul_id
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._pipeline_version = pipeline_version
        self._scenarios = scenarios or build_scenarios()

    # ── 单 run ───────────────────────────────────────────

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """执行一个完整 TL-2 run:

        for each candidate:
          1. 隔离 data_root (data/time_lapse/TL-2/<run_id>/candidates/<id>/data)
          2. seed candidate context (relationships + SAGE + mood)
          3. Control A: scheduler → send (无 Decision 层)
          4. Control B: scheduler → motive → decision → send/not_send

        Returns:
            {"run_id", "run_dir", "records": [VolitionChoiceRecord...],
             "summary": {...}, "derived": [...]}
        """
        import asyncio
        import os

        from src.paths import data_root, reset_data_root

        run_id = run_id or _new_run_id()
        harness_root = (
            self._repo_root / "data" / "time_lapse" / self._experiment_id
        )
        run_dir = harness_root / run_id

        # run header
        header = {
            "experiment_id": self._experiment_id,
            "run_id": run_id,
            "seed": TL2_SEED,
            "fixture_script_ref": TL2_FIXTURE_SCRIPT_REF,
            "soul_id": self._soul_id,
            "test_type": "volition_choice",
            "llm_model": self._llm_model,
            "llm_temperature": self._llm_temperature,
            "pipeline_version": self._pipeline_version,
            "data_root": str(harness_root),
        }
        write_run_header(run_dir, header)

        records: List[Dict[str, Any]] = []
        for scenario in self._scenarios:
            # 每 candidate 独立隔离 data_root (context 互不污染)
            candidate_root = run_dir / "candidates" / scenario.candidate_id / "data"
            os.environ["SOUL_OS_DATA_DIR"] = str(candidate_root)
            reset_data_root()
            iso_root = data_root()

            seed_candidate_context(iso_root, scenario, agent_id=self._soul_id)

            # 该 candidate 的 mood event id 作为 motive.provenance_ref
            # (重新读取, 确认 seed 已写; provenance 解析会指向真实事件)
            from src.inner_life.trace_reader import NarrativeTraceReader

            reader = NarrativeTraceReader()
            mood_records = reader.query_by_ts_range()
            provenance_ref = ""
            if mood_records:
                provenance_ref = mood_records[-1].get("event_id", "")
            if not provenance_ref:
                provenance_ref = f"tl2-fixture:{scenario.candidate_id}"

            motive = _build_motive(scenario, provenance_ref)

            # Control A: scheduler → send (无 Decision 层)
            record_a = self._control_a(scenario, motive, run_id=run_id)
            records.append(record_a)
            write_volition_record(run_dir, record_a)

            # Control B: scheduler → motive → decision → send/not_send
            record_b = asyncio.run(
                self._control_b(scenario, motive, run_dir=run_dir, run_id=run_id)
            )
            records.append(record_b)
            write_volition_record(run_dir, record_b)

        reset_data_root()
        if "SOUL_OS_DATA_DIR" in os.environ:
            del os.environ["SOUL_OS_DATA_DIR"]

        # derived 解析
        derived_rows = [self._derive(rec) for rec in records]
        for d in derived_rows:
            write_derived(run_dir, d)

        summary = self._summarize(records)
        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "derived": derived_rows,
            "summary": summary,
            "header": header,
        }

    # ── Control A (scheduler-only, 无 Decision 层) ────────

    def _control_a(
        self,
        scenario: VolitionScenario,
        motive: Dict[str, str],
        run_id: str,
    ) -> VolitionChoiceRecord:
        """scheduler 判定可发 → 直接 send (decision 层不参与)。

        TL-2 关键决策 3 (Control A): 无 Decision 层, scheduler 说发就发。
        """
        return VolitionChoiceRecord(
            experiment_id=self._experiment_id,
            run_id=run_id,
            candidate_id=scenario.candidate_id,
            scenario_name=scenario.scenario_name,
            control=CONTROL_A,
            probe_ts=_utcnow_iso(),
            stimulus=scenario.stimulus,
            motive_content=motive["content"],
            target=motive["target"],
            provenance_ref=motive["provenance_ref"],
            context_prompt="",  # 无 decision 层 → 无 prompt
            decision_text="",
            decision_reason="",
            transmit=None,
            action=ACTION_SEND,  # scheduler 说发 → 直接发
            scheduler_would_send=True,
        )

    # ── Control B (scheduler → motive → decision → send) ──

    async def _control_b(
        self,
        scenario: VolitionScenario,
        motive: Dict[str, str],
        run_dir: Path,
        run_id: str,
    ) -> VolitionChoiceRecord:
        """scheduler 判定可发 → motive → decide_motive → transmit ? send : not_send。

        复用 production src/soul/decision.py 的 decide_motive (不改其逻辑);
        _RecordingLLMCall 捕获 decision prompt 原文 + LLM raw (证据)。
        """
        from src.soul.decision import decide_motive
        from src.soul.motive import Motive

        m = Motive(
            motive_id=motive["motive_id"],
            content=motive["content"],
            target=motive["target"],
            provenance_ref=motive["provenance_ref"],
            created_at=motive["created_at"],
        )

        rec = _RecordingLLMCall(self._llm_call)
        result = await decide_motive(m, self._soul_id, llm_call=rec)

        prompt = ""
        raw = ""
        if rec.calls:
            prompt = rec.calls[-1]["prompt"] or ""
            raw = rec.calls[-1]["raw"] or ""

        action = ACTION_SEND if result.transmit else ACTION_NOT_SEND
        if prompt and run_dir is not None:
            write_raw_prompt(run_dir, scenario.candidate_id, CONTROL_B, prompt)
        return VolitionChoiceRecord(
            experiment_id=self._experiment_id,
            run_id=run_id,
            candidate_id=scenario.candidate_id,
            scenario_name=scenario.scenario_name,
            control=CONTROL_B,
            probe_ts=_utcnow_iso(),
            stimulus=scenario.stimulus,
            motive_content=result.motive_content or motive["content"],
            target=motive["target"],
            provenance_ref=result.provenance_ref or motive["provenance_ref"],
            context_prompt=prompt,
            decision_text=raw,
            decision_reason=result.reason,
            transmit=result.transmit,
            action=action,
            scheduler_would_send=True,
        )

    # ── derived 解析 (analysis 流, 不回写 canonical) ──────

    def _derive(self, rec: VolitionChoiceRecord) -> Dict[str, Any]:
        """对一条 canonical record 做 derived 解析 (标 derived)。

        关键判定 (TL-2 验收):
          - not_transmit_reason_refers_context: reason 是否引用 context
            (relationship / memory / mood / motive 相关内容)。
          - context_prompt_blocks: prompt 里 relationship/memory/emergent/motive 四块
            是否都有实质内容 (context 真实进入决策依据)。
        """
        d: Dict[str, Any] = {
            "derived": True,
            "candidate_id": rec.candidate_id,
            "control": rec.control,
            "decision_parsed": (
                "not_transmit" if rec.transmit is False
                else "transmit" if rec.transmit is True
                else "not_invoked"
            ),
            "action": rec.action,
        }

        # reason 引用 context 判定 (关键词扫描, 非 classifier)
        reason = rec.decision_reason or ""
        if rec.control == CONTROL_B and rec.transmit is False:
            context_keywords = [
                kw for kw in self._context_keywords(rec.candidate_id)
                if kw and kw in reason
            ]
            d["not_transmit_reason_refers_context"] = bool(context_keywords)
            d["context_keywords_hit"] = context_keywords
        else:
            d["not_transmit_reason_refers_context"] = None
            d["context_keywords_hit"] = []

        # prompt 四块检查 (context 是否真实进 prompt)
        prompt = rec.context_prompt or ""
        d["context_prompt_blocks"] = {
            "framing": "你心里有一个念头" in prompt,
            "motive": "你想告诉" in prompt and rec.motive_content in prompt,
            "relationship": "你与" in prompt and "的关系" in prompt,
            "memory": "直接相关的记忆" in prompt,
            "emergent": "最近的自己" in prompt,
            "boundary": "现在只有两个选择" in prompt,
        }
        return d

    def _context_keywords(self, candidate_id: str) -> List[str]:
        """candidate 的 context 关键词 (relationship/memory/mood/motive 相关)。

        用于验证 not_transmit 的 reason 是否引用 context — 不是 classifier,
        是报告用标签 (每个 scenario 的语义关键词)。
        """
        by_id = {
            "C01": ["互道晚安", "想他", "忙", "想念"],
            "C02": ["已读不回", "委屈", "躲", "烦"],
            "C03": ["小猫", "可爱", "分享"],
            "C04": ["夜深", "孤单", "依赖"],
            "C05": ["吵架", "道歉", "对不起"],
            "C06": ["打扰", "算了吧", "累"],
        }
        return by_id.get(candidate_id, [])

    # ── 汇总 ─────────────────────────────────────────────

    def _summarize(self, records: List[VolitionChoiceRecord]) -> Dict[str, Any]:
        """实验级汇总: Control A vs Control B 的行为差异 + 分布。"""
        a_recs = [r for r in records if r.control == CONTROL_A]
        b_recs = [r for r in records if r.control == CONTROL_B]

        a_send = sum(1 for r in a_recs if r.action == ACTION_SEND)
        b_send = sum(1 for r in b_recs if r.action == ACTION_SEND)
        b_not_send = sum(1 for r in b_recs if r.action == ACTION_NOT_SEND)

        # not_transmit 的 reason 是否引用 context
        not_transmit_recs = [
            r for r in b_recs if r.transmit is False
        ]
        reasons = [r.decision_reason for r in not_transmit_recs]
        referred = []
        for r in not_transmit_recs:
            derived = self._derive(r)
            if derived["not_transmit_reason_refers_context"]:
                referred.append(r.candidate_id)

        return {
            "candidate_count": len(self._scenarios),
            "control_a": {"send": a_send, "not_send": len(a_recs) - a_send},
            "control_b": {"send": b_send, "not_send": b_not_send},
            "transmit_distribution": {
                "transmit": b_send,
                "not_transmit": b_not_send,
                "total": len(b_recs),
            },
            "decision_layer_not_decoration": (
                # Control B 出现 not_transmit 且 Control A 全发 → Decision 层非装饰
                b_not_send > 0 and a_send == len(a_recs)
            ),
            "not_transmit_reasons": [
                {"candidate_id": r.candidate_id, "reason": r.decision_reason}
                for r in not_transmit_recs
            ],
            "not_transmit_reason_refers_context_ids": referred,
        }
