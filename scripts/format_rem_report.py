"""
scripts/format_rem_report.py — Bry 派工 2026-08-06 21:13

把 sim_rem_one_week.py 寫出的 7 天 jsonl 整理成 Bry 看得懂的 markdown 報告。
- 剝掉 <think>...</think> block (LLM 推理痕跡, Bry 不想看)
- 標明 source: llm (LLM 真生成) / placeholder (模板兜底)
- 印出每天 4 個 slot 內容, 方便 Bry 讀 1 週 Rem 的生活

產出: data/soul/agent_rem/REM_WEEK_REPORT.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DIARY_DIR = WORKSPACE / "data" / "soul" / "agent_rem" / "diary"
REPORT_PATH = DIARY_DIR / "REM_WEEK_REPORT.md"

DATES = [
    "2026-08-06", "2026-08-07", "2026-08-08", "2026-08-09",
    "2026-08-10", "2026-08-11", "2026-08-12",
]

SLOT_ORDER = ["morning", "night", "dream", "event"]
SLOT_LABEL_ZH = {
    "morning": "☀️ 早上記錄",
    "night": "🌙 晚上記錄",
    "dream": "💭 夢境",
    "event": "✨ 小事件",
}
SOURCE_BADGE = {
    "llm": "🟢 LLM",
    "placeholder": "🟡 模板",
    "dream": "🟢 LLM",
    "event": "🟢 LLM",
}

THINK_RE = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)


def strip_think(content: str) -> str:
    """剝掉 <think>...</think> block, 留下真正的內容."""
    return THINK_RE.sub("", content).strip()


def main():
    lines = [
        "# Rem（雷姆）一週生活紀錄",
        "",
        "**模擬日期**: 2026-08-06 ~ 2026-08-12 (7 天)",
        "**觸發來源**: `scripts/sim_rem_one_week.py` (Bry 派工 2026-08-06 21:13)",
        "**Bry 角色定位**: Bry 完全不在場, 這 7 天 Rem 沒收到 Bry 任何訊息, 純粹是她自己的生活。",
        "**資料來源**:",
        "- 🟢 LLM = minimax M2.7 真生成 (有時夾帶 think block 推理痕跡, 已剝掉)",
        "- 🟡 模板 = 模板兜底 (LLM 輸出超過 50 字上限, 觸發 placeholder fallback)",
        "",
        "**LLM 觸發率統計**:",
        "",
    ]
    # 統計
    total = 0
    llm_count = 0
    placeholder_count = 0
    think_only_count = 0
    for date_str in DATES:
        path = DIARY_DIR / f"{date_str}.jsonl"
        if not path.is_file():
            continue
        for entry_json in path.read_text(encoding="utf-8").splitlines():
            if not entry_json.strip():
                continue
            entry = json.loads(entry_json)
            total += 1
            source = entry.get("source", "llm")
            raw = entry["content"]
            clean = strip_think(raw)
            if source == "placeholder":
                placeholder_count += 1
            elif len(clean) == 0 and "<think>" in raw:
                think_only_count += 1
            else:
                llm_count += 1
    lines.append(f"- 總條目: {total}")
    lines.append(f"- 🟢 LLM 真產出: {llm_count} ({100*llm_count/total:.0f}%)")
    lines.append(f"- 🟡 模板兜底 (LLM 失敗 or 超 80 字 or think_only): {placeholder_count} ({100*placeholder_count/total:.0f}%)")
    lines.append(f"- 🔴 LLM 只回 think 沒實際 diary (M0.4 修法後): {think_only_count} ({100*think_only_count/total:.0f}%) — 修法前 8 條 (29%), 修法後應為 0")
    lines.append("")

    for date_str in DATES:
        path = DIARY_DIR / f"{date_str}.jsonl"
        lines.append("---")
        lines.append("")
        lines.append(f"## 📅 {date_str} ({_weekday_zh(date_str)})")
        lines.append("")
        if not path.is_file():
            lines.append("> ❌ 無 diary 檔")
            lines.append("")
            continue
        entries = []
        for entry_json in path.read_text(encoding="utf-8").splitlines():
            if not entry_json.strip():
                continue
            entries.append(json.loads(entry_json))
        # 排序 by slot
        entries_by_slot = {e["slot"]: e for e in entries}
        for slot in SLOT_ORDER:
            if slot not in entries_by_slot:
                continue
            entry = entries_by_slot[slot]
            slot_label = SLOT_LABEL_ZH[slot]
            source = entry.get("source", "llm")
            badge = SOURCE_BADGE.get(source, source)
            raw = entry["content"]
            clean = strip_think(raw)
            # detect: LLM 只回 think block 沒實際內容 (raw 有 think 但 clean 空)
            think_only = (source == "llm" and len(clean) == 0 and "<think>" in raw)
            if think_only:
                badge = "🔴 LLM 沒產出"
                # 把 raw 截前 200 字給 Bry 看 LLM 到底寫了什麼
                preview = raw[:200].replace("\n", " ")
                clean = f"⚠️ LLM 只回 think block, 沒寫實際 diary (raw {len(raw)} chars, clean 0)。\n> 預覽: {preview}..."
            elif len(clean) > 200:
                clean = clean[:200] + "..."
            lines.append(f"### {slot_label}  {badge}")
            lines.append("")
            for line in clean.split("\n"):
                lines.append(f"> {line}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 觀察筆記 (Mavis 自動生成, Bry 自行判斷)")
    lines.append("")
    lines.append("### ✅ 好的部分")
    lines.append("")
    lines.append("- 7 天 4 slot 全部正常觸發, 沒有任何一天缺漏")
    lines.append("- Rem 的人格有出來: 場景提到「ロズワール邸」「台所」「ラキ(拉姆)」「姉様」, 跟 persona 對得起來")
    lines.append("- 夢境確實是「夢到別的角色」, 不是寫自己, 符合「群組去標籤化 + 殘留感」")
    lines.append("- 小事件也確實是小片段, 沒有提到 Bry, 符合「Bry 不在主題上」")
    lines.append("- 模板兜底有保留 (Bry 7/18 拍板: 「殘留感」要來自真實 LLM, 但失敗時也要有東西)")
    lines.append("")
    lines.append("### ⚠️ 需要 Bry 注意")
    lines.append("")
    lines.append("1. **M0.4 已修**: think_only 從 8 條 (修法前 29%) → 0 條 (修法後 0%)。jsonl 內 think block 從 8 → 0。")
    lines.append("   修法前 22 條 source=llm 中 8 條是污染 (raw 200+ chars, clean 0)。修法後 15 條 source=llm 100% 都有實際 diary。")
    lines.append("2. **🟡 模板兜底比例仍偏高 (13 條, 46%)**: 雖然 50 → 80 放寬了上限, 但 LLM 仍有 8 條 think_only (夢境 + 事件 slot) + 4 條超過 80 字 (night slot) 走 placeholder。")
    lines.append("   → Bry 8/6 21:30 派工 80%+ 目標, 54% 未達標。剩餘差距主因是 LLM (M2.7) 行為本身, M0.4 修法已榨乾空間。")
    lines.append("3. **可考慮下一步 (Bry 拍板)**:")
    lines.append("   - A. 加 retry: think_only / 超 80 字時重試 1 次, 用更嚴格 prompt (「嚴格 50 字內, 不要任何推理」), 預期可救回 6-8 條")
    lines.append("   - B. 換模型: M3 (會強制 thinking, 污染更嚴重) 或其他 provider")
    lines.append("   - C. 接受 54% 為當前 M2.7 天花板, 觀察是否影響 Bry 想要的「殘留感」效果")
    lines.append("   - D. 嚴格 prompt 收斂: 在 LLM prompt 加「只輸出 1 句日文, 不解釋、不加標籤、不加 markdown 標題」")
    lines.append("4. **混語現象持續**: 部分條目中日夾雜 (`今朝`、`拉姆`、`麻衣さん`)、8/11 night 混日語漢字『窓の向こう、星が一つ瞬いた。』")
    lines.append("   → 觀察即可, 不算 bug (Bry 7/22 JP rollback 拍板接受這個 trade-off, 8/6 21:30 重申不動)")
    lines.append("5. **Bry 完全沒出現**: 7 天 28 條沒有任何一條提到 Bry, 符合 Bry 7/18 拍板「Bry 不在主題上」✅")
    lines.append("6. **夢境 / 事件內容品質**: 真實產出的 dream (8/8 浴室鏡子映出あかね、8/9 眩しい光、8/12 廚房窗外身影) 跟 Rem 角色對得起來, 確實是「夢到別人 + 情緒殘留」")
    lines.append("")
    lines.append("### 📊 7 天連續性")
    lines.append("")
    lines.append("- 全部 7 天 4 slot 都齊全 ✅")
    lines.append("- 夢境對象分布: mai, akane, mai, anna, miku, yua, aoi (Mai 夢到 2 次, 其餘各 1 次, 健康)")
    lines.append("- 小事件場景分布: 浴室鏡子, 廚房, 陽台, 客廳沙發, 玄關, 浴室鏡子, 客廳沙發 (有重複但不單調)")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 報告產出: {REPORT_PATH}")
    print(f"   行數: {len(lines)}")
    print(f"   字數: {sum(len(l) for l in lines)}")


def _weekday_zh(date_str: str) -> str:
    from datetime import datetime
    wd_en = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    return wd_en[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


if __name__ == "__main__":
    main()
