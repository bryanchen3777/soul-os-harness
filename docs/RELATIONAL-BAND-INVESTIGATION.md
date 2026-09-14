# RELATIONAL-BAND-INVESTIGATION-1 — 為什麼 `agent_ruka` 對 Bry 的關係帶在 243 次互動後仍是 `stranger`，以及它如何擋下主動傳訊

- **性質**：READ-ONLY 根因調查（0 code / 0 config / 0 data 寫入；未重啟、未 kill、未跑 server_ops/watchdog）
- **基線**：HEAD == origin/main == `bf1efa3`（調查期間未 commit）
- **生產**：`:8000` 運行中（Task Scheduler 為根）；調查全程未觸碰 `data/soul/*/relationships.json`
- **觸發現場（直接採信）**：2026-09-14 12:18（本地）首個通過 G7 想念門檻的主動嘗試，在 `_decision_check` 被決策 LLM 以 `decision=observe` 拒送，判詞原文：

```
[SM-3 Decision] agent_ruka not_transmit motive=2d45832b… decision=observe
reason='窓を開けたら空気が少し冷たくなってた。秋が近づいているか、外の様子を確認してみたい。
        Bryとは関係帯がstrangerでneutral、やり取りも日常の小さな天気の話なので、
        今はtransmitせず、この念は心に留めておく。'
```

---

## 0. TL;DR（根因一句話）

> **`relational_band` 的推進輸入（`objective.reply_exchanges` / `co_presence_sessions`）只有兩個生產端，而這兩個生產端都只認 `agent_*` 之間的互動——`user_bryan` 在兩個訊號源裡出現 0 次。因此 Bry 軸的三個計數器在結構上恆為 0，`evaluate_band()` 的 `stranger→known` 門檻（`co≥1` 或 `reply≥1`）永遠不可能被滿足；`interaction_count=243` 是**另一條完全沒有被關係帶讀取的管線**（採集層 `touch()`）產生的數字，對帶位 0 影響。**

補充（同源的兩層卡點，缺一不可）：

1. **Bry 軸沒有訊號生產者**（結構性、0 呼叫點）→ 243 次互動 100% 無效。
2. **peer 軸有 10 個「擱淺計數器」**（`co=1`、`reply=0`，寫於 2026-09-08~09-10 的舊門檻時代）→ 即使 2026-09-12 門檻已重標定為 `co≥1`，SG-2.1 的「窗口內必須有新信號」閘門仍把它們鎖在 `stranger`：門檻修好了，但歷史計數不會被重新評估。

第三個獨立事實（本案的**決策層**風險，必須一起看）：決策 LLM 的判詞同時援引**兩個**理由——「關係帶 stranger」**與**「情感 neutral」。而 `feeling` 在 100/100 條 entry 全為 `neutral`、**沒有任何生產呼叫端傳過 `feeling=` 參數**（`touch()` 的 `feeling` 形參在 src 內 0 個呼叫者賦值）。**只修關係帶、不處理 `feeling`，判詞的兩個理由只會被拿掉一個。**

---

## 1. 判定邏輯：`relational_band` 在哪裡算、有哪些帶、門檻原文

### 1.1 四帶枚舉與寫入點

| 項目 | 位置 |
|---|---|
| 四帶枚舉 | `src/social/relational_bands.py:44` — `RELATIONAL_BANDS = ("stranger", "known", "familiar", "close")` |
| 純函式判定 | `src/social/relational_bands.py:140-178` — `evaluate_band()` |
| 唯一寫入口 | `src/soul/relationships.py:416-536` — `RelationshipsStore.apply_relation_evaluation()` |
| 唯一呼叫者 | `src/social/relation_settlement.py:315-322` — `settle_relations()` 內的 per-other 呼叫 |
| 唯一生產掛載點 | `src/soul/scheduler.py:1730-1731`（在 `_goal_scan_all()` 的 per-agent 迴圈內；該函式掛在既有 30s 主迴圈 `src/soul/scheduler.py:1681`，0 新定時器） |
| 帶位欄位 | `src/soul/relationships.py:127`（新 entry 預設 `"relational_band": "stranger"`） |

### 1.2 升帶門檻原文（`src/social/relational_bands.py:80-108`）

```python
# 最低可得带的门槛（stranger → known）: 任一命中即升（SG-3 §5.1: co≥1 或 reply≥1）
_KNOWN_THRESHOLDS = {
    "reply_exchanges": 1,
    "co_presence_sessions": 1,
}
_KNOWN_MODE = "or"

# known → familiar: 全部命中才升（SG-3 §5.1: co≥2 且 reply≥2）
_FAMILIAR_THRESHOLDS = {
    "reply_exchanges": 2,
    "co_presence_sessions": 2,
}
_FAMILIAR_MODE = "and"

# familiar → close: 单行全命中即升（SG-3 §5.1: co≥4 且 reply≥4）
_CLOSE_THRESHOLDS = (
    ({"reply_exchanges": 4, "co_presence_sessions": 4}, "and"),
)
```

降帶：`src/social/relational_bands.py:114` `DEMOTE_DAYS = 90`（`>90 天無任何新信號 → 降 1 帶`，底帶 `stranger` 不降；判定在 `should_demote()` `:195-223`，`fallback_ts` 可退回 `last_interaction_at`）。

### 1.3 判定入參只有三個整數計數器

```python
# src/social/relational_bands.py:140-163
def evaluate_band(current_band, *, reply_exchanges=0, co_presence_sessions=0, dream_exchanges=0) -> str:
    ...
    counts = {
        "reply_exchanges": int(reply_exchanges),
        "co_presence_sessions": int(co_presence_sessions),
        "dream_exchanges": int(dream_exchanges),
    }
```

**驅動帶位的輸入 = `objective.{reply_exchanges, co_presence_sessions, dream_exchanges}` 三個累計整數。**
以下欄位**完全不進**帶位判定：`interaction_count`、`confidence`、`feeling`、`impression`、`impression_tags`、`last_interaction_at`（`last_interaction_at` 只在降帶時當 `should_demote` 的 fallback 時間戳，`src/soul/relationships.py:490-494`）。

