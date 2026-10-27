"""src/inner_life/contradiction_prefilter.py — 矛盾候選的「結構預濾器」（純函式、唯讀、零 LLM）。

工單：FEAT-SUPERSEDED-CONTRADICTION-PIPELINE（2026-10-26；「召回池 superseded 恆 0」的最後一哩）

為什麼需要這個模組
------------------
外部套件 ``soul_elevation.engine`` 的 ``supersede()`` 以「累積器已有獨立矛盾證據達門檻」
為觸發前提，而累積器的唯一寫入點 ``record_contradiction()`` 過去只在 ``supersede()``
**內部**被呼叫 ⇒ 累積器恆空 ⇒ ``supersede()`` 恆拋 ``ValueError`` ⇒ 生產圖譜的
``superseded`` 恆為 0（死鎖）。

本模組補上死鎖的**前半段接線**：把「靈魂節點 × 成熟 pattern」的配對，用**七道結構條件**
收斂成「**每 agent 至多 1 組**」的候選，交給語意判定器裁決；後半段
（判定 ⇒ 寫累積器 ⇒ 達門檻 ⇒ ``supersede()``）由
``src/inner_life/contradiction_judge_prototype.py`` 的 ``ContradictionPipeline`` 負責。

七道條件（**依序套用，任一不成立即剔除該配對**；缺一不可）
----------------------------------------------------------
1. **同 agent**：``soul_node["agent_id"] == pattern["agent_id"]``；
2. **靈魂節點型別**：``soul_node["node_type"] ∈ {belief, value, trait}``
   （**排除** ``essence`` 與 ``pattern``）；
3. **同候選維度**：**重用** ``elevation_adapter._node_candidate_dimension()``
   （**不另寫一份**維度判定，否則兩套維度語意會各自漂移）；
4. **內容正規化異質**：兩者 content 經「去空白 ＋ 去標點」正規化後**必須不相等**
   （同一命題的兩種標點寫法不是矛盾）；
5. **嚴格跨日**：兩者時間戳的 **UTC 日期字串必須不同**
   （同日產生的不同生活切片是同一段經歷的兩種切片，不是矛盾）；
6. **未見新證據**：pattern 的 ``source_ids`` **不得**已存在於該靈魂節點的既有證據集合
   （已消化過的證據不得再被當成「新的反例」）；
7. **決定性排序 ＋ 每日 1 對截斷**：候選對依
   ``(soul_node["node_id"], pattern["node_id"])`` 字典序升序，**每個 agent 至多 1 組**
   （字典序最小者優先）。

本模組刻意不做的事
------------------
- **不做語意判斷**：內容只做「正規化後是否相同」的字面比對，**不引入相似度／權重／
  門檻**；真正的「互斥與否」由語意判定器（LLM）負責 ⇒ 預濾便宜、判定準。
- **不讀檔、不連網、不寫任何狀態**：純函式；同一輸入兩次呼叫輸出逐位元相同。
- **不引用 supersede 門檻**：門檻由 ``soul_elevation.prior`` 持有，本模組不碰。
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 參與矛盾判定的靈魂節點型別（``essence``／``pattern`` **不參與**）。
#:
#: 🔺 排除 ``essence``：引擎對 essence 的 supersede 另有嚴格條件（valence 反轉 ＋
#: confidence delta 超門檻），本預濾器**不重做**那套判準 ⇒ 寧可不選，不可誤選。
#: 🔺 排除 ``pattern``：pattern 是**候選**，不是靈魂結構；矛盾要對照的是靈魂結構。
PREFILTER_SOUL_NODE_TYPES: Tuple[str, ...] = ("belief", "value", "trait")

#: 每個 agent 至多幾組候選（＝語意判定器每天最多被問幾次）。
MAX_PAIRS_PER_AGENT = 1

#: 正規化時要去除的標點集合（ASCII 標點 ＋ 常用中文標點）。
#:
#: 🔺 **只用於正規化**，不承載任何語意判斷：這張表是「什麼算標點」的**列舉**，
#: 不是「多像才算像」的度量。
_PUNCTUATION = frozenset(string.punctuation) | frozenset(
    "，。！？；：、「」『』（）《》〈〉【】〔〕…—～·・“”‘’"
)

# 🔺 維度函式沿用既有實作（``elevation_adapter._node_candidate_dimension``）——
# 工單明訂「不得另寫一份維度判定」。模組層匯入 **安全**：本模組不被
# ``elevation_adapter`` 模組層匯入（adapter 端是**遲延匯入**，見 adapter 的掛載點），
# 故無循環；這與 ``revision_chain`` 既有做法同款。


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


def normalize_content(content: Any) -> str:
    """命題正文的正規化（**決定性、字面、零語意**）。

    規則（**最小且可複現**）：

    1. 轉成字串；
    2. 去掉**所有**空白字元（含全形空格）；
    3. 去掉**所有**標點字元（ASCII 標點 ＋ 常用中文標點）。

    ⇒ 只有「標點／空白不同」的兩段文字會被判為**同一命題**（條件 4 因此成立）。
    ⚠️ **不**做相似度／權重／門檻比對（那是語意判定器的工作）。
    """
    text = _text(content)
    return "".join(
        ch for ch in text if not ch.isspace() and ch not in _PUNCTUATION
    )


def _utc_date(value: Any) -> Optional[str]:
    """時間戳 → **UTC 日期字串**（``YYYY-MM-DD``）；壞值回 ``None``（視為不成立）。

    🔺 **UTC 口徑**：帶時區的時間戳會先換算到 UTC 再取日期 ⇒ 跨時區的「同一天」
    不會被誤判成跨日。**壞值一律當「不成立」**（不猜、不放寬）。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat()


