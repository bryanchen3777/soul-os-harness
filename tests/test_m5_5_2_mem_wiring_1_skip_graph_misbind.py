"""
tests/test_m5_5_2_mem_wiring_1_skip_graph_misbind.py

MEM-WIRING-1 (P0 BUGFIX) — post_reply_commit 位置參數錯綁 (skip_graph misbind) 驗證套件。
工單決策已由 Owner 授權，本套件僅驗證修復，不改任何 production code。

Root cause:
  SAGELiteProvider.post_reply_commit 主路徑以「位置參數」把 source_pair /
  inner_life_event_id 傳進 run_in_executor → MemoryWriter.write_turn。
  write_turn 的簽名是
      write_turn(user_content, assistant_content, session_id,
                 skip_graph=False, source_pair=None, inner_life_event_id=None)
  第 4 位置參數是 skip_graph；非空字串 source_pair (例 "bryan:agent_rem") 在布林
  運算中 truthy → skip_graph=True → 誤跳 graph.sqlite 萃取落庫 (v1 mirror 仍寫)。
  → 帶 source_pair 的 AGENT_SPEAK 事實永遠進不了 graph.sqlite。

修法 (MEM-WIRING-1):
  post_reply_commit 主路徑改用 functools.partial 以 keyword 繫結 source_pair /
  inner_life_event_id；前三個位置參數 (user_content, assistant_content, session_id)
  保留原序。no-diary (agent_ram) 的「有意 skip_graph=True」分支原封不動。

驗收對照 (全部在隔離 temp data_root 執行, 0 生產資料污染):
  - 修復前: source_pair="bryan:agent_rem" → graph.sqlite total_facts == 0 (bug)
  - 修復後: source_pair=None                → total_facts >= 1
            source_pair="bryan:agent_rem"  → total_facts >= 1
  - v1 mirror 兩案皆正常寫入 (行為未破壞)
  - no-diary 有意 skip_graph=True 分支保留 (graph 0 facts + v1 mirror 仍寫)
  - 防回歸 AST 檢查: post_reply_commit 呼叫 write_turn 位置參數不得超過 3 個

Test count: 6
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

from src.memory.sage.provider import NO_DIARY_AGENTS, SAGELiteProvider
from src.memory.v1.store import V1Store
from src.paths import data_root, reset_data_root

PROVIDER_PY = Path(__file__).resolve().parent.parent / "src" / "memory" / "sage" / "provider.py"


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _hex_32() -> str:
    return uuid.uuid4().hex


def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_provider(tmp_path: Path, profile_id: str = "agent_rem", session_id: str = "s1"):
    """Isolated SAGELiteProvider (graph.sqlite 位於 tmp_path/data/memory/<profile>/)."""
    _isolated_data_root(tmp_path)
    soul_dir = tmp_path / "data" / "memory" / profile_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["USE_LLM_JUDGE"] = "false"
    prov = SAGELiteProvider(profile_id=profile_id, data_dir=str(soul_dir))
    prov.initialize(session_id=session_id)
    return prov, soul_dir


def _graph_total(prov: SAGELiteProvider) -> int:
    assert prov._store is not None, "provider 未 initialize"
    return int(prov._store.stats()["total_facts"])


def _v1_count(soul_dir: Path, profile_id: str) -> int:
    v1 = V1Store(soul_dir.parent, profile_id)
    return len(v1.all())


async def _commit(prov, sid, user, agent, source_pair=None, ilid=None) -> None:
    await prov.post_reply_commit(
        sid, user, agent,
        source_pair=source_pair,
        inner_life_event_id=ilid,
    )


def _is_write_turn_ref(node: ast.AST) -> bool:
    """是否為 self._writer.write_turn 的 bare method reference (沒有後綴 () 呼叫)。"""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "write_turn"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_writer"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    )


def _nested_write_turn_refs(call: ast.Call) -> list[ast.AST]:
    """從 call 的 args 遞迴找出 self._writer.write_turn bare reference（含 partial 內層）。"""
    refs: list[ast.AST] = []
    for arg in call.args:
        if _is_write_turn_ref(arg):
            refs.append(arg)
        elif isinstance(arg, ast.Call):
            refs.extend(_nested_write_turn_refs(arg))
    return refs


# ───────────────────────────────────────────────────────────
# A. A/B 核心鐵證: 兩種 source_pair 都必須落 graph.sqlite
# ───────────────────────────────────────────────────────────

class TestA_GraphWriteBothCases:
    """A/B 效果驗證: source_pair=None 與 "bryan:agent_rem" 都寫 graph >= 1 fact."""

    def test_a1_source_pair_none_writes_graph(self, tmp_path):
        """source_pair=None → graph.sqlite 正常落庫 (>= 1 Facts) + v1 mirror 正常。"""
        prov, soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", "Bry likes apples.", "Rem likes onigiri.",
                source_pair=None,
            ))
            n_graph = _graph_total(prov)
            assert n_graph >= 1, f"source_pair=None 竟未寫 graph: total_facts={n_graph}"
            n_v1 = _v1_count(soul_dir, "agent_rem")
            assert n_v1 >= 1, f"v1 mirror 未寫入: {n_v1}"
        finally:
            _restore_data_root()
            prov.shutdown()

    def test_a2_source_pair_string_writes_graph(self, tmp_path):
        """核心鐵證: source_pair='bryan:agent_rem' → graph.sqlite >= 1 Facts。
        修復前此案 total_facts==0 (skip_graph 誤綁), 修復後 >= 1。"""
        prov, soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", "Bry likes apples.", "Rem likes onigiri.",
                source_pair="bryan:agent_rem",
            ))
            n_graph = _graph_total(prov)
            assert n_graph >= 1, (
                f"source_pair='bryan:agent_rem' 未寫 graph (total_facts={n_graph}) — "
                f"skip_graph 誤綁尚未修復"
            )
            n_v1 = _v1_count(soul_dir, "agent_rem")
            assert n_v1 >= 1, f"v1 mirror 未寫入: {n_v1}"
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# B. Keyword 繫結 round-trip: source_pair / inner_life_event_id 精準落位
# ───────────────────────────────────────────────────────────

class TestB_KwargsRoundTrip:
    """兩個 keyword 都必須透過 post_reply_commit → write_turn 完整落庫。"""

    def test_b1_source_pair_and_canonical_eid_land(self, tmp_path):
        """source_pair 與 canonical inner_life_event_id 都要精準落庫。
        修復前: source_pair 被錯綁成 skip_graph (truthy → 跳 graph)，
        inner_life_event_id 被錯綁成 source_pair。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            canonical = _hex_32()
            asyncio.run(_commit(
                prov, "s1", "Bry likes apples.", "Rem likes onigiri.",
                source_pair="bryan:agent_rem", ilid=canonical,
            ))
            facts = list(prov._store.search_by_entity("Bry"))
            assert len(facts) >= 1, "graph 沒有 Bry 的事實"
            for f in facts:
                assert f.source_pair == "bryan:agent_rem", (
                    f"fact.source_pair={f.source_pair!r} != 'bryan:agent_rem'"
                )
                assert f.inner_life_event_id == canonical, (
                    f"fact.inner_life_event_id={f.inner_life_event_id!r} != canonical"
                )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# C. no-diary 有意 skip_graph=True 分支保留
