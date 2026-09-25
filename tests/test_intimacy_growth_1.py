"""INTIMACY-GROWTH-1：動態親密度演化模型 + Delta API 測試。

涵蓋：
  T1. 純函式邊界（compute_effective_intimacy clamp）
  T2. 阻尼單調性（calculate_intimacy_gain）
  T3. 惰性遷移冪等（_init_schema 連呼兩次不炸）
  T4. 舊 intimacy 欄位唯讀（update_delta 不得寫入舊欄）
  T5. delta 累加與 fail-safe

測試隔離：一律走 tmp_path 自建 DB，不碰 data_root() 的真實 memory.db。
"""
from __future__ import annotations

import sqlite3

import pytest

from src.agent.emotion import (
    BASE_STEP,
    EmotionEngine,
    calculate_intimacy_gain,
    compute_effective_intimacy,
)

TOL = 1e-9


# ─────────────────────────────────────────────
# T1. 純函式邊界
# ─────────────────────────────────────────────

def test_effective_intimacy_spec_acceptance_point() -> None:
    """spec 明列驗收點：compute_effective_intimacy(50, 30) == 80.0"""
    assert abs(compute_effective_intimacy(50, 30) - 80.0) < TOL


def test_effective_intimacy_clamps_upper() -> None:
    assert abs(compute_effective_intimacy(95, 20) - 100.0) < TOL


def test_effective_intimacy_clamps_lower() -> None:
    assert abs(compute_effective_intimacy(5, -20) - 0.0) < TOL


def test_effective_intimacy_passthrough_without_delta() -> None:
    assert abs(compute_effective_intimacy(60.0, 0.0) - 60.0) < TOL


# ─────────────────────────────────────────────
# T2. 阻尼單調性（阻尼探針）
# ─────────────────────────────────────────────

def test_gain_saturated_below_midrange() -> None:
    """核心驗收：越接近 100，增量越小。"""
    assert calculate_intimacy_gain(95.0) < calculate_intimacy_gain(50.0)


def test_gain_concrete_values() -> None:
    """寫死期望值，不只斷言大小關係。"""
    assert abs(calculate_intimacy_gain(50.0) - 0.375) < TOL
    assert abs(calculate_intimacy_gain(95.0) - 0.04875) < TOL


def test_gain_floor_never_reaches_zero() -> None:
    """下限 0.01：親密度永遠仍可微量成長。"""
    assert calculate_intimacy_gain(99.99) >= 0.01
    assert abs(calculate_intimacy_gain(100.0) - 0.01) < TOL


def test_gain_strictly_decreasing() -> None:
    """對 [0, 25, 50, 75, 95, 99] 逐對驗證嚴格遞減。"""
    ladder = [0.0, 25.0, 50.0, 75.0, 95.0, 99.0]
    gains = [calculate_intimacy_gain(x) for x in ladder]
    for prev, nxt in zip(gains, gains[1:]):
        assert prev > nxt, f"未嚴格遞減：{gains}"


def test_gain_base_step_override() -> None:
    """base_step 可覆寫，且 damping 為 (1 - (x/100)^2)。"""
    assert abs(calculate_intimacy_gain(50.0, base_step=1.0) - 0.75) < TOL
    assert abs(BASE_STEP - 0.5) < TOL


# ─────────────────────────────────────────────
# T3. 惰性遷移冪等
# ─────────────────────────────────────────────

def test_lazy_migration_idempotent(tmp_path) -> None:
    """連續兩次 _init_schema() 不得拋 duplicate column。"""
    db = tmp_path / "memory.db"
    engine = EmotionEngine(db_path=db)
    engine._init_schema()  # 第二次
    engine._init_schema()  # 第三次，保險

    cols = [
        row[1]
        for row in engine.conn.execute("PRAGMA table_info(agent_emotions)")
    ]
    assert cols.count("intimacy_delta") == 1, cols


