# Cross-Agent Interaction 設計稿（角色互相知道 + 互動 + 對話）

**日期**: 2026-08-22
**作者**: pro 主大腦（據 Owner 拍板）
**狀態**: 設計完成，待派工
**核心約束**: **絕對避免無線迴圈（LLM 拼命呼叫）**

---

## 一、目標

讓 10 隻角色互相知道、互相互動、互相對話，增加生活的多彩多姿。三層都做，對話 3 輪，開一個「內部交流板塊」讓 Owner 看到誰跟誰有交流。

---

## 二、三層設計

### Layer 1 — 互相知道（cross-agent awareness）

角色知道「我跟其他角色熟不熟」，不只「我跟 Bryan 熟不熟」。

- 擴 `src/llm/proxy.py` 的 `_format_relationship_block`（M5.13-3）：除了 Bryan，也注入 **top 3 其他角色**的 confidence band（認識 / 陌生人）。
- 純 prompt 注入，零迴圈風險。

### Layer 2 — 互相互動（shared activity）

有時抽 2 隻角色**一起做一件事**，兩隻都寫進 diary。

- 新 scheduler 觸發 `shared_event`：抽 2 隻角色 + 一個活動（沿用 `ACTIVITY_POOL`）。
- 兩隻各自寫 diary（「今天和 X 一起做了 Y」）。
- 單一事件、無對話、零迴圈風險。

### Layer 3 — 互相對話（cross-chat）

抽 2 隻角色進行**有界 3 輪對話**（A 開 → B 回 → A 收）。

- 新 scheduler 觸發 `cross_chat`：抽 2 隻角色。
- 3 輪對話，每輪是 **scheduler 明確驅動的 LLM call**（不是事件驅動）：
  - Turn 1：A 開場（system = A 的 persona + 「正在跟 B 聊天」，user = 「說一句話開場」）
  - Turn 2：B 回應（system = B 的 persona + 「正在跟 A 聊天」，user = 「A 說：『…』，回應」）
  - Turn 3：A 收尾（system = A 的 persona，user = 「B 說：『…』，回應收尾」）
- 結束後記錄到 interactions log。

### 內部交流板塊（board）

- 新 endpoint `GET /api/soul/interactions`：回傳最近的 shared_event + cross_chat 記錄。
- UI 加一個「交流」區塊，顯示「誰跟誰一起做了什麼 / 聊了什麼」。

---

## 三、無線迴圈防護（核心，不可違反）

迴圈根源 = 「A 說話 → 觸發 B → B 說話 → 觸發 A → …」。解法是**打斷觸發鏈**：

1. **scheduler 驅動，不是事件驅動**：對話由 scheduler 主動開，逐輪明確呼叫 A、B、A。**A 的訊息不會觸發 B**——是 scheduler 叫 B 回。
2. **有界輪數**：最多 3 輪，到點就結束。
3. **限頻 + 全體冷卻**：cross_chat 每 6-12h 最多一次，且全體共用冷卻（不會 10 隻同時開聊）。
4. **不自我觸發**：cross_chat 的 LLM call **不 publish AGENT_INTENT / AGENCY_TRIGGER**，不觸發其他角色、不進 Bryan 群聊路徑。
5. **與 Bryan 群聊隔離**：cross_chat 是獨立路徑，Bryan 的訊息不會點燃 cross_chat，cross_chat 也不會打擾 Bryan。

---

## 四、資料模型

- 新檔 `data/soul/interactions.jsonl`（append-only）：
  - `shared_event`: `{ts, type: "shared_event", agents: [A, B], activity, content}`
  - `cross_chat`: `{ts, type: "cross_chat", agents: [A, B], messages: [{agent, content} x3]}`

---

## 五、實作範圍

| 檔案 | 改動 |
|------|------|
| `src/llm/proxy.py` | Layer 1：`_format_relationship_block` 注入 top 3 cross-agent |
| `src/soul/scheduler.py` | Layer 2 + 3：`shared_event` + `cross_chat` 觸發 + 限頻冷卻 |
| `src/soul/dream_event.py` | Layer 2：shared activity 寫 diary |
| `src/io/gateway.py` | board：`/api/soul/interactions` endpoint |
| `static/index.html` | board：交流區塊 |
| `tests/` | 新測試（含迴圈防護測試） |

---

## 六、非目標（Out of Scope）

- 不改 Agency 4-stage / TriggerEnvelope / InnerLifeEvent / 4 handlers（frozen）。
- 不碰 History（仍 NOT AUTHORIZED）。
- 不讓 cross_chat 觸發 Bryan 的主動傳訊（proactive_dm 仍只走想念驅動）。
- 不讓角色對話變成「無限反應鏈」——3 輪封頂，scheduler 驅動。

---

*本設計稿供派工。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
