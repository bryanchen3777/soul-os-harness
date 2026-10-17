# OBS-CHRONO-1 — 端到端 Prompt / Chrono 觀測站

Status: **CLOSED（待 Owner 拍板後重啟主服務）**
Owner: Bryan ｜ Date: 2026-10-16 ｜ Branch: `main`
前置：`7dda491`（去錨定三修法）、`SOUL-BUG-004-SINK`（chrono 匯端接通）、`SOUL-BUG-001`

---

## 0. 起點：唯讀考古推翻了工單的目標 ①

工單原本要驗「`temperature` 究竟是扮演路由的 **0.85** 還是工具呼叫的 **0.5**」。
實測結果是：**那個分支不存在。**

| 主張 | 實測 |
|---|---|
| 有兩條溫度路徑 | 全檔**無任何 `if` 依 temperature 分流** |
| 工具呼叫路徑用 0.5 | 工具強制路徑 **2026-08-05 隨 Lesson 32 移除** |
| `self.temperature` 是 0.5 那條路徑的來源 | `proxy.py:3441` 賦值後**從未被讀取**（死屬性） |

⇒ `configs/default.yaml` 與 `proxy.py:3463`／`:4262` 的相關註解**已過期**。
角色生成無條件走 `self.roleplay_temperature`（`proxy.py:4263`）。

**目標①因此收斂為「驗證每一次呼叫的 Route ＋ Temperature 歸因」**（Owner 拍板）。

---

## 1. 考古：三個目標在生產上是什麼狀態

唯讀掃描 `data/logs/server_nohup*`（10 個檔）：

| 目標 | 狀態 | 證據 |
|---|---|---|
| ① temperature 數值 | ✅ 已可觀測 | 696 行 `[C1]`，分佈 `0.0:172 / 0.8:142 / 0.7:84 / 0.5:44 / 0.85:18 / 0.3:4` |
| ① route 歸因 | ❌ 缺 | `[C1]` 不記 caller，0.5 與 0.85 混在一起無從歸因 |
| ② CHRONO 是否進 prompt | ❌ **全盲** | `CHRONO_SOCIAL_CONTEXT` 命中 **0** |
| ③ 內在生活 / SAGE | ❌ **全盲** | `最近內在生活`、`你記得以下這些事情` 各命中 **0** |

`0.85` 在 10-14（6 次）與 10-16（12 次）出現於生產日誌，證明 `roleplay_temperature`
確有送達 provider；但 10-16 那 12 次都在 18:52 重啟之前，重啟後至今尚無角色扮演呼叫。

### ⚠️ 子代理報告的更正（自我修正）

第一階段考古曾報「`src/observability/console.py` 已把完整 system prompt 送到瀏覽器，
只需加欄位」。**該路徑不存在**——`src/observability/` 在本票之前根本不存在，
`memory_trace` 這個 key 在 `src/` 全庫命中 **0**。基於該錯誤前提提出的「console 落點」
選項不成立。Owner 拍板選 proxy.py，反而是唯一可行且正確的選項。

### 真實的 header 位置（勿再臆測）

| 標頭 | 位置 |
|---|---|
| `[CHRONO_SOCIAL_CONTEXT v2.2]` / `[/CHRONO_SOCIAL_CONTEXT]` | `src/temporal/render.py:18` / `:28` |
| `[最近內在生活]` | `src/llm/proxy.py:802`（private builder）、`:1444`（group builder） |
| `你記得以下這些事情:` | `src/llm/proxy.py:761`（private）、`:1403`（group） |

---

## 2. 實作

### 2.1 `src/observability/prompt_trace.py`（新增）

- 開關 `SOULOS_PROMPT_TRACE`（真值集合 `1/true/yes/on`），**預設關閉**。
- 關閉時熱路徑成本＝一個 `if` 判斷＋零 IO（連檔案都不建立）。
- **永遠不外洩對話內容**：只寫 presence／len／12 字元 sha256 前綴／時間中繼資料。
- 任何例外都不得上拋，失敗只 `logger.warning`（觀測不得拖垮主流程）。
- schema：`prompt_trace_v1`，每列帶 `schema_version`。
- 檔案：`data_root()/observability/prompt_trace.jsonl`，超過 5 MB 輪替為 `.1`。
- 凍結禁令：sidecar **不得** import 任何 LLM client（有守衛測試用 AST 掃描釘死）。

### 2.2 `src/llm/proxy.py`（兩個插針）

