"""
test_m2_0_inner_life_v2.py — M2.0 修法驗證: 內在人生記憶接回對話, 注入 diary/dream/event 摘要

Bry 派工 2026-08-07 15:44 (貼給 Mavis):
> 派 Mavis 用 A 方案設計: 在 _build_messages_group/_build_messages_private
> (呼應 β2.1 事件背景那段的做法) 注入一小段『最近 diary/dream/event』摘要文字,
> 只加輕量字串, 不動 SAGE/v1/Loader/consciousness 任何既有邏輯,
> 範圍限定在 proxy.py 這一個檔案

這個 v2 驗證修法:
- proxy.py 新增 _format_recent_inner_life(agent_id) helper
- 從 data/soul/{agent_id}/diary/{date}.jsonl 讀最近 N 天 entry
- 過濾 slot in (morning, night, dream, event) — diary/dream/event 全包
- 注入到 _build_messages_group / _build_messages_private 的 system_parts
- 注入位置: mood_desc 之後, current_time 之前 (跟 L266-271 / L279 對齊)
- 注入字串風格: 跟 β2.1 event_block 一樣 (使用說明 + 區塊標題)
- 範圍: 只動 proxy.py, 不動 SAGE/v1/Loader/consciousness
- 向後相容: agent 沒 diary jsonl → helper 回空, 注入 skip (Ram 是 _NO_DIARY 旗標但
  仍由 Scheduler 觸發 diary, 所以不需要 hardcode whitelist)

Mock 範圍:
- 測試 _format_recent_inner_life 直接讀檔 + 過濾 + 格式化
- 測試 _build_messages_group / _build_messages_private 內 system_parts 注入邏輯
- Source 層: proxy.py 內 _build_messages_group / _build_messages_private 出現 inner_life 字串
- 邊界: 沒 diary jsonl → 注入 skip
- 邊界: diary slot 是 placeholder (morning) → 也注入 (Bry 8/7 15:44 沒說要過濾 placeholder)
- 邊界: diary slot 不在 (morning/night/dream/event) → 過濾掉

Bry 派工原文 (要保留給未來 session 看):
- 「在 _build_messages_group/_build_messages_private (呼應 β2.1 事件背景那段的做法)
   注入一小段『最近 diary/dream/event』摘要文字, 只加輕量字串」
- 「不動 SAGE/v1/Loader/consciousness 任何既有邏輯, 範圍限定在 proxy.py 這一個檔案」
- 「請照慣例走 mock test 流程 (before→code→after→commit-only), 完成後回報」

Bry 派工精神 (要保留):
- 「現成先例可循優先於設計新模式」 — 沿用 β2.1 注入風格
- 「只加輕量字串」 — 注入字串, 不改既有 messages 結構 (加到 system_parts, 跟 mood_desc 一樣)
- 「範圍限定在 proxy.py 這一個檔案」 — 只動 proxy.py
- 「不動 SAGE/v1/Loader/consciousness」 — 記憶基礎設施不動
- 「Bry 拒絕把 diary 當事實送進長期記憶圖譜, 語意上不太對」 — 只注入 prompt context
"""
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


PROXY_PATH = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/src/llm/proxy.py"
)


# 抽 _build_messages_group / _build_messages_private 函式原始碼 (source 層驗證用)
def _get_fn_source(fn_name: str) -> str:
    source = PROXY_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"def {fn_name}.*?(?=\ndef |\nclass |\n# ─)",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"找不到 {fn_name} 函式")
    return match.group(0)


