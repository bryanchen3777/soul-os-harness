"""
src/inner_life/elevation_recall.py — DORMANT / SUPERSEDED 只讀回憶查詢（C4-IMPL）

定位（照 docs/ELEVATION-RECALL-CONTRACT.md §2–§5，契約逐字即規格）：
  - 這是**讀側顯式回憶查詢**：被問才查 DORMANT / SUPERSEDED 節點，
    **永不注入** EMERGENT block（主動投影路徑 ``format_emergent_block`` 0 改動）。
  - **0 寫入、0 狀態遷移**：不寫 elevation_nodes.jsonl / elevation_edges /
    任何 trace sidecar（連 elevation_projection_trace.jsonl 也不寫——
    回憶不是投影）；不呼叫 REINFORCE / SUPERSEDE / evaluate_lifecycle。
  - **失敗靜默**：檔不存在／壞行／壞時間戳 → 跳過回 ``[]``，絕不 raise
   （沿用 ``load_elevation_nodes`` fail-silent 語義）。
  - 呈現語氣（契約 §3.1）在**呈現層**做（``format_recall_hit``）；
    ``RecallHit`` 的 content 原文照錄，不加語氣前綴。

ELEVATION-RECALL-HARDENING-1（2026-10-19）本模組的三層結構：
  (a) ``_recallable_hits(...)`` — 回憶可命中節點的**唯一**過濾管線。
      兩條檢索路徑（契約路徑 ``query_recall`` ＋ 詞元級 ``query_recall_by_tags``）
      都走它 ⇒ **隔離守門只寫在這一處**（SOUL-BUG-010 裁定 1：守門不得散落
      到第 N 個消費端）。守門順序與條件逐字沿用原 ``query_recall`` 現況，
      只在中間插入第 5 步「隔離守門」。
  (b) 契約路徑 ``query_recall`` — 簽章／比對語意／回傳形狀／排序／失敗靜默
      **全部不動**（docs/ELEVATION-RECALL-CONTRACT.md §0–§7 逐字保留），
      只是改走 (a) 的共用管線因而獲得隔離守衛。
  (c) 詞元級路徑 ``query_recall_by_tags`` — **新增並存**入口（契約 §8 附錄），
      union 比對、deterministic、零 LLM。**不取代** (b)。

🔴 隔離資訊的來源：``lifecycle_overlay.load_quarantined_node_ids``（sidecar 的
唯一解讀者）。旗標**不**掛在節點 dict 上——``elevation_adapter`` 以
``ElevationNode(**n)`` 建引擎，而 ``ElevationNode`` 是外部套件的 frozen
dataclass（欄位固定），節點 dict 多一個 key 就會讓整條昇華路徑爆掉。

不碰 frozen contract：不改 emergent_projection 主動投影（含
NON_PROJECTED_LIFECYCLE_STATES）、SAGE、InnerLifeEvent、4 handlers、
essence 門檻；本模組只讀 ``elevation_nodes.jsonl``。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .emergent_projection import (
    DEFAULT_AGENT_ID,
    PROJECTABLE_NODE_TYPES,
    _normalize_lifecycle_state,
    _resolve_store_dir,
    load_elevation_nodes,
)
from .lifecycle_overlay import load_quarantined_node_ids

logger = logging.getLogger("soul_os.inner_life.elevation_recall")

# 回憶可查的態（契約 §2.1：只接受 dormant / superseded；active / weakening
# 是主動投影的轄區，不經回憶）。
RECALLABLE_LIFECYCLE_STATES: frozenset = frozenset({"dormant", "superseded"})

# DORMANT 呈現語氣前綴（契約 §3.1：沿 WEAKENING「我隱約覺得」先例，往過去再推一步）。
DORMANT_TONE_PREFIX = "我以前很在意"

# RecallHit 契約鍵（§2.2）：node + lineage_path + 狀態標，固定六鍵。
RECALL_HIT_KEYS: frozenset = frozenset(
    {"node_id", "node_type", "content", "lifecycle_state", "lineage_path", "superseded_by"}
)

# 詞元級入口（契約 §8 附錄）的守門常數。
#: 單一 tag 正規化後的最短長度（太短的片段噪音太大，例如單字／標點殘留）。
MIN_RECALL_TAG_LENGTH = 2
#: 單次查詢最多採用的 tag 數（取傳入順序前 N 個）——避免長句產生過多
#: 低訊噪 token（量測基準用的 jieba 詞元數遠低於此，屬保守上限）。
MAX_RECALL_TAGS = 12

# 正規化用的空白樣式（Python 的 ``\s`` 在 str 樣式下已涵蓋 CJK 全形空白）。
_WHITESPACE_RE = re.compile(r"\s+")


# ── 正規化與詞元守門（詞元級比對的 SSOT）──────────────────────────────


def _normalize_text(text: Any) -> str:
    """回憶比對用的正規化：lower() ＋ 全形轉半形 ＋ 去空白 ＋ 去標點。

    - 全形轉半形走 ``unicodedata.normalize("NFKC", ...)``（含全形英數與全形標點）。
    - 標點以 Unicode 類別 ``P*`` 判定（涵蓋中英文標點與全形標點）。
    - **不做**語意處理、不做同義詞擴展、不呼叫 LLM（deterministic）。
    """
    if not isinstance(text, str):
        return ""
    s = unicodedata.normalize("NFKC", text).lower()
    s = _WHITESPACE_RE.sub("", s)
    return "".join(ch for ch in s if not unicodedata.category(ch).startswith("P"))


def _normalize_tags(tags: Optional[Iterable[str]]) -> List[str]:
    """把傳入的 tags 正規化成「去重、上限 ``MAX_RECALL_TAGS``」的清單。

    守門順序（逐字照工單 §3.2(c)）：非空 str → 正規化後長度 ≥
    ``MIN_RECALL_TAG_LENGTH`` → 去重（保留傳入順序）→ 上限（取前 N 個）。

    傳入單一字串時視為**單一 tag**（不是逐字元序列）。
    """
    if tags is None:
        return []
    if isinstance(tags, str):
        candidates: List[Any] = [tags]
    else:
        try:
            candidates = list(tags)
        except TypeError:
            return []

    out: List[str] = []
    seen: Set[str] = set()
    for tag in candidates:
        if not isinstance(tag, str):
            continue
        norm = _normalize_text(tag)
        if len(norm) < MIN_RECALL_TAG_LENGTH:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
        if len(out) >= MAX_RECALL_TAGS:
            break
    return out


def _tags_match(normalized_tags: Sequence[str], normalized_content: str) -> bool:
    """union 比對：任一 tag 命中即算命中（intersection 在中文內容上近乎 0 命中）。"""
    if not normalized_tags or not normalized_content:
        return False
    return any(tag in normalized_content for tag in normalized_tags)


# ── 共用過濾管線（唯一守門點）──────────────────────────────────────────


def _resolve_state_filter(state_filter: Optional[Set[str]]) -> Optional[Set[str]]:
    """把 ``state_filter`` 解析成合法態集合。

    Returns:
        合法態集合；**回 ``None`` 代表 fail-closed**（空集合、含非法態、
        型別錯誤——全部回 ``[]``，沿用契約 §2.1）。
    """
    if state_filter is None:
        return set(RECALLABLE_LIFECYCLE_STATES)
    try:
        states = set(state_filter)
    except TypeError:
        return None
    # 空集合無可查態；含任何非法態（active / weakening / 未知字串）一律 []。
    if not states or not states <= RECALLABLE_LIFECYCLE_STATES:
        return None
    return states


def _is_quarantined(node: Dict[str, Any], quarantined_ids: Optional[Set[str]] = None) -> bool:
    """隔離判定（單點收斂）。

    - 主要來源：``lifecycle_overlay.load_quarantined_node_ids``（sidecar 的唯一解讀者）
      給出的節點 id 集合。
    - 防禦性補充：節點 dict 自己帶 ``quarantined`` 旗標時同樣視為隔離
      （任何真值旗標都擋下——硬不變式是「隔離節點**永不**召回」，寧可多擋不可漏擋）。
    """
    if quarantined_ids and node.get("node_id") in quarantined_ids:
        return True
    return bool(node.get("quarantined"))


def _is_recallable_node(
    node: Any, agent_id: str, states: Set[str],
    quarantined_ids: Optional[Set[str]] = None,
) -> bool:
    """單節點守門（無 IO）——共用管線的判定本體。

    順序與條件（逐字沿用原 ``query_recall`` 現況，**不得改動語意**）：
      1. agent_id 守門：只查該靈魂自己的節點；``"default"``（world node）永不回憶
      2. node_type ∈ PROJECTABLE_NODE_TYPES（pattern 不回憶）
      3. node_id / content 守門
      4. 🔴 隔離守門：被隔離的節點 ⇒ 跳過
      5. lifecycle_state ∈ 過濾態
    """
    if not isinstance(node, dict):
        return False
    # 只查該靈魂自己的節點（world node 永不回憶，雙保險）。
    node_agent = node.get("agent_id")
    if node_agent == DEFAULT_AGENT_ID:
        return False
    if node_agent != agent_id:
        return False
    # 只查 soul 四層（pattern 不回憶，沿用 PROJECTABLE_NODE_TYPES）。
    if node.get("node_type") not in PROJECTABLE_NODE_TYPES:
        return False
    node_id = node.get("node_id")
    content = node.get("content")
    if not node_id or not isinstance(content, str):
        return False
    # 🔴 隔離守門（本票核心）：SOUL-BUG-010 隔離的污染節點**永不召回**。
    if _is_quarantined(node, quarantined_ids):
        return False
    # 態過濾：缺省 lifecycle_state 視為 active（沿用 _normalize，
    # additive 相容）→ 自然落出回憶範圍。
    if _normalize_lifecycle_state(node) not in states:
        return False
    return True


def _recallable_hits(
    agent_id: str,
    state_filter: Optional[Set[str]],
    *,
    store_dir: Optional[Any],
) -> List[Dict[str, Any]]:
    """回憶可命中節點的**唯一**過濾管線（兩條檢索路徑共用）。

    條件與順序逐字沿用原 ``query_recall`` 現況（**不得改動語意**），
    唯一新增的是第 5 步「隔離守門」：

      1. agent_id 守門：非空 str；``"default"``（world node）永不回憶
      2. state_filter 守門：缺省＝{dormant, superseded}；含任何非法態 ⇒ []
      3. node_type ∈ PROJECTABLE_NODE_TYPES（pattern 不回憶）
      4. node_id / content 守門
      5. 🔴 隔離守門：被隔離的節點 ⇒ 跳過
      6. lifecycle_state ∈ 過濾態

    排序維持 ``created_ts asc + node_id asc``（現況不動）——由呼叫端在
    命中之後套用（此處只負責「可回憶的節點集合」）。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return []
    if agent_id == DEFAULT_AGENT_ID:
        return []  # world node 永不回憶
    states = _resolve_state_filter(state_filter)
    if states is None:
        return []
    resolved = _resolve_store_dir(store_dir)
    # 隔離資訊與節點狀態讀**同一個** store（sidecar 是隔離的唯一載體）。
    quarantined = load_quarantined_node_ids(resolved)
    nodes = load_elevation_nodes(resolved)
    return [n for n in nodes if _is_recallable_node(n, agent_id, states, quarantined)]


