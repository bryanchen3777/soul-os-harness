# 工單：DSH-MVP-0 — Implementation Baseline & Vertical-Slice Preparation

**日期**：2026-08-23
**性質**：MVP implementation（READ-ONLY baseline，不改 code）
**上游**：`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate 10/10 PASS → MVP AUTHORIZED）

---

## 目標

對目前 repo / DSH integration 做 READ-ONLY baseline，定位 Work Contract / Adapter / durable store / authority boundary 的落點，確認 DSH 原生 primitives，設計最小 vertical slice 的 module boundary，產出 MVP-1 可直接施工的 implementation plan。

## 範圍（READ-ONLY）

- 檢查 repo 結構：`C:\Users\bbfcc\.local\bin\soul-os-harness`
- 檢查 DSH 現有 API：`C:\Users\bbfcc\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh`
- 讀取 4 份 canonical contracts：`docs/DSH-WORK-CONTRACT.md`（2A）、`docs/DSH-WORKSPACE-DESIGN.md`（2B）、`docs/DSH-HUMAN-AUTHORITY.md`（2C）、`docs/DSH-PERSISTENCE.md`（2D）

## 做法（決策已定）

1. 定位 Work Contract 落點（哪個 module 承載 Work schema）
2. 定位 Adapter 落點（哪個 module 承載 DSH coupling）
3. 定位 durable store 落點（哪個 module 承載 WorkEvent log）
4. 定位 authority boundary 落點（哪個 module 承載 capability policy + approval）
5. 確認 DSH 現有：subagent API、workflow API、goal API、agent preset / persona、session / event trace
6. 設計最小 vertical slice 的 module boundary
7. 只提出 implementation plan，不開始大規模施工

## 驗收（Success Gate）

- Contract untouched PASS
- DSH native primitives mapped PASS
- Adapter seam identified PASS
- Durable boundary identified PASS
- Authority boundary identified PASS
- No premature implementation PASS

## 不做（Out of Scope）

- 不修改 2A–2D contracts
- 不碰 Soul OS 核心 Inner Life / Memory architecture
- 不建立第二套 orchestration engine
- 不寫 MVP code
- 不改 Soul OS runtime

## 回報格式

- proposed files / modules
- existing APIs
- gaps
- risks
- MVP-1 可直接施工的工單
