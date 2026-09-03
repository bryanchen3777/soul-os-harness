# Temporal Memory & Mem0 Primitives Contract（MR-1）

**工单**: MR-1 — Temporal Memory & Mem0 Primitives Contract
**阶段**: 设计（docs-only，0 code）
**日期**: 2026-09（MR-1）
**作者**: Developer（执行者，flash subagent）
**性质**: **非施工授权** — 本文件是设计契约，不授权任何代码改动。实作属 MR-2，须另行派工。
**Canonical 状态**: 以 `logs/ENGINEERING_STATE.md` 为准；本文件不改变任何 frozen contract。

---

## 1. 背景与目标

### 1.1 问题（MR-0 审计已确认，直接采信）

SAGE facts 层是 Soul OS 唯一没有时序维度的记忆层：

| 缺口 | 证据 |
|------|------|
| 无 `valid_from` / `invalidated_at` | `graph_store.py:110-121`（v1 schema，16 列）+ 实况 PRAGMA（agent_ruka graph.sqlite） |
| `event_time` 生产数据 100% NULL | `writer.py:558`（LLM 路径硬编码 `event_time=None`）+ 实况（agent_ruka 368 facts 全 NULL） |
| 覆写破坏历史 | `add_fact` 用 `INSERT OR REPLACE`（`graph_store.py:255`），同 fact_id 重写时旧版本被覆盖 |
| 硬删除不可回溯 | `remove_fact` 硬 DELETE（`graph_store.py:305-316`），`evolution._prune`（`evolution.py:245-252`）与 `_merge`（`evolution.py:297`）都走硬删 |

**结果**: 无法回答「特定时间点灵魂眼中的世界」——无法回溯、无法软删、无法表达「事实何时不再为真」。

### 1.2 目标

设计最小增量 A+B+C+D（全 additive，0 破坏 frozen contract），为 SAGE facts 层补上时序能力：

- **A. Schema v7 迁移 SQL**：facts 表加 `valid_from` / `invalidated_at` 列 + 历史数据回填。
- **B. GraphStore 新方法 API 签名**：`invalidate_fact`（软删）+ `get_facts_as_of`（时序回溯）。
- **C. Mem0 原语模块 Interface 规范**：独立模块，显式 `add_fact` / `update_fact` / `delete_fact` / `resolve_conflict`。
- **D. SAGE Reader as_of 扩展**：`retrieve_context(..., as_of=None)`，默认自动过滤已作废事实。

### 1.3 设计原则（锁定）

1. **只加不改**：新列（NULL 默认）、新方法、新模块、新参数（默认值向后兼容）——全部 additive。
2. **不碰 frozen contract**：SAGE 写入逻辑（`writer.py` 抽取/合并/矛盾）、v1 schema/store/loader、M5.10、M5.13-2、15 contracts 一律不动。
3. **复用已验证模式**：soul-elevation 层「Forgetting = lifecycle transition 不是 delete」+ 双时序语义（`docs/MEMORY-ELEVATION-DESIGN.md:99` 决策 E、SE-5 实作）平行应用到 SAGE facts 层。
4. **显式原语层独立**：Mem0 式原语不侵入现有隐式流程，作为新 API 提供。

---

## 2. A. Schema v7 迁移 SQL

### 2.1 迁移 SQL（`graph_store.py` `_migrate` 加 `from_version < 7` 分支）

```sql
-- 1. 加列（NULL 默认 = 向后兼容；仿 M5.4-5.2 inner_life_event_id 先例 graph_store.py:193-205）
ALTER TABLE facts ADD COLUMN valid_from REAL;        -- 事实开始有效的时间；NULL = 未知（迁移后不应存在）
ALTER TABLE facts ADD COLUMN invalidated_at REAL;    -- 事实失效的时间；NULL = 当前仍有效（永不过期）

-- 2. 历史数据回填（关键：event_time 100% NULL，不可用；用写入时间 timestamp 作为 valid_from 近似）
UPDATE facts SET valid_from = timestamp WHERE valid_from IS NULL;

-- 3. 时序查询索引（additive）
CREATE INDEX IF NOT EXISTS idx_validity ON facts(valid_from, invalidated_at);
```

### 2.2 回填逻辑（关键决策）

