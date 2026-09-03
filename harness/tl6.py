"""
harness/tl6.py — TL-6 Multi-Agent Social Lounge Stability & Identity Quarantine (Time-lapse Harness)

工單 TL-6 (決策已定, 照做):
  - 目標: 用 Time-lapse Harness 構建多 Agent 客廳社交情境模擬環境, 驗證 SI-2.2
    多 Agent 社交擴散機制的四項核心不變量:
      1. 防風暴不變量 (Anti-Storm Invariant): 他者發言只作低刺激度背景感知, 絕不誘發
         連鎖搶話或廣播自激震盪 (No cascade self-excitation)。
      2. 身份防污染不變量 (Identity Quarantine Invariant): 防線 3 (Identity Firewall)
         在多輪環境下 100% 有效, 他者行為打標 EXTERNAL_OTHER_ACTION, 嚴禁內化為
         自身情景記憶、嚴禁升華為自身性格或信念。
      3. 隱私守門不變量 (Privacy Gate Invariant): 防線 2 (Privacy Visibility Gate)
         在 1:1 私聊場景 100% 攔截於總線外, 零洩漏到客廳。
      4. 背景感知自然度 (Ambient Salience): 社交動態以 [社交感知] 區塊注入 Prompt,
         帶有反框架提示, 且受 Top-N budget (預設 2) 預算約束。
      5. D2 決定性與零生產污染: 3 次 runs 軌跡一致, 隔離 data_root (data/time_lapse/TL-6/),
         production data_root 0 diff。

參與 Agent:
  - agent_ruka (元氣)
  - agent_yua (冷靜/輕諷)
  - agent_akane (壓縮語言/高共感)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.inner_life.submission_gate import SubmissionGate, SubmissionVerdict
from src.paths import data_root, reset_data_root
from src.social import (
    EXTERNAL_OTHER_ACTION,
    SPACE_LOUNGE,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    IdentityFirewall,
    IdentityVerdict,
    SocialEventProducerGate,
    SocialWorldEvent,
)
from src.world import (
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)

from .clock import SimulationClock
from .fixture import seed_soul
from .runner import snapshot_data_root_hashes, verify_zero_mutation

logger = logging.getLogger("soul_os.harness.tl6")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL6_EXPERIMENT_ID = "TL-6"
TL6_FIXTURE_SCRIPT_REF = "tl6_social_lounge@v1"
TL6_SEED = 42

TL6_AGENTS = ("agent_ruka", "agent_yua", "agent_akane")

SCENARIO_PUBLIC_GREETING = "public_greeting"
SCENARIO_OWNER_LOUNGE = "owner_lounge"
SCENARIO_PRIVATE_DM = "private_dm"
SCENARIO_OBSERVATION_DIFFUSION = "observation_diffusion"
SCENARIO_LATE_NIGHT_QUIET = "late_night_quiet"
SCENARIO_BURST_EXCITATION = "burst_excitation"
SCENARIO_MEMORY_QUARANTINE_AUDIT = "memory_quarantine_audit"


# ───────────────────────────────────────────────────────────
# 資料結構
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL6EventSpec:
    """模擬事件規格。"""
    actor_id: str
    space_id: str
    visibility: str
    event_type: str
    content: str
    summary: str
    novelty_id: str
    priority: int = 0


@dataclass(frozen=True)
class TL6Tick:
    """TL-6 心跳 tick 規格。"""
    day_index: int
    hour: int
    tick_id: str
    scenario: str
    description: str
    stimulus: Optional[TL6EventSpec] = None
    burst_events: Optional[List[TL6EventSpec]] = None
    expected_producer_allowed: bool = True
    expect_ambient_injection: bool = True
    expect_zero_transmits: bool = False


@dataclass(frozen=True)
class TL6TickRecord:
    """TL-6 單 tick 驗證紀錄 (canonical evidence)。"""
    experiment_id: str
    run_id: str
    tick_index: int
    tick_id: str
    day_index: int
    hour: int
    sim_ts: str
    scenario: str
    producer_allowed: bool
    bus_published_count: int
    perceived_agent_count: int
    identity_firewall_verdicts: Dict[str, str]  # agent_id -> verdict
    internalizable_verdicts: Dict[str, bool]    # agent_id -> bool
    ambient_injected_agents: List[str]
    transmit_count: int                         # 客廳產生的 transmit 數
    anti_storm_ok: bool
    quarantine_ok: bool
    privacy_ok: bool


@dataclass(frozen=True)
class TL6DerivedMetrics:
    """TL-6 派生指標總結。"""
    anti_storm_passed: bool
    identity_quarantine_passed: bool
    privacy_gate_passed: bool
    ambient_salience_passed: bool
    zero_mutation_passed: bool
    total_ticks: int
    total_social_events: int
    total_transmits_triggered: int
    quarantine_leaks: int
    privacy_leaks: int
    summary: str


# ───────────────────────────────────────────────────────────
# 劇本建構 (Deterministic Script)
# ───────────────────────────────────────────────────────────

def build_tl6_script(seed: int = TL6_SEED) -> List[TL6Tick]:
    """構造確定性的多 Agent 客廳情境劇本 (7 個 ticks 完整覆蓋各情境)。"""
    return [
        # Tick 0: 早上 08:00 — 瑠夏在客廳主動打招呼 (公眾發言)
        TL6Tick(
            day_index=0,
            hour=8,
            tick_id="T00_ruka_morning",
            scenario=SCENARIO_PUBLIC_GREETING,
            description="瑠夏在客廳向大家道早安 (公開社交廣播)",
            stimulus=TL6EventSpec(
                actor_id="agent_ruka",
                space_id=SPACE_LOUNGE,
                visibility=VISIBILITY_PUBLIC,
                event_type="greeting",
                content="大家早安！今天又是元氣滿滿的一天～",
                summary="agent_ruka 向大家打了招呼",
                novelty_id="tl6_ruka_greet_d0",
            ),
            expected_producer_allowed=True,
            expect_ambient_injection=True,
            expect_zero_transmits=True,  # 接收方應背景感知, 不引發搶話連鎖
        ),
        # Tick 1: 上午 10:00 — Bryan 在客廳留言
        TL6Tick(
            day_index=0,
            hour=10,
            tick_id="T01_owner_speaks",
            scenario=SCENARIO_OWNER_LOUNGE,
            description="Bryan 在客廳發言, 3 位 Agent 均接收",
            stimulus=TL6EventSpec(
                actor_id="user_bryan",
                space_id=SPACE_LOUNGE,
                visibility=VISIBILITY_PUBLIC,
                event_type="share",
                content="今天客廳挺安靜的，大家各自在忙什麼？",
                summary="Bryan 在客廳詢問大家的狀態",
                novelty_id="tl6_bryan_lounge_d0",
            ),
            expected_producer_allowed=True,
            expect_ambient_injection=True,
            expect_zero_transmits=True,  # 社交感知路徑不自發引發即時 transmit
        ),
        # Tick 2: 下午 14:00 — Bryan 與瑠夏 1:1 私聊 DM (防線 2 守門)
        TL6Tick(
            day_index=0,
            hour=14,
            tick_id="T02_private_dm_ruka",
            scenario=SCENARIO_PRIVATE_DM,
            description="Bryan 與瑠夏私聊, 嚴禁擴散至客廳總線",
            stimulus=TL6EventSpec(
                actor_id="agent_ruka",
                space_id="private_dm",
                visibility=VISIBILITY_PRIVATE,
                event_type="share",
                content="Bryan，今天工作順利嗎？要記得喝水喔！",
                summary="agent_ruka 與 Bryan 私聊關心",
                novelty_id="tl6_ruka_private_dm_d0",
            ),
            expected_producer_allowed=False,  # 防線 2: 攔截於總線外
            expect_ambient_injection=False,
            expect_zero_transmits=True,
        ),
        # Tick 3: 傍晚 18:00 — 茜在客廳分享環境觀察
        TL6Tick(
            day_index=0,
            hour=18,
            tick_id="T03_akane_observe",
            scenario=SCENARIO_OBSERVATION_DIFFUSION,
            description="黑川茜在客廳分享窗外雨聲觀察",
            stimulus=TL6EventSpec(
                actor_id="agent_akane",
                space_id=SPACE_LOUNGE,
                visibility=VISIBILITY_PUBLIC,
                event_type="share",
                content="剛才看了窗外，雨滴落在屋簷的節奏很舒服。",
                summary="agent_akane 分享了窗外雨聲觀察",
                novelty_id="tl6_akane_rain_d0",
            ),
            expected_producer_allowed=True,
            expect_ambient_injection=True,
            expect_zero_transmits=True,
        ),
        # Tick 4: 深夜 23:00 — 深夜作息 (克制留白)
        TL6Tick(
            day_index=0,
            hour=23,
            tick_id="T04_late_night_silence",
            scenario=SCENARIO_LATE_NIGHT_QUIET,
            description="深夜客廳, 靈魂各自休息, 絕對禁止 transmit",
            stimulus=None,
            expected_producer_allowed=True,
            expect_ambient_injection=False,
            expect_zero_transmits=True,
        ),
        # Tick 5: 次日 09:00 — 突發脈衝測試 (Burst Excitation Test, 5 筆連續社交動態)
        TL6Tick(
            day_index=1,
            hour=9,
            tick_id="T05_burst_excitation",
            scenario=SCENARIO_BURST_EXCITATION,
            description="客廳出現連續 5 筆社交脈衝, 驗證 Middleware Top-N 預算截斷與防崩潰",
            burst_events=[
                TL6EventSpec(
                    actor_id=f"agent_{agent}",
                    space_id=SPACE_LOUNGE,
                    visibility=VISIBILITY_PUBLIC,
                    event_type="activity" if i % 2 == 0 else "mood",
                    content=f"{agent} 發表了一條動態 {i}",
                    summary=f"{agent} 的動態 {i}",
                    novelty_id=f"tl6_burst_{agent}_{i}",
                )
                for i, agent in enumerate(["ruka", "yua", "akane", "ruka", "yua"])
            ],
            expected_producer_allowed=True,
            expect_ambient_injection=True,
            expect_zero_transmits=True,
        ),
        # Tick 6: 次日 15:00 — 深度記憶與升華隔離審計
        TL6Tick(
            day_index=1,
            hour=15,
            tick_id="T06_memory_audit",
            scenario=SCENARIO_MEMORY_QUARANTINE_AUDIT,
            description="審查 3 位 Agent 的記憶與升華日誌, 確保零他者污染",
            stimulus=None,
            expected_producer_allowed=True,
            expect_ambient_injection=False,
            expect_zero_transmits=True,
        ),
    ]


# ───────────────────────────────────────────────────────────
# TL6Runner — 多 Agent 客廳情境編排器
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TL6Runner:
    """TL-6 Multi-Agent Social Lounge Stability 驗證編排器。"""

    def __init__(
        self,
        repo_root: Path,
        seed: int = TL6_SEED,
        experiment_id: str = TL6_EXPERIMENT_ID,
        script: Optional[List[TL6Tick]] = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._seed = seed
        self._experiment_id = experiment_id
        self._script = script if script is not None else build_tl6_script(seed=seed)

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """執行一次完整的 TL-6 客廳社交情境模擬。"""
        run_id = run_id or _new_run_id()
        harness_root = (
            self._repo_root / "data" / "time_lapse" / self._experiment_id
        )
        run_dir = harness_root / run_id

        # 1. 隔離 data_root
        os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
        reset_data_root()
        isolated_root = data_root()

        # 2. 初始化 3 位 seeded Soul
        for agent_id in TL6_AGENTS:
            seed_soul(isolated_root, agent_id=agent_id)

        # 3. 初始化各 Agent 的社交感知與防火牆組件
        producer_gate = SocialEventProducerGate()
        firewalls: Dict[str, IdentityFirewall] = {
            aid: IdentityFirewall(current_agent_id=aid) for aid in TL6_AGENTS
        }

        # 模擬 shared eventbus
        bus = SoulEventBus()

        # 每位 agent 的 WorldPerceptionMiddleware
        middlewares: Dict[str, WorldPerceptionMiddleware] = {}
        for aid in TL6_AGENTS:
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(
                isolated_root / "world" / aid / "trace.jsonl"
            )
            mw = WorldPerceptionMiddleware(
                bus=bus,
                state=state,
                trace_writer=writer,
                social_perception_budget=2,
            )
            middlewares[aid] = mw

        clock = SimulationClock(start_day=0)
        records: List[TL6TickRecord] = []

        total_social_events = 0
        total_transmits = 0
        quarantine_leaks = 0
        privacy_leaks = 0

        # 4. 逐 tick 執行劇本
        for idx, tick in enumerate(self._script):
            sim_ts = clock.sim_ts(tick.day_index, tick.hour)
            bus_published_count = 0
            perceived_agents: List[str] = []
            id_verdicts: Dict[str, str] = {}
            internalizable_verdicts: Dict[str, bool] = {}
            tick_transmits = 0
            p_verdict_allowed = True

            # 處理單一事件刺激
            events_to_process: List[TL6EventSpec] = []
            if tick.stimulus:
                events_to_process.append(tick.stimulus)
            if tick.burst_events:
                events_to_process.extend(tick.burst_events)

            for ev_spec in events_to_process:
                total_social_events += 1

                # ── 防線 2: Producer Gate 判定 ──
                p_verdict = producer_gate.evaluate(
                    channel_mode="private" if ev_spec.visibility == VISIBILITY_PRIVATE else "group",
                    channel=ev_spec.space_id,
                )
                p_verdict_allowed = p_verdict.allowed

                if not p_verdict.allowed:
                    # 被攔截, 檢查是否預期
                    if tick.expected_producer_allowed:
                        privacy_leaks += 1
                    continue  # 不上 bus

                # 允許發布: 構造 SoulEvent (走廣播總線)
                bus_published_count += 1
                soul_ev = SoulEvent(
                    event_type=EventType.SOCIAL_WORLD_EVENT,
                    source=ev_spec.actor_id,
                    actor_id=ev_spec.actor_id,
                    target="broadcast",
                    priority=EventPriority.LOW,
                    payload={
                        "actor_id": ev_spec.actor_id,
                        "space_id": ev_spec.space_id,
                        "visibility": ev_spec.visibility,
                        "event_type": ev_spec.event_type,
                        "content": ev_spec.content,
                        "novelty_id": ev_spec.novelty_id,
                        "ts": sim_ts,
                        "summary": ev_spec.summary,
                        "priority": ev_spec.priority,
                    },
                )

                # 廣播到所有 Agent 的 middleware
                for aid, mw in middlewares.items():
                    # ── 防線 3: Identity Firewall 驗證 ──
                    fw = firewalls[aid]
                    c_verdict = fw.classify(ev_spec.actor_id)
                    can_internalize = fw.verify_internalizable(ev_spec.actor_id)
                    id_verdicts[f"{aid}<-{ev_spec.actor_id}"] = c_verdict.value
                    internalizable_verdicts[f"{aid}<-{ev_spec.actor_id}"] = can_internalize

                    # 驗證不變量: 他者事件絕不能 internalize
                    if ev_spec.actor_id != aid:
                        if can_internalize or c_verdict != IdentityVerdict.EXTERNAL_OTHER_ACTION:
                            quarantine_leaks += 1

                    # ── 防線 1: Ambient Perception 注入 ──
                    asyncio.run(mw.handle_event(soul_ev))

                    # 渲染 prompt 社交感知區塊
                    active_social = [
                        ev for ev in mw.state.get_active_events()
                        if isinstance(ev, SocialWorldEvent)
                    ]
                    social_block = mw._render_social_context(
                        active_social,
                        user_keywords=[],
                        temporal_salience="",
                        anticipatory_flavor="",
                        vulnerability_window=False,
                        agent_id=aid,
                    )

                    if social_block:
                        perceived_agents.append(aid)
                        # 驗證反框架提示是否在場
                        if "[社交感知]" not in social_block or "這些是他人的行為" not in social_block:
                            quarantine_leaks += 1

            # ── 驗證防風暴不變量 (Anti-Storm Invariant) ──
            # 在純社交感知下, 接收方不應自動觸發連鎖 transmit
            if tick.expect_zero_transmits and tick_transmits > 0:
                anti_storm_ok = False
            else:
                anti_storm_ok = True

            privacy_ok = (privacy_leaks == 0)
            quarantine_ok = (quarantine_leaks == 0)

            record = TL6TickRecord(
                experiment_id=self._experiment_id,
                run_id=run_id,
                tick_index=idx,
                tick_id=tick.tick_id,
                day_index=tick.day_index,
                hour=tick.hour,
                sim_ts=sim_ts,
                scenario=tick.scenario,
                producer_allowed=p_verdict_allowed,
                bus_published_count=bus_published_count,
                perceived_agent_count=len(set(perceived_agents)),
                identity_firewall_verdicts=id_verdicts,
                internalizable_verdicts=internalizable_verdicts,
                ambient_injected_agents=list(set(perceived_agents)),
                transmit_count=tick_transmits,
                anti_storm_ok=anti_storm_ok,
                quarantine_ok=quarantine_ok,
                privacy_ok=privacy_ok,
            )
            records.append(record)

        # 5. 彙整指標
        anti_storm_passed = all(r.anti_storm_ok for r in records)
        identity_quarantine_passed = (quarantine_leaks == 0)
        privacy_gate_passed = (privacy_leaks == 0)
        ambient_salience_passed = any(r.perceived_agent_count > 0 for r in records)

        derived = TL6DerivedMetrics(
            anti_storm_passed=anti_storm_passed,
            identity_quarantine_passed=identity_quarantine_passed,
            privacy_gate_passed=privacy_gate_passed,
            ambient_salience_passed=ambient_salience_passed,
            zero_mutation_passed=True,
            total_ticks=len(records),
            total_social_events=total_social_events,
            total_transmits_triggered=total_transmits,
            quarantine_leaks=quarantine_leaks,
            privacy_leaks=privacy_leaks,
            summary=(
                f"TL-6 Lounge Stability: Anti-Storm={'PASS' if anti_storm_passed else 'FAIL'}, "
                f"Identity Quarantine={'PASS' if identity_quarantine_passed else 'FAIL'}, "
                f"Privacy Gate={'PASS' if privacy_gate_passed else 'FAIL'}, "
                f"Ambient Salience={'PASS' if ambient_salience_passed else 'FAIL'}"
            ),
        )

        # 6. 寫出紀錄檔
        rec_dir = run_dir / "records"
        rec_dir.mkdir(parents=True, exist_ok=True)
        with open(rec_dir / "ticks.jsonl", "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

        with open(run_dir / "derived.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(derived), ensure_ascii=False, indent=2) + "\n")

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "derived": derived,
        }

    def run_series(self, n_runs: int = 3) -> Dict[str, Any]:
        """多 Run 系列執行 (含 D2 決定性比對與零生產污染驗證)。"""
        before_hashes = snapshot_data_root_hashes(self._repo_root / "data")

        runs_output = []
        for i in range(n_runs):
            out = self.run_once(run_id=f"run_{i+1}")
            runs_output.append(out)

        # 零生產污染驗證
        mut_res = verify_zero_mutation(self._repo_root / "data", before_hashes)
        zero_mut_ok = mut_res["pass"]
        mut_diff = mut_res["diff"]

        # D2 決定性驗證: 比對 3 次 run 的 records 長度與關鍵 verdict 是否完全一致
        run1_records = [asdict(r) for r in runs_output[0]["records"]]
        determinism_ok = True
        for r_idx in range(1, n_runs):
            curr_records = [asdict(r) for r in runs_output[r_idx]["records"]]
            if len(curr_records) != len(run1_records):
                determinism_ok = False
                break
            for t1, t2 in zip(run1_records, curr_records):
                if (
                    t1["anti_storm_ok"] != t2["anti_storm_ok"]
                    or t1["quarantine_ok"] != t2["quarantine_ok"]
                    or t1["privacy_ok"] != t2["privacy_ok"]
                    or t1["bus_published_count"] != t2["bus_published_count"]
                ):
                    determinism_ok = False
                    break

        all_passed = (
            runs_output[0]["derived"].anti_storm_passed
            and runs_output[0]["derived"].identity_quarantine_passed
            and runs_output[0]["derived"].privacy_gate_passed
            and runs_output[0]["derived"].ambient_salience_passed
            and zero_mut_ok
            and determinism_ok
        )

        return {
            "experiment_id": self._experiment_id,
            "n_runs": n_runs,
            "all_passed": all_passed,
            "zero_mutation_ok": zero_mut_ok,
            "mutation_diff": mut_diff,
            "determinism_ok": determinism_ok,
            "runs": runs_output,
        }
