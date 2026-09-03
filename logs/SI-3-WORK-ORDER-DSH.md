# 工單：SI-3 第一階段 — 社交機會 (Opportunity) 與緊湊狀態 (Aggregator) 實作

**工單編號**：TICKET-SI-3-PHASE-1  
**派發者**：Soul OS 主大腦 (Master Planner)  
**執行者**：DSH Assistant (DeepSeek v4 Flash)  
**指派日期**：2026-09-03  
**上游合約**：[`docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md`](../docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md)  
**基線 Commit**：`cd0ea42` (TL-6 closed + Canonical Registry Aligned)

---

## 0. 執行者前置現狀對齊 (Context Onboarding)

在開始編寫代碼前，請 DSH 先閱讀並理解以下背景：
1. **單一事實來源**：參照 [`logs/ENGINEERING_STATE.md`](ENGINEERING_STATE.md)，TL-6（Social Lounge Stability Harness）已於 commit `7d0ebbb` 落地驗收，全系統 213 筆回歸測試全綠。
2. **跨 AI 評審共識**：參照 [`docs/MULTI-AI-SOCIAL-DIFFUSION-REVIEW.md`](../docs/MULTI-AI-SOCIAL-DIFFUSION-REVIEW.md)，GPT-4o 與 Claude 3.7 Sonnet 達成高度共識——**不做向量資料庫、不做連續公式、以帶有 TTL 的 Social Opportunity 與 Compact Social State 取代長程 Event Feed**。
3. **本工單定位**：實作純資料結構與感知聚合邏輯（零外部依賴、純 Python/Asyncio、0 Frozen Contract 改動）。

---

## 1. 目標

實作 SI-3 的兩大核心模組：
1. `src/social/opportunity.py`：可過期的社交機會（`SocialOpportunity`）與緩存池（`SocialOpportunityBuffer`）。
2. `src/social/aggregator.py`：緊湊社交感知狀態（`CompactSocialState`）與 Prompt 渲染器（`SocialPerceptionAggregator`）。
3. 執行 Notion 同步腳本，將工作進度同步至 Notion 團隊看板。

---

## 2. 範圍

### 需新增 / 修改的檔案：
- `src/social/opportunity.py` [NEW]
- `src/social/aggregator.py` [NEW]
- `src/social/__init__.py` [MODIFY]：導出新增的類別
- `tests/test_social_opportunity.py` [NEW]
- `scripts/update_notion_status.py` [RUN / SYNC]

---

## 3. 做法（決策已定，執行者照做）

### 步驟 1：同步 Notion 團隊看板
- 執行專案提供的同步腳本：
  ```bash
  python scripts/update_notion_status.py "[DSH 接單] 開始執行 SI-3 第一階段：Social Opportunity 與 Compact Aggregator 實作"
  ```
  確保回傳 `SUCCESS: Notion block created`。

### 步驟 2：實作 `src/social/opportunity.py`
照以下決策實作：
1. **`SocialOpportunity` dataclass**：
   - 欄位：`opportunity_id: str`, `source_event_id: str`, `actor_id: str`, `space_id: str`, `topic: str`, `summary: str`, `created_at: float`, `ttl_seconds: float = 300.0`, `salience_level: str = "noticeable"`, `world_occurrence_id: Optional[str] = None`, `metadata: Dict[str, Any] = field(default_factory=dict)`。
   - 方法 `is_expired(now: float) -> bool`：若 `now >= (created_at + ttl_seconds)` 則回傳 `True`。
2. **`SocialOpportunityBuffer` class**：
   - 預設 `max_capacity: int = 5`。
   - `add_opportunity(opp: SocialOpportunity) -> None`：若同 `opportunity_id` 已存在則更新；若超出容量則移除最舊的項目。
   - `get_active_opportunities(now: float) -> List[SocialOpportunity]`：自動過濾並修剪已過期的項目，回傳有效清單。
   - `clear() -> None`：清空緩存。

