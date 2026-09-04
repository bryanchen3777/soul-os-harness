# 🏛️ Soul OS 工單：SI-3 Phase 2 — 雙大腦協同：感知聚合器與社交機會接入 Middleware 與 SM-4 決策管線

**工單編號**：TICKET-SI-3-PHASE-2 (Dual-Brain Edition)  
**發起大腦**：Antigravity (當前輪值主大腦)  
**協同大腦**：DSH (當前輪值執行大腦 / DeepSeek v4 Flash)  
**發起日期**：2026-09-03  
**上游合約**：[`docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md`](../docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md)  
**基線 Commit**：`29af670` (SI-3 Phase 1 closeout verified)

---

## 🤝 零、 雙大腦協同背景對齊

- **Phase 1 成果**：`SocialOpportunity`（300s TTL）與 `SocialPerceptionAggregator`（緊湊感知狀態）已完備且 100% 測試綠燈。
- **操作教訓落實**：跑測試永遠指定精確檔案路徑（使用 `.\.venv\Scripts\python.exe -m pytest <path> -v`），不走全域收集以避免檔案遍歷鎖。
- **Phase 2 戰略任務**：將 Phase 1 的靜態資料結構真正「通電」接入 Soul OS 運行迴圈：
  1. **讀側感知**：用 `CompactSocialState` 替代原本的 raw event feed，使長程客廳感知固定在 ≤150 tokens。
  2. **意志決策**：讓帶有 TTL 的 `SocialOpportunity` 能作為 Motive 候選，在靈魂決策時 Lazy 注入，徹底貫徹「**Volition-before-Arbitration**」。

---

## 🎯 一、 目標與範圍

### 目標
在不破壞既有 Frozen Contract 的前提下，以最小 additive 方式完成管線接線：
1. `src/world/middleware.py`：整合 `SocialPerceptionAggregator`，將 `_render_social_context` 升級為緊湊狀態渲染。
2. `src/soul/decision.py`：`build_decision_prompt` 新增可選參數 `social_context: Optional[str] = None`（向後兼容）。
3. `src/soul/motive.py`：新增 `motive_from_social_opportunity` 轉換函數，將短暫社交機會轉為合法的 Motive 念頭。
4. `tests/test_si3_phase2_integration.py`：驗證全管線串接、Token 預算受控與留白常態。

### 範圍
- **修改檔案**：
  - `src/world/middleware.py`
  - `src/soul/decision.py`
  - `src/soul/motive.py`
- **新增檔案**：
  - `tests/test_si3_phase2_integration.py`
- **工具執行**：
  - `scripts/update_notion_status.py`

---

## 📋 二、 具體實作步驟（決策已定，照章執行）

### 步驟 1：同步 Notion 團隊看板
執行：
```bash
.\.venv\Scripts\python.exe scripts/update_notion_status.py "[雙大腦協同] DSH 接單啟動 SI-3 Phase 2：感知聚合器與 SM-4 決策管線接線"
```
確認回傳 `SUCCESS`。

### 步驟 2：`src/world/middleware.py` 感知管線接線
1. 導入：
   ```python
   from src.social.aggregator import SocialPerceptionAggregator
   ```
2. 在 `WorldPerceptionMiddleware.__init__` 中新增：
   ```python
   self._social_aggregators: Dict[str, SocialPerceptionAggregator] = {}
   ```
3. 新增私有輔助方法：
   ```python
   def _get_social_aggregator(self, agent_id: str) -> SocialPerceptionAggregator:
       if agent_id not in self._social_aggregators:
           self._social_aggregators[agent_id] = SocialPerceptionAggregator(current_agent_id=agent_id)
       return self._social_aggregators[agent_id]
   ```
4. 升級 `_render_social_context`：
   - 當 `social_events` 存在時，依序對當前 agent 的 aggregator 調用 `update_from_event(ev, now)`。
   - 調用 `state = aggregator.get_compact_state(agent_id, now)`。
   - 調用 `block = aggregator.render_compact_prompt_block(agent_id, state)`。
   - 返回 `block`。若 `block` 為空則回傳 `""`（自然留白）。
   - 保留既有 trace 記錄（`WorldPerceptionTrace`），維持可觀測性。

