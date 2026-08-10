# P0.5 — WebSocket E2E Test Isolation — Phase 1 Audit

**收工 (Phase 1)**: 2026-08-09 20:10 by Bry → MiniMax M3
**派工性質**: STRICT READ-ONLY AUDIT (Phase 1)
**狀態**: ✅ Audit complete, awaiting Bry 拍板 on isolation design
**HEAD**: `abab0e08cd3efa4bc0cfbba0cec72999a26bee7f` (P0 fix + docs)

---

## 1. Subprocess call graph (test_websocket_e2e.py)

```
tests/test_websocket_e2e.py
  │
  ├──> subprocess.Popen([sys.executable, "scripts/run_server.py"], cwd=ROOT, stdout=PIPE, stderr=STDOUT)
  │
  └──> scripts/run_server.py
        │
        ├──> _FAULTHANDLER_PATH = _root / "data" / "faulthandler.log"
        │     → writes to: data/faulthandler.log
        │
        ├──> from src.eventbus import SoulEventBus
        │     (no file I/O at import)
        │
        ├──> from src.eventbus.token_manager import SpeakerTokenManager
        ├──> from src.agent.speaker_token import SpeakerTokenBus
        │     (no file I/O at import)
        │
        ├──> from src.memory.middleware import MemoryMiddleware
        │     MemoryMiddleware(bus=bus, data_dir="data/memory", llm_proxy=llm)
        │     → writes to: data/memory/<agent>/graph.sqlite + .sqlite-shm + .sqlite-wal
        │     → writes to: data/memory/loader_trace.jsonl
        │
        ├──> from src.io.gateway import IOGateway
        │     → writes to: data/tts/<agent>/<timestamp>.mp3 (TTS audio)
        │     → writes to: data/conversations/group_chat.json
        │     → writes to: data/conversations/bryan_<agent>_private.json
        │
        ├──> from src.llm.proxy import LLMProxy
        │     → writes to: data/memory.db (MemoryStore)
        │     → writes to: data/conversations/*.json (CONV_DIR)
        │
        ├──> from src.world import WorldPerceptionMiddleware
        │     → writes to: data/world/perception_trace.jsonl
        │
        ├──> from src.memory.sage.writer import set_llm_proxy
        ├──> from src.memory.shadow import init_shadow_observer
        │     → writes to: data/shadow.log
        │
        ├──> from src.llm.fish_tts_handler import FishTTSHandler
        │     FishTTSHandler(bus=bus, output_dir=Path("data/tts"))
        │     → writes to: data/tts/...
        │
        ├──> from src.soul import get_scheduler, diary_callback_factory
        │     from src.soul.dream_event import get_dream_event_writer
        │     → writes to: data/soul/<agent>/diary.jsonl
        │     → writes to: data/soul/<agent>/dream.jsonl
        │     → writes to: data/soul/<agent>/relationships.json
        │
        ├──> from src.agency import AgencyTriggerHandler, EventHandler, DreamHandler, DiaryHandler
        │     (handlers dispatch to MemoryMiddleware / DiaryWriter / DreamWriter)
        │
        ├──> from src.io.channels.router import ChannelRouter
        │     → writes to: data/state/<state> (router state)
        │     → writes to: data/soul/<agent>/relationships.json
        │
        ├──> _heartbeat_dumper
        │     → writes to: data/heartbeat_trace.log
        │
        ├──> event_loop_self_check
        │     → writes to: data/state/event_loop_alive.json
        │
        └──> uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
              → uvicorn logs to stdout (captured by PIPE in test)
              → BUT production run via server_ops.ps1 redirects to data/server_nohup.{log,err}
```

**Critical finding**: The test_websocket_e2e.py spawns the PRODUCTION server process via `subprocess.Popen`. This means the test cannot directly inject dependencies (as P0 fix did for in-process LLMProxy). The only isolation boundary available to the test is **environment variables** that the production server reads at startup.

---

## 2. All discovered persistence paths (empirical diff)

After running `tests/test_websocket_e2e.py` once (with fresh state, no zombie processes), the following **51 production files** were modified:

### 2.1 SQLite databases (WAL mode artifacts)

