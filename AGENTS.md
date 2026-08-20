# Soul OS — 專案工作流程 (AGENTS.md)

本文件由 DSH 的 `dsh-agent-instructions` 自動載入為**專案指令**，疊在全域指令之上。
全域的「角色分工 + 省 token 鐵律 + 通用迭代流程」請見 `$DSH_HOME/AGENTS.md`（`~/.dsh/AGENTS.md`），本文件只放 Soul OS 專屬規則。

---

## Owner（Bryan）

- 只決定：**花錢的事**（換模型、加付費外部服務、改 LLM 成本結構）。
- 喜好建議：給出方向性偏好，由主大腦吸收轉成設計決策。
- 其餘工程/原則事務，主大腦可自行決策。

---

## 工單範本（decision-complete）

主大腦出的工單必須「決策完整」——執行者拿到就能做，**不需要再做任何設計決策**。

```markdown
## 工單：<標題>

### 目標
<一句話：要做什麼、為什麼>

### 範圍
<明確列出要動的檔案 / 模組>

### 做法（決策已定，執行者照做）
- <具體步驟 / 設計決策，已由主大腦拍板>

### 驗收（完成的定義）
- <可驗證的結果，例如：特定測試通過 / 特定行為出現>

### 測試
- <要寫哪些測試 / 要跑哪些現有測試>

### 不做（Out of Scope）
- <明確列出不要動的，避免執行者超範圍>

### Frozen Contract 注意
- <本工單不得碰的 frozen contract，參照 logs/ENGINEERING_STATE.md>

### 回報格式
- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
```

---

## 專案治理對齊

- **Canonical 狀態**：`logs/ENGINEERING_STATE.md` 是單一事實來源。文件更新以它為準。
- **Frozen Contract**：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 等，未經主大腦 + Owner 許可不得改動。
- **花錢事項**：一律回報 Owner (Bryan) 拍板，主大腦不得自行決定。
- **驗證紀律**：每階梯走「mock test → 回歸 → commit → 重啟 → 觀察」。實作後主大腦必須驗證，不能只信執行者口頭回報。
