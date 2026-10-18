"""
tests/inner_life/test_soul_bug_009_anchor_policy.py — SOUL-BUG-009 錨點政策守衛

缺陷（2026-10-17 唯讀審查實測成立）
------------------------------------
`elevate_matured_patterns()` 恆以 ``engine.elevate(pids[0], ...)`` 取候選維度組內
**最舊**的 pattern 當錨點。而：

  * ``soul_elevation/engine.py`` 用錨點的 content 當靈魂節點內容
    （``resolved_content = pattern.content``）；
  * ``_persist_result`` 以 ``(agent_id, node_type, sha256(content))`` 去重落盤。

⇒ 每次升格產生的 content 與既有靈魂節點逐位元相同 → 被去重丟棄 →
**靈魂層自 2026-10-11T23:18:19~20 起永久凍結**（實測：10 顆 value 靈魂節點全在
那一刻建立，其後每日新增 29 顆 pattern，靈魂節點數 0 變化）。

Owner 2026-10-18 裁決：錨點只當**代表**，不當**守門**
---------------------------------------------------
職責分離是唯一不引發連帶崩潰的架構：

* **守門**歸組級證據機制：``engine.elevate(min_evidence)`` 的維度計票、
  本模組既有的「已消化證據隔離」（VALUE-DEDUP-A-1 / INV-2）、
  EH-2 R1 垂直防火牆與 ``agent_id="default"`` 整組排除。
* **錨點**只挑「最能代表這次昇華、且 content 還沒說過」的內容載體。

因此本檔守的不變量是：

* 錨點**不得**是「最舊的那顆」（去重凍結的根因）
* 錨點的 content **不得**已被既有靈魂節點佔用；全被佔用時必須**冪等靜默返回**
* 組級證據未達標時**不得**昇華（防濫升格現在由這裡負責）
* 氣象／world pattern **不得**昇華（`default` 組排除）
* 同一段內容**永不**落盤成兩顆靈魂節點

⚠️ 本檔曾有三條測試釘著「孤立敘事不得升格」——那是主大腦把錨點誤升格為守門
人時的錯誤前提，Owner 已明確推翻。教訓見主大腦記憶體條目「否定式測試綠了
不代表守住了 X，它可能只是因為另一個原因而綠」。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.inner_life import (
    InnerLifeWriter,
    Provenance,
    TRIGGER_TYPE_DIARY_NIGHT,
)
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    _occupied_soul_contents,
    _select_anchor_representative,
    elevate_matured_patterns,
    run_elevation,
)
from src.inner_life.emergent_projection import DEFAULT_AGENT_ID, load_elevation_nodes

_SOUL_TYPES = {"belief", "value", "trait", "essence"}

# 三句刻意互不相似的敘事。**注意**：在「錨點只當代表」的裁決下，這三句本身
# 是可以昇格的（組級 3 份獨立證據已達標）—— 防範它們的是組級證據閘門，不是
# 錨點相似度。`test_group_below_min_evidence_never_promotes` 守的就是那一道。
_ISOLATED = [
    "今天把書房的書架重新排了一遍，灰塵掉在袖口上。",
    "便利店的關東煮今天換了新的湯頭，買了兩串回家。",
    "騎腳踏車經過河堤，看到有人放風箏，拉得很高。",
]

# 一對刻意高度相似的敘事
_REPEATED_A = "今天泡了茶，一個人坐在窗邊，忽然很想念 Bry。"
_REPEATED_B = "今天泡了茶，一個人坐在窗邊，又忽然很想念 Bry。"

# 第二組重複敘事（與上面刻意不相似）
_REPEATED_C = "路過河堤的時候起了風，時間突然過得很快。"
_REPEATED_D = "路過河堤的時候又起了風，時間突然過得很快。"


class _StubNode:
    """最小 stub：`_select_anchor_representative` 只吃 `.content`／`.node_id`／
    `.created_ts`。`load_elevation_nodes()` 回傳 dict，不是 `ElevationNode`。"""

    __slots__ = ("node_id", "content", "created_ts")

    def __init__(self, node_id, content, created_ts="2026-10-17T00:00:00+00:00"):
        self.node_id = node_id
        self.content = content
        self.created_ts = created_ts


def _event(writer: InnerLifeWriter, narrative: str, *, actor_id: str = "agent_rem"):
    return writer.create_event(
        provenance=Provenance(
            trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
            actor_id=actor_id,
            source_system="narrative",
            extras={"narrative": narrative},
        ),
    )


def _ingest(store_dir: Path, narratives, *, actor_id: str = "agent_rem"):
    writer = InnerLifeWriter()
    for narrative in narratives:
        run_elevation(
            _event(writer, narrative, actor_id=actor_id), [], store_dir=store_dir
        )


def _soul_contents(store_dir: Path):
    return [
        n["content"]
        for n in load_elevation_nodes(store_dir)
        if n.get("node_type") in _SOUL_TYPES
    ]


# ── 合成圖譜建構（繞過 run_elevation，直接寫節點/邊檔）──────────────
# 生產上 `world:weather_temp_change` 會繞過 consume 側的垂直防火牆，
# 以 `agent_id="default"` 落到圖譜（實測 7 條邊）。要測「default 組被排除」
# 這件事，就必須能直接構造那種圖譜形狀。


def _pattern(agent: str, content: str, ts=1.0) -> dict:
    return {
        "node_id": uuid.uuid4().hex,
        "node_type": "pattern",
        "content": content,
        "agent_id": agent,
        "candidate_node_type": "value",
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": ts,
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
    }


def _edge(agent: str, node_id: str, src: str) -> dict:
    return {
        "edge_id": uuid.uuid4().hex,
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": src,
        "agent_id": agent,
        "weight": 1.0,
        "inner_life_event_id": src,
        "trigger_type": "world:weather_temp_change",
        "valid_from_ts": "2026-10-17T00:00:00+00:00",
        "valid_until_ts": None,
    }


def _build_graph(store_dir: Path, patterns) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    nodes = [_pattern(agent, content) for agent, content in patterns]
    edges = [
        _edge(n["agent_id"], n["node_id"], f"src-{i}-{n['node_id'][:6]}")
        for i, n in enumerate(nodes)
    ]
    (store_dir / NODES_FILENAME).write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )
    (store_dir / EDGES_FILENAME).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in edges) + "\n",
        encoding="utf-8",
    )


# ── ① 防濫升格：閘門在組級，不在錨點 ───────────────────────────────


def test_group_below_min_evidence_never_promotes(tmp_path):
    """🔴 防範濫升格的正確落點（Owner 2026-10-18 裁定）：**組級證據未達標
    就不昇華**，不該靠錨點的相似度/degree 去二次攔截。

    單一事件 = 1 份獨立證據 < ``min_evidence=2`` ⇒ 零產出。
    """
    store_dir = tmp_path / "elevation"
    _ingest(store_dir, [_ISOLATED[0]])

    assert elevate_matured_patterns(store_dir=store_dir) == []
    assert _soul_contents(store_dir) == []


def test_three_independent_keys_are_enough_regardless_of_similarity(tmp_path):
    """組級證據達標就放行 —— 錨點**不得**因「敘事互異」而否決。

    這條是對錨點職責的正面釘法：若有人日後又把相似度門檻加回來擋昇華，
    這裡會紅。
    """
    store_dir = tmp_path / "elevation"
    _ingest(store_dir, _ISOLATED)

    assert len(elevate_matured_patterns(store_dir=store_dir)) == 1
    assert len(_soul_contents(store_dir)) == 1


def test_weather_pattern_never_promoted_even_when_near_identical(tmp_path):
    """🔴 氣象 pattern 不得升格。

    **反空轉設計**：生產氣象敘事是模板句，彼此極度相似，單靠「不成群集」
    是擋不住的；擋住它的是 ``agent_id="default"`` 整組排除。
    對照組用同樣內容掛在真 agent 上，證明該內容確實滿足組級門檻。
    """
    weather = "今天的天氣有變化，氣溫 28 度，空氣炎熱。"
    weather2 = "今天的天氣有變化，氣溫 28 度，空氣非常炎熱。"

    _build_graph(
        tmp_path / "world",
        [(DEFAULT_AGENT_ID, weather), (DEFAULT_AGENT_ID, weather2)],
    )
    assert elevate_matured_patterns(store_dir=tmp_path / "world") == []
    assert _soul_contents(tmp_path / "world") == [], (
        "氣象 pattern 被升格成靈魂節點（default 組排除失效）"
    )

    control = tmp_path / "control"
    _build_graph(control, [("agent_rem", weather), ("agent_rem", weather2)])
    assert len(elevate_matured_patterns(store_dir=control)) == 1, (
        "對照組失效：同樣內容掛在真 agent 上卻沒升格，"
        "代表 default 那一側的 0 產出可能只是門檻沒到（假綠）"
    )


# ── ② 反去重凍結：錨點必須挑「還沒說過的話」──────────────────────


def test_anchor_is_not_the_oldest_pattern(tmp_path):
    """🔴 反凍結守衛：最舊那顆是已佔用內容、後面兩顆還沒說過 ⇒ 錨點必須是
    未被佔用者，**不得**是 ``pids[0]``。
    """
    store_dir = tmp_path / "elevation"
    _ingest(store_dir, [_REPEATED_A, _REPEATED_B])

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert len(elevated) == 1

    # 再餵一批**新內容**，且刻意排在舊內容之前被 ingest，讓「最舊」與「最新」
    # 的判定真的會分岔。
    store_dir2 = tmp_path / "elevation2"
    _ingest(store_dir2, [_REPEATED_A, _REPEATED_B])
    first = elevate_matured_patterns(store_dir=store_dir2)
    assert len(first) == 1
    _ingest(store_dir2, [_REPEATED_C, _REPEATED_D])
    second = elevate_matured_patterns(store_dir=store_dir2)
    assert len(second) == 1
    assert second[0].content in (_REPEATED_C, _REPEATED_D), (
        f"第二顆靈魂節點的內容必須來自新一輪累積，實際={second[0].content!r}"
    )
    assert second[0].parent_node_id is not None


def test_anchor_returns_none_when_all_contents_occupied():
    """全被佔用 ⇒ 回 None（冪等靜默返回），而不是硬挑一顆會被去重吞掉的。"""
    nodes = [
        _StubNode("a", "已經說過的話一"),
        _StubNode("b", "已經說過的話二"),
    ]
    occupied = {"已經說過的話一", "已經說過的話二"}
    assert _select_anchor_representative(nodes, occupied) is None


def test_anchor_prefers_unoccupied_over_occupied():
    """只要還有任何一顆未被佔用，就絕不能回傳被佔用的那顆。"""
    nodes = [
        _StubNode("a", "已佔用", created_ts="2026-10-17T09:00:00+00:00"),
        _StubNode("b", "還沒說過", created_ts="2026-10-17T01:00:00+00:00"),
    ]
    picked = _select_anchor_representative(nodes, {"已佔用"})
    assert picked is not None and picked.content == "還沒說過", (
        "被佔用的 content 不該被選為代表 —— 那會讓靈魂層永遠停在舊內容上"
    )


def test_anchor_picks_freshest_among_unoccupied():
    """多顆都未被佔用時取最新鮮者（昇華是「此刻的沉澱」）。"""
    nodes = [
        _StubNode("a", "舊", created_ts="2026-10-17T01:00:00+00:00"),
        _StubNode("b", "新", created_ts="2026-10-17T09:00:00+00:00"),
    ]
    picked = _select_anchor_representative(nodes, set())
    assert picked is not None and picked.content == "新"


def test_anchor_selection_is_deterministic():
    """同輸入必得同輸出（昇華是每輪都走的熱路徑，非確定性會讓去重與回歸失去意義）。"""
    nodes = [
        _StubNode("a", "甲", created_ts="2026-10-17T03:00:00+00:00"),
        _StubNode("b", "乙", created_ts="2026-10-17T03:00:00+00:00"),
        _StubNode("c", "丙", created_ts="2026-10-17T03:00:00+00:00"),
    ]
    picks = {_select_anchor_representative(nodes, set()).node_id for _ in range(5)}
    assert len(picks) == 1


# ── ③ 限流歸屬：adapter 端**不設**門檻 ───────────────────────────
#
# 這裡曾有一對「新鮮鍵閘門」測試（blocks_below_k / opens_at_k）。已隨實作一起
# 拔除，理由寫在下面 —— 留下它們只會讓後人以為 adapter 端有閘門可調。
#
# 曾實作「距上一顆同類靈魂節點建立後，新鮮獨立證據 ≥ K(=2) 才動作」並配套
# 兩支測試。拔除的依據是**實測**，不是推測：把整個閘門關掉後全部測試依然全綠
# （MUT-02 NOT_RED）。原因是 K=2 與 `min_evidence` 數值相同，而本模組既有的
# 「已消化證據隔離」又把被靈魂節點消耗過的證據鍵**排除在計票外**、且按該維度
# **所有**靈魂節點扣除（比「只扣最後一顆」更嚴格）—— 兩者永遠同步觸發，
# 閘門不可能先於引擎擋下任何東西。也就是說那支守衛是**因為別的原因而綠**。
# Owner 裁定：無法被變異實驗擊穿的守衛就是心理安慰劑，不留。
#
# 限流的正確落點（單一事實來源）：
#   tests/test_elevation_evidence_scope_a1.py::test_a2_same_dimension_two_fresh_keys_still_accumulate
#   tests/test_elevation_evidence_scope_a1.py::test_e1_repeated_elevation_needs_two_fresh_keys_in_same_dimension
#   tests/inner_life/test_persist_idempotency.py  # 真冪等：同事件重跑邊不膨脹


# ── ④ 落盤層最後一道防線 ───────────────────────────────────────────


def test_soul_content_is_never_duplicated_across_rounds(tmp_path):
    """同一段內容永不落盤成兩顆靈魂節點。

    兩道機制的分工：錨點的佔用判斷管「要不要動作」，
    ``_persist_result`` 的 ``(agent, node_type, content_hash)`` 去重管
    「動作了也不產生重複靈魂」。
    """
    store_dir = tmp_path / "elevation"
    _ingest(store_dir, [_REPEATED_A, _REPEATED_B])
    elevate_matured_patterns(store_dir=store_dir)

    for _ in range(3):
        _ingest(store_dir, [_REPEATED_A, _REPEATED_C, _REPEATED_D])
        elevate_matured_patterns(store_dir=store_dir)

    contents = _soul_contents(store_dir)
    assert len(contents) == len(set(contents)), (
        f"同一段內容被落盤成多顆靈魂節點 —— content 去重失效：{contents}"
    )


def test_occupied_soul_contents_excludes_other_agents_and_types():
    """佔用集合只認同 (agent, 維度) —— 別隻靈魂說過的同一句話不算佔用。"""
    nodes = [
        _StubNode("x", "同一句話"),
    ]

    class _N:
        def __init__(self, nid, c, a, t):
            self.node_id, self.content, self.agent_id, self.node_type = nid, c, a, t

    class _E:
        _nodes = {
            "x": _N("x", "同一句話", "agent_rem", "value"),
            "y": _N("y", "同一句話", "agent_mai", "value"),
            "z": _N("z", "同一句話", "agent_rem", "belief"),
        }

    assert _occupied_soul_contents(_E(), "agent_rem", "value") == {"同一句話"}
    assert _occupied_soul_contents(_E(), "agent_akane", "value") == set()
    assert nodes  # 保持匯入不被最佳化工具刪掉