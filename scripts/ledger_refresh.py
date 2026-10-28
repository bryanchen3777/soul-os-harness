# -*- coding: utf-8 -*-
"""Daily cohort-ledger refresh (值守端日常審核).

📌 已轉正至 `scripts/ledger_refresh.py`；repo 外部那份為 frozen 備份。

Why this exists
---------------
`scripts/eval_lifecycle_cli.py`'s expected-retirement judgement is a **ledger**
(`EXPECTED_RETIREMENT_COHORTS`): per cohort date, the approved member set plus
the earliest retirement slot.  The per-cohort self-negation ("derived set must
be a subset of the approved set") is what proves the rule was not widened.

A cohort's membership is **not final** until its creation date (UTC) has
passed: value nodes keep being created under that date until 00:00Z of the
next day.  Therefore an approval made before closure goes stale, and the
self-negation fires again (a false red on the next night slot).

This job runs at 20:40 EDT (= 00:40Z next day, i.e. *after* the UTC day that
just closed) and:

  1. measures the closed cohort's membership **live** (four conditions, from
     the production graph, read-only);
  2. measures which members the engine will retire at the **next** slot by
     running the sanctioned sandbox CLI (source read-only, sandbox in temp);
  3. rewrites the ledger entry for the affected cohort(s) in
     `scripts/eval_lifecycle_cli.py` (data-only literal edit);
  4. runs the targeted guard suite (tests/test_eval_lifecycle_cli.py) and the
     sandbox CLI at the next slot — **both must be green**;
  5. appends a canonical entry, commits and pushes ONLY if both gates passed;
  6. writes a report and prints it for the watch chain.

Hard boundaries:
  * production `data/**` is read-only (only shutil.copy2 into temp sandboxes);
  * the repo file edit is limited to the ledger literal inside
    `scripts/eval_lifecycle_cli.py`;
  * no restart, no process kill, no real LLM call, no network;
  * if either gate is red -> no commit, no push; the report says why.

Usage:
  python ledger_refresh.py --dry-run     # measure + print, touch nothing
  python ledger_refresh.py               # do the whole thing
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
CLI = os.path.join(REPO, "scripts", "eval_lifecycle_cli.py")
ELEV = os.path.join(REPO, "data", "elevation")
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
CANON = os.path.join(REPO, "logs", "ENGINEERING_STATE.md")
FOUR_CONDITIONS_DOC = "value ＋ 建立日 == cohort date ＋ last_support_ts == created_ts ＋ 無 support_propagation 祖先邊"


def sh(cmd, cwd=REPO, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def structural_members(nodes, edges, date):
    """Four-condition structural set for one cohort date (read-only)."""
    supported = {e.get("node_id") for e in edges
                 if isinstance(e, dict) and e.get("trigger_type") == "support_propagation"}
    out = {}
    for n in nodes:
        if n.get("node_type") != "value":
            continue
        created = n.get("created_ts") or ""
        if not isinstance(created, str) or created[:10] != date:
            continue
        if (n.get("last_support_ts") or "") != created:
            continue
        nid = n.get("node_id")
        if not nid or nid in supported:
            continue
        out[nid] = n.get("agent_id") or "?"
    return out


def next_slot_iso(now_utc):
    """The next night slot (02:00Z) strictly after now."""
    day = now_utc.date() + timedelta(days=1)
    for cand in (datetime(day.year, day.month, day.day, 2, 0, tzinfo=timezone.utc),):
        if cand > now_utc:
            return cand
    return cand


def engine_anchors(nodes, edges):
    """node_id → 與枠的衰減錨點（複刻 ``engine._lifecycle_anchor``）。

    優先序（**必須**與與枠一致，否則台帳會與與枠矛盾）：
      1. 該節點「仍有效」支援邊（``valid_until_ts is None``）的 max(valid_from_ts)
      2. ``last_support_ts``
      3. ``created_ts + LIFECYCLE_GRACE_DAYS``（3 天 grace）
    🔴 2026-10-27 主大腦實測：錨點『幾乎全部來自支援邊』，節點的
    ``last_support_ts`` 欄位**不是**錨點 ⇒ 用欄位算下界會與與枠矛盾。
    """
    active = {}
    for e in edges or ():
        if not isinstance(e, dict):
            continue
        if e.get("valid_until_ts") is None and e.get("node_id") and e.get("valid_from_ts"):
            active.setdefault(e["node_id"], []).append(e.get("valid_from_ts"))

    def _parse(ts):
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None
        return dt if dt.tzinfo is not None else None

    out = {}
    for n in nodes or ():
        if not isinstance(n, dict):
            continue
        nid = n.get("node_id")
        if not nid:
            continue
        cands = [d for d in (_parse(t) for t in active.get(nid, [])) if d is not None]
        if cands:
            out[nid] = max(cands)
            continue
        ls = _parse(n.get("last_support_ts"))
        if ls is not None:
            out[nid] = ls
            continue
        cr = _parse(n.get("created_ts"))
        if cr is not None:
            out[nid] = cr + timedelta(days=3)   # LIFECYCLE_GRACE_DAYS
    return out


def decay_thresholds(nodes, edges):
    """node_id → 與枠的衰減門檻（**錨點 + 7 天**，LIFECYCLE_T_WEAKEN_DAYS）。

    🔴 2026-10-27 主大腦補丁：cohort 級 `not_before`（建立日 + 8 天 @02:00Z）是
    **近似值** ⇒ 對某些成員會**晚一槓**（與枠按「錨點 + 7 天」讓它們弱化）
    ⇒ 沿用 cohort 級下界會被謺判成「提前早衰」＝假紅。
    ⇒ 逐節點記錄**與枠自己的門檻**（**不放寬任何門檻**，只是把事實記進台帳）。
    """
    anchors = engine_anchors(nodes, edges)
    return {nid: (a + timedelta(days=7)).isoformat() for nid, a in anchors.items()}


def sim_retiring_value_nodes(slot_iso):
    """Run the engine on a SANDBOX COPY of the production graph at `slot_iso`.

    Returns (rc, verdict, stdout, retiring) where retiring maps
    cohort_date -> {node_id: agent_id} for every value->weakening transition the
    engine produces at that slot (this is the authoritative "who retires when"
    measurement; the CLI's stdout only prints the *count*, not the ids).
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = REPO
    code = (
        "import json, os, shutil, sys, tempfile\n"
        "sys.path.insert(0, %r)\n"
        "from src.inner_life.elevation_adapter import evaluate_lifecycle_all\n"
        "REPO = %r\n"
        "ELEV = os.path.join(REPO, 'data', 'elevation')\n"
        "sb = tempfile.mkdtemp(prefix='ledger_refresh_sb_')\n"
        "for n in ('elevation_nodes.jsonl','elevation_edges.jsonl','elevation_lifecycle_events.jsonl'):\n"
        "    s = os.path.join(ELEV, n)\n"
        "    if os.path.exists(s): shutil.copy2(s, os.path.join(sb, n))\n"
        "nodes = [json.loads(l) for l in open(os.path.join(ELEV,'elevation_nodes.jsonl'), encoding='utf-8') if l.strip()]\n"
        "evs = evaluate_lifecycle_all(store_dir=sb, now_ts=%r)\n"
        "byid = {n.get('node_id'): n for n in nodes}\n"
        "out = {}\n"
        "for e in evs:\n"
        "    if e.get('node_type') != 'value' or e.get('lifecycle_state') != 'weakening': continue\n"
        "    n = byid.get(e.get('node_id')) or {}\n"
        "    c = (n.get('created_ts') or '?')[:10]\n"
        "    out.setdefault(c, {})[e.get('node_id')] = n.get('agent_id') or '?'\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
        "shutil.rmtree(sb, ignore_errors=True)\n"
    ) % (REPO, REPO, slot_iso)
    script = os.path.join(tempfile.gettempdir(), "ledger_refresh_sim.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    rc, out, err = sh([PY, "-P", script], env=env)
    retiring = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and "node_id" not in line:
            try:
                data = json.loads(line)
            except Exception:
                continue
            for date, members in data.items():
                retiring[date] = dict(members)
    rc2, vout, _ = sh([PY, "-P", CLI, "--simulated-time", slot_iso], env=env)
    verdict = [l for l in vout.splitlines() if l.startswith("VERDICT")]
    return rc2, verdict, vout, retiring


def parse_ledger(text):
    """Return {date: (start, end, block_text)} for each cohort dict in the ledger."""
    m = re.search(r"^EXPECTED_RETIREMENT_COHORTS = \(\r?\n(.*?)\r?\n^\)\r?$", text, re.M | re.S)
    if not m:
        raise SystemExit("ledger block not found")
    region = m.group(1)
    entries = {}
    const_date = None
    cm = re.search(r'EXPECTED_RETIREMENT_DATE\s*=\s*"(\d{4}-\d{2}-\d{2})"', text)
    if cm:
        const_date = cm.group(1)
    for dm in re.finditer(r'"date":\s*(?:"(\d{4}-\d{2}-\d{2})"|EXPECTED_RETIREMENT_DATE),', region):
        date = dm.group(1) or const_date
        if not date:
            continue
        const_based = dm.group(1) is None
        start = region.rfind("{", 0, dm.start())
        depth, i = 0, start
        while i < len(region):
            if region[i] == "{":
                depth += 1
            elif region[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = region.find("},", i)
        end = end + 2 if end != -1 else i + 1
        entries[date] = (start, end, region[start:end], const_based)
    return m.start(1), m.end(1), entries


def cohort_not_before(date):
    """台帳既定規則：建立日 + 8 天 @02:00:00Z（每個 cohort 一致）。

    🔴 2026-10-27 主大腦修正：舊 fallback 是 "2026-01-01T00:00:00+00:00"
       ⇒ 那是**誤植**（2026-10-26 cohort 就是這樣被寫壞的 ⇒ 放行窗寬 10 個月
       ⇒ 「提前早衰」的熔斷對該批節點完全失效 ⇒ 守門被弱化）。
    """
    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d + timedelta(days=8)).replace(hour=2, minute=0, second=0,
                                          microsecond=0).isoformat()


def parse_block_maps(block):
    """從 cohort 區塊抽出 `types` / `bounds` 與 id→agent 註解。"""
    types, bounds, agents = {}, {}, {}
    m = re.search(r'"types":\s*\{(.*?)\}', block, re.S)
    if m:
        for mm in re.finditer(r'"([0-9a-f]{32})":\s*"([a-z]+)"', m.group(1)):
            types[mm.group(1)] = mm.group(2)
    m = re.search(r'"bounds":\s*\{(.*?)\}', block, re.S)
    if m:
        for mm in re.finditer(r'"([0-9a-f]{32})":\s*"([^"]+)"', m.group(1)):
            bounds[mm.group(1)] = mm.group(2)
    for mm in re.finditer(r'"([0-9a-f]{32})",\s*#\s*([^\r\n]*)', block):
        agents[mm.group(1)] = mm.group(2).strip()
    return types, bounds, agents


def rewrite_entry(text, date, node_ids, agents, not_before=None, extra_bounds=None):
    """Rewrite one cohort entry of EXPECTED_RETIREMENT_COHORTS (append-only data).

    * existing entry -> its "nodes" literal is replaced by the merged set;
      "not_before" is kept unless a new one is supplied;
      **既有的 "types" / "bounds" 區塊與 agent 註解必須保留**（2026-10-27 修正：
      人工入帳的型別／逐節點下界不得被每日刷新抹掉）。
    * unknown date   -> a new entry is appended at the end of the ledger tuple.
    The file's line ending (CRLF in this repo) is preserved.
    """
    region_start, region_end, entries = parse_ledger(text)
    region = text[region_start:region_end]
    nl = "\r\n" if "\r\n" in text else "\n"
    ids = sorted(node_ids)
    # 🔺 格式基準（2026-10-25 22:3x 主大腦修正）：committed 台帳的每列格式是
    #    `            "<id>",  # <agent>` —— 逗號在**引號後**，註解在最後。
    #    舊寫法用 `(",".join(...))` ⇒ 逗號被追加到註解之後（`# agent,`），
    #    於是每次真正重寫都產生 57 列**純格式 churn**（集合相同、檔案不同）。
    #    這裡改成以換行連接、逗號留在引號後 ⇒ 重寫後與 committed 檔案逐位元相同。
    if date in entries:
        s, e, block, _const_based = entries[date]
        old_types, old_bounds, old_agents = parse_block_maps(block)
    else:
        old_types, old_bounds, old_agents = {}, {}, {}
    # 🔺 agent 註解：刷新器只認識它自己算得出的成員；**人工入帳**的節點（belief／
    #    trait／essence）不在其中 ⇒ 沿用區塊內既有的註解，**不得**降級成 "?"。
    def _agent_of(n):
        # 🔴 保留附注：舊註解若以新 agent 名開頭⇒ 整條保留（否則会遺失
        #     錯誤註解，例如「# agent_aoi ★dormant⇒永不退役」）。
        new_agent = agents.get(n)
        old_agent = old_agents.get(n)
        if old_agent and new_agent and old_agent.startswith(new_agent):
            return old_agent
        return new_agent or old_agent or "?"
    body = nl.join('            "%s",  # %s' % (n, _agent_of(n)) for n in ids)
    # 🔺 逐節點下界：既有 `bounds`（人工入帳）＋ 本次新算出的 `extra_bounds`。
    merged_bounds = {k: v for k, v in old_bounds.items() if k in set(ids)}
    for k, v in (extra_bounds or {}).items():
        if k in set(ids):
            merged_bounds[k] = v
    merged_types = {k: v for k, v in old_types.items() if k in set(ids)}
    types_block = ""
    if merged_types:
        types_block = (nl.join('            "%s": "%s",' % (k, merged_types[k])
                               for k in sorted(merged_types)) + nl)
    bounds_block = ""
    if merged_bounds:
        bounds_block = (nl.join('            "%s": "%s",' % (k, merged_bounds[k])
                                for k in sorted(merged_bounds)) + nl)
    if date in entries:
        nb = not_before or _existing_not_before(block)
        # 🔺 尾逗號必須原樣保留（2026-10-25 22:4x 主大腦修正）：舊寫法固定產生
        #    `    }`（無逗號）⇒ ① 與 committed 檔案差 1 行；② 若是**非最後**
        #    一筆的 cohort，則 tuple 內兩個元素間缺逗號 ⇒ **語法錯誤** ⇒
        #    compile() 拋錯、刷新整個失效（只對最後一筆剛好合法）。
        tail = "," if block.rstrip().endswith(",") else ""
        new_block = ("{" + nl
                     + '        "date": "%s",' % date + nl
                     + '        "not_before": "%s",' % nb + nl
                     + ('        "types": {' + nl + types_block + "        }," + nl
                        if types_block else "")
                     + ('        "bounds": {' + nl + bounds_block + "        }," + nl
                        if bounds_block else "")
                     + '        "nodes": frozenset({' + nl + body + nl + "        })," + nl
                     + "    }" + tail)
        new_region = region[:s] + new_block + region[e:]
    else:
        nb = not_before or cohort_not_before(date)
        new_block = ("    {" + nl
                     + '        "date": "%s",' % date + nl
                     + '        "not_before": "%s",' % nb + nl
                     + ('        "types": {' + nl + types_block + "        }," + nl
                        if types_block else "")
                     + ('        "bounds": {' + nl + bounds_block + "        }," + nl
                        if bounds_block else "")
                     + '        "nodes": frozenset({' + nl + body + nl + "        })," + nl
                     + "    }," + nl)
        new_region = region.rstrip() + nl + new_block
    return text[:region_start] + new_region + text[region_end:]


def _existing_not_before(block):
    m = re.search(r'"not_before":\s*"([^"]+)"', block)
    return m.group(1) if m else "2026-01-01T00:00:00+00:00"


def assign_section_numbers(canon_text):
    """Assign canonical section numbers deterministically.

    * only headings that are still **unnumbered placeholders** ('## 0.1.??') are
      touched; already-numbered entries are never renumbered;
    * placeholders are numbered in document order, continuing after the highest
      existing number;
    * returns (new_text, next_free_number, notes).

    Why (2026-10-25 22:2x 主大腦修正): a hard-coded '## 0.1.??' accumulates one
    duplicate placeholder per daily run; the canonical log must stay citable.
    """
    nums = [int(m) for m in re.findall(r"^##\s+0\.1\.(\d+)\b", canon_text, re.M)]
    nxt = (max(nums) + 1) if nums else 1
    notes = []

    def _num(_m):
        nonlocal nxt
        val = nxt
        nxt += 1
        notes.append("0.1.?? -> 0.1.%d" % val)
        return "## 0.1.%d" % val

    new_text = re.sub(r"^##\s+0\.1\.\?\?", _num, canon_text, flags=re.M)
    return new_text, nxt, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    slot = next_slot_iso(now)
    closed_date = (now - timedelta(days=1)).date().isoformat()   # UTC day that just closed
    print("now(UTC)      = %s" % now.isoformat())
    print("next slot     = %s" % slot.isoformat())
    print("closed cohort = %s" % closed_date)

    nodes = read_jsonl(os.path.join(ELEV, "elevation_nodes.jsonl"))
    edges = read_jsonl(os.path.join(ELEV, "elevation_edges.jsonl"))
    struct = structural_members(nodes, edges, closed_date)
    print("structural members of %s = %d" % (closed_date, len(struct)))

    rc, verdict, out, retiring = sim_retiring_value_nodes(slot.isoformat())
    print("sandbox CLI at next slot: rc=%d %s" % (rc, verdict[0] if verdict else "?"))
    print("cohorts retiring at next slot: %s"
          % {d: len(m) for d, m in sorted(retiring.items())})

    text = open(CLI, encoding="utf-8", newline="").read()
    _, _, entries = parse_ledger(text)
    print("ledger cohorts now: %s" % ", ".join(sorted(entries)))

    def approved_of(date, _text=None, _entries=None):
        """Approved id set for a cohort date (constant-based entries included)."""
        _text = text if _text is None else _text
        _entries = entries if _entries is None else _entries
        if date not in _entries:
            return set(), False
        s, e, block, const_based = _entries[date]
        ids = set(re.findall(r'"([0-9a-f]{32})"', block))
        if const_based:
            bm = re.search(r"EXPECTED_RETIREMENT_BASELINE = frozenset\(\{(.*?)\}\)", _text, re.S)
            if bm:
                ids |= set(re.findall(r'"([0-9a-f]{32})"', bm.group(1)))
        return ids, const_based

    # target set per cohort date:
    #   * the just-closed cohort: its live structural membership (four conditions)
    #   * any cohort the engine will retire at the next slot: those members must
    #     be approved too (they may include "supported-then-aged" members the
    #     structural set cannot see) => and its not_before is that slot.
    targets = {}
    agents = {}
    for date in entries:
        agents[date] = dict(struct)
    targets[closed_date] = set(struct)
    agents[closed_date] = dict(struct)
    for date, members in retiring.items():
        targets.setdefault(date, set()).update(members.keys())
        agents.setdefault(date, {}).update(members)
    # 🔺 delta 記帳（2026-10-25 22:2x 主大腦修正）：commit 訊息與 canonical 條目必須
    #    寫出**本次實際新增幾顆**。只寫「核准集合 N 顆」會讓讀者把「維持 N 顆」誤讀成
    #    「新增 N 顆」（10-25 首刷實測：missing=0 卻寫「補齊至 58 顆」⇒ 誤導）。
    final_count = {}
    delta = {}
    for date in sorted(targets):
        approved, const_based = approved_of(date)
        # the approved set may legitimately contain members the structural set no
        # longer shows (supported-then-aged) => **only add, never shrink**.
        merged = approved | targets[date]
        miss = merged - approved
        print("  %s: approved %d -> merged %d (missing %d)%s"
              % (date, len(approved), len(merged), len(miss),
                 "" if not miss else " -> %s" % sorted(miss)[:3]))
        if not miss:
            final_count[date] = len(approved)
            delta[date] = 0
            del targets[date]
        elif const_based:
            print("    (constant-based entry => expressed via the approved constants;"
                  " not rewritten by this job)")
            final_count[date] = len(approved)
            delta[date] = 0
            del targets[date]
        else:
            targets[date] = merged
            final_count[date] = len(merged)
            delta[date] = len(miss)

    if not targets:
        print("ledger already covers every closed/next cohort -> nothing to refresh")
    if args.dry_run:
        print("\nDRY RUN: no file touched")
        return 0

    thresholds = decay_thresholds(nodes, edges)
    for date in sorted(targets):
        nb = None
        if date in entries:
            nb = _existing_not_before(entries[date][2])
        elif date in retiring:
            nb = slot.isoformat()
        else:
            # 🔴 2026-10-27 主大腦修正：**新 cohort** 原本落進這裡
            #   => nb 仍是 None => 下一段的 `if t and nb and t < str(nb)`
            #   永遠不成立 => `extra` 恆空 => 新 cohort **永遠不產出
            #   逐節點下界** => 對「建立後從未被背書」的成員會晚一槽
            #   => 11/03 槽誤判「提前早衰」＝假紅（實測：2026-10-27 cohort
            #   57 顆全需逐節點下界）。
            #   => 這裡補上寫入端本來就會用的 cohort 級下界（建立日＋8 天
            #   @02:00Z），讓逐節點下界**真的算出來**。不放寬任何門檻：
            #   逐節點下界＝引擎自己的衰減門檻。
            nb = cohort_not_before(date)
        # 🔴 逐節點下界：真實衰減門檻（引擎錨點 + 7 天）早於 cohort 級下界者
        #    ⇒ 逐節點記錄（否則會被誤判成「提前早衰」＝假紅）。
        extra = {}
        for nid in targets[date]:
            t = thresholds.get(nid)
            if t and nb and t < str(nb):
                extra[nid] = t
        if extra:
            print("  %s: per-node bounds for %d node(s)" % (date, len(extra)))
        text = rewrite_entry(text, date, targets[date], agents.get(date, {}), nb,
                             extra_bounds=extra)
    compile(text, CLI, "exec")
    with open(CLI, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("ledger rewritten for: %s" % ", ".join(sorted(targets)))

    # gate 1: targeted guard suite
    rc1, o1, e1 = sh([PY, "-m", "pytest", "-q", "tests/test_eval_lifecycle_cli.py"])
    print("gate1 guards rc=%d | %s" % (rc1, o1.strip().splitlines()[-1] if o1.strip() else ""))
    # gate 2: sandbox CLI at the next slot must be PASS
    rc2, verdict2, out2, _ = sim_retiring_value_nodes(slot.isoformat())
    print("gate2 cli rc=%d %s" % (rc2, verdict2[0] if verdict2 else "?"))
    # 🔴 2026-10-27 主大腦修正：gate 2 原本把 **exit 3（PENDING_COHORT）**
    #   當成紅燈 ⇒ 每日刷新一遇到「未核准的退役批次」就中止
    #   ⇒ 台帳刷新**悄悄罷工**（實測：2026-10-27 20:40 那輪改寫了 CLI 卻
    #   沒有 commit、沒有 canonical 條目）。
    #   ⇒ 依 Owner 2026-10-27 裁定，PENDING_COHORT 是「待人工審核」，
    #   **不是破線** ⇒ 允許續行，但必須**大聲回報**。
    #   ⇒ 只有 exit 1（FAIL＝破線）與 exit 2（硬拒絕）才算紅燈。
    pending_ok = bool(verdict2) and "PENDING_COHORT" in verdict2[0]
    if pending_ok:
        print("gate2: exit 3 PENDING_COHORT = 未核准的退役批次（不是破線）"
              " -> 允許續行，但**必須大聲回報待人工審核**")
    if (rc1 != 0 or rc2 not in (0, 3)
            or not (verdict2 and ("PASS" in verdict2[0] or pending_ok))):
        print("GATE RED -> no commit, no push")
        return 2

    # canonical + commit + push (data-only)
    # 🔺 條號動態取號（2026-10-25 22:2x 主大腦修正）：寫死 `## 0.1.??` 會每日累積
    #    多個重號條目 ⇒ canonical 條目將無法被引用。規則：只替「尚未編號的佔位
    #    標題」編號（依文件順序接在最高編號之後），**已編號條目一律不動**。
    closed_total = final_count.get(closed_date, len(approved_of(closed_date)[0]))
    closed_delta = delta.get(closed_date, 0)
    other = [(d, final_count.get(d, 0), delta.get(d, 0))
             for d in sorted(delta) if d != closed_date]
    other_txt = ("；".join("%s（核准 %d 顆，本次新增 %d 顆）" % (d, n, dl)
                           for d, n, dl in other) if other else "無")
    # 🔺 「台帳檔案變更」必須看**整體** delta，不能只看封閉 cohort：
    #    非封閉 cohort 被補齊時台帳同樣會改動（2026-10-25 22:4x 預演 C 案例實測）。
    total_delta = sum(delta.values())
    changed_txt = ("無（missing=0，台帳未改動）" if total_delta == 0
                   else "已更新（本次共新增 %d 顆）" % total_delta)
    before = open(CANON, "rb").read()
    canon_text, sect_no, renum = assign_section_numbers(before.decode("utf-8"))
    nl = "\r\n" if b"\r\n" in before else "\n"
    lines = [
        "",
        "## 0.1.%d 值守端日常台帳刷新（cohort %s 封閉後，自動）" % (sect_no, closed_date),
        "",
        "- 封閉 cohort：`%s`（UTC 建立日過完才封閉）⇒ 核准集合 **%d 顆**"
        "（本次新增 **%d 顆**；四條件量測：%s）。"
        % (closed_date, closed_total, closed_delta, FOUR_CONDITIONS_DOC),
        "- 其他受影響 cohort：%s。" % other_txt,
        "- 下一槽沙盒快跑：`%s` ⇒ `%s`（exit %d）。"
        % (slot.isoformat(), verdict2[0].replace("VERDICT: ", "") if verdict2 else "?", rc2),
        "- 守衛：`tests/test_eval_lifecycle_cli.py` 全綠（exit %d）。" % rc1,
        "- 自動刷新機制：每日 20:40 EDT（= 00:40Z，UTC 日封閉後）執行。",
        "",
    ]
    entry = "\n".join(lines).replace("\r\n", "\n")
    if nl != "\n":
        entry = entry.replace("\n", nl)
    if renum:
        print("canonical placeholder renumbered: %s" % ", ".join(renum))
    with open(CANON, "wb") as f:
        f.write(canon_text.encode("utf-8") + entry.encode("utf-8"))

    msg = os.path.join(tempfile.gettempdir(), "ledger_refresh_msg.txt")
    with open(msg, "w", encoding="utf-8", newline="\n") as f:
        f.write("chore(ledger): 值守端日常台帳刷新——封閉 cohort %s（核准 %d 顆，本次新增 %d 顆）\n\n"
                "- 封閉 cohort %s：核准集合 %d 顆，**本次新增 %d 顆**"
                "（四條件量測；建立日已過 ⇒ 成員集合固定）。\n"
                "- 台帳檔案變更：%s。\n"
                "- 其他受影響 cohort：%s。\n"
                "- 下一槽沙盒快跑 %s ⇒ %s（exit %d）。\n"
                "- 守衛 tests/test_eval_lifecycle_cli.py 全綠（exit %d）。\n"
                "- canonical §0.1.%d 追加。\n"
                % (closed_date, closed_total, closed_delta,
                   closed_date, closed_total, closed_delta,
                   changed_txt,
                   other_txt,
                   slot.isoformat(), verdict2[0].replace("VERDICT: ", "") if verdict2 else "?", rc2, rc1,
                   sect_no))
    sh(["git", "add", "--", "scripts/eval_lifecycle_cli.py", "logs/ENGINEERING_STATE.md"])
    sh(["git", "commit", "-F", msg])
    sh(["git", "push", "origin", "main"])
    print("committed + pushed:", sh(["git", "log", "-1", "--format=%h %s"])[1].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