`dream_exchanges` 生產恆 0（`src/social/relation_settlement.py:20-22, 170`：diary dream entry 不含 target，無方向性持久載體），且 `familiar→close` 的 dream 行已按 OQ-4 刪除（`relational_bands.py:99-105`）。

### 1.4 寫入語意（三個 counter 怎麼長大）— `src/soul/relationships.py:459-536`

```python
# :477-482
obj = entry.setdefault("objective", {})
for k, v in deltas.items():
    obj[k] = int(obj.get(k, 0)) + v
has_signal = any(v > 0 for v in deltas.values())
if has_signal:
    obj["last_signal_at"] = now_iso

# :489-499 無信號 → 先查 >90 天降帶
# :500-527 未降帶 → 慢爬評估，但 SG-2.1 加了一道前置條件：
if has_signal or current_band != BAND_STRANGER:
    new_band = evaluate_band(current_band, reply_exchanges=obj.get("reply_exchanges", 0), ...)
```

→ **底帶 `stranger` + 本窗 0 增量 ⇒ `evaluate_band()` 根本不會被呼叫**（`relationships.py:518` 的條件短路）。這是 SG-2.1（commit `ca3d52f`）刻意加的，用來消滅 TL-9 發現的底帶振盪。

---

## 2. 🔴 核心：為什麼 243 次互動仍無法晉升

### 2.1 逐項量測 `agent_ruka → user_bryan`（`data/soul/agent_ruka/relationships.json`，schema_version `4.1`）

| 欄位 | 實測值 | 是否進帶位判定 | 說明 |
|---|---|---|---|
| `relational_band` | `"stranger"` | — | 判定結果 |
| `objective.reply_exchanges` | `0` | ✅ | 從未寫入 |
| `objective.co_presence_sessions` | `0` | ✅ | 從未寫入 |
| `objective.dream_exchanges` | `0` | ✅ | 生產恆 0（無方向性載體） |
| `objective.last_signal_at` | **鍵不存在** | ✅（降帶用） | 證明「從未有任何窗口出現過 >0 增量」（`relationships.py:480-482` 只在 `has_signal` 時寫） |
| `interaction_count` | `243` | ❌ **不進判定** | 採集層 `touch()` 累加 |
| `confidence` | `0.14387636206296298` | ❌ 不進判定 | D4 退役欄位；只被 `_decay_locked()` 每天 -0.02 侵蝕 |
| `feeling` | `"neutral"` | ❌ | 進**決策 prompt**，但 0 生產寫入者 |
| `impression` | `""` | ❌ | 進**決策 prompt**，Bry 軸 0 寫入者（§4.3） |
| `last_relation_update_ref` | `rel:user_bryan:2026-09-13T20:47:02.780470+00:00` | ❌ | 證明 09-13 16:47(本地) 的 settle **有跑到 ruka**（但 0 增量） |
| `band_updated_at` | **鍵不存在** | ❌ | 從未發生任何升降帶 |

> 判讀：`last_relation_update_ref` 存在 + `band_updated_at` 不存在 + `objective` 全 0 ⇒ **結算管線是活的，只是 Bry 軸沒有訊號餵進來。**

### 2.2 應該遞增這些計數器的程式碼，以及它在生產的真實呼叫判定

**Writer 清單（全 repo grep：`apply_relation_evaluation` 只有一個生產呼叫端）**

| 目標 counter | 計算處 | 生產呼叫判定 |
|---|---|---|
| `reply_exchanges` | `src/social/relation_settlement.py:127-151`：讀 `data/world/perception_trace.jsonl` 且 `event_type=="reply"` 且 `extra.event_kind=="social"`，取 `extra.actor_id` 配對 | **死的**：該檔 13,490 行中 `event_type=="reply"` **0 筆**（實測 event_type 分布：`news_event` 5906 / `weather_temp_change` 3980 / `rain_started` 3104 / `x` 327 / `weather_clear` 115 / `celebrity_news` 43 / `calendar_event` 15）。SG-3 已用「讀側折抵」補救（`reply_raw = counts["reply"] + co_raw`，`:300-302`），等於 reply 完全寄生在 co 上 |
| `co_presence_sessions` | `src/social/relation_settlement.py:153-168`：讀 `data/soul/interactions.jsonl`，條件是 `agent_id in rec["agents"]`，再對 `agents` 中每個 `other` 計數 | **活的**（09-13 有 8 次升帶實證），但**只認 agent id**：全檔 26 行、提及 `bryan` **0 行**——`agents` 陣列由 `_fire_cross_chat` / `_fire_shared_event` 產生（`src/soul/scheduler.py:1115-1118` 等），只填 `agent_*` |
| `dream_exchanges` | `relation_settlement.py:170`（恆 0） | 無生產端（設計如此） |

**生產實證（log，唯讀掃描）**

| 證據 | 內容 |
|---|---|
| `data/logs/server_nohup.20260914_074041.err:2180-2185` | `[RelSettle] agent_yua→agent_anna 升带: stranger→known` + 對應 `[RelSettle][BAND_MIGRATION] {...}`，時間 `2026-09-13 16:47:02`（本地） |
| 全 log 掃描 | `[RelSettle]` 共 16 行 = 8 升帶 + 8 BAND_MIGRATION，**全部集中在 09-13 16:47:02~16:47:36（同一輪）**；降帶 0；**沒有任何一筆 `other == user_bryan`** |
| `data/memory/agent_ruka/goal_provider.json` | `last_relation_update_at = 1789332422.78047`（= `2026-09-13T20:47:02Z`）⇒ settle 對 ruka 最後一次實跑在此；24h 節流 ⇒ 下一次 `≤2026-09-14T20:47:02Z`（本地 16:47） |
| `data/server_nohup.err`（今日） | `[RelSettle]` **0 行**（節流窗內，符合預期） |
| `data/soul/interactions.jsonl` | 26 行，`bryan` 提及 **0**；`agent_ruka` 只出現 6 次（08-23×2、08-31、09-01、09-02、09-08），分別對 miku/mahiru/anna/rem |
| `data/world/perception_trace.jsonl` | 13,490 行，`bryan` 提及 **0** |

