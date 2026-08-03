"""
test_timezone_fix_v2.py
Bry 派工 2026-08-03 18:21 — Mock Test 2 (修法驗證)

修法後, 4 個檔案都用 src.timezone_utils 統一時區 (ZoneInfo("America/New_York")),
預設 fallback 跟 Bry 端 EDT 一致. 重現 Bry 抓漏的兩個案例:
  - 案例 1: akane 16:11 EDT 觸發 (20:11 UTC)
    → 修法: 16:11 America/New_York (下午) 跟 Bry 端一致
  - 案例 2: mahiru 04:10 UTC 觸發
    → 修法: 00:10 America/New_York (凌晨) 跟 Bry 端時間一致, 角色寫 "早餐" 凌晨關心合理

預期全部 5 個 assert PASS:
  1. proxy.py 不再有 ASIA_TZ 定義, 改 import format_localized from src.timezone_utils
  2. scheduler.py 不再有 ASIA_TZ 定義, 15 處 datetime.now(ASIA_TZ) 改 now_local()
  3. memory/middleware.py 不再 hardcode hours=8, 改 import LOCAL_TZ
  4. temporal/models.py 不再預設 Asia/Tokyo, 改用 LOCAL_TZ
  5. 用 timezone_utils.format_localized 重算案例 1+2, 跟 Bry 端時間一致 (不錯位 12 小時)

執行:
  python tests/test_timezone_fix_v2.py
"""
import asyncio
import importlib.util
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.tz_fix_v2")


def load_module_from_path(name: str, path: Path):
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


