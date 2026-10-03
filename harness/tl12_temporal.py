"""
harness/tl12_temporal.py — TA-2-A Temporal Context Participation (Owner 2026-10-03)

[SOUL OS WORK ORDER] Ticket: TA-2-A — Temporal Context Participation. Mode: IMPLEMENTATION.

核心定義（Owner 逐字）
----------------------
  "Time is not merely available as metadata. Temporal context must be capable of
   changing Soul's interpretation of temporally relevant events, including
   representing temporal uncertainty when the available temporal information
   is insufficient."

  "時間感知不是知道現在幾點，而是時間會改變 Soul 對正在發生之事的理解。"

核心判準（Owner 逐字）
--------------------
  移除 temporal context 後，若 Soul 對時間敏感事件的 interpretation 完全不變，
  TA-2-A 不成立。

  反過來：時間無關事件在加入／移除 temporal context 後，**不應被任意改變**。
  ⇒ 本實驗驗的是 **difference-in-difference**，不是單純 diff。

實驗設計
--------
  same soul / same probe / same model / same execution path / same time point
        ┌── Temporal ON   (temporal context 行存在)
        │
        └── Temporal OFF  (**只**移除 temporal context 行)

注入點
------
  **唯一注入點是 `llm_call` 的一層包裝**。GrowthProbe / SimulationClock /
  Observer / parse_decision_enum / determinism 機制**全部原樣重用**，
  沒有第二套 harness。ON 與 OFF 共用**同一個 GrowthProbe 類別實例化流程**
  與同一個 inner llm_call，唯一差別是 messages 裡多／少那一行。

  為什麼在 llm_call 層注入（Owner 工單要求「不能修改其他 prompt/context」）：
  GrowthProbe._interpretation_prompt() 產生的 prompt **本來就不含任何時間上下文**
  （只有 stimulus + 經歷上下文）。所以 ON/OFF 的差異**只可能**來自我們加的那一行。

邊界（Owner 工單）
----------------
  - **不改** `harness/observer.py`。`derive_interpretation()` 維持
    observation/reporting layer 身分，**不得**被偷偷改成時間 classifier。
    本模組的時間標記分析是 **TL-12 本地的報告層**，獨立於 observer，
    標記為 reporting-only、永不改寫原文。
  - **不做** meeting/meal keyword 觸發 rule engine（「提醒 Soul 現在幾點」被否決）。
  - **不做** TA-2-B temporal curiosity、不改 Agency、不加新 LLM abstraction。
  - **STRICT 0 PRODUCTION MUTATION**（§4.3）：derived 觀測永不回寫 canonical store。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from src.paths import data_root, reset_data_root
from src.timezone_utils import LOCAL_TZ, coarse_now_line

from .clock import DEFAULT_EPOCH_ISO, SimulationClock
from .observer import Observer, parse_decision_enum
from .probe import GrowthProbe

logger = logging.getLogger("soul_os.harness.tl12")

TL12_EXPERIMENT_ID = "TA-2-A"
TL12_SOUL_ID = "agent_ruka"
TL12_RUNS = 3  # 沿用 §5 determinism 的 3-run

# ────────────────────────────────────────────────────────────
# Canonical probes（Owner 逐字指定 + 對照組）
# ────────────────────────────────────────────────────────────

#: 時間敏感 probes：ON/OFF 應出現可歸因於時間的差異
TEMPORAL_SENSITIVE_PROBES: Tuple[Tuple[str, str], ...] = (
    ("evening_meeting", "晚上一起開會吧"),
    ("soon_meal", "等一下去吃飯"),
    ("tomorrow_meal", "明天一起吃飯？"),
    ("vague_meeting", "等等開會"),
)

#: 時間無關對照 probes：ON/OFF **不應**出現同等程度的系統性差異
#: （用來區分「時間造成的差異」vs「重跑 LLM 的自然變動」）
TEMPORAL_IRRELEVANT_PROBES: Tuple[Tuple[str, str], ...] = (
    ("non_temporal_opinion", "我覺得你那件外套的顏色很好看。"),
)

#: 至少兩個 temporal contexts（Owner 工單要求）
#: epoch 固定為 **2026-10-03（週六）**，本地時間分別 09:00 EDT 與 14:22 EDT。
TEMPORAL_CONTEXTS: Tuple[Tuple[str, str], ...] = (
    # (label, epoch_iso)  — epoch 本身即該 context 的起始時點
    ("sat_0900_edt", "2026-10-03T13:00:00+00:00"),   # 週六 09:00 EDT
    ("sat_1422_edt", "2026-10-03T18:22:00+00:00"),   # 週六 14:22 EDT
)

# ── TL-12 本地報告層的時間標記（reporting-only，永不改寫原文）──
# 🔴 這**不是** classifier，也**不是** cognition layer。它只是把原文裡
#    「有沒有把時間放進推理」記成可比對的標記，供 difference-in-difference 使用。
#    它的可信度由 **control group** 決定：若 control probe 也同樣觸發，
#    代表它量到的是雜訊而非時間效果 → 判 INCONCLUSIVE。
_TEMPORAL_MARKERS: Tuple[str, ...] = (
    # 單獨時段詞（🔴 必須收錄，否則 echo 規則無法自動把它們判為回音）
    "凌晨", "清早", "早上", "上午", "中午", "下午", "傍晚", "晚上", "深夜",
    "半夜", "午夜", "現在", "今天", "明天", "昨天", "今晚", "昨晚",
    # 相對時間錨
    "剛剛", "剛過", "待會", "等一下", "等會", "一陣子",
    # 餐飲時段分類（「午餐還是晚飯」那類推理的**結果**）
    "午餐", "中餐", "晚餐", "晚飯", "早餐", "早午餐", "下午茶", "宵夜",
    # 一天的段位
    "這一天", "一天", "一天已經", "已經過了", "快走完", "快收尾",
    "週末", "週六", "週日", "週一", "週二", "週三", "週四", "週五",
    # 未定／不確定（「等等開會」該有的狀態）
    "不確定", "還不知道", "不知道", "沒說", "沒定", "未定", "幾點", "再確認",
)


def _has_temporal_marker(text: str) -> bool:
    """報告層標記：原文是否含任何時間推理痕跡。**不改寫原文**。"""
    if not text:
        return False
    return any(m in text for m in _TEMPORAL_MARKERS)


def _inference_markers(
    text: str, stimulus: str, temporal_line: str
) -> List[str]:
    """🔴 判定「真正的時間推理」而非「文字回音」（Owner 2026-10-03 警示）。

    Owner 原話：**「不要因為看到 ON/OFF 有文字差異就直接 PASS。我們真正要回答的是
    ——時間是否改變了 Soul 對事情的理解？而不是——時間是否讓 Soul 說出了不同的句子？」**

    區分準則：**一個 marker 若其字面已存在於 stimulus 或注入的時間行中，
    它只是被複述（echo），不構成推理證據。**

      · 輸出「下午」——「下午」已在注入行裡 ⇒ **回音，非證據**
      · 輸出「午餐」——「午餐」不在 stimulus（「等一下去吃飯」）、
        也不在注入行（「週六。早上，剛過九點」）⇒ **必須經過推理
        （判斷這是午餐而非晚餐），是真證據**

    這是 TA-2-A 區分「理解改變」與「措辭改變」的核心規則。
    """
    if not text:
        return []
    # 🔴 回音基準必須含**整段被注入的文字**（模板框 + 時間行），
    #    否則「你大概知道現在是：…」裡的「現在」會被誤判為推理。
    echoable = f"{stimulus} {_TEMPORAL_HEADER} {temporal_line}"
    return sorted(
        {
            m for m in _TEMPORAL_MARKERS
            if m in text and m not in echoable
        }
    )


# ────────────────────────────────────────────────────────────
# Ablation wrapper —— 唯一注入點
# ────────────────────────────────────────────────────────────

_TEMPORAL_HEADER = "（你大概知道現在是：{line}）"


def build_temporal_line(epoch_iso: str) -> str:
    """由 deterministic clock 產生 **coarse-grained** 時間行（TIME-PERCEPTION-1）。

    🔴 必須是粗顆粒：`src.timezone_utils.coarse_now_line()` 產出
    「週六。下午，約兩點半。今天已經過了一大段。」
    **不得**重新引入精確時間作為主要 interpretation context
    （Owner 工單第 4 點；且精確時間會殺掉推理空間——見 Owner
    「粗顆粒不是少資訊，是保留足夠資訊讓 Soul 可以推理」）。

    不用「請思考時間」之類 meta-instruction（那是被否決的 rule engine）。
    """
    dt_utc = datetime.fromisoformat(epoch_iso.replace("Z", "+00:00"))
    local = dt_utc.astimezone(LOCAL_TZ)
    return coarse_now_line(local)


def make_temporal_llm_call(
    inner: Callable[..., Awaitable[Optional[str]]],
    temporal_line: Optional[str],
) -> Tuple[Callable[..., Awaitable[Optional[str]]], List[Dict[str, Any]]]:
    """包裝既有 llm_call：ON 加一行 temporal context，OFF 完全不加。

    Returns: (wrapped_llm_call, calls_log)

    🔴 除這一行的增刪外，**不修改任何其他 prompt/context**。ON 與 OFF 共用同一個
    `inner`，故 model / temperature / backend / 呼叫次數完全一致。
    """
    calls_log: List[Dict[str, Any]] = []

    async def llm_call(
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        sent = messages
        if temporal_line:
            sent = []
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content", "")
                    sent.append(
                        {
                            "role": "user",
                            "content": (
                                _TEMPORAL_HEADER.format(line=temporal_line) + "\n\n" + content
                            ),
                        }
                    )
                else:
                    sent.append(dict(m))
        raw = await inner(sent, agent_id, max_tokens, temperature)
        calls_log.append(
            {"messages": sent, "raw": raw, "max_tokens": max_tokens,
             "temperature": temperature, "temporal_on": bool(temporal_line)}
        )
        return raw

    return llm_call, calls_log


# ────────────────────────────────────────────────────────────
# Experiment records
# ────────────────────────────────────────────────────────────

@dataclass
class TL12Observation:
    """一次 probe 的觀測（canonical evidence = 原文照存 + TL-12 本地報告標記）。"""

    run_id: str
    context_label: str          # sat_0900_edt / sat_1422_edt
    probe_id: str
    probe_kind: str             # sensitive / irrelevant
    arm: str                    # ON / OFF
    checkpoint: str
    sim_ts: str
    temporal_line: str          # ON 時為實際注入行；OFF 時為 ""
    stimulus: str
    emergent_snapshot: str      # interpretation 原文（未改寫）
    motive_text: str
    decision_text: str
    reached_action: bool
    # ── TL-12 本地報告層（observer 不動）──
    report: Dict[str, Any] = field(default_factory=dict)


def _to_record(obs: TL12Observation) -> Dict[str, Any]:
    """轉成 observer.observe() 認得的 record 形狀（原文照存）。"""
    return {
        "checkpoint": obs.checkpoint,
        "sim_ts": obs.sim_ts,
        "stimulus": obs.stimulus,
        "emergent_snapshot": obs.emergent_snapshot,
        "motive_text": obs.motive_text,
        "decision_text": obs.decision_text,
        "reached_action": obs.reached_action,
    }


def _report_for(obs: TL12Observation) -> Dict[str, Any]:
    """TL-12 本地報告層。**不呼叫、不修改** `observer.derive_interpretation`。

    🔴 區分兩類標記（Owner 2026-10-03 警示）：
       `temporal_marker`      —— 任何時間痕跡（含回音）
       `temporal_inference`   —— **排除了 stimulus 與注入行已有的詞**，
                                必須經推理才能產生（例：從「早上」推出「午餐」）
       **TA-2-A 的判定以 `temporal_inference` 為準**，不以 `temporal_marker` 為準。
    """
    text = f"{obs.emergent_snapshot} {obs.motive_text}"
    inference = _inference_markers(text, obs.stimulus, obs.temporal_line)
    return {
        "reporting_only": True,
        "source_fields": ["emergent_snapshot", "motive_text"],
        "rewrites_source": False,
        "temporal_marker": _has_temporal_marker(text),
        "temporal_marker_hits": sorted(
            {m for m in _TEMPORAL_MARKERS if m in text}
        ),
        "temporal_inference": bool(inference),
        "temporal_inference_hits": inference,
        "n_chars": len(text),
    }


# ────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────

class TL12Runner:
    """TA-2-A temporal ablation 编排器（复用 harness/ 全部既有元件）。"""

    def __init__(
        self,
        repo_root,
        llm_call: Callable[..., Awaitable[Optional[str]]],
        soul_id: str = TL12_SOUL_ID,
        runs: int = TL12_RUNS,
        llm_model: str = "unknown",
    ) -> None:
        self._repo_root = repo_root
        self._inner_llm_call = llm_call
        self._soul_id = soul_id
        self._runs = int(runs)
        self._llm_model = llm_model
        self._observer = Observer()

    async def run(self) -> Dict[str, Any]:
        from .fixture import seed_soul

        observations: List[TL12Observation] = []
        calls_by_arm: Dict[str, int] = {"ON": 0, "OFF": 0}

        for ctx_label, epoch_iso in TEMPORAL_CONTEXTS:
            # 重用 SimulationClock（本 context 的起點即 epoch）
            clock = SimulationClock(start_day=0, epoch_iso=epoch_iso)
            sim_ts = clock.sim_ts(0, hour=0)
            temporal_line = build_temporal_line(epoch_iso)

            for probe_id, stimulus in TEMPORAL_SENSITIVE_PROBES + TEMPORAL_IRRELEVANT_PROBES:
                kind = "sensitive" if (probe_id, stimulus) in TEMPORAL_SENSITIVE_PROBES else "irrelevant"
                for arm in ("ON", "OFF"):
                    line = temporal_line if arm == "ON" else None
                    wrapped, log = make_temporal_llm_call(self._inner_llm_call, line)
                    probe = GrowthProbe(agent_id=self._soul_id, llm_call=wrapped)
                    for run_idx in range(self._runs):
                        run_id = f"{ctx_label}.{probe_id}.{arm}.R{run_idx}"
                        out = await probe.run(
                            stimulus=stimulus,
                            checkpoint=clock.label(0),
                            sim_ts=sim_ts,
                            fed_events=[],          # same relevant state：空經歷
                        )
                        calls_by_arm[arm] += len(log)
                        obs = TL12Observation(
                            run_id=run_id,
                            context_label=ctx_label,
                            probe_id=probe_id,
                            probe_kind=kind,
                            arm=arm,
                            checkpoint=clock.label(0),
                            sim_ts=sim_ts,
                            temporal_line=line or "",
                            stimulus=stimulus,
                            emergent_snapshot=out.emergent_snapshot,
                            motive_text=out.motive_text,
                            decision_text=out.decision_text,
                            reached_action=out.reached_action,
                        )
                        obs.report = _report_for(obs)
                        observations.append(obs)

        result = self._evaluate(observations)
        result["n_llm_calls_by_arm"] = calls_by_arm
        result["experiment_id"] = TL12_EXPERIMENT_ID
        result["soul_id"] = self._soul_id
        result["llm_model"] = self._llm_model
        result["runs_per_arm"] = self._runs
        return result

    # ── 判定（Owner 工單 §6：INCONCLUSIVE 是一等公民）──────────

    def _evaluate(self, observations: List[TL12Observation]) -> Dict[str, Any]:
        # 1) 既有 determinism 機制（跨 run 比對 decision_parsed）
        # 🔴 接線更正（2026-10-03 11:58）：**每個條件（ctx × probe × arm）
        #    必須各自成為一個獨立 run**，且該 run 內含該條件的 N 次重複。
        #    先前把所有條件塞進同一個 run，導致 derive_determinism 的
        #    matrix[checkpoint][run_id] 把 20 個不同條件折疊成同一個
        #    checkpoint 桶裡的 20 個不同 run_id → 實際比對的是「條件之間」，
        #    而非「同一條件的重複之間」⇒ 量錯了。
        runs_for_det: List[Dict[str, Any]] = []
        for ctx_label, _ in TEMPORAL_CONTEXTS:
            for probe_id, _ in TEMPORAL_SENSITIVE_PROBES + TEMPORAL_IRRELEVANT_PROBES:
                for arm in ("ON", "OFF"):
                    recs = [o for o in observations
                            if o.context_label == ctx_label and o.probe_id == probe_id
                            and o.arm == arm]
                    if not recs:
                        continue
                    # 同一條件的 N 次重複，各自視為一個「run」
                    for r in recs:
                        runs_for_det.append(
                            {
                                "run_id": f"{ctx_label}.{probe_id}.{arm}."
                                          f"{r.run_id.rsplit('.', 1)[-1]}",
                                "records": [_to_record(r)],
                            }
                        )
        determinism = self._observer.derive_determinism(runs_for_det)

        # 2) difference-in-difference
        dd: Dict[str, Any] = {}
        for ctx_label, _ in TEMPORAL_CONTEXTS:
            for probe_id, _ in TEMPORAL_SENSITIVE_PROBES + TEMPORAL_IRRELEVANT_PROBES:
                on = [o for o in observations if o.context_label == ctx_label
                      and o.probe_id == probe_id and o.arm == "ON"]
                off = [o for o in observations if o.context_label == ctx_label
                       and o.probe_id == probe_id and o.arm == "OFF"]
                if not on or not off:
                    continue
                on_rate = sum(
                    1 for o in on if o.report["temporal_inference"]
                ) / len(on)
                off_rate = sum(
                    1 for o in off if o.report["temporal_inference"]
                ) / len(off)
                on_texts = {o.emergent_snapshot for o in on}
                off_texts = {o.emergent_snapshot for o in off}
                on_stable = len(on_texts) == 1
                off_stable = len(off_texts) == 1
                dd[f"{ctx_label}.{probe_id}"] = {
                    "kind": on[0].probe_kind,
                    # 🔴 判定用 inference（推理），非 marker（含回音）
                    "on_inference_rate": round(on_rate, 3),
                    "off_inference_rate": round(off_rate, 3),
                    "delta": round(on_rate - off_rate, 3),
                    # 僅供對照觀察：含回音的 marker rate（不參與判定）
                    "on_marker_rate": round(
                        sum(1 for o in on if o.report["temporal_marker"]) / len(on), 3
                    ),
                    "off_marker_rate": round(
                        sum(1 for o in off if o.report["temporal_marker"]) / len(off), 3
                    ),
                    "on_stable_across_runs": on_stable,
                    "off_stable_across_runs": off_stable,
                    "on_distinct_outputs": len(on_texts),
                    "off_distinct_outputs": len(off_texts),
                    "text_changed": on_texts != off_texts,
                    "on_inference_hits": sorted(
                        {m for o in on for m in o.report["temporal_inference_hits"]}
                    ),
                    "off_inference_hits": sorted(
                        {m for o in off for m in o.report["temporal_inference_hits"]}
                    ),
                }

        sens = {k: v for k, v in dd.items() if v["kind"] == "sensitive"}
        ctrl = {k: v for k, v in dd.items() if v["kind"] == "irrelevant"}

        # 控制組雜訊地板：control 的 |delta| 上限
        noise_floor = max((abs(v["delta"]) for v in ctrl.values()), default=0.0)

        meaningful = [
            k for k, v in sens.items()
            if v["delta"] >= 0.5 and v["on_stable_across_runs"]
        ]
        control_clean = all(abs(v["delta"]) <= noise_floor for v in ctrl.values())
        det_ok = determinism["determinism_verdict"] == "PASS"

        # INCONCLUSIVE 條件（Owner 工單逐字）
        reasons: List[str] = []
        if not det_ok:
            reasons.append("determinism_verdict=BLOCKED：跨 run decision_parsed 不一致，"
                           "差異無法歸因於 temporal context")
        if noise_floor >= 0.5:
            reasons.append(
                f"control group 出現 comparable variation (|delta|max={noise_floor:.2f})，"
                f"代表量到的是 LLM 自然變動而非時間效果"
            )
        if not meaningful:
            reasons.append("無任何 temporal-sensitive probe 呈現穩定且可歸因的差異")
        # 🔴 Owner 警示（2026-10-03）：有文字差異 ≠ 有理解差異。
        #    若只有 marker（含回音）差而 inference（推理）無差，必須明說，
        #    **不得**因為「ON/OFF 說的話不一樣」就判 PASS。
        wording_only = [
            k for k, v in sens.items()
            if v["delta"] < 0.5
            and v["on_marker_rate"] != v["off_marker_rate"]
        ]
        if wording_only and not meaningful:
            reasons.append(
                f"{len(wording_only)} 組 probe 僅有**措辭差異**（含回音的 marker rate 變動，"
                f"但無任何『必須推理才能產生』的 temporal inference）"
                f"：{wording_only} —— 依 Owner 警示，**措辭不同不等於理解不同，不判 PASS**"
            )
        elif wording_only:
            reasons.append(
                f"另有 {len(wording_only)} 組 probe 僅有措辭差異（已由 inference 證據區分）：{wording_only}"
            )

        verdict = "PASS" if (meaningful and control_clean and det_ok) else "INCONCLUSIVE"

        return {
            "verdict": verdict,
            "inconclusive_reasons": reasons,
            "n_meaningful_sensitive_probes": len(meaningful),
            "meaningful_sensitive_probes": meaningful,
            "control_clean": control_clean,
            "control_noise_floor": noise_floor,
            "determinism": determinism,
            "difference_in_difference": dd,
            "temporal_lines": {
                label: build_temporal_line(epoch) for label, epoch in TEMPORAL_CONTEXTS
            },
        }


# ────────────────────────────────────────────────────────────
# Convenience entrypoint
# ────────────────────────────────────────────────────────────

async def run_tl12(llm_call: Callable[..., Awaitable[Optional[str]]], **kw) -> Dict[str, Any]:
    """執行 TA-2-A。**STRICT 0 production mutation**：在 isolated data_root 內跑。

    🔴 結束時**必須**把 `SOUL_OS_DATA_DIR` 與 data_root 完全復原為呼叫前的狀態，
    否則後續測試／生產會誤讀到暫存目錄。
    """
    import os
    import shutil
    import tempfile
    from pathlib import Path

    prev_env = os.environ.get("SOUL_OS_DATA_DIR", None)
    prev_root = data_root().resolve()
    iso = tempfile.mkdtemp(prefix="tl12_")
    try:
        os.environ["SOUL_OS_DATA_DIR"] = iso
        reset_data_root()
        assert data_root().resolve() != prev_root, (
            "isolated data_root 未生效 —— 拒絕在 production 上跑 TL-12"
        )
        runner = TL12Runner(
            repo_root=Path(__file__).resolve().parent.parent, llm_call=llm_call, **kw
        )
        return await runner.run()
    finally:
        if prev_env is None:
            os.environ.pop("SOUL_OS_DATA_DIR", None)
        else:
            os.environ["SOUL_OS_DATA_DIR"] = prev_env
        reset_data_root()
        assert data_root().resolve() == prev_root, (
            "TL-12 結束後 data_root 必須復原為呼叫前狀態"
        )
        shutil.rmtree(iso, ignore_errors=True)
