# SOUL-MEM-001 Phase 0 — 真實 Prompt Baseline 量測報告

- **工單**：SOUL-MEM-001（Gemini/Rem 開立）— 記憶系統熱路徑重構與狀態支配力審計
- **量測日期**：2026-10-12
- **量測者**：Lin（主大腦）
- **Bry 裁示**（2026-10-12）：本次**只做量測，不砍任何東西**；三條路徑都量，只挑一條做 Phase 0；狀態被外部竄改採**「服從但留痕」**語義
- **原始數據**：`logs/prompt_baseline_20261012.json`
- **量測腳本**：`scripts/measure_prompt_baseline.py`

---

## 0. 一句話結論

工單驗收標準 2 的前提**不成立**。真實數字不是「>3,500 → <700」，而是 **7,819–15,349 token，且其中 88–92% 是 persona**。排除 persona 後真正可壓縮的 payload 只有 **837–1,597 token**，已經接近工單自己訂的 <700 目標。「縮減 ≥5×」在數學上無從成立，因為起點根本不在 3,500。

**但工單想解決的問題是真的，只是診斷錯了對象。** 真正嚴重的 context 污染只有一處：三條失控的 assistant 訊息被持久化進 history 並逐輪重餵。

---

## 1. 量測方法與安全邊界

### 1.1 單位聲明（重要）

本機 `.venv` **沒有安裝 tiktoken**（實測 `ModuleNotFoundError`）。因此本報告為**估算**，非真實 tokenizer 計數。雙軌報告：

| 口徑 | 定義 | 用途 |
|---|---|---|
| `tok_cjk_aware`（主值） | CJK 漢字/假名 ≈ 1 token，其餘 ≈ 4 char/token | 符合中文內容實況 |
| `tok_chars4`（對照） | `len(text) / 4` | repo 既有慣例（`src/temporal/render.py:9`、`src/memory/sage/token_utils.py:16`） |

⚠️ **repo 既有的 `CHARS_PER_TOKEN = 4` 對中文嚴重低估**，本次 yua 的 persona 兩者相差 2.6 倍（14,054 vs 5,335）。引用任何 repo 內既有的 token 估算時都必須注意這點。

### 1.2 安全邊界（已遵守）

- ✅ 唯讀。全部 `open(mode='r')`，未寫、未改名、未刪除 `data/**` 任何檔案。
- ✅ 未啟動伺服器、未綁任何 port、未發任何 HTTP 請求 ⇒ **LLM 成本 = 0**。
- ✅ 未 import / 未呼叫 `consciousness._fire_intent`（會 publish 到 eventbus 觸發真實推理）。
- ✅ 未殺任何行程。
- ✅ 僅新增兩個檔案：`scripts/measure_prompt_baseline.py`、`logs/prompt_baseline_20261012.json` 及本報告。

---

## 2. 真實 Baseline

### 2.1 完整組裝後的 messages（實際送進模型的量體）

| agent | 路徑 | total | persona | **payload 排除 persona** | msgs |
|---|---|---|---|---|---|
| agent_yua | private | 15,349 | 14,054 | **1,295** | 12 |
| agent_yua | group | 14,956 | 14,054 | **902** | 3 |
| agent_ruka | private | 13,414 | 11,817 | **1,597** | 12 |
| agent_ruka | group | 12,734 | 11,817 | **917** | 3 |
| agent_aoi | private | 7,819 | 6,910 | **909** | 18 |
| agent_aoi | group | 7,747 | 6,910 | **837** | 3 |

**persona 佔比 88–92%。這是唯一的大頭。**

### 2.2 persona 檔案本體（10 份，共約 5,456,000 bytes 級）

