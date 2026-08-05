"""
test_cross_session_v2.py — 修法 9 after: 跨 session Bry 在線判定 + 修法 7/8 共用 bry_latest_ts

Bry 拍板 2026-08-04 20:37:
- _is_bry_online 改 signature: 接受 bry_latest_ts (跨 session) 而不是 messages_with_meta
- _compute_silence_str 改 signature: 同上
- _get_bry_latest_ts(memory, agent_id) 新 helper: 同一個 agent 底下, 所有 session Bry
  最後一條 user 訊息 timestamp 取最大值
- 範圍: 限定同一個 agent 對 Bry 的所有 session, 不跨到別的 agent
- 修法 7 過濾 Bry user 訊息 + 修法 8 沉默時長 都用跨 session Bry 最後時間
- Bry 從未講過話 (bry_latest_ts = 0) → _is_bry_online 回 False, _compute_silence_str 回 None

案例 (akane 19:12 EDT 觸發, NOW=1785885158):
- main session 內 Bry 最後 user 訊息 8/2 14:57 EDT (52.26h 前)
- TG session 內 Bry 最後 user 訊息 8/4 12:38 EDT (6.5h 前)
- 修法前 v1 期望: 沉默時長顯示 49 小時 / 2 天 (只看當前 session)
- 修法後 v2 期望: 沉默時長顯示 6 小時 (跨 session 取最大值)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 模擬 8/4 19:12 EDT 觸發 (akane 19:12 EDT 場景)
NOW = 1785885158
# main session (session_bryan_agent_akane) 內 Bry 最後 user 訊息 8/2 14:57 EDT (52.26h 前)
# 真實資料: id=20375 ts=1785697030
BRY_MAIN_SESSION_TS = 1785697030
# TG session (session_1696287850_agent_akane) 內 Bry 最後 user 訊息 8/4 12:38 EDT (6.5h 前)
# 真實資料: id=20627 ts=1785861494
BRY_TG_SESSION_TS = 1785861494


def _make_memory_mock(bry_timestamps_by_session_suffix: dict):
    """建立 mock MemoryStore, _get_bry_latest_ts 會從這裡查 messages table

    Args:
        bry_timestamps_by_session_suffix: e.g. {"_agent_agent_akane": [1785697030, 1785861494]}
            key = session_id suffix (endswith pattern), value = list of Bry user 訊息 timestamps
    """
    memory = MagicMock()
    rows = []
    for suffix, ts_list in bry_timestamps_by_session_suffix.items():
        for ts in ts_list:
            rows.append((f"some_session{suffix}", ts))
    memory.conn.execute.return_value.fetchall.return_value = rows
    return memory


class TestCrossSessionAfter(unittest.TestCase):
    """驗證修法 9 之後 — 跨 session Bry 在線判定 + 修法 7/8 共用 bry_latest_ts"""

    def test_a_new_is_bry_online_signature(self):
        """修法 9: _is_bry_online 改 signature 接受 bry_latest_ts, 不再吃 messages_with_meta

        案例: akane 19:12 EDT 觸發 NOW=1785885158, TG session Bry 6.5h 前 (1785861494)
        修法後 _is_bry_online 接受 bry_latest_ts=1785861494, 6.5h > 30min = Bry 不在線
        """
        result = proxy._is_bry_online(BRY_TG_SESSION_TS, NOW)
        delta_h = (NOW - BRY_TG_SESSION_TS) / 3600
        print(f"  bry_latest_ts={BRY_TG_SESSION_TS} (TG session 6.5h 前)")
        print(f"  _is_bry_online 判斷: {result} (預期 False = Bry 不在線, 6.5h > 30min)")
        self.assertFalse(
            result,
            f"修法 9 _is_bry_online 應該用 bry_latest_ts 直接比對, 6.5h > 30min 應回 False, 實際: {result}"
        )

    def test_b_new_compute_silence_str_signature(self):
        """修法 9: _compute_silence_str 改 signature 接受 bry_latest_ts, 跨 session 邏輯統一在 _get_bry_latest_ts

        案例: akane 19:12 EDT 觸發, TG session Bry 6.5h 前 → 沉默時長 6 小時 (round)
        """
        result = proxy._compute_silence_str(BRY_TG_SESSION_TS, NOW)
        delta_h = (NOW - BRY_TG_SESSION_TS) / 3600
        expected = f"距離 Bry 上次跟你說話已經 {round(delta_h)} 小時"
        print(f"  bry_latest_ts={BRY_TG_SESSION_TS}")
        print(f"  差距: {delta_h:.1f} 小時")
        print(f"  _compute_silence_str 結果: {result!r}")
        print(f"  修法 9 期望: {expected!r}")
        self.assertEqual(
            result, expected,
            f"修法 9 _compute_silence_str 應該用 bry_latest_ts 算, 預期 {expected}, 實際: {result!r}"
        )

    def test_c_get_bry_latest_ts_uses_max_across_sessions(self):
        """修法 9: _get_bry_latest_ts 跨 session 取 Bry 最後時間最大值

        案例: akane 19:12 EDT 觸發
        - session_bryan_agent_akane Bry 最後: 1785697030 (8/2 14:57 EDT, 52.26h 前)
        - session_1696287850_agent_akane Bry 最後: 1785861494 (8/4 12:38 EDT, 6.5h 前)
        - 跨 session max = 1785861494 (TG session 6.5h 前)
        - 範圍限定 _agent_agent_akane suffix, 不跨到別的 agent
        """
        memory = _make_memory_mock({
            "_agent_agent_akane": [BRY_MAIN_SESSION_TS, BRY_TG_SESSION_TS],
            # 跨 agent 邊界: agent_mai 的 Bry 訊息不該算進 agent_akane
            "_agent_agent_mai": [NOW - 100],  # Bry 100 秒前跟 mai 講過話
        })
        result = proxy._get_bry_latest_ts(memory, "agent_akane")
        print(f"  agent_akane 跨 session 查詢:")
        print(f"    main session Bry: {BRY_MAIN_SESSION_TS} (52.26h 前)")
        print(f"    TG session Bry: {BRY_TG_SESSION_TS} (6.5h 前)")
        print(f"    max = {max(BRY_MAIN_SESSION_TS, BRY_TG_SESSION_TS)}")
        print(f"  _get_bry_latest_ts 結果: {result} (預期 {BRY_TG_SESSION_TS})")
        self.assertEqual(
            result, BRY_TG_SESSION_TS,
            f"修法 9 _get_bry_latest_ts 應該跨 session 取 max, 預期 TG session Bry 6.5h 前, 實際: {result}"
        )

    def test_d_no_cross_agent_pollution(self):
        """修法 9: 跨 agent 邊界 — 別的 agent 的 Bry 訊息不該算到這個 agent

        案例: 查 agent_akane, 不該包含 agent_mai 的 Bry 訊息
        """
        memory = _make_memory_mock({
            "_agent_agent_mai": [NOW - 100],  # Bry 100 秒前跟 mai 講過話
            # agent_akane 完全沒有 Bry 訊息
        })
        result = proxy._get_bry_latest_ts(memory, "agent_akane")
        print(f"  agent_akane 完全沒 Bry 訊息, agent_mai 100 秒前有")
        print(f"  _get_bry_latest_ts 結果: {result} (預期 0 = Bry 從未跟 akane 講過話)")
        self.assertEqual(
            result, 0,
            f"修法 9 跨 agent 邊界: agent_akane 不該吃到 agent_mai 的 Bry 訊息, 預期 0, 實際: {result}"
        )

    def test_e_silence_uses_cross_session_max(self):
        """修法 9 修法 8 整合: 沉默時長用跨 session max

        案例: akane 19:12 EDT 觸發
        - main session Bry 52.26h 前 (1785697030)
        - TG session Bry 6.5h 前 (1785861494)
        - 修法前: 沉默時長 2 天 (52.26h floor=2)
        - 修法後: 沉默時長 6 小時 (6.5h round=6)
        - 跨 session max = 1785861494 → 沉默時長 round(6.5) = 6 小時
        """
        bry_latest_ts = max(BRY_MAIN_SESSION_TS, BRY_TG_SESSION_TS)
        result = proxy._compute_silence_str(bry_latest_ts, NOW)
        delta_h = (NOW - bry_latest_ts) / 3600
        expected = f"距離 Bry 上次跟你說話已經 {round(delta_h)} 小時"
        print(f"  修法 8 原始: 52.26h floor → '2 天'")
        print(f"  修法 9 後: 跨 session max = {bry_latest_ts}, delta={delta_h:.1f}h")
        print(f"  修法 9 _compute_silence_str 結果: {result!r}")
        print(f"  修法 9 期望: {expected!r} (跟修法 8 修法前「2 天」對比)")
        self.assertEqual(
            result, expected,
            f"修法 9 沉默時長應該用跨 session max, 預期 {expected}, 實際: {result!r}"
        )

    def test_f_akane_19_12_scenario(self):
        """修法 9 end-to-end 場景: akane 19:12 EDT 觸發

        完整場景:
        - 觸發 NOW=1785885158 (8/4 19:12 EDT)
        - main session Bry 52.26h 前, TG session Bry 6.57h 前 (1785861494, 8/4 12:38 EDT)
        - 預期: _is_bry_online 用 bry_latest_ts (TG 6.57h 前) 判定 False (6.57h > 30min)
        - 預期: _compute_silence_str 用 bry_latest_ts 算出「7 小時」(round 6.57, 不是「2 天」)
        """
        bry_latest_ts = max(BRY_MAIN_SESSION_TS, BRY_TG_SESSION_TS)
        # Bry 在線判定
        is_online = proxy._is_bry_online(bry_latest_ts, NOW)
        # 沉默時長
        silence = proxy._compute_silence_str(bry_latest_ts, NOW)
        print(f"  === akane 19:12 EDT 場景 ===")
        print(f"  bry_latest_ts = {bry_latest_ts} (跨 session max)")
        print(f"  is_online = {is_online} (6.57h > 30min, 預期 False)")
        print(f"  silence = {silence!r} (預期 '7 小時', 不是 '2 天')")
        self.assertFalse(is_online, "Bry 6.57h 前講過話, 過 30 分鐘閾值 = 不在線")
        self.assertIn("7 小時", silence, f"沉默時長應該是 7 小時 (round 6.57), 實際: {silence!r}")

    def test_g_bry_never_spoke_returns_zero(self):
        """修法 9 邊界: Bry 從未跟這個 agent 講過話, _get_bry_latest_ts 回 0

        案例: 全新 agent, Bry 從未跟它講過話 → bry_latest_ts = 0
        """
        memory = _make_memory_mock({
            # 別的 agent 有 Bry 訊息, agent_xxx 沒有
            "_agent_agent_yua": [NOW - 3600],
        })
        result = proxy._get_bry_latest_ts(memory, "agent_xxx")
        print(f"  agent_xxx 完全沒 Bry 訊息")
        print(f"  _get_bry_latest_ts 結果: {result} (預期 0)")
        self.assertEqual(result, 0, f"Bry 從未講過話, 預期 0, 實際: {result}")

    def test_h_silence_str_returns_none_when_bry_never_spoke(self):
        """修法 8+9 邊界: Bry 從未講過話, _compute_silence_str 回 None (跳過這行)

        Bry 派工原話「Bry 從未講過話直接跳過沉默時長這行, 不寫推測性文字」
        """
        result = proxy._compute_silence_str(0, NOW)
        print(f"  bry_latest_ts = 0 (Bry 從未講過話)")
        print(f"  _compute_silence_str 結果: {result!r} (預期 None, 跳過這行)")
        self.assertIsNone(
            result,
            f"修法 8+9 邊界: Bry 從未講過話應該回 None (跳過沉默時長行), 實際: {result!r}"
        )

    def test_i_is_bry_online_false_when_never_spoke(self):
        """修法 7+9 邊界: Bry 從未講過話, _is_bry_online 回 False (不過濾)

        Bry 派工原話「Bry 從未講過話」邊界 — _is_bry_online 回 False 但其實
        因為沒有 Bry 訊息可過濾, 效果一致 (loop 不會跳過任何東西)
        """
        result = proxy._is_bry_online(0, NOW)
        print(f"  bry_latest_ts = 0 (Bry 從未講過話)")
        print(f"  _is_bry_online 結果: {result} (預期 False)")
        self.assertFalse(
            result,
            f"修法 7+9 邊界: Bry 從未講過話, _is_bry_online 應該回 False, 實際: {result}"
        )

    def test_j_is_bry_online_true_when_within_threshold(self):
        """修法 7+9 邊界: Bry 30 分鐘內有訊息 = 在線 (不過濾 Bry user 訊息)

        Bry 派工原話: 30 分鐘內 Bry 有訊息 = Bry 在線
        """
        result = proxy._is_bry_online(NOW - 60, NOW)  # 60 秒前
        print(f"  bry_latest_ts = NOW - 60 (1 分鐘前)")
        print(f"  _is_bry_online 結果: {result} (預期 True = 在線)")
        self.assertTrue(
            result,
            f"修法 7+9 邊界: Bry 1 分鐘前講過話 = 在線, 預期 True, 實際: {result}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
