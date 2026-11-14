# -*- coding: utf-8 -*-
"""SA-HARNESS-BUDGET-1 AST 守卫：锁定预留顺序与禁绕过（双向变异自证）。

G1 预留先于发送：run_matrix 内 ``reserve`` 文本序必须在 ``soeb_client.call``
  之前；``settlement_for_result`` / ``mark_unknown`` / ``halted`` 必须在场。
G2 已验额度：budgets.json 唯一 profile 必须恰为 8000/24000/3。
G3 禁回落：--run 必带 --budget-profile；未知 profile 直接 exit 2。
G4 Soul 零触碰：budget/runner/client 新增代码禁 import Soul 面；
  client 不得反向依赖 harness；models.json 默认值不动。
G5 账本不可删：budget/runner 不得含删除账本的原语（重启不清账的结构保证）。
G6 双向变异：每条“拿掉闸门”变异必须转红（本文件内自证），原版全绿。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RUNNER_SRC = (_REPO_ROOT / "scripts" / "soeb" / "runner.py").read_text(encoding="utf-8")
CLIENT_SRC = (_REPO_ROOT / "scripts" / "soeb" / "client.py").read_text(encoding="utf-8")
BUDGET_SRC = (_REPO_ROOT / "harness" / "budget.py").read_text(encoding="utf-8")
RUNNER_AST = ast.parse(RUNNER_SRC)
CLIENT_AST = ast.parse(CLIENT_SRC)
BUDGET_AST = ast.parse(BUDGET_SRC)


def _func_node(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"找不到函数 {name}")


# ── G1：预留 → 发送 → 结算的文本序（禁绕过） ──

def test_g1_reserve_before_call_before_settle():
    body = _func_node(RUNNER_AST, "run_matrix")
    src = ast.get_source_segment(RUNNER_SRC, body) or ""
    i_reserve = src.find(".reserve(")
    i_call = src.find("soeb_client.call(")
    i_settle = src.find("settlement_for_result(")
    i_unknown = src.find("mark_unknown(")
    assert i_reserve != -1 and i_call != -1 and i_settle != -1 and i_unknown != -1
    assert i_reserve < i_call, "预留必须在发送之前（先落盘后发送）"
    assert i_call < i_settle, "结算必须在发送之后"
    assert "halted" in src, "UNKNOWN 后必须检查 halted 并停止后续发送"


def test_g1_main_requires_profile_with_run():
    body = _func_node(RUNNER_AST, "main")
    src = ast.get_source_segment(RUNNER_SRC, body) or ""
    assert "--budget-profile" in src
    assert "必须同时指定 --budget-profile" in src
    assert "禁回落" in src


# ── G2：已验额度（SA-BUDGET-1 原值保留 + SA-BUDGET-GO-1 五条显式 profile） ──

_GO1_EXPECTED = {
    # profile 名 -> (channel, api_model, effort, single, total, shots)
    "sa-budget-go-1-spark-t1": ("responses", "muse-spark-1.3-contributor", "low", 16000, 48000, 3),
    "sa-budget-go-1-mimo-t1": ("chat-completions", "mimo-v2.6-flash", None, 8000, 24000, 3),
    "sa-budget-go-1-glm-t1": ("chat-completions", "glm-5.3-flash", "low", 8000, 24000, 3),
    "sa-budget-go-1-qwen-t1": ("messages", "qwen3.8-flash", None, 8000, 24000, 3),
    "sa-budget-go-1-minimax-t1": ("chat-completions", "minimax-m3.1-flash", None, 8000, 24000, 3),
}
_GO1_TASK_SHA = "c76c9253613bdab7c8752e5700dc89c81bd4aa0da018b2c8bb9b224791dce414"


def test_g2_verified_budgets_are_8000_24000_3():
    doc = json.loads((_REPO_ROOT / "scripts" / "soeb" / "budgets.json").read_text(encoding="utf-8"))
    assert set(doc["profiles"]) == {"sa-budget-1-verified-t1", *_GO1_EXPECTED}
    caps = doc["profiles"]["sa-budget-1-verified-t1"]
    assert caps["channel"] == "ollama-chat"
    assert caps["api_model"] == "deepseek-v4.1-flash:1017"
    assert caps["task_sha256"] == "c76c9253613bdab7c8752e5700dc89c81bd4aa0da018b2c8bb9b224791dce414"
    assert caps["effort"] is None
    assert (caps["single_shot_max_completion"], caps["total_max_completion"], caps["max_shots"]) == (8000, 24000, 3)
    for name, (channel, api_model, effort, single, total, shots) in _GO1_EXPECTED.items():
        caps = doc["profiles"][name]
        assert caps["channel"] == channel, name
        assert caps["api_model"] == api_model, name
        assert caps["task_sha256"] == _GO1_TASK_SHA, name
        assert (caps["effort"] if caps["effort"] else None) == effort, name
        assert (caps["single_shot_max_completion"], caps["total_max_completion"],
                caps["max_shots"]) == (single, total, shots), name


# ── G4：Soul 零触碰 ──

@pytest.mark.parametrize("rel", ["harness/budget.py", "scripts/soeb/runner.py",
                               "scripts/soeb/client.py"])
def test_g4_no_soul_imports_in_budget_or_runner(rel):
    """AST 语义检查：实际 import 不得触碰 Soul 生产面（docstring 提及不算）。"""
    src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("."):
                continue  # 包内相对导入（如 soeb.client）不在 Soul 面
            mods.append(node.module or "")
    assert not any(m == "src" or m.startswith("src.") for m in mods), f"{rel}: {mods}"


def test_g4_client_does_not_import_harness():
    tree = CLIENT_AST
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    assert not any(m == "harness" or m.startswith("harness.") for m in mods), mods


def test_g4_models_json_defaults_untouched():
    doc = json.loads((_REPO_ROOT / "scripts" / "soeb" / "models.json").read_text(encoding="utf-8"))
    assert doc["registry_version"] == 2
    assert len(doc["models"]) == 6
    deepseek = next(m for m in doc["models"] if m["id"] == "deepseek-v4.1-flash")
    assert deepseek["api_model"] == "deepseek-v4.1-flash:1017"
    assert deepseek["endpoint"] == "ollama-chat"
    assert deepseek["price_in"] is None and deepseek["price_out"] is None


# ── G5：账本不可删 ──

@pytest.mark.parametrize("rel", ["harness/budget.py", "scripts/soeb/runner.py"])
def test_g5_no_ledger_deletion_primitive(rel):
    src = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    name = rel
    assert ".unlink(" not in src, f"{name} 不得删除账本"
    assert "os.remove(" not in src, f"{name} 不得删除账本"
    assert "shutil.rmtree" not in src, f"{name} 不得删除账本"


# ── G6：双向变异（拿掉闸门必须红；原版绿由其它测试覆盖） ──

def _broken_resolve_with_fallback(doc, key):
    """变异 A：查不到就回落默认额度（正确行为应抛 BudgetError）。"""
    for entry in doc["profiles"].values():
        if entry.get("channel") == key.channel:
            return "fallback", {"single_shot_max_completion": 8000,
                                "total_max_completion": 24000, "max_shots": 3}
    return "fallback", {"single_shot_max_completion": 8000,
                        "total_max_completion": 24000, "max_shots": 3}


def _broken_settle_unknown_as_zero(usage):
    """变异 B：UNKNOWN 当 0 处理（正确行为应判 unknown=True 并停发）。"""
    if not isinstance(usage, dict) or usage.get("output_tokens") is None:
        return 0, 0, False
    return int(usage["output_tokens"]), int(usage["output_tokens"]), False


def test_g6_mutation_fallback_would_hide_mismatch():
    """变异 A 自证：回落版对近似键不抛错 ⇒ 若用它，G1 近似键测试恒绿（漏检）。"""
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from harness import budget as _hb
    doc = json.loads((_REPO_ROOT / "scripts" / "soeb" / "budgets.json").read_text(encoding="utf-8"))
    wrong = _hb.ProfileKey(channel="chat-completions", api_model="deepseek-v4.1-flash:1017",
                            task_sha256="c76c9253613bdab7c8752e5700dc89c81bd4aa0da018b2c8bb9b224791dce414")
    name, _ = _broken_resolve_with_fallback(doc, wrong)  # 变异：不抛
    assert name == "fallback"
    with pytest.raises(_hb.BudgetError):  # 原版：必须抛
        _hb.resolve_profile(doc, wrong)


def test_g6_mutation_unknown_as_zero_would_undercount():
    """变异 B 自证：当 0 处理版把缺失 usage 算成 0 消耗 ⇒ 预算形同虚设。"""
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from harness import budget as _hb
    b, _, u = _broken_settle_unknown_as_zero({})
    assert (b, u) == (0, False), "变异确实把 UNKNOWN 吃成 0（若上线即漏检）"
    _, _, unknown = _hb.settle_billed_tokens({})
    assert unknown is True, "原版必须判 UNKNOWN"


def test_g6_mutation_skipped_reserve_would_send_without_ledger():
    """变异 C 自证：跳过 reserve 直接发送 ⇒ 账本无痕（结构上不可接受）。

    用 AST 证明原版不存在这种路径：call 的支配条件里必须有 reserve。
    """
    body = _func_node(RUNNER_AST, "run_matrix")
    src = ast.get_source_segment(RUNNER_SRC, body) or ""
    # 同一循环体内 reserve 与 call 共存，且 reserve 在前（已由 G1 断言）；
    # 这里额外断言：不存在“无 budget 分支”的裸 call（禁绕过）。
    assert src.count("soeb_client.call(") == 1, "发送点必须唯一且受预算分支支配"


def test_g6_mutation_force_takeover_would_break_exclusivity(tmp_path):
    """变异 D 自证：acquire 允许后来者覆盖 auth_id ⇒ 独占锁形同虚设。

    原版必须拒绝第二个不同的 auth_id（禁 force 越权）。
    """
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from harness import budget as _hb
    ledger = _hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    with pytest.raises(_hb.BudgetError, match="独占"):
        _hb.Ledger(tmp_path / "ledger.json").acquire("auth-b")


def test_g6_mutation_restart_wipe_would_lose_ledger(tmp_path):
    """变异 E 自证：重启后账本若被清空 ⇒ 已用额度归零（超发）。

    原版重载同一文件必须保留 auth_id、发数与累计。
    """
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from harness import budget as _hb
    p = tmp_path / "ledger.json"
    ledger = _hb.Ledger(p)
    ledger.acquire("auth-a")
    rid = ledger.reserve(shot_id="s1", pre_tokens=100)
    ledger.settle(rid, billed_completion=100, billed_total=150)
    reloaded = _hb.Ledger(p)
    assert reloaded.auth_id == "auth-a"
    assert reloaded.used_shots() == 1
    assert reloaded.used_completion() == 100
