"""
src/inner_life/epistemic_action.py — 自主求知行動（Internal Epistemic Action）

**SIM-EPIS-PIGGYBACK-1 ①**（工單 §3.1）。

結構核心：**Branching（set-valued transition）**
------------------------------------------------
依 ``docs/NORTH-STAR-AGENCY-ACTION-BOUNDARY-DCG-CONVERGENCE.md``（DCG 收斂 Round 5/5）
的裁決：**「Agency → NOT A CONSTRUCT」**——不存在已建立的獨立 Agency structural
dimension；唯一通過 ISD 的真新 structural dimension ＝ **Branching**。

⇒ 本模組的一切命名／說明都是「**張力分支上的多路徑展開**（set-valued branching）」：
**一條張力 ⇒ 多條候選路徑 ⇒ 其中一條實際命中證據 ⇒ 閉合**。
（**不是**「主動性」，**不是**「自主性」。）

觸發原料：**缺口／張力狀態空間**，**不是**「想念」
------------------------------------------------
實測（2026-10-21）M7-longing 的「想念」全日落在 0.05~0.06，門檻 0.3 ⇒ 若原料走
「想念」通道，這個機制會被同一道門檻擋住，做了也是空轉。

⇒ 本模組的張力來源**只有兩個既有通道**（**只讀引用，不改**）：
  1. ``src.inner_life.epistemic_appraisal`` 判定的**未閉合資訊缺口**；
  2. ``src.soul.temporal_phenomenology`` 的**張力／牵挂**狀態（``STATE_TENSION``）。

🔴 本模組**不讀**「想念值」、**不碰** ``LONGING_THRESHOLD``、**不碰** G4 靜音時段。

紀律
----
- **0 LLM**：路徑收束由**既有記憶／世界資料的實際命中**決定；查不到 ⇒ 張力維持，
  **不硬編故事**。
- **零打擾**：本模組**不對用戶發任何訊息**——不呼叫 ``_publish_agency_trigger``、
  不新增任何 ``proactive_dm`` 分支、不降低任何門檻。閉合事件走既有
  ``InnerLifeWriter`` ＋ ``SubmissionGate`` 通道 ⇒ 進 shadow log／SAGE。
- **fail-silent**：任何例外 → 空結果（不可讓 prompt 組裝失敗）。
- **deterministic**：同一份資料 ＋ 同一組參數 ⇒ 同一個結果。
- **只寫自家承載**：張力狀態承載（``epistemic_tensions.jsonl``，**append-only**）
  與話頭佇列，皆寫在呼叫端指定的 ``store_dir``（生產接線**預設關閉** ⇒ 生產零寫入）。

SIM-EPIS-ACTIVATE-READY（張力承載 append-only）
------------------------------------------------
張力狀態承載由「單一 JSON 覆寫」改為 **append-only JSONL**（每行一筆事件）：
多行程併寫時各自寫到檔尾（``O_APPEND`` 語意）⇒ **不再互相覆蓋**。
讀取語意＝**fold 全部事件、每個 ``tension_id`` 取最後一筆** ⇒ 等價於舊版的
dict 覆蓋，但寫入面已無覆蓋行為。舊版 ``epistemic_tensions.json`` 仍可讀：
首次讀取時**一次性**折算成事件寫入 JSONL，舊檔**改名**為 ``.migrated``
（**不得刪除**）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path as _FsPath
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.fsio.atomic_append import append_bytes

logger = logging.getLogger("soul_os.inner_life.epistemic_action")

# ── 路徑種類（Branching 的分支）────────────────────────────────────────────
PATH_MEMORY_RECALL = "memory_recall"
PATH_WORLD_VERIFY = "world_verify"
PATH_SELF_REASON = "self_reason"

#: 分支種類的固定順序（展開順序 ＝ 閉合嘗試順序；deterministic）。
PATH_KINDS: Tuple[str, ...] = (PATH_MEMORY_RECALL, PATH_WORLD_VERIFY, PATH_SELF_REASON)

# ── 張力種類與來源 ────────────────────────────────────────────────────────
TENSION_KIND_EPISTEMIC_GAP = "epistemic_gap"
TENSION_KIND_RELATIONAL = "relational_tension"

SOURCE_EPISTEMIC_APPRAISAL = "epistemic_appraisal"
SOURCE_TEMPORAL_PHENOMENOLOGY = "temporal_phenomenology"

#: 閉合事件在 ``Provenance.trace_ref`` 的前綴。
CLOSURE_TRACE_PREFIX = "epistemic_action:"

#: 舊版張力狀態承載檔名（單一 JSON；**唯讀相容**）。
#:
#: 🔴 本檔名**不再**作為寫入目標——新承載是 :data:`TENSION_STORE_EVENTS_FILENAME`
#: 的 append-only 事件檔。舊檔僅供一次性遷移（讀取後改名為 ``.migrated``）。
TENSION_STORE_FILENAME = "epistemic_tensions.json"

#: 張力狀態承載檔名（**append-only** JSONL；每行一筆事件；寫在 ``store_dir`` 底下）。
TENSION_STORE_EVENTS_FILENAME = "epistemic_tensions.jsonl"

#: 舊檔遷移後的改名後綴（舊檔**保留**，只改名；**不得刪除**）。
TENSION_STORE_MIGRATED_SUFFIX = ".migrated"

#: 張力事件的型別（**封閉集合**：不得自行擴充）。
TENSION_EVENT_OPENED = "tension_opened"
TENSION_EVENT_RESOLVED = "tension_resolved"
TENSION_EVENT_THOUGHT_ENQUEUED = "tension_thought_enqueued"

#: 事件型別的封閉集合（讀取時遇到集合外的型別 ⇒ 忽略該筆）。
TENSION_EVENT_KINDS: Tuple[str, ...] = (
    TENSION_EVENT_OPENED,
    TENSION_EVENT_RESOLVED,
    TENSION_EVENT_THOUGHT_ENQUEUED,
)

#: 張力狀態（**封閉集合**：讀取時遇到集合外的值 ⇒ 不靜默視為已消解，保留原值並告警）。
TENSION_STATUS_OPEN = "open"
TENSION_STATUS_RESOLVED = "resolved"
TENSION_STATUS_PARTIALLY_RESOLVED = "partially_resolved"
TENSION_STATUSES: Tuple[str, ...] = (
    TENSION_STATUS_OPEN,
    TENSION_STATUS_RESOLVED,
    TENSION_STATUS_PARTIALLY_RESOLVED,
)

#: 事件欄位的封閉形狀（``ts``／``agent_id``／``tension_id``／``kind``／
#: ``subject``／``status``／``payload``）。
TENSION_EVENT_FIELDS: Tuple[str, ...] = (
    "ts",
    "agent_id",
    "tension_id",
    "kind",
    "subject",
    "status",
    "payload",
)

#: Phase 2 思考結果的 4 個欄位（**保留 payload 鍵**；同時由記錄的**一級欄位**暴露）。
#:
#: - ``partial_resolution`` ＝ 本回合消解的比例（float）
#: - ``remaining_gap_count`` ＝ 思考後仍開著的資訊缺口數（int）
#: - ``decided`` ＝ 是否**完全**消解（bool）
#: - ``reason`` ＝ 判定理由（str）
TENSION_RESOLUTION_EXTRA_KEYS: Tuple[str, ...] = (
    "partial_resolution",
    "remaining_gap_count",
    "decided",
    "reason",
)

#: ``payload`` 內**不是**張力本體的保留欄位（取出張力本體時剔除）。
#:
#: 🔴 2026-10-23（BUG-EPISTEMIC-PARTIALLY-RESOLVED 追加）：Phase 2 的思考結果
#: （4 欄）與驅動快照（``drive_snapshot``）一併登錄為保留鍵 ⇒ **張力本體恆為
#: ``Tension`` 的 13 個正規欄位**（既有契約），extras 不得污染本體；它們改由
#: 記錄的一級欄位暴露（見 :func:`load_tension_records`）。
#: ⚠️ 既有 4 個保留鍵（``resolved_at``／``thought_payload``／``thought_tags``／
#: ``migration_digest``）**不得移除或改名**。
TENSION_PAYLOAD_EXTRA_KEYS: Tuple[str, ...] = (
    "resolved_at",
    "thought_payload",
    "thought_tags",
    "migration_digest",
) + TENSION_RESOLUTION_EXTRA_KEYS + (
    "drive_snapshot",
)

#: 話頭掛載槽的來源標籤（與 ``piggyback_queue.PIGGYBACK_SOURCE_CLOSURE`` 同值）。
PIGGYBACK_SOURCE_CLOSURE = "epistemic_closure"

#: SG-3 §6.3 P2：非 stranger 的關係 band（**鏡像** ``temporal_phenomenology`` 的
#: ``_NON_STRANGER_BANDS``；那個是私有常數，本模組不得引用它 ⇒ 自帶一份並以
#: 漂移守衛測試釘死兩者一致）。
_NON_STRANGER_BANDS: frozenset = frozenset({"known", "familiar", "close"})


# ── 資料模型 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BondSnapshot:
    """SG-3 §6.3 羈絆證據的**本模組視圖**（讀 relationships.json 而來）。

    ⚠️ 這是 ``src.soul.temporal_phenomenology._read_bond_evidence`` 的**鏡像讀取**
    （該函式是私有；本票不得改動該模組，也不得引用它的私有常數）。
    判定規則逐條對齊 SG-3 §6.3（P1 → P2 → P3 → P4），並由
    ``tests/inner_life/test_epistemic_action.py`` 的漂移守衛釘死與上游一致。
    """

    eligible: bool
    basis: str = "P4_none"
    impression_tags: Tuple[str, ...] = ()

    def to_bond_evidence(self) -> Any:
        """轉成上游的 ``BondEvidence``（交給公開的 ``classify_temporal_state``）。"""
        from src.soul.temporal_phenomenology import BondEvidence

        return BondEvidence(eligible=self.eligible, basis=self.basis)


@dataclass(frozen=True)
class Tension:
    """一條**未閉合**的驅動張力（原料）。

    欄位刻意攜帶「可重算」的原料（utterance / session_context / last_interaction_ts），
    因為 SELF_REASON 路徑要在**之後**重新判定同一條張力是否已自行消退。
    """

    tension_id: str
    kind: str
    agent_id: str
    subject: str
    summary: str
    tags: Tuple[str, ...]
    world_keys: Tuple[str, ...]
    source: str
    created_at: float
    utterance: str = ""
    session_context: str = ""
    last_interaction_ts: int = 0
    bond: BondSnapshot = field(default_factory=lambda: BondSnapshot(eligible=False))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tension_id": self.tension_id,
            "kind": self.kind,
            "agent_id": self.agent_id,
            "subject": self.subject,
            "summary": self.summary,
            "tags": list(self.tags),
            "world_keys": list(self.world_keys),
            "source": self.source,
            "created_at": self.created_at,
            "utterance": self.utterance,
            "session_context": self.session_context,
            "last_interaction_ts": self.last_interaction_ts,
            "bond": {
                "eligible": self.bond.eligible,
                "basis": self.bond.basis,
                "impression_tags": list(self.bond.impression_tags),
            },
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["Tension"]:
        """從持久化 dict 還原；壞資料 → ``None``（fail-silent）。"""
        if not isinstance(raw, dict):
            return None
        tension_id = raw.get("tension_id")
        kind = raw.get("kind")
        agent_id = raw.get("agent_id")
        if not isinstance(tension_id, str) or not tension_id:
            return None
        if not isinstance(kind, str) or not kind:
            return None
        if not isinstance(agent_id, str) or not agent_id:
            return None
        tags = raw.get("tags") or ()
        world_keys = raw.get("world_keys") or ()
        bond_raw = raw.get("bond") or {}
        bond = BondSnapshot(
            eligible=bool(bond_raw.get("eligible")),
            basis=str(bond_raw.get("basis") or "P4_none"),
            impression_tags=tuple(str(t) for t in (bond_raw.get("impression_tags") or ())),
        )
        return cls(
            tension_id=tension_id,
            kind=kind,
            agent_id=agent_id,
            subject=str(raw.get("subject") or ""),
            summary=str(raw.get("summary") or ""),
            tags=tuple(str(t) for t in tags),
            world_keys=tuple(str(t) for t in world_keys),
            source=str(raw.get("source") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            utterance=str(raw.get("utterance") or ""),
            session_context=str(raw.get("session_context") or ""),
            last_interaction_ts=int(raw.get("last_interaction_ts") or 0),
            bond=bond,
        )


@dataclass(frozen=True)
class Path:
    """一條候選路徑（Branching 的分支本體）。"""

    kind: str
    label: str
    available: bool
    reason: str


@dataclass(frozen=True)
class PathEvidence:
    """某一條路徑的探測結果（**實際命中**才算證據）。"""

    path_kind: str
    hit: bool
    detail: str
    refs: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_kind": self.path_kind,
            "hit": self.hit,
            "detail": self.detail,
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class Closure:
    """一次閉合的結果（三件產出缺一不可：事件 / resolved 狀態 / 可掛載小念頭）。"""

    closed: bool
    tension_id: str
    event_id: Optional[str] = None
    evidence: Optional[PathEvidence] = None
    thought_payload: Optional[str] = None
    thought_tags: Tuple[str, ...] = ()
    enqueued: bool = False
    resolved_marked: bool = False
    submitted: bool = False
    reason: str = ""


# ── 觸發判定 ──────────────────────────────────────────────────────────────
def should_act(tensions: Optional[Sequence[Tension]]) -> bool:
    """是否存在**未閉合**的驅動張力 ⇒ True。

    🔴 這是本機制**唯一**的觸發判定。它**不看**「想念值」、不看門檻。
    """
    if not tensions:
        return False
    for tension in tensions:
        if isinstance(tension, Tension):
            return True
    return False


# ── 多路徑展開（Branching, set-valued transition）─────────────────────────
def expand_paths(tension: Tension) -> List[Path]:
    """同一張力展開成多條候選路徑（**set-valued transition**）。

    至少兩條，且**不得**是同一路徑的變體：
      - ``MEMORY_RECALL``：翻記憶找線索（v1 記憶庫，tag 重疊命中）；
      - ``WORLD_VERIFY`` ：查世界事件佐證（world log shard，摘要命中）；
      - ``SELF_REASON``  ：自我提問（推演式收束；判據是**張力自帶的狀態向量**）。

    「選取依既有模組的可用輸入決定」：某一條分支**可不可查**取決於這條張力手上有
    沒有對應的查詢輸入（記憶詞元 / 世界詞元）。**分支數 ≥ 2 的結構不變式恆成立**
    （三條分支恆在，`available` 標記可查與否）——這樣「同一張力展開成多條」是
    **結構事實**，不是「碰巧查得到兩條」。

    🔴 **不得**引入任何新的 LLM 呼叫（沙盒零成本、避免語意被 LLM 漂移）。
    """
    tags = tuple(tension.tags or ())
    world_keys = tuple(tension.world_keys or ())
    return [
        Path(
            kind=PATH_MEMORY_RECALL,
            label="翻記憶找線索",
            available=bool(tags),
            reason=(
                f"記憶詞元可用（{len(tags)} 個）"
                if tags
                else "無記憶詞元可查（分支保留，實際不可查）"
            ),
        ),
        Path(
            kind=PATH_WORLD_VERIFY,
            label="查世界事件佐證",
            available=bool(world_keys),
            reason=(
                f"世界查詢詞元可用（{len(world_keys)} 個）"
                if world_keys
                else "無世界查詢詞元（分支保留，實際不可查）"
            ),
        ),
        Path(
            kind=PATH_SELF_REASON,
            label="自我提問（推演式收束）",
            available=True,
            reason="張力自帶狀態向量可判定（不需外部輸入）",
        ),
    ]


# ── 探測（實際命中才算證據）──────────────────────────────────────────────
def _normalize_words(words: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    for word in words or ():
        if not isinstance(word, str):
            continue
        text = word.strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def memory_probe(
    tension: Tension,
    *,
    memory_dir: Optional[Any] = None,
) -> PathEvidence:
    """MEMORY_RECALL 探測：翻既有 v1 記憶庫找線索（唯讀、tag 重疊、0 LLM）。

    命中＝**實際有記憶命中**。查不到 ⇒ 不閉合（張力維持，**不硬編故事**）。
    """
    tags = _normalize_words(tension.tags)
    if not tags:
        return PathEvidence(PATH_MEMORY_RECALL, False, "無記憶詞元，未查")
    try:
        if memory_dir is None:
            from src.paths import data_root

            memory_dir = _FsPath(data_root()) / "soul"
        memory_dir = _FsPath(memory_dir)
        store_file = memory_dir / tension.agent_id / "memories.jsonl"
        if not store_file.is_file():
            return PathEvidence(PATH_MEMORY_RECALL, False, "記憶庫不存在，無命中")
        from src.memory.v1.retrieval import retrieve
        from src.memory.v1.store import V1Store

        store = V1Store(memory_dir, tension.agent_id)
        log = retrieve(store, tags)
        hits = list(getattr(log, "retrieved_ids", []) or [])
        total = len(hits) + len(list(getattr(log, "rejected_ids", []) or []))
        if not hits:
            return PathEvidence(
                PATH_MEMORY_RECALL,
                False,
                f"翻記憶 {total} 筆，tag 重疊命中 0 筆（張力維持）",
            )
        return PathEvidence(
            PATH_MEMORY_RECALL,
            True,
            f"翻記憶 {total} 筆，tag 重疊命中 {len(hits)} 筆",
            refs=tuple(str(h) for h in hits[:8]),
        )
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 記憶探測失敗：%s: %s", type(exc).__name__, exc)
        return PathEvidence(PATH_MEMORY_RECALL, False, f"記憶探測失敗：{type(exc).__name__}")


def world_probe(
    tension: Tension,
    *,
    world_dir: Optional[Any] = None,
) -> PathEvidence:
    """WORLD_VERIFY 探測：查既有世界事件日誌佐證（唯讀、摘要命中、0 LLM）。"""
    keys = _normalize_words(tension.world_keys)
    if not keys:
        return PathEvidence(PATH_WORLD_VERIFY, False, "無世界查詢詞元，未查")
    try:
        if world_dir is None:
            from src.paths import data_root

            world_dir = _FsPath(data_root()) / "world" / "world_log"
        world_dir = _FsPath(world_dir)
        if not world_dir.is_dir():
            return PathEvidence(PATH_WORLD_VERIFY, False, "世界日誌目錄不存在，無命中")
        hits: List[str] = []
        scanned = 0
        for shard in sorted(world_dir.glob("*.jsonl")):
            try:
                lines = shard.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                scanned += 1
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                haystack = " ".join(
                    str(record.get(field_name) or "")
                    for field_name in ("summary", "event_type", "source")
                ).lower()
                if any(key in haystack for key in keys):
                    hits.append(str(record.get("world_event_id") or ""))
        if not hits:
            return PathEvidence(
                PATH_WORLD_VERIFY,
                False,
                f"查世界事件 {scanned} 筆，命中 0 筆（張力維持）",
            )
        return PathEvidence(
            PATH_WORLD_VERIFY,
            True,
            f"查世界事件 {scanned} 筆，命中 {len(hits)} 筆",
            refs=tuple(h for h in hits[:8] if h),
        )
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 世界探測失敗：%s: %s", type(exc).__name__, exc)
        return PathEvidence(PATH_WORLD_VERIFY, False, f"世界探測失敗：{type(exc).__name__}")


def self_reason_probe(
    tension: Tension,
    *,
    now: Optional[int] = None,
) -> PathEvidence:
    """SELF_REASON 探測：用**張力自帶的狀態向量**推演收束（0 LLM、deterministic）。

    - 關係張力（``relational_tension``）：三態現象學的**當下狀態**若已從
      「牵挂」走到「释然」，⇒ 張力已自行消退（**這就是證據**）。
    - 認知缺口（``epistemic_gap``）：重新評估後**不再回報未閉合缺口**
      （已內化／已無特徵證據）⇒ 缺口已閉合（**這就是證據**）。
    """
    now_ts = int(now if now is not None else time.time())
    try:
        if tension.kind == TENSION_KIND_RELATIONAL:
            from src.soul.temporal_phenomenology import (
                STATE_RESOLVED,
                STATE_TENSION,
                classify_temporal_state,
            )

            state = classify_temporal_state(
                tension.last_interaction_ts, now_ts, tension.bond.to_bond_evidence()
            )
            if state == STATE_RESOLVED:
                return PathEvidence(
                    PATH_SELF_REASON,
                    True,
                    "張力隨時間自行消退（三態：释然）",
                )
            if state == STATE_TENSION:
                return PathEvidence(PATH_SELF_REASON, False, "張力仍在（牽挂），未消退")
            return PathEvidence(PATH_SELF_REASON, False, "無感，無張力可收束")

        if tension.kind == TENSION_KIND_EPISTEMIC_GAP:
            if not tension.utterance:
                return PathEvidence(PATH_SELF_REASON, False, "無原始話語，無法重估")
            from src.inner_life.epistemic_appraisal import AppraisalInput, appraise

            record = appraise(
                AppraisalInput(
                    agent_id=tension.agent_id,
                    utterance=tension.utterance,
                    session_context=tension.session_context,
                )
            )
            if record is None:
                return PathEvidence(
                    PATH_SELF_REASON,
                    True,
                    "認知評估已不再回報未閉合缺口（已內化／已無特徵證據）",
                )
            return PathEvidence(PATH_SELF_REASON, False, "缺口仍在，未收束")

        return PathEvidence(PATH_SELF_REASON, False, "未知張力種類，無判據")
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 自我推演失敗：%s: %s", type(exc).__name__, exc)
        return PathEvidence(PATH_SELF_REASON, False, f"自我推演失敗：{type(exc).__name__}")


def default_probes(now: Optional[int] = None) -> Dict[str, Callable[[Tension], PathEvidence]]:
    """預設探測組（三條分支各一個；皆為唯讀、0 LLM）。

    ``now`` 會綁進 SELF_REASON 探測——**假時鐘是本機制的一部分**：判定張力是否
    已自行消退，必須用「當下」而不是「機器現在」。
    """
    now_ts = int(now) if now is not None else None

    def _memory(tension: Tension) -> PathEvidence:
        return memory_probe(tension)

    def _world(tension: Tension) -> PathEvidence:
        return world_probe(tension)

    def _self(tension: Tension) -> PathEvidence:
        return self_reason_probe(tension, now=now_ts)

    return {
        PATH_MEMORY_RECALL: _memory,
        PATH_WORLD_VERIFY: _world,
        PATH_SELF_REASON: _self,
    }


# ── 張力狀態承載（append-only 事件檔；只寫自家檔；生產接線預設關閉）────────
def _resolve_store_dir(store_dir: Optional[Any]) -> _FsPath:
    """承載目錄（未指定 ⇒ 既有預設 ``<data_root>/inner_life``）。"""
    if store_dir is None:
        from src.paths import data_root

        return _FsPath(data_root()) / "inner_life"
    return _FsPath(store_dir)


def tension_store_path(store_dir: Optional[Any] = None) -> Path:
    """張力狀態承載檔路徑（**append-only** 事件檔 ``epistemic_tensions.jsonl``）。"""
    return _resolve_store_dir(store_dir) / TENSION_STORE_EVENTS_FILENAME


def legacy_tension_store_path(store_dir: Optional[Any] = None) -> Path:
    """舊版單一 JSON 承載檔路徑（**唯讀相容**；遷移後改名保留）。"""
    return _resolve_store_dir(store_dir) / TENSION_STORE_FILENAME


def _make_tension_event(
    kind: str,
    tension: Tension,
    status: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """組一筆張力事件（欄位形狀封閉：``TENSION_EVENT_FIELDS``）。"""
    ts = float(now if now is not None else time.time())
    payload: Dict[str, Any] = tension.to_dict()
    if extra:
        payload.update(dict(extra))
    return {
        "ts": ts,
        "agent_id": tension.agent_id,
        "tension_id": tension.tension_id,
        "kind": kind,
        "subject": tension.subject,
        "status": status,
        "payload": payload,
    }


def _append_tension_events(
    store_dir: Optional[Any], events: Sequence[Mapping[str, Any]]
) -> bool:
    """把事件**追加**進承載檔（append-only）。

    - 🔴 2026-10-22（INFRA-CONCURRENT-APPEND-MUTEX）：**舊版的 ``open(path,"ab")``
      在 Windows 上並非原子追加** —— 它走 MSVCRT 的 ``_O_APPEND``，CRT 會用「該 handle
      自己快取的檔案大小」seek 到 EOF；兩個先後開啟的 handle 各自快取到同一個（過期）
      大小 ⇒ 寫到同一偏移 ⇒ **後者整筆覆蓋前者**（可完整解析、零損壞行 ⇒ 看起來像
      「整筆無痕跡消失」）。實測：30 輪併寫中 10 輪遺失（33.3%）。
    - 現改走 ``src.fsio.atomic_append.append_bytes``：**行程內互斥鎖 ＋ 平台原生原子追加**
      （Windows 用 ``CreateFileW(access=FILE_APPEND_DATA)``，核心層保證落在真正的檔尾，
      跨行程也安全）；**不持有長命 handle**（無 handle 生命週期管理）。
    - **單次** ``write()`` 送出整批位元組 ⇒ 同一行不會被兩個寫入者交錯。
    - ``flush()`` ＋ ``os.fsync()`` ⇒ 掉電不遺失（末筆可見）。
    """
    if not events:
        return True
    path = tension_store_path(store_dir)
    blob = b"".join(
        (json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for event in events
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        append_bytes(path, blob)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 張力承載寫入失敗：%s: %s", type(exc).__name__, exc)
        return False


def _read_tension_events(store_dir: Optional[Any]) -> List[Dict[str, Any]]:
    """讀出承載檔的**全部**事件（壞行略過；fail-silent）。"""
    path = tension_store_path(store_dir)
    try:
        if not path.exists():
            return []
        raw = path.read_bytes()
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 張力承載讀取失敗：%s: %s", type(exc).__name__, exc)
        return []
    events: List[Dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("kind") not in TENSION_EVENT_KINDS:
            continue
        events.append(event)
    return events


def _fold_tension_events(
    events: Sequence[Mapping[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """**fold** 事件序列 ⇒ 每個 ``tension_id`` 取**最後一筆**。

    與舊版「dict 覆蓋」等價（同 id 的最後一筆勝出），但**寫入面**已無覆蓋行為。
    鍵的順序＝該 ``tension_id`` **首次出現**的順序（與舊版 list 順序一致）。
    """
    state: Dict[str, Dict[str, Any]] = {}
    for event in events:
        tension_id = event.get("tension_id")
        if not isinstance(tension_id, str) or not tension_id:
            continue
        state[tension_id] = dict(event)
    return state


def _tension_body(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """從事件 payload 取出**張力本體**（剔除保留的額外欄位）。"""
    return {
        key: value
        for key, value in payload.items()
        if key not in TENSION_PAYLOAD_EXTRA_KEYS
    }


def _legacy_migrated_target(legacy_path: Path) -> Path:
    """舊檔改名後的目標路徑（**不覆寫**既有 ``.migrated``）。"""
    base = legacy_path.with_name(legacy_path.name + TENSION_STORE_MIGRATED_SUFFIX)
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = legacy_path.with_name(
            legacy_path.name + TENSION_STORE_MIGRATED_SUFFIX + f".{index}"
        )
        if not candidate.exists():
            return candidate
        index += 1


def _legacy_tension_events(raw_bytes: bytes, digest: str) -> List[Dict[str, Any]]:
    """把舊版單一 JSON 承載折算成事件（deterministic：agent 排序 ＋ 原 list 順序）。"""
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    agents = raw.get("agents")
    if not isinstance(agents, dict):
        return []
    events: List[Dict[str, Any]] = []
    for agent_id in sorted(agents):
        records = agents.get(agent_id)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            tension = Tension.from_dict(record.get("tension"))
            if tension is None:
                continue
            resolved = record.get("status") == "resolved"
            extra: Dict[str, Any] = {"migration_digest": digest}
            if resolved:
                extra["resolved_at"] = record.get("resolved_at")
            events.append(
                _make_tension_event(
                    TENSION_EVENT_RESOLVED if resolved else TENSION_EVENT_OPENED,
                    tension,
                    "resolved" if resolved else "open",
                    extra=extra,
                    now=tension.created_at or None,
                )
            )
    return events


def migrate_legacy_tension_store(store_dir: Optional[Any] = None) -> bool:
    """舊版單一 JSON 承載 → append-only 事件檔（**一次性**）。

    - 舊檔**改名**為 ``.migrated``（**不得刪除**）。
    - 以舊檔內容的 sha256 作為遷移標記 ⇒ 重複呼叫不會重複折算。
    """
    legacy_path = legacy_tension_store_path(store_dir)
    try:
        if not legacy_path.is_file():
            return False
        raw_bytes = legacy_path.read_bytes()
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 舊承載讀取失敗：%s: %s", type(exc).__name__, exc)
        return False

    digest = hashlib.sha256(raw_bytes).hexdigest()
    already = any(
        isinstance(event.get("payload"), dict)
        and event["payload"].get("migration_digest") == digest
        for event in _read_tension_events(store_dir)
    )
    if not already:
        events = _legacy_tension_events(raw_bytes, digest)
        if events and not _append_tension_events(store_dir, events):
            return False
    try:
        legacy_path.replace(_legacy_migrated_target(legacy_path))
        return True
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 舊承載改名失敗：%s: %s", type(exc).__name__, exc)
        return False


def load_tension_records(agent_id: str, store_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    """讀出某 agent 的張力紀錄（``{"tension": {...}, "status": "open"|"resolved"|
    "partially_resolved", "resolved_at": ..., <4 個思考結果欄位>}``）。

    讀取語意＝fold 全部事件 ⇒ 每個 ``tension_id`` 取最後一筆。

    🔴 狀態是**封閉集合**（``TENSION_STATUSES``）：讀到集合外的值 ⇒ **保留原值**
    ＋ **告警**（fail-closed）—— 不得靜默當成「已消解」，否則尚未消解的張力會被
    宣告消解而永久蒸發。

    🔴 Phase 2 的思考結果（``partial_resolution``／``remaining_gap_count``／
    ``decided``／``reason``）是**記錄的一級欄位**：它們已被剔除出張力本體
    （``Tension`` 只有 13 個正規欄位），所以**只能**從這裡讀。
    """
    migrate_legacy_tension_store(store_dir)
    records: List[Dict[str, Any]] = []
    for event in _fold_tension_events(_read_tension_events(store_dir)).values():
        if event.get("agent_id") != agent_id:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        status = event.get("status")
        if status not in TENSION_STATUSES:
            logger.warning(
                "[epistemic-action] 未知張力狀態 %r（tension_id=%s）⇒ 保留原值，**不視為已消解**",
                status,
                event.get("tension_id"),
            )
        records.append(
            {
                "tension": _tension_body(payload),
                "status": status,
                "resolved_at": payload.get("resolved_at"),
                # 🔴 Phase 2 的思考結果：**記錄一級欄位**暴露（extras 已被剔除出
                # 張力本體，消費者不必依賴「本體裡夾帶」）。
                # 缺值時為 ``None``（**不臆造** ``decided=False`` ——「沒有記錄」
                # 與「明確未決」是兩件事）；讀取端一律用 ``.get()`` ⇒ 不會 KeyError。
                "partial_resolution": payload.get("partial_resolution"),
                "remaining_gap_count": payload.get("remaining_gap_count"),
                "decided": payload.get("decided"),
                "reason": payload.get("reason"),
            }
        )
    return records


def save_tension_records(
    agent_id: str, records: Sequence[Mapping[str, Any]], store_dir: Optional[Any] = None
) -> bool:
    """寫回某 agent 的張力紀錄（**append-only 版本**：逐筆追加事件）。

    ⚠️ 語意差異（append-only 的必然結果）：舊版是「整份覆寫」，新版是「追加事件」
    ⇒ fold 之後的結果與舊版**等價**（同 id 的最後一筆勝出），但**既有事件不被刪除**。
    """
    events: List[Dict[str, Any]] = []
    for record in records or ():
        if not isinstance(record, dict):
            continue
        tension = Tension.from_dict(record.get("tension"))
        if tension is None:
            continue
        status = record.get("status")
        resolved = status == TENSION_STATUS_RESOLVED
        partially_resolved = status == TENSION_STATUS_PARTIALLY_RESOLVED
        extra: Dict[str, Any] = {}
        if resolved or partially_resolved:
            extra["resolved_at"] = record.get("resolved_at")
        # 🔴 Phase 2 的思考結果必須**往返存活**：``Tension.to_dict()`` 只輸出 13 個
        # 正規欄位（不含這 4 欄）⇒ 寫回時必須**從記錄把它們帶進 payload**，
        # 否則一次往返就把它們抹掉（部分消解的進度會消失）。
        # ⚠️ 用 ``is not None`` 判斷（不是 truthiness）⇒ ``decided=False`` 會被寫出。
        for _key in TENSION_RESOLUTION_EXTRA_KEYS:
            _value = record.get(_key)
            if _value is not None:
                extra[_key] = _value
        # 🔴 「部分消解」必須**原樣保留**：事件仍是既有的 ``tension_resolved``，
        # 但 status **不得**被降級成 ``open``（否則部分消解在往返中被抹平，
        # 未消解的缺口會被宣告成「還沒開始」）。
        if partially_resolved:
            event_kind = TENSION_EVENT_RESOLVED
            event_status = TENSION_STATUS_PARTIALLY_RESOLVED
        else:
            event_kind = TENSION_EVENT_RESOLVED if resolved else TENSION_EVENT_OPENED
            event_status = TENSION_STATUS_RESOLVED if resolved else TENSION_STATUS_OPEN
        events.append(
            _make_tension_event(
                event_kind,
                tension,
                event_status,
                extra=extra,
            )
        )
    return _append_tension_events(store_dir, events)


def load_open_tensions(
    agent_id: str, store_dir: Optional[Any] = None
) -> List[Tension]:
    """讀出某 agent 仍**未閉合**的張力（供下一輪重新推演）。

    🔴 「部分消解」（``partially_resolved``）**仍未閉合** ⇒ 必須留在佇列裡，
    否則它會從未閉合清單消失（未消解的張力永久蒸發）。
    """
    out: List[Tension] = []
    for record in load_tension_records(agent_id, store_dir):
        if record.get("status") not in (
            TENSION_STATUS_OPEN,
            TENSION_STATUS_PARTIALLY_RESOLVED,
        ):
            continue
        tension = Tension.from_dict(record.get("tension"))
        if tension is not None:
            out.append(tension)
    return out


def upsert_open_tension(tension: Tension, store_dir: Optional[Any] = None) -> bool:
    """把一條張力記成「未閉合」（**append-only**：追加一筆 opened 事件）。

    舊版是「同 id 覆寫」；append-only 下同 id 會有多筆事件，但 fold（取最後一筆）
    的結果**等價**，且**不再互相覆蓋別人的寫入**。
    """
    event = _make_tension_event(TENSION_EVENT_OPENED, tension, "open")
    return _append_tension_events(store_dir, [event])


def mark_tension_resolved(
    tension: Tension, store_dir: Optional[Any] = None, now: Optional[float] = None
) -> bool:
    """把一條張力標記 ``resolved``（**append-only**：追加一筆 resolved 事件）。"""
    resolved_at = float(now if now is not None else time.time())
    event = _make_tension_event(
        TENSION_EVENT_RESOLVED,
        tension,
        "resolved",
        extra={"resolved_at": resolved_at},
        now=resolved_at,
    )
    return _append_tension_events(store_dir, [event])


def record_thought_enqueued(
    tension: Tension,
    store_dir: Optional[Any] = None,
    *,
    thought_payload: str = "",
    thought_tags: Sequence[str] = (),
    now: Optional[float] = None,
) -> bool:
    """記錄「閉合後的小念頭已入隊」（**append-only**：追加一筆 thought 事件）。"""
    event = _make_tension_event(
        TENSION_EVENT_THOUGHT_ENQUEUED,
        tension,
        "resolved",
        extra={
            "thought_payload": thought_payload,
            "thought_tags": list(thought_tags),
        },
        now=now,
    )
    return _append_tension_events(store_dir, [event])



# ── 張力來源（只取兩個既有通道）──────────────────────────────────────────
def _parse_iso_ts(value: Any) -> Optional[int]:
    """寬容解析 ISO 時間戳 → epoch 秒（缺失／壞值 → ``None``，0 raise）。

    ⚠️ **鏡像** ``src.soul.temporal_phenomenology._parse_iso_ts``（私有函式）：
    P3 羈絆判準要求 ``last_interaction_at`` 是**合法 ISO**；本模組不得引用上游
    私有常數／私有函式 ⇒ 自帶一份並以漂移守衛測試釘死兩者一致。
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _read_bryan_relationship_entry(agent_id: str) -> Optional[Dict[str, Any]]:
    """讀 relationships.json 裡 Bryan 的 entry（唯讀；缺 entry → ``None``）。

    這是 :func:`read_bond_snapshot` 與 :func:`read_last_interaction_ts` 的**單一
    讀取面**（同一份檔案、同一個 entity key）⇒ 兩者不會讀到不同版本的資料。
    """
    try:
        from src.paths import data_root
        from src.soul.relationships import BRYAN_ENTITY_ID

        path = _FsPath(data_root()) / "soul" / agent_id / "relationships.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        others = data.get("others") if isinstance(data, dict) else None
        entry = others.get(BRYAN_ENTITY_ID) if isinstance(others, dict) else None
        return entry if isinstance(entry, dict) else None
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 關係資料讀取失敗：%s: %s", type(exc).__name__, exc)
        return None


