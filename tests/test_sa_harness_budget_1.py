# -*- coding: utf-8 -*-
"""SA-HARNESS-BUDGET-1 单元测试（全 mock，零真实出站）。

覆盖 Sol 工单第 1-5 条：
  精确匹配禁回落 / --run 必带 profile 超限拒发 / 单授权独占锁发送前预留 /
  首尾生成·工具·重试全计数禁自动重试加额 / 按协议结算含 reasoning，
  缺 usage/超时/存证失败保留预留标 UNKNOWN 并停止。

零出站：本文件禁用真实 socket（autouse 硬拦），所有发送经 monkeypatch
的假 ``soeb_client.call``，transport 为内存 canned 观测。
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from harness import budget as hb  # noqa: E402
from scripts.soeb import client as soeb_client  # noqa: E402
from scripts.soeb import runner as soeb_runner  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 全局硬拦：任何真实出站企图直接失败（证明零出站）
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("blocked: unit test must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


BUDGETS_PATH = _REPO_ROOT / "scripts" / "soeb" / "budgets.json"
VERIFIED_TASK_SHA = "c76c9253613bdab7c8752e5700dc89c81bd4aa0da018b2c8bb9b224791dce414"


def _verified_key() -> hb.ProfileKey:
    return hb.ProfileKey(
        channel="ollama-chat",
        api_model="deepseek-v4.1-flash:1017",
        task_sha256=VERIFIED_TASK_SHA,
        effort=None,
    )


def _canned_result(ok=True, output=100, reasoning=30, status=200, finish="stop"):
    usage = {"input_tokens": 50, "output_tokens": output,
             "total_tokens": 50 + output, "reasoning_tokens": reasoning}
    return soeb_client.CallResult(
        model_id="deepseek-v4.1-flash",
        protocol="chat-completions",
        ok=ok,
        latency_ms=5,
        text="### 1.x\n### 2.x\n### 3.x\n### 4.x\n### 5.x\n" if ok else "",
        reasoning_text="r" * reasoning if ok else "",
        finish_reason=finish if ok else "",
        usage=usage if ok else {},
        status_code=status,
        error="" if ok else "boom",
        request={"url": "http://127.0.0.1:9/x", "headers": {}, "body": {}},
    )


# ── 1. 精确匹配：已验组合命中，其它禁回落 ──

def test_verified_combo_resolves_with_8000_24000_3():
    doc = hb.load_budgets(BUDGETS_PATH)
    name, caps = hb.resolve_profile(doc, _verified_key())
    assert name == "sa-budget-1-verified-t1"
    assert caps["single_shot_max_completion"] == 8000
    assert caps["total_max_completion"] == 24000
    assert caps["max_shots"] == 3


@pytest.mark.parametrize("field,value", [
    ("channel", "chat-completions"),
    ("api_model", "deepseek-v4.1-flash"),
    ("task_sha256", "0" * 64),
    ("effort", "high"),
])
def test_near_miss_key_is_rejected_no_fallback(field, value):
    doc = hb.load_budgets(BUDGETS_PATH)
    base = {"channel": "ollama-chat", "api_model": "deepseek-v4.1-flash:1017",
            "task_sha256": VERIFIED_TASK_SHA, "effort": None}
    base[field] = value
    with pytest.raises(hb.BudgetError):
        hb.resolve_profile(doc, hb.ProfileKey(**base))


def test_budgets_json_verified_values_are_exact():
    doc = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    caps = doc["profiles"]["sa-budget-1-verified-t1"]
    assert (caps["single_shot_max_completion"], caps["total_max_completion"],
            caps["max_shots"]) == (8000, 24000, 3)


# ── 2. 超限直接拒绝 ──

def test_single_shot_over_limit_rejected(tmp_path):
    ledger = hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    with pytest.raises(hb.BudgetError, match="单发超限"):
        ledger.check_fits(single_shot_max_completion=8000, total_max_completion=24000,
                           max_shots=3, planned_max_tokens=8001)


def test_total_over_limit_rejected(tmp_path):
    ledger = hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    rid = ledger.reserve(shot_id="s1", pre_tokens=24000)
    ledger.settle(rid, billed_completion=24000, billed_total=24100)
    with pytest.raises(hb.BudgetError, match="累计超限"):
        ledger.check_fits(single_shot_max_completion=8000, total_max_completion=24000,
                           max_shots=3, planned_max_tokens=100)


def test_exact_limit_fits_not_rejected(tmp_path):
    """等额恰好放下 ⇒ 放行（变异 M2 把 > 改成 >= 会误拒，靠本测试转红）。"""
    ledger = hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    ledger.check_fits(single_shot_max_completion=8000, total_max_completion=24000,
                       max_shots=3, planned_max_tokens=8000)  # 等额不抛即过


def test_shots_over_limit_rejected(tmp_path):
    ledger = hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    for i in range(3):
        rid = ledger.reserve(shot_id=f"s{i}", pre_tokens=10)
        ledger.settle(rid, billed_completion=10, billed_total=60)
    with pytest.raises(hb.BudgetError, match="发数超限"):
        ledger.check_fits(single_shot_max_completion=8000, total_max_completion=24000,
                           max_shots=3, planned_max_tokens=10)


# ── 3. 单授权独占锁：预留先落盘；force 越权拒绝；重启不清账 ──

def test_reserve_persists_before_send_and_exclusive_lock(tmp_path, monkeypatch):
    ledger_path = tmp_path / "ledger.json"
    ledger = hb.Ledger(ledger_path)
    ledger.acquire("auth-a")
    with pytest.raises(hb.BudgetError, match="独占"):
        hb.Ledger(ledger_path).acquire("auth-b")

    doc = hb.load_budgets(BUDGETS_PATH)
    seen_files = []

    def _fake_call(endpoint, api_model, messages, **kwargs):
        # 发送时刻账本文件必须已含该次预留（先落盘后发送）
        persisted = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert any(r["status"] == "reserved" for r in persisted["reservations"])
        seen_files.append(True)
        return _canned_result()

    monkeypatch.setattr(soeb_runner.soeb_client, "call", _fake_call)
    registry = {"models": [{
        "id": "deepseek-v4.1-flash", "protocol": "chat-completions",
        "endpoint": "ollama-chat", "api_model": "deepseek-v4.1-flash:1017",
        "effort_supported": []}]}
    spec = json.loads((_REPO_ROOT / "verification/sa-budget-1/task-t1-standalone.json").read_text(encoding="utf-8"))
    task = soeb_runner.Task(id="T1", messages=spec["tasks"][0]["messages"], max_tokens=8000)
    endpoints = {"ollama-chat": soeb_client.Endpoint(base_url="http://127.0.0.1:9", api_key="fake",
                                                     protocol="chat-completions")}
    out = soeb_runner.run_matrix(endpoints, registry, [task], ["deepseek-v4.1-flash"],
                                 system_prompt="", save_raw=False, timeout=5.0,
                                 budget_doc=doc, budget_ledger=ledger,
                                 presend_dir=tmp_path / "presend")
    assert seen_files and len(out) == 1 and out[0]["budget_status"] == "settled"
    # 发前存证已落盘且脱敏
    pres = list((tmp_path / "presend").glob("presend-*.json"))
    assert len(pres) == 1


def test_restart_reload_preserves_ledger(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    ledger = hb.Ledger(ledger_path)
    ledger.acquire("auth-a")
    rid = ledger.reserve(shot_id="s1", pre_tokens=100)
    ledger.settle(rid, billed_completion=130, billed_total=180)
    # 模拟重启：重新加载同一文件
    reloaded = hb.Ledger(ledger_path)
    assert reloaded.auth_id == "auth-a"
    assert reloaded.used_shots() == 1
    assert reloaded.used_completion() == 130


# ── 4. 结算计一次；缺明细标未提供；缺失/非法/口径矛盾判 UNKNOWN；停发 ──

def test_settlement_counts_output_once_reasoning_not_added():
    billed, _, unknown = hb.settle_billed_tokens(
        {"input_tokens": 50, "output_tokens": 100, "total_tokens": 150, "reasoning_tokens": 30})
    assert unknown is False
    assert billed == 100  # output 已含推理：计一次，不重复相加


def test_missing_reasoning_detail_is_not_unknown():
    billed, _, unknown = hb.settle_billed_tokens(
        {"input_tokens": 50, "output_tokens": 100, "total_tokens": 150})
    assert unknown is False  # 缺明细记“未提供”，不判 UNKNOWN
    assert billed == 100


def test_contradictory_reasoning_is_unknown():
    _, _, unknown = hb.settle_billed_tokens(
        {"input_tokens": 50, "output_tokens": 50, "total_tokens": 100, "reasoning_tokens": 80})
    assert unknown is True  # 子集大于整体：口径矛盾


@pytest.mark.parametrize("usage", [
    {"input_tokens": 50, "output_tokens": 100, "total_tokens": 150, "reasoning_tokens": 30},
    {"input_tokens": 50, "output_tokens": 100, "total_tokens": 150},
    {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    {"output_tokens": 7},
    {"input_tokens": 50, "output_tokens": 50, "total_tokens": 100, "reasoning_tokens": 80},
    {"input_tokens": 50, "output_tokens": -3, "total_tokens": 47},
    {"input_tokens": 50, "output_tokens": 100, "total_tokens": "bad"},
    {"input_tokens": 50, "output_tokens": 100, "total_tokens": -1},
    {"input_tokens": 5},
    {},
    None,
])
def test_settlement_parity_client_vs_budget(usage):
    """两处结算实现逐项一致（SA-BUDGET-GO-1 同步要求）。"""
    hb_out = hb.settle_billed_tokens(usage)
    res = soeb_client.CallResult(
        model_id="m", protocol="chat-completions", ok=True, latency_ms=1,
        usage=dict(usage) if isinstance(usage, dict) else {},
        status_code=200,
        request={"url": "http://127.0.0.1:9/x", "headers": {}, "body": {}})
    cl_out = soeb_client.settlement_for_result(res)
    assert cl_out == hb_out, usage


def test_missing_usage_is_unknown_not_zero():
    for bad in (None, {}, {"input_tokens": 5}):
        _, _, unknown = hb.settle_billed_tokens(bad)
        assert unknown is True


def test_failed_call_marks_unknown_and_halts(tmp_path, monkeypatch):
    ledger = hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    doc = hb.load_budgets(BUDGETS_PATH)
    monkeypatch.setattr(soeb_runner.soeb_client, "call", lambda *a, **k: _canned_result(ok=False))
    registry = {"models": [{
        "id": "deepseek-v4.1-flash", "protocol": "chat-completions",
        "endpoint": "ollama-chat", "api_model": "deepseek-v4.1-flash:1017",
        "effort_supported": []}]}
    spec = json.loads((_REPO_ROOT / "verification/sa-budget-1/task-t1-standalone.json").read_text(encoding="utf-8"))
    tasks = [soeb_runner.Task(id="T1", messages=spec["tasks"][0]["messages"], max_tokens=8000),
             soeb_runner.Task(id="T1b", messages=spec["tasks"][0]["messages"], max_tokens=100)]
    endpoints = {"ollama-chat": soeb_client.Endpoint(base_url="http://127.0.0.1:9", api_key="fake",
                                                     protocol="chat-completions")}
    out = soeb_runner.run_matrix(endpoints, registry, tasks, ["deepseek-v4.1-flash"],
                                 system_prompt="", save_raw=False, timeout=5.0,
                                 budget_doc=doc, budget_ledger=ledger,
                                 presend_dir=tmp_path / "presend")
    # 第一发 UNKNOWN 后 halt：第二题不得再发
    assert ledger.halted is True
    assert out[0]["budget_status"] == "UNKNOWN"
    assert all(r["status"] in ("reserved", "UNKNOWN", "settled") for r in ledger.summary()["reservations"])
    assert len(out) == 1  # 停发：只有 1 笔


def test_no_auto_retry_single_call_per_slot(tmp_path, monkeypatch):
    calls = []
    ledger = hb.Ledger(tmp_path / "ledger.json")
    ledger.acquire("auth-a")
    doc = hb.load_budgets(BUDGETS_PATH)

    def _fake_call(endpoint, api_model, messages, **kwargs):
        calls.append(1)
        return _canned_result()
    monkeypatch.setattr(soeb_runner.soeb_client, "call", _fake_call)
    registry = {"models": [{
        "id": "deepseek-v4.1-flash", "protocol": "chat-completions",
        "endpoint": "ollama-chat", "api_model": "deepseek-v4.1-flash:1017",
        "effort_supported": []}]}
    spec = json.loads((_REPO_ROOT / "verification/sa-budget-1/task-t1-standalone.json").read_text(encoding="utf-8"))
    task = soeb_runner.Task(id="T1", messages=spec["tasks"][0]["messages"], max_tokens=8000, runs=2)
    endpoints = {"ollama-chat": soeb_client.Endpoint(base_url="http://127.0.0.1:9", api_key="fake",
                                                     protocol="chat-completions")}
    soeb_runner.run_matrix(endpoints, registry, [task], ["deepseek-v4.1-flash"],
                           system_prompt="", save_raw=False, timeout=5.0,
                           budget_doc=doc, budget_ledger=ledger,
                           presend_dir=tmp_path / "presend")
    assert len(calls) == 2  # runs=2 → 恰 2 发，无自动重试加发


# ── 5. 发前脱敏存证；B 边界 ──

def test_presend_headers_are_redacted():
    redacted = soeb_client.redact_headers({"Authorization": "Bearer oc_sk_" + "x" * 60})
    hb.validate_redacted_headers(redacted)  # 不抛即过
    with pytest.raises(hb.BudgetError):
        hb.validate_redacted_headers({"Authorization": "Bearer oc_sk_" + "x" * 60})


def test_budget_module_has_no_soul_imports():
    import ast as _ast
    tree = _ast.parse((_REPO_ROOT / "harness" / "budget.py").read_text(encoding="utf-8"))
    mods = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, _ast.ImportFrom):
            mods.append(node.module or "")
    # 允许 scripts.*（调用层）与标准库；禁 src.*（Soul 生产面）
    assert not any(m == "src" or m.startswith("src.") for m in mods), mods
    assert "import socket" not in (_REPO_ROOT / "harness" / "budget.py").read_text(encoding="utf-8")
    assert "import httpx" not in (_REPO_ROOT / "harness" / "budget.py").read_text(encoding="utf-8")