### 步驟 3：`src/soul/decision.py` 決策 Prompt 接線
1. 在 `build_decision_prompt` 函數簽名中追加可選參數：
   ```python
   social_context: Optional[str] = None,
   ```
   （放在 `temporal_anchor: Optional[str] = None` 之後，預設 `None` 確保向後兼容）。
2. 在 `build_decision_prompt` 的「3. Relevant context」區塊中：
   ```python
   if social_context:
       context_lines.append(social_context)
   ```
   （只進 Relevant context，不進 Framing 與 Boundary，嚴守 SM-2 契約）。

### 步驟 4：`src/soul/motive.py` 機會轉動機輔助
1. 實作純函數（不進 class，不依賴全局狀態）：
   ```python
   def motive_from_social_opportunity(
       opp: Any, # SocialOpportunity
       soul_name: str = "",
   ) -> Motive:
       """
       SI-3 Phase 2: 將有效的 SocialOpportunity 轉換為 Motive 候選 (SM-1/SM-3 兼容)。
       嚴格維持 Motive 5 字段凍結 (motive_id, content, target, provenance_ref, created_at)。
       """
       content = f"關於 {opp.actor_id} 在客廳提到的話題「{opp.topic}」"
       return Motive(
           motive_id=f"mot_{uuid.uuid4().hex[:12]}",
           content=content,
           target=TARGET_BRYAN,
           provenance_ref=f"opp:{opp.opportunity_id}",
           created_at=opp.created_at,
       )
   ```

### 步驟 5：編寫集成測試 `tests/test_si3_phase2_integration.py`
測試必須包含以下 4 個場景：
1. `test_middleware_compact_social_state_rendering()`: 驗證 `WorldPerceptionMiddleware` 處理 `SocialWorldEvent` 後，渲染出的 `social_block` 確實是 Compact Social State 格式，包含反框架提示語，且估算 token ≤ 150。
2. `test_middleware_social_opportunity_ttl_expiry_rendering()`: 驗證當機會過期且客廳無他人時，`social_block` 自動返回空字串 `""`，保持留白。
3. `test_social_opportunity_to_motive_decision_prompt()`: 驗證 `motive_from_social_opportunity` 產出的 Motive 能順利傳入 `build_decision_prompt`，且渲染出的 Prompt 包含四元決策選項（`transmit/observe/reflect/do_nothing`）與客廳背景情境。
4. `test_zero_cascading_volition_invariant()`: 驗證外部社交事件輸入後，絕不會自發修改 agent 的任何自傳檔案或記憶庫，僅存於記憶體緩存中。

---

## 🔒 三、 Frozen Contract 邊界（絕對紅線）

1. **No Cascading Volition**：社交感知與機會注入絕不自動賦予 `transmit`，決策依然交由 SM-4 四元單選。
2. **0 Frozen Contract 污染**：
   - 不修改 `InnerLifeEvent` 的 dataclass 欄位。
   - 不修改 `TriggerEnvelope`。
   - 不修改 `Agency 4 stages` 核心生命週期。
   - 不建額外 scoring/vector subsystem。

---

## 📊 四、 驗收標準與測試命令

請執行精確路徑測試（永遠指定明確路徑）：
```bash
.\.venv\Scripts\python.exe -m pytest tests/test_si3_phase2_integration.py tests/test_social_opportunity.py tests/test_tl6_social_harness.py -v
```
驗收條件：
1. 新增測試與既有測試全部 PASS。
2. 耗時在合理範圍內（< 60s）。
3. Notion 狀態成功更新。

---

## 📝 五、 回報格式

完成後，請 DSH 回報：
1. 改動與新增檔案清單
2. 測試執行結果（測試筆數、耗時）
3. 是否遵守 0 Frozen Contract 改動
4. Notion 更新狀態
