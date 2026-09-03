# 🏛️ Soul OS 工單：SI-3 Phase 1 — 雙大腦協同：社交機會 (Opportunity) 與緊湊狀態 (Aggregator) 實作

**工單編號**：TICKET-SI-3-PHASE-1 (Dual-Brain Edition)  
**發起大腦**：Antigravity (當前輪值主大腦)  
**協同大腦**：DSH (當前輪值執行大腦 / DeepSeek v4 Flash)  
**發起日期**：2026-09-03  
**上游合約**：[`docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md`](../docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md)  
**當前本地 HEAD**：`5fe23f5`（領先 origin/main 3 個 commit）

---

## 🤝 零、 雙大腦協同原則與背景交代（Peer Transparency）

依據 Owner (Bryan) 的戰略指示：
> **Soul OS 實施「雙大腦制度」**。LLM 的 Token 與配額並非無限，兩位大腦（Antigravity 與 DSH）隨時具備無縫互換、相互備援的能力。  
> 彼此以**平等、透明、互相尊重**的方式協作。無論誰擔任當前的架構主大腦，都必須將事實、背景、決策推導交代得清清楚楚。

### 0.1 關於本地領先 origin/main 的 3 個 Commit 交代
DSH 先前的審計查證完全正確，值得肯定。此處主動交代這 3 個 commit 的完整脈絡：

1. **`7d0ebbb`（feat: multi-agent social lounge stability harness (TL-6)）**：
   - 目的：驗收 SI-2 多 Agent 社交廣播與客廳共處架構，補足先前 Candidate 1 的驗證缺口。
   - 實作內容：`harness/tl6.py`（580 行）、`harness/run_tl6.py`、`tests/test_tl6_social_harness.py`。
   - 驗收結果：7 階段情境劇本 × 3 次系列 Run，四大不變量全過（Anti-Storm 100%、Identity Quarantine 100%、Privacy Gate 100%、Ambient Salience PASS），**全系統 213 筆回歸測試全綠（36.85s）**，未變動任何既有業務代碼。
2. **`cd0ea42`（docs: register TL-6 commit hash in canonical engineering state）**：
   - 目的：對齊單一事實來源 [`logs/ENGINEERING_STATE.md`](ENGINEERING_STATE.md)，將 TL-6 狀態由待 commit 更新為 `7d0ebbb`。
3. **`5fe23f5`（docs: lock SI-3 selective social attention contract and dispatch DSH work order）**：
   - 目的：將外部 AI 專家評審結論固化為 SI-3 架構契約，並建立 Notion 自動更新腳本。
4. **為什麼先前未 push 到遠端**：
   - 遵循「本地安全性與 Owner 確認優先」紀律，跨網路的遠端 push 需經 Owner 或協同大腦確認對齊後再統一推播，絕非隱瞞。

---

## 💎 一、 外部頂級 AI（GPT-4o × Claude 3.7 Sonnet）同行評審共識

在制定 SI-3 契約前，我們將架構諮詢文件（`docs/MULTI-AI-SOCIAL-DIFFUSION-REVIEW.md`）提交外部專家評審，兩者給出了高度一致的結論：

1. **拒絕連續公式與數學評分**：不做 Social Friction Decay、不做 Activation Energy、不做代幣經濟（避免重蹈 scoring engine 複雜度陷阱）。
2. **拒絕向量檢索**：**堅決不引入 Vector DB / Embedding**，保持輕量高效。
3. **社交機會與生命週期（Opportunity TTL）**：
   - 社交動態不直接引發發言（Ambient ≠ Inert），而是產生帶有 TTL（如 5 分鐘）的 `SocialOpportunity`。
   - 過期自然修剪，避免「昨天別人聊餅乾，今天突然回覆」的非人僵硬行為。
4. **緊湊狀態感知（Compact Social State）**：
   - `Events are history; social state is perception`。
   - 用「誰在場 + 最近活躍話題 + 客廳氛圍」的緊湊物件取代無窮無盡的 Event Feed，徹底固定 Token 預算。
5. **意志先於仲裁（Volition before Arbitration）**：
   - 靈魂先自主決定想不想說（transmit），碰撞時才用極簡 cooldown 仲裁。安靜是性格不是缺陷，代碼中絕不給安靜角色補償常數。

---

## 🎯 二、 工單目標與範圍

### 目標
落實 SI-3 第一階段核心感知層：建立帶有 TTL 的社交機會結構與緊湊狀態聚合器，完成單元測試，並同步更新 Notion 團隊看板。

### 範圍
- **新增檔案**：
  - `src/social/opportunity.py`
  - `src/social/aggregator.py`
  - `tests/test_social_opportunity.py`
- **修改檔案**：
  - `src/social/__init__.py`（導出新類別）
- **工具執行**：
  - `scripts/update_notion_status.py`

---

## 📋 三、 具體實作步驟（決策已定，照章執行）

### 步驟 1：遠端同步 (Git Push)
- 雙大腦完成對齊，確認 3 個 commit 均為合法且經過 213 筆測試驗證之資產。
- 執行：
  ```bash
  git push origin main
  ```
  確保本地與遠端 `origin/main` 統一收斂至 `5fe23f5`。

