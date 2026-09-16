"""
tests/harness/test_zero_mutation_guard_live_service_paths.py
FUP-1 Part B — 零變異守門（zero-mutation guard）對「活服務寫入」的降噪驗收。

背景（審計已釘死根因）：`harness/tl6.py`(:553/:561)、`tl7.py`、`tl9.py`、`tl10.py`、
`tl11.py`、runner 等守門，對**整個生產 `data/` 根**做 before/after sha256 比對（快照窗
約 6–10 秒），而 2026-09-16 14:18 上線的 **Telegram 通道心跳寫入器每 30 秒寫一次**
`data/heartbeats/telegram_channel.json` ⇒ 落在窗內就偽紅（修前實測 1/8 次，
`mutation_diff` 逐字指名該檔）。

修法：把「活服務運行時自身寫入」的路徑加入**共用**的 skip 設定
（`harness/runner.py::_is_mutation_skipped`，全庫守門共用同一份）。本檔負責：

  1. **牙齒還在**（D3，核心驗收）：在**非 skip** 的生產路徑製造一次寫入
     （`data/world/__guard_probe__.json`，`finally` 內立刻刪除），斷言守門**仍然變紅**
     且**逐字指名該探針**；若探針路徑其實也被 skip（假的牙齒），此測試會紅。
  2. **排除清單證據鎖定**（D2）：逐條斷言「實測被服務寫入的路徑」確實被排除，
     且**未被服務寫入的路徑仍受保護**（牙齒空間沒有被過度放寬）。
  3. **語意不回歸**（tmp 根，0 生產寫入）：非 skip 新增 → 紅；skip 路徑新增 → 不影響。
  4. **既有排除集合未被悄悄放寬**：`_MUTATION_SKIP_EXTS` 逐值鎖定。

全部離線；唯一觸碰生產 `data/**` 的是 (1) 的探針，且必在 `finally` 刪除。
"""

from __future__ import annotations

from pathlib import Path

from harness.runner import (
    _MUTATION_SKIP_DIRS,
    _MUTATION_SKIP_EXTS,
    _is_mutation_skipped,
    snapshot_data_root_hashes,
    verify_zero_mutation,
)

ROOT = Path(__file__).resolve().parents[2]
PROD_DATA = ROOT / "data"

#: D2 證據清單：2026-09-16 15:33 以 mtime 列舉「近 6 小時內被活的生產服務寫入」、
#: 且未被 `_MUTATION_SKIP_EXTS` 涵蓋的 `data/**` 路徑（逐筆見 runner.py 碼旁註解）。
LIVE_SERVICE_RUNTIME_PATHS = (
    "heartbeats/telegram_channel.json",              # 15:32:54（每 30 秒）
    "tts/agent_mai/20260916T022755_624898.mp3",      # 歷史偽紅（工單明列）
    "sessions/agent_rem_user_bryan.json",            # 15:32:14（每回合）
    "conversations/group_chat.json",                 # 15:10:54
    "memory.db",                                     # 15:14:45
    "memory/agent_mai/memories.jsonl",               # 12:41:16
    "state/event_loop_alive.json",                   # 15:29:51
    "state/post_1df312c_counter.json",               # 15:33:04（樣式 post_<commit>_counter.json）
    "elevation/elevation_trace.jsonl",               # 15:18:47
    "inner_life/trace.jsonl",                        # 15:18:47
    "world/perception_trace.jsonl",                  # 15:18:47（歷史偽紅，工單明列）
    "soul/decision_trace.jsonl",                     # 14:49:01
    "soul/motive_trace.jsonl",                       # 14:49:01
    "soul/interactions.jsonl",                       # 10:26:16
    "soul/agent_rem/relationships.json",             # 09:55:13
    "soul/agent_yua/diary/2026-09-16.jsonl",         # 10:26:16
    "heartbeat_trace.log",                           # 由 ext 清單涵蓋
    "logs/watchdog.log",                             # 由 ext 清單涵蓋
)

#: 反例（**必須仍受保護**）：與上面同目錄、但服務不會寫的路徑 ⇒ 用來證明 skip 清單沒有
#: 被「整目錄 glob」放寬。
STILL_PROTECTED_PATHS = (
    "soul/goals.json",
    "soul/agent_rem/profile.json",
    "agents/agent_rem.json",
    "ground_truth/x.json",
    "world/perception_summary.json",
    "sessions/agent_rem.json",           # 不符合 sessions/agent_*_user_*.json
    "state/other_counter.json",          # 不符合 state/post_*_counter.json
    "conversations/agent_mai_user_bryan.json",
)


# ─────────────────────────────────────────────────────────────
# (1) D3：牙齒還在（核心驗收）
# ─────────────────────────────────────────────────────────────

