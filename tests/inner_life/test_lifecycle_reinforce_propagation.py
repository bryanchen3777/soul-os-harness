"""
tests/inner_life/test_lifecycle_reinforce_propagation.py
  — LIFECYCLE-REINFORCE-CHAIN：支持傳遞（Support Propagation）守衛

缺陷（2026-10-22 實測定案，logs/LIFECYCLE-REINFORCE-GAP-WORK-ORDER.md）
--------------------------------------------------------------------
`soul_elevation.engine.reinforce()` 是引擎早就寫好、語意完整的 REINFORCE 轉換
（新支持證據累積 → 強化／重新激活、`last_support_ts` 刷新 ⇒ 衰減錨點刷新，
docstring 原話 ``old ≠ outdated``），但**生產零呼叫者** —— 全 repo ＋
soul-elevation 套件 ``git grep reinforce``，唯一呼叫者在 ``harness/tl4.py``
參考實作。

⇒ 既有 value/belief/trait 的支持邊**永遠停在昇華那一刻**
（``engine.elevate()`` 複製出的靈魂邊 ``valid_from_ts=now``）⇒ 衰減錨點＝出生
時刻 ⇒ **必然**在出生＋7 天弱化，**與該 agent 是否仍在過日子無關**。

實測（2026-10-22）：value/belief/trait 合計 112 顆，其中「有建檔日之後的支持
邊」者 **0 顆**。

修法（Owner 2026-10-22 裁示「立即開票」）
----------------------------------------
判準（工單 §3.1，決策已定）：**新經歷要能重新支持既有 value/belief/trait，
其唯一證據來源是：該靈魂節點的祖先 pattern（``parent_node_id`` 鏈）在靈魂節點
建立之後，又累積到了新的支持邊。** 而該新邊的產生條件是既有的 content-hash
合併機制（SOUL-BUG-001）：同一命題被**另一個獨立事件**重複產生 ⇒ 節點合併 ＋
邊重綁 ⇒ pattern 的支持邊 +1。

刻意**不做**語意相似比對：召回通道（方向 A）目前 VERDICT=NO-GO（池 0 顆、
命中率 0/200 = 0.0000 < 5% 門檻）⇒ 不得把靈魂保活掛在已判定 NO-GO 的通道上。

這檔守的不變量
--------------
* **T1** 傳遞生效：祖先 pattern 被重新確認後，既有的 value 節點**必須**新增
  ≥1 條支持邊。
* **T2** 錨點語意：刷新後的邊 ``valid_from_ts`` 必須 == **重新確認事件的
  時刻**，**絕非**傳遞執行的當下。
* **T3** 異常隔離：``SUPERSEDED``（終態）節點會讓 ``engine.reinforce()`` 拋
  ``ValueError`` ⇒ 必須被逐節點隔離，**其餘節點照樣完成傳遞**。
* **T4** 冪等去重：引擎重跑時，同一事件的支持邊**不得**重複累積。
* **T5** 邊界不越界：祖先 pattern 沒有新邊的靈魂節點，支持邊計數**恆等於 0**
  （不得憑空生成）。
* **T6** 落盤保證：傳遞出的邊**必須**真的寫進 ``elevation_edges.jsonl``，
  不得被 ``_dedup_edges`` 誤吞。

⚠️ 本檔**不**把「靈魂型節點跨日支持邊 = 0」寫成門檻 —— 那是**量測事實**，
修好之後它就該 > 0。守衛一律寫成**機制不變量**（祖先有新邊 ⇒ 該節點必有新邊）。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    TRACE_FILENAME,
    elevate_matured_patterns,
)

# ── 時間基準 ────────────────────────────────────────────────────────
_T0 = "2026-10-20T02:00:00+00:00"   # 靈魂節點與原始證據的建檔時刻
_T2 = "2026-10-22T12:00:00+00:00"   # 重新確認（祖先 pattern 的新證據）時刻
_T_LATER = "2026-10-22T12:30:00+00:00"

_AGENT = "agent_rem"


# ── 合成圖譜建構（繞過 run_elevation，直接寫節點/邊檔）──────────────


def _pattern(node_id: str, content: str, *, created: str = _T0,
             candidate: str = "value") -> dict:
    return {
        "node_id": node_id,
        "node_type": "pattern",
        "content": content,
        "agent_id": _AGENT,
        "candidate_node_type": candidate,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": created,
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
    }


def _soul(node_id: str, content: str, parent_id: str, *, created: str = _T0,
          node_type: str = "value", lifecycle_state: str = "active") -> dict:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "content": content,
        "agent_id": _AGENT,
        "candidate_node_type": node_type,
        "confidence": 0.6,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": created,
        "lineage_depth": 1,
        "lineage_path": [parent_id],
        "parent_node_id": parent_id,
        "provenance_ref": None,
        "lifecycle_state": lifecycle_state,
    }


def _edge(node_id: str, source_id: str, *, valid_from: str,
          agent: str = _AGENT) -> dict:
    return {
        "edge_id": uuid.uuid4().hex,
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": source_id,
        "agent_id": agent,
        "weight": 1.0,
        "inner_life_event_id": source_id,
        "trigger_type": "diary:morning",
        "valid_from_ts": valid_from,
        "valid_until_ts": None,
    }


def _write_store(store_dir: Path, nodes: list, edges: list) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / NODES_FILENAME).write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )
    (store_dir / EDGES_FILENAME).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in edges) + "\n",
        encoding="utf-8",
    )


def _read_edges(store_dir: Path) -> list:
    p = store_dir / EDGES_FILENAME
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _read_trace(store_dir: Path) -> list:
    """讀 trace 事件（``ElevationTraceWriter`` 寫在 store 內）。

    用途：T4 的冪等觀測點。**磁碟上的邊去重**（``_dedup_edges``）本來就擋得住
    重複落盤 ⇒ 「磁碟只有 1 條」是弱觀測；真正要釘的是「**第二次跑沒有再做一次
    傳遞工作**」，而那會留下一筆額外的 ``node_revised / reinforced_only`` trace。
    """
    p = store_dir / TRACE_FILENAME
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _reinforce_trace_count(store_dir: Path, node_id: str) -> int:
    return len([
        t for t in _read_trace(store_dir)
        if t.get("event_type") == "node_revised"
        and t.get("node_id") == node_id
        and t.get("reason") == "reinforced_only"
    ])


def _edges_of(store_dir: Path, node_id: str) -> list:
    return [e for e in _read_edges(store_dir) if e.get("node_id") == node_id]


def _build_scenario(store_dir: Path, *, with_reconfirm: bool = True,
                    soul_state: str = "active", soul_holds_copies: bool = True):
    """建構「靈魂節點 V ＋ 祖先 pattern P（＋ 重新確認的新邊）」。

    語意（刻意走真實路徑）：
      * P 原本有 2 條獨立證據（src1/src2）。
      * ``soul_holds_copies=True``（預設）⇒ V 持有它們的副本 ⇒ 這兩個鍵進入
        「已消化」集合、**不參與**後續計票 ⇒ 重新確認的新邊只有 1 份獨立證據
        ⇒ ``1 < min_evidence=2`` ⇒ **不會**昇華出新靈魂節點，但**足以**重新
        支持既有的 V。這是生產上「同一命題被重複產生」的真實路徑。
      * ``soul_holds_copies=False`` ⇒ V 不持有那些副本（祖先的邊對 V 而言是
        陌生的）⇒ 傳遞若不守「建立之後」這道時間條件，就會把祖先的**舊**證據
        錯傳成新支持。**T5 靠這個參數構造它的可觀察性**（否則去重層會把
        越界的邊遮掉，變異打不紅）。
    """
    p_id = uuid.uuid4().hex
    v_id = uuid.uuid4().hex
    content = "他總是在同一個位置停頓一下，才繼續說下去。"

    nodes = [
        _pattern(p_id, content),
        _soul(v_id, content, p_id, lifecycle_state=soul_state),
    ]
    edges = [
        _edge(p_id, "src1", valid_from=_T0),
        _edge(p_id, "src2", valid_from=_T0),
    ]
    if soul_holds_copies:
        edges += [
            _edge(v_id, "src1", valid_from=_T0),
            _edge(v_id, "src2", valid_from=_T0),
        ]
    if with_reconfirm:
        # 祖先 pattern 的新證據：同一命題被另一個獨立事件重新產生
        edges.append(_edge(p_id, "src3", valid_from=_T2))

    _write_store(store_dir, nodes, edges)
    return p_id, v_id


# ── T1：傳遞生效（決定性）──────────────────────────────────────────


def test_t1_ancestor_reconfirmation_reinforces_existing_soul_node(tmp_path):
    """🔴 祖先 pattern 被重新確認 ⇒ 既有 value 節點**必須**新增支持邊。

    反空轉設計：這條測試**不**靠「回傳了東西」判綠，而是直接檢查**磁碟上的
    邊**：V 的 ``src3`` 支持邊必須真的存在。
    """
    store_dir = tmp_path / "elevation"
    _, v_id = _build_scenario(store_dir)

    elevate_matured_patterns(store_dir=store_dir)

    v_edges = _edges_of(store_dir, v_id)
    src3 = [e for e in v_edges if e.get("source_id") == "src3"]
    assert src3, (
        "祖先 pattern 被重新確認後，既有 value 節點沒有新增支持邊 —— "
        f"支持傳遞鏈斷了。V 的邊={[e.get('source_id') for e in v_edges]}"
    )
    assert len(src3) >= 1


# ── T2：錨點語意（刷新到「重新確認時刻」，不是「傳遞當下」）──────────


def test_t2_refreshed_edge_carries_the_reconfirmation_timestamp(tmp_path):
    """🔴 衰減錨點必須被刷新到**重新確認事件的時刻**，不是傳遞執行的當下。

    這是整張票的**物理核心**：錨點＝「命題最後一次被確認的時間」。
    若傳成當下，節點的 7 天門檻會從「現在」起算 ⇒ 等於沒修。
    """
    store_dir = tmp_path / "elevation"
    _, v_id = _build_scenario(store_dir)

    elevate_matured_patterns(store_dir=store_dir)

    src3 = [e for e in _edges_of(store_dir, v_id) if e.get("source_id") == "src3"]
    assert src3, "沒有傳遞邊，錨點語意無從檢查"
    for e in src3:
        assert e.get("valid_from_ts") == _T2, (
            "支持邊的時間戳必須是重新確認事件���時刻 "
            f"{_T2}，實際={e.get('valid_from_ts')!r}"
        )


# ── T3：異常隔離（SUPERSEDED 終態不得中斷整批）──────────────────────


def test_t3_superseded_target_is_isolated_and_others_still_propagate(tmp_path):
    """🔴 ``SUPERSEDED`` 是終態，``engine.reinforce()`` 對它拋 ``ValueError``。

    必須**逐節點隔離**：一顆終態節點不得讓整批傳遞中斷（否則整條靈魂層
    會因為一顆壞節點而停止重新被支持）。
    """
    store_dir = tmp_path / "elevation"
    p_id, v_id = _build_scenario(store_dir, soul_state="superseded")

    # 再加一顆正常（active）的靈魂節點，掛在同一個祖先 pattern 上。
    # 🔴 刻意**不**給它 src3 邊：它必須**靠傳遞**拿到。若測試自己先塞好 src3，
    # 「有沒有拿到 src3」這個斷言就會**自我滿足**，跟傳遞有沒有跑完全無關
    # —— 那是盲腸守衛（2026-10-22 的 V3 變異實驗抓到的真實缺陷）。
    ok_id = uuid.uuid4().hex
    nodes = [json.loads(x) for x in
             (store_dir / NODES_FILENAME).read_text(encoding="utf-8").splitlines()
             if x.strip()]
    edges = [json.loads(x) for x in
             (store_dir / EDGES_FILENAME).read_text(encoding="utf-8").splitlines()
             if x.strip()]
    nodes.append(_soul(ok_id, "他停頓的那一下，其實是在等我回話。", p_id))
    _write_store(store_dir, nodes, edges)

    elevate_matured_patterns(store_dir=store_dir)

    ok_src3 = [e for e in _edges_of(store_dir, ok_id) if e.get("source_id") == "src3"]
    assert ok_src3, (
        "一顆 SUPERSEDED 節點讓整批傳遞中斷了 —— 逐節點隔離失效"
    )
    # 終態節點本身不應被寫入新的支持邊
    v_src3 = [e for e in _edges_of(store_dir, v_id) if e.get("source_id") == "src3"]
    assert not v_src3, "SUPERSEDED 終態節點不應被重新支持"


# ── T4：冪等去重（引擎重跑不得重複累積）────────────────────────────


def test_t4_rerun_does_not_duplicate_the_propagated_edge(tmp_path):
    """🔴 同一事件的支持邊**不得**重複累積（引擎每輪都會重跑）。

    冪等有兩層：① 傳遞前的鍵比對（靈魂節點已持有 ⇒ 跳過）
              ② 落盤層 ``_dedup_edges``。

    🔴 **雙觀測點**（單靠磁碟會是假綠）：第 ② 層本來就擋得住重複落盤 ⇒
    「磁碟只有 1 條」證明不了「傳遞邏輯冪等」。所以這裡**另外**釘住
    **trace 事件數**：第二次跑**不得**再做一次傳遞工作（否則會多一筆
    ``node_revised / reinforced_only``）。
    """
    store_dir = tmp_path / "elevation"
    _, v_id = _build_scenario(store_dir)

    elevate_matured_patterns(store_dir=store_dir)
    first = len([e for e in _edges_of(store_dir, v_id) if e.get("source_id") == "src3"])
    assert first == 1, f"第一次傳遞應產生恰好 1 條，實際 {first}"
    assert _reinforce_trace_count(store_dir, v_id) == 1, (
        "第一次傳遞應留下恰好 1 筆 reinforce trace"
    )

    for _ in range(3):
        elevate_matured_patterns(store_dir=store_dir)

    after = [e for e in _edges_of(store_dir, v_id) if e.get("source_id") == "src3"]
    assert len(after) == 1, (
        f"引擎重跑後同一事件的支持邊重複累積：{len(after)} 條"
    )
    assert _reinforce_trace_count(store_dir, v_id) == 1, (
        "引擎重跑後又做了一次傳遞工作（trace 多出 node_revised）—— "
        "冪等失效：磁碟去重只是最後一道遮羞布，錨點刷新邏輯本身沒冪等"
    )


# ── T5：邊界不越界（祖先沒有新邊 ⇒ 不得憑空生成支持邊）──────────────


def test_t5_soul_without_ancestor_new_edge_gets_nothing(tmp_path):
    """🔴 祖先 pattern 沒有新邊的靈魂節點，支持邊計數**恆等於 0**。

    🔴 **可觀察性設計（決定性）**：這裡刻意讓 V **不持有**祖先那兩條原始邊
    （``soul_holds_copies=False``）。否則祖先的舊邊會被**去重層**擋掉 ⇒
    「沒有新支持邊」這兩種情況（沒傳遞 vs 傳遞被去重擋）長得一模一樣 ⇒
    守衛會是假綠、變異打不紅。⇒ 現在祖先的舊邊對 V 而言是陌生的，一旦有人
    把「建立之後」這道時間條件拿掉，V 就會憑空拿到 2 條支持邊 ⇒ 本測試轉紅。
    """
    store_dir = tmp_path / "elevation"
    _, v_id = _build_scenario(store_dir, with_reconfirm=False,
                              soul_holds_copies=False)

    elevate_matured_patterns(store_dir=store_dir)

    v_edges = _edges_of(store_dir, v_id)
    assert v_edges == [], (
        "祖先 pattern 沒有新邊，靈魂節點卻憑空拿到了支持邊 —— 傳遞越界："
        f"{[e.get('source_id') for e in v_edges]}"
    )


# ── T6：落盤保證（傳遞邊必須真的寫進檔案）─────────────────────────


def test_t6_propagated_edge_is_actually_persisted(tmp_path):
    """🔴 傳遞出的邊**必須**落在 ``elevation_edges.jsonl`` 上。

    為什麼這條不是廢話：``_dedup_edges`` 的鍵是
    ``(node_id, source_id, inner_life_event_id)``；昇華時複製到靈魂節點的邊
    與傳遞出的邊**若同鍵**，傳遞邊會被去重丟棄 ⇒ 錨點刷新根本沒發生，
    而磁碟上看起來一切正常（**假綠**）。
    ⇒ 這條直接檢查磁碟檔案本體。
    """
    store_dir = tmp_path / "elevation"
    _, v_id = _build_scenario(store_dir)

    elevate_matured_patterns(store_dir=store_dir)

    on_disk = [e for e in _read_edges(store_dir)
               if e.get("node_id") == v_id and e.get("source_id") == "src3"]
    assert on_disk, (
        "傳遞邊沒有落到 elevation_edges.jsonl —— 落盤層把它吃掉了（假綠）"
    )
    assert len(on_disk) == 1
    # 邊的鍵必須可區分於原始複製邊
    all_keys = [(e.get("node_id"), e.get("source_id"), e.get("inner_life_event_id"))
                for e in _read_edges(store_dir)]
    assert len(all_keys) == len(set(all_keys)), (
        f"邊鍵重複（去重失效）：{all_keys}"
    )


# ── 機制不變量（非門檻）：祖先有新邊 ⇒ 該節點必有新邊 ───────────────


def test_mechanism_invariant_ancestor_new_edge_implies_soul_new_edge(tmp_path):
    """機制不變量（每日可重算，不得寫死筆數）：
    「某靈魂節點的祖先 pattern 有建立之後的新邊」⇒「該靈魂節點有建立之後的
    新邊」。這是 owner 要的**可泛化**形式，不是「邊數 = N」那種轉錄常數。
    """
    store_dir = tmp_path / "elevation"
    p_id, v_id = _build_scenario(store_dir)

    elevate_matured_patterns(store_dir=store_dir)

    edges = _read_edges(store_dir)
    pattern_new = [
        e for e in edges
        if e.get("node_id") == p_id and str(e.get("valid_from_ts")) > _T0
    ]
    soul_new = [
        e for e in edges
        if e.get("node_id") == v_id and str(e.get("valid_from_ts")) > _T0
    ]
    assert pattern_new, "前提失效：祖先 pattern 沒有新邊，這條測試測不到東西"
    assert soul_new, (
        "不變量被打破：祖先 pattern 有新邊，但靈魂節點沒有新邊 "
        "⇒ 衰減錨點不會被刷新"
    )
