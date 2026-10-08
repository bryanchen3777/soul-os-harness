"""VC Audit v1 — parser 自我測試（離線、0 API、0 行程）.

用**真實的** VC log 行片段驗證 ``runner.parse_turn`` 與
``runner.classify_empty_group``。這些行是 2026-10-07 從
``clients/voice_companion/logs/vc_agent_rem.log`` 原樣摘錄的，
不是憑空構造——regex 若與實際格式不符，這裡就會紅。

刻意**不**命名為 ``test_*.py``：本目錄位於 ``scripts/``，雖有
``pytest.ini`` 的 ``testpaths = tests`` 保護，仍避免任何被收集的可能
（``scripts/**`` 曾有 ``test_*.py`` 被收集後對真實 LLM API 發請求的事故）。

執行::

    .venv\\Scripts\\python.exe -m scripts.vc_audit.parser_selftest
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.vc_audit.runner import classify_empty_group, parse_turn  # noqa: E402

STIM = {"id": "C3", "text": "測試一下，聽得到嗎"}

# ── 真實 log 行（原樣摘錄）──
REST_TURN = [
    "2026-09-11 14:37:13,263 INFO [vc.web_server] [WS] msg type=text text=測試一下，聽得到嗎",
    "2026-09-11 14:37:19,259 INFO [vc.web_server] [TTS] rest-ok chars=52 bytes=1013760",
    "2026-09-11 14:37:19,300 INFO [vc.web_server] [LATENCY] asr=0.0ms first_audio=5989.7ms total=6036.5ms chars=52",
    "2026-09-11 14:37:19,300 INFO [vc.web_server] [TTS] round mode=rest input_chars=9 reply_chars=52 "
    "chunks=1 bytes=1013760 first_audio_ms=5989.7 total_ms=6036.5",
]

EMPTY_LLM_LENGTH = [
    "2026-10-03 17:27:04,490 WARNING [soul_os.vc_brain] [VC-LLM-EMPTY] content_pieces=0 max_tokens=1024 "
    "reasoning_seen=True reasoning_chunks=1009 chunks=1011 finish_reason='length' elapsed_ms=17908",
    "2026-10-03 17:27:04,498 WARNING [vc.web_server] [TTS] round ZERO-AUDIO mode=rest input_chars=24 "
    "reply_chars=0 chunks=0 bytes=0 first_audio_ms=18345.1 total_ms=18345.1 last_error=None",
]

EMPTY_LLM_STOP = [
    "2026-10-03 17:35:29,849 WARNING [soul_os.vc_brain] [VC-LLM-EMPTY] content_pieces=0 max_tokens=1024 "
    "reasoning_seen=True reasoning_chunks=212 chunks=214 finish_reason='stop' elapsed_ms=7706",
]

BARGE_TURN = [
    "2026-10-03 17:27:59,001 WARNING [vc.web_server] [UTT] barge reason=text gen:6->7",
    "2026-10-03 17:27:59,001 WARNING [vc.web_server] [UTT] reply-cancelled gen=6 curr=8",
]

CLIENT = {"client_turn_end_ms": 6050.0, "client_first_audio_ms": 6011.0, "timeout": None}

_failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  PASS  {label}: {actual!r}")
    else:
        print(f"  FAIL  {label}: got {actual!r}, expected {expected!r}")
        _failures.append(label)


def main() -> int:
    print("[1] REST 正常回合")
    r = parse_turn(REST_TURN, CLIENT, "rest", STIM, "selftest", "2026-10-07T00:00:00Z")
    check("first_audio_ms", r["first_audio_ms"], 5989.7)
    check("total_turn_ms", r["total_turn_ms"], 6036.5)
    check("reply_chars", r["reply_chars"], 52)
    check("tts_chunks", r["tts_chunks"], 1)
    check("tts_bytes", r["tts_bytes"], 1013760)
    check("zero_audio", r["zero_audio"], False)
    check("empty_llm", r["empty_llm"], None)
    check("barge_in", r["barge_in"], False)
    check("tts_error", r["tts_error"], None)

    print("[2] empty-LLM 族 E1/E2（finish_reason='length'）+ ZERO-AUDIO")
    r2 = parse_turn(EMPTY_LLM_LENGTH, CLIENT, "rest", STIM, "selftest", "2026-10-07T00:00:00Z")
    check("empty_llm", r2["empty_llm"], True)
    check("finish_reason", r2["finish_reason"], "length")
    check("reasoning_seen", r2["reasoning_seen"], True)
    check("reasoning_chunks", r2["reasoning_chunks"], 1009)
    check("empty_llm_group", r2["empty_llm_group"], "E1")
    check("zero_audio", r2["zero_audio"], True)
    check("tts_chunks", r2["tts_chunks"], 0)
    check("first_audio_ms", r2["first_audio_ms"], None)

    print("[3] empty-LLM 族 E3（finish_reason='stop' + reasoning）— 不能被『1024 太小』解釋")
    r3 = parse_turn(EMPTY_LLM_STOP, CLIENT, "rest", STIM, "selftest", "2026-10-07T00:00:00Z")
    check("finish_reason", r3["finish_reason"], "stop")
    check("reasoning_chunks", r3["reasoning_chunks"], 212)
    check("empty_llm_group", r3["empty_llm_group"], "E3")

    print("[4] barge-in 偵測")
    r4 = parse_turn(BARGE_TURN, CLIENT, "rest", STIM, "selftest", "2026-10-07T00:00:00Z")
    check("barge_in", r4["barge_in"], True)
    check("first_audio_ms 缺資料必須是 None 不是 0", r4["first_audio_ms"], None)

    print("[5] classify_empty_group 對應表（工單 §8）")
    check("length+reasoning", classify_empty_group("length", True), "E1")
    check("stop+reasoning", classify_empty_group("stop", True), "E3")
    check("stop+no reasoning", classify_empty_group("stop", False), "E4")
    check("length+no reasoning", classify_empty_group("length", False), "E2")
    check("其他", classify_empty_group("content_filter", True), "E5")
    check("未知", classify_empty_group(None, None), None)

    print("[6] 缺資料語意：0 與 None 不可互換")
    r5 = parse_turn(["2026-10-07 INFO [vc.web_server] [WS] connect peer=127.0.0.1"], CLIENT,
                    "rest", STIM, "selftest", "2026-10-07T00:00:00Z")
    for k in ("first_audio_ms", "total_turn_ms", "reply_chars", "tts_chunks",
              "tts_bytes", "zero_audio", "empty_llm"):
        check(f"{k} 在無資料時為 None", r5[k], None)

    print()
    if _failures:
        print(f"SELFTEST FAILED — {len(_failures)} 項：{_failures}")
        return 1
    print("SELFTEST PASSED — parser 與真實 log 格式一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())