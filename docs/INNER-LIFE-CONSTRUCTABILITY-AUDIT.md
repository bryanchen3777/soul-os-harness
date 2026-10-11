# 內部生活可工性審計（INNER-LIFE-CONSTRUCTABILITY-AUDIT）

> **性質**：READ-ONLY 調查 ＋ DOCS-ONLY 新文件。本票只新建本檔 1 個 MD；**0 `src/**` / 0 `tests/**` / 0 `configs/**` / 0 `scripts/**` / 0 `data/**` 寫入 / 0 服務重啟 / 0 kill / 未開 `data/faulthandler.log` / 0 Frozen Contract 觸碰 / 未用 `git add -A`**（逐檔明確 add 本檔）。
> **`data/**` 全程唯讀**（本票實際只用 `git`＋`grep`＋`read`，連 SQLite `mode=ro` 都未需動用；`data/faulthandler.log` 未開啟）。
> **調查時刻 HEAD**：`a1bf9e6`（TEST-HYGIENE-2；`git status` 僅 1 個預先存在的 untracked `scripts/_watchdog_hidden.vbs`，非本票產物，未動）。
> **前置基線**：今日已閉環 AUDIT-2（§0.1.15）、碰撞窗 14h（WINDOW-1）、昇華 consume 接線（CONSUME-1＋FUP-1，merge `8f645c1`）、自軸豁免（GOAL-AXIS-GUARD-1，`96528e8`＋`e799f11`）、去重冪等（VALUE-DEDUP-A-1，`fb7ab63`＋`469de8d`）、S 軸模擬通（S-SEED-SIM-1，`92cae38`）、hygiene 清零（`0aeca3b`）。**本句為調查時刻快照；之後生產已重啟 3 次（18:23 接縫＋碰撞窗、18:36 自軸豁免、18:48 去重冪等），新碼全生效**——凡需 runtime 證據的票一律列入 B 批。
> **門檻現況（AUDIT-2 七天窗 10-04~10-11）**：一❌（2/4：whim 8 / necessity 2 / goal 0 / world_collision 0，單一起源 **80.0%** 恰壓線）、二❌（`run_elevation` 每日行使 98 elevated、value 80，但**線頭來源 0/530**，value 全 `diary:night`；Owner 已裁定 diary 衍生 value **不計入**）、三✅（mahiru 連 8 天、7 distinct 標題）。

---

## 0. 盤點來源對照（逐項過，唯讀）

| # | 來源 | 本審計的取用結論 |
|---|---|---|
| 1 | `docs/INNER-LIFE-DEFINITION-OF-DONE.md`（三門檻＋依賴＋排除） | 門檻判準與依賴順序（門檻一→三→二，門檻二可獨立推進）維持 canonical；排除項（契約落地≠完成、發訊數、線頭總數、LLM 量）本文件沿用，不另開票 |
| 2 | `logs/ENGINEERING_STATE.md` §0.1（W1/W2、錨 A、P0/P1 缺口、日記標籤、門檻二錨點）＋ §5（里程碑） | W1 生產證據首次成立（whim 10-04 起 8 線頭）；W2 尚無效果；P0（新聞沉澱）已清理＋根因已修；P1（標籤冒充信念）已修（narrative 接入）；錨 A 已落地；§5 確認 M5.13/M5.14/M6.0/M5.15 全 CLOSED，無遺留施工債 |
| 3 | 已知未修項（見 §1） | 逐項已唯讀複核，全部轉成工單（A1/A2/A5/A3/B2/B1/C1）或觀測登記，無丟失 |
| 4 | INNER-LIFE-AUDIT-1 三大斷點殘留（見 §2） | 逐項歸屬：**0 項屬內在生活施工範圍**，三項分屬 TA-2 線 / SG 線 / 傳輸-時間感知線 |
| 5 | 門檻二完整鏈（見 §3） | dissolve✅ → consume✅（未生效） → elevate⚠️（行使中但 0 線頭來源） → projection⚠️（新 belief 投影未測；DORMANT/SUPERSEDED 回憶未做＝已知缺口） |

---

## 1. 已知未修項複核（唯讀實測，非自報）

