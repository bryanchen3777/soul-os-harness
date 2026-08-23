# 工單：DSH P1-A — Execution Target Contract

**日期**：2026-08-23
**性質**：Phase 1 implementation（可寫 code）
**上游**：`docs/DSH-P1-EXECUTION-ROUTING.md`（re-review READY FOR IMPLEMENTATION）、`docs/DSH-ADAPTER-BUILD-PLAN.md`（MA-4，IMPLEMENTATION AUTHORIZED）、baseline `34e91d4`

---

## 目標

落地 Execution Target Contract 的 **Domain Core 側**：新增 capability-neutral 的 `ExecutionShape` enum + shape 推導函數，並讓 BridgeMessage payload 承載 `execution_shape`。這鎖定「誰決定 target」的答案：**Soul 決定（推導），adapter 只 translate（映射）**。

本工單**不實作**真 DSH subagent / workflow / goal 呼叫（那是 P1-C），只把 contract 落地。

## 範圍（只允許）

- `src/work/schema.py`：新增 `ExecutionShape` enum（`single_shot` / `multi_stage` / `continuous`）——**授權改動**（decomposition §8 明記）
- `src/work/`（新增或既有模組）：新增 `derive_execution_shape(work) -> ExecutionShape` 推導函數
- `src/work_adapter/execution.py`：`build_execution_request` 的 payload 新增 `execution_shape` 欄位
- 測試

## 做法（決策已定，執行者照做）

### 1. `ExecutionShape` enum（`src/work/schema.py`）

```python
class ExecutionShape(str, Enum):
    """執行型態（capability-neutral，Domain Core contract，2A §7 / P1 decomposition §3）。

    不是 DSH primitive 名。Soul 從 Work Object 語義推導，adapter 才映射到
    DSH primitive（subagent / workflow / goal）。
    """
    SINGLE_SHOT = "single_shot"
    MULTI_STAGE = "multi_stage"
    CONTINUOUS = "continuous"
```

放在 `WorkState` / `WorkEventType` 等 enum 附近（§1 枚舉區）。

### 2. `derive_execution_shape(work: WorkObject) -> ExecutionShape`（Domain Core）

放 `src/work/workflow.py`（Chief 的 decision 閉環所在，shape 是 Chief/Soul 的 orchestration 決策）。決策規則（decomposition §3.3，操作級）：

```python
def derive_execution_shape(work: WorkObject) -> ExecutionShape:
    """從 Work Object 語義推導 execution shape（Soul 決定，adapter 只 translate）。

    規則（P1 decomposition §3.3）：
    - work.dependencies 非空 → multi_stage
    - 需「無 human/chief 介入的多輪自動續輪」→ continuous
    - 其餘（含 blocked 後單輪 specialist resume）→ single_shot

    resume discriminator（關鍵）：blocked 後由單一 specialist 再 handoff 一輪
    完成（blocked → resume 回 in_progress → consume_handoff）是 single_shot，
    不是 continuous。continuous 只保留給 goal 驅動多輪自動續輪。
    """
    if work.dependencies:
        return ExecutionShape.MULTI_STAGE
    # continuous 的判別：目前 Work Object 無「goal 驅動」欄位。
    # 第一期：dependencies 永為 []（create_work 硬編 []），且無 goal 語義載體，
    # 故本函數在第一期實質只回 single_shot。continuous 的觸發條件待 P1-D
    # 定義（goal resume semantics），此處先以「resume_state 帶 continuous 標記」為
    # 預留判別點，但不實作（避免假驗證）。
    return ExecutionShape.SINGLE_SHOT
```

> 關鍵：**不實作 continuous 的觸發條件**。第一期 Work Object 沒有 goal 語義載體，任何「推測 continuous」都是假邏輯。derive 函數第一期只回 `single_shot`（dependencies 非空 → multi_stage 雖寫，但第一期無觸發路徑，用單元測試覆蓋）。這符合 decomposition §3.3「dependencies 永為 [] → multi_stage 是 dead logic，用單元測試覆蓋」的要求。

### 3. `build_execution_request` 新增 `execution_shape`（`src/work_adapter/execution.py`）

payload 新增：

```python
payload={
    "work_id": work.work_id,
    "objective": work.objective,
    "role": role,
    "capability": capability,
    "execution_shape": derive_execution_shape(work).value,  # ← 新增
    "resume_state": work.resume_state.model_dump(mode="json"),
},
```

import `derive_execution_shape` from `src.work.workflow`。

## 驗收

1. `ExecutionShape` enum 存在，三值 `single_shot` / `multi_stage` / `continuous`，純 Python，零 DSH 引用。
2. `derive_execution_shape`：
   - 無 dependencies → `single_shot`
   - 有 dependencies → `multi_stage`（單元測試覆蓋，即使第一期無觸發路徑）
   - 不推測 continuous（第一期無 goal 語義載體）
3. `build_execution_request` 的 payload 含 `execution_shape`，值為 `derive_execution_shape(work).value`。
4. regression 全綠（239 + 新增）。

## 測試

新增 `tests/test_work_p1a_execution_shape.py`：
1. `ExecutionShape` enum 三值 + value 字串
2. `derive_execution_shape` 無 dependencies → single_shot
3. `derive_execution_shape` 有 dependencies → multi_stage（手工構造 WorkObject 帶非空 dependencies）
4. `derive_execution_shape` 零 DSH import（grep 或 import 檢查，與既有測試模式一致）
5. `build_execution_request` payload 含 `execution_shape` 且等於推導值
6. `execution_shape` 是 capability-neutral 字串，不含 DSH primitive 名（subagent/workflow/goal）

跑全 regression（含 test_work_adapter.py 27 + test_work_p1_preflight.py 9 + 其餘）。

## 不做（Out of Scope）

- **不實作**真 DSH subagent / workflow / goal 呼叫（P1-C）
- **不實作** continuous 觸發條件（P1-D，需 goal 語義載體）
- **不實作** multi_stage 的 workflow script 渲染（P1-D，A6 決策）
- **不實作** artifact store / refs content-address（P1-B）
- 不改 `src/work_adapter/bridge.py`、`src/work/kernel.py`、`dsh_adapter/`（P1-C 才動）
- 不改 `docs/` frozen contract（2A–2D）、不改 `docs/DSH-P1-EXECUTION-ROUTING.md`
- 不接 Soul runtime / eventbus / World / Inner Life / Agency

## Frozen Contract 注意

- `ExecutionShape` 是**新增** enum，不修改既有 `WorkState` / `ResultType` / `HandoffStatus`。
- 不改 2A §6 HandoffResult、§7 mapping、§8 non-negotiables。
- Domain Core 零 DSH import 永久不變（`ExecutionShape` 三值非 DSH primitive 名）。

## 回報格式

- 改動檔案清單
- derive_execution_shape 的實作方式 + 為何第一期不實作 continuous
- 新增測試清單 + 結果
- 完整回歸結果
- 確認 `git diff --stat` 只動列名檔案、`src/work/` 零 DSH import 不變
- 剩餘 architectural concerns