class TestM20InnerLifeHelper(unittest.TestCase):
    """驗證 _format_recent_inner_life helper 邏輯"""

    def test_a_helper_exists(self):
        """v2: _format_recent_inner_life helper 存在於 proxy 模組"""
        self.assertTrue(
            hasattr(proxy, "_format_recent_inner_life"),
            "v2 期望 proxy 模組有 _format_recent_inner_life helper"
        )
        self.assertTrue(
            callable(proxy._format_recent_inner_life),
            "v2 期望 _format_recent_inner_life 是 callable"
        )
        print("[v2] proxy 模組有 _format_recent_inner_life helper")

    def test_b_helper_returns_empty_when_no_diary(self):
        """v2: agent 沒 diary jsonl → helper 回空字串, 注入 skip"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(proxy, "INNER_LIFE_DATA_DIR", tmp):
                out = proxy._format_recent_inner_life("agent_nobody")
        self.assertEqual(
            out, "",
            f"v2 期望沒 diary 時回空字串, 實際: {out!r}"
        )
        print("[v2] 沒 diary 時 helper 回空字串 (注入 skip)")

    def test_c_helper_reads_recent_entries(self):
        """v2: helper 從 data/soul/{agent_id}/diary/{date}.jsonl 讀最近 N 天 entry

        Mock 場景: 建立 agent_test 資料夾, 寫今天/昨天/前天 三天 jsonl:
        - 今天: morning + night
        - 昨天: dream
        - 前天: event
        預期: helper 回 4 條 entry (全包 morning/night/dream/event)
        """
        with tempfile.TemporaryDirectory() as tmp:
            agent_id = "agent_test"
            agent_dir = Path(tmp) / agent_id / "diary"
            agent_dir.mkdir(parents=True, exist_ok=True)

            today = datetime.now()
            dates = {
                "today": today.strftime("%Y-%m-%d"),
                "yesterday": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
                "day_before": (today - timedelta(days=2)).strftime("%Y-%m-%d"),
            }

            # 今天: morning + night
            with (agent_dir / f"{dates['today']}.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-07T01:00:00+00:00", "slot": "morning",
                    "content": "今天起床時陽光從窗簾透進來。",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")
                f.write(json.dumps({
                    "ts": "2026-08-07T20:00:00+00:00", "slot": "night",
                    "content": "今天過完了, 還挺平靜的。",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")
            # 昨天: dream
            with (agent_dir / f"{dates['yesterday']}.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-06T03:00:00+00:00", "slot": "dream",
                    "content": "夢到跟大家在海邊散步。",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")
            # 前天: event
            with (agent_dir / f"{dates['day_before']}.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-05T15:00:00+00:00", "slot": "event",
                    "content": "走廊盡頭的燈突然閃了一下。",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")

            with patch.object(proxy, "INNER_LIFE_DATA_DIR", tmp):
                out = proxy._format_recent_inner_life(agent_id)

        # v2 期望: 4 條 entry 都被讀到
        self.assertIn("morning", out, f"v2 期望 helper 輸出含 morning slot, 實際: {out}")
        self.assertIn("night", out, f"v2 期望 helper 輸出含 night slot, 實際: {out}")
        self.assertIn("dream", out, f"v2 期望 helper 輸出含 dream slot, 實際: {out}")
        self.assertIn("event", out, f"v2 期望 helper 輸出含 event slot, 實際: {out}")
        # 期望含日期 + 內容
        self.assertIn(dates["today"], out)
        self.assertIn(dates["yesterday"], out)
        self.assertIn(dates["day_before"], out)
        self.assertIn("陽光", out, "v2 期望 helper 輸出含今天 morning 內容")
        print(f"[v2] helper 讀 3 天 4 條 entry (morning/night/dream/event) 全部輸出")

    def test_d_helper_filters_invalid_slots(self):
        """v2: helper 過濾掉 slot 不在 (morning, night, dream, event) 的 entry

        Mock 場景: jsonl 內有 noise slot (e.g. "test_debug"), 應該被過濾
        預期: helper 輸出不含 noise slot 內容
        """
        with tempfile.TemporaryDirectory() as tmp:
            agent_id = "agent_test"
            agent_dir = Path(tmp) / agent_id / "diary"
            agent_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            with (agent_dir / f"{today}.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-07T01:00:00+00:00", "slot": "morning",
                    "content": "正常 morning entry",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")
                f.write(json.dumps({
                    "ts": "2026-08-07T01:00:00+00:00", "slot": "test_debug",
                    "content": "NOISE_NOISE_NOISE",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")

            with patch.object(proxy, "INNER_LIFE_DATA_DIR", tmp):
                out = proxy._format_recent_inner_life(agent_id)

        self.assertIn("正常 morning", out)
        self.assertNotIn(
            "NOISE_NOISE_NOISE", out,
            f"v2 期望 helper 過濾掉非 diary slot, 但 noise 漏出去了: {out}"
        )
        print(f"[v2] helper 過濾掉非 diary slot (test_debug → 跳過)")

    def test_e_helper_truncates_long_entries(self):
        """v2: helper 截斷每條 entry 到 INNER_LIFE_MAX_CHARS_PER_ENTRY (Bry 派工: 「只加輕量字串」)"""
        with tempfile.TemporaryDirectory() as tmp:
            agent_id = "agent_test"
            agent_dir = Path(tmp) / agent_id / "diary"
            agent_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            long_content = "這是一條超長的 diary 內容" * 20  # 200+ chars
            with (agent_dir / f"{today}.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-07T01:00:00+00:00", "slot": "morning",
                    "content": long_content,
                    "source": "llm",
                }, ensure_ascii=False) + "\n")

            with patch.object(proxy, "INNER_LIFE_DATA_DIR", tmp):
                out = proxy._format_recent_inner_life(agent_id)

        # 預期: 截斷 (用 ... 或同類 marker), 不會把 200 字全塞
        self.assertLess(
            len(out), len(long_content),
            f"v2 期望 helper 截斷長 entry, 實際輸出 {len(out)} chars (原 {len(long_content)} chars)"
        )
        print(f"[v2] helper 截斷長 entry ({len(long_content)} chars → {len(out)} chars)")

    def test_f_helper_handles_missing_field(self):
        """v2: helper 容忍 entry 缺欄位 (e.g. 沒 content 欄位) → 跳過, 不 crash"""
        with tempfile.TemporaryDirectory() as tmp:
            agent_id = "agent_test"
            agent_dir = Path(tmp) / agent_id / "diary"
            agent_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            with (agent_dir / f"{today}.jsonl").open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-07T01:00:00+00:00", "slot": "morning",
                    # 沒 content 欄位
                    "source": "llm",
                }, ensure_ascii=False) + "\n")
                # 壞 JSON
                f.write("not a json\n")
                # 沒 slot
                f.write(json.dumps({
                    "ts": "2026-08-07T01:00:00+00:00",
                    "content": "沒 slot 欄位的 entry",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")

            with patch.object(proxy, "INNER_LIFE_DATA_DIR", tmp):
                # 不應該 crash
                out = proxy._format_recent_inner_life(agent_id)

        # 預期: 沒 content 跟沒 slot 都跳過, 只有「沒 slot 欄位的 entry」會被過濾
        # 因為 slot 不在 (morning/night/dream/event)
        self.assertNotIn("沒 slot 欄位", out)
        print(f"[v2] helper 容忍缺欄位 / 壞 JSON, 不 crash")


class TestM20InnerLifeInjection(unittest.TestCase):
    """驗證 _build_messages_group / _build_messages_private 注入邏輯"""

    def test_a_injection_in_build_messages_group(self):
        """v2: _build_messages_group 內 system_parts 注入 inner_life 區塊

        Bry 派工: 「在 _build_messages_group/_build_messages_private 注入」
        v2 期望: 兩個函式內呼叫 _format_recent_inner_life + append 到 system_parts
        """
        fn_source = _get_fn_source("_build_messages_group")
        self.assertIn(
            "_format_recent_inner_life", fn_source,
            "v2 期望 _build_messages_group 呼叫 _format_recent_inner_life (注入入口)"
        )
        self.assertIn(
            "system_parts", fn_source,
            "v2 期望 _build_messages_group 內有 system_parts (注入目標)"
        )
        # 注入位置: 在 mood_desc 之後 (跟 L266-271 對齊, 不在 bry_recent 之前)
        mood_desc_idx = fn_source.find("mood_desc")
        inner_life_idx = fn_source.find("_format_recent_inner_life")
        self.assertGreater(
            inner_life_idx, mood_desc_idx,
            f"v2 期望 inner_life 注入在 mood_desc 之後, 實際: "
            f"mood_desc at {mood_desc_idx}, inner_life at {inner_life_idx}"
        )
        # 注入風格: 跟 β2.1 一樣 (使用說明 + 區塊標題)
        self.assertIn(
            "[最近內在生活]", fn_source,
            "v2 期望 _build_messages_group 注入 [最近內在生活] 區塊標題 (跟 β2.1 [當下事件] 風格)"
        )
        print("[v2] _build_messages_group 注入 inner_life 到 system_parts (mood_desc 之後)")

    def test_b_injection_in_build_messages_private(self):
        """v2: _build_messages_private 內 system_parts 注入 inner_life 區塊"""
        fn_source = _get_fn_source("_build_messages_private")
        self.assertIn(
            "_format_recent_inner_life", fn_source,
            "v2 期望 _build_messages_private 呼叫 _format_recent_inner_life (注入入口)"
        )
        self.assertIn(
            "[最近內在生活]", fn_source,
            "v2 期望 _build_messages_private 注入 [最近內在生活] 區塊標題"
        )
        # 注入位置: 在 mood_desc 之後
        mood_desc_idx = fn_source.find("mood_desc")
        inner_life_idx = fn_source.find("_format_recent_inner_life")
        self.assertGreater(
            inner_life_idx, mood_desc_idx,
            f"v2 期望 inner_life 注入在 mood_desc 之後, 實際: "
            f"mood_desc at {mood_desc_idx}, inner_life at {inner_life_idx}"
        )
        print("[v2] _build_messages_private 注入 inner_life 到 system_parts (mood_desc 之後)")

    def test_c_inner_life_block_has_usage_note(self):
        """v2: inner_life 區塊含使用說明 (跟 β2.1 event_block 一樣)

        Bry 派工: 「呼應 β2.1 事件背景那段的做法」
        β2.1 風格: 「這是角色目前所處的情境, 請自然地反映在訊息中, 不要直接複述或解釋」
        v2 期望: inner_life 區塊也有類似反框架語句
        """
        proxy_source = PROXY_PATH.read_text(encoding="utf-8")
        # 找 [最近內在生活] 後面那段字串
        match = re.search(
            r"\[最近內在生活\].*?(?=\n        # |\n        if |\n        messages\.append)",
            proxy_source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "v2 期望找到 [最近內在生活] 區塊")
        block = match.group(0)
        # 期望含反框架語句 (不要直接複述 / 自然運用 / 不要重複 tag)
        for marker in ["不要", "自然", "tag"]:
            self.assertIn(
                marker, block,
                f"v2 期望 inner_life 區塊含反框架語句 ({marker}), 實際: {block}"
            )
        print("[v2] inner_life 區塊含反框架語句 (跟 β2.1 event_block 風格一致)")

    def test_d_no_movement_of_other_code(self):
        """v2 派工精神: 「範圍限定在 proxy.py 這一個檔案, 不動其他既有邏輯」

        v2 期望:
        - M2.0 新增的 _format_recent_inner_life helper 內沒 import 其他既有模組
        - M2.0 注入區段 (_build_messages_group / _build_messages_private 內 inner_life
          那幾行) 沒 import 新模組
        - 常數 INNER_LIFE_DATA_DIR / INNER_LIFE_DAYS / INNER_LIFE_MAX_ENTRIES / 
          INNER_LIFE_MAX_CHARS_PER_ENTRY 存在 proxy.py
        """
        proxy_source = PROXY_PATH.read_text(encoding="utf-8")
        # 1. 常數必須存在
        for const in ["INNER_LIFE_DATA_DIR", "INNER_LIFE_DAYS", "INNER_LIFE_MAX_ENTRIES", "INNER_LIFE_MAX_CHARS_PER_ENTRY"]:
            self.assertIn(
                const, proxy_source,
                f"v2 派工精神: proxy.py 應有 {const} 常數"
            )
        # 2. _format_recent_inner_life helper 內沒 import 其他既有模組
        # (Bry 派工: 「不動 SAGE/v1/Loader/consciousness 任何既有邏輯」)
        match = re.search(
            r"def _format_recent_inner_life.*?(?=\ndef |\nclass |\n# ─)",
            proxy_source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到 _format_recent_inner_life helper")
        helper_source = match.group(0)
        for forbidden_import in [
            "import src.soul.diary",
            "import src.soul.dream_event",
            "from src.soul.diary",
            "from src.soul.dream_event",
            "from src.agent.consciousness",
            "from src.memory.middleware",
            "from src.memory.v1.loader",
            "from src.soul import diary",
            "from src.soul import dream_event",
        ]:
            self.assertNotIn(
                forbidden_import, helper_source,
                f"v2 派工精神: _format_recent_inner_life 內不應 import {forbidden_import}"
            )
        # 3. M2.0 注入區段 (在 _build_messages_group / _build_messages_private 內) 
        # 沒 import 新模組
        for fn_name in ["_build_messages_group", "_build_messages_private"]:
            fn_source = _get_fn_source(fn_name)
            # 找 _format_recent_inner_life 呼叫區段
            inner_idx = fn_source.find("_format_recent_inner_life")
            if inner_idx >= 0:
                # 取後面 800 chars (注入邏輯範圍)
                section = fn_source[inner_idx:inner_idx+800]
                self.assertNotIn(
                    "from src", section,
                    f"v2 派工精神: {fn_name} M2.0 注入區段內不應 import from src.* "
                    f"(範圍限定 proxy.py 內, 用既有 helper)\n實際區段: {section[:200]}..."
                )
        print("[v2] _format_recent_inner_life 跟注入區段沒 import 其他既有模組 (派工精神守住)")

    def test_e_build_messages_group_with_real_inner_life(self):
        """v2 E2E: 帶 inner_life 注入, _build_messages_group 真的把內容放進 system message

        Mock 場景: agent_ruka 有真實 diary jsonl (ruka 18 天份 diary 已知存在)
        預期: 第一個 system message 內含 [最近內在生活] 區塊
        """
        from src.llm.proxy import _build_messages_group

        # 用簡單的 soul + 假 memory 物件
        class MockMemory:
            def get_group_history(self, limit):
                return []

        # 先檢查 ruka 真的 diary jsonl 存在
        ruka_diary = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/data/soul/agent_ruka/diary"
        )
        if not ruka_diary.is_dir():
            self.skipTest("ruka 沒 diary 資料夾, 跳過 E2E 注入驗證")

        messages = _build_messages_group(
            agent_id="agent_ruka",
            soul="你是瑠夏。",
            current_input="",
            memory_context="",
            memory=MockMemory(),
        )
        # 第一個 system message 應該含 inner_life 區塊
        first_system = next((m for m in messages if m["role"] == "system"), None)
        self.assertIsNotNone(first_system, "v2 期望 messages 內有 system message")
        if "[最近內在生活]" not in first_system["content"]:
            # ruka diary 是空的話, helper 回空, 注入 skip → 也是合法
            # 但 v2 E2E 期望有 diary 就能注入
            ruka_files = list(ruka_diary.glob("2026-*.jsonl"))
            print(f"[v2 E2E] ruka 有 {len(ruka_files)} 天 diary, 但 system message 沒 [最近內在生活]")
            print(f"[v2 E2E] 可能是 helper 沒讀到, 或內容量太多被截斷, 或 ruka diary 真的空")
            # 至少驗證 _format_recent_inner_life 直接呼叫有結果
            from src.llm.proxy import _format_recent_inner_life
            out = _format_recent_inner_life("agent_ruka")
            self.assertNotEqual(
                out, "",
                f"v2 E2E 期望 ruka helper 直接呼叫回非空, 實際空, "
                f"但 ruka 已知有 18 天份 diary"
            )
            self.fail(
                f"v2 E2E: ruka 有 diary 但 [最近內在生活] 沒注入 → "
                f"helper 直接呼叫回 {len(out)} chars, 注入邏輯有問題"
            )
        print(f"[v2 E2E] ruka _build_messages_group 系統 message 真的有 [最近內在生活] 區塊")


if __name__ == "__main__":
    unittest.main(verbosity=2)
