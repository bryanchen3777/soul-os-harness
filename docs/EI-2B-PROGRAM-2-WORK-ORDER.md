## 工單：EI-2B-PROGRAM-2 — lease 反轉＋三入口接線（正式，可施工）

日期：2026-11-08；執行者待派；READY。Owner 指示：第四步未完成不停；本票是 EI-2B 的後續施工票（PROGRAM-1 未達驗收，PARTIAL 收錄）。

前票結論（主腦複核，證據已驗）：PROGRAM-1 交付 `src/interfaces/coordinator.py`＋18 測試（172 重跑全綠，主腦獨立重跑一致），入口接線／fail-closed／背景標記未動。PROGRAM-1 標 BLOCKED 回報，誠實，無虛報。 coordinator＋測試按 PARTIAL 收錄為本票輸入，不重做。

### 本票推翻聲明（主腦決策，執行者不得自行擴大推翻範圍）

1. **推翻 B2 案 A 的 lease 部分**（冪等部分維持）：`Journal` 一行＝一個冪等鍵的生命週期（`journal.py:193-223` `_classify`：同 namespace 異 fingerprint 恆 CONFLICT；`:428-444` cancel 明言 never reused），與 lease「佔用→釋放→下一個佔用」的循環語意根本不相容。實測後果：PROGRAM-1 的 `lease_namespace` 內嵌 request key（`coordinator.py:127-143`）→ 異 key 同 soul 各自 NEW＋begin 全成功 → 雙雙 publish，互斥名存實亡；其並發測試只測同 key（測的是重放，不是互斥），與驗收定義脫節。此為阻斷級真漏洞，非殘留。
2. **接受 BLOCKED-1**：HTTP 無 mutating 入口（`src/io/gateway.py` 內 11 個 `@self.app.get` 全為讀取／除役／靜態 `:613-862`，唯一寫通道即 `/ws` `:914-986`；全 `src/io` 無 POST／PUT／DELETE，主腦已驗）。「四入口」修正為**三接線點**：web（/ws）、TG、voice。HTTP 概念併入 web，不另定位。
3. **決議 BLOCKED-2**：內部三入口無 bearer／registry 語意（`admit(` 僅見 `context_policy.py:62` 定義處，runtime 零調用，主腦已驗），且 `router.py inbound` payload 無 request 語意（`:948-965` 已驗）。決議：內部入口**不經** `admit()`（`admit()` 保持原樣，留給未來外部 HTTP POST）；走 `coordinator.admit_entry` 輕量路徑。key 規則見做法 4。

### 目標（一句話）

獨立 lease 表＋三入口 publish 前接 `admit_entry`，實現異 key 同 soul 真互斥（恰一 publish、一 409 busy），fail-closed＋背景歸屬收斂。

### 範圍（確切檔案／函式）

- 新 `src/interfaces/soul_lease.py`：SQLite 同庫（runtime 啟動 open 單一 WAL，caller-owned conn，沿用 B2 跨進程論證）新表 `soul_leases(soul_alias PK, holder_request_id, holder_entry, epoch, claimed_at)`。純函數＋caller-owned store，沿用 `journal.py:1-29` 合約風格。**Journal 語意零改動**（只讀重用冪等行部分）。
- 改 `src/interfaces/coordinator.py`：`admit_entry` 改兩步（先 acquire lease，後 claim 冪等行）；`lease_namespace` 保持（冪等行用，內嵌 soul＋key 正確）。
- 接線三處（行號主腦已驗，執行者複驗後寫）：
  1. `src/io/gateway.py` — `/ws` USER_MESSAGE 分支（`:930-986`）：解析成功後、`bus.publish`（`:986`）前插入。`:930-986` 內若有先於 publish 的持久寫，claim 在其前（執行者實測回報）。
  2. `src/io/channels/router.py` — `inbound`（`:875-966`）：同規則，publish（`:966`）前。
  3. `src/voice/input_router.py` — `_dispatch`（`:179`）gate 通過後、`touch_bryan_last_seen`（`:224`）前（若 touch 持久化；執行者實測 touch 是否持久並回報，否則落 `_publish_user_message`（`:215`）內 publish（`:227`）前）。
- 派送 fail-closed：`gateway.py:1017-1074`（O1）、`router.py:186-`（O2，刪 fallback `:218-255`）、TTS（O3）——缺登記即 uncertain＋日誌（沿用 PROGRAM-1 做法 7）。
- 背景歸屬標記（沿用 PROGRAM-1 做法 6，不動 lifecycle 本體；`evaluate_lifecycle_all()` 定義 `elevation_adapter.py:2079` 執行者複驗）。
- **不動**：`journal.py` 語意、`context_policy.admit`、`consciousness` `_pending`／競標、`eventbus` 投遞、`speaker_token`、`token_manager`（C3）、Frozen（下節）。

### 做法（決策已定，執行者照做）

