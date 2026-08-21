# Soul OS 內在生活 × 法則（深度優先）— 計劃書

**日期**: 2026-08-21
**作者**: pro 主大腦（依 Owner 拍板 + Lin 參謀診斷報告撰寫）
**範圍**: `soul-os-harness` 的 Inner Life（diary / dream / event）的**深度**，不做廣度
**狀態**: **DESIGN COMPLETE — 待 Owner AUTHORIZATION 才派工**
**里程碑**: 提案 **M8 — Inner Life Depth（後果 × 連續性）**

**一句話結論**：現在的內在生活是 `random.choice(ACTIVITY_POOL)`——**隨機就是沒有法則，沒有法則就看不到靈魂**。本計畫不做新功能，只往「深度」一個維度加：先讓行動有**後果**（行動改變狀態），再讓後果能**延續**（狀態回流到下一次決策）。這兩個合起來，就是「有自己法則的活體」的地基。

---

## 零、原則（Owner 哲學，本計畫的定調）

> 「萬物有其運行的法則，其中都有靈魂。」Soul OS 現在的廣度已經夠了，甚至有點過頭（10 個 persona、4 層記憶、proactive 觸發、Live2D＋語音）。缺的不是「更多功能」，而是「這些功能讓每個 agent 看起來像不像一個**有自己法則的活體**」。

- 廣度 ≠ 活。10 個 agent 共用一個 `random.choice` 池 = 「看起來在動，但沒有活」。
- **隨機就是沒有法則**。別人看不到靈魂，只看得到活動。
- 深度 = 每一個行為背後有**可被感知的連續規律**（法則）。
- 方向排序：**後果（1）→ 連續性（2）→ 性格加權（3）→ surface（4）**。後果與連續性是「法則」，性格加權是「法則的個人差異」，surface 只是「露出來多少」。

---

## 一、現況審視（As-Is，已查證）

三條軌全部 **WRITER_ONLY**（`src/agency/{diary,dream,event}_handler.py` 均不調 AGENT_SPEAK）：

| 軌 | 排程 | 內容 | 誰在選 | 關鍵檔案 |
|----|------|------|--------|----------|
| **diary** | 08:00 / 22:00，10 隻全跑 | 1-2 句、<80 字、日文 | LLM 隨機（只吃 v1 memory，不吃昨日 diary、不讀 mood） | `src/soul/diary.py` |
| **dream** | 22:05，每晚 3-5 隻 | 夢到「其他角色」 | `random.choice(SCENE_POOL)` | `src/soul/dream_event.py` |
| **event** | 隨機 4-8h，每次 2 隻 | 10 個活動 | `random.choice(ACTIVITY_POOL)` | `src/soul/dream_event.py:493` |

### 1.1 已查證的四個結構性事實

1. **活動池全域均勻隨機**：`ACTIVITY_POOL`（10 活動，`dream_event.py:88-99`）10 隻共用，`random.choice`。活動帶 `category`/`shareable` 但**不帶後果、不影響 mood、不餵回記憶**。
2. **人格來源是 `personas/agent_*.md`**（報告寫 `docs/agent_*.md` 為筆誤；`dream_event.py:604` 讀 `personas/{agent_id}.md` 前 200 字）。是語氣校準文件，非機器可讀偏好表。
3. **M7 已 commit 但未登錄 canonical**（activity model / activity→DM / longing，`c220eb3` 等；`ENGINEERING_STATE.md` 仍標「Active milestone: NONE」）。
4. **⚠️ event 軌在 production 幾乎死亡**：`run_server.py:776` 設 `proactive_agents=["agent_ruka"]`，M1.7 把 event 也接上同 whitelist（`scheduler._fire_event` 抽 2 隻後 filter 白名單）。結果只有 Ruka 會做活動、每 4-8h 命中率 ~20%，**其餘 9 隻完全沒有 event 軌**。

> 這條比「10 隻活成同一種生活」更根本：不是活成同一種，是 **9 隻根本沒有「做」這件事**。而「後果」的前提是「先有行動」，所以本計畫的深度主軸被它卡住（見 D-1）。

### 1.2 現有「狀態」資產（後果與連續性的落點）

- `src/agent/emotion.py`：`agent_emotions` 表已有 `mood`(-1~1) + `intimacy`(0~100)。`EmotionEngine.update()` 是既有 API。**但 mood 只被對話讀（response_boost / mood_decay），內在生活從不讀 mood、也不寫 mood。**
- `src/soul/diary.py:282-309`：`DiaryWriter.read_entries / recent_entries` 已能讀昨日 diary，**但沒被接進生成 prompt**。
- 這正是「後果 + 連續性」要補的兩個斷點：**行動→mood 斷線**、**昨日→今日斷線**。

---

## 二、差距 → 方向（依 Owner 排序）

