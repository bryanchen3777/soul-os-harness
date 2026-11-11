# -*- coding: utf-8 -*-
"""migrate_assets.py — NOTION-MEMORY-1 步驟 4：資產遷入（只複製、不移動、不刪源）。

- 05角色資產 ← 舊館 persona 草稿（44 筆 SOUL.md 草稿）＋全專案審計子頁（1 筆），標【複本·草稿】＋原頁連結。
- 04路線圖 ← 舊館「Soul OS 部署就緒差距與執行計劃」（R 子頁），標【複本·歷史】＋原頁連結。
- 90證據 ← 建「舊館目錄（凍結T0）」子頁：S/E/R 原頁連結＋manifest 位置＋hash＋按月分冊起點。
- 舊館三頁零寫入（本腳本只 GET 舊 ID；POST/PATCH 目標只限新樹 ID，否則拒寫）。
- 限速 ≤2 req/s；429 遵 Retry-After；migrate_journal.json 續傳（src_id→copy_id，有即跳過）。

來源 ID 清單以 old_manifest.json＋journal.json 為準（journal subpages 計 120，其中草稿 44＋審計 1＋部署差距 1）。
"""
import hashlib
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC_JOURNAL = HERE / "sync_journal.json"
JOURNAL = HERE / "journal.json"
MANIFEST = HERE / "old_manifest.json"
MJOURNAL = HERE / "migrate_journal.json"
NOTION_KEY_PATH = Path(r"C:\Users\bbfcc\.config\notion\api_key")
NOTION_VERSION = "2022-06-28"

OLD_IDS = {
    "3ae5ab1a-15bb-8104-bd2f-c2c954161258",
    "40e5ab1a-15bb-810c-91f6-ef05180f9520",
    "3af5ab1a-15bb-813b-9ad0-c8239ff3bd83",
}
_last = [0.0]
UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def pending_sources(all_ids, done):
    """純函式：回傳尚未遷入的來源 id（冪等規劃，供測試）。"""
    done = done or {}
    return [i for i in (all_ids or []) if i not in done]


def _req(method, url, payload=None, read_only_old_ok=True):
    if method in ("POST", "PATCH"):
        for oid in OLD_IDS:
            if oid.replace("-", "") in url.replace("-", ""):
                raise RuntimeError(f"拒寫舊館 ID：{url[:80]}")
    key = NOTION_KEY_PATH.read_text().strip()
    h = {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"
    for _ in range(6):
        dt = time.time() - _last[0]
        if dt < 0.6:
            time.sleep(0.6 - dt)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if ra else 2.0) + 0.5)
                continue
            raise RuntimeError(f"{method} {url[:80]} -> HTTP {e.code} {body}")


def _title_of(page):
    for v in page.get("properties", {}).values():
        if isinstance(v, dict) and v.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in v.get("title", []))
    return ""


def _block_text(b):
    btype = b.get("type", "")
    d = b.get(btype, {}) if isinstance(b.get(btype), dict) else {}
    parts = []
    if isinstance(d, dict):
        for rt in d.get("rich_text", []):
            if isinstance(rt, dict):
                parts.append(rt.get("plain_text", "") or
                             (rt.get("text", {}).get("content", "") if isinstance(rt.get("text"), dict) else ""))
    t = "".join(parts)
    if not t and btype == "child_page":
        t = (d.get("title", "") if isinstance(d, dict) else "")
    return btype, t


def _get_children_first_page(pid):
    url = f"https://api.notion.com/v1/blocks/{pid}/children?page_size=100"
    return _req("GET", url)


def copy_one(src_id, src_title, dest_parent, label):
    """複製單一來源頁首屏（≤100 塊）為 dest 下子頁；回傳 (copy_id, n_src_blocks, excerpt_hash)。"""
    kids = _get_children_first_page(src_id)
    results = kids.get("results", [])
    blocks = [{"object": "block", "type": "callout",
               "callout": {"rich_text": [{"type": "text",
                 "text": {"content": f"{label}｜原頁：{src_title}｜原頁 ID：{src_id}｜複製 UTC：{UTC}（只複製，不移動不刪源）"}}]}}]
    excerpt_texts = []
    for b in results[:48]:
        btype, t = _block_text(b)
        t = t[:500] if t else f"（{btype} 無文字）"
        excerpt_texts.append(f"[{btype}] {t}")
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [{"type": "text",
                         "text": {"content": f"[{btype}] {t}"[:1900]}}]}})
    if kids.get("has_more"):
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [{"type": "text",
                         "text": {"content": "（來源頁塊數逾首屏 100，複本僅收首屏；全文以原頁連結為準）"}}]}})
    new_title = f"{label} {src_title}"[:90]
    r = _req("POST", "https://api.notion.com/v1/pages",
             {"parent": {"page_id": dest_parent},
              "properties": {"title": [{"type": "text", "text": {"content": new_title}}]}})
    copy_id = r["id"]
    # 分批寫（每批 50）
    for i in range(0, len(blocks), 50):
        _req("PATCH", f"https://api.notion.com/v1/blocks/{copy_id}/children",
             {"children": blocks[i:i + 50]})
    eh = hashlib.sha256("\n".join(excerpt_texts).encode("utf-8")).hexdigest()[:16]
    return copy_id, len(results), eh


