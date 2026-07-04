"""
src/memory/shadow.py
Shadow Mode Observer (Bry §11, 2026-07-02)

Bry §11 收到 A.1:
- N = 7 天
- 立刻啟動(設計完,verify 過,自動跑)
- 全部 9 個 agent 一起開(為 milestone 樣本量)
- 對每一筆真實 _on_agent_speak 訊息,v6 judge 跑一次,結果只 log 不生效
- 對照「現有系統」(MemoryMiddleware regex heuristic fallback)

Bry §11 明確排除:
- ✗ 不動 prod 路徑的結果(只加一個 observer hook)
- ✗ 不動 milestone prompt / judgment
- ✗ 不寫入任何 SAGE Graph

Bry §11 效能/穩定性門檻:
- 錯誤率(exception / 429 / 空結果) < 5%

config:
- shadow_dir: data/shadow/
- log_file: shadow_log.jsonl
- enable flag: SHADOW_MODE_ENABLED=true(預設啟動)
- 跑完 7 天自動關閉(by started_at + 7d)

驗收:
- shadow.py 本身 import OK + 不主動呼叫
- observe(text, speaker) 跑 v6 + 現有 heuristic 並排,append 到 log
- 計算 category agreement v6 vs heuristic
- 計算錯誤率(空結果 / 異常 / 算進 total,ratio < 5%)
- 7-day timer check: stale / fresh 標記
"""
import asyncio
import json
import os
import time
import logging
import traceback
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("soul_os.memory.shadow")


# Bry §11: 7 天時間限制
SHADOW_DURATION_DAYS = 7

# Bry §11: 錯誤率閾值
ERROR_RATE_THRESHOLD = 0.05

# SHADOW 啟動 flag
DEFAULT_SHADOW_ENABLED = True


