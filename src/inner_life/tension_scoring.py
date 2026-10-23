"""src/inner_life/tension_scoring.py — 含時驅動評分（資訊缺口 ＋ 未決矛盾 ＋ 時間衰減窗口）。

CAMPAIGN-B-P1 §2.2（工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md）

為什麼需要這個模組
------------------
既有張力機制（``src/inner_life/epistemic_tension_scheduler.py`` ＋
``src/soul/temporal_phenomenology.py``）是**純時間驅動、角色零差異**：46 隻角色的
張力窗長得一模一樣，與「這個角色缺什麼、卡在哪」無關。本模組把張力第一次
**因角色而異**，且三個因子**全部鎖定到既有資料面**（**不新造語意模型**）。

三因子（全部唯讀）
------------------
| 因子 | 來源（唯讀） | 定義 |
|---|---|---|
| ① 資訊缺口 | ``<data_root>/soul/<agent_id>/life_threads.jsonl`` | ``status == "active"`` 且 ``sage_fact_id`` 為空 且 ``updated_at`` 距 ``now`` ≥ 24h 的線頭筆數 |
| ② 未決矛盾 | 同上（線頭列的 ``contradiction`` 欄位） | 欄位存在且為真值 → 計數；**資料面無此欄位 ⇒ 該因子恆 0** |
| ③ 時間衰減窗口 | ``src.soul.temporal_phenomenology.classify_temporal_state`` | ``temporal_state`` 直接取其三態回傳值；``decay_bucket`` 由**同一個** elapsed 值導出 |

排序規則（決定性，**不用權重**）
------------------------------
字典序：``temporal_state``（牵挂 > 释然 > 无感）→ ``unresolved_count``（大者前）
→ ``info_gap_count``（大者前）→ ``drive_code``（D1<D2<D3）。
**禁止**任何加權總分、**禁止**任何權重常數、**禁止**任何門檻常數。

驅動對應（Phase 1）
------------------
- ``D1``（理解驅動）⇐ ``info_gap_count > 0`` ⇒ **Phase 1 唯一具備真實資料面的驅動目標**。
- ``D3``（邊界修復驅動）**已自評分目標移除**（Owner 裁定 A，2026-10-22 23:1x）。實測四條
  「什麼算衝突」判準路徑：兩條恆 0；唯一非 0 的一條 48/48 來自 ``support_propagation`` 邊
  且內容是**跨日生活切片而非邏輯衝突** ⇒ 放寬判準會用**假矛盾餵飽引擎 supersede 門檻**
  ⇒ **誤殺活著的命題** ⇒ **寧可誠實移除，不做假矛盾**。
- ⇒ **張力驅動目標集合恆為 ``("D1",)``**（見 ``ACTIVE_DRIVE_TARGETS``），
  並由守衛 **T11** 釘死「D3 不得回歸」。
- ``unresolved_count`` 仍作為**觀測因子**保留於快照與稽核軌跡（誠實回報其值，含
  「來源不存在 ⇒ 恆 0」的實情），但**不再**驅動任何張力。
- ``D2``（被需要驅動）**不參與評分**：D2 的觸發語意「被忽略」需要「外部互動缺席」
  的判斷，而 Phase 1 的資料面只有 agent 自身視角 ⇒ **判準不足 ⇒ 不做**
  （工單 §2.2 明文；**不得假裝三條驅動都參與**）。

紀律
----
- **純函式、確定性、零 LLM 呼叫、零自造權重**。
- **三態窗常數一律取自** ``src.soul.temporal_phenomenology``（本模組**不另立窗常數**；
  ``decay_bucket`` 以「把 elapsed 餵回上游判定」的方式借用上游的窗）。
- **只讀**：本模組**不寫**任何檔案。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.soul.temporal_phenomenology import (
    STATE_CALM,
    STATE_RESOLVED,
    STATE_TENSION,
    classify_temporal_state,
)

logger = logging.getLogger("soul_os.inner_life.tension_scoring")

# ── 驅動代碼 ────────────────────────────────────────────────────────────
DRIVE_INFO_GAP = "D1"
#: D3（邊界修復驅動）**保留為參考常數，但已自張力驅動目標移除**
#: （Owner 裁定 A 收斂，2026-10-22）。**不得**再拿它來驅動張力；守衛 T11 會在
#: D3 回歸時轉紅。
DRIVE_BOUNDARY_REPAIR = "D3"

#: **張力驅動目標集合**（Phase 1 唯一具備真實資料面的驅動）。
#: 守衛 T11 釘死此集合 == ("D1",)。
ACTIVE_DRIVE_TARGETS: Tuple[str, ...] = (DRIVE_INFO_GAP,)

# ── 時間衰減窗口的三個標籤（值由上游三態映射而來）─────────────────────────
DECAY_BUCKET_CALM = "<24h"
DECAY_BUCKET_TENSION = "24h-7d"
DECAY_BUCKET_RESOLVED = ">=7d"

#: 三態 → 衰減窗口標籤（**標籤映射**，不是窗；窗由上游判定決定）。
_BUCKET_BY_STATE: Dict[str, str] = {
    STATE_CALM: DECAY_BUCKET_CALM,
    STATE_TENSION: DECAY_BUCKET_TENSION,
    STATE_RESOLVED: DECAY_BUCKET_RESOLVED,
}

#: 三態的**排序位階**（牵挂 > 释然 > 无感 ⇒ 張力窗內優先）。
_STATE_RANK: Dict[str, int] = {
    STATE_TENSION: 0,
    STATE_RESOLVED: 1,
    STATE_CALM: 2,
}

#: 資訊缺口的「陳舊」門檻（秒）＝ 24h（工單 §2.2 因子① 的定義值）。
#:
#: ⚠️ 這**不是**三態張力窗的下界 —— 三態窗一律取自
#: ``src.soul.temporal_phenomenology``（本模組**不另立窗常數**）。
INFO_GAP_STALE_SEC = 86_400

#: 線頭檔名（資料面唯讀入口）。
LIFE_THREADS_FILENAME = "life_threads.jsonl"


# ── 資料模型 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TensionScore:
    """一條驅動張力的三因子快照（可重算、可稽核）。"""

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


# ── 時間衰減窗口（借用上游三態判定）──────────────────────────────────────
def _elapsed_state(elapsed_sec: int) -> str:
    """elapsed 秒 → 三態（**借用上游判定**；本模組 0 窗常數）。

    以 ``last_interaction_ts = 1``／``now = elapsed + 1`` 餵進上游
    ``classify_temporal_state``（帶合格羈絆證據 ⇒ 純看時間窗）⇒ 窗常數由上游
    決定，本模組**不另立一套**。
    """
    elapsed = max(0, int(elapsed_sec))
    return str(classify_temporal_state(1, elapsed + 1, _eligible_bond()))


def _eligible_bond() -> Any:
    """合格羈絆證據（上游型別）；只用於「純時間窗」判定。"""
    from src.soul.temporal_phenomenology import BondEvidence

    return BondEvidence(True)


def decay_bucket(elapsed_sec: int) -> str:
    """elapsed 秒 → 衰減窗口標籤（``<24h`` / ``24h-7d`` / ``>=7d``）。"""
    return _BUCKET_BY_STATE.get(_elapsed_state(elapsed_sec), DECAY_BUCKET_CALM)


# ── 資料面讀取（唯讀）────────────────────────────────────────────────────
def _soul_dir(soul_dir: Optional[Any] = None) -> Path:
    if soul_dir is None:
        from src.paths import data_root

        return Path(data_root()) / "soul"
    return Path(soul_dir)


def life_threads_path(agent_id: str, soul_dir: Optional[Any] = None) -> Path:
    """線頭檔路徑（唯讀）。"""
    return _soul_dir(soul_dir) / agent_id / LIFE_THREADS_FILENAME


def _read_life_thread_rows(agent_id: str, soul_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    """讀線頭列（唯讀；壞行略過；檔案不存在 ⇒ ``[]``）。"""
    path = life_threads_path(agent_id, soul_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(
            "[tension-scoring] 線頭檔讀取失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return []
    rows: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _parse_iso_ts(value: Any) -> Optional[int]:
    """寬容解析 ISO 時間戳 → epoch 秒（缺失／壞值 → ``None``）。"""
    if not value or not isinstance(value, str):
        return None
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _is_empty(value: Any) -> bool:
    """空值判定（``None``／空字串／純空白 ⇒ 空）。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


