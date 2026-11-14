# -*- coding: utf-8 -*-
"""SA-BUDGET-GO-1 v2-Go detector + offline bidirectional guard (TEMP work script, zero network).

- judge(obs, single_cap) operates on in-memory observation dicts only.
  This module imports only json/re/sys => structurally cannot touch the network.
- finish 依協議分派（chat=stop / responses=completed / messages=end_turn），
  截斷一律不通過；單發上限依本發檔位（billed = output 原值，計一次）。
- verdict 三態：ACCEPT（halt none）/ 內容失敗（halt model）/
  UNKNOWN（halt batch）。缺 reasoning 明細記“未提供”，不判 UNKNOWN。
- `guard` 跑雙向自證 cases；`judge-file <observations.jsonl> <out.json>
  <single_cap>` 逐行判一批真實觀測。
"""
import json
import re
import sys

EXPECTED_FINISH = {
    "chat-completions": "stop",
    "responses": "completed",
    "messages": "end_turn",
}

MIN_OVERLAP = 30
REASONING_MARKERS = ["we need", "need answer", "let's produce", "<think>", "思考過程"]

SECTION_RE = re.compile(r"(?m)^\s*#{0,4}\s*([1-5])[\.\、\)\:\uff1a]")


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def _text_has_reasoning_mixed(text, reasoning):
    t = _norm(text)
    r = _norm(reasoning)
    if not r or not t:
        return False, ""
    if len(r) >= MIN_OVERLAP:
        grams = set()
        for i in range(len(t) - MIN_OVERLAP + 1):
            grams.add(t[i:i + MIN_OVERLAP])
        for i in range(len(r) - MIN_OVERLAP + 1):
            if r[i:i + MIN_OVERLAP] in grams:
                return True, "overlap>=%d at reasoning offset %d" % (MIN_OVERLAP, i)
    low = (text or "").lower()
    for m in REASONING_MARKERS:
        if m.lower() in low:
            return True, "marker:" + m
    return False, ""


def five_questions_ok(text):
    found = set(SECTION_RE.findall(text or ""))
    return found == {"1", "2", "3", "4", "5"}, sorted(found)


def reasoning_detail_of(obs):
    """推理明細：有就揭露數值，缺就記“未提供”（不判 UNKNOWN）。"""
    usage = obs.get("usage") or {}
    v = usage.get("reasoning_tokens")
    if isinstance(v, int) and v >= 0:
        return v
    raw = obs.get("usage_raw") or {}
    for key in ("completion_tokens_details", "output_tokens_details"):
        det = raw.get(key) or {}
        v = det.get("reasoning_tokens")
        if isinstance(v, int) and v >= 0:
            return v
    return "未提供"


def settlement_of(obs):
    """與 harness.budget.settle_billed_tokens / client.settlement_for_result 同規則。

    billed = output 原值（計一次）；缺失／非法／口徑矛盾 ⇒ unknown。
    """
    usage = obs.get("usage")
    if not isinstance(usage, dict):
        return None, True
    out = usage.get("output_tokens")
    if out is None or isinstance(out, bool):
        return None, True
    try:
        out_i = int(out)
    except (TypeError, ValueError):
        return None, True
    if out_i < 0:
        return None, True
    raw_r = usage.get("reasoning_tokens")
    if raw_r is not None:
        if isinstance(raw_r, bool):
            return None, True
        try:
            r_i = int(raw_r)
        except (TypeError, ValueError):
            return None, True
        if r_i < 0 or r_i > out_i:
            return None, True
    return out_i, False


def judge(obs, single_cap):
    channel = obs.get("channel") or ""
    expected = EXPECTED_FINISH.get(channel, "")
    text = obs.get("text") or ""
    reasoning = obs.get("reasoning_text") or ""
    checks = {}
    checks["http_200"] = (obs.get("status_code") == 200)
    checks["non_blank_text"] = bool(text.strip())
    checks["finish_ok"] = (obs.get("finish_reason") == expected) if expected else False
    billed, unknown = settlement_of(obs)
    checks["settlement_unknown"] = unknown
    checks["output_lt_cap"] = (not unknown and isinstance(billed, int)
                               and billed < single_cap)
    fq, _ = five_questions_ok(text)
    checks["five_questions"] = fq
    mixed, why = _text_has_reasoning_mixed(text, reasoning)
    checks["no_reasoning_leak"] = (not mixed)
    checks["_leak_detail"] = why

    detail = reasoning_detail_of(obs)
    verdict, scope = "ACCEPT", "none"
    if not checks["http_200"]:
        verdict, scope = "http=" + str(obs.get("status_code")), "batch"
    elif unknown:
        verdict, scope = "UNKNOWN-settlement", "batch"
    elif not checks["non_blank_text"]:
        verdict, scope = "empty-text", "model"
    elif not checks["finish_ok"]:
        verdict, scope = "finish=" + str(obs.get("finish_reason")), "model"
    elif not checks["output_lt_cap"]:
        verdict, scope = "output-over-budget", "model"
    elif not checks["five_questions"]:
        verdict, scope = "incomplete-five", "model"
    elif not checks["no_reasoning_leak"]:
        verdict, scope = "reasoning-leak", "model"
    return {"pass": verdict == "ACCEPT", "verdict": verdict,
            "halt_scope": scope, "checks": checks,
            "billed_completion": billed,
            "reasoning_detail": detail,
            "expected_finish": expected}


