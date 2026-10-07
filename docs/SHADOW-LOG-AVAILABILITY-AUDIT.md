# Shadow Log Availability Investigation — 根因調查

**狀態**：完成（只查不修）
**日期**：2026-10-07
**調查範圍**：producer / consumer 程式、設定與 flag、git 歷史、檔案時序、測試期待

---

## 結論一句話

**`shadow_log.jsonl` 是 2026-08-19 被「刻意停用」的，不是壞掉。**
停用者有明確的 Owner 決策與成本理由，commit 時間與最後一筆資料只差 45 分鐘。

**但這次調查推翻了 baseline 報告裡的一個誤判**：shadow log 從來就不是
continuity 觀測工具，所以「補回它」不能解決真正的 P0 blocker。詳見 §5。

---

## 1. Shadow Log 為什麼在 2026-08-19 停止？

### 直接原因

`scripts/run_server.py:1229`（commit `94e3c6b`，2026-08-19 13:03:18 -0400）：

```python
shadow_obs = init_shadow_observer(shadow_dir, enabled=False, llm_proxy=llm)
```

`src/memory/shadow.py` 的短路順序決定了一切：

```python
def is_active(self) -> bool:
    if not self.enabled:
        return False                       # ← enabled=False 在這裡就短路
    return datetime.now() < self.expires_at

async def observe(...):
    if not self.is_active():
        return {"shadow_active": False, "skipped": True}   # shadow.py:115-116
    ...
    with open(self.log_file, "a", ...) as f:               # shadow.py:151 寫入
```

`enabled=False` 讓 `observe()` 在**寫入區塊之前**就 return。因此：

- 觀察器 singleton **仍然被建立**（不是沒初始化）；
- `maybe_observe()` 仍然被 middleware 呼叫（`src/memory/middleware.py:577`）；
- 每次呼叫都安靜地回 `{"shadow_active": False, "skipped": True}`，**不寫檔、不報錯**。

所以現場看起來「活著但沒資料」——這正是為什麼不能只憑空白就假定 instrumentation 壞掉。

### 時序吻合

| 事件 | 時間 |
|---|---|
| shadow_log 最後一筆 | `2026-08-19T12:18:31` |
| 停用 commit `94e3c6b` | `2026-08-19 13:03:18 -0400` |

**相差 45 分鐘。** 這不是巧合能解釋的。

---

## 2. 刻意停用，還是意外失效？

**刻意停用，且有明確的 Owner 決策。**

commit 訊息逐字：

```
perf(memory): disable shadow observer to halve judge LLM calls

根因 (Bry 拍板 2026-08-18): 每則 AGENT_SPEAK 跑兩次完整 LLM judge
(=26 次串行 call):
  1. post_reply_commit 的 _extract_facts_llm (13 次)
  2. shadow observer 的 maybe_observe 又跑一次 (13 次)

shadow observer 是 7/2 的 7 天 A/B 實驗 (對照 v6 vs heuristic),
但 init 每次重啟都 reset started_at → 7 天永遠到不了, 實驗跑不完;
輸出 shadow_log.jsonl 完全沒人讀 (0 consumer, 確認過)。

修法: run_server.py init_shadow_observer enabled=True → False。
省一半 judge 成本, 零功能影響 (實驗早已完成)。
```

程式碼註解（`run_server.py:1224-1228`）也重複同一理由並標註「M7-judge-fix (Bry 拍板 2026-08-18)」。

**分類：A. intentional disabled。** 不是 B/C/D/E/F。

---

## 3. 底下那層真正的設計缺陷

雖然這次停用是刻意的，但調查過程揭出一個**結構性缺陷**，它正是當初要關掉它的理由：

### 3.1 7 天自動到期是自我否證的設計

```python
SHADOW_DURATION_DAYS = 7
self.started_at  = datetime.now()          # ← 每次行程啟動都重設
self.expires_at = self.started_at + timedelta(days=7)
```

觀察器由 `run_server.py` 在**每次服務啟動**時建立。服務只要重啟一次，
7 天計時就歸零。commit 訊息說得很準：

> init 每次重啟都 reset started_at → 7 天永遠到不了, 實驗跑不完

也就是說「跑滿 7 天自動關閉」這個需求**在當時的架構下根本無法達成**。
它不是「還沒到期」，是「永遠不會到期」。

### 3.2 文件裡的 switch 不存在

`src/memory/shadow.py:23` 的 docstring 宣稱：

```
- enable flag: SHADOW_MODE_ENABLED=true(預設啟動)
```

但全 repo 搜尋 `SHADOW_MODE_ENABLED` 的結果只有：

- `src/memory/shadow.py:23`（docstring 註解）
- `docs/MEMORY-STATUS-AND-PLAN.md:193`（文件）

**沒有任何一行程式讀取 `os.environ["SHADOW_MODE_ENABLED"]`。**
唯一的控制點是 `run_server.py` 裡那個硬寫的布林值。
⇒ 這個開關是文件裡的幻覺，runtime 沒有這個旋鈕。

### 3.3 沒有測試保護 shadow logging

全 repo 提到 shadow 的測試裡：

