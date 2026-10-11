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

不碰 frozen contract：不改 emergent_projection 主動投影（含
NON_PROJECTED_LIFECYCLE_STATES）、SAGE、InnerLifeEvent、4 handlers、
essence 門檻；本模組只讀 ``elevation_nodes.jsonl``。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .emergent_projection import (
    DEFAULT_AGENT_ID,
    PROJECTABLE_NODE_TYPES,
    _normalize_lifecycle_state,
    load_elevation_nodes,
)

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
    """
    try:
        # ── 參數守門（fail-closed，絕不 raise）──
        if not isinstance(agent_id, str) or not agent_id:
            return []
        if agent_id == DEFAULT_AGENT_ID:
            return []  # world node 永不回憶
        if not isinstance(query, str) or query == "":
            return []
        if state_filter is None:
            states = RECALLABLE_LIFECYCLE_STATES
        else:
            try:
                states = set(state_filter)
            except TypeError:
                return []
            # 空集合無可查態；含任何非法態（active / weakening / 未知字串）一律 []。
            if not states or not states <= RECALLABLE_LIFECYCLE_STATES:
                return []

        nodes = load_elevation_nodes(store_dir)
        hits: List[Dict[str, Any]] = []
        order: List[tuple] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            # 只查該靈魂自己的節點（world node 永不回憶，雙保險）。
            node_agent = node.get("agent_id")
            if node_agent == DEFAULT_AGENT_ID:
                continue
            if node_agent != agent_id:
                continue
            # 只查 soul 四層（pattern 不回憶，沿用 PROJECTABLE_NODE_TYPES）。
            node_type = node.get("node_type")
            if node_type not in PROJECTABLE_NODE_TYPES:
                continue
            node_id = node.get("node_id")
            content = node.get("content")
            if not node_id or not isinstance(content, str):
                continue
            # 子字串 deterministic 匹配（不做語義排序、不做 LLM）。
            if query not in content:
                continue
            # 態過濾：缺省 lifecycle_state 視為 active（沿用 _normalize，
            # additive 相容）→ 自然落出回憶範圍。
            state = _normalize_lifecycle_state(node)
            if state not in states:
                continue
            hits.append(
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "content": content,  # 原文照錄，不加語氣前綴（語氣隔離在呈現層）
                    "lifecycle_state": state,
                    "lineage_path": node.get("lineage_path"),  # 照抄，不展開遞迴查詢
                    "superseded_by": node.get("superseded_by"),  # 無則 null
                }
            )
            order.append((str(node.get("created_ts") or ""), node_id))

        # deterministic 排序：created_ts asc + node_id asc（沿用投影排序）。
        ranked = sorted(zip(order, hits), key=lambda pair: (pair[0][0], pair[0][1]))
        return [hit for _, hit in ranked]
    except Exception as exc:  # noqa: BLE001 — 失敗靜默，絕不 raise
        logger.debug("query_recall failed (fail-silent): %s: %s", type(exc).__name__, exc)
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
    "RECALLABLE_LIFECYCLE_STATES",
    "RECALL_HIT_KEYS",
    "format_recall_hit",
    "query_recall",
]