### 步驟 3：實作 `src/social/aggregator.py`
照以下決策實作：
1. **`CompactSocialState` dataclass**：
   - 欄位：`present_actors: List[str]`, `recent_topics: List[str]`, `last_speaker: Optional[str]`, `last_speech_ts: float`, `active_opportunities: List[SocialOpportunity]`, `lounge_mood: str = "calm"`。
2. **`SocialPerceptionAggregator` class**：
   - `update_from_event(event: SocialWorldEvent, now: float) -> Optional[SocialOpportunity]`：
     - 更新在場角色清單（`present_actors`）、最近發言者與時間。
     - 若事件具備話題（`event.content`），提取簡要 topic 並生成一筆 `SocialOpportunity` 加入 buffer。
   - `get_compact_state(agent_id: str, now: float) -> CompactSocialState`：回傳針對該角色的緊湊感知狀態。
   - `render_compact_prompt_block(agent_id: str, state: CompactSocialState) -> str`：
     - 輸出固定預算之 Prompt 區塊（不可超過 150 tokens）。
     - 必須包含反框架提示語：`「他人動態屬環境背景，無需逐條回覆；若無強烈動機，保持留白。」`。
     - 若無在場他人且無活躍話題，回傳空字串 `""`。

### 步驟 4：更新導出 `src/social/__init__.py`
- 將 `SocialOpportunity`、`SocialOpportunityBuffer`、`CompactSocialState`、`SocialPerceptionAggregator` 加至 `__all__`。

### 步驟 5：編寫單元測試 `tests/test_social_opportunity.py`
包含以下測試案例：
1. `test_opportunity_lifecycle_and_expiry()`: 驗證 `created_at + ttl_seconds` 前後狀態切換。
2. `test_buffer_auto_pruning_and_capacity()`: 驗證 buffer 在超過 5 筆時自動剔除最舊，以及過期自動 prune。
3. `test_aggregator_compact_state_and_rendering()`: 驗證事件輸入後狀態聚合正確，Prompt 渲染包含反框架警語且長度受控。
4. `test_identity_quarantine_invariant()`: 驗證外部 actor 的 opportunity 不會修改 `current_agent_id` 的自傳狀態。

---

## 4. 驗收（完成的定義）

1. `tests/test_social_opportunity.py` 新增測試 100% 通過。
2. 現有全套回歸測試（含 `tests/test_tl6_social_harness.py`）維持 100% 通過。
3. 0 引入外部未經授權之函式庫（如禁止引入向量檢索、chromadb 等）。
4. Notion 狀態成功更新。

---

## 5. 測試執行命令

```bash
# 1. 跑新寫的測試
pytest tests/test_social_opportunity.py -v

# 2. 跑社交與客廳回歸測試
pytest tests/test_tl6_social_harness.py tests/test_social_diffusion.py -v

# 3. 跑全系統快速回歸
pytest tests/ -k "not live and not real"
```

---

## 6. 不做（Out of Scope）

- **禁止引入向量資料庫 (No Vector DB / Embedding)**：嚴格依據跨 AI 評審共識，維持緊湊狀態與文字比對。
- **禁止修改 SM-4 與 Decision LLM 的核心程式碼**：此階段只做社交感知層與機會層，不碰決策排程器。
- **禁止修改 Frozen Contract**：Agency 4 stages、TriggerEnvelope、InnerLifeEvent 等嚴禁任何改動。

---

## 7. Frozen Contract 注意

- 參照 [`logs/ENGINEERING_STATE.md`](ENGINEERING_STATE.md) 的 Frozen Contract 條款。
- `actor_id != self` 的事件只得進入 Ambient 感知與 Opportunity Buffer，**嚴禁進入 Episodic Memory 或 SAGE Graph**。

---

## 8. 回報格式

完成後請在回報中提供：
1. 改動與新增檔案清單
2. 測試結果（測試名稱、通過數量、耗時）
3. Notion 更新之 Block ID 與執行截圖/日誌
4. 是否踩到任何 Frozen Contract 或意外邊界
