"""
test_self_check_v2.py
Mock Test 2 (修法驗證):
  run_server.py 有 event_loop_self_check module-level function.
  啟動 task 用 1s interval, 跑 3s 後確認 event_loop_alive.json 存在 + 內容正確.

預期: 3 個 assert PASS:
  1. from run_server import event_loop_self_check 成功
  2. task 啟動 3s 後 event_loop_alive.json 存在
  3. 內容有 last_alive_at + interval_seconds + source 欄位

執行:
  python tests/test_self_check_v2.py
"""
import asyncio
import importlib.util
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.self_check_v2")


def load_run_server_module():
    """載入 run_server.py module, 取得 event_loop_self_check function."""
    # run_server.py 在 scripts/, 跟 tests/ 平級, 需要 import 從 scripts/
    project_root = Path(__file__).resolve().parent.parent
    scripts_dir = project_root / "scripts"
    sys.path.insert(0, str(scripts_dir))

    spec = importlib.util.spec_from_file_location(
        "_run_server_under_test",
        scripts_dir / "run_server.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load run_server.py spec")
    mod = importlib.util.module_from_spec(spec)
    # 不要 exec_module (會 trigger lifespan / uvicorn), 只要 source
    # 用 read_text + compile 然後 exec 在新 namespace
    src = (scripts_dir / "run_server.py").read_text(encoding="utf-8")
    # compile 但不執行
    code = compile(src, str(scripts_dir / "run_server.py"), "exec")
    # 用 module-level exec 跑 source, 但要避免副作用 (uvicorn.run 等)
    # 簡化: 直接 exec, 並 catch exception; 反正 lifespan 在 function 內部, import 不會 trigger
    try:
        exec(code, mod.__dict__)
    except SystemExit:
        pass  # if __name__ == "__main__" → uvicorn.run 可能呼叫 sys.exit
    except Exception as e:
        # 其他錯誤 (e.g. lifespan 嘗試啟動) 不影響 self_check function 已經被定義
        logger.debug(f"exec run_server caught (不影響 self_check 測試): {e}")
    return mod


async def test_self_check_v2(tmp_dir: Path) -> Dict[str, Any]:
    """
    Step 3 Mock Test 2 (修法):
    載入 run_server module 拿 event_loop_self_check,
    啟動 task 用 1s interval + 0.1s first_delay, 跑 3s 後驗證.
    """
    logger.info("=" * 60)
    logger.info("  Step 3 Mock Test 2: 修法驗證 (self-check 跑, 寫檔)")
    logger.info("=" * 60)

    state_dir = tmp_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # ── 斷言 1: import event_loop_self_check 成功 ──
    mod = load_run_server_module()
    assert hasattr(mod, "event_loop_self_check"), (
        "run_server 應該有 event_loop_self_check, 但找不到"
    )
    self_check_fn = mod.event_loop_self_check
    logger.info("  ✓ event_loop_self_check 從 run_server import 成功")

    # ── 啟動 task (interval=1s, first_delay=0.1s 加速測試) ──
    task = asyncio.create_task(
        self_check_fn(
            state_dir=state_dir,
            interval_seconds=1,
            first_delay_seconds=0,
        )
    )

    # 等 3s, 應該至少寫 3 次 (interval=1s)
    await asyncio.sleep(3.0)

    # ── 斷言 2: event_loop_alive.json 存在 ──
    alive_file = state_dir / "event_loop_alive.json"
    assert alive_file.exists(), (
        f"{alive_file} 應該被 self_check 建立, 但不存在"
    )
    logger.info(f"  ✓ {alive_file.name} 已建立")

    # ── 斷言 3: 內容有 last_alive_at + interval_seconds + source ──
    raw = alive_file.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "last_alive_at" in payload, (
        f"payload 應該有 last_alive_at, keys: {list(payload.keys())}"
    )
    assert payload.get("interval_seconds") == 1, (
        f"payload interval_seconds 應該是 1, 實際 {payload.get('interval_seconds')}"
    )
    assert payload.get("source") == "run_server_event_loop_self_check", (
        f"payload source 應該是 run_server_event_loop_self_check, "
        f"實際 {payload.get('source')!r}"
    )
    logger.info(
        f"  ✓ payload 正確: last_alive_at={payload['last_alive_at']}, "
        f"interval={payload['interval_seconds']}, source={payload['source']}"
    )

    # 等 2s, 確認 self_check 會持續寫 (驗證 3s 後又有更新)
    first_write_ts = payload["last_alive_at"]
    await asyncio.sleep(2.0)
    payload2 = json.loads(alive_file.read_text(encoding="utf-8"))
    assert payload2["last_alive_at"] != first_write_ts, (
        f"self_check 應該在 2s 後再寫一次, "
        f"但 last_alive_at 沒變 ({first_write_ts})"
    )
    logger.info(
        f"  ✓ 持續寫: 2s 後 last_alive_at 更新 "
        f"({first_write_ts} → {payload2['last_alive_at']})"
    )

    # Cancel task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    return {
        "alive_file_exists": True,
        "payload": payload,
        "payload2": payload2,
        "writes_count": 2,  # 至少 2 次寫入確認
    }


def assert_v2_acceptance(result: Dict[str, Any]) -> None:
    errors: List[str] = []
    if not result["alive_file_exists"]:
        errors.append(
            "[event_loop_alive.json] 修法後應該存在, 但不存在"
        )
    p = result["payload"]
    if "last_alive_at" not in p:
        errors.append("[payload] 缺 last_alive_at")
    if p.get("interval_seconds") != 1:
        errors.append(
            f"[payload.interval_seconds] 應該是 1, 實際 {p.get('interval_seconds')}"
        )
    if p.get("source") != "run_server_event_loop_self_check":
        errors.append(
            f"[payload.source] 應該是 run_server_event_loop_self_check, "
            f"實際 {p.get('source')!r}"
        )
    if result["writes_count"] < 2:
        errors.append(
            f"[持續寫] 應該至少 2 次, 實際 {result['writes_count']}"
        )
    if errors:
        raise AssertionError(
            "Step 3 Mock Test 2 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        result = await test_self_check_v2(tmp_dir)
        assert_v2_acceptance(result)
        logger.info("\n✅ Step 3 Mock Test 2 (修法) PASS")
        logger.info(
            "   4 個 assert 通過: import 成功, 寫檔存在, "
            "payload 正確, 持續寫 2 次以上"
        )


if __name__ == "__main__":
    asyncio.run(main())
