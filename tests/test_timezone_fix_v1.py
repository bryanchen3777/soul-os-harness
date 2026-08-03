"""
test_timezone_fix_v1.py
Bry 派工 2026-08-03 18:21 — Mock Test 1 (現狀驗證)

查現狀: 系統用 ASIA_TZ (UTC+8) 計算「當下時間」餵給 LLM,
但 Bry 人在美東 EDT (UTC-4), 差 12 小時. 重現 Bry 抓漏的兩個案例:
  - 案例 1: akane 16:11 EDT 觸發 → 現狀餵 LLM 04:11 Asia/Taipei (凌晨)
    → 角色寫 "還沒睡著" 跟 Bry 端下午脫節
  - 案例 2: mahiru 04:10 UTC 觸發 → 現狀餵 LLM 12:10 Asia/Taipei (中午)
    → 角色寫 "早餐" 跟 Bry 端中午脫節

預期全部 4 個 assert PASS (現狀就是這樣, 這是 baseline):
  1. proxy.py 仍定義 ASIA_TZ = timezone(timedelta(hours=8))
  2. scheduler.py 仍定義 ASIA_TZ = timezone(timedelta(hours=8))
  3. _format_event_timestamp(akane_16_11_EDT) 算出 04:11 Asia/Taipei (凌晨)
  4. _format_event_timestamp(mahiru_04_10_UTC) 算出 12:10 Asia/Taipei (中午)

執行:
  python tests/test_timezone_fix_v1.py
"""
import asyncio
import importlib.util
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.tz_fix_v1")


