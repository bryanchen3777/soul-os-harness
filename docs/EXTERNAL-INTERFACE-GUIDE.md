# Soul OS 對外接口使用說明手冊（External Interface Guide）

> 適用版本：S8 合入後 main（S7-W5／S7-W6 已合入）。
> G1–G5 仍 **BLOCKED**（未決）：本手冊不承諾任何缺口能力，不代選架構選項，
> 不承諾線上可用／SLA。
> 對外閘道（trusted-LAN，埠 `18765`）的 LAN→gateway→cloud 路線已貫通：
> `rem/primary` 已實機驗證（見 §8）；其餘 soul／preset 組合未經驗證，不得擴稱。
> 本手冊為純文件新增；每條操作斷言均標註來源文件＋行號，無孤斷言。
> 行號以撰寫時 HEAD 的文件為準。

---

## 0. 來源一覽

| # | 來源文件 | 用途 |
|---|---|---|
| S1 | `src/interfaces/credentials.py` | issue／rotate／revoke 語義 |
| S2 | `src/interfaces/external_auth.py` | RegistryProvider／extract_bearer／serve_external |
| S3 | `src/interfaces/context_policy.py` | admit 行為 |
| S4 | `src/interfaces/config.py` | validate_registry／resolve_route 行為 |
| S5 | `src/interfaces/identity.py` | authenticate／authorize_route（401／403） |
| S6 | `configs/external_registry.example.json` | 唯一 registry 範本（逐欄位說明） |
| S7 | `docs/G1-G5-ARCHITECTURE-OPTIONS.md` | 已知限制（只轉述，不展開選項） |

---

## 1. Key 簽發（`issue_credential`）

1. 調用 `issue_credential()`（`src/interfaces/credentials.py:19`）。
2. 它返回 `(secret, sha256_hex_digest)` 二元組（S1:19–28）。
3. **secret 僅顯示一次**：「The secret is shown exactly once to the caller」
   （S1:22–23）。調用方必須在返回當下把 secret 交給客戶端保管，
   之後不再有任何取回路徑。
4. **只存 digest**：「only the digest ever enters a registry mapping」
   （S1:23）。registry 的 `credential_sha256` 欄位只放 digest，
   永遠不放 secret 明文。
5. **無落盤、無列印、無日誌**：「No files, no prints, no logging:
   the secret never leaves this tuple」（S1:24）。
6. 簽發算法：`secrets.token_urlsafe(32)` 生成 secret，
   `sha256(secret).hexdigest()` 生成 digest（S1:26–28；
   `_TOKEN_BYTES = 32` 見 S1:11）。
7. 輔助：`digest_of(secret)` 可由明文重算 digest，僅供測試／輪轉使用
   （S1:31–32「Test/rotation helper」）。

---

## 2. Registry 配置（以範本為準）

唯一範本為 `configs/external_registry.example.json`。
以下逐欄位說明，欄位名與範本原文一致；合法性規則來自
`src/interfaces/config.py:41–183`（`validate_registry`）。

> ⚠️ **不可部署聲明（原文保留）**：範本內所有識別值均為測試佔位，
> 字面即含 `do-not-deploy`
> （S6:2 `example-owner-do-not-deploy`、S6:3 `example-port`、
> S6:6–10 `example-soul`／`example-backend`／`example-agent`／`example-model`、
> S6:16–17 `example-client-do-not-deploy`），
> 且 `credential_sha256`（S6:22）為**測試佔位 digest、不可部署**。
> 部署前必須以 §1 簽發的新憑證 digest 全數替換，
> 不得把範本原文當作可部署示例使用。

### 2.1 頂層（4 鍵，缺一不可、多一不可）

| 欄位 | 範本值 | 規則（來源） |
|---|---|---|
| `owner_principal_id` | `example-owner-do-not-deploy`（S6:2） | 必須為合法 id；`clients[].principal_id` 必須與它相等（S4:53–55、S4:131–138） |
| `backends` | `["example-port"]`（S6:3） | 非空、無重複、元素皆合法 id（S4:56–67） |
| `routes` | 見 §2.2 | 非空（S4:69–71）；頂層恰 4 鍵、多餘鍵拒絕（S4:44–52） |
| `clients` | 見 §2.3 | 非空（S4:112–114）；頂層恰 4 鍵（S4:44–52） |

### 2.2 `routes[]`（每條恰 6 鍵）

範本示例（S6:5–12）：`soul_alias`／`backend_alias`／`agent_id`／
`public_model_id`／`backend_ref`／`context_window_tokens`=4096。

