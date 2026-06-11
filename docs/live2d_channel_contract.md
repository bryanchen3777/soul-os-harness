# Live2D Channel 契約 v0.1

> Soul OS × ai-avatar-bot 整合 — 皮肉之間的訊息契約
> 狀態：**草案 v0.1**（Phase 6.1 純文字 scaffold 用）
> 作者：Bryan × Mavis，2026-06-11
> 配套檔案：`src/io/channels/live2d.py`

---

## 設計原則

- **皮肉分離**：Soul OS 是「肉」（agent 邏輯 + 記憶 + 情緒），Live2D widget 是「皮」（渲染 + 對嘴 + 語音）
- **單向契約為主**：Soul OS → widget 主動發話；widget → Soul OS 只有 user 輸入 + 狀態
- **postMessage**：透過 `embed.js` 建 iframe，父子用 `postMessage` 通訊（同網域 origin 驗證）
- **Soul OS 不碰 DOM**：Python 端只負責廣播，前端 JS 接到廣播後轉 postMessage 給 widget
- **TTS 跟對嘴不綁死**：對嘴是 widget 內部 audio analyser 職責，Soul OS 不管

---

## Soul OS → widget（postMessage，**NS_OUT = `'avatar-widget-host'`**）

### `say`（agent 說話）— v0.1
```json
{
  "ns": "avatar-widget-host",
  "type": "say",
  "agent_id": "agent_yua",
  "text": "……想你了，你在嗎",
  "emotion": "melancholy",
  "lip_sync": false
}
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `ns` | string | ✅ | 固定 `"avatar-widget-host"`（跟 embed.js NS_OUT 對齊）|
| `type` | string | ✅ | 固定 `"say"` |
| `agent_id` | string | ✅ | `"agent_yua"` / `"agent_ruka"` / `"agent_akane"` |
| `text` | string | ✅ | agent 說的話（已過 reasoning 過濾）|
| `emotion` | string | ✅ | emotion 值域，見下表 |
| `lip_sync` | bool | ✅ | `false` = 純文字顯示不播 TTS（v0.1）/ `true` = TTS 播放 + 對嘴（v0.3）|

**emotion 值域**（Soul OS mood → Live2D expression 映射）：

| Soul OS emotion | Live2D expression | 備註 |
|----------------|-------------------|------|
| `calm` | `neutral` | 預設 |
| `melancholy` | `sad` | |
| `happy` | `happy` | |
| `cold` | `cold` | Yua 冷泡茶模式 |
| `warm` | `warm` | |

### `switch_model`（切換 agent 模型）— v0.4 規劃，**先不實作**
```json
{
  "ns": "avatar-widget-host",
  "type": "switch_model",
  "agent_id": "agent_ruka",
  "model_url": "./models/ruka/ruka.model3.json"
}
```

> Phase 6.4 才實作三 agent 切換。**v0.1 不送此訊息**。
> 延遲載入策略：只在 agent 主動發話的「前一次 say」才送 switch_model，避免記憶體爆。

---

## widget → Soul OS（postMessage，**NS_IN = `'avatar-widget'`**）

### `user_input`（用戶語音/文字輸入）— **Phase 6.2，v0.2 才加**

**實作路線：C（最小改動）**
- widget 內部 STT 結果用現有 `postToParent(type, payload)` 送出
- 改動範圍：
  - `widget.html`：在 STT 結果 callback 加 1 行 `postToParent('user_input', { text })`
  - `embed.js`：在 addEventListener 加 ~5 行 — 收到 user_input → 呼叫 `window.AvatarWidget.onUserInput?.(text)`
  - Soul OS 端 `index.html`：設 `window.AvatarWidget.onUserInput = (text) => ws.send(...)` 接到自家 WebSocket

**訊息契約：**
```json
{ "ns": "avatar-widget", "type": "user_input", "payload": { "text": "我在" } }
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `ns` | string | ✅ | 固定 `"avatar-widget"` |
| `type` | string | ✅ | 固定 `"user_input"` |
| `payload.text` | string | ✅ | 用戶輸入（語音 STT 結果 or 文字）|

