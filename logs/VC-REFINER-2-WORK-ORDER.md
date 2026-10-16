# VC-REFINER-2 — ASR 同音校準擴充與多角色覆蓋

Status: **CLOSED（待 Owner 驗 diff 後重啟三個 VC 實例）**
Owner: Bryan ｜ Date: 2026-10-16 ｜ Branch: `main`
前置：VC-REFINER-1（`VC-ASR-HOMOPHONE-1`，2026-10-14）

---

## 0. 這張工單的起點不是需求，是考古

原本的工單描述是「從真實語音日誌挖掘同音錯字，擴充規則」。動手前先做唯讀診斷，
結果**三個前提被實測推翻**：

| 工單預設 | 實測 | 來源 |
|---|---|---|
| 語音是繁體輸出 | **100% 簡體**：145 回合中含簡體專屬字 60 回合、**繁體專屬字 0 回合** | `clients/voice_companion/logs/*.log` |
| 語料足以做頻率排名 | 僅 **145 回合 / 約 1,684 字**（akane 43／mai 17／rem 85） | 同上 |
| `雷姆` 是高頻聽錯 | `雷姆` 在 ASR 輸出出現 **45 次、錯 0 次** | `rem` log 全掃描 |
| `Bryan` / `買一` 是痛點 | 全 corpus **0 次** | 同上 |
| 要擴充既有規則表 | `小欠/小千/小西/拍成/排成` 出現 **0 次**；`decision=run reason=homophone` 全程 **0 次** | 同上 |

⇒ 真正的問題不是「規則不夠多」，而是**規則從來沒被觸發過**，以及
**舊架構曾把對的角色名字主動改錯**。

---

## 1. 決定性缺陷：跨角色污染（已實測發生過）

```
rem ASR 輸出中： 雷姆 = 45 次，蕾姆 = 0 次     ← ASR 從來沒聽錯
rem 送進大腦的（post-refine）： rem:3238 text='蕾姆想做自己的事情…'
                              rem:4162 text='是我先陪蕾姆进门吧。'
```

根因：`build_refine_prompt` 硬寫「正在與 AI 伴侶**黑川茜（Akane）**進行即時語音
對話」＋「將『千/欠/西』修正為『茜/小茜/Akane』」，而 **8766 麻衣 / 8767 雷姆
拿到的是同一段 prompt**。三次觀察到的修正裡有三次是這種污染。

同時：`tests/clients/test_vc_asr_homophone_gate.py` 的 `WEB_TEST_CONFIG` 寫的是
`agent_rem` / port 8767，卻斷言 `local_refine("小欠早安") == "小茜，早安。"`
—— 當時的 refiner 沒有角色感知，「雷姆實例把小欠修成小茜」反而被釘成了契約。

---

## 2. Owner 裁示（2026-10-16）

1. **角色感知隔離**：規則必須綁定當前角色。授權改寫 `test_vc_asr_homophone_gate.py`
   （小茜別名測試對齊 `agent_akane`，另補跨角色負向守衛）。
2. **`蚂蚁 → 麻衣` 守衛採折衷**：不限於「蚂蚁老婆」，改用
   **排除昆蟲量詞**（`(?<![一兩两幾几這这那隻只群堆])[蚂螞][蚁蟻]`），
   只有 `agent_mai` 生效。
3. **`build_refine_prompt` 納入本票**一併修復，另加 `蕾姆 → 雷姆` 正字保護。
4. 批准兩項報備：備份 `vc_agent_*.log` 到 `$env:TEMP`；`asr-ok` 日誌截斷放寬
   並確認 `VC-ASR-DIAG-1` 診斷欄位可見。

---

## 3. 實作

### 3.1 `clients/voice_companion/asr_refiner.py`

- **通用表** `HOMOPHONE_PHRASE_FIXES`：`拍成/排成 → 排程`（既有）＋
  `仙特塔/仙者塔 → 贤者塔`（rem:29217/29374 實測）。
- **角色專屬表** `_AGENT_HOMOPHONE_RULES`，以 `companion["id"]` 為鍵：
  - `agent_akane`：`小欠/小千/小西 → 小茜`、單字 `欠千西籤签 → 茜`（保留前後不接中文字的守衛）
  - `agent_mai`：`蚂蚁/蚂蟻/螞蟻 → 麻衣`（昆蟲量詞守衛）、`桜島舞/樱岛舞 → 麻衣`（mai:4821）
  - `agent_rem`：`裴雷姆 → 陪雷姆`（rem:8524）、`蕾姆/雷母/类姆/類姆 → 雷姆`