# ── 因子判定 ─────────────────────────────────────────────────────────────
def is_info_gap(row: Dict[str, Any], now: int) -> bool:
    """因子①：該線頭列是否算「資訊缺口」。

    ``status == "active"`` 且 ``sage_fact_id`` 為空 且 ``updated_at`` 距 ``now``
    ≥ 24h（工單 §2.2 因子①）。
    """
    if str(row.get("status") or "") != "active":
        return False
    if not _is_empty(row.get("sage_fact_id")):
        return False
    updated = _parse_iso_ts(row.get("updated_at"))
    if updated is None:
        return False
    return (int(now) - updated) >= INFO_GAP_STALE_SEC


def is_unresolved_contradiction(row: Dict[str, Any]) -> bool:
    """因子②：該線頭列是否算「未決矛盾」。

    線頭列**有** ``contradiction`` 欄位且為真值 → 算一筆。
    ⚠️ 實測（2026-10-22）：``data/soul/*/life_threads.jsonl`` 全 944 個 JSONL 檔
    **完全沒有** ``contradiction`` 欄位 ⇒ 該因子在現行資料面**恆 0**
    （工單 §2.2：此時**不得**改用別的來源頂替）。
    """
    return bool(row.get("contradiction"))


def _source_row(row: Dict[str, Any], factor: str) -> Dict[str, Any]:
    """資料面列的可稽核快照（指回該計數）。"""
    return {
        "factor": factor,
        "thread_id": str(row.get("thread_id") or ""),
        "status": str(row.get("status") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "title": str(row.get("title") or ""),
        "sage_fact_id": row.get("sage_fact_id"),
        "contradiction": row.get("contradiction"),
    }


# ── 排序 ─────────────────────────────────────────────────────────────────
def _sort_key(score: TensionScore) -> Tuple[int, int, int, str]:
    """字典序鍵：temporal_state → unresolved（大者前）→ info_gap（大者前）→ drive_code。"""
    return (
        _STATE_RANK.get(str(score.temporal_state), len(_STATE_RANK)),
        -int(score.unresolved_count),
        -int(score.info_gap_count),
        str(score.drive_code),
    )


def sort_scores(scores: Sequence[TensionScore]) -> List[TensionScore]:
    """依字典序排序（**決定性**；無權重、無門檻）。"""
    return sorted(list(scores), key=_sort_key)


# ── 主入口 ───────────────────────────────────────────────────────────────
def _default_bond_evidence(agent_id: str) -> Optional[Any]:
    """從關係資料（唯讀）取羈絆證據；取不到 ⇒ ``None``（⇒ 上游判「无感」）。"""
    try:
        from src.inner_life.epistemic_action import read_bond_snapshot

        snapshot = read_bond_snapshot(agent_id)
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-scoring] 羈絆證據讀取失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return None
    if snapshot is None:
        return None
    try:
        return snapshot.to_bond_evidence()
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-scoring] 羈絆證據轉換失敗（%s）：%s: %s",
            agent_id,
            type(exc).__name__,
            exc,
        )
        return None


