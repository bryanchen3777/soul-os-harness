"""
test_timezone_fix_v1.py
Bry 派工 2026-08-03 18:21 — Mock Test 1

🔴 SOUL-TECH-DEBT-001（stale-test calibration, class (a)）— 本檔定位已翻轉
--------------------------------------------------------------------------
本檔原本是「**現狀驗證**（baseline）」測試：它斷言 4 個檔案**仍然**用
ASIA_TZ / 寫死 hours=8 / Asia/Tokyo，並用 Bry 抓漏的兩個案例重現「12 小時錯位」。
那正是**修法前**的世界觀。

修法已在同日落地（`src/timezone_utils.py`，Bry 拍板 America/New_York），
所以本檔的每一條斷言都已過期 —— 留著只會永遠紅，而且紅的原因與生產健康度
無關（它紅是在說「bug 修好了」，不是「壞了」）。

本批次校準為：**釘住修好後的狀態**（單一時區來源 = src.timezone_utils，
預設 America/New_York），並用 Bry 當年抓漏的**同一組兩個案例**驗證
12 小時錯位確實消失（akane 16:11 EDT 不再變成 04:11 凌晨；
mahiru 04:10 UTC 不再變成 12:10 中午）。

原案例的時間與時段（16:11 下午 / 00:10 凌晨）**刻意不在**本檔這條斷言裡
斷言 —— 因為它們會踩到一個**另一個**真實缺陷（format_localized 的星期錯一格），
已獨立拆成 `test_format_localized_weekday_matches_calendar` 單獨留紅，
不與本檔混在一起。見該測試內的 SOUL-BUG 說明。
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
    β2 時區修法（校準後）: 4 個檔案已統一走 src.timezone_utils,
    Bry 抓漏的 12 小時錯位已消失。
    """
    logger.info("=" * 60)
    logger.info("  Mock Test 1: 單一時區來源（America/New_York）驗證")
    logger.info("=" * 60)

    proxy_path = project_root / "src" / "llm" / "proxy.py"
    scheduler_path = project_root / "src" / "soul" / "scheduler.py"
    middleware_path = project_root / "src" / "memory" / "middleware.py"
    models_path = project_root / "src" / "temporal" / "models.py"

    # ── 斷言 1: 4 個檔案的「舊寫法」程式碼全部消失, 且都改走單一時區來源 ──
    # 注意: 只斷言**程式碼形式**消失。註解/docstring 裡仍留有歷史敘述
    # （proxy.py:30 / scheduler.py:14,34-36 / middleware.py:759,764 等）,
    # 那是刻意保留的決策紀錄, 不是缺陷 —— 早期版本用整段字串比對會誤判。
    legacy_code_forms = [
        ("proxy.py", proxy_path, "ASIA_TZ = timezone(timedelta(hours=8))"),
        ("proxy.py", proxy_path, "ASIA_TZ = timezone(timedelta(hours = 8))"),
        ("scheduler.py", scheduler_path, "ASIA_TZ = timezone(timedelta(hours=8))"),
        ("middleware.py", middleware_path, "_tz(_td(hours=8))"),
        ("models.py", models_path, 'ZoneInfo("Asia/Tokyo")'),
    ]
    for label, path, form in legacy_code_forms:
        src = path.read_text(encoding="utf-8")
        assert form not in src, (
            f"{label} 仍殘留修法前的寫法 {form!r}（應已統一到 src.timezone_utils）"
        )

    # 反向: 每個檔案都真的 import 了單一時區來源（不是「舊寫法剛好不匹配」）
    for label, path in [
        ("proxy.py", proxy_path),
        ("scheduler.py", scheduler_path),
        ("memory/middleware.py", middleware_path),
        ("temporal/models.py", models_path),
    ]:
        src = path.read_text(encoding="utf-8")
        assert "from src.timezone_utils" in src, (
            f"{label} 應該 import src.timezone_utils（單一時區來源）"
        )
    logger.info("  ✓ 4 個檔案: 舊寫法全數消失, 且都 import src.timezone_utils")

    # ── 斷言 2: 預設時區就是 Bry 拍板的 America/New_York ──
    from src.timezone_utils import DEFAULT_TIMEZONE_NAME, LOCAL_TZ, get_local_tz
    assert DEFAULT_TIMEZONE_NAME == "America/New_York", (
        f"預設時區應該是 America/New_York, 實際 {DEFAULT_TIMEZONE_NAME!r}"
    )
    assert LOCAL_TZ.key == "America/New_York", (
        f"LOCAL_TZ 應該是 America/New_York, 實際 {getattr(LOCAL_TZ, 'key', LOCAL_TZ)!r}"
    )
    # 邊界: 這確實不是 UTC+8 —— 否則「校準」等於沒做
    assert LOCAL_TZ.utcoffset(datetime(2026, 8, 2, 12, 0)) != timedelta(hours=8), (
        f"LOCAL_TZ 不該是 UTC+8: {LOCAL_TZ}"
    )
    logger.info(f"  ✓ 單一時區來源: DEFAULT_TIMEZONE_NAME = {DEFAULT_TIMEZONE_NAME}")

    # ── 斷言 3: Bry 抓漏的兩個案例, 12 小時錯位確實消失 ──
    # _format_event_timestamp 現已是 timezone_utils.format_localized 的別名
    # (proxy.py:34), 這裡直接 import 底層函式驗證, 避免再靠 exec 整個 proxy.py。
    from src.timezone_utils import format_localized

    # 案例 1: akane 16:11:03 EDT 觸發（= 20:11:03 UTC）
    # 修法前 → "04:11 Asia/Taipei（凌晨）"；修法後 → "16:11 America/New_York（下午）"
    akane_formatted = format_localized(datetime(2026, 8, 2, 20, 11, 3, tzinfo=timezone.utc))
    logger.info(f"  案例 1 (akane 16:11 EDT = 20:11 UTC) → {akane_formatted!r}")
    assert "America/New_York" in akane_formatted, (
        f"應該輸出 America/New_York, 實際 {akane_formatted!r}"
    )
    assert "16:11" in akane_formatted, (
        f"應該是 Bry 端的 16:11（不再被 +8 推到 04:11）, 實際 {akane_formatted!r}"
    )
    assert "下午" in akane_formatted, (
        f"16:11 應該判為「下午」, 實際 {akane_formatted!r}"
    )
    assert "凌晨" not in akane_formatted, (
        f"不該再出現修法前的 12 小時錯位（凌晨）, 實際 {akane_formatted!r}"
    )

    # 案例 2: mahiru 04:10:51 UTC 觸發
    # 修法前 → "12:10 Asia/Taipei（中午）"；修法後 → "00:10 America/New_York（凌晨）"
    mahiru_formatted = format_localized(datetime(2026, 8, 2, 4, 10, 51, tzinfo=timezone.utc))
    logger.info(f"  案例 2 (mahiru 04:10 UTC) → {mahiru_formatted!r}")
    assert "America/New_York" in mahiru_formatted, (
        f"應該輸出 America/New_York, 實際 {mahiru_formatted!r}"
    )
    assert "00:10" in mahiru_formatted, (
        f"應該是 Bry 端的 00:10（不再被 +8 推到 12:10）, 實際 {mahiru_formatted!r}"
    )
    assert "凌晨" in mahiru_formatted, (
        f"00:10 應該判為「凌晨」, 實際 {mahiru_formatted!r}"
    )
    assert "中午" not in mahiru_formatted, (
        f"不該再出現修法前的 12 小時錯位（中午）, 實際 {mahiru_formatted!r}"
    )

    # ── 動態驗證: proxy 的別名確實指向同一個實作 ──
    proxy_mod = load_module_from_path("_proxy_under_test", proxy_path)
    assert proxy_mod._format_event_timestamp is format_localized, (
        "proxy.py 的 _format_event_timestamp 應該就是 timezone_utils.format_localized 的別名"
    )
    assert proxy_mod._format_event_timestamp(
        datetime(2026, 8, 2, 20, 11, 3, tzinfo=timezone.utc)
    ) == akane_formatted, "別名與底層函式對同一輸入應給出同一結果"
    logger.info("  ✓ proxy._format_event_timestamp == timezone_utils.format_localized")

    return {
        "akane_formatted": akane_formatted,
        "mahiru_formatted": mahiru_formatted,
        "default_timezone_name": DEFAULT_TIMEZONE_NAME,
        "legacy_forms_gone": True,
        "single_source_imported": True,
    }


