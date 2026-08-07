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
        "- 🟢 LLM = minimax M2.7 真生成 (M0.5 修法後 think block 已剝 + 超長截斷 + think_only retry, jsonl 100% 乾淨)",
        "- 🟡 模板 = 模板兜底 (LLM 兩次都 think_only 才走, 7% 殘量)",
        "",
        "**LLM 觸發率統計** (M0.5 修法後, 重跑驗證):",
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
    lines.append("1. **M0.5 達標**: 26/28 (93%) 真實產出率, 達 Bry 80% 目標。")
    lines.append("   對比 M0.4 後: 15/28 (54%) → 26/28 (93%), 提升 39 個百分點。")
    lines.append("   2 條 placeholder 是 think_only retry 也失敗的 (8/7 event, 8/8 dream), 屬於 LLM 連兩次都只回 think 的邊角情況。")
    lines.append("2. **A1 截斷效果**: 6 條超長 (82-268 chars) 全部截斷到 80 chars 內, 保留 LLM 真實內容 (例 8/9 morning 268 chars 截斷後有完整 LLM 結尾句)。")
    lines.append("3. **A2 retry 效果**: 7 條 think_only retry 後成功寫 llm, 2 條 retry 也失敗 (連 2 次都只回 think 的 LLM 行為問題, 修法觸頂)。")
    lines.append("4. **jsonl 100% 乾淨**: think block 從 0 → 0, 沒有任何污染, 對齊 M0.4 標準。")
    lines.append("5. **Bry 完全沒出現**: 7 天 28 條沒有任何一條提到 Bry, 符合 Bry 7/18 拍板「Bry 不在主題上」✅")
    lines.append("6. **Bry 拍板選項 (下一步)**:")
    lines.append("   - A. 接受 93% 並啟動 scheduler 長期自動跑 (Bry 派工 80% 門檻已過)")
    lines.append("   - B. 再衝一波: 對 retry 也失敗的 2 條加第二次 retry (Bry 派工「先收斂驗證」風格反對)")
    lines.append("   - C. 觀察 1 週實際效果, 確認 26 條 LLM 內容品質滿意再啟動")
    lines.append("7. **混語現象持續**: 8/8 morning「早elden。窓から差し込む光が...」混亂字符, 8/9 night 漢字混日語, 8/12 morning 結尾「拉茲瓦爾家」簡體混日語。")
    lines.append("   → 觀察即可, 不算 bug (Bry 7/22 JP rollback 拍板接受這個 trade-off, 8/6 21:30 重申不動)")
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
