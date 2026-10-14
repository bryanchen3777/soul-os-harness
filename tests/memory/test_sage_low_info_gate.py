# -*- coding: utf-8 -*-
"""
tests/memory/test_sage_low_info_gate.py
SOUL-BUG-002（2026-10-14，Owner 裁示「分級重疊 · Gate 側」）

**沒有任何評分器**：本 loader 的管線是 `overlap 門檻 → category confidence 門檻 → eligible`，
全程沒有 score / rank / sort / weight。因此「低資訊 token 命中實質內容詞時仍作為時間
相依特徵參與排序加分」（Boost 側）在本 codebase **沒有消費端**，本次不實作。

本檔釘死兩件事：
1. **Gate 側有效**：查詢的有效 token 全落在 `_LOW_INFO_TOKENS`（今天／什麼／怎麼…）時，
   不得因為單一 token 命中就召回任何記憶。
2. **不得過度修法**：查詢同時含實質內容詞時，**必須仍然召回**——否則就是把 Bry 的
   記憶能力砍掉了。`今天 排程 怎麼樣` 這類句子的「時間脈絡」語意必須保留。

全部用 temp store，不讀生產語料（生產語料的對應紅燈由
tests/test_m5_3_s2_{b_normalization,retrieval_diagnostic}.py 負責）。
"""
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.v1.loader import (
    MemoryLoader,
    derive_query_tags,
    _LOW_INFO_TOKENS,
    _SHARED_STOPWORDS,
    _PER_AGENT_NAME_STOPWORDS,
    MIN_OVERLAP_SHORT_CONTENT,
    MIN_OVERLAP_LONG_CONTENT,
    MIN_OVERLAP_CONTENT_BOUNDARY,
)
from src.memory.v1.store import Memory, V1Store

AGENT = "agent_low_info_test"


def _corpus(tmp: Path):
    """三筆記憶，分別帶 今天 / 排程 / 複雜內容。"""
    store = V1Store(tmp / "data" / "memory", AGENT)
    store.add(Memory(
        memory_id="low_info_today", agent_id=AGENT,
        content="Bryan 起得很早 今天",
        tags=["bryan", "起得", "很早", "今天"],
        created_at=time.time(), category="preference", confidence=0.9,
    ))
    store.add(Memory(
        memory_id="has_schedule", agent_id=AGENT,
        content="今天排程跑起來了",
        tags=["排程", "跑起來"],
        created_at=time.time(), category="preference", confidence=0.9,
    ))
    store.add(Memory(
        memory_id="long_content", agent_id=AGENT,
        content="姊姊的角斷於那一晚的火和血的事故中雷姆在事故中活下來她記得每一個細節",
        tags=["姊姊的角", "事故", "活下來", "細節"],
        created_at=time.time(), category="diary", confidence=0.9,
    ))
    return MemoryLoader(store=store, trace_log_path=None)


def _eligible(loader, query: str):
    result = loader.load(derive_query_tags(query), AGENT)
    return [m.memory_id for m in result["eligible_memories"]]


# ─────────────────────────────────────────────────────────────
# 1. Gate 側：零資訊 token 不得單獨滿足門檻
# ─────────────────────────────────────────────────────────────


class TestGate:
    def test_pure_low_info_query_returns_nothing(self, tmp_path):
        loader = _corpus(tmp_path)
        for q in ("今天 天氣 怎麼 樣", "什麼", "怎麼樣 今天", "今天"):
            assert _eligible(loader, q) == [], (
                f"{q!r} 只由低資訊 token 構成，不得召回任何記憶"
            )

    def test_negative_queries_that_caused_the_red_lights_return_nothing(self, tmp_path):
        """修復前這兩句各會召回記憶（什麼×4 / 今天×2）。

        ⚠️ 命名注意：這支用 temp store，**不讀生產語料**，因此函式名刻意不含
        real_prod / real_service 等字樣——否則會被
        tests/infra/test_live_service_tests_are_opt_in.py 的 opt-in 護欄攔下，
        而那正是它該做的事：名字宣告會打生產，就必須真的要 opt-in 閘門。
        """
        loader = _corpus(tmp_path)
        assert _eligible(loader, "今天 天氣 怎麼 樣") == []
        assert _eligible(loader, "Python 是 什麼 程式語言") == []


# ─────────────────────────────────────────────────────────────
# 2. 不得過度修法：實質內容詞必須仍然能召回
# ─────────────────────────────────────────────────────────────


class TestNoOverCorrection:
    def test_mixed_query_still_recalls(self, tmp_path):
        """`今天 排程 怎麼樣` —— 有實質內容詞「排程」，必須仍然召回。"""
        loader = _corpus(tmp_path)
        assert "has_schedule" in _eligible(loader, "今天 排程 怎麼樣"), (
            "含實質內容詞的查詢必須仍然命中 —— 否則就是把 Bry 的記憶能力砍掉了"
        )

    def test_pure_content_query_untouched(self, tmp_path):
        loader = _corpus(tmp_path)
        assert "has_schedule" in _eligible(loader, "排程 跑起來")

    def test_long_content_threshold_is_unchanged(self, tmp_path):
        """分層門檻（Bry §25(b)）不得被這次修復改動：長內容仍需 2 個實質命中。"""
        assert MIN_OVERLAP_SHORT_CONTENT == 1
        assert MIN_OVERLAP_LONG_CONTENT == 2
        assert MIN_OVERLAP_CONTENT_BOUNDARY == 20

        loader = _corpus(tmp_path)
        # long_content 有 30+ tokens → 門檻 2；只命中「事故」時不得通過
        one_hit = _eligible(loader, "事故")
        assert "long_content" not in one_hit, "長內容仍需 2 個實質命中（單一命中不得放行）"
        # 命中兩個實質詞即可通過
        assert "long_content" in _eligible(loader, "事故 細節")


# ─────────────────────────────────────────────────────────────
# 3. 集合本身的契約（防「照著測試字面寫死」）
# ─────────────────────────────────────────────────────────────


class TestTokenSetContract:
    def test_no_compound_words_in_the_set(self):
        """反作弊守衛：集合只收**詞類**，不得出現含實體名詞的複合詞。

        例：'今天天氣' 這類字串會讓整個機制看起來像照著負向測試寫死。
        合法項最長 3 字（怎麼樣 / 為什麼 / 等一下），因此 3 為上限。
        """
        too_long = [t for t in _LOW_INFO_TOKENS if len(t) > 3]
        assert not too_long, (
            f"發現過長（疑似複合詞）的條目：{too_long}"
        )

    def test_word_classes_present(self):
        for t in ("今天", "昨天", "現在", "什麼", "怎麼", "怎麼樣", "為什麼", "誰"):
            assert t in _LOW_INFO_TOKENS, f"缺少疑問／時間詞：{t}"

    def test_does_not_steal_sage_stopwords_semantics(self):
        """兩張表語意不同、不可合併：低資訊實詞不得出現在功能詞停用詞表裡。"""
        assert not (_LOW_INFO_TOKENS & _SHARED_STOPWORDS), (
            "低資訊實詞不應出現在 _SHARED_STOPWORDS（那是切詞階段就丟棄的功能詞）"
        )
        assert "今天" not in _SHARED_STOPWORDS

    def test_per_agent_stopwords_untouched(self):
        """per-agent 專名機制是**另一套**，不可被這次修復牽動。"""
        assert _PER_AGENT_NAME_STOPWORDS["agent_rem"] == {"雷姆", "rem", "雷姆你", "昴"}