### 步驟 2：同步 Notion 團隊看板
- 執行專案腳本更新團隊進展看板：
  ```bash
  python scripts/update_notion_status.py "[雙大腦協同] DSH 接單啟動 SI-3 Phase 1：Opportunity TTL 與 Compact Aggregator 實作"
  ```
  確認回傳 `SUCCESS: Notion block created`。

### 步驟 3：實作 `src/social/opportunity.py`
依據合約第 3.1 & 3.2 節實作：
1. **`SocialOpportunity` dataclass**：
   ```python
   @dataclass
   class SocialOpportunity:
       opportunity_id: str        # 格式 "opp_<uuid4_hex[:12]>"
       source_event_id: str       # 關聯之 SocialWorldEvent.novelty_id
       actor_id: str              # 行為主體
       space_id: str              # "lounge" | "soul_wall"
       topic: str                 # 話題關鍵字 (<= 20 chars)
       summary: str               # 摘要 (<= 100 chars)
       created_at: float          # epoch timestamp
       ttl_seconds: float = 300.0 # 預設 300 秒 (5 分鐘)
       salience_level: str = "noticeable" # "subtle" | "noticeable" | "prominent"
       world_occurrence_id: Optional[str] = None # 共享事件關聯 ID
       metadata: Dict[str, Any] = field(default_factory=dict)

       def is_expired(self, now: float) -> bool:
           return now >= (self.created_at + self.ttl_seconds)
   ```
2. **`SocialOpportunityBuffer` class**：
   - `__init__(self, max_capacity: int = 5)`：預設最多留存 5 筆。
   - `add_opportunity(self, opp: SocialOpportunity) -> None`：同 ID 更新；超額時淘汰 `created_at` 最舊的項目。
   - `get_active_opportunities(self, now: float) -> List[SocialOpportunity]`：自動過濾並修剪已過期項目，回傳有效清單。
   - `clear(self) -> None`：重置緩存。

### 步驟 4：實作 `src/social/aggregator.py`
依據合約第 3.3 & 3.4 節實作：
1. **`CompactSocialState` dataclass**：
   - 欄位：`present_actors: List[str]`, `recent_topics: List[str]`, `last_speaker: Optional[str]`, `last_speech_ts: float`, `active_opportunities: List[SocialOpportunity]`, `lounge_mood: str = "calm"`。
2. **`SocialPerceptionAggregator` class**：
   - `update_from_event(self, event: SocialWorldEvent, now: float) -> Optional[SocialOpportunity]`：
     - 維護最近在場者名單（`present_actors`）與最近發言者資訊。
     - 若 `event.content` 包含有效交流，提取簡明 topic 並生成 `SocialOpportunity` 放入 buffer。
   - `get_compact_state(self, agent_id: str, now: float) -> CompactSocialState`：
     - 回傳給該 agent 的當前緊湊客廳感知狀態。
   - `render_compact_prompt_block(self, agent_id: str, state: CompactSocialState) -> str`：
     - 輸出固定預算之 Prompt 區塊（約 80 ~ 120 Tokens）。
     - **必須包含反框架警示語**：
       `「他人動態屬環境背景，無需逐條回覆；若無強烈動機，保持留白。」`
     - 若無在場他人且無活躍話題，回傳空字串 `""`。

### 步驟 5：更新導出 `src/social/__init__.py`
- 導出：`SocialOpportunity`, `SocialOpportunityBuffer`, `CompactSocialState`, `SocialPerceptionAggregator`。

### 步驟 6：編寫測試 `tests/test_social_opportunity.py`
至少覆蓋以下場景：
1. `test_opportunity_lifecycle_and_expiry`：驗證未過期、邊界與過期（`is_expired`）。
2. `test_buffer_pruning_and_fifo_capacity`：驗證過期自動剔除，以及達 5 筆上限時淘汰最舊者。
3. `test_aggregator_compact_state_and_rendering`：驗證 CompactSocialState 的聚合併檢查 Prompt 區塊反框架警示與 Token 緊湊性。
4. `test_identity_quarantine_invariant`：外部 actor 的 opportunity 只作為背景提示，不修改任何自身記憶檔案。

---

## 🔒 四、 Frozen Contract 與邊界防守（絕對不變量）

1. **No Cascading Volition**：社交感知或社交機會**絕不得繞過 SM-4 直接觸發發言**。
2. **Volition-before-Arbitration**：不建代碼級人格常數補償，誰想說話由角色意志自主產生。
3. **No Vector DB**：嚴禁引入向量資料庫、語意嵌入模型或額外大型外部依賴。
4. **Frozen Core**：`Agency 4 stages`、`TriggerEnvelope`、`InnerLifeEvent`、`SAGE 寫入` 嚴格禁止修改。

---

## 📊 五、 驗收標準與測試命令

```bash
# 1. 執行新寫的單元測試
pytest tests/test_social_opportunity.py -v

# 2. 驗證不破壞既有客廳情境穩定性 Harness
pytest tests/test_tl6_social_harness.py -v

# 3. 跑全系統快速回歸測試（213+ 測試確保全綠）
pytest tests/ -k "not live and not real"
```

---

## 📝 六、 回報格式

完成後，請 DSH 回報：
1. 執行的 Git push 結果與 Commit hash
2. 改動/新增檔案清單
3. 測試通過狀況（測試筆數、有無失敗、執行耗時）
4. Notion 狀態更新之 Block ID
5. 是否踩到任何邊界問題或需要主大腦進一步決策之處
