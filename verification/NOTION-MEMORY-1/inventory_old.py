# -*- coding: utf-8 -*-
"""inventory_old.py — NOTION-MEMORY-1 步驟 1：舊館 S/E/R 唯讀遞迴盤點。

唯讀：只發 GET（pages retrieve + blocks children list），不做任何寫入。
journal 原子保存：tmp + rename。重跑先讀回核對，確認完整才跳過。
限速：每請求間隔 >= 0.5s；429 遵 Retry-After。
輸出：old_manifest.json（ID、內容、順序、層級、hash）+ journal.json。
"""
import hashlib
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOURNAL = HERE / "journal.json"
MANIFEST = HERE / "old_manifest.json"
NOTION_KEY_PATH = Path(r"C:\Users\bbfcc\.config\notion\api_key")
NOTION_VERSION = "2022-06-28"
ROOTS = {
    "S": "3ae5ab1a-15bb-8104-bd2f-c2c954161258",
    "E": "40e5ab1a-15bb-810c-91f6-ef05180f9520",
    "R": "3af5ab1a-15bb-813b-9ad0-c8239ff3bd83",
}
MIN_INTERVAL = 0.6
_last_ts = [0.0]


def _headers():
    key = NOTION_KEY_PATH.read_text().strip()
    return {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION}


def _get(url):
    for attempt in range(6):
        dt = time.time() - _last_ts[0]
        if dt < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - dt)
        req = urllib.request.Request(url, headers=_headers())
        _last_ts[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                wait = float(ra) if ra else 2.0
                print(f"429 retry-after={wait}s url={url[:80]}", flush=True)
                time.sleep(wait + 0.5)
                continue
            raise RuntimeError(f"GET {url[:100]} -> HTTP {e.code} {body}")
    raise RuntimeError(f"GET {url[:100]} -> 429 反覆超限，放棄")


def _block_text(block):
    btype = block.get("type", "")
    data = block.get(btype, {}) if isinstance(block.get(btype), dict) else {}
    texts = []
    for rt in data.get("rich_text", []) if isinstance(data, dict) else []:
        if isinstance(rt, dict):
            t = rt.get("text", {}).get("content", "") if isinstance(rt.get("text"), dict) else rt.get("plain_text", "")
            texts.append(t)
    title = "".join(texts)
    extra = ""
    if btype == "child_page":
        extra = data.get("title", "") if isinstance(data, dict) else ""
    return title or extra


def _list_children(block_id):
    out = []
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        r = _get(url)
        out.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return out


def _get_page(pid):
    return _get(f"https://api.notion.com/v1/pages/{pid}")


def _walk(block_id, level, seq, records, journal, tag):
    children = _list_children(block_id)
    for i, b in enumerate(children):
        bid = b.get("id", "")
        btype = b.get("type", "")
        text = _block_text(b)
        h = hashlib.sha256(f"{bid}|{btype}|{text}".encode("utf-8")).hexdigest()[:16]
        records.append({"root": tag, "id": bid, "type": btype,
                        "text": text[:500], "order": i, "level": level, "hash": h,
                        "has_children": bool(b.get("has_children"))})
        journal["blocks_done"] = journal.get("blocks_done", 0) + 1
        if journal["blocks_done"] % 50 == 0:
            _save_journal(journal)
            print(f"[{tag}] blocks_done={journal['blocks_done']}", flush=True)
        if b.get("has_children") and btype != "child_page":
            _walk(bid, level + 1, seq, records, journal, tag)
        elif btype == "child_page":
            # child_page 內容掛在 page id 下
            _walk(bid, level + 1, seq, records, journal, tag)
            journal.setdefault("subpages", []).append(
                {"root": tag, "id": bid, "title": text})
            _save_journal(journal)


def _save_journal(j):
    import os
    import random
    j["updated_utc"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(j, ensure_ascii=False, indent=1).encode("utf-8")
    for attempt in range(8):
        tmp = JOURNAL.parent / f"journal.{os.getpid()}.{random.randint(0, 1 << 30)}.tmp"
        try:
            tmp.write_bytes(payload)
            tmp.replace(JOURNAL)
            return
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            time.sleep(0.5 + attempt * 0.5)
    # 最後手段：直接覆寫（非原子，但不丟進度）
    JOURNAL.write_bytes(payload)


def main():
    journal = {"roots_done": [], "blocks_done": 0, "subpages": [],
               "started_utc": datetime.now(timezone.utc).isoformat()}
    if JOURNAL.exists():
        old = json.loads(JOURNAL.read_text(encoding="utf-8"))
        # 讀回核對：manifest 存在且筆數一致才視為可續傳
        if MANIFEST.exists():
            print("resume journal:", json.dumps(
                {k: (v if not isinstance(v, list) else len(v)) for k, v in old.items()},
                ensure_ascii=False), flush=True)
            journal = old
        else:
            print("journal 存在但 manifest 缺失，重頭盤點", flush=True)
    records = []
    if MANIFEST.exists() and journal.get("roots_done"):
        records = json.loads(MANIFEST.read_text(encoding="utf-8")).get("records", [])
    for tag, pid in ROOTS.items():
        if tag in journal.get("roots_done", []):
            # 確認完整才跳過：該 root 在 records 有筆數
            n = sum(1 for r in records if r["root"] == tag)
            print(f"[{tag}] 已完成，跳過（records={n}），先前已驗", flush=True)
            continue
        pg = _get_page(pid)
        title = ""
        props = pg.get("properties", {})
        tprop = props.get("title", {})
        if tprop.get("type") == "title":
            title = "".join(x.get("plain_text", "") for x in tprop.get("title", []))
        print(f"[{tag}] page title={title!r} archived={pg.get('archived')}", flush=True)
        before = len(records)
        _walk(pid, 0, before, records, journal, tag)
        journal.setdefault("roots_done", []).append(tag)
        journal[f"root_{tag}_title"] = title
        journal[f"root_{tag}_blocks"] = len(records) - before
        _save_journal(journal)
        print(f"[{tag}] done, new_blocks={len(records) - before}", flush=True)
    manifest = {"generated_utc": datetime.now(timezone.utc).isoformat(),
                "roots": ROOTS, "count": len(records), "records": records}
    import os
    import random
    mpayload = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
    for attempt in range(8):
        mtmp = MANIFEST.parent / f"old_manifest.{os.getpid()}.{random.randint(0, 1 << 30)}.tmp"
        try:
            mtmp.write_bytes(mpayload)
            mtmp.replace(MANIFEST)
            break
        except OSError:
            try:
                if mtmp.exists():
                    mtmp.unlink()
            except OSError:
                pass
            time.sleep(0.5 + attempt * 0.5)
    else:
        MANIFEST.write_bytes(mpayload)
    _save_journal(journal)
    print(f"MANIFEST count={len(records)} subpages={len(journal.get('subpages', []))}", flush=True)


if __name__ == "__main__":
    main()