| 項 | 複核結果 | 去向 |
|---|---|---|
| P4 `suspend_on_takeover` dead API | 屬實：定義於 `src/goals/motive_provider.py:639`，全 `src` 呼叫點 **0**（僅 docstring `:563` 提及）；呼叫者僅 tests/harness（`test_goal_engine.py:529`、`test_goal_axis_guard.py:217`、`test_goal_driven_harness.py:379`）。生產 dead 確認 | A1 |
| `_14400` 函數名 | 屬實且為**誤名**：`tests/soul/test_life_thread_wake_gate_m3.py:1812` `test_t12_f1_collision_window_seconds_are_hardcoded_14400` 實 assert `14*3600==50400`（14h），名含 14400（=4h，舊窗值）實為 50400。生產常數 `WORLD_COLLISION_WINDOW_HOURS=14`（`life_thread_wake_gate.py:66`、`life_thread_origins.py:100`）正確，錯的只有測試函數名 | A2 |
| 既有 80 重複 value 節點 | 觀測確認：AUDIT-2 登記 value 80 全 `diary:night`；VALUE-DEDUP-A-1 已讓 `_persist_result` 對 (agent,node_type,content_hash) 冪等（未重啟未生效）。**不清倉決策已定**，本票只列觀測＋守門確認 | A5（觀測，不清倉） |
| signal 6 生產 0 次 | 定義確認：signal 6 ＝ Decision 連續 not_transmit ≥3 → SUSPENDED（`motive_provider.py:66` 常數 `GOAL_SUSPEND_NOT_TRANSMIT_STREAK=3`，`:389/:418/:559/:597`）。生產是否曾開火**未查**（本票未讀 DB），是否「該有」需唯讀觀測 decision_trace sidecar 後再判 | A3 |
| S4 motive_trace 等種子生產未驗 | 屬實：S-SEED-SIM-1（`92cae38`）tmp 隔離＋stub LLM＋假時鐘全鏈模擬通；生產 seed 事件 0 觀測 | B2 |
| whim 80% 壓線平衡 | 屬實：七天窗 whim 8/10＝**80.0%**，恰為門檻一「單一起源 ≤80%」邊界值（技術上達標線內，但無緩衝；且門檻一另因 2/4 起源而不成立） | B1 |
| goal B 軸在缺席期的語意 | 待裁定：0.1.16 新原則下 09-14「拒送留白」收窄至發訊端，goal 全 SUSPENDED 由「正確行為」改判「待查缺口」（GOAL-SUSPEND-SCOPE-1 唯讀調查已另開）；自軸豁免碼已併未生效；Bryan 回來喚醒是否及時缺生產事件 | B4（觀測）＋ C1（語意裁定） |

---

## 2. AUDIT-1 三大斷點殘留歸屬（內在生活施工範圍 vs 別線）

| 斷點 | 歸屬 | 理由與去向 |
|---|---|---|
| TA-2 10/10 無感 | **別線（TA-2）**，非內在生活施工範圍 | 時間參與解釋（temporal phenomenology）屬 TA-2 線；且 TA-2-A 已由 Owner 重定義並授權實作（§0.1.8），另線追蹤。本線不開票 |
| 關係 100/100 stranger | **別線（SG）**，非內在生活施工範圍 | 社交圖譜關係帶屬 SG 線；Bry 軸已由 RELATIONAL-BAND-FIX-1 修法＋SG-4 契約增補處理，peer 軸屬 SG 後續。本線不開票 |
| `silence_duration` 缺失 | **別線（傳輸-時間感知）**，非內在生活施工範圍 | 該量測點在全 `src` 不存在（僅 `last_interaction_period`，`proxy.py:976-995`）；同源 suffix bug 已由 TA-1 修復。本線不開票 |

**結論：三大斷點 0 項落入內在生活施工範圍，本審計不為其開票。**

---

## 3. 門檻二完整鏈狀態（dissolve → consume → elevate → projection）

| 環節 | 狀態 | 證據 |
|---|---|---|
| dissolve（M2 溶解＋catchup） | ✅ 已閉環 | M2-WIRING-1＋M2-CATCHUP-1；21 條溶解事實 21/21 有 `dissolved_at`＋`sage_fact_id` |
| consume（W3 接縫） | ✅ 已接線，**未生效** | CONSUME-1＋FUP-1 已併（`8f645c1`），生產未重啟；首輪 22:00 night slot 26 筆提交為部署前舊碼行為待重驗 |
| elevate（`run_elevation`） | ⚠️ 行使中但 0 線頭來源 | 每日行使（98 elevated、value 80），線頭來源 0/530；value 全 `diary:night` 且 Owner 裁定不計入門檻二 |
| projection（`emergent_projection` 對新 belief） | ⚠️ 未測 | read-side 投影存在（ACTIVE 態 belief/value/trait 投影先例），但**對「門檻二新產 belief」的投影是否發生未測過** → A4 |
| DORMANT/SUPERSEDED 回憶 | ❌ 已知缺口，未做 | SE-5 lifecycle 四態齊備；讀側回憶（「我以前很在意」）未做；TG-1 明確排除 DORMANT 出 v1 → 需 Owner 範圍裁定先行 → C4 |

