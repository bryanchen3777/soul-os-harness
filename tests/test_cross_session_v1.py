"""
test_cross_session_v1.py — 修法 9 baseline: _is_bry_online / _compute_silence_str 只看當前 session

Bry 拍板 2026-08-04 20:37:
- 問題根因: 修法 7 _is_bry_online 只看當前 session 自己的 Bry 最後訊息時間
  沒把 Bry 在其他 session (TG / 群聊) 的活躍狀態算進去
- 案例: akane 19:12 EDT 觸發時, session_bryan_agent_akane Bry 上次講話 8/2 19:45 EDT
  (49.5h 前), session_1696287850_agent_akane Bry 中午 12:38 EDT (6.5h 前) 講過
  修法 7 判定 Bry 不在線 (49.5h > 30min), 修法 8 沉默時長顯示 49.5h
  但 Bry 6.5h 前才在跟 akane 講話 (另一個視窗)
- 修法 9 方向: 同一個 agent 底下, 所有 session Bry 最後一條訊息時間取最大值
  拿這個最大值去跟現在時間比對是否在 30 分鐘閾值內
- 範圍: 限定同一個 agent 對 Bry 的所有 session, 不要跨到別的 agent

這個 v1 驗證現狀 (before 修法 9):
- _is_bry_online 用 messages_with_meta 參數算 (沒跨 session)
- _compute_silence_str 用 messages_with_meta 參數算 (沒跨 session)
- proxy 沒有 _get_bry_latest_ts / _get_bry_latest_ts_across_sessions helper
- proxy source 沒修法 9 注入字眼

Mock 範圍:
- 模擬 akane 19:12 EDT 觸發 (NOW=1785885158)
- main session 內 Bry 最後 user 訊息 8/2 19:45 EDT (49.5h 前)
- TG session 內 Bry 最後 user 訊息 8/4 12:38 EDT (6.5h 前)
- 修法前 v1 期望: 沉默時長顯示 49 小時 (只看當前 session 沒跨 session)
- 修法後 v2 期望: 沉默時長顯示 6 小時 (跨 session 取 Bry 最後時間)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 模擬 8/4 19:12 EDT 觸發
NOW = 1785885158
# main session (session_bryan_agent_akane) 內 Bry 最後 user 訊息 8/2 14:57 EDT (52.26h 前)
# 真實資料: id=20375 ts=1785697030
BRY_MAIN_SESSION_TS = 1785697030
# TG session (session_1696287850_agent_akane) 內 Bry 最後 user 訊息 8/4 12:38 EDT (6.5h 前)
# 真實資料: id=20627 ts=1785861494
BRY_TG_SESSION_TS = 1785861494


def _make_main_session_history():
    """session_bryan_agent_akane 內 history: Bry 52.26h 前最後一條 user"""
    return [
        {
            "role": "user", "content": "Bry 8/2 14:57 講的話 (52.26h 前)",
            "timestamp": BRY_MAIN_SESSION_TS, "speaker": "bryan", "is_private": True,
        },
        {
            "role": "assistant", "content": "akane 回應",
            "timestamp": BRY_MAIN_SESSION_TS + 60, "speaker": "agent_akane", "is_private": True,
        },
    ]


class TestCrossSessionBaseline(unittest.TestCase):
    """驗證現狀 (before 修法 9) — _is_bry_online / _compute_silence_str 只看單一 session"""

    def setUp(self):
        self.main_session_history = _make_main_session_history()

    def test_a_baseline_is_bry_online_uses_messages_param(self):
        """修法 9 已套用: _is_bry_online 改成接受 bry_latest_ts (跨 session 純函式)

        本測試原本寫成 v1 baseline (呼叫 _is_bry_online(messages, NOW))。
        Bry 拍板 2026-08-04 20:37 修法 9 之後 signature 改成:
        - src/llm/proxy.py:919 `def _is_bry_online(bry_latest_ts: int, now: int) -> bool`
          (docstring 明講「修法 9 改 signature: 改接受 bry_latest_ts 直接時間比對,
          不再傳 messages_with_meta」)
        - 跨 session 取最大值統一在 src/llm/proxy.py:949 `_get_bry_latest_ts(memory, agent_id)`

        本測試現在驗證現行契約: 純函式 + 30 分鐘閾值 (STALE_THRESHOLD_SEC, proxy.py:93)
        """
        self.assertFalse(
            proxy._is_bry_online(BRY_MAIN_SESSION_TS, NOW),
            f"main session Bry 最後講話 52.26h 前 > 30min 閾值, 應該判 False (不在線)"
        )
        self.assertTrue(
            proxy._is_bry_online(NOW - 600, NOW),
            "Bry 10 分鐘前講過話 < 30min 閾值, 應該判 True (在線)"
        )
        # Bry 從未講過話 (bry_latest_ts <= 0) → False (proxy.py:944)
        self.assertFalse(
            proxy._is_bry_online(0, NOW),
            "bry_latest_ts=0 (Bry 從未跟這個 agent 講過話) 應該判 False"
        )
        print(f"  _is_bry_online 修法 9 signature: (bry_latest_ts, now) — "
              f"52.26h→False / 10min→True / 0→False")

    def test_b_baseline_compute_silence_uses_messages_param(self):
        """修法 8+9 已套用: _compute_silence_str(bry_latest_ts, now) 跨 session 算沉默時長

        本測試原本寫成 v1 baseline (呼叫 _compute_silence_str(messages, NOW) 期望 2 天)。
        Bry 拍板 2026-08-04 20:37 修法 9 之後:
        - src/llm/proxy.py:1089 `def _compute_silence_str(bry_latest_ts: int, now: int)`
          <24h = "X 小時" (四捨五入) / >=24h = "X 天" (取整天數)
        """
        # 52.26h 前講過話 → 2 天
        self.assertEqual(
            proxy._compute_silence_str(BRY_MAIN_SESSION_TS, NOW),
            "距離 Bry 上次跟你說話已經 2 天",
            "52.26h 前 Bry 講過話 → 52.26 // 24 = 2 天"
        )
        # 6.5h 前講過話 → 7 小時 (四捨五入)
        self.assertEqual(
            proxy._compute_silence_str(BRY_TG_SESSION_TS, NOW),
            "距離 Bry 上次跟你說話已經 7 小時",
            "6.5h 前 Bry 在 TG session 講過話 → round(6.57) = 7 小時 (修法 9 跨 session 取最大值後應走這條)"
        )
        # Bry 從未講過話 → None (跳過這行)
        self.assertIsNone(
            proxy._compute_silence_str(0, NOW),
            "Bry 從未跟這個 agent 講過話 → 回 None (跳過沉默時長行)"
        )
        # Bry 在線 (剛講過話) → None (講了反而多餘)
        self.assertIsNone(
            proxy._compute_silence_str(NOW - 600, NOW),
            "Bry 10 分鐘前講過話 (在線) → 回 None, 不注入沉默時長行"
        )
        print(f"  _compute_silence_str: 52.26h→2 天 / 6.5h→7 小時 / 0→None / 在線→None")

    def test_c_baseline_no_cross_session_helper(self):
        """修法 9 已套用: proxy 有 _get_bry_latest_ts 跨 session helper

        本測試原本寫成 v1 baseline (斷言 proxy 沒有這個 helper)。
        Bry 拍板 2026-08-04 20:37 修法 9 之後:
        - src/llm/proxy.py:949 `def _get_bry_latest_ts(memory, agent_id: str) -> int`
          (跨 session SQL 查 messages table, 對 session_id 以 _{agent_id} 結尾者取 max ts)
        - 正式名稱就是 _get_bry_latest_ts, 沒有 _get_bry_latest_ts_across_sessions 這個名字
        """
        self.assertTrue(
            hasattr(proxy, "_get_bry_latest_ts"),
            "修法 9: proxy 應該有 _get_bry_latest_ts 跨 session helper (proxy.py:949)"
        )
        self.assertFalse(
            hasattr(proxy, "_get_bry_latest_ts_across_sessions"),
            "修法 9 正式命名是 _get_bry_latest_ts, 沒有 _get_bry_latest_ts_across_sessions"
        )
        print(f"[修法 9 已套用] proxy 有 _get_bry_latest_ts (跨 session 取 Bry 最後 ts)")

    def test_d_baseline_proxy_source_no_method_9(self):
        """修法 9 已套用: proxy source 有修法 9 注入字眼 + helper 定義

        本測試原本寫成 v1 baseline (斷言 proxy source 沒修法 9 字眼)。
        Bry 拍板 2026-08-04 20:37 修法 9 之後 proxy.py 內有:
        - proxy.py:949 `def _get_bry_latest_ts(...)` 定義
        - proxy.py:951-983 docstring 寫明 Bry 派工原話「同一個 agent 底下, 所有 session...」
        """
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "def _get_bry_latest_ts", proxy_source,
            "修法 9: proxy source 應該有 _get_bry_latest_ts 函數定義 (proxy.py:949)"
        )
        # 修法 9 拍板字眼 (Bry 派工原話)
        self.assertIn(
            "修法 9", proxy_source,
            "修法 9: proxy source 應該有「修法 9」註解字眼"
        )
        self.assertIn(
            "同一個 agent 底下", proxy_source,
            '修法 9: proxy source 應該有 Bry 派工原話「同一個 agent 底下」字眼'
        )
        print(f"[修法 9 已套用] proxy source 有 _get_bry_latest_ts 定義 + 修法 9 註解字眼")


if __name__ == "__main__":
    unittest.main(verbosity=2)
