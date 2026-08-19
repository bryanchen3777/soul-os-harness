"""
src/memory/llm_judge.py
LLM-as-Judge Fact Extraction + Discrete Judgment(2026-07-02)。

任務書 §2.1 兩步流程:
  Step A: extract_triples(text, context, agent_id) -> list[dict]
  Step B: judge_triple(triple, context, category) -> SUPPORTED|WEAK|UNSUPPORTED
  連續分數:confidence_from_judgment(judgment, category) -> float
    (用既有 THRESHOLDS dict 沿用,不需要重寫下游)

設計原則:
- 三檔離散分類(SUPPORTED / WEAK / UNSUPPORTED),不用 0-1 浮點數
- 每個 category 各自的 prompt 檔 + few-shot 範例
- feature flag USE_LLM_JUDGE 控制是否啟用,失敗 fallback 到舊 heuristic
- 離散分類映射到區間上中段,沿用 v0.1 門檻邏輯
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger("soul_os.memory.llm_judge")

# 類型別
Judgment = Literal["SUPPORTED", "WEAK", "UNSUPPORTED"]
Category = Literal["preference_plan_event_fact", "milestone", "diary"]

# Bry 施工書 (2026-07-02): Step B 拆兩階段
# - judge_stance: self_directed / other_directed
# - judge_content: preference_plan_event_fact / milestone
Stance = Literal["self_directed", "other_directed"]

# 對應 4 個 prompt 檔:3 個 content + 1 個 stance
PROMPT_FILES = {
    "stance": "judge_prompt_stance.md",
    "preference_plan_event_fact": "judge_prompt_preference_plan_event_fact.md",
    "milestone": "judge_prompt_milestone.md",
    "diary": "judge_prompt_diary.md",
}

# 三檔離散分類 → confidence 區間(中段)
# 沿用 v0.1 草案門檻邏輯,SUPPORTED = 區間上緣,WEAK = 中段,UNSUPPORTED = 直接丟
JUDGMENT_TO_CONFIDENCE = {
    "preference_plan_event_fact": {
        "SUPPORTED": 0.80,    # 區間上緣(門檻 0.65 之上)
        "WEAK": 0.55,         # 中段(門檻 0.65 之下,但仍可能觸發其他類別)
        "UNSUPPORTED": 0.0,   # 0 表示不寫入
    },
    "milestone": {
        "SUPPORTED": 0.85,    # 上緣(門檻 0.75 之上)
        "WEAK": 0.60,         # 中段(門檻 0.75 之下)
        "UNSUPPORTED": 0.0,
    },
    "diary": {
        "SUPPORTED": 0.55,    # 上緣(門檻 0.45 之上)
        "WEAK": 0.40,         # 中段(門檻 0.45 之下)
        "UNSUPPORTED": 0.0,
    },
}


def _load_prompt(category: str) -> str:
    """讀 judge prompt 檔(category 是 stance 或任一 Category)。"""
    prompt_dir = Path(__file__).parent / "judge_prompts"
    prompt_file = prompt_dir / PROMPT_FILES[category]
    if not prompt_file.exists():
        raise FileNotFoundError(f"prompt file not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


def _parse_judge_output(text: str) -> tuple[Judgment, str]:
    """從 LLM 回應解析 JUDGMENT / REASON(舊版,相容 CATEGORY / STANCE 格式)。"""
    text = text.strip()
    m_judge = re.search(r"JUDGMENT\s*:\s*(\w+)", text, re.IGNORECASE)
    m_reason = re.search(r"REASON\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    judgment = m_judge.group(1).upper() if m_judge else "UNSUPPORTED"
    if judgment not in ("SUPPORTED", "WEAK", "UNSUPPORTED"):
        judgment = "UNSUPPORTED"
    reason = m_reason.group(1).strip() if m_reason else ""
    return judgment, reason


def _parse_stance_output(text: str) -> tuple[Stance, Judgment, str]:
    """從 LLM 回應解析 STANCE / JUDGMENT / REASON。"""
    text = text.strip()
    m_stance = re.search(r"STANCE\s*:\s*(\w+)", text, re.IGNORECASE)
    m_judge = re.search(r"JUDGMENT\s*:\s*(\w+)", text, re.IGNORECASE)
    m_reason = re.search(r"REASON\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    raw_stance = m_stance.group(1).lower() if m_stance else "other_directed"
    stance = "self_directed" if raw_stance in ("self_directed", "self", "inner") else "other_directed"

    judgment = m_judge.group(1).upper() if m_judge else "UNSUPPORTED"
    if judgment not in ("SUPPORTED", "WEAK", "UNSUPPORTED"):
        judgment = "UNSUPPORTED"
    reason = m_reason.group(1).strip() if m_reason else ""
    return stance, judgment, reason


def _parse_category_judge_output(text: str, expected_category: Category) -> tuple[Judgment, str]:
    """從 LLM 回應解析 CATEGORY / JUDGMENT / REASON(支援 content 類別)。"""
    text = text.strip()
    m_judge = re.search(r"JUDGMENT\s*:\s*(\w+)", text, re.IGNORECASE)
    m_reason = re.search(r"REASON\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    judgment = m_judge.group(1).upper() if m_judge else "UNSUPPORTED"
    if judgment not in ("SUPPORTED", "WEAK", "UNSUPPORTED"):
        judgment = "UNSUPPORTED"
    reason = m_reason.group(1).strip() if m_reason else ""
    return judgment, reason


# ─────────────────────────────────────────────────────────────
# M7-judge-batch (Bry 拍板 2026-08-18): 批次解析
# 批次版 judge 一次判斷 N 個三元組, 回 JSON results 陣列 (用 index 對齊)。
# 這些 parser 把 JSON 陣列還原成「長度 N 的 list」, 缺項/格式壞補預設。
# ─────────────────────────────────────────────────────────────

def _parse_stance_batch_output(text: str, n: int) -> List[Dict[str, str]]:
    """解析批次 stance 的 JSON results 陣列, 回傳長度 n (缺項補 other_directed/UNSUPPORTED)。"""
    default = {"stance": "other_directed", "judgment": "UNSUPPORTED", "reason": ""}
    result = [dict(default) for _ in range(n)]
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return result
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return result
    items = data.get("results", [])
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
        except (ValueError, TypeError):
            continue
        if not (0 <= idx < n):
            continue
        raw_stance = str(item.get("stance", "")).lower()
        stance = "self_directed" if raw_stance in ("self_directed", "self", "inner") else "other_directed"
        judgment = str(item.get("judgment", "UNSUPPORTED")).upper()
        if judgment not in ("SUPPORTED", "WEAK", "UNSUPPORTED"):
            judgment = "UNSUPPORTED"
        result[idx] = {
            "stance": stance,
            "judgment": judgment,
            "reason": str(item.get("reason", "")),
        }
    return result


def _parse_content_batch_output(text: str, n: int) -> List[Dict[str, str]]:
    """解析批次 content 的 JSON results 陣列, 回傳長度 n (缺項補 UNSUPPORTED)。"""
    result = [{"judgment": "UNSUPPORTED", "reason": ""} for _ in range(n)]
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return result
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return result
    items = data.get("results", [])
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
        except (ValueError, TypeError):
            continue
        if not (0 <= idx < n):
            continue
        judgment = str(item.get("judgment", "UNSUPPORTED")).upper()
        if judgment not in ("SUPPORTED", "WEAK", "UNSUPPORTED"):
            judgment = "UNSUPPORTED"
        result[idx] = {
            "judgment": judgment,
            "reason": str(item.get("reason", "")),
        }
    return result


class LLMJudge:
    """LLM-as-Judge 評審器:三元組萃取 + 離散分類判準 + 連續 confidence 映射。"""

    def __init__(self, llm_proxy):
        """
        Args:
            llm_proxy: LLMProxy 實例 (from create_llm_proxy)
        """
        self.llm_proxy = llm_proxy
        # 預載 prompt(避免每次 judge 重新讀檔)
        self._prompts = {
            cat: _load_prompt(cat) for cat in PROMPT_FILES
        }

    async def extract_triples(
        self,
        text: str,
        context: str = "",
        agent_id: str = "",
    ) -> List[Dict[str, str]]:
        """
        Step A: 三元組萃取。

        Returns:
            list of {"subject": str, "predicate": str, "object": str}
            空陣列 = 無法明確萃取
        """
        if not text or len(text.strip()) < 4:
            return []

        system_prompt = (
            "你是 Memory Fact Extraction 助手。\n"
            "從給定的訊息文字 + 上下文中,萃取 0 或多個 (subject, predicate, object) 三元組。\n"
            "要求:1) 只萃取文字明確表達的內容,不推測 2) JSON 格式輸出 3) 無法明確萃取時輸出空陣列\n"
            "格式:{\"triples\": [{\"subject\": \"...\", \"predicate\": \"...\", \"object\": \"...\"}, ...]}"
        )
        user_msg = f"訊息:\n{text}\n\n上下文:\n{context or '(無)'}\n\n請萃取三元組。"
        try:
            r = await self.llm_proxy.backend.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                # JP rollback (Bry 拍板 2026-07-22 20:59): 從 LLMProxy backend model 動態拿
                # 原本 hardcode claude-haiku 跟 minimax OpenAI endpoint 不相容, 會 400
                model=self.llm_proxy.model,
                max_tokens=1000,  # Lesson 36D Bry 拍板 2026-07-26 12:18: 升 1000+ 解 41.5% length 截斷 (v31 ship)
                temperature=0.0,  # 三元組萃取要 deterministic
            )
            return self._parse_triples(r)
        except Exception as e:
            logger.warning(f"[LLMJudge.extract_triples] 失敗: {e}")
            return []

    def _parse_triples(self, llm_output: str) -> List[Dict[str, str]]:
        """解析 LLM 回傳的 JSON 三元組,失敗回空陣列。"""
        # 嘗試找 JSON block
        m = re.search(r"\{.*\}", llm_output, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
        triples = data.get("triples", [])
        # 標準化
        result = []
        for t in triples:
            if not isinstance(t, dict):
                continue
            subj = str(t.get("subject", "")).strip()
            pred = str(t.get("predicate", "提及")).strip()
            obj = str(t.get("object", "")).strip()
            if subj and obj:
                result.append({
                    "subject": subj[:60],
                    "predicate": pred[:30],
                    "object": obj[:100],
                })
        return result

    async def judge_triple(
        self,
        triple: Dict[str, str],
        context: str,
        category: Category,
    ) -> tuple[Judgment, str]:
        """
        Step B (舊版,相容介面): 對單個三元組跑三檔分類判準。
        新版 Step B 拆兩階段(judge_stance + judge_content),透過 extract_and_judge 呼叫。
        本方法保留相容性,但已不直接被 extract_and_judge 使用。

        Returns:
            (judgment, reason)
        """
        prompt = self._prompts[category]
        user_msg = (
            f"三元組: {triple['subject']} {triple['predicate']} {triple['object']}\n"
            f"原文: {context}\n"
            f"請依 prompt 判準回答 CATEGORY / JUDGMENT / REASON。"
        )
        try:
            r = await self.llm_proxy.backend.complete(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                model=self.llm_proxy.model,
                max_tokens=1000,  # Lesson 36D Bry 拍板 2026-07-26 12:18: 升 1000+ 解 length 截斷 (v31 ship)
                temperature=0.0,
            )
            return _parse_category_judge_output(r, category)
        except Exception as e:
            logger.warning(f"[LLMJudge.judge_triple] 失敗: {e}")
            return "UNSUPPORTED", f"LLM call failed: {e}"

    async def judge_stance(
        self,
        triple: Dict[str, str],
        context: str,
    ) -> tuple[Stance, Judgment, str]:
        """
        Bry 施工書 (2026-07-02) Step B-1。

        判斷三元組的陳述方向:
        - self_directed: 主體描述自己的內在狀態 / 感受 / 想法 / 自我覺察
        - other_directed: 主體描述外部世界 / 他人 / 計畫 / 客觀事件 / 對某物的穩定偏好

        Returns:
            (stance, judgment, reason)
        """
        prompt = self._prompts["stance"]
        user_msg = (
            f"三元組: {triple['subject']} {triple['predicate']} {triple['object']}\n"
            f"原文: {context}\n"
            f"請依 stance prompt 判準回答 STANCE / JUDGMENT / REASON。"
        )
        try:
            r = await self.llm_proxy.backend.complete(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                model=self.llm_proxy.model,
                max_tokens=1000,  # Lesson 36D Bry 拍板 2026-07-26 12:18: 升 1000+ 解 length 截斷 (v31 ship)
                temperature=0.0,
            )
            return _parse_stance_output(r)
        except Exception as e:
            logger.warning(f"[LLMJudge.judge_stance] 失敗: {e}")
            return "other_directed", "UNSUPPORTED", f"LLM call failed: {e}"

    def confidence_from_judgment(
        self,
        judgment: Judgment,
        category: Category,
    ) -> float:
        """離散分類 → 連續 confidence 數值(供既有門檻邏輯沿用)。"""
        return JUDGMENT_TO_CONFIDENCE[category][judgment]

    async def _judge_stance_batch(
        self,
        triples: List[Dict[str, str]],
        context: str,
    ) -> List[Dict[str, str]]:
        """
        M7-judge-batch (Bry 拍板 2026-08-18): 批次判斷所有三元組的 stance。
        1 次 LLM call 取代 N 次 (原 judge_stance 逐條)。
        回傳長度 == len(triples) 的 list, 每個元素 {stance, judgment, reason}。
        """
        if not triples:
            return []
        prompt = self._prompts["stance"]
        triple_lines = "\n".join(
            f"[{i}] {t['subject']} {t['predicate']} {t['object']}"
            for i, t in enumerate(triples)
        )
        user_msg = (
            f"以下是 {len(triples)} 個三元組, 請對每一個逐一判斷 STANCE / JUDGMENT / REASON。\n\n"
            f"{triple_lines}\n\n"
            f"原文: {context or '(無)'}\n\n"
            f"請輸出 JSON, 格式: "
            f'{{"results": [{{"index": 0, "stance": "self_directed", '
            f'"judgment": "SUPPORTED", "reason": "..."}}, ...]}}'
        )
        try:
            r = await self.llm_proxy.backend.complete(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                model=self.llm_proxy.model,
                max_tokens=2000,  # 批次多個三元組, 比單條 1000 放寬
                temperature=0.0,
            )
            return _parse_stance_batch_output(r, len(triples))
        except Exception as e:
            logger.warning(f"[LLMJudge._judge_stance_batch] 失敗: {e}")
            return [
                {"stance": "other_directed", "judgment": "UNSUPPORTED", "reason": "batch failed"}
                for _ in triples
            ]

    async def _judge_content_batch(
        self,
        triples: List[Dict[str, str]],
        text: str,
        category: Category,
    ) -> List[Dict[str, str]]:
        """
        M7-judge-batch (Bry 拍板 2026-08-18): 批次判斷所有三元組的 content 類別。
        每個 category 1 次 LLM call (取代逐條×逐類別)。回傳長度 == len(triples) 的
        list, 每個元素 {judgment, reason}。原文用 text (Bry §4 bug 修, 不用 context)。
        """
        if not triples:
            return []
        prompt = self._prompts[category]
        triple_lines = "\n".join(
            f"[{i}] {t['subject']} {t['predicate']} {t['object']}"
            for i, t in enumerate(triples)
        )
        user_msg = (
            f"以下是 {len(triples)} 個三元組, 請依 {category} prompt 判準, "
            f"對每一個輸出 JUDGMENT / REASON。\n\n"
            f"{triple_lines}\n\n"
            f"原文: {text or '(無)'}\n\n"  # Bry §4 bug 修: 用 text 不用 context
            f"請輸出 JSON, 格式: "
            f'{{"results": [{{"index": 0, "judgment": "SUPPORTED", "reason": "..."}}, ...]}}'
        )
        try:
            r = await self.llm_proxy.backend.complete(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                model=self.llm_proxy.model,
                max_tokens=2000,  # 批次多個三元組
                temperature=0.0,
            )
            return _parse_content_batch_output(r, len(triples))
        except Exception as e:
            logger.warning(f"[LLMJudge._judge_content_batch] 失敗 ({category}): {e}")
            return [
                {"judgment": "UNSUPPORTED", "reason": "batch failed"}
                for _ in triples
            ]

    async def extract_and_judge(
        self,
        text: str,
        context: str,
        agent_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Bry 施工書 (2026-07-02) 完整兩步流程 (M7-judge-batch 批次化 2026-08-18):
        - Step A: extract_triples (1 call, 不變)
        - Step B-1: _judge_stance_batch (1 call, 取代 N 次逐條)
        - Step B-2: _judge_content_batch (每個 category 1 call, 取代逐條×逐類別)

        最終 category:
          if stance == self_directed → category = "diary"
          else → category = content 結果

        回傳介面保持向下相容:{subject, predicate, object, category, judgment, reason, confidence}
        LLM 呼叫數: 1 + 1 + 2 = 4 (原 1 + N + 2M = 13)。
        """
        triples = await self.extract_triples(text, context, agent_id)
        trace = {
            "extract": {"n_triples": len(triples) if triples else 0},
            "stance_calls": 0,
            "stance_fail": 0,
            "stance_self_directed": 0,
            "stance_other_directed": 0,
            "content_calls": 0,
            "content_fail": 0,
            "content_calls_detail": [],
            "batched": True,  # M7-judge-batch marker
        }

        if not triples:
            logger.info(f"[LLMJudge.trace] text={text[:30]!r}: {trace}")
            return []

        # Step B-1: 批次 stance (1 call)
        trace["stance_calls"] = 1
        stance_results = await self._judge_stance_batch(triples, context)
        if len(stance_results) != len(triples):
            trace["stance_fail"] = 1
            stance_results = [
                {"stance": "other_directed", "judgment": "UNSUPPORTED", "reason": "batch failed"}
                for _ in triples
            ]
        for s in stance_results:
            if s.get("stance") == "self_directed":
                trace["stance_self_directed"] += 1
            else:
                trace["stance_other_directed"] += 1

        results = []
        content_candidates: List[tuple] = []  # (index, triple)

        for i, t in enumerate(triples):
            sr = stance_results[i]
            stance = sr.get("stance", "other_directed")
            stance_judgment = sr.get("judgment", "UNSUPPORTED")
            stance_reason = sr.get("reason", "")

            diary_captured = False
            if stance == "self_directed" and stance_judgment in ("SUPPORTED", "WEAK"):
                results.append({
                    **t,
                    "category": "diary",
                    "judgment": stance_judgment,
                    "reason": stance_reason,
                    "confidence": JUDGMENT_TO_CONFIDENCE["diary"][stance_judgment],
                    "stance": stance,
                })
                diary_captured = True

            if not diary_captured:
                content_candidates.append((i, t))

        # Step B-2: 批次 content (每個 category 1 call)
        if content_candidates:
            candidate_triples = [t for (_i, t) in content_candidates]
            content_best: Dict[int, tuple] = {}
            for cat in ("preference_plan_event_fact", "milestone"):
                trace["content_calls"] += 1
                content_results = await self._judge_content_batch(candidate_triples, text, cat)
                if len(content_results) != len(candidate_triples):
                    trace["content_fail"] += 1
                    continue
                for k, cr in enumerate(content_results):
                    i, t = content_candidates[k]
                    judgment = cr.get("judgment", "UNSUPPORTED")
                    reason = cr.get("reason", "")
                    conf = JUDGMENT_TO_CONFIDENCE[cat].get(judgment, 0.0)
                    trace["content_calls_detail"].append({
                        "category": cat,
                        "triple": f"{t['subject']} {t['predicate']} {t['object']!r}",
                        "judgment": judgment,
                        "confidence": conf,
                        "reason_snippet": reason[:80],
                    })
                    if conf > 0:
                        prev = content_best.get(i)
                        if prev is None or conf > prev[1]:
                            content_best[i] = (judgment, conf, cat, reason)

            for i, t in content_candidates:
                if i in content_best:
                    judgment, conf, cat, reason = content_best[i]
                    results.append({
                        **t,
                        "category": cat,
                        "judgment": judgment,
                        "reason": reason,
                        "confidence": conf,
                        "stance": stance_results[i].get("stance", "other_directed"),
                    })

        logger.info(f"[LLMJudge.trace] text={text[:30]!r}: {trace}")
        return results
