"""TA-2 Gate 2 — corrected behavioral validation（四臂 counterfactual design）。

Owner 2026-10-03 逐字：
  「TA-2 比 TL-1 多一個關鍵 requirement：stability ＋ counterfactual attribution」
  「必須保留：Temporal ON / Temporal OFF / ON-correct / ON-mismatched
    ＋ irrelevant control」

設計理由
--------
單純 ON vs OFF 的差異，可能只是「時間被提到了」這種**表面效應（echo）**。
而 **ON-correct vs ON-mismatched 必須產生不同結果**，才證明**時間的「值」本身
參與了推理**，而不只是時間的存在被複述。

這與 `tl12_temporal._inference_markers()` 的 echo/inference 區分**直接銜接**：
我們已定義「複述注入行裡已有的詞」不算推理證據；`ON-mismatched` 是這個區分的
**行為層版本**。

四臂
----
  OFF            無時間 context
  ON-correct     時間與情境相符（早上 ＋「去吃飯」）
  ON-mismatched  時間與情境矛盾（凌晨 3 點 ＋「吃晚飯」）
  IRRELEVANT     時間存在但 stimulus 與時間無關（雜訊地板）

判定
----
1. 穩定性：同一臂 N 次 run 的 observation 層須一致（沿用 P2 契約）
2. 歸因性：ON-correct vs ON-mismatched 須有差異 ⇒ 時間**值**參與推理
3. 對照組：irrelevant 臂的 |delta| 須低於效應量 ⇒ 排除自然變動雜訊

**INCONCLUSIVE 是一等公民**（P2 §6），不得為了 PASS 調參。
"""
from __future__ import annotations

import asyncio
import collections
import json
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.timezone_utils import LOCAL_TZ, coarse_now_line  # noqa: E402
from harness.tl12_temporal import _inference_markers  # noqa: E402

# ────────────────────────────────────────────────────────────
# 時間臂：每個 scenario 配「相符」與「矛盾」兩個時點
# ────────────────────────────────────────────────────────────
# epoch_iso = 該時點（UTC）。2026-10-03 是週六。
_EDT = timezone(timedelta(hours=-4))


def _sat(hour: int, minute: int = 0) -> str:
    """週六某時點的 UTC ISO 字串（EDT = UTC-4）。"""
    local = datetime(2026, 10, 3, hour, minute, tzinfo=_EDT)
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


#: (probe_id, stimulus, 與情境「相符」的時點, 與情境「矛盾」的時點, 為何相容/矛盾)
SCENARIOS: Tuple[Tuple[str, str, str, str, str], ...] = (
    (
        "meal_timing", "等一下去吃飯", _sat(11, 30), _sat(3, 10),
        "上午 11:30 吃飯＝午餐合理；凌晨 03:10 說「吃飯」語境不成立",
    ),
    (
        "meeting_timing", "晚上一起開會吧", _sat(19, 0), _sat(6, 20),
        "傍晚 19:00 開晚會合理；清晨 06:20 說「晚上開會」語境不成立",
    ),
    (
        "night_rest", "我該睡覺了", _sat(23, 30), _sat(11, 0),
        "深夜 23:30 就寢合理；上午 11:00 說「該睡覺了」語境不成立",
    ),
)

#: 時間無關的自然變動對照（雜訊地板）
IRRELEVANT_PROBE = "我覺得你那件外套的顏色很好看。"

N_RUNS = 6  # 2026-10-03 16:15 Owner 批准 3→6（契約 §5.1 修正記錄；門檻/規則表/probe 不動）
STUB_OR_LLM: str = "stub"


class RealLLM:
    """真實 LLM 適配器（與 harness 的 `make_real_llm_call` 同一路徑）。"""

    def __init__(self) -> None:
        from configs.loader import load_config
        from harness.runner import make_real_llm_call

        cfg = load_config()
        self.model = cfg.get("llm", {}).get("model", "unknown")
        self._call = make_real_llm_call(model=self.model, temperature=0.0)

    def __call__(self, messages, agent_id):
        # 🔴 `make_real_llm_call` 回傳的 `llm_call` 簽章是
        #    `(messages, agent_id, max_tokens, temperature)` —— 四個位置參數。
        #    （初版漏掉後兩者 → TypeError。）
        return self._call(messages, agent_id, 512, 0.0)


def _make_llm(real: bool):
    """回 (llm, label)。label 明確標示本輪用的是 stub 還是真實 LLM。"""
    if real:
        return RealLLM(), "real"
    return stub_llm, "stub"


_HEADER = "（你大概知道現在是：{line}）"