def score_tensions(
    agent_id: str,
    now: int,
    *,
    soul_dir: Optional[Any] = None,
    last_interaction_ts: Optional[int] = None,
    bond_evidence: Optional[Any] = None,
) -> List[TensionScore]:
    """評分某 agent 的驅動張力（**純函式、唯讀、零 LLM**）。

    回傳 **0～1 條：僅 ``D1``**（有資訊缺口時）。**Owner 裁定 A（2026-10-22）後 D3 已自
    評分目標移除** ⇒ **不會**再產出 D3 張力（守衛 T11 釘死）。無缺口 ⇒ 回傳 ``[]``（無張力）。

    ``unresolved_count`` 仍作為**觀測因子**被記錄於快照與稽核軌跡（誠實回報其值，含
    「來源不存在 ⇒ 恆 0」的實情），但**不驅動**任何張力。
    """
    now_ts = int(now)
    rows = _read_life_thread_rows(agent_id, soul_dir)
    gap_rows = [row for row in rows if is_info_gap(row, now_ts)]
    unresolved_rows = [row for row in rows if is_unresolved_contradiction(row)]

    if last_interaction_ts is None:
        try:
            from src.inner_life.epistemic_action import read_last_interaction_ts

            last_interaction_ts = read_last_interaction_ts(agent_id)
        except Exception as exc:  # noqa: BLE001 — fail-silent
            logger.debug(
                "[tension-scoring] 最後互動時間讀取失敗（%s）：%s: %s",
                agent_id,
                type(exc).__name__,
                exc,
            )
            last_interaction_ts = 0
    last_ts = int(last_interaction_ts or 0)
    if bond_evidence is None:
        bond_evidence = _default_bond_evidence(agent_id)

    elapsed = (now_ts - last_ts) if last_ts > 0 else 0
    if elapsed < 0:
        elapsed = 0
    temporal_state = str(classify_temporal_state(last_ts, now_ts, bond_evidence))
    bucket = decay_bucket(elapsed)

    source_rows: List[Dict[str, Any]] = [_source_row(row, "info_gap") for row in gap_rows]
    source_rows.extend(_source_row(row, "unresolved") for row in unresolved_rows)

    # 🔴 只有 D1 會成為張力驅動目標。
    #    D3（邊界修復驅動）**已自評分目標移除**（Owner 裁定 A，2026-10-22 23:1x）：
    #    實測四條「什麼算衝突」判準路徑，兩條恆 0、唯一非 0 的一條 48/48 來自
    #    `support_propagation` 邊且內容是跨日生活切片而非邏輯衝突 ⇒ 放寬判準會用
    #    **假矛盾餵飽引擎 supersede 門檻** ⇒ 誤殺活著的命題 ⇒ 寧可誠實移除。
    #    ⇒ 這裡**不得**再出現 `drive_code=DRIVE_BOUNDARY_REPAIR`（守衛 T11 會轉紅）。
    scores: List[TensionScore] = []
    if gap_rows:
        scores.append(
            TensionScore(
                agent_id=agent_id,
                drive_code=DRIVE_INFO_GAP,
                temporal_state=temporal_state,
                decay_bucket=bucket,
                info_gap_count=len(gap_rows),
                unresolved_count=len(unresolved_rows),
                source_rows=list(source_rows),
            )
        )
    return sort_scores(scores)


__all__ = [
    "ACTIVE_DRIVE_TARGETS",
    "DECAY_BUCKET_CALM",
    "DECAY_BUCKET_RESOLVED",
    "DECAY_BUCKET_TENSION",
    "DRIVE_BOUNDARY_REPAIR",
    "DRIVE_INFO_GAP",
    "INFO_GAP_STALE_SEC",
    "LIFE_THREADS_FILENAME",
    "TensionScore",
    "decay_bucket",
    "is_info_gap",
    "is_unresolved_contradiction",
    "life_threads_path",
    "score_tensions",
    "sort_scores",
]
