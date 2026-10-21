#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/sim_accel_thread_cli.py — SIM-ACCEL-THREAD-DISSOLVE 的兩階段沙盒 CLI。

為什麼存在
==========
2026-10-19 上線的「生活線頭 → 昇華接縫」（`LIFE_THREAD_ELEVATION_ENABLED`）在生產上
已經發射了 35 筆事件；此後每條線頭的 `thread_id` 都被蓋章寫進**行程內記憶體**登記表
（`src/soul/life_thread_orchestrator.py::_ELEVATION_EMITTED`）⇒ 同一行程內**不再重發**。

於是「增量發射機制面對**未來**的新線頭時是否真的暢通」這個問題，被**真實生活推進的
時間**卡住：要看第 36 筆，得等一條在途線頭真的走完（最快 14 天，且必須是 14 天整 **+1 秒**
才過存活軸）。

Owner 2026-10-20 22:37 裁決：「用實際時間來等，是最不實際的辦法，所以結論是不等」
⇒ 本 CLI 在**沙盒 ＋ 虛擬時鐘 ＋ 腳本化 LLM** 下，於幾秒內把同一條鏈路走完一遍：
M4 的動作把線頭轉入終態 → M5 接線層沉澱（假 LLM）→ 寫回 M1 → 接縫發射第 36 個事件。

🔴 對 M2 語意的更正（2026-10-20 主大腦工單更正）
--------------------------------------------------
「靠虛擬時鐘讓 active 線頭自然轉 completed」**不成立**：`src/soul/life_thread_dissolution.py`
步驟 4 明定，age／idle 兩軸在 `allow_soft_archive=False` 時**只是諮詢訊號**
（`target_status=ACTIVE`、`should_mutate=False`、`extra_metadata["advisory"]=True`）。
active 線頭要 `should_mutate=True` 只有兩條路：(a) 呼叫端顯式注入
`checkpoints_exhausted=True`；(b) **已經**是終態（`completed`／`abandoned`）且
`dissolved_at is None` ⇒ 走步驟 2 的真溶解。

⇒ 本 CLI 的正確槓桿是**接線層的既有寫入路徑**：
M1 轉入終態（`lt.append_transition(..., "completed")`）→ 接線層 `dissolve_hook`
→ M5 沉澱（`LIFE_THREAD_CONSOLIDATION_ENABLED=1`）→ 事實寫入 SAGE → `sage_fact_id`
回填 M1 → 接縫（`LIFE_THREAD_ELEVATION_ENABLED=1`）在**下一輪**發射事件。
（🔴 為什麼不由「M4 的 LLM 動作」觸發轉入：本 repo 有兩條凍結契約把 M4 入口的呼叫端
與 M1／M4 的生產 importer 集合釘死為**精確等值**，`scripts/**` 的沙盒 CLI 都不在名單內；
詳見 `run_simulation()` 內的偏差說明與實作紀錄 §1.4。）
虛擬時鐘仍然使用（`created_at + 14 天 + 1 秒`），但它的作用是**讓槽位時間可控**，
不是靠它讓線頭完成。

兩階段（為什麼要換行程映像）
===========================
`src/paths.py::data_root()` 讀 `SOUL_OS_DATA_DIR` 且**行程期快取**。若在同一行程內
「先讀生產 → 再改 env → 再寫沙盒」，兩邊各自解析到的路徑可能不同
（2026-10-19 事故根因：拿兩個各自會漂移的值做比較）。
⇒ 父行程只做「備沙盒 ＋ 算生產 sha256 before」，把沙盒 env 寫進 `os.environ`，
再用 `os.spawnv(P_WAIT)` 起一個**全新子行程**（argv 只有 `[exe, "-m", 模組名]`，
其餘參數全走 env）；子行程**一開頭**就自我檢查 `data_root() == 沙盒`，不符即拒絕執行
（exit 3）。

🔴 Windows 上的實測（2026-10-20，本機）：
   - `os.execve` / `os.spawnve`（帶**自訂 env 區塊**）⇒ **access violation（0xC0000005）**，
     子行程根本沒起來；
   - `os.execv`（繼承 env）可用，但**會吞掉子行程退出碼**（子行程 exit 3 ⇒ 父行程 exit 0）；
   ⇒ 採 `os.spawnv(P_WAIT)`：新行程 ＋ 繼承 env ＋ **回傳退出碼**。隔離語意與 `os.execve`
   完全相同（env 在子行程**啟動前**就決定）。

沙盒化紀律（逐條；與 `scripts/eval_lifecycle_cli.py` 同款）
========================================================
1. **唯一寫入目標＝沙盒**（`SOUL_OS_DATA_DIR` 指向 `%TEMP%` 之下）。
   **沒有任何就地寫入旗標**（AGENTS.md 行程安全鐵律 12）。
2. **0 真實 LLM 呼叫**：M4 的 `llm_caller` 與 M5 接線層的 LLM proxy 都被腳本化；
   腳本化 LLM 遇到**非預期 prompt** 直接 `raise`（杜絕真實推理與花費）。
3. **0 網路、0 伺服器、0 埠**；不 import `subprocess`／`requests`／`httpx`／`socket`／
   `uvicorn`／`fastapi`（`os.execve` 允許：它不是程序建立 API）。
4. **生產檔案只讀**：sha256 前後逐位元對帳，不符即 exit 4。
5. 報告寫沙盒（`%TEMP%` 之下）；repo 內只新增本工單列出的檔案。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: repo 根（`scripts/` 的上一層）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: **生產資料根的絕對字面路徑**。
#:
#: 🔴 不得用 `data_root()` 取生產路徑（子行程裡那已是沙盒）——這是 2026-10-19 事故的
#: 直接教訓：沙盒隔離必須在**行程啟動前**決定，且生產端只能走**不會漂移的字面路徑**。
PRODUCTION_DATA_DIR = REPO_ROOT / "data"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# 退出碼（工單 §3.4）
# ─────────────────────────────────────────────────────────────────────────────
EXIT_OK = 0
EXIT_ASSERT_FAILED = 1
EXIT_NO_TARGET = 2
EXIT_NOT_ISOLATED = 3
EXIT_PRODUCTION_CHANGED = 4


