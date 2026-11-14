# SA-OPEN-FIRE-1 diag-1 — 分層結論（純診斷，未重現 exit 7）

- 探測時間（UTC）：2026-11-14T23:32:29Z（`evidence.json` 各層另有獨立 `time_utc`）
- 目標：`ollama.com:443`（實彈同一主機；全程未說 HTTP、未 POST、未帶金鑰）
- 直譯器：repo `.venv` Python 3.11.15（與發送環境一致）

## 分層結果

| 層 |  verdict | 證據 |
|---|---|---|
| 代理配置 | PASS（直連） | `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy/NO_PROXY/no_proxy` 六者皆 `present=false`（僅記名＋是否為空，值永不記錄） |
| DNS | PASS | 1 次即中，16.0 ms，A `34.36.133.15`（與實彈當時解析一致；AAAA 無回應＝本機僅 IPv4 路徑） |
| TCP 443 | PASS | 1 次即通，31.0 ms，peer `34.36.133.15:443`（10 s 逾時未觸發） |
| TLS/SNI/憑證鏈 | PASS | 1 次握手成功，47.0 ms；TLSv1.3／`TLS_AES_256_GCM_SHA384`；issuer `CN=WR3 (Google Trust Services)`；subject `CN=ollama.com`；有效期 2026-09-16 → 2026-12-15（未過期；僅記 issuer/subject/有效期，無整鏈） |

## 已排除 vs 未定位

- 已排除：代理干擾、DNS 解析失敗、TCP 443 連線失敗、TLS 握手／SNI／憑證鏈有效性。
- 未定位：exit 7「無 HTTP 回應」出在哪一層之上——本次連線層全綠，**未能重現**；
  故障應在 TLS 之上（HTTP 應用層：讀逾時／對端 RST／傳輸堆疊行為／瞬時抖動），
  但本診斷未說 HTTP，故不得冒稱根因已明。

## 原樣聲明

- 握手成功不等於推理可用（本次未發任何 HTTP，更未觸模型端點）。
- 零真實推理：無 POST、無 Authorization 頭、無 `.env` 讀取、無 `data/**` 寫入、
  未動 `live-fire-1/*`（git 僅見 `diag-1/` 新增）。

## 是否建議重打（只給技術判斷）

- 技術上：連線層健康，重打一次在傳輸層面無已知阻擋；但 exit 7 的當次無回應原因未定位，
  重打屬「再取樣一次瞬時行為」，不是修復驗證。
- 花錢與否由 Owner 定；`sent.json` marker 已建，重打涉及額度政策，需主大腦＋Owner 裁決，
  本診斷不觸 marker、不重置、不建議繞過。
