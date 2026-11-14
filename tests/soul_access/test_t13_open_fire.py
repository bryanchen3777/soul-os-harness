"""SA-OPEN-FIRE-1 T13 one-time single-fire probe tests (sandbox only).

Decision-complete scope (SA-OPEN-FIRE-1): this file + the new
``scripts/sa_open_fire_probe.py`` are new; ``scripts/serve_sa_thin.py``,
``src/**``, ``configs/**``, ``data/**``, ``release/sa.json`` and every
Frozen contract are untouched. No server is started, no production port
is bound, no real request leaves the process.

All test names carry the t13 marker: ``pytest -q tests/soul_access -k t13``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every port preflight here injects a fake socket factory
(free dummy / raising occupied); no test ever binds a real port. The only
real key-adjacent value is a synthetic sandbox sentinel; production
digests are compared read-only, never used as credentials.

Coverage (ticket acceptance mapping):
  a. resend refused when the sent marker exists (atomic O_EXCL);
  b. concurrent double fire admits exactly one sender call;
  c. second attempt over quota refused (sender called once, ever);
  d. missing raw is NOT qualified (EvidenceGap; marker stays consumed);
  e. timeout counts as consumed, no retry; both doors finally closed;
  f. closed thin door: sandbox release=open still 403, 0 upstream calls,
     0 memory writes;
  g. offline str-only gap proof (dialog returns str, no raw/usage);
  h. CLI defaults closed: no --host/--port flags, check-only default;
  i. key ref is a variable NAME only (sentinel secret never in evidence);
  j. bidirectional mutations: relaxed/tightened mutants turn RED, revert GREEN.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

PROBE_PATH = _REPO_ROOT / "scripts" / "sa_open_fire_probe.py"
SERVE_THIN_PATH = _REPO_ROOT / "scripts" / "serve_sa_thin.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load(PROBE_PATH, "sa_open_fire_probe_t13")
thin_mod = _load(SERVE_THIN_PATH, "serve_sa_thin_t13")


class _FreeSocket:
    def bind(self, addr):
        self.bound = addr

    def close(self):
        pass


def _free_factory(*args, **kwargs):
    return _FreeSocket()


def _occupied_factory(*args, **kwargs):
    raise OSError("t13 simulated occupation")


class _CompleteSender:
    """Sandbox sender: returns a COMPLETE CallResult-shaped mapping."""

    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return {
            "text": "sandbox-reply",
            "raw": {"id": "sandbox-raw", "object": "chat.completion"},
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }


def _open_gate(tmp_path: Path) -> Path:
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    return gate


def _closed_gate(tmp_path: Path) -> Path:
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    return gate


def _fire(tmp_path, *, sender=None, gate=None, factory=_free_factory, door=None):
    sender = sender if sender is not None else _CompleteSender()
    gate = gate if gate is not None else _open_gate(tmp_path)
    kw = {"sender": sender, "socket_factory": factory, "gate_path": gate}
    if door is not None:
        kw["door"] = door
    return probe.attempt_fire(tmp_path, **kw), sender


# -- T13-a: resend refused ----------------------------------------------------

def test_t13_resend_refused_when_marker_exists(tmp_path):
    sender = _CompleteSender()
    gate = _open_gate(tmp_path)
    probe.create_marker_atomic(tmp_path)
    with pytest.raises(probe.AlreadySent):
        probe.attempt_fire(tmp_path, sender=sender, socket_factory=_free_factory, gate_path=gate)
    assert sender.calls == []


# -- T13-b: concurrent double fire admits exactly one -------------------------

def test_t13_concurrent_single_winner_single_sender_call(tmp_path):
    sender = _CompleteSender()
    gate = _open_gate(tmp_path)
    outcomes = []

    def _race():
        try:
            probe.attempt_fire(
                tmp_path, sender=sender, socket_factory=_free_factory, gate_path=gate
            )
            outcomes.append("sent")
        except probe.AlreadySent:
            outcomes.append("refused")

    threads = [threading.Thread(target=_race) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["refused", "sent"]
    assert len(sender.calls) == 1


# -- T13-c: second attempt over quota refused ---------------------------------

def test_t13_second_attempt_over_quota_refused(tmp_path):
    sender = _CompleteSender()
    gate = _open_gate(tmp_path)
    saved, _ = _fire(tmp_path, sender=sender, gate=gate)
    assert Path(saved).exists()
    with pytest.raises(probe.AlreadySent):
        probe.attempt_fire(tmp_path, sender=sender, socket_factory=_free_factory, gate_path=gate)
    assert len(sender.calls) == 1


# -- T13-d: missing raw is NOT qualified --------------------------------------

def test_t13_missing_raw_not_qualified_marker_consumed(tmp_path):
    def _str_only_sender(request):
        return {"text": "answer-without-evidence"}

    gate = _open_gate(tmp_path)
    with pytest.raises(probe.EvidenceGap):
        probe.attempt_fire(
            tmp_path, sender=_str_only_sender, socket_factory=_free_factory, gate_path=gate
        )
    # The attempt is consumed: no result file, rerun must not resend.
    assert not probe.result_path(tmp_path).exists()
    assert probe.marker_path(tmp_path).exists()
    with pytest.raises(probe.AlreadySent):
        probe.attempt_fire(
            tmp_path, sender=_CompleteSender(), socket_factory=_free_factory, gate_path=gate
        )


# -- T13-e: timeout consumed, no retry, doors finally closed ------------------

def test_t13_timeout_consumed_no_retry_doors_closed(tmp_path):
    calls = []

    def _timeout_sender(request):
        calls.append(request)
        raise TimeoutError("t13 simulated timeout")

    door = probe.InstanceDoor()
    gate = _open_gate(tmp_path)
    gate_before = gate.read_text(encoding="utf-8")
    with pytest.raises(TimeoutError):
        probe.attempt_fire(
            tmp_path, sender=_timeout_sender, socket_factory=_free_factory,
            gate_path=gate, door=door,
        )
    assert len(calls) == 1  # exactly one attempt, no retry
    assert probe.marker_path(tmp_path).exists()  # consumed
    assert door.is_open() is False  # instance door finally closed
    assert gate.read_text(encoding="utf-8") == gate_before  # gate never rewritten
    # Rerun refuses (no補發).
    with pytest.raises(probe.AlreadySent):
        probe.attempt_fire(
            tmp_path, sender=_CompleteSender(), socket_factory=_free_factory, gate_path=gate
        )
    assert len(calls) == 1


def test_t13_gate_closed_refuses_door_stays_closed_gate_untouched(tmp_path):
    sender = _CompleteSender()
    door = probe.InstanceDoor()
    gate = _closed_gate(tmp_path)
    with pytest.raises(probe.GateClosed):
        probe.attempt_fire(
            tmp_path, sender=sender, socket_factory=_free_factory,
            gate_path=gate, door=door,
        )
    assert sender.calls == []
    assert door.is_open() is False
    assert json.loads(gate.read_text(encoding="utf-8")) == {"status": "closed"}
    # The fenced attempt is consumed even though nothing was sent.
    assert probe.marker_path(tmp_path).exists()


def test_t13_blocked_port_refuses_before_marker(tmp_path):
    sender = _CompleteSender()
    gate = _open_gate(tmp_path)
    with pytest.raises(probe.BlockedPort):
        probe.attempt_fire(
            tmp_path, sender=sender, socket_factory=_occupied_factory, gate_path=gate
        )
    assert sender.calls == []
    assert not probe.marker_path(tmp_path).exists()  # nothing consumed


# -- T13-f: closed thin door, sandbox release=open ----------------------------

def test_t13_closed_zero_forward_zero_memory(tmp_path):
    registry_src = _REPO_ROOT / "configs" / "sa" / "registry.json"
    presets_src = _REPO_ROOT / "configs" / "sa" / "presets.json"
    before = {path.name for path in tmp_path.iterdir()}

    class _CountingForwarder:
        def __init__(self):
            self.calls = []

        def __call__(self, envelope, *, route=None, preset=None):
            self.calls.append(envelope)
            raise AssertionError("thin door must refuse before any forward")

    forwarder = _CountingForwarder()
    handler = thin_mod.build_thin_closed_handler(_forwarder=forwarder)
    with pytest.raises(Exception) as exc_info:
        handler(object(), route=None, preset=None)
    assert getattr(exc_info.value, "status", None) == 403
    assert forwarder.calls == []
    # Zero memory writes: no session/state file appeared in the sandbox dir.
    assert {path.name for path in tmp_path.iterdir()} == before
    assert thin_mod.is_thin_open() is False
    assert registry_src.exists() and presets_src.exists()


# -- T13-g: offline str-only gap proof ----------------------------------------

def test_t13_dialog_str_only_gap_offline():
    proof = probe.offline_evidence_gap_proof()
    assert proof["dialog_returns_str_only"] is True
    assert proof["dialog_return_type"] == "str"
    assert proof["has_raw"] is False
    assert proof["has_usage"] is False
    assert proof["evidence_channel_available"] is False
    assert proof["backend_call"]["max_retries"] == 0
    with pytest.raises(probe.EvidenceGap):
        probe.validate_evidence({"text": "answer-without-evidence"})


def test_t13_validate_evidence_accepts_complete_mapping():
    result = {
        "raw": {"id": "r"},
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
    assert probe.validate_evidence(result) is result
    for bad in (
        "plain-string",
        {"text": "no raw, no usage"},
        {"raw": "", "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"raw": {"id": "r"}, "usage": {"prompt_tokens": 1}},
        {"raw": {"id": "r"}, "usage": {"prompt_tokens": -1, "completion_tokens": 0}},
    ):
        with pytest.raises(probe.EvidenceGap):
            probe.validate_evidence(bad)


# -- T13-h: CLI defaults closed -----------------------------------------------

def test_t13_cli_defaults_closed_no_host_port_flags():
    option_strings = [
        text for action in probe.build_parser()._actions for text in action.option_strings
    ]
    assert "--host" not in option_strings
    assert "--port" not in option_strings
    assert probe.THIN_HOST == "127.0.0.1"
    assert probe.THIN_PORT == 18765
    assert probe.THIN_PORT == thin_mod.THIN_PORT


def test_t13_check_mode_sends_nothing_creates_no_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "is_loopback_free", lambda *a, **k: False)
    report = probe.run_check(tmp_path)
    assert report["billed_attempts"] == 0
    assert report["sent_marker_present"] is False
    assert not probe.marker_path(tmp_path).exists()
    assert not probe.result_path(tmp_path).exists()
    assert (tmp_path / "check-report.json").exists()


# -- T13-i: key ref is a name only --------------------------------------------

def test_t13_key_ref_is_name_only_secret_never_in_evidence(tmp_path, monkeypatch):
    sentinel = "t13-sandbox-secret-value"
    monkeypatch.setenv("OLLAMA_API_KEY", sentinel)
    path = probe.save_request(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert probe.KEY_REF in text
    assert sentinel not in text
    src = PROBE_PATH.read_text(encoding="utf-8")
    assert "getenv" not in src
    assert "environ" not in src


def test_t13_request_replayable_hash_stable_and_tamper_sensitive():
    first = probe.build_request()
    second = probe.build_request()
    assert first["sha256"] == second["sha256"]
    assert first["request_id"] == probe.REQUEST_ID == "req-sa-open-fire-1-0001"
    assert first["messages"] == [{"role": "user", "content": "你好。"}]
    tampered = dict(first)
    tampered["messages"] = [{"role": "user", "content": "你好！"}]
    assert tampered["messages"] != first["messages"]


def test_t13_fixed_route_pins_production_preset_values():
    presets = json.loads((_REPO_ROOT / "configs" / "sa" / "presets.json").read_text(encoding="utf-8"))
    primaries = [entry for entry in presets["presets"] if entry["name"] == "primary"]
    assert len(primaries) == 1
    assert probe.MODEL == primaries[0]["model"]
    assert probe.KEY_REF == primaries[0]["key_ref"]
    assert probe.SOUL_ALIAS == "rem"
    assert probe.BACKEND_ALIAS == "primary"
    assert probe.MAX_ATTEMPTS == 1


# -- T13-j: bidirectional mutations (relaxed RED / tightened RED / revert GREEN)

def test_t13_mutation_gate_check_bypassed_fires_while_closed_red(tmp_path, monkeypatch):
    """Relaxed mutant: gate check bypassed -> fires while closed (must be RED)."""
    sender = _CompleteSender()
    gate = _closed_gate(tmp_path)
    monkeypatch.setattr(probe, "read_gate_status", lambda path: "open")  # MUTANT
    probe.attempt_fire(tmp_path, sender=sender, socket_factory=_free_factory, gate_path=gate)
    if len(sender.calls) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: bypassed gate did not fire")
    # RED confirmed: without the gate check, a closed gate still fires.


def test_t13_mutation_gate_check_reverted_refuses_while_closed_green(tmp_path):
    """Revert: real gate check refuses while closed (GREEN)."""
    sender = _CompleteSender()
    gate = _closed_gate(tmp_path)
    with pytest.raises(probe.GateClosed):
        probe.attempt_fire(tmp_path, sender=sender, socket_factory=_free_factory, gate_path=gate)
    assert sender.calls == []


def test_t13_mutation_evidence_check_bypassed_accepts_str_only_red(tmp_path, monkeypatch):
    """Relaxed mutant: evidence validation bypassed -> str-only saved (RED)."""
    gate = _open_gate(tmp_path)

    def _mutant_save_result(evidence_dir, result):  # MUTANT: no validation at all
        path = probe.result_path(Path(evidence_dir))
        path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

    monkeypatch.setattr(probe, "save_result", _mutant_save_result)  # MUTANT
    saved = probe.attempt_fire(
        tmp_path,
        sender=lambda request: {"text": "unqualified-answer"},
        socket_factory=_free_factory,
        gate_path=gate,
    )
    if not Path(saved).exists():
        pytest.fail("MUTATION DUD -- STOP, report: bypassed evidence check did not save")
    # RED confirmed: unqualified text would be stored as evidence.


def test_t13_mutation_evidence_check_reverted_rejects_str_only_green(tmp_path):
    """Revert: real evidence check rejects str-only (GREEN)."""
    gate = _open_gate(tmp_path)
    with pytest.raises(probe.EvidenceGap):
        probe.attempt_fire(
            tmp_path,
            sender=lambda request: {"text": "unqualified-answer"},
            socket_factory=_free_factory,
            gate_path=gate,
        )
    assert not probe.result_path(tmp_path).exists()


def test_t13_mutation_gate_forced_closed_refuses_while_open_red(tmp_path, monkeypatch):
    """Tightened mutant: gate forced closed -> refuses a should-succeed fire (RED)."""
    sender = _CompleteSender()
    gate = _open_gate(tmp_path)
    monkeypatch.setattr(probe, "read_gate_status", lambda path: "closed")  # MUTANT
    with pytest.raises(probe.GateClosed):
        probe.attempt_fire(tmp_path, sender=sender, socket_factory=_free_factory, gate_path=gate)
    if sender.calls != []:
        pytest.fail("MUTATION DUD -- STOP, report: forced-closed gate leaked a send")
    # RED confirmed: the mutant over-refuses (zero sends while open).


def test_t13_mutation_reverted_full_stack_green(tmp_path):
    """Revert: open sandbox gate + free port + complete sender -> one save (GREEN)."""
    sender = _CompleteSender()
    gate = _open_gate(tmp_path)
    saved, _ = _fire(tmp_path, sender=sender, gate=gate)
    assert Path(saved).exists()
    assert len(sender.calls) == 1
    stored = json.loads(Path(saved).read_text(encoding="utf-8"))
    assert stored["request_id"] == probe.REQUEST_ID
    assert stored["billed_attempts"] == 1
    assert stored["usage"] == {"completion_tokens": 1, "prompt_tokens": 3, "total_tokens": 4}
