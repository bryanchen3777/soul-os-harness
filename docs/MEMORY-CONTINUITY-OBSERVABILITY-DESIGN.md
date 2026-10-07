# Memory Continuity Observability — 設計（Design Only）

**狀態**：DRAFT，待 Owner / 主大腦審核
**日期**：2026-10-07
**前置**：SOUL-REM-01 ✅ FROZEN · Memory Baseline ✅ · Shadow Log Investigation ✅
**本文不執行、不改任何 runtime。**

---

## 0. 本設計要回答的問題

不是「有沒有 memory？」—— 那可以用 count 回答。

而是：

> **某一筆具名記憶 M 被檢出、被實際注入 prompt 之後，這一回合的 response，
> 以及後續的 session，是否能觀察到與 M 的可判定關聯？**

這件事今天做不到，原因是**沒有一條完整 lineage**，而不是某一環壞掉。

---

## 1. 先講清楚：現況實際記錄了什麼變數

> 這節的存在是為了不重蹈 2026-10-07 早上的錯（看欄位名稱就推定能力）。
> 下面每個 ✅ 都指「程式碼裡真的有這個變數」，不是「文件說有」。

### 1.1 Retrieval 層 — 幾乎齊備 ✅

寫入點：`src/memory/middleware.py:200-227` `_append_loader_trace()`

| Bry 要求的欄位 | 實際變數 | 狀態 |
|---|---|---|
| `memory_id` | `candidates[].memory_id` | ✅ |
| retrieval query / trigger | `query` / `query_tags` | ✅ |
| confidence | `candidates[].confidence` + `confidence_threshold` | ✅ |
| accepted / rejected | `candidates[].status`（`selected` / `rejected_low_confidence`） | ✅ |
| rejection reason | `status` + `fail_safe_triggered` | ✅ |
| **join key** | `event_id` / `session_id` / `response_id` | ✅ **已經存在** |

**這一層不需要新增任何東西。** 而且 `event_id` 已經能 join 到 turn ——
這是整條 lineage 唯一不需要動的部分。

### 1.2 Injection 層 — 缺口，且比想像中精確 ⚠️

注入點：`src/memory/middleware.py:340-352`

```python
eligible = load_result["eligible_memories"]     # ← 這些物件帶 memory_id
if eligible:
    loader_block = format_for_prompt(eligible)  # ← 渲染成字串
    context = (context + "\n\n" + loader_block).strip() if context else loader_block
    logger.info(f"... eligible={len(eligible)} | context_len={len(context)}")
```

現況只記 `context_len_after_loader` — **一個長度數字**。

**關鍵事實**：`format_for_prompt()` 渲染出的格式是

```
[Recall relevant memories]
- (preference_plan_event_fact, conf 0.80, tags=雷姆,鬼族): <content>
[/Recall]
```

**它不含 memory_id。** 所以 injection 記錄**不可能事後從 prompt 文字還原**。
必須在注入當下、id 還在作用域內的時候快照。這決定了設計的形狀。

### 1.3 SAGE prefetch 路徑 — 完全没有觀測 ❌

`middleware.py:307` 在 loader 之前就先跑：

```python
context = await asyncio.to_thread(provider.prefetch, query, session_id=..., ...)
```

`SAGEProvider.prefetch()` 的回傳型別是 **`str`**（token-bounded 字串）。
它沒有 trace、沒有 id，而且 `_append_loader_trace` 只在 loader 分支裡呼叫。

**後果**：SAGE（elevation graph → pattern/belief/value/trait 節點，那 4,402 筆）
注入的內容**完全無法回溯**。這是目前最嚴重的一個洞，而且它跟 loader 是兩條不同的路。

### 1.4 Turn 層 — 綁定存在 ✅

`event_id` / `session_id` 兩側都有。`AGENT_SPEAK` payload 有 `text`。

### 1.5 Downstream 層 — 不存在 ❌

沒有任何 instrument 記錄「這回合的輸出與被注入記憶 M 的關聯」。

---

## 2. 設計原則（先把紅線釘死）

### P1 — 能力主張必須由可辨識變數支持

> 任何 capability claim 都必須由 instrumentation **所實際可辨識的變數**支持。
> 欄位名稱、schema 名稱、文件描述**本身不能當成測量證據**。

出處：2026-10-07 `shadow_log.context_provided` 被誤讀為「記憶影響了行為」，
實際它只是「呼叫當下有沒有 context 字串」。Bry 已將此原則凍結。

### P2 — 四段必須分離，不可互相代替

```
retrieval  ≠  injection  ≠  use  ≠  influence
```

| 段 | 定義 | 現況可得？ |
|---|---|---|
| **retrieval** | M 進入候選並通過 gate | ✅ loader_trace |
| **injection** | M 的內容真的進入本回合 prompt | ❌ 只知長度不知內容 |
| **use** | 本回合 output 實際用到 M 的內容 | ❌ |
| **influence** | 因為 M，output 或後續 session 產生可判定差異 | ❌ |

**任何一段都不能替代下一段當證據。** 特別是：
`eligible_count > 0`、`context_len > 0`、retrieval hit rate
—— 這些**全部只能證明 retrieval 發生過**。