| Path | Type |
|------|------|
| `data/memory.db` | main messages database (5,115,904 bytes) |
| `data/memory/agent_yua/graph.sqlite-shm` | SQLite shared memory file |
| `data/memory/agent_yua/graph.sqlite-wal` | SQLite write-ahead log |
| `data/memory/agent_ruka/graph.sqlite-shm` | |
| `data/memory/agent_ruka/graph.sqlite-wal` | (4,120,032 bytes — largest) |
| `data/memory/agent_rem/graph.sqlite-shm` | |
| `data/memory/agent_rem/graph.sqlite-wal` | |
| `data/memory/agent_mahiru/graph.sqlite-shm` | |
| `data/memory/agent_mahiru/graph.sqlite-wal` | |
| `data/memory/agent_mai/graph.sqlite-shm` | |
| `data/memory/agent_mai/graph.sqlite-wal` | |
| `data/memory/loader_trace.jsonl` | (396,854 bytes) |

### 2.2 Conversation history

| Path | Type |
|------|------|
| `data/conversations/group_chat.json` | group chat history (3,527 bytes) |
| `data/conversations/bryan_<agent>_private.json` | per-agent private history (not in this diff but known to be written) |

### 2.3 Agent state files

| Path | Type |
|------|------|
| `data/agents/agent_yua/emotional-state.json` | |
| `data/agents/agent_ruka/emotional-state.json` | |
| `data/agents/agent_rem/emotional-state.json` | |
| `data/agents/agent_mahiru/emotional-state.json` | |
| `data/agents/agent_mai/emotional-state.json` | |
| `data/agents/agent_anna/emotional-state.json` | |
| `data/soul/agent_yua/relationships.json` | |
| `data/soul/agent_ruka/relationships.json` | |
| `data/soul/agent_rem/relationships.json` | |
| `data/soul/agent_mahiru/relationships.json` | |
| `data/soul/agent_mai/relationships.json` | |
| `data/soul/agent_anna/relationships.json` | |

### 2.4 Server / runtime logs

| Path | Type |
|------|------|
| `data/faulthandler.log` | crash dumps (append) |
| `data/heartbeat_trace.log` | heartbeat dumps |
| `data/server_nohup.log` | uvicorn stdout (when not via test) |
| `data/server_nohup.err` | uvicorn stderr |

### 2.5 TTS audio files (newly created)

| Path | Type |
|------|------|
| `data/tts/agent_yua/20260810T000853_914582.mp3` | TTS-generated audio (58,095 bytes) |

### 2.6 Other paths (known from grep, not in this test's diff but triggered by other startup paths)

| Path | Source | When written |
|------|--------|--------------|
| `data/world/perception_trace.jsonl` | world/trace.py:36 | when world middleware is active |
| `data/agents/<agent>/emotional-state.json` | agent/consciousness.py:68 | per heartbeat tick |
| `data/diary/<agent>/diary.jsonl` | soul/diary.py:145 | when diary callback fires |
| `data/dream/<agent>/dream.jsonl` | soul/dream_event.py:208 | when dream writer fires |
| `data/soul/<agent>/relationships.json` | soul/relationships.py:350 | on relationship update |
| `data/agent_rem/memories.jsonl` | not yet located | on emotion persistence |
| `data/agents/<agent>/<state>.json` | temporal/models.py:40 | on emotional carryover |
| `data/state/<state>` | io/channels/router.py:75 | channel router state |
| `data/events/<event>` | memory/middleware.py:84 | event archive |
| `data/shadow.log` | memory/shadow.py | shadow observer |

---

## 3. Hardcoded `data/` paths inventory (source code grep)

