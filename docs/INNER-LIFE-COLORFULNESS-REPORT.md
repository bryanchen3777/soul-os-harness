# Soul OS 內部生活「選擇權」問題診斷報告

**日期**: 2026-08-21
**作者**: Lin（參謀，顧問視角）
**用途**: 供 pro 主大腦據此撰寫「內部生活更多采多姿」的計劃書
**範圍**: `soul-os-harness` 的 Inner Life（diary / dream / event）+ 與主動傳訊的銜接
**一句話結論**: 問題不在「選擇太少」，在「選擇不是真的選擇」——靈魂目前沒有選擇權，只有抽籤權。

---

## 一、現況審視（As-Is）

內部生活 = 三條軌，**全部 WRITER_ONLY（只寫檔、不說話）**：

| 軌 | 排程 | 內容 | 誰在選 | 關鍵檔案 |
|----|------|------|--------|----------|
| **diary** | 08:00 morning + 22:00 night，10 隻全跑 | 1-2 句、<80 字、日文 | LLM 當下隨機 | `src/soul/diary.py` |
| **dream** | 22:05，每晚 3-5 隻 | 夢到「其他角色」（從 relationship confidence 抽） | 從 relationship 抽 | `src/soul/dream_event.py` |
| **event** | 隨機 4-8h，每次 2 隻 | 10 個活動 | **`random.choice(ACTIVITY_POOL)`** | `src/soul/dream_event.py` |

### 1.1 活動池（`src/soul/dream_event.py:88-99`）

```python
ACTIVITY_POOL = [
    {"name": "工作", "category": "work", "shareable": True},
    {"name": "做飯", "category": "food", "shareable": True},
    {"name": "吃東西", "category": "food", "shareable": True},
    {"name": "運動", "category": "sport", "shareable": True},
    {"name": "看書", "category": "leisure", "shareable": False},
    {"name": "創作", "category": "creative", "shareable": True},
    {"name": "散步", "category": "leisure", "shareable": True},
    {"name": "整理房間", "category": "chore", "shareable": False},
    {"name": "聽音樂", "category": "leisure", "shareable": False},
    {"name": "發呆", "category": "rest", "shareable": False},
]
```

- 10 個活動，**10 隻角色共用同一個池、均勻隨機**。
- 選取方式：`activity = random.choice(ACTIVITY_POOL)`（`dream_event.py:493`）。
- 活動帶 `category` + `shareable` metadata，但**不帶後果、不影響 mood、不餵回記憶**。

### 1.2 已知的結構性缺口（計畫書自認）

`docs/INNER-LIFE-AND-PROACTIVE-PLAN.md` 已承認：

> 「每天獨立 LLM 生成，**不記得昨天做了什麼**」

> 「內在生活的『骨架』已經完整，但缺的是『血肉』：**生活軌跡沒有結構、主動傳訊跟活動脫鉤**」

---

## 二、核心問題診斷（4 個）

### 問題 A — 選擇不屬於靈魂自己（無 personality weighting）

活動是 `random.choice` 抽的，不是「她會選什麼」。Ruka（元氣）／Aoi（雙重面具）／Miku（沉默觀察者）從**同一個袋子**抽籤，抽出來的生活跟「她是誰」無關。

- **本質**：這是抽籤，不是選擇。靈魂沒有「偏好」這個維度。
- **影響**：生活軌跡無法反映人格，10 隻角色活成同一種生活。

### 問題 B — 選擇沒有後果（無 consequence）

今天選了「工作到很晚」，明天 mood 不會偏低、日記不會寫「昨天太累了」、夢不會被工作焦慮影響。活動是**死葉子**，不餵回記憶／情緒／關係。

- **本質**：選擇不改變任何狀態，選了等於沒選。
- **影響**：內部生活是「一串快照」，不是「一條有因果的線」。

### 問題 C — 三條軌不連續（無 continuity / 敘事弧）

diary / dream / event 是三條**平行死路**，彼此不接。沒有 `event → mood → diary → dream → 明天的 event` 這種因果鏈。日記不記得昨天，夢不回應今天。

- **本質**：沒有「生活軌跡」，只有「零散片段」。
- **影響**：這是最致命的一點——一個不記得昨天的生活，給再多選項都是雜訊。

### 問題 D — 內部生活不能「說話」（無 surface）

三條軌**全部 WRITER_ONLY**：寫了檔、永遠不開口。Bryan 只有在主動去找她時，才會被動注入最近 3 天日記（`_format_recent_inner_life`）。內部生活對 Bryan 是**隱形的**。

