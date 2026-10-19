"""
src/inner_life/recall_readiness.py — 回憶通道的**成熟度量測**（純函式，零 IO 副作用）

定位（ELEVATION-RECALL-HARDENING-1 工單 §3.3）：
  - 這是**量測層**：回答三個問題——
      ① 生產圖譜裡「可回憶池」到底有幾顆（成熟度）；
      ② 真實 user 訊息的命中率是多少（可量測）；
      ③ 第二階段（prompt 注入）能不能開工（go/no-go 判準）。
  - **三個純函式**（IO 注入：節點與探針都由呼叫端傳入）⇒ 守衛測試能直接
    餵入構造出來的資料，證明判定**不是空轉報綠**。
  - 匹配語意**不重寫一份**：詞元正規化與共用過濾管線一律重用
    ``src.inner_life.elevation_recall``（SSOT），避免兩處漂移。

門檻（工單 §9，四項全過才 GO）：
  - 可回憶池中**非隔離**的 dormant／superseded 四層節點數 ≥ 5
  - 池中 superseded 節點數 ≥ 1
  - 真實 probe 命中率 ≥ 15%
  - 命中的是**非隔離**節點 100%

不碰 frozen contract：不寫任何檔案、不呼叫 LLM、不發網路請求。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .emergent_projection import PROJECTABLE_NODE_TYPES, _normalize_lifecycle_state
from .elevation_recall import (
    RECALLABLE_LIFECYCLE_STATES,
    _is_quarantined,
    _is_recallable_node,
    _normalize_tags,
    _normalize_text,
    _resolve_state_filter,
)

# ── 第二階段接線判準門檻（工單 §9）────────────────────────────────────
#: 可回憶池（非隔離 × 四層 × dormant|superseded）最少幾顆才值得接線。
GO_MIN_RECALLABLE_POOL = 5
#: 池中至少要有幾顆 superseded（否則「以前覺得 A，後來發現 B」永遠不會出現）。
GO_MIN_SUPERSEDED = 1
#: 真實 probe 命中率下限（實測詞元級 union 對中文內容是 20.34%＝12/59）。
GO_MIN_HIT_RATE = 0.15
#: 命中中允許的隔離節點比例（硬不變式：必須是 0）。
GO_MAX_QUARANTINED_HIT_RATIO = 0.0

#: 命中率判定的單位（一個 probe ＝ 一筆真實 user 訊息）。
HIT_RATE_UNIT = "probe"


def _without_quarantine(node: Dict[str, Any]) -> Dict[str, Any]:
    """去掉隔離旗標的節點淺拷貝。

    用途：**反事實量測**——「若當初沒有隔離守門，這些節點會被命中嗎？」
    做法是把同一個 SSOT 過濾器套在「剝掉旗標」的節點上，而不是另寫一份
    守門（另寫一份會漂移，量出來的數字就不可信了）。
    """
    return {k: v for k, v in node.items() if k != "quarantined"}


def pool_summary(
    nodes: List[dict], quarantined_ids: Optional[Set[str]] = None
) -> dict:
    """回憶池成熟度。

    Args:
        nodes: 節點 dict 列表（**已套用 lifecycle overlay**）。
        quarantined_ids: 被隔離的節點 id 集合（由
            ``lifecycle_overlay.load_quarantined_node_ids`` 給出）；未傳入時
            退回「節點 dict 自帶 ``quarantined`` 旗標」的判定。兩者取**聯集**
            （寧可多擋不可漏擋）。

    Returns:
        dict（鍵名固定）：
          - ``total_nodes``：節點總數（單位：顆）
          - ``by_state``：lifecycle_state 分布（缺省視為 active）
          - ``by_node_type``：node_type 分布
          - ``quarantined``：帶隔離旗標的節點數
          - ``excluded_pattern``：pattern 節點數（**結構性排除**，不回憶）
          - ``blocked_by_quarantine``：若無隔離守門、本可回憶卻被守門擋下的節點數
          - ``recallable``：可回憶池大小（非隔離 × 四層 × dormant|superseded）
          - ``recallable_by_state``：可回憶池的態分布
          - ``recallable_by_node_type``：可回憶池的 node_type 分布
    """
    quarantined = set(quarantined_ids or ())
    by_state: Dict[str, int] = {}
    by_node_type: Dict[str, int] = {}
    recallable_by_state: Dict[str, int] = {"dormant": 0, "superseded": 0}
    recallable_by_node_type: Dict[str, int] = {}

    total = 0
    quarantined_count = 0
    excluded_pattern = 0
    blocked_by_quarantine = 0
    recallable = 0

    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        total += 1
        state = _normalize_lifecycle_state(node)
        by_state[state] = by_state.get(state, 0) + 1
        node_type = node.get("node_type")
        by_node_type[str(node_type)] = by_node_type.get(str(node_type), 0) + 1

        is_quarantined = _is_quarantined(node, quarantined)
        if is_quarantined:
            quarantined_count += 1
        if node_type == "pattern":
            excluded_pattern += 1

        is_projectable = node_type in PROJECTABLE_NODE_TYPES
        state_ok = state in RECALLABLE_LIFECYCLE_STATES
        if not (is_projectable and state_ok):
            continue
        if is_quarantined:
            blocked_by_quarantine += 1
            continue
        recallable += 1
        recallable_by_state[state] = recallable_by_state.get(state, 0) + 1
        key = str(node_type)
        recallable_by_node_type[key] = recallable_by_node_type.get(key, 0) + 1

    return {
        "total_nodes": total,
        "by_state": by_state,
        "by_node_type": by_node_type,
        "quarantined": quarantined_count,
        "excluded_pattern": excluded_pattern,
        "blocked_by_quarantine": blocked_by_quarantine,
        "recallable": recallable,
        "recallable_by_state": recallable_by_state,
        "recallable_by_node_type": recallable_by_node_type,
    }


def hit_rate(
    nodes: List[dict],
    probes: List[Tuple[str, List[str]]],
    state_filter: Optional[Set[str]] = None,
    quarantined_ids: Optional[Set[str]] = None,
) -> dict:
    """真實命中率（詞元級 union 比對）。

    Args:
        nodes: 節點 dict 列表（**已套用 lifecycle overlay**）。
        probes: ``[(agent_id, tags), ...]``；一個 probe ＝ 一筆真實 user 訊息。
        state_filter: 態過濾（缺省＝dormant＋superseded；非法態 fail-closed）。
        quarantined_ids: 被隔離的節點 id 集合（見 :func:`pool_summary`）。

    Returns:
        dict：
          - ``probes_total``：probe 總數（分子／分母的分母，單位：probe）
          - ``probes_hit``：至少命中 1 顆同 agent 可回憶節點的 probe 數（分子）
          - ``hit_rate``：``probes_hit / probes_total``（0.0 when 分母為 0）
          - ``unit``：``"probe"``
          - ``unique_nodes_hit``：命中的 unique 節點數（單位：顆）
          - ``hit_node_ids``：命中的節點 id 清單
          - ``match_pairs``：命中配對數（probe × 節點，單位：組）
          - ``match_pairs_quarantined``：**反事實**——若無隔離守門會命中的配對數
            （守門生效時必須為 0）
          - ``non_quarantined_hit_ratio``：命中配對中非隔離節點的比例
          - ``probes_without_tags``：tags 全部無效（正規化後長度 < 2）的 probe 數
    """
    states = _resolve_state_filter(state_filter)
    if states is None:
        states = set(RECALLABLE_LIFECYCLE_STATES)
    quarantined = set(quarantined_ids or ())

    probes_total = 0
    probes_hit = 0
    probes_without_tags = 0
    match_pairs = 0
    match_pairs_quarantined = 0
    hit_node_ids: List[str] = []
    hit_node_id_set: Set[str] = set()

    for probe in probes or []:
        try:
            agent_id, tags = probe
        except (TypeError, ValueError):
            continue
        probes_total += 1
        normalized_tags = _normalize_tags(tags)
        if not normalized_tags:
            probes_without_tags += 1
            continue

        probe_hit = False
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            node_id = node.get("node_id")
            if _is_quarantined(node, quarantined):
                # 反事實：剝掉隔離旗標後仍通過守門且內容命中
                # ⇒ 若當初沒有隔離守門，這一組配對就會被命中。
                if _is_recallable_node(
                    _without_quarantine(node), agent_id, states,
                    quarantined - {node_id},
                ):
                    q_content = _normalize_text(node.get("content"))
                    if q_content and any(tag in q_content for tag in normalized_tags):
                        match_pairs_quarantined += 1
                continue
            # 守門生效的判定：隔離節點在此即被排除（共用 SSOT 過濾器）。
            if not _is_recallable_node(node, agent_id, states, quarantined):
                continue
            content = _normalize_text(node.get("content"))
            if not content:
                continue
            if not any(tag in content for tag in normalized_tags):
                continue
            probe_hit = True
            match_pairs += 1
            if node_id is not None and node_id not in hit_node_id_set:
                hit_node_id_set.add(node_id)
                hit_node_ids.append(node_id)

        if probe_hit:
            probes_hit += 1

    non_quarantined = match_pairs - match_pairs_quarantined
    ratio = (non_quarantined / match_pairs) if match_pairs else 1.0

    return {
        "probes_total": probes_total,
        "probes_hit": probes_hit,
        "hit_rate": (probes_hit / probes_total) if probes_total else 0.0,
        "unit": HIT_RATE_UNIT,
        "unique_nodes_hit": len(hit_node_ids),
        "hit_node_ids": hit_node_ids,
        "match_pairs": match_pairs,
        "match_pairs_quarantined": match_pairs_quarantined,
        "non_quarantined_hit_ratio": ratio,
        "probes_without_tags": probes_without_tags,
    }


def go_no_go(summary: dict, hit: dict) -> dict:
    """第二階段接線判準（工單 §9 四項全過才 GO）。

    純判定：不讀檔、不呼叫 LLM。

    Args:
        summary: ``pool_summary`` 的輸出。
        hit: ``hit_rate`` 的輸出。

    Returns:
        dict：``{"verdict": "GO"|"NO-GO", "reasons": [...], "checks": {...}}``
        （``reasons`` 逐條寫明未過的原因）。
    """
    summary = summary or {}
    hit = hit or {}

    pool = int(summary.get("recallable") or 0)
    superseded = int((summary.get("recallable_by_state") or {}).get("superseded") or 0)
    rate = float(hit.get("hit_rate") or 0.0)
    quarantined_pairs = int(hit.get("match_pairs_quarantined") or 0)
    ratio = float(hit.get("non_quarantined_hit_ratio") or 0.0)

    checks: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []

    ok_pool = pool >= GO_MIN_RECALLABLE_POOL
    checks["recallable_pool"] = {
        "value": pool,
        "threshold": GO_MIN_RECALLABLE_POOL,
        "unit": "node",
        "pass": ok_pool,
    }
    if not ok_pool:
        reasons.append(
            "可回憶池（非隔離 × 四層 × dormant|superseded）只有 %d 顆，門檻 %d 顆"
            % (pool, GO_MIN_RECALLABLE_POOL)
        )

    ok_superseded = superseded >= GO_MIN_SUPERSEDED
    checks["superseded_in_pool"] = {
        "value": superseded,
        "threshold": GO_MIN_SUPERSEDED,
        "unit": "node",
        "pass": ok_superseded,
    }
    if not ok_superseded:
        reasons.append(
            "池中 superseded 節點 %d 顆，門檻 %d 顆（缺它就不會有「以前覺得 A，後來發現 B」）"
            % (superseded, GO_MIN_SUPERSEDED)
        )

    ok_rate = rate >= GO_MIN_HIT_RATE
    checks["hit_rate"] = {
        "value": rate,
        "threshold": GO_MIN_HIT_RATE,
        "unit": HIT_RATE_UNIT,
        "pass": ok_rate,
    }
    if not ok_rate:
        reasons.append(
            "真實命中率 %.4f（%d/%d probe），門檻 %.2f"
            % (rate, int(hit.get("probes_hit") or 0), int(hit.get("probes_total") or 0),
               GO_MIN_HIT_RATE)
        )

    ok_purity = (
        quarantined_pairs == 0
        and ratio >= (1.0 - GO_MAX_QUARANTINED_HIT_RATIO)
    )
    checks["non_quarantined_only"] = {
        "value": ratio,
        "threshold": 1.0 - GO_MAX_QUARANTINED_HIT_RATIO,
        "quarantined_match_pairs": quarantined_pairs,
        "unit": "ratio",
        "pass": ok_purity,
    }
    if not ok_purity:
        reasons.append(
            "命中中含隔離節點：%d 組配對、非隔離比例 %.4f（硬不變式要求 100%% 非隔離）"
            % (quarantined_pairs, ratio)
        )

    return {
        "verdict": "GO" if not reasons else "NO-GO",
        "reasons": reasons,
        "checks": checks,
    }


__all__ = [
    "GO_MAX_QUARANTINED_HIT_RATIO",
    "GO_MIN_HIT_RATE",
    "GO_MIN_RECALLABLE_POOL",
    "GO_MIN_SUPERSEDED",
    "HIT_RATE_UNIT",
    "go_no_go",
    "hit_rate",
    "pool_summary",
]