def _prompt(stimulus: str, temporal_line: Optional[str]) -> str:
    if temporal_line is None:
        return stimulus
    return _HEADER.format(line=temporal_line) + "\n\n" + stimulus


def _line(epoch_iso: str) -> str:
    return coarse_now_line(datetime.fromisoformat(epoch_iso.replace("Z", "+00:00")).astimezone(LOCAL_TZ))


_TIME_CLASSES: Tuple[str, ...] = (
    "凌晨", "清早", "早上", "上午", "中午", "下午", "傍晚", "晚上", "深夜",
)

#: stimulus 關鍵詞 → 它隱含的「可接受時段」集合（空集合＝該 stimulus 不需要時間推理）
_EXPECTED: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("午飯", ("中午", "下午")),
    ("晚飯", ("傍晚", "晚上")),
    ("吃飯", ("中午", "下午")),          # 模糊：可午餐可晚餐
    ("睡", ("深夜", "晚上", "凌晨")),
    ("開會", ("傍晚", "晚上", "下午")),
    ("吃晚飯", ("傍晚", "晚上")),
)


def _time_class_of(line: str) -> Optional[str]:
    """🔴 只從**注入的時間行**抽時段類別 —— 絕不看 stimulus。

    （初版缺陷：在整段 prompt 上分支，導致 stimulus 自帶的「晚上」一詞
      就觸發 evening 分支 ⇒ 無論注入什麼時間都給同一答案 = **echo 而非推理**。）
    """
    for k in _TIME_CLASSES:
        if k in line:
            return k
    return None


def _expected_classes(stimulus: str) -> Tuple[str, ...]:
    """從 stimulus 抽它隱含的可接受時段集合；空 ⇒ 該 stimulus 不涉時間推理。"""
    for key, allowed in _EXPECTED:
        if key in stimulus:
            return allowed
    return ()


def stub_llm(messages, agent_id):
    """**最小時間推理器**（deterministic）。

    邏輯：把「stimulus 隱含的可接受時段」與「注入時間行的實際時段」比對——
      相容 → 安排；不��容 → 察覺語境不成立；stimulus 不涉時間 → 不看時間。
    🔴 這是 stub：它**證明四臂設計可產生歸因**，**不證明真實 Soul 的時間認知**。
    """
    text = messages[-1].get("content", "")
    if "\n\n" in text:
        header, _, stimulus = text.partition("\n\n")
    else:
        header, stimulus = "", text
    line = header.split("：", 1)[1].rstrip("）") if "：" in header else ""

    now = _time_class_of(line)
    expected = _expected_classes(stimulus)

    if not expected or now is None:
        return json.dumps(
            {"has_motive": True, "content": "好，我知道了。", "decision": "ack"},
            ensure_ascii=False,
        )
    if now in expected:
        return json.dumps(
            {"has_motive": True,
             "content": f"那就{now}吧，我這邊可以。",
             "decision": f"schedule_{now}"},
            ensure_ascii=False,
        )
    return json.dumps(
        {"has_motive": True,
         "content": f"現在是{now}，但這件事聽起來是{'／'.join(expected)}才對，"
                    f"是不是改個時間？",
         "decision": "flag_incongruent"},
        ensure_ascii=False,
    )


def _analyse(text: str, stimulus: str, temporal_line: str) -> Dict[str, Any]:
    """報告層：抽出 observation 錨點 + echo/inference 區分（不改寫原文）。"""
    return {
        "decision": (text or "").strip()[:80],
        "inference_markers": _inference_markers(text or "", stimulus, temporal_line),
        "has_motive": "motive" in (text or "").lower() or bool(text),
    }


# ══════════════════════════════════════════════════════════════
# 🔴 以下量測層完全依 `docs/TA2-GATE2-MEASUREMENT-CONTRACT.md` 實作。
#    **契約於重跑前預登記；此處不得因結果而調整規則表、距離函式或門檻。**
# ══════════════════════════════════════════════════════════════

# §2.1 temporal_frame 提取規則（**順序敏感**，契約表逐字實作；
#      **不看實驗臂、不看 stimulus、不看注入時間**，只映射回應文本本身）
_FRAME_RULES: Tuple[Tuple[str, Tuple[str, ...], bool], ...] = (
    # (frame 名, 觸發詞, 是否需要「且不含排除詞」)
    ("siesta", ("補眠", "午睡", "午安"), False),
    ("late_night_meal", ("宵夜",), False),
    ("early_breakfast", ("早餐",), True),      # 且不含「宵夜」
    ("lunch", ("午餐", "中飯"), False),
    ("dinner", ("晚餐", "晚飯"), False),
    ("night", ("夜", "睡", "就寢"), True),      # 且不含「補眠」
    ("unframed", (), False),
)

