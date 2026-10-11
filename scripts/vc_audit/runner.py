"""VC Audit v1 — REST / LIVE A/B runner（Phase 1 harness）.

用法見同目錄 README.md。設計要點：

* **零生產接觸**：啟動前硬性檢查埠不在 {8000, 8765, 8766, 8767}；
  profile 與 log 皆經 launch_instance 隔離。
* **逐回合序列化**：送一個 stimulus，等回合結束（``state=IDLE``）再送下一個。
  因此「run 內第 N 個 [LATENCY] 紀錄」可無歧義對應「第 N 個 stimulus」——
  這是 log 解析能成立的唯一前提，**不可改成並行送測**。
* **缺資料即 null**：parser 抓不到的欄位一律填 ``None``，絕不用 0 代替。
  0 與「沒量到」語意不同，混用會讓統計把缺口當成極快（工單 §10）。

Phase 1 只建 harness，**不執行**。``--dry-run`` 僅做離線驗證，
0 API 呼叫、0 行程啟動。真實 REST/LIVE 對照屬 Phase 2，需 Owner 批准花費。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Windows console 預設 cp950，會把繁中輸出印成亂碼（與 log 檔本身的
# UTF-8 正無關——那是 console codepage 的問題）。JSONL 一律以 UTF-8 落盤。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

FORBIDDEN_PORTS = {8000, 8765, 8766, 8767}
HERE = Path(__file__).resolve().parent
DEFAULT_PROFILE = "clients/voice_companion/profiles/rem.json"

# ── server log 的實際格式（由 clients/voice_companion/web_server.py 印出）──
# 一律用 ASCII 數值欄位比對，不碰中文字段（cp950/UTF-8 對 ASCII 解讀一致）。
RE_LATENCY = re.compile(
    r"\[LATENCY\] asr=(?P<asr>[\d.]+)ms first_audio=(?P<fa>[\d.]+)ms "
    r"total=(?P<total>[\d.]+)ms chars=(?P<chars>\d+)"
)
RE_TTS_ROUND = re.compile(
    r"\[TTS\] round mode=(?P<mode>\w+) input_chars=(?P<in>\d+) reply_chars=(?P<reply>\d+) "
    r"chunks=(?P<chunks>\d+) bytes=(?P<bytes>\d+) first_audio_ms=(?P<fa>[\d.]+) total_ms=(?P<total>[\d.]+)"
)
RE_TTS_ZERO = re.compile(
    r"\[TTS\] round ZERO-AUDIO mode=(?P<mode>\w+) input_chars=(?P<in>\d+) "
    r"reply_chars=(?P<reply>\d+) chunks=(?P<chunks>\d+) bytes=(?P<bytes>\d+) "
    r"first_audio_ms=(?P<fa>[\d.]+) total_ms=(?P<total>[\d.]+)"
)
RE_LLM_EMPTY = re.compile(
    r"\[VC-LLM-EMPTY\] content_pieces=(?P<cp>\d+) max_tokens=(?P<mt>\d+) "
    r"reasoning_seen=(?P<rs>\w+) reasoning_chunks=(?P<rc>\d+) chunks=(?P<ch>\d+) "
    r"finish_reason='(?P<fr>[^']*)' elapsed_ms=(?P<el>[\d.]+)"
)
RE_BARGE = re.compile(r"\[UTT\] barge reason=(?P<reason>[\w:]+) gen:\d+->\d+")
RE_TTS_ERR = re.compile(r"\[TTS\] round FAILED mode=\w+.*last_error=(?P<err>.+)$")

STATE_IDLE = "IDLE"
STATE_ACTIVE = {"THINKING", "SPEAKING"}


def _f(pattern: re.Pattern, text: str, key: str) -> Optional[float]:
    m = pattern.search(text)
    return float(m.group(key)) if m else None


def _i(pattern: re.Pattern, text: str, key: str) -> Optional[int]:
    m = pattern.search(text)
    return int(m.group(key)) if m else None


def classify_empty_group(finish_reason: Optional[str], reasoning_seen: Optional[bool]) -> Optional[str]:
    """工單 §8 分組。禁止把 E1/E2/E3 合併成單一「token 不夠」結論。"""
    if finish_reason is None:
        return None
    if finish_reason == "length" and reasoning_seen:
        return "E1"
    if finish_reason == "stop" and reasoning_seen:
        return "E3"
    if finish_reason == "stop" and not reasoning_seen:
        return "E4"
    if finish_reason == "length" and not reasoning_seen:
        return "E2"
    return "E5"


def read_log_lines(log_path: Path) -> List[str]:
    """讀取 run 目錄下的 VC log。

    server 以 RotatingFileHandler 持有檔案；Python 的 open() 在 Windows 上
    允許共享讀取（與 .NET File.ReadAllBytes 的預設 FileShare.Read 不同）。
    """
    if not log_path.is_file():
        return []
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def find_log_file(run_dir: Path) -> Optional[Path]:
    log_dir = run_dir / "log"
    if not log_dir.is_dir():
        return None
    cands = sorted(log_dir.glob("vc_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


async def run_turn(ws, text: str, timeout: float) -> Dict[str, Any]:
    """送一個 stimulus，等回合結束。回傳 client 端觀察值。"""
    from aiohttp import WSMsgType

    t0 = time.perf_counter()
    seen_active = False
    first_audio_ms: Optional[float] = None
    turn_end_ms: Optional[float] = None
    error_msg: Optional[str] = None
    timed_out = False

    await ws.send_json({"type": "text", "text": text})

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=max(remaining, 0.1))
        except asyncio.TimeoutError:
            break

        if msg.type == WSMsgType.BINARY:
            if first_audio_ms is None:
                first_audio_ms = (time.perf_counter() - t0) * 1000.0
        elif msg.type == WSMsgType.TEXT:
            try:
                d = json.loads(msg.data)
            except Exception:
                continue
            mtype = d.get("type")
            if mtype == "state":
                st = d.get("state")
                if st in STATE_ACTIVE:
                    seen_active = True
                elif st == STATE_IDLE and seen_active:
                    turn_end_ms = (time.perf_counter() - t0) * 1000.0
                    break
            elif mtype == "error":
                error_msg = d.get("message")
        elif msg.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.CLOSING):
            timed_out = True
            break
        elif msg.type == WSMsgType.ERROR:
            timed_out = True
            break

    if turn_end_ms is None and not timed_out:
        timed_out = True
    return {
        "client_turn_end_ms": turn_end_ms,
        "client_first_audio_ms": first_audio_ms,
        "timeout": timed_out if timed_out else None,
        "ws_error": error_msg,
    }


def parse_turn(lines: List[str], client: Dict[str, Any], arm: str,
               stim: Dict[str, Any], run_id: str, ts_start: str) -> Dict[str, Any]:
    """把單一回合的 log slice 轉成一筆 evidence record。"""
    blob = "\n".join(lines)

    rec: Dict[str, Any] = {
        "arm": arm,
        "stimulus_id": stim["id"],
        "input_text": stim["text"],
        "input_chars": len(stim["text"]),
        "run_id": run_id,
        "ts_start": ts_start,
        # 以下皆預設 None；抓到才覆寫（缺資料 ≠ 0）
        "llm_first_token_ms": None,
        "llm_total_ms": None,
        "first_audio_ms": None,
        "total_turn_ms": None,
        "reply_chars": None,
        "tts_chunks": None,
        "tts_bytes": None,
        "zero_audio": None,
        "empty_llm": None,
        "finish_reason": None,
        "reasoning_seen": None,
        "reasoning_chunks": None,
        "barge_in": None,
        "tts_error": None,
        "empty_llm_group": None,
        "client_turn_end_ms": client.get("client_turn_end_ms"),
        "client_first_audio_ms": client.get("client_first_audio_ms"),
        "timeout": client.get("timeout"),
    }

    lat = [ln for ln in lines if "[LATENCY]" in ln]
    if lat:
        last = lat[-1]
        rec["first_audio_ms"] = _f(RE_LATENCY, last, "fa")
        rec["total_turn_ms"] = _f(RE_LATENCY, last, "total")
        rec["reply_chars"] = _i(RE_LATENCY, last, "chars")

    rounds = [ln for ln in lines if "[TTS] round" in ln]
    if rounds:
        r = rounds[-1]
        if "ZERO-AUDIO" in r:
            rec["zero_audio"] = True
            rec["tts_chunks"] = 0
            rec["tts_bytes"] = _i(RE_TTS_ZERO, r, "bytes")
        else:
            rec["zero_audio"] = False
            rec["tts_chunks"] = _i(RE_TTS_ROUND, r, "chunks")
            rec["tts_bytes"] = _i(RE_TTS_ROUND, r, "bytes")

    emp = [ln for ln in lines if "VC-LLM-EMPTY" in ln]
    if emp:
        last = emp[-1]
        rs = re.search(r"reasoning_seen=(\w+)", last)
        rec["empty_llm"] = True
        rec["reasoning_seen"] = (rs.group(1).lower() == "true") if rs else None
        rec["reasoning_chunks"] = _i(RE_LLM_EMPTY, last, "rc")
        rec["finish_reason"] = re.search(r"finish_reason='([^']*)'", last).group(1)
        rec["empty_llm_group"] = classify_empty_group(rec["finish_reason"], rec["reasoning_seen"])

    if any("[TTS] round FAILED" in ln for ln in lines):
        m = None
        for ln in lines:
            mm = RE_TTS_ERR.search(ln)
            if mm:
                m = mm.group("err").strip()
        rec["tts_error"] = m
    elif rec["zero_audio"] is False:
        rec["tts_error"] = None

    rec["barge_in"] = any(RE_BARGE.search(ln) for ln in lines)

    # llm_first_token_ms 目前無法量測：VC 未記錄 first-token 時戳。
    # 這是 instrumentation gap，Phase 1.5 補（工單 §4/§9）。
    return rec


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.25)
    return False


async def run_arm(args, corpus: Dict[str, Any], order: List[str], run_dir: Path) -> int:
    import aiohttp

    run_id = run_dir.name
    by_id = {s["id"]: s for s in corpus["stimuli"]}
    host, port = "127.0.0.1", args.port

    python_exe = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.is_file():
        print(f"[ERROR] 找不到 {python_exe}，請改用 --python 指定直譯器", file=sys.stderr)
        return 2

    stdout_path = run_dir / "instance_stdout.log"
    cmd = [
        str(python_exe), "-u", "-m", "scripts.vc_audit.launch_instance",
        "--profile", args.profile,
        "--port", str(port),
        "--tts", args.arm,
        "--run-dir", str(run_dir),
    ]
    print(f"[SPAWN] {' '.join(cmd)}")
    with open(stdout_path, "wb") as out:
        proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=out, stderr=subprocess.STDOUT)

    if not wait_for_port(host, port, args.boot_timeout):
        print(f"[ERROR] instance 未在 {args.boot_timeout}s 內 listen {port}；"
              f"見 {stdout_path}", file=sys.stderr)
        proc.terminate()
        return 3

    out_path = run_dir / f"evidence_{args.arm}.jsonl"
    written = 0
    try:
        async with aiohttp.ClientSession() as sess:
            url = f"http://{host}:{port}/ws"
            async with sess.ws_connect(url, ssl=False, heartbeat=None) as ws:
                for sid in order:
                    stim = by_id[sid]
                    log_path = find_log_file(run_dir)
                    before = len(read_log_lines(log_path)) if log_path else 0
                    ts_start = datetime.now(timezone.utc).isoformat()
                    client = await run_turn(ws, stim["text"], args.turn_timeout)
                    await asyncio.sleep(args.settle_delay)
                    after_lines = read_log_lines(log_path) if log_path else []
                    rec = parse_turn(after_lines[before:], client, args.arm, stim, run_id, ts_start)
                    with open(out_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                    print(
                        f"[{written:02d}/{len(order)}] {sid} "
                        f"first_audio={rec['first_audio_ms']} "
                        f"total={rec['total_turn_ms']} "
                        f"chunks={rec['tts_chunks']} "
                        f"zero={rec['zero_audio']} timeout={rec['timeout']}"
                    )
    finally:
        # 只終止本 runner 自己 spawn 的子進程；不枚舉、不依名稱殺任何行程。
        if proc.poll() is None:
            print(f"[CLEANUP] terminate self-spawned instance pid={proc.pid}")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print(f"[WARN] pid={proc.pid} 未在 10s 內結束，留給使用者處理", file=sys.stderr)

    print(f"[DONE] {written} records -> {out_path}")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VC Audit v1 REST/LIVE A/B runner")
    ap.add_argument("--arm", choices=["rest", "live"], help="TTS arm")
    ap.add_argument("--port", type=int, default=8790, help="audit instance 埠（預設 8790）")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="來源 profile")
    ap.add_argument("--run-dir", required=True, help="run 輸出目錄")
    ap.add_argument("--order", default="pilot_10", help="corpus.json 的 pilot_orders key")
    ap.add_argument("--turn-timeout", type=float, default=150.0,
                    help="單回合逾時秒數（REST 歷史 max≈29s + LLM 18s，留足餘裕）")
    ap.add_argument("--boot-timeout", type=float, default=60.0, help="等待 listen 的秒數")
    ap.add_argument("--settle-delay", type=float, default=1.0, help="回合結束後等 log 落盤的秒數")
    ap.add_argument("--dry-run", action="store_true", help="僅離線驗證，不啟動任何行程")
    args = ap.parse_args(argv)

    if args.port in FORBIDDEN_PORTS:
        print(f"[REFUSE] port {args.port} 是生產埠，audit runner 不得使用", file=sys.stderr)
        return 2

    corpus_path = HERE / "corpus.json"
    with open(corpus_path, encoding="utf-8") as fh:
        corpus = json.load(fh)
    order = corpus["pilot_orders"][args.order]["order"]

    if args.dry_run:
        print("[DRY-RUN] 不啟動任何行程、不發任何網路請求。")
        # corpus 自檢：expected_input_chars 必須等於實際字元數。
        # 這是 corpus 的唯一真實長度來源；寫錯會讓 A/B 的長度 covariate 失效。
        bad = [
            (s["id"], s["expected_input_chars"], len(s["text"]))
            for s in corpus["stimuli"]
            if s["expected_input_chars"] != len(s["text"])
        ]
        if bad:
            for sid, expected, actual in bad:
                print(f"  [CORPUS MISMATCH] {sid}: expected_input_chars={expected} "
                      f"but len(text)={actual}", file=sys.stderr)
            print("[DRY-RUN] FAILED - corpus 字數不一致", file=sys.stderr)
            return 1
        print("  corpus   = OK (expected_input_chars 與 len(text) 全部一致)")
        print(f"  arm      = {args.arm}")
        print(f"  port     = {args.port}  (production {sorted(FORBIDDEN_PORTS)} 已排除)")
        print(f"  profile  = {args.profile}")
        print(f"  order    = {args.order}")
        print(f"  turns    = {len(order)}")
        for sid in order:
            s = next(x for x in corpus["stimuli"] if x["id"] == sid)
            print(f"    {sid}  {len(s['text']):>2} chars  {s['text']}")
        print(f"  schema   = {HERE / 'evidence.schema.json'}")
        try:
            import jsonschema  # type: ignore
            with open(HERE / "evidence.schema.json", encoding="utf-8") as fh:
                jsonschema.Draft202012Validator.check_schema(json.load(fh))
            print("  schema   = VALID")
        except ImportError:
            print("  schema   = (jsonschema 未安裝，跳過 schema 驗證)")
        print("[DRY-RUN] OK")
        return 0

    if not args.arm:
        print("[ERROR] 實際執行需指定 --arm rest|live", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_dir.name,
        "arm": args.arm,
        "port": args.port,
        "profile": args.profile,
        "order": args.order,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        ).stdout.strip(),
        "python": sys.executable,
    }
    with open(run_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    return asyncio.run(run_arm(args, corpus, order, run_dir))


if __name__ == "__main__":
    raise SystemExit(main())