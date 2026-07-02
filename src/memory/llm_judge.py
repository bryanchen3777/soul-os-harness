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

# 對應 3 個 prompt 檔
PROMPT_FILES = {
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


def _load_prompt(category: Category) -> str:
    """讀 judge prompt 檔。"""
    prompt_dir = Path(__file__).parent / "judge_prompts"
    prompt_file = prompt_dir / PROMPT_FILES[category]
    if not prompt_file.exists():
        raise FileNotFoundError(f"prompt file not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


def _parse_judge_output(text: str) -> tuple[Judgment, str]:
    """從 LLM 回應解析 CATEGORY / JUDGMENT / REASON。
    失敗預設為 UNSUPPORTED(保守)。
    """
    text = text.strip()
    # 嘗試找 CATEGORY: ... / JUDGMENT: ... 結構
    m_cat = re.search(r"CATEGORY\s*:\s*(\w+)", text, re.IGNORECASE)
    m_judge = re.search(r"JUDGMENT\s*:\s*(\w+)", text, re.IGNORECASE)
    m_reason = re.search(r"REASON\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)

    judgment = m_judge.group(1).upper() if m_judge else "UNSUPPORTED"
    if judgment not in ("SUPPORTED", "WEAK", "UNSUPPORTED"):
        judgment = "UNSUPPORTED"
    reason = m_reason.group(1).strip() if m_reason else ""
    return judgment, reason


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
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
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
        Step B: 對單個三元組跑三檔分類判準。

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
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                temperature=0.0,
            )
            return _parse_judge_output(r)
        except Exception as e:
            logger.warning(f"[LLMJudge.judge_triple] 失敗: {e}")
            return "UNSUPPORTED", f"LLM call failed: {e}"

    def confidence_from_judgment(
        self,
        judgment: Judgment,
        category: Category,
    ) -> float:
        """離散分類 → 連續 confidence 數值(供既有門檻邏輯沿用)。"""
        return JUDGMENT_TO_CONFIDENCE[category][judgment]

    async def extract_and_judge(
        self,
        text: str,
        context: str,
        agent_id: str,
    ) -> List[Dict[str, Any]]:
        """
        完整兩步流程: 萃取 → 逐條分類。
        對每個 category 跑 Step B,回傳最終 fact list(含 category / confidence / judgment / reason)。
        """
        triples = await self.extract_triples(text, context, agent_id)
        results = []
        for t in triples:
            for cat in PROMPT_FILES:
                judgment, reason = await self.judge_triple(t, context, cat)
                conf = self.confidence_from_judgment(judgment, cat)
                if conf > 0:
                    results.append({
                        **t,
                        "category": cat,
                        "judgment": judgment,
                        "reason": reason,
                        "confidence": conf,
                    })
        return results