# ════════════════════════════════════════════════════════════════════
# 🔴 SOUL-BUG：format_localized 的星期錯一格（真實 production defect）
# ════════════════════════════════════════════════════════════════════
# 故意留紅。修好 production 後自然轉綠 —— 不要用 skip/xfail 蓋掉。

def test_format_localized_weekday_matches_calendar() -> None:
    """format_localized 輸出的星期必須等於日期的正確星期。

    🔴 SOUL-BUG（SOUL-TECH-DEBT-001 深查）— genuine production defect，**故意留紅**

    症狀: `format_localized()`（= `proxy._format_event_timestamp`, proxy.py:34）
    對每個 LLM system prompt 的時間行輸出一個**錯誤的星期**，整組錯一格。
    實測（本檔執行時）:
      2026-08-02（真的是週日）→ "2026-08-02 週六 16:11 America/New_York（下午）"
      2026-08-03（真的是週一）→ "2026-08-03 週日 16:11 America/New_York（下午）"
    → 日期與時刻、時區、時段標籤都對，**只有星期錯，且恆錯一格（早一天）**。

    根因: src/timezone_utils.py:250
        weekday = _WEEKDAY_CN[local.weekday()]
    `_WEEKDAY_CN`（timezone_utils.py:42）以**週日=0** 索引：
        ["週日","週一","週二","週三","週四","週五","週六"]
    但 `datetime.weekday()` 是**週一=0**。兩個索引基準不同 → 整組錯一格。

    諷刺的是, **同一個模組早就把這條規則寫下來了**:
    timezone_utils.py:196-207 明確寫著
        「🔴 索引口徑差異…直接用 `weekday()` 索引會整組錯一格」
        「⚠️ **不得**寫成 `_WEEKDAY_CN[dt.weekday()]` —— 索引起點不同, 會整組錯一格」
    並提供了正確的 `_weekday_cn(dt)`（用 `int(dt.strftime("%w"))`, :202-207）。
    `format_localized` 只是**沒呼叫**那個 helper。

    影響: 每次角色發話, system prompt 的時間行都告訴角色錯誤的星期幾。
    屬 TIME-PERCEPTION-1（Owner 2026-10-03 裁定）的時間錨點錯誤, 與
    「精確時間降格但保留 safety anchor」的設計直接衝突 —— 角色會據此說錯話。

    修法（1 行, 不在本工單權限內, 需另開 SOUL-BUG）:
        timezone_utils.py:250  `_WEEKDAY_CN[local.weekday()]`
                           →  `_weekday_cn(local)`
    （`_weekday_cn` 已在同檔 :202 定義, 改動 0 新增符號。）

    為什麼獨立成一支而不是併進 test_timezone_v1:
      兩者是**不同**的問題。test_timezone_v1 守的是「單一時區來源 + 12 小時
      錯位已修好」（綠）；本支守的是「星期索引基準正確」（紅）。混在一起會
      讓前者也跟著紅，等於用一個真 bug 蓋掉一個已修好的驗收。
    """
    from src.timezone_utils import format_localized

    # 刻意挑兩個不同星期幾、一個是週日, 確保不是巧合對上一次。
    cases = [
        # (UTC 輸入, 期望出現在輸出裡的正確星期, 說明)
        (datetime(2026, 8, 2, 20, 11, 3, tzinfo=timezone.utc), "週日", "2026-08-02 = 週日"),
        (datetime(2026, 8, 3, 20, 11, 3, tzinfo=timezone.utc), "週一", "2026-08-03 = 週一"),
        (datetime(2026, 8, 5, 14, 0, 0, tzinfo=timezone.utc), "週三", "2026-08-05 = 週三"),
    ]
    failures = []
    for utc_dt, expected_weekday, why in cases:
        out = format_localized(utc_dt)
        if expected_weekday not in out:
            failures.append(
                f"{why}: 期望含 {expected_weekday!r}, 實際 {out!r}"
            )

    assert not failures, (
        "SOUL-BUG: format_localized 的星期索引錯一格"
        "（timezone_utils.py:250 用 _WEEKDAY_CN[local.weekday()]，"
        "但該清單是週日=0 而 weekday() 是週一=0；"
        "同檔 :196-207 早已明文禁止此寫法並提供 _weekday_cn()，:250 沒呼叫它）。"
        "修法: 改成 _weekday_cn(local)。\n  " + "\n  ".join(failures)
    )