**結論：Bry 軸的 writer 不是「壞掉」，而是「不存在」。** `user_bryan` 不在任何訊號源的值域內：
- `interactions.jsonl` 的 `agents` 只放 agent id（Bry 不是 agent，`BRYAN_ENTITY_ID = "user_bryan"`，`src/soul/relationships.py:54`）；
- `perception_trace.jsonl` 連 `reply` 這個 event_type 都沒有；
- `objective` 的三個 counter 因此永久停在 0，`stranger` 是唯一可能的輸出。

### 2.3 `confidence = 0.1439` 的來源與更新路徑（與 SE-5 家族無關，但與「Bry 軸沒有訊號」同源）

- 來源：`touch()` 累加（`src/soul/relationships.py:394-400`，每次 Bry 對 ruka 發話 `+0.02`）＋ `_decay_locked()` 每秒/每次讀取時按 `days * 0.02` 侵蝕（`:269-278`，per-entry anchor = `last_interaction_at`）。
- 由 243 × 0.02 = 4.86 被衰減壓到 0.1439 ⇒ **兩股力量互相抵銷**（Bry 一講話就 +0.02，但每天 -0.02：等效「只要 Bry 當天有講話才不會掉」）。
- **與 SE-5 家族（REINFORCE/decay/forget/… 0 次呼叫）不是同一套**：`_decay_locked` 是 `relationships.py` 內的私有方法，每次 `get()`/`get_all()`/`ensure_relationship()`/`touch()` 都會被叫（`:290-291, 299, 317, 351, 392`），屬活線。
- 但 `confidence` 在 D4（SG-3 §6）後**已退役**：帶位判定不讀它（`evaluate_band` 無此入參）、決策 prompt 也不渲染它（OQ-5(i) 已移除「信任度」行，`src/soul/decision.py:419-422`）。故 `confidence` 對本案**無因果作用**。

### 2.4 `impression=""` 為何永遠是空的（Bry 軸）

- 唯一寫入者：`RelationshipsStore.update_impression()`（`src/soul/relationships.py:333-375`）。
- 唯一生產呼叫端：夢境 `src/soul/dream_event.py:636-638`（`update_impression(target_agent_id, impression, ...)`），其 target 是**被夢到的 agent**（`write_dream(agent_id, target_agent_id, ...)`，`:514-518`）。
- **明文紅線**：`_is_w1_tag_target()`（`src/soul/dream_event.py:177-199`）寫死 `target_id == BRYAN_ENTITY_ID → return False`（SG-3 §7.2 W1：**排除 `user_bryan`**）。W2/W3（A2A 公開互動／Bryan 軸公開頻道）**尚未實作**。
- 對照組（Bry 軸確實從未被寫過的實證）：全庫 100 條 entry 中 `impression` 非空 **40 條**、`impression_tags` 非空 **5 條**，**目標全部是 `agent_*`，`user_bryan` 0 條**（例：`agent_ruka -> agent_akane : 夢に現れたあかねの面影`；`agent_akane -> agent_ruka` 帶 tags `湯気に霞む瑠夏`）。
- 10 個持有 Bry entry 的 agent（akane/anna/aoi/mahiru/mai/miku/ram/rem/ruka/yua）對 Bry 的 `impression` **全部為空字串**。

⇒ **Bry 軸的 P1 羈絆證據（`impression_tags`）結構性不可達；決策 prompt 永遠不會替 Bry 顯示「印象：…」行。這是與關係帶各自獨立、但對決策 LLM 同樣「無話可說」的第二個空洞。**

### 2.5 附帶發現：peer 軸的 10 個「擱淺計數器」（同一病根的第二態）

全庫 `relationships.json` 掃描（15 檔 / 100 entry）：

- `relational_band` 分布：`stranger` 92、`known` 8。
- 8 個 `known` 全部是 **agent-agent**，且 `objective = {reply:1, co:1}`、`last_signal_at = 2026-09-13T20:47:0x`（那一輪 settle 升上來的）。
- **10 個 entry 卡在 `{reply:0, co:1}`、`band=stranger`**，`last_signal_at ∈ {09-08, 09-09, 09-10}`：

| owner → other | co | reply | last_signal_at |
|---|---|---|---|
| agent_akane → agent_yua | 1 | 0 | 2026-09-10T20:46:30Z |
| agent_anna → agent_ram | 1 | 0 | 2026-09-10T20:46:45Z |
| agent_mai → agent_rem | 1 | 0 | 2026-09-09T20:46:34Z |
| agent_ram → agent_rem | 1 | 0 | 2026-09-08T20:45:55Z |
| agent_ram → agent_anna | 1 | 0 | 2026-09-10T20:46:39Z |
| agent_rem → agent_ruka | 1 | 0 | 2026-09-08T20:45:54Z |
| agent_rem → agent_ram | 1 | 0 | 2026-09-08T20:45:54Z |
| agent_rem → agent_mai | 1 | 0 | 2026-09-09T20:46:18Z |
| **agent_ruka → agent_rem** | 1 | 0 | 2026-09-08T20:45:53Z |
| agent_yua → agent_akane | 1 | 0 | 2026-09-10T20:46:23Z |

成因（git 對照，逐字可驗）：