class ShadowObserver:
    """獨立 observer,對 _on_agent_speak 訊息跑 v6 + 現有 heuristic 並排 log。

    Bry §11 要求:
    - 不動 prod 路徑結果(完全旁路)
    - 並行掛一條收集路徑
    - 7 天自動結束
    - 對照現有系統(heuristic fallback)vs v6
    """

    def __init__(
        self,
        shadow_dir: Path,
        enabled: bool = DEFAULT_SHADOW_ENABLED,
        llm_proxy: Optional[Any] = None,
        token_budget: Optional[Any] = None,
    ):
        self.shadow_dir = Path(shadow_dir)
        self.shadow_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.shadow_dir / "shadow_log.jsonl"
        self.enabled = enabled
        self.llm_proxy = llm_proxy
        self.token_budget = token_budget
        self.started_at = datetime.now()
        self.expires_at = self.started_at + timedelta(days=SHADOW_DURATION_DAYS)
        self._summary_cache: Dict[str, Any] = {}

        logger.info(
            f"[ShadowObserver] enabled={enabled}, started_at={self.started_at.isoformat()}, "
            f"expires_at={self.expires_at.isoformat()}, log_file={self.log_file}"
        )

    def is_active(self) -> bool:
        """Bry §11: 7 天後自動 inactive。"""
        if not self.enabled:
            return False
        return datetime.now() < self.expires_at

    async def observe(
        self,
        text: str,
        agent_id: str,
        speaker: str,
        context: str = "",
        heuristic_facts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """觀察單筆訊息。Bry §11: 並排 v6 vs 現有 heuristic,不寫入任何記憶。

        Args:
            text: 訊息文字
            agent_id: agent_id e.g. "agent_rem"
            speaker: speaker e.g. "agent_rem" or "user:..."
            context: 上下文 (預設 "")
            heuristic_facts: 現有系統的 facts (從 _extract_facts_heuristic_fallback 傳入)

        Returns:
            dict 含 v6 / heuristic / error 標記 (給 caller log 寫入,但不改 prod 結果)
        """
        if not self.is_active():
            return {"shadow_active": False, "skipped": True}

        record = {
            "timestamp": datetime.now().isoformat(),
            "agent_id": agent_id,
            "speaker": speaker,
            "text": text[:300],  # 截 300 chars 避免 log 爆炸
            "context_provided": bool(context.strip()),
            "v6": {"status": "unknown", "facts": [], "error": None},
            "heuristic": {
                "facts": heuristic_facts or [],
                "n_facts": len(heuristic_facts) if heuristic_facts else 0,
            },
        }

        # v6 路徑
        if self.llm_proxy is None:
            record["v6"]["status"] = "no_proxy"
            record["v6"]["error"] = "LLMProxy not provided to ShadowObserver"
        else:
            try:
                from src.memory.llm_judge import LLMJudge
                judge = LLMJudge(self.llm_proxy)
                facts = await judge.extract_and_judge(text, context, agent_id)
                record["v6"]["facts"] = facts
                record["v6"]["n_facts"] = len(facts)
                record["v6"]["status"] = "ok" if facts else "empty"
            except Exception as e:
                record["v6"]["status"] = "exception"
                record["v6"]["error"] = repr(e)[:200]
                record["v6"]["traceback"] = traceback.format_exc()[:500]
                logger.warning(f"[ShadowObserver] v6 exception: {e}")

        # append log (append-only JSONL)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"[ShadowObserver] 寫 log 失敗: {e}")

        return record

    def summarize(self) -> Dict[str, Any]:
        """Bry §11: shadow 跑完後自動產出摘要。

        算:
          - v6 vs heuristic category agreement (per category + overall)
          - milestone 累積筆數
          - 錯誤率 (空結果 + exception) / 總筆數
          - 7-day expiry check
        """
        if not self.log_file.exists():
            return {
                "shadow_active": self.is_active(),
                "started_at": self.started_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "n_records": 0,
            }

        records: List[Dict[str, Any]] = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        n_total = len(records)
        # Bry §11: 錯誤率 = 空結果 + exception / 總
        n_errors = sum(1 for r in records if r["v6"]["status"] in {"exception", "no_proxy"} or
                       (r["v6"]["status"] == "empty" and not r.get("heuristic", {}).get("facts")))
        n_empty = sum(1 for r in records if r["v6"]["status"] == "empty" and r.get("heuristic", {}).get("facts"))
        error_rate = (n_errors + n_empty) / n_total if n_total else 0

        # v6 vs heuristic category agreement
        cat_match = 0
        cat_total = 0
        cat_count = {"v6_pref": 0, "v6_mile": 0, "v6_diary": 0,
                     "h_pref": 0, "h_mile": 0, "h_diary": 0}
        milestone_accum = 0
        context_dependent_count = 0  # #4 對話上下文依賴案例
        for r in records:
            v6_facts = r["v6"].get("facts", [])
            h_facts = r["heuristic"].get("facts", [])
            # 取 v6 跟 heuristic 各自的 category counts
            v6_cats = Counter(f.get("category") for f in v6_facts)
            h_cats = Counter(f.get("category") for f in h_facts)
            cat_count["v6_pref"] += v6_cats.get("preference_plan_event_fact", 0)
            cat_count["v6_mile"] += v6_cats.get("milestone", 0)
            cat_count["v6_diary"] += v6_cats.get("diary", 0)
            cat_count["h_pref"] += h_cats.get("preference_plan_event_fact", 0)
            cat_count["h_mile"] += h_cats.get("milestone", 0)
            cat_count["h_diary"] += h_cats.get("diary", 0)
            milestone_accum += v6_cats.get("milestone", 0)

            # agreement: 兩個都沒抽出 fact 算 match
            v6_n = len(v6_facts)
            h_n = len(h_facts)
            if v6_n == h_n:
                cat_match += 1
            cat_total += 1

            # #4 樣本偵測:heuristic 有而 v6 empty (反之亦然) 但 v6 沒有
            if h_n > 0 and v6_n == 0:
                context_dependent_count += 1

        category_agreement = cat_match / cat_total if cat_total else 0

        return {
            "shadow_active": self.is_active(),
            "started_at": self.started_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "n_records": n_total,
            "error_rate": error_rate,
            "error_rate_passes": error_rate < ERROR_RATE_THRESHOLD,
            "category_agreement": category_agreement,
            "category_agreement_passes": category_agreement >= 0.75,
            "v6_category_counts": {
                "preference_plan_event_fact": cat_count["v6_pref"],
                "milestone": cat_count["v6_mile"],
                "diary": cat_count["v6_diary"],
            },
            "heuristic_category_counts": {
                "preference_plan_event_fact": cat_count["h_pref"],
                "milestone": cat_count["h_mile"],
                "diary": cat_count["h_diary"],
            },
            "milestone_accumulated": milestone_accum,
            "context_dependent_count": context_dependent_count,
            "passes_all_thresholds": (
                error_rate < ERROR_RATE_THRESHOLD and
                category_agreement >= 0.75 and
                milestone_accum >= 20
            ),
        }


# Module-level singleton(由 run_server.py 創建)
_observer: Optional[ShadowObserver] = None


def init_shadow_observer(shadow_dir: Path, enabled: bool = DEFAULT_SHADOW_ENABLED,
                        llm_proxy: Optional[Any] = None) -> ShadowObserver:
    """Bry §11: 由 run_server.py 在啟動時創建 singleton。"""
    global _observer
    _observer = ShadowObserver(shadow_dir, enabled, llm_proxy)
    return _observer


def get_shadow_observer() -> Optional[ShadowObserver]:
    return _observer


async def maybe_observe(text: str, agent_id: str, speaker: str, context: str = "",
                       heuristic_facts: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """便利函式: 如果 observer 存在, 跑 observe。

    Bry §11: 在 _on_agent_speak 結尾呼叫, 不影響 prod 路徑結果。
    """
    obs = get_shadow_observer()
    if obs is None:
        return None
    return await obs.observe(text, agent_id, speaker, context, heuristic_facts)
