"""SIMULATION: does W1 (whim_driven) actually produce a life thread?

Bry 2026-10-03 rule: 需要時間的就跑模擬。下一個 night slot 要 22:00，
但「whim 會不會產出」現在就能用模擬回答。

方法（嚴守 STRICT 0 production mutation）:
  - isolated data_root（tempdir），結束後復原
  - 假時鐘：注入「週六 22:00 EDT」與「週六 08:00 EDT」兩個 slot
  - stub LLM：回傳合法 origin JSON，**不呼叫真實模型**
  - 逐一比較 flag ON / OFF 的 M3→woke 路徑與實際落盤的 origin_type

**這是觀察，不是驗收**——它回答「接線是否真的會走到」，不回答「產出品質如何」。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
sys.path.insert(0, str(REPO))

SOUL_ID = "agent_ruka"
SLOT_HOURS = {"morning": 8, "night": 22}


def _iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def stub_llm(messages, agent_id):
    """stub：回傳合法的 origin seed，不呼叫真實模型。

    🔴 簽章依 `life_thread_origins.py:985`：`llm_caller(messages, agent_id)`
    —— 只有 2 個位置參數（初版誤寫 4 個，導致 fail-silent 全部落空）。

    🔴 回傳格式依 `parse_actions()`（`:839-850`）＋ `apply_actions()`（`:853-900`）：
       必須是 `{"actions": [{...}]}`，且每個 action 用 **`op`**（不是 `action`），
       `create` 還需要 `title` / `narrative_content` / `next_check_hours`。
       非 JSON 或 actions 非 list ⇒ **不落盤**；欄位缺 ⇒ `skipped:create:incomplete`。
    """
    return json.dumps(
        {
            "actions": [
                {
                    "op": "create",
                    "title": "模擬產出的心緒",
                    "narrative_content": "這是模擬 stub 產生的敘事，用於驗證接線是否走到。",
                    "next_check_hours": 12,
                }
            ]
        },
        ensure_ascii=False,
    )


def seed_persona(iso_root: pathlib.Path, agent_id: str) -> bool:
    """把 production persona 複製進 isolated root。

    🔴 必要：whim 路徑條件②是 `load_soul_context(agent_id)` 非空。
    隔離 tempdir 沒有 persona ⇒ 條件恆不成立 ⇒ 模擬會**假陰性**。
    （第一版模擬就是因此誤判「whim 未觸發」。）
    """
    import shutil as _sh

    src = REPO / "data" / "personas" / f"{agent_id}.md"
    if not src.is_file():
        return False
    dst = iso_root / "personas"
    dst.mkdir(parents=True, exist_ok=True)
    _sh.copy2(src, dst / f"{agent_id}.md")
    return True


async def run_sim(whim_on: bool, slot: str) -> Dict[str, Any]:
    from src.paths import data_root, reset_data_root
    from src.soul import life_thread_orchestrator as orch

    prev_env = os.environ.get("SOUL_OS_DATA_DIR")
    prev_root = data_root().resolve()
    iso = tempfile.mkdtemp(prefix=f"lt_sim_{slot}_{int(whim_on)}_")
    try:
        os.environ["SOUL_OS_DATA_DIR"] = iso
        reset_data_root()
        assert data_root().resolve() != prev_root, "isolated root 未生效"

        # 🔴 播種 persona（whim 條件②需要 non-empty soul_context）
        seeded = seed_persona(data_root(), SOUL_ID)
        sc = orch.lt_origins.load_soul_context(SOUL_ID)
        print(f"    [{slot} flag={int(whim_on)}] persona 播種={seeded} "
              f"soul_context={'非空' if sc else '空'}")

        os.environ["LIFE_THREAD_WHIM_ENABLED"] = "1" if whim_on else "0"
        orch.reset_state()
        # 🔴 `_WHIM_DAILY` 是**模組級記憶體狀態**（每日上限計數器），
        #    `reset_state()` 只清 `_LAST_PROCESSED`、**不清它**。
        #    模擬每輪都是「同一天」，故必須手動歸零，否則第二輪會被
        #    第一輪用掉的每日額度擋掉（那是**正確行為**，但會掩蓋 slot 本身的驗證）。
        orch._WHIM_DAILY.clear()

        # 🔴 **重現生產狀態**：bootstrap 一次性路徑早在 2026-09-18 就用掉了。
        #    若不預先蓋章，`claim_bootstrap()` 會成功 ⇒ `bootstrap_mode=True`
        #    ⇒ `if not bootstrap_mode:` 整段跳過 ⇒ **whim 永遠沒有機會**
        #    （第一版模擬正是因此誤判「whim 未觸發」；這是模擬抓到的假陰性。）
        marker = orch.lt_boot.bootstrap_marker_path(
            orch.lt.life_threads_path(SOUL_ID)
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"claimed_at": "2026-09-18T08:00:00+00:00", "sim": True}),
            encoding="utf-8",
        )
        print(f"    [{slot} flag={int(whim_on)}] bootstrap 標記已預先蓋章={marker.name}")

        # 假時鐘：週六 22:00 / 08:00 EDT
        base = datetime.datetime(2026, 10, 3, SLOT_HOURS[slot], 0, 0,
                                 tzinfo=datetime.timezone(datetime.timedelta(hours=-4)))
        out = await orch.run_slot_pipeline(
            [SOUL_ID], base, slot, llm_caller=stub_llm
        )
        summary = out.get(SOUL_ID, {})

        # 落盤的實際 origin_type（fold 現行狀態）
        path = data_root() / "soul" / SOUL_ID / "life_threads.jsonl"
        origins: Dict[str, int] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("origin_type"):
                    o = e["origin_type"]
                    origins[o] = origins.get(o, 0) + 1
        return {
            "flag_on": whim_on,
            "slot": slot,
            "woke": summary.get("woke"),
            "reason": summary.get("reason"),
            "origin_type": summary.get("origin_type"),
            "whim_wake": summary.get("whim_wake"),
            "bootstrap": summary.get("bootstrap"),
            "skipped": summary.get("skipped"),
            "error": summary.get("error"),
            "raw_summary": summary,
            "落盤 origins": origins,
        }
    finally:
        if prev_env is None:
            os.environ.pop("SOUL_OS_DATA_DIR", None)
        else:
            os.environ["SOUL_OS_DATA_DIR"] = prev_env
        os.environ["LIFE_THREAD_WHIM_ENABLED"] = "0"
        reset_data_root()
        assert data_root().resolve() == prev_root
        shutil.rmtree(iso, ignore_errors=True)


async def main() -> int:
    print("=" * 76)
    print("SIMULATION — W1 whim_driven 接線是否真的會產出（假時鐘 + stub LLM）")
    print("=" * 76)
    print(f"  soul  : {SOUL_ID}")
    print(f"  假時鐘: 2026-10-03（週六）08:00 EDT / 22:00 EDT")
    print(f"  LLM   : stub（不呼叫真實模型）")
    print()
    results: List[Dict[str, Any]] = []
    for slot in ("morning", "night"):
        for flag in (False, True):
            r = await run_sim(flag, slot)
            results.append(r)
            tag = "flag=ON " if flag else "flag=OFF"
            print(f"  [{slot:<8}] {tag}  woke={str(r['woke']):<5} "
                  f"origin={str(r['origin_type']):<18} "
                  f"whim_wake={str(r['whim_wake']):<5} "
                  f"reason={str(r['reason'])[:34]}")
            print(f"           落盤 origins: {r['落盤 origins']}")
            print(f"           完整 summary: {json.dumps(r['raw_summary'], ensure_ascii=False)[:400]}")
    print()
    print("=" * 76)
    print("結論")
    print("=" * 76)
    any_whim = any(
        r["origin_type"] == "whim_driven" or r["whim_wake"] is True
        for r in results
    )
    any_necessity = any(r["origin_type"] == "necessity_driven" for r in results)
    off_produced = any(r["落盤 origins"] for r in results if not r["flag_on"])
    on_produced = any(r["落盤 origins"] for r in results if r["flag_on"])

    print(f"  flag OFF 有產出？ {off_produced}")
    print(f"  flag ON  有產出？ {on_produced}")
    print(f"  出現 whim_driven？ {any_whim}")
    if any_whim:
        print("  ✅ W1 接線在模擬下確實會產出 whim_driven 線頭")
        print("     ⇒ 22:00 slot 跑完後，生產資料應可見新的 origin")
    else:
        print("  ❌ 模擬下**未**出現 whim_driven —— 接線未被觸發")
        print("     ⇒ 需檢查觸發條件（M3 判定 / soul_context / 每日額度）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