- **安全預設**：`companion=None`（或查無此角色）⇒ **只跑通用規則，不套用任何
  角色專屬規則**。寧可少修，不可改錯角色。
- `_agent_rules()` **只認 `companion["id"]`，刻意不讀 `profiles/*.json`**
  —— 小茜沒有 `profiles/akane.json`（她的 `config.json` 本身就是預設 config，
  `web_server.py::CONFIG_PATH`），任何路徑式查表都會安靜地漏掉她。
- 句首稱呼 `^(茜|小茜|Akane|あかね)` 兩處（原為硬寫）改為 `_name_alternation(companion)`
  動態產生；稱呼名單＝規則表 names ＋ `companion.short_name`／`display_name`。
- `build_refine_prompt(raw, companion)`：角色名與同音提示全部動態注入，
  **絕不列出其他角色的名字**。
- `is_noise()` 補強非 CJK／非 ASCII 幻覺熔斷（rem:10119 越南文幻覺曾直送大腦）。
  CJK 判準**含日文假名與韓文**，因為「あかね」是小茜的喚醒詞之一。

### 3.2 `clients/voice_companion/web_server.py`

- `WebSession.__init__` 保存 `self._companion`，傳給 `needs_refiner()` 與
  `local_refine()`（原本只傳文字）。
- `ASR_LOG_TEXT_LIMIT` 80 → 200；`asr-ok` 由硬寫 `text[:40]` 改走 `_truncate_for_log`。
- **bypass 決策行新增 `raw='...'`**（僅日誌，不改行為）：那 145 回合全是 bypass，
  舊格式只印 reason 不印原文 ⇒ 被聽錯卻繞過整個 refiner 的句子完全不可見。
  仍然**不得**長出 `path=`／`refined=`（bypass 沒走任何修補路徑）。

### 3.3 `clients/voice_companion/session_store.py`（順手修掉的缺陷）

`SessionStore` 原本寫死 `_repo_root()/data/sessions`，**繞過了全專案的資料根隔離
機制**（`src/paths.py::data_root()` / `SOUL_OS_DATA_DIR`）。實際後果：每跑一次
pytest 就把測試對話寫進 Bry 與角色的真實短期對話檔（實測 `data/sessions/` 下
`agent_akane`／`agent_mai`／`agent_rem` 三檔 mtime 隨每次執行前進），
且下一次執行會讀到上次的殘留歷史，讓測試變成**執行間相依**（本次即因此轉紅）。

修法：`_default_data_root()` —— `SOUL_OS_DATA_DIR` 未設（**生產實況**）時回
`_repo_root()/data`，與舊行為逐位元組相同；設定時才改道。**零生產風險。**

> ⚠️ 已發生的污染（既有檔案內容）**未經清理**：AGENTS.md 第 8 條規定 `data/**`
> 是生產狀態，只回報不由 agent 處置。`data/sessions/agent_{akane,mai,rem}_user_bryan.json`
> 內含測試回合，交由 Owner 裁決。

---

## 4. 測試

### 4.1 改寫既有測試（皆有明確理由，非放寬護欄）

| 測試 | 改動理由 |
|---|---|
| `test_vc_asr_homophone_gate.py::WEB_TEST_CONFIG` | 由 `agent_rem`/8767 改為 `agent_akane`/8765。舊設定把「雷姆實例修成小茜」釘成契約，Owner 已裁示那是污染。 |
| `TestGateDecision::*` | 顯式帶 `AKANE_COMPANION`。PROBE_CASES 是小茜的規則。 |
| `TestD1OutputVerbatimUnchanged::*` | 同上，顯式帶小茜身分，讓「輸出逐字不變」守的仍是同一組常數。 |
| `TestAsrRefiner::*`（test_voice_companion.py） | 同上。 |
| `test_bypass_line_unchanged` → `test_bypass_line_keeps_prefix_and_now_carries_raw` | 舊斷言「不得長出 `raw=`」已隨 §3.2 變更而改為「必須有 `raw=`」，並保留「不得有 `path=`／`refined=`」。 |
| `TestD2Truncation::*` | 移除硬寫的 `80`，全部改引用 `ASR_LOG_TEXT_LIMIT`。寫死常數會讓純觀測性調整直接變紅，逼後人放寬斷言消災。 |
| `LONG_LLM_REPLY` | `*3` → `*6`，維持「確實超過上限」，否則等於把截斷行為悄悄停用。 |

