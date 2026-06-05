"""
test_agent_registry.py — Agent 動態載入測試

3 個場景：
1. 從 default.yaml 動態載入 Yua + Ruka（enabled=true）
2. disabled agent 不載入
3. 未知 class 丟出有意義的 ValueError
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from configs.loader import load_config, create_agents
from src.eventbus import SoulEventBus


@pytest.fixture
def mock_bus():
    bus = SoulEventBus()
    return bus


def test_create_agents_from_config(mock_bus):
    """場景一：從 default.yaml 動態載入 Yua + Ruka"""
    cfg = load_config()
    agents = create_agents(cfg, mock_bus)
    ids = [a.agent_id for a in agents]

    assert "agent_yua" in ids, f"agent_yua not in {ids}"
    assert "agent_ruka" in ids, f"agent_ruka not in {ids}"
    assert len(agents) == 2, f"Expected 2 agents, got {len(agents)}: {ids}"

    # 確認 intimacy_level 正確
    yua = next(a for a in agents if a.agent_id == "agent_yua")
    assert yua.state.intimacy_level == 80, f"Yua intimacy={yua.state.intimacy_level}"

    ruka = next(a for a in agents if a.agent_id == "agent_ruka")
    assert ruka.state.intimacy_level == 60, f"Ruka intimacy={ruka.state.intimacy_level}"

    print(f"[OK] Dynamic load {len(agents)} agents: {ids}")


def test_disabled_agent_not_loaded(mock_bus):
    """場景二：enabled=false 的 agent 不被載入"""
    cfg = load_config()
    # 把 Yua 設為 disabled
    for agent_cfg in cfg.get("agents", []):
        if agent_cfg.get("id") == "agent_yua":
            agent_cfg["enabled"] = False
            break

    agents = create_agents(cfg, mock_bus)
    ids = [a.agent_id for a in agents]

    assert "agent_yua" not in ids, f"Disabled Yua should not load, got {ids}"
    assert "agent_ruka" in ids, f"Ruka should still load, got {ids}"

    print("[OK] Disabled agent correctly skipped")


def test_unknown_class_raises(mock_bus):
    """場景三：未知 class 丟出 ValueError（含 class 名稱）"""
    cfg = {"agents": [{"id": "agent_x", "class": "AgentUnknown", "enabled": True}]}

    with pytest.raises(ValueError, match="AgentUnknown"):
        create_agents(cfg, mock_bus)

    print("[OK] Unknown class raises ValueError with class name")


if __name__ == "__main__":
    import asyncio

    bus = SoulEventBus()

    print("=== Agent Registry Tests ===\n")
    test_create_agents_from_config(bus)
    test_disabled_agent_not_loaded(bus)
    test_unknown_class_raises(bus)
    print("\n[OK] All tests passed!")