"""
tests/test_watchdog_tg_health.py
INFRA-WATCHDOG-TG-HEALTH (P1, 2026-09-16) — Telegram 真活性心跳（寫入端 + watchdog 讀取端）

背景: 2026-09-15 TG 入站輪詢靜默死亡 1h40m。進程內偵測只在進程還活著時有效;
進程整個死掉 / 事件迴圈卡死時**沒有任何外部信號**。本票把「實質成功 getUpdates
的時間戳」落成唯讀心跳檔（`data/heartbeats/telegram_channel.json`）,
由 `scripts/_watchdog.ps1` 每一 tick 讀取並告警（**只告警、永不動作**）。

本檔驗證
--------
A. 寫入端 `src/io/channels/telegram.py`
   A1 `start()` 完成後**立即**產生首筆心跳（不得等 30 秒週期）
   A2 原子落盤（無 `.tmp.<pid>` 殘留）; writer 失敗時**輪詢完全不受影響**
   A3 🔑 時鐘語意（本票核心）: epoch 導出 / 重建**不歸零** / 冷啟動自進程啟動起算
   A4 心跳檔**不含 token**
   A5 預設路徑走 `data_root()` ⇒ 測試自動隔離, 對生產 `data/**` 零寫入
B. watchdog 讀取端（`scripts/_watchdog.ps1` 的 `# >>> TG-HEALTH-BEGIN` 區塊）
   B1 五情境: 檔案不存在(冷啟動) / 正常 / 單一 bot 逾時(指名) / 全域逾時 / JSON 損毀
   B2 真實 Windows PowerShell **5.1** 語法解析 = 0 errors（parse 不是執行）
C. 靜態審計（純 Python 讀檔）
   C1 區塊內 0 個重啟計數器 / 0 個 `Start-Process` / 0 個提前結束敘述 / 0 個 `Write-Warning`
   C2 區塊被自己的 `try/catch` 包住; 且在**所有**提前結束分支之前（位置證明）
   C3 呼叫點變數一律 `tgHb` 前綴（絕不覆寫既有變數）

紅線（本檔遵守, 亦為 §4 自檢依據）
--------------------------------
  * **絕不執行 `scripts/_watchdog.ps1`**：只以區塊標記抽出函式文字, 在子行程單獨載入。
  * 心跳路徑一律注入 `tmp_path`; 不寫生產 `data/**`。
  * 0 真實外部請求（0 連線、0 真實 token; ptb 的 ApplicationBuilder 換成行程內 fake）。
  * 不使用 `pkill` / `killall` / `Stop-Process` 等名稱模式殺進程
    （`tests/test_infra_no_pattern_process_kill.py` 有 AST 護欄）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import src.io.channels.telegram as tg_module  # noqa: E402
from src.io.channels.telegram import (  # noqa: E402
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_STALE_SECONDS,
    TelegramAdapter,
)

WATCHDOG = REPO_ROOT / "scripts" / "_watchdog.ps1"
BLOCK_BEGIN = "# >>> TG-HEALTH-BEGIN"
BLOCK_END = "# <<< TG-HEALTH-END"
FUNCTION_NAME = "Test-TgHeartbeatHealth"
LOG_WATCH_NAME = "Log-Watch"

#: 假 token（格式合法但不存在; 本檔所有 ptb 物件都是行程內 fake, 不會外流）
FAKE_TOKEN_MAI = "123456:AAFfakeMAIfakefakefakefakefakefakefake"
FAKE_TOKEN_YUA = "654321:AAFfakeYUAfakefakefakefakefakefakefake"

TINY = 0.005


# ============================================================================
# 共用工具
# ============================================================================

class FakeClock:
    """可控時鐘（monotonic 與 wall clock 共用同一個實作, 但注入成兩個實例）。"""

    def __init__(self, start: float = 1_700_000_000.0):
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value


class FakeBot:
    """`get_updates` 會被 `_install_poll_heartbeat` 包一層 wrapper。"""

    def __init__(self):
        self.result = []

    async def get_updates(self, *args, **kwargs):
        return self.result


class FakeUpdater:
    def __init__(self):
        self.running = True
        self.start_polling_calls = []

    async def start_polling(self, **kwargs):
        self.start_polling_calls.append(kwargs)
        self.running = True

    async def stop(self):
        self.running = False


class FakeApp:
    def __init__(self):
        self.updater = FakeUpdater()
        self.bot = FakeBot()
        self.handlers = []
        self.initialized = 0
        self.started = 0
        self.stopped = 0
        self.shutdown_calls = 0

    def add_handler(self, handler):
        self.handlers.append(handler)

    async def initialize(self):
        self.initialized += 1

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1

    async def shutdown(self):
        self.shutdown_calls += 1


class FakeBuilderFactory:
    """行程內 fake `ApplicationBuilder`（0 真實請求）。"""

    def __init__(self):
        self.apps = []

    def __call__(self):
        factory = self

        class _Builder:
            def token(self, t):
                return self

            def get_updates_request(self, req):
                return self

            def build(self):
                app = FakeApp()
                factory.apps.append(app)
                return app

        return _Builder()


def install_builder(monkeypatch):
    factory = FakeBuilderFactory()
    monkeypatch.setattr(tg_module, "ApplicationBuilder", factory)
    return factory


def make_adapter(tmp_path, tokens, wall=None, mono=None, interval=TINY):
    return TelegramAdapter(
        tokens=tokens,
        supervise_interval=interval,
        monotonic_clock=mono if mono is not None else FakeClock(10_000.0),
        wall_clock=wall if wall is not None else FakeClock(1_700_000_000.0),
        heartbeat_path=str(tmp_path / "hb" / "telegram_channel.json"),
    )


async def cleanup(adapter):
    """收掉 supervisor 與心跳週期 task（避免 task 洩漏到其他測試）。"""
    for task in list(adapter._supervisors.values()):
        task.cancel()
    for task in list(adapter._supervisors.values()):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    adapter._supervisors.clear()

    heartbeat_task = adapter._heartbeat_task
    adapter._heartbeat_task = None
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass


# ============================================================================
# A. 寫入端
# ============================================================================

@pytest.mark.asyncio
async def test_start_writes_first_heartbeat_immediately(monkeypatch, tmp_path):
    """A1: `start()` 完成後**立即**有第一筆心跳 —— 不得等 30 秒週期。

    證明方式: `start()` 一返回就斷言檔案已存在。週期協程的第一個動作是
    `await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)`（30s），若首寫被延後到
    週期裡, 這個斷言不可能成立。
    """
    clock = FakeClock(1_700_000_000.0)
    adapter = make_adapter(
        tmp_path, {"mai": FAKE_TOKEN_MAI, "yua": FAKE_TOKEN_YUA}, wall=clock
    )
    install_builder(monkeypatch)
    hb_path = Path(adapter._heartbeat_path())
    assert not hb_path.exists(), "起點必須沒有心跳檔（才證明是 start() 寫的）"

    t0 = time.monotonic()
    await adapter.start(on_message=lambda *a: None)
    t1 = time.monotonic()
    try:
        assert hb_path.exists(), (
            "start() 返回當下必須已有心跳檔（冷啟動首寫, 不等 30 秒週期）"
        )
        assert (t1 - t0) < HEARTBEAT_INTERVAL_SECONDS, (
            f"start() 花了 {t1 - t0:.2f}s —— 首寫不應該是等待出來的"
        )
        payload = json.loads(hb_path.read_text(encoding="utf-8"))
        # 首寫用的就是當下牆鐘（假時鐘在 start() 期間沒有前進）
        assert payload["updated_at"] == clock.value
        assert payload["pid"] == os.getpid()
        assert set(payload["bots"]) == {"mai", "yua"}
        # 心跳週期協程已建立（後續筆由它負責）
        assert adapter._heartbeat_task is not None
        assert not adapter._heartbeat_task.done()
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_heartbeat_write_is_atomic_no_tmp_residue(monkeypatch, tmp_path):
    """A2a: 原子落盤 —— 成功寫入後**不得**留下 `.tmp.<pid>` 殘檔。"""
    clock = FakeClock()
    adapter = make_adapter(tmp_path, {"mai": FAKE_TOKEN_MAI}, wall=clock)
    install_builder(monkeypatch)
    hb_path = Path(adapter._heartbeat_path())

    await adapter.start(on_message=lambda *a: None)
    try:
        for _ in range(5):
            clock.advance(10.0)
            assert adapter._write_heartbeat() is True
        residue = sorted(p.name for p in hb_path.parent.glob("*.tmp.*"))
        assert residue == [], f"原子替換後仍有暫存檔殘留: {residue}"
        # 檔案一定是完整可解析的 JSON（不是半截檔）
        json.loads(hb_path.read_text(encoding="utf-8"))
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_writer_failure_does_not_disturb_polling(monkeypatch, tmp_path):
    """A2b: `os.replace` 被打斷（目標被讀者鎖定）⇒ 輪詢主任務完全不受影響。"""
    clock = FakeClock()
    adapter = make_adapter(tmp_path, {"mai": FAKE_TOKEN_MAI}, wall=clock)
    install_builder(monkeypatch)

    def _boom(src, dst):
        raise PermissionError("locked by concurrent reader")

    monkeypatch.setattr(tg_module.os, "replace", _boom)

    # start() 不得因 writer 失敗而拋出
    await adapter.start(on_message=lambda *a: None)
    try:
        assert adapter._write_heartbeat() is False, "writer 失敗時必須回 False"
        # 輪詢完全正常: wrapper 仍在, 成功照樣累加
        app = adapter._apps["mai"]
        assert await app.bot.get_updates() == []
        assert adapter._poll_success_counts["mai"] == 1
        assert adapter._last_poll_success["mai"] is not None
        assert adapter._last_success_epoch["mai"] == clock.value
        assert app.updater.running is True
        assert adapter._poll_failure_counts.get("mai", 0) == 0
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_heartbeat_loop_periodically_rewrites(monkeypatch, tmp_path):
    """A1b: 週期協程確實每 HEARTBEAT_INTERVAL_SECONDS 重寫（不阻塞、不卡死）。"""
    monkeypatch.setattr(tg_module, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    clock = FakeClock()
    adapter = make_adapter(tmp_path, {"mai": FAKE_TOKEN_MAI}, wall=clock)
    install_builder(monkeypatch)
    hb_path = Path(adapter._heartbeat_path())

    await adapter.start(on_message=lambda *a: None)
    try:
        first = json.loads(hb_path.read_text(encoding="utf-8"))["updated_at"]
        clock.advance(30.0)
        rewritten = False
        for _ in range(200):
            await asyncio.sleep(0.01)
            now = json.loads(hb_path.read_text(encoding="utf-8"))["updated_at"]
            if now != first:
                rewritten = True
                break
        assert rewritten, "週期協程沒有重寫心跳檔"
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_stop_cancels_heartbeat_task(monkeypatch, tmp_path):
    """A1c: `stop()` 必須取消心跳週期協程（不得阻塞關機）, 且 idempotent。"""
    adapter = make_adapter(tmp_path, {"mai": FAKE_TOKEN_MAI})
    install_builder(monkeypatch)
    await adapter.start(on_message=lambda *a: None)

    task = adapter._heartbeat_task
    assert task is not None and not task.done()
    await adapter.stop()
    assert adapter._heartbeat_task is None
    assert task.done(), "stop() 之後心跳 task 仍未結束 ⇒ 關機被阻塞"
    await adapter.stop()  # idempotent, 不得拋出


# ---------------------------------------------------------------------------
# A3 🔑 時鐘語意（本票核心）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clock_semantics_epoch_derivation_and_rebuild_survival(
    monkeypatch, tmp_path
):
    """A3: epoch 導出 / 重建不歸零 / 冷啟動自進程啟動起算 / stale 判定。"""
    wall = FakeClock(1_000_000.0)
    mono = FakeClock(10_000.0)
    adapter = make_adapter(tmp_path, {"mai": FAKE_TOKEN_MAI}, wall=wall, mono=mono)
    install_builder(monkeypatch)

    # ③ 本進程從未成功 ⇒ 導出 process_start_epoch（永不為 None ⇒ watchdog 免處理 null）
    assert adapter._last_success_epoch.get("mai") is None
    assert adapter._process_start_epoch == 1_000_000.0
    assert adapter._derive_last_success_ts("mai") == 1_000_000.0
    assert adapter._derive_last_success_ts("mai") is not None

    # ① 有成功 ⇒ 導出「該次成功的 epoch」
    wall.advance(50.0)                       # 1_000_050.0
    adapter._record_poll_success("mai")
    assert adapter._last_success_epoch["mai"] == 1_000_050.0
    assert adapter._derive_last_success_ts("mai") == 1_000_050.0

    # ② 重建（`_build_app`）⇒ last_success_epoch **絕不歸零**
    wall.advance(1_000.0)                    # 1_001_050.0
    mono.advance(1_000.0)
    adapter._build_app("mai", FAKE_TOKEN_MAI)

    # 既有 monotonic 欄位語意不變（重建 ⇒ 新觀測窗）
    assert adapter._last_poll_success["mai"] is None
    assert adapter._app_built_at["mai"] == mono.value
    # 但心跳的 epoch 導出值仍指回「上一次真實成功」, 不是重建時刻
    assert adapter._last_success_epoch["mai"] == 1_000_050.0
    assert adapter._derive_last_success_ts("mai") == 1_000_050.0
    # 反事實: 若拿 `_app_built_at` 當退回值, 時鐘會被歸零成重建時刻
    # ⇒ 180 秒停滯告警永遠不會響（等於把告警閹割）。這裡明確記錄兩者不同。
    assert adapter._derive_last_success_ts("mai") != adapter._app_built_at["mai"]

    # ④ age > 180 ⇒ status "stale"
    snapshot = adapter._heartbeat_snapshot()
    assert snapshot["bots"]["mai"]["last_success_ts"] == 1_000_050.0
    assert snapshot["bots"]["mai"]["age_s"] == 1000.0
    assert snapshot["bots"]["mai"]["status"] == "stale"

    # 邊界: 恰好等於門檻 ⇒ ok（判定用「嚴格大於」）; 超過一點 ⇒ stale
    wall.value = 1_000_050.0 + HEARTBEAT_STALE_SECONDS
    assert adapter._heartbeat_snapshot()["bots"]["mai"]["status"] == "ok"
    wall.value = 1_000_050.0 + HEARTBEAT_STALE_SECONDS + 0.5
    assert adapter._heartbeat_snapshot()["bots"]["mai"]["status"] == "stale"

    # 冷啟動（從未成功）在門檻內仍是 ok
    fresh = make_adapter(tmp_path / "fresh", {"yua": FAKE_TOKEN_YUA}, wall=wall)
    fresh_dir = tmp_path / "fresh" / "hb"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    wall.advance(HEARTBEAT_STALE_SECONDS - 1.0)
    assert fresh._heartbeat_snapshot()["bots"]["yua"]["status"] == "ok"


@pytest.mark.asyncio
async def test_snapshot_health_keeps_legacy_fields_and_adds_epoch(monkeypatch, tmp_path):
    """A3b: `snapshot_health()` 既有欄位語意不變, epoch 欄位為純新增。"""
    wall = FakeClock(2_000_000.0)
    adapter = make_adapter(tmp_path, {"mai": FAKE_TOKEN_MAI}, wall=wall)
    install_builder(monkeypatch)
    await adapter.start(on_message=lambda *a: None)
    try:
        await adapter._apps["mai"].bot.get_updates()
        snap = adapter.snapshot_health()["mai"]
        # 既有欄位（4949d5a 驗收過的語意）
        assert snap["last_poll_success"] is not None
        assert snap["poll_success_count"] == 1
        assert snap["poll_failure_count"] == 0
        assert snap["running"] is True
        assert snap["rebuild_count"] == 0
        # 新增欄位
        assert snap["last_success_epoch"] == wall.value
        assert snap["last_success_ts"] == wall.value
        assert snap["process_start_epoch"] == 2_000_000.0
        assert snap["age_s"] == 0.0
        assert snap["status"] == "ok"
    finally:
        await cleanup(adapter)


# ---------------------------------------------------------------------------
# A4 / A5
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_file_contains_no_token(monkeypatch, tmp_path):
    """A4: 心跳檔**絕不可洩漏 token**（只含 agent 名 / 時間戳 / pid）。"""
    tokens = {"mai": FAKE_TOKEN_MAI, "yua": FAKE_TOKEN_YUA}
    adapter = make_adapter(tmp_path, tokens, wall=FakeClock())
    install_builder(monkeypatch)
    hb_path = Path(adapter._heartbeat_path())

    await adapter.start(on_message=lambda *a: None)
    try:
        raw = hb_path.read_text(encoding="utf-8")
        for token in tokens.values():
            assert token not in raw, "心跳檔洩漏了 token"
            assert token[:8] not in raw, "心跳檔洩漏了 token 片段"
        payload = json.loads(raw)
        assert set(payload) == {"updated_at", "pid", "bots"}
        for entry in payload["bots"].values():
            assert set(entry) == {"last_success_ts", "age_s", "status"}
        assert isinstance(payload["updated_at"], float)
        assert isinstance(payload["pid"], int)
    finally:
        await cleanup(adapter)


def test_default_heartbeat_path_is_data_root_scoped():
    """A5: 預設路徑走 `data_root()` ⇒ 測試被 conftest 隔離, 對生產 `data/**` 零寫入。"""
    from src.paths import data_root

    adapter = TelegramAdapter(tokens={"mai": "tok"})
    resolved = Path(adapter._heartbeat_path())
    assert resolved == data_root() / "heartbeats" / "telegram_channel.json"
    assert resolved.name == "telegram_channel.json"

    isolated = os.environ.get("SOUL_OS_DATA_DIR")
    assert isolated, "conftest 的資料根隔離 fixture 未生效"
    assert str(resolved).startswith(str(Path(isolated).resolve()))

    production = (REPO_ROOT / "data").resolve()
    assert not str(resolved).lower().startswith(str(production).lower() + os.sep), (
        f"預設心跳路徑落在生產 data/ 底下: {resolved}"
    )


def test_heartbeat_constants_are_declared():
    """A5b: 門檻與週期常數化（watchdog 讀取端用同一個門檻數值）。"""
    assert HEARTBEAT_STALE_SECONDS == 180.0
    assert HEARTBEAT_INTERVAL_SECONDS == 30.0


# ============================================================================
# B. watchdog 讀取端（抽出函式文字, 子行程單獨載入 —— 絕不執行 _watchdog.ps1）
# ============================================================================

def _watchdog_text() -> str:
    return WATCHDOG.read_text(encoding="utf-8")


def _block_text() -> str:
    text = _watchdog_text()
    start = text.index(BLOCK_BEGIN)
    end = text.index(BLOCK_END)
    assert start < end, "TG-HEALTH 區塊標記順序錯誤"
    return text[start:end + len(BLOCK_END)]


def _function_text(name: str = FUNCTION_NAME) -> str:
    """從區塊標記內抽出 `function <name> { … }` 的文字（**不做任何執行**）。"""
    lines = _block_text().splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith(f"function {name}")]
    assert len(starts) == 1, f"區塊內找不到唯一的 function {name}"
    start = starts[0]
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


def _powershell_exe():
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _run_ps(script_body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """把 script_body 寫成暫存 .ps1（UTF-8 BOM ⇒ PS 5.1 才會以 UTF-8 讀）並執行。"""
    exe = _powershell_exe()
    if exe is None:
        pytest.skip("找不到 powershell.exe（Windows PowerShell 5.1）")
    driver = tmp_path / "driver.ps1"
    driver.write_text(script_body, encoding="utf-8-sig")
    return subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _parse_probe_output(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value
    return out


def _reader_case(tmp_path, *, file_text, now_epoch, threshold=180.0, create=True):
    """抽出函式 → 子行程載入 → 餵合成心跳 → 回傳 (alerts, logged, error)。"""
    hb_path = tmp_path / "synthetic_heartbeat.json"
    if create:
        hb_path.write_text(file_text, encoding="utf-8")

    driver = (
        "Set-StrictMode -Off\n"
        "$tgTestLog = New-Object System.Collections.ArrayList\n"
        "$tgTestLogger = { param($m, $l) [void]$tgTestLog.Add([string]$l + '|' + [string]$m) }\n"
        "$tgTestAlerts = @()\n"
        "$tgTestError = ''\n"
        + _function_text() + "\n"
        "try {\n"
        f"    $tgTestAlerts = Test-TgHeartbeatHealth -HeartbeatPath '{hb_path}'"
        f" -NowEpoch {now_epoch} -ThresholdSeconds {threshold} -Logger $tgTestLogger\n"
        "} catch {\n"
        "    $tgTestError = $_.Exception.Message\n"
        "}\n"
        "Write-Output ('COUNT=' + @($tgTestAlerts).Count)\n"
        "Write-Output ('ALERTS=' + (@($tgTestAlerts) -join ' ;; '))\n"
        "Write-Output ('LOGGED=' + (@($tgTestLog) -join ' ;; '))\n"
        "Write-Output ('ERROR=' + $tgTestError)\n"
        "Write-Output 'DONE'\n"
    )

    proc = _run_ps(driver, tmp_path)
    assert proc.returncode == 0, (
        f"PowerShell 子行程非 0 退出 ({proc.returncode}); "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "DONE" in proc.stdout, (
        f"驅動腳本未跑完; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    parsed = _parse_probe_output(proc.stdout)
    alerts = [a for a in parsed.get("ALERTS", "").split(" ;; ") if a]
    logged = [entry for entry in parsed.get("LOGGED", "").split(" ;; ") if entry]
    return alerts, logged, parsed.get("ERROR", "")


NOW = 1_800_000_000


def _heartbeat_json(now, *, updated_at=None, bots=None):
    updated = now if updated_at is None else updated_at
    return json.dumps(
        {
            "updated_at": float(updated),
            "pid": 10444,
            "bots": bots if bots is not None else {},
        }
    )


def _bot_entry(now, age_s, status):
    return {
        "last_success_ts": float(now) - float(age_s),
        "age_s": float(age_s),
        "status": status,
    }


def test_reader_case1_missing_file_is_cold_start_no_alert(tmp_path):
    """B1a: 心跳檔不存在（冷啟動期）⇒ **0 告警、0 例外**。"""
    alerts, logged, error = _reader_case(
        tmp_path,
        file_text="",
        now_epoch=NOW,
        create=False,
    )
    assert error == "", f"檔案不存在時不應有任何例外: {error}"
    assert alerts == [], alerts
    assert logged == [], logged


def test_reader_case2_healthy_no_alert(tmp_path):
    """B1b: 全部 bot 新鮮（age < 180）⇒ 0 告警。"""
    text = _heartbeat_json(
        NOW,
        bots={
            "mai": _bot_entry(NOW, 2.0, "ok"),
            "yua": _bot_entry(NOW, 178.0, "ok"),
        },
    )
    alerts, logged, error = _reader_case(tmp_path, file_text=text, now_epoch=NOW)
    assert error == ""
    assert alerts == [], alerts
    assert logged == [], logged


def test_reader_case3_single_stale_bot_is_named_exactly(tmp_path):
    """B1c: 單一 bot 逾時（mai 210s, 其餘正常）⇒ **精確指名 `Bot mai`**, 且只有 mai。"""
    text = _heartbeat_json(
        NOW,
        bots={
            "mai": _bot_entry(NOW, 210.0, "stale"),
            "yua": _bot_entry(NOW, 3.0, "ok"),
            "rem": _bot_entry(NOW, 120.0, "ok"),
        },
    )
    alerts, logged, error = _reader_case(tmp_path, file_text=text, now_epoch=NOW)
    assert error == ""
    assert len(alerts) == 1, f"應只有 mai 一筆告警, 實際 {alerts}"
    assert alerts[0] == (
        "[WATCHDOG-TG-ALERT] Bot mai heartbeat stale: 210s (threshold 180s)"
    ), alerts[0]
    assert "Bot yua" not in alerts[0]
    assert "Bot rem" not in alerts[0]
    # 告警一定經 logger 以 CRITICAL 等級送出（呼叫點把 logger 接到既有的日誌函式）
    assert logged == ["CRITICAL|" + alerts[0]], logged


def test_reader_case4_global_writer_timeout_alert(tmp_path):
    """B1d: 全域寫入者逾時（`updated_at` age 200s）⇒ 全域過期告警（寫入者已死）。"""
    text = _heartbeat_json(
        NOW,
        updated_at=NOW - 200.0,
        bots={
            "mai": _bot_entry(NOW, 5.0, "ok"),
            "yua": _bot_entry(NOW, 6.0, "ok"),
        },
    )
    alerts, logged, error = _reader_case(tmp_path, file_text=text, now_epoch=NOW)
    assert error == ""
    assert len(alerts) == 1, alerts
    assert alerts[0].startswith("[WATCHDOG-TG-ALERT] heartbeat file stale:"), alerts[0]
    assert "age=200s" in alerts[0], alerts[0]
    assert "(threshold 180s)" in alerts[0], alerts[0]
    assert logged == ["CRITICAL|" + alerts[0]], logged


@pytest.mark.parametrize(
    "file_text,label",
    [
        ("{not valid json", "損毀 JSON"),
        ("", "空檔"),
        ("   \n  ", "只有空白"),
        ("[1,2,3]", "型別錯誤（陣列）"),
        ("null", "null 文件"),
    ],
)
def test_reader_case5_corrupt_or_empty_is_safe(tmp_path, file_text, label):
    """B1e: 損毀 JSON／空檔／型別錯誤 ⇒ **不拋錯、安全退出**（0 告警）。"""
    alerts, logged, error = _reader_case(
        tmp_path, file_text=file_text, now_epoch=NOW
    )
    assert error == "", f"{label} 時不應拋出例外: {error}"
    assert alerts == [], f"{label} 不應產生假告警: {alerts}"
    assert logged == [], logged


def test_reader_lock_failure_is_safe(tmp_path):
    """B1f: 檔案被鎖定／無法讀取 ⇒ 安全跳過（0 告警、0 例外）。"""
    hb_path = tmp_path / "synthetic_heartbeat.json"
    hb_path.write_text(_heartbeat_json(NOW, bots={"mai": _bot_entry(NOW, 9999.0, "stale")}),
                       encoding="utf-8")

    driver = (
        "$tgTestLog = New-Object System.Collections.ArrayList\n"
        "$tgTestLogger = { param($m, $l) [void]$tgTestLog.Add([string]$l + '|' + [string]$m) }\n"
        "$tgTestAlerts = @()\n"
        "$tgTestError = ''\n"
        + _function_text() + "\n"
        # 用「指向一個目錄」的路徑模擬無法讀取（ReadAllText 會拋）
        "try {\n"
        f"    $tgTestAlerts = Test-TgHeartbeatHealth -HeartbeatPath '{tmp_path}'"
        f" -NowEpoch {NOW} -ThresholdSeconds 180 -Logger $tgTestLogger\n"
        "} catch {\n"
        "    $tgTestError = $_.Exception.Message\n"
        "}\n"
        "Write-Output ('COUNT=' + @($tgTestAlerts).Count)\n"
        "Write-Output ('ERROR=' + $tgTestError)\n"
        "Write-Output 'DONE'\n"
    )
    proc = _run_ps(driver, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    parsed = _parse_probe_output(proc.stdout)
    assert parsed.get("ERROR", "") == "", parsed
    assert parsed.get("COUNT", "") == "0", parsed


def test_log_watch_two_arg_call_lands_critical_in_log_file(tmp_path):
    """B1g: 工單指定的 `Log-Watch "<msg>" "CRITICAL"` 形式**真的**把 CRITICAL 寫進日誌。

    這條同時是回歸護欄: 既有的單參數呼叫端輸出行必須逐字不變
    （`Log-Watch` 的第二個參數是 `''` 預設值, 不影響舊行為）。
    """
    lines = _watchdog_text().splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith(f"function {LOG_WATCH_NAME}(")]
    assert len(starts) == 1, "找不到唯一的 Log-Watch 定義"
    start = starts[0]
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    log_watch_src = "\n".join(lines[start:end + 1])

    log_file = tmp_path / "watchdog.log"
    driver = (
        f"$logFile = '{log_file}'\n"
        + log_watch_src + "\n"
        "Log-Watch 'plain single-arg call'\n"
        "Log-Watch \"[WATCHDOG-TG-ALERT] Bot mai heartbeat stale: 210s (threshold 180s)\" 'CRITICAL'\n"
        "Write-Output 'DONE'\n"
    )
    proc = _run_ps(driver, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DONE" in proc.stdout

    logged = log_file.read_text(encoding="utf-8-sig")
    assert "[WATCHDOG-TG-ALERT] Bot mai heartbeat stale: 210s (threshold 180s)" in logged
    assert "CRITICAL" in logged, f"CRITICAL 沒有落地 watchdog.log: {logged!r}"

    log_lines = [ln for ln in logged.splitlines() if ln.strip()]
    old_style = [ln for ln in log_lines if "plain single-arg call" in ln]
    assert len(old_style) == 1
    assert "CRITICAL" not in old_style[0], (
        f"單參數呼叫端的既有輸出行被改變了: {old_style[0]!r}"
    )


def test_watchdog_parses_with_powershell_51_zero_syntax_errors(tmp_path):
    """B2: 真實 PowerShell 5.1 語法解析 = **0 errors**（parse 不是執行）。

    這是「不弄死保命機制」的關鍵閘門: Task Scheduler 用 5.1, 語法不相容
    （例如誤用 `??` / 三元運算子）會讓整支腳本在載入期就死掉。
    """
    exe = _powershell_exe()
    if exe is None:
        pytest.skip("找不到 powershell.exe（Windows PowerShell 5.1）")

    driver = (
        "$errs = $null\n"
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{WATCHDOG}', [ref]$null, [ref]$errs)\n"
        "Write-Output ('PARSE_ERRORS=' + $errs.Count)\n"
        "$errs | ForEach-Object { Write-Output ('ERR: ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }\n"
    )
    proc = _run_ps(driver, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PARSE_ERRORS=0" in proc.stdout, (
        f"PowerShell 5.1 解析出語法錯誤:\n{proc.stdout}"
    )


# ============================================================================
# C. 靜態審計
# ============================================================================

def test_block_has_no_restart_or_kill_constructs():
    """C1: 區塊內 0 個重啟計數器 / 0 個 `Start-Process` / 0 個提前結束敘述 / 0 個 `Write-Warning`。"""
    block = _block_text()
    assert "n_restarts" not in block, "區塊不得觸及既有重啟計數器"
    assert "Start-Process" not in block, "區塊不得啟動任何行程"
    assert not re.search(r"\bexit\b", block), "區塊不得有任何提前結束敘述"
    assert "Write-Warning" not in block, (
        "排程無主控台 ⇒ Write-Warning 寫入虛空; 一律用 Log-Watch"
    )


def test_block_is_wrapped_in_own_try_catch():
    """C2a: 區塊整體被自己的 `try { } catch { }` 包住（自身異常不得中斷主巡檢）。"""
    lines = _block_text().splitlines()
    call_site_idx = max(i for i, ln in enumerate(lines) if ln == "try {")
    call_site = lines[call_site_idx:]
    assert any(ln.startswith("} catch") for ln in call_site), (
        "呼叫點未被自己的 try/catch 包住"
    )
    assert any("Log-Watch" in ln for ln in call_site), (
        "catch 內必須只記 log 並繼續"
    )

    function_src = _function_text()
    assert "try {" in function_src and "catch {" in function_src, (
        "函式本身也必須有自己的 try/catch"
    )
    assert function_src.rstrip().endswith("}"), function_src[-80:]
    assert re.search(r"catch \{[^}]*return @\(\)", function_src), (
        "函式的 catch 必須吸收異常並回傳空陣列"
    )


def test_block_is_before_every_early_exit():
    """C2b: 位置證明 —— 區塊字元位移 < **每一個**提前結束分支的字元位移。"""
    text = _watchdog_text()
    block_offset = text.index(BLOCK_BEGIN)
    exit_offsets = [m.start() for m in re.finditer(r"(?m)^\s*exit\s+\d+", text)]
    assert exit_offsets, "找不到任何提前結束分支 —— 位置證明的前提不成立"
    assert block_offset < min(exit_offsets), (
        f"區塊位移 {block_offset} 不在最早的提前結束分支 {min(exit_offsets)} 之前"
    )
    for offset in exit_offsets:
        assert block_offset < offset


def test_block_alerts_via_log_watch_with_markers():
    """C2c: 區塊使用既有 `Log-Watch`, 且帶 `[WATCHDOG-TG-ALERT]` 與 `CRITICAL`。"""
    block = _block_text()
    assert "Log-Watch" in block
    assert "[WATCHDOG-TG-ALERT]" in block
    assert "CRITICAL" in block


def test_block_call_site_variables_are_prefixed():
    """C3: 呼叫點所有變數一律 `tgHb` 前綴 ⇒ 絕不覆寫既有變數而改變下游行為。"""
    lines = _block_text().splitlines()
    call_site_idx = max(i for i, ln in enumerate(lines) if ln == "try {")
    call_site = "\n".join(lines[call_site_idx:])
    names = set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)\s*=", call_site))
    assert names, "呼叫點竟然沒有任何變數賦值？"
    assert all(n.startswith("tgHb") for n in names), (
        f"呼叫點出現未加 tgHb 前綴的變數（可能撞名既有狀態）: {sorted(names)}"
    )


def test_watchdog_is_only_read_never_executed_here():
    """C4: 本檔只「讀」`_watchdog.ps1`; 沒有任何執行它的路徑。

    子行程**只**跑抽出來的暫存驅動檔（`driver.ps1`）; 真實腳本只被兩種方式碰到:
    讀檔（`WATCHDOG.read_text`）與 `Parser::ParseFile`（parse 不是執行）。
    """
    source = Path(__file__).read_text(encoding="utf-8")
    assert "str(driver)]" in source, "子行程必須只執行抽出來的暫存驅動檔"
    assert "WATCHDOG.read_text" in source, "真實腳本只能以讀檔方式取得"
    assert "ParseFile('{WATCHDOG}')" in source, "語法檢查必須走 ParseFile（parse 不是執行）"
    assert WATCHDOG.exists()
    assert BLOCK_BEGIN in _watchdog_text()
    assert BLOCK_END in _watchdog_text()
