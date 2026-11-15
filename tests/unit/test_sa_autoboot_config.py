"""SA-AUTOBOOT-TASK-1: boot/task config unit tests. Pure offline.

No server, no ports, no real keys, no scheduler touch. The three
``scripts/service/*.ps1`` files are read as TEXT and their pinned constants
/ guards are asserted; the 30s-timeout + fail-closed policy is proven on a
pure-python model of the same policy (no sockets: ``socket.create_connection``
is hard-blocked below).

Covers (per ticket):
  - fixed bind 127.0.0.1:18765, no 0.0.0.0, no --host/--port configurability;
  - probe interval 1s, max 30 attempts (= 30s timeout), fail-closed exit 2;
  - guard loop every 10s with stop-flag check;
  - control sequence close -> install -> ready -> open, one shot each,
    any non-200 stops (fail-closed, no infinite retry);
  - stop flag + logs under system TEMP (never data/**);
  - scheduler: \\SoulOS\\SA_Rem_Primary_Daemon, AtLogOn, current user
    (no SYSTEM), Hidden, Owner-authorization description;
  - hygiene: no Stop-Process/taskkill/kill, no production ports
    (:8000/:8765/:8766/:8767), no chat/completions billing path, no
    embedded secrets.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "scripts" / "service"
BOOT_PS1 = SERVICE_DIR / "start_sa_rem_boot.ps1"
REGISTER_PS1 = SERVICE_DIR / "register_autoboot_task.ps1"
UNREGISTER_PS1 = SERVICE_DIR / "unregister_autoboot_task.ps1"

PRODUCTION_PORTS = (":8000", ":8765", ":8766", ":8767")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("SA-AUTOBOOT-TASK-1: unit tests must not dial out")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _code_lines(path: Path) -> str:
    """Script text minus full-line comments (negative checks ignore prose)."""
    out = []
    for line in _read(path).splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


# -- files exist -----------------------------------------------------------------


def test_service_scripts_exist():
    for path in (BOOT_PS1, REGISTER_PS1, UNREGISTER_PS1):
        assert path.is_file(), path


# -- fixed bind -------------------------------------------------------------------


def test_boot_pins_loopback_bind():
    text = _read(BOOT_PS1)
    code = _code_lines(BOOT_PS1)
    assert "$ThinHost = '127.0.0.1'" in text
    assert "$ThinPort = 18765" in text
    assert "0.0.0.0" not in code  # prose denial lives in comments only
    assert "no 0.0.0.0" in text
    assert "--host" not in code and "--port" not in code


def test_no_production_port_anywhere():
    for path in (BOOT_PS1, REGISTER_PS1, UNREGISTER_PS1):
        text = _read(path)
        for port in PRODUCTION_PORTS:
            if port == ":8765":  # :18765 contains :8765 as substring; match exactly
                assert re.search(r"(?<!\d):8765(?!\d)", text) is None, (path, port)
            else:
                assert port not in text, (path, port)


# -- probe / guard timing ----------------------------------------------------------


def test_probe_timing_constants():
    text = _read(BOOT_PS1)
    assert "$ProbeIntervalSec = 1" in text
    assert "$ProbeMaxAttempts = 30" in text
    assert "$ProbeTimeoutSec = 30" in text
    assert "$GuardIntervalSec = 10" in text


def test_probe_kind_declared_models_static_no_healthz_no_chat():
    text = _read(BOOT_PS1)
    assert "LISTENING+MODELS-STATIC" in text
    assert "/rem/primary/v1/models" in text
    assert "chat/completions" in text  # referenced only as NEVER-send guard
    assert re.search(r"never.*chat/completions|chat/completions.*never", text, re.I) is not None


# -- fail-closed -------------------------------------------------------------------


def test_boot_fails_closed_no_infinite_retry():
    text = _read(BOOT_PS1)
    assert "fail-closed" in text
    assert "exit 2" in text
    assert re.search(r"while\s*\(\s*\$true\s*\)\s*\{[^}]*Send-ControlCommand", text, re.S) is None
    assert "no infinite retry" in text


def test_control_sequence_single_shot_ordered():
    text = _read(BOOT_PS1)
    assert "$ControlSequence = @('close', 'install', 'ready', 'open')" in text
    # any non-200 stops the sequence
    assert re.search(r"if\s*\(\$status\s*-ne\s*200\)", text) is not None


# -- deviation A: TEMP, never data/** ----------------------------------------------


def test_stop_flag_and_logs_under_temp_never_data():
    for path in (BOOT_PS1, UNREGISTER_PS1):
        text = _read(path)
        code = _code_lines(path)
        assert "sa-rem-boot" in text
        assert "$env:TEMP" in text
        assert ".stop_sa_rem" in text
        assert "data\\service" not in code
        assert "data/" not in code and "data\\" not in code.replace("data\\service", "")
    # register script carries no paths at all: no data, no stop flag needed
    reg_code = _code_lines(REGISTER_PS1)
    assert "data" not in reg_code.lower()
    for path in (BOOT_PS1, REGISTER_PS1, UNREGISTER_PS1):
        assert "data/**" in _read(path)  # the prohibition is documented in prose


# -- hygiene: no kills, no secrets --------------------------------------------------


def test_no_process_kill_capability():
    for path in (BOOT_PS1, REGISTER_PS1, UNREGISTER_PS1):
        code = _code_lines(path).lower()
        assert "stop-process" not in code, path
        assert "taskkill" not in code, path
        bare = [ln for ln in code.splitlines() if re.search(r"(?<![\w-])kill(?![\w-])", ln)]
        # only sanctioned appearances: Stop-ScheduledTask (system reclaim) or
        # "no/never kill" denial prose inside a log string
        assert all(("stop-scheduledtask" in ln or "no kill" in ln or "never" in ln) for ln in bare), (path, bare)


def test_no_embedded_secrets():
    for path in (BOOT_PS1, REGISTER_PS1, UNREGISTER_PS1):
        text = _read(path)
        assert "sk-" not in text
        assert "Bearer " not in text or "Bearer-less" in text or "Bearer GET" in text
        # key material is only ever referenced by variable name if at all
        assert "token_urlsafe" not in text
        assert re.search(r"(?i)api[_-]?key\s*=\s*['\"][^'\"]+['\"]", text) is None


# -- scheduler registration ----------------------------------------------------------


def test_register_task_identity_and_trigger():
    text = _read(REGISTER_PS1)
    assert "SA_Rem_Primary_Daemon" in text
    assert "\\SoulOS\\" in text or "\\SoulOS" in text
    assert "AtLogOn" in text
    assert "SYSTEM" in text  # referenced only as "no SYSTEM elevation"
    assert re.search(r"(?i)no.*SYSTEM|without.*SYSTEM|non-?elevat", text) is not None
    assert "Hidden" in text
    assert "Authorized by Owner (2026-11-15" in text


def test_unregister_disables_before_reclaim():
    text = _read(UNREGISTER_PS1)
    assert "Disable-ScheduledTask" in text
    assert text.index("Disable-ScheduledTask") < text.index("Stop-ScheduledTask")
    assert "Unregister-ScheduledTask" in text


# -- pure-python policy model: 30s timeout fail-closed -------------------------------
#
# Mirrors the ps1 policy exactly: poll up to 30 x 1s; one shot per control
# command; any non-200 (or lost contact) stops with fail-closed. No sockets.


def _boot_policy(probe_results, control_statuses):
    """Return ('open', n_probes) / ('fail-closed', reason)."""
    attempts = 0
    ready = False
    for ok in probe_results[:30]:
        attempts += 1
        if ok:
            ready = True
            break
    if not ready:
        return ("fail-closed", "probe-timeout"), attempts
    for cmd, status in zip(("close", "install", "ready", "open"), control_statuses):
        if status != 200:
            return ("fail-closed", "door-%s-%s" % (cmd, status)), attempts
    return ("open", None), attempts


def test_policy_all_healthy_opens_within_budget():
    (outcome, _), n = _boot_policy([False, False, True], [200, 200, 200, 200])
    assert outcome == "open"
    assert n == 3


def test_policy_30s_timeout_fails_closed():
    (outcome, reason), n = _boot_policy([False] * 60, [200, 200, 200, 200])
    assert outcome == "fail-closed"
    assert reason == "probe-timeout"
    assert n == 30  # capped: 30 x 1s, no attempt 31


def test_policy_zero_probe_results_is_timeout_not_success():
    (outcome, reason), n = _boot_policy([], [200, 200, 200, 200])
    assert outcome == "fail-closed"
    assert reason == "probe-timeout"
    assert n == 0


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_policy_any_non200_door_step_stops_sequence(idx):
    statuses = [200, 200, 200, 200]
    statuses[idx] = 500
    (outcome, reason), _ = _boot_policy([True], statuses)
    assert outcome == "fail-closed"
    assert reason.startswith("door-")


def test_policy_lost_contact_fails_closed():
    (outcome, reason), _ = _boot_policy([True], [200, -1, 200, 200])
    assert outcome == "fail-closed"
    assert reason == "door-install--1"