| agent | chars | tok(cjk) | tok(c/4) |
|---|---|---|---|
| agent_yua | 21,340 | 14,054 | 5,335 |
| agent_ruka | 17,575 | 11,817 | 4,393 |
| agent_aoi | 11,288 | 6,910 | 2,822 |
| agent_rem | 10,640 | 8,119 | 2,660 |
| agent_mahiru | 10,614 | 6,723 | 2,653 |
| agent_akane | 10,216 | 7,483 | 2,554 |
| agent_miku | 9,699 | 5,734 | 2,424 |
| agent_mai | 8,094 | 5,112 | 2,023 |
| agent_anna | 8,028 | 5,197 | 2,007 |
| agent_ram | 6,534 | 3,933 | 1,633 |

### 2.3 proxy read-side 投影層（這才是真正的 Σt 投影）

`src/llm/proxy.py` 目前有 8 個 `_format_*` 投影區塊。實測每個 agent 產出的 token：

| 區塊 | token（agent_*) | 備註 |
|---|---|---|
| `_format_recent_inner_life` | 227–271 | 最大宗 |
| `_format_emergent_block` | 167–188 | |
| `_format_capability_block` | 132（固定） | |
| `_format_life_thread_lines` | 116–126 | |
| `_format_horizon_block` | 0–68 | 僅 agent_rem 有內容 |
| `_format_attachment_str` | 13 | |
| `_format_relationship_block` | **0** | **空殼，無投影** |
| `_format_temporal_context` | **0** | **空殼，無投影** |

**合計約 723–1,010 token。** 這就是工單想「以權威指令取代」的對象——它已經存在、有契約定序（契約 §2.1）、而且大多數區塊是有內容的。

**值得注意的是：`_format_relationship_block` 與 `_format_temporal_context` 目前產出 0 token。** 工單若重建一套新的「權威狀態注入」，不會比現況更好——現況已經少到連兩個區塊都沒東西可講。

---

## 3. 🔴 唯一的真實異常：mahiru 私聊歷史被三條巨型 assistant 訊息佔滿

### 3.1 數據

`data/conversations/` 私聊歷史檔大小分布：

| 檔案 | chars | tok | turns |
|---|---|---|---|
| **`bryan_agent_mahiru_private.json`** | **22,220** | **15,848** | 17 |
| `bryan_agent_ruka_private.json` | 2,169 | 1,040 | 20 |
| `bryan_agent_akane_private.json` | 2,091 | 844 | 19 |
| `bryan_agent_yua_private.json` | 1,928 | 848 | 20 |
| `group_chat.json` | 2,266 | 655 | 20 |

**mahiru 是中位數的 15–20 倍。**

### 3.2 形狀分析（只量形狀，未印內容）

```
  # role           chars  content_kind
  0 assistant         37  plain
  1 assistant         37  plain
  2 assistant         45  plain
  3 assistant         34  plain
  4 assistant      6,690  plain +HUGE   ←
  5 assistant      6,938  plain +HUGE   ←
  6 assistant      6,722  plain +HUGE   ←
  7 assistant        199  plain
 ...（其餘皆 <200 chars）

total chars: 20,866   avg/turn: 1,227
```

**三條連續的 assistant 訊息各約 6,700–6,940 字，合計 20,350 字，佔整份歷史 97.5%。** 其餘 14 輪合計僅 516 字。

### 3.3 為什麼這才是真正的問題

1. 這是**逐輪重餵的**——每次 private 組裝都會把這三條原樣送進模型。
2. 這是**自我強化的**——assistant 看到自己上一次的巨型輸出，傾向產生同型態的輸出。
3. 現有的 `strip_assistant_parens`（REM-TEXT-1, 2026-09-11，`src/llm/proxy.py:1345`）**只剝除括號動作段**，對 6,900 字的無括號巨塊無效。
4. 工單描述的「剔除過去的執行軌跡與過時對話」**方向是對的**，但它把原因歸給「Charter 全量重傳」，實際上是**個別 agent 的歷史汙染**。

**這一處的量級（15,848 token）就超過工單設定的整個 "<700 token" 目標 22 倍。** 處理它不需要 `state.json` 架構、不需要 Gatekeeper、不需要重寫 prompt 構造器。

---

