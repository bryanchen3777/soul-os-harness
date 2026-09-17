"""
harness/runner.py — TL-1 run 编排 (隔离 data_root + 执行序列 + determinism) (TL-1)

TL-0 规格 §6.7 / §7 (D7, 已拍板):
  - 隔离 data_root: SOUL_OS_DATA_DIR → data/time_lapse/<experiment_id>/<run_id>/,
    0 production mutation (跑前跑后 production data_root 逐档 byte-hash 0 diff)。
  - 每 run 执行序列 (§6.7):
      1. 建立 run 隔离 data_root
      2. 初始化 seeded Soul (persona + seeded memory baseline)
      3. T0: probe → GrowthProbeRecord(T0)
      4. SimulationClock: 依序喂 D1-D15 事件
      5. T15: probe → GrowthProbeRecord(T15)
      6. SimulationClock: 依序喂 D16-D30 事件
      7. T30: probe → GrowthProbeRecord(T30)
      8. 产出 canonical records + raw outputs; derived 解析另行产出
      9. 决定性验证: 同 fixture 再跑 run_2、run_3 → §5 比对
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.paths import data_root, reset_data_root

from .clock import SimulationClock
from .fixture import (
    TL1_EXPERIMENT_ID,
    TL1_FIXTURE_SCRIPT_REF,
    TL1_SEED,
    TL1_SOUL_ID,
    TL1_STIMULUS,
    FixtureEvent,
    build_script,
    experience_sequence_hash,
    inject_event,
    seed_soul,
)
from .observer import Observer
from .probe import GrowthProbe
from .records import (
    GrowthProbeRecord,
    RunHeader,
    append_derived,
    append_probe_record,
    read_probe_records,
    write_raw_output,
    write_run_header,
)

logger = logging.getLogger("soul_os.harness.runner")

# 隔离 data_root 的挂载点 (production data/ 之下, harness 唯一写区, §7.1)
TIME_LAPSE_DIR_NAME = "time_lapse"

# 0 mutation 验证时跳过的 harness 写区 (data/time_lapse/)
_MUTATION_SKIP_DIRS = {
    TIME_LAPSE_DIR_NAME,
}

# 0 mutation 验证时跳过的 production server 运行时文件 (并发活动, 非 harness 写入):
# *.log / *.err / *.pid / *.txt / *.bak / *.old / *.tmp — production server
# (heartbeat_trace.log / faulthandler.log 等) 在 harness 实验期间可能并发写入。
# harness 的 0 mutation 契约只约束「production 数据文件」, 不约束 server 日志。
# P4-2: 修正 SQLite 暫存檔副檔名比對缺口，涵蓋 .db-wal/.db-shm/.sqlite-wal/.sqlite-shm/.sqlite3-wal/.sqlite3-shm 等變體。
_MUTATION_SKIP_EXTS = {
    ".log", ".err", ".pid", ".txt", ".bak", ".old", ".tmp",
    ".db-wal", ".db-shm",
    ".sqlite-wal", ".sqlite-shm",
    ".sqlite3-wal", ".sqlite3-shm",
}

# FUP-1 Part B + TEST-INFRA-DATA-ROOT-GUARD-1：由「活的生產服務運行時實際寫入」推導出的精確路徑／窄樣式 allowlist。
# 收窄 4 條過寬樣式（恢復檢測力）：
#   1. heartbeats 整目錄 → heartbeats/telegram_channel.json
#   2. tts 整目錄 → tts/**/*.mp3
#   3. state/post_*_counter.json → state/post_*[0-9a-f]_counter.json
#   4. soul/agent_*/diary/*.jsonl → soul/agent_*/diary/????-??-??.jsonl
# TEST-INFRA-GUARD-LIVE-WRITER-COVERAGE-1：再補 8 條**事件驅動**線上寫入者（見下方 D1-1..D1-8）。
# 鐵律：只准加精確路徑／窄樣式；不得加整目錄 skip（`agents` / `state` / `conversations` 等
# 一律不得進 `_MUTATION_SKIP_DIRS`），否則守門會被鈍化。
_MUTATION_SKIP_PATTERNS = (
    # 精確收窄 (D1-1)：Telegram 通道心跳，每 30 秒一寫（src/io/channels/telegram.py:300）
    "heartbeats/telegram_channel.json",
    # 精確收窄 (D1-2)：TTS 音檔輸出（src/voice/tts_service.py:69, src/llm/fish_tts_handler.py:408）
    "tts/**/*.mp3",
    # 15:32:14 — 服務每回合寫入的對話 session 檔
    "sessions/agent_*_user_*.json",
    # 15:10:54 — 服務群聊對話記錄
    "conversations/group_chat.json",
    # 15:14:45 — 服務主記憶庫（sqlite；-shm/-wal 已由 ext 清單涵蓋）
    "memory.db",
    # 12:41:16 — 服務匯出的 agent 記憶
    "memory/agent_*/memories.jsonl",
    # 15:29:51 — 服務事件迴圈活性心跳
    "state/event_loop_alive.json",
    # 精確收窄 (D1-3)：watchdog 逐 commit 的計數檔，commit hash 為十六進位字元（scripts/_watchdog.ps1:181）
    "state/post_*[0-9a-f]_counter.json",
    # 15:18:47 — 服務自身 flush（同一秒成批寫入）
    "elevation/elevation_edges.jsonl",
    "elevation/elevation_nodes.jsonl",
    "elevation/elevation_trace.jsonl",
    "inner_life/trace.jsonl",
    # 15:18:47 — perception trace（工單明列的歷史偽紅來源）
    "world/perception_trace.jsonl",
    # 14:49:01 — 服務 30s wake 的決策／動機 trace
    "soul/decision_trace.jsonl",
    "soul/motive_trace.jsonl",
    # 10:26:16／09:55:13 — 服務自身寫入的互動、日記與關係檔
    "soul/interactions.jsonl",
    "soul/agent_*/relationships.json",
    # 精確收窄 (D1-4)：服務自身寫入的日記檔（YYYY-MM-DD.jsonl, src/soul/diary.py:257, dream_event.py:480）
    "soul/agent_*/diary/????-??-??.jsonl",

    # ── TEST-INFRA-GUARD-LIVE-WRITER-COVERAGE-1：補齊「事件驅動」的線上寫入者 ──
    # 上面的樣式已 100% 覆蓋所有**週期性**線上寫入（TG 心跳 30s、event_loop_alive 30s、
    # watchdog counter 5m、perception/decision/motive trace、日誌輪替…）。
    # 以下為**事件驅動**寫入者：只要 Bryan 在守門執行期間剛好發訊／用語音／下指令就會落
    # 進快照窗內 ⇒ 偽紅。逐條皆為精確路徑／窄樣式，**不得**改成整目錄 skip。
    #
    # (D1-1) touch_bryan_last_seen — src/io/channels/bryan_state.py:60
    #   呼叫端：TG 入站 src/io/channels/router.py:646、Web WS 入站 src/io/gateway.py:882、
    #   語音入站 src/voice/input_router.py:224。節奏：事件驅動（每次 Bryan 發話）。
    "state/bryan_last_seen.json",
    # (D1-2) _save_last_tg_user_global — src/io/channels/router.py:618
    #   節奏：事件驅動（Bryan 自任一通道入站時更新全域 last_tg_user）。
    "state/last_tg_user.json",
    # (D1-3) set_tts_enabled — src/llm/tts_toggle.py:57
    #   呼叫端：/tts 指令 src/io/channels/telegram.py:44。節奏：事件驅動（Bryan 打 /tts）。
    "state/tts_toggle.json",
    # (D1-4) 離線主動訊息積壓 — src/io/channels/router.py:672
    #   節奏：事件驅動（Bry 不在 web 且無 last_tg_user 時，角色主動觸發訊息 append）。
    "state/outbox.json",
    # (D1-5) 私聊對話 — src/llm/proxy.py:3489 (_save_private_instance)
    #   實際檔名：{user_id}_{agent_id}_private.json / bryan_{agent_id}_private.json。
    #   節奏：事件驅動（每一輪私聊），窄樣式只涵蓋 *_private.json。
    "conversations/*_private.json",
    # (D1-6) 情感狀態 — src/agent/consciousness.py:77 (save), :498 (session 結束)
    #   節奏：事件驅動（主動意圖 / session 結束）。
    "agents/*/emotional-state.json",
    # (D1-7) SESSION_END carryover — src/agent/consciousness.py:411
    #   節奏：事件驅動（SESSION_END）。
    "agents/*/carryover.json",
    # (D1-8) 修正 fnmatch 語意缺口：本專案用 fnmatch，`**` **不是遞迴**，
    #   故 `tts/**/*.mp3` 命中不了直接放在 data/tts/ **根層**的 mp3。
    #   data/tts/ 本來就是 TTS 產物目錄（src/voice/tts_service.py:69,
    #   src/llm/fish_tts_handler.py:408），同類檔案不該因深度不同而有不同待遇
    #   ⇒ **同時保留** `tts/**/*.mp3` 並新增本條。
    "tts/*.mp3",
)


def is_sqlite_temp_file(path: Path | str) -> bool:
    """判斷檔名是否為 SQLite 暫存檔 (-wal / -shm)。

    涵蓋 .db-wal / .db-shm / .sqlite-wal / .sqlite-shm / .sqlite3-wal / .sqlite3-shm 等變體。
    對照組（如 x.db、x.db-backup、y-wal.txt）回傳 False。
    """
    name = Path(path).name.lower()
    return any(name.endswith(ext) for ext in (
        ".db-wal", ".db-shm",
        ".sqlite-wal", ".sqlite-shm",
        ".sqlite3-wal", ".sqlite3-shm",
    ))


def _is_mutation_skipped(rel: Path) -> bool:
    """`rel`（相對 production data_root）是否屬 0 mutation 驗證的排除範圍。

    排除三類：harness 寫區／服務運行時寫入路徑（見上面註解）／server 日誌與 sqlite wal/shm 類副檔名。
    集中在一處，讓「未來新增只需改一處」。
    """
    if rel.parts and rel.parts[0] in _MUTATION_SKIP_DIRS:
        return True
    posix = rel.as_posix()
    if any(fnmatch.fnmatch(posix, pattern) for pattern in _MUTATION_SKIP_PATTERNS):
        return True
    if is_sqlite_temp_file(rel):
        return True
    return Path(posix).suffix.lower() in _MUTATION_SKIP_EXTS


# ───────────────────────────────────────────────────────────
# 0 mutation 验证 (§7.2 规则 3)
# ───────────────────────────────────────────────────────────

def snapshot_data_root_hashes(data_root_dir: Path) -> Dict[str, str]:
    """对 production data_root 列出逐档 byte hash (跳过 harness 写区与活服务运行时路径)。

    Returns:
        {相对路径: sha256 hex} — 只含 run 前已存在的文件。
    """
    data_root_dir = Path(data_root_dir)
    snapshot: Dict[str, str] = {}
    if not data_root_dir.exists():
        return snapshot
    for path in sorted(data_root_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_root_dir)
        if _is_mutation_skipped(rel):
            continue
        try:
            snapshot[rel.as_posix()] = _sha256_file(path)
        except PermissionError:
            continue
    return snapshot


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zero_mutation(
    data_root_dir: Path, before: Dict[str, str]
) -> Dict[str, Any]:
    """run 系列结束后重算 production data_root hash, 与 before 比对。

    Returns:
        {"pass": bool, "diff": {path: (before_hash, after_hash)}, "added": [...]}
    """
    after = snapshot_data_root_hashes(data_root_dir)
    diff: Dict[str, tuple[str, str]] = {}
    for path, h_before in before.items():
        h_after = after.get(path)
        if h_after != h_before:
            diff[path] = (h_before, h_after or "<missing>")
    # 新增文件 (run 前不存在, run 后出现, 且不在 harness 写区)
    added = sorted(set(after.keys()) - set(before.keys()))
    return {"pass": not diff and not added, "diff": diff, "added": added}


# ───────────────────────────────────────────────────────────
# LLM call 工厂
# ───────────────────────────────────────────────────────────

def make_real_llm_call(
    model: Optional[str] = None, temperature: float = 0.0
) -> Callable[..., Any]:
    """构造真实 LLM call (temperature=0, TL-0 §5.1)。

    用 configs.loader.create_llm_backend (不需要 bus), 直接调 backend.complete。
    temperature 强制 0 (忽略调用方传入值, 保证 probe path 一律 temperature=0)。
    """
    from configs.loader import create_llm_backend, load_config

    cfg = load_config()
    backend = create_llm_backend(cfg)
    resolved_model = model or cfg.get("llm", {}).get("model", "gpt-4o-mini")

    async def llm_call(
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        try:
            # TL-0 §5.1 规则 2: probe path (及 run 期间 pipeline 的 LLM 呼叫)
            # 一律 temperature=0。忽略调用方传入值 (decide_motive 内部传 0.3),
            # 强制 0, 保证系列内采样方差最小化。
            return await backend.complete(
                messages,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=0.0,
            )
        except Exception as e:  # noqa: BLE001 — fail-closed: 失败返回 None
            logger.warning(
                f"[TL-1] LLM call 失败 (fail-closed=None): "
                f"{type(e).__name__}: {e}"
            )
            return None

    return llm_call


def make_stub_llm_call(
    responses: Optional[Dict[str, List[Optional[str]]]] = None,
) -> Callable[..., Any]:
    """构造 stub LLM call (确定性, 测试用)。

    responses: {"interpretation": [...], "decision": [...]} 响应队列;
               缺省用确定性默认响应。
    """
    responses = responses or {}
    interp_queue = list(responses.get("interpretation", []))
    decision_queue = list(responses.get("decision", []))
    calls: List[Dict[str, Any]] = []

    async def llm_call(
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        calls.append(
            {
                "messages": messages,
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        content = messages[-1]["content"] if messages else ""
        if "has_motive" in content and "decision" not in content:
            return interp_queue.pop(0) if interp_queue else '{"has_motive": false}'
        if "decision" in content:
            return decision_queue.pop(0) if decision_queue else (
                '{"decision": "not_transmit", "reason": "stub"}'
            )
        return None

    llm_call.calls = calls  # type: ignore[attr-defined]
    return llm_call


# ───────────────────────────────────────────────────────────
# TL-1 Runner
# ───────────────────────────────────────────────────────────

class TL1Runner:
    """TL-1 Same-Stimulus Longitudinal Growth Test 编排器。"""

    def __init__(
        self,
        repo_root: Path,
        llm_call: Callable[..., Any],
        seed: int = TL1_SEED,
        experiment_id: str = TL1_EXPERIMENT_ID,
        soul_id: str = TL1_SOUL_ID,
        llm_model: str = "unknown",
        llm_temperature: float = 0.0,
        pipeline_version: str = "unknown",
    ) -> None:
        self._repo_root = Path(repo_root)
        self._llm_call = llm_call
        self._seed = seed
        self._experiment_id = experiment_id
        self._soul_id = soul_id
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._pipeline_version = pipeline_version
        self._observer = Observer()
        self._script = build_script(seed=seed)

    # ── 单 run ───────────────────────────────────────────

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """执行一个完整 run (§6.7 序列 1-8)。

        Returns:
            {"run_id", "run_dir", "records": [T0, T15, T30], "header": {...}}
        """
        run_id = run_id or uuid.uuid4().hex
        harness_root = self._repo_root / "data" / TIME_LAPSE_DIR_NAME / self._experiment_id
        run_dir = harness_root / run_id

        # 1. 建立 run 隔离 data_root (SOUL_OS_DATA_DIR → run_dir)
        os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
        reset_data_root()
        isolated_root = data_root()

        # 2. 初始化 seeded Soul (persona baseline + seeded memory baseline)
        seed_soul(isolated_root, agent_id=self._soul_id)

        # 3-7. 执行序列 (T0 probe → D1-D15 → T15 probe → D16-D30 → T30 probe)
        clock = SimulationClock(start_day=0)
        probe = GrowthProbe(agent_id=self._soul_id, llm_call=self._llm_call)

        fed_events: List[FixtureEvent] = []
        records: List[Dict[str, Any]] = []

        # T0 (D0, 喂任何事件前)
        t0 = self._probe_and_record(
            probe=probe,
            clock=clock,
            checkpoint="T0",
            fed_events=list(fed_events),
            run_dir=run_dir,
        )
        records.append(t0)

        # 喂 D1-D15
        for ev in self._script:
            if ev.day_index > 15:
                break
            inject_event(isolated_root, ev, clock.sim_ts(ev.day_index), self._soul_id)
            fed_events.append(ev)
        clock.advance(15)

        # T15 (D15)
        t15 = self._probe_and_record(
            probe=probe,
            clock=clock,
            checkpoint="T15",
            fed_events=list(fed_events),
            run_dir=run_dir,
        )
        records.append(t15)

        # 喂 D16-D30
        for ev in self._script:
            if ev.day_index <= 15:
                continue
            inject_event(isolated_root, ev, clock.sim_ts(ev.day_index), self._soul_id)
            fed_events.append(ev)
        clock.advance(15)

        # T30 (D30)
        t30 = self._probe_and_record(
            probe=probe,
            clock=clock,
            checkpoint="T30",
            fed_events=list(fed_events),
            run_dir=run_dir,
        )
        records.append(t30)

        # 8. run header
        header = RunHeader(
            experiment_id=self._experiment_id,
            run_id=run_id,
            seed=self._seed,
            fixture_script_ref=TL1_FIXTURE_SCRIPT_REF,
            soul_id=self._soul_id,
            llm_model=self._llm_model,
            llm_temperature=self._llm_temperature,
            pipeline_version=self._pipeline_version,
            data_root=str(isolated_root),
        )
        write_run_header(run_dir, header)

        # derived 解析 (独立 analysis/ 流, 不回写 canonical)
        for rec in records:
            derived = self._observer.observe(rec)
            append_derived(run_dir, derived)

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "header": header.to_dict(),
        }

    def _probe_and_record(
        self,
        probe: GrowthProbe,
        clock: SimulationClock,
        checkpoint: str,
        fed_events: List[FixtureEvent],
        run_dir: Path,
    ) -> Dict[str, Any]:
        """执行一次 probe + 写 canonical record + raw output。"""
        import asyncio

        output = asyncio.run(
            probe.run(
                stimulus=TL1_STIMULUS,
                checkpoint=checkpoint,
                sim_ts=clock.label(),
                fed_events=fed_events,
            )
        )

        record = GrowthProbeRecord(
            checkpoint=checkpoint,
            sim_ts=clock.label(),
            stimulus=output.stimulus,
            experience_sequence_hash=experience_sequence_hash(fed_events),
            experience_event_count=len(fed_events),
            emergent_snapshot=output.emergent_snapshot,
            motive_text=output.motive_text,
            decision_text=output.decision_text,
            reached_action=output.reached_action,
            probe_ts=output.motive.created_at if output.motive else _utcnow_iso(),
        )
        append_probe_record(run_dir, record)
        write_raw_output(run_dir, checkpoint, "emergent", output.emergent_snapshot)
        write_raw_output(run_dir, checkpoint, "motive", output.motive_text)
        write_raw_output(run_dir, checkpoint, "decision", output.decision_text)
        return record.to_dict()

    # ── run 系列 (D2 determinism) ────────────────────────

    def run_series(self, n_runs: int = 3) -> Dict[str, Any]:
        """同 fixture 连跑 n_runs 次, 做 determinism 比对 (§5)。

        Returns:
            {"runs": [...], "determinism": {...}, "mutation": {...}}
        """
        production_root = self._repo_root / "data"

        # 0 mutation 验证: run 前快照
        before = snapshot_data_root_hashes(production_root)

        runs: List[Dict[str, Any]] = []
        for i in range(n_runs):
            logger.info(f"[TL-1] run {i + 1}/{n_runs} 开始")
            run = self.run_once()
            runs.append(run)
            logger.info(f"[TL-1] run {i + 1}/{n_runs} 完成: {run['run_id']}")

        # determinism 比对 (跨 run, 每 checkpoint)
        determinism = self._observer.derive_determinism(
            [{"run_id": r["run_id"], "records": r["records"]} for r in runs]
        )

        # 0 mutation 验证: run 后重算
        mutation = verify_zero_mutation(production_root, before)

        return {
            "runs": runs,
            "determinism": determinism,
            "mutation": mutation,
        }


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