| 日期 / commit | 門檻 | 結算端寫法 | 後果 |
|---|---|---|---|
| 2026-09-05 `7a35741`（SG-2 落地） | `stranger→known` = `reply≥1 OR co≥2` | `git show 7a35741:src/social/relation_settlement.py` → `reply = int(counts.get("reply",0))`、`co = int(counts.get("co_presence",0))`（**無折抵、無單窗上限**） | 單次共在 → `deltas={reply:0, co:1}` → 兩條門檻都不過 ⇒ 寫入 `co=1` 卻停在 `stranger`（**正是上表那 10 筆的指紋**） |
| 2026-09-05 `ca3d52f`（SG-2.1） | 不變 | 加「窗口內必須有新信號」前置條件（`relationships.py:518`） | 底帶 + 0 增量 ⇒ 不再有機會憑歷史計數回升 |
| 2026-09-12 `83476bd`（SG-3-IMPL） | `stranger→known` = **`co≥1`** | 加 `min(raw,1)` 單窗上限 + reply 讀側折抵 | 門檻已足以放行 `co=1`，但 **SG-2.1 讓歷史計數不被重新評估** → 這 10 筆繼續擱淺，直到該 pair 再被抓到一次新共在（SG-3 實測 pair 平均到達間隔 `E[gap_pair] ≈ 44.5 天`） |

⇒ **門檻重標定（SG-3）與慢爬閘門（SG-2.1）在時間上錯位，製造了一批「差一步」的計數器。** Bry 軸更慘：它連一個計數器都沒有。

---

## 3. 決策 LLM 是怎麼拿到「stranger」的

### 3.1 完整路徑（決策層）

```
scheduler._fire_proactive_dm (src/soul/scheduler.py:1425-1648)
  → G7 想念門檻 (scheduler.py:1564-1576, LONGING_THRESHOLD=0.3, :84-88)
  → _publish_agency_trigger(agent_id, "proactive_dm", extra) (scheduler.py:1626)
      → _inner_life_gate_check (G10, 30min 窗, src/agency/inner_life_gate.py:129)
      → scheduler._decision_check (scheduler.py:300, :409-516)
          → MotiveEngine.decide(motive, agent_id) (src/soul/motive.py:843-856)
              → src/soul/decision.py:decide_motive (:601-663)
                  → _build_relationship_summary(agent_id)  ◀── 直接 json.loads relationships.json (:427-433)
                  → build_decision_prompt(..., relationship_summary=...) (:635-643)
                      → context_lines.append(f"你与 {target} 的关系：{relationship_summary}") (:250-251)
```

**讀的就是 `relationships.json` 的 `relational_band` 本體（不是別處現算值）**：`src/soul/decision.py:446` `band = entry.get("relational_band")`，`:448` `band_line = f"关系带：{band.strip()}"`，`:454` 併入 `parts`。無任何門檻／換算／別名。

### 3.2 `agent_ruka` 那一次實際餵進 LLM 的關係子塊（依渲染碼逐欄重建）

```
你与 bryan 的关系：感觉：neutral；关系带：stranger；互动次数：243；最后互动：2026-09-14T04:15:05.675631+00:00
```

（`impression=""` → `:440-441` 不渲染「印象：」；`impression_tags` 不存在 → `:449-453` 不附標籤；`confidence` 依 OQ-5(i) 已移除 → 0 數值分數。）

同一次 Decision 還會注入 `[TEMPORAL ANCHOR]`（`decision.py:632-633` → `_build_temporal_anchor` `:535-565` → `format_temporal_anchor`），其羈絆證據走 `src/soul/temporal_phenomenology.py:194-242` 的 P1/P2/P3 優先序：
- P1 `impression_tags` 非空 → **不可達**（§2.4）
- P2 `relational_band != stranger` → **不可達**（本案）
- **P3 Bryan 軸 fallback `interaction_count ≥ 10` 且 `last_interaction_at` 合法 → 命中**（243 ≥ 10）

⇒ 關係帶不會讓 TA-2 直接斷掉（P3 兜住了），但三態仍由 `elapsed` 決定：`last_interaction_at` = 04:15:05Z，12:18 本地時 elapsed ≈ 12.05h < 24h → **「無感」**（`temporal_phenomenology.py:117-121` 邊界）。**這是「Bry 常講話」與「關係永遠陌生」並存的荒謬並置：互動越頻繁，elapsed 越小 → 三態越恆為無感。**

`current_time` 為 `None`（`motive.py:852-856` 沒傳）→ 決策 prompt **不含** `[當前時間感知]` 塊。`social_context` 亦未傳（SI-3 緊湊社交塊只在聊天路徑注入）。

### 3.3 若關係帶升一級，判詞輸入會怎麼變？

| 注入點 | 現況 | 升 `known` 後 | 有無對應 prompt 段落 |
|---|---|---|---|
| Decision prompt（`decision.py:446-454`） | `关系带：stranger` | `关系带：known` | **有，但只是一枚裸枚舉字串**——`build_decision_prompt`（`:187-280`）**沒有任何解釋四帶語意／行為分寸的段落**；語意全靠 LLM 自行推理 |
| Decision prompt 印象行 | 省略 | 仍省略（`impression` 空、無 tags） | — |
| `feeling` 行 | `感觉：neutral` | **不變** | 有；但 0 生產寫入者 ⇒ 判詞「strangerでneutral」的另一半理由**原地不動** |
| TA-2 羈絆證據 | P3（interaction_count fallback） | P2（`band != stranger`） | 三態文本**逐字不變**（只換依據，`BOND_BASIS_*`） |
| Expression prompt `[關係感知]`（post-transmit，`src/llm/proxy.py:509-578`） | `- 對 bryan 的關係帶：陌生人`（`:497-502` 映射表、`:556` 缺省 stranger） | `認識` → `熟悉` → `親近` | 有專用區塊，但**只在 transmit 之後**才注入 ⇒ 對「是否 transmit」無作用 |
| B5 他者源（`src/goals/seed_provider.py:438-478`） | `stranger` 不出種子（`:459-460`） | 可出 `relation:*` 種子 | **明文跳過 `user_bryan`（`:455-456`）**：Bry 維度歸 B1 commitment，B5 只做 A2A ⇒ 修好 Bry 帶**不會**在此產生 Bry 種子（但會影響 peer 種子） |