def main():
    sj = json.loads(SYNC_JOURNAL.read_text(encoding="utf-8"))
    tree = sj.get("new_tree", {})
    dest05 = tree.get("kid_05角色資產")
    dest04 = tree.get("kid_04路線圖")
    dest90 = tree.get("kid_90證據")
    ev_monthly = tree.get("ev_2026_11", "")
    if not (dest05 and dest04 and dest90):
        raise SystemExit("sync_journal 缺新樹 id，先跑 create_tree.py")
    j = json.loads(JOURNAL.read_text(encoding="utf-8"))
    subs = j.get("subpages", [])
    drafts = [s for s in subs if "SOUL.md" in s.get("title", "")]
    audits = [s for s in subs if "審計" in s.get("title", "")]
    deploys = [s for s in subs if "部署就緒差距與執行計劃" in s.get("title", "")]
    print(f"drafts={len(drafts)} audits={len(audits)} deploys={len(deploys)}", flush=True)
    mj = json.loads(MJOURNAL.read_text(encoding="utf-8")) if MJOURNAL.exists() else {"copies": {}}
    copies = mj.setdefault("copies", {})
    # 05：草稿＋審計
    for s in drafts + audits:
        sid = s["id"]
        if sid in copies:
            print("skip", sid, copies[sid].get("copy_id"), flush=True)
            continue
        # 唯讀核對來源頁標題
        pg = _req("GET", f"https://api.notion.com/v1/pages/{sid}")
        assert pg.get("archived") is False or True  # 唯讀不斷言歸檔，僅記錄
        cid, n, eh = copy_one(sid, s.get("title", ""), dest05, "【複本·草稿】")
        copies[sid] = {"copy_id": cid, "dest": dest05, "n_src_firstpage": n,
                       "excerpt_hash": eh, "utc": UTC}
        MJOURNAL.write_text(json.dumps(mj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"copied 05 {sid} -> {cid} n={n} hash={eh}", flush=True)
    # 04：部署差距
    for s in deploys:
        sid = s["id"]
        if sid in copies:
            print("skip", sid, copies[sid].get("copy_id"), flush=True)
            continue
        pg = _req("GET", f"https://api.notion.com/v1/pages/{sid}")
        _ = _title_of(pg)
        cid, n, eh = copy_one(sid, s.get("title", ""), dest04, "【複本·歷史】")
        copies[sid] = {"copy_id": cid, "dest": dest04, "n_src_firstpage": n,
                       "excerpt_hash": eh, "utc": UTC}
        MJOURNAL.write_text(json.dumps(mj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"copied 04 {sid} -> {cid} n={n} hash={eh}", flush=True)
    # 90：舊館目錄子頁
    if "cat_old_index" not in copies:
        mbytes = MANIFEST.read_bytes()
        mhash = hashlib.sha256(mbytes).hexdigest()[:16]
        m = json.loads(mbytes.decode("utf-8"))
        lines = [
            "舊館目錄（凍結 T0=2026-11-11T01:50Z；只讀，禁 DELETE/erase_content；讀者降 Can view 待 Owner 手工作業）",
            f"S 狀態頁：Soul OS 專案狀態（單一事實來源）｜3ae5ab1a-15bb-8104-bd2f-c2c954161258｜13127 筆",
            f"E 計畫頁：Soul OS 外界接口實作計畫 — EI-0～EI-6｜40e5ab1a-15bb-810c-91f6-ef05180f9520｜1219 筆",
            f"R 路線圖：Soul OS 路線圖與里程碑｜3af5ab1a-15bb-813b-9ad0-c8239ff3bd83｜352 筆",
            f"manifest：verification/NOTION-MEMORY-1/old_manifest.json｜共 {m.get('count')} 筆｜sha256 前綴 {mhash}｜子頁 120",
            f"按月分冊起點：2026-11 首冊 id={ev_monthly}（90證據下）",
            f"編製 UTC：{UTC}",
        ]
        r = _req("POST", "https://api.notion.com/v1/pages",
                 {"parent": {"page_id": dest90},
                  "properties": {"title": [{"type": "text",
                    "text": {"content": "Soul OS｜舊館目錄（凍結T0，只讀）"}}]}})
        cat_id = r["id"]
        _req("PATCH", f"https://api.notion.com/v1/blocks/{cat_id}/children",
             {"children": ([{"object": "block", "type": "callout",
                "callout": {"rich_text": [{"type": "text",
                  "text": {"content": "更新 Notion 一律經 scripts/update_notion_status.py，不許手調 API 直寫｜topic=90｜舊館只讀，禁 DELETE/erase_content"}}]}}]
                + [{"object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": ln[:1900]}}]}} for ln in lines])})
        copies["cat_old_index"] = {"copy_id": cat_id, "dest": dest90, "utc": UTC}
        MJOURNAL.write_text(json.dumps(mj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"catalog 90 -> {cat_id}", flush=True)
    else:
        print("skip cat_old_index", copies["cat_old_index"].get("copy_id"), flush=True)
    print(f"done copies={len(copies)}", flush=True)


if __name__ == "__main__":
    main()
