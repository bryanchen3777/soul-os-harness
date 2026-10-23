"""src/inner_life/contradiction_probe.py — 矛盾探針（**沙盒原型**：純函式、唯讀、零 LLM）。

工單：logs/FEAT-SCHEMA-CONTRADICTION-RFC-WORK-ORDER.md（FEAT-SCHEMA-CONTRADICTION）
RFC：docs/FEAT-SCHEMA-CONTRADICTION-RFC.md

為什麼需要這個模組
------------------
矛盾欄位與引擎 API **早已存在**且語意完整（``ElevationNode.contradiction_pressure``
＝ ``tuple[ContradictionRecord]``；``engine.record_contradiction()``；既有計數器
``_count_independent_contradictions()``／``_contradiction_spread_days()``），但
``record_contradiction()`` 的**唯一**呼叫點在 ``engine.supersede()`` 內，而
``supersede()`` 又以「矛盾證據數達門檻」為觸發前提 ⇒ **結構性死鎖**
（累積器恆空 ⇒ ``supersede()`` 恆拋 ``ValueError``）。

本模組回答的是死鎖的**前半段**：「候選矛盾從哪裡來」——**只定義、只量測、不落地**。

本票的硬約束（工單 §2.2／§6）
------------------------------
- **純函式**：輸入＝已載入的節點／邊序列；**自己不讀檔**（讀檔由呼叫端做）⇒
  測試完全不需要生產資料。
- **零新欄位**：既有 ``contradiction_pressure``／``ContradictionRecord`` 欄位族沿用，
  本模組不新增、不修改任何欄位定義。
- **不呼叫 ``engine.record_contradiction()``**：本票只定義與量測，**不寫引擎狀態**
  （寫入路徑屬 Phase 2 提案，見 RFC 第 5 節）。
- **零權重／零門檻常數**：模組內**無任何數值常數**；跨日判定與門檻一律沿用
  ``soul_elevation`` 既有實作（``LIFECYCLE_*`` 門檻**不得**新增／調低／調高）。
- **零 LLM、零 IO**：deterministic；同一輸入兩次呼叫結果逐位元相等。

候選矛盾判準（工單 §2.1，順序固定）
------------------------------------
對每個 agent 的每個靈魂節點 ``N``（``node_type ∈ {value, belief, trait}``、
``lifecycle_state != "superseded"``）：

1. 取 ``N`` **建立之後**到達的有效支持邊集合 ``E(N)``：``edge.node_id == N.node_id``
   且 ``edge.valid_from_ts > N.created_ts`` 且 ``edge.valid_until_ts is None``。
2. 對每條 ``e ∈ E(N)``，以 ``e.source_id`` 解析其**來源事件**，找出該來源在
   **同一 agent、同一 lineage 根**下產生的**另一個**靈魂節點 ``N'``
   （``N' != N``）。
3. 若 ``N'.content != N.content``（**逐位元不等**）⇒ 記為一組**候選矛盾**
   ``(N, N', e)``。
4. ``N'.content == N.content`` ⇒ **不是**矛盾（同命題再確認；已由 REINFORCE 鏈處理）
   ⇒ 不記。

兩處**判準歧義的收斂**（本模組的明確取捨，RFC 第 2 節逐條記錄）
----------------------------------------------------------------
- **「同一 lineage 根」**取**節點層級**的 lineage root（沿 ``parent_node_id`` 鏈上溯
  到無父者；鏈斷時退回 ``lineage_path`` 首段）。此判準**包含**工單括號內的
  「``N'.parent_node_id`` 與 ``N.parent_node_id`` 同鏈或同根」——兩節點同根即其父
  同根或一方為另一方祖先。
- **「另一個靈魂節點」**與 ``N`` 套用**同一組**資格條件（型別 ∈ Phase 1 參與型別、
  ``lifecycle_state != "superseded"``）⇒ 探針對稱、可稽核。
- **時間比對**先**統一解析**再比（``created_ts``／``valid_from_ts`` 皆帶微秒或無，
  字串格式不一致 ⇒ 直接字串比較會錯）⇒ 壞值一律當「不成立」，不猜。

本模組**不**實作 Phase 2 的引擎接線（不 import 引擎、不寫任何節點狀態）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 參與 Phase 1 探針的靈魂節點型別（工單 §2.1：``essence`` **不參與**）。
PROBE_NODE_TYPES: Tuple[str, ...] = ("value", "belief", "trait")

#: 不參與探針的 lifecycle 狀態（工單 §2.1：``superseded`` 不參與）。
EXCLUDED_LIFECYCLE_STATE = "superseded"


@dataclass(frozen=True)
class CandidateContradiction:
    """一組**候選**矛盾 ``(N, N', e)``（frozen；可稽核、可序列化）。

    欄位形態**沿用既有** ``soul_elevation.models.ContradictionRecord``：
    ``source_id``／``ts``／``event_identity``／``provenance_ref`` 四者逐字取自
    來源邊 ``e``；``node_id``／``counterpart_node_id``／``agent_id``／``node_type``
    則是探針自帶的稽核軌跡（**不寫入**引擎欄位，僅供 Phase 2 提案與量測對帳）。
    """

    node_id: str
    counterpart_node_id: str
    source_id: str
    ts: str
    event_identity: Optional[str]
    provenance_ref: Optional[str]
    agent_id: str
    node_type: str


def _get(obj: Any, key: str) -> Any:
    """取欄位值（dict／物件皆可）⇒ 呼叫端可餵 JSON 列或引擎物件。"""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _text(value: Any) -> str:
    """轉成字串（``None`` → 空字串；其餘以 ``str()`` 語義）。"""
    if value is None:
        return ""
    return str(value)


def _parse_ts(value: Any) -> Optional[datetime]:
    """統一解析 ISO 8601 時間戳 → aware ``datetime``（UTC）。

    ``created_ts`` 帶微秒、``valid_from_ts`` 可能帶微秒或無 ⇒ **必須先統一解析
    再比**（字串比較在格式不一致時會錯）。壞值／缺值 → ``None``（⇒ 該配對不成立，
    不猜、不放寬）。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text.removesuffix("Z") + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _content_bytes(node: Any) -> bytes:
    """節點 ``content`` 的 UTF-8 位元組（``content`` 逐位元比對用）。"""
    content = _get(node, "content")
    if content is None:
        return b""
    return str(content).encode("utf-8")


def _lineage_root(node_id: str, by_id: Mapping[str, Any]) -> Optional[str]:
    """節點的 **lineage 根**（沿 ``parent_node_id`` 鏈上溯）。

    - 鏈走到底（無父）⇒ 該節點即根；
    - 鏈斷（父節點不在本次輸入內）或成環 ⇒ 退回 ``lineage_path`` 首段
      （引擎既有的反範式化欄位，語意同為根 id）；仍取不到 ⇒ ``None``
      （⇒ 無法證明同根 ⇒ 不配對）。
    """
    seen: set = set()
    current: Optional[str] = node_id
    while current and current not in seen:
        seen.add(current)
        node = by_id.get(current)
        if node is None:
            break
        parent = _get(node, "parent_node_id")
        if not parent:
            return current
        current = str(parent)
    node = by_id.get(node_id)
    path = _get(node, "lineage_path") if node is not None else None
    if isinstance(path, str) and path:
        return next(iter(path.split("/")), None)
    return None


def _is_probe_node(node: Any) -> bool:
    """該節點是否為參與探針的靈魂節點（型別 ∈ Phase 1 型別 且 未 superseded）。"""
    if _get(node, "node_type") not in PROBE_NODE_TYPES:
        return False
    return _get(node, "lifecycle_state") != EXCLUDED_LIFECYCLE_STATE


def find_candidate_contradictions(
    nodes: Sequence[Any],
    edges: Sequence[Any],
    *,
    agent_id: Optional[str] = None,
) -> List[CandidateContradiction]:
    """找出**候選**矛盾組 ``(N, N', e)``（純函式、deterministic、唯讀）。

    判準見模組 docstring（工單 §2.1，逐條照做）。**本函式不讀檔、不寫檔、不呼叫
    引擎**；輸入為已載入的節點／邊序列（dict 列或引擎物件皆可）。

    Args:
        nodes: 節點序列（每個節點須有 ``node_id``／``node_type``／``content``／
            ``agent_id``／``created_ts``／``lifecycle_state``；``parent_node_id``／
            ``lineage_path`` 用於 lineage 判定）。
        edges: 邊序列（每條邊須有 ``node_id``／``source_id``／``valid_from_ts``／
            ``valid_until_ts``；``inner_life_event_id`` 用於事件身分歧。
        agent_id: 只看此 agent（``None`` ⇒ 全部 agent）。

    Returns:
        候選矛盾清單（**依輸入順序**節點 → 邊 → 對應節點 ⇒ deterministic）。
    """
    node_list = list(nodes)
    edge_list = list(edges)

    by_id: Dict[str, Any] = {}
    for node in node_list:
        node_id = _get(node, "node_id")
        if isinstance(node_id, str) and node_id:
            by_id[node_id] = node

    # ── 參與探針的靈魂節點（同一資格條件套用於 N 與 N' ⇒ 對稱）──
    eligible: Dict[str, Any] = {}
    for node_id, node in by_id.items():
        if not _is_probe_node(node):
            continue
        if agent_id is not None and _text(_get(node, "agent_id")) != _text(agent_id):
            continue
        eligible[node_id] = node

    # ── 邊索引（按 node_id）──
    edges_by_node: Dict[str, List[Any]] = {}
    for edge in edge_list:
        node_id = _get(edge, "node_id")
        if isinstance(node_id, str) and node_id:
            edges_by_node.setdefault(node_id, []).append(edge)

    # ── 來源 → 產生它的靈魂節點（同一 agent；工單 §2.1 步驟 2 的「來源事件」）──
    producers: Dict[Tuple[str, str], List[str]] = {}
    for node_id, node in eligible.items():
        agent = _text(_get(node, "agent_id"))
        for edge in edges_by_node.get(node_id, ()):
            key = (agent, _text(_get(edge, "source_id")))
            producers.setdefault(key, []).append(node_id)

    results: List[CandidateContradiction] = []
    for node_id, node in eligible.items():
        created = _parse_ts(_get(node, "created_ts"))
        if created is None:
            continue
        agent = _text(_get(node, "agent_id"))
        root = _lineage_root(node_id, by_id)
        for edge in edges_by_node.get(node_id, ()):
            # E(N)：建立之後到達 ＋ 仍有效
            if _get(edge, "valid_until_ts") is not None:
                continue
            valid_from = _parse_ts(_get(edge, "valid_from_ts"))
            if valid_from is None or not (valid_from > created):
                continue
            key = (agent, _text(_get(edge, "source_id")))
            for other_id in producers.get(key, ()):
                if other_id == node_id:
                    continue
                other = eligible[other_id]
                if _lineage_root(other_id, by_id) != root:
                    continue
                if _content_bytes(node) == _content_bytes(other):
                    continue
                event_identity = _get(edge, "inner_life_event_id")
                results.append(
                    CandidateContradiction(
                        node_id=node_id,
                        counterpart_node_id=other_id,
                        source_id=_text(_get(edge, "source_id")),
                        ts=_text(_get(edge, "valid_from_ts")),
                        event_identity=(
                            event_identity if isinstance(event_identity, str) else None
                        ),
                        provenance_ref=(
                            event_identity if isinstance(event_identity, str) else None
                        ),
                        agent_id=agent,
                        node_type=_text(_get(node, "node_type")),
                    )
                )
    return results


__all__ = [
    "EXCLUDED_LIFECYCLE_STATE",
    "PROBE_NODE_TYPES",
    "CandidateContradiction",
    "find_candidate_contradictions",
]
