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
  5. TEST-INFRA-GUARD-LIVE-WRITER-COVERAGE-1：事件驅動線上寫入者樣式邊界（雙向）。
     - 正向：8 條新樣式各至少 1 個真實檔名 ⇒ skip=True（含 tts/ 根層 mp3，驗證新增的
       `tts/*.mp3`；以及嵌套 mp3，驗證舊 `tts/**/*.mp3` 仍有效）。
     - 負向：同目錄未知檔 ⇒ skip=False（牙齒仍在咬），含 `conversations/xxx_private.yaml`、
       `state/bryan_last_seen.bak2.json`、`agents/agent_x/mood.json`、`tts/chime.wav` 等。
"""

from __future__ import annotations

import fnmatch
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


# ─────────────────────────────────────────────────────────────
# 5. TEST-INFRA-GUARD-LIVE-WRITER-COVERAGE-1：事件驅動線上寫入者的
#    跳過樣式邊界（正向覆蓋 + 負向對照，雙向鎖定牙齒）
# ─────────────────────────────────────────────────────────────

#: D3-1 正向：本票新增的 8 條樣式，每條至少一個**真實**檔名 ⇒ 必須 skip=True。
#: 逐條對應 runner.py::_MUTATION_SKIP_PATTERNS 的 D1-1..D1-8 註解。
EVENT_DRIVEN_WRITER_PATHS = (
    # D1-1 touch_bryan_last_seen (src/io/channels/bryan_state.py:60)
    ("state/bryan_last_seen.json", "TG/Web/語音入站更新 Bry 最後發話時間"),
    # D1-2 _save_last_tg_user_global (src/io/channels/router.py:618)
    ("state/last_tg_user.json", "任一通道入站更新全域 last_tg_user"),
    # D1-3 set_tts_enabled (src/llm/tts_toggle.py:57 ← telegram.py:44 /tts 指令)
    ("state/tts_toggle.json", "/tts 指令切換 TTS 開關"),
    # D1-4 outbox (src/io/channels/router.py:672)
    ("state/outbox.json", "Bry 離線時主動訊息積壓"),
    # D1-5 _save_private_instance (src/llm/proxy.py:3489)
    ("conversations/1696287850_agent_akane_private.json", "真實私聊對話檔（新格式 user_id_agent_id）"),
    ("conversations/bryan_agent_mai_private.json", "真實私聊對話檔（legacy bryan_ 前綴）"),
    # D1-6 AgentConsciousness.save (src/agent/consciousness.py:77, :498)
    ("agents/agent_akane/emotional-state.json", "真實情感狀態檔（主動意圖／session 結束）"),
    # D1-7 carryover (src/agent/consciousness.py:411)
    ("agents/agent_akane/carryover.json", "SESSION_END carryover"),
    # D1-8 修正 fnmatch `**` 非遞迴的缺口：根層 mp3 必須被涵蓋
    ("tts/foo.mp3", "data/tts/ **根層** mp3（新樣式 tts/*.mp3 的唯一目的）"),
    ("tts/agent_mai/20260916T022755_624898.mp3", "嵌套 mp3（舊樣式 tts/**/*.mp3 仍須有效）"),
)

#: D3-2 負向對照（牙齒必須還在咬）：與上面**同目錄**、但服務不會寫的路徑 ⇒ 必須 skip=False。
NEGATIVE_CONTROL_PATHS = (
    ("state/unknown_state.json", "state/ 下的未知檔不得被 skip"),
    ("state/bryan_last_seen.bak2.json", "不是精確檔名 bryan_last_seen.json（.bak2.json 尾綴不在 ext 清單）"),
    ("conversations/other.json", "conversations/ 下的非私聊檔不得被 skip"),
    ("conversations/xxx_private.yaml", "同樣 *_private 但非 .json ⇒ 樣式不得命中"),
    ("agents/agent_x/mood.json", "agents/<id>/ 下的其他 json 不得被 skip"),
    ("tts/chime.wav", "tts/ 根層非 mp3 不得被 skip"),
    ("tts/root_audio.ogg", "tts/ 根層非 mp3 不得被 skip"),
)


@pytest.mark.parametrize(
    "rel_path, reason",
    EVENT_DRIVEN_WRITER_PATHS,
    ids=[p for p, _ in EVENT_DRIVEN_WRITER_PATHS],
)
def test_event_driven_live_writer_paths_are_skipped(rel_path: str, reason: str):
    """正向：事件驅動寫入者路徑必須被 skip（否則 Bryan 一發訊就偽紅）。"""
    assert _is_mutation_skipped(Path(rel_path)) is True, (
        f"{reason}：路徑 '{rel_path}' 應被 skip 但未被 skip ⇒ 守門會偽紅"
    )


@pytest.mark.parametrize(
    "rel_path, reason",
    NEGATIVE_CONTROL_PATHS,
    ids=[p for p, _ in NEGATIVE_CONTROL_PATHS],
)
def test_event_driven_writer_sibling_paths_stay_protected(rel_path: str, reason: str):
    """負向：同目錄未知檔必須仍受保護 ⇒ 證明沒有整目錄 glob 放寬、牙齒還在咬。"""
    assert _is_mutation_skipped(Path(rel_path)) is False, (
        f"{reason}：路徑 '{rel_path}' 竟被 skip ⇒ 牙齒被鈍化（過度放寬）"
    )


def _matched_skip_patterns(posix: str) -> list[str]:
    """回傳 `posix` 命中的 path pattern（比對語意與 `_is_mutation_skipped` 完全一致）。"""
    return [p for p in _MUTATION_SKIP_PATTERNS if fnmatch.fnmatch(posix, p)]


def test_emotional_state_json_dot_bak_is_not_matched_by_new_pattern():
    """工單 D3-2 列出的 `agents/agent_x/emotional-state.json.bak` — 誠實拆解其真實語意。

    牙齒本體（本票要鎖的東西）：新樣式 `agents/*/emotional-state.json` **不得**命中
    `emotional-state.json.bak`（否則任何 backup 檔都會被一起放行）。

    已知交互作用（**非**本票新增的放寬）：該檔尾綴 `.bak` 落在**既有的**
    `_MUTATION_SKIP_EXTS`（早於本票存在、且由 `test_skip_sets_shape_locked` 精確鎖定），
    故 `_is_mutation_skipped` 整體仍回 True。本測試把兩件事分開斷言，避免把「既有語意」
    誤記成「本票把樣式放寬到 .bak」。
    """
    rel = "agents/agent_x/emotional-state.json.bak"
    assert _matched_skip_patterns(rel) == [], (
        f"新樣式不得命中 backup 檔：命中={_matched_skip_patterns(rel)}"
    )
    # 兩條 agents 樣式都必須精確拒絕 .bak
    assert "agents/*/emotional-state.json" in _MUTATION_SKIP_PATTERNS
    assert "agents/*/carryover.json" in _MUTATION_SKIP_PATTERNS
    # 明確記錄跳過來源是 ext 清單（既有語意），不是 path pattern
    assert Path(rel).suffix.lower() == ".bak"
    assert ".bak" in _MUTATION_SKIP_EXTS
    assert _is_mutation_skipped(Path(rel)) is True