def _source_ids(node: Any) -> Tuple[str, ...]:
    """節點的 source_ids（去重、保序）。

    支援兩種形狀：``source_ids``（序列）與 ``source_id``（單值）⇒ 呼叫端給哪種
    都能用；兩者皆缺 ⇒ 空 tuple。
    """
    raw = _get(node, "source_ids")
    if raw is None:
        single = _get(node, "source_id")
        raw = () if single is None else (single,)
    if isinstance(raw, str):
        raw = (raw,)
    try:
        items = list(raw)
    except TypeError:
        return ()
    seen: set = set()
    ordered: List[str] = []
    for item in items:
        if item is None:
            continue
        value = str(item)
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _candidate_dimension(node: Any) -> str:
    """候選維度（**沿用既有函式**，不自創維度語意）。

    🔺 遲延匯入 ``elevation_adapter``：模組層若直接匯入，會與 adapter 的遲延匯入
    互相等待（循環）⇒ 沿用 repo 既有做法（``revision_chain`` 亦為此型態）。
    """
    from src.inner_life.elevation_adapter import _node_candidate_dimension

    return _node_candidate_dimension(node)


def select_conflict_candidate_pairs(
    nodes: Sequence[Any],
    matured_patterns: Sequence[Any],
) -> Dict[str, List[Tuple[dict, dict]]]:
    """挑出「每 agent 至多 1 組」的矛盾候選配對（**純函式**、**決定性**）。

    Args:
        nodes: 靈魂節點序列（dict 或具 ``node_id``／``agent_id``／``node_type``／
            ``content``／``created_ts``／``confidence``／``valence``／``source_ids``
            等欄位的物件皆可）。
        matured_patterns: 成熟 pattern 節點序列（同形；``node_type == "pattern"``）。

    Returns:
        ``{agent_id: [(soul_node, candidate_pattern), ...]}``；**每個 agent 至多 1 組**，
        且取字典序最小的那一組。

    Notes:
        - **純函式**：不讀檔、不連網、不寫任何狀態；同一輸入兩次呼叫輸出逐位元相同。
        - 回傳的是**呼叫端傳入的原物件**（identity 保留）⇒ 呼叫端可直接把同一份
          節點交給判定器與引擎，不必再回查。
        - 輸出字典的 key 依 ``agent_id`` 排序 ⇒ 輸出本身也是決定性的。
    """
    valid: List[Tuple[str, str, str, Any, Any]] = []

    for soul_node in nodes:
        # ── 條件 1：同 agent ──
        agent_id = _text(_get(soul_node, "agent_id"))
        if not agent_id:
            continue
        # ── 條件 2：靈魂節點型別（排除 essence／pattern）──
        if _text(_get(soul_node, "node_type")) not in PREFILTER_SOUL_NODE_TYPES:
            continue
        # ── 條件 5：靈魂節點的 UTC 日期（壞值視為不成立）──
        soul_day = _utc_date(_get(soul_node, "created_ts"))
        if soul_day is None:
            continue
        # ── 條件 3：候選維度（沿用既有函式）──
        soul_dimension = _candidate_dimension(soul_node)
        # ── 條件 4：內容正規化後必須不等 ──
        soul_content = normalize_content(_get(soul_node, "content"))
        # ── 條件 6：靈魂節點的既有證據集合 ──
        soul_sources = set(_source_ids(soul_node))

        for pattern in matured_patterns:
            if _text(_get(pattern, "agent_id")) != agent_id:
                continue
            if _candidate_dimension(pattern) != soul_dimension:
                continue
            if normalize_content(_get(pattern, "content")) == soul_content:
                continue
            pattern_day = _utc_date(_get(pattern, "created_ts"))
            if pattern_day is None or pattern_day == soul_day:
                continue
            if set(_source_ids(pattern)) & soul_sources:
                continue
            valid.append(
                (
                    _text(_get(soul_node, "node_id")),
                    _text(_get(pattern, "node_id")),
                    agent_id,
                    soul_node,
                    pattern,
                )
            )

    # ── 條件 7：決定性排序 ＋ 每 agent 至多 1 組（字典序最小者優先）──
    valid.sort(key=lambda item: (item[0], item[1]))
    picked: Dict[str, Tuple[Any, Any]] = {}
    for _soul_id, _pattern_id, agent_id, soul_node, pattern in valid:
        if agent_id in picked:
            continue
        picked[agent_id] = (soul_node, pattern)

    return {
        agent_id: [picked[agent_id]] for agent_id in sorted(picked)
    }


__all__ = [
    "MAX_PAIRS_PER_AGENT",
    "PREFILTER_SOUL_NODE_TYPES",
    "normalize_content",
    "select_conflict_candidate_pairs",
]