| 欄位 | 規則（來源） |
|---|---|
| 6 鍵齊備、無多餘鍵 | S4:77–85（鍵數≠6 拒絕；多餘鍵以 `registry` 拒絕） |
| `soul_alias`／`backend_alias`／`agent_id`／`backend_ref` | 皆須合法 id（S4:92–99） |
| `public_model_id` | 須通過 `is_valid_public_model_id`（S4:100–101） |
| `context_window_tokens` | 須為正整數（`type(x) is int` 且 `> 0`，S4:102–103） |
| `backend_ref` | 必須落在 `backends` 集合內（S4:104–105） |
| `(soul_alias, backend_alias)` | 不得重複（S4:106–109） |

### 2.3 `clients[]`（每條恰 7 鍵）

範本示例（S6:15–23）：`client_id`／`principal_id`／`purpose=interactive`／
`allowed_routes=["example-soul/example-backend"]`／`scope=owner_private`／
`allow_persistent_memory=true`／`credential_sha256`（測試佔位）。

| 欄位 | 規則（來源） |
|---|---|
| 7 鍵齊備、無多餘鍵 | S4:122–130（鍵數≠7 拒絕；多餘鍵以 `registry` 拒絕） |
| `client_id` | 合法 id 且不重複（S4:133–141） |
| `principal_id` | 合法 id 且**必須等於**頂層 `owner_principal_id`（S4:135–138） |
| `purpose` | 僅 `interactive`／`utility`（S4:144–145） |
| `scope` | 僅 `owner_private`／`none`；且 `utility` 必須配 `none`＋`allow_persistent_memory=false`，`interactive` 必須配 `owner_private`（S4:146–158） |
| `allow_persistent_memory` | 必須為布林值（`type(x) is bool`，S4:150–152） |
| `allowed_routes` | 非空、無重複；每條形如 `soul/backend` 且兩段皆合法 id；每條必須對應一條已定義 route（S4:159–173） |
| `credential_sha256` | 64 位十六進（大小寫皆可，存時轉小寫）；客戶端之間不得重複（S4:174–179；正規化 S4:26–38） |

### 2.4 安裝（`RegistryProvider`）

- Provider 不讀環境、不監看文件：候選 mapping 由調用方顯式傳入
  （S2:21–24）。
- `update()` 先以 `validate_registry` 全量校驗，成功才切換快照；
  壞候選拋 `InterfaceError`，舊快照繼續服務（S2:24–27、S2:40–44）。
- 快照不可變；在途請求按舊快照繼續，新請求讀新快照（S2:27–29）。
- 初始構造同樣全量校驗（S2:32–34）。

---

## 3. Key 輪轉（`rotate_registry`）

1. 先以 §1 簽發新憑證，取其 digest（S1:19–28；`digest_of` 見 S1:31–32）。
2. 調用 `rotate_registry(raw, client_id, new_digest)`
   （`src/interfaces/credentials.py:76`）。
3. **同 client 原地替換**：「Rotation replaces the digest in place:
   one client keeps exactly one digest」（S1:79–80）。
4. **無雙 key 寬限期、無別名 client**：「no dual-key grace period,
   no alias client」（S1:80）。舊 secret 在替換生效即刻失效；
   **調用方需自行銜接空窗**（例如先發新 secret 給客戶端再切 registry，
   或接受瞬斷重連）。
5. 輸入 mapping 永不被改動；返回新 mapping（S1:80–81）。
   壞 digest、未知 client、或 digest 與另一 client 碰撞時拋
   `InterfaceError` 且不返回任何東西（S1:81–82；碰撞檢查 S1:91–95）。
6. 完整 schema 校驗仍發生在安裝時：`RegistryProvider.update`
   跑 `validate_registry`，成功才切換（S1:83–84；S2:40–44）。
7. 切換後仍只有 `update()` 成功才生效；失敗時舊快照繼續服務（S2:26–27）。

---

## 4. Key 撤銷（`revoke_registry`）

1. 調用 `revoke_registry(raw, client_id)`
   （`src/interfaces/credentials.py:105`）。
2. **刪除整條 client 條目**：「Revocation deletes the client entry:
   no hidden backdoor, no tombstone that still authenticates」（S1:108–109）。
   無墓碑、无殘留認證路徑。
3. **末戶撤銷拒絕 409**：撤銷最後一個剩餘 client 時拋
   `InterfaceError(409, "last_client", ...)`（S1:110–111、S1:117–118；
   訊息原文 "Refusing to revoke the last client."）。
   調用方應**下線該入口**（停止對外服務），而不是提供空 client 列表
   （S1:111–112「the caller decommissions the entry point
   instead of serving an empty client list」）。
4. 未知 client 拋 `InterfaceError`（S1:112；經 `_client_index` 的 404
   `client_not_found`，S1:64–73）。
5. 輸入 mapping 永不被改動（S1:113）。

---

## 5. 調用格式與錯誤碼表

### 5.1 授權頭（唯一憑證通道）

