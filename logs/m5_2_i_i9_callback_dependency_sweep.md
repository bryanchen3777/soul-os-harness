# M5.2-I Phase I-9 — Final Verification / Callback Dependency Sweep

**派工**: 2026-08-08 17:50 EDT (Bry 派工)
**執行**: read-only sweep, 0 commit / 0 push / 0 file modified
**結果**: ✅ I-9 Gate PASS

---

## 1. Scope 確認

- ✅ Read-only sweep, 無任何 production / test / handler / eventbus 改動
- ✅ Callback Dependency Matrix 完成
- ✅ 4 handler ingress 全部確認為 AGENCY_TRIGGER
- ✅ AGENCY_TRIGGER payload contract 未被 I-6/I-7/I-8 意外改動
- ✅ 完整 regression 190/191 PASS (唯一 M3.1 frozen exception 仍允許)

---

## 2. Callback Dependency Matrix

### 2.1 Callback attribute references (4 個 scheduler callback 欄位)

| 屬性 | scheduler.py | run_server.py | tests | 分類 |
|------|--------------|---------------|-------|------|
| `_dream_callback` | L159 屬性定義, L293 register 寫入, L422-458 註解「M5.2-I Phase 6 移除 callback dependency」 | L382-392 noop 函式, L410 `register_dream_event` 呼叫 | `test_m5_2_h2_dream_bridge.py` (no-op lambda), `test_m1_7_event_whitelist_v1.py` (v1 baseline) | API COMPAT (M5.2-H2 noop pass-through) |
| `_event_callback` | L160 屬性定義, L294 register 寫入, L442-518 註解「移除 callback dependency」 | L397-408 noop 函式, L410 `register_dream_event` 呼叫 | `test_m1_7_event_whitelist_v1/v2.py` (legacy observation), `test_m5_2_h_event_bridge.py` (no-op lambda) | API COMPAT (M5.2-H1 noop pass-through) |
| `_proactive_dm_callback` | L164 屬性定義, L349 register 寫入, L574-679 註解「不再依賴 callback」 | L445-461 noop 函式, L492 `register_proactive_dm` 呼叫 | `test_m5_2_g_proactive_dm_bridge.py` (no-op lambda), `test_proactive_whitelist_v1/v2.py`, `test_proactive_density_v2.py` | API COMPAT (M5.2-G noop pass-through) |
| `_heartbeat_callback` | L163 屬性定義, L321 register 寫入, **L570, L626, L643 active execution** (`_fire_heartbeat` 真的 `await self._heartbeat_callback(agent_id)`) | L422-443 整段註解, L491 `register_heartbeat` 呼叫註解 | `test_m3_disabled_mode.py` (H3-I12: 斷言 scheduler 保留 `_heartbeat_callback` 屬性), `test_proactive_density_v1/v2.py` | HEARTBEAT EXCEPTION (修法 12: Bry 8/6 17:12 拍板, 機制保留, run_server.py 不 register) |

### 2.2 `register*()` callsites

| API | scheduler.py 定義 | run_server.py | tests | 分類 |
|-----|-------------------|---------------|-------|------|
| `register(agent_id, callback)` (diary) | L267-279 (寫入 `_callbacks[morning/night]` + `_all_agents.append`) | **未呼叫** (I-7 移除 `scheduler.register` 改用 `diary_callbacks_real` dict lookup) | `test_m5_2_h3_diary_bridge.py:93` (測試 helper) | API COMPAT (向後相容, production 不再呼叫) |
| `register_dream_event(dream_cb, event_cb)` | L281-307 | L410 真呼叫 (傳 noop `_dream_callback`, `_event_callback`) | `test_m1_7_event_whitelist_v1/v2.py`, `test_m5_2_h_event_bridge.py`, `test_m5_2_h2_dream_bridge.py` | API COMPAT (向後相容, callback 為 noop) |
| `register_heartbeat(callback)` | L309-331 | L491 **註解** (`# scheduler.register_heartbeat(_heartbeat_callback)`) | `test_proactive_whitelist_v1/v2.py` (test 內仍 register 為了測試自身需要) | HEARTBEAT EXCEPTION (修法 12: 機制保留, production 不呼叫) |
| `register_proactive_dm(callback)` | L333-360 | L492 真呼叫 (傳 noop `_proactive_dm_callback`) | `test_m5_2_g_proactive_dm_bridge.py:102/292/334/378`, `test_proactive_whitelist_v1/v2.py` | API COMPAT (向後相容, callback 為 noop) |

