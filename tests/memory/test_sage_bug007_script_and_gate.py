# -*- coding: utf-8 -*-
"""
tests/memory/test_sage_bug007_script_and_gate.py
SOUL-BUG-007（2026-10-17，Owner 裁示）：缺陷 A 字形正規化 ＋ 缺陷 B 防離題 Gate

釘死兩個獨立機制，任何一方被改動都必須轉紅：

**缺陷 A｜簡繁字形分裂**
  語料實測約 323 處簡體 tag 混進繁體管線。決定性證據：同一個 store，
  `q=評價` 只召得 2 筆、`q=评价` 召得 5 筆 —— 繁體查詢漏掉 60% 該找到的記憶，
  純粹是字形不同。修法在 `_expand_tag_components` 內做轉換，
  **不碰 frozen 的 writer.py、不改磁碟既有資料**。

**缺陷 B｜無主題的泛化謂語單獨開 Gate**
  問 agent_yua「你喜歡看書嗎」，庫裡根本沒有「書」，卻因為命中「喜歡」
  而召回 5 筆「你喜歡聽…」的離題記憶。低召回是誠實（我不知道），
  假召回會讓角色一本正經地說錯話 —— 後者嚴重得多。

全部用 temp store，不讀生產語料、不發任何網路請求。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.v1.loader import (
    MemoryLoader,
    derive_query_tags,
    _expand_tag_components,
    _normalize_script,
    _SIMPLIFIED_TO_TRADITIONAL,
    _LOW_INFO_TOKENS,
    _SHARED_STOPWORDS,
)
from src.memory.v1.store import Memory, V1Store

AGENT = "agent_bug007_test"

# 簡繁兩種寫法（每組第一個是繁體、第二個是簡體）
SCRIPT_PAIRS = [
    ("評價", "评价"),
    ("記得", "记得"),
    ("對方", "对方"),
    ("說話", "说话"),
    ("東西", "东西"),
    ("狀態", "状态"),
    ("書", "书"),
    ("處理", "处理"),
    ("不會", "不会"),
    ("確認", "确认"),
]

# 一個簡繁對應有多種寫法的字，收錄任何一個都會改變詞義
AMBIGUOUS_CHARS = "后里面松干只云冲制板丑范谷姜借划汇复发折台系征准筑布尽秘舍几才"

# 必須**留在池外**的真主題 / 專有名詞
MUST_STAY_CONTENTFUL = ["顏色", "鬼族", "麻衣", "雷姆", "瑠夏", "口味", "書", "角"]


def _add(store, mid, tags, *, confidence=0.8, category="preference", created_at=1000.0):
    store.add(Memory(
        memory_id=mid, agent_id=AGENT, content=" ".join(tags), tags=list(tags),
        created_at=created_at, category=category, confidence=confidence,
    ))


def _loader(tmp_path):
    return MemoryLoader(V1Store(tmp_path / "data" / "memory", AGENT), None)


def _ids(loader, query):
    return [m.memory_id for m in
            loader.load(derive_query_tags(query), AGENT)["eligible_memories"]]


# ─────────────────────────────────────────────────────────────
# 缺陷 A：簡繁字形正規化
# ─────────────────────────────────────────────────────────────


class TestScriptNormalization:
    def test_traditional_and_simplified_expand_identically(self):
        """兩端字形一致 —— 這是整個缺陷 A 的修復點本身。"""
        for trad, simp in SCRIPT_PAIRS:
            assert _expand_tag_components([trad]) == _expand_tag_components([simp]), (
                f"{trad} 與 {simp} 展開結果不同："
                f"{sorted(_expand_tag_components([trad]))} vs "
                f"{sorted(_expand_tag_components([simp]))}"
            )

    def test_normalization_is_idempotent(self):
        """已正規化的字串再跑一次不得改變（轉換不該有副作用）。"""
        for _, simp in SCRIPT_PAIRS:
            once = _normalize_script(simp)
            assert _normalize_script(once) == once, f"{simp} 轉換不具冪等性"

    def test_traditional_input_is_untouched(self):
        for trad, _ in SCRIPT_PAIRS:
            assert _normalize_script(trad) == trad, (
                f"繁體字串不得被這個轉換改動：{trad} -> {_normalize_script(trad)}"
            )

    def test_mapping_has_no_self_entries(self):
        bad = {k: v for k, v in _SIMPLIFIED_TO_TRADITIONAL.items() if k == v}
        assert not bad, f"不得自我對映：{bad}"

    def test_mapping_has_no_chained_entries(self):
        """value 不得同時是某個 key，否則單遍掃描會產生非預期的二次轉換。"""
        chained = {k: v for k, v in _SIMPLIFIED_TO_TRADITIONAL.items() if v in _SIMPLIFIED_TO_TRADITIONAL}
        assert not chained, f"鏈式對映：{chained}"

    def test_mapping_has_no_ambiguous_entries(self):
        """歧義字黑名單一字都不准出現在對照表裡。

        收錄歧義字比漏召回更糟：它會**靜默**把正確的詞換成錯的，
        不會報錯，只會讓召回在沒有任何訊號的情況下劣化。
        """
        offenders = sorted(set(_SIMPLIFIED_TO_TRADITIONAL) & set(AMBIGUOUS_CHARS))
        assert not offenders, f"對照表收錄了歧義字：{offenders}"

    def test_both_sides_of_the_query_are_normalized(self, tmp_path):
        """端到端：記憶用簡體 tag、查詢用繁體字，仍然必須召得回。

        ⚠️ fixture 刻意用**兩字以上**的詞：單字 CJK 會被 derive_query_tags 的
        `len <= 1` 規則濾掉（既有機制，非本票範圍），用它做 fixture 會測不到東西。
        """
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "simp_tag", ["评价"])
        # ⚠️ 簡體詞刻意放在**非第一個**位置：只正規化第一個 tag 的變異
        #    （`_i == 0` 型態）必須被這條抓到，否則多標籤記憶會漏掉第二個之後的詞。
        _add(store, "simp_tag2", ["麻衣", "处理", "记得"])
        loader = MemoryLoader(store, None)
        assert "simp_tag" in _ids(loader, "評價"), "繁體查詢必須命中簡體 tag"
        assert "simp_tag2" in _ids(loader, "處理"), (
            "繁體查詢必須命中「非第一個」位置上的簡體 tag"
        )


# ─────────────────────────────────────────────────────────────
# 缺陷 B：無主題的泛化謂語 / 話語支架詞不得單獨開 Gate
# ─────────────────────────────────────────────────────────────


class TestOffTopicGate:
    def _corpus(self, tmp_path):
        """三筆：都在乎/記得某件事，但沒有任何一筆的主題是「書」。"""
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "likes_listening", ["喜歡", "聽"])
        _add(store, "remembers_something", ["記得", "事情"])
        _add(store, "speaker_said", ["說話者", "表示"])
        return MemoryLoader(store, None)

    def test_generic_predicate_alone_does_not_open_the_gate(self, tmp_path):
        loader = self._corpus(tmp_path)
        assert _ids(loader, "喜歡") == [], "只命中「喜歡」不得召回任何記憶"
        assert _ids(loader, "記得") == [], "只命中「記得」不得召回任何記憶"
        assert _ids(loader, "說話者") == [], "只命中「說話者」不得召回任何記憶"

    def test_off_topic_question_returns_nothing(self, tmp_path):
        """完整重現生產病灶：問「你喜歡看書嗎」，庫裡沒有書，應回 0。"""
        loader = self._corpus(tmp_path)
        assert _ids(loader, "你喜歡看書嗎") == [], (
            "庫裡沒有「書」時必須誠實回空，不得拿「喜歡聽」冒充"
        )

    def test_real_topic_still_opens_the_gate(self, tmp_path):
        """收編不得過度修法：命中真主題詞時必須仍然召回。

        主題詞用兩字的「讀書／聽歌」——單字 CJK 會被 derive_query_tags 濾掉。
        """
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "likes_books", ["喜歡", "讀書"])
        _add(store, "likes_listening", ["喜歡", "聽歌"])
        loader = MemoryLoader(store, None)
        ids = _ids(loader, "你喜歡讀書嗎")
        assert ids == ["likes_books"], f"命中真主題的記憶必須召回且排第一：{ids}"

    def test_generic_predicate_acts_as_boost_not_as_gate(self, tmp_path):
        """「喜歡」在 Gate 失效後，必須轉成 SAGE-RANK-1 的 Secondary Boost。"""
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "plain_book", ["讀書"])
        _add(store, "likes_book", ["讀書", "喜歡"])
        loader = MemoryLoader(store, None)
        # Boost 來自「查詢端」命中的低資訊詞，所以 query 必須真的含「喜歡」。
        result = loader.load(derive_query_tags("你喜歡讀書嗎"), AGENT)
        rows = {r["memory_id"]: r for r in result["trace"]["candidates"]}
        assert rows["likes_book"]["boost_score"] > rows["plain_book"]["boost_score"], (
            "帶泛化謂語者應獲得 Boost 加分"
        )
        assert rows["likes_book"]["primary_score"] == rows["plain_book"]["primary_score"]
        assert [m.memory_id for m in result["eligible_memories"]][0] == "likes_book"

    def test_proper_nouns_are_not_low_info(self):
        """角色專名 df 很高，但那代表「常被提到」，不是「沒區辨力」。"""
        for name in ("雷姆", "瑠夏", "麻衣", "真昼", "三玖"):
            assert name not in _LOW_INFO_TOKENS, (
                f"專有名詞不得收進低資訊池：{name}（df 高 ≠ 零區辨力）"
            )

    def test_real_content_words_are_not_low_info(self):
        """泛化謂語的受詞才是主題本身，必須留在池外。"""
        for w in MUST_STAY_CONTENTFUL:
            assert w not in _LOW_INFO_TOKENS, f"真主題詞不得收進低資訊池：{w}"


# ─────────────────────────────────────────────────────────────
# 既有契約不得被破壞（SOUL-BUG-002 與切詞階段的護欄）
# ─────────────────────────────────────────────────────────────


class TestExistingContractsIntact:
    def test_original_time_and_question_words_still_present(self):
        for w in ("今天", "昨天", "現在", "什麼", "怎麼", "怎麼樣", "為什麼", "誰"):
            assert w in _LOW_INFO_TOKENS, f"SOUL-BUG-002 的既有詞不得被移除：{w}"

    def test_no_entry_longer_than_three_chars(self):
        """反作弊護欄（沿用 test_sage_low_info_gate）：只收詞類，不收複合片語。"""
        too_long = [t for t in _LOW_INFO_TOKENS if len(t) > 3]
        assert not too_long, f"過長（疑似複合詞）的條目：{too_long}"

    def test_does_not_overlap_function_word_stoplist(self):
        """低資訊實詞不得出現在功能詞停用詞表（兩表語意不同、不可合併）。"""
        assert not (_LOW_INFO_TOKENS & _SHARED_STOPWORDS), (
            f"發現重疊：{sorted(_LOW_INFO_TOKENS & _SHARED_STOPWORDS)}"
        )

    def test_plain_time_question_query_still_returns_nothing(self, tmp_path):
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "m1", ["bryan", "起得", "很早", "今天"])
        loader = MemoryLoader(store, None)
        assert _ids(loader, "今天 天氣 怎麼 樣") == []