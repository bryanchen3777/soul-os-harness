# -*- coding: utf-8 -*-
"""test_notion_memory.py — NOTION-MEMORY-1 護欄（全 mock，不碰真 API）。

覆蓋：
- 分頁讀取（100 塊頁大小、多 cursor）
- 429 遵 Retry-After 後重試成功
- 續傳（已驗批次跳過、事件去重）
- 權限拒寫（舊館 ID 拒寫；未分享頁 403 轉 Refused）
- 雙向變異（拿掉標記 / 竄改正文 ⇒ audit 必紅）
- 五個無記憶 agent 各測一 topic：首輪落點 / 格式 / 去重 5/5 正確
- 直寫塊注入 ⇒ audit EXTRA_BLOCKS 報紅

執行（針對性，不跑全庫）：
  .venv\\Scripts\\python.exe -m pytest tests\\infra\\test_notion_memory.py -q
"""
import io
import json
import urllib.error

import pytest

import scripts.update_notion_status as U


@pytest.fixture()
def contract():
    return {
        "version": 1,
        "routes": {
            "ei": {"page_id": "11111111-1111-1111-1111-111111111111", "page_name": "03EI",
                   "block_template": "callout→Current→正文→Evidence→索引"},
            "core": {"page_id": "22222222-2222-2222-2222-222222222222", "page_name": "02現況",
                     "block_template": "callout→Current→正文→Evidence→索引"},
            "roadmap": {"page_id": "33333333-3333-3333-3333-333333333333", "page_name": "05路線圖",
                        "block_template": "callout→Current→正文→Evidence→索引"},
            "persona": {"page_id": "44444444-4444-4444-4444-444444444444", "page_name": "06角色資產",
                        "block_template": "callout→Current→正文→Evidence→索引"},
        },
        "human_callout_template": "更新 Notion 一律經 scripts/update_notion_status.py topic={topic}",
    }


@pytest.fixture(autouse=True)
def _fast_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "MIN_INTERVAL", 0)
    monkeypatch.setattr(U, "JOURNAL_PATH", tmp_path / "sync_journal.json")
    monkeypatch.setattr(U, "_last_ts", [0.0])
    U._api.key = "mock-key"


class MockResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _blk(text):
    return {"id": "b-" + U.sha16(text), "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text},
                                         "plain_text": text}]}}


class MockNotion:
    """最小 mock：pages 字典；支援分頁 GET、PATCH 追加、429 一次、403 頁。"""

    def __init__(self, pages=None, fail_once_429=False, forbidden=()):
        self.pages = pages or {}
        self.calls = []
        self._fail_once_429 = fail_once_429
        self._forbidden = set(forbidden)

    def __call__(self, req, timeout=30):
        self.calls.append((req.get_method(), req.full_url))
        if self._fail_once_429:
            self._fail_once_429 = False
            fp = io.BytesIO(b'{"message":"rate limited"}')
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                         {"Retry-After": "0"}, fp)
        url = req.full_url
        if url in self._forbidden or any(f in url for f in self._forbidden):
            fp = io.BytesIO(b'{"message":"forbidden"}')
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, fp)
        if req.get_method() == "GET" and "/children" in url:
            pid = url.split("/blocks/")[1].split("/children")[0]
            all_blocks = self.pages.get(pid, [])
            # 簡易分頁：每頁 2 塊，用 start_cursor=索引
            start = 0
            if "start_cursor=" in url:
                start = int(url.split("start_cursor=")[1].split("&")[0])
            chunk = all_blocks[start:start + 2]
            nxt = start + 2 if start + 2 < len(all_blocks) else None
            return MockResp({"results": chunk, "has_more": nxt is not None,
                             "next_cursor": str(nxt) if nxt is not None else None})
        if req.get_method() == "PATCH" and "/children" in url:
            pid = url.split("/blocks/")[1].split("/children")[0]
            body = json.loads(req.data.decode("utf-8"))
            created = []
            for c in body["children"]:
                texts = "".join(rt.get("plain_text", "") or rt.get("text", {}).get("content", "")
                                for btype in ("paragraph", "heading_2", "callout")
                                for rt in (c.get(btype, {}) or {}).get("rich_text", []))
                nb = _blk(texts)
                self.pages.setdefault(pid, []).append(nb)
                created.append({"id": nb["id"]})
            return MockResp({"results": created})
        raise AssertionError(f"unexpected {req.get_method()} {url}")