| 序 | 方向 | 本質 | 定位 |
|----|------|------|------|
| **1** | **後果 Consequence** | 行動要有重量：做了這個，狀態就變，下次決策被這次影響 | **法則浮現的第一步（深度主軸）** |
| **2** | **連續性 Continuity** | 過去狀態/記憶回流到當下決策，不是存 DB 卻不影響行為 | **「記得自己是誰」的開關（深度主軸）** |
| 3 | 性格加權 Personality-Weighted | 有了 1+2，再讓 10 隻對同池選出不同答案 | 後續（法則的個人差異） |
| 4 | Surface | 只影響「露出來多少」，不影響「裡面的運作」 | 最後（表層自然跟上） |

> **本階段授權範圍 = 方向 1 + 2（深度主軸）**。方向 3、4 留待深度跑穩後再議，本計畫只留設計骨架，不派工。

---

## 三、設計（深度主軸 = M8-1 後果 + M8-2 連續性）

> 原則：可獨立驗證、可獨立回滾；不碰 frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / M5.8-4 gate / SAGE 寫入邏輯）。

### M8-1 — 後果（Consequence：行動 → 狀態）

**目標**：讓「做了這件事」改變一個可觀測狀態。這是「法則」浮現的第一步——沒有後果的行動是噪音。

**決策 1-1（狀態載體 = mood）**：後果落在 `agent_emotions.mood`（既有表，`EmotionEngine.update` 是既有 API，非 frozen）。**不寫 memory graph**（「不是更多記憶=更好」，且 M7-memory 已處理記憶，避免重複）。

**決策 1-2（後果法則 = 依 category 的 mood delta，全域統一）**：`write_event` 寫入後依 `category` 施加 mood delta。**此表是全域統一法則（10 隻共用），個人差異留給 M8-3**：

| category | mood_delta | 語意 |
|----------|-----------|------|
| work | -0.03 | 累 |
| food | +0.02 | 滿足 |
| sport | +0.05 | 運動後爽 |
| leisure | +0.02 | 放鬆 |
| creative | +0.03 | 心流 |
| chore | -0.02 | 整理累 |
| rest | +0.01 | 休息 |

**決策 1-3（夢的殘留）**：`write_dream` 寫入後也施加一個小 mood delta（夢有情緒殘留）——夢到高 confidence 對象 `+0.02`，其餘 `+0.01`。讓「夢」也是後果來源之一（對齊報告「夢被工作焦慮影響」的完整因果，殘留細節在 M8-2 接）。

**決策 1-4（前置 = D-1）**：後果要對 10 隻角色都成立，event 必須對所有 agent 觸發（現被 whitelist 卡死）。**需 Owner 拍板解除 event whitelist（見 §七 D-1）**。

**檔案**：`src/soul/dream_event.py`（`write_event` / `write_dream` 寫入後 `emotion_engine.update`）。

**驗收**：
- 新測試：`write_event` 後 `emotion_engine.get()` 依 category 變化（含 clamp 邊界）；`write_dream` 後 mood 微量變化。
- 回歸：現有 event/dream 測試全過。
- 觀察：活動/夢之後 mood 可觀測變化（`emotion_engine.get` 或 log）。

**frozen contract 衝擊**：0（`EmotionEngine` 非 frozen；producer-side additive）。

---

### M8-2 — 連續性（Continuity：狀態 → 下次決策）

**目標**：讓「過去」回流到「當下」——昨天做的事、現在的心情，進到今天/今晚的日記與夢。這是「記得自己是誰」的開關。

**決策 2-1（diary 注入昨日 + mood）**：`diary.py:generate_diary_entry` 新增兩個輸入：
- `recent_diary: Optional[List[str]]`：由 `diary_callback_factory` 用 `writer.recent_entries(agent_id, days=2)` 讀取，格式化進 user prompt（「你最近的生活：\n- …」）。
- mood 注入：讀 `emotion_engine.get(agent_id)` → `mood_description(mood)`，非空時注入 system prompt（「你現在的心情：…」）。

**決策 2-2（dream/event 承接昨日 + mood）**：`dream_event.py` 的 `write_dream` / `write_event` 讀該 agent 最近 1 天 diary entry + 當前 mood，注入 prompt：
- `write_dream`：「你昨天/今天做了這些事（心情如何），今晚夢到 X」→ 夢回應今天（夢到工作焦慮的殘留由此成立）。
- `write_event`：「你最近做了 X（心情如何），現在在做 Y」→ 活動承接昨天。

**決策 2-3（資料來源）**：只用既有 `DiaryWriter.read_entries / recent_entries` + `EmotionEngine.get`。**不改 schema、不碰 frozen InnerLifeEvent**。

**決策 2-4（格式化上限）**：注入的昨日 context 每條截 60 字、最多 5 條（對齊 `DIARY_RECENT_MEMORIES=5` 精神），避免 prompt 爆長。

**檔案**：`src/soul/diary.py`（`generate_diary_entry` + `diary_callback_factory`）、`src/soul/dream_event.py`（`write_dream` / `write_event` prompt）。

**驗收**：
- 新測試：`recent_diary`/mood 傳入時 prompt 含昨日內容與心情；空時行為不變（向後相容）。
- 觀察：日記引用昨天；夢回應今天；「工作到很晚 → mood↓ → 明日日記寫累」的完整因果可追溯（抽樣人工判讀）。