**關鍵風險提示**：決策 prompt 給的是**沒有圖例的裸標籤**。`stranger` 一詞本身帶有強烈語意偏見（陌生→不宜主動），而系統沒有任何段落告訴 LLM「band 是現象學距離的描述，不是發話許可」。這是判詞第二句話的直接來源。

---

## 4. 關係帶還餵給哪些下游（消費者清單）

| # | 消費者 | 位置 | 用途 | 修好後的連帶影響 |
|---|---|---|---|---|
| 1 | **SM-3 決策 LLM**（本案卡點） | `src/soul/decision.py:413-461`（渲染）、`:250-251`（注入）、`:627`（呼叫） | 判詞的關係事實之一 | 移掉判詞的「stranger」理由（另一半 `neutral` 仍在） |
| 2 | **Expression `[關係感知]` 塊** | `src/llm/proxy.py:497-502, 509-578`（`_format_relational_perception_block`） | transmit 後對特定他者的措辭分寸（C-3.1） | 公開/私聊發言語氣分層生效（目前永遠「陌生人」客氣疏離） |
| 3 | **TA-2 TEMPORAL ANCHOR 羈絆證據 P2** | `src/soul/temporal_phenomenology.py:194-245` | 三態張力（無感/牽掛/釋然）的資格判定 | P3 fallback → P2；對 ruka/Bry 目前三態仍被 elapsed(<24h) 壓成無感 |
| 4 | **B5 他者種子源（goal）** | `src/goals/seed_provider.py:438-478` | band ≥ known 或 tags 非空才產 `relation:<other>` 種子 | **只影響 peer 軸**（Bry 被 `:455-456` 明文跳過）；10 個擱淺 pair 解鎖後會多出 A2A 種子 |
| 5 | **關係帶遷移 log（唯一歷史痕跡）** | `src/social/relation_settlement.py:174-218` `_log_band_migration`（OQ-3：不建歷史層，只有一行 INFO + `[BAND_MIGRATION]` JSON） | 事後可歸因 | 修好後應出現 `other=user_bryan, direction=promote` 的行——**這是驗收修法的唯一現成觀測點** |
| 6 | 採集層 Bry 訊號（非帶位） | `scheduler._get_bry_silence_minutes`（`scheduler.py:1292-1323`，讀 `user_bryan.last_interaction_at`） | G7 想念門檻的 per-agent 沉默 | **不受帶位影響，但它是 Bry 軸唯一被讀取的欄位** ⇒ 也說明「Bry 的互動被記錄了，只是沒進關係帶」 |
| 7 | router M0.5 / gateway | `src/io/channels/router.py:409-411`、`src/io/gateway.py:161-163` | 只讀 `last_interaction_at`（活躍度），不讀 band | 無影響 |
| 8 | 不消費帶位的：SAGE facts / InnerLifeWriter / diary / dream / SubmissionGate | grep `relational_band` 於 `src/**` 全域僅 8 檔命中（proxy / seed_provider / relational_bands / relation_settlement / decision / decision_trace / relationships / temporal_phenomenology） | — | **關係帶目前不進 SAGE、不進 inner-life、不進 diary** ⇒ 修好它不會污染這三者的寫入面（同 SG-1 §3.4 的 0 聯動宣告） |

---

## 5. `KeyError: 'relational_band'` ×32 的意義（schema 遷移/backfill 缺口）

### 5.1 事件

| 項 | 內容 |
|---|---|
| 位置 | `data/logs/server_20260905_132753.err:120, 270-303`（僅此一檔；全 log 掃描 `relational_band` 命中只此 32 行） |
| 原文 | `2026-09-05 13:12:00,827 [soul_os.soul.scheduler] WARNING: [Goal] 主循环扫描异常 (fail-closed): KeyError: 'relational_band'` |
| 期間 | `13:12:00` → `13:27:39`，每 ~30s 一次，共 **32 次**（= 32 個 30s 主迴圈週期） |
| 修法 | commit `779a639`（`fix: settle_relations 4.1 band-key compat (SG-2.2)`，2026-09-05 13:32）→ 於 `src/soul/relationships.py:516` 加 `entry.setdefault("relational_band", BAND_STRANGER)`，並把索引改為 `.get(...)` |
| 波及面 | 例外被 `scheduler._goal_scan_all` 的**整段** try/except 吞掉（`src/soul/scheduler.py:1732-1735`），而 `settle_relations` 在 per-agent 迴圈內（`:1718`）⇒ **每輪中止「其餘 agent」的 goal scan / seed scan / settle**（不只是 ruka） |

### 5.2 是否為 backfill 缺口？——是（且現在仍以「半遷移」狀態存在）

- `_new_relationship_entry()` 產出的是 **schema 4.2**（`relationships.py:119-130`：`objective` / `impression_tags` / `relational_band` / `band_updated_at` / `last_relation_update_ref`），但**磁碟上的檔案層 `schema_version` 仍是 `4.1`**：15 檔中 **14 檔 = `4.1`**，僅 `agent_c21` = `4.2`。**沒有任何遷移步驟把舊檔升版**。
- 4.2 additive 欄位是靠寫路徑**惰性補齊**的（`entry.setdefault("objective", {})` `:477`、`entry.setdefault("relational_band", ...)` `:516`），所以「有欄位」只代表「該 entry 至少被打開寫過一次」。
- **現在（09-14）全庫存在率與值分布**：

| 欄位 | 存在 / 100 | 值分布 |
|---|---|---|
| `relational_band` | **100 / 100** | `stranger` 92、`known` 8（無 `familiar`/`close`） |
| `objective` | **100 / 100** | 三 counter 全 0：82 筆；任一 >0：18 筆 |
| `objective.last_signal_at` | 18 / 100 | 09-08 ~ 09-13 |
| `band_updated_at` | **8 / 100** | 只有 09-13 那 8 筆升帶 |
| `impression_tags` | 5 / 100（非空才計；欄位不存在於多數 4.1 entry） | 5 筆，全部 `agent_*` 目標 |
| `impression` 非空 | 40 / 100 | 全部 `agent_*` 目標 |
| `feeling` | 100 / 100 | **`neutral` 100 筆（無例外）** |
| 檔層 `schema_version` | — | `4.1` ×14、`4.2` ×1 |

