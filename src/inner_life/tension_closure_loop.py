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
import json
import logging
import os
import tempfile
from dataclasses import dataclass
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

    回傳 ``ClosureTrace``；下列情形回 ``None``（皆**不寫任何檔案**）：

    - 旗標關閉（**第一道**；不讀任何生產路徑）；
    - ``store_dir`` 不合格（位於生產 ``data/`` 之下／不在 temp 之下／解析不可得）；
    - 該 agent 沒有可解析的驅動剖面（本票僅 ``agent_akane``）；
    - 沒有任何張力（三因子皆 0）；
    - top-1 張力的驅動代碼不在剖面內。
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

    # ⑥ 思考消解張力（寫沙盒；欄位形狀與生產一致）。
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

    # ⑦ 階段 1.5（Phase 2）：張力驅動思考（**決定性**；**不呼叫 LLM**）。
    active_thinker: Thinker = thinker if thinker is not None else DeterministicMockThinker()
    prompt = build_thinking_prompt(think)
    outcome = active_thinker.think(think, prompt)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    store_path = Path(store_dir) / TENSION_STORE_FILENAME
    event = _tension_event(
        agent_id, top, tension_id, resolved_at, last_interaction_ts, bond_payload, outcome
    )
    if not _append_jsonl(store_path, event):
        return None

    # ⑧ 更新 working state（寫沙盒；新承載）。
    drives_touched = _drives_touched(top)
    # Phase 2 語意：思考後仍開著的缺口數＝outcome.remaining_gap_count
    #（預設 mock 每次只消解 1 個 ⇒ 與 Phase 1 的「−1」等值；Null 對照組則不減）。
    gap_after = max(0, int(outcome.remaining_gap_count))
    gap_before = int(top.info_gap_count)
    # D3 已非張力驅動目標（Owner 裁定 A，2026-10-22）⇒ 消解 D1 **不會**減少未決矛盾數
    #（該因子自裁定起僅為觀測值，誠實回報、不驅動任何張力）。
    contradiction_after = int(top.unresolved_count)
    working_path = Path(store_dir) / WORKING_STATE_FILENAME
    row = _working_state_row(
        agent_id,
        resolved_at,
        tension_id,
        top.drive_code,
        drives_touched,
        gap_after,
        contradiction_after,
        open_gap_count_before=gap_before,
        partial_resolution=float(outcome.partial_resolution),
        remaining_gap_count=int(outcome.remaining_gap_count),
        decided=bool(outcome.decided),
        thinker=type(active_thinker).__name__,
        think_prompt_sha256=prompt_sha256,
    )
    if not _append_jsonl(working_path, row):
        return None

    return ClosureTrace(
        agent_id=agent_id,
        drive_code=top.drive_code,
        tension_id=tension_id,
        resolved_at=resolved_at,
        think_request=think,
        tension_store_path=str(store_path),
        working_state_path=str(working_path),
        drives_touched=tuple(drives_touched),
        open_gap_count_after=gap_after,
        open_contradiction_count_after=contradiction_after,
        partial_resolution=float(outcome.partial_resolution),
        remaining_gap_count=int(outcome.remaining_gap_count),
        decided=bool(outcome.decided),
        think_prompt_sha256=prompt_sha256,
    )


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
    "feature_enabled",
]
