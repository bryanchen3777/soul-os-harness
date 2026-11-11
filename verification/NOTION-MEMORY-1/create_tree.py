# -*- coding: utf-8 -*-
"""create_tree.py — NOTION-MEMORY-1 步驟 2：建新樹（Owner 已建根頁下 7 子頁），回填 contract。

B 案：Owner 已在 Notion UI 建頂層根頁並分享給整合：
  root_id = 4155ab1a-15bb-80a4-8cb2-d228aa8a4047（parent=workspace，已驗證可讀）。
本腳本不再 POST parent.workspace（舊阻塞點），只做：
  1. 根頁改名（PATCH title →「Soul OS｜記憶庫」，單次改名，可逆，不動其他屬性）；
  2. 在根頁下建 7 子頁（冪等：journal 有 kid_* 即跳過）＋各頁頂 callout＋Current 初版；
  3. 90 下按月建 2026-11 首冊（冪等）；
  4. contract 回填新頁真實 UUID（取代全部 PENDING-CREATION；T0 不動）。

寫入限速 ≤2 req/s；429 遵 Retry-After。ID 寫入 sync_journal.json；contract 回填新 UUID。
T0/H0 凍結在 freeze.json，本腳本不改 freeze.json。
"""
import hashlib
import json
import subprocess
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

EXISTING_ROOT_ID = "4155ab1a-15bb-80a4-8cb2-d228aa8a4047"
ROOT_TITLE = "Soul OS｜記憶庫"
OLD_IDS = {
    "3ae5ab1a-15bb-8104-bd2f-c2c954161258",
    "40e5ab1a-15bb-810c-91f6-ef05180f9520",
    "3af5ab1a-15bb-813b-9ad0-c8239ff3bd83",
}
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


def _head_short():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=HERE.parent.parent, capture_output=True, text=True,
                           timeout=20)
        s = r.stdout.strip()
        return s if s else "unknown"
    except Exception:
        return "unknown"


def title_prop(t):
    return {"title": [{"type": "text", "text": {"content": t}}]}


def block_callout(topic):
    tpl = ("更新 Notion 一律經 scripts/update_notion_status.py，不許手調 API 直寫｜"
           f"topic={topic}｜寫入位置=本頁 Current 段｜欄位=正文1200字內含下一步/阻塞/來源UTC/證據索引｜"
           "反例：首頁底部追加日期流水帳是錯的，須經腳本事件鍵去重")
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": [{"type": "text", "text": {"content": tpl}}]}}


def block_h2(t):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": t}}]}}


def block_p(t):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": t}}]}}


UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
HEAD = _head_short()
SRC = f"logs/ENGINEERING_STATE.md@{HEAD}"