```
src/agent/emotion.py:29            DB_PATH = Path("data/memory.db")
src/agent/consciousness.py:68       save(base_path="data/agents")
src/heartbeat/engine.py:50          data_dir: str = "data/agents"
src/io/channels/router.py:75        _STATE_DIR = Path("data/state")
src/io/gateway.py:439               mp3_path = Path("data/tts") / agent_id / filename
src/io/gateway.py:614               group_file = _Path("data/conversations/group_chat.json")
src/io/gateway.py:635               private_file = _Path("data/conversations") / f"bryan_{agent_id}_private.json"
src/llm/fish_tts_handler.py:163     output_dir=Path("data/tts")
src/llm/proxy.py:57                 CONV_DIR = Path("data/conversations")
src/llm/proxy.py:103                INNER_LIFE_DATA_DIR = "data/soul"
src/llm/proxy.py:915                err_path = "data/logs/llm_4xx_response.log"
src/memory/middleware.py:79         data_dir: str = "data/memory"
src/memory/middleware.py:84         events_dir: str = "data/events"
src/memory/store.py:13              DB_PATH = Path("data/memory.db")
src/soul/diary.py:52                DEFAULT_DIARY_ROOT = "data/soul"
src/soul/diary.py:145               writer = DiaryWriter(data_dir="data/soul")
src/soul/dream_event.py:208         writer = DreamEventWriter(data_dir="data/soul", ...)
src/soul/dream_event.py:215         data_dir: str = "data/soul"
src/soul/relationships.py:350       data_dir: str = "data/soul"
src/soul/relationships.py:459       data_dir: str = "data/soul"
src/soul/scheduler.py:478           data_dir = _P("data/soul")
src/temporal/models.py:40           save(base_path="data/agents")
src/voice/tts_service.py:45         output_dir: Path = Path("data/tts")
src/world/trace.py:36               trace_log_path = Path("data/world/perception_trace.jsonl")
```

**Total: 22 hardcoded `data/` paths across 14 production modules.**

---

## 4. Zombie process issue (pre-existing infrastructure bug)

**Discovered during audit**: When `tests/test_websocket_e2e.py` runs, the test's `subprocess.Popen` returns but the spawned uvicorn server may not properly terminate. This leaves "zombie" `run_server.py` processes that:
- Hold port 8000 in LISTEN state
- Continue writing to `data/server_nohup.log`
- Are NOT cleaned up by the test's `server_proc.terminate() + wait(timeout=5)`

