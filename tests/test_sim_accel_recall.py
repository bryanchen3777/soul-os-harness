# -*- coding: utf-8 -*-
"""tests/test_sim_accel_recall.py — 虛擬時鐘快進沙盒 CLI 的守衛測試。

工單：``logs/SIM-ACCEL-RECALL-30D-WORK-ORDER.md`` §3.4。

本檔守的是**快進與矛盾注入的五條邊界**：

  MUT-S1 沙盒化失效  沙盒與來源共用同一個 inode（硬連結）⇒ 對沙盒的寫入其實
                     寫進來源（轉紅：CLI 自己的 sha256 對帳會中止並 exit 5）
  MUT-S2 矛盾不足    矛盾去重失效（同一 source_id 記 3 次也算 3 個獨立）⇒ 不足 3 個
                     卻被當成獨立證據（轉紅）
  MUT-S3 矛盾同日    跨日門檻失效（矛盾全落在同一個模擬日也算數）⇒ 同日矛盾被接受（轉紅）
  MUT-S4 sidecar 欄位  sidecar 事件拿掉 ``lifecycle_state`` ⇒ 舊節點永遠不變 superseded（轉紅）
  MUT-S5 GO 判準     沙盒 GO 判準硬寫成 True ⇒ 未達判準也報 GO（轉紅）

⚠️ 反假綠對照組：
  * 快進確實把節點推進 dormant（證明不是「什麼都沒做所以看起來很乾淨」）。
  * 四項判準在**空圖譜**上回 NO-GO（證明 GO 不是常數）。
  * 矛盾不足 / 矛盾同日**確實被引擎拒絕**（引擎門檻是真的，不是本檔自訂的）。

紅線：本檔不得啟動伺服器、不得綁 :8000/:8765/:8766/:8767、不得對生產發真實請求、
不得殺任何行程、**不得使用 subprocess**（tests/infra 的 R5 白名單大小鎖死 22）；
沙盒一律寫在 ``tmp_path``，生產 ``data/**`` 只**唯讀**存取。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import shutil
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI_PATH = REPO_ROOT / "scripts" / "sim_accel_recall_cli.py"
PROD_DIR = REPO_ROOT / "data" / "elevation"
PROD_CONV = REPO_ROOT / "data" / "conversations"
PROD_FILES = (
    "elevation_nodes.jsonl",
    "elevation_edges.jsonl",
    "elevation_lifecycle_events.jsonl",
)

def _sha(path) -> str:
    """檔案 sha256（檔不存在回 "<missing>"）。"""
    path = pathlib.Path(path)
    if not path.exists():
        return "<missing>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cli():
    """以檔案路徑載入 CLI 模組（scripts/ 不是 package）。"""
    spec = importlib.util.spec_from_file_location("sim_accel_recall_cli_under_test", CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load_cli()


def _prod_available() -> bool:
    return all((PROD_DIR / name).exists() for name in PROD_FILES)


def _clone_sandbox(src: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    """把沙盒三檔複製一份（每個測試各自一份，避免互相污染）。"""
    dest.mkdir(parents=True, exist_ok=True)
    for name in PROD_FILES:
        source = src / name
        if source.exists():
            shutil.copy2(source, dest / name)
    return dest


@pytest.fixture(scope="module")
def fast_sandbox(tmp_path_factory):
    """把生產圖譜快進 30 天到一個沙盒（模組級：只做一次）。"""
    if not _prod_available():
        pytest.skip("生產圖譜不存在（此環境無 data/elevation 三檔）")
    root = tmp_path_factory.mktemp("sim_accel")
    sandbox = root / "sandbox"
    CLI.copy_graph(PROD_DIR, sandbox)
    sim_now = datetime.now(timezone.utc) + timedelta(days=30)
    passes = CLI.fast_forward(sandbox, sim_now)
    return {"sandbox": sandbox, "sim_now": sim_now, "passes": passes}


@pytest.fixture
def sandbox_copy(fast_sandbox, tmp_path):
    """快進後沙盒的獨立副本（測試可安心改它）。"""
    return _clone_sandbox(fast_sandbox["sandbox"], tmp_path / "sandbox")


# ══ ① 快進本身（＋ 生產零副作用）══════════════════════════════════════
def test_fast_forward_30d_produces_dormant_nodes(fast_sandbox):
    """快進確實把節點推進 dormant（不是「什麼都沒做所以看起來乾淨」）。"""
    from src.inner_life.emergent_projection import load_elevation_nodes

    nodes = load_elevation_nodes(fast_sandbox["sandbox"])
    states = [n.get("lifecycle_state") for n in nodes if isinstance(n, dict)]
    assert states.count("dormant") > 0, "快進 30 天後一顆 dormant 都沒有"
    # 冪等：最後一輪必須是 0 筆（no-op，不是失敗）。
    assert fast_sandbox["passes"][-1]["events"] == 0, (
        "快進沒有收斂到固定點：%s" % fast_sandbox["passes"]
    )


def test_fast_forward_only_appends_lifecycle_events(tmp_path):
    """🔴 MUT-S1：快進**不得**改動圖譜本體（節點檔／邊檔逐位元相同）。

    快進的產出是**沙盒 sidecar 上追加的 ``lifecycle_transition`` 事件**——那是
    設計中的輸出（生命週期軌跡），不是污染；圖譜本體（節點檔／邊檔）必須逐位元不動。

    設計註記（**踩過的坑**）：生產圖譜是**活的 append-only 檔**——生產服務每小時
    會自己 append 整點天氣 pattern 節點（實測 2026-10-21T03:18:19Z 寫入一筆
    ``node_type=pattern``／``agent_id=default`` 的天氣節點）。⇒ 任何「跨長時間窗
    比對生產檔 hash」的斷言都**天生會誤報**（第一次全庫回歸就是這樣紅的：
    模組匯入時取的基準 vs 斷言時刻，中間被服務寫了一筆）。

    ⇒ 這裡用生產圖譜的 **tmp 副本**當工作區（服務碰不到它）⇒ 斷言確定性成立，
    且證明的仍是同一件事：「圖譜本體一個位元組都沒被快進改動」。
    """
    if not _prod_available():
        pytest.skip("生產圖譜不存在（此環境無 data/elevation 三檔）")
    source = tmp_path / "source"
    CLI.copy_graph(PROD_DIR, source)
    graph_files = ("elevation_nodes.jsonl", "elevation_edges.jsonl")
    before = {name: _sha(source / name) for name in graph_files}
    sidecar = source / "elevation_lifecycle_events.jsonl"
    sidecar_before_lines = len(sidecar.read_text(encoding="utf-8").splitlines())

    sim_now = datetime.now(timezone.utc) + timedelta(days=30)
    passes = CLI.fast_forward(source, sim_now)
    assert passes[-1]["events"] == 0, "快進沒有收斂到固定點"

    for name in graph_files:
        assert _sha(source / name) == before[name], (
            "圖譜本體 %s 被快進改動了 —— 沙盒化失效" % name
        )
    new_lines = sidecar.read_text(encoding="utf-8").splitlines()[sidecar_before_lines:]
    for line in new_lines:
        event = json.loads(line)
        assert event.get("event") == "lifecycle_transition", (
            "sidecar 只准追加 lifecycle_transition 事件，實際：%r" % event.get("event")
        )
        assert event.get("lifecycle_state") in ("weakening", "dormant", "superseded")
    # （新行數不設下限：生產圖譜目前已帶著 2026-10-20 事故的污染，節點已在固定點上 ⇒
    #   快進可能是 0 筆 no-op。不變量是「圖譜本體不動」，不是「一定有新事件」。）


def test_cli_main_reports_go_and_keeps_source_intact(tmp_path):
    """端到端：CLI 跑完回 GO（exit 0），且**來源**三檔逐位元未動。

    來源用生產圖譜的 tmp 副本（見上面「踩過的坑」註記：生產檔是活的，比對必須
    落在確定性的視窗裡）。生產面另有專門的 marker 守衛（下一條）。
    """
    if not _prod_available():
        pytest.skip("生產圖譜不存在（此環境無 data/elevation 三檔）")
    source = tmp_path / "source"
    CLI.copy_graph(PROD_DIR, source)
    before = {name: _sha(source / name) for name in PROD_FILES}

    out_dir = tmp_path / "sandbox"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = CLI.main([
            "--days", "30",
            "--source-dir", str(source),
            "--out-dir", str(out_dir),
            "--json",
        ])
    payload_text = buf.getvalue().split("[report]")[0]
    assert code == 0, (
        "CLI 應回 GO（exit 0），實際 %s"
        "（3 = 拒絕執行：沙盒與來源共用 inode ⇒ 寫沙盒等於寫來源）" % code
    )
    payload = json.loads(payload_text)
    assert payload["verdict"]["verdict"] == "GO"
    assert payload["all_production_unchanged"] is True
    for name in PROD_FILES:
        assert _sha(source / name) == before[name], "CLI 改動了來源圖譜 %s" % name


def _production_marker_count() -> int:
    """生產面出現「沙盒寫入標記」的次數（合成矛盾事件 ＋ 合成節點 provenance）。"""
    total = 0
    for name in ("elevation_lifecycle_events.jsonl", "elevation_nodes.jsonl"):
        path = PROD_DIR / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        total += text.count("contradiction_supersede")
        total += text.count("sim_accel_supersede")
    return total


def test_production_graph_contains_no_sandbox_markers(tmp_path):
    """🔴 沙盒資料**不得**流回生產（marker 守衛；確定性斷言）。

    生產面若出現本 CLI 的寫入標記（sidecar 的 ``reason=contradiction_supersede``，
    或合成節點的 ``provenance_ref=sim_accel_supersede_*``），就代表沙盒資料外洩。

    註：斷言的是「CLI 跑完後，生產面的標記數**沒有增加**」——而不是「標記數 = 0」。
    原因：生產目前帶著 2026-10-20 事故留下的標記（待主大腦裁決清理），用絕對值當
    基準會讓本檔在清理前後紅綠互換（＝把別人的裁決綁進我的守衛）。
    """
    if not _prod_available():
        pytest.skip("生產圖譜不存在（此環境無 data/elevation 三檔）")
    markers_before = _production_marker_count()
    out_dir = tmp_path / "sandbox"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = CLI.main([
            "--days", "30",
            "--out-dir", str(out_dir),
            "--json",
        ])
    assert code in (0, 5), "CLI 退出碼 %s 非預期（0=GO；5=生產面有變動，依工單規則非零退出）" % code
    payload = json.loads(buf.getvalue().split("[report]")[0])
    new_ids = [r["new_node_id"] for r in payload["supersede_records"]]

    for name in ("elevation_lifecycle_events.jsonl", "elevation_nodes.jsonl"):
        text = (PROD_DIR / name).read_text(encoding="utf-8", errors="replace")
        for node_id in new_ids:
            assert node_id not in text, (
                "生產節點檔出現本次沙盒合成節點 %s —— 沙盒資料外洩到生產了" % node_id
            )
    assert _production_marker_count() == markers_before, (
        "生產面的沙盒標記數增加了 —— 沙盒資料外洩到生產了"
    )


# ══ ② 矛盾注入：真的產出 superseded ＋ lineage ════════════════════════
def test_contradiction_injection_produces_one_superseded(sandbox_copy, fast_sandbox):
    """注入 3 份跨 2 個模擬日的矛盾 ⇒ 舊節點 superseded、新節點掛 lineage。"""
    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    sim_now = fast_sandbox["sim_now"]
    nodes = load_elevation_nodes(sandbox_copy)
    quarantined = load_quarantined_node_ids(sandbox_copy)
    edges = CLI._load_edges(sandbox_copy)
    target = CLI.select_supersede_target(nodes, quarantined)
    assert target is not None, "沙盒池內找不到可取代的 value 節點"

    result = CLI.inject_supersede(
        sandbox_copy, nodes, edges, target, group=0, sim_now=sim_now
    )
    persisted = CLI.persist_supersede(sandbox_copy, result, sim_now=sim_now)

    after = {n["node_id"]: n for n in load_elevation_nodes(sandbox_copy)}
    old = after[target["node_id"]]
    new = after[persisted["node"]["node_id"]]
    assert old["lifecycle_state"] == "superseded", "舊節點沒被標成 superseded"
    assert old["superseded_by"] == new["node_id"], "sidecar 沒帶 superseded_by"
    assert new["parent_node_id"] == target["node_id"], "新節點的 parent 不對"
    assert new["lineage_depth"] == int(target.get("lineage_depth") or 0) + 1
    assert target["node_id"] in str(new["lineage_path"])


def test_supersede_requires_three_independent_contradictions(sandbox_copy, fast_sandbox):
    """🔴 MUT-S2：同一 ``source_id`` 記 3 次**不算** 3 份獨立證據 ⇒ 必須失敗。

    這個測試同時是「引擎門檻是真的」的證據：它呼叫的是**凍結引擎**的
    ``record_contradiction`` + ``supersede``，門檻在 ``soul_elevation`` 裡。
    """
    from soul_elevation.engine import InternalizingEngine

    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    sim_now = fast_sandbox["sim_now"]
    nodes = load_elevation_nodes(sandbox_copy)
    quarantined = load_quarantined_node_ids(sandbox_copy)
    edges = CLI._load_edges(sandbox_copy)
    target = CLI.select_supersede_target(nodes, quarantined)
    assert target is not None

    engine = CLI._rebuild_engine_for(
        sandbox_copy,
        CLI.normalize_contradiction_pressure(nodes),
        list(edges),
        agent_id=target["agent_id"],
        llm=None,
    )
    # 同一 source_id 記 3 次（跨 2 個模擬日 ⇒ 跨日門檻不是阻因，獨立性才是）。
    engine.record_contradiction(target["node_id"], source_id="sim_same",
                                ts=sim_now.isoformat())
    engine.record_contradiction(target["node_id"], source_id="sim_same",
                                ts=sim_now.isoformat())
    engine.record_contradiction(target["node_id"], source_id="sim_same",
                                ts=(sim_now + timedelta(days=1)).isoformat())
    with pytest.raises(ValueError):
        engine.supersede(
            target["node_id"],
            new_content="沙盒合成的新命題",
            new_confidence=0.6,
            valence="neutral",
            source_ids=["sim_same"],
            ts=sim_now.isoformat(),
        )


def test_supersede_requires_contradictions_across_two_days(sandbox_copy, fast_sandbox):
    """🔴 MUT-S3：3 份獨立矛盾但**全落在同一個模擬日** ⇒ 必須失敗。"""
    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    sim_now = fast_sandbox["sim_now"]
    nodes = load_elevation_nodes(sandbox_copy)
    quarantined = load_quarantined_node_ids(sandbox_copy)
    edges = CLI._load_edges(sandbox_copy)
    target = CLI.select_supersede_target(nodes, quarantined)
    assert target is not None

    engine = CLI._rebuild_engine_for(
        sandbox_copy,
        CLI.normalize_contradiction_pressure(nodes),
        list(edges),
        agent_id=target["agent_id"],
        llm=None,
    )
    for i in range(3):
        engine.record_contradiction(target["node_id"], source_id="sim_g%d" % i,
                                    ts=sim_now.isoformat())
    with pytest.raises(ValueError):
        engine.supersede(
            target["node_id"],
            new_content="沙盒合成的新命題",
            new_confidence=0.6,
            valence="neutral",
            source_ids=["sim_g0", "sim_g1", "sim_g2"],
            ts=sim_now.isoformat(),
        )


# ══ ③ sidecar 事件欄位（overlay 唯一辨識的 key）═══════════════════════
def test_sidecar_event_carries_lifecycle_state_key(sandbox_copy, fast_sandbox):
    """🔴 MUT-S4：sidecar 事件**必須**帶 ``lifecycle_state``（少它＝事件被忽略）。"""
    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    sim_now = fast_sandbox["sim_now"]
    nodes = load_elevation_nodes(sandbox_copy)
    quarantined = load_quarantined_node_ids(sandbox_copy)
    edges = CLI._load_edges(sandbox_copy)
    target = CLI.select_supersede_target(nodes, quarantined)
    result = CLI.inject_supersede(
        sandbox_copy, nodes, edges, target, group=0, sim_now=sim_now
    )
    persisted = CLI.persist_supersede(sandbox_copy, result, sim_now=sim_now)
    event = persisted["event"]
    assert "lifecycle_state" in event, "sidecar 事件少了 lifecycle_state ⇒ overlay 會忽略它"
    assert event["lifecycle_state"] == "superseded"
    assert event["event"] == "lifecycle_transition"
    assert event["superseded_by"] == persisted["node"]["node_id"]
    # sidecar 事件的欄位集合必須與 evaluate_lifecycle_all 的事件同形。
    assert {
        "ts", "node_id", "event", "lifecycle_state", "lifecycle_state_before",
        "lifecycle_state_after", "anchor_ts", "reason", "agent_id", "node_type",
    } <= set(event.keys())


# ══ ④ GO 判準不是常數（反假綠）═════════════════════════════════════════
def test_go_criteria_are_not_hardcoded_true(tmp_path):
    """🔴 MUT-S5：**判準未全過**的圖譜必須回 NO-GO（GO 不是常數）。

    用**合成**圖譜（2 顆 dormant、非隔離）⇒ 池 = 2 < 5 ⇒ 判準 ② 不過 ⇒ NO-GO。
    不拿生產圖譜當基準：生產是**活的 append-only 檔**（且目前帶著待裁決的污染），
    用它當基準＝把測試綁在別人會改的狀態上（斷言誤報時修斷言，不改內容迎合斷言）。
    """
    store = tmp_path / "s"
    nodes_dir = store
    nodes_dir.mkdir(parents=True, exist_ok=True)
    node_lines = []
    for i in range(2):
        node_lines.append(json.dumps({
            "node_id": "n%d" % i, "node_type": "value", "content": "合成命題 %d" % i,
            "confidence": 0.5, "stability": 0.0, "valence": "neutral",
            "agent_id": "agent_alice", "parent_node_id": None, "lineage_depth": 0,
            "lineage_path": "n%d" % i, "created_ts": "2026-08-29T00:00:00+00:00",
            "provenance_ref": None, "lifecycle_state": "dormant",
        }, ensure_ascii=False))
    (nodes_dir / "elevation_nodes.jsonl").write_text(
        "\n".join(node_lines) + "\n", encoding="utf-8")

    measured = CLI.measure(store, conversations_dir=PROD_CONV, probes_limit=200)
    assert measured["verdict"]["verdict"] == "NO-GO", (
        "池只有 2 顆（< 5）⇒ 判準應為 NO-GO；若報 GO 就是被寫死成 True 了"
    )
    checks = measured["verdict"]["checks"]
    assert checks["recallable_pool"]["pass"] is False
    assert checks["superseded_in_pool"]["pass"] is False


def test_go_criteria_all_pass_after_fast_forward_and_injection(fast_sandbox, tmp_path):
    """快進 + 注入後，四項判準必須 4/4 PASS（且不是空轉綠）。"""
    if not _prod_available():
        pytest.skip("生產圖譜不存在（此環境無 data/elevation 三檔）")
    sandbox = _clone_sandbox(fast_sandbox["sandbox"], tmp_path / "sandbox")
    sim_now = fast_sandbox["sim_now"]

    from src.inner_life.emergent_projection import load_elevation_nodes
    from src.inner_life.lifecycle_overlay import load_quarantined_node_ids

    nodes = load_elevation_nodes(sandbox)
    quarantined = load_quarantined_node_ids(sandbox)
    edges = CLI._load_edges(sandbox)
    target = CLI.select_supersede_target(nodes, quarantined)
    assert target is not None
    result = CLI.inject_supersede(sandbox, nodes, edges, target, group=0, sim_now=sim_now)
    CLI.persist_supersede(sandbox, result, sim_now=sim_now)

    measured = CLI.measure(sandbox, conversations_dir=PROD_CONV, probes_limit=200)
    verdict = measured["verdict"]
    assert verdict["verdict"] == "GO", "四項判準未全過：%s" % verdict["reasons"]
    for key, check in verdict["checks"].items():
        assert check["pass"] is True, "判準 %s 未過" % key
    assert measured["summary"]["recallable_by_state"].get("superseded", 0) >= 1
    assert measured["hit"]["probes_hit"] > 0, "命中率是 0 —— 這是空轉綠"
