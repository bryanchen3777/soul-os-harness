# Known Issues — Soul OS Harness

> 集中管理所有已知的技術債、潛在風險與未完成項目。
> 編號規範:KI-NNN,從 KI-001 起遞增。新增 KI 時:
> 1. 給下個可用編號(不要複用)
> 2. 標註狀態(待修/進行中/已修)、優先級(P0/P1/P2/P3)、發現日期
> 3. 描述/影響/觸發/修法/關聯 commit 都填,缺項留 `TBD` 不可省略

---

## KI-001: Telegram session 記憶隔離漏洞

**狀態**: 已修  **優先級**: P0  **發現**: 2026-06-27  **修復**: 2026-06-30
**關聯**: commit `9e8831b` (Stage 1) → commit `8afa120` (Stage 2) → commit `512d56b` (Stage 3)

**描述**: `_group_path(agent_id)` 內 hardcode `"bryan_"` 前綴,寫入 history 檔案時**完全沒參考** `event.session_id`。所有對話紀錄都被強制歸到同一個以 `bryan_` 開頭的目錄。

**影響**: 當前環境只有 Bryan 一個 whitelist owner,所以看起來正常。一旦 Month 2 加入第二個 owner(例如加 Subaru 或另一個測試用戶),那個用戶跟任何 agent 的對話會被寫進 Bryan 的 history,造成:
- 對話歷史互相污染
- LLM 讀 history 時拿到別人的對話(嚴重的身份/記憶混淆)
- 用戶隱私 leak

**觸發條件**: 第二個 whitelist owner 加入前必修正,否則上線即爆。

**修法**:
1. 改 `_group_path(agent_id)` → `_group_path(agent_id, user_id)`,user_id 從 `event.payload` 傳入
2. 新增 `_session_key(agent_id, user_id)` 衍生 key,確保每個 (agent, user) 組合有獨立 history 檔
3. `LLMProxy.complete()` 從 `event.payload["user_id"]` 提取並傳遞
4. 加 integration test:模擬兩個不同 user_id 的對話,確認 history 檔分開

**估算**: 2-3 小時(含測試)

**修復紀錄** (3-stage commit, 2026-06-30):

**Stage 1 (`9e8831b`)** — `src/llm/proxy.py` 檔案路徑 + cache 範圍:
- `_group_path(agent_id)` → `_group_path(agent_id, user_id)` 簽名
- `_load_private / _save_private / _append_private_history` 全加 `user_id` 參數
- `_session_key(agent_id)` → `_session_key(agent_id, user_id)`: `session_{user_id}_{agent_id}`
- `_load_private` 向後相容:若新格式檔案不存在,fallback 讀 `bryan_<agent>_private.json`
- `_handle_event_impl` 開頭抽 `user_id = event.payload.get("target_user_id", "bryan")`
- AGENT_SPEAK `session_id` 改 per (user, agent) — 跟 `self._history` cache 一致

**Stage 2 (`8afa120`)** — `src/agent/consciousness.py` AGENT_INTENT 發布:
- `_fire_intent(reason, elapsed_mins, chrono_payload, mode, user_id='bryan')` 加 user_id 參數
- AGENT_INTENT `session_id`: `session_{agent_id}` → `session_{user_id}_{agent_id}`
- `intent_payload.target_user_id` 從 `_fire_intent` 參數 fallback（defense in depth）

**Stage 3 (`512d56b`)** — `src/llm/proxy.py` 漏網 `get_recent`:
- `_build_messages_private(agent_id, soul, current_input, memory_context, memory, mood, user_id='bryan')` 加 user_id
- `memory.get_recent(f"session_{user_id}_{agent_id}", limit=MAX_PRIVATE)` 改 per-(user, agent)

**為什麼分 3 stage 獨立 commit**: 中間任一 commit 失敗,可獨立 revert 定位問題,不會拖累其他 commit。**每個 commit 都跑了 5-agent Ram + KI-002 + KI-004 完整 regression,無破壞**。