- 格式：`Authorization: Bearer <secret>`（S2:52–53）。
- **`Bearer` 前綴大小寫敏感，必須精確**：實現為
  `value.startswith("Bearer ")`（S2:82）——`bearer`／`BEARER` 等
  一律不被接受，按 401 處理（S2:82–83 經 `_unauth`，S2:12–13）。
- token 不得為空，不得內嵌任何空白（空格／Tab／換行／回車）
  （S2:84–89）。
- 必須**恰好一個** `Authorization` 條目：缺失或重複 → 401
  （S2:51–52、S2:79–80）。
- 非 Authorization 重名頭 → 400（與 admit 一致，S2:54–55、S2:90–91）。
- 傳入形態錯誤（mapping／字串／bytes、或非 `(name, value)` 對）
  → 400 `invalid_request(headers)`（S2:57–71 經 `_bad_headers`，
  S2:16–17）。

### 5.2 錯誤碼表

| 狀況 | 碼 | 來源 |
|---|---|---|
| query 或 body 帶 key（`query_token`／`body_token` 非空） | **401**，即使 Authorization 頭本身有效 | S2:98–101「any key smuggled outside the Authorization header is refused with 401 even when the header itself is valid」；S2:108–109 |
| Authorization 缺失／重複、scheme 非 `Bearer `、空 token、token 含空白 | **401** `unauthorized` | S2:51–53、S2:79–89；`_unauth` 定義 S2:12–13（"Invalid credentials."） |
| 憑證與 registry digest 無匹配（認證失敗） | **401** | `admit` 經 `authenticate`（S3:63）；`identity.py:10` 拋 401 |
| 已認證但路由不在該 client `allowed_routes` 內 | **403** `forbidden` | `identity.py:34`、`identity.py:39`（"Route access is forbidden."） |
| 请求體非法 | **400**／**413** | S2:101–102「malformed bodies stay 400/413」（經 admit，S3:62–102） |
| 未知路由（`resolve_route` 無匹配） | **404** `route_not_found` | `config.py:186–190`（"Unknown route."）；`serve_external` 的認證與路由重用 admit（S2:101、S2:112） |
| 非 Authorization 重名頭 | **400** | S2:90–91；admit 頭正規化 S3:33–43 |

### 5.3 錯誤不回顯憑證

- 「No error path ever echoes the credential」（S2:55）。
- 「every auth failure raises before the executor is touched,
  and no error carries the raw credential」（S2:103–104）。
- executor 在 admit 成功後**恰好執行一次**；認證失敗時 executor
  完全不被觸碰（S2:102–104、S2:106–113）。

---

## 6. 已知限制（只轉述，不展開選項）

以下為 `docs/G1-G5-ARCHITECTURE-OPTIONS.md` 的「已知限制」條列轉述；
架構選項（A／B）與 Owner 圈選見該文件 §G1–G5 與 §決策待辦清單
（S7:22、S7:34、S7:46、S7:58、S7:70、S7:74–81）。
**本手冊不代選 G1–G5。**

- **G1**：Journal 行狀態終態為 `transport_sent`，`completed` 只存在於
  `outcomes["memory"]`；**不具跨重啟終態**——重啟後 `ctx._backend_ledger`
  清空，已完成不可再證（S7:18–19）。
- **G2**：`record_outcome` 僅接受 `dest == "memory"`（`journal.py:399`
  `bad dest` 為預期拒絕）；backend 完成只走無址 `submit_backend`；
  終態記憶體化、**不可跨重啟恢復**（S7:30–31）。
- **G3**：`Journal` 無 `cancel`；取消唯一入口為 coordinator 的有條件取消；
  取消證據只活在記憶體帳本，**重啟後已取消／已密封區分隨之遺失**
  （fail-closed，呼叫方不得假設可恢復）（S7:42–43）。
- **G4**：舊 `submit` 一切一般例外→`UNCERTAIN`，同 `request_id` 重調
  不重調 port；`attempt` 圍欄與 stored call 皆為記憶體態，
  **重啟後不可恢復**；`UNKNOWN` 仍不得盲重送（S7:54–55）。
- **G5**：`Decision`／`PortResult`／`PortError` 只從
  `src/interfaces/backend_execution.py` 匯入；舊 `runtime_port` import
  路徑**永遠報 `ImportError`**，呼叫方必須改 import 源（S7:66–67）。

---

## 7. 不承諾事項

- G1–G5 缺口能力（見 §6）不承諾；已誠實收斂的缺口不得為「讓手冊好看」
  而放寬或重述。
- 閘道僅在受信 LAN 內提供服務（見 §8）；不承諾線上可用／SLA。
- 範本 registry 為測試佔位（見 §2 開頭警告），不可直接部署。

---

## 8. 對外閘道運維（LAN→gateway→cloud，S8）