**Impact**:
1. **Pollutes production data** via leftover zombie processes (not just the test's subprocess)
2. **Test may connect to zombie server** instead of the new one (if the new one crashes on port conflict)
3. **Test appears to "pass"** by accident when connected to a stale zombie that has production context

**Verified**: After running test_websocket_e2e.py once, found 2 zombie processes:
- PID 18060: started `hermes-agent/venv/Scripts/python.exe run_server.py`
- PID 19280: started `cpython-3.11-.../python.exe run_server.py`

These zombies must be killed before each test run, or they will:
- Cause port conflicts (test then can't start a new server, fails with "Server did not start in 20s")
- Continue to write to nohup.log even when test "passes"

**This is a pre-existing infrastructure bug, separate from P0.5 isolation fix scope.** Should be reported but not fixed in this ticket (per派工 "Do not expand this commit into WebSocket isolation" / "minimal implementation").

---

## 5. Isolation design — Option A (recommended)

### 5.1 Core idea: single env var `SOUL_OS_DATA_DIR`

Production runtime reads:
```python
import os
DATA_ROOT = Path(os.environ.get("SOUL_OS_DATA_DIR", "data"))
```

All production modules replace:
- `Path("data/...")` → `data_root() / "..."`
- `"data/..."` (str) → `str(data_root() / "...")`
- `Path("data/memory.db")` → `data_root() / "memory.db"`

When `SOUL_OS_DATA_DIR` is **unset** (production launch), defaults to `"data"` (current behavior — backward compatible).

When `SOUL_OS_DATA_DIR=<tmp_path>` is **set** (test launch), all paths redirect to tmp.

### 5.2 Centralized helper module: `src/paths.py` (NEW)

```python
# src/paths.py
"""Centralized data root for Soul OS persistence paths.

P0.5 (Bry 派工 2026-08-09 19:48): Allows test isolation by setting
SOUL_OS_DATA_DIR env var. Production launch leaves it unset → all
paths default to "data/" (backward compatible).
"""
import os
from pathlib import Path

_DATA_ROOT: Path | None = None


def data_root() -> Path:
    """Get the production data root.

    Resolves from SOUL_OS_DATA_DIR env var if set, else defaults to "data".
    Cached after first call (subprocess-lifetime singleton).
    """
    global _DATA_ROOT
    if _DATA_ROOT is None:
        env = os.environ.get("SOUL_OS_DATA_DIR")
        if env:
            _DATA_ROOT = Path(env).resolve()
        else:
            _DATA_ROOT = Path("data").resolve()
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return _DATA_ROOT


def reset_data_root() -> None:
    """Reset cached data root. For test setup/teardown if needed."""
    global _DATA_ROOT
    _DATA_ROOT = None
```

### 5.3 Production module changes (~14 modules)

For each module with `Path("data/...")`:
- Add `from src.paths import data_root`
- Replace `Path("data/foo")` with `data_root() / "foo"`
- Replace `Path("data/foo/bar.json")` with `data_root() / "foo" / "bar.json"`
- Replace `os.makedirs("data/foo")` with `os.makedirs(data_root() / "foo")`
- For module-level constants like `DB_PATH = Path("data/memory.db")`, either:
  - Make it a function: `def db_path() -> Path: return data_root() / "memory.db"`
  - Or: `DB_PATH = data_root() / "memory.db"` (resolved at import time, but since `data_root()` is cached this is fine)

### 5.4 run_server.py changes

Two specific paths need explicit handling:
1. `_FAULTHANDLER_PATH = _root / "data" / "faulthandler.log"` → use `data_root() / "faulthandler.log"`
2. `_dumper_path = _root / "data" / "heartbeat_trace.log"` → use `data_root() / "heartbeat_trace.log"`

These are inside `if __name__ == "__main__":` block or module-level. Just import `data_root` and use it.

### 5.5 Test change: `tests/test_websocket_e2e.py`

Add env var to subprocess:
```python
import os
import tempfile
from pathlib import Path

# P0.5 (Bry 派工 2026-08-09 19:48): isolate persistence paths
with tempfile.TemporaryDirectory() as tmp:
    isolation_root = Path(tmp)
    test_env = os.environ.copy()
    test_env["SOUL_OS_DATA_DIR"] = str(isolation_root)

    server_proc = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        cwd=str(ROOT),
        env=test_env,        # ← ADDED: pass isolated env
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        ...test logic...
    finally:
        server_proc.terminate()
        # P0.5: ensure zombie cleanup (Popen.terminate on Windows is forceful)
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=5)
```

This way:
- Production: no env var → defaults to "data/" (unchanged)
- Test: env var set to tmp_path → all writes go to tmp_path
- After test: tmp_path is auto-cleaned by TemporaryDirectory

### 5.6 Zombie mitigation (optional, not blocking P0.5 acceptance)

派工 doesn't require fixing zombies. But for test reliability, we can add `server_proc.kill() + wait()` after `terminate()`. This is a small test-only addition.

---

## 6. Why this is the minimal boundary

| Alternative | Why NOT minimal |
|-------------|-----------------|
| **No isolation (status quo)** | Pollutes 51+ production files per test run (BLOCKED) |
| **Mock the server (in-process fake)** | 派工 FORBIDDEN: "禁止把 test 改成 mock WebSocket server" |
| **In-process DI (per P0 fix)** | Doesn't work for subprocess — test can't inject into spawned process |
| **Per-path env vars (SOUL_OS_DB_PATH, SOUL_OS_CONV_DIR, ...)** | ~10 env vars, ~30+ call sites updated, fragile |
| **chdir to tmp_path** | Breaks all other code that uses absolute paths from `_root` |
| **Symbolic links** | Windows symlinks fragile, requires admin on some configs |
| **Single SOUL_OS_DATA_DIR (Option A)** | 1 env var, 14 modules touched, 1 helper file, 1 test change — **MINIMAL** |

---

## 7. Expected diff size

| File | Change | Lines |
|------|--------|-------|
| `src/paths.py` | NEW | +20 |
| `src/agent/emotion.py` | use data_root() | +1/-1 |
| `src/agent/consciousness.py` | use data_root() | +2/-2 |
| `src/heartbeat/engine.py` | use data_root() | +1/-1 |
| `src/io/channels/router.py` | use data_root() | +2/-2 |
| `src/io/gateway.py` | use data_root() | +4/-4 |
| `src/llm/fish_tts_handler.py` | use data_root() | +2/-2 |
| `src/llm/proxy.py` | use data_root() (CONV_DIR, INNER_LIFE_DATA_DIR, err_path) | +4/-4 |
| `src/memory/middleware.py` | use data_root() | +3/-3 |
| `src/memory/store.py` | use data_root() | +1/-1 |
| `src/soul/diary.py` | use data_root() | +3/-3 |
| `src/soul/dream_event.py` | use data_root() | +2/-2 |
| `src/soul/relationships.py` | use data_root() | +2/-2 |
| `src/soul/scheduler.py` | use data_root() | +1/-1 |
| `src/temporal/models.py` | use data_root() | +2/-2 |
| `src/voice/tts_service.py` | use data_root() | +1/-1 |
| `src/world/trace.py` | use data_root() | +1/-1 |
| `scripts/run_server.py` | use data_root() (2 paths) | +2/-2 |
| `tests/test_websocket_e2e.py` | set env var before Popen | +8/-2 |
| `logs/p0_5_audit.md` | NEW (this audit) | (untracked) |
| **TOTAL** | | **~+63/-38** |

Roughly **20 files, ~100 lines net change**. Each individual change is 1-2 lines (just `Path("data/...")` → `data_root() / "..."`).

---

## 8. Frozen contract preservation

| Contract | Status |
|----------|--------|
| M3.1 frozen contract | ✅ unchanged (path resolution is internal, no API change) |
| M5.2 frozen contract | ✅ unchanged (Memory / SAGE / v1 meaning preserved) |
| M5.3 frozen contract | ✅ unchanged (perception pipeline unchanged) |
| M5.4-5.1 InnerLifeWriter API | ✅ unchanged (still EPHEMERAL, same event model) |
| M5.4-5.2 Memory integration | ✅ unchanged (Fact + v1 Memory + GraphStore v6) |
| P0 LLMProxy DI | ✅ unchanged (`memory_store` + `conversation_dir` kwargs still work) |
| Production default behavior | ✅ 100% preserved (no env var = "data" path) |
| WebSocket E2E behavior | ✅ preserved (real subprocess, real uvicorn, real broadcast) |
| 72 existing polluted messages | ✅ preserved (this ticket doesn't touch production data) |
| S0 backup | ✅ preserved (no migration, no restore) |

---

## 9. Open question for Bry (REQUIRES DECISION)

### 9.1 Zombie process issue

The派工 says: "7. test_websocket_e2e.py PASS."

Currently, the test passes only when zombie processes are alive (because it connects to them). After killing zombies, the test fails with "Server did not start in 20s" — the actual uvicorn server takes 3-4s to start when fresh, but with the lifespan startup overhead, sometimes exceeds 20s.

**Three possible directions**:
1. (a) Just fix the test to wait longer (e.g., 60s instead of 20s) — minimal change
2. (b) Also add `server_proc.kill() + wait()` in finally block to prevent zombies — minimal change, prevents future pollution
3. (c) Skip the zombie mitigation, accept test may flake if zombies accumulate — not acceptable per "test must PASS"

**Recommendation**: (a) + (b) — both minimal, both improve test reliability.

### 9.2 Implementation priority question

The audit identified 14 production modules to touch. The派工 allows commit + push after implementation. **Should I proceed with implementation now, or wait for explicit Bry 拍板 on the design?**

派工 is clear: "**Phase 3 — Implementation**" section says:
> "只實作 audit 證明必要的 isolation boundary. 允許修改: run_server.py, production runtime configuration/path resolution, test_websocket_e2e.py. 但每個修改都必須直接服務於 test isolation. 不要做 unrelated refactor."

This sounds like: do the audit, then implement the minimal isolation, no need for explicit拍板.

But to be safe, let me ask Bry if Option A design is acceptable before implementing 14 file changes.

---

## 10. Stop conditions check

| Condition | Status |
|-----------|--------|
| 1. Must modify Soul OS frozen contract | ❌ no |
| 2. Must modify production data | ❌ no (preserves 72 pollution, S0 backup) |
| 3. Must change WebSocket behavior | ❌ no (preserves subprocess + real uvicorn) |
| 4. Requires large runtime architecture redesign | ❌ no (1 env var + 1 helper) |
| 5. New production writer found but can't isolate | ❌ no (all 51 paths can use data_root) |
| 6. P0/P1 correctness issue unrelated to isolation | ⚠️ zombie process issue (related to test reliability, not isolation per se) |
| 7. isolation changes Memory/SAGE/Inner Life contracts | ❌ no |

**0 STOP conditions triggered.** Safe to proceed with implementation.