## 4. 對工單三項驗收標準的逐條判定

| # | 工單驗收標準 | 判定 | 依據 |
|---|---|---|---|
| 1 | 決策保真度：10 次真實 tick 舊新重放，Valence/Arousal 100% 一致 | **不可執行且門檻錯誤** | ① 會產生 20 次真實 LLM 呼叫（成本非 0）；② `src/heartbeat/engine.py:214-216` 明載 Agency Stage 1-4 不讀 carryover，該決策路徑目前**沒有接線**；③ 換 prompt 本就可能改變輸出，100% 一致往往代表新架構沒生效 |
| 2 | Input Token >3,500 → <700，縮減 ≥5× | **前提不成立** | 真實 total 7,819–15,349；persona 佔 88–92%；排除 persona 後 payload 僅 837–1,597，**已接近 <700**。5× 縮減無從成立 |
| 3 | 狀態漂移注入測試，模型須 0 步完全服從竄改後狀態 | **紅線衝突，已由 Bry 改判** | 竄改 `data/**` 觸 AGENTS.md 鐵律 8。Bry 2026-10-12 裁示改為**「服從但留痕」**（詳 §5） |

### 附：工單五個事實前提的核對結果

| 工單假設 | 實際 |
|---|---|
| `core/` 為專案目錄 | ❌ 不存在。架構為 `src/<domain>/` |
| `carryover.db` | ❌ 不存在。實際為 `data/agents/<agent_id>/carryover.json`（每角色一份，10+ 份）。欄位 4 殘留值 + `decay_rate` + 工單漏掉的 `source_event` / `triggered_at` |
| `DispatchGate`（longing / concern reserve / 今日已傳送旗標） | ❌ 不存在。`longing` 是純函數即時推導（`src/agent/emotion.py:1172` `compute_longing`），且只在 Soul Wall 唯讀顯示端點（`src/io/gateway.py:541`）使用，非 LLM dispatch 輸入。存成持久欄位會變過期快取，且與 Gatekeeper「實體必須出現於 O_t」規則衝突（推導值不會每次都在觀測裡） |
| 全量重傳的 `Charter` | ❌ 找不到對應檔案。實際 system prompt 來源為 `personas/<agent_id>.md`，經 `src/llm/proxy.py:1574` 組裝 |
| 單一 prompt 構造器 | ❌ 有兩條獨立組裝路徑（`_build_messages_group:682` / `_build_messages_private:1329`），且 `src/agent/consciousness.py` 內有 **11 份** 各自的 `_build_intent_payload` |

---

## 5. 「服從但留痕」語義定義（Bry 2026-10-12 裁示）

工單驗收 3 原文「模型必須 0 步完全服從篡改後的新狀態」宣告人工編輯具有絕對權威，與本 repo「`data/**` 異常只回報、不靜默採納」的紀律相反。**裁示為折衷方案，語義定義如下：**

1. **服從**：`state.json` 遭外部修改後，下一輪執行**正常採納新值**，不還原、不質疑、不要求 Bry 確認。
2. **留痕**：每次採納外部竄改，必須在 `events.jsonl` 寫入一筆帶明確標記的紀錄，欄位至少含：
   - `state_hash_before` / `state_hash_after`
   - `drift_detected: true`
   - `drift_fields`: 具體被改動的欄位清單
   - `drift_source`: 外部（預設值，非模型自身輸出）
3. **模型自身產生的 patch** 與**外部竄改**在日誌中必須可區分，不得混為一談。
4. Bry 互動層需能看到竄改紀錄（檔案位置由實作時決定）。

⚠️ 本語義僅為規格定義，**Phase 0 不實作**（本輪範圍為純量測）。

---

## 6. 建議的 Phase 0（修正版）範圍

Bry 裁示「三條路徑都量，只挑一條做 Phase 0」。基於本報告，**建議直接挑私聊路徑（`_build_messages_private`）**：