def test_guard_still_catches_probe_write_into_non_skipped_production_path():
    """在非 skip 的生產路徑寫一個探針 ⇒ 守門必須**仍然紅**並**指名該探針**。

    這是本 Part 的驗收核心：skip 清單只能排除「服務自身寫入」，不得把守門鈍化到
    「測試寫生產根也抓不到」。探針路徑若其實落在 skip 內，本測試會紅（假牙齒偵測）。
    """
    assert PROD_DATA.is_dir(), f"生產 data 根不存在，無法驗證牙齒：{PROD_DATA}"
    probe = PROD_DATA / "world" / "__guard_probe__.json"
    probe_rel = probe.relative_to(PROD_DATA).as_posix()
    assert not _is_mutation_skipped(Path(probe_rel)), f"探針路徑意外落在 skip 內：{probe_rel}"
    assert not probe.exists(), f"探針檔已存在（前一輪未清理）：{probe}"

    before = snapshot_data_root_hashes(PROD_DATA)
    try:
        probe.write_text('{"probe": "zero-mutation-guard-teeth"}', encoding="utf-8")
        res = verify_zero_mutation(PROD_DATA, before)
        assert res["pass"] is False, (
            "守門已被 skip 清單鈍化：測試寫入生產根竟未變紅 "
            f"(added={res['added']} diff={res['diff']})"
        )
        assert probe_rel in res["added"], (
            f"守門未逐字指名探針 ⇒ 探針無效（該路徑其實也被 skip？）: "
            f"added={res['added']} diff={res['diff']}"
        )
    finally:
        probe.unlink(missing_ok=True)
    assert not probe.exists(), "探針必須在 finally 內刪除（不得留下生產污染）"


# ─────────────────────────────────────────────────────────────
# (2) D2：排除清單證據鎖定 + 牙齒空間未被過度放寬
# ─────────────────────────────────────────────────────────────

def test_live_service_runtime_paths_are_excluded():
    """實測被服務寫入的路徑必須被排除（否則守門會繼續偽紅）。"""
    not_excluded = [p for p in LIVE_SERVICE_RUNTIME_PATHS if not _is_mutation_skipped(Path(p))]
    assert not_excluded == [], f"這些活服務路徑未被排除 ⇒ 仍會偽紅：{not_excluded}"


def test_service_never_written_paths_remain_protected():
    """同目錄下服務不寫的路徑**必須仍受保護** ⇒ 證明沒有整目錄 glob 放寬。"""
    loosened = [p for p in STILL_PROTECTED_PATHS if _is_mutation_skipped(Path(p))]
    assert loosened == [], f"這些路徑被過度放寬（牙齒被鈍化）：{loosened}"


def test_skip_sets_shape_locked():
    """既有排除集合不得被悄悄放寬：dir 集合只多 heartbeats/tts，ext 集合逐值不變。"""
    assert _MUTATION_SKIP_DIRS == {"time_lapse", "heartbeats", "tts"}
    assert _MUTATION_SKIP_EXTS == {
        ".log", ".err", ".pid", ".txt", ".bak", ".old", ".tmp",
        ".sqlite-shm", ".sqlite-wal", "-shm", "-wal",
    }


# ─────────────────────────────────────────────────────────────
# (3) 語意不回歸（tmp 根；0 生產寫入）
# ─────────────────────────────────────────────────────────────

def test_skip_semantics_on_temp_root(tmp_path):
    """tmp 根上：非 skip 新增 ⇒ 紅；skip 路徑新增 ⇒ 不影響；核心偵測未失效。"""
    root = tmp_path / "data"
    (root / "world").mkdir(parents=True)
    (root / "world" / "keep.json").write_text("{}", encoding="utf-8")
    before = snapshot_data_root_hashes(root)

    # (a) 非 skip 路徑新增 → 紅
    (root / "world" / "new.json").write_text("{}", encoding="utf-8")
    res = verify_zero_mutation(root, before)
    assert res["pass"] is False
    assert "world/new.json" in res["added"]

    # (a2) 非 skip 路徑**改內容** → 紅（diff 路徑，不只是 added）
    (root / "world" / "keep.json").write_text('{"changed": true}', encoding="utf-8")
    res_changed = verify_zero_mutation(root, before)
    assert res_changed["pass"] is False
    assert "world/keep.json" in res_changed["diff"]

    # (b) 只有 skip 路徑新增/改動 → 不影響（把 (a) 的痕跡清掉後重驗）
    (root / "world" / "new.json").unlink()
    (root / "world" / "keep.json").write_text("{}", encoding="utf-8")
    (root / "heartbeats").mkdir()
    (root / "heartbeats" / "telegram_channel.json").write_text("{}", encoding="utf-8")
    (root / "state").mkdir()
    (root / "state" / "post_abc1234_counter.json").write_text("{}", encoding="utf-8")
    (root / "world" / "perception_trace.jsonl").write_text("x", encoding="utf-8")
    res2 = verify_zero_mutation(root, snapshot_data_root_hashes(root))
    assert res2["pass"] is True, f"skip 清單未生效：{res2}"

    # (c) 核心偵測仍未失效：空 before ⇒ 任何**非 skip** 檔都算新增 → 必須紅，
    #     而 skip 路徑仍不得出現在 added（兩件事同時成立才算真牙齒）。
    (root / "world" / "again.json").write_text("{}", encoding="utf-8")
    res3 = verify_zero_mutation(root, {})
    assert res3["pass"] is False
    assert "world/again.json" in res3["added"]
    assert "world/keep.json" in res3["added"]
    assert "heartbeats/telegram_channel.json" not in res3["added"]
    assert "state/post_abc1234_counter.json" not in res3["added"]
    assert "world/perception_trace.jsonl" not in res3["added"]