- **回填源 = `timestamp`（写入时间），不是 `event_time`**。理由：MR-0 审计确认 `event_time` 生产数据 100% NULL（`writer.py:558` LLM 路径硬编码 None），只有 regex fallback 填（`writer.py:761,784`）。`timestamp` 是每条 fact 必填的写入时间戳（`models.py:12`，`NOT NULL`），语义 = 「这条事实被记录的时刻」，是 `valid_from` 的合理近似。
- **回填时机**：在 `ALTER TABLE` 之后、`schema_meta` 版本写入之前，同一迁移分支内执行。保证迁移完成后 `valid_from` 无 NULL（`timestamp` NOT NULL，故 `UPDATE ... WHERE valid_from IS NULL` 全覆盖）。
- **`invalidated_at` 不回填**：默认 NULL = 当前有效。历史数据全部视为「现在仍有效」，与迁移前行为逐位一致（迁移前所有 fact 都可被读到）。
- **幂等**：`ALTER TABLE` 用 try/except `sqlite3.OperationalError` 包裹（列已存在时跳过），与既有 v2-v6 迁移分支写法一致（`graph_store.py:132-205`）。

### 2.3 向后兼容方案

| 层面 | 兼容方式 |
|------|----------|
| 旧 DB 文件（v6 及以下） | `_migrate` 依序执行到 v7，`schema_meta.version` 6→7；旧 368+ facts 全保留 |
| 旧代码读新列 | `SELECT *` 多出 2 列，`_row_to_fact` 需 `setdefault("valid_from", None)` / `setdefault("invalidated_at", None)`（MR-2 实作，仿 `inner_life_event_id` 先例 `graph_store.py:218-220`） |
| `Fact` dataclass | 加 2 个 `Optional[float] = None` 字段（MR-2 实作，仿 `models.py:35` 先例）；`to_dict` / `from_dict` 同步 setdefault |
| 新代码读旧数据 | 新列 NULL → `Fact` 默认 None → 语义「valid_from 未知 / 永不过期」 |
| 既有查询 | 默认路径自动过滤已作废（见 §5），迁移后无已作废事实 → 行为与现状逐位一致 |

---

## 3. B. GraphStore 新方法 API 签名

### 3.1 `invalidate_fact`（软删除）

```python
@_locked
def invalidate_fact(self, fact_id: str, at_time: Optional[float] = None) -> bool:
    """软删除：标记 invalidated_at，不动 remove_fact 硬删语义。

    Args:
        fact_id: 要失效的 fact id。
        at_time: 失效时刻（unix float）。None = time.time()（当前时刻）。

    Returns:
        True  = 已标记失效（或已处于失效状态，幂等）。
        False = fact_id 不存在。

    语义（锁定）:
        - 只 UPDATE facts SET invalidated_at = ?，绝不 DELETE。
        - 幂等：已失效的 fact 再次调用返回 True，且**保留最早的 invalidated_at**
          （不覆盖、不把失效时间往后推——防止重复调用缩短有效区间）。
        - 同步更新内存图 edge 的 invalidated_at 属性（graph 与 DB 一致，
          仿 update_weight 双写模式 graph_store.py:284-302）。
        - 与 remove_fact（硬删）互不调用：硬删 = 既有 decay/prune 语义（frozen），
          软删 = 新显式原语语义。两者并存，文档明确。
    """
```

### 3.2 `get_facts_as_of`（时序回溯查询）

```python
@_locked
def get_facts_as_of(self, as_of_time: float) -> list[Fact]:
    """时序回溯查询：返回在 as_of_time 时刻有效的事实。

    SQL（锁定）:
        SELECT * FROM facts
        WHERE (valid_from IS NULL OR valid_from <= ?)
          AND (invalidated_at IS NULL OR invalidated_at > ?)
        ORDER BY weight DESC, timestamp DESC

    边界语义（半开区间 [valid_from, invalidated_at)）:
        - valid_from <= as_of_time：事实从 valid_from 时刻起有效（含边界）。
        - invalidated_at > as_of_time：事实在 invalidated_at 时刻仍有效，
          失效从 invalidated_at 之后开始（含边界）。
        - NULL valid_from（理论残留）视为无起点；NULL invalidated_at 视为永不过期。
    """
```

### 3.3 既有方法零改动（锁定）

- `remove_fact`（硬删）、`update_weight`、`set_anchor`、`update_merge_lineage`、`add_fact`（INSERT OR REPLACE）**签名与语义一律不动**。
- `evolution.py` 的 `_prune` / `_merge` / `_conflict_flag` 继续走硬删（frozen 语义），**不改为软删**。软删只由新原语层与 `invalidate_fact` 触发。

---

## 4. C. Mem0 原语模块 Interface 规范

### 4.1 模块位置

新独立模块 `src/memory/primitives.py`（MR-0 审计建议 `src/memory/temporal/primitives.py`，工单拍板 `src/memory/primitives.py`，以工单为准）。

