"""
tests/harness/test_infra_data_root_guard.py
TEST-INFRA-DATA-ROOT-GUARD-1 (P2) — 測試資料目錄隔離與變異守門收窄（恢復檢測力）。

驗證內容：
  1. Must-Fail 探針（Fail-Closed 核心牙齒）：
     在非 skip 的生產路徑（data/critical_probe.json）寫入探針，
     驗證守門 100% 變紅並精確指名該檔案；finally 內清理並斷言 0 殘留。
  2. D1 樣式收窄邊界驗證（正向/反向雙向鎖定）：
     - heartbeats: heartbeats/telegram_channel.json (skip) vs heartbeats/other.json (not skip)
     - tts: tts/**/*.mp3 (skip) vs tts/**/*.wav, tts/*.json (not skip)
     - state/post_*_counter.json: post_*[0-9a-f]_counter.json (skip) vs post_backup_counter.json (not skip)
     - soul/agent_*/diary/*.jsonl: ????-??-??.jsonl (skip) vs backup.jsonl, 20260916.jsonl (not skip)
  3. D2 SQLite 暫存檔副檔名比對（P4-2）：
     - is_sqlite_temp_file:
       .db-wal / .db-shm / .sqlite-wal / .sqlite-shm / .sqlite3-wal / .sqlite3-shm (skip)
       對照組: x.db / x.db-backup / y-wal.txt / y-wal.json (not skip)
     - _is_mutation_skipped: 對照組 x.db / x.db-backup / y-wal.json 不得被 skip
  4. 隔離性與守門語意在 tmp 根驗證。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.runner import (
    _MUTATION_SKIP_DIRS,
    _MUTATION_SKIP_EXTS,
    _MUTATION_SKIP_PATTERNS,
    _is_mutation_skipped,
    is_sqlite_temp_file,
    snapshot_data_root_hashes,
    verify_zero_mutation,
)

ROOT = Path(__file__).resolve().parents[2]
PROD_DATA = ROOT / "data"


# ─────────────────────────────────────────────────────────────
# 1. Must-Fail 探針（Fail-Closed 核心牙齒）
# ─────────────────────────────────────────────────────────────

def test_guard_catches_critical_probe_in_production_data_root():
    """在非 skip 的生產根目錄寫入 critical_probe.json ⇒ 守門必須 100% 變紅並指名該檔。

    確認探針安全無覆蓋：
      - critical_probe.json 的 suffix 為 .json（不在 _MUTATION_SKIP_EXTS）
      - parts[0] 為 critical_probe.json（不在 _MUTATION_SKIP_DIRS = {'time_lapse'}）
      - 不匹配 _MUTATION_SKIP_PATTERNS 中任何條目
      ⇒ _is_mutation_skipped 回傳 False。
    """
    assert PROD_DATA.is_dir(), f"生產 data 目錄不存在：{PROD_DATA}"
    probe = PROD_DATA / "critical_probe.json"
    probe_rel = "critical_probe.json"

    # 前置斷言：探針路徑不可落在任何 skip 樣式內
    assert not _is_mutation_skipped(Path(probe_rel)), (
        f"探針路徑意外被 skip 規則排除：{probe_rel}"
    )
    # 前置斷言：乾淨狀態，不得有前輪殘留
    assert not probe.exists(), f"探針檔已存在（前輪測試未清理）：{probe}"

    before = snapshot_data_root_hashes(PROD_DATA)
    try:
        probe.write_text('{"probe": "TEST-INFRA-DATA-ROOT-GUARD-1"}', encoding="utf-8")
        res = verify_zero_mutation(PROD_DATA, before)

        assert res["pass"] is False, "守門未捕捉到生產資料根的新增檔案（牙齒失效）"
        assert probe_rel in res["added"], (
            f"守門未逐字指名探針檔案：added={res['added']} diff={res['diff']}"
        )
    finally:
        probe.unlink(missing_ok=True)

    # 零外洩斷言：探針必須刪除乾淨
    assert not probe.exists(), "探針檔案未在 finally 中成功刪除，留下生產污染！"


# ─────────────────────────────────────────────────────────────
# 2. D1 樣式收窄邊界驗證（雙向鎖定）
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rel_path, expected_skipped, reason",
    [
        # (1) heartbeats: 精確檔案 data/heartbeats/telegram_channel.json
        ("heartbeats/telegram_channel.json", True, "線上真實心跳檔應被 skip"),
        ("heartbeats/other_service.json", False, "同目錄其他檔案不得被 skip（已恢復檢測力）"),
        ("heartbeats/channel_config.json", False, "同目錄非心跳檔不得被 skip"),
        ("heartbeats/sub/nested.json", False, "子目錄檔案不得被 skip"),

        # (2) tts: tts/**/*.mp3
        ("tts/agent_mai/20260916T022755_624898.mp3", True, "真實 TTS 音檔應被 skip"),
        ("tts/agent_rem/20260715T120000_123456.mp3", True, "不同 agent 的 mp3 應被 skip"),
        ("tts/agent_mai/voice.wav", False, "非 mp3 格式不得被 skip（已恢復檢測力）"),
        ("tts/agent_mai/audio.ogg", False, "非 mp3 格式不得被 skip"),
        ("tts/metadata.json", False, "TTS 目錄下的非音檔不得被 skip"),

        # (3) state/post_*_counter.json: post_*[0-9a-f]_counter.json
        ("state/post_1df312c_counter.json", True, "真實十六進位 short commit hash 計數器應被 skip"),
        ("state/post_e9aba39_counter.json", True, "真實十六進位 hash 計數器應被 skip"),
        ("state/post_015139e_counter.json", True, "真實十六進位 hash 計數器應被 skip"),
        ("state/post_backup_counter.json", False, "非十六進位字元 counter 不得被 skip（已恢復檢測力）"),
        ("state/post_custom_counter.json", False, "非十六進位字元 counter 不得被 skip"),
        ("state/other_counter.json", False, "不符合 post_* 格式的 counter 不得被 skip"),

        # (4) soul/agent_*/diary/*.jsonl: ????-??-??.jsonl
        ("soul/agent_yua/diary/2026-09-16.jsonl", True, "真實 YYYY-MM-DD 日記應被 skip"),
        ("soul/agent_rem/diary/2026-01-01.jsonl", True, "真實 YYYY-MM-DD 日記應被 skip"),
        ("soul/agent_yua/diary/backup.jsonl", False, "非日期命名的 diary jsonl 不得被 skip（已恢復檢測力）"),
        ("soul/agent_yua/diary/20260916.jsonl", False, "非 YYYY-MM-DD 格式不得被 skip"),
        ("soul/agent_yua/diary/temp_diary.jsonl", False, "非日期格式不得被 skip"),
        ("soul/agent_yua/diary/summary.json", False, "非 jsonl 檔不得被 skip"),
    ],
)
def test_narrowed_skip_patterns_boundaries(rel_path: str, expected_skipped: bool, reason: str):
    """驗證 D1 收窄後的 4 條樣式在正向（線上寫入者）與反向（同目錄非預期檔）均符合預期。"""
    actual = _is_mutation_skipped(Path(rel_path))
    assert actual == expected_skipped, (
        f"{reason}：路徑 '{rel_path}' 預期 skipped={expected_skipped}，實際={actual}"
    )


# ─────────────────────────────────────────────────────────────
# 3. D2 SQLite 暫存檔副檔名比對（P4-2）
# ─────────────────────────────────────────────────────────────

def test_is_sqlite_temp_file_predicate():
    """驗證 SQLite 暫存檔判定函式：涵蓋完整變體，且對照組精確拒絕。"""
    # 應判為 True 的 SQLite WAL/SHM 變體
    sqlite_temp_targets = [
        "memory.db-wal",
        "memory.db-shm",
        "state.sqlite-wal",
        "state.sqlite-shm",
        "graph.sqlite3-wal",
        "graph.sqlite3-shm",
        "nested/path/to/my_data.db-wal",
        "nested/path/to/my_data.sqlite3-shm",
    ]
    for target in sqlite_temp_targets:
        assert is_sqlite_temp_file(target) is True, (
            f"SQLite 暫存檔變體應判定為 True：{target}"
        )

    # 對照組（必須為 False）：主庫檔、非 WAL/SHM 衍生檔、偽 WAL 檔案
    sqlite_temp_controls = [
        "memory.db",
        "state.sqlite",
        "graph.sqlite3",
        "x.db-backup",
        "x.db.bak",
        "y-wal.txt",
        "y-wal.json",
        "y-shm.dat",
        "wal-analysis.json",
        "shm_dump.raw",
    ]
    for control in sqlite_temp_controls:
        assert is_sqlite_temp_file(control) is False, (
            f"對照組不得判定為 SQLite 暫存檔：{control}"
        )


def test_mutation_skipped_sqlite_temp_controls():
    """驗證 _is_mutation_skipped 對 SQLite 暫存檔跳過，但對照組不得跳過。"""
    # 應跳過的 sqlite 暫存檔
    assert _is_mutation_skipped(Path("test.db-wal")) is True
    assert _is_mutation_skipped(Path("test.db-shm")) is True
    assert _is_mutation_skipped(Path("test.sqlite-wal")) is True
    assert _is_mutation_skipped(Path("test.sqlite-shm")) is True
    assert _is_mutation_skipped(Path("test.sqlite3-wal")) is True
    assert _is_mutation_skipped(Path("test.sqlite3-shm")) is True

    # 對照組（不得跳過）：
    # x.db（主庫未列入 pattern 者）
    assert _is_mutation_skipped(Path("test.db")) is False
    # x.db-backup（非 wal/shm）
    assert _is_mutation_skipped(Path("test.db-backup")) is False
    # y-wal.json（非 sqlite wal）
    assert _is_mutation_skipped(Path("y-wal.json")) is False
    # y-shm.json（非 sqlite shm）
    assert _is_mutation_skipped(Path("y-shm.json")) is False


# ─────────────────────────────────────────────────────────────
# 4. 語意不回歸與隔離驗證（tmp 根）
# ─────────────────────────────────────────────────────────────

def test_guard_semantics_on_temp_data_root(tmp_path):
    """在隔離 tmp 資料根上驗證守門整體行為：非 skip 變異必紅，skip 變異不影響。"""
    root = tmp_path / "data"
    root.mkdir()
    (root / "world").mkdir()
    (root / "world" / "baseline.json").write_text("{}", encoding="utf-8")

    before = snapshot_data_root_hashes(root)
    assert "world/baseline.json" in before

    # 1. 寫入收窄後的 skip 檔案，不應使守門變紅
    (root / "heartbeats").mkdir()
    (root / "heartbeats" / "telegram_channel.json").write_text('{"hb": 1}', encoding="utf-8")
    (root / "tts" / "agent_mai").mkdir(parents=True)
    (root / "tts" / "agent_mai" / "20260916T000000_123456.mp3").write_bytes(b"\xff\xfb\x90\x00")
    (root / "state").mkdir()
    (root / "state" / "post_abcdef1_counter.json").write_text('{"n": 1}', encoding="utf-8")
    (root / "soul" / "agent_yua" / "diary").mkdir(parents=True)
    (root / "soul" / "agent_yua" / "diary" / "2026-09-16.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "test.db-wal").write_bytes(b"wal-bytes")

    after_skip_only = verify_zero_mutation(root, before)
    assert after_skip_only["pass"] is True, (
        f"收窄後的合法寫入不應造成守門變紅：{after_skip_only}"
    )

    # 2. 寫入非 skip 檔案，守門必須精確變紅
    (root / "world" / "unexpected.json").write_text("{}", encoding="utf-8")
    after_unexpected = verify_zero_mutation(root, before)
    assert after_unexpected["pass"] is False
    assert "world/unexpected.json" in after_unexpected["added"]