_EXCLUDE_FOR: Dict[str, Tuple[str, ...]] = {
    "early_breakfast": ("宵夜",),
    "night": ("補眠",),
}


def extract_temporal_frame(text: str) -> str:
    """契約 §2.1。**只**依回應文本判定事件的時段歸屬。"""
    t = text or ""
    for frame, keys, exclusive in _FRAME_RULES:
        if not keys:
            return frame
        if not any(k in t for k in keys):
            continue
        if exclusive and any(bad in t for bad in _EXCLUDE_FOR.get(frame, ())):
            continue
        return frame
    return "unframed"


# §3 距離函式：categorical mismatch
def d(a: str, b: str) -> int:  # noqa: E743
    return 0 if a == b else 1


# ══════════════════════════════════════════════════════════════
# TA-2-MEAS-AUDIT-1（2026-10-03 21:15，Owner 批准）
#   目的：**恢復 evidence observability**，不是改變 measurement semantics。
#   本票唯一改動：每筆 observation 保存 **raw response text** ＋ version id。
#   🔴 明文凍結（v1 契約全部不動）：temporal_frame 規則表、mentions_time、
#      inference_markers、距離函式、ΔR/ΔI/DiD、門檻、probe、時點、N=6。
#   🔴 不得因看到 outlier 就修 extractor；若發現缺陷，**記錄 finding 不修**。
# ══════════════════════════════════════════════════════════════
MEASUREMENT_VERSION = "TA-2-GATE2-v1+frozen(TA-2-MEAS-AUDIT-1 raw-evidence-restore)"
RUN_ID = f"{MEASUREMENT_VERSION} N={N_RUNS}"


def _obs_from_text(text: str, stimulus: str, temporal_line: str) -> Dict[str, Any]:
    """契約 §2.2。primary = temporal_frame；secondary 僅記錄。

    🔴 TA-2-MEAS-AUDIT-1：**保存 `raw_text`**，使每個 frame 可回溯到原始回應。
       這是 evidence persistence，**不改變任何分類規則**。
    """
    from harness.tl12_temporal import _inference_markers, _TEMPORAL_MARKERS

    return {
        # 🔴 evidence preservation（本次唯一新增）
        "raw_text": text,
        # §2.1 primary
        "temporal_frame": extract_temporal_frame(text),
        # §2.2 secondary（不參與判定）
        "mentions_time": any(m in (text or "") for m in _TEMPORAL_MARKERS),
        "inference_markers": _inference_markers(text or "", stimulus, temporal_line),
        "raw_len": len(text or ""),
        "measurement_version": MEASUREMENT_VERSION,
    }


def _arm_frames(arm: Dict[str, Any]) -> List[str]:
    return [o["temporal_frame"] for o in arm["observations"]]


async def run_arm(stimulus: str, epoch_iso: Optional[str], n_runs: int, llm,
                  probe_id: str = "", arm: str = "") -> Dict[str, Any]:
    """跑單一臂 N 次，回 observation 錨點序列（含 raw 原文，可逐筆回溯）。"""
    line = _line(epoch_iso) if epoch_iso else ""
    prompt_text = _prompt(stimulus, line or None)
    outs = []
    for idx in range(n_runs):
        raw = llm([{"role": "user", "content": prompt_text}], "agent_ruka")
        if hasattr(raw, "__await__"):
            raw = await raw
        obs = _obs_from_text(_extract_text(raw), stimulus, line)
        # 🔴 audit traceability：每筆 observation 可唯一對應回該次 response
        obs["probe_id"] = probe_id
        obs["arm"] = arm
        obs["run_index"] = idx
        obs["stimulus"] = stimulus
        obs["injected_temporal_line"] = line
        outs.append(obs)
    frames = [o["temporal_frame"] for o in outs]
    return {
        "stimulus": stimulus,
        "epoch_iso": epoch_iso,
        "temporal_line": line,
        "n_runs": n_runs,
        "probe_id": probe_id,
        "arm": arm,
        "observations": outs,
        "temporal_frames": frames,
        "observation_stable": len(set(frames)) == 1,   # 契約 §4 必要條件 3
    }


def _extract_text(raw: Any) -> str:
    """真實 LLM 回傳可能是 dict 或 JSON 字串；統一取出可讀內容。"""
    if isinstance(raw, dict):
        return str(raw.get("content") or raw.get("decision") or raw)
    s = str(raw or "")
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            return str(j.get("content") or j.get("decision") or s)
    except json.JSONDecodeError:
        pass
    return s