def read_bond_snapshot(agent_id: str) -> Optional[BondSnapshot]:
    """讀 SG-3 §6.3 羈絆證據（relationships.json 唯讀；缺 entry → ``None``）。

    ⚠️ 鏡像 ``src.soul.temporal_phenomenology._read_bond_evidence``（私有函式）。
    判定規則逐條對齊；漂移由測試釘死。**只讀、不寫、不建立 entry**。
    """
    try:
        entry = _read_bryan_relationship_entry(agent_id)
        if entry is None:
            return None
        tags_raw = entry.get("impression_tags")
        impression_tags = (
            tuple(str(t) for t in tags_raw if str(t).strip())
            if isinstance(tags_raw, list)
            else ()
        )
        if impression_tags:
            return BondSnapshot(True, "P1_impression_tags", impression_tags)
        band = entry.get("relational_band")
        if isinstance(band, str) and band in _NON_STRANGER_BANDS:
            return BondSnapshot(True, "P2_relational_band", impression_tags)
        count = entry.get("interaction_count")
        last_interaction_at = entry.get("last_interaction_at")
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 10
            and _parse_iso_ts(last_interaction_at) is not None
        ):
            return BondSnapshot(True, "P3_interaction_count", impression_tags)
        return BondSnapshot(False, "P4_none", impression_tags)
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 羈絆證據讀取失敗：%s: %s", type(exc).__name__, exc)
        return None


