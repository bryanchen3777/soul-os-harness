"""src/inner_life/revision_chain.py — 同源修訂鏈路（G-1 ＋ G-2；**離線沙盒，未掛載**）。

工單：logs/CAMPAIGN-B-BATTLE2-G1-G2-REVISION-CHAIN-WORK-ORDER.md
（戰役二第一片：G-1＋G-2 離線原型與測試守衛 —— 先驅施工）

這一層做什麼
------------
讓 ``lineage_depth`` 第一次在**生產語意**下**合法地**大於 1：

既有 soul node（belief／value／trait）在收到**同維度的新成熟證據**時，
不是被 anti-runaway 吃掉，而是產生一個**同層後繼節點 N'**
（``parent_node_id``＝舊、``lineage_depth``＝舊＋1、``lineage_path``＝舊路徑／新 id），
舊節點的證據邊帶 ``valid_until_ts`` 留痕，新證據邊掛到 N'。

四個公開介面（**簽章不可自創**；工單 §①）
----------------------------------------
- :func:`select_revision_targets` — 候選篩選（**7 條決定性條件**）
- :func:`compute_revise_confidence` — **F5 防護**（逐位元保留舊信心度）
- :func:`build_revise_provenance` — **G-2 承接**（舊命題 ＋ 舊 source 血統）
- :func:`apply_revision` — 引擎接線薄層（**fail-closed**、不重試）

硬性禁令（**結構性保證**，不靠註解；守衛 I7 以 AST 把關）
---------------------------------------------------------
- **不得** import：``src.llm``（含 ``src.llm.proxy``）、``httpx``、``requests``、
  ``socket``、``subprocess``、``urllib``、``asyncio``、**``soul_elevation``**
  （含 ``soul_elevation.engine``）。
- 本模組**不做任何網路呼叫、不呼叫 LLM、不讀寫任何檔案**（純函式層 ＋ 注入物件）。
- 🔺 ``engine.revise()`` 是**對注入物件的 duck-typed 方法呼叫**（``engine: Any``）
  ⇒ **本來就不需要 import 引擎**；連引擎型別都不引入。

🔺 唯一的既有依賴是**維度函式**（工單 §③ 明訂）
------------------------------------------------
工單 §③ 指定「同族維度＝``_node_candidate_dimension(node)``（**既有函式**，
``elevation_adapter.py:869``）⇒ **不得**自創維度」⇒ 本模組 import 該既有函式。

🔺 **揭露（間接依賴）**：本模組**直接**匯入僅 ``src.inner_life.elevation_adapter``，
**間接**依賴 ``soul_elevation``（該模組於模組層匯入 ``StubElevationLLM`` 等）。
這是**為複用既有** ``_node_candidate_dimension()`` 而**不重造輪子**的必然結果
（工單 §③ 明訂沿用既有函式）⇒ **不得**為了「零間接依賴」而複製該函式的實作。
守衛 I7 守的是**直接匯入**層級（AST 檢查本模組自身的 import 敘述）⇒ 本模組**合規**；
``soul_elevation`` 是純 Python、import 期 0 成本 0 檔案 I/O ⇒ **0 LLM 成本不受影響**。
本模組**不 import 引擎型別**、**不呼叫任何引擎寫路徑**（``revise`` 也不在既有
``ENGINE_WRITE_CALLS`` 名單內）。

🔺 fail-closed 邊界：``engine.get_node()`` 找不到舊節點時拋的是 ``KeyError``
------------------------------------------------
``soul_elevation/engine.py:292-297``：``get_node()`` 找不到時拋 ``KeyError``
（**不是** ``ValueError``）。本模組**自覺地**選擇 **(乙)：維持只 catch ``ValueError``**，
讓 ``KeyError`` **冒泡**：

- **為什麼**：正確接線下舊節點**必然在引擎註冊表內**
  （``elevation_adapter._rebuild_engine_for`` L932-935 把該 agent 的**全部**節點
  載進 ``engine._nodes``，含 soul node）⇒ ``KeyError`` 屬**理論上不可達**。
  若真的發生，代表**呼叫端把引擎與 nodes 配對錯誤**（接線錯誤）
  ⇒ **冒泡比靜默回 ``False`` 更能暴露錯誤**（回 ``False`` 會把它偽裝成「門檻不足」）。
- 🔺 **不得**為了省事而擴大 catch 範圍：程式錯、網路錯等**其他例外必須冒泡**。
  （選項甲「明確 catch ``KeyError``」會把接線錯誤與門檻不足混為一談 ⇒ 未採用。）

本片**不掛載**生產路徑
----------------------
掛載屬**下一片**（須另行授權）：本片只把**掛載契約**以守衛 I8 釘死
（``elevation_adapter.elevate_matured_patterns()`` 函式體內確有 ``nodes``／``edges``／
``engine`` 三個可用名稱 ⇒ 下一片掛載不需重構）。

紀律
----
- **純函式、決定性**：同一輸入 ⇒ 逐位元相同的篩選結果（輸出依 ``node_id`` 排序）。
- **零 LLM 成本**：本模組**不碰** LLM；``StubElevationLLM.classify()`` 恆回傳
  ``confidence = 0.5`` ⇒ 若不顯式傳 ``new_confidence``，會把樁的假值寫進真實 belief
  ⇒ **F5 防護**逐位元保留舊信心度（**修訂不等於信心改變**）。
- **不重試、不迴圈**：:func:`apply_revision` 對引擎的 ``ValueError``（門檻不足）
  fail-closed 回 ``False``；其他例外**不得**被吞掉。
- **不碰 G-3**：``record_contradiction()``／``supersede()``／``reinforce()``
  屬 G-3（另片），本模組**一律不呼叫**。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.inner_life.elevation_adapter import _node_candidate_dimension


#: 可作為**修訂目標**的 ``node_type``（工單 §②.2）。
#:
#: 🔺 **排除 ``essence``**：引擎的保守邊界（valence 反轉 ＋ ≥2 新獨立證據 ＋
#: confidence delta 超 0.3，否則只 reinforce、**不改 lineage**）⇒ 排除後**永不**
#: 對 essence 發起 ``revise()``。
#: 🔺 **排除 ``pattern``**：``revise()`` 對 pattern 會產出「候選 pattern」，
#: **不是靈魂血統** ⇒ depth 不會 +1。
REVISION_TARGET_NODE_TYPES: frozenset = frozenset({"belief", "value", "trait"})


@dataclass(frozen=True)
class RevisionTarget:
    """一次修訂的目標（**純資料**；欄位固定，工單 §①）。

    node_id        : str                 目標舊節點 id（**修訂的是它**）
    new_content    : str                 新命題正文（＝新成熟 pattern 的 content）
    new_confidence : float               新節點信心度（**F5：逐位元＝舊信心度**）
    old_source_ids : Tuple[str, ...]     舊節點既有證據 source_ids（去重、保序）
    new_source_ids : Tuple[str, ...]     本輪新證據 source_ids（去重、保序）
    valence        : Optional[str]       舊節點 valence（**原封不動**傳遞）
    """

    node_id: str
    new_content: str
    new_confidence: float
    old_source_ids: Tuple[str, ...]
    new_source_ids: Tuple[str, ...]
    valence: Optional[str]


# ── 小工具（純函式、決定性）───────────────────────────────────────────────
def _normalize_content(content: Any) -> str:
    """命題正文的正規化：**只去首尾空白**。

    🔴 **不引入相似度門檻、不引入權重**（Owner 凍結條款）⇒ 相同內容不得觸發改寫。
    """
    return str(content or "").strip()


def _dedup_source_ids(source_ids: Sequence[Any]) -> Tuple[str, ...]:
    """source_ids 去重、**保持出現順序**（決定性）。"""
    seen: set = set()
    ordered: List[str] = []
    for raw in source_ids:
        if raw is None:
            continue
        value = str(raw)
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _evidence_keys(edges: Sequence[Mapping[str, Any]]) -> set:
    """一組證據邊的 ``(source_id, inner_life_event_id)`` 鍵集合。"""
    return {
        (edge.get("source_id"), edge.get("inner_life_event_id"))
        for edge in edges
        if isinstance(edge, Mapping)
    }


def _node_mapping(node: Any) -> Dict[str, Any]:
    """把節點（mapping 或引擎的屬性式節點物件）轉成三欄 mapping。

    🔺 引擎的 ``ElevationNode`` 是**屬性式**（``.node_id``／``.content``／``.confidence``）
    ⇒ ``build_revise_provenance()`` 需要的 mapping 由本函式供應（**不 import 引擎型別**）。
    """
    if isinstance(node, Mapping):
        return {
            "node_id": node.get("node_id"),
            "content": node.get("content"),
            "confidence": node.get("confidence"),
        }
    return {
        "node_id": getattr(node, "node_id", None),
        "content": getattr(node, "content", None),
        "confidence": getattr(node, "confidence", None),
    }


# ── ① 候選篩選 ───────────────────────────────────────────────────────────
def select_revision_targets(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    agent_id: str,
) -> List[RevisionTarget]:
    """選出「可合法發起同源修訂」的目標節點（**7 條決定性條件**，工單 §②）。

    條件（**任一條不成立即不選**）：

    1. 只看 ``agent_id`` 相符的節點；
    2. **修訂目標**＝``node_type ∈ {belief, value, trait}`` 的 soul node
       （**排除 essence／pattern**）；
    3. **同族維度**＝``_node_candidate_dimension(node)``（既有函式；不自創維度）；
    4. **新成熟證據**＝某顆 ``node_type == "pattern"`` 且其候選維度命中目標維度，
       且其證據邊的 ``(source_id, inner_life_event_id)`` **不在**目標節點的
       既有證據集合內；
    5. **內容確有更新**：新 pattern 的 ``content`` 與目標節點的 ``content``
       **正規化後不等**（正規化＝去首尾空白）；
    6. ``new_source_ids``＝該新 pattern 的證據 source_ids（去重、保序）；
       ``old_source_ids``＝目標節點的既有證據 source_ids（去重、保序）；
    7. 輸出**依 ``node_id`` 排序**（決定性；不得依 dict 迭代序）。

    🔺 **每個目標節點至多一條修訂鏈**：同一 soul node 若有多顆合格的新 pattern，
    取 ``node_id`` 排序後的**第一顆**（決定性）⇒ 單次 ``apply_revision()`` 不會
    對同一父節點生出兩條鏈。
    """
    agent_nodes = [
        node
        for node in nodes
        if isinstance(node, Mapping) and node.get("agent_id") == agent_id
    ]

    edges_by_node: Dict[str, List[Mapping[str, Any]]] = {}
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        node_id = edge.get("node_id")
        if node_id is None:
            continue
        edges_by_node.setdefault(str(node_id), []).append(edge)

    patterns = sorted(
        (node for node in agent_nodes if node.get("node_type") == "pattern"),
        key=lambda node: str(node.get("node_id")),
    )

    targets: List[RevisionTarget] = []
    for node in sorted(agent_nodes, key=lambda item: str(item.get("node_id"))):
        if node.get("node_type") not in REVISION_TARGET_NODE_TYPES:
            continue
        dimension = _node_candidate_dimension(node)
        old_edges = edges_by_node.get(str(node.get("node_id")), [])
        old_source_ids = _dedup_source_ids(
            edge.get("source_id") for edge in old_edges
        )
        old_keys = _evidence_keys(old_edges)
        old_content = _normalize_content(node.get("content"))

        for pattern in patterns:
            if _node_candidate_dimension(pattern) != dimension:
                continue
            pattern_edges = edges_by_node.get(str(pattern.get("node_id")), [])
            new_source_ids = _dedup_source_ids(
                edge.get("source_id") for edge in pattern_edges
            )
            if not (_evidence_keys(pattern_edges) - old_keys):
                continue
            new_content = str(pattern.get("content") or "")
            if _normalize_content(new_content) == old_content:
                continue
            targets.append(
                RevisionTarget(
                    node_id=str(node.get("node_id")),
                    new_content=new_content,
                    new_confidence=compute_revise_confidence(node),
                    old_source_ids=old_source_ids,
                    new_source_ids=new_source_ids,
                    valence=node.get("valence"),
                )
            )
            break

    return sorted(targets, key=lambda target: target.node_id)


# ── ② F5 防護 ─────────────────────────────────────────────────────────────
def compute_revise_confidence(old_node: Mapping[str, Any]) -> float:
    """修訂後節點的信心度：**逐位元保留舊信心度**（F5 防護，工單 §③）。

    🔴 **不得**回退到樁的 ``0.5``：``StubElevationLLM.classify()`` 恆回傳
    ``confidence = 0.5`` ⇒ 若不顯式傳 ``new_confidence``，會把**樁的假值寫進真實 belief**。
    🔴 本片**不引入任何新的信心度公式／加權**（Owner 邊界）⇒ **修訂不等於信心改變**。
    """
    return float(old_node["confidence"])


# ── ③ G-2 承接 ───────────────────────────────────────────────────────────
def build_revise_provenance(
    old_node: Mapping[str, Any],
    *,
    old_source_ids: Sequence[str],
    new_source_ids: Sequence[str],
) -> Dict[str, Any]:
    """建構 ``engine.revise()`` 的 ``provenance``（G-2「舊 Source 承接」，工單 §④）。

    鍵名**沿用引擎既有的 ``old_*`` 命名慣例**（``soul_elevation/engine.py:709-711``
    已用 ``old_node_id``／``old_content``／``old_confidence``）：

    ==================  ==================================================
    ``old_node_id``     目標節點 ``node_id``
    ``old_content``     目標節點 ``content``
    ``old_confidence``  目標節點 ``confidence``
    ``old_source_ids``  目標節點的**全部**既有證據 source_ids（tuple）
    ``new_source_ids``  本輪新證據的 source_ids（tuple）
    ==================  ==================================================

    🔺 這是「重估」的上下文：``engine.revise()`` 會把 ``provenance`` 併入 ``merged``
    後交給 ``self._llm.classify(new_content, merged, prior_for_llm)``
    ⇒ **舊命題與其證據血統**就在那裡。樁只讀 ``prior_node_type`` ⇒ 該上下文**機械上
    不生效**；但它必須**先備且被守衛釘死**（G-3 的語意判定會直接讀它）。
    """
    return {
        "old_node_id": old_node.get("node_id"),
        "old_content": old_node.get("content"),
        "old_confidence": old_node.get("confidence"),
        "old_source_ids": tuple(old_source_ids),
        "new_source_ids": tuple(new_source_ids),
    }


# ── ④ 引擎接線薄層 ───────────────────────────────────────────────────────
def apply_revision(
    engine: Any,
    target: RevisionTarget,
    *,
    source_type: str = "inner_life_event",
) -> bool:
    """把一個修訂目標接到注入的引擎上（**fail-closed**；工單 §⑤）。

    - **只 catch ``ValueError``**（引擎的門檻不足會拋 ``ValueError``）
      ⇒ 其他例外**不得**被吞掉。
    - **不重試、不迴圈、不呼叫 LLM**。
    - **不得**自行呼叫 ``engine.record_contradiction()``／``engine.supersede()``／
      ``engine.reinforce()``（**G-3 專屬**）。

    🔺 **舊節點從注入的引擎取回**（``engine.get_node()``，引擎既有唯讀 API）：
    ``RevisionTarget`` 的欄位固定（工單 §①）**不含 ``old_content``**，而 G-2 的
    provenance **必含**它 ⇒ 這是唯一能產出完整承接資訊的途徑；
    ``engine.get_node()`` 回傳的正是引擎 registry 裡的同一顆舊節點
    ⇒ **血緣不會因選擇器快照過期而失真**。

    🔺 **自覺的邊界（選項乙）**：``get_node()`` 找不到舊節點時拋 ``KeyError``
    （**不是** ``ValueError``）⇒ 它**不被**本函式 catch，會**冒泡**。
    理由：正確接線下舊節點必然在引擎註冊表內
    （``elevation_adapter._rebuild_engine_for`` L932-935 載入該 agent 的全部節點）
    ⇒ ``KeyError`` 屬**理論上不可達**；真發生即代表**接線錯誤**（引擎與 nodes 配對錯誤）
    ⇒ 冒泡比靜默回 ``False`` 更能暴露它（``False`` 會把它偽裝成「門檻不足」）。
    🔺 **不得**擴大 catch 範圍：其他例外（程式錯、網路錯）必須冒泡。
    """
    try:
        old_node = _node_mapping(engine.get_node(target.node_id))
        engine.revise(
            target.node_id,
            target.new_content,
            target.new_confidence,
            valence=target.valence,
            provenance=build_revise_provenance(
                old_node,
                old_source_ids=target.old_source_ids,
                new_source_ids=target.new_source_ids,
            ),
            source_ids=list(target.new_source_ids),
            source_type=source_type,
        )
        return True
    except ValueError:
        return False
