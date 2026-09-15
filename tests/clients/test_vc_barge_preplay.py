"""
tests/clients/test_vc_barge_preplay.py — VC-BARGE-PREPLAY-1 驗收：
auto-VAD barge-in 只在「本回合真的有音訊」時才 armed（修掉沉默窗自我取消）。

病根（唯讀鑑識已定案）：伺服器 `web_server.py:_run_reply` 開頭（LLM 首字之前）就宣告 SPEAKING，
而首個 PCM 幀 ~3.0s 後才到；client 端 barge 舊判定只看 `state === "SPEAKING"` ⇒
TTFT 沉默窗內 barge 已被 armed，麥克風能量誤觸 ⇒ interrupt ⇒ reply-cancelled。

修正（決策已拍板）：複用 VC-VAD-TIMING-1（552f84b）已建好且已測試的音訊時鐘 `playbackDrained`，
不另造平行旗標。armed 判定抽成純函式 `isBargeArmed(st, drained)` 並由實際路徑呼叫。

本檔案的斷言方式：**結構化抽取**（平衡大括號掃描取出實際出貨的 JS 函式/區塊，再做語意或
區塊歸屬分析），不使用「某字串不在檔案裡」這種 0 命中文字比對（本專案已吃過假護欄的虧）。

- Test A：純函式真值表（從 web_ui.py 抽出 → 受控轉譯 → 實際求值）
- Test B：實際路徑（mic audioprocess）的 barge 閘門 = 該純函式；舊 `state === "SPEAKING"` 不再守門
- Test C：複用的時鐘只由「真的 binary PCM 幀」驅動；worklet/fallback 兩條路徑皆涵蓋；冷卻時鐘逐字不動
- Test D：PTT 路徑未被連帶鎖住（不引用 armed 判準；純函式僅出現在宣告處與 mic 閘門）
- Test E：可觀測性（伺服器端行為實測，in-process、0 外部網路）
- Test F：協定/護欄相容（WS 訊息集合不變、interrupt 仍為既有形式）

手動驗收程序（Owner 執行，需 0 服務重啟以外的環境；本檔案只做離線靜態/行為驗證）：
  1) 對 Mai 說話（auto-VAD 開）→ 送出後**等 5 秒不要出聲** → 回覆必須**播出**，不得被自我取消。
  2) 回覆**播放中**開口打斷 → 必須仍然有效（角色停止說話、進入聆聽）。
  3) 回覆**播放中或沉默窗內按下 PTT** → 必須**立即**打斷（PTT 不受本判定限制）。

執行：.venv\\Scripts\\python.exe -m pytest tests/clients/test_vc_barge_preplay.py -q
全離線：不呼叫瀏覽器 / 麥克風 / 音效卡 / 網路。
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import logging
import re
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.web_server import WebSession  # noqa: E402
from clients.voice_companion.web_ui import HTML_PAGE  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 結構化抽取工具（平衡括號/大括號掃描，字串字面值感知）
# ─────────────────────────────────────────────────────────────

def _match_pair(src: str, idx: int, och: str, cch: str) -> int:
    """回傳 src[idx] 開括號的配對位置（跳過 '...' / "..." 字面值）。找不到 → AssertionError（測試紅）。"""
    depth = 0
    quote = None
    i = idx
    while i < len(src):
        ch = src[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == och:
            depth += 1
        elif ch == cch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"未閉合：{src[idx:idx + 40]!r}")


def _strip_js_comments(src: str) -> str:
    """把 JS 註解（// 與 /* */）以等長空白取代：offset 不變，結構化掃描不被註解誤導。

    必要：本票在 JS 內加了說明註解（含 queuePlaybackSamples() / isBargeArmed 等字樣），
    若不剝除，呼叫點計數與區塊歸屬分析會被註解污染。
    """
    out = list(src)
    i, n = 0, len(src)
    quote = None
    while i < n:
        ch = src[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            end = n if j == -1 else j + 2
            for k in range(i, end):
                if src[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _extract_js_block(source: str, anchor: str) -> str:
    """自 anchor 起取出第一個完整的 {...} 區塊（結構化抽取，非文字存在性比對）。"""
    start = source.index(anchor)
    open_idx = source.index("{", start + len(anchor))
    close_idx = _match_pair(source, open_idx, "{", "}")
    return source[start:close_idx + 1]


def _span_of(source: str, anchor: str) -> tuple[int, int]:
    start = source.index(anchor)
    return start, start + len(_extract_js_block(source, anchor))


def _if_blocks(block: str) -> list[tuple[int, int, str]]:
    """列出 block 內每個 `if (<cond>) { ... }` 的 (open_idx, close_idx, cond)。"""
    out: list[tuple[int, int, str]] = []
    for m in re.finditer(r"\bif\s*\(", block):
        pstart = m.end() - 1
        pend = _match_pair(block, pstart, "(", ")")
        cond = block[pstart + 1:pend].strip()
        bstart = pend + 1
        while bstart < len(block) and block[bstart].isspace():
            bstart += 1
        if bstart < len(block) and block[bstart] == "{":
            out.append((bstart, _match_pair(block, bstart, "{", "}"), cond))
    return out


def _enclosing_conds(block: str, needle_idx: int) -> list[str]:
    """needle_idx 位置外層所有 if 條件的鏈（由內到外）。"""
    inner = [b for b in _if_blocks(block) if b[0] < needle_idx < b[1]]
    inner.sort(key=lambda b: b[1] - b[0])  # 最內層優先
    return [cond for _o, _c, cond in inner]


def _split_strings(expr: str) -> list[str]:
    """把 JS 運算式切成 [非字串, 字串, 非字串, ...]（偶數索引為非字串段）。"""
    return re.split(r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')', expr)


def _sub_outside_strings(expr: str, pattern: str, repl: str) -> str:
    parts = _split_strings(expr)
    return "".join(
        p if (i % 2) else re.sub(pattern, repl, p) for i, p in enumerate(parts)
    )


def _js_predicate_to_python(fn_src: str):
    """把 JS 純函式（單一 return 述句）轉成 Python lambda。

    只允許受控詞彙（白名單正則 + 禁 ; { }），確保 eval 不可能執行任意碼。
    """
    header = fn_src[:fn_src.index("{")]
    params = header[header.index("(") + 1:header.rindex(")")]
    names = [p.strip() for p in params.split(",") if p.strip()]
    assert len(names) == 2, f"isBargeArmed 應為二元純函式，實得 {names}"

    body = fn_src[fn_src.index("{") + 1:fn_src.rindex("}")]
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    assert len(stmts) == 1 and stmts[0].startswith("return "), (
        f"純函式必須只有單一 return 述句，實得 {stmts!r}"
    )
    expr = stmts[0][len("return "):]
    assert re.fullmatch(r"[A-Za-z0-9_$\s.=\"'!&|<>\-]+", expr), f"運算式含白名單外字元：{expr!r}"

    py = _sub_outside_strings(expr, r"!==", " != ")
    py = _sub_outside_strings(py, r"===", " == ")
    py = _sub_outside_strings(py, r"\btrue\b", "True")
    py = _sub_outside_strings(py, r"\bfalse\b", "False")
    py = _sub_outside_strings(py, r"\bnull\b", "None")
    py = _sub_outside_strings(py, r"&&", " and ")
    py = _sub_outside_strings(py, r"\|\|", " or ")
    py = _sub_outside_strings(py, r"!", " not ")
    for idx, name in enumerate(names):
        py = _sub_outside_strings(py, r"\b" + re.escape(name) + r"\b", f"a{idx}")
    assert not re.search(r"[;{}]", py), f"轉譯後仍殘留語句分隔字元：{py!r}"
    return names, py


def _barge_armed():
    """從實際出貨的 HTML_PAGE 取出 isBargeArmed 並回傳可呼叫的 Python 等價函式。"""
    fn_src = _extract_js_block(_JS, "function isBargeArmed(")
    _names, py = _js_predicate_to_python(fn_src)
    return eval(f"lambda a0, a1: {py}", {"__builtins__": {}}), fn_src  # noqa: S307


def _mic_barge_block() -> str:
    """mic 收音/打斷所在的 onaudioprocess 區塊（實際 barge 路徑）。"""
    return _extract_js_block(_JS, "recNode.onaudioprocess = function (e)")


# 註解剝除後的頁面（所有結構化掃描一律用這份，避免註解污染 offset 與區塊歸屬分析）
_JS = _strip_js_comments(HTML_PAGE)


SERVER_SRC_PATH = REPO_ROOT / "clients/voice_companion/web_server.py"


def _python_func_source(name: str) -> str:
    """以 AST 取出 web_server.py 內指定函式的原始碼（結構化，非文字比對）。"""
    src = SERVER_SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            seg = ast.get_source_segment(src, node)
            assert seg, f"{name} 原始碼片段取得失敗"
            return seg
    raise AssertionError(f"web_server.py 找不到函式 {name}")


# ─────────────────────────────────────────────────────────────
# Test A：純函式真值表
# ─────────────────────────────────────────────────────────────

class TestABargeArmedTruthTable:
    def test_truth_table_armed_only_when_speaking_with_audio(self):
        """(SPEAKING, drained=false) → armed；其餘三種組合 → NOT armed。"""
        armed, fn_src = _barge_armed()
        assert armed("SPEAKING", False) is True, f"播放中必須 armed：{fn_src}"
        assert armed("SPEAKING", True) is False, "沉默窗（尚無音訊）不得 armed"
        assert armed("IDLE", False) is False
        assert armed("IDLE", True) is False

    def test_unknown_state_is_not_armed(self):
        """非 SPEAKING 的任何 state（含 LISTENING/THINKING/undefined）一律不得 armed。"""
        armed, _ = _barge_armed()
        for st in ("LISTENING", "THINKING", "undefined", ""):
            assert armed(st, False) is False, f"state={st!r} 不得 armed"

    def test_undefined_drain_value_fails_closed(self):
        """drained 非明確 False（undefined/null）→ fail-closed 不 armed（不得用寬鬆的真值判定）。"""
        armed, fn_src = _barge_armed()
        assert armed("SPEAKING", None) is False, f"drained 非明確 false 必須 fail-closed：{fn_src}"

    def test_no_parallel_audio_clock_introduced(self):
        """設計拍板：複用 playbackDrained，不得另造第二個「本回合是否已有音訊」旗標。"""
        assert "turnAudioStarted" not in HTML_PAGE, (
            "不得另造平行時鐘；armed 判準必須單一來源（playbackDrained）"
        )


# ─────────────────────────────────────────────────────────────
# Test B：實際路徑的閘門就是該純函式
# ─────────────────────────────────────────────────────────────

class TestBRealPathUsesThePureFunction:
    def test_barge_sends_are_guarded_by_the_pure_function(self):
        """兩個 interrupt 發送點都必須被 isBargeArmed(state, playbackDrained) 這道閘門包住。"""
        block = _mic_barge_block()
        sends = [m.start() for m in re.finditer(r"send\(\{\s*type:\s*\"interrupt\"\s*\}\)", block)]
        assert len(sends) == 2, f"應有 auto/manual 兩個 interrupt 發送點，實得 {len(sends)}"
        for idx in sends:
            conds = _enclosing_conds(block, idx)
            assert "isBargeArmed(state, playbackDrained)" in conds, (
                f"interrupt 發送點未被純函式閘門包住，外層條件={conds}"
            )

    def test_legacy_speaking_only_gate_no_longer_guards_barge(self):
        """舊判定（只看 state === "SPEAKING"）不得再出現在 barge 發送點的外層條件鏈中。"""
        block = _mic_barge_block()
        sends = [m.start() for m in re.finditer(r"send\(\{\s*type:\s*\"interrupt\"\s*\}\)", block)]
        for idx in sends:
            conds = _enclosing_conds(block, idx)
            assert 'state === "SPEAKING"' not in conds, (
                f"barge 仍被舊的 SPEAKING-only 判定守門：{conds}"
            )

    def test_pure_function_is_called_with_the_reused_clock(self):
        """閘門必須傳入既有的 playbackDrained（而非任何新旗標）。"""
        block = _mic_barge_block()
        assert "isBargeArmed(state, playbackDrained)" in block

    def test_mic_mute_gate_unchanged(self):
        """autoMuted（不聽自己喇叭）閘門維持原樣 —— 本票只改 barge arming，不動收音靜音。"""
        block = _mic_barge_block()
        assert 'var autoMuted = auto && (state === "SPEAKING" || !playbackDrained);' in block


# ─────────────────────────────────────────────────────────────
# Test C：複用的時鐘只由「真的 binary PCM 幀」驅動
# ─────────────────────────────────────────────────────────────

class TestCReusedClockDrivenOnlyByRealAudio:
    def test_first_frame_sets_drained_false_inside_the_funnel(self):
        """首幀判定：playbackDrained=false 發生在 queuePlaybackSamples（播放取樣唯一入口）。"""
        funnel = _extract_js_block(_JS, "function queuePlaybackSamples(")
        assert "playbackDrained = false;" in funnel
        assert "cancelTailTimer();" in funnel

    def test_funnel_covers_both_playback_paths(self):
        """worklet 與 ScriptProcessor 兩條路徑都涵蓋：首幀判定必須在分派之前（552f84b 的教訓）。"""
        funnel = _extract_js_block(_JS, "function queuePlaybackSamples(")
        i_set = funnel.index("playbackDrained = false;")
        i_worklet = funnel.index("workletReady && workletNode")
        i_fallback = funnel.index("pushFallbackSamples(")
        assert i_set < i_worklet, "首幀判定必須早於 worklet 分派"
        assert i_set < i_fallback, "首幀判定必須早於 fallback 分派"

    def test_funnel_only_called_from_binary_pcm_frames(self):
        """queuePlaybackSamples 的呼叫點必須全部落在 WS binary PCM 幀處理區塊內（無其他來源）。"""
        anchor = "ws.onmessage = function (ev)"
        handler_start, handler_end = _span_of(_JS, anchor)
        calls = [
            m.start() for m in re.finditer(r"(?<!function )queuePlaybackSamples\(", _JS)
        ]
        assert len(calls) == 2, f"預期 2 個呼叫點（44.1k 直送 / 重採樣），實得 {len(calls)}"
        for off in calls:
            assert handler_start < off < handler_end, "queuePlaybackSamples 出現非 binary 幀來源的呼叫"

    def test_binary_handler_branch_is_speaking_gated(self):
        """binary 幀只在 SPEAKING 分支入列 → 該分支的入列呼叫才是真正的「首幀」。"""
        handler = _extract_js_block(_JS, "ws.onmessage = function (ev)")
        calls = [
            m.start() for m in re.finditer(r"(?<!function )queuePlaybackSamples\(", handler)
        ]
        assert len(calls) == 2
        for idx in calls:
            conds = _enclosing_conds(handler, idx)
            assert any('state === "SPEAKING"' in c for c in conds), (
                f"入列未被 SPEAKING 分支守門，外層條件={conds}"
            )

    def test_drain_clock_untouched(self):
        """回合結束時鐘逐字不動：onPlaybackDrained + TAIL_BUFFER_MS(400) 設回 true。"""
        drain = _extract_js_block(_JS, "function onPlaybackDrained(")
        assert "playbackActive = false;" in drain
        assert "cancelTailTimer();" in drain
        assert "tailTimer = setTimeout(function ()" in drain
        assert "playbackDrained = true;" in drain
        m = re.search(r"TAIL_BUFFER_MS = (\d+)", HTML_PAGE)
        assert m and int(m.group(1)) == 400, "TAIL_BUFFER_MS 冷卻時鐘不得改動"

    def test_drain_notified_from_both_paths(self):
        """排空通知在 worklet 與 fallback 兩條路徑都存在（開麥錨點來源）。"""
        assert "workletNode.port.onmessage = function (e)" in _JS
        worklet_cb = _extract_js_block(_JS, "workletNode.port.onmessage = function (e)")
        assert "onPlaybackDrained();" in worklet_cb
        fallback = _extract_js_block(_JS, "playNode.onaudioprocess = function (e)")
        assert "hadAudio && fallbackAvail === 0" in fallback
        assert "onPlaybackDrained();" in fallback

    def test_clock_initial_value_is_drained(self):
        """初始值必須為 true（尚無音訊）→ 連線後第一次回覆的沉默窗不會 armed。"""
        m = re.search(r"var playbackActive = false, playbackDrained = (\w+)", _JS)
        assert m and m.group(1) == "true", "playbackDrained 初始值必須為 true"


# ─────────────────────────────────────────────────────────────
# Test D：PTT 路徑未被連帶鎖住
# ─────────────────────────────────────────────────────────────

class TestDPttPathNotGated:
    def test_ptt_senders_do_not_reference_the_arm_predicate(self):
        """sendPttStart / sendPttStop 不得引用 armed 判準（PTT 任何時候都要立即生效）。"""
        for name in ("sendPttStart", "sendPttStop"):
            body = _extract_js_block(_JS, f"function {name}(")
            assert "isBargeArmed" not in body, f"{name} 不得碰 armed 判準"
            assert "playbackDrained" not in body, f"{name} 不得碰播放狀態判準"

    def test_ptt_listeners_intact_and_ungated(self):
        """PTT 事件綁定（按住 micBtn / 空白鍵）不得被 armed 判準守門。"""
        anchors = (
            'micBtn.addEventListener("pointerdown"',
            'document.addEventListener("keydown"',
            'document.addEventListener("keyup"',
        )
        for anchor in anchors:
            body = _extract_js_block(_JS, anchor)
            assert "isBargeArmed" not in body, f"{anchor} 不得被 armed 判準守門"
        down = _extract_js_block(_JS, 'micBtn.addEventListener("pointerdown"')
        assert "sendPttStart();" in down, "PTT 按下必須直接送 ptt_start（不經 barge 閘門）"

    def test_arm_predicate_confined_to_declaration_and_mic_gate(self):
        """isBargeArmed 只准出現在 (1) 自身宣告 (2) mic barge 閘門；任何其他地方都是洩漏。"""
        decl_span = _span_of(_JS, "function isBargeArmed(")
        gate_span = _span_of(_JS, "recNode.onaudioprocess = function (e)")
        occ = [m.start() for m in re.finditer(r"isBargeArmed", _JS)]
        assert len(occ) >= 2, "isBargeArmed 必須被實際路徑呼叫"
        for off in occ:
            in_decl = decl_span[0] <= off < decl_span[1]
            in_gate = gate_span[0] <= off < gate_span[1]
            assert in_decl or in_gate, f"isBargeArmed 洩漏到其他區塊 offset={off}"

    def test_ptt_interrupt_is_server_side(self):
        """PTT 打斷走伺服器 on_ptt_start（不受 client 播放狀態影響）——協定層確認。"""
        ptt = _python_func_source("on_ptt_start")
        assert 'self._barge("ptt_start")' in ptt
        for forbidden in ("playbackDrained", "isBargeArmed", "first_chunk_time", "_written_chunks"):
            assert forbidden not in ptt, f"PTT 打斷不得被 {forbidden} 守門（沉默窗內也必須立即生效）"


# ─────────────────────────────────────────────────────────────
# Test E：可觀測性（伺服器端行為實測，in-process、0 外部網路）
# ─────────────────────────────────────────────────────────────

WEB_TEST_CONFIG = {
    "companion": {"id": "agent_test", "display_name": "測試", "short_name": "測"},
    "web": {"host": "127.0.0.1", "port": 0},
}


class _RecordingWS:
    """收集伺服器送出的 JSON（觀察 _set_state 是否真的通知 client）。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