async def main() -> int:
    real = "--real" in sys.argv
    llm, STUB_OR_LLM = _make_llm(real)
    results: Dict[str, Any] = {}
    print("=" * 78)
    print("TA-2 Gate 2 — 四臂 counterfactual behavioral validation")
    print("=" * 78)
    print(f"  LLM: {STUB_OR_LLM}   N per arm: {N_RUNS}")
    if real:
        print(f"  model: {getattr(llm, 'model', 'unknown')}  temperature=0.0")
    print()

    for probe_id, stimulus, ok_iso, bad_iso, why in SCENARIOS:
        print(f"── {probe_id}：「{stimulus}」")
        print(f"   {why}")
        off = await run_arm(stimulus, None, N_RUNS, llm, probe_id, 'OFF')
        on_ok = await run_arm(stimulus, ok_iso, N_RUNS, llm, probe_id, 'ON_correct')
        on_bad = await run_arm(stimulus, bad_iso, N_RUNS, llm, probe_id, 'ON_mismatched')
        results[probe_id] = {
            "stimulus": stimulus, "rationale": why,
            "OFF": off, "ON_correct": on_ok, "ON_mismatched": on_bad,
        }
        print(f"   OFF           : {off['temporal_frames']}")
        print(f"   ON-correct    : {on_ok['temporal_line']}")
        print(f"                  {on_ok['temporal_frames']}")
        print(f"   ON-mismatched : {on_bad['temporal_line']}")
        print(f"                  {on_bad['temporal_frames']}")
        print()

    # 對照組（雜訊地板）
    ctrl = await run_arm(IRRELEVANT_PROBE, _sat(19, 0), N_RUNS, llm, 'irrelevant', 'ON')
    ctrl_off = await run_arm(IRRELEVANT_PROBE, None, N_RUNS, llm, 'irrelevant', 'OFF')
    results["_control"] = {"ON": ctrl, "OFF": ctrl_off,
                           "stimulus": IRRELEVANT_PROBE}
    print(f"── irrelevant control：「{IRRELEVANT_PROBE}」")
    print(f"   ON : {ctrl['temporal_frames']}")
    print(f"   OFF: {ctrl_off['temporal_frames']}")

    # ══════════════════════════════════════════════════════════
    # 判定 —— 嚴格依契約 §4（**門檻已預登記，不得調整**）
    # ══════════════════════════════════════════════════════════
    verdict_reasons: List[str] = []

    def _mode(frames: List[str]) -> str:
        """多數決（N=6 時 first-run 單一樣本有偏；此為樣本統計，非規則變更）。"""
        return collections.Counter(frames).most_common(1)[0][0] if frames else "unframed"

    # ΔR = relevant probes 的 arm 距離（d(correct_mode, mismatched_mode)）
    per_probe: Dict[str, int] = {}
    for pid, r in results.items():
        if pid == "_control":
            continue
        fc = r["ON_correct"]["temporal_frames"]
        fm = r["ON_mismatched"]["temporal_frames"]
        # 必要條件 3：同一 probe 兩臂各自 observation 穩定
        if not r["ON_correct"]["observation_stable"]:
            verdict_reasons.append(
                f"{pid}：ON-correct 的 temporal_frame 跨 run 不一致（{fc}）"
            )
        if not r["ON_mismatched"]["observation_stable"]:
            verdict_reasons.append(
                f"{pid}：ON-mismatched 的 temporal_frame 跨 run 不一致（{fm}）"
            )
        per_probe[pid] = d(_mode(fc), _mode(fm))
    dR = max(per_probe.values()) if per_probe else 0

    # ΔI = irrelevant control 的 arm 距離（d(ON, OFF)）
    ctrl_frames_on = ctrl["temporal_frames"]
    ctrl_frames_off = ctrl_off["temporal_frames"]
    dI = d(_mode(ctrl_frames_on), _mode(ctrl_frames_off))

    did = dR - dI

    c1 = dR >= 1
    c2 = did >= 0.5
    c3 = not any("跨 run 不一致" in r for r in verdict_reasons)

    print()
    print("=" * 78)
    print("逐筆 audit evidence（TA-2-MEAS-AUDIT-1）")
    print("=" * 78)
    print(f"  version: {MEASUREMENT_VERSION}")
    print(f"  N per arm: {N_RUNS}")
    print()
    # 每一筆 observation 都列出 raw 原文，讓 frame 可逐筆回溯。
    for pid, r in results.items():
        arms = (["ON_correct", "ON_mismatched", "OFF"] if pid != "_control"
                else ["ON", "OFF"])
        print(f"  ── probe: {pid}  「{r['ON_correct']['stimulus'] if pid != '_control' else IRRELEVANT_PROBE}」")
        for an in arms:
            a = r.get(an)
            if not a:
                continue
            print(f"     arm={an}  注入行: {a['temporal_line'] or '(無)'}")
            for o in a["observations"]:
                mark = ""
                if not (a["observation_stable"] and
                        o["temporal_frame"] == a["temporal_frames"][0]):
                    mark = "   ◀◀ 離群"
                print(f"       run{o['run_index']}  frame={o['temporal_frame']:<16} "
                      f"mentions_time={str(o['mentions_time']):<5} "
                      f"inference={o['inference_markers']}{mark}")
                print(f"             raw: {o['raw_text'][:150]}")
        print()

    print("=" * 78)
    print("判定（依預登記契約 §4）")
    print("=" * 78)
    # 🔴 Owner 要求：逐 probe / 逐臂列出**完整 frame 序列**，
    #    以便判斷「是哪一個 probe 有較高 LLM variance」，
    #    還是「整個 temporal interpretation layer 都不穩定」。
    print("  ── 完整 temporal_frame 序列（每臂 N=%d）──" % N_RUNS)
    for pid, r in results.items():
        if pid == "_control":
            continue
        print(f"  {pid}")
        print(f"      correct   : {r['ON_correct']['temporal_frames']}")
        print(f"      mismatched: {r['ON_mismatched']['temporal_frames']}")
    print(f"  irrelevant")
    print(f"      ON        : {ctrl_frames_on}")
    print(f"      OFF       : {ctrl_frames_off}")
    print()
    for pid, v in per_probe.items():
        fc_m = _mode(results[pid]["ON_correct"]["temporal_frames"])
        fm_m = _mode(results[pid]["ON_mismatched"]["temporal_frames"])
        print(f"  {pid:<18} frame(correct)={fc_m:<16} frame(mismatch)={fm_m:<16} d={v}")
    print(f"  {'irrelevant':<18} frame(ON)={_mode(ctrl_frames_on):<16} "
          f"frame(OFF)={_mode(ctrl_frames_off):<16} d={dI}")
    print()
    print(f"  ΔR (relevant arm 距離)      = {dR}")
    print(f"  ΔI (基礎染色量 baseline)    = {dI}")
    print(f"  ΔR − ΔI (temporal-specific) = {did}")
    print()
    print(f"  條件1  ΔR ≥ 1              : {'✅' if c1 else '❌'}")
    print(f"  條件2  ΔR − ΔI ≥ 0.5       : {'✅' if c2 else '❌'}")
    print(f"  條件3  observation 跨 run 穩定: {'✅' if c3 else '❌'}")
    for r in verdict_reasons:
        print(f"     - {r}")

    verdict = "PASS" if (c1 and c2 and c3) else "INCONCLUSIVE"
    print()
    print(f"  最終判定 : {verdict}")
    if dR == dI and dR > 0:
        print()
        print("  ⛔ §7 停止條件觸發：ΔR ≈ ΔI ⇒ 真實 LLM **普遍**被 temporal context 染色，")
        print("     此結果**不得**用來證明 TA-2。")

    print()
    if STUB_OR_LLM == "stub":
        print("  ⚠️ 本判定基於 **stub LLM**（結構自證）。真實 LLM 版須另跑；")
        print("     本結果**不可**宣稱 Soul 的時間認知已參與推理。")
    else:
        print("  本判定基於 **真實 LLM**（契約 §4 預登記門檻）。")
    print()
    print("  ⚠️ 本判定基於 **stub LLM**（Gate 2 結構自證 + 方法論驗證）。")
    print("     真實 LLM 下的同一四臂設計須另跑；本結果**不可**直接")
    print("     宣稱 Soul 的時間認知已參與推理。")

    out_dir = REPO / "data" / "harness_out" / "tl12"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "ta2a_gate2_four_arm_stub.json"
    out = out_dir / (
        "ta2a_gate2_four_arm_stub.json" if STUB_OR_LLM == "stub"
        else "ta2a_gate2_four_arm_real.json"
    )
    out.write_text(
        json.dumps({"verdict": verdict,
                    "contract": "docs/TA2-GATE2-MEASUREMENT-CONTRACT.md",
                    "reasons": verdict_reasons,
                    "dR_per_probe": per_probe,
                    "dR": dR, "dI": dI, "did": did,
                    "conditions": {"c1_dR_ge_1": c1, "c2_did_ge_0_5": c2,
                                   "c3_observation_stable": c3},
                    "llm": STUB_OR_LLM, "n_runs": N_RUNS,
                    "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"  結果已寫: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
