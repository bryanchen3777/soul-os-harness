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
        """Baseline: _is_bry_online 用 messages_with_meta 參數算 (沒跨 session)
        模擬 main session 內 Bry 52.26h 前, 呼叫 _is_bry_online(main_session_history, NOW)
        期望 False (Bry 不在線, 52.26h > 30min)
        注意: 這個 case 沒法 mock 跨 session 數據, 因為現狀 _is_bry_online 只看傳入的 messages
        """
        result = proxy._is_bry_online(self.main_session_history, NOW)
        delta_sec = NOW - BRY_MAIN_SESSION_TS
        delta_h = delta_sec / 3600
        print(f"  Bry main session 最後: ts={BRY_MAIN_SESSION_TS} (8/2 14:57 EDT)")
        print(f"  觸發時間: ts={NOW} (8/4 19:12 EDT)")
        print(f"  差距: {delta_h:.1f} 小時")
        print(f"  _is_bry_online 判斷: {result} (預期 False = Bry 不在線)")
        self.assertFalse(
            result,
            f"_is_bry_online 應該回 False (Bry 不在線, 52.26h > 30min), 實際: {result}"
        )

    def test_b_baseline_compute_silence_uses_messages_param(self):
        """Baseline: _compute_silence_str 用 messages_with_meta 參數算 (沒跨 session)
        模擬 main session 內 Bry 52.26h 前, 呼叫 _compute_silence_str(main_session_history, NOW)
        期望顯示 2 天 (52.26h floor=2) — 但現狀沒考慮 TG session Bry 6.5h 前講過話
        """
        result = proxy._compute_silence_str(self.main_session_history, NOW)
        print(f"  _compute_silence_str 結果: {result!r}")
        print(f"  Baseline 期望: 顯示「距離 Bry 上次跟你說話已經 2 天」 (52.26h floor=2)")
        self.assertEqual(
            result, "距離 Bry 上次跟你說話已經 2 天",
            "Baseline (v1) 期望 _compute_silence_str 只看當前 session, 顯示 2 天 (52.26h floor)"
        )

    def test_c_baseline_no_cross_session_helper(self):
        """Baseline: proxy 沒有 _get_bry_latest_ts / _get_bry_latest_ts_across_sessions helper"""
        self.assertFalse(
            hasattr(proxy, "_get_bry_latest_ts"),
            "Baseline (v1) 期望 proxy 沒有 _get_bry_latest_ts, 修法 9 才加"
        )
        self.assertFalse(
            hasattr(proxy, "_get_bry_latest_ts_across_sessions"),
            "Baseline (v1) 期望 proxy 沒有 _get_bry_latest_ts_across_sessions"
        )
        print(f"[v1 baseline] proxy 沒有 _get_bry_latest_ts / _get_bry_latest_ts_across_sessions")

    def test_d_baseline_proxy_source_no_method_9(self):
        """Baseline: proxy source 沒修法 9 注入字眼
        注意: proxy 已有「跨 session」字眼 (RAG 注入註解 L80774)
        修法 9 注入字眼用更精準的 pattern 找
        """
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            "_get_bry_latest_ts", proxy_source,
            "Baseline (v1) 期望 proxy source 沒 _get_bry_latest_ts, 修法 9 才加"
        )
        # 修法 9 拍板字眼 (精準 pattern, 排除 RAG 既有「跨 session」)
        self.assertNotIn(
            "修法 9", proxy_source,
            "Baseline (v1) 期望 proxy source 沒「修法 9」字眼, 修法 9 commit 才加"
        )
        # 修法 9 拍板 Bry 派工原話字眼
        self.assertNotIn(
            "同一個 agent 底下", proxy_source,
            "Baseline (v1) 期望 proxy source 沒「同一個 agent 底下」字眼"
        )
        print(f"[v1 baseline] proxy source 沒修法 9 注入字眼 (排除 RAG 既有「跨 session」)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
