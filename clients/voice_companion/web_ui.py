"""
web_ui.py — 黑川茜 Web 語音伴侶前端（VC-1.3；VC-1.4 可視化 + 失敗透通）。

單頁 UI（HTML+CSS+JS 全內嵌，繁體中文，無建置步驟）。瀏覽器端負責：
- 收音：getUserMedia（echoCancellation/noiseSuppression）→ 降採樣 16k PCM → WS binary
- PTT 按鈕（按住說話，滑鼠/觸碰/空白鍵）＋ Auto-VAD 切換（本地 RMS）
- 放音：AudioContext 44.1k，收 WS binary（Int16 PCM）→ Float32 佇列餵 ScriptProcessor
- 打斷：播放中本地收音 RMS 超門檻 150ms → 送 WS interrupt
- 可視化（VC-1.4）：即時輸入音量表（AnalyserNode getByteTimeDomainData RMS → 水平 bar）＋
  「🎙️ 傳送中」指示；server `{"type":"error"}` 事件顯示於 #errorBox（黃底紅字，
  402 額度等失敗原因透通），下一個 utterance 開始時自動清除

畫面關鍵元素（測試依賴）：#micBtn（PTT 按鈕）、#statusText（狀態文字）、
#errorBox（錯誤訊息區）、#meterBar/#meterFill/#meterLabel（輸入音量表）。
"""

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>黑川茜 · 語音伴侶</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    background: #14141c; color: #e8e6f0;
    min-height: 100vh; display: flex; flex-direction: column; align-items: center;
    padding: 24px 16px; gap: 16px;
  }
  header { font-size: 22px; font-weight: 600; letter-spacing: 1px; }
  header small { display: block; font-size: 12px; color: #9a94b0; text-align: center; }
  #statusBar {
    display: flex; align-items: center; gap: 10px; background: #1e1e2a;
    padding: 10px 18px; border-radius: 999px; font-size: 15px;
  }
  .dot { width: 14px; height: 14px; border-radius: 50%; display: inline-block; }
  .dot.idle { background: #3fae5a; box-shadow: 0 0 8px #3fae5a; }
  .dot.listening { background: #3fae5a; box-shadow: 0 0 12px #3fae5a; animation: pulse 1.2s infinite; }
  .dot.thinking { background: #d9a414; box-shadow: 0 0 10px #d9a414; animation: pulse 0.8s infinite; }
  .dot.speaking { background: #e25555; box-shadow: 0 0 12px #e25555; animation: pulse 0.6s infinite; }
  @keyframes pulse { 50% { opacity: 0.45; } }
  #micBtn {
    width: 180px; height: 180px; border-radius: 50%; border: none; cursor: pointer;
    background: radial-gradient(circle at 30% 30%, #4a4a66, #2a2a3c);
    color: #fff; font-size: 18px; font-weight: 600; letter-spacing: 1px;
    touch-action: none; user-select: none; transition: transform 0.08s;
  }
  #micBtn:active, #micBtn.hold { background: radial-gradient(circle at 30% 30%, #7c4f9e, #4a2a66); transform: scale(0.96); }
  #micBtn:disabled { opacity: 0.5; cursor: not-allowed; }
  .controlRow { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; justify-content: center; }
  .controlRow label { font-size: 14px; display: flex; align-items: center; gap: 6px; cursor: pointer; }
  #chat {
    width: min(640px, 96vw); height: 300px; overflow-y: auto; background: #1a1a26;
    border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 8px;
    font-size: 14px; line-height: 1.6;
  }
  .msg { max-width: 85%; padding: 8px 12px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: #2d3a56; }
  .msg.akane { align-self: flex-start; background: #3a2d52; }
  .msg .who { font-size: 11px; opacity: 0.65; margin-bottom: 2px; }
  #typeRow { display: flex; gap: 8px; width: min(640px, 96vw); }
  #textInput { flex: 1; padding: 10px 12px; border-radius: 10px; border: 1px solid #3a3a4e; background: #1e1e2a; color: #e8e6f0; font-size: 14px; }
  #sendText { padding: 10px 18px; border-radius: 10px; border: none; background: #4a4a66; color: #fff; cursor: pointer; font-size: 14px; }
  #errorBox {
    display: none; align-items: center; justify-content: space-between; gap: 8px;
    color: #ff6b6b; background: rgba(255, 210, 60, 0.14); border: 1px solid rgba(255, 210, 60, 0.45);
    font-size: 13px; min-height: 20px; max-width: 640px; text-align: center;
    padding: 6px 12px; border-radius: 8px; width: min(640px, 96vw);
  }
  #errorText { flex: 1; }
  #errorDismiss { background: none; border: none; color: #ffd23c; cursor: pointer; font-size: 15px; padding: 0 2px; }
  #meterWrap { width: min(640px, 96vw); display: flex; flex-direction: column; gap: 4px; }
  #meterLabel { font-size: 12px; color: #9a94b0; letter-spacing: 0.5px; }
  #meterBar { height: 10px; border-radius: 999px; background: #2a2a3c; overflow: hidden; }
  #meterFill {
    height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, #3fae5a, #d9a414 70%, #e25555);
    transition: width 0.06s linear;
  }
  .hint { font-size: 12px; color: #8a84a0; margin-top: -8px; }
</style>
</head>
<body>
  <!-- 提示：若長時間無反應，請檢查 Fish API 額度（402：Insufficient API credit）。ASR 與 TTS 共用同一筆額度 -->
  <header>黑川茜<small>Web 語音伴侶 · VC-1.3</small></header>
  <div id="statusBar">
    <span id="statusDot" class="dot idle"></span>
    <span id="statusText">🟢 聆聽</span>
  </div>
  <button id="micBtn" disabled>按住說話</button>
  <div class="controlRow">
    <label><input type="checkbox" id="autoVad"> Auto-VAD（自動聆聽）</label>
    <span class="hint">PTT：按住說話（滑鼠 / 觸碰 / 空白鍵）</span>
  </div>
  <div id="meterWrap">
    <div id="meterLabel">麥克風待命</div>
    <div id="meterBar"><div id="meterFill"></div></div>
  </div>
  <div id="chat"></div>
  <div id="typeRow">
    <input id="textInput" placeholder="打字給茜…（Enter 送出）" autocomplete="off">
    <button id="sendText">送出</button>
  </div>
  <div id="errorBox"><span id="errorText"></span><button id="errorDismiss" title="關閉">✕</button></div>
<script>
(function () {
  "use strict";
  var state = "IDLE";
  var ws = null, audioCtx = null, micStream = null;
  var recNode = null, playNode = null, micSource = null, analyser = null;
  var playQueue = [];                 // Float32 播放佇列（Int16 → /32768）
  var MAX_PLAY_BUFFER = 8820;         // 200ms @44.1k（超過即丟棄防堆積）
  var pttActive = false, autoSpeaking = false, autoSilenceMs = 0;
  var speakEnergyMs = 0;
  var VAD_THRESHOLD = 0.02, VAD_SILENCE_MS = 500, BARGE_MS = 150;
  var $ = function (id) { return document.getElementById(id); };

  function setState(s) {
    state = s;
    var dot = $("statusDot"), txt = $("statusText");
    dot.className = "dot " + (s === "SPEAKING" ? "speaking" : s === "THINKING" ? "thinking" : s === "LISTENING" ? "listening" : "idle");
    txt.textContent = s === "SPEAKING" ? "🔴 茜說話中…（開口可打斷）" : s === "THINKING" ? "🟡 思考中…" : s === "LISTENING" ? "🟢 聆聽中…" : "🟢 聆聽";
    var btn = $("micBtn");
    if (s === "LISTENING") { btn.classList.add("hold"); } else { btn.classList.remove("hold"); }
    if (s !== "SPEAKING") { playQueue.length = 0; } // 非說話狀態 → 清播放佇列（打斷靜音）
  }
  function addMsg(role, text) {
    var chat = $("chat"), div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = '<div class="who">' + (role === "user" ? "你" : "茜") + "</div>" + escapeHtml(text);
    chat.appendChild(div); chat.scrollTop = chat.scrollHeight;
  }
  function escapeHtml(t) {
    return String(t).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function setError(m, persistent) {
    var box = $("errorBox");
    box.dataset.persistent = persistent ? "1" : "";
    box.style.display = m ? "flex" : "none";
    $("errorText").textContent = m;
  }
  function showError(m) {
    // 暫態錯誤：6 秒後自動清除
    setError(m, false);
    if (m) { setTimeout(function () { setError("", false); }, 6000); }
  }
  function persistentError(m) {
    // 常駐錯誤（VC-1.5）：不自動清除，直到點擊 ✕ 或重新載入
    setError(m, true);
  }
  function send(obj) { if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); } }
  function sendPttStart() {
    if (pttActive) { return; }
    if (!micStream) {
      persistentError("⚠️ 麥克風尚未就緒：請允許麥克風權限（瀏覽器會彈出詢問）");
      return;
    }
    pttActive = true;
    if ($("errorBox").dataset.persistent !== "1") { setError("", false); }  // 下一個 utterance 開始 → 自動清除暫態錯誤
    send({ type: "ptt_start" });
  }
  function sendPttStop() { if (!pttActive) { return; } pttActive = false; send({ type: "ptt_stop" }); }
  $("errorDismiss").addEventListener("click", function () { setError("", false); });

  // ── 輸入音量表（VC-1.4：AnalyserNode RMS → 水平 bar ＋ 傳送中指示）──
  function updateMeter() {
    var fill = $("meterFill"), label = $("meterLabel");
    if (!analyser) {
      fill.style.width = "0%";
      label.textContent = "麥克風待命";
      requestAnimationFrame(updateMeter);
      return;
    }
    var buf = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buf);
    var sum = 0;
    for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
    var rms = Math.sqrt(sum / buf.length);
    fill.style.width = Math.min(100, Math.round(rms * 400)) + "%";
    label.textContent = (state === "LISTENING" || pttActive || autoSpeaking) ? "🎙️ 傳送中" : "麥克風待命";
    requestAnimationFrame(updateMeter);
  }

  // ── WebSocket ──
  function connect() {
    var proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(proto + "://" + location.host + "/ws");
    ws.binaryType = "arraybuffer";
    ws.onopen = function () { $("micBtn").disabled = false; showError(""); };
    ws.onclose = function () { $("micBtn").disabled = true; setState("IDLE"); showError("連線已中斷，重新載入頁面重連。"); };
    ws.onerror = function () { showError("WebSocket 錯誤"); };
    ws.onmessage = function (ev) {
      if (typeof ev.data === "string") {
        var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (msg.type === "state") { setState(msg.state); }
        else if (msg.type === "transcript") { addMsg(msg.role, msg.text); }
        else if (msg.type === "error") { showError(msg.message || "錯誤"); }
      } else {
        // binary = Int16 PCM 44.1k mono 播放分片
        var i16 = new Int16Array(ev.data);
        if (state === "SPEAKING" && playQueue.length < MAX_PLAY_BUFFER) {
          for (var i = 0; i < i16.length; i++) { playQueue.push(i16[i] / 32768.0); }
        }
      }
    };
  }

  // ── 放音（44.1k）──
  function ensureAudioResume() {
    // 瀏覽器自動播放政策：非手勢建立的 AudioContext 會停在 suspended → 無聲
    if (audioCtx && audioCtx.state === "suspended" && audioCtx.resume) { audioCtx.resume(); }
  }

  function startPlayback() {
    audioCtx = new AudioContext({ sampleRate: 44100 });
    if (audioCtx.state === "suspended" && audioCtx.resume) { audioCtx.resume(); }
    playNode = audioCtx.createScriptProcessor(2048, 0, 1);
    playNode.onaudioprocess = function (e) {
      var out = e.outputBuffer.getChannelData(0);
      for (var i = 0; i < out.length; i++) { out[i] = playQueue.length ? playQueue.shift() : 0; }
    };
    playNode.connect(audioCtx.destination);
  }

  // ── 收音（getUserMedia → 降採樣 16k → Int16 → WS binary）──
  function resampleTo16k(input, ratio) {
    // 線性插值降採樣：input 為 Float32Array（監聽率），ratio = 16000 / srcRate
    var src = input, n = Math.floor(input.length * ratio);
    if (n <= 0) { return new Float32Array(0); }
    var out = new Float32Array(n);
    for (var i = 0; i < n; i++) {
      var pos = i / ratio;
      var i0 = Math.floor(pos), frac = pos - i0;
      var i1 = i0 + 1 < src.length ? i0 + 1 : i0;
      out[i] = src[i0] * (1 - frac) + src[i1] * frac;
    }
    return out;
  }
  function startMic() {
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      .then(function (stream) {
        micStream = stream;
        if (!audioCtx) { startPlayback(); }
        micSource = audioCtx.createMediaStreamSource(stream);
        recNode = audioCtx.createScriptProcessor(4096, 1, 1);
        var ratio = 16000 / audioCtx.sampleRate;
        recNode.onaudioprocess = function (e) {
          var ch = e.inputBuffer.getChannelData(0);
          // 本地 RMS（Auto-VAD 與打斷偵測用）
          var sum = 0, len = ch.length;
          for (var k = 0; k < len; k++) { sum += ch[k] * ch[k]; }
          var rms = Math.sqrt(sum / len);
          var durMs = len / audioCtx.sampleRate * 1000;
          if (state === "SPEAKING") {
            speakEnergyMs = rms > VAD_THRESHOLD ? speakEnergyMs + durMs : 0;
            if (speakEnergyMs >= BARGE_MS) { speakEnergyMs = 0; send({ type: "interrupt" }); }
          }
          var auto = $("autoVad").checked;
          if (auto) {
            if (rms > VAD_THRESHOLD) {
              autoSilenceMs = 0;
              if (!autoSpeaking) { autoSpeaking = true; sendPttStart(); }
            } else {
              autoSilenceMs += durMs;
              if (autoSpeaking && autoSilenceMs >= VAD_SILENCE_MS) { autoSpeaking = false; sendPttStop(); }
            }
          }
          // 只有 LISTENING 才把音訊送伺服器（binary = Int16 PCM 16k mono）
          if (ws && ws.readyState === WebSocket.OPEN && (pttActive || auto)) {
            var s16 = resampleTo16k(ch, ratio);
            var buf = new Int16Array(s16.length);
            for (var j = 0; j < s16.length; j++) {
              var v = s16[j] * 32767;
              buf[j] = v > 32767 ? 32767 : v < -32768 ? -32768 : v | 0;
            }
            ws.send(buf.buffer);
          }
        };
        micSource.connect(recNode);
        analyser = audioCtx.createAnalyser();  // 音量表資料源（VC-1.4）
        analyser.fftSize = 512;
        micSource.connect(analyser);
        var silentGain = audioCtx.createGain(); silentGain.gain.value = 0; // 靜音目的地：驅動 onaudioprocess 且無回授
        recNode.connect(silentGain);
        silentGain.connect(audioCtx.destination);
        showError("");
        requestAnimationFrame(updateMeter);  // 啟動音量表繪製迴圈
      })
      .catch(function (err) {
        // VC-1.5：錯誤分類常駐顯示（不自動清除）
        var name = err && err.name ? err.name : "UnknownError";
        var hint;
        if (name === "NotAllowedError") { hint = "權限被拒：請在瀏覽器網址列鎖頭允許麥克風權限"; }
        else if (name === "SecurityError") { hint = "不安全來源：麥克風需要 HTTPS 或 localhost"; }
        else if (name === "NotFoundError") { hint = "找不到麥克風裝置，請檢查連接"; }
        else { hint = (err && err.message) || "未知錯誤"; }
        persistentError("⚠️ 麥克風無法啟動（" + name + "）：" + hint);
      });
  }

  // ── 事件綁定 ──
  var micBtn = $("micBtn");
  micBtn.addEventListener("pointerdown", function (e) { e.preventDefault(); ensureAudioResume(); sendPttStart(); });
  micBtn.addEventListener("pointerup", sendPttStop);
  micBtn.addEventListener("pointerleave", sendPttStop);
  micBtn.addEventListener("pointercancel", sendPttStop);
  document.addEventListener("pointerdown", function () { ensureAudioResume(); }); // 任意首次點擊解凍播放
  document.addEventListener("keydown", function (e) {
    if (e.code === "Space" && !e.repeat && document.activeElement !== $("textInput")) { e.preventDefault(); if (!pttActive) { sendPttStart(); } }
  });
  document.addEventListener("keyup", function (e) { if (e.code === "Space") { sendPttStop(); } });
  $("sendText").addEventListener("click", function () {
    var t = $("textInput").value.trim();
    if (t) { send({ type: "text", text: t }); $("textInput").value = ""; }
  });
  $("textInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      var t = e.target.value.trim();
      if (t) { send({ type: "text", text: t }); e.target.value = ""; }
    }
  });
  $("autoVad").addEventListener("change", function () {
    if (!this.checked) { autoSpeaking = false; autoSilenceMs = 0; sendPttStop(); }
  });

  connect();
  micBtn.addEventListener("click", function () { startMic(); }, { once: true });

  // VC-1.5：不安全來源常駐提示（getUserMedia 只在 HTTPS 或 localhost 可用）
  if (window.isSecureContext === false) {
    persistentError(
      "⚠️ 麥克風需要 HTTPS 或 localhost 才可使用。請改開 https://" + location.hostname + ":8765（接受憑證警告）或 http://127.0.0.1:8765"
    );
  }
})();
</script>
</body>
</html>
"""