---

## 4. 工單總表（12 票：A 5 / B 4 / C 3；另 A6/A7＋C1 已完成保留備查，共 15 行）

| ID | 目標一句話 | 工法 | 大小 | 依賴（前置票） | Frozen 觸碰 | 需 Owner 裁定 |
|---|---|---|---|---|---|---|
| A1 IL-DEADAPI-1 | `suspend_on_takeover` 生產 dead API 處置（三選一：刪／接線／留＋註記） | READ-ONLY | S | 無 | 無 | **有**（刪留二選一） |
| A2 IL-TESTNAME-1 | 誤名測試 `..._hardcoded_14400` 改名（實值 50400，名實不符） | IMPL（test-only） | S | 無 | 無 | 無 |
| A3 IL-SIGNAL6-1 | signal 6（連續 not_transmit≥3 掛起）生產是否曾開火唯讀觀測＋「是否該有」判定 | READ-ONLY | S | 無 | 無 | 無 |
| A4 IL-PROJ-1 | `emergent_projection` 對門檻二新產 belief 的投影覆蓋審計（測試＋trace 雙查） | READ-ONLY | S | 無 | 無 | 無 |
| A5 IL-DEDUP-1 | 既有 80 value 觀測＋VALUE-DEDUP-A-1 冪等守門生效確認（**不清倉**） | READ-ONLY | S | 重啟已完成（18:23 起），待自然 slot 確認生效；觀測本身可先行 | 無 | 無 |
| A6 ELEVATION-SEAM-1 | 線頭→昇華接縫生效驗證＋缺口補齊（Owner 10-11 已授權開票）✅ 已完成（見 §0.1.15＋部署記錄），本表保留備查 | IMPL/TEST | M | 生產重啟（Owner 授權） | 無（既有接縫路徑內；超範圍需另申請） | 無（已授權；閾值調整另見 C2） |
| A7 WORLD-COLLISION-BREAK-1 | `world_collision` 全時 0 打破（W2 無效果的後續；Owner 10-11 已授權開票）✅ 已完成（見 §0.1.15＋部署記錄），本表保留備查 | IMPL/TEST | M | 無（sim 先行） | 無（超範圍需另申請） | 無（已授權；閾值最終值另見 C2） |
| B1 IL-WHIMBAL-1 | whim 80.0% 壓線平衡追蹤（完整 7 天窗重測＋緩衝評估） | READ-ONLY | S | 生產重啟＋完整 7 天窗 | 無 | 無 |
| B2 IL-S4PROD-1 | S4 `motive_trace` 種子生產驗證（sim 已通 `92cae38`，待生產 seed 事件讀數） | TEST | S | 生產自然 seed 事件 | 無 | 無 |
| B3 IL-SEAM-EFFECT-1 | 接縫生效後線頭來源 value 觀測（`run_elevation` 線頭來源 0/530 是否鬆動） | READ-ONLY | S | A6＋生產重啟＋≥1 輪 `run_elevation` | 無 | 無 |
| B4 IL-BWAKE-1 | Bryan 回來喚醒及時性觀測（在場事件→自軸/S 軸 goal 恢復時延） | READ-ONLY | M | Bryan 在場事件（自然發生，不等人為觸發） | 無 | 無（語意裁定見 C1） |
| C1 IL-GOALABS-1 | goal 缺席期語意裁定請示（B 軸暫停可接受／自軸不該停；沿 0.1.16 原則）語意已裁定並實作；殘留喚醒及時性併入 B4，本票關閉備查 | READ-ONLY | M | GOAL-SUSPEND-SCOPE-1 調查結論（參考，不硬等） | 無 | **有** |
| C2 IL-THRESH-1 | `world_collision` 閾值最終裁定請示（0.33 見 weather／0.23 見 news，出處 `docs/INNER-LIFE-DEFINITION-OF-DONE.md:122`「門檻降至 0.33 ⇒ weather 可見；降至 0.23 ⇒ news 可見」；另見 `docs/LIFE-THREAD-W2-PERCEPTION-SCORING-WORK-ORDER.md:45-46` 實算 0.3300／0.2300；與 09-14「不值得傳訊」一併權衡） | READ-ONLY | S | 無 | 無 | **有** |
| C3 IL-DIARYVAL-1 | diary 衍生 value 既有節點處置裁定請示（不清倉／重標／保留三選一；門檻二不計入已定） | READ-ONLY | S | A5 觀測（參考） | 無 | **有** |
| C4 IL-RECALL-1 | DORMANT/SUPERSEDED 回憶讀取設計＋實作（「我以前很在意」可查詢） | IMPL | M | C1（範圍）＋B3（有線頭來源產物後才有真實回憶對象） | 無（讀側；碰 SAGE 寫入需另申請） | **有**（TG-1 排除在先） |