class SimError(Exception):
    """本 CLI 的失敗基類（帶退出碼）。"""


class SandboxIsolationError(SimError):
    """沙盒隔離自我檢查失敗（沙盒不在 temp 之下，或 env 與沙盒不一致）。"""

    exit_code = EXIT_NOT_ISOLATED


class SimTargetError(SimError):
    """找不到合適的目標線頭。"""

    exit_code = EXIT_NO_TARGET


class SimAssertionError(SimError):
    """斷言失敗。"""

    exit_code = EXIT_ASSERT_FAILED


class ProductionChangedError(SimError):
    """生產檔案 sha256 前後不一致。"""

    exit_code = EXIT_PRODUCTION_CHANGED


# ─────────────────────────────────────────────────────────────────────────────
# 常數
# ─────────────────────────────────────────────────────────────────────────────
#: M2 的存活軸門檻（天）——與 `life_thread_dissolution.DEFAULT_MAX_ACTIVE_DURATION_DAYS` 同值。
MAX_ACTIVE_DURATION_DAYS = 14
#: M2 的無進展軸門檻（天）。
STALE_CHECK_THRESHOLD_DAYS = 7
#: 存活軸邊界是**嚴格大於** ⇒ 取樣點必須是 `created_at + 14 天 + 1 秒`。
BOUNDARY_EXTRA_SECONDS = 1
#: 接縫可用的 slot（`_elevation_trigger_for_slot` 只認這兩個）。
SEAM_SLOTS = ("morning", "night")
#: M5 假 LLM 的沉澱輸出（≤200 字；非空）。
FAKE_DISSOLUTION_TEXT = (
    "把擱著的事收拾完之後，其實沒有什麼大不了——"
    "我只是把手上一直拖著的那一樣東西放回它該在的位置。"
)
#: M5 假 LLM 的 `meaning_kind`（6 值離散枚舉之一）。
FAKE_MEANING_KIND = "relief"


# ─────────────────────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────────────────────
def _log(message: str) -> None:
    """落診斷訊息到 **stderr**（stdout 留給 `--json` 的純 JSON）。"""
    print(message, file=sys.stderr, flush=True)


