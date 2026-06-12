/* =====================================================================
 * embed.js — AI 虛擬人嵌入載入器
 * 用法：在任何網站貼一行（跨網站請用部署後的完整網址）：
 *   <script src="https://YOUR-DEPLOY.example/embed.js"></script>
 *   同網域可用： <script src="embed.js" data-widget="widget.html"></script>
 *
 * 建立右下角 iframe（裝虛擬人）+ 收合泡泡，用 postMessage 與 iframe 溝通，
 * 並開好 microphone 權限。對外提供 window.AvatarWidget = { open, close, say }。
 * ===================================================================== */
(function () {
  'use strict';

  // 注入收合泡泡的 hover / 注意力 pulse 動畫
  var awStyle = document.createElement('style');
  awStyle.textContent =
    '#avatar-widget-root .aw-bubble{transition:transform .15s, box-shadow .15s;}'
    + '#avatar-widget-root .aw-bubble:hover{transform:scale(1.07);}'
    + '#avatar-widget-root .aw-bubble:active{transform:scale(.95);}'
    + '#avatar-widget-root .aw-bubble:focus-visible{outline:3px solid rgba(91,84,232,.45);outline-offset:3px;}'
    + '#avatar-widget-root .aw-bubble::after{content:"";position:absolute;inset:0;border-radius:50%;animation:awpulse 2.2s ease-out infinite;pointer-events:none;}'
    + '@keyframes awpulse{0%{box-shadow:0 0 0 0 rgba(91,84,232,.5);}70%{box-shadow:0 0 0 13px rgba(91,84,232,0);}100%{box-shadow:0 0 0 0 rgba(91,84,232,0);}}';
  (document.head || document.documentElement).appendChild(awStyle);

  // 1) 找出自己的位置，推算 widget.html 的網址（可用 data-widget 覆蓋）
  var me = document.currentScript || (function () {
    var ss = document.getElementsByTagName('script');
    for (var i = ss.length - 1; i >= 0; i--) { if (/embed\.js(\?|$)/.test(ss[i].src || '')) return ss[i]; }
    return null;
  })();
  var base = me ? me.src.replace(/[^/]*$/, '') : '';
  var widgetUrl = (me && me.getAttribute('data-widget')) || (base + 'widget.html');
  // 自動加 cache-bust（widget.html 改 dev 要硬重 load；embed.js 自身也有 ?v= 區隔）
  // host 可以用 ?data-widget-cb=8 或單純改 embed.js 觸發（因為 widget.html 透過 embed.js 帶進來）
  // 簡化策略：embed.js 自身 ?v= 已經是 cache-bust — 新 embed.js 載入時也會重 build widgetUrl，
  // 但 browser 還可能 cache widget.html。強制每次 embed.js 載入時帶 _=<timestamp> 給 widget.html
  if (widgetUrl.indexOf('?') < 0) {
    widgetUrl = widgetUrl + '?_t=' + Date.now();
  } else {
    widgetUrl = widgetUrl + '&_t=' + Date.now();
  }
  var startOpen = (me && me.getAttribute('data-open') !== 'false'); // 預設一進來就展開
  var widgetOrigin = (function () { try { return new URL(widgetUrl, location.href).origin; } catch (e) { return '*'; } })();

  // 把可設定項帶進 widget：皮=model / 肉的語音後端=api / 內容=knowledge / 聲線=voice / 角色=agent
  var cfg = new URLSearchParams();
  ['model', 'api', 'knowledge', 'voice', 'agent'].forEach(function (k) {
    var v = me && me.getAttribute('data-' + k);
    if (v) cfg.set(k, v);
  });
  var cfgQs = cfg.toString();
  var iframeSrc = widgetUrl + (cfgQs ? (cfgQs.charAt(0) === '?' ? cfgQs : '&' + cfgQs) : '');
  // 上面：原本邏輯用 widgetUrl.indexOf('?') 判斷，現在已帶 _t= 所以一定是 ? 開頭或 & 開頭；改用 cfgQs 自己的開頭

  var EXPANDED = { w: 340, h: 480 };
  var NS_OUT = 'avatar-widget-host'; // 父 → 子
  var NS_IN  = 'avatar-widget';      // 子 → 父

  // 2) 建外層容器
  var root = document.createElement('div');
  root.id = 'avatar-widget-root';
  // Phase 6.4：bottom 80px → 200px（更往上抬，避開 Soul OS input-bar 整個高度 + 發送按鈕）
  // Soul OS #input-bar 高約 80-100px，加 100px 安全邊距
  root.style.cssText = [
    'position:fixed', 'right:16px', 'bottom:200px',
    'z-index:2147483000', 'width:' + EXPANDED.w + 'px', 'height:' + EXPANDED.h + 'px'
  ].join(';');

  // 3) iframe（虛擬人本體）
  var iframe = document.createElement('iframe');
  iframe.src = iframeSrc;
  iframe.title = 'AI 虛擬人助理';                 // 無障礙：給 iframe 一個名字
  iframe.setAttribute('allow', 'microphone; autoplay'); // 語音輸入 + 音訊播放
  iframe.setAttribute('allowtransparency', 'true');
  iframe.style.cssText = 'width:100%;height:100%;border:0;background:transparent;color-scheme:normal;';

  // 4) 收合後的小泡泡（iframe 收起時顯示，點它再展開）
  var bubble = document.createElement('button');
  bubble.type = 'button';
  bubble.className = 'aw-bubble';
  bubble.setAttribute('aria-label', '開啟 AI 虛擬人助理');
  bubble.textContent = '💬';
  bubble.style.cssText = [
    'position:absolute', 'right:2px', 'bottom:2px', 'width:64px', 'height:64px',
    'border:0', 'border-radius:50%', 'cursor:pointer', 'font-size:28px',
    'background:linear-gradient(135deg,#7d78f0,#5b54e8)', 'color:#fff',
    'box-shadow:0 8px 22px rgba(0,0,0,.3)',
    'display:none', 'align-items:center', 'justify-content:center'
  ].join(';');

  root.appendChild(iframe);
  root.appendChild(bubble);
  (document.body || document.documentElement).appendChild(root);

  // 確保 iframe 載入完才送 postMessage（widget 內 listener 沒 attach 之前 postMessage 會丟失）
  // 對外暴露 onReady callback
  var _onReady = null;
  iframe.addEventListener('load', function () {
    // 多延一 tick 給 widget.html 的 message listener 註冊
    setTimeout(function () {
      if (typeof _onReady === 'function') { try { _onReady(); } catch (e) {} }
    }, 0);
  });

  // 5) 展開 / 收合
  function setOpen(open) {
    if (open) {
      root.style.width = EXPANDED.w + 'px';
      root.style.height = EXPANDED.h + 'px';
      iframe.style.display = 'block';
      bubble.style.display = 'none';
    } else {
      root.style.width = '60px';
      root.style.height = '60px';
      iframe.style.display = 'none';
      bubble.style.display = 'flex';
    }
  }
  bubble.onclick = function () { setOpen(true); };
  setOpen(startOpen);

  // 6) 接收 iframe 的訊息（驗證來源 origin）
  window.addEventListener('message', function (e) {
    if (widgetOrigin !== '*' && e.origin !== widgetOrigin) return; // 只收來自自己 widget 的訊息
    var d = e.data || {};
    if (d.ns !== NS_IN) return;
    if (d.type === 'close') setOpen(false);                 // 使用者按 ✕ → 收成泡泡
    if (d.type === 'ready') {
      // widget 載入完成 → flush pending init（switch_agent + set_voice_enabled）
      if (pendingInit) {
        var initPayload = { agent: pendingInit.agent || '' };
        if (pendingInit.model) initPayload.model = pendingInit.model;
        if (pendingInit.voice_actual) initPayload.voice = pendingInit.voice_actual;
        sendToWidget('switch_agent', initPayload);
        sendToWidget('set_voice_enabled', { enabled: !!pendingInit.voice });
        pendingInit = null;
      }
    }
    if (d.type === 'error') console.warn('[avatar] widget error:', d.message);
    // Phase 6.2 路線 D：widget STT 結果 → 觸發 host 設的 onUserInput callback
    if (d.type === 'user_input' && typeof window.AvatarWidget.onUserInput === 'function') {
      try { window.AvatarWidget.onUserInput(d.text || ''); }
      catch (err) { console.warn('[avatar] onUserInput callback error:', err); }
    }
  });

  // 等 widget ready 期間，init 設定先 buffer 在這
  // {agent, model?, voice_actual?, voice (boolean for set_voice_enabled)?}
  var pendingInit = null;

  // 7) 對外 API：別的程式可以叫她說話 / 開關 / 接收 STT
  // onUserInput: function(text) — 設了就接 STT 結果；不設就 widget 走 KB fallback
  var _onUserInput = null;
  function sendToWidget(type, payload) {
    iframe.contentWindow && iframe.contentWindow.postMessage(
      Object.assign({ ns: NS_OUT, type: type }, payload || {}), widgetOrigin);
  }
  window.AvatarWidget = {
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
    say: function (text) {
      setOpen(true);
      sendToWidget('say', { text: String(text || '').slice(0, 600) });
    },
    // Phase 6.4：host 動態切換對應角色（含 model + voice 切換）
    // 確保 widget listener ready 後才送（避免 message 丟失）
    // pendingInit 在 message handler 收到 widget 'ready' 時 flush
    //   switchAgent(agent, model?, voice?)
    //     - agent: 'yua' | 'ruka' | 'akane' | '' (空字串=通用)
    //     - model: optional Live2D model3.json URL（不傳就不切 model）
    //     - voice: optional msedge voice name（不傳就不切 TTS voice）
    switchAgent: function (agent, model, voice) {
      var payload = { agent: agent || '' };
      if (model) payload.model = model;
      if (voice) payload.voice = voice;
      if (pendingInit) {
        pendingInit.agent = agent || '';
        if (model) pendingInit.model = model;
        if (voice) pendingInit.voice = voice;
      } else {
        pendingInit = { agent: agent || '', voice: true };
        if (model) pendingInit.model = model;
        if (voice) pendingInit.voice_actual = voice;
      }
      sendToWidget('switch_agent', payload);
    },
    // Phase 6.4：群聊時 host 設 false → 麥克風 + onTap 自我介紹都暗掉
    // 文字輸入仍可使用
    setVoiceEnabled: function (enabled) {
      if (pendingInit) {
        pendingInit.voice = !!enabled;
      } else {
        pendingInit = { agent: '', voice: !!enabled };
      }
      sendToWidget('set_voice_enabled', { enabled: !!enabled });
    },
    get onUserInput() { return _onUserInput; },
    set onUserInput(fn) { _onUserInput = (typeof fn === 'function') ? fn : null; }
  };
})();
