#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/eval_lifecycle_cli.py — 生命週期評估的**離線 CLI 驅動器**（唯讀來源 + 自建沙盒）。

**為什麼存在**（Owner 2026-10-19 裁示「方案一」）：
`evaluate_lifecycle_all()` 全量跑完 398 節點 / 903 邊只要 0.066 秒，但接線在
scheduler 的 night slot（22:00）上 ⇒ 功能驗收被迫等 5～6 小時的牆鐘。
本腳本把「功能邏輯驗收」從牆鐘排程上解耦：十秒內跑完並印出對帳報告。

**保真度保證**：`evaluate_lifecycle_all()` 是純 disk→disk 函式（自節點檔＋邊檔重建
引擎，不依賴伺服器行程的任何記憶體狀態），因此「在生產圖譜的**副本**上跑」與
「22:00 在生產目錄上跑」的**計算結果逐位元相同**，唯一差別是 sidecar 的寫入目標。
⇒ 離線驗收不是模擬，是等價重現。

🔴🔴🔴 **安全契約（2026-10-19 事故後重寫）— 本腳本沒有任何「就地寫入」路徑**：
本腳本**永遠只寫入自己 `tempfile.mkdtemp()` 建立的沙盒**。來源目錄（預設＝生產
`data/elevation/`）只被**讀取與複製**，永遠不被寫入。

**為什麼是「移除能力」而不是「加更嚴的判斷」**：
前一版曾提供 `--data-dir … --in-place`（就地寫入），其「拒絕寫生產」的判斷是
`_same_dir(target, production_dir)`。該判斷**被實測證偽**：生產目錄的解析值取自
`src.paths.data_root()`，而它是**行程期快取**的單例；`tests/conftest.py` 的
autouse fixture 會把 `SOUL_OS_DATA_DIR` 指向每個測試自己的 tmp 目錄，於是
「測試行程快取到的生產目錄」與「CLI 子行程即時重讀到的生產目錄」**不是同一個**，
比較失效 ⇒ CLI 對生產執行了就地寫入，寫進 167 筆 lifecycle_transition。
⇒ 教訓：**不要用「可能漂移的兩個值」去擋寫入**。**把寫入能力本身拿掉**，才是
結構性保證。本檔因此**不提供任何就地寫入旗標**。

**其他硬性保證**：
- 不啟動任何伺服器、不綁任何埠、不發任何網路請求（`llm=None`）。
- 不修改 `src/**`、`data/**`；只新增 `scripts/eval_lifecycle_cli.py`。
- 沙盒在結束時刪除；報告寫到 `%TEMP%`。

用法：
    .venv\\Scripts\\python.exe scripts\\eval_lifecycle_cli.py
    .venv\\Scripts\\python.exe scripts\\eval_lifecycle_cli.py --simulated-time 2026-10-20T02:00:00+00:00
    .venv\\Scripts\\python.exe scripts\\eval_lifecycle_cli.py --source-dir <graph dir>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.inner_life.elevation_adapter import evaluate_lifecycle_all  # noqa: E402
from src.inner_life.emergent_projection import (  # noqa: E402
    WEAKENING_TONE_PREFIX,
    format_emergent_block,
)
from src.inner_life.lifecycle_overlay import LIFECYCLE_EVENTS_FILENAME  # noqa: E402

NODES_FILENAME = "elevation_nodes.jsonl"
EDGES_FILENAME = "elevation_edges.jsonl"
COPY_FILES = (NODES_FILENAME, EDGES_FILENAME, LIFECYCLE_EVENTS_FILENAME)

SOUL_TYPES = ("belief", "value", "trait", "essence")
DEFAULT_KEY_NODE = "6a92c906b245"