### 4.2 Interface（锁定）

```python
class MemoryPrimitives:
    """Mem0 式显式记忆原语层。只提供新 API，不拦截/改写既有 SAGE 写入管线。

    绝不破坏: writer._write_single 隐式流程（抽取/合并/矛盾）、
    evolution 硬删 prune、v1 mirror。原语层与隐式流程互不调用。
    """

    def __init__(self, graph_store: GraphStore) -> None:
        """持有 GraphStore 引用。不持有 writer/evolution。"""

    def add_fact(self, fact: Fact) -> str:
        """显式新增。映射到 graph_store.add_fact。
        - fact.valid_from 未设置时默认 = time.time()（MR-2 实作在写入前填充）。
        - 返回 fact_id；失败返回 ""（与 writer.add_fact 返回约定一致）。
        """

    def update_fact(self, fact_id: str, new_fact: Fact,
                    reason: str = "update") -> str:
        """显式更新：写新版本 + 失效旧版本 + lineage。
        - 新 fact 写入（新 fact_id，valid_from = now）。
        - 旧 fact invalidate_fact(fact_id, now)（软删，可回溯）。
        - 新 fact.merged_from = [fact_id]，merge_reason = reason。
        - 返回新 fact_id；旧 fact_id 不存在时返回 ""（不静默创建）。
        """

    def delete_fact(self, fact_id: str, reason: str = "delete") -> bool:
        """显式删除（软删）。映射到 invalidate_fact(fact_id, now)。
        - 绝不硬删。reason 仅用于日志/审计（MR-2 可考虑写入 merge_reason 或独立审计表，
          本契约不新增表）。
        - 返回 invalidate_fact 的结果。
        """

    def resolve_conflict(self, winner_id: str, loser_id: str,
                         reason: str = "conflict") -> bool:
        """冲突解决：winner 保留 + loser 失效 + lineage 记录。
        - invalidate_fact(loser_id, now)（软删，loser 可回溯）。
        - update_merge_lineage(winner_id, merged_from=[loser_id], reason)
          （复用既有 graph_store.update_merge_lineage，graph_store.py:516-536）。
        - 返回 True = 成功；winner/loser 任一不存在 = False。
        """
```

### 4.3 与既有流程的关系（锁定）

| 流程 | 关系 |
|------|------|
| `writer.add_fact` / `extract_and_write` / `write_turn`（隐式） | **原封不动**。原语层不拦截、不改写、不替换。 |
| `evolution._prune` / `_merge`（硬删） | **原封不动**。原语层不调用 evolution，evolution 不调用原语层。 |
| `graph_store.update_merge_lineage` | 原语层**复用**（只读调用，不改其签名）。 |
| v1 mirror（`_mirror_to_v1_store`） | **不涉及**。原语层不写 v1。 |

---

## 5. D. SAGE Reader as_of 扩展

### 5.1 签名扩展（additive 参数）

```python
def retrieve_context(
    self,
    query: str,
    top_k: int = 5,
    max_hops: int = 2,
    max_tokens: int = MAX_TOKENS_DEFAULT,
    min_weight: float = 0.1,
    mode: RecallMode = "balanced",
    boost_tags: Optional[list[str]] = None,
    source_pair_filter: Optional[set[str]] = None,
    as_of: Optional[float] = None,   # NEW（MR-1 契约，MR-2 实作）
) -> ContextResult:
```

### 5.2 关键不变量（工单锁定，最高优先）

> **`as_of is None`（默认）时，SQL 自动过滤 `WHERE invalidated_at IS NULL`——既有调用端自动享受软删红利，永不读到已作废旧事实。**

- **默认路径（as_of=None）**：`search_by_entity` / `get_all_facts` 的 SQL 自动加 `AND invalidated_at IS NULL`。实现方式（MR-2 二选一，推荐 ①）：
  - ① 给 `search_by_entity` / `get_all_facts` 加 additive 参数 `include_invalidated: bool = False`（默认 False = 过滤已作废）。既有调用端（reader 默认路径、middleware prefetch）零改动自动享受红利。
  - ② 直接改 SQL 加过滤条件（更简，但 `export_json` / `stats` 等需要全量视角的调用端需显式豁免——见 §5.3）。
- **as_of 给定**：候选集来源改用 `get_facts_as_of(as_of)`（替代 `search_by_entity` / `get_all_facts`），其余评分/多样性/链构建逻辑不变。
- **评分不变**：recency 仍用 `f.timestamp`（写入时间，`reader.py:132`），不因 as_of 改变——as_of 只决定「哪些事实可见」，不改变「如何排序」。

