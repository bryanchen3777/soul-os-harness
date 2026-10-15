# SOUL-BUG-001 — 昇降持久層去重連帶丟棄證據邊，EL-DD-2 閘門結構性不可達

**工單編號**：SOUL-BUG-001
**嚴重度**：**P0（邏輯致命，生產未爆但時鐘已啟動）**
**狀態**：**✅ CLOSED（Bry 2026-10-14 裁示 A′ 方案並授權施工）**｜xfail 看門狗已轉正為常態回歸
**發現方式**：SOUL-TECH-DEBT-001 C 類深度鑑識
**引入 commit**：`fb7ab63`（2026-10-11）`feat(elevation): _persist_result idempotent on (agent,node_type,content_hash) (VALUE-DEDUP-A-1)`

---

## 0. ✅ 結案摘要（Bry 2026-10-14 裁示「節點合併 + 邊重綁 + 邊去重」）

**採 A′ 方案：節點冪等 100% 保留，證據邊改為重綁到 canonical 節點。**

### 樞紐：落盤去重鍵與引擎消費鍵必須同構

| | 鍵 |
|---|---|
| 落盤邊去重 | `(node_id, source_id, inner_life_event_id)` |
| 引擎「已消化」消費鍵（`_rebuild_engine_for`） | `(source_id, inner_life_event_id)` |
| SE-1 獨立證據鍵（`soul_elevation`） | `(source_id, event_identity)` |

⇒ **同一事件重跑**：三鍵相同 → 收斂 → 邊仍 1 條（冪等守住）
⇒ **跨事件同內容**：三鍵相異 → 合法累積 → `min_evidence=2` 達標

兩個需求因此在**同一個數學模型**下相容，而非互相妥協。

### 三處程式改動（全在 `src/inner_life/elevation_adapter.py`）

1. `_load_existing_dedup_keys` → **`_load_existing_dedup_index`**：回傳 `key -> canonical_node_id`（原版只回 set，無從得知該綁去哪）。canonical 取**檔案順序第一列**（`setdefault` 語意：已有值為 None 才補位）。
2. `_persist_result`：`skipped_node_ids` 拆成 `rebind: dict` + `unrebindable: set`。重複節點仍不寫入；邊先 remap `node_id`，再丟 `unrebindable` 的邊（**順序不可反**）。
3. 新增 `_edge_identity()` / `_dedup_edges()`，並把邊去重**移出 `if node_records:` 區塊**（舊版無新節點時邊完全不去重）。

### 三組變異實驗（全部轉紅，守衛皆非恆真式）

| 變異 | 結果 |
|---|---|
| `MUT-1` rebind 退回舊的「丟邊」 | ✅ 4 支轉紅（watchdog + 3 支 persist） |
| `MUT-2` 停用邊層去重 | ✅ 2 支轉紅（`test_h1` + `test_same_event_reingest_edges_still_deduped`） |
| `MUT-3` 邊鍵漏掉 `inner_life_event_id` | ⚠️ **首輪未轉紅 → 覆蓋缺口 → 補 `test_same_source_different_event_both_edges_kept` → 復驗轉紅** |

### Bry 裁示的三個邊界

1. **跨 `source_type` 合併**：批准合併。節點承載命題、出處由邊承載；`sage_fact` 與 `inner_life_event` 若產出同一命題，合併為一顆 canonical 並讓兩種來源證據邊並存（跨模態雙重印證）。⇒ `_edge_identity` 刻意**不含** `source_type`。
2. **生產資料 30 列重複節點**：維持**不清倉、不回溯合併**（`docs/INNER-LIFE-CONSTRUCTABILITY-AUDIT.md:29` 已載明該決策）。修復只對未來寫入生效。
3. **`test_persist_idempotency.py` 斷言翻轉**：批准。並把 `test_partial_skip_only_dup_node_and_its_edges` **改名為** `test_duplicate_node_coalesced_edges_rebound`——原測試名本身即編碼了缺陷。

### 對「6 筆已校準測試」的自我更正

本工單立案時曾記載「修好會翻轉 6 筆已校準測試」。**該數字錯誤**。實際為 **3 支測試翻轉 ＋ 2 篇 docstring 改寫**；`test_elevate_mechanism.py` 與 `test_elevation_evidence_scope_a1.py` 共 21 支中 20 支與本缺陷無關（前者靠 `_make_distinct_events` 繞開 dedup 分支，後者手寫 JSONL 繞過 `_persist_result`）。

---

## 一、現象

`_persist_result`（`src/inner_life/elevation_adapter.py:353-396`）對**所有** node_type 做內容去重，且當節點被判定重複時，**連帶丟棄該節點的全部證據邊**：

