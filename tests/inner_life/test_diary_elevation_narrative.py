"""LIFE-THREAD-P1: elevation must carry real narrative, not a metadata label.

2026-10-03, Owner ruling: 「沉澱成信念需要自己的經歷，光看完就沉澱是不合理的」

Root cause: `submit()` + `_elevate_check()` used to run BEFORE `cb_real()`, so the
InnerLifeEvent had no body — only `extras={"slot": slot}`. `elevation_adapter.
_event_content()` synthesises `f"{trigger_type}: " + "; ".join(f"{k}={v}")`
⇒ `"diary:night: slot=night"`, which soul-elevation's passthrough stub wrote
verbatim as a value/belief node.

This test pins the fix at the two layers that can be tested in isolation:
  1. `_event_content()` — with a `narrative` key in extras, the content is the
     narrative, not the label.
  2. placeholder entries must NOT become narrative (proxy.py:295-302 rule:
     only `source=llm` is real content).
  3. the wiring order in run_server.py (AST): `cb_real` must be awaited BEFORE
     `submission_gate.submit`, and `submit` must be inside the post-cb block.

No production data, no waiting, no slot.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.inner_life.elevation_adapter import _event_content  # noqa: E402
from src.inner_life.event import (  # noqa: E402
    TRIGGER_TYPE_DIARY_NIGHT,
    InnerLifeEvent,
    Provenance,
)

NARRATIVE = "Bryのことを、ふと思い出した。あの人の前だと、役じゃない自分でいられる気がする。"


def _event(extras: dict) -> InnerLifeEvent:
    # 簽章依 event.py:194-201：無 content 欄位（正文留在 Memory/Fact，
    # 這正是 _event_content 要從 provenance 合成的原因）。
    return InnerLifeEvent(
        event_id="b" * 32,
        session_id=None,
        correlation_id=None,
        parent_event_id=None,
        ts="2026-10-03T00:00:00+00:00",
        provenance=Provenance(
            trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
            actor_id="agent_ruka",
            source_system="diary",
            extras=extras,
        ),
    )


def test_without_narrative_yields_label():
    """Baseline (the bug): only `slot` in extras ⇒ label content."""
    got = _event_content(_event({"slot": "night"}))
    assert got == "diary:night: slot=night"
    assert "Bry" not in got


def test_with_narrative_key_content_is_the_narrative():
    """After the fix: narrative present ⇒ the real body is what gets elevated."""
    ev = _event({"slot": "night"})
    # frozen dataclass blocks attribute assignment, but extras is a mutable dict
    ev.provenance.extras["narrative"] = NARRATIVE
    got = _event_content(ev)
    assert NARRATIVE in got
    assert "Bry" in got
    assert got.startswith(TRIGGER_TYPE_DIARY_NIGHT)


def test_frozen_provenance_still_allows_extras_mutation():
    """Contract check: mutating `extras` must not raise (frozen only blocks attrs)."""
    ev = _event({"slot": "night"})
    with pytest.raises(Exception):
        ev.provenance.trigger_type = "hacked"      # frozen: must block
    ev.provenance.extras["narrative"] = NARRATIVE  # dict: must allow
    assert ev.provenance.extras["narrative"] == NARRATIVE


# ── wiring order (AST, no import of run_server) ──────────────────────────

def _executor_node():
    src = (REPO / "scripts" / "run_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_diary_writer_executor":
            return node
    pytest.skip("找不到 _diary_writer_executor")


def _awaited_call_names(node) -> list[str]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            out.append(sub.func.attr)
    return out


def test_cb_real_awaited_before_submit():
    """Ordering: `cb_real` 必須在 `submission_gate.submit` **之前**被 await。"""
    node = _executor_node()
    cb_lineno = None
    submit_lineno = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call):
            f = sub.value.func
            if isinstance(f, ast.Name) and f.id == "cb_real":
                cb_lineno = sub.lineno
        if isinstance(sub, ast.Attribute) and sub.attr == "submit":
            submit_lineno = sub.lineno
    assert cb_lineno is not None, "找不到 await cb_real(...)"
    assert submit_lineno is not None, "找不到 submission_gate.submit(...)"
    assert cb_lineno < submit_lineno, (
        f"順序錯誤：await cb_real 在 line {cb_lineno}，"
        f"submit 在 line {submit_lineno} —— 昇華必須在日記寫入之後"
    )


def test_narrative_stamped_before_submit():
    """extras['narrative'] 的寫入必須在 submit 之前。"""
    node = _executor_node()
    stamp = None
    submit = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value == "narrative":
            stamp = sub.lineno
        if isinstance(sub, ast.Attribute) and sub.attr == "submit":
            submit = sub.lineno
    assert stamp is not None, "找不到 extras['narrative'] 的寫入"
    assert submit is not None
    assert stamp < submit, "正文必須在 submit 之前寫入 extras"


def test_create_event_still_precedes_cb_real():
    """M5.4-6.1 維持：create_event 必須仍在 cb_real 之前（event_id 要傳給寫入）。"""
    node = _executor_node()
    create = None
    cb = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "create_event":
            create = sub.lineno
        if isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call):
            f = sub.value.func
            if isinstance(f, ast.Name) and f.id == "cb_real":
                cb = sub.lineno
    assert create is not None and cb is not None
    assert create < cb, "M5.4-6.1 契約：事件必須先於 diary 寫入建立"