- payload 排除 persona 後最大（1,295–1,597），可優化空間最明確。
- **mahiru 的巨型歷史污染就在這條路徑上**，是唯一有實質異常的案例。
- group 路徑 payload 僅 837–917 tokens，已經很精簡，沒有優化理由。

### 建議下一步（另開工單，不在本輪）

1. **查清 mahiru 那三條 6,900 字訊息的來源**——是模型失控、LLM 串流重複、還是 tool 輸出被誤寫入 assistant 回合。**這是根因，未查清前不該動任何架構。**
2. 加一道 history 寫入端的尺寸閘門（例如單則 assistant 訊息超過 N 字拒絕寫入並告警），成本遠低於整套 `state.json`。
3. 「狀態支配力」若確定要做，**應針對既有的 8 個 `_format_*` 投影層擴充，不是另建 `core/prompt_builder.py`**——否則會出現兩套並行的 prompt 組裝邏輯。

---

## 7. 🔴 根因鑑識（2026-10-12 追查，補充 §3）

§3 記錄了 Mahiru 的巨型訊息異常。本節為根因追查結果，**已完全確定**。

### 7.1 三條訊息的機械結構

| 性質 | 實測 |
|---|---|
| 首 48 字元 | `{"text": "……Bryan？在吗？粥要凉了…` |
| 換行數 | **0**（單行） |
| `"` 數量 | 3（`{`、`text`、value 開頭）——**value 的開引號無配對結尾** |
| 結尾 | 中文字元（`好` / `。` / `真`）——非結構化結尾 |
| `json.loads` | 失敗：`Unterminated string starting at char 9` |
| 三者相似度 | jaccard ≈ 0.00–0.085（**互不相同**） |
| 內部自我重複 | turn4/turn5 各有 14 個重複的 120 字元區塊；turn6 無 |

### 7.2 根因鏈（100% 確定）

1. 呼叫走 `response_format={"type":"json_object"}` JSON mode（`src/llm/proxy.py:1691, 1711-1712`；Bry 2026-08-05 拍板 Lesson 32「只用 response_format 純一路」）。
2. 模型輸出 `{"text": "` 開頭的 JSON 殼，接著寫入中文散文。
3. 散文退化為重複（「——嗯。好」這類短句反覆），**打滿 `max_tokens: 4000`**（`configs/default.yaml`）⇒ `finish_reason=length` ⇒ 回應在 JSON 字串尚未閉合處被硬截斷。
4. 當時的**純 text 兜底路徑沒有 unwrap 這個外殼**，於是整段 `{"text": "…(未閉合)` 被當成 assistant 內容原樣持久化進 `bryan_agent_mahiru_private.json`。
5. 下一輪歷史中出現該樣板 ⇒ 模型模仿 JSON 殼 ⇒ 再次退化重複 ⇒ **自我強化的路徑依賴**（原假設成立，機制已證實）。

### 7.3 ⚠️ 這個 bug 已經被修過兩次——都不是本專案修的

| 日期 | 修補 | 內容 |
|---|---|---|
| 2026-08-04 | 修法 10（Bry 拍板） | `proxy.py:1870-1885`：`finish_reason == "length"` ⇒ `_safe_truncate_on_length()` 強制截斷至 200 字元。**註解明文點名 mahiru 為已知案例**（id=20661/20687） |
| 2026-09-05 | PERSONA-FIX-1 D1 | `proxy.py:3168-3206` `_unwrap_text_json_shell()`：剝除 `{"text": "` 殼。**其註解引用的 log 證據正是同一類事故**（`server_20260730_205447.err` L9374-9399、`server_20260801_205401.err` L4701） |

**汙染檔最後寫入時間：2026-09-03 14:10:54 —— 比 D1 修復早 2 天。**

### 7.4 全庫掃描：孤例，且已停止活動

掃描 `data/conversations/` 全部 **153 個 .json**，條件為「含 `{"text": "` 殼」或「assistant 訊息 >1,000 字」：