def read_last_interaction_ts(agent_id: str) -> int:
    """讀某 agent 與 Bryan 的**最後互動**時間（epoch 秒；無 → ``0``）。

    資料源＝relationships.json 的 ``last_interaction_at``（**唯讀**）。

    為什麼不是 memory DB 的 messages 表：排程路徑必須在**沒有任何對話流量**
    （訊息數 = 0）時也能判定「是否落在張力窗內」⇒ 判斷必須只依賴**關係狀態**
    本身。關係狀態與羈絆證據同源（同一份 relationships.json）⇒ 兩者一致。
    """
    try:
        entry = _read_bryan_relationship_entry(agent_id)
        if entry is None:
            return 0
        parsed = _parse_iso_ts(entry.get("last_interaction_at"))
        return int(parsed) if parsed is not None else 0
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[epistemic-action] 最後互動時間讀取失敗：%s: %s", type(exc).__name__, exc
        )
        return 0



def _derive_tags(text: str) -> List[str]:
    """詞元化走 SSOT（``src.memory.v1.loader.derive_query_tags``）；失敗 → 空。"""
    try:
        from src.memory.v1.loader import derive_query_tags

        return [t for t in derive_query_tags(text or "") if isinstance(t, str) and t.strip()]
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 詞元化失敗：%s: %s", type(exc).__name__, exc)
        return []