### 2.3 `_callbacks` 內部使用

| 位置 | 用途 | 分類 |
|------|------|------|
| L151 屬性定義 | `Dict[str, Dict[str, DiaryCallback]]` (canonical storage) | INFRASTRUCTURE |
| L275-276 `register()` 寫入 | API COMPAT (production 不再呼叫 register) | INFRASTRUCTURE |
| L363 `registered_agents()` property | 從 `_callbacks.keys()` 取 list | **觀察/日誌** (跟 `_all_agents` 不一定對齊) |
| L375 `start()` log | `agents={len(self._callbacks)}` 日誌 | 日誌用, 不影響觸發 |
| **L820-832 `_fire_all` (I-8 後)** | 用 `_all_agents` 迭代, **完全脫離 `_callbacks`** | PRODUCTION (M5.2-I8) |

### 2.4 4 條 fire_* path — callback dependency 矩陣

| Path | Scheduler method | AGENCY_TRIGGER | callback 仍 invoke? | 真正 execution |
|------|------------------|----------------|---------------------|----------------|
| proactive_dm | `_fire_proactive_dm` (L727) | ✅ L730 (trigger_type="proactive_dm") | ❌ No (I-6 移除) | AgencyTriggerHandler.handle_event → llm_executor |
| event | `_fire_event` (L550) | ✅ L553 (trigger_type="event") | ❌ No (I-6 移除) | EventHandler.handle_event → writer.write_event |
| dream | `_fire_dream` (L482) | ✅ L482-485 (trigger_type="dream", extra={target_agent_id, all_agents}) | ❌ No (I-6 移除) | DreamHandler.handle_event → writer.write_dream + relationship side effect |
| morning / night | `_fire_all` (L820-832) | ✅ L829 (trigger_type=slot) | ❌ No (I-6 移除 invoke, I-8 移除 iteration) | DiaryHandler.handle_event → diary_callbacks_real[agent_id] |
| heartbeat | `_fire_heartbeat` (L640-650) | ❌ No (legacy AGENT_INTENT) | ✅ **YES** `await self._heartbeat_callback(agent_id)` L643 | HEARTBEAT EXCEPTION (suspended, 修法 12) |

---

## 3. 4 Handler Ingress 確認

### 3.1 src/agency/ 4 個 handler 全部以 AGENCY_TRIGGER 為 production ingress

| Handler | File | trigger_type filter | writer_executor signature | 訂閱 |
|---------|------|---------------------|---------------------------|------|
| AgencyTriggerHandler | `trigger_handler.py` | `"proactive_dm"` (L83) | `LLMExecutor = Callable[[str, TriggerEnvelope], Awaitable[None]]` (L30) | `bus.subscribe(event_filter={EventType.AGENCY_TRIGGER})` (run_server.py:502) |
| EventHandler | `event_handler.py` | `"event"` (L108) | `WriterExecutor = Callable[[str], Awaitable[None]]` (L53) | `bus.subscribe(event_filter={EventType.AGENCY_TRIGGER})` (run_server.py:528) |
| DreamHandler | `dream_handler.py` | `"dream"` | `Callable[[str, str, List[str]], Awaitable[None]]` (dreamer, target, all_agents) | `bus.subscribe(event_filter={EventType.AGENCY_TRIGGER})` (run_server.py:563) |
| DiaryHandler | `diary_handler.py` | `in {"morning", "night"}` | `DiaryWriterExecutor = Callable[[str, str], Awaitable[None]]` (agent_id, slot) (L61) | `bus.subscribe(event_filter={EventType.AGENCY_TRIGGER})` (run_server.py:603) |

### 3.2 Handler 對 scheduler callback / register / _callbacks 的依賴

