"""
scripts/migrate_speaker_labels.py
KI-006 前置遷移腳本(2026-07-02)。

掃描現有 messages 表中 speaker 值為模糊值(如 "agent_id", "system", "agent", "bryan", 空字串)的 rows,
嘗試用 session_id pattern 回填正確 agent_id,無法回填者標記為 'unknown:legacy'。

設計:
- "bryan" speaker 保留(這是 user 訊息的正確標記,不要動)
- "agent_id" / "system" / "agent" speaker → 從 session_id pattern 推斷
- 空字串或無法推斷 → 標記為 "unknown:legacy"
- 群聊 (`group` session) 中模糊 speaker → 標記為 "unknown:legacy" (群聊本來就混 agent,
  無法用 session_id 推斷)

Usage:
    python scripts/migrate_speaker_labels.py --dry-run
    python scripts/migrate_speaker_labels.py
"""
import sys
import re
import sqlite3
import argparse
from pathlib import Path

REPO = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
DB = REPO / "data" / "memory.db"

# 模糊值清單 — 只有這些才會被嘗試遷移
AMBIGUOUS_SPEAKERS = {"agent_id", "system", "agent"}
LEGACY_MARK = "unknown:legacy"

# session_id → agent_id 對照(用 LIKE pattern 模糊匹配)
SESSION_AGENT_PATTERNS = [
    # 具名 agent(從 SOUL.md 已知)
    (r"miku", "agent_miku"),
    (r"mai", "agent_mai"),
    (r"aoi", "agent_aoi"),
    (r"anna", "agent_anna"),
    (r"mahiru", "agent_mahiru"),
    (r"ram", "agent_ram"),
    (r"akane", "agent_akane"),
    (r"ruka", "agent_ruka"),
    (r"rem", "agent_rem"),
    (r"yua", "agent_yua"),
]


def infer_agent_id_from_session(session_id: str) -> str | None:
    """從 session_id 推斷 agent_id。"""
    if not session_id:
        return None
    sid = session_id.lower()
    # 用 pattern 比對,找最長(最 specific)的對應
    matches = []
    for pattern, agent_id in SESSION_AGENT_PATTERNS:
        if re.search(pattern, sid):
            matches.append(agent_id)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # 多個 match → 用最長的 pattern 優先(具體勝過 generic)
        return max(matches, key=len)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只顯示遷移計畫,不改動 database"
    )
    args = parser.parse_args()

    if not DB.exists():
        print(f"❌ {DB} 不存在")
        sys.exit(1)

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    # 1. 統計現況 — 只看模糊 speaker('bryan' 跟 'user' 保留)
    placeholders = ",".join("?" * len(AMBIGUOUS_SPEAKERS))
    cur.execute(f"""
        SELECT speaker, COUNT(*) FROM messages
        WHERE speaker IN ({placeholders}) OR speaker IS NULL OR speaker = ''
        GROUP BY speaker
    """, list(AMBIGUOUS_SPEAKERS))
    ambiguous_before = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM messages")
    total_before = cur.fetchone()[0]

    print("=" * 70)
    print("  Speaker Label Migration Report (KI-006 前置)")
    print("=" * 70)
    print(f"\nDatabase: {DB}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print(f"\n總 messages: {total_before}")
    print(f"模糊 speaker rows(需遷移): {sum(ambiguous_before.values())} ({sum(ambiguous_before.values())/total_before*100:.1f}%)")
    for sp, cnt in ambiguous_before.items():
        print(f"  {sp!r}: {cnt}")

    # 2. 跑遷移計畫
    cur.execute(f"""
        SELECT id, session_id, speaker FROM messages
        WHERE speaker IN ({placeholders}) OR speaker IS NULL OR speaker = ''
    """, list(AMBIGUOUS_SPEAKERS))
    rows = cur.fetchall()

    plan = {"fixed": [], "unfixable": []}
    for msg_id, session_id, speaker in rows:
        new_speaker = infer_agent_id_from_session(session_id)
        if new_speaker:
            plan["fixed"].append((msg_id, session_id, speaker, new_speaker))
        else:
            plan["unfixable"].append((msg_id, session_id, speaker))

    print(f"\n--- 遷移計畫 ---")
    print(f"可修復(從 session_id 推斷): {len(plan['fixed'])}")
    print(f"無法修復(標記 legacy): {len(plan['unfixable'])}")

    # 3. 按 agent_id 統計修復
    fixed_by_agent = {}
    for msg_id, session_id, old_speaker, new_speaker in plan["fixed"]:
        fixed_by_agent[new_speaker] = fixed_by_agent.get(new_speaker, 0) + 1
    if fixed_by_agent:
        print(f"\n可修復的 agent 分布:")
        for aid, cnt in sorted(fixed_by_agent.items(), key=lambda x: -x[1]):
            print(f"  {aid}: {cnt}")

    # 4. 範例 sample
    print(f"\n--- 範例 5 個遷移樣本 ---")
    for msg_id, session_id, old_speaker, new_speaker in plan["fixed"][:5]:
        print(f"  msg_id={msg_id} | session={session_id!r} | {old_speaker!r} → {new_speaker!r}")
    print()
    if plan["unfixable"]:
        print(f"--- 範例 5 個無法修復的 ---")
        for msg_id, session_id, speaker in plan["unfixable"][:5]:
            print(f"  msg_id={msg_id} | session={session_id!r} | speaker={speaker!r} (legacy)")

    # 5. 執行遷移
    if not args.dry_run and (plan["fixed"] or plan["unfixable"]):
        print(f"\n--- 執行遷移 ---")
        for msg_id, session_id, old_speaker, new_speaker in plan["fixed"]:
            cur.execute(
                "UPDATE messages SET speaker = ? WHERE id = ?",
                (new_speaker, msg_id)
            )
        for msg_id, session_id, old_speaker in plan["unfixable"]:
            cur.execute(
                "UPDATE messages SET speaker = ? WHERE id = ?",
                (LEGACY_MARK, msg_id)
            )
        conn.commit()
        print(f"  ✓ 更新 {len(plan['fixed'])} 筆 → 明確 agent_id")
        print(f"  ✓ 標記 {len(plan['unfixable'])} 筆 → {LEGACY_MARK}")

        # 6. 跑驗證查詢
        print(f"\n--- After migration ---")
        cur.execute(f"""
            SELECT speaker, COUNT(*) FROM messages
            WHERE speaker IN ({placeholders}) OR speaker IS NULL OR speaker = ''
            GROUP BY speaker
        """, list(AMBIGUOUS_SPEAKERS))
        after_amb = dict(cur.fetchall())
        print(f"模糊 speaker rows after: {sum(after_amb.values())} (should be 0)")

        # 對 3 個代表性 agent 跑驗證查詢(任務書要求)
        for aid in ["agent_miku", "agent_aoi", "agent_rem"]:
            cur.execute("SELECT COUNT(*) FROM messages WHERE speaker = ?", (aid,))
            cnt = cur.fetchone()[0]
            print(f"  {aid}: {cnt} messages with speaker = {aid!r}")

    elif args.dry_run:
        print(f"\n⚠️  DRY-RUN 模式,未實際寫入")
        print(f"   重新跑不加 --dry-run 來執行遷移")

    conn.close()
    print(f"\n--- 結束 ---")


if __name__ == "__main__":
    main()