def assert_v1_acceptance(result: Dict[str, Any]) -> None:
    errors: List[str] = []
    if not result["legacy_forms_gone"]:
        errors.append("[舊寫法] 修法前的 UTC+8 寫法應該已全數消失, 還在")
    if not result["single_source_imported"]:
        errors.append("[單一時區來源] 4 個檔案應該都 import src.timezone_utils")
    if result["default_timezone_name"] != "America/New_York":
        errors.append(
            f"[預設時區] 應該是 America/New_York, 實際 {result['default_timezone_name']!r}"
        )
    if "America/New_York" not in result["akane_formatted"]:
        errors.append(
            f"[akane case] 應該含 'America/New_York', 實際 {result['akane_formatted']!r}"
        )
    if "16:11" not in result["akane_formatted"]:
        errors.append(
            f"[akane case] 應該算 16:11, 實際 {result['akane_formatted']!r}"
        )
    if "America/New_York" not in result["mahiru_formatted"]:
        errors.append(
            f"[mahiru case] 應該含 'America/New_York', 實際 {result['mahiru_formatted']!r}"
        )
    if "00:10" not in result["mahiru_formatted"]:
        errors.append(
            f"[mahiru case] 應該算 00:10, 實際 {result['mahiru_formatted']!r}"
        )
    if errors:
        raise AssertionError(
            "Mock Test 1 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


async def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    result = await test_timezone_v1(project_root)
    assert_v1_acceptance(result)
    logger.info("\n✅ Mock Test 1 (單一時區來源) PASS")
    logger.info("   4 個檔案舊寫法全消失 + 都 import timezone_utils")
    logger.info("   預設時區 America/New_York, Bry 抓漏的 12 小時錯位已消失")


if __name__ == "__main__":
    asyncio.run(main())
