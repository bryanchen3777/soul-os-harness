"""
src/inner_life/piggyback_queue.py — 話頭掛載槽（Opportunistic Piggybacking）

**SIM-EPIS-PIGGYBACK-1 ②**（工單 §3.2）。

為什麼存在
----------
「零打擾」的另一面是「別把想講的話憋成垃圾」。有些念頭**不足以單獨成一則訊息**
（講出去只是打擾），但**值得在下次話題剛好相關時順嘴帶一句**。本模組就是那個槽：
一個**每 agent 最多 8 筆**的 FIFO 佇列，進場的只有**已閉合**的副產物，
出場的條件是「**當次話題 tags 有重疊**」，而且**一次最多 1 個**、**帶出即消耗**。

紀律（與本票其他模組同款）
--------------------------
- **唯讀輸入、唯寫自家佇列**：本模組只寫 ``<store_dir>/<agent>.json``，
  不碰 ``data/soul``、不碰記憶庫、**不對用戶發任何訊息**。
- **隔離紅線**：被隔離（quarantined）的節點／事件**不得進隊、不得被帶出**。
  隔離判定走既有單點收斂 ``lifecycle_overlay.load_quarantined_node_ids``。
- **fail-silent**：任何讀寫異常 → 空結果（寧可不帶，不可讓 prompt 組裝失敗）。
- **deterministic**：同一份佇列 ＋ 同一組話題 tags ⇒ 同一個結果。
- **0 LLM**：本模組不含任何模型呼叫。

**本票不部署**：``FEATURE_ENV`` 預設關閉（見 :func:`feature_enabled`），
生產零行為變化；啟用另需 Owner 授權。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("soul_os.inner_life.piggyback_queue")

# ── 容量與時效 ────────────────────────────────────────────────────────────

#: 每 agent FIFO 佇列上限（筆）。滿 ⇒ 丟棄**最舊**的一筆。
PIGGYBACK_QUEUE_CAPACITY = 8

#: 掛載項存活秒數（7 天）。
#:
#: ⚠️ **語意一致但獨立命名**：這個 7 天與 ``src/soul/temporal_phenomenology.py``
#: 的「7 天」是同一個現象學語意（張力窗口），但那個模組的 7 天是**私有常數**
#: （``_TENSION_MAX_ELAPSED_SEC``）。本票**不得引用該模組的私有常數**
#: ⇒ 此處用具名新常數，兩者的「一致」靠本模組 docstring 明文 + 測試釘死，
#: 不靠 import（避免把兩個模組綁成硬依賴）。
PIGGYBACK_TTL_SECONDS = 7 * 24 * 3600

#: 一次「帶出」最多幾個（工單 §3.3：一次最多 1 個）。
PIGGYBACK_MAX_PER_TOPIC = 1

#: 唯一合法入隊來源：**已閉合**的認知閉合副產物。
#:
#: 🔴 未閉合的張力**拒絕入隊**——那會變成打擾（工單 §3.2）。
PIGGYBACK_SOURCE_CLOSURE = "epistemic_closure"

#: 全票（自主求知行動 ＋ 話頭掛載）的**唯一人工 opt-in 旗標**。
#:
#: 缺省＝關閉 ⇒ 生產零行為變化（工單 §4.5）。本票**不部署**；
#: 啟用另需 Owner 授權。
FEATURE_ENV = "SOUL_OS_EPISTEMIC_PIGGYBACK"

#: 旗標的關閉值（小寫比對；缺省＝未設定＝關閉）。
FEATURE_OFF_VALUES: frozenset = frozenset({"off", "0", "false", "no", "none"})

#: 佇列檔名前綴（每 agent 一檔）。
QUEUE_FILE_SUFFIX = ".piggyback.json"

#: agent_id → 檔名用的安全字元（其餘以 ``_`` 置換）。
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def feature_enabled() -> bool:
    """本票旗標是否開啟（**缺省關閉** ⇒ 生產零行為變化）。

    解析規則與 repo 其他旗標一致：**非空且非 off/0/false/no/none 即 ON**；
    變數「缺席」**不**代表 ON。
    """
    raw = os.environ.get(FEATURE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in FEATURE_OFF_VALUES


# ── 詞元正規化（話題重疊判定）───────────────────────────────────────────────
def normalize_tag(tag: Any) -> str:
    """把一個詞元正規化成可比較的 key（NFKC + 小寫 + 去空白）。

    空／非字串 → ``""``（呼叫端視為無效詞元）。
    """
    if not isinstance(tag, str):
        return ""
    text = unicodedata.normalize("NFKC", tag).lower().strip()
    return "".join(ch for ch in text if not ch.isspace())


def _normalized_tags(tags: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for tag in tags or ():
        key = normalize_tag(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def tags_overlap(left: Optional[Iterable[Any]], right: Optional[Iterable[Any]]) -> List[str]:
    """兩組詞元的重疊（正規化後的交集，原樣回傳左邊的 key）。

    空集合 ⇒ ``[]``（⇒ 不重疊 ⇒ 不帶出）。
    """
    right_keys = set(_normalized_tags(right))
    if not right_keys:
        return []
    return [key for key in _normalized_tags(left) if key in right_keys]


# ── 資料模型 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PiggybackItem:
    """一筆待掛載的小念頭。

    欄位對應工單 §3.2 的狀態定義：``{tags, payload, created_at, expires_at, source}``。
    ``refs`` 是附加的**隔離引用**（節點 id／事件 id）——任一被隔離即不得入隊／帶出。
    """

    tags: Tuple[str, ...]
    payload: str
    created_at: float
    expires_at: float
    source: str
    refs: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tags": list(self.tags),
            "payload": self.payload,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "source": self.source,
            "refs": list(self.refs),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["PiggybackItem"]:
        """從持久化 dict 還原；壞資料 → ``None``（fail-silent，丟棄該筆）。"""
        if not isinstance(raw, dict):
            return None
        tags = raw.get("tags")
        payload = raw.get("payload")
        created_at = raw.get("created_at")
        expires_at = raw.get("expires_at")
        source = raw.get("source")
        if not isinstance(tags, (list, tuple)):
            return None
        if not isinstance(payload, str) or not payload.strip():
            return None
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
            return None
        if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
            return None
        if not isinstance(source, str) or not source:
            return None
        refs = raw.get("refs") or ()
        if not isinstance(refs, (list, tuple)):
            refs = ()
        return cls(
            tags=tuple(str(t) for t in tags),
            payload=payload,
            created_at=float(created_at),
            expires_at=float(expires_at),
            source=source,
            refs=tuple(str(r) for r in refs),
        )

    def is_expired(self, now: float) -> bool:
        """是否已過期（``now >= expires_at`` ⇒ 過期）。"""
        return float(now) >= float(self.expires_at)

    def is_quarantined(self, quarantined_ids: Optional[Iterable[str]]) -> bool:
        """是否命中隔離集合（refs 任一命中即算隔離）。"""
        if not quarantined_ids:
            return False
        blocked = set(quarantined_ids)
        return any(ref in blocked for ref in self.refs)


# ── 佇列持久化（每 agent 一檔）─────────────────────────────────────────────
def _safe_agent_name(agent_id: str) -> str:
    """把 agent_id 轉成安全的檔名 stem（保留可讀性 + 加短雜湊防撞名）。"""
    raw = str(agent_id or "")
    stem = _UNSAFE_NAME_RE.sub("_", raw).strip("_") or "agent"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}"


def resolve_store_dir(store_dir: Optional[Any] = None) -> Path:
    """解析佇列目錄：顯式傳入 > ``data_root()/inner_life/piggyback``。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root  # lazy import 避免循環

    return Path(data_root()) / "inner_life" / "piggyback"


