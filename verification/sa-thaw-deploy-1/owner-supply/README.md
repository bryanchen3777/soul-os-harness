# Owner 待填清單（SA-THAW-DEPLOY-1 第二段）

狀態：`DESIGNED/BLOCKED`。以下三份 FILL 檔填完 → 工程核驗 → 送臥龍裁部署。
`DEPLOYED_CLOSED` 在臥龍放行前禁登。

## 填寫規則（必讀）

1. 這三份是**模板**，不是生產配置，填完也不會自動部署。
2. **秘密明文不經聊天傳**：KEY、token 明文一律不貼到對話、不寫進這三份檔案。
   KEY 只放在你本機環境變數裡（見第 3 份）；入站 token 見第 2 份說明。
3. 你已批的方向：四 preset 同一 URL、同一 KEY（ollama cloud）；各 model 沿用既有對應、不推定相同；provider 仍 `openai`。
4. 填完告訴我一聲，我做核驗＋送臥龍，不會擅自安裝／重啟。

## 三份檔案

| 檔案 | 要你給什麼 | 幾分鐘 |
|---|---|---|
| `01-preset-models.FILL.json` | 同一 base_url（一份，填一次、四個別名共用）＋四個別名各自的 model ID（各填一個，不推定相同；若某個別名在 ollama cloud 上根本沒有對應 model，直寫 `"NO_MAPPING"`，工程按拒絕路徑處理並報臥龍，不猜） | 3 分鐘 |
| `02-registry-identity.FILL.json` | 正式 owner（綁誰）＋正式 client（哪個客戶端、用哪條 LAN key 身份）＋開哪幾條路由（預設只開 `rem/primary` 一條做閉門驗證，其餘先關） | 2 分鐘 |
| `03-backend-credential.FILL.json` | 既有 ollama cloud 的 URL 來源＋KEY 放在哪個本機環境變數（只給變數名，不給值）＋確認入站憑證獨立新建（不重用 Ollama KEY） | 2 分鐘 |

## KEY 在哪裡（只做一次）

KEY 明文只存在於**你跑生產服務的那台機器**的環境變數，例如：

```powershell
$env:OLLAMA_CLOUD_KEY = '...你在本機貼上明文，只在這台機器上...'
```

然後在第 3 份檔案裡只寫 `"key_env": "OLLAMA_CLOUD_KEY"`（變數名，不是值）。
工程驗證時只查存在性（present/absent），永不讀值、永不回報值。
