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
- **只寫自家承載**：張力狀態承載（``epistemic_tensions.json``）與話頭佇列，
  皆寫在呼叫端指定的 ``store_dir``（生產接線**預設關閉** ⇒ 生產零寫入）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path as _FsPath
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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

#: 張力狀態承載檔名（寫在 ``store_dir`` 底下）。
TENSION_STORE_FILENAME = "epistemic_tensions.json"

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


# ── 張力狀態承載（只寫自家檔；生產接線預設關閉）──────────────────────────
def tension_store_path(store_dir: Optional[Any] = None) -> Path:
    """張力狀態承載檔路徑。"""
    if store_dir is None:
        from src.paths import data_root

        store_dir = _FsPath(data_root()) / "inner_life"
    return _FsPath(store_dir) / TENSION_STORE_FILENAME


def _load_store(store_dir: Optional[Any]) -> Dict[str, Any]:
    path = tension_store_path(store_dir)
    try:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 張力承載讀取失敗：%s: %s", type(exc).__name__, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    agents = raw.get("agents")
    return agents if isinstance(agents, dict) else {}


def _save_store(store_dir: Optional[Any], store: Mapping[str, Any]) -> bool:
    path = tension_store_path(store_dir)
    payload = {"agents": dict(store)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[epistemic-action] 張力承載寫入失敗：%s: %s", type(exc).__name__, exc)
        return False


def load_tension_records(agent_id: str, store_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    """讀出某 agent 的張力紀錄（``{"tension": {...}, "status": "open"|"resolved"}``）。"""
    agents = _load_store(store_dir)
    records = agents.get(agent_id)
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def save_tension_records(
    agent_id: str, records: Sequence[Mapping[str, Any]], store_dir: Optional[Any] = None
) -> bool:
    """寫回某 agent 的張力紀錄。"""
    agents = _load_store(store_dir)
    agents[agent_id] = [dict(r) for r in records]
    return _save_store(store_dir, agents)


def load_open_tensions(
    agent_id: str, store_dir: Optional[Any] = None
) -> List[Tension]:
    """讀出某 agent 仍**未閉合**的張力（供下一輪重新推演）。"""
    out: List[Tension] = []
    for record in load_tension_records(agent_id, store_dir):
        if record.get("status") != "open":
            continue
        tension = Tension.from_dict(record.get("tension"))
        if tension is not None:
            out.append(tension)
    return out


def upsert_open_tension(tension: Tension, store_dir: Optional[Any] = None) -> bool:
    """把一條張力記成「未閉合」（同 id 覆蓋，不重複堆積）。"""
    records = load_tension_records(tension.agent_id, store_dir)
    payload = {"tension": tension.to_dict(), "status": "open", "resolved_at": None}
    for index, record in enumerate(records):
        existing = record.get("tension")
        if isinstance(existing, dict) and existing.get("tension_id") == tension.tension_id:
            records[index] = payload
            break
    else:
        records.append(payload)
    return save_tension_records(tension.agent_id, records, store_dir)


def mark_tension_resolved(
    tension: Tension, store_dir: Optional[Any] = None, now: Optional[float] = None
) -> bool:
    """把一條張力標記 ``resolved``（寫回既有狀態承載）。"""
    resolved_at = float(now if now is not None else time.time())
    records = load_tension_records(tension.agent_id, store_dir)
    found = False
    for index, record in enumerate(records):
        existing = record.get("tension")
        if isinstance(existing, dict) and existing.get("tension_id") == tension.tension_id:
            records[index] = {
                "tension": existing,
                "status": "resolved",
                "resolved_at": resolved_at,
            }
            found = True
            break
    if not found:
        records.append(
            {
                "tension": tension.to_dict(),
                "status": "resolved",
                "resolved_at": resolved_at,
            }
        )
    return save_tension_records(tension.agent_id, records, store_dir)


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


def read_bond_snapshot(agent_id: str) -> Optional[BondSnapshot]:
    """讀 SG-3 §6.3 羈絆證據（relationships.json 唯讀；缺 entry → ``None``）。

    ⚠️ 鏡像 ``src.soul.temporal_phenomenology._read_bond_evidence``（私有函式）。
    判定規則逐條對齊；漂移由測試釘死。**只讀、不寫、不建立 entry**。
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
        if not isinstance(entry, dict):
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
    "TENSION_KIND_EPISTEMIC_GAP",
    "TENSION_KIND_RELATIONAL",
    "TENSION_STORE_FILENAME",
    "BondSnapshot",
    "Closure",
    "Path",
    "PathEvidence",
    "Tension",
    "close_tension",
    "collect_tensions",
    "default_probes",
    "expand_paths",
    "load_open_tensions",
    "mark_tension_resolved",
    "memory_probe",
    "read_bond_snapshot",
    "run_background_action",
    "self_reason_probe",
    "should_act",
    "tension_store_path",
    "upsert_open_tension",
    "world_probe",
]