def queue_path(agent_id: str, store_dir: Optional[Any] = None) -> Path:
    """某 agent 的佇列檔路徑。"""
    return resolve_store_dir(store_dir) / f"{_safe_agent_name(agent_id)}{QUEUE_FILE_SUFFIX}"


def load_items(agent_id: str, store_dir: Optional[Any] = None) -> List[PiggybackItem]:
    """讀出某 agent 的全部掛載項（壞檔 → 空清單，絕不 raise）。"""
    path = queue_path(agent_id, store_dir)
    try:
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[piggyback] 佇列讀取失敗（視為空）：%s: %s", type(exc).__name__, exc)
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("items")
    if not isinstance(items, list):
        return []
    out: List[PiggybackItem] = []
    for entry in items:
        item = PiggybackItem.from_dict(entry)
        if item is not None:
            out.append(item)
    return out


def save_items(
    agent_id: str, items: Sequence[PiggybackItem], store_dir: Optional[Any] = None
) -> bool:
    """寫回某 agent 的佇列（失敗 → ``False``，fail-silent）。"""
    path = queue_path(agent_id, store_dir)
    payload = {
        "agent_id": agent_id,
        "items": [item.to_dict() for item in items],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[piggyback] 佇列寫入失敗：%s: %s", type(exc).__name__, exc)
        return False


# ── 隔離（quarantine）─────────────────────────────────────────────────────
def resolve_quarantined_ids(elevation_store_dir: Optional[Any] = None) -> Set[str]:
    """取得被隔離的節點 id 集合（唯讀；走既有單點收斂）。

    隔離集合的**唯一解讀者**是 ``src.inner_life.lifecycle_overlay``；本模組
    只呼叫它，不自創第二套判定（避免守門散落到 N 個消費端）。
    """
    try:
        from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

        if elevation_store_dir is None:
            from src.paths import data_root

            elevation_store_dir = Path(data_root()) / "elevation"
        return set(load_quarantined_node_ids(elevation_store_dir))
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug("[piggyback] 隔離集合讀取失敗（視為空）：%s: %s", type(exc).__name__, exc)
        return set()


# ── 狀態機：入隊 ──────────────────────────────────────────────────────────
def enqueue(
    agent_id: str,
    *,
    tags: Sequence[Any],
    payload: str,
    source: str,
    refs: Sequence[str] = (),
    store_dir: Optional[Any] = None,
    quarantined_ids: Optional[Iterable[str]] = None,
    now: Optional[float] = None,
) -> bool:
    """入隊一筆掛載項。

    守門（任一不過 ⇒ ``False``，不入隊）：
      1. ``source`` **必須**是 ``epistemic_closure``（未閉合的張力拒絕入隊）；
      2. ``refs`` 不得命中隔離集合；
      3. ``payload`` 不得為空。

    容量：每 agent FIFO，上限 :data:`PIGGYBACK_QUEUE_CAPACITY` 筆，滿 ⇒ 丟最舊。
    過期：``expires_at = created_at + PIGGYBACK_TTL_SECONDS``（7 天）。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    if source != PIGGYBACK_SOURCE_CLOSURE:
        # 🔴 只有「已閉合的副產物」可以進槽；未閉合的張力進來＝打擾。
        logger.debug("[piggyback] 拒絕入隊（非閉合來源）：source=%r", source)
        return False
    if not isinstance(payload, str) or not payload.strip():
        return False
    normalized = _normalized_tags(tags)
    if not normalized:
        return False

    blocked = set(quarantined_ids) if quarantined_ids is not None else set()
    if blocked and any(ref in blocked for ref in refs):
        logger.debug("[piggyback] 拒絕入隊（命中隔離）")
        return False

    created = float(now if now is not None else 0.0)
    item = PiggybackItem(
        tags=tuple(normalized),
        payload=payload,
        created_at=created,
        expires_at=created + PIGGYBACK_TTL_SECONDS,
        source=source,
        refs=tuple(str(r) for r in refs),
    )

    items = load_items(agent_id, store_dir)
    items.append(item)
    # FIFO 容量：滿 ⇒ 丟最舊（list 頭 = 最舊）。
    while len(items) > PIGGYBACK_QUEUE_CAPACITY:
        items.pop(0)
    return save_items(agent_id, items, store_dir)


# ── 狀態機：挑選 / 消耗 ───────────────────────────────────────────────────
def pick_for_topic(
    agent_id: str,
    topic_tags: Sequence[Any],
    *,
    store_dir: Optional[Any] = None,
    quarantined_ids: Optional[Iterable[str]] = None,
    now: Optional[float] = None,
) -> Optional[PiggybackItem]:
    """挑一筆與當次話題相關的掛載項，**並且消耗掉它**。

    規則（工單 §3.2 / §3.3）：
      - 候選必須與 ``topic_tags`` **重疊 ≥ 1 個 tag**；無重疊 ⇒ 不帶（``None``）。
      - 過期者**不帶**（且順手從佇列清掉）。
      - 隔離者**不帶**。
      - **一次最多 1 個**（:data:`PIGGYBACK_MAX_PER_TOPIC`）。
      - 帶出即**從佇列移除**（避免重複）。

    回傳 ``None`` 表示「這次不帶」（寧可不帶）。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return None
    now_ts = float(now if now is not None else 0.0)
    items = load_items(agent_id, store_dir)
    if not items:
        return None

    # 過期清理（順手寫回，避免過期項長期堆積）。
    alive = [item for item in items if not item.is_expired(now_ts)]
    dropped_expired = len(items) - len(alive)

    blocked = set(quarantined_ids) if quarantined_ids is not None else set()
    picked: Optional[PiggybackItem] = None
    remaining: List[PiggybackItem] = []
    for item in alive:
        if picked is None:
            if blocked and item.is_quarantined(blocked):
                # 🔴 隔離者永不帶出（寧可不帶）。
                continue
            if tags_overlap(item.tags, topic_tags):
                picked = item
                continue  # 帶出 ⇒ 消耗（不入 remaining）
        remaining.append(item)

    if dropped_expired or picked is not None:
        save_items(agent_id, remaining, store_dir)
    return picked


def purge_expired(
    agent_id: str,
    *,
    store_dir: Optional[Any] = None,
    now: Optional[float] = None,
) -> int:
    """清掉過期項；回傳清掉幾筆。"""
    now_ts = float(now if now is not None else 0.0)
    items = load_items(agent_id, store_dir)
    alive = [item for item in items if not item.is_expired(now_ts)]
    removed = len(items) - len(alive)
    if removed:
        save_items(agent_id, alive, store_dir)
    return removed


__all__ = [
    "FEATURE_ENV",
    "FEATURE_OFF_VALUES",
    "PIGGYBACK_MAX_PER_TOPIC",
    "PIGGYBACK_QUEUE_CAPACITY",
    "PIGGYBACK_SOURCE_CLOSURE",
    "PIGGYBACK_TTL_SECONDS",
    "PiggybackItem",
    "enqueue",
    "feature_enabled",
    "load_items",
    "normalize_tag",
    "pick_for_topic",
    "purge_expired",
    "queue_path",
    "resolve_quarantined_ids",
    "resolve_store_dir",
    "save_items",
    "tags_overlap",
]
