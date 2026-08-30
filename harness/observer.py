"""
harness/observer.py — Observer (structured observation + derived 解析) (TL-1)

TL-0 规格 §4.3 (已拍板):
  - derived 解析标 `derived`, 写独立 analysis/ 流, 永不回写 canonical。
  - derived 字段: decision_parsed / motive_present / motive_parsed /
    interpretation_class / change_verdict / trace_links / determinism_verdict。

工单 (TL-1): structured observation = decision enum + derived 解析
  stance / concern / attribution (interpretation 的解析维度, 仅供报告)。

硬规则 (§4.3): derived 层永不写回 canonical store, 永不改写
  emergent_snapshot / motive_text / decision_text 原文。

判定 (§8 Level 0-3):
  - change_verdict 由跨 checkpoint 比对 derived 结果得出:
      NO_CHANGE (L0) / SURFACE_ONLY (L1) / INTERPRETATION_DECISION_CHANGED (L2) /
      FULL_TRACEABLE (L3)。
  - determinism_verdict 由跨 run 比对 decision_parsed 得出: PASS / BLOCKED (§5)。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("soul_os.harness.observer")

# ───────────────────────────────────────────────────────────
# decision enum (§4.3)
# ───────────────────────────────────────────────────────────

DECISION_TRANSMIT = "transmit"
DECISION_SKIP = "skip"
DECISION_INDETERMINATE = "indeterminate"


def parse_decision_enum(record: Dict[str, Any]) -> str:
    """从 probe record 解析 decision enum (transmit / skip / indeterminate)。

    规则:
      - decision_text 非空且含 '"decision": "transmit"' → transmit
      - decision_text 非空且含 '"decision": "not_transmit"' → skip
      - 其他 (无 decision / 坏输出 / 未走到 decision) → indeterminate
    """
    text = record.get("decision_text", "") or ""
    if '"decision": "transmit"' in text:
        return DECISION_TRANSMIT
    if '"decision": "not_transmit"' in text:
        return DECISION_SKIP
    return DECISION_INDETERMINATE


# ───────────────────────────────────────────────────────────
# interpretation 解析维度 (stance / concern / attribution, 仅供报告)
# ───────────────────────────────────────────────────────────

# stance 关键字 (motive 对 stimulus 的立场)
_STANCE_CONCERNED = ("担心", "在意", "失落", "不安", "难过", "在意", "怕", "担心")
_STANCE_RESIGNED = ("算了", "没关系", "习惯", "慢慢变淡", "需要空间", "不是她的错")
_STANCE_NEUTRAL = ("不知道", "也许", "可能", "想")

# concern 主题关键字
_CONCERN_ALEX = ("Alex", "他", "朋友")
_CONCERN_SELF = ("自己", "我", "人家")
_CONCERN_BRYAN = ("Bry", "Bryan", "你")
_CONCERN_RELATIONSHIP = ("关系", "朋友", "感情", "疏远", "变淡")

# attribution 归因关键字
_ATTRIBUTION_EXTERNAL = ("忙", "工作", "有事", "需要空间", "别人", "群里", "躲")
_ATTRIBUTION_INTERNAL = ("自己", "想太多", "我的错", "决定", "习惯")


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def derive_interpretation(record: Dict[str, Any]) -> Dict[str, str]:
    """从 probe record 解析 interpretation 维度 (stance/concern/attribution)。

    全部是报告用标签 (简单关键字规则), 不是 classifier, 不改写原文。
    """
    motive_text = record.get("motive_text", "") or ""
    emergent = record.get("emergent_snapshot", "") or ""
    haystack = f"{motive_text} {emergent}"

    if not motive_text:
        stance, concern, attribution = "none", "none", "none"
    else:
        if _contains_any(haystack, _STANCE_CONCERNED):
            stance = "concerned"
        elif _contains_any(haystack, _STANCE_RESIGNED):
            stance = "resigned"
        elif _contains_any(haystack, _STANCE_NEUTRAL):
            stance = "neutral"
        else:
            stance = "unclassified"

        if _contains_any(haystack, _CONCERN_ALEX):
            concern = "alex"
        elif _contains_any(haystack, _CONCERN_RELATIONSHIP):
            concern = "relationship"
        elif _contains_any(haystack, _CONCERN_BRYAN):
            concern = "bryan"
        elif _contains_any(haystack, _CONCERN_SELF):
            concern = "self"
        else:
            concern = "unclassified"

        if _contains_any(haystack, _ATTRIBUTION_EXTERNAL):
            attribution = "external"
        elif _contains_any(haystack, _ATTRIBUTION_INTERNAL):
            attribution = "internal"
        else:
            attribution = "uncertain"

    return {
        "stance": stance,
        "concern": concern,
        "attribution": attribution,
    }


# ───────────────────────────────────────────────────────────
# Observer — 组装 derived 记录
# ───────────────────────────────────────────────────────────

class Observer:
    """structured observation: 把 probe record 解析成 derived 记录 (标 derived)。"""

    def observe(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """对一条 probe record 做 derived 解析 (标 derived: true, 附 source_field)。"""
        decision_parsed = parse_decision_enum(record)
        motive_present = bool(record.get("motive_text", ""))
        interp = derive_interpretation(record)

        derived: Dict[str, Any] = {
            "derived": True,
            "checkpoint": record.get("checkpoint", ""),
            "sim_ts": record.get("sim_ts", ""),
            "decision_parsed": decision_parsed,
            "decision_source_field": "decision_text",
            "motive_present": motive_present,
            "motive_source_field": "motive_text",
            "motive_parsed": {
                "target": "bryan",
                "stance": interp["stance"],
                "concern": interp["concern"],
                "attribution": interp["attribution"],
            },
            "motive_parsed_source_field": "motive_text",
            "interpretation_class": interp["stance"],
            "interpretation_source_field": "emergent_snapshot",
            "reached_action": bool(record.get("reached_action", False)),
        }
        return derived

    # ── 跨 checkpoint 比对 (§8 Level 0-3) ────────────────

    def derive_change_verdict(
        self,
        records: List[Dict[str, Any]],
        trace_links: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """跨 checkpoint 比对 derived 结果, 得出 change_verdict (Level 0-3)。

        Args:
            records: 一个 run 的 3 条 probe records (T0/T15/T30 顺序)
            trace_links: checkpoint → fed event id 列表 (可追溯性证据)

        Returns:
            {"change_verdict": str, "level": int, "evidence": {...}}
        """
        if len(records) < 2:
            return {"change_verdict": "NO_CHANGE", "level": 0, "evidence": {}}

        parsed = [parse_decision_enum(r) for r in records]
        motives = [r.get("motive_text", "") or "" for r in records]
        stances = [derive_interpretation(r)["stance"] for r in records]

        # decision enum 变化 (可解释方向上的改变)
        decision_changed = len(set(parsed)) > 1
        # motive 内容/指向变化
        motive_changed = len(set(motives)) > 1
        # interpretation 立场变化
        stance_changed = len(set(stances)) > 1

        # trace_links: 有 fed events 可追溯 (T15/T30 有经历)
        has_trace = bool(trace_links) and any(
            links for links in (trace_links or {}).values()
        )

        if not (decision_changed or motive_changed or stance_changed):
            verdict, level = "NO_CHANGE", 0
        elif decision_changed and has_trace:
            # 完整闭环: decision 在可解释方向上改变 + 可追溯到 fed events
            verdict, level = "FULL_TRACEABLE", 3
        elif (motive_changed or stance_changed) and has_trace:
            # Level 2 门槛: motive 内容/指向改变 (或 interpretation 立场改变)
            # + 可追溯到 fed events (decision 未变不降级, §8 Level 2 只要求
            # motive 内容/指向改变 或 decision 改变, 二选一)
            verdict, level = "INTERPRETATION_DECISION_CHANGED", 2
        elif motive_changed or stance_changed:
            # 有变化但 trace_links 缺失 → 保守降级 (§8 Level 2 要求可追溯)
            verdict, level = "SURFACE_ONLY", 1
        else:
            verdict, level = "SURFACE_ONLY", 1

        return {
            "change_verdict": verdict,
            "level": level,
            "evidence": {
                "decision_parsed": parsed,
                "motive_changed": motive_changed,
                "stance_changed": stance_changed,
                "decision_changed": decision_changed,
                "has_trace_links": has_trace,
            },
        }

    # ── 跨 run 比对 (§5 determinism) ────────────────────

    def derive_determinism(
        self, runs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """跨 run 比对 decision_parsed (每 checkpoint), 得出 determinism_verdict。

        §5.1 规则 4: 任一个 checkpoint 的 decision_parsed 在 3 次 run 之间
        不一致 → determinism BLOCKED。比对锚点只有 decision_parsed
        (+ reached_action 作为 sanity check)。

        Args:
            runs: 每个 run 的 {"run_id": str, "records": [T0, T15, T30]}

        Returns:
            {"determinism_verdict": "PASS"|"BLOCKED", "matrix": {...}}
        """
        matrix: Dict[str, Dict[str, str]] = {}
        for run in runs:
            run_id = run["run_id"]
            for rec in run["records"]:
                cp = rec.get("checkpoint", "")
                matrix.setdefault(cp, {})[run_id] = parse_decision_enum(rec)

        blocked = False
        for cp, by_run in matrix.items():
            values = set(by_run.values())
            if len(values) > 1:
                blocked = True

        return {
            "determinism_verdict": "BLOCKED" if blocked else "PASS",
            "matrix": matrix,
        }
