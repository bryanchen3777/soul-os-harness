"""INFRA-CONCURRENT-APPEND-MUTEX — Windows append-only 承載檔的併寫守衛（2026-10-22）

🔴 為什麼有這張票
------------------
Owner 裁決（2026-10-22）：**開立 P1 票 INFRA-CONCURRENT-APPEND-MUTEX**，拒絕長期容忍
flaky 守衛（放寬守衛＝讓缺陷永久隱形；貼「環境問題」標籤＝駝鳥）。

🔴 根因（實測定案，非推論）
---------------------------
Windows 上 CPython 的 ``open(path, "ab")`` **不是原子追加**：它走 MSVCRT 的 ``_O_APPEND``，
CRT 在每次寫入前用「**該 handle 自己快取的檔案大小**」seek 到 EOF。兩個先後開啟的 handle
各自快取到**同一個（過期）大小** ⇒ 寫到同一偏移 ⇒ **後者整筆覆蓋前者**。

樣態：可完整解析、**零損壞行**、``write()`` 全部回傳成功 ⇒ 看起來像「整筆無痕跡消失」。

量測（6 個獨立行程產生真實 CPU 負載、30 輪、每輪 2 執行緒 × 30 筆、同一段 append 程式碼）：

===========================  ==========================================
寫法                          結果
===========================  ==========================================
現況 ``open(path,"ab")``        30 輪中 **10 輪遺失**（33.3%）
行程內互斥 ＋ Win32 核心層追加   **30/30 全對**
===========================  ==========================================

⚠️ 別用「自己算寫入位置」修 append 缺陷（``stat``／``SEEK_END``／讀檔算長度）——
那正是把過期 metadata 當真相；實測 8/8 輪檔案大小多出 60 bytes。

🔴 本檔的三道守衛
-----------------
A. **結構守衛**：生產路徑（張力承載）必須真的走原子追加原語；Windows 上必須走核心層
   （``FILE_APPEND_DATA``），不得退回 MSVCRT 的 ``_O_APPEND``。
B. **行為守衛**：N 輪併寫（2 執行緒 × 30 筆）每一輪都必須：60 行、60 個可解析且
   tension_id 唯一的紀錄、**檔案位元組數 == 各紀錄位元組數之和**（位元級 ⇒ 覆蓋／截斷
   都會被抓到）。
C. **位元組契約守衛**：承載檔必須是 LF-only（不得出現 CR）——切換到 Win32 寫入後仍成立。

🔴 雙向變異實驗（由主大腦手動執行，結果見 logs/ENGINEERING_STATE.md §0.1.36）
---------------------------------------------------------------------------
設定環境變數 ``SOULOS_APPEND_MUTATION=legacy_crt_append`` 後重跑本檔 ⇒
**守衛 B 必須轉紅**（證明守衛不是盲腸）。該變異只影響 tmp 目錄，**不可能**觸達生產。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.fsio import atomic_append
from src.inner_life import epistemic_action as ea

MUTATION_ENV = atomic_append.MUTATION_ENV
ROUNDS = int(os.environ.get("SOULOS_APPEND_GUARD_ROUNDS", "12"))
WORKERS = 2
PER_WORKER = 30
TOTAL = WORKERS * PER_WORKER


def _tension(agent_id: str, tension_id: str) -> "ea.Tension":
    """一條最小張力（原料齊全 ⇒ 可 to_dict/from_dict 往返）。"""
    return ea.Tension(
        tension_id=tension_id,
        kind=ea.TENSION_KIND_RELATIONAL,
        agent_id=agent_id,
        subject="沈默",
        summary="兩天沒講話了",
        tags=("tag_a",),
        world_keys=(),
        source=ea.SOURCE_TEMPORAL_PHENOMENOLOGY,
        created_at=1000.0,
        utterance="",
    )


# ── 守衛 A：結構（生產路徑真的走原子追加原語）───────────────────────────────
def test_guard_a_production_path_routes_through_atomic_append(tmp_path, monkeypatch):
    """張力承載的寫入必須經過 ``append_bytes``（原子追加原語），不得自己 open。"""
    store = tmp_path / "tensions"
    calls: list[tuple[Path, bytes]] = []

    def _recorder(path, blob):
        calls.append((Path(path), bytes(blob)))

    monkeypatch.setattr(ea, "append_bytes", _recorder)
    assert ea.upsert_open_tension(_tension("agent_x", "t-1"), store) is True
    assert len(calls) == 1, "張力承載寫入必須走 append_bytes 原語"
    target, blob = calls[0]
    assert target.name.startswith("epistemic_tensions"), "寫入目標必須是張力承載檔：%s" % target
    assert blob.endswith(b"\n"), "紀錄必須以 LF 結尾"
    assert json.loads(blob.decode("utf-8").strip())["tension_id"] == "t-1"


def test_guard_a_windows_must_use_kernel_atomic_append():
    """Windows 上必須走核心層原子追加（``FILE_APPEND_DATA``），不得退回 CRT 的 ``_O_APPEND``。

    🔴 這是**確定性**的迴歸偵測器（不靠 flaky 重現）：一旦有人把原語改回
    ``open(path,"ab")``（或把變異旗標打開），本測試**必定**轉紅。
    行為守衛（守衛 B）在**無負載**下不一定能抓到遺失（實測遺失率隨負載起伏），
    ⇒ 不可單獨承擔「守衛有效」的證明責任。
    """
    if os.name != "nt":
        pytest.skip("非 Windows 平台（POSIX 的 O_APPEND 本身即原子）")
    assert atomic_append.uses_kernel_atomic_append() is True, (
        "Windows 上必須使用核心層原子追加（FILE_APPEND_DATA）；"
        "退回 MSVCRT _O_APPEND 會讓併發寫入互相覆蓋（實測 33.3% 遺失率）"
    )


# ── 守衛 B：行為（併寫不遺失，位元級）─────────────────────────────────────
@pytest.mark.parametrize("round_index", range(ROUNDS))
def test_guard_b_concurrent_appends_keep_every_record(tmp_path, round_index):
    """併寫：2 執行緒 × 30 筆，每一筆都必須在檔內（位元級驗證）。"""
    store = tmp_path / ("tensions_r%02d" % round_index)
    barrier = threading.Barrier(WORKERS)
    errors: list[BaseException] = []

    def _worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=10)
            for index in range(PER_WORKER):
                ea.upsert_open_tension(
                    _tension(agent_id="agent_%d" % worker_id, tension_id="w%d-%d" % (worker_id, index)),
                    store,
                )
        except BaseException as exc:  # noqa: BLE001 — 記下來給斷言看
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"併寫執行緒不可失敗：{errors!r}"
    assert not any(thread.is_alive() for thread in threads), "併寫執行緒必須收束"

    path = ea.tension_store_path(store)
    raw = path.read_bytes()
    lines = [line for line in raw.split(b"\n") if line.strip()]
    assert len(lines) == TOTAL, (
        f"併寫：{TOTAL} 筆（{WORKERS} 執行者 × {PER_WORKER} 筆）必須全部保留，實得 {len(lines)} 行"
    )
    ids = {json.loads(line.decode("utf-8"))["tension_id"] for line in lines}
    assert len(ids) == TOTAL, "併寫後每一筆的 tension_id 都必須可見且唯一"
    # 位元級：檔案大小必須等於各紀錄位元組數之和（覆蓋／截斷都會被抓到）
    assert len(raw) == sum(len(line) + 1 for line in lines), (
        "承載檔大小必須等於各紀錄（含 LF）位元組數之和 ⇒ 不得有覆蓋或截斷"
    )
    # LF-only 位元組契約（不得出現 CR）
    assert b"\r" not in raw, "承載檔必須是 LF-only（不得出現 CR）"


# ── 守衛 C：世界日誌同樣走原子追加，且維持 LF-only 位元組契約 ────────────────
def test_guard_c_world_log_uses_atomic_append_and_lf_only(tmp_path, monkeypatch):
    """世界日誌的 binary append 也要走原子追加原語，且輸出維持 LF-only。"""
    from src.world import world_log as wl

    calls: list[tuple[Path, bytes]] = []
    real = atomic_append.append_bytes

    def _recorder(path, blob):
        calls.append((Path(path), bytes(blob)))
        return real(path, blob)

    monkeypatch.setattr(wl, "append_bytes", _recorder)
    writer = wl.WorldLogWriter(log_dir=tmp_path / "worldlog")
    event = SimpleNamespace(
        source="weather",
        novelty_id="n-1",
        type="weather",
        ts="2026-10-22T00:00:00+00:00",
        summary="開始下雨了",
        priority=0.5,
        data={"text": "line1\nline2"},
    )
    assert writer.write(event) is True
    assert calls, "世界日誌寫入必須走原子追加原語"
    blob = calls[-1][1]
    assert blob.endswith(wl.RECORD_TERMINATOR.encode("utf-8")), "世界日誌行必須以單一 LF 結尾"
    assert b"\r" not in blob, "世界日誌必須是 LF-only（跨平台逐位元相同）"
