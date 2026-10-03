"""EH-4.2-DEF-1 — 定義句繫詞邊界修正（兩個缺陷同一根因）。

缺陷 ①「五元組 subject 污染」：subject 被抽成「氣炸鍋**就**」。
缺陷 ②「定義句不產生錨點」：打標寫入 subject=「氣炸鍋就」，
        之後 `get_idiolect_facts()` 以「氣炸鍋」檢索命中不了。

根因：`_RELATION_PATTERNS` 的 `(r"(.+?)是(.+)", "是", 1.0)`。
`re.search` 取最左匹配、`.+?` 非貪婪逐字試 ⇒ 對「氣炸鍋就是個…」會把「就」
留在 group(1)；「就是」是中文**複合繫詞**，本不可拆開。

修法：邊界修正（非關鍵字黑名單）—— `就是` 列為原子 alternative 且排在 `是` 之前。

**全部隔離**：使用 tmp data_root，0 production mutation。
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@pytest.fixture()
def writer(tmp_path, monkeypatch):
    """隔離的 MemoryWriter（USE_LLM_JUDGE=false → 純 heuristic 路徑）。

    🔴 簽章依 `writer.py:158-167`：第一個位置參數是 `graph_store`，
    `agent_id` 是**關鍵字參數**且預設 ""（初版誤當位置參數）。
    """
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("USE_LLM_JUDGE", "false")
    from src.paths import reset_data_root

    reset_data_root()
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.writer import MemoryWriter

    gs = GraphStore(db_path=tmp_path / "graph.sqlite")
    w = MemoryWriter(gs, agent_id="agent_rem")
    try:
        yield w
    finally:
        for closer in (getattr(w, "close", None), getattr(gs, "close", None)):
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        reset_data_root()


def _extract(writer, text):
    facts, _ = writer._extract_facts_heuristic_fallback(
        text, subject_hint=None, session_id="test_eh42", source="inference"
    )
    return facts


# ── 缺陷 ①：subject 不得含語氣詞污染 ────────────────────────────

def test_definition_sentence_subject_is_clean(writer):
    """「氣炸鍋就是個…」的 subject 必須是「氣炸鍋」，不得是「氣炸鍋就」。"""
    facts = _extract(writer, "氣炸鍋就是個插電烤熟食物的箱子。")
    assert facts, "定義句應抽出至少一筆 fact"
    subs = [f.subject for f in facts]
    assert "氣炸鍋" in subs, f"subject 應為「氣炸鍋」，實得 {subs}"
    assert "氣炸鍋就" not in subs, f"subject 被繫詞污染：{subs}"


def test_predicate_keeps_compound_copula_semantics(writer):
    """繫詞整體歸入 predicate 語意，object 不得殘留「就是」。"""
    facts = _extract(writer, "氣炸鍋就是個插電烤熟食物的箱子。")
    objs = " ".join(f.object for f in facts)
    assert "就是" not in objs, f"object 殘留繫詞：{objs}"


# ── 缺陷 ②：定義句必須能產生錨點（可被以「氣炸鍋」檢索命中）─────

def test_definition_sentence_produces_searchable_subject(writer):
    """錨點可被**乾淨的實體名**檢索命中 —— 這是缺陷②的直接驗收。"""
    facts = _extract(writer, "氣炸鍋就是個插電烤熟食物的箱子。")
    assert any(f.subject == "氣炸鍋" for f in facts), (
        f"以「氣炸鍋」檢索必須命中，實得 {[f.subject for f in facts]}"
    )


# ── 保持既有合法行為 ────────────────────────────────────────────

def test_plain_copula_still_splits_correctly(writer):
    """單純繫詞「是」不得受影響（非複合繫詞仍照原樣切）。"""
    facts = _extract(writer, "雷姆是羅茲瓦爾公館的女僕。")
    subs = [f.subject for f in facts]
    assert any(s.startswith("雷姆") for s in subs), f"實得 {subs}"
    assert not any("就是" in s for s in subs), f"實得 {subs}"


def test_other_relations_unchanged(writer):
    """其他關係型不得被繫詞邊界修正波及。"""
    facts = _extract(writer, "雷姆喜歡做蘋果塔。")
    assert any(f.predicate in ("喜歡", "likes") for f in facts), (
        f"實得 {[(f.subject, f.predicate) for f in facts]}"
    )


def test_non_definition_sentence_unaffected(writer):
    """非定義句（無繫詞）行為不變。"""
    facts = _extract(writer, "今天下午我去公園散步，看到花開了。")
    # 本句不含繫詞/關係動詞，抽出與否皆屬既有邊界；只要求不拋例外
    assert isinstance(facts, list)


def test_pattern_is_boundary_fix_not_keyword_blacklist():
    """結構斷言：修的是**繫詞邊界**，不是把污染字串加進黑名單。

    檢查兩件事：
      ① `_RELATION_PATTERNS` 的繫詞樣式含複合繫詞 `就是` 作原子 alternative
         （且排在單獨 `是` 之前，否則 re.search 仍會先命中單獨「是」）。
      ② `_normalize_entity` 內**沒有**針對特定字串的 `.replace()/strip()`
         特例處理（那會是 keyword blacklist，正是本票明文禁止的做法）。
    """
    import ast
    import inspect

    from src.memory.sage import writer as W
    from src.memory.sage.writer import MemoryWriter

    pats = [(p, pred) for p, pred, _w in W._RELATION_PATTERNS if pred == "是"]
    assert pats, "應存在 predicate=是 的樣式"
    # `就是` 必須是獨立 alternative，且 index 小於單獨 `是`
    ok = False
    for pat, _pred in pats:
        if "(?:就是|是)" in pat or "|就是|" in pat:
            alt = pat.split("(", 1)[1]
            ok = alt.find("就是") < alt.rfind("是")
            break
    assert ok, f"繫詞樣式必須把「就是」列為排在「是」之前的原子 alternative：{pats}"

    # ② _normalize_entity 不得含針對特定污染字串的特例替換
    fn = inspect.getsource(MemoryWriter._normalize_entity)
    tree = ast.parse(inspect.cleandoc(fn).replace("    ", "", 1)
                     if False else fn.lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("replace", "rstrip", "lstrip", "strip"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        assert len(arg.value) >= 2, (
                            f"_normalize_entity 出現單字元特例 {arg.value!r} —— "
                            f"疑似 keyword blacklist（本票禁止）"
                        )