### 5.3 全量视角豁免（锁定）

| 调用端 | 行为 | 说明 |
|--------|------|------|
| `export_json` | 导出**全部**（含已作废） | 备份/迁移用途，历史完整性优先 → 调 `get_all_facts(min_weight=0.0, include_invalidated=True)` |
| `stats()` | 保持现状（全部计数） | 可加 `active_facts` 区分（MR-2 可选，不强制） |
| `get_fact(fact_id)` | 保持现状（单条直查，不过滤） | 显式按 id 查询，调用端自行判断 |
| `get_anchor_facts` | 保持现状 | 锚点保护语义不变（MR-2 评估是否过滤，本契约不锁定） |

### 5.4 向后兼容验证（MR-2 验收用）

- [ ] `as_of=None` 时，迁移后（无已作废事实）行为与迁移前**逐位一致**（回归）。
- [ ] `invalidate_fact` 后，`as_of=None` 的 `retrieve_context` 不再返回该 fact（软删红利自动生效）。
- [ ] `invalidate_fact` 后，`get_facts_as_of(失效前)` 含该 fact；`get_facts_as_of(now)` 不含。
- [ ] `as_of` 给定时的候选集 = `get_facts_as_of` 结果，评分/多样性/链构建与默认路径共用同一套逻辑。

---

## 6. 验收清单（MR-1 设计完成度）

- [x] Schema v7 迁移 SQL + 回填逻辑（§2）
- [x] GraphStore API 签名：`invalidate_fact` / `get_facts_as_of`（§3）
- [x] Mem0 原语 Interface：`add_fact` / `update_fact` / `delete_fact` / `resolve_conflict`（§4）
- [x] as_of 过滤逻辑 + 向后兼容验证（§5）
- [x] 明确「只设计，0 code」（本文件头部 + §7）
- [x] 不碰 frozen contract（§7）

---

## 7. 范围声明

### 本工单（MR-1）只设计，0 code

- 本工单**不产生任何代码改动**。所有 SQL / 签名 / Interface 均为设计契约，供 MR-2 实作。
- 不 commit、不 push（等验收）。

### Out of Scope（MR-2 才做）

- 实作：`graph_store.py` v7 迁移分支、`models.py` Fact 加字段、`primitives.py` 新模块、`reader.py` as_of 参数、`search_by_entity` / `get_all_facts` 过滤参数、测试。
- 落点 E（CONFLICT_RESOLVE 显式化接入 evolution）——触碰 evolution 行为，需单独工单评估（MR-0 审计 §5.2 落点 E 明确不并入最小增量）。

### Frozen Contract 声明（本工单 0 触碰）

| Frozen Contract | 状态 |
|----------------|------|
| SAGE 写入逻辑（`writer.py` 抽取/合并/矛盾/镜像） | **不动** |
| v1 schema / store / loader（frozen=True + append-only + Bry §12） | **不动** |
| M5.10（LLM Judge 事件契约） | **不动** |
| M5.13-2（STRICT FROZEN：confidence ≥ 0.3 → 認識） | **不动** |
| 15 contracts（M3 / M3.1 ABC / M3.1 Bus / M5.4-5.1 InnerLifeEvent 9 fields / parent_event_id / lineage / M5.9-2 QUALIFYING_TYPES / M5.9-3 Adapter / M5.15-3 canonical bus path / M5.15-5 source_world_event_novelty_id / VALID_SOURCES / _NOVELTY_ID_RE） | **不动** |
| `remove_fact` 硬删语义 + evolution prune/merge/conflict 行为 | **不动**（软删为新增并行语义） |

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 落点 A 改 `graph_store.py` 被误判为「动 SAGE」 | 对照 M5.4-5.2 先例（`graph_store.py:193-205` 加 inner_life_event_id 列）——同类 additive 扩展已验证；本工单仅设计，实作时 MR-2 需同样声明 |
| `invalidate_fact` 与 evolution 硬删并存 → 双语义 | 文档明确：硬删 = 既有 decay/prune 语义（frozen）；软删 = 新显式原语语义；两者互不调用（§3.3） |
| 原语层与现有隐式流程重叠 | 原语层只提供新 API，不拦截/改写 `_write_single` 流程（§4.3） |
| as_of 查询效能 | `idx_validity ON facts(valid_from, invalidated_at)` 索引（§2.1） |
| 默认过滤已作废改变既有行为 | 迁移后无已作废事实 → 行为逐位一致；软删红利是预期增强，非回归（§5.4） |
