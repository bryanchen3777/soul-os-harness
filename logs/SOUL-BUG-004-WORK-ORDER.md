# SOUL-BUG-004 — chrono 時間感知雙端切斷，全角色主動發言喪失時間脈絡

**工單編號**：SOUL-BUG-004
**嚴重度**：**P0（最高優先）**
**狀態**：APPROVED & OPEN（Bry 裁示立案 2026-10-13）｜**已掛 `xfail(strict=True)` 看門狗**
**發現方式**：SOUL-TECH-DEBT-001 C 類深度鑑識（chrono / SYSTEM_TICK 相關測試）
**引入 commit**：`95891e8`（M5.7-2，移除 SYSTEM_TICK 訂閱）；匯端死碼早於本工單即已存在

**看門狗**：`tests/test_soul_bug_watchdogs.py::test_chrono_context_must_reach_prompt`

---

## 一、現象

`chrono_context`（時間偏置區塊）**永遠無法抵達任何 LLM system prompt**。
Agent 仍然「活著」、仍然會說話、沒有任何錯誤與告警——但每一次發言都**不知道自己現在是幾點、上次說話是多久前、今天已經過了多久**。

## 二、根因：**兩端皆斷**（主大腦逐行複核 2026-10-13）

### (a) 源端 — SYSTEM_TICK 訂閱被註解，`_on_tick` 不可達

`src/agent/consciousness.py:150-158`：

```python
self.bus.subscribe(
    ...
    event_filter={
        EventType.USER_MESSAGE,
        # EventType.SYSTEM_TICK,  # M5.7-2: 拿掉, 避免 proactive Agency
        EventType.AGENT_SPEAK,
        EventType.SESSION_END,
    },
    ...
)
```

`:149` 的註解已自陳「`_on_tick` method 保留 (dead code, 給未來觀察 / debug 用), **不會被呼叫**」。
`:171-172` 的 `elif event.event_type == EventType.SYSTEM_TICK: await self._on_tick(event)` 因此是死碼。

**連帶後果**：`_on_tick`（`:325-347`）是唯一會把 SYSTEM_TICK 的 `event.payload` **整包**透傳進
`_fire_intent(chrono_payload=event.payload)` 的路徑（`:341`），也只有它有條件帶到 `chrono_block`。

### (b) 中段 — 所有活路徑都只傳 `draft`，`chrono_context` 恆為空字串

`_fire_intent`（`:446-447`）：

```python
if chrono_payload:
    intent_payload["chrono_context"] = chrono_payload.get("chrono_block", "")
```

逐一盤點生產上**活著的**四個呼叫點：

| 呼叫點 | 觸發 | 傳入的 chrono_payload | 有 `chrono_block`？ |
|---|---|---|---|
| `:230` | `USER_MESSAGE`（私聊） | `{"draft": ...}` (+target_channel/target_user_id) | ❌ |
| `:259` / `:291` | `USER_MESSAGE`（群聊分支） | `{"draft": content}` | ❌ |
| `:338` | **`_on_tick`（已死）** | `event.payload`（整包，含 chrono_block） | ✅ 但不可達 |
| `:386` | `AGENT_SPEAK` 跟進 | `{"draft": ...}` | ❌ |

⇒ **生產上 `intent_payload["chrono_context"]` 恆為 `""`**，從未有任何非空值。

### (c) 匯端 — `PromptContext` 整個類別從未被實例化

`src/llm/proxy.py:1555` 定義 `class PromptContext`，`:1568` 有 `chrono_context: str = ""` 插槽，
`:1596-1598` 有對應的注入邏輯。

但全庫（含 `scripts/`、`tests/`）搜尋 `PromptContext(` 於**實例化位置** **0 處命中**——
`src/` 內只有 `proxy.py:1555` 這一處類別定義本身。這個類別（含 chrono 插槽與注入）
**沒有任何生產呼叫端**。實際的 prompt 組裝走 `_build_messages_*`，其簽章根本沒有 `chrono_context` 參數。

repo 自己的契約文件 `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md:128` 已記錄
「`PromptContext`（proxy.py L1329-1385）為死代碼，0 實例化」。

### ⚠️ 這不是沒人發現，而是**已發現並被正式擱置**

`docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md:357`：