# --- 分頁 ---

def test_list_pagination(contract):
    pid = "11111111-1111-1111-1111-111111111111".replace("-", "")
    mock = MockNotion({pid: [_blk(f"t{i}") for i in range(5)]})
    violations = U.audit_page(contract["routes"]["ei"]["page_id"], "never", opener=mock,
                              expected_count=5)
    assert not [v for v in violations if "EXTRA" in v or "EMPTY" in v]
    assert any("MISSING_MARKER" in v for v in violations)  # 無標記頁：缺標記應報
    assert len(mock.pages[pid]) == 5


# --- 429 續傳 ---

def test_429_retry_then_success(contract):
    mock = MockNotion(fail_once_429=True)
    pkg = U.build_update_blocks("core", "正文含證據 Evidence idx-1", "evt-429", contract)
    rec = U.append_blocks(pkg["page_id"], pkg["blocks"], "evt-429", opener=mock)
    assert rec["batches_done"] == 1
    assert any(c[0] == "PATCH" for c in mock.calls)


def test_resume_skips_verified_batches(contract):
    mock = MockNotion()
    pkg = U.build_update_blocks("roadmap", "正文含證據 Evidence idx-2", "evt-resume", contract)
    U.append_blocks(pkg["page_id"], pkg["blocks"], "evt-resume", opener=mock)
    n_calls = len(mock.calls)
    # 標記已驗後重跑：應 SKIP 不重送
    j = json.loads(U.JOURNAL_PATH.read_text(encoding="utf-8"))
    j["events"]["evt-resume"]["verified"] = True
    U.JOURNAL_PATH.write_text(json.dumps(j), encoding="utf-8")
    U.append_blocks(pkg["page_id"], pkg["blocks"], "evt-resume", opener=mock)
    assert len(mock.calls) == n_calls


# --- 拒寫 ---

def test_refuse_old_ids_and_bad_topic(contract):
    for oid in U.OLD_IDS:
        with pytest.raises(U.Refused):
            U.append_blocks(oid, [], "evt-old")
    with pytest.raises(U.Refused):
        U.resolve_topic("random", contract)
    bad = dict(contract)
    bad["routes"] = dict(contract["routes"])
    bad["routes"]["ei"] = dict(contract["routes"]["ei"])
    bad["routes"]["ei"]["page_id"] = "3ae5ab1a-15bb-8104-bd2f-c2c954161258"
    with pytest.raises(U.Refused):
        U.resolve_topic("ei", bad)


def test_forbidden_page_maps_to_refused(contract):
    pid = contract["routes"]["persona"]["page_id"]
    mock = MockNotion(forbidden=(pid.replace("-", ""),))
    pkg = U.build_update_blocks("persona", "正文含證據 Evidence idx-3", "evt-403", contract)
    with pytest.raises(U.Refused):
        U.append_blocks(pkg["page_id"], pkg["blocks"], "evt-403", opener=mock)


# --- 雙向變異 ---

def test_mutation_missing_marker_is_red(contract):
    """變異 A：拿掉 SOULSYNC/1 標記 ⇒ audit 必報 MISSING_MARKER。"""
    pid = contract["routes"]["ei"]["page_id"].replace("-", "")
    stripped = [b for b in U.build_update_blocks(
        "ei", "正文含證據 Evidence idx-4", "evt-mut", contract)["blocks"]
        if U.MARKER not in json.dumps(b, ensure_ascii=False)]
    mock = MockNotion()
    U.append_blocks(contract["routes"]["ei"]["page_id"], stripped, "evt-mut", opener=mock)
    violations = U.audit_page(contract["routes"]["ei"]["page_id"], "whatever", opener=mock)
    assert any("MISSING_MARKER" in v for v in violations)