本節只描述已貫通並經驗證的路線，不新增能力承諾。

### 8.1 生效範圍（只宣稱已驗證者）

- **埠**：`18765`（`src/external_gateway/app.py:71`，`EXTERNAL_PORT` 凍結常數；
  服務腳本 `scripts/serve_external_gateway.py` 預設即此埠）。
- **已實機驗證路由**：五魂全通（2026-11-10）
  （`POST /<soul>/primary/v1/chat/completions`，soul＝
  `rem／mai／ruka／akane／mahiru`，逐魂回 **200**，見 §8.6）。
  其他 preset 組合**未經驗證**，不得擴稱為「兩 provider 均完成」。
- **soul→agent 精確映射**（`src/external_gateway/routing.py:51–65`，五對凍結，
  錯配即 500）：
  `rem→agent_rem`、`mai→agent_mai`、`ruka→agent_ruka`、
  `akane→agent_akane`、`mahiru→agent_mahiru`。

### 8.2 Bearer 操作（憑證通道唯一）

- 簽發／輪轉／撤銷見 §1／§3／§4；請求頭格式與大小寫規則見 §5.1。
- 無 key／錯 key 一律 **401**（`unauthorized`），且認證失敗時後端
  executor 完全不被觸碰（見 §5.3）。
- 錯誤不回顯憑證：任何 5xx 為固定文案，4xx 經過去敏（見 §5.3）。

### 8.3 非串流限制

- 首批只做非串流：`stream: true` 一律 **400**
  （`src/external_gateway/openai_shim.py:184–189`，
  "Streaming is not supported by this interface version."）。
  SSE 為後續增量，本節不承諾。

### 8.4 兩種重載語義（改錯會以為「沒生效」）

- **preset 逐 call 重讀**：每次聊天請求都經 `file_preset_loader`
  重新讀取 preset 檔（`src/external_gateway/app.py:146–170`），
  故**換模型免重啟**——改 preset 檔後下一次請求即生效。
- **registry 啟動快照**：服務啟動時讀一次 registry 檔建成
  `RegistryProvider` 物件（`scripts/serve_external_gateway.py:89`），
  運行中只讀該記憶體快照。故**改路由需重啟**——只改磁碟檔、
  不重建服務，線上行為不變。

### 8.5 受信 LAN 明文風險

- 閘道為 trusted-LAN 明文 HTTP，無 TLS
  （`scripts/serve_external_gateway.py:93–96` 公告文案明示
  "trusted-LAN plaintext, no TLS"）。**不得**把該埠暴露到非受信網路；
  需要跨網存取時，須在閘道之外另加受信反向代理／TLS（本手冊不代選方案）。

### 8.6 實機證據 H4／M4（引用既有，不補發請求）

- **H4**：Owner 直連 ollama cloud
  `model=deepseek-v4.1-flash:1017` 回 **200**（`chatcmpl-233`
  前綴 id 族）。來源：Sol S8-CLOSEOUT-1 工單記述之既有證據；
  原始出處待主腦合入 canonical 時補登。本次工單未發起任何真實 LLM 請求。
- **M4**：`POST /rem/primary/v1/chat/completions` 回 **200**；
  無 key／錯 key 皆 **401**。來源同 H4（工單記述之既有證據，
  未補發請求，原始出處待補登）。
- **五魂煙測（2026-11-10，Owner 完備化授權）**：registry 擴為五路由
  （`mai／ruka／akane／mahiru × primary`，`agent_id` 嚴格
  `agent_<soul>`，`allowed_routes` 同步五條，digest 不變）＋閘道重啟
  載入新快照後，逐魂一次最小 `hi` 調用：
  `rem 200(chatcmpl-b503)`、`mai 200(chatcmpl-236f)`、
  `ruka 200(chatcmpl-3e70)`、`akane 200(chatcmpl-e31b)`、
  `mahiru 200(chatcmpl-c542)`，**5/5 全 200**；
  同期錯 key 五魂全 **401**（認證層擋下，executor 未觸碰）。
  全部走同一 `primary` preset（`deepseek-v4.1-flash:1017`），
  未新增成本維度。
- 上述兩項只證明「`rem/primary` 的 LAN→gateway→cloud 通路可達」；
  **200 不等於人格／記憶持久化驗收**（回 200 只代表通路與形狀正確，
  不代表該 soul 的人格、記憶或長期狀態已驗證）。

### 8.7 未驗證項（誠實登記）

- `mai／ruka／akane／mahiru` 四魂的實機通路**已驗證**（見 §8.6 五魂煙測）。
- deepseek 直連 provider 的實機通路未驗證（H4 覆蓋的是 ollama cloud）。
- 生命週期補證（轉變純度／關鍵反例／投影對帳）另案處理，不綁住 S8
  （見 `verification/S8/REPORT.md`）。
