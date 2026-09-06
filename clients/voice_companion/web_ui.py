"""
web_ui.py — 黑川茜 Web 語音伴侶前端（VC-1.3；VC-1.4 可視化 + 失敗透通）。

單頁 UI（HTML+CSS+JS 全內嵌，繁體中文，無建置步驟）。瀏覽器端負責：
- 收音：getUserMedia（echoCancellation/noiseSuppression）→ 降採樣 16k PCM → WS binary
- PTT 按鈕（按住說話，滑鼠/觸碰/空白鍵）＋ Auto-VAD 切換（本地 RMS）
- 放音（VC-2.3-04）：AudioWorklet 獨立音訊執行緒＋Float32 環形緩衝區（Blob URL 動態註冊），相容 ScriptProcessor fallback
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
  var workletNode = null, workletReady = false, workletLoading = false;
  var pendingAudioChunks = [];        // Worklet 加載期間暫存分片
  var MAX_PLAY_BUFFER = 44100 * 30;   // 30s @44.1k 環形緩衝容量
  var fallbackCap = MAX_PLAY_BUFFER;
  var fallbackBuf = new Float32Array(fallbackCap);
  var fallbackRead = 0, fallbackWrite = 0, fallbackAvail = 0;
  var pttActive = false, autoSpeaking = false, autoSilenceMs = 0, autoHoldFrames = 0, autoVoiceMs = 0;
  var speakEnergyMs = 0, barging = false;
  var VAD_THRESHOLD = 0.02, VAD_SILENCE_MS = 500, BARGE_MS = 150, BARGE_AUTO_THRESHOLD = 0.04, BARGE_AUTO_MS = 200, AUTO_START_MS = 260, AUTO_HOLD_MS = 1800;
  var $ = function (id) { return document.getElementById(id); };

  function setState(s) {
    var leavingSpeaking = (state === "SPEAKING") && s !== "SPEAKING";
    state = s;
    if (workletReady && workletNode) {
      workletNode.port.postMessage({ type: "state", state: s });
    }
    if (s === "SPEAKING") { ensurePlayback(); } // 茜開始說話 → 確保播放圖存在（打字路徑也能出聲）
    if (leavingSpeaking) {
      if (barging) {
        // 使用者主動開口打斷（VC-2.2 Barge-in）→ 豁免講完冷卻期，直接收音
        autoHoldFrames = 0;
        barging = false;
      } else {
        // 茜自然講完 → AUTO_HOLD_MS 不聽：喇叭尾音/殘響不該觸發 Auto-VAD（曾 0.6s 太短仍回授）
        autoHoldFrames = Math.ceil(AUTO_HOLD_MS * (audioCtx ? audioCtx.sampleRate : 44100) / 1000 / 4096);
      }
      autoVoiceMs = 0;
    }
    var dot = $("statusDot"), txt = $("statusText");
    dot.className = "dot " + (s === "SPEAKING" ? "speaking" : s === "THINKING" ? "thinking" : s === "LISTENING" ? "listening" : "idle");
    txt.textContent = s === "SPEAKING" ? "🔴 茜說話中…（開口可打斷）" : s === "THINKING" ? "🟡 思考中…" : s === "LISTENING" ? "🟢 聆聽中…" : "🟢 聆聽";
    var btn = $("micBtn");
    if (s === "LISTENING") { btn.classList.add("hold"); } else { btn.classList.remove("hold"); }
    // 注意：不在此清空環形緩衝區 —— IDLE 不代表瀏覽器已播完（伺服器 send 完即回 IDLE），
    // 過早清空會砍掉未播音訊（「只聽到開頭兩字、後半省略」）。只在打斷/新回合時 flush（flushPlayback）。
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
    flushPlayback();  // 新回合開始 → 中斷上一輪殘音（barge-in 語意）
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

  // ── WebSocket 與自動重連（VC-2.1）──
  var reconnectAttempts = 0, reconnectTimer = null, heartbeatTimer = null;
  var PREBUFFER_SAMPLES = 3500; // ~80ms @44.1k：行動網路防抖動預緩衝
  var isBuffering = true;

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectAttempts++;
    var delay = Math.min(10000, Math.floor(1000 * Math.pow(1.5, reconnectAttempts - 1)));
    var dot = $("statusDot"), txt = $("statusText");
    dot.className = "dot thinking";
    txt.textContent = "🟡 連線中斷，正在自動重連 (第 " + reconnectAttempts + " 次)…";
    reconnectTimer = setTimeout(function () {
      connect();
    }, delay);
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(function () {
      if (ws && ws.readyState === WebSocket.OPEN) {
        send({ type: "ping" });
      }
    }, 25000);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function getAuthToken() {
    try {
      var hash = window.location.hash || "";
      if (hash.indexOf("#") === 0) { hash = hash.substring(1); }
      var pairs = hash.split("&");
      for (var i = 0; i < pairs.length; i++) {
        var part = pairs[i].split("=");
        if (decodeURIComponent(part[0]) === "token" && part.length > 1) {
          return decodeURIComponent(part[1]);
        }
      }
    } catch (e) {}
    return "";
  }

  function connect() {
    clearTimeout(reconnectTimer);
    if (ws) {
      try {
        ws.onopen = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        ws.close();
      } catch (e) {}
      ws = null;
    }
    var proto = location.protocol === "https:" ? "wss" : "ws";
    var token = getAuthToken();
    var wsUrl = proto + "://" + location.host + "/ws" + (token ? "?token=" + encodeURIComponent(token) : "");
    ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    ws.onopen = function () {
      reconnectAttempts = 0;
      clearTimeout(reconnectTimer);
      $("micBtn").disabled = false;
      setState("IDLE");
      showError("");
      startHeartbeat();
    };
    ws.onclose = function () {
      stopHeartbeat();
      $("micBtn").disabled = true;
      setState("IDLE");
      scheduleReconnect();
    };
    ws.onerror = function () {
      // 錯誤伴隨 onclose 事件，由 onclose 統一調度重連
    };
    ws.onmessage = function (ev) {
      if (typeof ev.data === "string") {
        var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        if (msg.type === "state") { setState(msg.state); }
        else if (msg.type === "transcript") { addMsg(msg.role, msg.text); }
        else if (msg.type === "error") { showError(msg.message || "錯誤"); }
        else if (msg.type === "pong") { /* 心跳正常回應 */ }
      } else {
        // binary = Int16 PCM 44.1k mono 播放分片（server 固定 44100）
        ensurePlayback();
        var i16 = new Int16Array(ev.data);
        if (state === "SPEAKING") {
          var rate = audioCtx ? audioCtx.sampleRate : 44100;
          var f32in = new Float32Array(i16.length);
          for (var j = 0; j < i16.length; j++) { f32in[j] = i16[j] / 32768.0; }
          if (rate !== 44100) {
            // ctx 實際採樣率 ≠44100（裝置端）→ 線性重採樣，避免變調（怪聲）
            var rs = resampleLinear(f32in, 44100, rate);
            queuePlaybackSamples(rs);
          } else {
            queuePlaybackSamples(f32in);
          }
        }
      }
    };
  }

  // ── 放音（44.1k：AudioWorklet 獨立音訊執行緒 ＋ 0 複製環形緩衝區，相容 ScriptProcessor fallback）──
  var WORKLET_CODE = [
    "class AkaneAudioProcessor extends AudioWorkletProcessor {",
    "  constructor() {",
    "    super();",
    "    this.capacity = 44100 * 30;",
    "    this.buffer = new Float32Array(this.capacity);",
    "    this.readIndex = 0;",
    "    this.writeIndex = 0;",
    "    this.available = 0;",
    "    this.isBuffering = true;",
    "    this.prebufferSamples = 3500;",
    "    this.speaking = false;",
    "    this.port.onmessage = (e) => {",
    "      var d = e.data;",
    "      if (!d) return;",
    "      if (d.type === 'audio') {",
    "        var s = d.samples, len = s.length;",
    "        for (var i = 0; i < len; i++) {",
    "          if (this.available < this.capacity) {",
    "            this.buffer[this.writeIndex] = s[i];",
    "            this.writeIndex = (this.writeIndex + 1) % this.capacity;",
    "            this.available++;",
    "          } else {",
    "            this.buffer[this.writeIndex] = s[i];",
    "            this.writeIndex = (this.writeIndex + 1) % this.capacity;",
    "            this.readIndex = (this.readIndex + 1) % this.capacity;",
    "          }",
    "        }",
    "      } else if (d.type === 'flush') {",
    "        var fade = Math.min(128, this.available);",
    "        for (var k = 0; k < fade; k++) {",
    "          var idx = (this.readIndex + k) % this.capacity;",
    "          this.buffer[idx] *= (1.0 - k / fade);",
    "        }",
    "        this.available = fade;",
    "        this.writeIndex = (this.readIndex + fade) % this.capacity;",
    "        this.isBuffering = true;",
    "      } else if (d.type === 'state') {",
    "        this.speaking = (d.state === 'SPEAKING');",
    "      }",
    "    };",
    "  }",
    "  process(inputs, outputs) {",
    "    var out = outputs[0];",
    "    if (!out || !out[0]) return true;",
    "    var ch = out[0];",
    "    var n = ch.length;",
    "    if (this.isBuffering) {",
    "      if (this.available >= this.prebufferSamples || !this.speaking) {",
    "        this.isBuffering = false;",
    "      } else {",
    "        ch.fill(0);",
    "        return true;",
    "      }",
    "    }",
    "    for (var i = 0; i < n; i++) {",
    "      if (this.available > 0) {",
    "        ch[i] = this.buffer[this.readIndex];",
    "        this.readIndex = (this.readIndex + 1) % this.capacity;",
    "        this.available--;",
    "      } else {",
    "        ch[i] = 0;",
    "        this.isBuffering = true;",
    "      }",
    "    }",
    "    for (var c = 1; c < out.length; c++) { out[c].set(ch); }",
    "    return true;",
    "  }",
    "}",
    "registerProcessor('akane-audio-processor', AkaneAudioProcessor);"
  ].join('\\n');

  function pushFallbackSamples(s) {
    var len = s.length;
    for (var i = 0; i < len; i++) {
      if (fallbackAvail < fallbackCap) {
        fallbackBuf[fallbackWrite] = s[i];
        fallbackWrite = (fallbackWrite + 1) % fallbackCap;
        fallbackAvail++;
      } else {
        fallbackBuf[fallbackWrite] = s[i];
        fallbackWrite = (fallbackWrite + 1) % fallbackCap;
        fallbackRead = (fallbackRead + 1) % fallbackCap;
      }
    }
  }

  function flushFallbackBuffer() {
    var fade = Math.min(128, fallbackAvail);
    for (var k = 0; k < fade; k++) {
      var idx = (fallbackRead + k) % fallbackCap;
      fallbackBuf[idx] *= (1.0 - k / fade);
    }
    fallbackAvail = fade;
    fallbackWrite = (fallbackRead + fade) % fallbackCap;
    isBuffering = true;
  }

  function queuePlaybackSamples(f32) {
    if (workletReady && workletNode) {
      try {
        workletNode.port.postMessage({ type: "audio", samples: f32 }, [f32.buffer]);
      } catch (e) {
        workletNode.port.postMessage({ type: "audio", samples: f32 });
      }
    } else if (workletLoading) {
      if (pendingAudioChunks.length < 100) {
        pendingAudioChunks.push(f32);
      }
    } else {
      pushFallbackSamples(f32);
    }
  }

  function resampleLinear(input, fromRate, toRate) {
    // 線性插值重採樣：44100 輸入 → ctx 實際採樣率（防止裝置端 48k 造成變調）
    var ratio = toRate / fromRate;
    var n = Math.floor(input.length * ratio);
    if (n <= 0 || n === input.length) { return input; }
    var out = new Float32Array(n);
    for (var i = 0; i < n; i++) {
      var pos = i / ratio;
      var i0 = Math.floor(pos), frac = pos - i0;
      var i1 = i0 + 1 < input.length ? i0 + 1 : i0;
      out[i] = input[i0] * (1 - frac) + input[i1] * frac;
    }
    return out;
  }
  function ensureAudioResume() {
    // 瀏覽器自動播放政策：非手勢建立的 AudioContext 會停在 suspended → 無聲
    if (audioCtx && audioCtx.state === "suspended" && audioCtx.resume) { audioCtx.resume(); }
  }

  function initScriptProcessorFallback() {
    if (playNode) return;
    playNode = audioCtx.createScriptProcessor(2048, 0, 1);
    playNode.onaudioprocess = function (e) {
      var out = e.outputBuffer.getChannelData(0);
      if (isBuffering) {
        if (fallbackAvail >= PREBUFFER_SAMPLES || state !== "SPEAKING") {
          isBuffering = false;
        } else {
          for (var z = 0; z < out.length; z++) { out[z] = 0; }
          return;
        }
      }
      for (var i = 0; i < out.length; i++) {
        if (fallbackAvail > 0) {
          out[i] = fallbackBuf[fallbackRead];
          fallbackRead = (fallbackRead + 1) % fallbackCap;
          fallbackAvail--;
        } else {
          out[i] = 0;
          isBuffering = true;
        }
      }
    };
    playNode.connect(audioCtx.destination);
  }

  function initAudioWorklet() {
    if (!window.AudioWorkletNode || !audioCtx.audioWorklet) {
      initScriptProcessorFallback();
      return;
    }
    workletLoading = true;
    var blob = new Blob([WORKLET_CODE], { type: "application/javascript" });
    var blobUrl = URL.createObjectURL(blob);
    audioCtx.audioWorklet.addModule(blobUrl).then(function () {
      URL.revokeObjectURL(blobUrl);
      if (!audioCtx) return;
      workletNode = new AudioWorkletNode(audioCtx, "akane-audio-processor");
      workletNode.connect(audioCtx.destination);
      workletReady = true;
      workletLoading = false;
      workletNode.port.postMessage({ type: "state", state: state });
      while (pendingAudioChunks.length > 0) {
        var chunk = pendingAudioChunks.shift();
        try {
          workletNode.port.postMessage({ type: "audio", samples: chunk }, [chunk.buffer]);
        } catch (e) {
          workletNode.port.postMessage({ type: "audio", samples: chunk });
        }
      }
    }).catch(function (err) {
      console.warn("AudioWorklet failed, fallback to ScriptProcessor", err);
      workletLoading = false;
      workletReady = false;
      initScriptProcessorFallback();
      while (pendingAudioChunks.length > 0) {
        pushFallbackSamples(pendingAudioChunks.shift());
      }
    });
  }

  function startPlayback() {
    if (audioCtx) { ensureAudioResume(); return; } // 冪等：已存在則只解凍
    audioCtx = new AudioContext({ sampleRate: 44100 });
    if (audioCtx.state === "suspended" && audioCtx.resume) { audioCtx.resume(); }
    initAudioWorklet();
  }
  // 播放與麥克風解耦：任何觸發點（手勢/收到語音）確保播放圖存在並嘗試解凍
  function ensurePlayback() {
    if (audioCtx) { ensureAudioResume(); } else { startPlayback(); }
  }
  // 打斷/新回合才清播放佇列（正常播放由 onaudioprocess 自然消耗，勿在 IDLE 清空）
  function flushPlayback() {
    isBuffering = true;
    pendingAudioChunks = [];
    if (workletReady && workletNode) {
      workletNode.port.postMessage({ type: "flush" });
    }
    flushFallbackBuffer();
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
        ensurePlayback(); // 麥克風就緒 → 播放圖確保存在（冪等；播放不再依賴首次點擊 micBtn）
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
          var auto = $("autoVad").checked;
          if (autoHoldFrames > 0) { autoHoldFrames--; }
          // Auto-VAD 不聽自己喇叭：茜講話期間＋講完 holdoff 內不收音/不觸發（防回授迴圈）
          var autoMuted = auto && (state === "SPEAKING" || autoHoldFrames > 0);
          if (autoMuted && !autoSpeaking) { autoVoiceMs = 0; }
          if (autoMuted && autoSpeaking && !barging) { autoSpeaking = false; autoSilenceMs = 0; sendPttStop(); }

          if (state === "SPEAKING") {
            if (auto) {
              // VC-2.2 Auto-VAD Barge-in：提升門檻（0.04）防喇叭回授，連續發音 ≥ BARGE_AUTO_MS (200ms) 打斷
              speakEnergyMs = rms > BARGE_AUTO_THRESHOLD ? speakEnergyMs + durMs : 0;
              if (speakEnergyMs >= BARGE_AUTO_MS) {
                speakEnergyMs = 0;
                barging = true;
                flushPlayback();
                send({ type: "interrupt" });
                autoSpeaking = true;
                autoVoiceMs = 0;
                autoSilenceMs = 0;
                setState("LISTENING");
                sendPttStart();
              }
            } else {
              // 手動模式打斷
              speakEnergyMs = rms > VAD_THRESHOLD ? speakEnergyMs + durMs : 0;
              if (speakEnergyMs >= BARGE_MS) {
                speakEnergyMs = 0;
                flushPlayback();
                send({ type: "interrupt" });
              }
            }
          }

          if (auto && !autoMuted) {
            if (rms > VAD_THRESHOLD) {
              autoSilenceMs = 0;
              autoVoiceMs += durMs;
              // 需連續說話 ≥ AUTO_START_MS 才觸發：短促回音/殘響不誤開
              if (!autoSpeaking && autoVoiceMs >= AUTO_START_MS) { autoSpeaking = true; autoVoiceMs = 0; sendPttStart(); }
            } else {
              autoVoiceMs = 0;
              autoSilenceMs += durMs;
              if (autoSpeaking && autoSilenceMs >= VAD_SILENCE_MS) { autoSpeaking = false; sendPttStop(); }
            }
          }
          // 只有 LISTENING（auto）或 PTT 按住時才把音訊送伺服器（binary = Int16 PCM 16k mono）
          if (ws && ws.readyState === WebSocket.OPEN && (pttActive || (auto && (!autoMuted || autoSpeaking)))) {
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
  micBtn.addEventListener("pointerdown", function (e) { e.preventDefault(); ensurePlayback(); sendPttStart(); });
  micBtn.addEventListener("pointerup", sendPttStop);
  micBtn.addEventListener("pointerleave", sendPttStop);
  micBtn.addEventListener("pointercancel", sendPttStop);
  document.addEventListener("pointerdown", function () { ensurePlayback(); }); // 任意首次點擊 → 建立並解凍播放（使用者手勢內）
  document.addEventListener("keydown", function (e) {
    if (e.code === "Space" && !e.repeat && document.activeElement !== $("textInput")) { e.preventDefault(); if (!pttActive) { ensurePlayback(); sendPttStart(); } }
  });
  document.addEventListener("keyup", function (e) { if (e.code === "Space") { sendPttStop(); } });
  $("sendText").addEventListener("click", function () {
    ensurePlayback(); // 送出打字 → 手勢內建立播放（純打字使用者也出聲）
    flushPlayback();  // 新回合 → 中斷上一輪殘音
    var t = $("textInput").value.trim();
    if (t) { send({ type: "text", text: t }); $("textInput").value = ""; }
  });
  $("textInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      ensurePlayback();
      flushPlayback(); // 新回合 → 中斷上一輪殘音
      var t = e.target.value.trim();
      if (t) { send({ type: "text", text: t }); e.target.value = ""; }
    }
  });
  $("autoVad").addEventListener("change", function () {
    if (!this.checked) { autoSpeaking = false; autoSilenceMs = 0; sendPttStop(); }
  });

  // VC-2.1：手機端前景喚醒與焦點恢復
  function handleVisibilityOrFocus() {
    ensureAudioResume();
    if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
      clearTimeout(reconnectTimer);
      reconnectAttempts = 0;
      connect();
    }
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { handleVisibilityOrFocus(); }
  });
  window.addEventListener("focus", handleVisibilityOrFocus);
  document.addEventListener("touchstart", function () { ensurePlayback(); }, { passive: true });

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