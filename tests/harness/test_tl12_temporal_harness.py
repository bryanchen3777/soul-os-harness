"""TA-2-A — Temporal Context Participation：Gate 1 結構守門。

Owner 工單 (2026-10-03) Gate 1 逐字：
  - TL-12 使用既有 harness infrastructure
  - 沒有建立 duplicate LLM runner
  - SimulationClock 被重用
  - GrowthProbe 被重用
  - existing observer 被重用
  - existing determinism mechanism 被重用
  - production data 0 mutation
  - existing TL experiments 不受影響

另含 Gate 2 的**前置條件**斷言（不要求 LLM 真的產生差異——那是行為觀察，
由 `run_tl12` 對真 LLM 執行；本檔只保證實驗在方法上可歸因）。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import Any, Dict, List

import pytest

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import tl12_temporal as tl12  # noqa: E402

TL12_PATH = REPO / "harness" / "tl12_temporal.py"
OBSERVER_PATH = REPO / "harness" / "observer.py"
PROBE_PATH = REPO / "harness" / "probe.py"
CLOCK_PATH = REPO / "harness" / "clock.py"


# ── Gate 1.1 基礎設施重用（逐字工單條目）────────────────────

def test_tl12_reuses_existing_infrastructure():
    src = TL12_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            if node.level:  # relative import → 記錄被 from 的名稱
                for a in node.names:
                    imports.add(f".{a.name}")
    # SimulationClock / GrowthProbe / Observer / determinism mechanism
    # （相對 import 的 module 是 None，名稱在 aliases 裡）
    for required in (".SimulationClock", ".GrowthProbe", ".Observer"):
        assert required in imports, f"必須重用 {required}（imports={sorted(imports)}）"
    # determinism：必須呼叫既有 derive_determinism
    assert "derive_determinism" in src, "必須重用既有 determinism mechanism"


def test_tl12_does_not_define_duplicate_llm_runner():
    """🔴 不得建立第二套 LLM runner / 第二套 harness。"""
    tree = ast.parse(TL12_PATH.read_text(encoding="utf-8"))
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    # 允許的：TL12Runner（編排器）與 TL12Observation（資料）
    # 禁止：再造 *Runner / *Probe / *Clock / *Observer
    forbidden = {c for c in classes
                 if c.endswith("Probe") or c.endswith("Clock")
                 or c.endswith("Observer") or c == "LLMRunner"}
    assert not forbidden, f"TL-12 不得重新定義 {forbidden}"
    assert "TL12Runner" in classes, "應有 TL12Runner 編排器"


def test_tl12_does_not_modify_observer():
    """observer.py 不得因 TA-2 被改動（它是 observation layer，不是 cognition layer）。"""
    src = OBSERVER_PATH.read_text(encoding="utf-8")
    # observer 不得含任何時間相關的 cognition 邏輯
    for banned in ("temporal_marker", "meeting", "meal", "午餐", "晚餐", "開會"):
        assert banned not in src, f"observer.py 出現 TA-2 相關字串 {banned!r} —— 語意被污染"


def test_tl12_report_layer_is_reporting_only():
    """TL-12 本地報告層必須自稱 reporting-only、不得改寫原文。"""
    src = TL12_PATH.read_text(encoding="utf-8")
    assert '"reporting_only": True' in src
    assert '"rewrites_source": False' in src
    # 🔴 AST 層判定：TL-12 的**程式碼**不得呼叫 derive_interpretation
    #    （字串可能出現在註解裡，故不能用純文字比對）。
    tree = ast.parse(src)
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    } | {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "derive_interpretation" not in called, (
        "TL-12 不得呼叫 derive_interpretation（那是 observer 的職責，"
        "TL-12 只在 observer 產生的 structured evidence 上做比較）"
    )


# ── Gate 1.2 canonical probes 與 contexts（工單 §3 §4）────────

def test_canonical_probes_present():
    ids = {p for p, _ in tl12.TEMPORAL_SENSITIVE_PROBES}
    for required in ("evening_meeting", "soon_meal", "tomorrow_meal", "vague_meeting"):
        assert required in ids, f"缺少 canonical probe {required}"
    assert len(tl12.TEMPORAL_IRRELEVANT_PROBES) >= 1, "必須有 temporal-irrelevant 對照"


def test_at_least_two_temporal_contexts():
    assert len(tl12.TEMPORAL_CONTEXTS) >= 2
    labels = {lbl for lbl, _ in tl12.TEMPORAL_CONTEXTS}
    assert "sat_0900_edt" in labels and "sat_1422_edt" in labels


def test_temporal_lines_are_coarse_grained():
    """工單 §4：不得重新加入精確時間作為主要 interpretation context。"""
    machine = re.compile(r"\d{1,2}:\d{2}|\bEDT\b|\bEST\b|\bUTC\b")
    for label, epoch in tl12.TEMPORAL_CONTEXTS:
        line = tl12.build_temporal_line(epoch)
        assert not machine.search(line), f"{label} 洩漏精確時間: {line!r}"
        assert "週六" in line, f"{label} 應是週六: {line!r}"


# ── Gate 1.3 ON/OFF 唯一差異（工單 §2）────────────────────

@pytest.mark.asyncio
async def test_on_off_differs_by_exactly_the_temporal_line():
    captured: List[str] = []

    async def _cap(messages, agent_id, max_tokens, temperature):
        captured.append(messages[0]["content"])
        return '{"has_motive": false}'

    line = tl12.build_temporal_line(tl12.TEMPORAL_CONTEXTS[0][1])
    on, _ = tl12.make_temporal_llm_call(_cap, line)
    await on([{"role": "user", "content": "PROBE"}], "a", 10, 0.0)
    off, _ = tl12.make_temporal_llm_call(_cap, None)
    await off([{"role": "user", "content": "PROBE"}], "a", 10, 0.0)

    on_msg, off_msg = captured
    assert on_msg != off_msg, "ON 與 OFF 必須不同"
    # OFF 必須是 PROBE 原文
    assert off_msg == "PROBE", "OFF 不得修改任何原文"
    # ON 去掉第一段（temporal 行）後必須與 OFF **逐字元相同**
    assert on_msg.split("\n\n", 1)[1] == off_msg, "ON 除 temporal 行外不得有任何差異"


@pytest.mark.asyncio
async def test_off_arm_never_injects_temporal():
    async def _cap(messages, agent_id, max_tokens, temperature):
        assert "你大概知道現在是" not in messages[0]["content"]
        return '{"has_motive": false}'

    off, _ = tl12.make_temporal_llm_call(_cap, None)
    await off([{"role": "user", "content": "PROBE"}], "a", 10, 0.0)


# ── Gate 1.4 INCONCLUSIVE 是一等公民（工單 §6）─────────────

@pytest.mark.asyncio
async def test_stub_llm_yields_inconclusive_not_pass():
    """stub 回傳固定字串 ⇒ 必須 INCONCLUSIVE，**不得** PASS。"""
    from harness.runner import make_stub_llm_call
    from harness.tl12_temporal import run_tl12

    stub = make_stub_llm_call({"interpretation": ['{"has_motive": false}'] * 300})
    res = await run_tl12(stub)
    assert res["verdict"] == "INCONCLUSIVE", f"應為 INCONCLUSIVE，實得 {res['verdict']}"
    assert res["inconclusive_reasons"], "INCONCLUSIVE 必須給出理由"


@pytest.mark.asyncio
async def test_on_and_off_call_counts_are_symmetric():
    """ON/OFF 呼叫次數必須對稱（除 temporal 行外不得改變任何行為）。"""
    from harness.runner import make_stub_llm_call
    from harness.tl12_temporal import run_tl12

    stub = make_stub_llm_call({"interpretation": ['{"has_motive": false}'] * 300})
    res = await run_tl12(stub)
    calls = res["n_llm_calls_by_arm"]
    assert calls["ON"] == calls["OFF"], f"ON/OFF 呼叫次數不對稱: {calls}"


# ── Gate 1.5 production 0 mutation（工單 §Production safety）───

@pytest.mark.asyncio
async def test_run_tl12_does_not_touch_production_data_dir():
    """TL-12 必須在 isolated data_root 內跑，結束後 data_root 復原。"""
    import os

    from src.paths import data_root
    from harness.runner import make_stub_llm_call
    from harness.tl12_temporal import run_tl12

    prod_before = data_root().resolve()
    stub = make_stub_llm_call({"interpretation": ['{"has_motive": false}'] * 300})
    await run_tl12(stub)
    prod_after = data_root().resolve()
    assert prod_before == prod_after, "run_tl12 結束後 data_root 必須復原"
    assert "SOUL_OS_DATA_DIR" not in os.environ or os.environ.get(
        "SOUL_OS_DATA_DIR"
    ) == str(prod_before)


def test_echo_is_not_inference():
    """🔴 Owner 2026-10-03 警示：措辭差異 ≠ 理解差異。

    複述注入行裡已有的詞（「下午」）**不算**推理；
    必須推理才能產生的詞（「午餐」——stimulus 與注入行都沒有）才算。
    """
    stimulus = "等一下去吃飯"
    line = tl12.build_temporal_line(tl12.TEMPORAL_CONTEXTS[0][1])  # 週六早上

    # 1) 回音：輸出重複了注入行已有的「早上」→ 不是推理
    echo = f"現在是早上，我現在沒辦法去。{stimulus}"
    assert tl12._inference_markers(echo, stimulus, line) == [], (
        "複述注入行的詞不得算作推理"
    )

    # 2) 真推理：「午餐」不在 stimulus 也不在注入行 → 必須經過推理（判斷是午餐非晚餐）
    infer = f"那我建議吃午餐。{stimulus}"
    hits = tl12._inference_markers(infer, stimulus, line)
    assert "午餐" in hits, f"『午餐』應被判定為推理：{hits}"

    # 3) 複述 stimulus 裡已有的詞也不算（「等一下」本來就在 stimulus）
    echo2 = f"那就等一下再說。{stimulus}"
    assert "等一下" not in tl12._inference_markers(echo2, stimulus, line)


def test_report_records_both_marker_and_inference():
    # 改用 14:22 context（注入行是「下午」），輸出裡的「早上」就**不在**注入行
    line = tl12.build_temporal_line(tl12.TEMPORAL_CONTEXTS[1][1])
    obs = tl12.TL12Observation(
        run_id="r", context_label="sat_1422_edt", probe_id="soon_meal",
        probe_kind="sensitive", arm="ON", checkpoint="D0", sim_ts="t",
        temporal_line=line, stimulus="等一下去吃飯",
        # 「早上」不在 stimulus 也不在「下午…」行 → 仍是推理；
        # 「午餐」同理。兩者都應進 inference。
        emergent_snapshot='{"has_motive": true, "content": "早上我沒吃午餐，現在可以嗎"}',
        motive_text="早上我沒吃午餐，現在可以嗎", decision_text="", reached_action=False,
    )
    rep = tl12._report_for(obs)
    assert rep["reporting_only"] is True
    assert rep["rewrites_source"] is False
    # stimulus「等一下去吃飯」＋注入行「週六。下午，約兩點半。今天已經過了一大段。」都沒有
    # 「早上」與「午餐」⇒ 兩者都是必須推理才能產生的（推論當下適不適合午餐）
    assert "早上" in rep["temporal_inference_hits"]
    assert "午餐" in rep["temporal_inference_hits"]
    # 「下午」在注入行裡 → 若輸出提到它，只能是回音，不得算推理
    obs2 = tl12.TL12Observation(
        run_id="r", context_label="sat_1422_edt", probe_id="soon_meal",
        probe_kind="sensitive", arm="ON", checkpoint="D0", sim_ts="t",
        temporal_line=line, stimulus="等一下去吃飯",
        emergent_snapshot='{"has_motive": true, "content": "下午再說"}',
        motive_text="下午再說", decision_text="", reached_action=False,
    )
    rep2 = tl12._report_for(obs2)
    assert "下午" not in rep2["temporal_inference_hits"], "複述注入行不得算推理"


def test_observer_change_verdict_untouched():
    """TL-12 不得改 Observer 的既有判定語意。"""
    src = OBSERVER_PATH.read_text(encoding="utf-8")
    for token in ("NO_CHANGE", "SURFACE_ONLY", "INTERPRETATION_DECISION_CHANGED",
                  "FULL_TRACEABLE"):
        assert token in src, f"Observer 既有 Level 枚舉 {token} 消失"
