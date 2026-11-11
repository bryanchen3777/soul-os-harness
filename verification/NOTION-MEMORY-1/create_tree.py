# -*- coding: utf-8 -*-
"""create_tree.py — NOTION-MEMORY-1 步驟 2：建新樹（根頁＋7 子頁），回填 contract。
寫入限速 ≤2 req/s；429 遵 Retry-After。ID 寫入 journal；contract 回填新 UUID。
"""
import hashlib
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOURNAL = HERE / "sync_journal.json"
CONTRACT = HERE.parent.parent / "docs" / "notion_contract.json"
NOTION_KEY_PATH = Path(r"C:\Users\bbfcc\.config\notion\api_key")
NOTION_VERSION = "2022-06-28"
_last = [0.0]


def _req(method, url, payload=None):
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


def title_prop(t):
    return {"title": [{"type": "text", "text": {"content": t}}]}


def block_callout(topic):
    tpl = "更新 Notion 一律經 scripts/update_notion_status.py，不許手調 API 直寫｜topic=%s｜寫入位置=本頁 Current 段｜欄位=正文1200字內含下一步/阻塞/來源UTC/證據索引｜反例：首頁底部追加日期流水帳是錯的，須經腳本事件鍵去重" % topic
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": [{"type": "text", "text": {"content": tpl}}]}}


def block_h2(t):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": t}}]}}


def block_p(t):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": t}}]}}


UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
H0 = "19e6fd1"

CURRENT_00 = (
    "【樞紐索引】新規劃→01現況；EI→02EI；核心→03核心；路線圖→04路線圖；角色資產→05；"
    "證據→90證據（按月分冊）。｜規劃：B案凍結另起，舊館唯讀封存｜實作：新樹7頁＋腳本唯一寫入｜"
    "驗收：tests/infra/test_notion_memory.py 9項｜部署：本頁以下只經腳本寫入｜下一步：資產遷入＋90證據建冊｜"
    "阻塞：待舊館盤點manifest｜來源：logs/ENGINEERING_STATE.md@" + H0 + "｜UTC：" + UTC)


def main():
    j = json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {"events": {}}
    tree = j.setdefault("new_tree", {})
    if "root_id" not in tree:
        r = _req("POST", "https://api.notion.com/v1/pages",
                 {"parent": {"type": "workspace", "workspace": True},
                  "properties": title_prop("Soul OS｜新館（B案，2026-11-11起唯一寫入樹）")})
        tree["root_id"] = r["id"]
        print("root", r["id"], flush=True)
    kids = [
        ("00入口", CURRENT_00),
        ("01現況", "待 canonical §1 CURRENT STATE 重建。來源：logs/ENGINEERING_STATE.md@" + H0 + "｜UTC：" + UTC),
        ("02EI", "待 canonical EI-2B-PROGRAM-3 ACCEPTED(§~10100)＋PENDING-1-RATIFIED-1110(§~10249)重建，不照抄舊館完成宣稱。來源同上｜UTC：" + UTC),
        ("03核心", "待 canonical 生命週期/昇華/投影節重建。來源同上｜UTC：" + UTC),
        ("04路線圖", "待 canonical 路線圖節＋舊館「部署就緒差距與執行計劃」複本（標歷史）重建。來源同上｜UTC：" + UTC),
        ("05角色資產", "persona草稿／審計子頁複本遷入地（標草稿＋原頁連結）。來源同上｜UTC：" + UTC),
        ("90證據", "證據分冊索引（按月建頁，每100事件分冊）。先建2026-11分冊。來源同上｜UTC：" + UTC),
    ]
    for name, current in kids:
        key = "kid_" + name
        if key in tree:
            print("skip", name, tree[key], flush=True)
            continue
        r = _req("POST", "https://api.notion.com/v1/pages",
                 {"parent": {"page_id": tree["root_id"]},
                  "properties": title_prop("Soul OS｜" + name)})
        tree[key] = r["id"]
        print("kid", name, r["id"], flush=True)
        _req("PATCH", f"https://api.notion.com/v1/blocks/{r['id']}/children",
             {"children": [block_callout(name), block_h2("Current"), block_p(current),
                            block_h2("Evidence"), block_p("（待 90 證據建冊後定點索引）")]})
    # 90 下按月建頁：2026-11 首冊
    if "ev_2026_11" not in tree:
        r = _req("POST", "https://api.notion.com/v1/pages",
                 {"parent": {"page_id": tree["kid_90證據"]},
                  "properties": title_prop("Soul OS｜90證據・2026-11（分冊1）")})
        tree["ev_2026_11"] = r["id"]
        print("ev", r["id"], flush=True)
        _req("PATCH", f"https://api.notion.com/v1/blocks/{r['id']}/children",
             {"children": [block_h2("Evidence・2026-11"), block_p("首冊。舊館目錄＋快照hash待盤點manifest完成後回填。")]})
    JOURNAL.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    # 回填 contract
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    mp = {"ei": "kid_02EI", "core": "kid_03核心", "roadmap": "kid_04路線圖", "persona": "kid_05角色資產"}
    for topic, k in mp.items():
        c["routes"][topic]["page_id"] = tree[k]
    c["routes"]["status01"] = {"page_id": tree["kid_01現況"], "page_name": "01現況",
                               "block_template": "callout→Current標題→正文→Evidence標題→索引"}
    c["new_tree"] = {k: v for k, v in tree.items()}
    c["t0_utc"] = UTC
    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    print("contract backfilled", flush=True)
    # freeze 記錄
    (HERE / "freeze.json").write_text(json.dumps(
        {"t0_utc": UTC, "h0_commit": H0,
         "note": "T0前留舊館、之後只寫新樹。新腳本拒寫全部舊ID。舊頁Can view由人工作業收權限。"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("T0=", UTC, "H0=", H0, flush=True)


if __name__ == "__main__":
    main()
