"""Bry 派工查證 #2: 統計所有 server log 含動作標記的訊息, 收集格式樣本."""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

LOG_DIR = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/logs")

# 動作標記候選格式
ACTION_PATTERNS = {
    "full_paren_ja": r"[（(][^()\n]{2,80}[）)]",  # 全形或半形括號
    "asterisk": r"\*[^*\n]{2,80}\*",  # *動作*
    "dash_dash": r"--[^-]{2,80}--",  # --動作--
    "em_dash": r"——[^—\n]{2,80}——",  # ——動作——
    "jps_kagi": r"「[^」\n]{2,80}」",  # 「動作」 (但可能是對話)
    "single_paren": r"\([^)]{2,80}\)",  # 半形 (action) 跟數字/英文混淆
}


def is_action_format(text, fmt):
    """判斷 text 是否是動作描述 (不是 Bry/數字)."""
    if fmt == "full_paren_ja":
        # 排除一些常見的非動作: 純中文括號翻譯, 純數字, 純字母
        m = re.findall(r"[（(]([^()\n]{2,80})[）)]", text)
        for inner in m:
            # 動作描述通常含動詞/形容詞, 不只是名詞或純翻譯
            if any(kw in inner for kw in ["走", "靠", "低頭", "抬頭", "笑", "看", "聽", "說",
                                            "摸", "抬", "點", "伸", "張", "望", "轉", "咬",
                                            "嘆", "抱", "拉", "搖", "點", "紅", "垂", "彎",
                                            "彎腰", "起身", "坐下", "站", "走過", "走近",
                                            "摸", "閉", "睜", "揮", "收", "伸出"]):
                return True
        return False
    return True


def main():
    all_logs = sorted(LOG_DIR.glob("server_*.err"))
    print(f"=== 搜尋 {len(all_logs)} 個 server log ===\n")

    all_msgs = []
    for log in all_logs:
        if log.stat().st_size < 1000:
            continue
        try:
            content = log.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # 抓生成完成 + text=...audio_text=...
        # 格式: text='...' audio_text='...' emotion='...' translation='...'
        # 用兩段 greedy match: text='xxx' audio_text='yyy'
        for m in re.finditer(
            r"\[LLMProxy\] 生成完成 \| agent=(\w+) text='((?:[^'\\]|\\.)*)' audio_text='((?:[^'\\]|\\.)*)'",
            content,
        ):
            agent, text, audio_text = m.group(1), m.group(2), m.group(3)
            all_msgs.append({
                "source": log.name,
                "agent": agent,
                "text": text,
                "audio_text": audio_text,
                "same": text == audio_text,
            })
    print(f"=== 抓到的「生成完成」紀錄: {len(all_msgs)} ===\n")

    # 1. text vs audio_text 完全相同統計
    same_count = sum(1 for m in all_msgs if m["same"])
    diff_count = len(all_msgs) - same_count
    print(f"text == audio_text (完全相同): {same_count} ({100*same_count/len(all_msgs):.1f}%)")
    print(f"text != audio_text (有差):     {diff_count} ({100*diff_count/len(all_msgs):.1f}%)")

    # 2. 動作標記格式統計 (用 audio_text 來看, 跟 Bry 派工的「TTS 唸出來」直接相關)
    print(f"\n=== 動作標記格式統計 (以 audio_text 為主) ===\n")
    format_counter = {k: 0 for k in ACTION_PATTERNS}
    has_action_samples = []
    for m in all_msgs:
        at = m["audio_text"]
        for fmt, pat in ACTION_PATTERNS.items():
            if re.search(pat, at):
                if is_action_format(at, fmt):
                    format_counter[fmt] += 1
                    if len(has_action_samples) < 8 and fmt == "full_paren_ja":
                        has_action_samples.append(m)
    for fmt, cnt in format_counter.items():
        print(f"  {fmt:20s} {cnt} 條")

    print(f"\n=== 含全形括號的 audio_text 樣本 (前 8 條) ===\n")
    for s in has_action_samples:
        print(f"  [{s['agent']:15s}] {s['audio_text'][:120]}")
        print()

    # 3. 抽 5 條 text != audio_text 的差異樣本
    diff_samples = [m for m in all_msgs if not m["same"]][:5]
    print(f"\n=== text != audio_text 差異樣本 (前 5 條) ===\n")
    for s in diff_samples:
        print(f"  [{s['agent']:15s}]")
        print(f"    text:      {s['text'][:100]}")
        print(f"    audio_text: {s['audio_text'][:100]}")


if __name__ == "__main__":
    main()
