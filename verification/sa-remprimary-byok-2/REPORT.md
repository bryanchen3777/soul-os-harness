# SA-BYOK-VERIFY-2 驗證結論：PASS（2026-11-15）

## 一句話
SA-GW-OLLAMA-ALIGN-1（commit 4ed54c1）＋ T12 重釘（commit 8c03a03）
落地後，白話 OpenAI 請求體經薄端 `:18765/rem/primary` 拿到
**HTTP 200**，回覆 35 字。單包已消耗
（`req-sa-byok-verify-2-0001`），不再重打。

## 實測（Owner 授權第二發單包，門開→打→關全程一次性）
- 薄端起服 `:18765`，`close→install→ready→open` 全 `exit 0`，門 `open`。
- 探針 `probe_byok_open_2.py`：白話 OpenAI 體
  `{"model":"sa-rem-primary","messages":[{"role":"user","content":"你好"}],
  "max_tokens":64,"stream":false}` ＋ Bearer 入站鑰（運行時讀檔，值不落盤）。
- 回包：`HTTP 200`，`reply_chars=35`，
  `reply_sha256=bf56a90c…1fb0a`（雜湊見 `result.json`），`transport_calls=1`。
- `usage` 全零：上游原樣回傳（與 OPEN-1 同形已知現象，不阻斷）。
- 收尾：`panic-close exit 0` → 門 `closed` → 優雅關閉薄端 →
  回收自建任務 → `:18765` 無 LISTEN、`:8000` 存活（PID 9968 未動）。
  兩發授權均已耗盡。

## 附帶
- 第一發起錯任務（誤把探針腳本當服務起）自拒 `exit 6`，
  gate 未開、零發送、零標記、零消耗，不影響本結論。
- MiniMax Code 側 `soul-os / sa-rem-primary` 條目已重寫回配置
  （`C:\Users\bbfcc\.minimax\config.yaml`，結構校驗通過，鑰指紋與
  註冊表一致）。App 端需重載／重啟才能在模型清單看到並真正調用；
  薄端平時不常駐、門常閉，調用前需走單包開關（另立探針／指令）。

## 證據
- 探針：`probe_byok_open_2.py`（本目錄）
- 單包標記：`sent.json`（已消耗，永不重打）
- 結果：`result.json`（計數／雜湊，無明文）