1. `_handle_event_impl`：**所有後處理完成、`_complete_with_retry` 送出之前**，
   記 `route=f"roleplay:{reason}"`、`temperature=self.roleplay_temperature`。
2. `generate_event_text`：retry 迴圈之前，
   記 `route="event"`、`temperature=self.event_temperature`（0.7）。

**為什麼需要兩個**：事件生成是另一條路徑（`event_temperature` 0.7），
不在 `_handle_event_impl` 的觀測點內。只有一個插針時 0.7 這條永遠缺席，
「歸因」就不完整。

### 2.3 `configs/default.yaml`

`temperature: 0.5` 保留（相容舊設定載入）但加註**明確不可達**的來由，
並指向 `roleplay_temperature` 為實際調校入口。

---

## 3. 觀測記錄長什麼樣

```json
{
  "schema_version": "prompt_trace_v1",
  "ts": 1792...,
  "agent_id": "agent_akane",
  "route": "roleplay:heartbeat",
  "mode": "group",
  "reason": "heartbeat",
  "temperature": 0.85,
  "model": "deepseek-v4.1-flash",
  "provider": "ollama",
  "n_messages": 14,
  "system_prompt_len": 30389,
  "system_prompt_sha256_12": "ab12cd34ef56",
  "user_prompt_len": 42,
  "user_prompt_sha256_12": "…",
  "blocks": {
    "chrono":      {"present": true, "len": 412, "sha256_12": "…",
                    "time_period": "afternoon", "silence_hours": 3.2},
    "inner_life":  {"present": true, "len": 880, "sha256_12": "…"},
    "sage_memory": {"present": true, "len": 320, "sha256_12": "…"}
  }
}
```

**沒有** `system_prompt` / `user_prompt` / `messages` 這類原文欄位，
`sentinel` 測試會掃描整個檔案確認。

---

## 4. 測試

`tests/observability/test_prompt_trace.py`（19 項）：

- `TestKillSwitch` — 預設關閉、真值集合、**關閉時連檔案都不建立**（零 IO 實際保證）
- `TestNoSensitiveLeak` — 🔴 哨兵字串三種注入位置都不得落盤；列欄位白名單；
  hash 可比對但不可還原
- `TestObservabilityGoals` — route＋temperature 歸因、chrono presence 與 metadata、
  **缺席必須誠實報 false**、inner_life／sage 偵測
- `TestSchemaAndRobustness` — schema_version、append 語意、寫入失敗不得上拋、空 messages
- `TestFrozenProhibition` — AST 掃描，sidecar 不得 import LLM client
- `TestProxyHookAttribution` — 🔴 **插針層守衛**：用 spy backend 實際驅動
  `_handle_event_impl` 與 `generate_event_text`，斷言 trace 記的溫度
  **等於實際送給 backend 的溫度**；關閉時 backend 仍被呼叫（觀測不影響功能）

### 🔴 變異實驗（8 組，全紅）

| 變異 | 結果 |
|---|---|
| MUT-A 關閉時仍寫檔（kill switch 失效） | RED 1 |
| MUT-B 哨兵防護失效（把原文寫進列） | RED 2 |
| MUT-C chrono presence 恆 True | RED 1 |
| MUT-D inner_life 偵測移除 | RED 1 |
| MUT-E sage 偵測移除 | RED 1 |
| MUT-F 觀測失敗上拋（拖垮主流程） | RED 1 |
| MUT-G roleplay 插針不帶 temperature | RED 1 |
| MUT-H event 插針溫度改 0.0 | RED 1 |

**MUT-G／H 首輪是 GREEN 的**——只測 `trace_prompt()` 本身時，把 proxy 插針的
temperature 改掉整套測試仍全綠。補上 `TestProxyHookAttribution` 後才轉紅。
這是本票最值得記錄的一點：**歸因數值若不由「真的從那一點觸發」的測試守住，等於沒有守衛。**

---

## 5. 待 Owner 裁決

1. **重啟主服務（`:8000`）**讓觀測點生效。注意 `SOULOS_PROMPT_TRACE` 需設為環境變數
   才會啟用；預設關閉時新程式碼只是多一個 `if`。
2. 其他 subsystem 的 LLM 呼叫（SAGE judge、memory writer、diary/dream）**不在本票範圍**：
   它們不走 `LLMProxy` 的這兩條路。若要全覆蓋需要各自的插針，屬獨立工單。