> 不處置 `PromptContext` 死代碼（記錄於 §2.6，**另開工單**）。

`docs/EH-4-EPISTEMIC-MIND-CONTRACT.md:1040` 同樣記載：

> **不處理** `PromptContext` 死代碼 …… （`CLEAN-HERMES-RESIDUE`，EH-1 D4 裁定）

⇒ EH-1 D4 當時承諾的「另開工單」**從未開出**。
這筆不是未知缺陷，而是**一份被遺忘的承諾**——而它承諾要處理的正是全角色時間感知的匯端。
本工單即為該承諾的兌現。

### (d) 唯一讀取 `chrono_context` 的消費端也在空值上運行

`src/llm/proxy.py:3706-3712`：

```python
if not user_message and reason == "user_message":
    ctx = event.payload.get("chrono_context", {})
    if isinstance(ctx, dict) and ctx.get("draft"):   # ← 恆 False
```

`isinstance(ctx, dict)` 在生產上**恆為 False**（值是 `""` 這個 str），這個「Fix Bug 5」從未真正生效過。

## 三、為何日誌完全安靜

整條鏈的每一段都「照常執行」：tick 事件照樣被 dispatch（只是沒有訂閱者）、chrono 區塊照樣被 render（只是沒有消費者）、
payload 照樣帶著空字串往下走。**沒有 exception、沒有 warn、沒有計數器**。
這是典型的「靜默斷鏈」——`src/world/middleware.py:265-303` 那四個從 chrono_context 反推
temporal_salience / anticipatory_flavor / vulnerability_window / silence_hours 的函式，
現在同樣全部恆為預設值。

## 四、修復方向（Bry 2026-10-13 裁示）

> 立即接通源端（或建立替代時間注入管道），打通匯端注入鏈。

拆成三個可獨立驗收的子步：

1. **匯端先行（風險最低、收益最直接）**：在 live 的 `_build_messages_group` / `_build_messages_private`
   上加回 `chrono_context` 參數與 system prompt 注入。`PromptContext` 要嘛接回這條鏈，
   要嘛明確標註為 deprecated 並刪除——**不可留著當第二份真相**。
2. **中段**：讓每個 `_fire_intent` 呼叫點都真的帶上 `chrono_block`（或在 `_fire_intent` 內統一由
   `temporal/` 現算一次，兩條路都可，但必須只有一條為準）。
3. **源端**：`SYSTEM_TICK` 不可恢復訂閱的話（M5.7-2 是為了避免 proactive Agency 重複觸發），
   則改由 scheduler / heartbeat 引擎主動注入時間偏置。**不要為了拿時間而把 SYSTEM_TICK 訂閱整個打開**——
   那會把 M5.7-2 想解決的問題帶回來。

## 五、驗收

1. `tests/test_soul_bug_watchdogs.py::test_chrono_context_must_reach_prompt` **轉為 XPASS→FAILED 為止**：
   修復者必須移除 `xfail` 標記並讓它常態綠燈。
2. 至少一個角色的實際發言 system prompt 內含非空 chrono 區塊（須附實證 log 截段）。
3. `src/world/middleware.py:265-303` 的四個反推函式在生產條件下不再恆為預設值。
4. 全庫回歸 `tests/test_chrono_integration.py tests/test_m5_3_s2_d_world_awareness.py
   tests/test_soul_bug_watchdogs.py` 通過。
5. **不得**順帶恢復 SYSTEM_TICK 訂閱而引入 proactive 重複觸發（須對照 M5.7-2 的原始動機逐條驗證）。

## 六、主大腦複核紀錄（2026-10-13）

- `:155` SYSTEM_TICK 註解、`:171-172` 死碼分支：**已逐行讀取確認**。
- 四個 `_fire_intent` 呼叫點的 `chrono_payload` 內容：**已逐行讀取確認**（上表）。
- `PromptContext(` 全庫（含 scripts/tests/docs）0 處實例化，`src/` 僅有類別定義：**已全庫 grep 確認**。
- `docs/EH-1:128/:357`、`docs/EH-4:1040` 已記載該死代碼並承諾「另開工單」：**已讀取確認**。
- `proxy.py:3706-3712` 的 `isinstance(ctx, dict)` 恆 False：**已逐行讀取確認**。
