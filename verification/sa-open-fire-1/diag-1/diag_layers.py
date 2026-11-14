"""SA-OPEN-FIRE-1 diag-1: layered connectivity diagnosis for live-fire exit 7.

Scope: read-only probes against ollama.com ONLY. No keys, no Authorization
header, no POST to any model endpoint, no .env read, no production writes.
Each layer max 2 attempts, 10 s timeout each. TLS section records only
issuer/subject/validity (desensitized, no chain dump).

Usage (repo .venv Python):
  .venv\\Scripts\\python.exe verification/sa-open-fire-1/diag-1/diag_layers.py
Output:
  verification/sa-open-fire-1/diag-1/evidence.json (layered evidence)
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

HOST = "ollama.com"
PORT = 443
TIMEOUT = 10
MAX_ATTEMPTS = 2

DIAG_DIR = Path(__file__).resolve().parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def probe_proxy() -> dict:
    names = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]
    vars_ = {}
    for n in names:
        v = os.environ.get(n)
        # Name + set/empty only; NEVER record the value (may embed credentials).
        vars_[n] = {"present": v is not None, "empty": (v == "") if v is not None else None}
    return {"time_utc": _utc_now(), "target": "process env (names only)", "vars": vars_}


def probe_dns() -> dict:
    attempts = []
    for i in range(1, MAX_ATTEMPTS + 1):
        t0 = time.monotonic()
        try:
            infos = socket.getaddrinfo(HOST, PORT, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            addrs = sorted({info[4][0] for info in infos})
            fams = sorted({str(info[0]) for info in infos})
            attempts.append({"n": i, "ok": True, "elapsed_ms": dt_ms,
                             "families": fams, "addresses": addrs})
            break  # success: stop early (max 2, point reached)
        except socket.gaierror as e:
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            attempts.append({"n": i, "ok": False, "elapsed_ms": dt_ms,
                             "errno": e.errno, "error": str(e)[:200]})
    ok = any(a["ok"] for a in attempts)
    return {"time_utc": _utc_now(), "target": HOST, "attempts": attempts,
            "verdict": "PASS" if ok else "FAIL"}


def probe_tcp() -> dict:
    attempts = []
    for i in range(1, MAX_ATTEMPTS + 1):
        t0 = time.monotonic()
        try:
            s = socket.create_connection((HOST, PORT), timeout=TIMEOUT)
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            peer = s.getpeername()
            s.close()
            attempts.append({"n": i, "ok": True, "elapsed_ms": dt_ms,
                             "peer": [str(peer[0]), int(peer[1])]})
            break
        except (socket.timeout, TimeoutError) as e:
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            attempts.append({"n": i, "ok": False, "elapsed_ms": dt_ms,
                             "error": "timeout: %s" % str(e)[:200]})
        except OSError as e:
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            attempts.append({"n": i, "ok": False, "elapsed_ms": dt_ms,
                             "errno": e.errno, "error": str(e)[:200]})
    ok = any(a["ok"] for a in attempts)
    return {"time_utc": _utc_now(), "target": "%s:%d" % (HOST, PORT),
            "timeout_s": TIMEOUT, "attempts": attempts,
            "verdict": "PASS" if ok else "FAIL"}


def _name_to_str(name) -> str:
    # cert issuer/subject are tuples of RDN sequences; render compactly.
    try:
        parts = []
        for rdn in name:
            for key, val in rdn:
                parts.append("%s=%s" % (key, val))
        return ",".join(parts)[:300]
    except Exception:
        return str(name)[:300]


def probe_tls() -> dict:
    attempts = []
    for i in range(1, MAX_ATTEMPTS + 1):
        t0 = time.monotonic()
        try:
            raw = socket.create_connection((HOST, PORT), timeout=TIMEOUT)
            ctx = ssl.create_default_context()
            # Hostname check + SNI on; handshake only, no HTTP, no app data.
            tls = ctx.wrap_socket(raw, server_hostname=HOST)
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            cert = tls.getpeercert()
            version = tls.version()
            cipher = tls.cipher()  # (name, version, bits)
            tls.close()
            not_after = cert.get("notAfter", "?") if cert else "?"
            not_before = cert.get("notBefore", "?") if cert else "?"
            attempts.append({"n": i, "ok": True, "elapsed_ms": dt_ms,
                             "tls_version": version,
                             "cipher": {"name": cipher[0], "bits": cipher[2]} if cipher else None,
                             "issuer": _name_to_str(cert.get("issuer", ())) if cert else "?",
                             "subject": _name_to_str(cert.get("subject", ())) if cert else "?",
                             "not_before": not_before, "not_after": not_after})
            break
        except (socket.timeout, TimeoutError) as e:
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            attempts.append({"n": i, "ok": False, "elapsed_ms": dt_ms,
                             "error": "timeout: %s" % str(e)[:200]})
        except ssl.SSLError as e:
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            attempts.append({"n": i, "ok": False, "elapsed_ms": dt_ms,
                             "error": "ssl: %s" % str(e)[:300]})
        except OSError as e:
            dt_ms = round((time.monotonic() - t0) * 1000, 1)
            attempts.append({"n": i, "ok": False, "elapsed_ms": dt_ms,
                             "errno": e.errno, "error": str(e)[:200]})
    ok = any(a["ok"] for a in attempts)
    return {"time_utc": _utc_now(), "target": "%s:%d (SNI=%s)" % (HOST, PORT, HOST),
            "timeout_s": TIMEOUT, "attempts": attempts,
            "verdict": "PASS" if ok else "FAIL"}


def main() -> int:
    started = _utc_now()
    proxy = probe_proxy()
    dns = probe_dns()
    tcp = probe_tcp()
    tls = probe_tls() if tcp["verdict"] == "PASS" else {
        "time_utc": _utc_now(), "target": "%s:%d" % (HOST, PORT),
        "attempts": [], "verdict": "SKIPPED",
        "reason": "TCP FAIL -> TLS not attempted (layered stop)"}
    excluded, unlocated = [], []
    if dns["verdict"] == "PASS":
        excluded.append("DNS resolution")
    else:
        unlocated.append("DNS (FAIL or inconclusive)")
    if tcp["verdict"] == "PASS":
        excluded.append("TCP connect :443")
    elif dns["verdict"] == "PASS":
        unlocated.append("TCP connect :443 (DNS ok but connect failed/hung)")
    else:
        unlocated.append("TCP connect :443 (DNS also failed; layer entangled)")
    if tls.get("verdict") == "PASS":
        excluded.append("TLS handshake+SNI+chain-validity")
    elif tls.get("verdict") == "SKIPPED":
        unlocated.append("TLS (skipped: TCP not established)")
    else:
        unlocated.append("TLS handshake/SNI/chain")
    # Hard truth: handshake success != inference usable; no HTTP was spoken.
    evidence = {
        "diag": "SA-OPEN-FIRE-1 diag-1",
        "started_utc": started,
        "finished_utc": _utc_now(),
        "host": HOST, "port": PORT,
        "interpreter": "repo .venv Python",
        "guarantees": {
            "no_key": True, "no_authorization_header": True,
            "no_model_endpoint_post": True, "no_dotenv_read": True,
            "no_production_write": True, "max_attempts_per_layer": MAX_ATTEMPTS,
            "timeout_s": TIMEOUT, "no_http_spoken": True,
        },
        "layers": {"proxy": proxy, "dns": dns, "tcp": tcp, "tls": tls},
        "excluded": excluded,
        "unlocated": unlocated,
        "notes": [
            "TLS PASS (if observed) means handshake+SNI+chain-validity only; "
            "it does NOT mean model inference is usable (no HTTP was spoken).",
            "Proxy section records names + set/empty only; values never recorded.",
        ],
    }
    out = DIAG_DIR / "evidence.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "proxy": proxy["vars"],
        "dns": dns["verdict"], "dns_attempts": dns["attempts"],
        "tcp": tcp["verdict"], "tcp_attempts": tcp["attempts"],
        "tls": tls["verdict"], "tls_attempts": tls["attempts"],
        "excluded": excluded, "unlocated": unlocated,
        "evidence": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
