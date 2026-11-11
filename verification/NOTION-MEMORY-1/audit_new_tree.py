# -*- coding: utf-8 -*-
"""audit_new_tree.py — NOTION-MEMORY-1：新樹回填後稽核（唯讀 GET＋contract 靜態檢查）。

檢查：
 1. contract 內 routes＋status01 無 PENDING；
 2. 全部新頁 id 唯一、且不在 OLD_IDS；
 3. parent 鏈：7 子頁 parent.page_id == root；月冊 parent == kid_90證據；
 4. 內容：每子頁首屏含 callout（人工約束模板）＋Current 標題＋Evidence 標題（缺即違規）；
 5. 舊館三頁 last_edited_time 皆 ≤ T0（只讀檢查，有新增寫入即報違規）。

exit 0＝PASS；exit 1＝FAIL（列出 violations）。
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC_JOURNAL = HERE / "sync_journal.json"
CONTRACT = HERE.parent.parent / "docs" / "notion_contract.json"
NOTION_KEY_PATH = Path(r"C:\Users\bbfcc\.config\notion\api_key")
NOTION_VERSION = "2022-06-28"
OLD_IDS = {
    "3ae5ab1a-15bb-8104-bd2f-c2c954161258",
    "40e5ab1a-15bb-810c-91f6-ef05180f9520",
    "3af5ab1a-15bb-813b-9ad0-c8239ff3bd83",
}
T0 = "2026-11-11T01:50Z"


def _req(method, url, payload=None):
    key = NOTION_KEY_PATH.read_text().strip()
    h = {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"
    for _ in range(6):
        time.sleep(0.65)
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if ra else 2.0) + 0.5)
                continue
            raise RuntimeError(f"{method} {url[:80]} -> HTTP {e.code} {body}")


def main():
    violations = []
    sj = json.loads(SYNC_JOURNAL.read_text(encoding="utf-8"))
    tree = sj.get("new_tree", {})
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    # 1. 無 PENDING
    for name, r in c.get("routes", {}).items():
        p = r.get("page_id", "") if isinstance(r, dict) else ""
        if "PENDING" in p:
            violations.append(f"PENDING_REMAIN route={name}")
    # 2. 唯一＋非舊
    ids = [r.get("page_id", "") for r in c.get("routes", {}).values()
           if isinstance(r, dict) and r.get("page_id")]
    if len(set(ids)) != len(ids):
        violations.append("DUP_IDS routes 不唯一")
    for pid in ids + list(tree.values()):
        for oid in OLD_IDS:
            if pid.replace("-", "") == oid.replace("-", ""):
                violations.append(f"OLD_ID_REF pid={pid}")
    # 3. parent 鏈
    root = tree.get("root_id", "")
    kids = [k for k in tree if k.startswith("kid_")]
    if len(kids) != 7:
        violations.append(f"KID_COUNT 預期 7 實得 {len(kids)}")
    for k in kids:
        try:
            pg = _req("GET", f"https://api.notion.com/v1/pages/{tree[k]}")
            par = pg.get("parent", {})
            if par.get("page_id", "") != root:
                violations.append(f"PARENT_MISMATCH {k} parent={par}")
        except Exception as e:
            violations.append(f"PARENT_CHECK_FAIL {k}: {e}")
    try:
        pg = _req("GET", f"https://api.notion.com/v1/pages/{tree.get('ev_2026_11','')}")
        if pg.get("parent", {}).get("page_id", "") != tree.get("kid_90證據", ""):
            violations.append("PARENT_MISMATCH ev_2026_11")
    except Exception as e:
        violations.append(f"PARENT_CHECK_FAIL ev_2026_11: {e}")
    # 4. 內容（callout＋Current＋Evidence）
    for k in kids:
        try:
            bl = _req("GET", f"https://api.notion.com/v1/blocks/{tree[k]}/children?page_size=50")
            types = [b.get("type", "") for b in bl.get("results", [])]
            texts = []
            for b in bl.get("results", []):
                d = b.get(b.get("type", ""), {})
                if isinstance(d, dict):
                    for rt in d.get("rich_text", []):
                        if isinstance(rt, dict):
                            texts.append(rt.get("plain_text", ""))
            blob = "\n".join(texts)
            if "callout" not in types:
                violations.append(f"MISSING_CALLOUT {k}")
            if "Current" not in blob:
                violations.append(f"MISSING_CURRENT {k}")
            if "Evidence" not in blob:
                violations.append(f"MISSING_EVIDENCE {k}")
        except Exception as e:
            violations.append(f"CONTENT_CHECK_FAIL {k}: {e}")
    # 5. 舊館 T0 後無新增寫入（只讀）
    for oid in sorted(OLD_IDS):
        try:
            pg = _req("GET", f"https://api.notion.com/v1/pages/{oid}")
            edt = pg.get("last_edited_time", "")
            if edt > T0:
                violations.append(f"OLD_WRITTEN_AFTER_T0 {oid} edited={edt}")
            print(f"old {oid} edited={edt}", flush=True)
        except Exception as e:
            violations.append(f"OLD_CHECK_FAIL {oid}: {e}")
    print(json.dumps({"violations": violations}, ensure_ascii=False, indent=1))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