class _StubStreamer:
    def __init__(self) -> None:
        self.interrupt_calls = 0
        self.start_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def interrupt(self) -> None:
        self.interrupt_calls += 1


class _StubDetector:
    def __init__(self) -> None:
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1


class _StubSink:
    """對齊 AudioRelaySink 的最小介面：first_chunk_time（回合開始時由 _run_reply 重置為 None）。"""

    def __init__(self, first_chunk_time: float | None = None) -> None:
        self.first_chunk_time = first_chunk_time
        self._written_chunks = 0
        self._written_bytes = 0


def _make_session(first_chunk_time: float | None = None):
    ws = _RecordingWS()
    streamer = _StubStreamer()
    detector = _StubDetector()
    sink = _StubSink(first_chunk_time)
    session = WebSession(
        ws,
        config=WEB_TEST_CONFIG,
        brain=object(),
        refiner=object(),
        asr=object(),
        streamer=streamer,
        detector=detector,
        sink=sink,
    )
    return session, ws, streamer


def _records_with(caplog, tag: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if tag in r.getMessage()]


class TestEServerObservability:
    def test_set_state_writes_state_log(self, caplog):
        """_set_state 必須寫 log（此前完全不寫 ⇒ 磁碟上無法得知伺服器送了哪個 state）。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, ws, _ = _make_session()

        async def _run():
            await session._set_state("SPEAKING")

        asyncio.run(_run())

        state_logs = _records_with(caplog, "[STATE] SPEAKING")
        assert len(state_logs) == 1, "_set_state 必須留下 [STATE] 記錄"
        assert state_logs[0].levelno == logging.INFO
        assert {"type": "state", "state": "SPEAKING"} in ws.sent

    def test_set_state_dedup_does_not_relog(self, caplog):
        """狀態未變更（dedup early-return）不得重複寫 log／重複通知。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, ws, _ = _make_session()

        async def _run():
            await session._set_state("SPEAKING")
            await session._set_state("SPEAKING")

        asyncio.run(_run())

        assert len(_records_with(caplog, "[STATE] SPEAKING")) == 1
        assert len([m for m in ws.sent if m.get("type") == "state"]) == 1

    def test_interrupt_origin_recorded_in_barge_reason(self, caplog):
        """interrupt 帶 origin → barge reason 記成 interrupt:<origin>（log 可分辨分支）。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, ws, streamer = _make_session()

        async def _run():
            await session.on_interrupt("autoBargeIn")
            await session.on_interrupt("manualBargeIn")

        asyncio.run(_run())

        auto = _records_with(caplog, "[UTT] barge reason=interrupt:autoBargeIn")
        manual = _records_with(caplog, "[UTT] barge reason=interrupt:manualBargeIn")
        assert len(auto) == 1, "autoBargeIn 分支必須可從 log 分辨"
        assert len(manual) == 1, "manualBargeIn 分支必須可從 log 分辨"
        assert auto[0].levelno == logging.WARNING
        assert streamer.interrupt_calls == 2, "兩種 origin 都仍必須真的打斷 streamer"

    def test_interrupt_without_origin_keeps_legacy_reason(self, caplog):
        """舊 client（無 origin）→ 維持既有 reason=interrupt（既有測試/協定相容，不得改成 interrupt:）。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, _ws, _streamer = _make_session()

        async def _run():
            await session.on_interrupt()

        asyncio.run(_run())

        legacy = [
            r for r in _records_with(caplog, "[UTT] barge reason=interrupt")
            if "reason=interrupt gen:" in r.getMessage()
        ]
        assert len(legacy) == 1, "無 origin 時必須維持 legacy reason=interrupt"

    def test_interrupt_origin_is_sanitized(self, caplog):
        """origin 淨化：只允許 ASCII 英數與 _-:（防 log 注入）。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, _ws, _streamer = _make_session()

        async def _run():
            await session.on_interrupt("evil origin\ninjected=1")

        asyncio.run(_run())

        recs = _records_with(caplog, "[UTT] barge reason=interrupt:")
        assert len(recs) == 1
        msg = recs[0].getMessage()
        assert "\n" not in msg, "log 注入必須被擋掉"
        m = re.search(r"reason=interrupt:(\S*) gen:", msg)
        assert m, f"reason 格式不符：{msg!r}"
        assert m.group(1) == "evilorigininjected1", f"淨化結果不符：{m.group(1)!r}"

    def test_dispatch_passes_origin_from_ws_payload(self):
        """WS 分派：interrupt 訊息的 origin 欄位必須被傳入 on_interrupt（結構化抽取）。"""
        server_src = SERVER_SRC_PATH.read_text(encoding="utf-8")
        m = re.search(
            r'elif mtype == "interrupt":\s*\n\s*(await session\.on_interrupt\(.*\))',
            server_src,
        )
        assert m, "找不到 interrupt 分派分支（或分派未傳 origin）"
        assert m.group(1).strip() == 'await session.on_interrupt(data.get("origin"))', (
            f"分派必須把 payload 的 origin 傳入 on_interrupt，實得 {m.group(1)!r}"
        )


class TestFPreAudioDetection:
    """(b) 伺服器端「只偵測、不改行為」：本回合零音訊就被打斷 → WARNING，但取消行為不變。"""

    def test_preaudio_interrupt_logged_and_still_cancels(self, caplog):
        """first_chunk_time is None（本回合零音訊）→ [BARGE-PREAUDIO] WARNING，且照常取消。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, ws, streamer = _make_session(first_chunk_time=None)

        async def _run():
            session._generation = 3          # 對齊事故日誌 gen=3
            session.state = session.STATE_SPEAKING  # 事故實況：伺服器已宣告 SPEAKING（沉默窗內）
            await session.on_interrupt("autoBargeIn")

        asyncio.run(_run())

        recs = _records_with(caplog, "[BARGE-PREAUDIO]")
        assert len(recs) == 1, "沉默窗（零音訊）打斷必須留痕"
        assert recs[0].levelno == logging.WARNING
        assert recs[0].getMessage() == "[BARGE-PREAUDIO] turn=3 origin=autoBargeIn audio_written=0"
        # 只偵測、不改行為：取消照常發生
        assert streamer.interrupt_calls == 1, "取消行為不得被偵測碼改動"
        assert session._generation == 4, "generation 仍須推進（原取消語意）"
        assert {"type": "state", "state": "IDLE"} in ws.sent

    def test_no_preaudio_warning_once_audio_written(self, caplog):
        """first_chunk_time 有值（本回合已有音訊）→ 不得誤報 pre-audio；取消照常。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        session, _ws, streamer = _make_session(first_chunk_time=123.0)

        async def _run():
            await session.on_interrupt("manualBargeIn")

        asyncio.run(_run())

        assert _records_with(caplog, "[BARGE-PREAUDIO]") == [], "已有音訊不得誤報 pre-audio"
        assert len(_records_with(caplog, "[UTT] barge reason=interrupt:manualBargeIn")) == 1
        assert streamer.interrupt_calls == 1

    def test_cancel_is_unconditional_not_gated_on_audio_written(self):
        """(c) 取消不得被 gate 在「是否已有音訊」上：_barge 呼叫必須在 on_interrupt 頂層述句。

        若把它移進 `if first_chunk_time is None:` 之內 → 沉默窗內的 interrupt 會被擋掉，
        而 client auto 路徑送 interrupt 後立刻 sendPttStart()（伺服器視為合法打斷）⇒ 連帶鎖住 PTT。
        """
        src = textwrap.dedent(_python_func_source("on_interrupt"))
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        top_calls = []
        for stmt in fn.body:  # 只看頂層述句（不含任何 if/for/while 內部）
            for node in ast.walk(stmt):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_barge"
                ):
                    top_calls.append(stmt)
        assert top_calls, "_barge 必須是 on_interrupt 的無條件頂層呼叫（不得被 gate）"

    def test_preaudio_detection_uses_turn_scoped_signal(self):
        """偵測訊號必須是回合級 first_chunk_time（_run_reply 每回合重置），不是累計計數。"""
        on_interrupt = _python_func_source("on_interrupt")
        assert 'getattr(self._sink, "first_chunk_time", None) is None' in on_interrupt
        run_reply = _python_func_source("_run_reply")
        assert "self._sink.first_chunk_time = None" in run_reply, (
            "first_chunk_time 必須在回合開始時重置（否則偵測會跨回合殘留）"
        )


class TestGCacheAndDeploymentGuards:
    def test_index_response_forbids_caching(self):
        """(洞 3) 頁面回應必須帶 no-store：否則「改了、重載了、卻還是舊行為」。"""
        handler = _python_func_source("index_handler")
        assert '"Cache-Control": "no-store, no-cache, must-revalidate"' in handler
        assert '"Pragma": "no-cache"' in handler

    def test_index_http_response_headers_observed_over_http(self):
        """(洞 3) 實測 HTTP 回應標頭（in-process TestServer，臨時埠，0 生產影響）。"""
        from aiohttp.test_utils import TestClient, TestServer

        from clients.voice_companion.web_server import build_app

        async def _run():
            app = build_app(
                WEB_TEST_CONFIG,
                brain=object(),
                refiner=object(),
                asr=object(),
                streamer_factory=lambda sink: _StubStreamer(),
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                resp = await client.get("/")
                assert resp.status == 200
                body = await resp.text()
                assert "no-store" in resp.headers.get("Cache-Control", ""), (
                    f"缺少 Cache-Control: no-store，實得 {resp.headers.get('Cache-Control')!r}"
                )
                assert resp.headers.get("Pragma") == "no-cache"
                # 回應內容確實是現行原始碼渲染（in-process 即時渲染）
                assert "isBargeArmed" in body
            finally:
                await client.close()

        asyncio.run(_run())

    def test_optional_is_imported_for_origin_signature(self):
        """(洞 1) on_interrupt(origin: Optional[str]) 的型別必須可解析（不留 NameError 隱患）。"""
        src = SERVER_SRC_PATH.read_text(encoding="utf-8")
        assert re.search(r"^from typing import .*\bOptional\b", src, re.M), "缺 Optional import"
        module = importlib.import_module("clients.voice_companion.web_server")
        assert hasattr(module, "Optional"), "Optional 必須在模組命名空間中可見"
        assert hasattr(module, "Callable") and hasattr(module, "List")
        # 呼叫端真的能帶 origin（行為層證明：不會在 WS 收到 interrupt 時才爆 NameError）
        session, _ws, _streamer = _make_session()
        asyncio.run(session.on_interrupt("autoBargeIn"))

    def test_barge_module_has_no_new_ws_message_types(self):
        """WS 訊息型別集合不變（origin 只是既有 interrupt payload 的附加欄位）。"""
        server_src = SERVER_SRC_PATH.read_text(encoding="utf-8")
        handled = set(re.findall(r'mtype == "([a-z_]+)"', server_src))
        assert handled == {"ptt_start", "ptt_stop", "interrupt", "text", "ping"}
        client_types = set(re.findall(r'send\(\{\s*type:\s*"([a-zA-Z_]+)"', _JS))
        assert client_types <= handled | {"pong", "state", "transcript", "error"}, (
            f"client 送了伺服器未處理的型別：{client_types - handled}"
        )


# ─────────────────────────────────────────────────────────────
# Test F：協定/護欄相容
# ─────────────────────────────────────────────────────────────

class TestFProtocolCompat:
    def test_interrupt_send_form_still_present(self):
        """既有發送形式 send({ type: "interrupt" }) 必須保留（VC-VAD-TIMING-1 護欄與 WS 協定相容）。"""
        block = _mic_barge_block()
        assert len(re.findall(r"send\(\{\s*type:\s*\"interrupt\"\s*\}\)", block)) == 2

    def test_origin_is_attached_at_both_barge_sites(self):
        """兩個 barge 分支各自把 origin 設為可分辨字串，且 send() 是唯一出口（附帶 origin）。"""
        block = _mic_barge_block()
        assert 'interruptOrigin = "autoBargeIn";' in block
        assert 'interruptOrigin = "manualBargeIn";' in block
        send_fn = _extract_js_block(HTML_PAGE, "function send(obj)")
        assert 'obj.origin = interruptOrigin;' in send_fn
        assert 'obj.type === "interrupt"' in send_fn

    def test_barge_thresholds_untouched(self):
        """barge 能量門檻原封不動（本票只改 armed 條件，不改門檻）。"""
        assert "BARGE_AUTO_THRESHOLD = 0.04" in HTML_PAGE
        assert "BARGE_AUTO_MS = 200" in HTML_PAGE
        assert "BARGE_MS = 150" in HTML_PAGE

    def test_server_speaking_announcement_position_untouched(self):
        """設計拍板：不得動 _run_reply 開頭宣告 SPEAKING 的位置（SPEAKING 另有 UI/反回音用途）。"""
        run_reply = _python_func_source("_run_reply")
        i_speaking = run_reply.index("await self._set_state(self.STATE_SPEAKING)")
        i_awaiting = run_reply.index("reply-awaiting-llm")
        assert i_speaking < i_awaiting, "SPEAKING 仍須在 reply-awaiting-llm 之前宣告（伺服器語意不動）"