def load_module_from_path(name: str, path: Path):
    """載入 module from absolute path, 避免 import 副作用."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    src = path.read_text(encoding="utf-8")
    code = compile(src, str(path), "exec")
    try:
        exec(code, mod.__dict__)
    except SystemExit:
        pass
    except Exception as e:
        logger.debug(f"exec {name} caught: {e}")
    return mod


async def test_timezone_v1(project_root: Path) -> Dict[str, Any]:
    """
    β2 時區修法 Mock Test 1 (現狀):
    確認 4 個檔案仍用 ASIA_TZ / hardcode, 12 小時錯位真實存在.
    """
    logger.info("=" * 60)
    logger.info("  Mock Test 1: 現狀驗證 (12 小時錯位重現)")
    logger.info("=" * 60)

    proxy_path = project_root / "src" / "llm" / "proxy.py"
    scheduler_path = project_root / "src" / "soul" / "scheduler.py"
    middleware_path = project_root / "src" / "memory" / "middleware.py"
    models_path = project_root / "src" / "temporal" / "models.py"

    # ── 斷言 1: proxy.py 仍定義 ASIA_TZ = timezone(timedelta(hours=8)) ──
    proxy_src = proxy_path.read_text(encoding="utf-8")
    assert "ASIA_TZ = timezone(timedelta(hours=8))" in proxy_src, (
        "proxy.py 應該有 ASIA_TZ = timezone(timedelta(hours=8)) (現狀), "
        "但沒找到"
    )
    assert "Asia/Taipei" in proxy_src, (
        "proxy.py 應該寫死 'Asia/Taipei' 字串 (現狀), 但沒找到"
    )
    # 確認是 f-string format (不是註解)
    assert "Asia/Taipei" in proxy_src and "{period}" in proxy_src, (
        "proxy.py 應該有 f-string 含 'Asia/Taipei' + '{period}' (現狀), "
        "但沒找到 (可能是註解或被改過)"
    )
    logger.info("  ✓ proxy.py 現狀: ASIA_TZ=UTC+8 + 'Asia/Taipei' 字串寫死在 f-string")

    # ── 斷言 2: scheduler.py 仍定義 ASIA_TZ = timezone(timedelta(hours=8)) ──
    scheduler_src = scheduler_path.read_text(encoding="utf-8")
    assert "ASIA_TZ = timezone(timedelta(hours=8))" in scheduler_src, (
        "scheduler.py 應該有 ASIA_TZ = timezone(timedelta(hours=8)) (現狀), "
        "但沒找到"
    )
    asia_tz_count = scheduler_src.count("datetime.now(ASIA_TZ)")
    assert asia_tz_count >= 11, (
        f"scheduler.py 應該有 ≥11 處 datetime.now(ASIA_TZ) (M0.4 紀錄 11 處), "
        f"實際 {asia_tz_count} (M0.4 後續 commit 可能加更多)"
    )
    logger.info(
        f"  ✓ scheduler.py 現狀: ASIA_TZ=UTC+8 + {asia_tz_count} 處引用 "
        f"(M0.4 紀錄 11 處, 後續 commit 加了 4 處)"
    )

    # ── 斷言 3: memory/middleware.py 仍 hardcode hours=8 ──
    middleware_src = middleware_path.read_text(encoding="utf-8")
    assert "_tz(_td(hours=8))" in middleware_src, (
        "memory/middleware.py L544 應該有 _tz(_td(hours=8)) hardcode (現狀, β2.1 我寫的)"
    )
    logger.info("  ✓ memory/middleware.py 現狀: hardcode hours=8 (β2.1 我寫的)")

    # ── 斷言 4: temporal/models.py 預設 Asia/Tokyo ──
    models_src = models_path.read_text(encoding="utf-8")
    assert 'ZoneInfo("Asia/Tokyo")' in models_src, (
        "temporal/models.py L110 預設應該是 ZoneInfo('Asia/Tokyo') (現狀, UTC+9), "
        "但沒找到"
    )
    logger.info("  ✓ temporal/models.py 現狀: PersonaConfig.timezone 預設 Asia/Tokyo")

    # ── 斷言 5: 4 個檔案還沒 import timezone_utils (現狀) ──
    for label, path in [
        ("proxy.py", proxy_path),
        ("scheduler.py", scheduler_path),
        ("memory/middleware.py", middleware_path),
        ("temporal/models.py", models_path),
    ]:
        src = path.read_text(encoding="utf-8")
        assert "from src.timezone_utils" not in src, (
            f"{label} 現狀不該 import src.timezone_utils, 但已 import"
        )
    logger.info(
        "  ✓ 4 個檔案現狀: 都還沒 import src.timezone_utils"
    )

    # ── 動態驗證: 載入 proxy.py 跑 _format_event_timestamp 重現 Bry 抓漏案例 ──
    proxy_mod = load_module_from_path("_proxy_under_test", proxy_path)
    fmt = proxy_mod._format_event_timestamp

    # 案例 1: akane 16:11:03 EDT 觸發 (20:11:03 UTC)
    akane_event_ts = datetime(2026, 8, 2, 20, 11, 3, tzinfo=timezone.utc)
    akane_formatted = fmt(akane_event_ts)
    logger.info(f"  案例 1 (akane 16:11 EDT = 20:11 UTC):")
    logger.info(f"    現狀算出: {akane_formatted!r}")
    # 預期現狀: "2026-08-03 週日 04:11 Asia/Taipei（凌晨）"
    # 等等, UTC+8 加 20:11 UTC = 04:11 next day Asia/Taipei
    assert "Asia/Taipei" in akane_formatted, (
        f"現狀應該含 'Asia/Taipei' 字串, 實際 {akane_formatted!r}"
    )
    assert "04:11" in akane_formatted, (
        f"現狀應該算 04:11 Asia/Taipei (凌晨), 實際 {akane_formatted!r}"
    )
    assert "凌晨" in akane_formatted, (
        f"現狀時段應該是 '凌晨' (04:11), 實際 {akane_formatted!r}"
    )
    # Bry 端 16:11 EDT 是下午, 但現狀餵 LLM 04:11 凌晨
    # 12 小時錯位 = Bry 端 hour (16) - 現狀 hour (04) = 12

    # 案例 2: mahiru 04:10:51 UTC 觸發
    mahiru_event_ts = datetime(2026, 8, 2, 4, 10, 51, tzinfo=timezone.utc)
    mahiru_formatted = fmt(mahiru_event_ts)
    logger.info(f"  案例 2 (mahiru 04:10 UTC):")
    logger.info(f"    現狀算出: {mahiru_formatted!r}")
    # 預期現狀: "2026-08-02 週日 12:10 Asia/Taipei (中午)"
    assert "Asia/Taipei" in mahiru_formatted, (
        f"現狀應該含 'Asia/Taipei' 字串, 實際 {mahiru_formatted!r}"
    )
    assert "12:10" in mahiru_formatted, (
        f"現狀應該算 12:10 Asia/Taipei (中午), 實際 {mahiru_formatted!r}"
    )
    assert "中午" in mahiru_formatted, (
        f"現狀時段應該是 '中午' (12:10), 實際 {mahiru_formatted!r}"
    )
    # mahiru 寫 "早餐" 跟 Bry 端中午脫節

    return {
        "akane_formatted": akane_formatted,
        "mahiru_formatted": mahiru_formatted,
        "proxy_has_asia_tz": True,
        "scheduler_has_asia_tz": True,
        "middleware_has_hardcode": True,
        "models_has_asia_tokyo": True,
    }


def assert_v1_acceptance(result: Dict[str, Any]) -> None:
    errors: List[str] = []
    if not result["proxy_has_asia_tz"]:
        errors.append("[proxy.py ASIA_TZ] 現狀應該有, 沒了")
    if not result["scheduler_has_asia_tz"]:
        errors.append("[scheduler.py ASIA_TZ] 現狀應該有, 沒了")
    if not result["middleware_has_hardcode"]:
        errors.append("[middleware.py hardcode] 現狀應該有, 沒了")
    if not result["models_has_asia_tokyo"]:
        errors.append("[models.py Asia/Tokyo] 現狀應該有, 沒了")
    if "Asia/Taipei" not in result["akane_formatted"]:
        errors.append(
            f"[akane case] 現狀應該含 'Asia/Taipei', 實際 {result['akane_formatted']!r}"
        )
    if "04:11" not in result["akane_formatted"]:
        errors.append(
            f"[akane case] 現狀應該算 04:11, 實際 {result['akane_formatted']!r}"
        )
    if "Asia/Taipei" not in result["mahiru_formatted"]:
        errors.append(
            f"[mahiru case] 現狀應該含 'Asia/Taipei', 實際 {result['mahiru_formatted']!r}"
        )
    if "12:10" not in result["mahiru_formatted"]:
        errors.append(
            f"[mahiru case] 現狀應該算 12:10, 實際 {result['mahiru_formatted']!r}"
        )
    if errors:
        raise AssertionError(
            "Mock Test 1 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


async def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    result = await test_timezone_v1(project_root)
    assert_v1_acceptance(result)
    logger.info("\n✅ Mock Test 1 (現狀) PASS")
    logger.info("   5 個檔案 assert + 2 個案例重現 PASS")
    logger.info("   12 小時錯位真實存在, 證明改 ASIA_TZ→America/New_York 是根因修法")


if __name__ == "__main__":
    asyncio.run(main())