# ── 預期退役集合（Owner 2026-10-23 裁定：選項乙「改判準」）────────────────
# 語意：2026-10-11 建立的 Legacy Value 節點，因從未再被日常 Pattern 背書
# （last_support_ts == created_ts、祖先 support_propagation 邊＝0），
# 隨生命週期自然代謝而轉入 weakening —— 這是**預期代謝**，不是破線。
# 🔴 下列基線是 Owner 核准的「指定集合」；規則推導結果**不得超出**它。
EXPECTED_RETIREMENT_DATE = "2026-10-11"
EXPECTED_RETIREMENT_BASELINE = frozenset({
    "0b398afedf30486baa75047028b3319d",  # agent_akane
    "24bad790f8954dc2ae4711c4c1c6ffac",  # agent_aoi
    "56391bd5ce154d9f8ee09ef40280ec71",  # agent_miku
    "60a2d5e453914ac3bbbbc02cb549b076",  # agent_yua
    "6a92c906b245458cb0c4c64ea214f2eb",  # agent_mahiru  ★關鍵反例
    "7fcf5557b1994fd1977973f7dcdf5a38",  # agent_ram
    "8dd335036f21453b85c72d17eaf5b303",  # agent_rem
    "9f9b13498c334268aaf2e95429bd2228",  # agent_ruka
    "a8b48b0210e74363b506f5750d61d125",  # agent_mai
    "d0df567934274a9697feff6d1b0d9af5",  # agent_anna
})


# ── 路徑 ──────────────────────────────────────────────────────────────────
def runtime_store_dir() -> Path:
    """**執行期**的資料根（可能受 `SOUL_OS_DATA_DIR` 影響）。僅作預設來源。"""
    try:
        from src.paths import data_root

        return Path(data_root()) / "elevation"
    except Exception:
        return REPO_ROOT / "data" / "elevation"


def repo_store_dir() -> Path:
    """repo 內的實體生產目錄（與 `SOUL_OS_DATA_DIR` 無關）。**永不寫入。**"""
    return REPO_ROOT / "data" / "elevation"