註：A1 雖需 Owner 裁定但調查本身現在可開（裁定為驗收條件而非開工條件），故列 A 批；同理 A5 的重啟依賴僅為「生效確認」部分，觀測可先行。

---

## 5. 依賴排序（三批）

### A 批：現在可開（5 票：A1–A5；A6/A7 已完成保留備查）
無前置生產證據需求，未決裁定阻擋（A1 的裁定在驗收端）。建議順序：A3/A4/A5（純觀測先行，0 風險）→ A2（test-only 改名）→ A1（調查＋裁定請示）。

### B 批：需等生產證據（4 票：B1–B4）
- B1：重啟已完成（18:23 起），待自然 slot 滿完整 7 天窗。
- B2：等生產自然 seed 事件（不等人為觸發、不為了驗證而造事件）。
- B3：重啟已完成（18:23 起），A6 已閉環，待自然 slot ≥1 輪 `run_elevation`。
- B4：等 Bryan 自然在場事件（跨越數小時的等待不得依賴背景 job，以落盤證據事後回讀）。

### C 批：需 Owner 先裁定（3 票：C2–C4；C1 已關閉備查）
- C1：goal 缺席期語意（B 軸 vs 自軸）。
- C2：world_collision 閾值最終值。
- C3：diary-value 既有節點處置。
- C4：DORMANT/SUPERSEDED 回憶範圍（TG-1 排除在先，需範圍裁定；實作另依賴 B3 產物）。

---

## 6. 每票驗收（一句話）

- A1：產出 dead API 三選一處置建議＋Owner 勾選其一（未改 `src` 即算達標，調查票）。
- A2：誤名測試函數名實值相符（50400），全庫回歸差分 0。
- A3：以唯讀查詢回答「signal 6 生產開火次數＋是否符合預期」，附查詢語句與輸出。
- A4：以測試＋trace 雙查回答「新 belief 是否被投影」，缺口轉為明確測試債或關閉。
- A5：80 value 觀測快照＋冪等守門生效（重啟後新重複 0 新增）確認，**無任何刪除**。
- A6：重啟後首輪 slot 線頭事件經 consume 進入 pattern 候選（`thread_id`＋`sage_fact_id` 逐筆對應），frozen 三面 diff 全空。
- A7：sim 證明 `world_collision` 可達＋生產出現首個非零讀數（或證偽並回報瓶頸轉移）。
- B1：完整 7 天窗重測報告（四起源計數＋單一起源占比＋緩衝評估）。
- B2：生產 S4 種子事件 ≥1 筆可追溯觀測（或窗口內無事件的如實 NO DATA 報告）。
- B3：線頭來源 value 計數由 0/530 鬆動（或如實未鬆動＋原因定位）。
- B4：Bryan 在場→goal 恢復時延觀測報告（或窗口內無在場事件的 NO DATA 報告）。
- C1：Owner 對缺席期語意逐項勾選（B 軸／自軸／喚醒期望）。
- C2：Owner 勾定閾值（或維持現狀的明示決定）。
- C3：Owner 勾定既有節點處置（三選一）。
- C4：DORMANT/SUPERSEDED 節點可查詢＋回憶測試全綠（以合成資料驗收，不靠等生產）。

---

## 7. 明確排除項（本次刻意不列）

- ❌ **diary-value 回填**：Owner 已裁定 diary 衍生 value 不計入門檻二；既有節點處置待 C3 裁定，在此之前不開任何回填／重算票。
- ❌ **`motive_trace` 歷史重寫**：append-only 變更日誌語意，歷史列只讀，S4 驗證只看新事件（B2）。
- ❌ **EH-4.2 殘留 2 缺陷**（定義句錨點、五元組 subject 污染邊界外）：屬記憶昇華線（EH），非內在生活施工範圍。
- ❌ **TL-1 determinism 重驗**：P2 方法論線（§0.1.9），非內在生活施工範圍。
- ❌ **MS-3.1 `voice_session_*` 權限映射裁定**：屬工具感知線（§0.1.4 待 Owner），非內在生活施工範圍。
- ❌ **GOAL-SUSPEND-SCOPE-1 重複開票**：該唯讀調查已另開，C1 只做語意裁定請示，不重複調查。
- ❌ **生產重啟本身**：重啟需 Owner 逐次授權，由 Owner 在獨立終端執行；本審計只登記依賴，不開重啟票。
