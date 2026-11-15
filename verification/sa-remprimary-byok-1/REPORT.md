# SA-BYOK-VERIFY-1 驗證結論：BLOCKED（2026-11-15）

## 一句話
MiniMax Code 送出的**白話 OpenAI 請求體**，走薄端 `:18765/rem/primary`
進來會拿到 **500** —— 不是鑰匙錯、不是門沒開，是生產預設寫的
`provider: "openai"` 在後端分發層**結構性拒收**（fail closed）。
單包已消耗（`sent.json`，`req-sa-byok-verify-1-0001`），不再重打。

## 實測（Owner 授權單包，門開→打→關，全程一次性）
- 薄端起服 `:18765`（closed by construction），`close→install→ready→open`
  全 `exit 0`，門狀態 `open`。
- 探針 `probe_byok_open_1.py`：白話 OpenAI 體
  `{"model":"sa-rem-primary","messages":[{"role":"user","content":"你好"}],
  "max_tokens":64,"stream":false}` ＋ Bearer 入站鑰（運行時讀檔，值不落盤）。
- 回包：`HTTP 500`（探針 `exit 7`，按紀律停手不重打）。
- 收尾：`panic-close exit 0` → 門 `closed` → 優雅關閉薄端 →
  回收自建任務 → `:18765` 無 LISTEN（僅 TIME_WAIT 殘留）、`:8000`
  存活。單包授權已耗盡。

## 根因（唯讀代碼核對，無二次發送）
1. 白話體走 `parse_chat_request` → `PresetRoutingBackend.complete`
   → `build_backend(preset)`（`src/external_gateway/backends.py:227`）。
2. 生產 `configs/sa/presets.json` 的 `primary` 寫的是
   `"provider": "openai"`；`build_backend` 只接
   `deepseek`／`ollama`，`openai`／`gemini` 直接 raise 500
   （`backends.py:36-39,234` 明文註記 fail closed）。
3. 500 發生在**分發層、撥上游之前**：上游一字節沒打，不產生上游費用。
4. 對照：OPEN-1 密封包成功是走 `soul_live_handler` 活循環（繞過
   `build_backend`），與白話路徑是兩條不同鏈路 —— 前者通不證明後者通。

## 影響
- 剛寫入 MiniMax Code 配置的 `soul-os / sa-rem-primary` 條目**已回滾**
  （`config.yaml` 雜湊恢復改前值，備份保留在
  `config.yaml.bak-20261115-remprimary`），不留假裝能用的入口。
- 四個生產 preset 全是 `provider: "openai"`，且 `client-bryan-01`
  僅被授權 `rem/primary` —— 目前**沒有任何白話可用的路由**。

## 下一步（待主腦裁決）
- 白話路徑要通，必須動生產側（二選一，皆需主腦工單＋Owner 拍板）：
  A. 讓 `build_backend` 接住 `openai` provider（代碼變更）；
  B. 為 `rem/primary` 增設 `ollama`/`deepseek` preset（配置變更）。
- 在那之前，MiniMax Code 清單不掛 rem/primary（架構誠實性）。

## 證據
- 探針：`probe_byok_open_1.py`（本目錄）
- 單包標記：`sent.json`（`req-sa-byok-verify-1-0001`，已消耗，永不重打）
- 薄端服務日誌行：`POST /rem/primary/v1/chat/completions → 500`
 （自建任務輸出，前後各四個 control 200 包夾）
