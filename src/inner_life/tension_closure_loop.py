"""src/inner_life/tension_closure_loop.py — 短期思考閉環（**沙盒原型**）。

CAMPAIGN-B-P1 §2.3（工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md）
CAMPAIGN-B-P2 §3.2（工單：logs/CAMPAIGN-B-P2-D1-THINKING-LOOP-SANDBOX-WORK-ORDER.md）

三段閉環
--------
1. **張力驅動思考**：由 ``tension_scoring`` 取字典序 top-1 張力 ⇒ 組出
   :class:`ThinkRequest`（**純資料**：agent_id、drive_code、三因子快照、source_rows）。
2. **（Phase 2）張力驅動思考提詞**：由 ``tension_thinking.build_thinking_prompt``
   組出決定性提示詞 ⇒ 交給注入的思考器（預設
   :class:`~src.inner_life.tension_thinking.DeterministicMockThinker`）⇒ 得
   :class:`~src.inner_life.tension_thinking.ThinkOutcome`。
   🔴 **仍不呼叫 LLM**（Phase 2 是離線沙盒版；真實 LLM 呼叫屬 Phase 3）。
3. **思考消解張力**：依 outcome 分流 —— ``decided=True`` ⇒ 事件狀態 ``resolved``；
   ``decided=False`` ⇒ 事件狀態 ``partially_resolved``（**新值**）＋ 記錄
   ``partial_resolution``／``remaining_gap_count``／``reason``。
   寫入**沙盒** ``epistemic_tensions.jsonl``（append-only，**與生產同欄位形狀**）。
4. **更新 working state**：寫入**沙盒** ``working_state.jsonl``（**新承載**，
   append-only）。

開關與隔離（硬性）
------------------
- :func:`feature_enabled` **預設 ``False``**；OFF 時 :func:`drive_loop`
  **不建立任何檔案、不讀任何生產路徑、直接回 ``None``。
- :func:`drive_loop` **必須**拒絕 ``store_dir`` 位於生產 ``data/`` 之下
  （以 ``src.paths.data_root()`` 解析結果判斷；**若解析不可得則 fail-closed
  拒絕執行**，不猜）。
  ⚠️ 2026-10-19 事故教訓：生產目錄的解析值在行程期可能與子行程即時讀取值不同
  ⇒ 本模組採**雙條件**：「只允許寫入 ``store_dir``」＋「``store_dir`` 必須由
  呼叫端建立且位於 **temp 之下**」；**不以「兩個值相等」作為唯一守門條件**。
- **不接** ``src/llm/proxy.py``、``src/inner_life/epistemic_tension_scheduler.py``、
  任何排程器、任何 handler（本票不部署）。
- **不寫入** ``data/soul/motive_trace.jsonl`` 或任何既有承載（motive 語義不同：
  motive 是「對外意圖」，working state 是「角色自身認知狀態」）。

Phase 4（新增；工單：logs/CAMPAIGN-B-P4-ASYNC-THINKING-LOOP-WORK-ORDER.md）
--------------------------------------------------------------------------
- **async 閉環**：:func:`drive_loop_async`（``async def``）—— 讓非同步思考
  （:class:`~src.inner_life.tension_thinking.LLMThinker.think`）在**不阻塞 Event Loop**
  的前提下驅動 Working State。
- 🔴 **兩條路徑互斥且明確 raise**：同步 :func:`drive_loop` 收到 **async** thinker ⇒
  ``TypeError``；async :func:`drive_loop_async` 收到**同步** thinker ⇒ ``TypeError``。
  （**不得**靜默、**不得**「湊合著跑」⇒ 不允許任何一條路徑偷偷跑另一條的分支。）
- 🔴 **共用同一核心**（守衛 P5-T4）：階段 1（取張力）與階段 2／3（分流、寫檔、欄位）
  由 :func:`_prepare_loop` ＋ :func:`_finish_loop` 共用 ⇒ **同一輸入、同一 outcome ⇒
  兩條路徑寫出的 working_state ／張力事件逐位元相同**（不是兩套語意、兩個真相來源）。
- **稽核錨點語意變更**（工單 §2.3）：``think_prompt_sha256`` 從「Phase 2 提示詞的
  sha256」改為「**本次思考實際送出的** prompt 的 sha256」⇒ **由送出者回報**
  （``ThinkOutcome.think_prompt_sha256``），**接收者不得自行重算**。
- **唯讀上下文接口**：:func:`format_working_state_lines`（**沙盒專用**；過
  :func:`check_store_dir` 的守門）＋ :func:`format_working_state_lines_production`
  （**Phase 5-① 新增**：**路徑不可注入**的生產唯讀介面，供 ``proxy.py`` 接線）。
  兩者共用同一份格式化核心 :func:`_read_working_state_lines`。

working state 欄位（固定）
--------------------------
Phase 1（既有 7 欄）：``agent_id``、``ts``、``resolved_tension_id``、``drive_code``、
``drives_touched``、``open_gap_count_after``、``open_contradiction_count_after``。

Phase 2（新增 6 欄，工單 §3.2(d)）：``open_gap_count_before``、``partial_resolution``、
``remaining_gap_count``、``decided``、``thinker``（類別名）、``think_prompt_sha256``
（64 hex）。

- ``drives_touched`` ＝ 本次快照中**有未解項的張力驅動目標**（Phase 1 **僅 D1**；D3 已自
  驅動目標移除，Owner 裁定 A 2026-10-22 ⇒ 未決矛盾數即使 > 0 也不列入）；``drive_code``
  ＝ 本次**實際消解**的那一條。
- ``open_gap_count_after`` ＝ 思考後仍開著的缺口數（＝ ``outcome.remaining_gap_count``）。
  ⚠️ Phase 2 語意：預設 mock **每次只消解 1 個缺口** ⇒ 與 Phase 1 的「−1」語意
  **等值**；注入 :class:`~src.inner_life.tension_thinking.NullThinker` 時則**不減**
  （誠實回報「沒消解」）。**不**回寫 life_threads ⇒ 線頭檔不變。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.inner_life.drive_profile import load_drive_profile
from src.inner_life.tension_scoring import (
    DRIVE_INFO_GAP,
    TensionScore,
    score_tensions,
)
from src.inner_life.tension_thinking import (
    DeterministicMockThinker,
    ThinkOutcome,
    Thinker,
    build_thinking_prompt,
)

logger = logging.getLogger("soul_os.inner_life.tension_closure_loop")

#: 本票的**唯一人工 opt-in 旗標**（缺省＝關閉 ⇒ 生產零行為變化）。
FEATURE_ENV = "SOUL_OS_TENSION_DRIVE_LOOP"

#: 旗標的關閉值（小寫比對；缺省＝未設定＝關閉）。
FEATURE_OFF_VALUES: frozenset = frozenset({"off", "0", "false", "no", "none"})

#: 沙盒張力承載檔名（**與生產同名同形**；append-only JSONL）。
TENSION_STORE_FILENAME = "epistemic_tensions.jsonl"

#: working state 承載檔名（**新承載**；append-only JSONL）。
WORKING_STATE_FILENAME = "working_state.jsonl"

#: 張力事件的**狀態值**（Phase 2 新增「部分消解」一態）。
#:
#: - ``resolved`` ＝ 思考後缺口全消（``outcome.decided is True``）。
#: - ``partially_resolved`` ＝ 思考後**仍有缺口**（``outcome.decided is False``）。
TENSION_STATUS_RESOLVED = "resolved"
TENSION_STATUS_PARTIALLY_RESOLVED = "partially_resolved"

#: repo 根（生產 ``data/`` 的靜態位置；用於隔離的第二道條件）。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: working state 的欄位（固定；Phase 1 的 7 欄 ＋ Phase 2 新增的 6 欄）。
WORKING_STATE_FIELDS: Tuple[str, ...] = (
    "agent_id",
    "ts",
    "resolved_tension_id",
    "drive_code",
    "drives_touched",
    "open_gap_count_after",
    "open_contradiction_count_after",
    # ── CAMPAIGN-B-P2 §3.2(d) 新增 6 欄（既有 7 欄一個字都沒動）────────────
    "open_gap_count_before",
    "partial_resolution",
    "remaining_gap_count",
    "decided",
    "thinker",
    "think_prompt_sha256",
)


# ── 資料模型 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ThinkRequest:
    """「張力驅動思考」的輸入（**純資料**；Phase 1 不呼叫 LLM）。"""

    agent_id: str
    drive_code: str
    temporal_state: str
    decay_bucket: str
    info_gap_count: int
    unresolved_count: int
    source_rows: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "drive_code": self.drive_code,
            "temporal_state": self.temporal_state,
            "decay_bucket": self.decay_bucket,
            "info_gap_count": self.info_gap_count,
            "unresolved_count": self.unresolved_count,
            "source_rows": list(self.source_rows),
        }


@dataclass(frozen=True)
class ClosureTrace:
    """一次閉環的可稽核結果（沙盒）。"""

    agent_id: str
    drive_code: str
    tension_id: str
    resolved_at: float
    think_request: ThinkRequest
    tension_store_path: str
    working_state_path: str
    drives_touched: Tuple[str, ...]
    open_gap_count_after: int
    open_contradiction_count_after: int
    # ── CAMPAIGN-B-P2 §3.2(e) 新增（既有欄位一個都沒刪、沒改名）──────────
    partial_resolution: float = 0.0
    remaining_gap_count: int = 0
    decided: bool = False
    think_prompt_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "drive_code": self.drive_code,
            "tension_id": self.tension_id,
            "resolved_at": self.resolved_at,
            "think_request": self.think_request.to_dict(),
            "tension_store_path": self.tension_store_path,
            "working_state_path": self.working_state_path,
            "drives_touched": list(self.drives_touched),
            "open_gap_count_after": self.open_gap_count_after,
            "open_contradiction_count_after": self.open_contradiction_count_after,
            "partial_resolution": float(self.partial_resolution),
            "remaining_gap_count": int(self.remaining_gap_count),
            "decided": bool(self.decided),
            "think_prompt_sha256": str(self.think_prompt_sha256),
        }


# ── 旗標 ─────────────────────────────────────────────────────────────────
def feature_enabled() -> bool:
    """本票旗標是否開啟（**缺省關閉** ⇒ 生產零行為變化）。

    解析規則與 repo 其他旗標一致：**非空且非 off/0/false/no/none 即 ON**；
    變數「缺席」**不**代表 ON。
    """
    raw = os.environ.get(FEATURE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in FEATURE_OFF_VALUES


# ── 隔離守門 ─────────────────────────────────────────────────────────────
def _is_under(target: Path, base: Path) -> bool:
    """``target`` 是否在 ``base`` 之下（含相等）。"""
    try:
        return target == base or base in target.parents
    except Exception:  # noqa: BLE001 — fail-closed
        return False


def _production_data_root() -> Optional[Path]:
    """生產資料根（``src.paths.data_root()`` 解析結果）；解析不可得 ⇒ ``None``。"""
    try:
        from src.paths import data_root

        return Path(data_root()).resolve()
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.debug(
            "[tension-closure-loop] 生產資料根解析失敗：%s: %s", type(exc).__name__, exc
        )
        return None


def _temp_root() -> Optional[Path]:
    """temp 根（沙盒必須位於其下）；解析不可得 ⇒ ``None``。"""
    try:
        return Path(tempfile.gettempdir()).resolve()
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.debug("[tension-closure-loop] temp 根解析失敗：%s: %s", type(exc).__name__, exc)
        return None


def check_store_dir(store_dir: Any) -> Tuple[bool, str]:
    """沙盒 ``store_dir`` 是否可用（**雙條件**：不是生產 ＋ 位於 temp 之下）。

    回傳 ``(允許?, 理由)``。任一條件不滿 ⇒ 拒絕（fail-closed）。
    """
    try:
        target = Path(store_dir).resolve()
    except Exception as exc:  # noqa: BLE001 — fail-closed
        return False, f"store_dir 無法解析：{type(exc).__name__}: {exc}"

    prod_root = _production_data_root()
    if prod_root is None:
        # 生產資料根解析不可得 ⇒ 不猜 ⇒ 拒絕執行（工單 §2.3）。
        return False, "生產資料根解析不可得 ⇒ fail-closed"
    if _is_under(target, prod_root):
        return False, "store_dir 位於生產資料根之下"

    repo_data = (REPO_ROOT / "data").resolve()
    if _is_under(target, repo_data):
        return False, "store_dir 位於 repo data/ 之下"

    temp_root = _temp_root()
    if temp_root is None:
        return False, "temp 根解析不可得 ⇒ fail-closed"
    if not _is_under(target, temp_root):
        return False, "store_dir 不在 temp 之下（非沙盒）"

    return True, "ok"


# ── 寫入（append-only；只寫 store_dir）───────────────────────────────────
def _append_jsonl(path: Path, record: Dict[str, Any]) -> bool:
    """把一筆記錄追加進 JSONL（append-only；單次寫入 ⇒ 不交錯）。"""
    blob = (json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from src.fsio.atomic_append import append_bytes

        append_bytes(path, blob)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-closure-loop] 沙盒寫入失敗（%s）：%s: %s",
            path,
            type(exc).__name__,
            exc,
        )
        return False


def _make_tension_id(agent_id: str, score: TensionScore) -> str:
    """決定性的張力 id（同樣快照 ⇒ 同一 id ⇒ 冪等）。"""
    material = "|".join(
        [
            str(agent_id),
            str(score.drive_code),
            str(score.temporal_state),
            str(score.decay_bucket),
            str(score.info_gap_count),
            str(score.unresolved_count),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"drive_tension:{digest}"


def _tension_event(
    agent_id: str,
    score: TensionScore,
    tension_id: str,
    resolved_at: float,
    last_interaction_ts: int,
    bond_payload: Dict[str, Any],
    outcome: ThinkOutcome,
) -> Dict[str, Any]:
    """組一筆張力事件（**欄位形狀與生產一致**：ts/agent_id/tension_id/kind/
    subject/status/payload）。

    🔴 Phase 2（工單 §3.2(c)）：狀態**依 outcome 分流** ——
    ``decided=True`` ⇒ ``resolved``；``decided=False`` ⇒ ``partially_resolved``
    （新值）＋ 於 payload 記錄 ``partial_resolution``／``remaining_gap_count``／
    ``decided``／``reason``。頂層欄位集合**不變**（仍是 TENSION_EVENT_FIELDS）。
    """
    from src.inner_life.epistemic_action import TENSION_EVENT_RESOLVED

    status = (
        TENSION_STATUS_RESOLVED if outcome.decided else TENSION_STATUS_PARTIALLY_RESOLVED
    )
    verb = "消解" if outcome.decided else "部分消解"
    payload = {
        "agent_id": agent_id,
        "bond": bond_payload,
        "created_at": resolved_at,
        "kind": "drive_tension",
        "last_interaction_ts": int(last_interaction_ts),
        "session_context": "",
        "source": "drive_scoring",
        "subject": score.drive_code,
        "summary": (
            f"{score.drive_code} 驅動張力{verb}"
            f"（資訊缺口 {score.info_gap_count} 筆／未決矛盾 {score.unresolved_count} 筆）"
        ),
        "tags": [],
        "tension_id": tension_id,
        "utterance": "",
        "world_keys": [],
        "resolved_at": resolved_at,
        "drive_snapshot": {
            "drive_code": score.drive_code,
            "temporal_state": score.temporal_state,
            "decay_bucket": score.decay_bucket,
            "info_gap_count": score.info_gap_count,
            "unresolved_count": score.unresolved_count,
            "source_rows": list(score.source_rows),
        },
        # ── CAMPAIGN-B-P2 §3.2(c)：思考結果（新增；頂層欄位不動）──────────
        "partial_resolution": float(outcome.partial_resolution),
        "remaining_gap_count": int(outcome.remaining_gap_count),
        "decided": bool(outcome.decided),
        "reason": str(outcome.reason),
    }
    return {
        "ts": resolved_at,
        "agent_id": agent_id,
        "tension_id": tension_id,
        "kind": TENSION_EVENT_RESOLVED,
        "subject": score.drive_code,
        "status": status,
        "payload": payload,
    }


def _working_state_row(
    agent_id: str,
    ts: float,
    tension_id: str,
    drive_code: str,
    drives_touched: List[str],
    open_gap_count_after: int,
    open_contradiction_count_after: int,
    *,
    open_gap_count_before: int,
    partial_resolution: float,
    remaining_gap_count: int,
    decided: bool,
    thinker: str,
    think_prompt_sha256: str,
) -> Dict[str, Any]:
    """組一筆 working state（Phase 1 的 7 欄 ＋ Phase 2 新增的 6 欄）。

    🔴 工單 §3.2(d)：既有 7 欄**一個字都沒改**（順序不變）；新增 6 欄附加在後。
    """
    return {
        "agent_id": agent_id,
        "ts": ts,
        "resolved_tension_id": tension_id,
        "drive_code": drive_code,
        "drives_touched": list(drives_touched),
        "open_gap_count_after": int(open_gap_count_after),
        "open_contradiction_count_after": int(open_contradiction_count_after),
        "open_gap_count_before": int(open_gap_count_before),
        "partial_resolution": float(partial_resolution),
        "remaining_gap_count": int(remaining_gap_count),
        "decided": bool(decided),
        "thinker": str(thinker),
        "think_prompt_sha256": str(think_prompt_sha256),
    }


# ── 主入口 ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _PreparedLoop:
    """``_run_loop()`` 的**階段 1 產物**（階段 1.5 之前的全部決定性內容）。

    🔴 同步與 async 兩條路徑**共用**本物件 ⇒ 同一輸入 ⇒ 逐位元相同
    （守衛 P5-T4：兩條路徑的輸出必須逐位元相同，不是兩套語意、兩個真相來源）。
    """

    agent_id: str
    score: TensionScore
    request: ThinkRequest
    prompt: str
    tension_id: str
    resolved_at: float
    last_interaction_ts: int
    bond_payload: Dict[str, Any]
    store_path: Path
    working_path: Path
    thinker_name: str


def _thinker_is_async(thinker: Any) -> bool:
    """思考器的 ``think()`` 是否為 **coroutine function**（async thinker 判定）。

    🔴 判定**只看宣告**（``async def think``）⇒ 同步的橋接器（``def think`` 內部
    ``asyncio.run``）仍被視為**同步** thinker ⇒ 走同步路徑（Phase 3 的守衛
    ``_SyncLLMThinkerBridge`` 正是這種情形）。
    """
    think = getattr(thinker, "think", None)
    if think is None:
        return False
    try:
        return bool(inspect.iscoroutinefunction(think))
    except Exception:  # noqa: BLE001 — fail-closed（視為「非 async」）
        return False


def _reported_prompt_sha(outcome: Any) -> str:
    """outcome **回報**的提示詞 sha256（**送出者回報**；接收者**不得**重算）。

    🔴 工單 §2.3：稽核錨點必須由**送出者**回報 —— 接收者自行重算＝指回自己組的
    提示詞＝「稽核指針指錯對象」（Phase 3 的原始缺陷）。
    送出者未回報（缺欄位／空字串）⇒ **誠實留白** ``""``（不猜）。
    """
    return str(getattr(outcome, "think_prompt_sha256", "") or "")


def _prepare_loop(
    agent_id: str,
    now: int,
    store_dir: Any,
    thinker_name: str,
) -> Optional[_PreparedLoop]:
    """階段 1（取張力）＋ 階段 1.5 的前半（組 ``ThinkRequest`` 與提示詞）。

    回傳 ``None`` 的情形（**皆不寫任何檔案**）：旗標關閉；``store_dir`` 不合格；
    該 agent 沒有可解析的驅動剖面；沒有任何張力；top-1 張力的驅動代碼不在剖面內。

    🔴 **同步與 async 兩條路徑共用本函式** ⇒ 同一輸入 ⇒ 同一份決定性內容。
    """
    # ① 旗標（**最先**；OFF ⇒ 0 讀 0 寫）。
    if not feature_enabled():
        return None

    # ② 沙盒隔離（雙條件；fail-closed）。
    allowed, reason = check_store_dir(store_dir)
    if not allowed:
        logger.debug("[tension-closure-loop] 拒絕執行：%s", reason)
        return None

    # ③ 驅動剖面（唯讀；本票僅 agent_akane 可解析）。
    profile = load_drive_profile(agent_id)
    if profile is None:
        return None
    profile_codes = set(profile.codes())

    # ④ 評分（唯讀、零 LLM）。
    scores = score_tensions(agent_id, int(now))
    if not scores:
        return None
    top = scores[0]
    if top.drive_code not in profile_codes:
        return None

    # ⑤ 張力驅動思考（純資料；**不呼叫 LLM**）。
    think = ThinkRequest(
        agent_id=agent_id,
        drive_code=top.drive_code,
        temporal_state=top.temporal_state,
        decay_bucket=top.decay_bucket,
        info_gap_count=top.info_gap_count,
        unresolved_count=top.unresolved_count,
        source_rows=list(top.source_rows),
    )

    # ⑥ 事件骨架（決定性；兩條路徑相同）。
    resolved_at = float(int(now))
    tension_id = _make_tension_id(agent_id, top)
    try:
        from src.inner_life.epistemic_action import read_last_interaction_ts

        last_interaction_ts = int(read_last_interaction_ts(agent_id))
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-closure-loop] 最後互動時間讀取失敗：%s: %s", type(exc).__name__, exc
        )
        last_interaction_ts = 0
    bond_payload = _bond_payload(agent_id)

    # ⑦ 階段 1.5 的提示詞（**決定性**；兩條路徑逐位元相同）。
    prompt = build_thinking_prompt(think)

    return _PreparedLoop(
        agent_id=agent_id,
        score=top,
        request=think,
        prompt=prompt,
        tension_id=tension_id,
        resolved_at=resolved_at,
        last_interaction_ts=last_interaction_ts,
        bond_payload=bond_payload,
        store_path=Path(store_dir) / TENSION_STORE_FILENAME,
        working_path=Path(store_dir) / WORKING_STATE_FILENAME,
        thinker_name=str(thinker_name),
    )


def _finish_loop(prepared: _PreparedLoop, outcome: ThinkOutcome) -> Optional[ClosureTrace]:
    """階段 2（分流）＋ 階段 3（寫 working state）—— **同步與 async 共用**。

    🔴 寫入**只**發生在 ``prepared.store_path`` 與 ``prepared.working_path``
    （皆在沙盒 ``store_dir`` 之下 ⇒ 生產路徑結構上不可達）。
    """
    # 階段 2：思考消解張力（寫沙盒；欄位形狀與生產一致）。
    event = _tension_event(
        prepared.agent_id,
        prepared.score,
        prepared.tension_id,
        prepared.resolved_at,
        prepared.last_interaction_ts,
        prepared.bond_payload,
        outcome,
    )
    if not _append_jsonl(prepared.store_path, event):
        return None

    # 階段 3：更新 working state（寫沙盒；新承載）。
    drives_touched = _drives_touched(prepared.score)
    # Phase 2 語意：思考後仍開著的缺口數＝outcome.remaining_gap_count
    #（預設 mock 每次只消解 1 個 ⇒ 與 Phase 1 的「−1」等值；Null 對照組則不減）。
    gap_after = max(0, int(outcome.remaining_gap_count))
    gap_before = int(prepared.score.info_gap_count)
    # D3 已非張力驅動目標（Owner 裁定 A，2026-10-22）⇒ 消解 D1 **不會**減少未決矛盾數
    #（該因子自裁定起僅為觀測值，誠實回報、不驅動任何張力）。
    contradiction_after = int(prepared.score.unresolved_count)
    # 🔴 稽核錨點：**取自 outcome**（送出者回報）⇒ 不得自行重算（工單 §2.3）。
    prompt_sha256 = _reported_prompt_sha(outcome)
    row = _working_state_row(
        prepared.agent_id,
        prepared.resolved_at,
        prepared.tension_id,
        prepared.score.drive_code,
        drives_touched,
        gap_after,
        contradiction_after,
        open_gap_count_before=gap_before,
        partial_resolution=float(outcome.partial_resolution),
        remaining_gap_count=int(outcome.remaining_gap_count),
        decided=bool(outcome.decided),
        thinker=prepared.thinker_name,
        think_prompt_sha256=prompt_sha256,
    )
    if not _append_jsonl(prepared.working_path, row):
        return None

    return ClosureTrace(
        agent_id=prepared.agent_id,
        drive_code=prepared.score.drive_code,
        tension_id=prepared.tension_id,
        resolved_at=prepared.resolved_at,
        think_request=prepared.request,
        tension_store_path=str(prepared.store_path),
        working_state_path=str(prepared.working_path),
        drives_touched=tuple(drives_touched),
        open_gap_count_after=gap_after,
        open_contradiction_count_after=contradiction_after,
        partial_resolution=float(outcome.partial_resolution),
        remaining_gap_count=int(outcome.remaining_gap_count),
        decided=bool(outcome.decided),
        think_prompt_sha256=prompt_sha256,
    )


def _run_loop(
    agent_id: str,
    now: int,
    store_dir: Any,
    outcome_provider: Any,
    *,
    thinker_name: str = "",
) -> Optional[ClosureTrace]:
    """共享核心（**同步**）：階段 1 取張力 → 階段 1.5 取 outcome → 階段 2 分流
    → 階段 3 寫 working state。

    ``outcome_provider`` 是一個**同步** callable ``(req, prompt) -> ThinkOutcome``。

    🔴 async 路徑（:func:`drive_loop_async`）**共用**階段 1（:func:`_prepare_loop`）與
    階段 2／3（:func:`_finish_loop`）⇒ 兩條路徑的輸出**逐位元相同**（守衛 P5-T4）。
    （階段 1.5 本身必須由各自的路徑執行：同步＝直接呼叫；async＝``await``。）

    ``thinker_name``（keyword-only）＝寫進 working state 的 ``thinker`` 欄位
    （**類別名**；Phase 2 語意不變）。
    """
    prepared = _prepare_loop(agent_id, now, store_dir, thinker_name)
    if prepared is None:
        return None
    outcome = outcome_provider(prepared.request, prepared.prompt)
    return _finish_loop(prepared, outcome)


def drive_loop(
    agent_id: str,
    now: int,
    store_dir: Any,
    *,
    thinker: Optional[Thinker] = None,
) -> Optional[ClosureTrace]:
    """跑一次短期思考閉環（**沙盒**；OFF ⇒ ``None`` 且不建立任何檔案）。

    ``thinker``（**keyword-only**、預設 ``None``）＝ 注入的思考器；``None`` ⇒ 使用
    :class:`~src.inner_life.tension_thinking.DeterministicMockThinker`（決定性、
    **不燒 LLM**）。

    🔴 **相容性**（工單 §3.2(a)）：既有呼叫（不傳 ``thinker``）**位置參數與語意
    一律不變** —— 簽章只新增一個預設的 keyword-only 參數。既有生產呼叫者：**無**
    （實測全庫僅守衛測試呼叫本函式）。

    🔴 **只接受同步 thinker**（工單 §2.2）：收到 **async** thinker ⇒ **明確 raise**
    ``TypeError``（訊息指明改用 :func:`drive_loop_async`）⇒ **不得**靜默、**不得**
    「湊合著跑」。檢查在**最前面** ⇒ **不建立任何檔案**。

    回傳 ``ClosureTrace``；下列情形回 ``None``（皆**不寫任何檔案**）：

    - 旗標關閉（**第一道**；不讀任何生產路徑）；
    - ``store_dir`` 不合格（位於生產 ``data/`` 之下／不在 temp 之下／解析不可得）；
    - 該 agent 沒有可解析的驅動剖面（本票僅 ``agent_akane``）；
    - 沒有任何張力（三因子皆 0）；
    - top-1 張力的驅動代碼不在剖面內。
    """
    active_thinker: Thinker = thinker if thinker is not None else DeterministicMockThinker()

    # 🔴 互斥（工單 §2.2）：同步路徑**只**接受同步 thinker。
    if _thinker_is_async(active_thinker):
        raise TypeError(
            "drive_loop() 只接受**同步** thinker（think() 必須回傳 ThinkOutcome）；"
            "收到 async thinker（think() 回傳 awaitable）⇒ 請改用 drive_loop_async()。"
        )

    def _sync_provider(req: ThinkRequest, prompt: str) -> ThinkOutcome:
        return active_thinker.think(req, prompt)

    return _run_loop(
        agent_id,
        now,
        store_dir,
        _sync_provider,
        thinker_name=type(active_thinker).__name__,
    )


async def drive_loop_async(
    agent_id: str,
    now: int,
    store_dir: Any,
    *,
    thinker: Optional[Any] = None,
) -> Optional[ClosureTrace]:
    """跑一次短期思考閉環（**async**；沙盒；OFF ⇒ ``None`` 且不建立任何檔案）。

    🔴 **只接受 async thinker**（工單 §2.2）：``think()`` 必須回傳 awaitable。
    收到**同步** thinker ⇒ **明確 raise** ``TypeError``（訊息指明「請改用
    ``drive_loop()`` 或傳入 async thinker」）⇒ **不得**靜默、**不得**「湊合著跑」。
    ``thinker=None`` ⇒ 沒有可用的預設 async thinker ⇒ 同樣明確 raise。

    🔴 **不阻塞 Event Loop**：整個 async 函式內**沒有** ``time.sleep``、沒有同步阻塞
    呼叫；唯一的 await 點是 ``thinker.think(...)``。

    🔴 **與同步路徑共用同一核心**（:func:`_prepare_loop` ＋ :func:`_finish_loop`）
    ⇒ 同一輸入、同一 outcome ⇒ 兩條路徑寫出的 working_state ／張力事件**逐位元相同**
    （守衛 P5-T4）。
    """
    if thinker is None:
        raise TypeError(
            "drive_loop_async() 必須注入 **async** thinker（think() 必須回傳 awaitable）；"
            "沒有預設的 async thinker ⇒ 請改用 drive_loop()（同步路徑）。"
        )
    if not _thinker_is_async(thinker):
        raise TypeError(
            "drive_loop_async() 只接受 **async** thinker（think() 必須回傳 awaitable）；"
            "收到同步 thinker ⇒ 請改用 drive_loop() 或傳入 async thinker。"
        )

    prepared = _prepare_loop(agent_id, now, store_dir, type(thinker).__name__)
    if prepared is None:
        return None
    outcome = await thinker.think(prepared.request, prepared.prompt)
    return _finish_loop(prepared, outcome)


def _drives_touched(top: TensionScore) -> List[str]:
    """本次快照中有未解項的**張力驅動目標**（Phase 1 **僅 D1**）。

    🔴 D3 已自驅動目標移除（Owner 裁定 A，2026-10-22）⇒ ``unresolved_count`` 即使 > 0
    也**不得**列入 ``drives_touched``（守衛 T7／T11 釘死）。
    """
    touched: List[str] = []
    if top.info_gap_count > 0:
        touched.append(DRIVE_INFO_GAP)
    return touched


def _bond_payload(agent_id: str) -> Dict[str, Any]:
    """羈絆證據的可稽核視圖（形狀同生產張力 payload 的 ``bond``）。"""
    try:
        from src.inner_life.epistemic_action import read_bond_snapshot

        snapshot = read_bond_snapshot(agent_id)
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-closure-loop] 羈絆證據讀取失敗：%s: %s", type(exc).__name__, exc
        )
        snapshot = None
    if snapshot is None:
        return {"eligible": False, "basis": "P4_none", "impression_tags": []}
    return {
        "eligible": bool(snapshot.eligible),
        "basis": str(snapshot.basis),
        "impression_tags": list(snapshot.impression_tags),
    }


# ── 唯讀上下文接口（Phase 4 沙盒 ＋ Phase 5-① 生產唯讀）─────────────────
def _date_label(raw_ts: Any) -> str:
    """working state 記錄的日期標籤（``YYYY-MM-DD``）。

    🔴 生產 ``working_state.jsonl`` 的 ``ts`` 是 **epoch 秒（數字）** ⇒ 需轉成
    UTC 日期；字串型 ``ts``（ISO）則取**前 10 字元**（與 ``proxy.py`` 同款）。
    取不到 ⇒ ``????-??-??``（與 ``proxy.py`` 的 fallback 同款）。
    """
    if isinstance(raw_ts, str):
        return raw_ts[:10] or "????-??-??"
    try:
        return datetime.fromtimestamp(float(raw_ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return "????-??-??"


def _safe_int(value: Any) -> int:
    """寬鬆取整（缺值／壞值 ⇒ ``0``；**不得** raise）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    """寬鬆取浮點（缺值／壞值 ⇒ ``0.0``；**不得** raise）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _read_working_state_lines(
    agent_id: str,
    store_dir: Any,
    *,
    max_entries: int = 5,
    max_chars: int = 60,
) -> List[str]:
    """共享唯讀核心（**不做路徑守門**；守門由兩個公開介面各自負責）。

    本體自 ``format_working_state_lines()`` 原樣搬移，**逐字元不得改動**。

    🔺 搬移規則（工單 §2.2.1）：縮排維持 4 空白、行內容逐位元不變 ⇒ Phase 4 的
    變異錨點 M11／M12／M13 仍能逐位元命中。
    """
    path = Path(store_dir) / WORKING_STATE_FILENAME
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-closure-loop] 唯讀介面讀檔失敗：%s: %s", type(exc).__name__, exc
        )
        return []

    # ② 取**最後一筆有效記錄**（壞行跳過、不 raise）。
    record: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        # 別的角色的記錄不是本角色的狀態 ⇒ 跳過（欄位缺漏則不判）。
        owner = parsed.get("agent_id")
        if owner and str(owner) != str(agent_id):
            continue
        record = parsed
    if record is None:
        return []

    # ③ 已想完 ⇒ 無未解張力 ⇒ 空 list。
    if record.get("decided") is True:
        return []

    # ④ 未解 ⇒ 一行。
    remaining = _safe_int(record.get("remaining_gap_count"))
    partial = _safe_float(record.get("partial_resolution"))
    label = _date_label(record.get("ts"))
    line = f"- [{label} 張力未解] 還有 {remaining} 個未解的問題（已想過 {partial:.0%}）"

    # ⑤ 截斷（與 proxy.py 同款：切到 max_chars - 3 再加 "..."）。
    limit = int(max_chars)
    if limit > 0 and len(line) > limit:
        line = line[: limit - 3] + "..."

    # ⑥ 上限語意。
    return [line][: max(0, int(max_entries))]


def format_working_state_lines(
    agent_id: str,
    store_dir: Any,
    *,
    max_entries: int = 5,
    max_chars: int = 60,
) -> List[str]:
    """（Phase 4 契約，**語意完全不變**）沙盒 store_dir 的唯讀上下文行。

    把 working_state 的最新狀態轉成**唯讀**的內在生活風格行。

    格式**對齊**既有 Inner Life 區塊（``src/llm/proxy.py`` 的
    ``_format_recent_inner_life()``）：``- [<日期> <標籤>] <摘要 截斷至 60 字>``。

    🔴 **唯讀**：只讀 ``store_dir`` 下的 ``working_state.jsonl``；**不得**寫、
    **不得** append、**不得** checkpoint（守衛 P5-T11 以位元組對帳釘死）。

    🔴 **不接線**：本函式**只是確定性接口**（**沙盒專用**）；Phase 5-① 的 prompt
    組裝走的是 :func:`format_working_state_lines_production`，**不是**本函式 ⇒
    本函式**不得**接進 ``proxy.py`` 或任何 prompt 組裝路徑。

    行為（工單 §2.4，逐條）：

    1. ``store_dir`` 必須先過 :func:`check_store_dir` 的既有雙條件；不合格 ⇒
       **回空 list**（**不得** raise、**不得**寫入生產）。
    2. 檔案不存在／無有效記錄 ⇒ **回空 list**（fail-silent，絕不 raise）。
    3. 取**最後一筆有效記錄**（壞行跳過、不 raise）；該筆 ``decided is True``
       （已想完 ⇒ 無未解張力）⇒ **回空 list**。
    4. 否則回**一條**：
       ``"- [{YYYY-MM-DD} 張力未解] 還有 {remaining_gap_count} 個未解的問題（已想過 {partial_resolution:.0%}）"``
       （日期取該筆 ``ts``；``remaining_gap_count``／``partial_resolution`` 缺值時
       以 ``0``／``0.0`` 代入）。
    5. 整行**截斷至** ``max_chars``（60）⇒ 切到 ``max_chars - 3`` 再加 ``...``
       （與 ``proxy.py`` 既有截斷語意同款）。
    6. ``max_entries`` ＝ 上限（預設 5）；目前只輸出 1 條，但**介面必須支援上限語意**。

    🔴 **不得**呼叫 LLM、**不得**連網。
    """
    # ① 隔離守門（與閉環同一套雙條件）；不合格 ⇒ 空 list（不 raise）。
    allowed, reason = check_store_dir(store_dir)
    if not allowed:
        logger.debug("[tension-closure-loop] 唯讀介面拒絕：%s", reason)
        return []
    return _read_working_state_lines(
        agent_id, store_dir, max_entries=max_entries, max_chars=max_chars
    )


def format_working_state_lines_production(
    agent_id: str,
    *,
    max_entries: int = 5,
    max_chars: int = 60,
) -> List[str]:
    """（Phase 5 新增）生產 working_state 的唯讀上下文行。

    🔴 **路徑不可注入**：``store_dir`` **不在簽章裡**，由本函式內部從
    ``src.paths.data_root()`` 推導 ⇒ 呼叫端無法指定任何路徑。
    🔴 **唯讀**：只讀 ``<data_root>/inner_life/working_state.jsonl``；
    **不得**寫、**不得** append、**不得** checkpoint（守衛 P6-T14 以位元組對帳釘死）。
    🔴 **fail-silent**：解析失敗／檔案不存在／壞行 ⇒ 回空 list，**不得** raise。
    """
    try:
        from src.paths import data_root

        store_dir = Path(data_root()) / "inner_life"
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-closure-loop] 生產資料根解析失敗：%s: %s", type(exc).__name__, exc
        )
        return []
    return _read_working_state_lines(
        agent_id, store_dir, max_entries=max_entries, max_chars=max_chars
    )


__all__ = [
    "FEATURE_ENV",
    "FEATURE_OFF_VALUES",
    "TENSION_STATUS_PARTIALLY_RESOLVED",
    "TENSION_STATUS_RESOLVED",
    "TENSION_STORE_FILENAME",
    "WORKING_STATE_FIELDS",
    "WORKING_STATE_FILENAME",
    "ClosureTrace",
    "ThinkRequest",
    "check_store_dir",
    "drive_loop",
    "drive_loop_async",
    "feature_enabled",
    "format_working_state_lines",
    "format_working_state_lines_production",
]