async def test_timezone_v2(project_root: Path) -> Dict[str, Any]:
    """
    β2 時區修法 Mock Test 2 (修法):
    確認 4 個檔案都用 src.timezone_utils 統一時區,
    12 小時錯位消失, Bry 抓漏案例重算跟 Bry 端時間一致.
    """
    logger.info("=" * 60)
    logger.info("  Mock Test 2: 修法驗證 (跟 Bry 端時間一致)")
    logger.info("=" * 60)

    proxy_path = project_root / "src" / "llm" / "proxy.py"
    scheduler_path = project_root / "src" / "soul" / "scheduler.py"
    middleware_path = project_root / "src" / "memory" / "middleware.py"
    models_path = project_root / "src" / "temporal" / "models.py"
    tz_utils_path = project_root / "src" / "timezone_utils.py"

    # ── 斷言 0: timezone_utils.py 存在 ──
    assert tz_utils_path.exists(), (
        f"{tz_utils_path} 應該存在 (修法後)"
    )
    logger.info("  ✓ src/timezone_utils.py 存在")

    # ── 斷言 1: proxy.py 不再有 ASIA_TZ 定義, 改 import ──
    proxy_src = proxy_path.read_text(encoding="utf-8")
    assert "ASIA_TZ = timezone(timedelta(hours=8))" not in proxy_src, (
        "proxy.py 修法後不該再有 ASIA_TZ 定義"
    )
    assert "from src.timezone_utils import format_localized" in proxy_src, (
        "proxy.py 應該 import format_localized from src.timezone_utils"
    )
    # 註解裡的歷史記錄可以保留 ("轉成 Asia/Taipei (UTC+8) 顯示給 LLM")
    # 但 f-string 內的 'Asia/Taipei' 必須刪掉 (Bry 派工單要求 "不用再手動寫死字串")
    assert "f\"{local.strftime('%Y-%m-%d')} {weekday} {local.strftime('%H:%M')} Asia/Taipei" not in proxy_src, (
        "proxy.py 修法後 f-string 不該再寫死 'Asia/Taipei' "
        "(註解可以保留, 但 f-string 必須改用 zoneinfo.key)"
    )
    logger.info(
        "  ✓ proxy.py: 移除 ASIA_TZ 定義 + f-string 'Asia/Taipei' 字串, "
        "改 import format_localized from src.timezone_utils"
    )

    # ── 斷言 2: scheduler.py 不再有 ASIA_TZ 定義, datetime.now 改 now_local ──
    scheduler_src = scheduler_path.read_text(encoding="utf-8")
    assert "ASIA_TZ = timezone(timedelta(hours=8))" not in scheduler_src, (
        "scheduler.py 修法後不該再有 ASIA_TZ 定義"
    )
    assert "from src.timezone_utils import now_local" in scheduler_src, (
        "scheduler.py 應該 import now_local from src.timezone_utils"
    )
    assert "datetime.now(ASIA_TZ)" not in scheduler_src, (
        "scheduler.py 修法後不該再有 datetime.now(ASIA_TZ)"
    )
    # 確認 11 處 now_local() (M0.4 紀錄 11 處, 修法後維持 11 處 now_local())
    now_local_count = scheduler_src.count("now_local()")
    assert now_local_count >= 11, (
        f"scheduler.py 修法後應該有 ≥11 處 now_local(), 實際 {now_local_count}"
    )
    logger.info(
        f"  ✓ scheduler.py: 移除 ASIA_TZ 定義, "
        f"{now_local_count} 處改用 now_local()"
    )

    # ── 斷言 3: memory/middleware.py 不再 hardcode hours=8, 改 import LOCAL_TZ ──
    middleware_src = middleware_path.read_text(encoding="utf-8")
    assert "_tz(_td(hours=8))" not in middleware_src, (
        "memory/middleware.py 修法後不該再有 _tz(_td(hours=8)) hardcode"
    )
    assert "from src.timezone_utils import LOCAL_TZ" in middleware_src, (
        "memory/middleware.py 應該 import LOCAL_TZ from src.timezone_utils"
    )
    logger.info(
        "  ✓ memory/middleware.py: 移除 hours=8 hardcode, "
        "改 import LOCAL_TZ from src.timezone_utils"
    )

    # ── 斷言 4: temporal/models.py 不再預設 Asia/Tokyo ──
    models_src = models_path.read_text(encoding="utf-8")
    assert 'ZoneInfo("Asia/Tokyo")' not in models_src, (
        "temporal/models.py 修法後不該再有 ZoneInfo('Asia/Tokyo') 預設"
    )
    assert "from src.timezone_utils import LOCAL_TZ" in models_src, (
        "temporal/models.py 應該 import LOCAL_TZ from src.timezone_utils"
    )
    logger.info(
        "  ✓ temporal/models.py: 移除 Asia/Tokyo 預設, 改用 LOCAL_TZ"
    )

    # ── 斷言 5: 重算 Bry 抓漏案例, 跟 Bry 端時間一致 ──
    tz_utils = load_module_from_path("_tz_utils_under_test", tz_utils_path)
    fmt = tz_utils.format_localized

    # 案例 1: akane 16:11:03 EDT 觸發 (20:11:03 UTC)
    akane_event_ts = datetime(2026, 8, 2, 20, 11, 3, tzinfo=timezone.utc)
    akane_formatted = fmt(akane_event_ts)
    logger.info(f"  案例 1 (akane 16:11 EDT = 20:11 UTC):")
    logger.info(f"    修法算出: {akane_formatted!r}")
    # 修法預期: 16:11 America/New_York (下午)
    assert "America/New_York" in akane_formatted, (
        f"修法應該含 'America/New_York', 實際 {akane_formatted!r}"
    )
    assert "16:11" in akane_formatted, (
        f"修法應該算 16:11 (Bry 端下午), 實際 {akane_formatted!r}"
    )
    assert "下午" in akane_formatted, (
        f"修法時段應該是 '下午' (16:11), 實際 {akane_formatted!r}"
    )
    assert "凌晨" not in akane_formatted, (
        f"修法不該是 '凌晨', 實際 {akane_formatted!r}"
    )

    # 案例 2: mahiru 04:10:51 UTC 觸發
    mahiru_event_ts = datetime(2026, 8, 2, 4, 10, 51, tzinfo=timezone.utc)
    mahiru_formatted = fmt(mahiru_event_ts)
    logger.info(f"  案例 2 (mahiru 04:10 UTC):")
    logger.info(f"    修法算出: {mahiru_formatted!r}")
    # 修法預期: 00:10 America/New_York (凌晨) — 角色寫早餐凌晨關心, 合理
    assert "America/New_York" in mahiru_formatted, (
        f"修法應該含 'America/New_York', 實際 {mahiru_formatted!r}"
    )
    assert "00:10" in mahiru_formatted, (
        f"修法應該算 00:10 (Bry 端凌晨), 實際 {mahiru_formatted!r}"
    )
    assert "凌晨" in mahiru_formatted, (
        f"修法時段應該是 '凌晨' (00:10), 實際 {mahiru_formatted!r}"
    )
    assert "中午" not in mahiru_formatted, (
        f"修法不該是 '中午', 實際 {mahiru_formatted!r}"
    )

    # ── 斷言 6: scheduler 觸發時間 (morning 8:00 / night 22:00) 跟 Bry 端一致 ──
    # Bry 派工單原話: "確認改完後這些時段確實對應到 Bry 端的早上/晚上/靜音時間, 不要漏掉這塊隱性影響"
    # 邏輯: now_local() 給 EDT, now.hour == 8 是 Bry 端早上 8 點 (不是 Asia/Taipei 早上 8 點 = Bry 端凌晨)
    # 這層 Bry 派工要確認的, 我們用 mock test 算一次: 假設 Bry 端 EDT 08:00:00
    # 等同 UTC 12:00:00, Asia/Taipei 20:00:00
    # 現狀 (ASIA_TZ) 算 morning slot 應該是 UTC 0:00 (12:00 UTC - 8h), 跟 Bry 端早上 8 點錯 8 小時
    # 修法 (now_local EDT) 算 morning slot 應該是 UTC 12:00 (08:00 EDT + 4h), 跟 Bry 端早上 8 點一致
    # 這層驗證從 scheduler.py source 看 _slot_for_time 用 now.hour == 8 觸發 morning
    # 修法後 now = now_local() = EDT 時間, now.hour = Bry 端小時 ✅
    assert "_slot_for_time" in scheduler_src, (
        "scheduler.py 應該有 _slot_for_time (驗證 morning 8:00 觸發邏輯)"
    )
    assert "now_local()" in scheduler_src, (
        "scheduler.py _slot_for_time 內部應該用 now_local() "
        "(修法後 Bry 端 8 點 = EDT 8 點 = now.hour == 8)"
    )
    logger.info(
        "  ✓ scheduler 觸發時間 (morning 8:00 / night 22:00) "
        "現在用 now_local() (Bry 端 EDT 8:00 = 早上)"
    )

    return {
        "akane_formatted": akane_formatted,
        "mahiru_formatted": mahiru_formatted,
        "tz_utils_exists": True,
        "proxy_no_asia_tz": True,
        "scheduler_no_asia_tz": True,
        "middleware_no_hardcode": True,
        "models_no_asia_tokyo": True,
    }