# ── I/O ───────────────────────────────────────────────────────────────────
def read_jsonl(path: Path) -> list:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def copy_graph(src: Path, dst: Path) -> None:
    """把來源圖譜的必要檔案複製到沙盒（**不**複製 40MB 的 trace 檔）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        s = src / name
        if s.exists():
            shutil.copy2(s, dst / name)


def agent_ids(nodes: list) -> list:
    ids = {n.get("agent_id") for n in nodes if isinstance(n, dict)}
    ids.discard("default")
    ids.discard(None)
    return sorted(ids)


def prompt_snapshot(store_dir: Path, agents: list) -> dict:
    """用**真實**的 prompt 組裝函式離線算出各 agent 的投影正文。

    ``record_trace=False`` ⇒ 不寫 projection trace sidecar（唯讀）。
    """
    snap = {}
    for a in agents:
        try:
            block = format_emergent_block(a, store_dir=store_dir, record_trace=False)
        except Exception as exc:
            snap[a] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
            continue
        snap[a] = {
            "ok": True,
            "block": block,
            "len": len(block),
            "weak_prefix_count": block.count(WEAKENING_TONE_PREFIX),
        }
    return snap


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


# ── 評估時鐘：必須 tz-aware ────────────────────────────────────────────────
# 🔴 2026-10-22 實測事故（假綠來源）：`--simulated-time` 若給 **naive** 時間戳
# （`2026-10-23T02:00:00`，無時區），`soul_elevation.engine._days_between` 會拿
# offset-aware 的 anchor 去減 offset-naive 的 now ⇒ `TypeError: can't subtract
# offset-naive and offset-aware datetimes`。該例外被 `evaluate_lifecycle_all` 的
# fail-closed `except` 吞掉並回傳 `[]` ⇒ CLI 看到「0 筆新轉變但圖非空」⇒ 判成
# **冪等 no-op ⇒ VERDICT: PASS**。⇒ **假綠**（真值是 14 筆新轉變）。
#
# ⇒ 修法不是「多印一個警告」，是**移除能力**：naive 時間戳在**評估開始前**就被
# 硬拒（exit 2），CLI 結構上不可能再進入「0 筆 ⇒ PASS」這條路徑。
def _parse_simulated_time(raw: str):
    """解析 `--simulated-time`。

    回傳 ``(iso_string, None)`` 或 ``(None, error_message)``。

    **必須是 tz-aware**：naive 時間戳會讓生命週期評估在引擎內拋 TypeError，
    而 fail-closed 會把它偽裝成「0 筆轉變 = 冪等 no-op = PASS」。
    """
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, "評估時鐘不得為空字串"
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        return None, "評估時鐘不是合法的 ISO 8601：%r（%s）" % (raw, exc)
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return None, (
            "評估時鐘必須帶時區（tz-aware）：%r 沒有時區資訊。"
            "引擎的衰減算術需要 offset-aware 錨點；naive 會讓評估拋 TypeError，"
            "並被 fail-closed 偽裝成『0 筆轉變 = PASS』的假綠。"
            "請改用 UTC 形式，例如 2026-10-23T02:00:00+00:00。" % raw
        )
    return parsed.isoformat(), None


def _matches_key_node(node_id, key_node) -> bool:
    """節點 id 是否命中關鍵反例。

    🔴 2026-10-22 實測缺陷：`DEFAULT_KEY_NODE = "6a92c906b245"` 是 **12 字元前綴**，
    而真實事件的 ``node_id`` 是 **32 字元完整 id**（`6a92c906b245458cb0c4c64ea214f2eb`）。
    舊碼用 `e.get("node_id") == key_node` 精確比對 ⇒ 在真實資料上**恆為 False**，
    守衛 ③「關鍵反例不得被轉」**完全空轉**（單元測試餵的是假 id `"KEY"`，故測試綠）。
    ⇒ 比對改為**前綴語意**：任一方向為另一方的前綴即命中。
    """
    if not node_id or not key_node:
        return False
    a, b = str(node_id), str(key_node)
    return a == b or a.startswith(b) or b.startswith(a)


class _WarningCollector(logging.Handler):
    """收集評估期間的 WARNING 記錄（用來區分「真的 no-op」與「評估拋例外」）。"""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def expected_retirement_set(nodes: list, edges: list) -> set:
    """推導「預期退役集合」。

    四條件全滿足、缺一不可：
      1. node_type == "value"
      2. created_ts 的日期部分 == EXPECTED_RETIREMENT_DATE
      3. last_support_ts == created_ts（建立後從未再被背書）
      4. 沒有任何 trigger_type == "support_propagation" 的邊指向它（祖先支持邊＝0）

    🔴 **不得**引入任何時間門檻算術（不得出現 timedelta／天數比較）——
    衰減門檻是凍結的，本判準只認「建立日 + 從未背書」這個**結構性事實**。
    """
    supported = {e.get("node_id") for e in edges
                 if isinstance(e, dict) and e.get("trigger_type") == "support_propagation"}
    out = set()
    for n in nodes:
        if not isinstance(n, dict) or n.get("node_type") != "value":
            continue
        created = n.get("created_ts") or ""
        if not isinstance(created, str) or created[:10] != EXPECTED_RETIREMENT_DATE:
            continue
        if (n.get("last_support_ts") or "") != created:
            continue
        nid = n.get("node_id")
        if not nid or nid in supported:
            continue
        out.add(nid)
    return out


def baseline_violation(expected: set) -> list:
    """推導集合若超出 Owner 核准的基線 ⇒ 規則被放寬 ⇒ **執行期自我否決**。

    這是「規則不得被悄悄放寬」的**執行期**保險（不只靠 CI）。
    """
    extra = sorted(x for x in expected if x not in EXPECTED_RETIREMENT_BASELINE)
    if extra:
        return ["⓪ 預期退役集合超出核准基線（規則被放寬）：%s" % extra]
    return []


# 🔴 弱化語氣裝飾的還原：`X` ⇒ `我隐约觉得「X」`（投影層渲染，見
#    `emergent_projection.project_emergent`）。去掉裝飾後即可比較「正文本身」
#    有沒有被改動。
_WEAKENING_DECORATION_RE = re.compile(re.escape(WEAKENING_TONE_PREFIX) + r"「(.*?)」")


def strip_weakening_decoration(text: str) -> str:
    """把弱化語氣裝飾還原（`我隐约觉得「X」` ⇒ `X`）。

    用途：判斷「投影正文的差異是否**只**來自弱化裝飾」。
    🔴 這是**比較用**的正規化，不是判定規則本身。
    """
    if not text:
        return ""
    return _WEAKENING_DECORATION_RE.sub(r"\1", text)


def prompt_diff_is_expected(diff: dict, expected_count: int,
                            before_block: str, after_block: str) -> bool:
    """某個 agent 的投影正文差異是否**完全由預期代謝解釋**。

    🔴 為什麼需要這條（2026-10-23 實測）：弱化節點的投影會把 `X` 渲染成
    `我隐约觉得「X」`（+7 字元）⇒ **只要**有 value 轉入 weakening，該 agent 的
    投影正文就**必然**逐位元不同 ⇒ ④ 的比較必然報差異。10/24 夜槽實測：
    9 筆白名單 value 轉入 weakening ⇒ 9 個 agent 的正文各多一個弱化前綴。
    ⇒ 那不是破線，是預期代謝的正常投影後果。

    放行條件（缺一不可，缺一 ⇒ 仍然是破線）：
      1. 該 agent 確實有預期代謝命中（`expected_count > 0`）
      2. 弱化前綴的增量 == 該 agent 的預期代謝命中數
      3. 去掉弱化語氣裝飾後，before/after 正文**逐位元相同**
    ⇒ 多出／少了前綴、正文被改動、命中數對不上 ⇒ **一律仍然報破線**。
    """
    if expected_count <= 0:
        return False
    if (diff.get("after_weak", 0) - diff.get("before_weak", 0)) != expected_count:
        return False
    return strip_weakening_decoration(before_block) == strip_weakening_decoration(after_block)


def assess(
    *,
    new_events: list,
    prompt_diffs: list,
    key_node: str,
    weak_before: int,
    weak_after: int,
    graph_nodes: int = 0,
    soul_types: tuple = SOUL_TYPES,
    eval_error: str = "",
    undelivered: int = 0,
    expected_retirement: frozenset = frozenset(),
) -> list:
    """**純函式**：依四項準則回傳失敗清單（空清單 = 全過）。

    刻意抽成純函式：守衛測試能直接餵入構造出來的惡意事件，證明這套判定
    **不是空轉報綠**（例：塞一筆 value 類型的轉變進去，它必須報 FAIL）。

    ⚠️ 「0 筆新轉變」有兩種截然不同的成因，**不可混為一談**：
      - **圖是空的**（0 節點）⇒ 評估根本沒東西可算 ⇒ **FAIL**。
      - **圖非空但無新轉變** ⇒ 該轉的都已经轉過了（**冪等 no-op**）⇒ **不是失敗**。
        （本機 2026-10-19 實例：那 167 顆 pattern 已在 sidecar 裡是 weakening，
        重跑不會再降一級 ⇒ 今晚 22:00 的預期結果就是 0 筆。）
    ⇒ `graph_nodes` 必須傳入，才能分辨這兩者。

    ⚠️ **第三種成因（2026-10-22 實測新增）**：**評估本身拋了例外**。`evaluate_lifecycle_all`
    是 fail-closed 的（`except Exception` → warning → 回 `[]`），於是「評估失敗」與
    「冪等 no-op」在回傳值上**長得一模一樣**（都是 0 筆）。⇒ 呼叫端必須把例外
    **顯式帶進來**（`eval_error`），否則就會出現「CLI 報 PASS，實際上根本没評估」
    的假綠。

    🔴 **預期代謝（Owner 2026-10-23 裁定「改判準」）**：
    `expected_retirement` 內的 value 節點轉入 `weakening` ⇒ 分類為
    **EXPECTED_METABOLISM**（不是破線、不計入關鍵反例）；其餘任何靈魂類
    （belief/value/trait/essence，含非白名單的 value）或未知類型轉變 ⇒ **破線**。
    弱化包裝的**允許增量 ＝ EXPECTED_METABOLISM 的筆數**（等於則不報）。
    目標狀態 `dormant` / `superseded` **一律是破線**（退役＝弱化，不是休眠/取代）。
    """
    fails = []
    if eval_error:
        fails.append("⓪ 生命週期評估拋例外（fail-closed 回 []，不可判為 no-op PASS）：%s"
                     % eval_error)
    if undelivered:
        fails.append("⓪ 轉變未落地（評估回傳 %d 筆，但 sidecar 只增加 %d 筆）"
                     % (undelivered, 0))
    if not new_events and graph_nodes <= 0:
        fails.append("① 沒有任何轉變產生，且圖為空（評估無圖可算）")

    # ── 分類每一筆新轉變（Owner 2026-10-23「改判準」）────────────────────
    #   pattern          ⇒ 正常演化，永遠不是破線
    #   value→weakening
    #     且在預期退役集合內 ⇒ EXPECTED_METABOLISM（預期代謝，不是破線）
    #   其餘靈魂類／未知類型 ⇒ UNEXPECTED ⇒ 破線
    expected_ids = set(expected_retirement or ())
    expected_count = 0
    soul_hits = {}
    non_pattern = {}
    bad_target = {}
    key_hits = []
    for e in new_events:
        ntype = e.get("node_type")
        state = e.get("lifecycle_state")
        nid = e.get("node_id")
        is_expected = (ntype == "value" and state == "weakening" and nid in expected_ids)
        if is_expected:
            # EXPECTED_METABOLISM：預期代謝 ⇒ 不是破線，也不計入關鍵反例。
            expected_count += 1
        else:
            # 🔴 目標狀態一律檢查：退役＝弱化，不是休眠/取代（**即使在預期退役集合內**）。
            if state in ("dormant", "superseded"):
                bad_target[state] = bad_target.get(state, 0) + 1
            if ntype != "pattern":
                if ntype in soul_types:
                    soul_hits[ntype] = soul_hits.get(ntype, 0) + 1
                non_pattern[ntype] = non_pattern.get(ntype, 0) + 1
        # 🔴 關鍵反例：只有**不屬於**預期代謝的轉變才報破線。
        if _matches_key_node(nid, key_node) and not is_expected:
            key_hits.append(e)

    if soul_hits:
        fails.append("② 靈魂結構誤傷：%s" % soul_hits)
    if non_pattern:
        fails.append("② 出現非 pattern 轉變：%s" % non_pattern)
    for bad in ("dormant", "superseded"):
        if bad_target.get(bad):
            fails.append("② 轉變目標含 %s" % bad)
    if key_hits:
        fails.append("③ 關鍵反例 %s 被轉變 %d 次" % (key_node, len(key_hits)))
    # 🔴 只報「**無法由預期代謝解釋**」的投影差異（判定見
    #    `prompt_diff_is_expected`）：預期代謝造成的弱化裝飾是正常投影後果，
    #    不是破線；任何其餘的正文改變仍然是破線。
    unexplained_diffs = [d for d in prompt_diffs
                         if not d.get("explained_by_expected_metabolism")]
    if unexplained_diffs:
        fails.append("④ Prompt 投影正文改變：%s" % unexplained_diffs)
    # 🔴 弱化包裝的允許增量 ＝ EXPECTED_METABOLISM 的筆數（等於則不報）。
    if weak_after - weak_before != expected_count:
        fails.append("④ 弱化包裝計數 %d → %d" % (weak_before, weak_after))
    return fails


# ── 主流程 ────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="eval_lifecycle_cli",
        description="生命週期評估離線驅動器（唯讀來源 + 自建沙盒；永不寫入來源）",
    )
    ap.add_argument("--source-dir", default=None,
                    help="圖譜來源目錄（**唯讀**）。預設 = 執行期資料根的 elevation/")
    ap.add_argument("--simulated-time", default=None,
                    help="評估時鐘（ISO 8601）。預設 = 真實 now。"
                         "要預測某個 night slot 的結果時，傳入該 slot 的 UTC 時刻")
    ap.add_argument("--key-node", default=DEFAULT_KEY_NODE,
                    help="關鍵反例節點 id（必須維持 active，不得轉 weakening）")
    ap.add_argument("--report", default=None,
                    help="完整 UTF-8 報告輸出路徑（預設寫到 %%TEMP%%）")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出到 stdout")
    args = ap.parse_args(argv)

    source = Path(args.source_dir) if args.source_dir else runtime_store_dir()
    if not source.exists():
        print("[refuse] 來源目錄不存在：%s" % source, file=sys.stderr)
        return 2

    # 🔴 評估時鐘必須 tz-aware —— **在評估開始前**就硬拒，不讓它進入
    # 「0 筆轉變 ⇒ 冪等 no-op ⇒ PASS」這條假綠路徑。
    simulated_time, clock_error = _parse_simulated_time(args.simulated_time)
    if clock_error:
        print("[refuse] 評估時鐘無效：%s" % clock_error, file=sys.stderr)
        print("[refuse] 判定中止（不評估、不產報告）——寧可沒有結果，也不要假綠。",
              file=sys.stderr)
        return 2

    sandbox = Path(tempfile.mkdtemp(prefix="lifecycle_cli_sandbox_"))
    try:
        # 🔴 結構性保證：寫入目標只有 sandbox，且必須位於 temp 之下。
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root not in sandbox.resolve().parents:
            print("[refuse] 沙盒不在 temp 之下（%s）—— 內部錯誤，中止。" % sandbox,
                  file=sys.stderr)
            return 3

        copy_graph(source, sandbox)
        sidecar = sandbox / LIFECYCLE_EVENTS_FILENAME
        nodes = read_jsonl(sandbox / NODES_FILENAME)
        edges = read_jsonl(sandbox / EDGES_FILENAME)
        # 🔴 預期退役集合（Owner 2026-10-23「改判準」）：四條件推導 ＋ 基線自我否決。
        #    `baseline_violation` 是**執行期**保險：規則若被放寬，CLI 會自己喊 FAIL。
        expected_retirement = expected_retirement_set(nodes, edges)
        baseline_fails = baseline_violation(expected_retirement)
        agents = agent_ids(nodes)

        before_events = read_jsonl(sidecar)
        before_prompt = prompt_snapshot(sandbox, agents)

        # 🔴 捕捉評估期間的 fail-closed 警告：`evaluate_lifecycle_all` 把例外吞掉
        # 回傳 `[]`，與「真的冪等 no-op」在回傳值上無法區分 ⇒ 必須從日誌把
        # 真相撈出來，否則 CLI 會對「根本没評估」報 PASS。
        adapter_logger = logging.getLogger("soul_os.inner_life.elevation_adapter")
        collector = _WarningCollector()
        adapter_logger.addHandler(collector)
        try:
            t0 = time.perf_counter()
            events = evaluate_lifecycle_all(store_dir=sandbox, now_ts=simulated_time)
            elapsed = time.perf_counter() - t0
        finally:
            adapter_logger.removeHandler(collector)

        eval_error = ""
        for rec in collector.records:
            try:
                msg = rec.getMessage()
            except Exception:
                msg = str(rec.msg)
            if "evaluate_lifecycle_all failed" in msg:
                tb = ""
                if rec.exc_info:
                    tb = "".join(traceback.format_exception(*rec.exc_info))
                eval_error = "%s | %s" % (msg, tb.strip())
                break

        after_events = read_jsonl(sidecar)
        after_prompt = prompt_snapshot(sandbox, agents)
        new_events = after_events[len(before_events):]

        # 🔴 本次「命中」的預期代謝（白名單內 value 轉入 weakening）。
        #    與 `expected_retirement`（集合）分開報：集合是「誰有資格」，
        #    命中是「這次真的轉了幾筆」—— 弱化包裝的允許增量看的是命中數。
        expected_hits = [e for e in new_events
                         if e.get("node_type") == "value"
                         and e.get("lifecycle_state") == "weakening"
                         and e.get("node_id") in expected_retirement]
        # 每個 agent 各自命中幾筆（用來判斷投影差異能否被預期代謝**完全解釋**）
        node_agent = {n.get("node_id"): n.get("agent_id")
                      for n in nodes if isinstance(n, dict)}
        expected_by_agent = {}
        for e in expected_hits:
            ag = node_agent.get(e.get("node_id"))
            expected_by_agent[ag] = expected_by_agent.get(ag, 0) + 1

        # 🔴 結構性檢查：評估**回傳**了轉變、但 sidecar 沒增加 ⇒ 轉變沒落地
        #    （`_append_jsonl` 寫入失敗時 adapter 只發 warning，不會回 0）。
        #    ⇒ 沒有這條，「0 筆新轉變」又會被誤判成冪等 no-op。
        undelivered = max(0, len(events) - len(new_events))

        by_type = {}
        to_state = {}
        for e in new_events:
            by_type[e.get("node_type")] = by_type.get(e.get("node_type"), 0) + 1
            to_state[e.get("lifecycle_state")] = to_state.get(e.get("lifecycle_state"), 0) + 1

        prompt_diffs = []
        for a in agents:
            b, aft = before_prompt.get(a, {}), after_prompt.get(a, {})
            if not (b.get("ok") and aft.get("ok")):
                prompt_diffs.append({"agent": a, "error": b.get("error") or aft.get("error")})
                continue
            if b.get("block") != aft.get("block"):
                diff = {
                    "agent": a,
                    "before_len": b.get("len"),
                    "after_len": aft.get("len"),
                    "before_weak": b.get("weak_prefix_count"),
                    "after_weak": aft.get("weak_prefix_count"),
                }
                # 🔴 這筆差異能否**完全由預期代謝解釋**（弱化裝飾是正常投影後果，
                #    不是破線；無法解釋者仍然是破線 —— 見 `prompt_diff_is_expected`）。
                diff["explained_by_expected_metabolism"] = prompt_diff_is_expected(
                    diff, expected_by_agent.get(a, 0),
                    b.get("block") or "", aft.get("block") or "")
                prompt_diffs.append(diff)

        weak_total_before = sum(v.get("weak_prefix_count", 0) for v in before_prompt.values())
        weak_total_after = sum(v.get("weak_prefix_count", 0) for v in after_prompt.values())

        # 評估後的圖譜狀態分布（overlay 套用後 ⇒ 反映 sidecar 的實際效果）
        state_dist = {}
        try:
            from src.inner_life.emergent_projection import load_elevation_nodes

            for n in load_elevation_nodes(sandbox):
                st = n.get("lifecycle_state") or "active"
                state_dist[st] = state_dist.get(st, 0) + 1
        except Exception:
            state_dist = {}
        graph_nodes = sum(state_dist.values())

        fails = list(baseline_fails) + list(assess(
            new_events=new_events,
            prompt_diffs=prompt_diffs,
            key_node=args.key_node,
            weak_before=weak_total_before,
            weak_after=weak_total_after,
            graph_nodes=graph_nodes,
            eval_error=eval_error,
            undelivered=undelivered,
            expected_retirement=expected_retirement,
        ))

        result = {
            "mode": "sandbox-only",
            "source_dir": str(source),
            "source_is_repo_production": _same(source, repo_store_dir()),
            "sandbox_dir": str(sandbox),
            "simulated_time": simulated_time,
            "elapsed_seconds": round(elapsed, 4),
            "agents": agents,
            "sidecar_before": len(before_events),
            "sidecar_after": len(after_events),
            "transitions": len(new_events),
            "engine_events_returned": len(events),
            "by_node_type": by_type,
            "by_to_state": to_state,
            "soul_type_transitions": {t: by_type.get(t, 0) for t in SOUL_TYPES},
            "expected_retirement": sorted(expected_retirement),
            "expected_retirement_count": len(expected_retirement),
            "graph_nodes": graph_nodes,
            "graph_state_distribution": state_dist,
            "no_op_note": (not new_events and graph_nodes > 0 and not eval_error
                           and not undelivered),
            "eval_error": eval_error,
            "undelivered_transitions": undelivered,
            "key_node": args.key_node,
            "key_node_transitions": len([e for e in new_events
                                         if _matches_key_node(e.get("node_id"),
                                                             args.key_node)]),
            "weak_prefix_before": weak_total_before,
            "weak_prefix_after": weak_total_after,
            "prompt_diffs": prompt_diffs,
            "prompt_diffs_unexplained": len(
                [d for d in prompt_diffs
                 if not d.get("explained_by_expected_metabolism")]),
            "verdict": "FAIL" if fails else "PASS",
            "failures": fails,
        }

        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            # 🔴 預期退役集合明細（Owner 2026-10-23「改判準」）
            retirement_lines = [
                "預期退役集合（EXPECTED_METABOLISM）= %d 筆" % len(expected_retirement),
            ]
            node_info = {n.get("node_id"): n for n in nodes if isinstance(n, dict)}
            for _nid in sorted(expected_retirement):
                _info = node_info.get(_nid) or {}
                retirement_lines.append(
                    "  %s  type=%s  agent=%s"
                    % (_nid, _info.get("node_type", "?"), _info.get("agent_id", "?")))
            retirement_lines.append(
                "本次命中（轉入 weakening）= %d 筆" % len(expected_hits))
            lines = [
                "=" * 74,
                "LIFECYCLE OFFLINE EVAL (CLI) — mode=sandbox-only",
                "=" * 74,
                "source_dir (唯讀)    = %s" % source,
                "  來源=repo 生產目錄  = %s" % result["source_is_repo_production"],
                "sandbox_dir (唯一寫入)= %s" % sandbox,
                "simulated_time       = %s" % (simulated_time or "(real now)"),
                "eval_error           = %s" % (eval_error or "無（評估正常完成）"),
                "undelivered          = %d 筆（評估回傳但 sidecar 未增加 ⇒ 轉變沒落地）"
                % undelivered,
                "elapsed_seconds      = %s" % result["elapsed_seconds"],
                "sidecar  %d -> %d   (新增 %d 筆轉變)"
                % (result["sidecar_before"], result["sidecar_after"], result["transitions"]),
                "by_node_type         = %s" % by_type,
                "by_to_state          = %s" % to_state,
                "soul_type_counts     = %s   (非預期代謝者必須全 0；本次預期退役命中 %d 筆)"
                % (result["soul_type_transitions"], len(expected_hits)),
                *retirement_lines,
                "graph_nodes          = %d   state_dist=%s" % (graph_nodes, state_dist),
                "no_op_note           = %s" % (
                    "是（0 筆新轉變，但圖非空 ⇒ 冪等 no-op，非失敗）"
                    if result["no_op_note"] else "否"),
                "key_node %s transitions = %d  (非預期代謝時必須 0)"
                % (args.key_node, result["key_node_transitions"]),
                "weak_prefix  %d -> %d" % (weak_total_before, weak_total_after),
                "prompt_diffs         = %s" % (prompt_diffs or "無（所有 agent 正文逐位元相同）"),
                "prompt_diffs_unexplained = %d 筆（只有這些會觸發 ④；可由預期代謝解釋者不報）"
                % result["prompt_diffs_unexplained"],
                "",
                "VERDICT: %s" % result["verdict"],
            ]
            for f in fails:
                lines.append("  - " + f)
            print("\n".join(lines).encode("ascii", "backslashreplace").decode("ascii"))

        report_path = Path(args.report) if args.report else (
            Path(os.environ.get("TEMP", ".")) / "eval_lifecycle_report.md")
        body = [
            "# 生命週期離線評估報告（eval_lifecycle_cli）",
            "",
            "- 模式：`sandbox-only`（唯一寫入目標＝自建沙盒）",
            "- 來源（唯讀）：`%s`" % source,
            "- 來源是否為 repo 生產目錄：%s" % result["source_is_repo_production"],
            "- 沙盒：`%s`" % sandbox,
            "- 評估時鐘：%s" % (simulated_time or "(real now)"),
            "- 評估例外：%s" % (eval_error or "無（評估正常完成）"),
            "- 耗時：%.4f 秒" % elapsed,
            "- sidecar：%d → %d（新增 %d 筆）"
            % (result["sidecar_before"], result["sidecar_after"], result["transitions"]),
            "- 轉變類型分布：%s" % by_type,
            "- 轉變目標狀態：%s" % to_state,
            "- 預期退役集合（EXPECTED_METABOLISM）：%d 筆%s"
            % (len(expected_retirement),
               ("（" + ", ".join(sorted(expected_retirement)) + "）")
               if expected_retirement else ""),
            "- 本次命中（轉入 weakening）：%d 筆" % len(expected_hits),
            "- 靈魂結構轉變（**非預期代謝者**必須全 0）：%s" % result["soul_type_transitions"],
            "- 關鍵反例 `%s` 轉變次數（**非預期代謝時**必須 0）：%d"
            % (args.key_node, result["key_node_transitions"]),
            "- 弱化包裝計數：%d → %d" % (weak_total_before, weak_total_after),
            "- Prompt 正文差異：%s" % (prompt_diffs or "無"),
            "- Prompt 正文差異（**無法由預期代謝解釋** ⇒ 觸發 ④）：%d 筆"
            % result["prompt_diffs_unexplained"],
            "- **判定：%s**" % result["verdict"],
            "",
            "## 各 agent 投影（評估前 → 評估後）",
            "",
            "| agent | before_len | after_len | weak 前 | weak 後 |",
            "|---|---|---|---|---|",
        ]
        for a in agents:
            b, aft = before_prompt.get(a, {}), after_prompt.get(a, {})
            body.append("| %s | %s | %s | %s | %s |" % (
                a, b.get("len", "-"), aft.get("len", "-"),
                b.get("weak_prefix_count", "-"), aft.get("weak_prefix_count", "-")))
        report_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        print("[report] %s" % report_path)
        return 1 if fails else 0
    finally:
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
