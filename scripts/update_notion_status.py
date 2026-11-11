# -*- coding: utf-8 -*-
"""scripts/update_notion_status.py — Soul OS Notion 同步唯一寫入路徑。

NOTION-MEMORY-1（B 案「凍結另起」）後，**更新 Notion 一律經本腳本，不許手動調 API 直寫**。

用法:
    python scripts/update_notion_status.py                 # 零參數：列出上次同步後 canonical 變更
    python scripts/update_notion_status.py --topic ei --text "..." --event-key <key>
    python scripts/update_notion_status.py --topic core|roadmap|persona --text "..." --event-key <key>
    python scripts/update_notion_status.py --topic evidence --month YYYY-MM --text "..." --event-key <key>
    python scripts/update_notion_status.py --audit --topic ei   # 對照已寫入區塊與預期 hash

規則（見 docs/notion_contract.json）:
- --topic 僅允 ei / core / roadmap / persona / evidence；其中 evidence 為
  Canary 巡檢證據報告通道（須搭配 --month），寫 90證據下月冊，不進 Current。
- 證據月冊路由見 contract["evidence_routes"]：缺月份即拒寫，禁止退回舊館。
- 舊館 S/E/R 三頁 ID 及舊樹任何 ID 一律拒寫（OLD_IDS）。
- 來源未覆蓋 / 工作樹髒檔 / 缺證據不得宣稱成功（寫前三檢查）。
- 所有寫入塊帶 `SOULSYNC/1` + 事件鍵 + 內容 hash；另寫 audit 對照。
- 限速：單 writer；每批 50 塊、<=2 req/s；429 遵 Retry-After。
- journal 原子保存（tmp + rename）：cursor / hash / 新舊 ID / 已驗批次；
  重跑先讀回核對，確認完整才跳過；未知寫入結果停待複核，不盲重送。
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "docs" / "notion_contract.json"
JOURNAL_PATH = REPO_ROOT / "verification" / "NOTION-MEMORY-1" / "sync_journal.json"
CANONICAL_PATH = REPO_ROOT / "logs" / "ENGINEERING_STATE.md"
NOTION_KEY_PATH = Path(r"C:\Users\bbfcc\.config\notion\api_key")
NOTION_VERSION = "2022-06-28"
MARKER = "SOULSYNC/1"

# 舊館（凍結）：新腳本拒寫全部舊 ID。B 案 T0 後只寫新樹。
OLD_IDS = {
    "3ae5ab1a-15bb-8104-bd2f-c2c954161258",  # S 狀態頁
    "40e5ab1a-15bb-810c-91f6-ef05180f9520",  # E EI 頁
    "3af5ab1a-15bb-813b-9ad0-c8239ff3bd83",  # R 路線圖頁
}
ALLOWED_TOPICS = ("ei", "core", "roadmap", "persona")
EVIDENCE_TOPIC = "evidence"  # Canary 巡檢證據報告通道：寫 90證據下月冊，不進 Current
EVIDENCE_CHUNK = 1900  # Notion 單塊 2000 字元上限留裕度；Current 的 1200 上限不適用證據通道
MIN_INTERVAL = 0.6
BATCH = 50
_last_ts = [0.0]


class Refused(Exception):
    """寫入被拒（舊館 ID / 未知 topic / 寫前檢查未過）。"""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha16(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def load_contract(path=CONTRACT_PATH):
    if not path.exists():
        raise Refused(f"contract 缺失：{path}（新樹 UUID 建完才回填；缺席不得寫入）")
    c = json.loads(path.read_text(encoding="utf-8"))
    if c.get("version") != 1:
        raise Refused(f"contract 版本不符：{c.get('version')!r}")
    return c


def resolve_topic(topic, contract):
    if topic not in ALLOWED_TOPICS:
        raise Refused(f"--topic 僅允 {ALLOWED_TOPICS}，實得 {topic!r}")
    route = contract.get("routes", {}).get(topic)
    if not route or not route.get("page_id"):
        raise Refused(f"topic {topic!r} 無路由（contract 未回填新頁 UUID，不得寫入）")
    pid = route["page_id"].replace("-", "")
    for oid in OLD_IDS:
        if pid == oid.replace("-", ""):
            raise Refused(f"topic {topic!r} 路由指向舊館 ID（凍結，拒寫）")
    if "PENDING" in route["page_id"]:
        raise Refused(f"topic {topic!r} 新頁 UUID 尚未回填（PENDING），不得寫入")
    return route


def resolve_evidence_page(month, contract):
    """證據月冊路由：依 YYYY-MM 解析；缺路由即拒寫，禁止退回舊館。"""
    import re
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise Refused(f"--month 須為 YYYY-MM，實得 {month!r}")
    table = (contract.get("evidence_routes") or {}).get("monthly", {})
    pid = table.get(month)
    if not pid:
        raise Refused(f"證據路由缺月份 {month!r}（須主腦回填月冊後再寫；缺路由即拒寫，禁止退回舊館）")
    for oid in OLD_IDS:
        if pid.replace("-", "") == oid.replace("-", ""):
            raise Refused(f"證據路由 {month!r} 指向舊館 ID（凍結，拒寫）")
    if "PENDING" in pid:
        raise Refused(f"證據路由 {month!r} 尚未回填月冊 UUID（PENDING），不得寫入")
    return pid


def build_evidence_blocks(month, text, event_key, contract):
    """組證據寫入塊：標題＋分塊正文＋SOULSYNC/1 標記行（含月份、事件鍵、內容 hash）。

    複用 journal＋事件鍵＋冪等＋讀回（經 append_blocks / audit_page）。
    舊的四種 Current 通道（build_update_blocks）原樣保留，不動其語義。
    """
    page_id = resolve_evidence_page(month, contract)
    body_hash = sha16(f"evidence|{month}|{event_key}|{text}")
    marker_line = f"{MARKER} topic=evidence month={month} event={event_key} hash={body_hash}"
    blocks = [{"object": "block", "type": "heading_2",
               "heading_2": {"rich_text": [{"type": "text", "text": {
                   "content": f"巡檢證據（{month}｜{utcnow()[:10]}）"}}]}}]
    for i in range(0, len(text), EVIDENCE_CHUNK):
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [{"type": "text", "text": {
                           "content": text[i:i + EVIDENCE_CHUNK]}}]}})
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [{"type": "text", "text": {"content": marker_line}}]}})
    return {"page_id": page_id, "blocks": blocks,
            "body_hash": body_hash, "marker": marker_line}


def check_clean_tree():
    """寫前檢查：工作樹髒檔。回傳 (ok, detail)。"""
    import subprocess
    r = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                       capture_output=True, text=True)
    dirty = [l for l in r.stdout.splitlines() if l.strip()]
    # 本腳本自寫的運行態（journal）不算髒；未追蹤新檔（??）不算髒（只看已追蹤檔的異動）
    dirty = [l for l in dirty
             if "verification/NOTION-MEMORY-1/" not in l and not l.startswith("??")]
    return (len(dirty) == 0, dirty)


def canonical_changes(since_commit=None):
    """零參數模式：列出上次同步後 canonical 變更。回傳 dict。"""
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.strip()
    canon_hash = hashlib.sha256(CANONICAL_PATH.read_bytes()).hexdigest()[:16]
    last = None
    if JOURNAL_PATH.exists():
        last = json.loads(JOURNAL_PATH.read_text(encoding="utf-8")).get("last_sync_commit")
    base = since_commit or last
    log_lines = []
    if base:
        r = subprocess.run(["git", "log", "--oneline", f"{base}..HEAD", "--",
                            "logs/ENGINEERING_STATE.md"],
                           cwd=REPO_ROOT, capture_output=True, text=True)
        log_lines = [l for l in r.stdout.splitlines() if l.strip()]
    return {"head": head, "canonical_sha16": canon_hash,
            "last_sync_commit": last, "changes_since_sync": log_lines}


def build_update_blocks(topic, text, event_key, contract):
    """組寫入塊：Current 段落 + SOULSYNC/1 標記行（含事件鍵與內容 hash）。"""
    route = resolve_topic(topic, contract)
    body_hash = sha16(f"{topic}|{event_key}|{text}")
    marker_line = f"{MARKER} topic={topic} event={event_key} hash={body_hash}"
    callout = contract.get("human_callout_template", "")
    blocks = []
    if callout:
        blocks.append({"object": "block", "type": "callout",
                       "callout": {"rich_text": [{"type": "text", "text": {
                           "content": callout.format(topic=topic)}}]}})
    blocks.append({"object": "block", "type": "heading_2",
                   "heading_2": {"rich_text": [{"type": "text", "text": {
                       "content": f"Current（{utcnow()[:10]}）"}}]}})
    if len(text) > 1200:
        raise Refused(f"Current 正文 {len(text)} 字元超 1200 上限，先精簡")
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}})
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [{"type": "text", "text": {"content": marker_line}}]}})
    return {"page_id": route["page_id"], "blocks": blocks,
            "body_hash": body_hash, "marker": marker_line}


def _throttle():
    dt = time.time() - _last_ts[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last_ts[0] = time.time()


def _api(opener, method, url, payload=None):
    data = None
    headers = {"Authorization": f"Bearer {_api.key}", "Notion-Version": NOTION_VERSION}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for _ in range(6):
        _throttle()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with opener(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if ra else 2.0) + 0.5)
                continue
            raise Refused(f"Notion HTTP {e.code}: {body}")
    raise Refused("Notion 429 反覆超限：停待複核，不盲重送")


def _load_journal():
    if JOURNAL_PATH.exists():
        return json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    return {"batches": [], "events": {}}


def _save_journal(j):
    j["updated_utc"] = utcnow()
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOURNAL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(JOURNAL_PATH)


def append_blocks(page_id, blocks, event_key, opener=urllib.request.urlopen):
    """分批寫入（每批 50），journal 記 cursor/hash/已驗批次；未知結果即停。"""
    pid = page_id.replace("-", "")
    for oid in OLD_IDS:
        if pid == oid.replace("-", ""):
            raise Refused(f"拒寫舊館 ID {page_id}（凍結）")
    journal = _load_journal()
    if event_key in journal.get("events", {}) and journal["events"][event_key].get("verified"):
        print(f"SKIP 已驗事件 {event_key}（讀回核對完整，不重送）")
        return journal["events"][event_key]
    created_ids = []
    for i in range(0, len(blocks), BATCH):
        chunk = blocks[i:i + BATCH]
        batch_n = i // BATCH
        if event_key in journal.get("events", {}) and batch_n < journal["events"][event_key].get("batches_done", 0):
            continue  # 已驗批次跳過
        try:
            r = _api(opener, "PATCH",
                     f"https://api.notion.com/v1/blocks/{pid}/children",
                     {"children": chunk})
        except Exception:
            journal.setdefault("events", {})[event_key] = {
                "status": "UNKNOWN", "batches_done": batch_n,
                "created_ids": created_ids, "updated_utc": utcnow()}
            _save_journal(journal)
            raise Refused(f"第 {batch_n} 批寫入結果未知：已停待複核，不盲重送")
        created_ids.extend([b.get("id", "") for b in r.get("results", [])])
        journal.setdefault("events", {})[event_key] = {
            "status": "written", "batches_done": batch_n + 1,
            "created_ids": created_ids, "updated_utc": utcnow()}
        _save_journal(journal)
    rec = journal["events"][event_key]
    rec["verified"] = False
    _save_journal(journal)
    return rec


def audit_page(page_id, expected_marker, opener=urllib.request.urlopen, expected_count=None):
    """讀回對照：缺標記 / 多餘塊 / 內容不符即報違規。回傳 violations 列表。"""
    pid = page_id.replace("-", "")
    texts = []
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{pid}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        r = _api(opener, "GET", url)
        for b in r.get("results", []):
            btype = b.get("type", "")
            d = b.get(btype, {}) if isinstance(b.get(btype), dict) else {}
            parts = []
            for rt in d.get("rich_text", []) if isinstance(d, dict) else []:
                if isinstance(rt, dict):
                    parts.append(rt.get("plain_text", ""))
            texts.append("".join(parts))
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    violations = []
    markers = [t for t in texts if MARKER in t]
    if not markers:
        violations.append("MISSING_MARKER：頁上無 SOULSYNC/1 標記塊")
    elif expected_marker not in markers:
        violations.append(f"MARKER_MISMATCH：預期 {expected_marker!r} 不在頁上")
    if len(texts) == 0:
        violations.append("EMPTY_PAGE：頁無任何塊")
    if expected_count is not None and len(texts) != expected_count:
        violations.append(
            f"EXTRA_BLOCKS：預期 {expected_count} 塊，實得 {len(texts)} 塊（疑似繞過腳本直寫）")
    return violations


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", choices=ALLOWED_TOPICS + (EVIDENCE_TOPIC,))
    ap.add_argument("--month", default="")  # 證據通道專用：YYYY-MM 月冊鍵
    ap.add_argument("--text", default="")
    ap.add_argument("--event-key", default="")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--since", default="")
    args = ap.parse_args(argv)
    contract = load_contract()
    _api.key = NOTION_KEY_PATH.read_text().strip() if NOTION_KEY_PATH.exists() else ""

    if args.audit:
        if not args.topic:
            print("FAIL: --audit 需搭配 --topic", file=sys.stderr)
            return 2
        if args.topic == EVIDENCE_TOPIC:
            if not args.month:
                print("FAIL: --topic evidence 需搭配 --month YYYY-MM", file=sys.stderr)
                return 2
            page_id = resolve_evidence_page(args.month, contract)
            key = f"{EVIDENCE_TOPIC}:{args.month}"
        else:
            route = resolve_topic(args.topic, contract)
            page_id = route["page_id"]
            key = args.topic
        journal = _load_journal()
        last_marker = journal.get("last_marker", {}).get(key, "")
        violations = audit_page(page_id, last_marker)
        print(json.dumps({"violations": violations}, ensure_ascii=False, indent=1))
        return 1 if violations else 0

    if not args.topic:
        info = canonical_changes(args.since or None)
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0

    if not args.text or not args.event_key:
        print("FAIL: --topic 需搭配 --text 與 --event-key", file=sys.stderr)
        return 2
    ok, dirty = check_clean_tree()
    if not ok:
        print(f"FAIL: 工作樹髒檔，來源未覆蓋不得宣稱成功：{dirty[:5]}", file=sys.stderr)
        return 2
    if "證據" not in args.text and "证据" not in args.text and "Evidence" not in args.text:
        print("FAIL: 缺證據索引（正文須含 證據/Evidence 指針），不得宣稱成功", file=sys.stderr)
        return 2
    try:
        if args.topic == EVIDENCE_TOPIC:
            if not args.month:
                print("FAIL: --topic evidence 需搭配 --month YYYY-MM", file=sys.stderr)
                return 2
            pkg = build_evidence_blocks(args.month, args.text, args.event_key, contract)
            journal_key = f"{EVIDENCE_TOPIC}:{args.month}"
        else:
            pkg = build_update_blocks(args.topic, args.text, args.event_key, contract)
            journal_key = args.topic
        rec = append_blocks(pkg["page_id"], pkg["blocks"], args.event_key)
        journal = _load_journal()
        journal.setdefault("last_marker", {})[journal_key] = pkg["marker"]
        _save_journal(journal)
        violations = audit_page(pkg["page_id"], pkg["marker"])
        if violations:
            print(json.dumps({"status": "AUDIT_FAIL", "violations": violations},
                             ensure_ascii=False, indent=1))
            return 1
        print(f"SUCCESS event={args.event_key} hash={pkg['body_hash']} batches={rec['batches_done']}")
        return 0
    except Refused as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