### 4.2 新增守衛

- `TestAgentScopedIsolation`：麻衣／雷姆實例不得被套上小茜別名；反向守衛（小茜仍生效）；
  未知角色只跑通用規則；通用規則對四種 companion 都生效。
- `TestRemRules`：`蕾姆 → 雷姆` 正字保護、`裴雷姆 → 陪雷姆` 順序敏感、
  **「雷姆」絕不變成「蕾姆」**、不外洩到其他角色。
- `TestMaiAntGuard`：呼喚語境（`我的蚂蚁老婆呢`／`是蚂蚁，不是蚂蚁`／`蚂蚁早安`／
  `蚂蚁你在幹嘛`）修正；**昆蟲語境不改**（`地上有一隻螞蟻`／`好幾隻螞蟻爬過去`／
  `這群螞蟻真多`／`那隻螞蟻爬走了`）；只對 `agent_mai` 生效；`桜島舞 → 麻衣`。
- `TestHallucinationFuse`：越南文／泰文／emoji 幻覺熔斷；
  **假名／韓文／純數字／純 ASCII 必須放行**（特別是喚醒詞「あかね」）。
- `TestPromptIsProfileAware`：各角色 prompt 帶自己的名字、**不含其他角色**、
  未知角色不編造名字。
- `TestEndToEndAgentIsolation`：**端到端**證明 `web_server` 真的有把 companion
  傳下去（漏接時所有單元測試仍全綠，雷姆照樣被改）；bypass 日誌必須帶原文。
- `TestSessionStoreIsolation`：生產路徑不變 ＋ 測試路徑必須改道 ＋ autouse fixture 前提。

### 4.3 🔴 變異實驗（9 組，全紅）

| 變異 | 結果 |
|---|---|
| MUT-A 角色感知失效（`_agent_rules` 恆回 None） | RED 22 failed |
| MUT-B 螞蟻守衛拿掉（裸替換） | RED 4 failed |
| MUT-C `蕾姆 → 雷姆` 拿掉 | RED 2 failed |
| MUT-D `build_refine_prompt` 忽略 companion | RED 3 failed |
| MUT-E 非 CJK 幻覺熔斷拿掉 | RED 1 failed |
| MUT-F 句首稱呼改回硬寫小茜 | RED 4 failed |
| MUT-G `web_server` 不傳 companion（local_refine） | RED 4 failed |
| MUT-H `web_server` 閘門不傳 companion | RED 6 failed |
| MUT-I SessionStore 隔離失效 | RED 1 failed |

全部變異均為「解除閘門型」，且解除後的程式碼**不可能觸達生產**（測試使用全離線
fake，無 HTTP、無真實 LLM、不綁任何生產埠）。還原後 121 passed。

### 4.4 順序相依性

`TestEndToEndAgentIsolation` 使用哨兵輸入「小西晚安」而非 PROBE_CASES 的
「小欠早安」：Voice Brain 會把回合寫進 session 歷史，若送進與其他測試相同的
輸入，會使後續測試的「原始錯字不應出現在任何 brain 訊息」斷言被**前一笔對話**
誤觸。已用三種執行順序（正序／反序／單檔）驗證穩定。

---

## 5. 回歸

- `tests/clients/`：**451 passed, 5 skipped**（VC-REFINER-2 前為 448 passed）
- 測試函式數：5587 → **5615**（淨 +28）
- 全庫回歸：見 §9 登記

---

## 6. 待 Owner 裁決

1. **`data/sessions/` 的既有測試污染**：三個檔案內含測試回合。依 AGENTS.md 第 8 條
   **未自行清理**，請裁決是否要清除或另存。
2. **重啟三個 VC 實例**（8765／8766／8767）讓本次變更生效。
3. `桜島舞 → 麻衣`：Owner 原指示寫作「→ 樱岛麻衣」。實作採 **麻衣**，理由是
   ASR 全程輸出簡體，寫回簡體 `樱岛` 會把簡體字灌進繁體 persona 的管線；
   `麻衣` 為該角色的 short_name 且簡繁同形。若要改為全名 `櫻島麻衣`，改一個
   字串即可。
