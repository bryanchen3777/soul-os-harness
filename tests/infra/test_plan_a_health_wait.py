"""PLAN-A-HEALTH-WAIT — Plan A 的健康等待契約（Owner 2026-10-31 裁定：60s → 180s）。

為什麼有這條（2026-10-31 實測）
--------------------------------
2026-10-31 00:09:19，Plan A 回報::

    ERROR service not healthy within 60s (pid=...) - Plan A FAILED

但主服務在 **00:10:10** 就緒（相差 51 秒）⇒ 這是**假失敗**，不是真的沒起來。
成因：主服務啟動期會跑 Elevation 掃描，偶遇外部 LLM 延遲時，綁埠可被拉長到 ~120 秒；
而 Plan A 的健康等待上限只有 60 秒 ⇒ 上限比「實際就緒時間」短。

假失敗的代價不是「多寫一行 log」：watchdog 的重啟預算 ``$counter.n_restarts``
是在**拉起之前**就遞增的（``scripts/_watchdog.ps1`` step 7）⇒ 假失敗照樣燒預算。

⇒ 裁定：放寬至 **180 秒**，對齊 ``server_ops.ps1`` 的 ``$healthReadyTimeoutSec``。

本檔釘住這條
------------
* **P1** ``$healthWaitTimeoutSec`` 存在且為 180；
* **P2** 截止時間**用常數**（不得寫死 ``AddSeconds(60)``）；
* **P3** 失敗訊息**用常數**（不得寫死 ``60s``）—— 否則訊息會與實際行為脫節；
* **P4** 與 ``server_ops.ps1`` 的 ``$healthReadyTimeoutSec`` **實讀比對**（不抄常數），
  且兩者必須落在 **[90, 600]**（上限必須小於 watchdog 的維護鎖 stale 門檻 10 分鐘）；
* **M1／M2／M3** 雙向變異：把 180 改回 60、把截止時間改回寫死、把訊息改回寫死
  ⇒ 守門**必須轉紅**（護欄自己不是盲腸）。

本檔純 Python、唯讀（只讀 ``scripts/*.ps1`` 的原始碼），
不啟任何行程、不碰 ``data/**``、不綁任何埠。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_A_PATH = REPO_ROOT / "scripts" / "_start_plan_a.ps1"
SERVER_OPS_PATH = REPO_ROOT / "scripts" / "server_ops.ps1"

#: 逾時上限必須落在這個區間（理由見 server_ops.ps1 的 $healthReadyTimeoutSec 註解：
#: 下限 90s ＝ 實測 uvicorn 綁埠要 1~3 分鐘；上限 600s ＝ watchdog 維護鎖 stale 門檻）。
MIN_TIMEOUT_SEC = 90
MAX_TIMEOUT_SEC = 600

#: Owner 2026-10-31 裁定的值。
EXPECTED_TIMEOUT_SEC = 180


def _read(path: Path) -> str:
    """讀 .ps1 原始碼（去 BOM；行尾正規化，避免 CRLF/LF 影響比對）。"""
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _code_only(source: str) -> str:
    """去掉整行註解，避免註解裡出現的秒數被誤判成程式碼。"""
    kept = []
    for line in source.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _timeout_of(source: str, var_name: str):
    """抓出 ``$var = <整數>`` 的值；抓不到回 None。

    🔺 取**最後一個**賦值：PowerShell 的語意是「後賦值覆蓋前賦值」
    ⇒ 守門必須與執行期一致；否則「影子賦值」（在檔尾再賦一次）會讓本守門**假綠**
    —— 那正是文字層級守門最典型的盲區。
    """
    pattern = r"^\s*\$%s\s*=\s*(\d+)\s*$" % re.escape(var_name)
    matches = re.findall(pattern, _code_only(source), re.M)
    return int(matches[-1]) if matches else None


def _check_plan_a(source: str, ops_source: str) -> None:
    """P1–P4 的全部斷言集中在這裡 —— 變異實驗直接餵「變異後的原始碼」即可。"""
    code = _code_only(source)

    # ── P1：具名常數存在且為裁定值 ──
    timeout = _timeout_of(source, "healthWaitTimeoutSec")
    assert timeout == EXPECTED_TIMEOUT_SEC, (
        "Plan A 的 $healthWaitTimeoutSec 應為 %d，實際 %r"
        % (EXPECTED_TIMEOUT_SEC, timeout)
    )

    # ── P2：截止時間必須用常數 ──
    assert "AddSeconds(60)" not in code, (
        "截止時間不得寫死 AddSeconds(60) —— 必須使用 $healthWaitTimeoutSec"
    )
    assert re.search(r"AddSeconds\(\s*\$healthWaitTimeoutSec\s*\)", code), (
        "截止時間必須使用 $healthWaitTimeoutSec"
    )

    # ── P3：失敗訊息必須用常數 ──
    assert "within 60s" not in code, (
        "失敗訊息不得寫死 'within 60s' —— 否則訊息會與實際上限脫節"
    )
    assert "${healthWaitTimeoutSec}s" in code, (
        "失敗訊息必須使用 ${healthWaitTimeoutSec}s"
    )

    # ── P4：與 server_ops.ps1 實讀比對 + 區間 ──
    ops_timeout = _timeout_of(ops_source, "healthReadyTimeoutSec")
    assert ops_timeout is not None, (
        "server_ops.ps1 的 $healthReadyTimeoutSec 不見了 —— 這份守門的對照基準可能已漂移"
    )
    assert timeout == ops_timeout, (
        "Plan A 的健康等待（%r 秒）與 server_ops.ps1 的就緒探針（%r 秒）不一致"
        % (timeout, ops_timeout)
    )
    assert MIN_TIMEOUT_SEC <= timeout <= MAX_TIMEOUT_SEC, (
        "健康等待上限 %r 秒不在 [%d, %d] 區間內" % (timeout, MIN_TIMEOUT_SEC, MAX_TIMEOUT_SEC)
    )


def test_p1_p4_plan_a_health_wait_contract():
    """P1–P4：Plan A 的健康等待必須是具名常數 180，且與 server_ops.ps1 一致。"""
    _check_plan_a(_read(PLAN_A_PATH), _read(SERVER_OPS_PATH))


def test_p5_both_scripts_still_define_their_own_constant():
    """P5：兩個腳本各自定義自己的具名常數（跨檔常數慣例），不得有一邊靠字面值。"""
    plan_a_code = _code_only(_read(PLAN_A_PATH))
    ops_code = _code_only(_read(SERVER_OPS_PATH))
    assert "$healthWaitTimeoutSec" in plan_a_code, "Plan A 必須有自己的 $healthWaitTimeoutSec"
    assert "$healthReadyTimeoutSec" in ops_code, "server_ops.ps1 必須保有 $healthReadyTimeoutSec"


# ───────────────────────────────────────────────────────────
# 雙向變異：每條變異都必須讓上面的守門**轉紅**
# ───────────────────────────────────────────────────────────

def _mutate_back_to_60(source: str) -> str:
    return source.replace(
        "$healthWaitTimeoutSec = 180", "$healthWaitTimeoutSec = 60", 1
    )


def _mutate_deadline_to_literal(source: str) -> str:
    return source.replace(
        "AddSeconds($healthWaitTimeoutSec)", "AddSeconds(60)", 1
    )


def _mutate_message_to_literal(source: str) -> str:
    return source.replace(
        "within ${healthWaitTimeoutSec}s", "within 60s", 1
    )


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("M1_常數改回 60", _mutate_back_to_60),
        ("M2_截止時間改回寫死", _mutate_deadline_to_literal),
        ("M3_失敗訊息改回寫死", _mutate_message_to_literal),
    ],
)
def test_m1_m3_mutations_turn_the_guard_red(name, mutate):
    """牙齒：變異必須真的讓守門轉紅（否則這條守門就是裝飾）。"""
    plan_a_src = _read(PLAN_A_PATH)
    ops_src = _read(SERVER_OPS_PATH)
    mutated = mutate(plan_a_src)
    assert mutated != plan_a_src, "變異沒有真的改到任何東西（錨點可能已漂移）"
    with pytest.raises(AssertionError):
        _check_plan_a(mutated, ops_src)


WATCHDOG_PATH = REPO_ROOT / "scripts" / "_watchdog.ps1"

#: Owner 2026-11-01 裁定：STARTUP_WINDOW = 30（PORT_RELEASE）
#: ＋ 180（Plan A healthWait）＋ 60（buffer）＝ 270。
EXPECTED_STARTUP_WINDOW_SEC = 270

#: buffer 的秒數（啟動窗口扣除釋放等待與健康等待後的餘量）。
STARTUP_WINDOW_BUFFER_SEC = 60


def _check_watchdog_window(watchdog_source: str, plan_a_source: str, ops_source: str) -> None:
    """P6–P7：三方交叉 —— watchdog 啟動窗口必須蓋住釋放等待＋健康等待＋buffer。

    三個秒數全部**實讀三檔實際常數**做比較（不得寫死任一單邊為字面值）；
    變異實驗直接餵「變異後的原始碼」即可。
    """
    startup = _timeout_of(watchdog_source, "STARTUP_WINDOW_SECONDS")
    assert startup is not None, (
        "_watchdog.ps1 的 $STARTUP_WINDOW_SECONDS 不見了 —— 對照基準可能已漂移"
    )
    port_release = _timeout_of(watchdog_source, "PORT_RELEASE_WAIT_SECONDS")
    assert port_release is not None, (
        "_watchdog.ps1 的 $PORT_RELEASE_WAIT_SECONDS 不見了 —— 對照基準可能已漂移"
    )
    plan_a = _timeout_of(plan_a_source, "healthWaitTimeoutSec")
    assert plan_a is not None, (
        "_start_plan_a.ps1 的 $healthWaitTimeoutSec 不見了 —— 對照基準可能已漂移"
    )
    ops = _timeout_of(ops_source, "healthReadyTimeoutSec")
    assert ops is not None, (
        "server_ops.ps1 的 $healthReadyTimeoutSec 不見了 —— 對照基準可能已漂移"
    )

    # ── P6：啟動窗口必須是裁定值 270（30 PORT_RELEASE ＋ 180 healthWait ＋ 60 buffer）──
    assert startup == EXPECTED_STARTUP_WINDOW_SEC, (
        "watchdog 的 $STARTUP_WINDOW_SECONDS 應為 %d，實際 %r"
        % (EXPECTED_STARTUP_WINDOW_SEC, startup)
    )

    # ── P7a：三方交叉不等式（全實讀常數比較，不得寫死單邊）──
    assert startup >= port_release + plan_a + STARTUP_WINDOW_BUFFER_SEC, (
        "啟動窗口（%r 秒）必須 ≥ 釋放等待（%r）＋ 健康等待（%r）＋ buffer（%r）"
        % (startup, port_release, plan_a, STARTUP_WINDOW_BUFFER_SEC)
    )

    # ── P7b：server_ops 的就緒探針必須顯式等於 Plan A 的健康等待 ──
    assert ops == plan_a, (
        "server_ops 的健康就緒（%r 秒）與 Plan A 的健康等待（%r 秒）不一致"
        % (ops, plan_a)
    )


def test_p6_p7_watchdog_startup_window_tripartite():
    """P6–P7：watchdog 270 ≥ PORT_RELEASE ＋ Plan A healthWait ＋ 60 buffer，三方實讀比對。"""
    _check_watchdog_window(_read(WATCHDOG_PATH), _read(PLAN_A_PATH), _read(SERVER_OPS_PATH))


# ───────────────────────────────────────────────────────────
# 雙向變異（P6/P7）：每條變異都必須讓上面的三方守門**轉紅**
# ───────────────────────────────────────────────────────────

def _mutate_startup_back_to_90(source: str) -> str:
    return source.replace(
        "$STARTUP_WINDOW_SECONDS = 270", "$STARTUP_WINDOW_SECONDS = 90", 1
    )


def _mutate_port_release_inflated(source: str) -> str:
    return source.replace(
        "$PORT_RELEASE_WAIT_SECONDS = 30", "$PORT_RELEASE_WAIT_SECONDS = 300", 1
    )


def _mutate_ops_timeout_drift(source: str) -> str:
    return source.replace(
        "$healthReadyTimeoutSec = 180", "$healthReadyTimeoutSec = 120", 1
    )


@pytest.mark.parametrize(
    "name,mutate,target",
    [
        ("M4_啟動窗口改回 90", _mutate_startup_back_to_90, "watchdog"),
        ("M5_釋放等待膨脹到 300", _mutate_port_release_inflated, "watchdog"),
        ("M6_server_ops 就緒漂移到 120", _mutate_ops_timeout_drift, "ops"),
    ],
)
def test_m4_m6_mutations_turn_the_tripartite_guard_red(name, mutate, target):
    """牙齒：變異必須真的讓三方守門轉紅（否則這條守門就是裝飾）。"""
    watchdog_src = _read(WATCHDOG_PATH)
    plan_a_src = _read(PLAN_A_PATH)
    ops_src = _read(SERVER_OPS_PATH)
    if target == "watchdog":
        mutated = mutate(watchdog_src)
        assert mutated != watchdog_src, "變異沒有真的改到任何東西（錨點可能已漂移）"
        with pytest.raises(AssertionError):
            _check_watchdog_window(mutated, plan_a_src, ops_src)
    else:
        mutated = mutate(ops_src)
        assert mutated != ops_src, "變異沒有真的改到任何東西（錨點可能已漂移）"
        with pytest.raises(AssertionError):
            _check_watchdog_window(watchdog_src, plan_a_src, mutated)