- ✅ **0 references** — handler grep 結果只看到:
  - docstring 提到「scheduler」是 history context (event_handler.py:11-19, dream_handler.py, diary_handler.py:17-21)
  - docstring 提到「register on bus」(bus.subscribe 模式, 不是 scheduler register)
  - `diary_handler.py:74` stale 註解:「run_server.py 注入 `lambda aid, s: scheduler._callbacks[aid][s](aid, s)`」實際 production 用 `diary_callbacks_real.get(agent_id)`, 註解是 stale 但不影響執行 (M5.2-J+ 可清)
- ✅ agency.py:7「No side effects, no bus integration, no scheduler」明確聲明 4 stages 跟 scheduler 解耦
- ✅ state.py:5「不接 scheduler / production」明確聲明

---

## 4. AGENCY_TRIGGER Payload Contract 確認

### 4.1 eventbus schema.py (frozen since M5.2-G)

```python
# schema.py:48-53
# M5.2-G: Scheduler → Agency bridge trigger
# Bry 拍板 2026-08-08 M5.2-F: 跟 AGENT_INTENT 語意分離
AGENCY_TRIGGER  = "agency_trigger"     # M5.2-G: Scheduler 發給 Agency 的 trigger
```

- ✅ 未被 I-6 / I-7 / I-8 改動
- ✅ 純 additive (既有 event type / payload / semantics 不變)

### 4.2 _publish_agency_trigger (frozen since M5.2-G, optional `extra` from M5.2-H Phase 2)

```python
# scheduler.py:176-227
async def _publish_agency_trigger(
    self,
    agent_id: str,
    trigger_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    # payload 結構 (4 個 fire path 共用):
    payload = {
        "trigger_type": trigger_type,
        "agent_id": agent_id,
        "reason": f"scheduler.{trigger_type}",
        "elapsed_mins": elapsed_mins,
        "timestamp": now_local().isoformat(),
        "extra": dict(extra) if extra else {},
    }
    await self._bus.publish(trigger_event)  # event_type=EventType.AGENCY_TRIGGER
```

- ✅ 未被 I-6 / I-7 / I-8 改動
- ✅ 4 條 fire path 全部走這個 method (L482 dream, L553 event, L730 proactive_dm, L829 morning/night)
- ✅ dream 的 `target_agent_id` / `all_agents` 走 `extra` dict (C1: TriggerEnvelope frozen, extra 走 dict payload)

---

## 5. Production Architectural State (M5.2-I I-8 end)

```
Legacy callback
   └── API compatibility only
       ├── scheduler.register(agent_id, callback) 接受 Optional (I-6)
       ├── scheduler.register_dream_event(dream_cb, event_cb) 接受 Optional
       ├── scheduler.register_heartbeat(callback) 保留 (heartbeat exception)
       └── scheduler.register_proactive_dm(callback) 接受 Optional
   └── no longer invoked by scheduler (I-6)
   └── production:
       ├── _diary_noop_cb 已移除 (I-7)
       ├── _dream_callback / _event_callback / _proactive_dm_callback 是 noop (M5.2-H1/H2/G)
       └── _heartbeat_callback 整段註解 (修法 12, heartbeat exception)
   └── no longer iterated by scheduler for _fire_all (I-8)

Production
   │
   ▼
Scheduler
   ├── _fire_proactive_dm (callback=None works) ──→ AGENCY_TRIGGER
   ├── _fire_event (callback=None works)         ──→ AGENCY_TRIGGER
   ├── _fire_dream (callback=None works)          ──→ AGENCY_TRIGGER
   └── _fire_all (_all_agents iteration)          ──→ AGENCY_TRIGGER ← I-8 NEW
   │
   ▼
Agency / Handler (4 handlers wired, all 4 active paths)
   ├── AgencyTriggerHandler → llm_executor
   ├── EventHandler         → writer.write_event
   ├── DreamHandler         → writer.write_dream + relationship side effect
   └── DiaryHandler         → diary_callbacks_real[agent_id](agent_id, slot)
```

---

## 6. I-9 Gate 評估