| 檔案 | 實際期待 |
|---|---|
| `test_m7_memory_judge_fire_and_forget.py` | `monkeypatch.setattr(shadow_mod, "maybe_observe", noop_observe)` —— 驗證 hook 是 fire-and-forget，**不是**驗證有寫 log |
| `test_intimacy_growth_2.py` | 與 shadow mode 無關（`SHADOW` 是一個 decay 狀態變數） |
| `test_rem_text_guard.py` / `test_rem_soul_regression.py` | 與 shadow mode 無關 |

停用後 `test_m7_memory_judge_fire_and_forget.py` 仍 **2 passed**。
⇒ 沒有任何測試會因為 shadow logging 停擺而紅，也沒有任何測試在保護它。

---

## 4. 恢復 observability 是否需要碰 frozen Memory contract？

**不需要。** 但有兩個必須先知道的前提。

### 4.1 停用本身是一行、且不在 frozen 範圍

改動點是 `scripts/run_server.py:1229` 的一個布林字面值，
**不在 `src/memory/**` 內**。翻回 `enabled=True` 即可立即恢復寫入。
這與 Memory / SAGE contract 無關，不需要 Owner + 主大腦授權邊界。

### 4.2 但恢復它會同時做三件事，其中兩件是壞事

| 後果 | 評估 |
|---|---|
| 每則回覆多跑 13 次串行 LLM call | 🔴 Bry 當初關掉它的直接原因，屬成本回歸 |
| 7 天自動到期仍然失效（每次重啟歸零） | 🔴 同 3.1 的缺陷未被修 |
| shadow_log 重新有資料 | ✅ 但**答非所問**，見 §5 |

---

## 5. 🔴 本次調查推翻的東西：我對 baseline 的誤判

`docs/MEMORY-BASELINE-OBSERVATION-2026-10-07.md` §2.4 原本寫：

> Interpretation 層：可觀測性已中斷 / 49 天無資料 /
> 「取回後是否影響後續行為」這一格，目前無法產出證據。

**這個框架是錯的。** 更正如下：

### 5.1 shadow_log 從來就不是 continuity 儀器

它的 schema（`shadow.py:118-128`）只有：

```
timestamp / agent_id / speaker / text / context_provided
v6: {status, facts, n_facts, error}
heuristic: {facts, n_facts}
```

它是 **2026-07-02 的 7 天 A/B 實驗**：用 LLMJudge 與現有 heuristic 並排跑，
比對兩者抽出事實的一致率（`summarize()` 算 category_agreement 與 error_rate）。

其中 `context_provided` 只是一個布林值，代表「呼叫當下有沒有 context 字串」，
**不是**「某個被取回的記憶改變了後續行為」。

**它沒有任何欄位能把「取回的記憶」連到「行為的改變」。**

### 5.2 所以真正的 P0 不是「停了 49 天」

正確的描述是：

> **能回答「記憶被取回後是否改變後續行為」的儀器，從來沒有被建造過。**

shadow log 是 v6 judge 的品質對照實驗，在 2026-08-19 被正當地關閉；
它不是 continuity 的觀測鏈，把它打開也不會變成觀測鏈。

這反而**強化**了 Bry 的判斷 —— 問題比「49 天沒資料」更根本。
但原因不同：不是 instrumentation 壞了，而是**選錯了儀器**。

---

## 6. 對下一步的影響

Bry 設定的邊界是：

> 在 Shadow Log 的停止原因與可觀測性狀態未釐清之前，不設計 downsampling 的
> regression criterion，也不修改 Memory runtime。

原因已釐清，邊界可以解除一半，但**換成新的邊界**：

| 選項 | 是否需要 frozen contract | 建議 |
|---|---|---|
| 翻回 `enabled=True` 補回 shadow_log | 否（一行） | ❌ 不建議：答非所問 + 成本回歸 |
| 修 3.1 的 7 天到期缺陷 | 是（`src/memory/shadow.py`） | ⚠️ 但修好仍然答非所問 |
| **設計一個真正的 continuity 觀測鏈** | 待評估 | ✅ 這才是 P0 的正面處理 |

要回答「記憶被取回後是否影響行為」，需要的是把
`loader_trace`（哪一筆記憶被檢出、confidence 多少）
與**該回合的實際 prompt 內容／輸出**關聯起來的機制。

目前 `data/sessions/*.json` 有對話歷史，可作為關聯來源；
但那是 conversation history，不是「當回合注入了哪些記憶」的快照。

**本調查到此為止，不動任何東西。** 下一個決定屬於設計決策，需要 Bry 拍板。

---

## 7. 本次調查遵守的邊界

| 約束 | 狀態 |
|---|---|
| 不啟動服務 | ✅ |
| 不發 production request | ✅ |
| 不修改 `src/memory/**` | ✅ |
| 不重啟 / 恢復 shadow log | ✅ |
| 不補寫歷史 shadow records | ✅ |
| 不改 threshold / gate | ✅ |
| 不改 Palace | ✅ |
| 不修改 `personas/agent_rem.md` | ✅ |

全部為讀取與搜尋作業。