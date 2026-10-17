# -*- coding: utf-8 -*-
"""
tests/memory/test_sage_rank1_loader_ordering.py
SAGE-RANK-1（2026-10-17，Owner 裁示「案 C：Corpus-Local IDF 加權 ＋ Boost 排序」）

**本檔只測排序，不測召回。** SOUL-BUG-002 的 Gate 契約由
`tests/memory/test_sage_low_info_gate.py` 負責釘死；本檔另有一組
「排序不得改變入選集合」的守衛，確保 RANK-1 是**純排序**而非另一套過濾。

釘死四件事：
1. **稀有實詞必須贏高頻通用詞**（這是本票存在的理由）。
2. **Boost 側生效**：同實詞權重下，帶時間詞者浮上來。
3. **權重比例不可被「順手」改壞** —— 釘死 Bry 親自校準的那個陷阱：
   高 confidence 命中高頻廢詞，**不得**反超低 confidence 命中稀有詞的記憶。
4. **100% 確定性**：confidence → created_at → memory_id 三段平手破除。

全部用 temp store，不讀生產語料、不發任何網路請求。
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.v1.loader import (
    MemoryLoader,
    derive_query_tags,
    _corpus_idf,
    RANK_IDF_WEIGHT,
    RANK_LOW_INFO_BOOST,
    RANK_IDF_SMOOTHING,
)
from src.memory.v1.store import Memory, V1Store

AGENT = "agent_rank1_test"
OTHER_AGENT = "agent_rank1_other"

# 高頻通用詞（df 高 → IDF 低）。
# ⚠️ SOUL-BUG-007（2026-10-17）把「記得」「喜歡」「說話者」等收進 _LOW_INFO_TOKENS
#    之後，這些詞**連 Gate 都開不了**，因此本檔的 fixture **不得**用它們當高頻詞，
#    否則 eligible 會直接歸零、測試測到的東西完全變樣。
#    （這個衝突真的發生過：BUG-007 上線後本檔 4 支測試轉紅，fixture 必須換詞。）
#    改用「事情」—— 它正是生產語料裡 df 極高、卻因為帶有實質語意而留在 Gate 外的詞。
GENERIC = "事情"
GENERIC2 = "味道"
# 稀有專有名詞
RARE = "鬼族"
# 低資訊／時間詞（Boost 側的加分來源）
TIMEWORD = "今天"


def _assert_fixture_words_pass_the_gate():
    """反守衛：fixture 用的高頻詞必須在 Gate 外，否則本檔整組測試失去意義。"""
    from src.memory.v1.loader import _LOW_INFO_TOKENS
    for w in (GENERIC, GENERIC2, RARE):
        assert w not in _LOW_INFO_TOKENS, (
            f"fixture 詞 {w} 已被收進低資訊池，無法通過 Gate —— 請換一個 fixture 詞"
        )


def _add(store, mid, tags, *, agent=AGENT, confidence=0.8,
         category="preference", created_at=1000.0, content=None):
    store.add(Memory(
        memory_id=mid,
        agent_id=agent,
        content=content if content is not None else " ".join(tags),
        tags=list(tags),
        created_at=created_at,
        category=category,
        confidence=confidence,
    ))


def _build_generic_heavy(tmp: Path, n_generic=8):
    """n_generic 筆高 DF 通用詞記憶 ＋ 1 筆稀有詞記憶。"""
    store = V1Store(tmp / "data" / "memory", AGENT)
    for i in range(n_generic):
        _add(store, f"generic_{i}", [GENERIC], created_at=1000.0 + i)
    _add(store, "rare_kitsune", [RARE], created_at=2000.0)
    return MemoryLoader(store=store, trace_log_path=None)


def _ids(loader, query, agent=AGENT):
    result = loader.load(derive_query_tags(query), agent)
    return [m.memory_id for m in result["eligible_memories"]]


# ─────────────────────────────────────────────────────────────
# 1. 主題：稀有實詞必須贏高頻通用詞
# ─────────────────────────────────────────────────────────────


class TestRareWinsOverGeneric:
    def test_fixture_words_are_actually_reachable(self, tmp_path):
        """先確認 fixture 詞過得了 Gate —— 否則下面所有斷言都是恆真的。"""
        _assert_fixture_words_pass_the_gate()

    def test_rare_term_ranks_above_high_df_generic(self, tmp_path):
        """`鬼族`（df=1）必須排在 `記得`（df=8）之前。

        這是本票存在的唯一理由。修改前這些記憶是**建立順序**輸出，
        稀有詞完全沒有被優先的機會。
        """
        ids = _ids(_build_generic_heavy(tmp_path), f"{GENERIC} {RARE}")
        assert ids[0] == "rare_kitsune", (
            f"稀有詞記憶必須排第一，實際順序={ids}"
        )
        assert ids.count("rare_kitsune") == 1
        assert len(ids) == 9, "排序不得改變入選集合的組成"

    def test_ranking_is_independent_of_insertion_order(self, tmp_path):
        """先插弱的後插強 / 先插強的後插弱的 —— 排序結果必須一致。

        這條直接封死「把排序寫成依賴輸入順序」也騙得過測試的退路。
        """
        weak_first = V1Store(tmp_path / "a" / "memory", AGENT)
        for i in range(8):
            _add(weak_first, f"generic_{i}", [GENERIC], created_at=1000.0 + i)
        _add(weak_first, "rare_kitsune", [RARE], created_at=2000.0)

        strong_first = V1Store(tmp_path / "b" / "memory", AGENT)
        _add(strong_first, "rare_kitsune", [RARE], created_at=2000.0)
        for i in range(8):
            _add(strong_first, f"generic_{i}", [GENERIC], created_at=1000.0 + i)

        a = _ids(MemoryLoader(weak_first, None), f"{GENERIC} {RARE}")
        b = _ids(MemoryLoader(strong_first, None), f"{GENERIC} {RARE}")
        assert a == b, f"排序結果不得依賴插入順序：{a} vs {b}"

    def test_corpus_is_per_agent(self, tmp_path):
        """IDF 必須是 corpus-local：別隻 agent 的記憶不得影響本 agent 的任何分數。

        這條釘在**分數**而不是順序上。因為把 N 灌大只會讓「稀有詞相對更稀有」，
        稀有詞本來就贏，順序常常不會翻 —— 順序層的斷言會變恆真式。
        而分數是直接可觀察的：同一筆記憶在兩種 store 下必須算出**完全相同**的
        primary_score，否則代表 df/N 統計跨越了 agent 邊界。
        """
        clean = V1Store(tmp_path / "clean" / "memory", AGENT)
        for i in range(8):
            _add(clean, f"generic_{i}", [GENERIC], created_at=1000.0 + i)
        _add(clean, "rare_kitsune", [RARE], created_at=2000.0)

        polluted = V1Store(tmp_path / "polluted" / "memory", AGENT)
        # 別隻 agent 塞 500 筆同一個詞
        for i in range(500):
            _add(polluted, f"other_{i}", [RARE], agent=OTHER_AGENT,
                 created_at=1000.0 + i)
        for i in range(8):
            _add(polluted, f"generic_{i}", [GENERIC], created_at=1000.0 + i)
        _add(polluted, "rare_kitsune", [RARE], created_at=2000.0)

        query = derive_query_tags(f"{GENERIC} {RARE}")

        def score_of(store):
            result = MemoryLoader(store, None).load(query, AGENT)
            rows = {r["memory_id"]: r for r in result["trace"]["candidates"]}
            return rows["rare_kitsune"]["primary_score"]

        assert score_of(clean) == score_of(polluted), (
            "別隻 agent 的 500 筆記憶改變了本 agent 的 idf —— "
            "df/N 統計必須限縮在 agent 自己的記憶庫內"
        )


# ─────────────────────────────────────────────────────────────
# 2. Boost 側：同實詞權重下，帶時間詞者加分
# ─────────────────────────────────────────────────────────────


class TestLowInfoBoost:
    def _tie_corpus(self, tmp_path):
        """兩筆記憶命中同一個稀有詞，df 相同 → primary 完全相同。"""
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "no_boost", [RARE], created_at=1000.0, confidence=0.9)
        _add(store, "with_boost", [RARE, TIMEWORD], created_at=1000.0, confidence=0.9)
        return MemoryLoader(store, None)

    def test_timeword_hit_gets_boost(self, tmp_path):
        ids = _ids(self._tie_corpus(tmp_path), f"{RARE} {TIMEWORD}")
        assert ids[0] == "with_boost", (
            f"同實詞權重下，帶時間詞者必須浮上來：{ids}"
        )

    def test_boost_score_is_reported_in_trace(self, tmp_path):
        loader = self._tie_corpus(tmp_path)
        result = loader.load(derive_query_tags(f"{RARE} {TIMEWORD}"), AGENT)
        rows = {r["memory_id"]: r for r in result["trace"]["candidates"]}
        assert rows["with_boost"]["boost_score"] == RANK_LOW_INFO_BOOST
        assert rows["no_boost"]["boost_score"] == 0.0
        # 兩者 primary 必須相等（否則這支測試測的不是 boost）
        assert rows["with_boost"]["primary_score"] == rows["no_boost"]["primary_score"]

    def test_boost_cannot_outvote_a_real_word_hit(self, tmp_path):
        """一個實詞命中（保底 10.0 分）必須贏過 9 個低資訊詞（9.0 分）。

        這是權重比例的數學下界：RANK_IDF_WEIGHT × 保底 1.0 = 10.0。
        任何人把 RANK_IDF_WEIGHT 調小、或把 boost 調大，這條就會紅。
        """
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        # 9 個不同的低資訊詞，讓對方拿到滿額 boost
        low_words = ["今天", "昨天", "最近", "現在", "剛剛", "剛才", "待會", "明天", "此刻"]
        _add(store, "many_boost", low_words, created_at=2000.0, confidence=0.9)
        _add(store, "one_real", [RARE], created_at=1000.0, confidence=0.9)
        loader = MemoryLoader(store, None)

        ids = _ids(loader, f"{RARE} " + " ".join(low_words))
        assert ids[0] == "one_real", (
            f"單一實詞命中必須贏過滿額低資訊 boost：{ids}"
        )


# ─────────────────────────────────────────────────────────────
# 3. Bry 親自校準的權重陷阱（最關鍵的一條）
# ─────────────────────────────────────────────────────────────


class TestWeightCalibration:
    def test_high_confidence_generic_never_beats_low_confidence_rare(self, tmp_path):
        """conf=0.95 命中高頻廢詞  **不得** 贏過 conf=0.75 命中稀有詞。

        這正是 Owner 2026-10-17 校準權重比例時指出的陷阱：
        若把 confidence 乘 10 混進加權和，兩筆的總分會是
          廢詞：1.2 + 9.5 = 10.7
          稀有：5.2 + 7.5 = 12.7
        而廢詞若命中兩個（「記得＋事情」）就會 13.7 反超。
        ⇒ confidence **只能**當平手破除器，不得進加權和。
        """
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        # ⚠️ 灌水詞必須讓 df 真的「高」：filler 同時帶 記得/事情，
        #    否則 事情 只出現在灌水記憶裡、df=1、反而變成稀有詞，
        #    這個測試就會測到相反的東西（這是本檔初版真的踩到的坑）。
        for i in range(8):
            _add(store, f"filler_{i}", [GENERIC, GENERIC2],
                 created_at=1000.0 + i)
        # 灌水記憶：confidence 最高，命中的是滿庫都是的通用詞
        _add(store, "confident_generic", [GENERIC, GENERIC2],
             confidence=0.95, created_at=9000.0)
        # 精準記憶：confidence 剛好過門檻，命中的是全庫只有它的稀有詞
        _add(store, "humble_rare", [RARE],
             confidence=0.75, created_at=1000.0)

        loader = MemoryLoader(store, None)
        ids = _ids(loader, f"{GENERIC} {GENERIC2} {RARE}")
        assert ids.index("humble_rare") < ids.index("confident_generic"), (
            f"稀有詞命中必須贏過高 confidence 灌水記憶：{ids}"
        )

    def test_idf_weight_is_dominant_scale(self, tmp_path):
        """權重常數不得被「順手」調整 —— 它們是上面那條陷阱的數學前提。"""
        assert RANK_IDF_WEIGHT == 10.0
        assert RANK_LOW_INFO_BOOST == 1.0
        assert RANK_IDF_SMOOTHING == 1.0

    def test_idf_is_always_positive_and_decreasing(self):
        """保底恆正 + 隨 df 單調遞減 —— 這是整個排序能成立的前提。"""
        N = 400
        prev = None
        for df in range(0, N + 1):
            v = _corpus_idf(df, N)
            assert v >= RANK_IDF_SMOOTHING, f"idf(df={df})={v} 低於保底 {RANK_IDF_SMOOTHING}"
            if prev is not None:
                assert v < prev, f"idf 必須隨 df 嚴格遞減：df={df} → {v}，前一格 {prev}"
            prev = v


# ─────────────────────────────────────────────────────────────
# 4. 確定性 tie-break
# ─────────────────────────────────────────────────────────────


class TestDeterministicTieBreak:
    # ⚠️ 每個 tie-break 維度的 fixture 都刻意讓**其餘維度與預期方向相反**。
    #    否則拿掉該維度後，剩下的破除器會剛好給出同樣的答案 ⇒ 測試變恆真式。
    #    （這個缺陷在第一版變異實驗中真的讓 MUT-08 變綠。）

    def test_confidence_breaks_tie(self, tmp_path):
        # 高 confidence 的記憶刻意給「較大」的 memory_id：
        # 拿掉 confidence 後會由 memory_id 判成 a_low 在前 ⇒ 本測試轉紅。
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "a_low", [RARE], confidence=0.76, created_at=1000.0)
        _add(store, "z_high", [RARE], confidence=0.95, created_at=1000.0)
        loader = MemoryLoader(store, None)
        ids = _ids(loader, RARE)
        assert ids == ["z_high", "a_low"], (
            f"完全同分時必須由 confidence 破除：{ids}"
        )

    def test_newer_wins_when_confidence_ties(self, tmp_path):
        # 較新的記憶刻意給「較大」的 memory_id，理由同上。
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "a_old", [RARE], confidence=0.9, created_at=1000.0)
        _add(store, "z_new", [RARE], confidence=0.9, created_at=5000.0)
        loader = MemoryLoader(store, None)
        ids = _ids(loader, RARE)
        assert ids == ["z_new", "a_old"], f"confidence 平手時新的優先：{ids}"

    def test_memory_id_is_the_final_tiebreak(self, tmp_path):
        """連 confidence 與 created_at 都相同 → memory_id 字典序，全序無殘餘不確定性。"""
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "zzz", [RARE], confidence=0.9, created_at=1000.0)
        _add(store, "aaa", [RARE], confidence=0.9, created_at=1000.0)
        loader = MemoryLoader(store, None)
        ids = _ids(loader, RARE)
        assert ids == ["aaa", "zzz"], f"最後由 memory_id 破除：{ids}"

    def test_repeated_loads_are_byte_identical(self, tmp_path):
        """同一 loader 連跑 5 次，順序必須逐位元組相同。"""
        loader = _build_generic_heavy(tmp_path, n_generic=6)
        runs = [tuple(_ids(loader, f"{GENERIC} {RARE}")) for _ in range(5)]
        assert len(set(runs)) == 1, f"排序不具確定性：{runs}"

    def test_primary_score_is_independent_of_input_order(self, tmp_path):
        """🔴 浮點加總不具結合律：同一組 token 的**輸入順序**不得改變分數。

        set 的迭代順序受 PYTHONHASHSEED 影響。若求和不先排序，
        同一份記憶在不同行程可能算出差在最後幾個 ulp 的分數，
        讓平手時的排序在不同機器上不一致 —— 違反 Owner 的「100% 確定性」。

        實測：6 個這類 idf 值共有 720 種排列、會產生 **2 種不同總和**。
        這不是理論風險，是可觀察的。
        """
        import itertools
        from src.memory.v1.loader import _primary_score

        dfs = {"tok_a": 900, "tok_b": 300, "tok_c": 120,
               "tok_d": 45, "tok_e": 12, "tok_f": 3}
        corpus_size = 997
        scores = {
            _primary_score(perm, dfs, corpus_size)
            for perm in itertools.permutations(dfs.keys())
        }
        assert len(scores) == 1, (
            f"主導分數依賴輸入順序，共產生 {len(scores)} 種結果：{sorted(scores)}"
        )


# ─────────────────────────────────────────────────────────────
# 5. 排序是純排序：不得改變入選集合（SOUL-BUG-002 防護不退化）
# ─────────────────────────────────────────────────────────────


class TestGateIsUntouched:
    def test_pure_low_info_query_still_returns_nothing(self, tmp_path):
        loader = _build_generic_heavy(tmp_path)
        for q in ("今天", "最近", "什麼", "今天 最近"):
            assert _ids(loader, q) == [], f"{q!r} 只由低資訊詞構成，不得召回"

    def test_confidence_gate_still_rejects(self, tmp_path):
        """低 confidence 仍被擋 —— 排序只作用在已通過 gate 的集合上。"""
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        _add(store, "too_low_conf", [RARE], confidence=0.4)
        _add(store, "ok_conf", [RARE], confidence=0.8)
        ids = _ids(MemoryLoader(store, None), RARE)
        assert ids == ["ok_conf"], f"confidence gate 不得被排序繞過：{ids}"

    def test_no_metadata_still_rejected(self, tmp_path):
        store = V1Store(tmp_path / "data" / "memory", AGENT)
        store.add(Memory(memory_id="legacy", agent_id=AGENT, content=RARE,
                         tags=[RARE], created_at=1000.0))
        assert _ids(MemoryLoader(store, None), RARE) == [], (
            "沒有 v1.1 metadata 的舊資料仍須 fail-safe"
        )

    def test_membership_equals_unordered_gate_result(self, tmp_path):
        """入選集合必須與「排序前的集合」完全相同 —— 排序是雙射，不是過濾。"""
        loader = _build_generic_heavy(tmp_path, n_generic=5)
        result = loader.load(derive_query_tags(f"{GENERIC} {RARE}"), AGENT)
        eligible = {m.memory_id for m in result["eligible_memories"]}
        selected = {r["memory_id"] for r in result["trace"]["candidates"]
                    if r["status"] == "selected"}
        assert eligible == selected, (
            f"排序不得改變入選集合：eligible={eligible} selected={selected}"
        )


# ─────────────────────────────────────────────────────────────
# 6. 注入量契約：預設不截斷
# ─────────────────────────────────────────────────────────────


class TestTopKContract:
    def test_default_is_no_truncation(self, tmp_path):
        """生產注入量契約：預設必須全量回傳。

        舊簽章預設值是 `3`，但全庫沒有任何呼叫端傳過它。若沿用該預設，
        這次改動會**無預警**把注入量砍到 3 筆。
        """
        loader = _build_generic_heavy(tmp_path, n_generic=8)
        result = loader.load(derive_query_tags(f"{GENERIC} {RARE}"), AGENT)
        assert len(result["eligible_memories"]) == 9

    def test_explicit_top_k_truncates_after_ranking(self, tmp_path):
        loader = _build_generic_heavy(tmp_path, n_generic=8)
        result = loader.load(derive_query_tags(f"{GENERIC} {RARE}"), AGENT, 2)
        ids = [m.memory_id for m in result["eligible_memories"]]
        assert len(ids) == 2, f"明確指定時才截斷：{ids}"
        assert ids[0] == "rare_kitsune", (
            f"截斷必須發生在排序之後、保留最高分者：{ids}"
        )
        # 第 2 名是 8 筆同分的通用詞之一（由 created_at / memory_id 破除），
        # 這裡只斷言它是 generic_*，不釘死是哪一筆 —— 那是 tie-break 的細節。
        assert ids[1].startswith("generic_"), f"第 2 名應為通用詞記憶：{ids}"


# ─────────────────────────────────────────────────────────────
# 7. 端到端：排序 1:1 反映到 prompt（format_for_prompt 是順序透傳的）
# ─────────────────────────────────────────────────────────────


class TestReachesPromptUnchanged:
    def test_ranked_order_reaches_format_for_prompt(self, tmp_path):
        from src.memory.v1.loader import format_for_prompt
        loader = _build_generic_heavy(tmp_path, n_generic=8)
        result = loader.load(derive_query_tags(f"{GENERIC} {RARE}"), AGENT)
        rendered = format_for_prompt(result["eligible_memories"])
        assert rendered.index(RARE) < rendered.index(GENERIC), (
            "稀有詞記憶必須在 prompt 裡排在通用詞記憶之前"
        )