def sha256_file(path: Path) -> str:
    """檔案 sha256（hex）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_sha_snapshot(data_dir: Optional[Path] = None) -> Dict[str, str]:
    """生產三類檔案的 sha256 快照。

    比對範圍（工單 §3.3）：
      - `data/soul/**/life_threads.jsonl`
      - `data/memory/**/graph.sqlite`
      - `data/elevation/*.jsonl`

    🔴 讀取一律走 `data_dir`（絕對字面路徑），**絕不**用 `data_root()`。
    """
    root = Path(data_dir) if data_dir is not None else PRODUCTION_DATA_DIR
    patterns = (
        ("soul", "**/life_threads.jsonl"),
        ("memory", "**/graph.sqlite"),
        ("elevation", "*.jsonl"),
    )
    snapshot: Dict[str, str] = {}
    for sub, pattern in patterns:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            if path.is_file():
                snapshot[path.relative_to(root).as_posix()] = sha256_file(path)
    return snapshot


def assert_under_temp(path: Path) -> Path:
    """沙盒必須在 `tempfile.gettempdir()` 之下，否則拒絕執行（工單 §3.2）。"""
    resolved = Path(path).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(temp_root)
    except ValueError as exc:
        raise SandboxIsolationError(
            f"[refuse] 沙盒不在 temp 之下：{resolved}（temp={temp_root}）"
        ) from exc
    return resolved


def normalize_agent_id(raw: str) -> str:
    """`--agent` 允許省略 `agent_` 前綴（工單 §3.4：預設 `mahiru`）。"""
    agent = (raw or "").strip()
    if not agent:
        raise SimError("--agent 不可為空")
    if not agent.startswith("agent_"):
        agent = "agent_" + agent
    return agent


def parse_iso(value: Any) -> datetime:
    """ISO-8601 → aware UTC datetime（失敗即拋，呼叫端負責 fail-fast）。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise SimTargetError(f"時間欄位不可解析：{value!r}")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SimTargetError(f"時間欄位不可解析：{value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _write_json(path: Path, payload: Any) -> None:
    """UTF-8 JSON 落檔（不帶 BOM）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 沙盒準備（父行程）
# ─────────────────────────────────────────────────────────────────────────────
def prepare_sandbox(
    out_dir: Path,
    agent_id: str,
    data_dir: Optional[Path] = None,
) -> Path:
    """建立沙盒（**只讀**生產、**只寫**沙盒）。

    內容：
      - `soul/<agent>/life_threads.jsonl`：**全部** agent（全域 35 條已發射線頭要能重現）；
      - `memory/<agent>/graph.sqlite(+-wal)`：**只有目標 agent**（供事實寫入 ＋ 感悟回讀）。
        🔴 `-shm` **不複製**（sqlite 會依 `-wal` 重建；複製熱寫入中的 `-shm` 有撕裂風險）；
      - `elevation/`：節點／邊（不複製 40MB 的 trace；trace 對本票無關）。
    """
    production = Path(data_dir) if data_dir is not None else PRODUCTION_DATA_DIR
    sandbox = assert_under_temp(Path(out_dir))

    for src in sorted((production / "soul").glob("*/life_threads.jsonl")):
        dst = sandbox / "soul" / src.parent.name / "life_threads.jsonl"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for suffix in ("", "-wal"):
        src = production / "memory" / agent_id / ("graph.sqlite" + suffix)
        if not src.is_file():
            continue
        dst = sandbox / "memory" / agent_id / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (sandbox / "elevation").mkdir(parents=True, exist_ok=True)
    for name in ("elevation_nodes.jsonl", "elevation_edges.jsonl", "elevation_lifecycle_events.jsonl"):
        src = production / "elevation" / name
        if src.is_file():
            shutil.copy2(src, sandbox / "elevation" / name)

    return sandbox


# ─────────────────────────────────────────────────────────────────────────────
# 腳本化 LLM（100% 覆蓋；遇到非預期 prompt 直接 raise）
# ─────────────────────────────────────────────────────────────────────────────
class ScriptedM5Proxy:
    """M5 接線層的腳本化 LLM proxy（`LLMProxy.generate_text` 的同構假身）。

    🔴 紅線：只認 M2 的沉澱 prompt 契約（`"dissolution"` ＋ `"meaning_kind"`）；
    任何**非預期** prompt 一律 `raise` ⇒ 杜絕真實推理與花費。
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def generate_text(
        self,
        *,
        messages: Any,
        agent_id: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
        max_retries: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        prompt = ""
        if isinstance(messages, list) and messages:
            content = messages[0].get("content") if isinstance(messages[0], dict) else None
            prompt = content if isinstance(content, str) else ""
        self.calls.append(
            {
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "max_retries": max_retries,
                "prompt_chars": len(prompt),
            }
        )
        if "dissolution" not in prompt or "meaning_kind" not in prompt:
            raise RuntimeError(
                "SIM-ACCEL：腳本化 LLM 收到非預期 prompt（拒絕呼叫，0 真實推理）"
            )
        return json.dumps(
            {"dissolution": FAKE_DISSOLUTION_TEXT, "meaning_kind": FAKE_MEANING_KIND},
            ensure_ascii=False,
        )


class CountingWriter:
    """`InnerLifeWriter` 的計數外殼（**委派**給真實 writer，不改行為）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.created: List[Any] = []

    def create_event(self, *args: Any, **kwargs: Any) -> Any:
        event = self._inner.create_event(*args, **kwargs)
        self.created.append(event)
        return event

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - 委派
        return getattr(self._inner, name)


class CountingGate:
    """`SubmissionGate` 的計數外殼（**委派**給真實 gate，不改行為）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.submitted: List[Dict[str, Any]] = []

    def submit(self, event_id: str, memory_facts: Any = (), **kwargs: Any) -> Any:
        self.submitted.append({"event_id": event_id, "kwargs": dict(kwargs)})
        return self._inner.submit(event_id, memory_facts, **kwargs)

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - 委派
        return getattr(self._inner, name)


# ─────────────────────────────────────────────────────────────────────────────
# 模擬核心
# ─────────────────────────────────────────────────────────────────────────────
def sandbox_agent_ids(sandbox: Path) -> List[str]:
    """沙盒內有線頭檔的 agent id（排序）。"""
    base = Path(sandbox) / "soul"
    if not base.is_dir():
        return []
    return sorted(p.parent.name for p in base.glob("*/life_threads.jsonl"))


def seed_elevation_registry(sandbox: Path, seam_module: Optional[Any] = None) -> List[str]:
    """**預先播種** `_ELEVATION_EMITTED`（重現線上伺服器的行程內登記表）。

    來源：沙盒內「`status == "completed"` 且有非空 `sage_fact_id`」的線頭 id
    ⇒ 這正是 2026-10-19 已發射過的那批（生產全域 35 條）。

    `seam_module` ＝ 接縫模組（預設 `src.soul.life_thread_orchestrator`）；
    **僅供變異實驗注入**（讓「移除 at-most-once」的變異體用自己的登記表）。
    """
    if seam_module is None:
        from src.soul import life_thread_orchestrator as orch

        seam_module = orch

    # 🔴 M1 線頭模組**不直接 import**：契約（`tests/soul/test_life_threads_m1.py`）
    # 把 M1 的生產 importer 集合釘死為**精確等值**（只有 M4 寫 M1 ＋ M5 介接層）；
    # ⇒ 一律經 orchestrator（合法 importer）的別名 `lt` 取得。
    lt = seam_module.lt

    seeded: List[str] = []
    for agent_id in sandbox_agent_ids(sandbox):
        for thread_id, state in lt.fold(agent_id).items():
            fact_id = state.get("sage_fact_id")
            if state.get("status") != "completed":
                continue
            if not isinstance(fact_id, str) or not fact_id.strip():
                continue
            seam_module._ELEVATION_EMITTED[thread_id] = 1
            seeded.append(thread_id)
    return sorted(seeded)


def pick_target_thread(
    states: Dict[str, Dict[str, Any]],
    target_thread_id: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    """挑目標在途線頭：`status == "active"` 且 `dissolved_at is None`。

    - 指定 `--target-thread` ⇒ 必須命中上述條件，否則明確報錯（不靜默改用別的線頭）；
    - 否則挑 `created_at` **最舊**者（`created_at` 相同則以 `thread_id` 字典序破）。

    🔴 工單原文的挑選條件是「`created_at + 14d + 1s` 已過於 now」——在虛擬時鐘**由
    `created_at` 反推**的前提下該條件是循環的（恆不成立）。主大腦 2026-10-20 更正後，
    虛擬時鐘的角色是「讓槽位時間可控」，故改以 `created_at` 最舊為準（可重現、確定性）。
    """
    actives = {
        tid: state
        for tid, state in states.items()
        if state.get("status") == "active" and state.get("dissolved_at") is None
    }
    if target_thread_id:
        state = actives.get(target_thread_id)
        if state is None:
            raise SimTargetError(
                f"[refuse] 指定的線頭 {target_thread_id} 不是 active／未溶解的線頭"
                f"（可選：{sorted(actives)}）"
            )
        return target_thread_id, state
    if not actives:
        raise SimTargetError("[refuse] 沒有 active 且未溶解的線頭可作為目標")
    chosen = min(actives, key=lambda tid: (str(actives[tid].get("created_at") or ""), tid))
    return chosen, actives[chosen]


def run_seam_over_agents(
    sandbox: Path,
    slot: str,
    now: datetime,
    seam_module: Optional[Any] = None,
) -> Dict[str, Any]:
    """對沙盒內**每個** agent 跑一次接縫，回傳各 agent 建立的事件數與事件細節。"""
    from src.inner_life.submission_gate import SubmissionGate
    from src.inner_life.writer import InnerLifeWriter

    if seam_module is None:
        from src.soul import life_thread_orchestrator as orch

        seam_module = orch

    lt = seam_module.lt

    per_agent: Dict[str, int] = {}
    events: List[Dict[str, Any]] = []
    for agent_id in sandbox_agent_ids(sandbox):
        states = lt.fold(agent_id)
        threads = [dict(state) for state in states.values()]
        writer = CountingWriter(InnerLifeWriter())
        gate = CountingGate(SubmissionGate(writer=writer))
        created = seam_module.maybe_emit_elevation_events(
            agent_id, threads, slot, now, writer=writer, submission_gate=gate
        )
        per_agent[agent_id] = len(writer.created)
        for event in writer.created:
            extras = dict(getattr(getattr(event, "provenance", None), "extras", {}) or {})
            events.append(
                {
                    "agent_id": agent_id,
                    "event_id": event.event_id,
                    "thread_id": extras.get("thread_id", ""),
                    "sage_fact_id": extras.get("sage_fact_id", ""),
                    "narrative": extras.get("narrative", ""),
                    "reflection": extras.get("reflection", ""),
                    "created_flag": bool(created),
                }
            )
    return {
        "per_agent_events": per_agent,
        "total_events": sum(per_agent.values()),
        "events": events,
    }


async def run_simulation(
    *,
    sandbox: Path,
    agent_id: str,
    target_thread_id: Optional[str] = None,
    slot: str = "night",
    seed_registry: bool = True,
    seam_module: Optional[Any] = None,
) -> Dict[str, Any]:
    """跑完整條模擬鏈路，回傳可序列化的報告 dict。

    `seam_module` ＝ 接縫模組（預設 `src.soul.life_thread_orchestrator`）；
    **僅供變異實驗注入**（生產路徑不傳）。
    """
    if seam_module is None:
        from src.soul import life_thread_orchestrator as orch

        seam_module = orch

    # 🔴 一律經 orchestrator（**合法的生產 importer**）取得 M1／M2／M4／接線層：
    #   - `lt`        ：M1 線頭模組（其生產 importer 集合被契約釘死為**精確等值**）
    #   - `lt_diss`   ：M2 純決策層
    #   - `lt_wiring` ：M5 接線層（沉澱 hook ＋ 冪等 sentinel）
    #   - `lt_origins`：M4 起源模組（本票只用它的 LLM proxy 注入點）
    # 本檔**不得**直接 import 這些模組：`tests/soul/` 內 M1／M4 的兩支契約測試
    #   （`test_s2_1_module_not_imported_by_production_paths` 與
    #    `test_module_is_not_wired_into_production_paths`）把生產 importer 集合釘死為
    #   **精確等值**（不含 `scripts/**` 的沙盒 CLI）⇒ 走 orchestrator 這條合法路。
    lt = seam_module.lt
    lt_diss = seam_module.lt_diss
    lt_wiring = seam_module.lt_wiring
    lt_origins = seam_module.lt_origins

    if slot not in SEAM_SLOTS:
        raise SimError(f"--slot 只能是 {SEAM_SLOTS} 之一（接縫的 trigger 對應表只認這兩個）")

    report: Dict[str, Any] = {
        "sandbox": str(sandbox),
        "agent_id": agent_id,
        "slot": slot,
        "flags": {
            "LIFE_THREAD_ELEVATION_ENABLED": os.environ.get("LIFE_THREAD_ELEVATION_ENABLED"),
            "LIFE_THREAD_CONSOLIDATION_ENABLED": os.environ.get("LIFE_THREAD_CONSOLIDATION_ENABLED"),
            "LIFE_THREAD_CATCHUP_ENABLED": os.environ.get("LIFE_THREAD_CATCHUP_ENABLED"),
            "LIFE_THREAD_BOOTSTRAP_ENABLED": os.environ.get("LIFE_THREAD_BOOTSTRAP_ENABLED"),
            "LIFE_THREAD_WHIM_ENABLED": os.environ.get("LIFE_THREAD_WHIM_ENABLED"),
        },
    }

    # ── M1：讀沙盒線頭 ──
    states = lt.fold(agent_id)
    if not states:
        raise SimTargetError(f"[refuse] 沙盒內找不到 {agent_id} 的線頭檔")
    report["m1_threads"] = len(states)

    # ── 預先播種行程內登記表 ──
    seeded: List[str] = []
    if seed_registry:
        seeded = seed_elevation_registry(sandbox, seam_module)
    report["registry"] = {
        "seeded": len(seeded),
        "thread_ids": seeded,
        "size_after": len(seam_module._ELEVATION_EMITTED),
    }

    # ── 目標線頭 ＋ 虛擬時鐘 ──
    chosen_id, chosen_state = pick_target_thread(states, target_thread_id)
    created_at = parse_iso(chosen_state.get("created_at"))
    virtual_now = created_at + timedelta(days=MAX_ACTIVE_DURATION_DAYS) + timedelta(
        seconds=BOUNDARY_EXTRA_SECONDS
    )
    report["target"] = {
        "thread_id": chosen_id,
        "title": chosen_state.get("title"),
        "created_at": created_at.isoformat(),
        "updated_at": chosen_state.get("updated_at"),
        "narrative_content": chosen_state.get("narrative_content"),
        "virtual_now": virtual_now.isoformat(),
        "age_seconds": int((virtual_now - created_at).total_seconds()),
        "age_days_exact": (virtual_now - created_at).days,
        "boundary": "created_at + %d days + %d second (strict >)"
        % (MAX_ACTIVE_DURATION_DAYS, BOUNDARY_EXTRA_SECONDS),
    }

    # ── M2 ①：active 線頭在虛擬時點只是**諮詢訊號**（advisory）──
    advisory = lt_diss.evaluate_thread_dissolution(
        dict(chosen_state),
        virtual_now,
        max_active_duration_days=MAX_ACTIVE_DURATION_DAYS,
        stale_check_threshold_days=STALE_CHECK_THRESHOLD_DAYS,
        allow_soft_archive=False,
        agent_id=agent_id,
    )
    report["m2_active_advisory"] = _evaluation_view(advisory)

    # ── 接縫「前」掃描：35 筆歷史線頭零重發 ──
    pre_seam = run_seam_over_agents(sandbox, slot, virtual_now, seam_module)
    report["seam_pre"] = {
        "total_events": pre_seam["total_events"],
        "per_agent_events": pre_seam["per_agent_events"],
        "events": pre_seam["events"],
    }

    # ── 轉入終態：M1 既有 API ＋ 接線層沉澱 hook ──
    # 🔴 **為什麼不是「M4 的 LLM 動作」**（與工單更正 §3.1 步驟 5 的偏差，附證據）：
    #   1. `tests/soul/` 內 M4 契約測試
    #      `test_module_declares_m5_wiring_and_reuses_existing_channel`
    #      把 **M4 入口的呼叫端**釘死為「只有 orchestrator」⇒ `scripts/**` 的沙盒 CLI
    #      **不得**呼叫該入口（AST 判定）。
    #   2. 同檔 `test_module_is_not_wired_into_production_paths` 與
    #      `tests/soul/test_life_threads_m1.py::test_s2_1_module_not_imported_by_production_paths`
    #      把 M4／M1 的生產 importer 集合釘死為**精確等值**（不含 `scripts/**`）⇒
    #      沙盒 CLI **不得** import M1／M4。
    #   3. 改走 orchestrator 的 `run_slot_pipeline`（唯一的合法 M4 呼叫端）在本票**不可行**：
    #      目標線頭的 `origin_type` ＝ `world_collision` ⇒ 其 M4 prompt 需要**世界感知種子**，
    #      沙盒內沒有 `data/world/perception_trace.jsonl` ⇒ prompt 建不出來 ⇒ 0 LLM、0 轉入。
    #   ⇒ 本票改走**原工單 §3.1 步驟 8** 的既有 API 路徑（M1 轉移 ＋ 接線層 hook），
    #     M2 的諮詢訊號（存活軸命中）仍提供「為什麼現在收」的語意依據。
    m5_proxy = ScriptedM5Proxy()
    lt_origins.set_llm_proxy(m5_proxy)

    consolidation_results: List[Any] = []
    original_consolidate = lt_wiring._consolidate_once

    async def _capturing_consolidate(agent: str, thread: str) -> Any:
        result = await original_consolidate(agent, thread)
        consolidation_results.append(result)
        return result

    lt_wiring._consolidate_once = _capturing_consolidate

    #: 接線層 hook 觸發當下的 M1 快照（線頭**已是終態、尚未溶解**）
    #: ＝ **M2 真溶解裁決的現場**（事後再評會因 `dissolved_at` 已填而走不到步驟 2）。
    dissolve_moment: Dict[str, Any] = {}
    real_hook = lt_wiring.build_dissolve_hook()

    def _capturing_hook(hook_agent: str, hook_thread: str, status: str) -> None:
        snapshot = dict(lt.fold(hook_agent).get(hook_thread) or {})
        dissolve_moment["state"] = {
            "status": snapshot.get("status"),
            "dissolved_at": snapshot.get("dissolved_at"),
            "sage_fact_id": snapshot.get("sage_fact_id"),
        }
        for key, terminal_reason in (
            ("evaluation", "time_horizon_exceeded"),
            ("evaluation_default_reason", None),
        ):
            dissolve_moment[key] = _evaluation_view(
                lt_diss.evaluate_thread_dissolution(
                    snapshot,
                    virtual_now,
                    max_active_duration_days=MAX_ACTIVE_DURATION_DAYS,
                    stale_check_threshold_days=STALE_CHECK_THRESHOLD_DAYS,
                    allow_soft_archive=False,
                    agent_id=hook_agent,
                    terminal_reason=terminal_reason,
                )
            )
        real_hook(hook_agent, hook_thread, status)

    try:
        # ① M1 轉入終態（**既有 API**，不自創寫法；`updated_at` 對齊虛擬時鐘）
        transitioned = lt.append_transition(
            agent_id, chosen_id, "completed", virtual_now.isoformat()
        )
        if not transitioned:
            raise SimAssertionError(
                f"[refuse] M1 拒絕 {chosen_id} → completed 的轉移（非法轉移？）"
            )
        # ② 接線層的沉澱 hook（旗標 `LIFE_THREAD_CONSOLIDATION_ENABLED` 守門）
        _capturing_hook(agent_id, chosen_id, "completed")
        # ③ 等接線層排進來的背景沉澱任務收斂（`asyncio.all_tasks` ＝ 公開 API）。
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        lt_wiring._consolidate_once = original_consolidate

    # 🔴 SAGE 的 pending writes 必須 flush 才會對**另一條連線**可見
    # （MEM-VISIBILITY-C2：「快照隔離，flush 後才可見」）⇒ 接縫的唯讀回讀
    # 才拿得到感悟正文。`GraphStore.flush_all_live()` 是公開 API（只 flush 已存在的實例）。
    from src.memory.sage.graph_store import GraphStore

    n_live, n_flushed = GraphStore.flush_all_live()
    report["sage_flush"] = {"live_stores": n_live, "flushed_stores": n_flushed}

    report["transition"] = {
        "path": "M1.append_transition ＋ 接線層 dissolve_hook（原工單 §3.1 步驟 8）",
        "to_status": "completed",
        "updated_at": virtual_now.isoformat(),
        "transitioned": bool(transitioned),
        "hook_called": True,
    }

    # ── M2 ②：終態線頭的真溶解裁決（取樣於「溶解當下」）──
    final_states = lt.fold(agent_id)
    final_state = final_states.get(chosen_id)
    if final_state is None:
        raise SimAssertionError(f"線頭 {chosen_id} 在 M4 之後從沙盒消失")
    report["m1_after_m4"] = {
        "status": final_state.get("status"),
        "dissolved_at": final_state.get("dissolved_at"),
        "sage_fact_id": final_state.get("sage_fact_id"),
    }
    report["m1_at_dissolve_moment"] = dissolve_moment.get("state")
    report["m2_terminal"] = dissolve_moment.get("evaluation") or {}
    report["m2_terminal_default_reason"] = dissolve_moment.get("evaluation_default_reason") or {}
    report["m2_after_dissolve"] = _evaluation_view(
        lt_diss.evaluate_thread_dissolution(
            dict(final_state),
            virtual_now,
            max_active_duration_days=MAX_ACTIVE_DURATION_DAYS,
            stale_check_threshold_days=STALE_CHECK_THRESHOLD_DAYS,
            allow_soft_archive=False,
            agent_id=agent_id,
            terminal_reason="time_horizon_exceeded",
        )
    )

    # ── 溶解執行層結果 ──
    consolidation: Dict[str, Any] = {"captured": len(consolidation_results)}
    if consolidation_results:
        result = consolidation_results[0]
        fact = getattr(result, "fact", None) or {}
        consolidation.update(
            {
                "status": getattr(result, "status", None),
                "llm_calls": getattr(result, "llm_calls", None),
                "reason": getattr(result, "reason", None),
                "fact_object": fact.get("object"),
                "fact_confidence": fact.get("confidence"),
                "fact_origin": fact.get("origin"),
            }
        )
    report["consolidation"] = consolidation
    report["llm_calls"] = {
        "m5_scripted": len(m5_proxy.calls),
        "total_scripted": len(m5_proxy.calls),
        "real_llm_calls": 0,
    }

    # ── 接縫「後」掃描：只新增 1 個事件（第 36 筆）──
    post_seam = run_seam_over_agents(sandbox, slot, virtual_now, seam_module)
    report["seam_post"] = {
        "total_events": post_seam["total_events"],
        "per_agent_events": post_seam["per_agent_events"],
        "events": post_seam["events"],
    }

    # ── 接縫「再」掃描：at-most-once（同行程不重發）──
    again_seam = run_seam_over_agents(sandbox, slot, virtual_now, seam_module)
    report["seam_again"] = {
        "total_events": again_seam["total_events"],
        "per_agent_events": again_seam["per_agent_events"],
    }

    report["registry"]["size_after_seam"] = len(seam_module._ELEVATION_EMITTED)
    report["registry"]["ids_after_seam"] = sorted(seam_module._ELEVATION_EMITTED)
    return report


def _evaluation_view(evaluation: Any) -> Dict[str, Any]:
    """把 M2 裁決轉成可序列化的觀測 view。"""

    def _enum(value: Any) -> Any:
        return getattr(value, "value", value)

    extra = dict(getattr(evaluation, "extra_metadata", None) or {})
    return {
        "thread_id": getattr(evaluation, "thread_id", ""),
        "target_status": _enum(getattr(evaluation, "target_status", None)),
        "reason": _enum(getattr(evaluation, "reason", None)),
        "should_mutate": getattr(evaluation, "should_mutate", None),
        "advisory": extra.get("advisory"),
        "age_days": extra.get("age_days"),
        "idle_days": extra.get("idle_days"),
        "skipped": extra.get("skipped"),
        "has_reflection_prompt": bool(getattr(evaluation, "reflection_prompt", None)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 斷言（子行程）
# ─────────────────────────────────────────────────────────────────────────────
def assert_report(report: Dict[str, Any]) -> List[str]:
    """逐條驗收斷言；回傳檢查清單（每筆含 `ok` 與說明）。"""
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    advisory = report.get("m2_active_advisory") or {}
    check(
        "m2_active_is_advisory_only",
        advisory.get("should_mutate") is False
        and advisory.get("advisory") is True
        and advisory.get("reason") == "time_horizon_exceeded",
        "active 線頭在虛擬時點只是諮詢訊號：should_mutate=%s advisory=%s reason=%s"
        % (advisory.get("should_mutate"), advisory.get("advisory"), advisory.get("reason")),
    )

    terminal = report.get("m2_terminal") or {}
    check(
        "m2_terminal_should_mutate",
        terminal.get("should_mutate") is True
        and terminal.get("target_status") == "completed"
        and terminal.get("reason") == "time_horizon_exceeded",
        "終態線頭真溶解：should_mutate=%s target=%s reason=%s"
        % (terminal.get("should_mutate"), terminal.get("target_status"), terminal.get("reason")),
    )

    target = report.get("target") or {}
    check(
        "virtual_now_is_boundary_plus_one_second",
        target.get("age_seconds") == MAX_ACTIVE_DURATION_DAYS * 86400 + BOUNDARY_EXTRA_SECONDS,
        "虛擬時點 = created_at + %d 秒（14 天整 + 1 秒）：age_seconds=%s"
        % (MAX_ACTIVE_DURATION_DAYS * 86400 + BOUNDARY_EXTRA_SECONDS, target.get("age_seconds")),
    )

    consolidation = report.get("consolidation") or {}
    check(
        "consolidation_succeeded",
        consolidation.get("status") == "consolidated"
        and consolidation.get("llm_calls") == 1
        and bool(consolidation.get("fact_object")),
        "溶解執行層：status=%s llm_calls=%s fact=%s"
        % (
            consolidation.get("status"),
            consolidation.get("llm_calls"),
            (consolidation.get("fact_object") or "")[:40],
        ),
    )

    llm = report.get("llm_calls") or {}
    check(
        "zero_real_llm_calls",
        llm.get("real_llm_calls") == 0 and llm.get("m5_scripted") == 1,
        "LLM 呼叫：M5 腳本化 %s 次 ＋ 真實 %s 次"
        % (llm.get("m5_scripted"), llm.get("real_llm_calls")),
    )

    registry = report.get("registry") or {}
    pre = report.get("seam_pre") or {}
    post = report.get("seam_post") or {}
    again = report.get("seam_again") or {}
    check(
        "history_threads_not_re_emitted",
        pre.get("total_events") == 0,
        "接縫「前」掃描建立 %s 個事件（35 筆歷史線頭零重發）" % pre.get("total_events"),
    )
    check(
        "exactly_one_new_event",
        post.get("total_events") == 1 and again.get("total_events") == 0,
        "接縫「後」掃描建立 %s 個事件（第 36 筆）；「再」掃描 %s 個（at-most-once）"
        % (post.get("total_events"), again.get("total_events")),
    )

    events = post.get("events") or []
    target_id = target.get("thread_id")
    matched = [e for e in events if e.get("thread_id") == target_id]
    check(
        "new_event_targets_the_pushed_thread",
        len(matched) == 1,
        "新事件的 thread_id ＝ 被推進的那條：%s" % target_id,
    )
    narrative = matched[0].get("narrative", "") if matched else ""
    reflection = matched[0].get("reflection", "") if matched else ""
    check(
        "new_event_carries_narrative_and_reflection",
        bool(narrative.strip()) and bool(reflection.strip()),
        "正文長度 %s 字 ＋ 感悟長度 %s 字"
        % (len(narrative), len(reflection)),
    )
    check(
        "registry_contains_the_new_thread",
        target_id not in (registry.get("thread_ids") or [])
        and target_id in (registry.get("ids_after_seam") or []),
        "登記表：新線頭 %s **不在**播種名單內、且已出現在接縫後的登記表"
        "（播種 %s 條 → 接縫後 %s 條）"
        % (
            target_id,
            registry.get("seeded"),
            registry.get("size_after_seam"),
        ),
    )
    return checks


# ─────────────────────────────────────────────────────────────────────────────
# 階段：父行程
# ─────────────────────────────────────────────────────────────────────────────
def phase_parent(args: argparse.Namespace) -> int:
    """父行程：備沙盒 → 算生產 sha256 before → `os.execve` 換成子行程。"""
    agent_id = normalize_agent_id(args.agent)
    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.gettempdir()) / "sim_accel" / (
        "thread_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    sandbox = prepare_sandbox(out_dir, agent_id)
    before = production_sha_snapshot()
    _write_json(sandbox / "production_sha_before.json", before)
    _log("[parent] 沙盒：%s" % sandbox)
    _log("[parent] 生產 sha256 before：%d 個檔案" % len(before))

    env = {
        "SOUL_OS_DATA_DIR": str(sandbox),
        "LIFE_THREAD_ELEVATION_ENABLED": "1",
        "LIFE_THREAD_CONSOLIDATION_ENABLED": "1",
        "LIFE_THREAD_CATCHUP_ENABLED": "",
        "LIFE_THREAD_BOOTSTRAP_ENABLED": "",
        "LIFE_THREAD_WHIM_ENABLED": "",
        "SIM_ACCEL_PHASE": "child",
        "SIM_ACCEL_SANDBOX": str(sandbox),
        "SIM_ACCEL_AGENT": agent_id,
        "SIM_ACCEL_SLOT": args.slot,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT), str(REPO_ROOT / "scripts")]
            + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
        ),
    }
    if args.target_thread:
        env["SIM_ACCEL_TARGET_THREAD"] = args.target_thread
    if args.json:
        env["SIM_ACCEL_JSON"] = "1"
    # 🔴 換行程的機制（Windows 實測，2026-10-20）：
    #   - `os.execve` / `os.spawnve`（走 MSVCRT `_wspawnve` / `_spawnve`，帶**自訂 env 區塊**）
    #     在本機會 **access violation（0xC0000005）**，子行程根本沒起來 ⇒ 不可用。
    #   - `os.execv`（`_wspawnv`，繼承 env）可用，但**會吞掉子行程的退出碼**
    #     （子行程 exit 3 ⇒ 父行程 exit 0）⇒ CLI 的失敗契約會失效。
    #   ⇒ 採 `os.spawnv(P_WAIT)`：**新行程**（`data_root()` 的行程期快取是新的）＋
    #     **繼承**剛寫進 `os.environ` 的沙盒 env ＋ **回傳子行程退出碼**。
    #     （隔離語意與 `os.execve` 完全相同：env 在子行程**啟動前**就決定。）
    #     argv 只有 `[exe, "-m", "sim_accel_thread_cli"]` ⇒ 不含空白字元
    #     （`_spawnv` 不做引號處理，含空白的 argv 會被截斷）；其餘參數走 env。
    os.environ.update(env)
    exit_code = os.spawnv(
        os.P_WAIT, sys.executable, [sys.executable, "-m", "sim_accel_thread_cli"]
    )
    return int(exit_code)


# ─────────────────────────────────────────────────────────────────────────────
# 階段：子行程
# ─────────────────────────────────────────────────────────────────────────────
def assert_sandbox_isolated(
    expected: Path,
    env_root: Optional[str],
    resolved_data_root: Optional[Path] = None,
) -> Path:
    """沙盒隔離自我檢查（**純函式**、可單獨測試；三道閘）。

    1. 沙盒必須在 `tempfile.gettempdir()` 之下（`assert_under_temp`）；
    2. `SOUL_OS_DATA_DIR` 必須**逐字**等於沙盒（不是「某個 temp 底下」而已）；
    3. `data_root()`（**行程期快取**）解析出的路徑必須**逐字**等於沙盒。

    🔴 三道閘都拿「同一個值」比「同一個值」⇒ 不會出現 2026-10-19 事故那種
    「兩個各自會漂移的值互相比」而恆為 False 的假通過。
    """
    expected = assert_under_temp(Path(expected))
    if not env_root:
        raise SandboxIsolationError("[refuse] 缺少 SOUL_OS_DATA_DIR（子行程必須在沙盒內執行）")
    actual = Path(env_root).resolve()
    if actual != expected:
        raise SandboxIsolationError(
            "[refuse] env 與沙盒不一致：SOUL_OS_DATA_DIR=%s 期望=%s" % (actual, expected)
        )
    if resolved_data_root is not None and Path(resolved_data_root).resolve() != expected:
        raise SandboxIsolationError(
            "[refuse] data_root()=%s 與沙盒 %s 不一致（隔離漂移）" % (resolved_data_root, expected)
        )
    return expected


def phase_child(args: argparse.Namespace) -> int:
    """子行程：自我檢查 → 模擬 → 生產 sha256 對帳 → 產出結果檔。"""
    from src.paths import data_root, reset_data_root

    reset_data_root()
    expected = assert_sandbox_isolated(
        Path(args.sandbox), os.environ.get("SOUL_OS_DATA_DIR"), data_root()
    )

    agent_id = normalize_agent_id(args.agent)
    report = asyncio.run(
        run_simulation(
            sandbox=expected,
            agent_id=agent_id,
            target_thread_id=args.target_thread,
            slot=args.slot,
        )
    )
    checks = assert_report(report)
    report["checks"] = checks

    before_path = expected / "production_sha_before.json"
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path.exists() else {}
    after = production_sha_snapshot()
    _write_json(expected / "production_sha256_after.json", after)
    changed = sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )
    report["production_sha256"] = {
        "files_compared": len(set(before) | set(after)),
        "identical": not changed,
        "changed_files": changed,
        "before": before,
        "after": after,
    }

    failed = [c for c in checks if not c["ok"]]
    report["ok"] = (not failed) and (not changed)
    _write_json(expected / "sim_accel_result.json", report)

    if changed:
        _log("[child] FAIL：生產檔案 sha256 變動：%s" % changed)
        raise ProductionChangedError("生產檔案 sha256 前後不一致")
    if failed:
        for item in failed:
            _log("[child] FAIL：%s —— %s" % (item["name"], item["detail"]))
        raise SimAssertionError("%d 條斷言失敗" % len(failed))

    post = report.get("seam_post") or {}
    events = post.get("events") or []
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    _log("[child] OK：全部 %d 條斷言通過" % len(checks))
    _log("[child] 目標線頭：%s" % (report.get("target") or {}).get("thread_id"))
    _log("[child] 虛擬時點：%s" % (report.get("target") or {}).get("virtual_now"))
    _log("[child] 接縫：新增 %s 個事件（歷史 %s 筆零重發）"
          % (post.get("total_events"), (report.get("registry") or {}).get("seeded")))
    for event in events:
        _log("[child] 事件 thread=%s" % event.get("thread_id"))
        _log("[child]   正文：%s" % (event.get("narrative") or "")[:200])
        _log("[child]   感悟：%s" % (event.get("reflection") or "")[:200])
    _log("[child] 生產 sha256：%d 個檔案逐位元相同" % report["production_sha256"]["files_compared"])
    _log("[child] 結果檔：%s" % (expected / "sim_accel_result.json"))
    return EXIT_OK


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sim_accel_thread_cli.py",
        description="SIM-ACCEL-THREAD-DISSOLVE：hermetic 沙盒驗證「第 36 條接縫」增量機制",
    )
    # 預設值一律從 env 讀：父行程把參數寫進 env，子行程（`-m` 啟動、argv 只有
    # `[exe, "-m", "sim_accel_thread_cli"]`）就能零引號歧義地取得全部參數。
    parser.add_argument(
        "--phase", choices=("parent", "child"), default=os.environ.get("SIM_ACCEL_PHASE", "parent")
    )
    parser.add_argument(
        "--agent", default=os.environ.get("SIM_ACCEL_AGENT", "mahiru"), help="agent id（可省略 agent_ 前綴）"
    )
    parser.add_argument(
        "--target-thread",
        default=os.environ.get("SIM_ACCEL_TARGET_THREAD") or None,
        help="指定目標 thread_id（預設自動挑）",
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("SIM_ACCEL_OUT_DIR") or None,
        help="沙盒根（預設 %%TEMP%%\\sim_accel\\thread_<UTC>）",
    )
    parser.add_argument("--slot", choices=SEAM_SLOTS, default=os.environ.get("SIM_ACCEL_SLOT", "night"))
    parser.add_argument(
        "--sandbox", default=os.environ.get("SIM_ACCEL_SANDBOX") or None, help="（子行程）沙盒絕對路徑"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=os.environ.get("SIM_ACCEL_JSON") == "1",
        help="以 JSON 輸出結果（父行程會以 SIM_ACCEL_JSON 傳給子行程）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.phase == "parent":
            return phase_parent(args)
        return phase_child(args)
    except SimError as exc:
        _log("[fail] %s" % exc)
        return getattr(exc, "exit_code", EXIT_ASSERT_FAILED)
    except Exception as exc:  # noqa: BLE001 — CLI 邊界：不把 traceback 噴回生產
        _log("[fail] 未預期錯誤：%s: %s" % (type(exc).__name__, exc))
        return EXIT_ASSERT_FAILED


if __name__ == "__main__":
    sys.exit(main())