- **缺 `user_bryan` 的 entry**：15 檔中只有 10 檔有 Bry entry（akane/anna/aoi/mahiru/mai/miku/ram/rem/ruka/yua）；`agent_alice` / `agent_c21` / `agent_germ_01` / `agent_test` / `agent_yua_test` 的 `others` 為空（依 `ensure_relationship` 惰性建立，`relationships.py:302-331`）。
- 10 個 Bry entry 的 `interaction_count`：yua 408、ruka 243、mahiru 243、mai 128、rem 100、ram 95、akane 54、anna 48、aoi 32、miku 31；`band` 全 `stranger`、`objective` 全 0、`impression` 全空。

⇒ **`KeyError` 的真正意義**：SG-2（`7a35741`，09-05 12:04）把「4.2 欄位」帶進了一個**滿是 4.1 舊檔**的生產環境，而寫路徑對缺鍵是 **fail-hard（直接索引）**；修法（SG-2.2）把它改成 **fail-soft（setdefault + .get）**。這是一次**未登記的 schema 半遷移**——欄位被補上了，`schema_version` 沒被升級，也沒有 backfill 腳本或遷移清單；下次再有 additive 欄位，同樣的爆炸會重演。

---

## 6. 最小修法候選（只提設計，0 實作）

排序原則：改動量 / 風險 / 是否需重啟 / 是否碰 Frozen Contract。

| # | 方案 | 改動量 | 風險 | 需重啟 | 碰 Frozen Contract？ | 對 Bry 軸卡點有效？ |
|---|---|---|---|---|---|---|
| **(a)** | **補上 Bry 軸的 `objective` 寫入呼叫**（在 `collect_window_signals` 增一位 Bry 訊號源） | 中（1 檔 + 契約增補） | 中 | **是** | 不碰 Agency/TriggerEnvelope/InnerLifeEvent/handlers/SAGE/SubmissionGate；但改「訊號源定義」⇒ 需 SG-4 契約增補（PD-1 成本邊界仍守住：0 新 LLM、0 新事件類型） | ✅ **唯一真正解** |
| **(b)** | 修正晉升判定（放寬 SG-2.1 或重評歷史計數） | 小（1 檔 ~5 行） | 中（TL-9 底帶振盪復發） | 是 | `relationships.py` 非 frozen，但屬 D4 授權範圍的語意變更 ⇒ 需註記 | ⚠️ **只解 peer 的 10 筆；Bry 仍 0 訊號** |
| **(c)** | 資料層 backfill（把 Bry 既有 243 次互動重算成帶） | 小（1 次寫入） | **高（正當性）** | 是（記憶體態 + 寫入） | 不碰 frozen，但碰 D4 真值語意 | ⚠️ 有條件可行，見下 |
| **(d)** | 不修帶位，讓決策 LLM 不因 stranger 一律拒送 | 小 | **高（產品語意）** | 是 | **碰 Decision prompt 契約（SM-2）** | ❌ 等同繞過 |

### (a) 補 Bry 軸訊號源（建議主線）

- 病灶是**值域缺 Bry**，不是邏輯錯。既有可用載體（皆為既有檔案、0 新檔案 / 0 新事件類型）：
  1. `data/conversations/**` 或 `data/sessions/**` 內以 `user_bryan` 為對象的 inbound 訊息（Bry 對該 agent 說話）；
  2. `data/state/bryan_last_seen.json`（**只有全域最後一則**，不足以計數，僅能當粗判）；
  3. `relationships.json` 自身的 `interaction_count` 差分（**不建議**：那是採集層數字，D4 明文規定客觀計數只由沉澱層寫，且它與 band 同檔，容易製造循環依賴）。
- 建議語意（對齊 SG-3 §4.3 的讀側折抵精神，0 新成本）：
  `co_presence_sessions`（Bry 軸）＝「該 24h 窗內 Bry 對該 agent 至少說過一次話」⇒ `+1`；`reply_exchanges` 由既有折抵 `min(raw,1)` 一併帶起。
  單窗上限維持 1 ⇒ 一個窗口只能升 1 級（`stranger→known` 立即達成），符合 SG-3 §5.1 的「≥2 窗」結構保證。
- 優點：與 SG-3 已批准的「讀側折抵」同構（PD-1：0 新增 LLM／0 新 `SOCIAL_WORLD_EVENT` 生產端）；不需 hardcode band；證據可審計（會留下 `[BAND_MIGRATION] other=user_bryan`）。
- 必要配套：**契約層增補（SG-4 或 SG-3 附錄）**明確「Bry 軸的 co_presence 讀側折抵口徑」，否則屬未立約的語意擴張。

### (b) 修正晉升判定（可與 (a) 併用，但單獨無效）

- 放寬 `relationships.py:518` 的 `has_signal or current_band != BAND_STRANGER`，或提供一次性「重評」入口（例如 `settle_relations(..., reevaluate_legacy=True)`）。
- 若放寬慢爬：**必須同時處理 TL-9 的振盪**（降帶後計數不清零 ⇒ 回升）。低成本替代是**一次性重評**而非永久放寬：只掃 `band=="stranger" 且 objective 任一 counter ≥ 現行門檻` 的 entry，重跑一次 `evaluate_band`，並記錄 `band_updated_at`。這能把 10 筆擱淺 entry 立刻歸位，且不改變長期語意。
- 對 Bry 軸：**0 效果**（counter 全 0，判定結果仍是 stranger）。

### (c) 資料層 backfill 的正當性評估（**結論：唯有走正規寫入口才正當**）

