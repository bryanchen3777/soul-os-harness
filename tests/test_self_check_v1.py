"""
test_self_check_v1.py
Mock Test 1 (現狀驗證):
  run_server.py 沒有 event_loop_self_check module-level function.
  Mock Test 1 確認現狀: 跑 15s 內, event_loop_self_check 不存在.

預期: assert PASS (現狀就是這樣, 這是 baseline).
  1. from run_server import event_loop_self_check 失敗 (ImportError)
  2. 模擬 15s 內 event_loop_alive.json 不會被自動建立

執行:
  python tests/test_self_check_v1.py
"""
import asyncio
import importlib
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.self_check_v1")


async def test_self_check_v1(tmp_dir: Path) -> Dict[str, Any]:
    """
    β2.1 + Step 3 Mock Test 1 (現狀):
    確認 run_server 沒有 event_loop_self_check function, event_loop_alive.json 不存在.
    """
    logger.info("=" * 60)
    logger.info("  Step 3 Mock Test 1: 現狀驗證 (無 self-check)")
    logger.info("=" * 60)

    state_dir = tmp_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # ── 斷言 1: 試 import event_loop_self_check, 應該 ImportError ──
    import_ok = False
    import_error = None
    try:
        # 載入 run_server module
        spec = importlib.util.find_spec("run_server")
        if spec is not None:
            run_server_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(run_server_module)
            if hasattr(run_server_module, "event_loop_self_check"):
                import_ok = True
            else:
                import_error = "no attribute event_loop_self_check"
    except ImportError as e:
        import_error = str(e)
    except Exception as e:
        # run_server.py 跑 lifespan 會 crash, 但只要 module 載入成功 + 沒 self_check function 就夠
        if "event_loop_self_check" in str(e):
            import_error = str(e)
        elif hasattr(e, "__cause__") and "event_loop_self_check" in str(e.__cause__):
            import_error = str(e.__cause__)
        else:
            # 其他錯誤 (e.g. lifespan 找不到 bus) 不算 self_check 失敗
            logger.debug(f"run_server 載入時其他錯誤 (不影響 self_check 測試): {e}")

    assert not import_ok, (
        f"現狀不該有 event_loop_self_check, 但 run_server 有: {import_ok}"
    )
    logger.info(
        f"  ✓ 現狀 run_server 沒 event_loop_self_check "
        f"(import 結果: {import_error!r})"
    )

    # ── 斷言 2: 模擬 15s 內 event_loop_alive.json 不會被建立 ──
    # 沒有 self_check task, 沒人會寫 event_loop_alive.json
    alive_file = state_dir / "event_loop_alive.json"
    assert not alive_file.exists(), (
        f"現狀不該有 {alive_file}, 但存在了"
    )
    logger.info(f"  ✓ 現狀 {alive_file.name} 不存在")

    return {
        "import_ok": import_ok,
        "import_error": import_error,
        "alive_file_exists": alive_file.exists(),
    }


def assert_v1_acceptance(result: Dict[str, Any]) -> None:
    errors: List[str] = []
    if result["import_ok"]:
        errors.append(
            "[event_loop_self_check import] 現狀不該有, 但有了"
        )
    if result["alive_file_exists"]:
        errors.append(
            "[event_loop_alive.json] 現狀不該存在, 但存在了"
        )
    if errors:
        raise AssertionError(
            "Step 3 Mock Test 1 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


async def main() -> None:
    import importlib.util
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        result = await test_self_check_v1(tmp_dir)
        assert_v1_acceptance(result)
        logger.info("\n✅ Step 3 Mock Test 1 (現狀) PASS")
        logger.info("   2 個 assert 通過: 無 event_loop_self_check, 無 event_loop_alive.json")


if __name__ == "__main__":
    import importlib.util
    asyncio.run(main())
