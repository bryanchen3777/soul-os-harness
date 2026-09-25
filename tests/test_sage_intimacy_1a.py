"""SAGE-INTIMACY-1A: post_reply_commit 沉澱計數回傳契約。

契約（本票拍板）:
    post_reply_commit 之**每一條 return 路徑**都回傳統一結構的 dict::

        {"fact_count": int, "tagged_count": int,
         "fact_ids": list[str], "tagged_entities": list[str]}

背景（C2 迴歸鎖）:
    原始碼中 ``tagged_entities`` 只在 ``if fact_ids:`` 分支內首次賦值。
    當 ``fact_ids`` 為空（**純閒聊，最常見情境**）時該分支不進入 ⇒ 若結尾
    return 直接引用 ``tagged_entities`` 會拋 ``NameError``。由於呼叫端
    （MemoryMiddleware._commit_async）是 fire-and-forget 背景 task，此例外
    會被 ``except Exception`` 吞成一行 warning ⇒ **靜默失效**。
    T1 即為此缺陷的機械迴歸鎖。

隔離:
    全部測試以 ``tmp_path`` + ``SOUL_OS_DATA_DIR`` 隔離，**不觸碰生產 data/**。
    零 LLM、零付費 API、零網路（``USE_LLM_JUDGE=false`` 走 heuristic 萃取）。
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.sage.provider import (  # noqa: E402
    NO_DIARY_AGENTS,
    SAGELiteProvider,
)
from src.paths import data_root, reset_data_root  # noqa: E402

PROVIDER_PY = (
    Path(__file__).resolve().parent.parent / "src" / "memory" / "sage" / "provider.py"
)

# 契約欄位（四欄全到齊才算合規回傳）
_EXPECTED_KEYS = {"fact_count", "tagged_count", "fact_ids", "tagged_entities"}


# ───────────────────────────────────────────────────────────
# Helpers（沿用 test_m5_5_2_mem_wiring_1_skip_graph_misbind 的隔離模式）
# ───────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_provider(tmp_path: Path, profile_id: str = "agent_rem", session_id: str = "s1"):
    """Isolated SAGELiteProvider（graph.sqlite 位於 tmp_path/data/memory/<profile>/）。"""
    _isolated_data_root(tmp_path)
    soul_dir = tmp_path / "data" / "memory" / profile_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["USE_LLM_JUDGE"] = "false"
    prov = SAGELiteProvider(profile_id=profile_id, data_dir=str(soul_dir))
    prov.initialize(session_id=session_id)
    return prov, soul_dir


def _commit(prov, sid, user, agent, source_pair=None, ilid=None):
    """回傳 post_reply_commit 結果（本票核心：必須是 dict）。"""
    return asyncio.run(
        prov.post_reply_commit(
            sid, user, agent,
            source_pair=source_pair,
            inner_life_event_id=ilid,
        )
    )


def _seed_definitional_fact(prov, text: str) -> list[str]:
    """直接經 writer 寫入一輪 user 事實，回傳 fact_id 清單。

    用於 T2/T3 的「有 fact」前置條件，繞過 heuristics 抽取的不確定性，
    讓測試對「fact_ids 非空」的前提有機械保證。
    """
    writer = prov._writer
    assert writer is not None, "provider 未 initialize (writer 為 None)"
    ids = writer.extract_and_write(
        text,
        subject_hint="user",
        session_id="s1",
        source="user",
        source_pair="bryan:agent_rem",
        inner_life_event_id=uuid.uuid4().hex,
    )
    prov._store.flush()
    return list(ids)


# ───────────────────────────────────────────────────────────
# T0. 回傳契約：四欄齊備
# ───────────────────────────────────────────────────────────

class TestT0_ContractShape:
    """回傳必須是 dict 且四欄齊備（型別契約，非 None）。"""

    def test_t0_dict_with_all_keys(self, tmp_path):
        prov, _ = _make_provider(tmp_path)
        try:
            res = _commit(prov, "s1", "Bry likes apples.", "Rem likes onigiri.")
            assert isinstance(res, dict), (
                f"post_reply_commit 必須回傳 dict，實得 {type(res).__name__}: {res!r}"
            )
            assert set(res.keys()) == _EXPECTED_KEYS, (
                f"回傳欄位不符契約: 實得 {sorted(res.keys())}，"
                f"期望 {sorted(_EXPECTED_KEYS)}"
            )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# T1. C2 迴歸鎖：fact_ids 為空 ⇒ 不得 NameError
# ───────────────────────────────────────────────────────────

class TestT1_EmptyFactIdsRegression:
    """C2 迴歸鎖：純閒聊（fact_ids 空）時 tagged_entities 未賦值 → 舊碼 NameError。

    機械證明手法：以 monkeypatch 強制 ``write_turn`` 回 ``[]``，**精準製造**
    ``fact_ids`` 為空的情境，再斷言回傳為 dict 且兩計數皆 0。
    舊碼在此必拋 NameError（`tagged_entities` 未綁定）。
    """

    def test_t1a_empty_fact_ids_returns_zero_counts(self, tmp_path):
        prov, _ = _make_provider(tmp_path)
        try:
            # 精準製造 fact_ids == [] ⇒ `if fact_ids:` 分支不進入
            prov._writer.write_turn = lambda *a, **kw: []
            res = _commit(prov, "s1", "今天天氣不錯。", "是啊，適合散步。")
            assert isinstance(res, dict), "空 fact_ids 路徑必須回 dict（舊碼此處 NameError）"
            assert res["fact_count"] == 0
            assert res["tagged_count"] == 0
            assert res["fact_ids"] == []
            assert res["tagged_entities"] == []
        finally:
            _restore_data_root()
            prov.shutdown()

    def test_t1b_tagged_entities_preinitialized_in_source(self):
        """原始碼層級鎖：fact_ids 之前必須有 tagged_entities 的前置初始化。

        AST 檢查 ``post_reply_commit`` 函式體內，是否存在對 ``tagged_entities``
        的 AnnAssign / Assign，且其行號早於 ``if fact_ids:`` 分支中的賦值。
        這讓「移除前置初始化」的變異必然變紅。
        """
        tree = ast.parse(PROVIDER_PY.read_text(encoding="utf-8"))
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "post_reply_commit"),
            None,
        )
        assert fn is not None, "provider.py 找不到 post_reply_commit"

        init_lines: list[int] = []
        for node in ast.walk(fn):
            targets: list[ast.expr] = []
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "tagged_entities":
                    init_lines.append(node.lineno)

        assert init_lines, (
            "post_reply_commit 內找不到 tagged_entities 的賦值 — C2 前置初始化缺失"
        )
        first_if_fact_ids = next(
            (n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.If)
             and isinstance(n.test, ast.Name) and n.test.id == "fact_ids"),
            None,
        )
        assert first_if_fact_ids is not None, "找不到 `if fact_ids:` 分支"
        assert min(init_lines) < first_if_fact_ids, (
            f"tagged_entities 首次賦值 (L{min(init_lines)}) 必須早於 "
            f"`if fact_ids:` (L{first_if_fact_ids}) — 否則空 fact_ids 時 NameError"
        )


# ───────────────────────────────────────────────────────────
# T2. 有 fact、無 tagged
# ───────────────────────────────────────────────────────────

class TestT2_FactsWithoutTagging:
    """有 fact、無 tagged ⇒ fact_count > 0、tagged_count == 0。"""

    def test_t2_facts_no_tagged(self, tmp_path):
        prov, _ = _make_provider(tmp_path)
        try:
            _seed_definitional_fact(prov, "Bry likes apples.")
            prov._writer.write_turn = lambda *a, **kw: [
                "stub-fact-id-1", "stub-fact-id-2"
            ]
            res = _commit(prov, "s1", "Bry likes apples.", "Rem likes onigiri.")
            assert res["fact_count"] == 2, f"fact_count 應為 2，實得 {res['fact_count']}"
            assert res["tagged_count"] == 0, (
                f"無定義句 marker 不應打標，實得 tagged_count={res['tagged_count']}"
            )
            assert res["fact_ids"] == ["stub-fact-id-1", "stub-fact-id-2"]
            assert res["tagged_entities"] == []
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# T3. 有 fact、有 tagged
# ───────────────────────────────────────────────────────────

class TestT3_FactsWithTagging:
    """有 fact、有 tagged ⇒ 兩者皆 > 0（tagged 走真 EH-3.1 閘門）。"""

    def test_t3_facts_and_tagged(self, tmp_path):
        prov, _ = _make_provider(tmp_path)
        try:
            # 定義句（marker「就是個」）⇒ 過 Gate B；subject 為 user fact 之主體
            user_text = "氣炸鍋就是個懶人神器。"
            seed_ids = _seed_definitional_fact(prov, user_text)
            assert seed_ids, "前置條件失敗: heuristic 未萃取出任何 fact"

            res = _commit(prov, "s1", user_text, "原來如此。")
            assert res["fact_count"] > 0, (
                f"定義句輪次應有 fact，實得 {res['fact_count']}"
            )
            assert res["tagged_count"] > 0, (
                f"定義句閘門應打標成功之實體，實得 tagged_count=0 "
                f"(fact_ids={res['fact_ids']})"
            )
            assert res["tagged_entities"], (
                f"tagged_entities 不得為空（tagged_count={res['tagged_count']}）"
            )
            assert all(isinstance(e, str) for e in res["tagged_entities"]), (
                "tagged_entities 元素必須是 str"
            )
            assert res["fact_ids"] == res.get("fact_ids"), "fact_ids 必須存在"
            assert all(isinstance(f, str) for f in res["fact_ids"]), (
                "fact_ids 元素必須是 str"
            )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# T4. no-diary 分支：回 dict 而非 None
# ───────────────────────────────────────────────────────────

class TestT4_NoDiaryBranch:
    """agent_ram (NO_DIARY_AGENTS) ⇒ no-diary 提前 return 也必須回 dict。"""

    def test_t4a_no_diary_returns_dict(self, tmp_path):
        assert "agent_ram" in NO_DIARY_AGENTS, "前置條件: agent_ram 應在 NO_DIARY_AGENTS"
        prov, _ = _make_provider(tmp_path, profile_id="agent_ram")
        try:
            res = _commit(prov, "s1", "Bry likes apples.", "Ram likes onigiri.")
            assert isinstance(res, dict), (
                f"no-diary 分支必須回 dict（舊碼回 None），實得 {type(res).__name__}"
            )
            assert set(res.keys()) == _EXPECTED_KEYS
            assert res["fact_count"] == 0, "skip_graph 路徑不回 fact_id ⇒ fact_count 應為 0"
            assert res["tagged_count"] == 0
        finally:
            _restore_data_root()
            prov.shutdown()

    def test_t4b_no_diary_branch_is_dict_in_source(self):
        """AST 鎖：no-diary 分支的 return 必須帶值（不得是裸 return）。"""
        tree = ast.parse(PROVIDER_PY.read_text(encoding="utf-8"))
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "post_reply_commit"),
            None,
        )
        assert fn is not None
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert len(returns) >= 2, (
            f"post_reply_commit 應有多條 return 路徑（no-diary + 主路徑），實得 {len(returns)}"
        )
        for r in returns:
            assert r.value is not None, (
                f"L{r.lineno}: 發現裸 return（無值）— 所有路徑必須回傳結構化 dict"
            )
            assert isinstance(r.value, ast.Dict), (
                f"L{r.lineno}: return 必須是 dict literal，實得 {type(r.value).__name__}"
            )
            keys = {
                k.value for k in r.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            assert keys == _EXPECTED_KEYS, (
                f"L{r.lineno}: return dict 欄位不符契約: {sorted(keys)}"
            )


# ───────────────────────────────────────────────────────────
# T5. 回傳型別契約 + 簽名凍結
# ───────────────────────────────────────────────────────────

class TestT5_ReturnTypeAndSignature:
    """回傳型別必須是 dict；簽名參數不得變動（AST 結構鎖）。"""

    def test_t5a_annotation_is_dict(self):
        src = PROVIDER_PY.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "post_reply_commit"),
            None,
        )
        assert fn is not None
        assert fn.returns is not None, "post_reply_commit 缺少 return type annotation"
        ann = ast.unparse(fn.returns)
        assert ann == "dict", f"return annotation 應為 `dict`，實得 `{ann}`"

    def test_t5b_signature_frozen(self):
        """位置參數與 keyword 參數名稱/順序全部凍結（M5.5-2 AST 鎖對齊）。

        實檔簽名（實讀確認，非工單假設）:
            (self, session_id, last_user_msg, agent_reply,
             source_pair=None, inner_life_event_id=None)
        source_pair / inner_life_event_id 是 **positional-or-keyword**（非 kwonly）。
        """
        tree = ast.parse(PROVIDER_PY.read_text(encoding="utf-8"))
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "post_reply_commit"),
            None,
        )
        assert fn is not None
        pos = [a.arg for a in fn.args.args]
        assert pos == [
            "self", "session_id", "last_user_msg", "agent_reply",
            "source_pair", "inner_life_event_id",
        ], f"簽名凍結被破壞，實得 {pos}"
        assert fn.args.kwonlyargs == [], (
            f"不得新增 keyword-only 參數，實得 {[a.arg for a in fn.args.kwonlyargs]}"
        )
        assert fn.args.vararg is None and fn.args.kwarg is None, (
            "不得新增 *args / **kwargs"
        )
        # 後兩個參數必須有預設值（呼叫端相容性）
        defaults = [ast.unparse(d) for d in fn.args.defaults]
        assert defaults == ["None", "None"], (
            f"source_pair / inner_life_event_id 預設值必須皆為 None，實得 {defaults}"
        )

    def test_t5c_never_returns_none(self, tmp_path):
        """T5 行為面：實際呼叫不得回 None。"""
        prov, _ = _make_provider(tmp_path)
        try:
            res = _commit(prov, "s1", "hi", "hello")
            assert res is not None, "post_reply_commit 回傳 None — 契約違反"
            assert isinstance(res, dict)
            # 明確排除 bool/int 等「非 None 但非 dict」的退化回傳
            assert not isinstance(res, (bool, int, float, str, list, tuple))
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# T6. 防護：計數記錄失敗不得中斷（middleware 側契約）
# ───────────────────────────────────────────────────────────

class TestT6_MiddlewareFireAndForget:
    """middleware._commit_async 必須維持 fire-and-forget 且計數記錄隔離。"""

    def test_t6a_create_managed_task_preserved(self):
        mw = (Path(__file__).resolve().parent.parent
              / "src" / "memory" / "middleware.py").read_text(encoding="utf-8")
        assert "create_managed_task(_commit_async())" in mw, (
            "_commit_async 必須仍以 create_managed_task 呼叫（fire-and-forget）"
        )
        assert "await _commit_async()" not in mw, (
            "_commit_async 不得被同步 await（M7-latency-fix 73 秒阻塞事故）"
        )

    def test_t6b_metrics_log_uses_actual_names(self):
        """記錄使用實際 logger 名稱與 provider 區域變數（非工單假設的 sage_provider）。"""
        mw = (Path(__file__).resolve().parent.parent
              / "src" / "memory" / "middleware.py").read_text(encoding="utf-8")
        assert "[SAGE-METRICS] session=%s facts=%d tagged=%d" in mw, (
            "缺少結構化 [SAGE-METRICS] 記錄行"
        )
        assert "commit_result.get(\"fact_count\", 0)" in mw
        assert "commit_result.get(\"tagged_count\", 0)" in mw
        # 工單假設 self.sage_provider，實檔為區域變數 provider
        assert "sage_provider" not in mw, (
            "middleware 使用了不存在的 self.sage_provider（實檔為區域變數 provider）"
        )