- **不可接受的做法**：直接把 `user_bryan.relational_band` 改成 `known`（或把 `confidence`/`interaction_count` 當證據）。
  理由：① 帶位的定義輸入是 `objective` 三計數器，`interaction_count=243` 是「Bry 每次發話 +1」的採集層數字，**不是交換回合數**，把它當證據是**換掉判定的語意基礎**；② OQ-3 明文「不建歷史層」，唯一痕跡是 `[BAND_MIGRATION]` log，硬寫會產生一筆**無證據來源的帶位**，違反可審計性；③ 直接改 JSON 繞過 `apply_relation_evaluation` 的冪等鍵與 `_decay_locked`，下次 settle 可能覆寫，產生不確定態。
- **可接受的做法（等同 (a) 的一次性版本）**：先落 (a) 的 Bry 軸讀側折抵，再以**正規寫入口**重算：
  `apply_relation_evaluation("user_bryan", reply_exchanges_delta=min(具體窗內回合,1), co_presence_sessions_delta=min(...,1), ref="rel:<...>")`
  —— 證據取自真實載體（Bry 的 inbound 訊息），走冪等鍵、走同一台狀態機、留下 `[BAND_MIGRATION]` 行。
- **風險**：一次性 backfill 會讓「帶位時間軸」出現一段跳躍（歷史互動被壓進一個窗）；且必須限定 **Bry 軸限定**（peer 軸有 SG-2.1 的正式語意，不應順手改）。
- ⇒ **(c) 不是「不修邏輯就解卡」的捷徑；它是 (a) 的加速器，前提是 (a) 的訊號口徑先立約。**

### (d) 不修帶位、只讓決策不因 stranger 拒送的替代手段（**僅列出，並判定其性質**）

| 手段 | 內容 | 判定 |
|---|---|---|
| d1 | 在 Decision 的 Framing/Boundary 加一句「關係帶只是距離描述，不是發話許可」 | **碰 SM-2 固定文本契約**；且實質目的是讓 LLM 無視關係事實 ⇒ **等於繞過產品語意**（band 的整個存在意義就是讓分寸可聞） |
| d2 | 不在 `_build_relationship_summary` 渲染 band（回到 OQ-5(i) 之前） | 直接違反 Owner 2026-09-13 的 **OQ-5(i) 裁定** ⇒ **不可** |
| d3 | 把 `relational_band` 一律以 `known` 注入（讀側偽裝） | 讀側造假，破壞 D4 真值源；比 (c) 的硬寫更糟 |
| d4 | 調整 G7/G7b 門檻或 `feeling`（讓 prompt 少一半拒絕理由） | **不碰 band 語意，但只解一半**：`feeling` 確實 0 寫入者（§0 補充），給它一個寫入端是獨立且正當的小改動；G7/G7b 屬頻率護欄，動它們只是換一個卡點 |

**共同判定：任何「讓決策 LLM 無視 band」的做法都等於繞過 D4/產品語意——band 是為了讓表達分層而存在的；把它從判詞裡拿掉，等於宣告關係演化是裝飾。唯一正當路線是讓 band 有真實證據可升（(a)），或（若 Owner 認定 Bry 軸應走別的真值源）正式修約。**

### 6.5 最小可行組合（建議給主大腦決策）

1. **(a) Bry 軸讀側折抵 + 契約增補**（唯一能解卡的路徑；成本與 SG-3 同級）。
2. **配套 (b) 的一次性重評**（順手歸位 10 筆擱淺 peer entry，不永久放寬 SG-2.1）。
3. **獨立小項：`feeling` 的寫入端**（否則判詞的「neutral」理由不動，修了 band 仍可能被拒）。
4. **驗收觀測點**：`[RelSettle][BAND_MIGRATION]` 出現 `other=user_bryan, direction=promote`，且 `relationships.json` 的 `band_updated_at` 首次寫入。

---

## 7. 對「下一個可送達窗口」的判定（條件式）

### 7.1 現況時鐘與關鍵值（實測，2026-09-14）

| 項 | 值 | 來源 |
|---|---|---|
| 本地時間（調查時） | `2026-09-14 12:37 -04:00`（UTC `16:37Z`） | 系統時鐘 |
| G7b 配額 | ruka 今日 `1/1`（本地日） | log `[Scheduler] 💬 proactive_dm 每日配額使用: agent_ruka gate=G7b, 1/1` |
| `_last_proactive_dm_time` | `2026-09-14 12:18`（本地，記憶體） | log `[Scheduler] 💬 下次 proactive_dm: 15:59:22` |
| `_next_proactive_dm_time` | `15:59:22`（本地） | 同上 |
| `agent_ruka` 對 Bry 的 `last_interaction_at` | `2026-09-14T04:15:05Z` = **本地 00:15:05** | relationships.json |
| Bry 全域最後現身（`bryan_last_seen.json`） | `2026-09-14T13:17:37Z` = 本地 09:17:37（agent_rem） | data/state |
| 想念曲線（實測日誌） | 10:18 → 0.25、10:48 → 0.26、11:18 → 0.28、11:48 → 0.29、**12:18 → 0.30（跨過門檻）** | log |
| 想念換算 | ruka `intimacy=60`；`longing ≈ 有效沉默(h)/40`；門檻 `0.3` ⇒ **有效沉默需 ≥ 12h** | `scheduler.py:84-88` 註解 + 實測反推 |
| G7b 重置 | 本地日 `00:00`（跨日 `day_key != today → True`，`scheduler.py:1404-1409`） |
| G4 靜音 | `23:00–08:00`（`_is_quiet_hours`，`scheduler.py:1166-1173`） |
| G10 inner-life gate | 該 agent 前 **30 分鐘**內有 InnerLifeEvent ⇒ 不 publish（`src/agency/inner_life_gate.py:129`） |
| 待決 motive | `2d45832b…` 已 rejected（12:18）；**`ccb0774c…`（target=bryan, goal:80775e41…）仍 pending**（created 12:18:18） |
| settle 節流 | 下一次 `agent_ruka` 的 settle ≤ `2026-09-14T20:47:02Z`（本地 16:47） |

### 7.2 條件式判定