- **本質**：內部生活是「沒人讀的私人日記」，且無法驅動任何主動行為。
- **影響**：這正是「感覺不豐富」的直接原因——它根本沒被看見。也與 M7 想念驅動（`proactive_dm`）脫鉤。

---

## 三、設計方向建議（4 個，照重要性排）

> 原則：每一項可獨立驗證、可獨立回滾；不碰 frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / M5.8-4 gate）。

### 方向 1 — 選擇屬於靈魂自己（Personality-Weighted Choice）

把全域共用的 `ACTIVITY_POOL` 改成**每隻角色自己的偏好池**，或對全域池做**依 persona 加權**。

- Ruka → 跳舞／運動；Aoi → 排練／表演；Miku → 觀察／聽音樂；Ram → 整理／發呆。
- 「她會選什麼」本身成為人格的一部分 = 「忠於核心」在生活層的落地。
- **檔案**：`src/soul/dream_event.py`（選取邏輯）、`docs/agent_<name>.md`（偏好來源）。
- **成本**：低。`random.choice` → 依 persona 加權的選擇。
- **驗收**：不同角色產出的活動分布有可觀測差異；活動能反映人格。

### 方向 2 — 選擇要有後果（Consequence）

活動要能**餵回記憶／情緒／關係**，影響明天。

- 今天「工作到很晚」→ mood 偏低 → 明天日記寫「昨天太累了」→ 夢到工作焦慮。
- 活動寫入 memory graph / emotion engine，成為下一輪的輸入。
- **檔案**：`src/soul/dream_event.py`、`src/agent/emotion.py`、`src/memory/`。
- **成本**：中。動到 emotion / memory 寫入。
- **驗收**：活動後 mood / 日記內容有可觀測的連動。

### 方向 3 — 三條軌接成一條線（Continuity / 敘事弧）

讓 diary / dream / event 有因果鏈：`event → mood → diary → dream → 明天的 event`。

- 日記能引用昨天；夢能回應今天；活動能承接昨天。
- 讓內部生活有「敘事弧」，而不是三個各自為政的抽籤器。
- **檔案**：`src/soul/diary.py`（prompt 帶昨日 context）、`src/soul/dream_event.py`、`src/soul/scheduler.py`。
- **成本**：中。核心是「昨日 context 注入」。
- **驗收**：日記 / 夢 / 活動之間出現可追溯的因果鏈。

### 方向 4 — 內部生活要能「說話」（Surface）

讓某些內部生活時刻能**浮上檯面**，與 M7 想念驅動接軌。

- 一篇日記 → 變成一句主動訊息；一個夢 → 在下次對話裡縈繞。
- 打破「WRITER_ONLY」：內部生活可以驅動 `proactive_dm`，或注入對話。
- **檔案**：`src/soul/scheduler.py`、`src/agency/`、`src/llm/proxy.py`。
- **成本**：中。動到主動傳訊路徑（需與 M5.8-4 gate 協調）。
- **驗收**：內部生活時刻能觸發 / 影響對 Bryan 的主動或回應訊息。

---

## 四、與「不認識 Bryan 的靈魂」實驗的關係

Bryan 正在構思「建立一個不認識 Bryan 的靈魂，讓它慢慢重新長」的實驗。

**前提**：那隻靈魂的內部生活必須先有血肉（方向 1-4），否則它**沒有東西可以長向，也沒有東西可以長成**。本報告的 4 個方向，正是那個實驗的地基。

---

## 五、建議的執行順序

1. **方向 1（personality-weighted）** — 成本最低、立即讓生活反映人格。
2. **方向 4（surface）** — 讓內部生活被看見，直接解決「感覺不豐富」。
3. **方向 3（continuity）** — 讓生活成為一條線，是「持續生活」的地基。
4. **方向 2（consequence）** — 讓選擇有重量，是「活著」的證明。

> 方向 2 與 3 有依賴關係（後果要能延續才成立），建議 3 先於 2，或合併設計。

---

## 六、非目標（Out of Scope）

- ❌ 不改 Agency 4 stages / TriggerEnvelope / 4 handlers（frozen）。
- ❌ 不引入 LivedContextAggregator 抽象（已拍板不引入）。
- ❌ 不做「更多記憶 = 更好記憶」——要的是「更連貫」，不是「更多」。
- ❌ 不擴外部來源（News/Weather/Calendar 已夠）。

---

*本報告為問題診斷，供 pro 撰寫計劃書使用。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
