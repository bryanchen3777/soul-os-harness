# -*- coding: utf-8 -*-
"""tests/test_recall_prompt_injection.py — 方向 A 第二階段（Prompt 注入）的守衛測試。

工單：``logs/SIM-ACCEL-RECALL-30D-WORK-ORDER.md`` §3.4。

本檔守的是**接線的三條硬邊界**，每一條都對應一次變異實驗（轉紅即證明守衛有牙）：

  MUT-P1 隔離守門    共用管線拿掉隔離守門 ⇒ 隔離節點進回憶區塊（轉紅）
  MUT-P2 啟用判準    啟用判準從「GO」改成「永遠 True」⇒ 未達 GO 仍注入（轉紅）
  MUT-P3 取代者正文  拿不到取代者正文 ⇒ 句式退化成「但後來改變了」（轉紅）

⚠️ 每條「不得發生 X」都配「該發生的位置確實發生了」的對照組（反假綠）：
  * 同一批節點**沒有** sidecar 時**確實被召回**（證明不是無差別清空）。
  * 四項判準全過的沙盒**確實**產出區塊（證明不是永遠空字串）。
  * 現況（生產池 = 0）**確實**是空字串 ⇒ prompt 逐位元不變。

紅線：本檔不得啟動伺服器、不得綁 :8000/:8765/:8766/:8767、不得對生產發真實請求、
不得殺任何行程、**不得使用 subprocess**（tests/infra 的 R5 白名單大小鎖死 22）；
所有 store 一律 ``tmp_path``，不碰 ``data/**``（唯一的 ``data/**`` 存取是**唯讀**
讀取生產圖譜，用來證明「現況池 = 0 ⇒ prompt 逐位元不變」）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.inner_life import recall_prompt
from src.inner_life.elevation_recall import query_recall_by_tags
from src.inner_life.emergent_projection import format_emergent_block
from src.inner_life.recall_readiness import go_no_go, hit_rate, pool_summary

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_STORE = REPO_ROOT / "data" / "elevation"
PROD_CONV = REPO_ROOT / "data" / "conversations"

ALICE = "agent_alice"
NODES_FILENAME = "elevation_nodes.jsonl"
LIFECYCLE_EVENTS_FILENAME = "elevation_lifecycle_events.jsonl"


# ── 合成資料（欄位對齊 elevation_nodes.jsonl 記錄）────────────────────
def _node(node_id, node_type="belief", content="深夜加班讓人心累。", agent_id=ALICE,
          created_ts="2026-08-29T00:00:00.000000+00:00", lifecycle_state=None, **extra):
    node = {
        "node_id": node_id,
        "node_type": node_type,
        "content": content,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "agent_id": agent_id,
        "parent_node_id": None,
        "lineage_depth": 0,
        "lineage_path": node_id,
        "created_ts": created_ts,
        "provenance_ref": None,
    }
    if lifecycle_state is not None:
        node["lifecycle_state"] = lifecycle_state
    node.update(extra)
    return node


def _write_nodes(store_dir: Path, nodes) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / NODES_FILENAME).write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )


def _append_nodes(store_dir: Path, nodes) -> None:
    """append-only 追加節點（與生產落盤語意同款）。"""
    with open(store_dir / NODES_FILENAME, "a", encoding="utf-8") as fh:
        for node in nodes:
            fh.write(json.dumps(node, ensure_ascii=False) + "\n")


def _write_events(store_dir: Path, events) -> None:
    """寫生命週期 sidecar（隔離旗標的唯一合法載體）。"""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / LIFECYCLE_EVENTS_FILENAME).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def _quarantine_event(node_id, state="dormant"):
    return {
        "ts": "2026-10-19T19:30:00+00:00",
        "node_id": node_id,
        "event": "quarantine",
        "lifecycle_state": state,
        "reason": "SOUL-BUG-010 historical truncation victim",
    }


def _write_conversations(conv_dir: Path, messages) -> Path:
    """寫一份合成對話（探針來源，僅供本檔的沙盒量測）。"""
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "bryan_agent_alice_private.json").write_text(
        json.dumps(messages, ensure_ascii=False), encoding="utf-8"
    )
    return conv_dir


# ── 「四項判準全過」的合成沙盒 ──────────────────────────────────────────
DORMANT_CONTENTS = [
    "我以前很在意每個週末都去海邊散步。",
    "我以前很在意咖啡要手沖不要義式。",
    "我以前很在意看完一部電影要寫下感想。",
    "我以前很在意整理書桌能讓心情變好。",
    "我以前很在意爬山的時候不要看手機。",
    "我以前很在意幫朋友保留那本舊詩集。",
]
SUPERSEDED_CONTENT = "我以前覺得只要努力就一定會有回報。"
SUPERSEDER_CONTENT = "我以前覺得方向比努力更重要。"


def _go_store(tmp_path: Path):
    """建立一個「四項判準全過」的沙盒 store ＋ 合成對話目錄（零生產依賴）。"""
    store = tmp_path / "store"
    nodes = [
        _node("n-dormant-%d" % i, "value", content, lifecycle_state="dormant")
        for i, content in enumerate(DORMANT_CONTENTS)
    ]
    nodes.append(_node(
        "n-superseded", "belief", SUPERSEDED_CONTENT,
        lifecycle_state="superseded", superseded_by="n-new",
    ))
    nodes.append(_node(
        "n-new", "belief", SUPERSEDER_CONTENT,
        lifecycle_state="active", parent_node_id="n-superseded",
        lineage_depth=1, lineage_path="n-superseded|n-new",
    ))
    _write_nodes(store, nodes)
    conv = _write_conversations(tmp_path / "conversations", [
        {"role": "user", "content": "海邊 散步"},
        {"role": "assistant", "content": "好。"},
        {"role": "user", "content": "努力 回報"},
    ])
    return store, conv


@pytest.fixture(autouse=True)
def _reset_enable_cache():
    """每次測試前清掉行程內啟用快取（否則 TTL 30 分鐘會讓測試互相污染）。"""
    recall_prompt._ENABLE_CACHE.update(
        {"key": None, "ts": 0.0, "enabled": False, "verdict": None}
    )
    yield


# ══ ① 隔離守門（MUT-P1）═══════════════════════════════════════════════
def test_control_group_same_nodes_without_sidecar_are_recalled(tmp_path):
    """反假綠：同一批節點**沒有** sidecar 時**確實被召回**（不是無差別清空）。"""
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-bad", "value", "海邊的週末我一定會去。",
                               lifecycle_state="dormant")])
    hits = query_recall_by_tags(ALICE, ["海邊"], store_dir=store)
    assert [h["node_id"] for h in hits] == ["n-bad"]


def test_quarantined_node_never_appears_in_recall_block(tmp_path, monkeypatch):
    """🔴 MUT-P1：隔離節點**永不**出現在回憶區塊裡。

    設計註記：隔離節點的內容刻意**不**撞任何探針（探針＝「海邊 散步」），
    否則「命中非隔離比例 = 1.0」這條判準會先判 NO-GO，區塊整個不出現——
    那就分不清是「隔離守門擋掉」還是「啟用判準擋掉」。這裡讓**提問**撞上隔離節點
    （「潛水艇」），但**探針**不撞 ⇒ 啟用判準仍是 GO ⇒ 唯一能擋住它的就是隔離守門。
    """
    store, conv = _go_store(tmp_path)
    _append_nodes(store, [
        _node("n-bad", "value", "我以前很在意那艘潛水艇。", lifecycle_state="dormant"),
    ])
    _write_events(store, [_quarantine_event("n-bad")])
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)

    block = recall_prompt.format_recall_block(ALICE, "海邊 散步 潛水艇", store_dir=store)
    assert block != "", "對照組失效：連正常節點都沒進區塊，這條守衛會假綠"
    assert "潛水艇" not in block, "隔離節點被召回進區塊了 —— 隔離守門失效"
    assert "我以前很在意那艘潛水艇。" not in block


# ══ ② 啟用判準（MUT-P2）════════════════════════════════════════════════
def test_block_empty_when_verdict_not_go(tmp_path, monkeypatch):
    """🔴 MUT-P2：四項判準未達 GO ⇒ 區塊為空（**即使有命中**）。"""
    store = tmp_path / "s"
    _write_nodes(store, [_node("n1", "value", "海邊的週末", lifecycle_state="dormant")])
    conv = _write_conversations(tmp_path / "conversations", [
        {"role": "user", "content": "海邊 週末"},
    ])
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)

    assert recall_prompt.is_recall_enabled(store_dir=store) is False, (
        "池只有 1 顆（< 5）⇒ 判準應為 NO-GO"
    )
    block = recall_prompt.format_recall_block(ALICE, "海邊 週末", store_dir=store)
    assert block == "", "未達 GO 卻注入了回憶區塊 —— 啟用判準失效"


def test_go_sandbox_really_produces_a_block(tmp_path, monkeypatch):
    """反假綠：四項判準全過的沙盒**確實**產出區塊（不是永遠空字串）。"""
    store, conv = _go_store(tmp_path)
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)

    assert recall_prompt.is_recall_enabled(store_dir=store) is True, (
        "合成沙盒的四項判準應全過 —— 否則下面的區塊斷言會假綠"
    )
    block = recall_prompt.format_recall_block(ALICE, "海邊 散步", store_dir=store)
    assert block.startswith(recall_prompt.RECALL_BLOCK_HEADER)


def test_kill_switch_off_yields_empty_block(tmp_path, monkeypatch):
    """人工 kill switch（``SOUL_OS_RECALL_INJECTION=off``）⇒ 一律不注入。"""
    store, conv = _go_store(tmp_path)
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)
    monkeypatch.setenv(recall_prompt.RECALL_INJECTION_ENV, "off")
    assert recall_prompt.format_recall_block(ALICE, "海邊 散步", store_dir=store) == ""


# ══ ③ 呈現句式（MUT-P3）════════════════════════════════════════════════
def test_dormant_node_renders_dormant_tone(tmp_path, monkeypatch):
    """DORMANT 節點 ⇒ 「我以前很在意……」的過去式語氣。"""
    store, conv = _go_store(tmp_path)
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)
    block = recall_prompt.format_recall_block(ALICE, "海邊 散步", store_dir=store)
    assert "我以前很在意" in block


def test_superseded_node_renders_supersession_phrase(tmp_path, monkeypatch):
    """🔴 MUT-P3：SUPERSEDED 節點 ⇒ 「我以前覺得 A，但後來發現 B」（B = 取代者正文）。"""
    store, conv = _go_store(tmp_path)
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)
    block = recall_prompt.format_recall_block(ALICE, "努力 回報", store_dir=store)
    assert "我以前覺得" in block, "SUPERSEDED 命中沒有走過去式句式"
    assert "但後來發現" in block, "取代者正文沒有接上 —— 呈現層退化"
    assert SUPERSEDER_CONTENT in block, "取代者正文缺席"


# ══ ④ 語氣隔離（回憶不得進 EMERGENT block）════════════════════════════
def test_recall_block_never_enters_emergent_block(tmp_path, monkeypatch):
    """契約 §3.1：DORMANT / SUPERSEDED 不得被當成「現在的自我定義」獨立投影。

    註記：既有的 **lineage 降維**（SE-5 第 6 條）本來就會把被取代的舊命題以
    「我以前觉得 A，但后来发现 B」的形式帶進 EMERGENT —— 那是**凍結契約**允許的
    （只讀 lineage、不復活 A）。本檔守的是：A 不得**獨立**被投影，DORMANT 一律不投影。
    """
    from src.inner_life.emergent_projection import project_emergent

    store, conv = _go_store(tmp_path)
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: conv)
    emergent = format_emergent_block(ALICE, store_dir=store, record_trace=False)
    assert recall_prompt.RECALL_BLOCK_HEADER not in emergent
    for content in DORMANT_CONTENTS:
        assert content not in emergent, "dormant 記憶漏進 EMERGENT block 了"
    projected = project_emergent(ALICE, store_dir=store, record_trace=False)
    assert [p["node_id"] for p in projected] == ["n-new"], (
        "只有 active 的取代者該被投影；dormant / superseded 不得獨立投影"
    )


# ══ ⑤ 現況：生產池 = 0 ⇒ prompt 逐位元不變 ════════════════════════════
def test_production_pool_state_and_block_are_consistent(monkeypatch):
    """現況（生產圖譜）的**一致性**守衛：區塊空 ⇔ 判準非 GO。

    註：生產圖譜目前帶著 2026-10-20 事故留下的污染（待主大腦裁決清理），其四項判準
    暫時是 GO ⇒ 區塊非空。本檔因此**不**斷言生產的具體狀態（那是裁決的事），只斷言
    「判準與注入行為一致」——這條在清理前後都成立。

    工單 §4.2 的硬條件（「現況池 = 0 ⇒ prompt 逐位元不變」）要等生產回復乾淨後由
    主大腦覆核；那時本條會自動退化成「區塊必須為空」。
    """
    if not PROD_STORE.exists():
        pytest.skip("生產圖譜不存在（此環境無 data/elevation）")
    monkeypatch.setattr(recall_prompt, "runtime_conversations_dir", lambda: PROD_CONV)
    enabled = recall_prompt.is_recall_enabled(store_dir=PROD_STORE)
    block = recall_prompt.format_recall_block("agent_yua", "海邊 咖啡", store_dir=PROD_STORE)
    if not enabled:
        assert block == "", "判準非 GO 卻注入了回憶區塊 —— 啟用判準失效"
    if block:
        assert block.startswith(recall_prompt.RECALL_BLOCK_HEADER), (
            "區塊必須帶獨立標題（語氣隔離）"
        )


class _StubMemory:
    """``memory`` 最小替身（``_build_messages_private`` 只用這一個 method）。"""

    def get_recent_with_meta(self, session_key, limit=20):
        return []


def _stub_proxy_io(monkeypatch):
    """把 proxy 的 IO 型 helper 全部 stub 成空字串（測試不碰生產資料）。"""
    from src.llm import proxy

    for name in (
        "_format_horizon_block",
        "_format_capability_block",
        "_format_emergent_block",
        "_format_relationship_block",
        "_format_relational_perception_block",
        "_format_recent_inner_life",
        "_format_attachment_str",
        "_format_temporal_anchor",
    ):
        monkeypatch.setattr(proxy, name, lambda *a, **k: "")
    monkeypatch.setattr(proxy, "_load_self_recent", lambda *a, **k: [])
    monkeypatch.setattr(proxy, "_load_bry_recent", lambda *a, **k: [])
    return proxy


def _build_private_prompt(proxy):
    return proxy._build_messages_private(
        agent_id=ALICE,
        soul="測試用靈魂",
        current_input="今天天氣如何",
        memory_context="",
        memory=_StubMemory(),
    )


def test_recall_block_is_wired_into_private_prompt(monkeypatch):
    """接線真的活著：區塊非空時**確實**進入私聊 system prompt。"""
    proxy = _stub_proxy_io(monkeypatch)
    marker = "【回憶】MARKER-測試"
    monkeypatch.setattr(proxy, "_format_recall_block", lambda *a, **k: marker)
    messages = _build_private_prompt(proxy)
    system_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    assert marker in system_text, "RECALL block 沒接進私聊 prompt"


def test_private_prompt_is_byte_identical_when_recall_block_empty(monkeypatch):
    """區塊為空 ⇒ prompt 與未實作時**逐位元相同**（硬條件）。"""
    proxy = _stub_proxy_io(monkeypatch)
    before = _build_private_prompt(proxy)
    monkeypatch.setattr(proxy, "_format_recall_block", lambda *a, **k: "")
    after = _build_private_prompt(proxy)
    assert before == after, "空區塊仍然改動了 prompt —— 接線不是 no-op"


# ══ ⑥ 漂移守衛：探針收集必須與凍結 CLI 同語意 ══════════════════════════
def test_probe_collection_matches_frozen_cli():
    """``recall_prompt.collect_probe_messages`` 與 ``scripts/recall_readiness_cli.py``
    的 ``collect_user_messages`` 必須產出**同一批**探針（否則啟用判準與量測面漂移）。"""
    import importlib.util

    if not PROD_CONV.exists():
        pytest.skip("生產對話目錄不存在（此環境無 data/conversations）")
    cli_path = REPO_ROOT / "scripts" / "recall_readiness_cli.py"
    spec = importlib.util.spec_from_file_location("_frozen_recall_readiness_cli", cli_path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    frozen = cli.collect_user_messages(PROD_CONV, 200)
    mine = recall_prompt.collect_probe_messages(200, conversations_dir=PROD_CONV)
    assert [(a, t) for a, t, _c in frozen] == mine, (
        "探針收集與凍結 CLI 漂移了 —— 啟用判準與量測面會講不同的故事"
    )


def test_hit_rate_uses_same_pure_functions(tmp_path):
    """量測面與啟用面必須是**同一套純函式**（不得各寫一份算法）。"""
    store, conv = _go_store(tmp_path)
    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    nodes = load_elevation_nodes(store)
    quarantined = load_quarantined_node_ids(store)
    summary = pool_summary(nodes, quarantined)
    probes = recall_prompt.collect_probe_messages(200, conversations_dir=conv)
    hit = hit_rate(nodes, probes, quarantined_ids=quarantined)
    verdict = go_no_go(summary, hit)
    assert verdict["verdict"] == "GO", "合成沙盒的四項判準應全過"
    assert summary["recallable"] >= 5
    assert summary["recallable_by_state"].get("superseded", 0) >= 1