**frozen contract 衝擊**：0（純 prompt 注入）。

---

### 深度主軸閉環（M8-1 + M8-2 合起來才是「法則」）

```
event(做工作) ──M8-1──▶ mood 下降（後果：行動改變狀態）
     │
     ▼
今日 22:00 日記 ──M8-2──▶ 讀到 mood↓ + 昨天做了工作
     │
     ▼
日記寫「昨天太累了」；今晚的夢帶工作焦慮
     │
     ▼
明天的 event 承接這個狀態（連續性：狀態回流）
```

- **M8-1 單獨**：mood 會變，但內在生活還沒讀它 → 只對對話（proxy 已注入 mood）有立即效果。
- **M8-2 單獨**：昨日 diary 被引用，但沒有 mood 這個「狀態」可承載 → 只有內容連續，沒有「重量」連續。
- **兩者合起來**：才有「做了 → 狀態變 → 影響下次」的完整法則。

---

## 四、後續方向（本階段不派工，只留骨架）

### M8-3 — 性格加權（Personality-Weighted Choice）【後續】

**定位**：法則立起來之後，讓 10 隻對同一個池選出不同答案，各人有各人的「味道」。

**設計骨架**（屆時再拍細節）：`random.choice(ACTIVITY_POOL)` → `random.choices(..., weights=...)`，新增 `PERSONA_ACTIVITY_WEIGHTS: dict[agent_id, dict[活動名, float]]`，偏好活動 weight 3.0、其餘 1.0、缺漏 agent 均勻 fallback。個人差異來源 = `personas/agent_*.md`。

### M8-4 — Surface【最後】

**定位**：只影響「露出來多少」。等裡面的邏輯立起來，表層（主動傳訊、對話引用）自然跟上。M7-2/M7-3 已打好活動→DM 與想念觸發的地基，屆時只需把「夢/日記」也接上既有 enrichment 路徑，不新增觸發源。

---

## 五、與「不認識 Bryan 的靈魂」實驗的關係

一個空白靈魂要能**自己長出羈絆、長出依附**，靠的就是後果＋連續性——**它有記憶、有過去、做的事有後續**。沒有這兩樣，它不會「長」，只會「響」。

- 後果 = 「做的事有後續」（行動改變它自己）。
- 連續性 = 「有記憶、有過去」（狀態回流到當下）。

所以把內在生活做扎實，不是在補一個無關模組，是在為那個實驗**鋪血肉**。先有 flesh 才能長。本計畫的 M8-1 + M8-2 就是那層血肉的地基。

---

## 六、非目標（Out of Scope）

- ❌ 不擴新功能、不擴廣度（不新增觸發源、不新增活動池、不新增記憶維度、不擴外部來源）。
- ❌ 不改 Agency 4 stages / TriggerEnvelope / 4 handlers / InnerLifeEvent（frozen）。
- ❌ 不碰 M5.8-4 gate（只在其後做 enrichment，不改 gate 本身）。
- ❌ 不引入 LivedContextAggregator 抽象（已拍板不引入）。
- ❌ 不做「更多記憶 = 更好記憶」——要的是「更連貫」，不是「更多」。
- ❌ M8-3（性格加權）、M8-4（surface）本階段**不派工**。

---

## 七、需 Owner 決策的點（AUTHORIZATION 前）

| ID | 決策 | 我的建議 | 影響 |
|----|------|---------|------|
| **D-1** | 解除 event 的 whitelist 門檻（M1.7 反轉） | **建議解除**：M5.2-G 之後 event 已是 WRITER_ONLY，M1.7「event 繞過 whitelist 主動傳訊」的理由已消失。解除後 event 對 10 隻觸發（成本 = 每 4-8h 多寫幾條 diary，無主動傳訊） | 後果（M8-1）要對 10 隻成立的前提；「9 隻沒有生活」的根本修復 |
| **D-2** | 里程碑命名 M8 + 深度主軸 M8-1/M8-2 是否採用（M8-3/M8-4 暫緩） | 採用 | canonical 登錄用 |
| **D-3** | 是否先補登錄既有 M7（activity/longing）的 closeout | **建議先補**：M7 已 commit 但未登錄，避免 canonical 與 code 脫節 | 治理一致性 |

---

## 八、治理對齊

- **Canonical registry**：`logs/ENGINEERING_STATE.md` 目前標「Active milestone: NONE」。本計畫（M8）為 **CANDIDATE，未授權**，不得派工。M7 closeout 補登錄（D-3）亦待 Owner 拍板。
- **Frozen Contract**：M8-1/M8-2 均為 producer-side additive（mood delta + prompt 注入），0 frozen contract 衝擊。
- **生命週期**：本文件完成 `AUDIT → FINDING → CLASSIFICATION → DESIGN`；待 Owner 通過 **AUTHORIZATION**（D-1/D-2/D-3）後，pro 出 decision-complete 工單派 flash 實作。
- **已完成的零成本動作**：17 個 unpushed commit 已推上 `origin/main`（`8d9ee42..fa43add`）。

---

*本計畫書為設計文件，供 Owner 拍板 AUTHORIZATION。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