1. lease 語意（鎖非帳，可刪；帳在 journal 行，保留）：
   - `acquire(alias, request_id, entry_kind, epoch, now, ttl)` 單事務：無行或舊行 `claimed_at+ttl <= now` → DELETE 舊＋INSERT 新 → 勝；有未過期行 → 敗（busy 體：holder 類別＋epoch＋`Retry-After: 1`）。過期接管必須記警告日誌（前 holder 可能仍活著，殘留風險如實記錄）。
   - `release(alias, request_id)`：終端路徑最佳努力 DELETE（fire-and-forget，異常吞＋日誌；失敗不影響正確性，只令下請求等多一個 TTL）。
   - 啟動 `recover()`：DELETE 全部 lease 行（重啟即全釋放；uncertain 證據留在 journal 冪等行，新請求新 key 新行不受阻，B7 gap 照舊記）。
   - TTL 常數默認 1800s（覆蓋 LLM 長執行），測試注入短 TTL（fake clock）。
2. `admit_entry` 兩步：① acquire 勝 → ② `journal.claim`（冪等行，沿用現狀）→ `begin`；任一步敗／`JournalError` → 409 不 publish（busy／uncertain 映射沿用現狀，不排隊不丟訊）。
3. feature flag 沿用 `SOUL_OS_PER_SOUL_MUTEX` 預設關＝舊行為逐字不變；三接線點新分支全包 flag 內。
4. key 規則（內部入口無重放識別能力，誠實版）：每次 publish 機會 mint 新 `request_id`（uuid4 hex）＋新 `idempotency_key`（同值或派生，執行者定實現並回報）；重試＝新請求（可能重做，順序由 lease 串行化保證不並發）。同 key 重放→REPLAY 語意保留（外部未來用）。
5. 死鎖禁令：grant 檢查只在入口 publish 之前；handler 內不新增等待-grant；靜態守衛保留＋擴（lease acquire 不得出现在 handler 路徑，測試斷言）。
6. outbox 滯留／孤兒 mp3：保留＋uncertain＋日誌＋對帳，永不自動派送（B6 沿用）。

### 驗收（完成的定義）

- **真互斥 fixture**（異 key 同 soul，threading＋barrier＋fake clock）：兩入口同時請求 → 恰一 `should_publish`、一 409 busy（含 holder 類別＋`Retry-After: 1`）；queue 無增長、無 outcome 寫入、敗者不碰 runtime。
- 同 key 同體重放 → REPLAY 取固定回應，不碰 runtime；同 key 異體 → CONFLICT。
- lease 釋放後新請求可 NEW；TTL 過期接管記日誌；uncertain 行保留不自動釋放（需對帳後 cancel）；重啟 recover 後舊 epoch→uncertain gap、新 epoch 照常。
- 三入口相容 fixture：flag 關＝舊行為逐字一致；開＋無衝突＝同結果＋多 journal／lease 行。
- 背景 fixture：diary／carryover／SAGE 背景提交在 lease 被持有時仍完成，帶非請求標記，不出現在請求 outcome。
- fail-closed fixture：缺登記→uncertain＋日誌，gateway／router／TTS 零送達（mock 斷言 0 send）；outbox 走保留＋對帳。
- 全庫回歸（`.venv`）＋AST 守衛通過；`git status` 僅含預期檔案（新 `soul_lease.py`＋測試＋三接線點修改）。

### 測試

- 新增 `tests/interfaces/test_soul_lease.py`（acquire／busy／過期接管／release／recover／多 soul 互不干擾／fault injection）＋擴 `test_coordinator.py`（兩步 admit、真互斥異 key 並發）。
- 既有：EI-1／EI-2A guards、token／bus、`tests/` 全庫回歸。
- 紀律：`.venv` 解譯器、指名範圍；禁啟伺服器、禁綁生產埠、禁真實 API／LLM、禁殺行程；`data/**` 只讀（自建 TEMP）。

### 不做（Out of Scope）

- 不改 `journal.py` 任何語意；不動 `admit()`；不動 C1/C2/C3；不碰 lifecycle 評估本體；InnerLifeWriter 持久化不在本票；不部署／不重啟生產；未 ACCEPTED 前不 commit／push／合入 main。
- 跨進程多副本部署的 lease 競爭實測（單進程＋WAL 論證覆蓋，留待部署時再驗，殘留聲明）。

### Frozen Contract 注意

- 不碰：Agency 4 stages、TriggerEnvelope、InnerLifeEvent、4 handlers、SAGE 寫入邏輯、world middleware、衰減門檻、cohort 台帳、Journal ownership／recovery 語意、persona／corpus。
- `journal.py` 24h EXPIRED 永不自動重做（`:14-17`）保持；EI-1 admit 非空 request_id（`context_policy.py:64-73`）保持。

### 回報格式

- 改動檔案清單（行號級）＋開工前後 `git status` 對照。
- 測試結果（跑了哪些、過幾筆、有無失敗；全庫失敗逐項說明是否本票行為）。
- 三接線點複驗行號＋touch 持久性結論；B2 反轉落點確認；殘留風險（TTL 接管／多副本未實測）如實記錄。
