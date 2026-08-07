"""
test_m2_0_inner_life_v1.py — M2.0 baseline: 內在人生記憶完全斷開, 對話組裝沒注入

Bry 派工 2026-08-07 15:44 (貼給 Mavis):
> 派 Mavis 用 A 方案設計: 在 _build_messages_group/_build_messages_private
> (呼應 β2.1 事件背景那段的做法) 注入一小段『最近 diary/dream/event』摘要文字,
> 只加輕量字串, 不動 SAGE/v1/Loader/consciousness 任何既有邏輯,
> 範圍限定在 proxy.py 這一個檔案

Bry 8/7 15:34 記憶串接查證 (派工原話):
> 對話記憶: ... 角色跟你對話時可以調用的記憶庫, 讓角色能在對話中主動提起
> 自己的內在生活(例如「今天夢到 XX」), 還是兩套系統完全斷開?
> 之前查到一半中斷過, 這次要徹底查完

這個 v1 驗證現狀 (before M2.0 修法):
- _format_recent_inner_life helper 不存在
- proxy.py 內 _build_messages_group / _build_messages_private 沒有注入
  diary/dream/event 摘要區塊
- proxy.py 內沒有 [最近內在生活] / ## 你的最近內在生活 / inner_life 相關字串
- diary.py / dream_event.py 寫入正常, 但 read_entries / recent_entries 無 caller
- v2 修法要讓上面三點變成「存在」, 並在對話組裝時真的注入到 system_parts

Bry 派工精神 (要保留給未來 session 看):
- 「現成先例可循優先於設計新模式」 — 沿用 β2.1 注入風格
- 「Bry 派工 A 方案等同 B 方案覆蓋時, 改動更小的優先」 — A 方案只動 proxy.py
- 「Bry 拒絕把 diary 當事實送進長期記憶圖譜, 語意上不太對」
- 「不為假設中的未來灑過濾網」 — 只動對話組裝, 不動記憶基礎設施
- 「只加輕量字串」 — 注入字串, 不改既有 messages 結構

Bry 拒絕的選項 (要保留):
- B 方案 (改 agent_intent 出口套 whitelist): 改動大, 為假設的未來灑過濾網
- C 方案 (mirror 進 v1 store): 影響全部 10 隻角色記憶管線核心基礎設施
- 直接把 diary 拆 fact 送進 SAGE graph: 語意上不對 (日記是主觀生活片段,
  不是需要 LLMJudge 判斷真假的對話事實)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 注入位置: 跟 L266-271 mood_desc / L279 current_time 對齊
PROXY_PATH = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/src/llm/proxy.py"
)


class TestM20InnerLifeBaseline(unittest.TestCase):
    """驗證現狀 (before M2.0 修法) — 沒有 inner_life 注入"""

    def setUp(self):
        self.proxy_source = PROXY_PATH.read_text(encoding="utf-8")

    def test_a_no_helper_function(self):
        """v1: _format_recent_inner_life helper 不存在 (修法前沒有)"""
        self.assertFalse(
            hasattr(proxy, "_format_recent_inner_life"),
            "v1 baseline 期望 proxy 模組沒有 _format_recent_inner_life (修法前不存在)"
        )
        print("[v1 baseline] proxy 模組沒有 _format_recent_inner_life (預期: 修法後才存在)")

    def test_b_proxy_source_no_inner_life_injection(self):
        """v1: proxy.py 內 _build_messages_group / _build_messages_private 沒有 inner_life 字串

        Bry 派工: 「不動 SAGE/v1/Loader/consciousness 任何既有邏輯, 範圍限定在 proxy.py」
        v1 驗證: proxy.py 內沒有 [最近內在生活] / 你的最近內在生活 / inner_life 字串
        v2 修法: 上面三個字串會出現, 並且在 _build_messages_group / _build_messages_private 內
        """
        # 找 _build_messages_group / _build_messages_private 函式
        for fn_name in ["_build_messages_group", "_build_messages_private"]:
            match = re.search(
                rf"def {fn_name}.*?(?=\ndef |\nclass |\n# ─)",
                self.proxy_source,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"找不到 {fn_name} 函式")
            fn_source = match.group(0)
            # v1 baseline 期望: 函式內沒有 inner_life / 內在生活 / 你的最近內在生活
            for marker in ["_format_recent_inner_life", "[最近內在生活]", "你的最近內在生活", "inner_life"]:
                self.assertNotIn(
                    marker, fn_source,
                    f"v1 baseline 期望 {fn_name} 沒有 {marker} (修法前沒注入), "
                    f"但找到了 → 修法可能已套用或 v1 寫在修法後"
                )
        print("[v1 baseline] _build_messages_group / _build_messages_private 沒有 inner_life 注入")

    def test_c_diary_dream_disconnected(self):
        """v1: diary.py 寫入 API 存在, 但 reader API 無 caller (Bry 8/7 15:34 查證結論)

        這個測試保護 Bry 查證結論: 對話記憶完整, 內在人生記憶完全斷開
        修法前: read_entries / recent_entries 在 diary.py 內定義但無 caller
        修法後: _format_recent_inner_life 會用 read_entries (或自己 Path.read_text)
        """
        diary_path = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/diary.py"
        )
        diary_source = diary_path.read_text(encoding="utf-8")
        # 找 read_entries / recent_entries 函式定義
        for fn_name in ["def read_entries", "def recent_entries"]:
            self.assertIn(
                fn_name, diary_source,
                f"v1 期望 diary.py 內有 {fn_name} 函式定義 (Bry 8/7 15:34 查證)"
            )
        # 找整個 codebase 內 read_entries / recent_entries 的 caller
        repo_root = Path("C:/Users/bbfcc/.local/bin/soul-os-harness")
        caller_count = 0
        for src_file in repo_root.rglob("src/**/*.py"):
            try:
                content = src_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if "diary.py" in str(src_file):
                continue  # 跳過 diary.py 自己
            for fn_name in ["read_entries", "recent_entries"]:
                if f".{fn_name}(" in content or f" {fn_name}(" in content:
                    caller_count += 1
        # v1 baseline 期望: 無 caller (Bry 查證結論)
        self.assertEqual(
            caller_count, 0,
            f"v1 baseline 期望 read_entries/recent_entries 無 caller (Bry 8/7 15:34 查證結論), "
            f"實際找到 {caller_count} 個 caller — 修法可能已套用或 v1 寫在修法後"
        )
        print(f"[v1 baseline] diary read_entries/recent_entries 無 caller (Bry 8/7 15:34 查證結論保留下來)")

    def test_d_no_diary_root_constant_in_proxy(self):
        """v1: proxy.py 內沒有 DIARY_ROOT / INNER_LIFE_DAYS / INNER_LIFE_MAX_ENTRIES 常數

        v2 修法會新增這些常數, 跟 β2.1 風格一致 (Bry 派工: 「呼應 β2.1 事件背景那段的做法」)
        v1 baseline 確認修法前 proxy.py 沒有這些常數
        """
        for marker in ["INNER_LIFE_DAYS", "INNER_LIFE_MAX_ENTRIES", "INNER_LIFE_DATA_DIR"]:
            self.assertNotIn(
                marker, self.proxy_source,
                f"v1 baseline 期望 proxy.py 沒有 {marker} 常數 (修法後才加), "
                f"但找到了"
            )
        print("[v1 baseline] proxy.py 沒有 INNER_LIFE_* 常數 (修法後才加)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