### P3 — 模型自稱記得不是證據

```
「我記得主人喜歡咖啡。」   ← ❌ 不是 continuity 證據
```

合格的 continuity 證據必須可追溯：

```
memory M-123
  ↓ 本回合被 retrieved（loader_trace 有 record）
  ↓ 實際 injection 包含 M-123（injection snapshot 有 record）
  ↓ 本回合 response / interpretation
  ↓ 後續 behavior 出現可判定關聯（且判定規則事先寫死）
```

### P4 — 不得為觀測增加每回合 LLM 成本

`shadow` observer 是因為每則回覆多跑 13 次串行 judge 而被 Bry 關掉的
（commit `94e3c6b`）。**新的 continuity 儀器不得重蹈同一個經濟學錯誤。**
推論：**influence 不可在線量測，只能離線重播。**

### P5 — 沒有證據不等於沒有發生

延用 2026-10-07 已驗證的語意不對稱：缺資料 = 沒有判定依據，
不是「沒有影響」。缺資料的格子必須標為 gap，不能判成 negative。

---

## 3. 設計：lineage 四層

```
┌─ Memory Store ─────────────────────────────────────┐
│  source of truth：memory_id + content + confidence │
└───────────────┬───────────────────────────────────┘
                │ candidate retrieval
                ▼
┌─ L1 RETRIEVAL ────────────────────────────────────┐
│  loader_trace.jsonl                               │
│  ✅ 已有，不需新增                                  │
│  event_id / session_id / response_id  ← join key   │
│  memory_id / query / query_tags / confidence /     │
│  confidence_threshold / status / fail_safe         │
└───────────────┬───────────────────────────────────┘
                │ status == "selected"
                ▼
┌─ L2 INJECTION SNAPSHOT ── ★核心缺口★ ─────────────┐
│  【新增】每回合一份，記錄「實際進了 prompt 的東西」   │
│  · injection_id                                   │
│  · event_id / session_id（join 到 L1）             │
│  · source: "loader" | "sage_prefetch" | "both"     │
│  · items[]:                                       │
│      - memory_id / node_id（哪一筆）               │
│      - rendered_text（渲染後實際字串）              │
│      - position（注入區塊位置）                     │
│  · block_hash（整段 injection 的雜湊）               │
│  · snapshot_ref（若快照本體另存）                   │
└───────────────┬───────────────────────────────────┘
                │ 進入 _build_messages_* 的 system message
                ▼
┌─ L3 TURN ─────────────────────────────────────────┐
│  ✅ 已有：AGENT_SPEAK payload.text                 │
│  ✅ 已有：event_id / session_id / correlation_id   │
│  【建議】prompt 快照或可重建的 hash                │
└───────────────┬───────────────────────────────────┘
                │
                ▼
┌─ L4 DOWNSTREAM EVIDENCE ── ★方法論缺口★ ─────────┐
│  【新增，離線】                                     │
│  · use（同一回合）：output 是否含 M 的區別性內容      │
│  · influence（跨回合／跨 session）：                │
│      只能靠 counterfactual replay 判定              │
│      —— 有 M / 沒 M 各跑一次，比對輸出差異          │
└───────────────────────────────────────────────────┘
```

---

## 4. L2 Injection Snapshot 的具體設計

### 4.1 為什麼必須在注入當下快照

因為 `format_for_prompt()` 不含 `memory_id`。事後從 prompt 文字還原是不可行的。
而 `eligible` 物件在 `middleware.py:341` 就已經帶著 id —— 這是一行之差的資訊。

### 4.2 記錄格式（提議）

```json
{
  "injection_id": "<uuid>",
  "ts": "2026-10-07T...Z",
  "event_id": "<與 loader_trace 相同>",
  "session_id": "...",
  "agent_id": "agent_rem",
  "source": "loader",
  "items": [
    {
      "memory_id": "b4491fd5-...",
      "node_id": null,
      "origin": "memory_store",
      "category": "preference_plan_event_fact",
      "confidence": 0.8,
      "rendered_text": "(preference_plan_event_fact, conf 0.80, tags=雷姆,鬼族): <原文>",
      "position": 0
    }
  ],
  "block_hash": "sha256:...",
  "context_len_after": 412
}
```

- **append-only JSONL**，與 `loader_trace` / `world/perception_trace` 同一 pattern
  （repo 既有慣例，`src/world/trace.py` 明文對齊 loader_trace）。
- 寫入失敗只 log warning，不 raise —— 跟既有 sidecar 一致。
- 位置：`data/memory/injection_trace.jsonl`。

### 4.3 紅線：紅acted 版本

`rendered_text` 會包含記憶原文，可能有敏感內容。三個選項：

| 選項 | 說明 |
|---|---|
| A. 存全文 | 分析最完整，但磁碟上複製一份記憶內容 |
| B. 只存 id + hash | 最安全，但無法做 use 判定（§5.1 需要文字） |
| C. 存全文 + TTL／保留期 | 折衷 |

**需要 Bry 決定。** 我傾向 C，因為 use 判定需要文字，但保留期能控制暴露面。

---