```python
if key in existing:
    skipped_node_ids.add(rec.get("node_id"))
    continue
...
if skipped_node_ids:
    edge_records = [
        e for e in edge_records if e.get("node_id") not in skipped_node_ids
    ]
```

## 二、根因鏈

1. `_event_content`（`:128-139`）由 `trigger_type + provenance.extras` 推導 pattern 內容。
2. 生產事件的 `extras` **近乎恆定**：`scripts/run_server.py:1808` `extras={"slot": slot}`；`:1669-1672` `extras={target_agent_id, all_agents_count}`。
3. ⇒ 同一 `(agent, trigger, extras)` 產生的 pattern **內容 byte 相同**。
4. ⇒ 去重後每組只保留 1 個 pattern，其餘連同證據邊被丟棄。
5. ⇒ `_count_independent_evidence` 永遠無法達到 2 ⇒ **EL-DD-2 昇降閘門（`soul_elevation/engine.py:500-507`，`DEFAULT_ELEVATE_MIN_EVIDENCE=2`）對 `diary:night` / `dream:dream` 結構性不可達**。

**主大腦獨立驗證**（不採信執行者自報）：

```
$ 掃描 data/elevation/elevation_nodes.jsonl（唯讀）
總節點: 531   pattern 種類: 462
  13x  dream:dream: all_agents_count=10; target_agent_id=...
  11x  dream:dream: all_agents_count=10; target_agent_id=...
   9x  dream:dream: all_agents_count=10; target_agent_id=...
   5x  dream:dream: all_agents_count=10; target_agent_id=...
   5x  dream:event
```

內容格式本身就是 `trigger:extras` 的直接展開，**證明重複是生產常態而非測試假象**。

## 三、為何是附帶損傷

該去重的原始目標是 **`value` 節點**：`docs/INNER-LIFE-CONSTRUCTABILITY-AUDIT.md:29` 記載 80 個重複 value。套用到 `pattern` 節點並連帶丟棄其證據邊，**不在原始範圍內**。

## 四、時序風險（為何是 P0）

現存重複節點寫入於去重上線**之前**，服務尚未帶新代碼重啟。**真正生效時點＝服務重啟之後**。一旦重啟，昇降機制將靜默停擺——且因為一切照常運行、沒有錯誤、不會有任何告警。

## 五、修復方向（Bry 2026-10-12 裁示）

去重邏輯必須**嚴格限定在 `value` 節點**，或對 `pattern` 的證據邊執行 **Re-parent**（轉移至既有節點）。**絕不可直接丟棄。**

## 六、⚠️ 修復後的連帶影響（必須一併處理）

SOUL-TECH-DEBT-001 已將 `tests/test_elevate_mechanism.py` 與 `tests/test_elevation_evidence_scope_a1.py` 的 6 筆測試校準到「持久層去重」這個**現行語意**。**若本工單修復缺陷，這些斷言必須跟著回退**——否則會把正確行為判為錯誤。

此相依是刻意的：它讓這個取捨變成顯性的，而不是藏在一堆綠燈裡。

## 七、驗收

1. 同一 `(agent, trigger, extras)` 連續 3 次事件，`_count_independent_evidence` 能達到 2 以上並觸發昇降。
2. `value` 節點去重行為維持（80 個重複 value 不再增長）。
3. 相依的 6 筆測試依最終裁示調整，且**必須明確記錄調整理由**。
4. 全庫回歸 `tests/test_elevate_mechanism.py tests/test_elevation_evidence_scope_a1.py` 通過。

## 八、看門狗（Bry 2026-10-13 裁示，已建立並實測）

**`tests/test_soul_bug_watchdogs.py::test_elevation_pattern_evidence_accumulation_must_reach_two`**
—— `@pytest.mark.xfail(strict=True, reason="SOUL-BUG-001: ...")`

**設計要點（這是本守衛的關價值，別把它改回普通測試）**：

它**刻意使用空的 `extras`**（`extras={}`），也就是 `scripts/run_server.py:1808` 的生產形狀。
而既有 `tests/test_elevate_mechanism.py` 的 `_make_distinct_events` 給每條事件不同的 extras，
**那個加工本身就是這個缺陷的藏身處**——它讓 pattern 內容彼此相異，於是繞開了去重。

⇒ 這個守衛與既有測試的差別不是「多測了一些」，而是「**移除了那個加工**」。

實測結果（`--runxfail` 驗證，2026-10-13）：
```
AssertionError: 2 條生產形狀事件未觸發昇降 —— 獨立證據未達 2。
  已落盤 pattern 數=1（預期 2，但去重可能只留 1）
  已落盤證據邊數=0
```
⇒ 缺陷以正確理由失敗，**非假哨兵**。

**修復本工單後**：移除 `xfail` 標記，將本測試轉正為常態回歸。
`strict=True` 會在修復瞬間噴 XPASS（視為 FAILED）強迫這件事發生。