def _tension_id(agent_id: str, kind: str, seed: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{agent_id}|{kind}|{seed}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:16]}"


def collect_tensions(
    agent_id: str,
    *,
    utterance: str = "",
    session_context: str = "",
    now: Optional[int] = None,
    last_interaction_ts: int = 0,
    bond: Optional[BondSnapshot] = None,
) -> List[Tension]:
    """從**兩個既有通道**收集未閉合張力（缺口 ＋ 張力／牵挂）。

    🔴 **不讀想念值、不看門檻**——原料是「缺口／張力」狀態空間。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return []
    now_ts = int(now if now is not None else time.time())
    out: List[Tension] = []

    # ── 通道 1：epistemic_appraisal 判定的未閉合資訊缺口 ──
    try:
        from src.inner_life.epistemic_appraisal import AppraisalInput, appraise

        record = appraise(
            AppraisalInput(
                agent_id=agent_id,
                utterance=utterance or "",
                session_context=session_context or "",
            )
        )
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 認知評估失敗：%s: %s", type(exc).__name__, exc)
        record = None

    if record is not None:
        entity_terms = tuple(str(t) for t in (getattr(record, "entity_terms", ()) or ()))
        tags = _derive_tags(utterance) + list(entity_terms)
        deduped: List[str] = []
        for tag in tags:
            text = tag.strip()
            if text and text not in deduped:
                deduped.append(text)
        if deduped:
            subject = entity_terms[0] if entity_terms else (deduped[0] if deduped else "")
            out.append(
                Tension(
                    tension_id=_tension_id(agent_id, TENSION_KIND_EPISTEMIC_GAP, subject),
                    kind=TENSION_KIND_EPISTEMIC_GAP,
                    agent_id=agent_id,
                    subject=subject,
                    summary=f"還不認得「{subject}」這件事",
                    tags=tuple(deduped),
                    world_keys=entity_terms,
                    source=SOURCE_EPISTEMIC_APPRAISAL,
                    created_at=float(now_ts),
                    utterance=utterance or "",
                    session_context=session_context or "",
                )
            )

    # ── 通道 2：temporal_phenomenology 的張力／牵挂狀態 ──
    try:
        from src.soul.temporal_phenomenology import STATE_TENSION, classify_temporal_state

        snapshot = bond if bond is not None else read_bond_snapshot(agent_id)
        if snapshot is None:
            snapshot = BondSnapshot(False, "P4_none")
        state = classify_temporal_state(
            last_interaction_ts, now_ts, snapshot.to_bond_evidence()
        )
        if state == STATE_TENSION:
            tags = list(snapshot.impression_tags)
            if "bryan" not in [t.lower() for t in tags]:
                tags.append("bryan")
            out.append(
                Tension(
                    tension_id=_tension_id(agent_id, TENSION_KIND_RELATIONAL, "bryan"),
                    kind=TENSION_KIND_RELATIONAL,
                    agent_id=agent_id,
                    subject="bryan",
                    summary="和 Bryan 之間有一段明顯的間隔",
                    tags=tuple(tags),
                    world_keys=(),
                    source=SOURCE_TEMPORAL_PHENOMENOLOGY,
                    created_at=float(now_ts),
                    last_interaction_ts=int(last_interaction_ts),
                    bond=snapshot,
                )
            )
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 三態張力判定失敗：%s: %s", type(exc).__name__, exc)

    return out


# ── 閉合判定與產出 ────────────────────────────────────────────────────────
def _default_writer() -> Any:
    """預設 writer（既有通道：``InnerLifeWriter`` ＋ ``NarrativeTraceWriter``）。"""
    from src.inner_life.trace import NarrativeTraceWriter
    from src.inner_life.writer import InnerLifeWriter

    return InnerLifeWriter(trace_writer=NarrativeTraceWriter())


def _build_thought(tension: Tension, evidence: PathEvidence) -> Tuple[str, Tuple[str, ...]]:
    """由閉合過程連帶產生的**小念頭**（不足以單獨成訊，但值得順嘴帶一句）。

    deterministic、0 LLM：句型固定，內容來自張力主詞 ＋ 證據描述。
    """
    subject = tension.subject or "這件事"
    payload = f"我後來自己想通了「{subject}」——{evidence.detail}。"
    return payload, tuple(tension.tags or ())


def close_tension(
    tension: Tension,
    *,
    probes: Optional[Mapping[str, Callable[[Tension], PathEvidence]]] = None,
    writer: Any = None,
    gate: Any = None,
    tension_store_dir: Optional[Any] = None,
    queue_store_dir: Optional[Any] = None,
    elevation_store_dir: Optional[Any] = None,
    now: Optional[int] = None,
) -> Closure:
    """嘗試把一條張力走完多條分支，直到**某一條實際命中證據**才閉合。

    閉合時產出**三件**（缺一不可）：
      1. ``epistemic_closure`` 事件（既有 ``writer`` ＋ ``submission_gate`` 通道
         ⇒ 進 shadow log／SAGE，**不對用戶發訊**）；
      2. 該張力標記 ``resolved``（寫回張力狀態承載）；
      3. **可掛載小念頭**（帶 topic tags）⇒ 交給話頭掛載槽入隊。

    🔴 **沒有任何一條分支命中證據 ⇒ 不閉合**（不產生事件、不標 resolved、不入隊）。
    這是「不硬編故事」的程式級保證。
    """
    now_ts = int(now if now is not None else time.time())
    probe_map = dict(probes) if probes else default_probes(now=now_ts)

    evidence: Optional[PathEvidence] = None
    for path in expand_paths(tension):
        if not path.available:
            continue
        probe = probe_map.get(path.kind)
        if probe is None:
            continue
        try:
            result = probe(tension)
        except Exception as exc:  # noqa: BLE001 — fail-silent
            logger.debug(
                "[epistemic-action] 分支 %s 探測例外：%s: %s", path.kind, type(exc).__name__, exc
            )
            continue
        if result is not None and result.hit:
            evidence = result
            break

    if evidence is None:
        return Closure(
            closed=False,
            tension_id=tension.tension_id,
            reason="所有分支皆未命中證據（張力維持，不硬閉合）",
        )

    # ── 產出 1：epistemic_closure 事件（既有 writer 通道）──
    event_id: Optional[str] = None
    submitted = False
    try:
        from src.inner_life.event import (
            Provenance,
            TRIGGER_TYPE_SYSTEM,
        )

        own_writer = writer if writer is not None else _default_writer()
        event = own_writer.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_SYSTEM,
                actor_id=tension.agent_id,
                source_system="system",
                trace_ref=f"{CLOSURE_TRACE_PREFIX}{tension.tension_id}",
                extras={
                    "tension_kind": tension.kind,
                    "tension_source": tension.source,
                    "closed_via": evidence.path_kind,
                    "evidence": evidence.detail[:200],
                    "subject": (tension.subject or "")[:120],
                },
            ),
            ts=datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        )
        event_id = event.event_id
        if gate is not None:
            gate.submit(event_id)
            submitted = True
    except Exception as exc:  # noqa: BLE001 — fail-silent（不硬閉合）
        logger.warning(
            "[epistemic-action] 閉合事件寫入失敗（fail-closed）：%s: %s", type(exc).__name__, exc
        )
        return Closure(
            closed=False,
            tension_id=tension.tension_id,
            evidence=evidence,
            reason=f"閉合事件寫入失敗：{type(exc).__name__}",
        )

    # ── 產出 2：標記 resolved ──
    resolved_marked = mark_tension_resolved(tension, tension_store_dir, now=float(now_ts))

    # ── 產出 3：可掛載小念頭 → 話頭掛載槽入隊 ──
    thought_payload, thought_tags = _build_thought(tension, evidence)
    enqueued = False
    try:
        from src.inner_life import piggyback_queue

        quarantined = piggyback_queue.resolve_quarantined_ids(elevation_store_dir)
        enqueued = piggyback_queue.enqueue(
            tension.agent_id,
            tags=thought_tags,
            payload=thought_payload,
            source=PIGGYBACK_SOURCE_CLOSURE,
            refs=tuple(evidence.refs),
            store_dir=queue_store_dir,
            quarantined_ids=quarantined,
            now=float(now_ts),
        )
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.warning(
            "[epistemic-action] 小念頭入隊失敗（fail-closed）：%s: %s", type(exc).__name__, exc
        )

    # ── 產出 2b：把「小念頭已入隊」記進張力承載（append-only 事件）──
    if enqueued:
        try:
            record_thought_enqueued(
                tension,
                tension_store_dir,
                thought_payload=thought_payload,
                thought_tags=thought_tags,
                now=float(now_ts),
            )
        except Exception as exc:  # noqa: BLE001 — fail-silent（不影響閉合結果）
            logger.debug(
                "[epistemic-action] 小念頭事件寫入失敗：%s: %s", type(exc).__name__, exc
            )

    return Closure(
        closed=True,
        tension_id=tension.tension_id,
        event_id=event_id,
        evidence=evidence,
        thought_payload=thought_payload,
        thought_tags=thought_tags,
        enqueued=enqueued,
        resolved_marked=resolved_marked,
        submitted=submitted,
        reason=f"以 {evidence.path_kind} 分支命中證據而閉合",
    )


# ── 背景行動（一次完整回合）────────────────────────────────────────────────
def run_background_action(
    agent_id: str,
    *,
    utterance: str = "",
    session_context: str = "",
    now: Optional[int] = None,
    last_interaction_ts: int = 0,
    writer: Any = None,
    gate: Any = None,
    tension_store_dir: Optional[Any] = None,
    queue_store_dir: Optional[Any] = None,
    elevation_store_dir: Optional[Any] = None,
    memory_dir: Optional[Any] = None,
    world_dir: Optional[Any] = None,
    probes: Optional[Mapping[str, Callable[[Tension], PathEvidence]]] = None,
) -> List[Closure]:
    """跑一輪自主求知行動：收集張力 ⇒ 判定 ⇒ 展開分支 ⇒ 閉合。

    **全程不對用戶發任何訊息**：本函式只做「讀資料 ＋ 寫自家承載 ＋ 寫既有
    內在生活事件通道」。回傳值是閉合結果清單（給呼叫端觀測），不是發訊動作。

    fail-silent：任何例外 → 空清單（絕不影響既有路徑）。
    """
    try:
        now_ts = int(now if now is not None else time.time())
        probe_map = dict(probes) if probes else default_probes(now=now_ts)
        if memory_dir is not None or world_dir is not None:
            base_memory = memory_dir
            base_world = world_dir
            probe_map[PATH_MEMORY_RECALL] = lambda t: memory_probe(t, memory_dir=base_memory)
            probe_map[PATH_WORLD_VERIFY] = lambda t: world_probe(t, world_dir=base_world)

        # 1) 先把「先前仍未閉合」的張力撈回來（它們可能被 SELF_REASON 收束）。
        tensions = list(load_open_tensions(agent_id, tension_store_dir))

        # 2) 再收當下的新張力（兩個既有通道）。
        fresh = collect_tensions(
            agent_id,
            utterance=utterance,
            session_context=session_context,
            now=now_ts,
            last_interaction_ts=last_interaction_ts,
        )
        known = {t.tension_id for t in tensions}
        for tension in fresh:
            if tension.tension_id in known:
                continue
            tensions.append(tension)
            known.add(tension.tension_id)
            upsert_open_tension(tension, tension_store_dir)

        # 3) 觸發判定（唯一入口：存在未閉合張力）。
        if not should_act(tensions):
            return []

        # 4) 逐條走分支閉合。
        closures: List[Closure] = []
        for tension in tensions:
            closure = close_tension(
                tension,
                probes=probe_map,
                writer=writer,
                gate=gate,
                tension_store_dir=tension_store_dir,
                queue_store_dir=queue_store_dir,
                elevation_store_dir=elevation_store_dir,
                now=now_ts,
            )
            closures.append(closure)
        return closures
    except Exception as exc:  # noqa: BLE001 — fail-silent（不影響既有路徑）
        logger.warning(
            "[epistemic-action] 背景行動失敗（fail-silent）：%s: %s", type(exc).__name__, exc
        )
        return []


__all__ = [
    "CLOSURE_TRACE_PREFIX",
    "PATH_KINDS",
    "PATH_MEMORY_RECALL",
    "PATH_SELF_REASON",
    "PATH_WORLD_VERIFY",
    "PIGGYBACK_SOURCE_CLOSURE",
    "SOURCE_EPISTEMIC_APPRAISAL",
    "SOURCE_TEMPORAL_PHENOMENOLOGY",
    "TENSION_EVENT_FIELDS",
    "TENSION_EVENT_KINDS",
    "TENSION_EVENT_OPENED",
    "TENSION_EVENT_RESOLVED",
    "TENSION_EVENT_THOUGHT_ENQUEUED",
    "TENSION_KIND_EPISTEMIC_GAP",
    "TENSION_KIND_RELATIONAL",
    "TENSION_PAYLOAD_EXTRA_KEYS",
    "TENSION_RESOLUTION_EXTRA_KEYS",
    "TENSION_STATUSES",
    "TENSION_STATUS_OPEN",
    "TENSION_STATUS_PARTIALLY_RESOLVED",
    "TENSION_STATUS_RESOLVED",
    "TENSION_STORE_EVENTS_FILENAME",
    "TENSION_STORE_FILENAME",
    "TENSION_STORE_MIGRATED_SUFFIX",
    "BondSnapshot",
    "Closure",
    "Path",
    "PathEvidence",
    "Tension",
    "close_tension",
    "collect_tensions",
    "default_probes",
    "expand_paths",
    "legacy_tension_store_path",
    "load_open_tensions",
    "load_tension_records",
    "mark_tension_resolved",
    "memory_probe",
    "migrate_legacy_tension_store",
    "read_bond_snapshot",
    "read_last_interaction_ts",
    "record_thought_enqueued",
    "run_background_action",
    "save_tension_records",
    "self_reason_probe",
    "should_act",
    "tension_store_path",
    "upsert_open_tension",
    "world_probe",
]