def test_lazy_migration_from_legacy_schema(tmp_path) -> None:
    """模擬舊庫（僅 4 欄、帶既有資料），遷移後既有值必須逐位元保留。"""
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE agent_emotions (
            agent_id   TEXT PRIMARY KEY,
            mood       REAL DEFAULT 0.0,
            intimacy   REAL DEFAULT 50.0,
            updated_at TEXT
        )
    """)
    conn.execute(
        "INSERT INTO agent_emotions VALUES ('agent_legacy', 0.25, 73.5, 'x')"
    )
    conn.commit()
    conn.close()

    engine = EmotionEngine(db_path=db)
    cols = [
        row[1]
        for row in engine.conn.execute("PRAGMA table_info(agent_emotions)")
    ]
    assert "intimacy_delta" in cols
    assert len(cols) == 5, cols

    # 既有 row 未被 DELETE / UPDATE
    assert engine.get("agent_legacy") == (0.25, 73.5)
    # 新欄位對舊 row 預設 0.0
    assert abs(engine.get_delta("agent_legacy") - 0.0) < TOL
    assert engine.get_delta("agent_legacy") == pytest.approx(0.0)


# ─────────────────────────────────────────────
# T4. 舊欄位唯讀
# ─────────────────────────────────────────────

def test_update_delta_does_not_touch_legacy_intimacy(tmp_path) -> None:
    """建一 row 帶 intimacy=100.0，update_delta 後仍是 100.0（逐位元不變）。"""
    db = tmp_path / "memory.db"
    engine = EmotionEngine(db_path=db)
    engine.conn.execute(
        "INSERT INTO agent_emotions (agent_id, mood, intimacy, updated_at) "
        "VALUES ('agent_ro', 0.0, 100.0, 'x')"
    )
    engine.conn.commit()

    engine.update_delta("agent_ro", 0.375)
    engine.update_delta("agent_ro", 0.25)

    mood, intimacy = engine.get("agent_ro")
    assert intimacy == 100.0
    assert mood == 0.0
    # delta 正常累加，未污染舊欄
    assert abs(engine.get_delta("agent_ro") - 0.625) < TOL


def test_update_delta_clamps_range(tmp_path) -> None:
    engine = EmotionEngine(db_path=tmp_path / "memory.db")
    assert abs(engine.update_delta("a", 500.0) - 100.0) < TOL
    assert abs(engine.update_delta("b", -500.0) - (-100.0)) < TOL


# ─────────────────────────────────────────────
# T5. delta 累加與 fail-safe
# ─────────────────────────────────────────────

def test_get_delta_missing_agent_is_zero(tmp_path) -> None:
    """不存在的 agent -> 0.0，且不拋。"""
    engine = EmotionEngine(db_path=tmp_path / "memory.db")
    assert engine.get_delta("agent_does_not_exist") == 0.0


def test_get_delta_null_is_zero(tmp_path) -> None:
    engine = EmotionEngine(db_path=tmp_path / "memory.db")
    engine.conn.execute(
        "INSERT INTO agent_emotions (agent_id, mood, intimacy, intimacy_delta, updated_at) "
        "VALUES ('agent_null', 0.0, 50.0, NULL, 'x')"
    )
    engine.conn.commit()
    assert engine.get_delta("agent_null") == 0.0


def test_update_delta_accumulates(tmp_path) -> None:
    """累加兩次後值正確，且回傳值 == 讀回值。"""
    engine = EmotionEngine(db_path=tmp_path / "memory.db")
    first = engine.update_delta("agent_x", 0.375)
    assert abs(first - 0.375) < TOL
    second = engine.update_delta("agent_x", 0.25)
    assert abs(second - 0.625) < TOL
    assert abs(engine.get_delta("agent_x") - 0.625) < TOL


def test_update_delta_upsert_creates_row_without_touching_get(tmp_path) -> None:
    """UPSERT 建立新 row 時，get() 的 (mood, intimacy) 落回預設 (0.0, 50.0)。"""
    engine = EmotionEngine(db_path=tmp_path / "memory.db")
    engine.update_delta("agent_new", 0.5)
    assert engine.get("agent_new") == (0.0, 50.0)


def test_growth_end_to_end_damped_curve(tmp_path) -> None:
    """端到端：從 base 60 連續成長，總量收斂且單調遞增。"""
    engine = EmotionEngine(db_path=tmp_path / "memory.db")
    base = 60.0
    agent = "agent_ruka"
    prev_eff = base
    for _ in range(200):
        delta = engine.get_delta(agent)
        eff = compute_effective_intimacy(base, delta)
        gain = calculate_intimacy_gain(eff)
        engine.update_delta(agent, gain)
        new_eff = compute_effective_intimacy(base, engine.get_delta(agent))
        assert new_eff >= prev_eff
        prev_eff = new_eff
    # 200 步 × 每步 ≥0.01，必定有實質成長，且不超過 clamp 上限
    assert prev_eff > base
    assert prev_eff <= 100.0