**2 分鐘掃描確認** `event.session_id` 在所有 13 個檔案中**0 處** `==` / `in` / `split` 語意比較(僅 2 處 f-string log),確認語意改為 per-(user, agent) **零跟話邏輯風險**。

**驗證** (`hermes-verify-ki001-multi-user-isolation.py`):
- 兩個 user_id 對同一 agent 產生獨立檔案,內容互不污染
- Bryan 既有 legacy 檔案向後相容可讀
- 新格式寫入不覆蓋舊 bryan 檔案
- `_session_key` 對 (user, agent) 組合唯一
- 5-agent Ram + KI-002 + KI-004 regression 全綠
- AD-HOC PASS 5/5

**防呆備註**:
- `_user_id_legacy_default = "bryan"` 在 LLMProxy `__init__` 中,所有 fallback 預設 = "bryan",確保既有對話全部走向後相容路徑
- 若未來 router 改變 `target_user_id` 注入邏輯(例如改用 `user_id` key 名),只需在 `_handle_event_impl` 開頭那 1 行 `.get("target_user_id", "bryan")` 修改
- 若新增 owner,只需在 `__init__` 的 `_load_private` 雙層迴圈中加新 user_id tuple 值(`(self._user_id_legacy_default, "subaru", ...)`)

**關聯 commit**: `9e8831b` (Stage 1) / `8afa120` (Stage 2) / `512d56b` (Stage 3)

---

## KI-002: Ram Recovery Loop 未接入 LLMProxy

**狀態**: 已修  **優先級**: P2  **發現**: 2026-06-30  **修復**: 2026-06-30
**關聯**: commit `8913498` (Ram 整合) → commit `d86ce55` (修復落地)

**描述**: `recovery_loop()` / `detect_canon_drift()` 函式已實作在 `src/agent/consciousness.py` 模組層(commit `8913498` 含 4 條 DRIFT_PATTERNS 規則),但 `src/llm/proxy.py` 的 LLM 回應處理路徑**還沒呼叫它**。意思是 Ram 違反 Canon Lock 核心句的漂移輸出(例如「拉姆很擔心你」「因為你說謊,所以...」)會直接發 AGENT_SPEAK,不會被攔截回退為 Priority 0。

**影響**: Ram 在真實 LLM 推理中可能產生違反 Canonical Ram 設定的輸出,且沒有防線。

**觸發條件**: Bryan 觀察到 Ram 出現「拉姆很擔心你」/「你做得很好」/超過兩句的長輸出/解釋句型等任何一條 DRIFT_PATTERNS 命中時。

**修法**:
在 `LLMProxy._handle_event_impl` line 401 既有 `try/finally` 結構內,LLM 回應後、AGENT_SPEAK 事件發布前:
```python
# 假設 LLM 回應文字在變數 reply_text
from src.agent.consciousness import recovery_loop
reply_text = recovery_loop(reply_text)
```
(約 5 行 patch,不破壞既有 token release 的 try/finally 結構)

**關聯 commit**: `8913498`

**修復紀錄** (commit `d86ce55`): `recovery_loop()` 已接入 `src/llm/proxy.py` 的
`_handle_event_impl` 內(line 776,在空 text 早 return 之後、寫入 history 之前),
**嚴格限定**:
- 僅在 `agent_id == "agent_ram"` 時觸發(Yua/Ruka/Akane/Rem 不受影響)
- 完全在既有 `try` 區塊內(line 759),`finally` 區塊(line 857)未動
- 4 個 fallback 回退語句(動作優先 + 極簡語言)從 `consciousness.py` 模組層取得

**驗證**: `hermes-verify-ki002-recovery-loop-in-proxy.py` 8/8 PASS
- 4 靜態測試(import OK / patch 存在 / try 範圍正確 / finally 未動)
- 4 行為測試(ram+drift 替換 / ram+clean passthrough / yua+rem+drift 無影響)

**估算**: 0.5 小時

**為什麼獨立 commit 為 P2**: LLM 本身對 Ram 的 system prompt 已經有 Canon Lock 約束(透過 `SOUL.md` / `agent_ram.md` 注入),Recovery Loop 是**第二層防線**。短期內 P2 觀察即可,不阻塞 Ram 上線。