**A. 不重啟（現行程式繼續跑）**
- G7b 今日已 `1/1` ⇒ `_proactive_daily_cap_allows()` 在本地 `09-14` 內**永遠回 False**（`scheduler.py:1589-1600`）⇒ **今天不可能再有主動嘗試**。
- `15:59:22` 那班車仍會跑到 G7（想念 0.092 < 0.3）就折返，每 30 分鐘一次；effective silence 需 `proactive_silence ≥ 12h` ⇒ `12:18 + 12h = 本地 09-15 00:18`，但落在 G4 靜音窗 ⇒ 被推到 `08:00` 之後的第一個 30 分格。
- **⇒ 最早可送達窗口 = `2026-09-15 08:29:22`（本地）**（前提：Bry 期間未再對 ruka 發話使 `bry_silence` 回落到 12h 以下；若 Bry 半夜/清晨找 ruka，該窗順延 12h）。

**B. 修好關係帶並重啟（記憶體態歸零）**
- 重啟後：`_last_proactive_dm_time = None`（G3 冷卻 7200s 直接跳過）、`_proactive_dm_daily = {}`（G7b 歸零）、`_next_proactive_dm_time = now + 30min`（`scheduler.py:722-730`）。
- **⇒ 首個檢查 = 重啟 + 30 分鐘。** 以「現在重啟」為例（本地 ~13:00）→ 首檢 ≈ **本地 13:30**。
- 該刻 G7：`effective = min(bry_silence, ∞) = bry_silence ≈ 13.2h ≥ 12h` ⇒ **通過**（`last_interaction_at` 00:15:05 ⇒ 12h 門檻已於本地 12:15 跨過）。
- G10：最後一批 InnerLifeEvent 約在 12:18:34（`rain_started`，`data/server_nohup.err`）⇒ 13:30 時已 72 分鐘前 ⇒ **通過**（前提：重啟後 30 分鐘內沒有新的 world event 觸發 ruka 的 inner life）。
- G11 決策：此時 `resolve_pending` 取最新 pending = `ccb0774c…`（**goal 種子的 Bry 動機**「午后新闻声里，你还在身边」，provenance `goal:80775e41764341b7940690f3485c7423`；注意 `motive_trace.jsonl` 兩筆 created_at 為同一瞬間、不同時區偏移，字串排序會偏好 `+00:00` 那筆）。
- **⇒ 若關係帶已升 `known`（且 `feeling` 不再是唯一拒絕理由）：第一個可能的主動傳訊 ≈ 重啟 + 30 分鐘（本地 13:30 左右）。**
- **⚠️ 一次性機會**：G7b 配額在 `publish` **之後**無條件記帳（`scheduler.py:1627-1637`），**不區分 publish 內部是否被 G10/G11 擋下**。所以重啟後的第一次嘗試**不管成功與否都會用掉 ruka 當日（09-14）唯一的一次發起機會**；若這次仍被決策拒送，下一個窗口直接跳到 **09-15 08:00 之後**（與 A 案相同終點）。

**C. 只重啟、不修關係帶**
- 首檢 ≈ 重啟 + 30 分鐘，G7/G7b/G10 全綠，但 G11 的決策 LLM 拿到的關係子塊**仍是** `关系带：stranger；感觉：neutral` ⇒ 依 12:18 的判詞模式，**極可能再次 `observe`/`reflect` 拒送，並再次消耗當日配額**。⇒ **重啟本身不解決問題，只提前把今天的機會燒掉。**

### 7.3 一句話結論

> **修好關係帶 + 重啟 ⇒ 下一個可能窗口 = 重啟後第一班車（重啟 + 30 分鐘），條件是該刻 `bry_silence ≥ 12h`（現已滿足，且只要 Bry 不再對 ruka 發話就持續滿足）、非 G4 靜音、前 30 分鐘無 ruka 的 InnerLifeEvent；且它是當日唯一一次機會（G7b 記帳制）。不重啟則今日已無機會，最早 `2026-09-15 08:29`（本地）。**

---

## 8. 紅線遵循與意外

- ✅ 全程 0 檔案改動（`data/**`、`src/**`、`logs/ENGINEERING_STATE.md` 皆未動）；本次僅新增本文件，未 commit、未 push ⇒ `HEAD == origin/main == bf1efa3`。
- ✅ 未開啟 `data/faulthandler.log`（僅在檔案列舉時讀到 size/mtime）；未清理/輪替任何 log。
- ✅ 未重啟/未 kill；未執行 `server_ops` / watchdog / Plan A；未在命令列出現受禁字串。
- ✅ 未對任何角色發訊息；未觸碰 `relationships.json` 或其任何會改 `last_interaction_at` 的路徑（全部為 `Get-Content -Raw` / `read` 純讀）。
- 意外（順手發現，供主大腦參考）：
  1. **`feeling` 全庫 100/100 恆 `neutral`**，0 生產寫入者（`touch(feeling=...)` 無任何呼叫端）⇒ 決策判詞的第二個理由同樣結構性凍結。
  2. **`motive_trace.jsonl` 的 `created_at` 時區偏移混用**（`+00:00` 與 `-04:00` 同瞬間），而 `resolve_pending` 用**字串排序**取最新（`src/soul/motive.py:384`）⇒ pending 選取在跨偏移時並非時間序（本案兩筆同瞬間，實際等了 `+00:00` 那筆）。
  3. **10 個 peer entry 擱淺**（§2.5）：SG-3 門檻重標定與 SG-2.1 慢爬閘門時間錯位，製造「差一步」的計數器；`familiar→close` 目前 0/100 可達（門檻 `co≥4 且 reply≥4`，而 `E[gap_pair] ≈ 44.5 天` ⇒ 中位 164 天）。
  4. **檔案層 `schema_version` 半遷移**：14/15 檔仍 `4.1`，4.2 欄位靠 `setdefault` 惰性補齊；下次 additive 欄位會重演 SG-2.2 的爆炸模式。