CLEAN_TEXT = (
    "### 1. 第一問作答：合理。\n\n"
    "### 2. 第二問作答：屬於 History。\n\n"
    "### 3. 第三問作答：可能成為 Disposition。\n\n"
    "### 4. 第四問作答：不應直接修改 Persona。\n\n"
    "### 5. 第五問作答：新增與並存。\n"
)
CANNED_REASONING = (
    "We need answer Chinese. Need analyze Persona History Living concepts. "
    "Must distinguish facts assumptions unknowns. Let's produce five sections."
)


def canned(channel="chat-completions", status=200, finish=None, completion=100,
           text=CLEAN_TEXT, reasoning=CANNED_REASONING,
           reasoning_detail=10, no_usage=False, raw_detail=True):
    finish = finish if finish is not None else EXPECTED_FINISH[channel]
    obs = {"channel": channel, "status_code": status, "finish_reason": finish,
           "text": text, "reasoning_text": reasoning}
    if not no_usage:
        usage = {"input_tokens": 50, "output_tokens": completion,
                 "total_tokens": 50 + completion}
        if reasoning_detail is not None:
            usage["reasoning_tokens"] = reasoning_detail
        obs["usage"] = usage
        if raw_detail and reasoning_detail is not None:
            obs["usage_raw"] = {"output_tokens_details":
                                {"reasoning_tokens": reasoning_detail}}
        else:
            obs["usage_raw"] = {}
    return obs


def run_guard():
    cases = [
        # (name, obs, cap, exp_pass, exp_verdict, exp_scope)
        ("G1-chat-accept", canned("chat-completions"), 8000, True, "ACCEPT", "none"),
        ("G2-responses-accept", canned("responses"), 16000, True, "ACCEPT", "none"),
        ("G3-messages-accept", canned("messages"), 8000, True, "ACCEPT", "none"),
        ("G4-missing-detail-still-accept",
         canned(reasoning_detail=None), 8000, True, "ACCEPT", "none"),
        ("R1-text-mixed-reasoning-reject",
         canned(text=CANNED_REASONING + "\n" + CLEAN_TEXT), 8000,
         False, "reasoning-leak", "model"),
        ("R2-empty-text-reject",
         canned(text="   ", reasoning=""), 8000, False, "empty-text", "model"),
        ("R3-chat-truncation-reject",
         canned("chat-completions", finish="length"), 8000,
         False, "finish=length", "model"),
        ("R4-responses-wrong-finish-reject",
         canned("responses", finish="stop"), 16000,
         False, "finish=stop", "model"),
        ("R5-messages-truncation-reject",
         canned("messages", finish="max_tokens"), 8000,
         False, "finish=max_tokens", "model"),
        ("R6-over-cap-reject", canned(completion=8000), 8000,
         False, "output-over-budget", "model"),
        ("R7-missing-usage-unknown", canned(no_usage=True), 8000,
         False, "UNKNOWN-settlement", "batch"),
        ("R8-contradiction-unknown",
         canned(completion=50, reasoning_detail=80), 8000,
         False, "UNKNOWN-settlement", "batch"),
        ("R9-incomplete-five-reject",
         canned(text="### 1. a\n### 2. b\n"), 8000,
         False, "incomplete-five", "model"),
        ("R10-http-error-batch", canned(status=429), 8000,
         False, "http=429", "batch"),
    ]
    results = []
    ok = True
    for name, obs, cap, exp_pass, exp_verdict, exp_scope in cases:
        got = judge(obs, cap)
        passed = (got["pass"] == exp_pass and got["verdict"] == exp_verdict
                  and got["halt_scope"] == exp_scope)
        results.append({"name": name, "pass": passed,
                        "detail": "verdict=" + got["verdict"] + " scope=" + got["halt_scope"],
                        "expected": exp_verdict + "/" + exp_scope})
        ok = ok and passed
    # 缺明細記“未提供”且仍可過（G4 的附加斷言）
    g4 = judge(canned(reasoning_detail=None), 8000)
    det_ok = (g4["reasoning_detail"] == "未提供" and g4["pass"] is True)
    results.append({"name": "D1-missing-detail-marked-unprovided",
                    "pass": det_ok,
                    "detail": "reasoning_detail=" + str(g4["reasoning_detail"])})
    ok = ok and det_ok
    return ok, results


def main(argv):
    if argv and argv[0] == "judge-file":
        if len(argv) < 3:
            print("usage: detector_go1.py judge-file <observations.jsonl> <cap> [out.json]")
            return 2
        lines = [ln for ln in open(argv[1], encoding="utf-8").read().splitlines()
                 if ln.strip()]
        cap = int(argv[2])
        out_path = argv[3] if len(argv) > 3 else (argv[1] + ".verdict.json")
        out = [dict(judge(json.loads(ln), cap), shot=i + 1)
               for i, ln in enumerate(lines)]
        json.dump(out, open(out_path, "w", encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=2)
        print(json.dumps({"shots": len(out),
                          "accept": sum(1 for o in out if o["pass"]),
                          "verdicts": [o["verdict"] for o in out]},
                         ensure_ascii=False, indent=2))
        return 0
    if len(argv) == 1 and argv[0] == "guard":
        ok, results = run_guard()
        print(json.dumps({"verdict": "ALL GREEN" if ok else "RED",
                          "results": results},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print("usage: detector_go1.py guard | judge-file <observations.jsonl> <cap> [out.json]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
