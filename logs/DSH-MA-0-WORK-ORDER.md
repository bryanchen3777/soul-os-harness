# 工單：DSH-MA-0 — Multi-Agent Environment Architecture & Adapter Boundary Audit

**日期**：2026-08-23
**性質**：READ-ONLY architecture audit（不寫 code）
**上游**：`logs/ENGINEERING_STATE.md` §5.8（DSH Multi-Agent MVP COMPLETE / ACCEPTED）

---

## 目標

回答三個架構問題，作為 DSH Adapter 實作的前置設計：

1. **DSH 到底負責什麼、Domain Core 到底負責什麼**
2. **Chief / Specialist / Subagent / Workflow / Goal 如何映射**
3. **Soul OS 放進這個環境後，哪些東西能自由演化、哪些 boundary 必須永久鎖死**

## 範圍（READ-ONLY）

- 讀 DSH primitives：`C:\Users\bbfcc\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\` 下的 `dsh-subagent` / `dsh-workflow` / `dsh-goal` / `dsh-agent-presets` / `dsh-persona` / `dsh-session` README
- 讀 Domain Core：`src/work/` 十一個模組（schema / state_machine / store / ports / bridge / roles / kernel / workflow / authority / persistence / e2e）
- 讀 4 份 contract：`docs/DSH-WORK-CONTRACT.md`（2A）、`DSH-WORKSPACE-DESIGN.md`（2B）、`DSH-HUMAN-AUTHORITY.md`（2C）、`DSH-PERSISTENCE.md`（2D）

## 做法（決策已定，執行者照做）

1. **責任邊界**：DSH（execution：subagent / workflow / goal / session / tools）vs Domain Core（durable truth + authority：WorkEvent log / AuthorityStore / single-writer / HumanAuthorityPort）的明確劃分。核心原則：Soul OS owns the durable work truth. DSH owns ephemeral execution.
2. **映射表**：Chief → subagent（role=chief preset）、Specialist → subagent（role=developer/tester/auditor preset）、多階段 → workflow、自動續輪 → goal、session trace → dsh-session、capability → DSH tool、resume_state → Adapter 重掛。
3. **演化 vs 鎖死**：哪些 boundary 永久鎖死（2A–2D invariants、single-writer store-level、HumanAuthorityPort seam、DSH coupling 只在 Adapter、No-DSH Survival），哪些能自由演化（DSH Adapter 的實作細節、preset 內容、tool 選擇）。

## 驗收（完成的定義）

- 3 個問題有明確答案。
- 責任邊界表（DSH vs Domain Core）。
- 映射表（Chief/Specialist/Subagent/Workflow/Goal → DSH primitive）。
- boundary 鎖死清單（永久鎖死 vs 可演化）。

## 不做（Out of Scope）

- 不寫 code、不建 DSH plugin、不實作 Adapter。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1~7 的檔案。

## 回報格式

- 3 個問題的答案
- 責任邊界表
- 映射表
- boundary 鎖死清單（永久鎖死 vs 可演化）
- 剩餘 architectural concerns
