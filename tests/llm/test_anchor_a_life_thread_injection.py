"""LIFE-THREAD-ANCHOR-A: life-thread narrative must reach the prompt.

契約 §6.2.2 逐字規格：
  - 格式 `- [<YYYY-MM-DD> thread] <narrative 截斷至 60 字>`
  - 截斷常數對齊既有 INNER_LIFE_MAX_CHARS_PER_ENTRY（不新增第二套語意）
  - 總行數上限維持 INNER_LIFE_MAX_ENTRIES = 5（不提高）
  - 只取 active 線頭的 narrative_content
  - 與 diary 同一區塊、既有注入塊逐字文案不得改動

Isolated: synthetic jsonl in a tmp data_root. No production, no waiting.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.paths import reset_data_root  # noqa: E402


def _write_threads(root: pathlib.Path, agent: str, rows: list) -> None:
    d = root / "soul" / agent
    d.mkdir(parents=True, exist_ok=True)
    (d / "life_threads.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def _row(tid, status, narrative, updated="2026-10-02T08:00:00", seq=1):
    return {
        "thread_id": tid,
        "status": status,
        "origin_type": "whim_driven",
        "narrative_content": narrative,
        "updated_at": updated,
        "event_seq": seq,
    }


@pytest.fixture()
def iso_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    import importlib

    import src.llm.proxy as proxy

    importlib.reload(proxy)
    yield tmp_path, proxy
    reset_data_root()


def test_missing_file_is_silent(iso_root):
    root, proxy = iso_root
    assert proxy._format_life_thread_lines("agent_nope") == []
    assert proxy._format_recent_inner_life("agent_nope") == ""


def test_active_thread_appears_with_thread_tag(iso_root):
    root, proxy = iso_root
    _write_threads(
        root,
        "agent_x",
        [_row("t1", "active", "Bryのことを、ふと思い出した。")],
    )
    lines = proxy._format_life_thread_lines("agent_x")
    assert len(lines) == 1
    assert lines[0].startswith("- [")
    assert " thread] " in lines[0]
    assert "Bryのことを、ふと思い出した。" in lines[0]


def test_non_active_excluded(iso_root):
    root, proxy = iso_root
    _write_threads(
        root,
        "agent_x",
        [
            _row("t1", "active", "還在進行中的事"),
            _row("t2", "completed", "已經結束的事"),
            _row("t3", "abandoned", "被放棄的事"),
        ],
    )
    lines = proxy._format_life_thread_lines("agent_x")
    assert len(lines) == 1
    assert "還在進行中的事" in lines[0]
    assert "已經結束的事" not in lines[0]
    assert "被放棄的事" not in lines[0]


def test_empty_narrative_excluded(iso_root):
    root, proxy = iso_root
    _write_threads(
        root,
        "agent_x",
        [_row("t1", "active", ""), _row("t2", "active", "   ")],
    )
    assert proxy._format_life_thread_lines("agent_x") == []


def test_truncation_at_60_chars(iso_root):
    root, proxy = iso_root
    long_text = "あ" * 200
    _write_threads(root, "agent_x", [_row("t1", "active", long_text)])
    lines = proxy._format_life_thread_lines("agent_x")
    body = lines[0].split(" thread] ", 1)[1]
    assert len(body) == proxy.INNER_LIFE_MAX_CHARS_PER_ENTRY
    assert body.endswith("...")
    assert proxy.INNER_LIFE_MAX_CHARS_PER_ENTRY == 60


def test_append_only_fold_keeps_latest_state(iso_root):
    """同 thread_id 多列事件 → 依契約 §2.4 取現行狀態。"""
    root, proxy = iso_root
    _write_threads(
        root,
        "agent_x",
        [
            _row("t1", "active", "舊的敘述", updated="2026-10-01T08:00:00", seq=1),
            _row("t1", "completed", "新的敘述", updated="2026-10-02T08:00:00", seq=2),
        ],
    )
    lines = proxy._format_life_thread_lines("agent_x")
    assert lines == []          # 現行狀態是 completed → 不該出現
    _write_threads(
        root,
        "agent_x",
        [
            _row("t1", "completed", "舊的敘述", updated="2026-10-01T08:00:00", seq=1),
            _row("t1", "active", "新的敘述", updated="2026-10-02T08:00:00", seq=2),
        ],
    )
    lines = proxy._format_life_thread_lines("agent_x")
    assert len(lines) == 1 and "新的敘述" in lines[0]


def test_merged_with_diary_and_capped_at_5(iso_root):
    """錨 A 與 diary 同一區塊；總行數上限維持 5（不提高）。"""
    root, proxy = iso_root
    d = root / "soul" / "agent_x" / "diary"
    d.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _DT

    today = _DT.now().strftime("%Y-%m-%d")
    (d / f"{today}.jsonl").write_text(
        "\n".join(
            json.dumps(
                {"slot": "morning", "source": "llm", "content": f"日記第{i}則"},
                ensure_ascii=False,
            )
            for i in range(1, 6)
        )
        + "\n",
        encoding="utf-8",
    )
    _write_threads(root, "agent_x", [_row("t1", "active", "線頭的敘述")])

    block = proxy._format_recent_inner_life("agent_x")
    assert block, "區塊不得為空"
    lines = block.splitlines()
    assert len(lines) == proxy.INNER_LIFE_MAX_ENTRIES == 5
    # 契約 §6.2.2：線頭插在 diary **之後**、截斷之前 ⇒ 截斷後仍須在
    assert "thread]" in lines[-1]
    assert "線頭的敘述" in lines[-1]
    # 既有 diary 行的逐字格式不得改變
    assert lines[0].startswith("- [") and " morning] " in lines[0]
    assert any(ln.endswith(f"日記第{i}則") for i in range(1, 5) for ln in lines)


def test_placeholder_diary_still_excluded(iso_root):
    """既有規則不得被本次改動破壞：placeholder 不注入。"""
    root, proxy = iso_root
    d = root / "soul" / "agent_x" / "diary"
    d.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _DT

    today = _DT.now().strftime("%Y-%m-%d")
    (d / f"{today}.jsonl").write_text(
        json.dumps(
            {"slot": "morning", "source": "placeholder", "content": "假的日記"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert "假的日記" not in proxy._format_recent_inner_life("agent_x")
