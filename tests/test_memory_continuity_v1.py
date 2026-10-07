"""Memory Continuity Observability v1 — 行為測試（T1–T12）。

🔴 測試隔離紅線：
* 所有 sidecar 輸出都落在 pytest ``tmp_path``，**絕不寫入 ``data/**``**。
* 不啟動任何伺服器、不綁任何生產埠、不對線上服務發真實請求。
* 本檔案**不含任何 ``subprocess`` 呼叫**（spawn guard R5 逐行列管），
  取 HEAD 原始碼的 helper 放在掃描範圍外的 ``harness/continuity_analyze.py``。
* 不殺任何行程。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness import continuity_analyze, continuity_gc  # noqa: E402
from src.memory.continuity import (  # noqa: E402
    GAP_WARNING_PREFIX,
    INJECTION_FILENAME,
    SCHEMA_INJECTION,
    SCHEMA_TURN,
    TURN_FILENAME,
    USE_CONFIDENCE,
    ContinuityRecorder,
    TurnLinkIndex,
    canonicalize,
    classify_response_status,
    match_ngrams,
    ngrams,
    score_against_memories,
    text_hash,
)

AGENT = "agent_rem"


# ── helpers ────────────────────────────────────────────────────


class FakeBus:
    """最小 bus 替身：只吃 publish，不啟動任何東西。"""

    def __init__(self) -> None:
        self.published = []

    async def publish(self, event) -> None:
        self.published.append(event)


def make_memory(memory_id: str, content: str, confidence: float = 0.9,
                category: str = "preference", tags=None):
    """造一個 duck-typed Memory（避免依賴 frozen dataclass 建構順序）。"""
    return SimpleNamespace(
        memory_id=memory_id,
        agent_id=AGENT,
        content=content,
        tags=list(tags or ["咖啡"]),
        confidence=confidence,
        category=category,
    )


def make_event(event_type: str, payload: dict, event_id: str = "evt-1",
               correlation_id=None, session_id: str = "s1"):
    """造一個 duck-typed SoulEvent（只帶 continuity / middleware 讀得到的欄位）。"""
    from src.eventbus.schema import EventPriority

    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        source=AGENT,
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=payload,
        session_id=session_id,
        correlation_id=correlation_id,
        inner_life_event_id=None,
    )


def read_rows(path: Path):
    import json
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def build_middleware(tmp_path: Path, monkeypatch, eligible):
    """組一個 MemoryMiddleware，SAGE / loader 全換成 stub。

    🔴 不啟動 SAGE provider、不打真實 LLM、不寫 data/**（data_dir 指 tmp）。
    """
    from src.memory import middleware as mw

    monkeypatch.setattr(mw, "_RELATIONSHIPS_AVAILABLE", False)
    middleware = mw.MemoryMiddleware(
        bus=FakeBus(),
        data_dir=str(tmp_path / "memory"),
        events_dir=str(tmp_path / "events"),
    )

    class StubProvider:
        def prefetch(self, query, session_id=None, source_pair_filter=None):
            return ""

    class StubLoader:
        def load(self, query_tags, agent_id, top_k_per_category=3):
            return {
                "response_id": "loader-response-id-1",
                "eligible_memories": eligible,
                "trace": {
                    "eligible_count": len(eligible),
                    "fail_safe_triggered": None,
                    "candidates": [],
                },
            }

    monkeypatch.setattr(middleware, "_get_provider", lambda agent_id: StubProvider())
    monkeypatch.setattr(middleware, "_get_loader", lambda agent_id: StubLoader())
    return middleware


# ── T1: canonicalize / hash ────────────────────────────────────


def test_t1_canonicalize_and_hash_are_deterministic():
    samples = [
        "Bryan 喜歡咖啡",          # 中英混排
        "雷姆のことが好き",        # 日文
        "café",                  # 結合字元（NFKC 生效）
        "emoji 🙂 test",          # emoji
        "line1\r\nline2",        # CRLF
        "",                      # 空字串
    ]
    for text in samples:
        first = text_hash(text)
        assert first == text_hash(text), f"{text!r} hash 不穩定"
        assert len(first) == 64
    assert canonicalize("ＣＡＦÉ") == canonicalize("café")
    assert canonicalize("ABC") == canonicalize("abc")
    # 刻意保留空白/標點（保守低估的來源，工單指定）
    assert canonicalize("喜歡,咖啡") != canonicalize("喜歡咖啡")


# ── T2: injection schema 完整 ──────────────────────────────────


def test_t2_injection_row_has_all_schema_fields(tmp_path):
    recorder = ContinuityRecorder(data_dir=tmp_path)
    eligible = [
        make_memory("mem-1", "Bryan 喜歡咖啡"),
        make_memory("mem-2", "姊姊喜歡抹茶"),
        make_memory("mem-3", "週日想去看展"),
    ]
    snapshot_id = recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT}, correlation_id="corr-1"),
        eligible=eligible,
        response_id="loader-rid-1",
    )
    rows = read_rows(tmp_path / INJECTION_FILENAME)
    assert len(rows) == 3

    required = {
        "schema_version", "snapshot_id", "event_id", "correlation_id",
        "session_id", "agent_id", "memory_id", "memory_text",
        "memory_text_hash", "retrieval_confidence", "injection_rank",
        "injection_timestamp", "response_id", "retention_expires_at",
    }
    for row in rows:
        assert required <= set(row), f"缺欄位: {required - set(row)}"
        assert row["schema_version"] == SCHEMA_INJECTION
        assert row["snapshot_id"] == snapshot_id
        assert row["correlation_id"] == "corr-1"
        assert row["agent_id"] == AGENT
        assert isinstance(row["injection_rank"], int)
        assert isinstance(row["memory_text"], str)
        assert isinstance(row["memory_text_hash"], str)
        assert isinstance(row["retrieval_confidence"], float)
        # 同一次注入共用同一 snapshot_id
    # injection_rank 必須是 0,1,2 連續
    assert [r["injection_rank"] for r in rows] == [0, 1, 2]


# ── T3: rank 對應 + memory_text == format_for_prompt 渲染內容 ─


def test_t3_rank_maps_to_eligible_index_and_rendered_text(tmp_path):
    from src.memory.v1.loader import format_for_prompt

    eligible = [
        make_memory("mem-1", "Bryan 喜歡咖啡"),
        make_memory("mem-2", "姊姊喜歡抹茶"),
        make_memory("mem-3", "週日想去看展"),
    ]
    recorder = ContinuityRecorder(data_dir=tmp_path)
    recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT}),
        eligible=eligible,
        response_id="loader-rid-1",
    )
    rows = read_rows(tmp_path / INJECTION_FILENAME)

    rendered = format_for_prompt(eligible)
    for row in rows:
        memory = eligible[row["injection_rank"]]
        assert row["memory_id"] == memory.memory_id
        # T3: memory_text 就是實際被 format_for_prompt 渲染出去的 content
        assert row["memory_text"] == memory.content
        assert memory.content in rendered
        assert row["memory_text_hash"] == text_hash(memory.content)

    # D4: eligible 只讀 —— 呼叫後不得被改動
    assert [m.memory_id for m in eligible] == ["mem-1", "mem-2", "mem-3"]
    assert len(eligible) == 3


# ── T4: 4-gram 邊界 ───────────────────────────────────────────


def test_t4_ngram_boundaries():
    # 0 命中
    assert match_ngrams("Bryan 喜歡咖啡", "今天天氣不錯呢") == set()
    assert match_ngrams("Bryan 喜歡咖啡", "") == set()
    # 1 個命中 → 不算 candidate
    one = match_ngrams("abcdefgh", "zzzabcdyyy")
    assert len(one) == 1
    assert score_against_memories("zzzabcdyyy", [("m1", "abcdefgh")])["candidate"] is False
    # 2 個以上**彼此不同**的 4-gram → 算 candidate
    assert score_against_memories("abcdefgh", [("m1", "abcdefgh")])["candidate"] is True
    # 同一 4-gram 重複只算一次（set 去重）
    assert len(match_ngrams("abcabcabc", "abcabcabcabcabcabcabcabc")) == 3
    # len(norm) < 4 → 空集合
    assert ngrams("abc") == set()
    assert ngrams("") == set()
    assert len(ngrams("abcd")) == 1
    # 標點不去除 → 保守低估（工單指定語意）
    assert match_ngrams("喜歡,咖啡", "喜歡咖啡") == set()


# ── T5: use 標記語意（不得出現 memory_used）────────────────────


def test_t5_use_is_heuristic_never_memory_used(tmp_path):
    recorder = ContinuityRecorder(data_dir=tmp_path)
    link_index = TurnLinkIndex()
    recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT}, correlation_id="corr-1"),
        eligible=[make_memory("mem-1", "Bryan 喜歡咖啡")],
        response_id="loader-rid-1",
        link_index=link_index,
    )
    row = recorder.record_turn(
        make_event(
            "AGENT_SPEAK",
            {"agent_id": AGENT, "text": "我記得 Bryan 喜歡咖啡 對吧"},
            event_id="evt-s1",
            correlation_id="corr-1",
        ),
        link_index=link_index,
    )
    assert row["link_status"] == "linked"
    assert row["use_heuristic"]["use_confidence"] == USE_CONFIDENCE == "heuristic"
    assert row["use_heuristic"]["candidate"] is True

    # 🔴 全檔不得出現 memory_used 這個字串
    targets = [
        REPO_ROOT / "src" / "memory" / "continuity.py",
        REPO_ROOT / "src" / "memory" / "middleware.py",
        REPO_ROOT / "harness" / "continuity_analyze.py",
        REPO_ROOT / "harness" / "continuity_gc.py",
    ]
    for path in targets:
        assert "memory_used" not in path.read_text(encoding="utf-8"), (
            f"{path.name} 出現 memory_used —— use 只能是 heuristic 訊號"
        )


# ── T6: join ───────────────────────────────────────────────────


def test_t6_linked_and_unlinked_join(tmp_path):
    link_index = TurnLinkIndex()
    recorder = ContinuityRecorder(data_dir=tmp_path)

    # linked：先注入（寫入 link index），再 speak（pop）
    snapshot_id = recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT},
                         event_id="evt-i", correlation_id="corr-1"),
        eligible=[make_memory("mem-1", "Bryan 喜歡咖啡")],
        response_id="loader-rid-1",
        link_index=link_index,
    )
    turn = recorder.record_turn(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": "今天天氣不錯"},
                   event_id="evt-s", correlation_id="corr-1"),
        link_index=link_index,
    )
    assert turn["link_status"] == "linked"
    assert turn["injection_snapshot_id"] == snapshot_id

    # unlinked：查無 → null + unlinked，**且不拋例外**
    unlinked = recorder.record_turn(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": "嗨"},
                   event_id="evt-s2", correlation_id="corr-nope"),
        link_index=link_index,
    )
    assert unlinked["link_status"] == "unlinked"
    assert unlinked["injection_snapshot_id"] is None


# ── T7: stub turn 仍留下一列 ───────────────────────────────────


def test_t7_stub_turn_is_recorded_as_stub(tmp_path, monkeypatch):
    middleware = build_middleware(tmp_path, monkeypatch, [])
    # 構造 proxy.py stub 路徑形狀的 AGENT_SPEAK：text="" + is_stub=True
    stub_event = make_event(
        "AGENT_SPEAK",
        {
            "agent_id": AGENT,
            "text": "",
            "is_stub": True,
            "stub_reason": "llm_failed",
        },
        event_id="evt-stub",
        correlation_id="corr-stub",
    )

    import asyncio
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        middleware._on_agent_speak(stub_event)
    )

    rows = read_rows(tmp_path / "memory" / TURN_FILENAME)
    assert len(rows) == 1, "stub turn 必須留下可追蹤的紀錄，不得被 early return 靜默吃掉"
    assert rows[0]["response_status"] == "stub"
    assert rows[0]["response_text"] == ""
    assert rows[0]["schema_version"] == SCHEMA_TURN

    # 三種 response_status 都可區分
    assert classify_response_status(stub_event) == "stub"
    assert classify_response_status(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": ""})
    ) == "empty_text"
    assert classify_response_status(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": "嗨"})
    ) == "ok"


# ── T8: TurnLinkIndex 邊界 ─────────────────────────────────────


def test_t8_turn_link_index_ttl_eviction_and_single_consume():
    # TTL 過期
    clock = {"t": 0.0}
    index = TurnLinkIndex(ttl_seconds=10.0, clock=lambda: clock["t"])
    index.record("c1", "snap-1")
    clock["t"] = 9.0
    assert index.pop("c1") == "snap-1"
    index.record("c2", "snap-2")
    clock["t"] = 25.0        # 25 - 15(linked_at) >= 10
    assert index.pop("c2") is None, "TTL 過期必須逐出"

    # 上限 200：逐出最舊
    index = TurnLinkIndex(clock=lambda: 0.0)
    for i in range(250):
        index.record(f"c{i}", f"snap-{i}")
    assert len(index) == 200
    assert index.pop("c0") is None, "最舊的必須被逐出"
    assert index.pop("c249") == "snap-249"

    # pop 後不可重複消費
    index.record("c9", "snap-9")
    assert index.pop("c9") == "snap-9"
    assert index.pop("c9") is None


# ── T9: retention = timestamp + 30d ────────────────────────────


def test_t9_retention_expires_at_is_30_days(tmp_path):
    recorder = ContinuityRecorder(data_dir=tmp_path)
    recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT}),
        eligible=[make_memory("mem-1", "Bryan 喜歡咖啡")],
        response_id="loader-rid-1",
    )
    recorder.record_turn(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": "嗨"}),
        link_index=TurnLinkIndex(),
    )

    for row in read_rows(tmp_path / INJECTION_FILENAME):
        delta = datetime.fromisoformat(row["retention_expires_at"]) - \
            datetime.fromisoformat(row["injection_timestamp"])
        assert abs(delta - timedelta(days=30)).total_seconds() <= 1
    for row in read_rows(tmp_path / TURN_FILENAME):
        delta = datetime.fromisoformat(row["retention_expires_at"]) - \
            datetime.fromisoformat(row["timestamp"])
        assert abs(delta - timedelta(days=30)).total_seconds() <= 1


# ── T10: 寫入失敗隔離（memory_context 逐 byte 相同）────────────


def test_t10_sidecar_failure_does_not_change_memory_context(tmp_path, monkeypatch, caplog):
    """🔴 Bry 硬性驗收：sidecar 完全失效時，memory_context 逐 byte 相同。"""
    eligible = [
        make_memory("mem-1", "Bryan 喜歡咖啡"),
        make_memory("mem-2", "姊姊喜歡抹茶"),
    ]
    payload = {"agent_id": AGENT, "draft": "今天想喝什麼", "text": "今天想喝什麼"}

    # ── run A: sidecar 正常 ──
    mw_ok = build_middleware(tmp_path / "a", monkeypatch, eligible)
    event_ok = make_event("AGENT_INTENT", dict(payload), event_id="evt-a",
                          correlation_id="corr-a")
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(mw_ok._on_agent_intent(event_ok))
    context_ok = event_ok.payload["memory_context"]
    assert read_rows(tmp_path / "a" / "memory" / INJECTION_FILENAME), "sidecar 應該有寫入"

    # ── run B: sidecar 完全失效（寫檔必失敗）──
    monkeypatch.setattr(
        ContinuityRecorder, "_append_lines",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk on fire")),
    )
    caplog.clear()
    mw_bad = build_middleware(tmp_path / "b", monkeypatch, eligible)
    event_bad = make_event("AGENT_INTENT", dict(payload), event_id="evt-b",
                           correlation_id="corr-b")
    loop.run_until_complete(mw_bad._on_agent_intent(event_bad))
    context_bad = event_bad.payload["memory_context"]

    # 🔴 逐 byte 相同（唯一差異應該只有 agent/事件識別碼的 log，不在 context 內）
    assert context_ok == context_bad
    assert context_ok.encode("utf-8") == context_bad.encode("utf-8")
    # 失效時不該寫出任何 injection 列
    assert read_rows(tmp_path / "b" / "memory" / INJECTION_FILENAME) == []
    # 但失敗必須**可見**：instrumentation gap 不得靜默
    assert any(GAP_WARNING_PREFIX in r.message for r in caplog.records)
    loop.close()


# ── T11: --rescore 等價（零費用重判）──────────────────────────


def test_t11_rescore_reproduces_written_heuristic(tmp_path, monkeypatch):
    recorder = ContinuityRecorder(data_dir=tmp_path)
    link_index = TurnLinkIndex()
    recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT},
                         event_id="evt-i", correlation_id="corr-1"),
        eligible=[make_memory("mem-1", "Bryan 喜歡咖啡"),
                  make_memory("mem-2", "姊姊喜歡抹茶")],
        response_id="loader-rid-1",
        link_index=link_index,
    )
    recorder.record_turn(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": "Bryan 喜歡咖啡 我記得"},
                   event_id="evt-s", correlation_id="corr-1"),
        link_index=link_index,
    )

    layer2 = read_rows(tmp_path / INJECTION_FILENAME)
    layer3 = read_rows(tmp_path / TURN_FILENAME)
    rescored = continuity_analyze.rescore(layer2, layer3)

    assert len(rescored) == 1
    assert rescored[0]["use_heuristic"] == layer3[0]["use_heuristic"]
    assert rescored[0]["needs_rescore"] is False

    # algorithm_version 不符 → 標記待重算
    stale = [dict(layer3[0])]
    stale[0]["use_heuristic"] = dict(stale[0]["use_heuristic"],
                                     algorithm_version="ngram3-v0")
    assert continuity_analyze.rescore(layer2, stale)[0]["needs_rescore"] is False
    assert continuity_analyze.rescore(layer2, stale,
                                      algorithm_version="ngram3-v0")[0]["needs_rescore"] is True


# ── T12: GC dry-run 不改動任何檔案 ─────────────────────────────


def test_t12_gc_dry_run_leaves_files_byte_identical(tmp_path, capsys):
    recorder = ContinuityRecorder(data_dir=tmp_path)
    recorder.record_injection(
        event=make_event("AGENT_INTENT", {"agent_id": AGENT}),
        eligible=[make_memory("mem-1", "Bryan 喜歡咖啡")],
        response_id="loader-rid-1",
    )
    recorder.record_turn(
        make_event("AGENT_SPEAK", {"agent_id": AGENT, "text": "嗨"}),
        link_index=TurnLinkIndex(),
    )
    injection = tmp_path / INJECTION_FILENAME
    turn = tmp_path / TURN_FILENAME
    before = (injection.read_bytes(), turn.read_bytes())

    result = continuity_gc.plan(tmp_path)
    continuity_gc.print_plan(result, apply=False)
    out = capsys.readouterr().out
    assert "DRY-RUN" in out

    assert (injection.read_bytes(), turn.read_bytes()) == before, \
        "dry-run 絕對不得改動任何位元組"
    # 剛寫的資料不該被判為過期
    assert result["files"]["layer2"]["would_drop"] == 0
    assert result["files"]["layer3"]["would_drop"] == 0


def test_t12_gc_plan_flags_expired_rows(tmp_path):
    expired_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with open(tmp_path / INJECTION_FILENAME, "w", encoding="utf-8") as fh:
        fh.write(f'{{"retention_expires_at": "{expired_ts}"}}\n')
        fh.write('{"retention_expires_at": "2099-01-01T00:00:00+00:00"}\n')
    result = continuity_gc.plan(tmp_path)
    assert result["files"]["layer2"]["would_drop"] == 1
    assert result["files"]["layer2"]["would_keep"] == 1