```
 shell   big    worst  last_write         turns  file
     3     3    6,938  2026-09-03 14:10        17  bryan_agent_mahiru_private.json

命中檔案數: 1 / 掃描總檔案數: 153
```

- **僅 1 / 153 命中**，其餘 10 個 agent 的私聊歷史皆正常（700–1,050 tokens）。
- **已 38 天未寫入**（至 2026-10-12）。自愈迴圈未在運行。

### 7.5 結論：真正的殘留問題在「讀取端」，不在「寫入端」

- 寫入端：**已修復**（修法 10 + D1）。再加一道 Size Gatekeeper 等於對已修好的 bug 補第三道牆，且可能掩蓋未來真正的訊號。
- 讀取端：**無防護**。該檔 17 turns < `MAX_PRIVATE = 20`（`proxy.py:75`），所以這 3 條 blob **至今仍會在 mahiru 每一輪私聊被原樣重餵**，持續消耗約 15,848 tokens。
- 這才是目前**仍然成立且成本極低**的處理點：在讀取側過濾 legacy 污染（可沿用既有 `strip_assistant_parens`（REM-TEXT-1, 2026-09-11, `proxy.py:1345`）的 read-side 保護先例，**只影響 prompt，不寫回 `data/`**）。

---

## 9. REM-LEGACY-CLEANSE 實作與驗收（Bry 放行 2026-10-12）

Bry 裁示：不加寫入端防線（已由 修法 10 + D1 雙重修補）、不做狀態權威注入、服從但留痕封存；**放行讀取端 legacy 污染過濾**。

### 9.1 改動範圍

| 檔案 | 改動 |
|---|---|
| `src/text_guard.py` | 新增純函式 `sanitize_legacy_runaway_turn()`（無 IO、無狀態）＋兩個門檻常數 |
| `src/llm/proxy.py:56` | import 新函式 |
| `src/llm/proxy.py:1520-1537` | `_build_messages_private` 歷史迴圈接入無條件淨化 ＋ `logger.warning` |
| `tests/test_legacy_runaway_cleanse.py` | 新增 12 項回歸測試 |
| `scripts/verify_legacy_cleanse.py` | A/B 驗收腳本（monkeypatch 停用/啟用淨化器） |
| `scripts/measure_prompt_baseline.py` | 抽樣清單加入 `agent_mahiru`（原未列入，無驗收對象） |

### 9.2 設計要點

- **觸發門檻 >1,000 字元**（採 Bry 工單原訂值）。依據：正常 assistant 訊息 max ≤ 97 字元（修法 10 於 2026-08-04 量得 p99=94、0 條 >200），汙染為 6,690–6,938。1,000 落在極寬空白帶。
- **截斷目標 200 字元**（對齊修法 10 `_safe_truncate_on_length` 規格）。取尾 200 字、找最後一個完整標點斷點、不硬切字中間——完全複製修法 10 語意，不另創標準。
- **無 config gate**（與 `strip_assistant_parens` 不同）。後者是 per-agent 風格開關（`text_channel_stage_guard`，預設 False，只對 agent_rem 開）；本項是安全淨化，若設 gate 會**靜默失效**而無人察覺。
- **只影響 prompt，嚴禁回寫 `data/conversations/`**——與 REM-TEXT-1 同一邊界。
- 合法完整的 JSON 殼（`{"text": "hi"}`）**不動**，那是 PERSONA-FIX-1 D1 `_unwrap_text_json_shell` 的職責，兩者不重疊。

### 9.3 驗收數據（A/B 實測，非推估）

`scripts/verify_legacy_cleanse.py`，同一條 prompt 組裝路徑，唯一差異為淨化器停用/啟用：

```
               total   persona  payload_excl_persona   msgs
BEFORE        23,135     6,723                16,412     18
AFTER          8,463     6,723                 1,740     18

回收 token / 輪 : 14,672
降幅             : 63.4%
```

runtime log 確認恰好三筆、且只命中那三條：

