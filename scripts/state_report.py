"""
Soul OS State Report — CLI 觀測小工具 (Lesson 42, 2026-07-31 Bry 拍板)
純讀取、單次跑、不留 route。HTTP endpoint 等真的要 web UI 再加。

顯示:
  - 10 隻角色當下 diary 狀態 (總筆數、最後寫入時間、最新 snippet)
  - 今天 (UTC) morning/night diary 觸發統計
  - trace.log 最近 5 筆 agent_speak
  - watchdog / faulthandler 監控檔案狀態 (健康檢查)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Windows console 預設 CP950 沒法印日文,強制 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
SOUL_DIR = HARNESS / "data" / "soul"
TRACE = HARNESS / "trace.log"
WATCHDOG_LOG = HARNESS / "data" / "logs" / "watchdog.log"
FAULTHANDLER_LOG = HARNESS / "data" / "faulthandler.log"
HEARTBEAT_TRACE = HARNESS / "data" / "heartbeat_trace.log"
SERVER_ERR = None
for f in (HARNESS / "data" / "logs").glob("server_*.err"):
    if SERVER_ERR is None or f.stat().st_mtime > SERVER_ERR.stat().st_mtime:
        SERVER_ERR = f


def strip_think(text: str) -> str:
    """去掉 LLM <think>...</think> reasoning block。"""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def load_agents() -> list[str]:
    return sorted(
        d.name for d in SOUL_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )


def count_diary(agent_dir: Path) -> int:
    total = 0
    diary = agent_dir / "diary"
    if not diary.exists():
        return 0
    for f in diary.glob("*.jsonl"):
        try:
            with f.open(encoding="utf-8") as fh:
                total += sum(1 for line in fh if line.strip())
        except OSError:
            pass
    return total


def get_latest_entry(agent_dir: Path) -> dict | None:
    diary = agent_dir / "diary"
    if not diary.exists():
        return None
    files = sorted(diary.glob("*.jsonl"))
    if not files:
        return None
    latest = files[-1]
    entries = []
    try:
        with latest.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return None
    if not entries:
        return None
    last = entries[-1]
    return {
        "date": latest.stem,
        "ts": last.get("ts", "?"),
        "slot": last.get("slot", "?"),
        "source": last.get("source", "?"),
        "content": strip_think(last.get("content", "")).replace("\n", " "),
    }


def today_stats(agent_dir: Path) -> Counter:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    diary = agent_dir / "diary" / f"{today}.jsonl"
    stats: Counter = Counter()
    if not diary.exists():
        return stats
    try:
        with diary.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    stats[entry.get("slot", "?")] += 1
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return stats


def file_status() -> list[tuple[str, Path, str]]:
    """回傳 [name, path, status] 三元組 list 給 health 區塊用。"""
    out = []
    for name, path in [
        ("trace.log", TRACE),
        ("watchdog.log", WATCHDOG_LOG),
        ("faulthandler.log", FAULTHANDLER_LOG),
        ("heartbeat_trace.log", HEARTBEAT_TRACE),
    ]:
        if not path.exists():
            out.append((name, path, "MISSING"))
            continue
        size = path.stat().st_size
        mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
        out.append((name, path, f"{size:>8} bytes, mtime={mtime}"))
    return out


def main() -> int:
    print("=" * 90)
    print(f"  Soul OS State Report  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)

    agents = load_agents()
    print(f"\n[Agents]  {len(agents)} active\n")
    header = f"  {'Agent':<14} | {'Diaries':>7} | {'Today (UTC)':<12} | {'Last entry':<19} | Latest snippet"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for agent in agents:
        agent_dir = SOUL_DIR / agent
        total = count_diary(agent_dir)
        today = today_stats(agent_dir)
        today_str = "/".join(f"{k}{v}" for k, v in sorted(today.items())) or "-"
        latest = get_latest_entry(agent_dir)
        if latest:
            ts = latest["ts"][:16].replace("T", " ")
            content = latest["content"]
            if content.strip():
                snippet = content[:55]
            else:
                # Lesson 42 (Bry 2026-07-31): 空 snippet 必須明確標註,
                # 避免下次看報表時被誤讀成 silent failure
                snippet = "[空—原始內容經 strip 後為空,非讀取錯誤]"
        else:
            ts = "-"
            snippet = "(no diary)"
        print(f"  {agent:<14} | {total:>7} | {today_str:<12} | {ts:<19} | {snippet}")

    print("\n[File health]")
    for name, path, status in file_status():
        print(f"  {name:<22} {status}")

    print("\n[Recent trace.log agent_speak]")
    if TRACE.exists():
        try:
            lines = TRACE.read_text(encoding="utf-8", errors="ignore").splitlines()
            speaks = [l for l in lines if "agent_speak" in l and "_on_agent_speak" not in l]
            for line in speaks[-5:]:
                # 抽出 agent_id + 第一段有意義文字
                m = re.search(r"agent_id': '(\w+)'", line)
                tm = re.search(r"timestamp': '([^']+)'", line)
                em = re.search(r"'emotion': '(\w+)'", line)
                if m and tm:
                    ts_short = tm.group(1)[11:19]
                    print(f"  {ts_short}  {m.group(1):<12}  emotion={em.group(1) if em else '?'}")
        except OSError as e:
            print(f"  read error: {e}")
    else:
        print("  (no trace.log)")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