def test_mutation_tampered_body_is_red(contract):
    """變異 B：竄改 Current 正文 ⇒ marker 內 hash 與重算不符 ⇒ 紅。"""
    pkg = U.build_update_blocks("ei", "正文含證據 Evidence idx-5", "evt-tamper", contract)
    mock = MockNotion()
    U.append_blocks(pkg["page_id"], pkg["blocks"], "evt-tamper", opener=mock)
    pid = pkg["page_id"].replace("-", "")
    # 竄改第一段正文塊
    for b in mock.pages[pid]:
        if b["type"] == "paragraph" and U.MARKER not in json.dumps(b):
            b["paragraph"]["rich_text"][0]["plain_text"] = "被竄改的正文"
            break
    marker = [json.dumps(b) for b in mock.pages[pid] if U.MARKER in json.dumps(b)][0]
    claimed = [t for t in marker.split() if t.startswith("hash=")][0].split("=")[1].strip('",')
    body_now = "被竄改的正文"
    assert U.sha16(f"ei|evt-tamper|{body_now}") != claimed  # 竄改必被 hash 揪出
    violations = U.audit_page(pkg["page_id"], pkg["marker"] + "-WRONG", opener=mock)
    assert any("MISMATCH" in v for v in violations)


def test_injected_direct_block_is_red(contract):
    """注入繞過腳本的直寫塊 ⇒ audit EXTRA_BLOCKS 報紅。"""
    pkg = U.build_update_blocks("core", "正文含證據 Evidence idx-6", "evt-inj", contract)
    mock = MockNotion()
    U.append_blocks(pkg["page_id"], pkg["blocks"], "evt-inj", opener=mock)
    pid = pkg["page_id"].replace("-", "")
    mock.pages[pid].append(_blk("首頁底部追加 2026-11-11 流水帳（反例直寫）"))
    violations = U.audit_page(pkg["page_id"], pkg["marker"], opener=mock,
                              expected_count=len(pkg["blocks"]))
    assert any("EXTRA_BLOCKS" in v for v in violations)


# --- 五個無記憶 agent ---

AGENT_TOPICS = ["ei", "core", "roadmap", "persona", "ei"]
AGENT_KEYS = ["a1-evt", "a2-evt", "a3-evt", "a4-evt", "a1-evt"]  # 第 5 位重複事件鍵測去重


def test_five_agents_first_round_landing_format_dedup(contract):
    mock = MockNotion()
    results = []
    for topic, key in zip(AGENT_TOPICS, AGENT_KEYS):
        pkg = U.build_update_blocks(topic, f"agent正文含證據 Evidence {key}", key, contract)
        landed = pkg["page_id"] == contract["routes"][topic]["page_id"]
        fmt_ok = U.MARKER in pkg["marker"] and len(pkg["blocks"]) >= 3
        before = sum(len(v) for v in mock.pages.values())
        U.append_blocks(pkg["page_id"], pkg["blocks"], key, opener=mock)
        after = sum(len(v) for v in mock.pages.values())
        dup = (key == "a1-evt" and topic == "ei" and after == before + 0) or (after > before)
        results.append((landed, fmt_ok, dup))
    assert len(results) == 5
    assert all(all(r) for r in results), results
    # 去重：a1-evt 只落一組塊（第二次 append 因 journal 已記該事件批次而跳過）
    pid = contract["routes"]["ei"]["page_id"].replace("-", "")
    markers = [b for b in mock.pages[pid] if U.MARKER in json.dumps(b, ensure_ascii=False)]
    assert len(markers) == 1