# --- Current 初版（canonical 重建，每段 ≤1200 字；來源 commit＋UTC） ---
KIDS_SPEC = [
    ("00入口樞紐",
     "【樞紐索引】新規劃→01現況；EI→02EI；核心→03核心；路線圖→04路線圖；角色資產→05；"
     "證據→90證據（按月分冊）。｜規劃：B案凍結另起，舊館 S/E/R 唯讀封存（T0=2026-11-11T01:50Z），"
     "之後只寫新樹｜實作：新樹7頁＋腳本唯一寫入｜驗收：tests/infra/test_notion_memory.py｜"
     "部署：本頁以下只經腳本寫入｜下一步：資產遷入＋90證據建冊｜阻塞：舊館讀者降 Can view 待 Owner 手工作業｜"
     f"來源：{SRC}｜UTC：{UTC}"),
    ("01現況",
     "【現況】EI-2 整步 ACCEPTED 並合入（EI-2B-PROGRAM-3 2026-11-08；EI-2B e635 2026-11-09 合入 main；"
     "EI-1 追認 CLOSED 2026-11-09）；S3-W1/S4-W2/S5-W3-1/S6-W4 逐票 ACCEPTED（至 2026-11-10）；"
     "KEY-1＋DOC-1＋GAP-DECISION-1 閉環；G1–G5 仍 BLOCKED 待 Owner 圈選 A/B。｜"
     "規劃：S7 開工（傳輸綁定）｜實作：src/external_gateway＋tests 逐票合入｜驗收：全庫 5 紅皆 G1–G5 預期紅，零新增｜"
     "部署：DEPLOY-1109 生產重啟已生效（:8000 health 200）｜下一步：S7＋台帳夜槽改紐約 22:00 另票｜"
     f"阻塞：無（舊館降權待手工）｜來源：{SRC}｜UTC：{UTC}"),
    ("02EI",
     "【EI】EI-2B-PROGRAM-3 ACCEPTED（2026-11-08，主腦複核：雙守衛 74 綠，interfaces 246 綠，TTL 接管已除，"
     "deny 零副作用；全庫 5 紅全歸因非本票）。EI-2 整步完成（EI-2A＋EI-2B 皆 ACCEPTED）。｜"
     "MINIMAX-PRESET-1110（Owner 指名 ollama 掛 minimax-m3.1-flash，2026-11-10，五魂 5/5 全 200，"
     "routes 5→10，digest 不變，deepseek 直連仍未驗證）。｜PENDING-1-RATIFIED-1110：2026-11-02 cohort 追認 "
     "962c9fce…（belief／agent_ruka）入帳，台帳只增不減。｜下一步：S7 傳輸綁定｜"
     f"阻塞：無｜來源：{SRC}｜UTC：{UTC}"),
    ("03核心",
     "【核心】生命週期評估＝disk→disk 函式 evaluate_lifecycle_all（CLI 沙盒快跑離線驗收＝等價重現）；"
     "轉變類型純度：value/belief/trait/essence 轉變數恆等於 0（唯一例外＝Owner 核准台帳內 Legacy Value 退役，"
     "三態 PASS/FAIL/PENDING_COHORT）。｜昇華防火牆維持（EH-2.1 R1：world:news* 等外部情報不得沉澱為信念）；"
     "衰減門檻凍結，禁偽造邊、禁手動消紅。｜下一步：維持夜槽 Canary 非阻塞巡檢｜"
     f"阻塞：無｜來源：{SRC}｜UTC：{UTC}"),
    ("04路線圖",
     "【路線圖】S7 傳輸綁定可開工；台帳夜槽改紐約當地 22:00（LEDGER-SLOT-TZ-1 未合入）；"
     "G1–G5 選 A 誠實收斂或選 B 另案升級（待 Owner 圈選，B 需重開工單）。｜"
     "舊館「Soul OS 部署就緒差距與執行計劃」複本（標歷史＋原頁連結）遷入本頁子頁，內容以複本為準、舊館為源。｜"
     f"下一步：資產遷入 04 子頁回填｜阻塞：無｜來源：{SRC}｜UTC：{UTC}"),
    ("05角色資產",
     "【角色資產】首批五魂凍結：rem／mai／ruka／akane／mahiru（S2-RULING-1109；埠 :18765；LAN 明文 HTTP；"
     "首批非串流；preset 雙預設 deepseek-v4.1-flash＋minimax-m3.1-flash，2026-11-10 五魂×雙預設全 200）。｜"
     "舊館 persona 草稿（44 筆 SOUL.md 草稿）＋全專案審計子頁複本（標草稿＋原頁連結）遷入本頁子頁。｜"
     f"下一步：遷入 05 子頁回填｜阻塞：無｜來源：{SRC}｜UTC：{UTC}"),
    ("90證據",
     "【證據】舊館目錄（凍結 T0=2026-11-11T01:50Z）：S=13127／E=1219／R=352，共 14698 筆，子頁 120；"
     "manifest 見 verification/NOTION-MEMORY-1/old_manifest.json（抽樣 1470 筆，回取覆核實質不符 0）。｜"
     "按月分冊：先建 2026-11 首冊，每 100 事件分冊。｜"
     f"下一步：90 下建舊館目錄子頁＋月冊回填｜阻塞：無｜來源：{SRC}｜UTC：{UTC}"),
]

EV_PAGE_TITLE = "Soul OS｜90證據・2026-11（分冊1）"


def plan_missing(existing_keys):
    """純函式：回傳尚缺的 kid key（冪等規劃，供測試）。"""
    want = [f"kid_{name}" for name, _ in KIDS_SPEC]
    return [k for k in want if k not in (existing_keys or {})]