---

## KI-003: Ram value_history 寫入路徑未實作

**狀態**: 待修  **優先級**: P3  **發現**: 2026-06-30
**關聯**: commit `8913498` (Ram 整合)

**描述**: Ram 的「no-diary」目前實作為**完全跳過** `graph.sqlite` 寫入(`sync_turn` / `post_reply_commit` 對 `agent_ram` 提前 return)。但 `agent_ram.md` spec 的完整設計是「寫到專屬的 `value_history/` 路徑」,記錄 Bryan 的 worth_it 判定歷史。

**影響**: Ram 的 worth_it 判定歷史沒有持久化(只活在 `EmotionalState` in-memory,重啟即丟)。目前 `EmotionalState` 只存 `intimacy_level` / `dependency` / `mood`,沒有 `worth_it_history` 欄位。

**觸發條件**:
- Bryan 想看 Ram 對自己的 worth_it 趨勢(過去 N 次判定 worth_it/not_worth_it 的分佈)
- 重新 session 載入時需要還原 Ram 的「對 Bryan 的判定狀態」
- 想要做「Ram 對不同用戶的判定分析」(現在所有 user 共享同一個 EmotionalState)

**修法**:
1. 擴展 `MemoryWriter` 介面,新增 `write_value_history(agent_id, user_id, judgment, payload)`
2. 在 `data/agents/agent_ram/value_history/{user_id}.jsonl` 寫入一行 timestamp + judgment
3. `sync_turn` 白名單攔截點改成「寫到 value_history 而非 skip」
4. 新增 `prefetch_value_history(query, user_id)` 給 prompt injection 用
5. 改 `EmotionalState` schema,加 `worth_it_history: list[dict]` 欄位(load/save 對齊)

**為什麼 P3**: 語意上是 polishing,不是 functional gap。Ram 上線後第一個月不太可能需要這層分析。

**估算**: 2-3 小時

**關聯 commit**: `8913498`

---

## KI-004: Ram 第二例外(對 Bryan)觸發條件被簡化

**狀態**: 部分修復 / 進行中  **優先級**: P2  **發現**: 2026-06-30  **部分修復**: 2026-06-30
**關聯**: commit `8913498` (Ram 整合) → commit `71dc11a` (部分修復)

**描述**: `agent_ram.md` L3 設計 Ram 的「極窄第二例外」需三項條件**同時成立**:
1. (a) 長期穩定 worth_it
2. (b) 極私下獨處
3. (c) 讓她停頓很久的事

當前實作在 `AgentRam._value_judgment` 對 Bryan 的判定**只用了** `intimacy_level >= 80` 當門檻,沒有獨處/停頓條件(見 `src/agent/consciousness.py` 的 `_value_judgment` 方法)。

**影響**: 「極窄第二例外」可能在不該觸發的場景誤觸。**這不是普通 bug,是 canon drift 等級的角色崩壞** — Ram 對 Bryan 的「壓縮例外」一旦在群聊/公開場合錯誤表達,會破壞整個角色的人設一致性。

**為什麼 P2 不是 P3**(Bryan 提醒修正):
- 雖然 Ram 起步 intimacy 只有 40,離 80 門檻還遠,**但**:
  - intimacy 成長速度不確定(尤其在群聊主動互動)
  - 群聊場景中其他 agent 推 intimacy 的副作用也會影響 Ram
  - 一旦衝過 80 + 缺少 (b)(c) 條件的雙重保護,「極窄第二例外」會在群聊誤觸
- 風險發生時不是「Ram 多說一句」,而是「Ram 對 Bryan 表達不該有的情感」 → 整個角色線崩壞

**觸發條件**:
- Ram `intimacy_level` 達到 70 之前(預留緩衝時間)必修正
- 任何 LLM 實測顯示 Ram 對 Bryan 出現「群聊場合的壓縮例外語言」時立即修

