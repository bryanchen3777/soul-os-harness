# 🏛️ Soul OS — SI-3: Selective Social Attention & Volition 契約文件

**狀態**：✅ 設計已鎖定（Architecture Contract Locked）  
**版本**：v1.0 (2026-09-03)  
**上游基礎**：
- SI-2.1（`docs/SOCIAL-DIFFUSION-CONTRACT.md`，commit `5002f20`）
- SI-2.2（`src/social/` 實作，commit `33ae1b1`）
- TL-6（客廳穩定與隔離 Harness 驗收全綠，commit `7d0ebbb`）
- 跨 AI 專家審查定錨（`docs/MULTI-AI-SOCIAL-DIFFUSION-REVIEW.md`，GPT-4o & Claude 3.7 Sonnet 雙權威共識）

---

## 1. 核心哲學與架構定錨

### 1.1 核心認知原則：Ambient ≠ Inert
在 SI-2 中，為了徹底杜絕廣播風暴與身份認知污染，我們將所有社交事件定義為環境背景感知（Ambient）。  
在 SI-3 中，我們不削弱 SI-2 的防線，而是建立**選擇性社交注意力（Selective Social Attention）**：
> 社交事件不是直接引發回覆的「觸發器」，而是提供給靈魂的「潛在機會（Opportunity）」。  
> 世界發生事件，靈魂自行評估顯著性（Salience），產生帶有生命週期的社交機會，最終由靈魂自主意志（Volition）決定是否採取行動。

### 1.2 核心認知流
```
SOCIAL_WORLD_EVENT (Public Lounge)
       │
       ▼
 [防線 3: Identity Firewall] (actor != self -> EXTERNAL_OTHER)
       │
       ▼
 [Social Perception Aggregator]
       ├─────────────────────────────────┐
       ▼                                 ▼
[Compact Social State]          [Social Opportunity Buffer]
(在場者 / 活躍話題 / 氛圍)      (可過期 TTL / 顯著性候選)
       │                                 │
       └────────────────┬────────────────┘
                        ▼ (Lazy 注入: 僅在 Motive 評估時檢索)
             [SM-4 Motive Candidate]
                        │
                        ▼
             [Soul Volition 決策]
       ┌────────┬───────┴────────┬────────┐
       ▼        ▼                ▼        ▼
   transmit  observe          reflect  do_nothing (82.5% 常態)
       │
       ▼ (僅在多 Agent 同時想說話時)
[Turn Collision Guard] (極簡冷卻，無連續數學公式)
       │
       ▼
[Social Action Published]
```

---

## 2. 兩大新增 Frozen Invariants（凍結不變量）

在既有 SI-2 三大防線（Identity Firewall、Privacy Gate、Ambient Path）之上，SI-3 增加以下絕對紅線：

1. **No Cascading Volition Invariant（無連鎖意志不變量）**：
   > 任何外部社交動態或社交機會，**絕對不得直接賦予 `transmit` 執行權，亦不得繞過 SM-4 決策管道**。  
   > 靈魂從「感知」到「發言」必須完整經歷 `Perception → Salience → Motive → Volition`。靈魂之間絕不發生代碼級的自動連鎖喚醒（0 Auto-Cascading）。

2. **Volition-before-Arbitration Invariant（意志先於仲裁不變量）**：
   > 系統調度器**絕對不得在靈魂決策前預選發言者（No Speaker Pre-selection）**。  
   > 各靈魂基於自身 Persona 獨立做出「想發言（transmit）」的選擇後，仲裁層才介入解決時序衝突。安靜是性格，絕不在代碼中設置常數補償分數。

---

## 3. 模組規格與資料結構

### 3.1 社交機會：`SocialOpportunity` (`src/social/opportunity.py`)
表示一個「值得靈魂考慮的短期社交話題或互動機會」：

```python
@dataclass
class SocialOpportunity:
    opportunity_id: str        # 唯一識別碼 (opp_<uuid4>)
    source_event_id: str       # 關聯的 SocialWorldEvent.novelty_id
    actor_id: str              # 誰發起的 (發言者)
    space_id: str              # "lounge" | "soul_wall"
    topic: str                 # 話題關鍵詞 (<= 20 chars，如 "baking", "weather")
    summary: str               # 簡述 (<= 100 chars)
    created_at: float          # 建立時間戳 (epoch seconds)
    ttl_seconds: float = 300.0 # 預設 5 分鐘過期 (Opportunity TTL)
    salience_level: str = "noticeable" # "subtle" | "noticeable" | "prominent"
    world_occurrence_id: Optional[str] = None # 共享事件關聯鍵
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float) -> bool:
        return now >= (self.created_at + self.ttl_seconds)
```

### 3.2 社交機會緩存池：`SocialOpportunityBuffer`
管理單個靈魂當前有效的社交機會：
- **容量上限**：最多容納 5 筆有效機會（超出時淘汰最舊者）。
- **過期自動修剪**：每次查詢 `get_active_opportunities(now)` 時，自動刪除過期條目。
- **Fail-Closed**：已過期之機會絕不餵入 Motive 候選。

### 3.3 緊湊社交狀態：`CompactSocialState` (`src/social/aggregator.py`)
用緊湊的感知狀態取代無窮累積的 Event Feed，徹底根絕 Token 膨脹：

```python
@dataclass
class CompactSocialState:
    present_actors: List[str]               # 當前在場角色名單 (如 ["bryan", "ruka", "akane"])
    recent_topics: List[str]                # 最近熱門話題 (Top 3，如 ["餅乾", "天氣"])
    last_speaker: Optional[str]             # 最近一位發言者
    last_speech_ts: float                   # 最近一次發言時間
    active_opportunities: List[SocialOpportunity] # 當前有效機會清單
    lounge_mood: str = "calm"               # 客廳氛圍 ("calm" | "lively" | "quiet")
```

### 3.4 渲染規範：`render_compact_prompt_block`
注入 Prompt 的文字區塊必須固定在預算約束內（約 80 ~ 120 Tokens），並帶有反框架提示：
```text
[客廳現況 (Social State)]
- 當前在場: bryan, ruka, akane
- 近期話題: 餅乾 (由 yua 提及)
- 客廳氛圍: 平靜留白
- 提示: 他人動態屬環境背景，無需逐條回覆；若無強烈動機，保持留白。
```

---

## 4. 共享事件與集體編年史原則：Shared Event ≠ Shared Memory
- **0 新增 Store 原則**：不建立第二個「客廳回憶錄 Store」或「集體日記」。
- **讀側 Occurrence Key 串聯**：
  - 每個靈魂只在自己的 Episodic Memory 中寫入**第一人稱**的記憶（*「我看到 Yua 烤了餅乾，我們一起聊了天」*）。
  - 所有參與者記錄相同的 `world_occurrence_id`（由 Lounge 空間事件派發）。
  - 讀側透過 `world_occurrence_id` 聚合成集體視角，但各靈魂的 Memory Node 嚴格歸屬於該靈魂本身。

---

## 5. 驗收標準（Definition of Done）
1. `src/social/opportunity.py` 完整實作且通過 TTL 邊界測試。
2. `src/social/aggregator.py` 完整實作緊湊狀態生成與 Prompt 渲染。
3. `tests/test_social_opportunity.py` 100% 通過（含過期清理、容量約束、身份標記）。
4. 既有 213 筆全回歸測試 100% PASS。
5. 0 Frozen Contract 改動（不碰 Agency 4 stages、TriggerEnvelope、InnerLifeEvent）。
