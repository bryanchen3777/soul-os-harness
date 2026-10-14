# SOUL-BUG-004-SINK — chrono 時間感知：僅修匯端（不碰 SYSTEM_TICK 訂閱架構）

**母工單**：[SOUL-BUG-004](SOUL-BUG-004-WORK-ORDER.md)（P0）
**本工單**：**只做匯端（sink）**，Bry 2026-10-13 裁示：「先不動它，待 005 修完後，在專屬工單中僅處理匯端，**絕不貿然碰 SYSTEM_TICK 的訂閱架構**。」
**狀態**：DRAFT，**待 Bry 核准後才開工**
**前置條件**：SOUL-BUG-005 已修（`277a8d9`）✅

---

## 一、為什麼只修匯端

SOUL-BUG-004 的源端（`consciousness.py:155` SYSTEM_TICK 被 M5.7-2 註解）**涉及 M5.7-2 的原始決策**——
當初註解是為了避免 proactive Agency 重複觸發。動它等於回退一項已生效的架構決策，**不屬技術債清理範圍**。

而匯端（`proxy.py` 的 prompt 組裝）**沒有這種相依**：
`PromptContext` 早已是 0 實例化的死碼（`docs/EH-1:357`、`docs/EH-4:1040` 都記載「另開工單」，那張單從未開出），
所以「把 chrono 插槽接回 live 的 prompt 組裝器」是**純增益、零回退風險**的動作。

## 二、目標（一句話）

讓 `AGENT_INTENT.payload["chrono_context"]` 若有值，**真的出現在 LLM 的 system prompt 裡**。

## 三、範圍（動哪些檔案）

| 檔案 | 改動 |
|---|---|
| `src/llm/proxy.py` | 在 live 的 `_build_messages_group` / `_build_messages_private` 加 `chrono_context` 參數與注入 |
| `src/agent/consciousness.py` | **不動 `:155`**；僅在中段補上 `chrono_block` 的傳遞（見 §四-2） |
| `tests/` | 新增回歸測試＋把 `tests/test_soul_bug_watchdogs.py::test_chrono_context_must_reach_prompt` 的 `xfail` 移除 |

**禁止**：動 `consciousness.py:155`、恢復 `SYSTEM_TICK` 訂閱、動 `_on_tick` 的死碼（那屬 SOUL-BUG-003 的裁示範圍）。

## 四、做法（決策已定，執行者照做）

### 1. 匯端：在 live builder 加插槽

`_build_messages_group(...)` / `_build_messages_private(...)` 現有簽章**沒有** `chrono_context` 參數
（這正是看門狗第一道斷言失敗的原因）。加一個 keyword 參數（預設 `""`，保持所有既有呼叫端不動），
並在組裝 `system_parts` 的位置（參照死碼 `proxy.py:1596-1598` 的形狀）加入：

```python
if chrono_context.strip():
    system_parts.append(f"\n{chrono_context.strip()}")
```

⚠️ **注入位置必須在 `memory_context` 之後、歷史訊息之前**，與死碼 `:1596` 的相對順序一致，
避免改變既有 system prompt 的前綴語意。

### 2. 中段：讓 chrono_context 有機會非空

`_fire_intent`（`consciousness.py:446-447`）已有
`intent_payload["chrono_context"] = chrono_payload.get("chrono_block", "")`。
問題是**四個活著的呼叫點都不帶 `chrono_block`**（只有已死的 `_on_tick` 會帶整包）。

**決策**：在 `_fire_intent` 內、`chrono_payload` 為真但**缺** `chrono_block` 時，
**由 `src/temporal/` 現算一次**並填入，而不是恢復 SYSTEM_TICK。

理由：時間偏置本來就該在「真的要說話的那一刻」現算（那是 `_fire_intent` 的語意時刻），
而不是靠 tick 事件推播。**這同時符合 Bry 對 M5.7-2 的顧慮——不碰訂閱架構。**

⚠️ 現算的函式介面由執行者在開工時依 `src/temporal/render.py` 現況確認後填入本節；
**若發現沒有可直接呼叫的單一函式，停下回報，不要自行拼接 render 字串。**

### 3. 消費端順帶修正

`proxy.py:3706-3712` 的「Fix Bug 5」目前在生產上恆 False
（`isinstance(ctx, dict)` 而實際值是 `str`）。若 §四-2 已讓 `chrono_context` 帶值，
這段 fallback 的語意需一併釐清（`draft` 已在 `_fire_intent` 另一條分支傳遞，
這個 fallback 可能整段是冗餘的）。**若確認冗餘，刪除並在 commit 說明；若非冗餘，改成正確型別判斷。**

### 4. `PromptContext` 的處置（二選一，執行者不得自行決定）

| 選項 | 內容 |
|---|---|
| **X（推薦）** | 刪除 `proxy.py:1555-1602` 整個 `PromptContext` 類別——它 0 實例化，且新插槽已由 live builder 承擔，**不留第二份真相** |
| Y | 保留但整份標 `@deprecated`，並在註解指明新路徑為 `_build_messages_*` |

⇒ **需 Bry 拍板。預設走 X。**

## 五、測試

1. **必須**：移除 `tests/test_soul_bug_watchdogs.py::test_chrono_context_must_reach_prompt` 的
   `@pytest.mark.xfail`。`strict=True` 會在修好瞬間噴 XPASS（視為 FAILED）——
   這是 Bry 指定的防線，**修復者必須顯式移除標記**，不可留著讓它噴。
2. 新增端到端斷言：`AGENT_INTENT.payload["chrono_context"]` 非空時，
   經 `_build_messages_group` 產出的 messages 中 system 訊息**必須包含該字串**。
3. 新增負向斷言：`chrono_context=""`（預設）時，system prompt **不得**多出空白段落
   （防「接線成功但注入空字串」這種半修復）。
4. 回歸：`pytest -q tests/test_chrono_integration.py tests/test_soul_bug_watchdogs.py tests/test_m5_3_s2_d_world_awareness.py`。
5. 全庫回歸。

## 六、不做（Out of Scope）

- ❌ 動 `consciousness.py:155` / 恢復 `SYSTEM_TICK` 訂閱（SOUL-BUG-003 的裁示範圍）
- ❌ 清理 `_on_tick` 死碼（SOUL-BUG-003）
- ❌ 動 `src/world/middleware.py:265-303` 的四個反推函式（它們會隨 chrono 有值而自然恢復，無需改）
- ❌ 順帶修 SOUL-BUG-002 / 001

## 七、Frozen Contract 注意

- Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯：**不得改動**。
- 本工單唯一動到 `src/` 的是 `llm/proxy.py` 的 system prompt 組裝與 `agent/consciousness.py` 的 `_fire_intent` 內部取值，
  **不涉及上述任何 frozen contract**。
- `PromptContext` 若走選項 X 刪除，須確認全庫（含 `scripts/`、`docs/` 引用）無呼叫端——
  主大腦已實測 `PromptContext(` 全庫 0 實例化，但執行者須自行複驗一次。

## 八、回報格式

- 改動檔案清單（含行數）
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 看門狗是否已移除 `xfail` 並轉綠
- 是否踩到 Frozen Contract / 是否觸及 SYSTEM_TICK 訂閱架構（預期答案：否）