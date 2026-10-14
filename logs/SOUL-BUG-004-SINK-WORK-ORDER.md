# SOUL-BUG-004-SINK — chrono 時間感知：僅修匯端（不碰 SYSTEM_TICK 訂閱架構）

**母工單**：[SOUL-BUG-004](SOUL-BUG-004-WORK-ORDER.md)（P0）
**本工單**：**只做匯端（sink）**，Bry 2026-10-13 裁示：「先不動它，待 005 修完後，在專屬工單中僅處理匯端，**絕不貿然碰 SYSTEM_TICK 的訂閱架構**。」
**狀態**：✅ **APPROVED & CLOSED（`f649224`，2026-10-14）**
**前置條件**：SOUL-BUG-005 已修（`277a8d9`）✅

> ## ✅ 結案紀錄（2026-10-14，commit `f649224`）
>
> **匯端接通時順帶修復了 `src/temporal/core.py` 未正規化 UTC 的 4 小時時區偏移，達成端到端正確性。**
>
> 這是 Bry 2026-10-14 追加裁示批准的原地閉環（不切單）。理由：
> 接通匯端的根本目的不是讓管線通，而是讓 prompt 獲得**正確的**時間感知；
> 明知是慢 4 小時的鐘卻為了守住邊界原樣送出，等於主動製造角色認知幻覺。
>
> 實際交付：
> - 匯端：`chrono_context` kwarg + 注入（兩個 live builder）、payload 透傳、
>   **刪除 `PromptContext` 死碼**（0 實例化，EH-1/EH-4 承諾但從未開出的那張工單）、
>   刪除恆 False 的「Fix Bug 5」死碼。
> - 中段：`_fire_intent` 在發言那一刻現算 `_build_chrono_block()`，
>   **沿用既有單一實作** `build_temporal_context` + `render_temporal_block`
>   （與 `heartbeat/engine.py:225-233` 同參數），未重複實作任何格式化邏輯。
> - 時區：`build_temporal_context` 加入 `now = now.astimezone(config.timezone)`
>   一行根治，**同時修好 `heartbeat/engine.py:205` 的潛在隱患**。
>
> **SYSTEM_TICK 訂閱確認未動**（`consciousness.py:155` 原樣保留註解），
> `_on_tick` 死碼亦未動 —— 屬 SOUL-BUG-003 的裁示範圍。
>
> 驗收見下方 §九。

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
---

## 九、驗收實測（2026-10-14，主大腦親跑）

### 9.1 時鐘對齊（Bry 點名的三個案例）

修復前（UTC 小時直接當本地小時）→ 修復後：

| UTC 輸入 | 紐約本地 | 修復前標籤 | 修復後標籤 |
|---|---|---|---|
| `12:00` | 08:00 | `afternoon` ❌ | **`morning`** ✅ |
| `18:00` | 14:00 | `evening` ❌ | **`afternoon`** ✅ |
| `00:00` | 20:00 | `deep_night` ❌ | **`evening`** ✅ |

全 24 個 UTC 小時掃描：修復前**每一個**時段標籤都錯；修復後全部與本地一致。

脆弱窗（22:00-04:00 persona 本地）：紐約 18:00（UTC 22:00）**不再**誤判為脆弱窗；
紐約 23:00（UTC 03:00）正確開啟。

### 9.2 新測試：7 支，且做過變異實驗

`tests/test_temporal_timezone_normalization.py`（7 tests）：
Bry 點名的三案例 / 全 24 小時掃描 / EDT-EST DST 邊界 / 脆弱窗本地性 / 渲染層同步。

🔴 **變異實驗（Bry 紀律：綠燈不算證據）**：
以 in-process monkeypatch 把 persona 時區強制回 UTC（＝修復前語意）後重跑
→ **7 支全紅**；正常情況 **7 支全綠**。
⇒ 這些斷言是**真守衛**，不是恆真式。

### 9.3 回歸

- `tests/test_temporal_timezone_normalization.py + test_soul_bug_watchdogs.py +
  test_m6_0_3_validation_d_e_f_g_h + test_m5_3_s2_e_e2e_world_perception +
  test_m5_3_s2_d_world_awareness + test_chrono_integration + tests/infra +
  test_tl2_volition + test_m3_2_semantic_enrichment`
  → **240 passed, 1 xfailed**
- 護欄 `tests/infra`：白名單數字 `address=9 / kill=1 / subprocess=22` **全數未變**
- SOUL-BUG-001 看門狗仍為 **XFAIL**（缺陷未修，符合預期）
- 全庫回歸結果見 `logs/SOUL-TECH-DEBT-001-EXECUTION-REPORT.md` §11

### 9.4 主大腦複核紀錄（不採信執行者自報）

- `consciousness.py:155` 的 `SYSTEM_TICK` 註解**原封不動**：已實測第 150-161 行內容。
- `PromptContext` 全庫已無實例化（僅剩註解與 2026-07 的 `_backup_*` 舊備份目錄）。
- 刪除 `dataclass` import 安全：`proxy.py` 已無任何 `dataclass` / `field(` 使用（已 grep）。
- `_build_chrono_block` 未自造格式化：簽章與 `heartbeat/engine.py:225-233` 逐一比對一致。
- Worker 曾在 `:3706-3712` 刪除死碼前保留了證據註解，符合「不留第二份真相」的要求。
- **Worker 誠實回報了三處工單事實不符**（見 §十），主大腦複核後確認成立。

---

## 十、執行者回報的三處「工單事實不符」（主大腦複核後成立，保留記錄）

本次是一張**別人寫的工單**，執行過程中發現工單本身有三處不精確。
保留這三條是為了讓未來的工單不要再重複犯：

1. **看門狗第二道斷言並非「失敗」，而是「空洞地通過」**。
   工單說它檢查 `EventType.SYSTEM_TICK` 有無活的訂閱者；實際上 `consciousness.py:171`
   是一行**非註解的 `elif` 死碼**，讓該斷恆 True。移除 `xfail` 時 pytest 噴出 XPASS(strict)
   才暴露。⇒ **「測試存在」不等於「測試在測東西」**；`strict=True` 在這裡意外扮演了偵測角色。

2. **`docs/EH-1:128` 引用的行號已過期**。
   文件說 `PromptContext` 在 `proxy.py L1329-1385`，實際位於 **1554-1611**。
   ⇒ 文件裡的行號是**會過期的**，不可作為刪除依據（本次以實測行號為準）。

3. **「單一可呼叫函式」其實是一對**。
   chrono 區塊需要 `build_temporal_context`（建模）+ `render_temporal_block`（渲染）
   兩個呼叫，與 `heartbeat/engine.py:225-233` 完全同構。工單寫「單一函式」過於簡化。
   ⇒ 執行者沿用既有一對函式、未自造格式化，這是正確處置。

另外 Worker **刻意沒有「順手修正」UTC 慣例**，而是照實沿用並回報 —— 這個判斷正確：
沿用既有語意是本工單的邊界，偏移問題屬另一層，交由主大腦裁示後原地閉環。