**修法**:
1. 在 `chrono_payload` schema 加兩個 flag:
   - `private_context: bool` — 是否為 Bryan 與 Ram 獨處(非群聊)
   - `pause_event: bool` — 是否觸發「讓她停頓很久的事」(例如用戶長時間沉默後的精準回應)
2. `AgentRam._value_judgment` 對 Bryan 路徑改成 AND 判斷:
   ```python
   if speaker == "bryan":
       if (intimacy >= 80
           and cp.get("private_context", False)
           and cp.get("pause_event", False)):
           return "worth_it"  # 第二例外觸發
   ```
3. 對應的 `chrono_payload` 注入邏輯:HeartbeatEngine 或 LLMProxy 需要根據 session 狀態填入這兩個 flag
4. 加 integration test:模擬群聊 vs 獨處 × intimacy 80+ 兩種組合,確認只有 (獨處 AND intimacy 80+) 才觸發

**估算**: 1-2 小時(含 chrono 規格定義)

**部分修復紀錄** (commit `71dc11a`):
已實作 `private_context` 判定(`mode == "private" AND target_agent == "agent_ram"`),
**群聊防呆測試通過**: intimacy=99 + `private_context=False` 在 3 種群聊變體下
都絕對不會觸發 `bryan_second_exception`(Task D ⭐ 群聊防呆測試)。

**未完成部分**: `pause_event`(「讓她停頓很久的事」觸發訊號)目前 **hardcode 為 False**,
尚無可靠的深層情感訊號來源可用(非簡單關鍵詞匹配可解決,類似 Akane 的
windowed scoring signal extraction 機制需要另立子任務評估是否可複用或需
重新設計)。

**實際效果**: 因為三條件是嚴格 AND,其中 `pause_event` 恆為 False,
`bryan_second_exception` 判定目前實質上**永遠不會被觸發**。
此為刻意的保守設計 — 假陽性風險 > 功能缺失風險。

**關閉條件**: 需設計並驗證 `pause_event` 的可靠訊號來源後,狀態才能轉為「已修」。
- 訊號抽取需獨立子任務評估(可行性 + 既有 signal extraction 機制是否可複用)
- 設計完成後需新增單元測試覆蓋正面/反面案例,並跑過 Ram 5-agent 整合 regression
- 完成後 commit 時同時更新本節與狀態欄位

**驗證**: `hermes-verify-ki004-second-exception-guard.py` 8/8 PASS
- A. 三條件全滿足 → `bryan_second_exception`
- B. 群聊 (缺 `private_context`) → `worth_it`
- C. `pause_event=False` → `worth_it`
- D. ⭐ 群聊防呆 intimacy=99 → 絕不觸發 (3 種群聊變體)
- E. intimacy<70 → `not_worth_it`
- F. Roswaal 例外 (regression)
- G. Threat `not_acceptable` (regression)
- H. 5-agent Ram integration (regression)

**關聯 commit**: `8913498` (Ram 整合) → `71dc11a` (部分修復落地)

---

## Backlog 維護約定

- **編號嚴格單調遞增** — 不要複用 KI-NNN,刪除 KI 時保留 entry 加 `**狀態**: 已棄用` 而非整段刪
- **每個 commit 若新增技術債,必同步新增 KI** — 避免技術債只在 commit message 裡看到,後人難以查找
- **每個 KI 必填**:狀態 / 優先級 / 發現日期 / 描述 / 影響 / 觸發 / 修法 / 估算 / 關聯 commit,缺項可填 TBD 但不能省略整個欄位
- **優先級定義**:
  - **P0** — 阻擋上線 / 已有資料污染風險 / 安全漏洞
  - **P1** — 影響 production behavior 但有 workaround
  - **P2** — 未來可能角色崩壞或使用者體驗破壞,需在特定 milestone 前修
  - **P3** — Polishing / 觀測性 / 分析能力,延後處理

---

**最後更新**: 2026-06-30 (KI-001 → 已修,KI-002 → 已修,KI-004 → 部分修復) · **維護者**: Bryan + MiniMax M3
