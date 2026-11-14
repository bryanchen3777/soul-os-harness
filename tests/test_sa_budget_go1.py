# -*- coding: utf-8 -*-
"""SA-BUDGET-GO-1 单元测试（全 mock，零真实出站）。

覆盖：
  1. 五条 Go 显式 profile 精确命中（channel/api_model/任务SHA/effort 与实发一致）；
  2. 近似组合（错 effort / 错 channel / 未知模型）一律拒绝，禁回落；
  3. T1 派生 spec：messages SHA 与 sa-budget-1 一致、system 为空、温度 0；
  4. v2-Go detector 双向自证全绿（零网络）。
"""
from __future__ import annotations

import importlib.util
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
from scripts.soeb import runner as soeb_runner  # noqa: E402

BUDGETS_PATH = _REPO_ROOT / "scripts" / "soeb" / "budgets.json"
TASK_SHA = "c76c9253613bdab7c8752e5700dc89c81bd4aa0da018b2c8bb9b224791dce414"

GO1_MATRIX = {
    # profile 名 -> (channel, api_model, effort, single, total, shots)
    "sa-budget-go-1-spark-t1": ("responses", "muse-spark-1.3-contributor", "low", 16000, 48000, 3),
    "sa-budget-go-1-mimo-t1": ("chat-completions", "mimo-v2.6-flash", None, 8000, 24000, 3),
    "sa-budget-go-1-glm-t1": ("chat-completions", "glm-5.3-flash", "low", 8000, 24000, 3),
    "sa-budget-go-1-qwen-t1": ("messages", "qwen3.8-flash", None, 8000, 24000, 3),
    "sa-budget-go-1-minimax-t1": ("chat-completions", "minimax-m3.1-flash", None, 8000, 24000, 3),
}


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("blocked: unit test must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.mark.parametrize("profile,args", list(GO1_MATRIX.items()))
def test_go1_profiles_resolve_exact(profile, args):
    channel, api_model, effort, single, total, shots = args
    doc = hb.load_budgets(BUDGETS_PATH)
    name, caps = hb.resolve_profile(
        doc, hb.ProfileKey(channel=channel, api_model=api_model,
                            task_sha256=TASK_SHA, effort=effort))
    assert name == profile
    assert (caps["single_shot_max_completion"], caps["total_max_completion"],
            caps["max_shots"]) == (single, total, shots)


@pytest.mark.parametrize("field,value", [
    ("channel", "chat-completions"),   # spark 若走错通道
    ("effort", None),                  # spark 缺 effort
    ("effort", "high"),                # spark 错 effort
    ("api_model", "muse-spark-1.3"),   # 别名/截断
    ("task_sha256", "0" * 64),
])
def test_go1_spark_near_miss_rejected(field, value):
    doc = hb.load_budgets(BUDGETS_PATH)
    base = {"channel": "responses", "api_model": "muse-spark-1.3-contributor",
            "task_sha256": TASK_SHA, "effort": "low"}
    base[field] = value
    with pytest.raises(hb.BudgetError):
        hb.resolve_profile(doc, hb.ProfileKey(**base))


@pytest.mark.parametrize("spec_file,effort", [
    ("task-t1-go-effort-low.json", "low"),
    ("task-t1-go-effort-null.json", None),
])
def test_go1_spec_sha_matches_sa_budget_1(spec_file, effort):
    spec = soeb_runner.load_spec(
        _REPO_ROOT / "verification" / "sa-budget-go-1" / spec_file,
        default_max_tokens=2048)
    assert spec["_system_prompt"] == ""
    assert len(spec["_tasks"]) == 1 and spec["_tasks"][0].id == "T1"
    task = spec["_tasks"][0]
    assert task.temperature == 0.0
    assert task.effort == effort
    assert hb.canonical_task_sha256(task.messages) == TASK_SHA


def _load_detector():
    path = _REPO_ROOT / "verification" / "sa-budget-go-1" / "detector_go1.py"
    loader = importlib.util.spec_from_file_location("detector_go1", path)
    mod = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(mod)
    return mod


def test_go1_detector_guard_all_green():
    det = _load_detector()
    ok, results = det.run_guard()
    assert ok, results
    assert len(results) == 15