| Gate 條件 | 狀態 | 證據 |
|-----------|------|------|
| Scheduler.proactive_dm → AGENCY_TRIGGER | ✅ | L730, 4 個 regression test (test_m5_2_g_proactive_dm_bridge.py 11/11) |
| Scheduler.event → AGENCY_TRIGGER | ✅ | L553, test_m5_2_h_event_bridge.py 11/11 |
| Scheduler.dream → AGENCY_TRIGGER | ✅ | L482-485, test_m5_2_h2_dream_bridge.py 14/14 |
| Scheduler.morning/night → AGENCY_TRIGGER | ✅ | L829, test_m5_2_h3_diary_bridge.py 13/13, I-8 architectural verification 4/4 |
| 4 Handler 用 AGENCY_TRIGGER 為 ingress | ✅ | 4 個 handler 全部 `bus.subscribe(event_filter={EventType.AGENCY_TRIGGER})`, 4 trigger_type filter |
| Handler 不依賴 scheduler callback | ✅ | grep 0 references |
| Callback 只剩 Legacy API compat + Heartbeat exception | ✅ | matrix 2.1 / 2.2 確認 |
| Regression 190/191 (唯一 M3.1 frozen exception 允許) | ✅ | 跑 15 個 suite, 190/191 PASS |
| Frozen files 0 modification | ✅ | git status 跟 I-8 end 相同 (僅 M5.2-I scope 既有 5 modified + 1 new src/agency/) |
| commit/push | ✅ 禁止 | 0 commit / 0 push |

**所有 Gate PASS → I-9 完整收工。**

---

## 7. Bry 派工 I-9 派工原文「真正可以不再靠 callback」vs 現況

Bry 派工原文:
> 「I-9 的目的就是證明『真的可以不再靠 callback』, 而不是為了追求把所有 callback 字串從 repository 裡刪光。」

確認:
- ✅ **真的可以不再靠 callback** — 4 條 fire_* path 在 production 完全不 invoke callback,真實 execution 全走 AGENCY_TRIGGER → 4 handler
- ✅ **沒有追求刪光 callback 字串** — run_server.py 仍 register_dream_event / register_proactive_dm / (heartbeat 註解),但都是 noop pass-through
- ✅ **callback 存在意義**:
  - 1. **API compatibility** — scheduler.register() 仍接受 callback 參數 (向後相容舊測試 + 過渡期)
  - 2. **heartbeat exception** — 修法 12 Bry 派工保留, run_server.py 不再 register 但 scheduler 機制在 (未來恢復不用重寫)
  - 3. **test infrastructure** — M1.7 v2 / v1 測試仍用 callback recorder 觀察舊路徑

---

## 8. Outstanding 觀察 (read-only 階段標出, 不動)

1. `src/agency/diary_handler.py:74` stale 註解:
   ```python
   # Production 必須注入實際 executor (run_server.py 注入
   # `lambda aid, s: scheduler._callbacks[aid][s](aid, s)`)。
   ```
   實際 production 用 `diary_callbacks_real.get(agent_id)`,註解是過時的。**M5.2-J+ 可清** (Bry 派工 read-only 階段禁止改)。

2. `eventbus/schema.py` AGENCY_TRIGGER 沒在 L256+ Payload 慣例文件描述 (其他 event_type 都有契約描述)。**M5.2-J+ 可補** (Bry 派工 read-only 階段禁止改)。

3. `run_server.py:594` 註解「(scheduler.register 漏了?)」是 stale — 實際 I-7 後 production 不再用 `scheduler.register`,改用 `diary_callbacks_real` dict lookup。**M5.2-J+ 可清** (Bry 派工 read-only 階段禁止改)。

4. `run_server.py:382-461` 3 個 noop callback (`_dream_callback` / `_event_callback` / `_proactive_dm_callback`) 仍存在。Bry 派工原文 M5.2-H 註解「Phase 2 之後可考慮完全移除 callback 欄位 (跟 proactive_dm 平行)」。**M5.2-J+ scope** 是否完全移除,等 Bry 拍板。

---

## 9. 結論

- ✅ M5.2-I Phase I-9 收工 (read-only)
- ✅ 所有 Gate 通過
- ✅ 完整 regression 190/191 (唯一 M3.1 frozen exception 仍允許)
- ✅ 0 commit / 0 push / 0 file modified
- ✅ 等待 Bry 拍板是否進入 M5.2-J (commit + release)