```
[REM-LEGACY-CLEANSE] Sanitized legacy runaway turn (agent=agent_mahiru, 6690 -> 199 chars)
[REM-LEGACY-CLEANSE] Sanitized legacy runaway turn (agent=agent_mahiru, 6938 -> 200 chars)
[REM-LEGACY-CLEANSE] Sanitized legacy runaway turn (agent=agent_mahiru, 6722 -> 199 chars)
```

### 9.4 對 Bry 驗收標準的逐條對照

| Bry 驗收標準 | 實測 | 判定 |
|---|---|---|
| 私聊歷史 token 由 15,848 降至 800–1,000 | **911**（ruka 1,040 / akane 844 / yua 848 之中位數帶） | ✅ 達標 |
| 每輪回收約 1.5 萬 token | **14,672 tokens / 輪，降幅 63.4%** | ✅ 達標 |
| 不回寫 `data/**` | 全程唯讀，`_append_private_history` 未觸碰 | ✅ 達標 |
| 對齊 Fix 10 規格 | 200 字元上限、尾段取用、標點斷點 | ✅ 完全一致 |
| 不影響正常訊息 | 其餘 9 個 agent 歷史 0 命中；12 項單元測試含零行為變化斷言 | ✅ 達標 |

⚠️ **口徑澄清**：Bry 提到的「800–1,000 tokens」是**私聊歷史檔本身**的中位數。組裝後的 `payload_excl_persona` 為 1,740，差額 829 = 8 個投影區塊 753 + 其他 system 內容 76。該投影層每個 agent 都要背，屬既有設計，非本次引入。

### 9.5 殘餘風險（主動揭露）

1. **跨 agent 適用性**：本次掃描僅覆蓋 `data/conversations/` 頂層。若其他目錄（如 `conversations_backup_old/`、群聊備份）存在同型汙染，該路徑不受本次保護。
2. **群聊路徑未納入**：依 Bry 指示只改 `_build_messages_private`。若 group 歷史日後出現同型汙染，需另開工單。
3. **閾值為靜態常數**：若未來 persona 或正常回覆長度普遍上升，1,000 需重估。可複用修法 10 的量測手法（`check_response_length_dist.py`）重跑。
4. **本機 `.venv` 無 tiktoken**：token 數為 CJK 口徑估算，與 §1.1 聲明一致。

---

## 10. 回歸驗證（2026-10-12）

### 10.1 全庫回歸結果

`.venv\Scripts\python.exe -m pytest -q tests`（ Bry 要求必須指名 repo 內 `.venv` 直譯器）：

```
105 failed, 6231 passed, 20 skipped, 1 warning, 15 errors  — 678.48s
```

### 10.2 這些失敗與本次改動無關（已用 A/B 實測定案，非推論）

Bry 紀律「自報不算證據」：執行者宣稱「0 失敗」必須以客觀痕跡獨立複核。因此做**可逆還原對照**——把 `src/llm/proxy.py` 與 `src/text_guard.py` 還原到 HEAD，用**完全相同**的 pytest 參數再跑一次，比對結果。

| 執行 | 指令參數 | 結果 |
|---|---|---|
| HEAD 基線（移除本次改動） | `-q --tb=no --ignore=tests/test_legacy_runaway_cleanse.py -k "text_guard or proxy or prompt or private or rem_text or parens"` | `15 failed, 189 passed, 4 skipped, 6150 deselected, 1 error` |
| 本次改動（同一參數） | 同上 | `15 failed, 189 passed, 4 skipped, 6150 deselected, 1 error` |

**逐字相同。本次改動新增失敗數 = 0。**

失敗成因與本次改動無關，可分三類：

