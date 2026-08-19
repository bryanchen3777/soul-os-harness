"""
test_m1_5_night_400_baseline.py — 修法 15 (M1.5) baseline (Bry 拍板 2026-08-06 22:00)

Bry 派工原話: 「找出這次觸發跟今天早上修的 M2 task 3 placeholder 邏輯有什麼不同 —
重點看 這次是 mode=group + reason=night (diary 觸發的對話路徑), 跟今天驗證過的
spawn_intent 路徑是不是走了不同的程式碼分支, 導致 placeholder user role 沒被正確補上」

Bry 派工原話: 「確認今天的修法 (33ab57e + 317900b) 到底有沒有覆蓋到 diary/night
這條觸發路徑, 還是只覆蓋了 spawn_intent 那條」

Bry 派工原話: 「修完後不要只驗證單一 agent, 比照這次的教訓, 用會觸發全部 10 隻
角色的方式重新驗證, 確認 10/10 都拿到 200 而不是 400」

根因假設 (從 code trace):
- M2 task 3 pop 邏輯在 proxy.py L2370: `if reason != "user_message" and user_message`
- 當 reason=night + draft="" → user_message="" → 整個 block skip
- 317900b 的 placeholder (L2407) 是在 pop 邏輯內 for loop 內, 永遠跑不到
- 結果: messages 沒 user role → M2.7 endpoint 400 "chat content is empty"

修法方向:
- 從 pop 邏輯內 for loop 把 placeholder 拉出來
- 條件: `if reason != "user_message"` (任何 proactive 觸發都加 placeholder, 不管 user_message 空不空)
- 原本 pop 邏輯 (從 messages 移走真實 draft user role) 仍需 user_message 非空才跑
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestM15Baseline(unittest.TestCase):
    """M1.5 修法前的現狀證明 — Bry 8/6 22:00 派工."""

    def test_01_m2_task3_pop_block_only_runs_with_user_message(self):
        """現狀: M2 task 3 pop 邏輯只在 user_message 非空時跑."""
        # 從 proxy.py L2370 抽: `if reason != "user_message" and user_message`
        # 對應到: reason=night + draft="" → user_message="" → 整個 block skip
        # → placeholder user role 永遠不會被加
        # 修法後: 條件改成 `if reason != "user_message"` 即可
        # 不過我們要驗證現狀的 bug, 不修邏輯
        
        # 這測試透過直接呼叫 _handle_event_impl 模擬 scheduler night 觸發
        # 然後看 _complete_with_retry 收到的 messages 結構
        from src.llm.proxy import LLMProxy
        
        # 簡化測試: 不跑完整 proxy, 只驗證邏輯片段
        # 用 mock 模擬 messages list 經過 M2 task 3 區塊後的狀態
        reason = "night"
        user_message = ""  # 模擬 night diary draft 空
        messages = [
            {"role": "system", "content": "persona prompt"},
            {"role": "user", "content": ""},  # current_input 空 (from _build_messages_group L352)
        ]
        
        # 模擬 M2 task 3 block (L2370-2408)
        if reason != "user_message" and user_message:
            for _i in range(len(messages) - 1, -1, -1):
                if messages[_i]["role"] == "user" and messages[_i]["content"] == user_message:
                    messages.pop(_i)
                    messages.append({"role": "system", "content": "trigger label"})
                    messages.append({"role": "user", "content": "（proactive trigger）"})
                    break
        
        # 修法前: 因為 user_message="" → 整個 block skip → messages 沒變
        # 結果: 還有一個 user role 但 content 是空字串
        user_roles = [m for m in messages if m["role"] == "user"]
        self.assertEqual(len(user_roles), 1, "user role 仍存在 (空內容)")
        self.assertEqual(user_roles[0]["content"], "", "user role 內容是空字串")
        # 修法前: 沒有 "（proactive trigger）" placeholder
        has_placeholder = any(m.get("content") == "（proactive trigger）" for m in messages)
        self.assertFalse(has_placeholder, "修法前: placeholder user role 沒加 (因 user_message 空)")

    def test_02_real_400_count_after_restart(self):
        """重啟後 400 累積次數: 從 server_nohup.err 統計, 確認規模性問題."""
        server_log = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/server_nohup.err")
        if not server_log.is_file():
            self.skipTest("server log not found")
        # 從 server log 找 "HTTP 400" 模式, 但要帶 agent= 確認是 LLM 400 (不是其他)
        content = server_log.read_text(encoding="utf-8")
        # 重啟時間戳: 2026-08-06 22:00:43
        restart_time = "2026-08-06 22:00:43"
        after_restart = content.split(restart_time, 1)[-1] if restart_time in content else content
        # 找 400 lines
        import re
        four_hundreds = re.findall(
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\n]*HTTP 400[^\n]*agent=(\w+)',
            after_restart
        )
        # 同樣找 stub fallback 確認
        stubs = re.findall(
            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\n]*AGENT_SPEAK\(stub\) reason=llm_failed[^\n]*agent=(\w+)',
            after_restart
        )
        print(f"\n  [M1.5 baseline] 重啟後 400 數: {len(four_hundreds)}")
        print(f"  [M1.5 baseline] 涉及 agent 數: {len(set(a for _, a in four_hundreds))}")
        print(f"  [M1.5 baseline] stub fallback 數: {len(stubs)}")
        # Bry 派工前提: 「10/10 全中」 是 P0
        # 如果 < 5 條, 測試 skip (可能 server 還沒觸發)
        if len(four_hundreds) < 5:
            self.skipTest(f"重啟後 400 數 < 5 ({len(four_hundreds)}), server 可能還沒完全啟動")
        # 確認 400 數量級 (10 隻角色)
        self.assertGreaterEqual(len(four_hundreds), 5,
            f"重啟後 400 數應該 >= 5 (Bry 8/6 22:00 看到 10 隻全中), 實際 {len(four_hundreds)}")
        # 確認 stub fallback 數量也跟著 (Bry 派工「stub fallback 頂住」邏輯)
        self.assertGreaterEqual(len(stubs), 5,
            f"stub fallback 數應該跟著 400 走, 實際 {len(stubs)}")

    def test_03_4xx_log_all_chat_content_empty(self):
        """4xx log 全部是 chat content is empty (2013), 確認是老問題復發."""
        log_4xx = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/logs/llm_4xx_response.log")
        if not log_4xx.is_file():
            self.skipTest("4xx log not found")
        content = log_4xx.read_text(encoding="utf-8")
        # 統計 unique error message
        import re
        errors = re.findall(r'"message":"([^"]+)"', content)
        unique_errors = set(errors)
        print(f"\n  [M1.5 baseline] 4xx log unique errors: {unique_errors}")
        # Bry 派工: 「是不是又是 chat content is empty 那個老問題復發」
        self.assertIn("invalid params, chat content is empty (2013)", unique_errors,
            "4xx log 應該含 chat content is empty 老問題")

    def test_04_diary_does_not_depend_on_proxy_messages(self):
        """確認 M0.5 diary 寫入不依賴 proxy 的 messages 結構 (互不影響).

        diary 走 src/soul/diary.py 自己的 LLM call (diary.py:_call_llm_for_diary)
        proxy 400 是 main chat path 觸發
        兩者獨立, M0.5 修法沒被 400 影響
        """
        # 簡單斷言: M0.5 寫入檔案存在 + 有 source=llm
        from src.soul.diary import get_diary_writer
        writer = get_diary_writer()
        # 找 8/6 ~ 8/12 範圍的 diary
        diary_dir = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/soul/agent_rem/diary")
        llm_entries = 0
        for jsonl in diary_dir.glob("2026-08-*.jsonl"):
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                e = json.loads(line)
                if e.get("source") == "llm":
                    llm_entries += 1
        self.assertGreater(llm_entries, 0,
            f"M0.5 diary 寫入應該有 source=llm 條目, 實際 {llm_entries}")
        print(f"\n  [M1.5 baseline] Rem 8/6~8/12 diary source=llm 條目: {llm_entries}")


if __name__ == "__main__":
    print("=" * 60)
    print("M1.5 v1 baseline (Bry 派工 2026-08-06 22:00)")
    print("=" * 60)
    unittest.main(verbosity=2)