def _build_hit(node: Dict[str, Any]) -> Dict[str, Any]:
    """組出契約 §2.2 的 RecallHit（六鍵，content 原文照錄）。"""
    return {
        "node_id": node.get("node_id"),
        "node_type": node.get("node_type"),
        "content": node.get("content"),  # 原文照錄，不加語氣前綴（語氣隔離在呈現層）
        "lifecycle_state": _normalize_lifecycle_state(node),
        "lineage_path": node.get("lineage_path"),  # 照抄，不展開遞迴查詢
        "superseded_by": node.get("superseded_by"),  # 無則 null
    }


def _rank_hits(pairs: Sequence[Tuple[Tuple[str, Any], Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """deterministic 排序：created_ts asc + node_id asc（沿用投影排序）。

    ``pairs`` 為 ``[((created_ts_str, node_id), hit), ...]``——排序鍵**必須**帶
    created_ts（只用 node_id 排序會改變既有排序語意）。
    """
    ranked = sorted(pairs, key=lambda pair: (pair[0][0], pair[0][1]))
    return [hit for _, hit in ranked]


def _collect_hits(nodes: Sequence[Dict[str, Any]], matched) -> List[Dict[str, Any]]:
    """把「已命中」的節點組成 RecallHit 並排序（兩條檢索路徑共用）。

    ``matched(node) -> bool`` 是各路徑自己的比對語意（子字串／詞元 union）；
    過濾與守門早已在共用管線完成，故此處只負責「組 hit ＋ 排序」。
    """
    order: List[Tuple[str, Any]] = []
    hits: List[Dict[str, Any]] = []
    for node in nodes:
        if not matched(node):
            continue
        hits.append(_build_hit(node))
        order.append((str(node.get("created_ts") or ""), node.get("node_id")))
    return _rank_hits(list(zip(order, hits)))


# ── 檢索路徑 ───────────────────────────────────────────────────────────


def query_recall(
    agent_id: str,
    query: str,
    state_filter: Optional[Set[str]] = None,
    *,
    store_dir: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """顯式只讀回憶查詢（契約 §2.1–§2.2）。

    Args:
        agent_id: 只查該靈魂自己的節點；非空字串必填，
            ``agent_id == "default"`` 的 world node 永不回憶（回 ``[]``）。
        query: 回憶檢索字串（子字串匹配 ``content``，deterministic；
            不做語義排序、不做 LLM）；空字串回 ``[]``。
        state_filter: 態過濾（``{"dormant"}``、``{"superseded"}`` 或兩者）；
            缺省＝兩者；**含非法態一律回 ``[]``**（fail-closed）。
        store_dir: 可選 store 目錄（缺省 ``data_root()/elevation``；
            測試傳 tmp 目錄，不碰生產 ``data/elevation/``）。

    Returns:
        list[RecallHit]：``[{node_id, node_type, content（原文照錄）,
        lifecycle_state, lineage_path（照抄）, superseded_by}]``，
        按 ``created_ts asc + node_id asc`` 排序；無命中或任何失敗回 ``[]``。

    ⚠️ ELEVATION-RECALL-HARDENING-1：節點取得改走共用管線
    ``_recallable_hits``（因此獲得隔離守門）。**簽章／比對語意／回傳形狀／
    排序／失敗靜默全部不變**（契約 §0–§7 逐字保留）。
    """
    try:
        # ── 參數守門（fail-closed，絕不 raise）──
        if not isinstance(query, str) or query == "":
            return []
        candidates = _recallable_hits(agent_id, state_filter, store_dir=store_dir)
        # 子字串 deterministic 匹配（不做語義排序、不做 LLM）。
        return _collect_hits(candidates, lambda node: query in node.get("content"))
    except Exception as exc:  # noqa: BLE001 — 失敗靜默，絕不 raise
        logger.debug("query_recall failed (fail-silent): %s: %s", type(exc).__name__, exc)
        return []


def query_recall_by_tags(
    agent_id: str,
    tags: Sequence[str],
    state_filter: Optional[Set[str]] = None,
    *,
    store_dir: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """詞元級回憶查詢（契約 §8 附錄新增，**不**取代 query_recall）。

    比對語意：把 query 與 content 各自**正規化**後，任一 tag 命中即算命中
    （union；intersection 在中文內容上會把命中率壓到近 0）。
      - 正規化：lower()、全形轉半形、去除所有空白與標點
      - tag 守門：非空 str、正規化後長度 ≥ 2、去重、**上限 12 個**（取傳入順序前 12）
      - 不做語義排序、不呼叫 LLM、deterministic

    過濾條件與隔離守門與 query_recall **完全相同**（共用 ``_recallable_hits``）：

    Args:
        agent_id: 同 ``query_recall``（world node 永不回憶）。
        tags: 詞元序列（單一字串視為單一 tag）。
        state_filter: 同 ``query_recall``（含非法態一律回 ``[]``）。
        store_dir: 同 ``query_recall``。

    Returns:
        list[RecallHit]：形狀同 ``query_recall``（契約六鍵），
        按 ``created_ts asc + node_id asc`` 排序；無命中或任何失敗回 ``[]``。
    """
    try:
        normalized_tags = _normalize_tags(tags)
        if not normalized_tags:
            return []
        candidates = _recallable_hits(agent_id, state_filter, store_dir=store_dir)

        def _matched(node: Dict[str, Any]) -> bool:
            content = _normalize_text(node.get("content"))
            return _tags_match(normalized_tags, content)

        return _collect_hits(candidates, _matched)
    except Exception as exc:  # noqa: BLE001 — 失敗靜默，絕不 raise
        logger.debug(
            "query_recall_by_tags failed (fail-silent): %s: %s", type(exc).__name__, exc
        )
        return []


def format_recall_hit(hit: Dict[str, Any], superseder_content: Optional[str] = None) -> str:
    """回憶呈現層（契約 §3.1 語氣隔離：呼叫方自定位置，禁入 EMERGENT block）。

    - DORMANT → 過去式前綴「我以前很在意……」。
    - SUPERSEDED → 沿用 lineage 降維句式「我以前覺得 A，但後來發現 B。」；
      有取代者正文（``superseder_content``）時帶 B，無則退化為「但後來改變了」。

    Args:
        hit: ``query_recall`` 回傳的 RecallHit（content 原文）。
        superseder_content: 取代者節點正文（呼叫方另查， recall 本身不展開遞迴查詢）。

    Returns:
        帶過去式語氣標記的呈現字串（絕不當當前信念注入）。
    """
    content = hit.get("content", "")
    if hit.get("lifecycle_state") == "superseded":
        if isinstance(superseder_content, str) and superseder_content.strip():
            return f"我以前覺得「{content}」，但後來發現「{superseder_content.strip()}」。"
        return f"我以前覺得「{content}」，但後來改變了。"
    return f"{DORMANT_TONE_PREFIX}「{content}」。"


def _store_path(store_dir: Optional[Any]) -> Path:
    """測試輔助：回憶讀側的 store 檔路徑（斷言 0 寫入用）。"""
    from .emergent_projection import NODES_FILENAME, _resolve_store_dir

    return _resolve_store_dir(store_dir) / NODES_FILENAME


__all__ = [
    "DORMANT_TONE_PREFIX",
    "MAX_RECALL_TAGS",
    "MIN_RECALL_TAG_LENGTH",
    "RECALLABLE_LIFECYCLE_STATES",
    "RECALL_HIT_KEYS",
    "format_recall_hit",
    "query_recall",
    "query_recall_by_tags",
]