def backfill_contract_data(contract, tree):
    """純函式：contract 回填一致性（供測試＋主流程共用）。

    - routes ei/core/roadmap/persona＋status01 全部填真實 UUID，不得殘留 PENDING；
    - 全部 id 唯一且不在 OLD_IDS；
    - 保留既有 t0_utc（T0 不動），只寫 last_build_utc。
    """
    mp = {"ei": "kid_02EI", "core": "kid_03核心",
          "roadmap": "kid_04路線圖", "persona": "kid_05角色資產"}
    c = json.loads(json.dumps(contract))  # deep copy
    for topic, k in mp.items():
        if k not in tree:
            raise ValueError(f"tree 缺 {k}，不得回填（防 PENDING 殘留）")
        c.setdefault("routes", {}).setdefault(topic, {})["page_id"] = tree[k]
    c["routes"]["status01"] = {
        "page_id": tree["kid_01現況"], "page_name": "01現況",
        "block_template": "callout→Current標題→正文→Evidence標題→索引"}
    c["new_tree"] = {k: v for k, v in tree.items()}
    for name, pid in list(c.get("routes", {}).items()):
        p = pid.get("page_id", "") if isinstance(pid, dict) else ""
        if "PENDING" in p:
            raise ValueError(f"route {name} 仍 PENDING，回填未完成")
    ids = [r.get("page_id", "") for r in c["routes"].values()
           if isinstance(r, dict) and r.get("page_id")]
    if len(set(ids)) != len(ids):
        raise ValueError("route page_id 不唯一")
    for pid in ids:
        for oid in OLD_IDS:
            if pid.replace("-", "") == oid.replace("-", ""):
                raise ValueError(f"route 指向舊館 ID（凍結，拒寫）：{pid}")
    return c


def main():
    j = json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {"events": {}}
    tree = j.setdefault("new_tree", {})
    # 1. 根頁：固定用 Owner 已建頁；若 journal 殘留舊 workspace POST 的 root，一律覆寫為既有根
    if tree.get("root_id") not in (None, EXISTING_ROOT_ID) and tree.get("root_id") != EXISTING_ROOT_ID:
        print(f"override stale root {tree.get('root_id')} -> {EXISTING_ROOT_ID}", flush=True)
    tree["root_id"] = EXISTING_ROOT_ID
    # 1b. 改名（先讀：已是目標即跳過 PATCH）
    try:
        cur = _req("GET", f"https://api.notion.com/v1/pages/{EXISTING_ROOT_ID}")
        props = cur.get("properties", {})
        title_now = ""
        for v in props.values():
            if isinstance(v, dict) and v.get("type") == "title":
                title_now = "".join(x.get("plain_text", "") for x in v.get("title", []))
        print(f"root now title={title_now!r}", flush=True)
        if title_now != ROOT_TITLE:
            _req("PATCH", f"https://api.notion.com/v1/pages/{EXISTING_ROOT_ID}",
                 {"properties": title_prop(ROOT_TITLE)})
            print(f"renamed root -> {ROOT_TITLE}", flush=True)
        else:
            print("root title already target, skip rename", flush=True)
    except Exception as e:
        raise RuntimeError(f"根頁讀取/改名失敗：{e}")
    # 2. 7 子頁
    for name, current in KIDS_SPEC:
        key = "kid_" + name
        if len(current) > 1200:
            raise ValueError(f"{name} Current {len(current)} 字超 1200 上限")
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
        JOURNAL.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    # 3. 90 下按月建頁：2026-11 首冊
    if "ev_2026_11" not in tree:
        r = _req("POST", "https://api.notion.com/v1/pages",
                 {"parent": {"page_id": tree["kid_90證據"]},
                  "properties": title_prop(EV_PAGE_TITLE)})
        tree["ev_2026_11"] = r["id"]
        print("ev", r["id"], flush=True)
        _req("PATCH", f"https://api.notion.com/v1/blocks/{r['id']}/children",
             {"children": [block_h2("Evidence・2026-11"),
                            block_p("首冊。舊館目錄＋快照 hash 待盤點 manifest 完成後回填。")]})
        JOURNAL.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        print("skip ev_2026_11", tree["ev_2026_11"], flush=True)
    JOURNAL.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    # 4. 回填 contract（T0 不動）
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    c = backfill_contract_data(c, tree)
    if not c.get("t0_utc"):
        c["t0_utc"] = "2026-11-11T01:50Z"
    c["last_build_utc"] = UTC
    c["last_build_commit"] = HEAD
    if "blocker" in c and "workspace" in str(c.get("blocker", "")):
        c["blocker_resolved"] = ("Owner 已建根頁 4155ab1a…並分享；workspace 直建阻塞解除（B 案 fondo）。" + UTC)
    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    print("contract backfilled", flush=True)
    print("T0(keep)=", c.get("t0_utc"), "build=", UTC, "head=", HEAD, flush=True)


if __name__ == "__main__":
    main()