## 5. L4 Downstream Evidence 的方法論

### 5.1 use（同一回合）：可用確定性訊號，零 LLM 成本

不要用 LLM judge。可用**區別性內容比對**：

> 某段文字只有在記憶 M 中出現、且不在本回合其他上下文中出現。

實作上是 n-gram / 內容雜湊比對。成本為 0，符合 P4。

⚠️ 已知弱點：會有 false positive（模型可能用其他來源說出相同的話）。
因此 **use 的判定必須標記為 heuristic，不能當成唯一證據。**

### 5.2 influence（跨回合／跨 session）：只能靠 counterfactual replay

在線量測 influence 需要「同一個 prompt 有／沒注入 M 的對照」，
這在線上做不到（那意味著每回合多跑一次模型）。
所以：

```
離線選樣（例如 200 個有注入的回合）
  ↓
用保存的 turn 快照 + injection snapshot 重播兩次
  ├─ replay WITH M
  └─ replay WITHOUT M
  ↓
比對輸出差異
  ↓
差異可判定 ⇒ M 對該回合有 influence
```

這與 SOUL-REM-01 已建立的 `--rescore` 同一思路：**評估與執行解耦**。

**成本控制**：抽樣重播，不是全量。抽樣策略與數量需要 Bry 拍板。

### 5.3 必須事先寫死的東西

在看到結果之前先寫定，否則會變成事後找藉口：

| 項目 | 為什麼要事先定 |
|---|---|
| influence 的判定門檻（輸出差異多大算有影響） | 否則可以挑數字 |
| use 的 n-gram 參數 | 否則可以調到想要的結果 |
| 抽樣方法（隨機／分層） | 否則會挑不利的回合 |
| 什麼算「不可判定」 | 對應 P5，缺證據 ≠ 沒影響 |

---

## 6. 需要碰的 frozen contract 表面

| # | 表面 | 性質 | 是否需要 Owner 授權 |
|---|---|---|---|
| **S1** | `src/memory/middleware.py:340-352` 加注入快照 sidecar | **觀測附加**，不改 retrieval／injection 行為。既有先例：同檔案的 `_append_loader_trace` 就是同型 sidecar | 🟡 **需確認** —— 檔案在 `src/memory/**` 內，但不在列舉的 frozen items（SAGE 寫入邏輯 / InnerLifeEvent / Agency / TriggerEnvelope / 4 handlers） |
| **S2** | SAGE `prefetch()` 回傳結構化結果（ids + rendered text），取代純字串 | **SAGE 讀側簽章變更** | 🔴 **是 —— 這是真的 frozen 邊界** |
| **S3** | Turn 層的 prompt 快照／hash | 可能觸及 `src/llm/proxy.py` | 🟡 需確認 |
| **S4** | 離線 replay harness | 新檔，不碰 runtime | ✅ 否 |

**關鍵取捨**：

- 只做 **S1 + S4** ⇒ 可以在**完全不碰 frozen contract** 的情況下，
  拿到 loader 路徑的完整 continuity lineage（retrieval → injection → turn → use → influence）。
  覆蓋面限於 loader path。
- 加上 **S2** ⇒ 連 SAGE prefetch 路徑也覆蓋到（SAGE 是 lived experience／elevation 的主要來源）。

**這是 Bry 要決定的第一件事**：先做 A 方案（不碰 frozen，得到 60% 覆蓋），
還是直接要 100% 覆蓋（必須碰 S2）。

---

## 7. 明確不做的事

- 不重新啟用 `shadow_log`（commit `94e3c6b` 的 Owner 決策維持）。
- 不修 `shadow.py` 的 7 天到期缺陷與不存在的 `SHADOW_MODE_ENABLED` 開關。
  可另列 instrumentation hygiene，但對本設計沒有直接價值。
- 不在線上做 influence 量測（違反 P4）。
- 不設計任何 downsampling regression criterion（延續 Bry 的順序鎖）。

---

## 8. 驗收標準（設計本身，不是實作）

這份設計在實作前應該能回答：

1. 每個 continuity 主張，對應到哪一個**實際變數**？（P1）
2. 四段分離是否在設計中真的分開了？哪一段仍只有 proxy？（P2）
3. 如果模型說「我記得你喜歡咖啡」，這句話在本設計裡會被歸到哪一段？（P3）
4. 整條鏈有沒有增加任何每回合的 LLM 呼叫？（P4）
5. 哪些格子在資料不足時會被標成 gap 而不是 negative？（P5）
6. 要拿到 100% 覆蓋必須碰什麼？拿到 60% 可以不碰什麼？（§6）

---

## 9. 待 Bry 決定

| # | 決定點 | 選項 |
|---|---|---|
| D1 | 覆蓋範圍 | A. 只做 loader path（不碰 frozen） ／ B. 含 SAGE path（必須碰 S2） |
| D2 | injection snapshot 是否存全文 | A. 全文 ／ B. 只 id+hash ／ C. 全文+保留期 |
| D3 | influence 抽樣數量與策略 | 需要拍板 |
| D4 | S1 是否算 frozen | 需確認 `src/memory/middleware.py` 的 sidecar 附加是否在授權邊界內 |