1. **環境缺套件**：`test_proxy_stub_speak.py` 等回報 `async def functions are not natively supported. You need to install ... pytest-asyncio`——`.venv` 未安裝 `pytest-asyncio`。
2. **測試檔自身缺 fixture**：`test_event_bus.py` 回報 `fixture 'bus' not found`（測試宣告 `async def test_x(bus: SoulEventBus)` 但檔內無對應 `@pytest.fixture`）。連帶 `test_event_generation_v1/v2`、`test_self_check_v1/v2`、`test_timezone_fix_v1/v2`、`test_memory_middleware` 等 15 個 ERROR。
3. **baseline 斷言與現況不符（歷史累積）**：`test_stale_filter_v1.py::test_c_baseline_proxy_has_no_stale_threshold_constant` 斷言 proxy **不應**含 `STALE_THRESHOLD_SEC` 但它存在（修法 7 已於 2026-08-04 加入）；`test_cross_session_v1` / `test_proactive_*` / `test_m2_0_*` / `test_finish_length_truncate_v1::test_b_baseline_proxy_source_no_method_10` 等屬同型「baseline 測試與已實作的修法不一致」。這些是 Bry 2026-08 那一批修法加入後未同步更新的存量測試。

⚠️ 這批存量失敗**應另開工單清理**（尤其 `pytest-asyncio` 缺套件會擋掉所有 async 測試），但不屬 SOUL-MEM-001 範圍。

### 10.3 🔴 過程事故主動揭露：備份失敗導致改動一度遺失

為做 §10.2 的 A/B 還原對照，我寫了一支 PowerShell 腳本備份兩個檔案後 `git checkout` 還原。**`text_guard.py` 備份成功，但 `proxy.py` 備份未寫成**（`Copy-Item` 未報錯但目標檔不存在，屬無聲失敗）。該腳本的 `finally` 還原步驟因此拋錯，結果是：

- `src/text_guard.py`：從 TEMP 備份完整還原 ✅
- `src/llm/proxy.py`：**停留在 HEAD 狀態，本次改動一度全部遺失** ❌

**處置**：立即依原編輯內容重建 `proxy.py` 的兩處改動（import 行 + `_build_messages_private` 歷史迴圈接入），並以三重驗證確認重建無誤：
- `git diff --stat` = `proxy.py 17 insertions` / `text_guard.py 77 insertions`，與原始改動一致。
- `scripts/verify_legacy_cleanse.py` 重跑，數字**完全相同**（BEFORE 23,135 / AFTER 8,463 / 回收 14,672 / 63.4%）。
- `tests/test_legacy_runaway_cleanse.py` 12 項全過。

**教訓（後續必遵守）**：`git checkout -- <file>` 是**破壞性**操作，會靜默丟棄工作樹改動。任何「暫時還原以做 A/B」的腳本都必須：
1. 備份後**逐一比對 byte 大小**，不一致就 `throw` 中止，絕不碰工作樹；
2. 用**全新且已驗證存在**的目錄做備份目標（本次 `New-Item -Force` 後未確認 `Copy-Item` 是否真的落地）；
3. `finally` 的還原路徑必須先確認備份檔存在，否則還原本身會失敗並讓狀況更糟。

第二次 A/B（`so_mem_ab2.ps1`）已加入上述三項防護並成功執行（備份 251,462 / 7,727 bytes 雙方一致才放行，finally 還原後比對 `RESTORE OK : True`）。

---

## 11. 本報告未驗證的事項

- 所有 token 數皆為**估算**（無 tiktoken）。若需精確值，需安裝對應模型的 tokenizer（需 Owner 核准）。
- 只量了 3 個 agent（yua / ruka / aoi）的完整組裝；其餘 7 份 persona 只量了檔案大小。
- 投影層只量了 4 個 agent。
- history 只盤點 `data/conversations/` 頂層，未遞迴 `conversations_backup_old/`。
- **未驗證**：這三條路徑在真實生產流量下的實際 token 用量（provider 端 `usage.prompt_tokens` 未取得，因不得發真實請求）。
- **§7 根因已完全確定**，唯一未驗證的是「當時 `finish_reason` 實際回傳值」——`proxy.py:1845` 有記錄該值，但對應時段的 server log（2026-09-03）已不在 `data/logs/`（現存最早為 2026-08-14），故以截斷特徵反推。