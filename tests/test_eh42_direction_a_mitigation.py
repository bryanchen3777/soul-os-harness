"""
tests/test_eh42_direction_a_mitigation.py

[EH-4.2-DIRECTION-A-MITIGATION]
方向 A（DEV/隔離風險圍堵）：暫停新增四條 eh4_* 衍生列離線驗證。

驗證矩陣：
1. 定義句同化時：第一槽（earth_term, source='user'）依舊經 EH-3.1 正常萃取、Gate 打標為
   origin='assimilated', horizon_state='aware', learned_at > 0，並 flush 落盤。
2. 方向 A 暫停生效：四條 eh4_* 衍生列嚴格為 0（不新增 eh4_mental_model 等槽位）。
3. 零污染保證：DB 中 0 筆帶有 DDL 預設值（lived_experience / NULL learned_at）的 eh4_* 列。
4. 既有歷史資料可讀性：先前已存在的四槽事實，在讀側（get_idiolect_facts / get_fact）
   依然可被正常檢索與讀取，不受寫入暫停影響。
5. 非定義句（如「我買了一台氣炸鍋」）：依然觸發 Gate 攔截，零同化。
6. 可撤銷性驗證：若顯式指定 SOUL_OS_PAUSE_EH4_WRITE_BACK="0"，仍可走回既有寫入路徑。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
import pytest

from src.inner_life.epistemic_appraisal import clear_pack_cache
from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact
from src.memory.sage.provider import SAGELiteProvider
from src.paths import reset_data_root, data_root


DEFINITION_SENTENCE = "氣炸鍋就是個插電吹熱風把食物烤熟的箱子，外殼會燙要注意。"


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_LLM_JUDGE", "false")
    clear_pack_cache()
    reset_data_root()
    # 預設為方向 A 暫停
    monkeypatch.delenv("SOUL_OS_PAUSE_EH4_WRITE_BACK", raising=False)
    yield
    clear_pack_cache()
    reset_data_root()


def _make_isolated_provider(tmp_path: Path, profile_id: str = "agent_rem") -> tuple[SAGELiteProvider, Path]:
    soul_dir = tmp_path / "data" / "memory" / profile_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    prov = SAGELiteProvider(profile_id=profile_id, data_dir=str(soul_dir))
    prov.initialize(session_id="s1")
    db_path = soul_dir / "graph.sqlite"
    return prov, db_path


def _query_db_direct(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM facts ORDER BY timestamp ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_direction_a_preserves_slot1_and_pauses_four_slots(tmp_path):
    """驗證 1 & 2 & 3：定義句同化，第一槽正常放行落盤，四條衍生列嚴格 0 寫入，零污染。"""
    provider, db_path = _make_isolated_provider(tmp_path)

    # 模擬 user 說出定義句並走真實 post_reply_commit
    await provider.post_reply_commit(
        "s1",
        DEFINITION_SENTENCE,
        "雷姆輕輕點頭。",
        source_pair=f"bryan:{provider.profile_id}",
    )

    rows = _query_db_direct(db_path)

    # 斷言 1：第一槽完好放行（origin='assimilated', horizon_state='aware', learned_at 有效）
    slot1_rows = [r for r in rows if "氣炸鍋" in r["subject"] and r["source"] == "user"]
    assert len(slot1_rows) >= 1
    assert slot1_rows[0]["origin"] == "assimilated"
    assert slot1_rows[0]["horizon_state"] == "aware"
    assert slot1_rows[0]["learned_at"] is not None
    assert slot1_rows[0]["learned_at"] > 0

    # 斷言 2：四條衍生列嚴格為 0！
    eh4_rows = [r for r in rows if str(r["predicate"]).startswith("eh4_")]
    assert len(eh4_rows) == 0, f"方向 A 應暫停新增四槽，但殘留了衍生列: {eh4_rows}"


@pytest.mark.asyncio
async def test_direction_a_existing_four_slots_remain_readable(tmp_path):
    """驗證 4：寫側暫停不影響讀側；既有歷史四槽資料依然可被正常讀取。"""
    provider, db_path = _make_isolated_provider(tmp_path)
    now = time.time()

    # 預先寫入完整的歷史五元組（含第一槽與四條 eh4_* 衍生槽位）
    predicates = [
        ("is_a", "烤食物箱子", "user"),
        ("eh4_mental_model", "熱風循環加熱", "inference"),
        ("eh4_safety_rule", "外殼高溫防燙", "inference"),
        ("eh4_duty_action", "主動提醒散熱", "inference"),
        ("eh4_idiolect", "旋風烤盒", "inference"),
    ]
    fids = []
    for pred, obj, src in predicates:
        fid = provider._store.add_fact(
            Fact(
                subject="氣炸鍋",
                predicate=pred,
                object=obj,
                source=src,
                timestamp=now,
            )
        )
        provider._store.set_fact_dimensions(
            fid,
            origin="assimilated",
            horizon_state="aware",
            learned_at=now,
        )
        fids.append(fid)
    provider._store.flush()

    assert len(_query_db_direct(db_path)) == 5

    # 斷言 1：既有 get_idiolect_facts 正常讀取歷史所有內化事實（5 筆）
    idiolect_facts = provider._store.get_idiolect_facts()
    assert len(idiolect_facts) == 5

    # 斷言 2：get_fact 精確讀取
    f_mm = provider._store.get_fact(fids[1])
    assert f_mm is not None
    assert f_mm.predicate == "eh4_mental_model"
    assert f_mm.object == "熱風循環加熱"


@pytest.mark.asyncio
async def test_direction_a_non_definitional_sentence_zero_assimilation(tmp_path):
    """驗證 5：非定義句（未包含 marker）依然被 Gate 攔截，零同化。"""
    provider, db_path = _make_isolated_provider(tmp_path)

    # 非定義句（日常提及「我買了一台氣炸鍋」）
    user_msg = "我上週在特賣會買了一台氣炸鍋。"
    await provider.post_reply_commit(
        "s1",
        user_msg,
        "雷姆眨眨眼睛。",
        source_pair=f"bryan:{provider.profile_id}",
    )

    rows = _query_db_direct(db_path)
    # 斷言：若有萃取出的 fact，未通過 Gate，維度維持預設 lived_experience 且 learned_at IS NULL
    for r in rows:
        assert r["origin"] == "lived_experience"
        assert r["learned_at"] is None


@pytest.mark.asyncio
async def test_direction_a_revocable_entry(tmp_path, monkeypatch):
    """驗證 6：可撤銷性，顯式指定 SOUL_OS_PAUSE_EH4_WRITE_BACK='0' 或 'false' 時走回舊路徑（具已知原子性風險）。"""
    monkeypatch.setenv("SOUL_OS_PAUSE_EH4_WRITE_BACK", "0")
    provider, db_path = _make_isolated_provider(tmp_path)

    await provider.post_reply_commit(
        "s1",
        DEFINITION_SENTENCE,
        "雷姆輕輕點頭。",
        source_pair=f"bryan:{provider.profile_id}",
    )

    rows = _query_db_direct(db_path)
    # 斷言：撤銷暫停後，舊四槽寫回被執行（產生 eh4_* 列）
    eh4_rows = [r for r in rows if str(r["predicate"]).startswith("eh4_")]
    assert len(eh4_rows) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("val", ["1", "true", "TRUE", "invalid_value", "random_string", "2", ""])
async def test_direction_a_fail_closed_on_unknown_and_explicit_pause(tmp_path, monkeypatch, val):
    """驗證 7（Fail-Closed 模式核查）：'1'、'true'、空值、未知設定值一律維持暫停，絕不無聲啟用舊寫入。"""
    monkeypatch.setenv("SOUL_OS_PAUSE_EH4_WRITE_BACK", val)
    provider, db_path = _make_isolated_provider(tmp_path)

    await provider.post_reply_commit(
        "s1",
        DEFINITION_SENTENCE,
        "雷姆輕輕點頭。",
        source_pair=f"bryan:{provider.profile_id}",
    )

    rows = _query_db_direct(db_path)
    # 斷言：在任何非 0/false 設定下，四槽衍生列嚴格為 0！
    eh4_rows = [r for r in rows if str(r["predicate"]).startswith("eh4_")]
    assert len(eh4_rows) == 0, f"在 SOUL_OS_PAUSE_EH4_WRITE_BACK='{val}' 下竟非 fail-closed: {eh4_rows}"
    # 但第一槽合法放行依然健在
    slot1_rows = [r for r in rows if "氣炸鍋" in r["subject"] and r["source"] == "user"]
    assert len(slot1_rows) >= 1
    assert slot1_rows[0]["origin"] == "assimilated"