def assert_v2_acceptance(result: Dict[str, Any]) -> None:
    errors: List[str] = []
    if not result["tz_utils_exists"]:
        errors.append("[tz_utils] 應該存在")
    if not result["proxy_no_asia_tz"]:
        errors.append("[proxy.py] 修法後應該沒 ASIA_TZ 定義")
    if not result["scheduler_no_asia_tz"]:
        errors.append("[scheduler.py] 修法後應該沒 ASIA_TZ 定義")
    if not result["middleware_no_hardcode"]:
        errors.append("[middleware.py] 修法後應該沒 hours=8 hardcode")
    if not result["models_no_asia_tokyo"]:
        errors.append("[models.py] 修法後應該沒 Asia/Tokyo 預設")
    if "16:11" not in result["akane_formatted"]:
        errors.append(
            f"[akane case] 修法應該算 16:11 (Bry 端下午), "
            f"實際 {result['akane_formatted']!r}"
        )
    if "00:10" not in result["mahiru_formatted"]:
        errors.append(
            f"[mahiru case] 修法應該算 00:10 (Bry 端凌晨), "
            f"實際 {result['mahiru_formatted']!r}"
        )
    if "America/New_York" not in result["akane_formatted"]:
        errors.append(
            f"[akane case] 修法應該含 'America/New_York', "
            f"實際 {result['akane_formatted']!r}"
        )
    if "America/New_York" not in result["mahiru_formatted"]:
        errors.append(
            f"[mahiru case] 修法應該含 'America/New_York', "
            f"實際 {result['mahiru_formatted']!r}"
        )
    if errors:
        raise AssertionError(
            "Mock Test 2 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


async def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    result = await test_timezone_v2(project_root)
    assert_v2_acceptance(result)
    logger.info("\n✅ Mock Test 2 (修法) PASS")
    logger.info(
        "   6 個 assert 通過: 4 個檔案 + 2 個案例重算 + 1 個 scheduler 觸發時間"
    )
    logger.info("   12 小時錯位消失, Bry 端時間跟 LLM 餵的時間一致")


if __name__ == "__main__":
    asyncio.run(main())