# ───────────────────────────────────────────────────────────

class TestC_NoDiaryIntentPreserved:
    """agent_ram (NO_DIARY_AGENTS): 有意的 skip_graph=True 不得被誤刪。"""

    def test_c1_agent_ram_skip_graph_intent(self, tmp_path):
        assert "agent_ram" in NO_DIARY_AGENTS
        prov, soul_dir = _make_provider(tmp_path, "agent_ram")
        try:
            asyncio.run(_commit(
                prov, "s1", "Bry likes apples.", "Ram likes onigiri.",
                source_pair="bryan:agent_ram",
            ))
            # 有意分支: graph 不寫 (Ram 不寫 diary), 但 v1 mirror 仍寫
            n_graph = _graph_total(prov)
            assert n_graph == 0, f"agent_ram 竟寫了 graph: total_facts={n_graph}"
            n_v1 = _v1_count(soul_dir, "agent_ram")
            assert n_v1 >= 1, f"agent_ram v1 mirror 未寫入: {n_v1}"
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# D. 防回歸 AST 檢查
# ───────────────────────────────────────────────────────────

class TestD_ASTRegressionGuard:
    """防回歸: post_reply_commit 呼叫 write_turn 不得以位置參數傳第 4 位參數。"""

    def test_d1_no_positional_4th_arg_to_write_turn(self):
        src = PROVIDER_PY.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "post_reply_commit"),
            None,
        )
        assert fn is not None, "provider.py 找不到 post_reply_commit"

        run_in_executor_calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run_in_executor"
        ]
        assert len(run_in_executor_calls) >= 1, \
            "post_reply_commit 內找不到 run_in_executor 呼叫"

        checked = 0
        bound_keywords: set[str] = set()
        for call in run_in_executor_calls:
            refs = _nested_write_turn_refs(call)
            if not refs:
                continue
            # 找到承載 write_turn reference 的 arg（可能是裸 ref，或包在 partial() 內）
            idx_ref = next(
                i for i, a in enumerate(call.args)
                if _is_write_turn_ref(a)
                or (isinstance(a, ast.Call) and _nested_write_turn_refs(a))
            )
            # write_turn reference 之後的所有位置參數，會被執行器以位置方式
            # 追加到 write_turn → 不得超過 3 個 (user_content, assistant_content, session_id)
            trailing = len(call.args) - (idx_ref + 1)
            assert trailing <= 3, (
                f"run_in_executor 內 write_turn 之後以位置參數傳了 {trailing} 個"
                f" (不得超過 3) — 第 4+ 個位置參數會錯綁 skip_graph/source_pair"
            )
            checked += 1
            pnode = call.args[idx_ref]
            if (isinstance(pnode, ast.Call)
                    and isinstance(pnode.func, ast.Name)
                    and pnode.func.id == "partial"):
                bound_keywords.update(k.arg for k in pnode.keywords if k.arg)

        assert checked >= 1, \
            "post_reply_commit 內找不到作為執行目標的 write_turn reference"
        assert "source_pair" in bound_keywords, \
            "修法未落地: write_turn 呼叫缺少 source_pair= keyword"
        assert "inner_life_event_id" in bound_keywords, \
            "修法未落地: write_turn 呼叫缺少 inner_life_event_id= keyword"

    def test_d2_no_direct_positional_write_turn_call(self):
        """若未來有人改成直接呼叫 self._writer.write_turn(...)，位置參數也不得超過 3。"""
        src = PROVIDER_PY.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "post_reply_commit"),
            None,
        )
        assert fn is not None, "provider.py 找不到 post_reply_commit"
        direct_calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and _is_write_turn_ref(n.func)
        ]
        for call in direct_calls:
            assert len(call.args) <= 3, (
                f"write_turn 直接呼叫以位置參數傳了 {len(call.args)} 個"
                f" (不得超過 3) — 第 4 位置參數是 skip_graph"
            )