Soul OS 端收到後 → `ChannelRouter.inbound(agent_id="agent_yua", text="我在", user_id=...session_id, channel="live2d")` → 走既有的 USER_MESSAGE 路徑。

> **v0.1 不實作** — 文字輸入走現有 WebSocket / Telegram，語音輸入等 Phase 6.2 路線 C。

### `ready`（widget 初始化完成）
```json
{ "ns": "avatar-widget", "type": "ready" }
```

Soul OS 收到後可選擇觸發歡迎語（v0.1 不實作）。

### `error`
```json
{ "ns": "avatar-widget", "type": "error", "message": "STT failed" }
```

Soul OS 收到後可 log 或回饋到 SYSTEM_ERROR bus event（v0.1 只 log）。

---

## 路由路徑

```
Soul OS agent 說話
   ↓
LLMProxy → AGENT_SPEAK (payload 帶 target_channel="live2d")
   ↓
[Bus] → ChannelRouter._on_agent_speak
   ↓
adapters["live2d"].send(agent_id, text, user_id, emotion, lip_sync)
   ↓
Live2DChannelAdapter 組 postMessage payload
   ↓
ChannelRouter 廣播 payload 到所有 WebSocket 前端
   ↓
前端 JS 監聽 channel="live2d" 訊息，轉 postMessage 給 widget iframe
   ↓
widget.html 收到 say → 顯示 #bubble 文字 → (Phase 6.3+) 觸發 TTS + 對嘴
```

---

## 版本演進

| 版本 | 範圍 | 狀態 |
|------|------|------|
| **v0.1** | 純文字 say（lip_sync=false），不碰 TTS / 對嘴 | 🟡 Scaffold（這文件）|
| v0.2 | 加 emotion → Live2D expression 映射 | ⬜ |
| v0.3 | lip_sync=true，widget 內 TTS + 對嘴（browser 內建 SpeechSynthesis 路線）| ⬜ |
| v0.4 | switch_model 三 agent 切換 + 延遲載入 | ⬜ |
| v0.5 | msedge-tts 神經語音（拿真實音量驅動對嘴）| ⬜（要看 msedge-tts 風險接受度）|

---

## 安全 / 邊界

- **postMessage targetOrigin**：dev 設 `*`，production 必須設實際 domain
- **NS 欄位驗證**：embed.js 跟 widget.html 都會驗 `e.data.ns` 符合預期
- **iframe sandbox**：embed.js 設 `sandbox="allow-scripts allow-same-origin"`（待定，預設不開 sandbox）
- **TTS 風險**：Phase 6.3+ 走 browser 內建 TTS（免費、穩定、音質差），不依賴 msedge-tts 非官方端點
- **語音輸入 STT 隱私**：webkitSpeechRecognition 送 Chrome → Google 雲端，UI 要揭露給用戶

---

## 開放問題

- [ ] `embed.js` 是否能改 NS_OUT 而不影響 ai-avatar-bot 既有功能？（應該可以，NS 是純 tag）
- [ ] IOGateway WebSocket broadcast 需要過濾 target_channel 嗎？（v0.1 全 broadcast，前端 JS 過濾）
- [ ] `user_id` 從哪來？Live2D widget 沒有 Telegram 概念，可能要 Web session 唯一 ID
- [ ] 多 widget 並存（多個瀏覽器分頁）怎麼處理？broadcast 給全部 vs 單一

---

## 參考

- `src/io/channels/router.py` — ChannelRouter 既有架構
- `src/io/channels/base.py` — ChannelAdapter ABC
- `C:\Users\bbfcc\.local\bin\ai-avatar-bot\embed.js` — NS_IN/NS_OUT 慣例來源
- `C:\Users\bbfcc\.local\bin\ai-avatar-bot\widget.html` — Live2D + 對嘴 + TTS 主體
- `INTEGRATION_PLAN_INPUT.md` — Perplexity 規劃 input
