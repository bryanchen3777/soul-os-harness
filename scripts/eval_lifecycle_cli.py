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

# ── 預期退役 cohort 台帳（Owner 2026-10-23 裁定：選項乙「改判準」）────────
# 語意：value 節點若「建立後從未被日常 Pattern 背書」
# （last_support_ts == created_ts、祖先 support_propagation 邊＝0），
# 會在出生＋7 天（引擎的 LIFECYCLE_T_WEAKEN_DAYS，凍結的衰減門檻）
# 自然轉入 weakening —— 這是**預期代謝**，不是破線。
#
# 🔴 **為什麼是「台帳」而不是單一 cohort**（2026-10-25 主大腦沙盒實測）：
# 生產圖譜裡「從未被背書的 value」**每天都在產生新 cohort**
# （10/11: 10 顆、10/18: 10 顆、10/19: 10 顆、10/20: 1 顆、10/21: 19 顆、
# 10/22: 38 顆、10/23: 46 顆、10/24: 58 顆、10/25: 10 顆）
# ⇒ 單一 cohort 白名單是一台**結構性的假紅產生器**（每個 night slot 都會再炸
# 一次，而它其實是 Owner 親自宣告過的「預期代謝」）。
# ⇒ 台帳化的同時，判定改為**三分類**：
#    expected（已核准 cohort）→ pending（結構同構但**尚未核准**）→ breaks（真破線）。
#
# 🔴 **台帳只認「已量測、已核准」的 cohort**；結構同構但未核准者一律進 pending，
#    由主大腦依實測追加 —— **CLI 不得自行核准**（放行必須有界、可被推翻）。
#
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

# 🔴 預期退役的**時間下界**（Owner 2026-10-23 裁示核准）。
#    語意：10 顆 Legacy Value 的**合約退役槽** ＝ 2026-10-24 22:00 EDT
#    ＝ 2026-10-25T02:00:00Z。⇒ **早於**此槽的弱化是「提前早衰」，
#    必須熔斷報警，**不得**用「預期代謝」放行。
#    🔴 這是 Owner 核准的「退役窗」常數，**不是**衰減門檻：
#    判準層仍不得做任何「距今 N 天」型算術（守衛
#    `test_cli_does_not_import_decay_thresholds` 會轉紅）。
EXPECTED_RETIREMENT_NOT_BEFORE = "2026-10-25T02:00:00+00:00"

# 🔴 **已核准的「預期退役 cohort 台帳」**（Owner 2026-10-25 主大腦沙盒實測核准）。
#    每筆 = 一個建立日 cohort 的（建立日、核准節點集合、最早可退役槽）。
#
#    `nodes` ＝ 該 cohort **在所有量測槽中出現過的全部退役節點的聯集**
#    （同一批的成員會因支援邊時間不同而分批跨門檻，故必須取聯集）。
#    量測方式：`evaluate_lifecycle_all()` 對生產圖譜的**沙盒副本**逐槽跑
#    （2026-10-26T02:00Z ～ 2026-11-03T02:00Z 九個槽），唯讀生產。
#
#    ⚠️ **兩處刻意的偏離**（皆已向主大腦回報）：
#    1. `2026-10-18` 多含一顆 `9110a6de74f6…`（agent_aoi）：該節點是
#       SOUL-BUG-010 的 quarantine 受害者（sidecar 內為 `dormant`），
#       **永遠不會**轉入 weakening ⇒ 不會出現在任何量測槽的退役清單裡。
#       但它**符合四條件推導**（value ＋ 建立日 10-18 ＋ last_support==created
#       ＋ 無 support_propagation 祖先邊）⇒ 若不列入，逐 cohort 的自我否決
#       會因「推導集合超出核准清單」而誤報 FAIL。
#       ⇒ 列入它**不放寬任何實際退役**（dormant ≠ weakening，永遠不會命中
#       expected），只是讓「推導 ⊆ 核准」的不變量成立。
#    2. `2026-10-11` 直接引用 `EXPECTED_RETIREMENT_BASELINE`（Owner 核准的
#       10 顆），避免手抄 32 字元 id 時打錯一個字元。
#
# 🔴 **2026-10-30 Owner 核准入帳（5 筆 belief）**：CLI 沙盒快跑回報
#    `PENDING_COHORT`（exit 3）的 5 筆 belief 節點，經四條件與時間窗診斷
#    驗證後由 Owner 核准入帳 —— 2026-10-23 cohort 4 筆
#    （agent_mai／agent_aoi／agent_rem／agent_anna）
#    ＋ 2026-10-24 cohort 1 筆（agent_yua）。
#    🔴 **逐節點 `bounds` ＝ 引擎自己的衰減門檻（last_support_ts ＋ 7 天）**：
#       這 5 筆的 cohort 級 `not_before`（建立日＋8 天 @02:00Z）比它們的
#       真實門檻**晚一槽** ⇒ 必須逐節點覆寫，否則會被誤判成「提前早衰」
#       （破線）。與 2026-10-27 既有 cohort 同一慣例；本 cohort 既有 46 筆
#       bounds 已逐筆驗證全部等於 created_ts ＋ 7 天（46/46）。
#
#    🔴 `not_before` ＝ 該 cohort 的**首次退役槽**（宣告式常數；判準層不做
#    任何「距今 N 天」型算術，守衛 `test_cli_does_not_import_decay_thresholds`）。
EXPECTED_RETIREMENT_COHORTS = (
    {
        "date": EXPECTED_RETIREMENT_DATE,
        "not_before": EXPECTED_RETIREMENT_NOT_BEFORE,
        "nodes": EXPECTED_RETIREMENT_BASELINE,
    },
        {
        "date": "2026-10-18",
        "not_before": "2026-10-26T02:00:00+00:00",
        "bounds": {
            "1afdab194ee74551b7f63e362c14906c": "2026-10-25T12:00:10.121130+00:00",
            "295d22681002416eaebb7d424f6ccd96": "2026-10-25T12:00:06.899792+00:00",
            "519cd7aac84940b9a262b957310ddb41": "2026-10-25T12:00:08.279439+00:00",
            "68e2b79aa4b7496387801fb45c98a546": "2026-10-25T12:00:11.938623+00:00",
            "7b26877f9a5b44d8986cf1a1e94a58f0": "2026-10-25T12:00:13.991714+00:00",
            "9110a6de74f6472ca0e4decb95dc5949": "2026-10-25T12:00:20.541075+00:00",
            "963e307dba12427fb5314a0d7786ea95": "2026-10-25T12:00:12.755051+00:00",
            "b2134d90e96a411893e13b5aa14a2cc4": "2026-10-25T12:00:17.267221+00:00",
            "c6463695ebe84c718e5ec709b848b105": "2026-10-25T12:00:18.855425+00:00",
            "d3e3f393853c4db6a9587a758824b818": "2026-10-25T12:00:15.977870+00:00",
        },
        "nodes": frozenset({
            "1afdab194ee74551b7f63e362c14906c",  # agent_akane
            "295d22681002416eaebb7d424f6ccd96",  # agent_yua
            "519cd7aac84940b9a262b957310ddb41",  # agent_ruka
            "68e2b79aa4b7496387801fb45c98a546",  # agent_rem
            "7b26877f9a5b44d8986cf1a1e94a58f0",  # agent_mahiru
            "9110a6de74f6472ca0e4decb95dc5949",  # agent_aoi ★dormant⇒永不退役
            "963e307dba12427fb5314a0d7786ea95",  # agent_ram
            "b2134d90e96a411893e13b5aa14a2cc4",  # agent_mai
            "c6463695ebe84c718e5ec709b848b105",  # agent_miku
            "d3e3f393853c4db6a9587a758824b818",  # agent_anna
        }),
    },
        {
        "date": "2026-10-19",
        "not_before": "2026-10-27T02:00:00+00:00",
        "bounds": {
            "69b4b4b4e9d2401d9b87766f13c81802": "2026-10-26T12:00:28.796886+00:00",
            "6ad89f084f1d48939296d7cfac48dc55": "2026-10-26T12:00:41.045203+00:00",
            "704c8f762cc24a8895df9dda40a163d7": "2026-10-26T12:00:42.853545+00:00",
            "8734050055ff46ae9af3bed1f8d2ed8a": "2026-10-26T12:00:21.924475+00:00",
            "8b3971948c104e649d0b1f41f1920b80": "2026-10-26T12:00:30.960806+00:00",
            "b78ae5a81e804022b8e51e9ac55cfd45": "2026-10-26T12:00:27.117484+00:00",
            "b983e5a206ff4bf68ac176e1c9e41865": "2026-10-26T12:00:44.362620+00:00",
            "bbe18d2bf8f740f6908af76f5422cc00": "2026-10-26T12:00:46.075891+00:00",
            "e2e99a523adb4fe6adcb5ec3dbd4c62b": "2026-10-26T12:00:18.751036+00:00",
            "ee70ceda185e4ca5aed66a3a60a33e14": "2026-10-26T12:00:25.712219+00:00",
        },
        "nodes": frozenset({
            "69b4b4b4e9d2401d9b87766f13c81802",  # agent_ram
            "6ad89f084f1d48939296d7cfac48dc55",  # agent_anna
            "704c8f762cc24a8895df9dda40a163d7",  # agent_mai
            "8734050055ff46ae9af3bed1f8d2ed8a",  # agent_ruka
            "8b3971948c104e649d0b1f41f1920b80",  # agent_mahiru
            "b78ae5a81e804022b8e51e9ac55cfd45",  # agent_rem
            "b983e5a206ff4bf68ac176e1c9e41865",  # agent_miku
            "bbe18d2bf8f740f6908af76f5422cc00",  # agent_aoi
            "e2e99a523adb4fe6adcb5ec3dbd4c62b",  # agent_yua
            "ee70ceda185e4ca5aed66a3a60a33e14",  # agent_akane
        }),
    },
        {
        "date": "2026-10-20",
        "not_before": "2026-10-28T02:00:00+00:00",
        "types": {
            "575efe6b68ef42bf8a904c1eac0561dd": "trait",
            "57c164011e3e4065bdcd05b6eeaa9c28": "trait",
            "c43b515f2fdb4ac0bc2855c60d59c2c9": "trait",
            "ee1ac76f4f25435fa55f3a231641108c": "trait",
        },
        "bounds": {
            "575efe6b68ef42bf8a904c1eac0561dd": "2026-10-27T02:05:25.882525+00:00",
            "57c164011e3e4065bdcd05b6eeaa9c28": "2026-10-27T02:05:23.242942+00:00",
            "74406ee863574f0398b02bed1de3e9d5": "2026-10-27T12:00:29.684239+00:00",
            "c43b515f2fdb4ac0bc2855c60d59c2c9": "2026-10-27T02:05:19.975473+00:00",
            "ee1ac76f4f25435fa55f3a231641108c": "2026-10-27T02:05:28.553858+00:00",
        },
        "nodes": frozenset({
            "171be517538945abb126ea66af34aa50",  # agent_aoi
            "33194d9da2fb4bd1860c69638d4321d2",  # agent_mahiru
            "536049d72ce44c9cb065b3c3575cdd75",  # agent_ram
            "575efe6b68ef42bf8a904c1eac0561dd",  # agent_ram
            "57c164011e3e4065bdcd05b6eeaa9c28",  # agent_akane
            "6d6de3b3f24041a4ac2c4f246c749aeb",  # agent_mai
            "74406ee863574f0398b02bed1de3e9d5",  # agent_yua
            "7906aef95d55426789ed21c996d4050c",  # agent_akane
            "9a1daf40ee9f4103a3e2e380a64ea9e2",  # agent_rem
            "b031a9fb32f64bf5a7418073e3adab34",  # agent_anna
            "b82bd21bcf1c44e38c52ff72361309d1",  # agent_miku
            "c43b515f2fdb4ac0bc2855c60d59c2c9",  # agent_rem
            "cbd28b1afed34da484b93c666c51dcaa",  # agent_ruka
            "ee1ac76f4f25435fa55f3a231641108c",  # agent_ruka
        }),
    },
        {
        "date": "2026-10-21",
        "not_before": "2026-10-29T02:00:00+00:00",
        # 🔴 2026-10-28 Owner 核准入帳：2026-10-21 cohort 的 4 顆**靈魂型**
        #    （3 belief ＋ 1 trait）退役批次 —— 建立於 2026-10-21、
        #    last_support_ts == created_ts、無 support_propagation 祖先邊、
        #    已於 2026-10-29T02:00:25Z 實體落盤（slot 2026-10-29T02:00:00Z）。
        #    ★ 這 4 顆**不在** 19 顆 value 集合內 ⇒ 必須以 `types` 記載型別，
        #      否則未記載型別會被視為 "value" ⇒ 型別比對失敗 ⇒ 誤判破線。
        #    ★ 與 2026-10-20 cohort 的 4 顆 trait 同構（既有先例）。
        "types": {
            "5f83b79f626441a39c76c7a55b2961d5": "belief",   # agent_ram
            "bfad28d21ee84ca7bbb78aea786999c8": "trait",    # agent_yua
            "cf61d09a57bd4ea992c4b925740a1ce8": "belief",   # agent_miku
            "d703149f3ae9475a80bc01621042a799": "belief",   # agent_ruka
        },
        "bounds": {
            "04ab9b3c3e244cc28ee9eb6a6c344c95": "2026-10-28T12:00:12.856724+00:00",
            "07c268736b3747d1b5e8f7e3670603cc": "2026-10-28T12:00:17.563778+00:00",
            "1e6f6d06de6746da9b08032f159a129a": "2026-10-28T12:10:40.607667+00:00",
            "370ac25955b9470f85fecc79ece1e005": "2026-10-28T12:10:40.741911+00:00",
            "4244023c2cc84e41ad3704205cd86da7": "2026-10-28T02:00:09.360893+00:00",
            "42b15eebec354f61ab2c46bdfdbdd09f": "2026-10-28T02:00:17.586332+00:00",
            "4660ce03240b40e2bf5bc2b4a691a892": "2026-10-28T02:00:12.548971+00:00",
            "4b5002620cba442790db2134d42d1b64": "2026-10-28T02:00:16.687487+00:00",
            "58b56251b6944e19a931f81c25cf1a6f": "2026-10-28T02:00:18.521652+00:00",
            "70cbd6a72ddc456e9c6505f8e61e97b6": "2026-10-28T12:10:40.717630+00:00",
            "8377645589c24fd4ad57cc3e2be748e3": "2026-10-28T02:00:20.444832+00:00",
            "9c5f79823c88489987c72e38949fc33f": "2026-10-28T02:00:14.892200+00:00",
            "a2d208ad228e4f2ba92c5d6e16463c67": "2026-10-28T02:00:10.879012+00:00",
            "a6aad545683c44d69c69cda244257f87": "2026-10-28T12:00:28.462066+00:00",
            "af2b20eacddd42939f8cde8962ca6df9": "2026-10-28T12:10:40.624762+00:00",
            "aff9c75c347a43dda426563eb8fdc9a4": "2026-10-28T12:10:40.693551+00:00",
            "b085cbf072d34aaeb7be8ebc7ce43281": "2026-10-28T12:10:40.650151+00:00",
            "bd1d1134c3cd4afdb9490a0f389a69b5": "2026-10-28T12:10:40.670590+00:00",
            "fc85b012e7604e0cb1d2fbd228c6f4c2": "2026-10-28T02:00:13.465758+00:00",
        },
        "nodes": frozenset({
            "04ab9b3c3e244cc28ee9eb6a6c344c95",  # agent_yua
            "07c268736b3747d1b5e8f7e3670603cc",  # agent_ruka
            "1e6f6d06de6746da9b08032f159a129a",  # agent_anna
            "370ac25955b9470f85fecc79ece1e005",  # agent_rem
            "4244023c2cc84e41ad3704205cd86da7",  # agent_ruka
            "42b15eebec354f61ab2c46bdfdbdd09f",  # agent_mai
            "4660ce03240b40e2bf5bc2b4a691a892",  # agent_rem
            "4b5002620cba442790db2134d42d1b64",  # agent_anna
            "58b56251b6944e19a931f81c25cf1a6f",  # agent_miku
            "70cbd6a72ddc456e9c6505f8e61e97b6",  # agent_ram
            "8377645589c24fd4ad57cc3e2be748e3",  # agent_aoi
            "9c5f79823c88489987c72e38949fc33f",  # agent_mahiru
            "a2d208ad228e4f2ba92c5d6e16463c67",  # agent_akane
            "a6aad545683c44d69c69cda244257f87",  # agent_akane
            "af2b20eacddd42939f8cde8962ca6df9",  # agent_aoi
            "aff9c75c347a43dda426563eb8fdc9a4",  # agent_miku
            "b085cbf072d34aaeb7be8ebc7ce43281",  # agent_mahiru
            "bd1d1134c3cd4afdb9490a0f389a69b5",  # agent_mai
            "fc85b012e7604e0cb1d2fbd228c6f4c2",  # agent_ram
            # 🔴 2026-10-28 Owner 核准入帳：4 顆靈魂型退役（3 belief ＋ 1 trait）。
            "5f83b79f626441a39c76c7a55b2961d5",  # agent_ram
            "bfad28d21ee84ca7bbb78aea786999c8",  # agent_yua
            "cf61d09a57bd4ea992c4b925740a1ce8",  # agent_miku
            "d703149f3ae9475a80bc01621042a799",  # agent_ruka
        }),
    },
        {
        "date": "2026-10-22",
        "not_before": "2026-10-30T02:00:00+00:00",
        "bounds": {
            "0064cf183f654e2f8740b96da9ca2729": "2026-10-29T12:00:59.280842+00:00",
            "08d60396a7714656b0fa6c77b7c85a43": "2026-10-29T12:00:41.882495+00:00",
            "0c13bef1360d4c6ebdc7756da7578e25": "2026-10-29T12:00:49.337143+00:00",
            "13f940899fd44220800abae9e50b7661": "2026-10-29T12:00:38.678723+00:00",
            "1408be73efa3417494af091f626db345": "2026-10-29T12:00:14.500084+00:00",
            "2a73b9a47d4f46e2a035bc45017dba58": "2026-10-29T12:00:47.883197+00:00",
            "2c8a4e466c8a4888ac99dfa2b642e585": "2026-10-29T12:00:23.778403+00:00",
            "2f4c61b270fa41d28295211d31015bc7": "2026-10-29T12:00:56.298735+00:00",
            "339be0618eb241fe83bcc1a7cc9f1c5a": "2026-10-29T12:00:45.696476+00:00",
            "36fc3a5bdc0b4967bf19989c43b225c3": "2026-10-29T12:00:52.484282+00:00",
            "42cfa9b31c574d878225fe2a3454b0bf": "2026-10-29T12:00:44.170089+00:00",
            "47d94d6a37614a27a446b3730366f7e6": "2026-10-29T12:00:29.566415+00:00",
            "49a51a80cac943fbb99dee457f1d0e48": "2026-10-29T12:00:47.845202+00:00",
            "4d8cff438359403f9a97670b6fda6aaa": "2026-10-29T12:00:35.674780+00:00",
            "52cab83848714193b0079050c9407644": "2026-10-29T12:00:29.539419+00:00",
            "540fd899d60f43a6b92ae5254c3a5480": "2026-10-29T12:00:32.584004+00:00",
            "69a49fccdbc646a6861ac41c46ad2214": "2026-10-29T12:00:32.618863+00:00",
            "6a39b0a330284fbdb16db53243d4154c": "2026-10-29T02:00:55.529599+00:00",
            "6f24fa449e734147bc43029bae8e088c": "2026-10-29T12:00:26.780563+00:00",
            "71b7da99f0f1481a9bc4d6a6a3edaabb": "2026-10-29T02:01:12.323423+00:00",
            "7385e28ac2244254a103aaa711797d48": "2026-10-29T12:00:11.311700+00:00",
            "7460b45e7ed34728a33f5b3e19a775a3": "2026-10-29T12:00:05.425639+00:00",
            "8394f25a1de24734af2f9088ed512a1b": "2026-10-29T12:00:50.955958+00:00",
            "8981d206d44940d1a6174d8403d4f51c": "2026-10-29T12:00:28.346274+00:00",
            "8f3b15951d054e72ac1fa7f55ea616f7": "2026-10-29T12:00:57.483152+00:00",
            "98d597995130496d960aee2b8ce0d691": "2026-10-29T12:00:37.495276+00:00",
            "a0e77f23ae954177b0dff925bd507d56": "2026-10-29T02:00:38.076957+00:00",
            "a1d13a0ce8d74b52a2cbce45a30840fc": "2026-10-29T02:00:27.858488+00:00",
            "a913cfd36a2b44418274597eef2d7a07": "2026-10-29T12:00:25.599861+00:00",
            "b80d21f1313642c2a179ae31361f3d36": "2026-10-29T12:00:31.148335+00:00",
            "ba89e4ff3a7640fe8ef841fa55c40450": "2026-10-29T12:00:40.512067+00:00",
            "d57e5a0b72944bf98d327863c73837f3": "2026-10-29T12:01:02.222286+00:00",
            "e06a3ac4ae48401aa22cb3e936dc16a0": "2026-10-29T12:00:44.187373+00:00",
            "e57ac7084c3647a4a6dfd4273cf1df0a": "2026-10-29T12:00:25.615047+00:00",
            "eb942fb03aca480ea0f7497f4d3374eb": "2026-10-29T12:00:54.236832+00:00",
            "f6aedaf0255549af8214e4641207f109": "2026-10-29T12:00:19.539765+00:00",
            "ff0af1b8564743b18967111bfe210dbf": "2026-10-29T12:01:00.489304+00:00",
            "ff2c11876f094657a4b6b5f537c169d5": "2026-10-29T12:00:38.701997+00:00",
        },
        "nodes": frozenset({
            "0064cf183f654e2f8740b96da9ca2729",  # agent_shizuku
            "08d60396a7714656b0fa6c77b7c85a43",  # agent_kurisu
            "0c13bef1360d4c6ebdc7756da7578e25",  # agent_reze
            "13f940899fd44220800abae9e50b7661",  # agent_kaori
            "1408be73efa3417494af091f626db345",  # agent_akane
            "2a73b9a47d4f46e2a035bc45017dba58",  # agent_minami
            "2c6d0cabb4844b729fc0bf487f018750",  # agent_ruka
            "2c8a4e466c8a4888ac99dfa2b642e585",  # agent_ai
            "2f4c61b270fa41d28295211d31015bc7",  # agent_sakura
            "339be0618eb241fe83bcc1a7cc9f1c5a",  # agent_mashiro
            "36fc3a5bdc0b4967bf19989c43b225c3",  # agent_rio
            "37cd3ff1ab2d4ef7a3aaeb4c79ce2cee",  # agent_ram
            "42cfa9b31c574d878225fe2a3454b0bf",  # agent_makima
            "47d94d6a37614a27a446b3730366f7e6",  # agent_mahiru
            "49a51a80cac943fbb99dee457f1d0e48",  # agent_aoi
            "4c943311aedc4c7b9a732e6c77767df1",  # agent_anna
            "4d8cff438359403f9a97670b6fda6aaa",  # agent_kaguya
            "52cab83848714193b0079050c9407644",  # agent_haruhi
            "540fd899d60f43a6b92ae5254c3a5480",  # agent_anna
            "68435bfe2da44b0c811a04ce112adffc",  # agent_rem
            "69a49fccdbc646a6861ac41c46ad2214",  # agent_iroha
            "6a39b0a330284fbdb16db53243d4154c",  # agent_mahiru
            "6f24fa449e734147bc43029bae8e088c",  # agent_asuna
            "71b7da99f0f1481a9bc4d6a6a3edaabb",  # agent_mai
            "7385e28ac2244254a103aaa711797d48",  # agent_ruka
            "7460b45e7ed34728a33f5b3e19a775a3",  # agent_yua
            "7a459662175742e8826c9344477eaa45",  # agent_aoi
            "8394f25a1de24734af2f9088ed512a1b",  # agent_rin
            "8981d206d44940d1a6174d8403d4f51c",  # agent_fuuka
            "8f3b15951d054e72ac1fa7f55ea616f7",  # agent_shea
            "98d597995130496d960aee2b8ce0d691",  # agent_kana
            "a0e77f23ae954177b0dff925bd507d56",  # agent_akane
            "a1d13a0ce8d74b52a2cbce45a30840fc",  # agent_yua
            "a913cfd36a2b44418274597eef2d7a07",  # agent_mochizuki_ai
            "b80d21f1313642c2a179ae31361f3d36",  # agent_hiro
            "ba89e4ff3a7640fe8ef841fa55c40450",  # agent_kirino
            "d57e5a0b72944bf98d327863c73837f3",  # agent_yue
            "e06a3ac4ae48401aa22cb3e936dc16a0",  # agent_miku
            "e57ac7084c3647a4a6dfd4273cf1df0a",  # agent_ram
            "e928bbed121f4ec58a96d3b9f0890d7c",  # agent_miku
            "eb942fb03aca480ea0f7497f4d3374eb",  # agent_sachi
            "f6aedaf0255549af8214e4641207f109",  # agent_rem
            "ff0af1b8564743b18967111bfe210dbf",  # agent_tsukasa
            "ff2c11876f094657a4b6b5f537c169d5",  # agent_mai
        }),
    },
        {
        "date": "2026-10-23",
        "not_before": "2026-10-31T02:00:00+00:00",
        "types": {
            "310a62dcfbcd48a69729d48d0e3a3dfb": "belief",
            "3a7308ce1519493e94029a4d8cf2c418": "belief",
            "ac640743f7b04750a5ab436fc40dccf6": "belief",
            "f852908766ad46f4883580462a835989": "belief",
        },
        "bounds": {
            "002232c610504a13a9cebcd2a34bf37d": "2026-10-30T12:01:12.628544+00:00",
            "0567db7ab97940e8b9f7457d547aac45": "2026-10-30T12:00:57.181156+00:00",
            "1382b88a10374e35b29f8a07ded74ad8": "2026-10-30T02:01:18.580179+00:00",
            "240a059732284414a4b522702024cd7e": "2026-10-30T12:00:44.044075+00:00",
            "28eb6e3216714b17a3aae0f64b55406e": "2026-10-30T12:01:07.927136+00:00",
            "2bf15a92392a4362a618db172bea0bd4": "2026-10-30T12:01:20.895951+00:00",
            "310a62dcfbcd48a69729d48d0e3a3dfb": "2026-10-30T12:01:17.385299+00:00",
            "38a0651fdce546709f544f03bbdb793a": "2026-10-30T12:01:01.381618+00:00",
            "3a7308ce1519493e94029a4d8cf2c418": "2026-10-30T12:01:22.443087+00:00",
            "44f424e7a58e4bbd9e50fb6cf0587579": "2026-10-30T02:01:14.255790+00:00",
            "5db2038b2ea4478298ee587c483250fe": "2026-10-30T12:00:47.723574+00:00",
            "673362d810be43f591f52de2350e82ce": "2026-10-30T12:00:59.488190+00:00",
            "682d8e08f8944cef9b03c81911d11802": "2026-10-30T12:01:15.714315+00:00",
            "69d0dee8200743b5894e31edb8d43a28": "2026-10-30T12:01:06.623047+00:00",
            "6c5baf9eded244c4a4e0cbbbdb1c5709": "2026-10-30T02:01:57.778677+00:00",
            "738ec6bc6de94213903f9d014b6f0461": "2026-10-30T02:01:56.824502+00:00",
            "754a4e143afe47cd9f87654dfe20900a": "2026-10-30T12:00:45.658868+00:00",
            "7cb2434264ae4ce88710bdf27fa0a8d5": "2026-10-30T02:00:54.234196+00:00",
            "806154cc795e4776a402868ee7d2cbbb": "2026-10-30T12:00:46.640663+00:00",
            "8321c3fa088e48ccaced52bf14f2f132": "2026-10-30T02:01:44.349331+00:00",
            "85a3b08fcf0346bfa6ec97b67209f89a": "2026-10-30T02:01:01.274824+00:00",
            "8b6d5f1e082f4c76b4762e82c2a59bff": "2026-10-30T12:01:17.424505+00:00",
            "937a5c5288de490788c5e1a907bfdb79": "2026-10-30T12:01:03.039564+00:00",
            "94426b9ef5464ee0b74ffc50a3cdc144": "2026-10-30T02:01:52.682011+00:00",
            "953d71bb3fe945d5827f4ebbf60c56b2": "2026-10-30T02:01:46.911407+00:00",
            "96a83218fc2645809e5cfe8e9d7dcc97": "2026-10-30T02:01:47.939233+00:00",
            "9861c86e1ed94a87803382376189da77": "2026-10-30T02:01:55.288945+00:00",
            "990552e019b04d85b928ac8670e01926": "2026-10-30T12:01:04.910816+00:00",
            "9b2f4802c0894b518c50a71b33268ce4": "2026-10-30T02:01:28.453754+00:00",
            "9d4af70406fd4606990fa529abc48a8c": "2026-10-30T02:01:50.924606+00:00",
            "9f74b9763bc944389e8c98477d026a66": "2026-10-30T02:01:04.061416+00:00",
            "a3ee3a86f01f4589acc74fb7e18fe913": "2026-10-30T02:01:22.203160+00:00",
            "ac640743f7b04750a5ab436fc40dccf6": "2026-10-30T02:01:10.112764+00:00",
            "b06db3840b8545f98d708a2c114f0981": "2026-10-30T02:01:45.685237+00:00",
            "b4b2d74388b845cc9aa8ea3761a57c06": "2026-10-30T12:01:22.524803+00:00",
            "b59da74b5deb49dd923b6108638bdd36": "2026-10-30T12:00:54.780745+00:00",
            "b8c8b22509f643f98a9b2979372fa560": "2026-10-30T02:01:49.653848+00:00",
            "c812f60e9f6a43aeb8c508397486d82c": "2026-10-30T12:00:50.210943+00:00",
            "cb1d730711314a41afb91ce180cdc330": "2026-10-30T12:01:13.900615+00:00",
            "cefad5aa3d9944b8941e11fc471100f6": "2026-10-30T12:00:53.411340+00:00",
            "d0d1a08e45ed455cb1eb1daedd36d39f": "2026-10-30T02:01:38.832379+00:00",
            "d388efa5ea514ab6b0d4c83266c233dd": "2026-10-30T02:01:42.918611+00:00",
            "d5d3b017053c409cbcfa61ec52a5220b": "2026-10-30T12:01:18.680799+00:00",
            "d75e794cd0014deab96dc7e190c49a65": "2026-10-30T12:00:48.973986+00:00",
            "dd5d135e37524634baff3f7513db0277": "2026-10-30T12:01:10.728193+00:00",
            "ed5b1aa5b27b47a8bd049be9176c52f8": "2026-10-30T02:01:10.111762+00:00",
            "ee9e9b9d5f174b55bbaa5ef5f19659a5": "2026-10-30T02:01:54.321442+00:00",
            "f68b7691a5cd44d8a26ec58f208f15f9": "2026-10-30T02:01:32.221593+00:00",
            "f852908766ad46f4883580462a835989": "2026-10-30T02:01:24.559726+00:00",
            "fe4484bef142463dbe62dd55b9f56dd8": "2026-10-30T12:00:51.675335+00:00",
        },
        "nodes": frozenset({
            "002232c610504a13a9cebcd2a34bf37d",  # agent_rio
            "0567db7ab97940e8b9f7457d547aac45",  # agent_kaori
            "10e4ca38ec8c4024ace5ffe6f8eec26d",  # agent_anna
            "1382b88a10374e35b29f8a07ded74ad8",  # agent_mahiru
            "240a059732284414a4b522702024cd7e",  # agent_ai
            "28eb6e3216714b17a3aae0f64b55406e",  # agent_reze
            "2bf15a92392a4362a618db172bea0bd4",  # agent_tsukasa
            "310a62dcfbcd48a69729d48d0e3a3dfb",  # agent_mai
            "38a0651fdce546709f544f03bbdb793a",  # agent_kurisu
            "3a7308ce1519493e94029a4d8cf2c418",  # agent_aoi
            "44f424e7a58e4bbd9e50fb6cf0587579",  # agent_ram
            "4df8ea17cfde403084fef41e2a328ddc",  # agent_rem
            "5db2038b2ea4478298ee587c483250fe",  # agent_fuuka
            "673362d810be43f591f52de2350e82ce",  # agent_kirino
            "682d8e08f8944cef9b03c81911d11802",  # agent_sakura
            "69d0dee8200743b5894e31edb8d43a28",  # agent_minami
            "6c5baf9eded244c4a4e0cbbbdb1c5709",  # agent_yotsuba
            "738ec6bc6de94213903f9d014b6f0461",  # agent_shino
            "754a4e143afe47cd9f87654dfe20900a",  # agent_mochizuki_ai
            "7cb2434264ae4ce88710bdf27fa0a8d5",  # agent_yua
            "806154cc795e4776a402868ee7d2cbbb",  # agent_asuna
            "8321c3fa088e48ccaced52bf14f2f132",  # agent_chizuru
            "85a3b08fcf0346bfa6ec97b67209f89a",  # agent_ruka
            "8b6d5f1e082f4c76b4762e82c2a59bff",  # agent_shea
            "937a5c5288de490788c5e1a907bfdb79",  # agent_makima
            "94426b9ef5464ee0b74ffc50a3cdc144",  # agent_nino
            "953d71bb3fe945d5827f4ebbf60c56b2",  # agent_ichika
            "96a83218fc2645809e5cfe8e9d7dcc97",  # agent_itsuki
            "9861c86e1ed94a87803382376189da77",  # agent_saki
            "990552e019b04d85b928ac8670e01926",  # agent_mashiro
            "9b2f4802c0894b518c50a71b33268ce4",  # agent_mai
            "9d4af70406fd4606990fa529abc48a8c",  # agent_nagisa
            "9f74b9763bc944389e8c98477d026a66",  # agent_akane
            "a3ee3a86f01f4589acc74fb7e18fe913",  # agent_anna
            "ac640743f7b04750a5ab436fc40dccf6",  # agent_rem
            "b06db3840b8545f98d708a2c114f0981",  # agent_emilia
            "b4b2d74388b845cc9aa8ea3761a57c06",  # agent_yue
            "b59da74b5deb49dd923b6108638bdd36",  # agent_kana
            "b8c8b22509f643f98a9b2979372fa560",  # agent_mami
            "c812f60e9f6a43aeb8c508397486d82c",  # agent_hiro
            "cb1d730711314a41afb91ce180cdc330",  # agent_sachi
            "cefad5aa3d9944b8941e11fc471100f6",  # agent_kaguya
            "d0d1a08e45ed455cb1eb1daedd36d39f",  # agent_aoi
            "d388efa5ea514ab6b0d4c83266c233dd",  # agent_beatrice
            "d5d3b017053c409cbcfa61ec52a5220b",  # agent_shizuku
            "d75e794cd0014deab96dc7e190c49a65",  # agent_haruhi
            "dd5d135e37524634baff3f7513db0277",  # agent_rin
            "ded34a9ea0f94fcb9c79bfd965140c2f",  # agent_mahiru
            "ed5b1aa5b27b47a8bd049be9176c52f8",  # agent_rem
            "edecb4cc8fc641e9b30502e29430751f",  # agent_mai
            "ee9e9b9d5f174b55bbaa5ef5f19659a5",  # agent_rika
            "f68b7691a5cd44d8a26ec58f208f15f9",  # agent_miku
            "f852908766ad46f4883580462a835989",  # agent_anna
            "fe4484bef142463dbe62dd55b9f56dd8",  # agent_iroha
        }),
    },
        {
        "date": "2026-10-24",
        "not_before": "2026-11-01T02:00:00+00:00",
        "types": {
            "59a4cd01d63f4fa2875d221e0bce92b4": "belief",
        },
        "bounds": {
            "029ff2882da4409baedbed4b0c54c810": "2026-10-31T12:00:10.156382+00:00",
            "068cf1b57c634f85b607f03322ae796a": "2026-10-31T02:01:56.085157+00:00",
            "09ff9b82b67f46cdbd8c17844e65a1eb": "2026-10-31T02:02:05.766113+00:00",
            "0c307614a0584ec8b7e7e050a1aa4268": "2026-10-31T12:00:22.363198+00:00",
            "149b5bc3af064d58b81726df17736149": "2026-10-31T12:00:46.173809+00:00",
            "1a6728d355464cacb8a2cc9ee1a95975": "2026-10-31T02:01:58.622018+00:00",
            "2484b1723a9e4369bb3979d949d390fc": "2026-10-31T02:01:57.294438+00:00",
            "29f3699163404212bc4003efa04bf1ac": "2026-10-31T12:00:51.235518+00:00",
            "311439cb93fe4dc682d823694dfd75e9": "2026-10-31T12:00:04.650026+00:00",
            "36e3094c1ac2476ebd8a55b914b9a6c2": "2026-10-31T02:02:09.459291+00:00",
            "4014334b37784d149135b42f8cb990e4": "2026-10-31T12:00:49.593515+00:00",
            "402bf7333bfb4166b873acb1146da5a6": "2026-10-31T02:02:06.765038+00:00",
            "42f4a18c1ab54ccbbe645025ae2f5ae8": "2026-10-31T02:01:04.257442+00:00",
            "4bee940a3ea64fe9a07665c81d15c879": "2026-10-31T12:00:28.493798+00:00",
            "503410c9d54144888954617361cced8e": "2026-10-31T12:00:27.143883+00:00",
            "51c759ffc3a74e63aed033f065872b90": "2026-10-31T12:00:30.855259+00:00",
            "54d68583d28a41f8994d342749678c6e": "2026-10-31T02:00:52.442803+00:00",
            "56912ffddd604aa4b3834610560c8609": "2026-10-31T12:00:25.825435+00:00",
            "59a4cd01d63f4fa2875d221e0bce92b4": "2026-10-31T02:00:51.278109+00:00",
            "60df9025683d46cd9c03545ab73d34e0": "2026-10-31T12:00:43.822090+00:00",
            "626cb717a06c4bc8bc591046a6b62404": "2026-10-31T12:00:32.151702+00:00",
            "62c1de5182f64e688c17f9c9482d70d1": "2026-10-31T02:01:05.633103+00:00",
            "637182b7a87a457f862ac2418bb62c57": "2026-10-31T12:00:53.975115+00:00",
            "6938a4cbf5f34780b13b2e1cc476ae2d": "2026-10-31T12:00:23.391105+00:00",
            "6de109e0fe7740b2b30a87796d0d9ee7": "2026-10-31T12:00:34.693949+00:00",
            "71d329a2b2b64d3388fc44117c51a5ca": "2026-10-31T12:00:10.125975+00:00",
            "753da79371ce420a9b825ad987078998": "2026-10-31T02:00:51.278109+00:00",
            "807e63ca889242c499f361939c72d888": "2026-10-31T12:03:25.023531+00:00",
            "80aa3ae817fb4149bcfe11d0d0dec63d": "2026-10-31T02:00:58.377190+00:00",
            "8d494673211244b8ab8df9de570e4c56": "2026-10-31T02:02:00.640497+00:00",
            "8d793ddb03ea463a87a30973fb4d3c2a": "2026-10-31T12:00:24.468630+00:00",
            "8e8122853f9c409a872539fe27fd72ee": "2026-10-31T12:00:28.464210+00:00",
            "91d53a9576b4412487a1ddf45648345e": "2026-10-31T12:00:20.111018+00:00",
            "93003e1cf0cb4b62aa34ff265e47307e": "2026-10-31T12:00:52.686120+00:00",
            "95b0254ff96343a99774b84114731c77": "2026-10-31T12:00:36.153870+00:00",
            "96e5751de0db44078e7cf95bb332e4ab": "2026-10-31T02:02:02.128853+00:00",
            "973c5c81510545ac802f37501debf357": "2026-10-31T12:00:47.957801+00:00",
            "976125d5a37f491c9289230d44ecb46e": "2026-10-31T12:00:38.861517+00:00",
            "a2f0d6b9ceb14fdbbfccc3a09e4f4a14": "2026-10-31T12:00:55.055554+00:00",
            "aa5f16319ae34f898f3edadb92e9beb4": "2026-10-31T12:00:17.793024+00:00",
            "aa9a787d7576481fb01351f326d821ab": "2026-10-31T12:00:07.986526+00:00",
            "b32646658d624d0eb24077af1bffc126": "2026-10-31T02:01:54.634723+00:00",
            "b33d29f10d32408db8719b04d1fb95e5": "2026-10-31T12:00:17.826750+00:00",
            "cc942161c5c94abd8e636b0c5b840dd0": "2026-10-31T02:02:03.224558+00:00",
            "cebae5e5938c42a798c5f67e003c93d4": "2026-10-31T02:00:54.579261+00:00",
            "d0af69d777cc46779937f7a4da56fb53": "2026-10-31T12:00:21.200118+00:00",
            "dc3eb7d892d84e6aa30dc1f846196a60": "2026-10-31T12:00:34.666767+00:00",
            "de12656df0594002bbb4b93e849e5bbf": "2026-10-31T12:00:23.364956+00:00",
            "e2809538cfc64b86ab7039fbab0d72ac": "2026-10-31T12:00:06.814994+00:00",
            "e5acad81946448cb94a6b9b3207707c4": "2026-10-31T02:02:07.919504+00:00",
            "f01f7cc8a0d844b6bbf9f4de6a0aebcf": "2026-10-31T12:00:42.464164+00:00",
            "f24c9999851c4dd0bc6520bd8a145124": "2026-10-31T12:00:11.621238+00:00",
            "fa5974d97bb640d9ac2df93df5d602fd": "2026-10-31T02:01:53.523332+00:00",
            "fb23d6b3ea0f4057aaa59c5cc2672468": "2026-10-31T12:00:14.536216+00:00",
            "fe8cbcb9592540b28eb7e96d74409fc9": "2026-10-31T12:00:02.286987+00:00",
            "ff112088981a4dba9579c6a775370f3f": "2026-10-31T12:00:37.566147+00:00",
        },
        "nodes": frozenset({
            "029ff2882da4409baedbed4b0c54c810",  # agent_rem
            "068cf1b57c634f85b607f03322ae796a",  # agent_emilia
            "09ff9b82b67f46cdbd8c17844e65a1eb",  # agent_rika
            "0c307614a0584ec8b7e7e050a1aa4268",  # agent_asuna
            "149b5bc3af064d58b81726df17736149",  # agent_rio
            "1a6728d355464cacb8a2cc9ee1a95975",  # agent_itsuki
            "2484b1723a9e4369bb3979d949d390fc",  # agent_ichika
            "29f3699163404212bc4003efa04bf1ac",  # agent_shea
            "311439cb93fe4dc682d823694dfd75e9",  # agent_ruka
            "36e3094c1ac2476ebd8a55b914b9a6c2",  # agent_yotsuba
            "4014334b37784d149135b42f8cb990e4",  # agent_sakura
            "402bf7333bfb4166b873acb1146da5a6",  # agent_saki
            "42f4a18c1ab54ccbbe645025ae2f5ae8",  # agent_miku
            "4bee940a3ea64fe9a07665c81d15c879",  # agent_mai
            "503410c9d54144888954617361cced8e",  # agent_iroha
            "51c759ffc3a74e63aed033f065872b90",  # agent_kana
            "54d68583d28a41f8994d342749678c6e",  # agent_ruka
            "56912ffddd604aa4b3834610560c8609",  # agent_hiro
            "577234a010764c3ca32f0d126bc6b469",  # agent_shino
            "57a3a5270fe04f339d5b8ac7b9d8070d",  # agent_minami
            "59a4cd01d63f4fa2875d221e0bce92b4",  # agent_yua
            "60df9025683d46cd9c03545ab73d34e0",  # agent_rin
            "626cb717a06c4bc8bc591046a6b62404",  # agent_kaori
            "62c1de5182f64e688c17f9c9482d70d1",  # agent_aoi
            "637182b7a87a457f862ac2418bb62c57",  # agent_tsukasa
            "6466d7e5c9604b59aa01fe22363304a5",  # agent_mai
            "6938a4cbf5f34780b13b2e1cc476ae2d",  # agent_fuuka
            "6de109e0fe7740b2b30a87796d0d9ee7",  # agent_miku
            "71d329a2b2b64d3388fc44117c51a5ca",  # agent_mahiru
            "753da79371ce420a9b825ad987078998",  # agent_yua
            "807e63ca889242c499f361939c72d888",  # agent_minami
            "80aa3ae817fb4149bcfe11d0d0dec63d",  # agent_ram
            "8d494673211244b8ab8df9de570e4c56",  # agent_mami
            "8d793ddb03ea463a87a30973fb4d3c2a",  # agent_haruhi
            "8e8122853f9c409a872539fe27fd72ee",  # agent_kaguya
            "91d53a9576b4412487a1ddf45648345e",  # agent_ai
            "93003e1cf0cb4b62aa34ff265e47307e",  # agent_shizuku
            "95b0254ff96343a99774b84114731c77",  # agent_kurisu
            "96e5751de0db44078e7cf95bb332e4ab",  # agent_nagisa
            "973c5c81510545ac802f37501debf357",  # agent_sachi
            "976125d5a37f491c9289230d44ecb46e",  # agent_mashiro
            "a2f0d6b9ceb14fdbbfccc3a09e4f4a14",  # agent_yue
            "aa5f16319ae34f898f3edadb92e9beb4",  # agent_aoi
            "aa9a787d7576481fb01351f326d821ab",  # agent_rem
            "b32646658d624d0eb24077af1bffc126",  # agent_chizuru
            "b33d29f10d32408db8719b04d1fb95e5",  # agent_mahiru
            "cc942161c5c94abd8e636b0c5b840dd0",  # agent_nino
            "cebae5e5938c42a798c5f67e003c93d4",  # agent_akane
            "d0af69d777cc46779937f7a4da56fb53",  # agent_mochizuki_ai
            "d478efba133b42f48f43a9b20790fa23",  # agent_aoi
            "dc3eb7d892d84e6aa30dc1f846196a60",  # agent_kirino
            "de12656df0594002bbb4b93e849e5bbf",  # agent_anna
            "e2809538cfc64b86ab7039fbab0d72ac",  # agent_akane
            "e5acad81946448cb94a6b9b3207707c4",  # agent_shino
            "e71cc0d9e58d4049b989ee9c0e905827",  # agent_mami
            "f01f7cc8a0d844b6bbf9f4de6a0aebcf",  # agent_reze
            "f24c9999851c4dd0bc6520bd8a145124",  # agent_anna
            "fa5974d97bb640d9ac2df93df5d602fd",  # agent_beatrice
            "fb23d6b3ea0f4057aaa59c5cc2672468",  # agent_ram
            "fe8cbcb9592540b28eb7e96d74409fc9",  # agent_yua
            "ff112088981a4dba9579c6a775370f3f",  # agent_makima
        }),
    },
        {
        "date": "2026-10-25",
        "not_before": "2026-11-02T02:00:00+00:00",
        "bounds": {
            "09c71aa58cc44e05b25a18c6a9596224": "2026-11-01T12:00:55.598469+00:00",
            "0e529801f5934c2ba677a9730cf30f78": "2026-11-01T02:02:45.200812+00:00",
            "10299061190c423bba68b6abc0d7176b": "2026-11-01T12:00:47.386070+00:00",
            "18d364aa36024e23ab09201ca97cbb16": "2026-11-01T12:00:54.139382+00:00",
            "1d702a43aad64f7dbe5a916f16ffc093": "2026-11-01T12:00:31.884624+00:00",
            "1e3bbe7519064153a8351d603c5bd70c": "2026-11-01T12:01:26.302581+00:00",
            "1f765268aae94692a639e206f98c2189": "2026-11-01T12:01:16.660663+00:00",
            "2b17043395ab40a5b1c5c7110cd14e9e": "2026-11-01T02:02:23.472817+00:00",
            "3098cfcdaa8c4e92bb1531a462f12892": "2026-11-01T12:00:51.119550+00:00",
            "36a215fc302644508f0e1e6bb45602b5": "2026-11-01T12:18:41.054919+00:00",
            "3ad4dff5be3243fc94988e5525570aa1": "2026-11-01T12:00:52.710471+00:00",
            "3d43520e22d54442a4e51c9246a36056": "2026-11-01T12:01:08.057419+00:00",
            "3dd101fd398b4951af4de627b3c22b9c": "2026-11-01T12:00:23.604510+00:00",
            "45ef23d0b9f9496582275b0d7e4727f7": "2026-11-01T02:02:26.343944+00:00",
            "4632818ecaa1451c80e2fe8d47e090d1": "2026-11-01T12:01:00.469612+00:00",
            "4bf82edbe66e409d878a20f279e3cb83": "2026-11-01T12:00:20.558927+00:00",
            "508691f8cb004a9a8d5d992b5bc8b16b": "2026-11-01T12:01:11.454301+00:00",
            "53f0941e71124800a07c89d2d1d39073": "2026-11-01T12:00:24.875024+00:00",
            "54d1bb4641d6470e9108802eb6f509e3": "2026-11-01T02:02:31.463323+00:00",
            "562da36870304901b1678d55bf12eb89": "2026-11-01T02:02:47.867211+00:00",
            "56c48338fedf499a898976e80b276c26": "2026-11-01T12:00:28.119555+00:00",
            "5c378fe6744144059e42983659d54ed5": "2026-11-01T12:01:12.697800+00:00",
            "5f2a73d780b64828a08d80704fbf6a43": "2026-11-01T12:00:42.387158+00:00",
            "64325480fb4142ac9f71f4626518368d": "2026-11-01T12:18:41.187151+00:00",
            "6af32e9f66a74d8ea2b1eb7efb71451f": "2026-11-01T12:00:26.342652+00:00",
            "6b84b711d59d400c84d98c4d874dcb79": "2026-11-01T02:02:18.384591+00:00",
            "6eb5f9fe2838440eb8d19ee4c8fc9b8b": "2026-11-01T12:00:22.445311+00:00",
            "71d2a537da2d4aac9b99b1775cf73480": "2026-11-01T02:02:37.779332+00:00",
            "749fc75cfab7426d93b152da460bc8cb": "2026-11-01T12:00:49.855497+00:00",
            "76af1963e27a4f3fadbf34036aa7a1fc": "2026-11-01T12:00:43.627902+00:00",
            "794eb4bbf4274ba088b2565e278f50ba": "2026-11-01T12:00:35.598805+00:00",
            "79895f76e6974bc8b66a13cdba0d2be3": "2026-11-01T12:01:14.048235+00:00",
            "8355312eb8dd4287b6cd160d224105a2": "2026-11-01T12:01:01.942992+00:00",
            "8b7f652769f74b0494293c2cae70257d": "2026-11-01T02:02:43.885939+00:00",
            "8b9efdc2bb4c4b1899ebb896bb492632": "2026-11-01T12:01:03.731831+00:00",
            "951d0c15a6e64980ada70762e7f72b38": "2026-11-01T12:00:35.544253+00:00",
            "9744adc262b54fd19fd9096e1d4121da": "2026-11-01T12:00:56.840217+00:00",
            "a38e2c8796374456aea2ccc9d75555cb": "2026-11-01T12:00:42.412716+00:00",
            "a8eba6894291418c9766cba104183cb2": "2026-11-01T12:00:37.312340+00:00",
            "ab3e42207be74138a939e9a343ada635": "2026-11-01T12:00:59.319473+00:00",
            "af3c7ff2cddd4f51a1ffdc013eb223ee": "2026-11-01T02:02:42.311200+00:00",
            "b0a1690de9f6480a9ee2d86c8a3caddd": "2026-11-01T12:01:06.536003+00:00",
            "b2c0a39cd50548f7a066427c340df0e3": "2026-11-01T12:01:09.521537+00:00",
            "bd3f6189e5a94cb1b3124cc5deb83c6d": "2026-11-01T12:00:51.094188+00:00",
            "c04da567cf1c4c24ada5e59e45ba8568": "2026-11-01T12:00:22.373754+00:00",
            "c3b2c9f7553e40479b7c35099c636e1a": "2026-11-01T12:00:28.139708+00:00",
            "c4373ede472d4d389a27f48f4a1a597b": "2026-11-01T12:18:41.106354+00:00",
            "c6fe3abd5e3141d58ec92968be1c451d": "2026-11-01T02:02:25.172027+00:00",
            "c9e4661a9d724eaf93092bdd15c852f4": "2026-11-01T12:01:06.493006+00:00",
            "ca1c549638dd4411aca9a16b653e1666": "2026-11-01T12:01:35.102395+00:00",
            "ca57e6819f604b2da487e659989942cb": "2026-11-01T12:01:09.479947+00:00",
            "d24984280011439d8923050c3078f549": "2026-11-01T12:01:16.748953+00:00",
            "d34e42aafb184e669a5bd129b3a48ee2": "2026-11-01T12:00:33.153685+00:00",
            "e461c1449cc94b80b6ec2169560a8c66": "2026-11-01T12:00:46.084699+00:00",
            "f844dc4a6f2c46479fef90c099cb3046": "2026-11-01T12:00:55.640605+00:00",
            "fa243a4f6db14fbfb46a9a07420f12f4": "2026-11-01T12:00:58.089646+00:00",
            "fc1fc5f5b1f847b6bb3d754437f2188a": "2026-11-01T12:00:44.873155+00:00",
            "fd4412d7218746ff8e6238c7135c4ffb": "2026-11-01T12:00:19.617126+00:00",
        },
        "nodes": frozenset({
            "09c71aa58cc44e05b25a18c6a9596224",  # agent_anna
            "0e529801f5934c2ba677a9730cf30f78",  # agent_saki
            "10299061190c423bba68b6abc0d7176b",  # agent_hiro
            "18d364aa36024e23ab09201ca97cbb16",  # agent_kaori
            "1d702a43aad64f7dbe5a916f16ffc093",  # agent_mai
            "1e3bbe7519064153a8351d603c5bd70c",  # agent_mami
            "1f765268aae94692a639e206f98c2189",  # agent_aoi
            "2b17043395ab40a5b1c5c7110cd14e9e",  # agent_chizuru
            "3098cfcdaa8c4e92bb1531a462f12892",  # agent_mahiru
            "36a215fc302644508f0e1e6bb45602b5",  # agent_nagisa
            "3ad4dff5be3243fc94988e5525570aa1",  # agent_kana
            "3d43520e22d54442a4e51c9246a36056",  # agent_sachi
            "3dd101fd398b4951af4de627b3c22b9c",  # agent_rem
            "45ef23d0b9f9496582275b0d7e4727f7",  # agent_ichika
            "4632818ecaa1451c80e2fe8d47e090d1",  # agent_minami
            "4bf82edbe66e409d878a20f279e3cb83",  # agent_ruka
            "508691f8cb004a9a8d5d992b5bc8b16b",  # agent_shea
            "53f0941e71124800a07c89d2d1d39073",  # agent_ram
            "54d1bb4641d6470e9108802eb6f509e3",  # agent_itsuki
            "562da36870304901b1678d55bf12eb89",  # agent_yotsuba
            "56c48338fedf499a898976e80b276c26",  # agent_akane
            "5c378fe6744144059e42983659d54ed5",  # agent_shizuku
            "5f2a73d780b64828a08d80704fbf6a43",  # agent_mochizuki_ai
            "64325480fb4142ac9f71f4626518368d",  # agent_shino
            "6af32e9f66a74d8ea2b1eb7efb71451f",  # agent_mahiru
            "6b84b711d59d400c84d98c4d874dcb79",  # agent_beatrice
            "6eb5f9fe2838440eb8d19ee4c8fc9b8b",  # agent_ruka
            "71d2a537da2d4aac9b99b1775cf73480",  # agent_nagisa
            "749fc75cfab7426d93b152da460bc8cb",  # agent_iroha
            "76af1963e27a4f3fadbf34036aa7a1fc",  # agent_asuna
            "794eb4bbf4274ba088b2565e278f50ba",  # agent_rem
            "79895f76e6974bc8b66a13cdba0d2be3",  # agent_tsukasa
            "8355312eb8dd4287b6cd160d224105a2",  # agent_reze
            "8b7f652769f74b0494293c2cae70257d",  # agent_rika
            "8b9efdc2bb4c4b1899ebb896bb492632",  # agent_rin
            "951d0c15a6e64980ada70762e7f72b38",  # agent_aoi
            "9744adc262b54fd19fd9096e1d4121da",  # agent_kurisu
            "a38e2c8796374456aea2ccc9d75555cb",  # agent_ram
            "a8eba6894291418c9766cba104183cb2",  # agent_ai
            "ab3e42207be74138a939e9a343ada635",  # agent_mashiro
            "af3c7ff2cddd4f51a1ffdc013eb223ee",  # agent_nino
            "b0a1690de9f6480a9ee2d86c8a3caddd",  # agent_rio
            "b2c0a39cd50548f7a066427c340df0e3",  # agent_sakura
            "bd3f6189e5a94cb1b3124cc5deb83c6d",  # agent_kaguya
            "c04da567cf1c4c24ada5e59e45ba8568",  # agent_akane
            "c3b2c9f7553e40479b7c35099c636e1a",  # agent_anna
            "c4373ede472d4d389a27f48f4a1a597b",  # agent_rika
            "c6fe3abd5e3141d58ec92968be1c451d",  # agent_emilia
            "c9e4661a9d724eaf93092bdd15c852f4",  # agent_mai
            "ca1c549638dd4411aca9a16b653e1666",  # agent_shino
            "ca57e6819f604b2da487e659989942cb",  # agent_miku
            "d24984280011439d8923050c3078f549",  # agent_yue
            "d34e42aafb184e669a5bd129b3a48ee2",  # agent_miku
            "e461c1449cc94b80b6ec2169560a8c66",  # agent_haruhi
            "f844dc4a6f2c46479fef90c099cb3046",  # agent_kirino
            "fa243a4f6db14fbfb46a9a07420f12f4",  # agent_makima
            "fc1fc5f5b1f847b6bb3d754437f2188a",  # agent_fuuka
            "fd4412d7218746ff8e6238c7135c4ffb",  # agent_yua
        }),
    },
        {
        "date": "2026-10-26",
        "not_before": "2026-11-03T02:00:00+00:00",
        "bounds": {
            "009b435f55034f15950c29e310474d85": "2026-11-02T12:01:56.624267+00:00",
            "019e44e71980436595c054830d292659": "2026-11-02T12:01:25.931898+00:00",
            "05a620145730455da68a86df65790c04": "2026-11-02T12:01:20.359229+00:00",
            "11af7cb8447f4208b98352341e0878e8": "2026-11-02T12:00:48.389712+00:00",
            "1815f94b359a47ae807781902f386c95": "2026-11-02T12:01:29.394861+00:00",
            "26cedfd48c88476fad5955f5bf10339e": "2026-11-02T02:01:44.137173+00:00",
            "2a35bddd4c7f40e89050bb053f4f34d3": "2026-11-02T12:00:34.214661+00:00",
            "30d40df310c74b8e91f5282c9dcb29fd": "2026-11-02T12:01:37.283897+00:00",
            "31ac1ca97f4e45aa8235b0c75282e882": "2026-11-02T12:00:46.455500+00:00",
            "3aec608dc31d466c9c0d66fa2e93530f": "2026-11-02T12:01:05.764872+00:00",
            "3e7aef3e71e041f992338c27c6032b60": "2026-11-02T02:01:20.555145+00:00",
            "3ed7a663c4ab4c49a581a10811556792": "2026-11-02T12:00:54.826445+00:00",
            "4034513ee577440aa40799682c1fbf3e": "2026-11-02T02:01:07.096257+00:00",
            "48aaa8aba73344079a39e965379e587c": "2026-11-02T12:01:01.854042+00:00",
            "4e6ae50609da4212b9909430eec2df8c": "2026-11-02T12:01:35.591709+00:00",
            "59777d94b38a451b9b951b0a44afbde7": "2026-11-02T12:01:27.972040+00:00",
            "5eb4e011129d4c3495bfa43e3bdf9899": "2026-11-02T12:01:04.231254+00:00",
            "5fabbf8030dc49b7bad7d5b86b76392c": "2026-11-02T12:01:24.292747+00:00",
            "628224704c114a50b5fd01d0792cf1cf": "2026-11-02T02:01:01.175433+00:00",
            "6eb1b0ae9ca8412a85c10cc767dd12ea": "2026-11-02T12:01:18.451379+00:00",
            "78894255019248c0b6924bcc687d0579": "2026-11-02T12:00:58.252203+00:00",
            "78e945cc688e45b4a96ea0cd3f105a69": "2026-11-02T12:00:41.316154+00:00",
            "79a2f0646f4e401e8ccf2c2bac46a657": "2026-11-02T12:01:00.075006+00:00",
            "80424fbb3df04e80b52720776265cf62": "2026-11-02T02:01:45.505564+00:00",
            "849ac1fb43b94e9abe52d1fc33c2d170": "2026-11-02T02:18:52.011347+00:00",
            "8a599a3094e3472cbdb1c2ff26fd9631": "2026-11-02T02:01:42.782160+00:00",
            "9afbede1c6dc44ac8322f1351ec1fe32": "2026-11-02T12:01:10.439813+00:00",
            "9b2013d7af3b4287a90b9c49096069a4": "2026-11-02T12:01:49.725829+00:00",
            "9f19bc7d30e8430da43bc83914aa6d4a": "2026-11-02T12:01:07.424575+00:00",
            "a2ab1df0a19f4c35a007a6d4d9283032": "2026-11-02T12:00:51.975894+00:00",
            "ac27ba3e85a849daae4dfaf137b47749": "2026-11-02T12:00:53.497444+00:00",
            "b3a52413a0fe43349ff819fcbfd12d9b": "2026-11-02T12:00:31.190101+00:00",
            "b3cf2e691a6e4ea4974954cf36344187": "2026-11-02T12:01:13.731786+00:00",
            "b63e6d49c6544a4dbf18c1a1893e56e8": "2026-11-02T12:00:28.313112+00:00",
            "c88f55acc11e48c99ff3d0214b28ec5a": "2026-11-02T02:01:40.356713+00:00",
            "caf74e05fd0141288921da911f4f13c7": "2026-11-02T02:01:48.704071+00:00",
            "d4ec11238f7b49638a8112d551bba04e": "2026-11-02T12:01:17.327919+00:00",
            "da602f46d5784deab42a5e641c578fa3": "2026-11-02T12:01:53.138338+00:00",
            "e16a775c926448ef91c64d3ca7893ff6": "2026-11-02T02:01:51.220575+00:00",
            "e63dc8b15f4545baa6fb2db1b9ebf7c7": "2026-11-02T02:01:46.622242+00:00",
            "e6ad2f93f44544cda6eefd61274791de": "2026-11-02T02:01:11.599299+00:00",
            "e91e41779c1b4d26a3987c90a9b5449e": "2026-11-02T12:01:47.756628+00:00",
            "ef8c220ffe924f7590638b35497a5453": "2026-11-02T12:01:34.113289+00:00",
            "f410ef169e4d4e6fb6df13eaff8b6aad": "2026-11-02T02:01:53.722868+00:00",
            "fa8a1325a9754a098b1f75ee8c950dc9": "2026-11-02T02:01:56.690507+00:00",
            "fe375a48d8574aa092ae4c1e33136fb8": "2026-11-02T12:01:15.442082+00:00",
        },
        "nodes": frozenset({
            "009b435f55034f15950c29e310474d85",  # agent_shino
            "019e44e71980436595c054830d292659",  # agent_sachi
            "05a620145730455da68a86df65790c04",  # agent_rin
            "11af7cb8447f4208b98352341e0878e8",  # agent_miku
            "1815f94b359a47ae807781902f386c95",  # agent_shea
            "26cedfd48c88476fad5955f5bf10339e",  # agent_emilia
            "2a35bddd4c7f40e89050bb053f4f34d3",  # agent_akane
            "30d40df310c74b8e91f5282c9dcb29fd",  # agent_yue
            "31ac1ca97f4e45aa8235b0c75282e882",  # agent_mai
            "3aec608dc31d466c9c0d66fa2e93530f",  # agent_kaguya
            "3e7aef3e71e041f992338c27c6032b60",  # agent_kaori
            "3ed7a663c4ab4c49a581a10811556792",  # agent_mochizuki_ai
            "4034513ee577440aa40799682c1fbf3e",  # agent_mahiru
            "48aaa8aba73344079a39e965379e587c",  # agent_hiro
            "4e6ae50609da4212b9909430eec2df8c",  # agent_tsukasa
            "59777d94b38a451b9b951b0a44afbde7",  # agent_sakura
            "5eb4e011129d4c3495bfa43e3bdf9899",  # agent_iroha
            "5fabbf8030dc49b7bad7d5b86b76392c",  # agent_rio
            "628224704c114a50b5fd01d0792cf1cf",  # agent_rem
            "6eb1b0ae9ca8412a85c10cc767dd12ea",  # agent_reze
            "78894255019248c0b6924bcc687d0579",  # agent_fuuka
            "78e945cc688e45b4a96ea0cd3f105a69",  # agent_ram
            "79a2f0646f4e401e8ccf2c2bac46a657",  # agent_haruhi
            "80424fbb3df04e80b52720776265cf62",  # agent_ichika
            "849ac1fb43b94e9abe52d1fc33c2d170",  # agent_kurisu
            "8a599a3094e3472cbdb1c2ff26fd9631",  # agent_chizuru
            "9afbede1c6dc44ac8322f1351ec1fe32",  # agent_kirino
            "9b2013d7af3b4287a90b9c49096069a4",  # agent_nagisa
            "9f19bc7d30e8430da43bc83914aa6d4a",  # agent_kana
            "a2ab1df0a19f4c35a007a6d4d9283032",  # agent_aoi
            "ac27ba3e85a849daae4dfaf137b47749",  # agent_ai
            "b3a52413a0fe43349ff819fcbfd12d9b",  # agent_ruka
            "b3cf2e691a6e4ea4974954cf36344187",  # agent_makima
            "b63e6d49c6544a4dbf18c1a1893e56e8",  # agent_yua
            "c88f55acc11e48c99ff3d0214b28ec5a",  # agent_beatrice
            "caf74e05fd0141288921da911f4f13c7",  # agent_asuna
            "d4ec11238f7b49638a8112d551bba04e",  # agent_minami
            "da602f46d5784deab42a5e641c578fa3",  # agent_rika
            "e16a775c926448ef91c64d3ca7893ff6",  # agent_nino
            "e63dc8b15f4545baa6fb2db1b9ebf7c7",  # agent_itsuki
            "e6ad2f93f44544cda6eefd61274791de",  # agent_anna
            "e91e41779c1b4d26a3987c90a9b5449e",  # agent_mami
            "ef8c220ffe924f7590638b35497a5453",  # agent_shizuku
            "f410ef169e4d4e6fb6df13eaff8b6aad",  # agent_saki
            "fa8a1325a9754a098b1f75ee8c950dc9",  # agent_yotsuba
            "fe375a48d8574aa092ae4c1e33136fb8",  # agent_mashiro
        }),
    },
    {
        "date": "2026-10-27",
        "not_before": "2026-11-04T02:00:00+00:00",
        "bounds": {
            "04aceb9fd6b2497aabb8924a457eefac": "2026-11-03T02:02:06.052374+00:00",
            "06f6d4e7eb0d4a7b9346883f82531d6d": "2026-11-03T02:01:53.417779+00:00",
            "0840f9f9607540b5ac8635b79daf93d7": "2026-11-03T12:00:46.814209+00:00",
            "0b3066d97df94b8b93eba47c0eff9bf8": "2026-11-03T12:01:34.311830+00:00",
            "0c5c22c7a5bc4e2188013654e03dcfeb": "2026-11-03T02:01:37.243946+00:00",
            "14e3330e92794e6aaf0aa2f4ef954b3b": "2026-11-03T02:01:25.938139+00:00",
            "15297f228a3b42f8bba7428c7808923f": "2026-11-03T12:00:40.689606+00:00",
            "1fbdfb1d49f3435cbd8b41cac66bde06": "2026-11-03T12:00:27.222658+00:00",
            "2188fa8a41f94743a2497911680b07da": "2026-11-03T12:01:26.553927+00:00",
            "21f41aad01ce48eb9e208f17eb18db0a": "2026-11-03T12:01:30.593026+00:00",
            "2346bf4bde6f44859ee9ca0616151f85": "2026-11-03T12:01:24.162255+00:00",
            "240ffc4c8c344fad98e353b412765521": "2026-11-03T12:19:18.672622+00:00",
            "294b80a5e9e3486e9b49cc954d77a442": "2026-11-03T12:01:07.722137+00:00",
            "2c3d2fef54924b21a8ad08510fa634c0": "2026-11-03T02:02:09.509568+00:00",
            "32bfb5b3fecd48dc960e9bce8c5a98ef": "2026-11-03T02:01:30.087771+00:00",
            "3657f1f41f1a4367bc2696b241159259": "2026-11-03T12:02:02.646209+00:00",
            "418ca0c009aa470f917882896e0c564f": "2026-11-03T12:01:03.280031+00:00",
            "46036ecb5b754f68bf9ea5be808a58b3": "2026-11-03T12:01:49.872672+00:00",
            "46c36efea8224091b06a638ed645fcbb": "2026-11-03T12:01:19.933907+00:00",
            "4d674a1ac791495d8f9f62c9a035f70c": "2026-11-03T12:19:18.878492+00:00",
            "500d161545cd48739c342e07e01d0d4a": "2026-11-03T02:01:55.140072+00:00",
            "573a155c06e542a081b43bef66e0cfa0": "2026-11-03T02:01:48.802939+00:00",
            "5adfbe8f2d40414b8a94aae4b5b88781": "2026-11-03T12:01:18.183461+00:00",
            "5dcee1aab1354dbf836a34169eeef68d": "2026-11-03T02:02:02.483629+00:00",
            "61b6b03714e24f08b8039223cf588ef3": "2026-11-03T12:00:59.906765+00:00",
            "7a9b81d3e44444ff9c01a9138616200a": "2026-11-03T02:01:22.805168+00:00",
            "87f57d6333484987b855e2d8bbfde487": "2026-11-03T12:00:48.963342+00:00",
            "890604bfd99c45a08d9c270217960168": "2026-11-03T12:01:31.827551+00:00",
            "8d2a2346a48d4fba9ede4408ab5ae142": "2026-11-03T12:00:51.404363+00:00",
            "91e0393ae8044799930fe504ad194870": "2026-11-03T02:01:42.894187+00:00",
            "9288812603bf4a259fe74bf92774d6b2": "2026-11-03T12:19:18.722815+00:00",
            "9a55fe64db5d488d85c9662d2341c4cd": "2026-11-03T02:00:58.876083+00:00",
            "ab4f5aae592541d0bbf0c101d821e8fb": "2026-11-03T12:01:04.754636+00:00",
            "adf77cde7e154e239fdca1ffba95fe60": "2026-11-03T12:00:53.252694+00:00",
            "ae37a6edff7f41c5865fd5c268be15e6": "2026-11-03T12:00:55.238559+00:00",
            "b4785b3ff4024631b7f097f3febd8ed8": "2026-11-03T12:01:51.068845+00:00",
            "bf172f5937594bd097c8e9df5ec26723": "2026-11-03T12:01:06.204732+00:00",
            "bfc384ae03e047c4af5306fd308bc0fb": "2026-11-03T12:01:37.438791+00:00",
            "c198c1ce633e438ba519d4b01a0ea08b": "2026-11-03T12:01:27.854871+00:00",
            "c3ea0985daa64756bd0791434baf6e4d": "2026-11-03T02:01:51.415169+00:00",
            "cd8335c64da1487683bc4bd1542f9ff9": "2026-11-03T12:00:31.808414+00:00",
            "d0561be148f446dc8687426c3513cf4d": "2026-11-03T02:01:11.324607+00:00",
            "d8c0847e1d874ba095057bc7a51e47c6": "2026-11-03T12:00:28.857715+00:00",
            "e0c9a22a0ee9446db952266c6f74cf07": "2026-11-03T12:01:14.825884+00:00",
            "f0168fc3b07e400c8b0c0741d68e972d": "2026-11-03T02:01:56.775549+00:00",
            "f073c251689e4051b6b83cda0669ab6a": "2026-11-03T12:01:58.181701+00:00",
            "f36183ac4be3437a89ad2992070d0416": "2026-11-03T12:00:51.358521+00:00",
            "f472d095f0014f75be2e7ddd5f01471c": "2026-11-03T02:01:00.818908+00:00",
            "f99f5ccadf34418ea29376935f401cf7": "2026-11-03T02:00:55.934194+00:00",
            "feb68b5f83a9409380eb6f3dfcd37380": "2026-11-03T12:01:36.240165+00:00",
            "ff2fd3fbbc9d45a8b3d5a051f32e49d9": "2026-11-03T12:01:10.053116+00:00",
        },
        "nodes": frozenset({
            "04aceb9fd6b2497aabb8924a457eefac",  # agent_saki
            "06f6d4e7eb0d4a7b9346883f82531d6d",  # agent_emilia
            "0840f9f9607540b5ac8635b79daf93d7",  # agent_mai
            "0b3066d97df94b8b93eba47c0eff9bf8",  # agent_asuna
            "0c5c22c7a5bc4e2188013654e03dcfeb",  # agent_sachi
            "14e3330e92794e6aaf0aa2f4ef954b3b",  # agent_kurisu
            "15297f228a3b42f8bba7428c7808923f",  # agent_ram
            "1fbdfb1d49f3435cbd8b41cac66bde06",  # agent_yua
            "2188fa8a41f94743a2497911680b07da",  # agent_rin
            "21f41aad01ce48eb9e208f17eb18db0a",  # agent_sakura
            "2346bf4bde6f44859ee9ca0616151f85",  # agent_reze
            "240ffc4c8c344fad98e353b412765521",  # agent_emilia
            "294b80a5e9e3486e9b49cc954d77a442",  # agent_kaguya
            "2c3d2fef54924b21a8ad08510fa634c0",  # agent_yotsuba
            "32bfb5b3fecd48dc960e9bce8c5a98ef",  # agent_minami
            "3657f1f41f1a4367bc2696b241159259",  # agent_shino
            "418ca0c009aa470f917882896e0c564f",  # agent_haruhi
            "46036ecb5b754f68bf9ea5be808a58b3",  # agent_mami
            "46c36efea8224091b06a638ed645fcbb",  # agent_mashiro
            "4d674a1ac791495d8f9f62c9a035f70c",  # agent_shizuku
            "500d161545cd48739c342e07e01d0d4a",  # agent_ichika
            "573a155c06e542a081b43bef66e0cfa0",  # agent_beatrice
            "5adfbe8f2d40414b8a94aae4b5b88781",  # agent_makima
            "5dcee1aab1354dbf836a34169eeef68d",  # agent_nino
            "61b6b03714e24f08b8039223cf588ef3",  # agent_fuuka
            "7a9b81d3e44444ff9c01a9138616200a",  # agent_kaori
            "87f57d6333484987b855e2d8bbfde487",  # agent_miku
            "890604bfd99c45a08d9c270217960168",  # agent_shea
            "8d2a2346a48d4fba9ede4408ab5ae142",  # agent_mahiru
            "91e0393ae8044799930fe504ad194870",  # agent_shizuku
            "9288812603bf4a259fe74bf92774d6b2",  # agent_itsuki
            "9a55fe64db5d488d85c9662d2341c4cd",  # agent_mahiru
            "ab4f5aae592541d0bbf0c101d821e8fb",  # agent_hiro
            "adf77cde7e154e239fdca1ffba95fe60",  # agent_ai
            "ae37a6edff7f41c5865fd5c268be15e6",  # agent_mochizuki_ai
            "b4785b3ff4024631b7f097f3febd8ed8",  # agent_nagisa
            "bf172f5937594bd097c8e9df5ec26723",  # agent_iroha
            "bfc384ae03e047c4af5306fd308bc0fb",  # agent_yue
            "c198c1ce633e438ba519d4b01a0ea08b",  # agent_rio
            "c3ea0985daa64756bd0791434baf6e4d",  # agent_chizuru
            "cd8335c64da1487683bc4bd1542f9ff9",  # agent_akane
            "d0561be148f446dc8687426c3513cf4d",  # agent_asuna
            "d8c0847e1d874ba095057bc7a51e47c6",  # agent_ruka
            "e0c9a22a0ee9446db952266c6f74cf07",  # agent_kirino
            "f0168fc3b07e400c8b0c0741d68e972d",  # agent_itsuki
            "f073c251689e4051b6b83cda0669ab6a",  # agent_rika
            "f36183ac4be3437a89ad2992070d0416",  # agent_aoi
            "f472d095f0014f75be2e7ddd5f01471c",  # agent_anna
            "f99f5ccadf34418ea29376935f401cf7",  # agent_rem
            "feb68b5f83a9409380eb6f3dfcd37380",  # agent_tsukasa
            "ff2fd3fbbc9d45a8b3d5a051f32e49d9",  # agent_kana
        }),
    },
    {
        "date": "2026-10-28",
        "not_before": "2026-11-05T02:00:00+00:00",
        "bounds": {
            "0cb9c8688b41484d8eb75d6c1c4d3de0": "2026-11-04T19:55:15.610923+00:00",
            "19fe657390014686891dac021a9dd24b": "2026-11-04T12:01:47.115044+00:00",
            "1a829a3c8a434f159e5d6f525d349e66": "2026-11-04T02:01:54.434245+00:00",
            "219c35267bfd4d54aa1e314d08cba03d": "2026-11-04T12:01:34.152574+00:00",
            "22d07048b8cf4ff984755206f41113f9": "2026-11-04T12:00:58.001804+00:00",
            "238d7615e1484fec8d458ca4734f0434": "2026-11-04T02:02:09.849823+00:00",
            "2bd763d61ea34fc5878dedd64c73da84": "2026-11-04T12:01:28.703107+00:00",
            "2cd98b74f1414a55b2d3b24ffbf16b14": "2026-11-04T12:01:09.812814+00:00",
            "3453501501fa4c27be7e0d8abef50ca0": "2026-11-04T12:00:51.387051+00:00",
            "34a2f763b69a4462ac0af5166bf2b949": "2026-11-04T12:00:55.552391+00:00",
            "37fbc9670933400a8c6d01d9f6090c88": "2026-11-04T02:02:02.288696+00:00",
            "3afce1aca7d94dc4a3011f996bbc1efd": "2026-11-04T19:54:54.487529+00:00",
            "3fdde4265d2c4091a8fcef47c0a70424": "2026-11-04T12:01:30.331063+00:00",
            "40b428a31f464894ac6d6bcc8e369168": "2026-11-04T12:01:15.503539+00:00",
            "40daeccc99b645d0a25285dea91a9c18": "2026-11-04T02:00:50.261471+00:00",
            "444633e11ad5447591837735ba0c9c0b": "2026-11-04T19:55:05.778091+00:00",
            "470c96514e0342b7b954af4c2e6caf5c": "2026-11-04T12:01:06.409288+00:00",
            "4ae376d34d064a34bde0005a0d68ec20": "2026-11-04T12:02:04.350486+00:00",
            "4b5208a35fa14aa482bc9dd995b153e9": "2026-11-04T19:55:07.454415+00:00",
            "4c125ca25ce64c369ff433f55af5bed9": "2026-11-04T02:02:05.630891+00:00",
            "4dec72548ecb4dff9015c7e9b8b07a03": "2026-11-04T12:01:58.727302+00:00",
            "51ef9574c90a4640bf45bb72b88046ba": "2026-11-04T12:01:53.885036+00:00",
            "52379b20611a4b2e98df1cb89496e7da": "2026-11-04T02:01:35.818601+00:00",
            "5475a4f2ef2f4bf693c03dd204739e61": "2026-11-04T02:01:16.786354+00:00",
            "555c14cdf39b41429746c9b5fdc8694b": "2026-11-04T02:00:37.014282+00:00",
            "5b0d4d41ff4b45ae91a7d2969e6a3d42": "2026-11-04T12:00:41.696748+00:00",
            "5b7d469d2cfb4dcc8ddf8666a5afd4bd": "2026-11-04T19:54:49.008148+00:00",
            "5d6564a07e4749e4a3c6df2e3e955ef9": "2026-11-04T12:01:56.925152+00:00",
            "655e4df3e1854cd6bfd7eeb96a19bff2": "2026-11-04T12:01:07.763498+00:00",
            "66452f451f5b4885aa0514d9b217776a": "2026-11-04T19:55:31.467240+00:00",
            "680304cf9f1a4e99a14729e15f244f28": "2026-11-04T02:01:50.473078+00:00",
            "69261365288d41798a647ea4bc7548e4": "2026-11-04T12:01:37.147831+00:00",
            "695174a2f99f43df8ce622242b3e6274": "2026-11-04T02:19:30.694852+00:00",
            "6a029e46669b42b1b5501ac0f27878d1": "2026-11-04T12:02:00.699368+00:00",
            "6f28a24f1adf488baaa9f9aab225249a": "2026-11-04T12:01:13.758826+00:00",
            "78cbcd98569c4975add4df6cdc75b6aa": "2026-11-04T12:19:38.810555+00:00",
            "7b6f39b2b037423283850e40024c1e4e": "2026-11-04T19:55:38.391965+00:00",
            "7d521bce2049474ab5738b9a65f327b3": "2026-11-04T19:55:11.492890+00:00",
            "7e6c50e6e15a41549aeb45317588999d": "2026-11-04T19:54:53.749594+00:00",
            "9078366a5d454936bb2a1b2b7bb25b19": "2026-11-04T12:01:17.520004+00:00",
            "918621789e564b1b9fbde71fb1209fce": "2026-11-04T19:55:30.287710+00:00",
            "91b7841a6a894714a9d1c8ff82d2162d": "2026-11-04T12:01:35.780753+00:00",
            "92b7f638a44845ef9edca4bfa1ebbe62": "2026-11-04T12:01:44.220858+00:00",
            "92cdccaedd004b6b9cfa7f8c2e38303f": "2026-11-04T19:55:37.549170+00:00",
            "9fee8502bde543caaa022046a344cb73": "2026-11-04T12:00:32.572915+00:00",
            "a0fbb6d60d5b4f65a5d0c0b526d0bcd1": "2026-11-04T12:01:19.239584+00:00",
            "a71f3a2241dc453aaa302e5a24e6fea7": "2026-11-04T12:01:42.563533+00:00",
            "af25d837a8bb4edd84da24bb941d4b78": "2026-11-04T19:55:19.720389+00:00",
            "b636d9b2c8e7440c88126553e527f431": "2026-11-04T12:02:07.234072+00:00",
            "cb1dc3c207b8463b81f7ec10dde96005": "2026-11-04T12:01:00.518219+00:00",
            "cb8dcae24ec34ec7ab530d3239df713e": "2026-11-04T12:01:21.370947+00:00",
            "d52845a0ac5e4584bb24d04834eb5e16": "2026-11-04T02:01:46.391599+00:00",
            "d5441bf8f7cd40609062c6dcca64b0bc": "2026-11-04T12:01:11.238615+00:00",
            "dd996c36e77a42b9b74f83178bee33f9": "2026-11-04T19:54:59.899284+00:00",
            "e0028ff0c8164231b83dc4f79ad26293": "2026-11-04T02:01:20.358346+00:00",
            "e0ae91d1554843518987064da3a451cf": "2026-11-04T12:01:40.233865+00:00",
            "e909d3df9aa643b9a3f7f4b80722322e": "2026-11-04T12:00:37.245066+00:00",
            "eba6758cc33e4b4f8532e7720774bf5b": "2026-11-04T02:01:25.339495+00:00",
            "f21e5ccbe9374a468ca5de7717a6b2a8": "2026-11-04T02:01:18.613509+00:00",
            "f6ab28f705564cabb0fec4a90a55831a": "2026-11-04T12:00:34.639052+00:00",
            "fb6aecdb5a41437c8c501f6d22ce4c03": "2026-11-04T12:00:51.334480+00:00",
        },
        "nodes": frozenset({
            "0cb9c8688b41484d8eb75d6c1c4d3de0",  # agent_minami
            "19fe657390014686891dac021a9dd24b",  # agent_yue
            "1a829a3c8a434f159e5d6f525d349e66",  # agent_ichika
            "219c35267bfd4d54aa1e314d08cba03d",  # agent_reze
            "22d07048b8cf4ff984755206f41113f9",  # agent_miku
            "238d7615e1484fec8d458ca4734f0434",  # agent_yotsuba
            "2bd763d61ea34fc5878dedd64c73da84",  # agent_makima
            "2cd98b74f1414a55b2d3b24ffbf16b14",  # agent_asuna
            "3453501501fa4c27be7e0d8abef50ca0",  # agent_rem
            "34a2f763b69a4462ac0af5166bf2b949",  # agent_mai
            "37fbc9670933400a8c6d01d9f6090c88",  # agent_nino
            "3afce1aca7d94dc4a3011f996bbc1efd",  # agent_chizuru
            "3fdde4265d2c4091a8fcef47c0a70424",  # agent_mashiro
            "40b428a31f464894ac6d6bcc8e369168",  # agent_hiro
            "40daeccc99b645d0a25285dea91a9c18",  # agent_anna
            "444633e11ad5447591837735ba0c9c0b",  # agent_kaori
            "470c96514e0342b7b954af4c2e6caf5c",  # agent_ai
            "4ae376d34d064a34bde0005a0d68ec20",  # agent_rika
            "4b5208a35fa14aa482bc9dd995b153e9",  # agent_kurisu
            "4c125ca25ce64c369ff433f55af5bed9",  # agent_saki
            "4dec72548ecb4dff9015c7e9b8b07a03",  # agent_mami
            "51ef9574c90a4640bf45bb72b88046ba",  # agent_emilia
            "52379b20611a4b2e98df1cb89496e7da",  # agent_sachi
            "5475a4f2ef2f4bf693c03dd204739e61",  # agent_kaori
            "555c14cdf39b41429746c9b5fdc8694b",  # agent_rem
            "5b0d4d41ff4b45ae91a7d2969e6a3d42",  # agent_ram
            "5b7d469d2cfb4dcc8ddf8666a5afd4bd",  # agent_anna
            "5d6564a07e4749e4a3c6df2e3e955ef9",  # agent_itsuki
            "655e4df3e1854cd6bfd7eeb96a19bff2",  # agent_mochizuki_ai
            "66452f451f5b4885aa0514d9b217776a",  # agent_saki
            "680304cf9f1a4e99a14729e15f244f28",  # agent_chizuru
            "69261365288d41798a647ea4bc7548e4",  # agent_rio
            "695174a2f99f43df8ce622242b3e6274",  # agent_tsukasa
            "6a029e46669b42b1b5501ac0f27878d1",  # agent_nagisa
            "6f28a24f1adf488baaa9f9aab225249a",  # agent_haruhi
            "78cbcd98569c4975add4df6cdc75b6aa",  # agent_kirino
            "7b6f39b2b037423283850e40024c1e4e",  # agent_yotsuba
            "7d521bce2049474ab5738b9a65f327b3",  # agent_mami
            "7e6c50e6e15a41549aeb45317588999d",  # agent_beatrice
            "9078366a5d454936bb2a1b2b7bb25b19",  # agent_iroha
            "918621789e564b1b9fbde71fb1209fce",  # agent_sachi
            "91b7841a6a894714a9d1c8ff82d2162d",  # agent_rin
            "92b7f638a44845ef9edca4bfa1ebbe62",  # agent_shizuku
            "92cdccaedd004b6b9cfa7f8c2e38303f",  # agent_tsukasa
            "9fee8502bde543caaa022046a344cb73",  # agent_yua
            "a0fbb6d60d5b4f65a5d0c0b526d0bcd1",  # agent_kaguya
            "a71f3a2241dc453aaa302e5a24e6fea7",  # agent_shea
            "af25d837a8bb4edd84da24bb941d4b78",  # agent_nino
            "b636d9b2c8e7440c88126553e527f431",  # agent_shino
            "cb1dc3c207b8463b81f7ec10dde96005",  # agent_aoi
            "cb8dcae24ec34ec7ab530d3239df713e",  # agent_kana
            "d52845a0ac5e4584bb24d04834eb5e16",  # agent_beatrice
            "d5441bf8f7cd40609062c6dcca64b0bc",  # agent_fuuka
            "dd996c36e77a42b9b74f83178bee33f9",  # agent_ichika
            "e0028ff0c8164231b83dc4f79ad26293",  # agent_kurisu
            "e0ae91d1554843518987064da3a451cf",  # agent_sakura
            "e909d3df9aa643b9a3f7f4b80722322e",  # agent_akane
            "eba6758cc33e4b4f8532e7720774bf5b",  # agent_minami
            "f21e5ccbe9374a468ca5de7717a6b2a8",  # agent_kirino
            "f6ab28f705564cabb0fec4a90a55831a",  # agent_ruka
            "fb6aecdb5a41437c8c501f6d22ce4c03",  # agent_mahiru
        }),
    },
    {
        "date": "2026-10-29",
        "not_before": "2026-11-06T02:00:00+00:00",
        "bounds": {
            "01f657b6892f4c5f9a2f13326056562e": "2026-11-05T12:00:28.429675+00:00",
            "031ebd75cfbe452d960e92462a67d621": "2026-11-05T02:01:31.466069+00:00",
            "0639c04b903a47b8894727e796fba63d": "2026-11-05T12:00:55.857950+00:00",
            "086e9545dedf4c1f9a5e6ebc5dddbb2f": "2026-11-05T02:01:47.838088+00:00",
            "0c3c2c6e52cd44f1adde3d73a25e7c65": "2026-11-05T02:29:18.921090+00:00",
            "1157634d1a9a4a31b8e7a5f80a48c836": "2026-11-05T02:02:00.283111+00:00",
            "12c64f890eaa4656ba977d12a7fa7833": "2026-11-05T12:01:40.514356+00:00",
            "137f1cee7b204e62885eacd5ea881307": "2026-11-05T02:02:33.517217+00:00",
            "14a4c94f4eb2496293e47c64a6992085": "2026-11-05T12:02:21.588294+00:00",
            "15e23dd0ac314e7caa5e1d56c30d6f82": "2026-11-05T12:01:49.897255+00:00",
            "166420be1093464c80d30b73125a5b00": "2026-11-05T12:01:45.142254+00:00",
            "16e9352ebcf84b0da73d1938d9640d92": "2026-11-05T02:01:58.320174+00:00",
            "1ef7aabcbe4b4abe988a677e9f67bcbb": "2026-11-05T12:02:12.538207+00:00",
            "2363a4d1bb30488dbd5991dde586c827": "2026-11-05T12:02:03.577037+00:00",
            "26a2a186d4b24a99a870cc07f1d95a5e": "2026-11-05T02:00:57.070967+00:00",
            "2724e9b290ef4ecebad832640deb8a9f": "2026-11-05T02:01:02.817840+00:00",
            "28d875e22b184b969bbceefcaec3dfe2": "2026-11-05T02:02:13.498414+00:00",
            "2914053335174586b6b21c66b003b673": "2026-11-05T12:00:59.897704+00:00",
            "3634c72894a3412cb0f047691060e295": "2026-11-05T02:01:33.859314+00:00",
            "3b12f59a46ae4a93ae6f5e3b3a6664a3": "2026-11-05T02:29:19.871759+00:00",
            "3ba3978254a1460bae615efb6164a59b": "2026-11-05T02:01:56.158625+00:00",
            "3ba8a53a7799447c8517db7add926088": "2026-11-05T12:02:26.146002+00:00",
            "3c7ac79afc1d4be4af3a784977992c35": "2026-11-05T12:01:54.631659+00:00",
            "45a27dd5c04848ffb8e6d0904a4996a1": "2026-11-05T02:01:28.128666+00:00",
            "45e2b74fa4754471a726e0eefea5377f": "2026-11-05T12:02:30.563019+00:00",
            "46a6d93e6a7a4bd5918bb74172488f97": "2026-11-05T02:02:11.813940+00:00",
            "46f3812fc6824fcab989f3d64be2daa8": "2026-11-05T12:02:28.293360+00:00",
            "48fa34d57c374565b2014b797ea86f08": "2026-11-05T02:02:19.371634+00:00",
            "499216c124ef4fe9994e9c4e13c96d7d": "2026-11-05T02:00:59.070569+00:00",
            "49b53e7e5d6e47d287d0faefd42b0280": "2026-11-05T02:01:41.031443+00:00",
            "4a37aabbf71e4b299421fdfde96b74f1": "2026-11-05T12:00:23.788298+00:00",
            "4b426737b9654f4a9fc7b9da56010693": "2026-11-05T12:00:42.064149+00:00",
            "4b6cdffa4c0f4911a1a3870be680225f": "2026-11-05T12:02:23.829956+00:00",
            "4c216fd0b5ab40ce981a875f195ea6cc": "2026-11-05T12:01:34.401304+00:00",
            "4cacab40d2dc44bfb447bc1455a8e18e": "2026-11-05T02:01:19.163431+00:00",
            "52d4097ef47145e7accd586c8af27ed5": "2026-11-05T12:00:46.702317+00:00",
            "52dac488d29c4f42b83a8debd37b8bef": "2026-11-05T12:01:21.525846+00:00",
            "565f1c9407884e9794848d219160be0d": "2026-11-05T02:29:19.515331+00:00",
            "56ac9358558643b5abf7c2bef8b7710d": "2026-11-05T12:02:17.284760+00:00",
            "574720388af2447d96defa6b640ee0ff": "2026-11-05T02:02:02.588833+00:00",
            "5af262e3c8124a649c6e1c795c4a4b7a": "2026-11-05T02:01:23.867022+00:00",
            "5c30961348344745ab0182891aa2b206": "2026-11-05T12:01:32.100367+00:00",
            "61cab369a36142fc8a9b45ff6613a6d5": "2026-11-05T12:02:15.069538+00:00",
            "68ee76999bf248e0a9e9588837d47c40": "2026-11-05T02:01:09.832963+00:00",
            "6b3a7fc4cc3a426ba67e30a98be6fe85": "2026-11-05T02:00:53.957866+00:00",
            "6cb39dd159aa40c6a556432de2e6d5e1": "2026-11-05T02:29:19.721918+00:00",
            "70fa4fb7b0204ab1876440c3b208e1c3": "2026-11-05T02:01:07.150261+00:00",
            "735bcd3f961f41bfb6ab54c5267f9b26": "2026-11-05T12:00:49.129483+00:00",
            "7521666f4c9f44ed9d93c6cf859fdeff": "2026-11-05T02:02:42.415317+00:00",
            "7592b254254b45f6a72a673b405ed879": "2026-11-05T02:01:36.683036+00:00",
            "786d87dabcef4b68885cd960c57f1929": "2026-11-05T12:01:18.315493+00:00",
            "78c4b71c09494d24b7efcd62e18879ba": "2026-11-05T02:29:19.400240+00:00",
            "799554fdfd5c44d3b2016598888247dc": "2026-11-05T02:02:07.241420+00:00",
            "7ba6aaa4d4b64eafa11f76c14ffe5419": "2026-11-05T02:29:19.092846+00:00",
            "7dabc45bc78945aeb6e1fed60e758211": "2026-11-05T02:00:49.050049+00:00",
            "7e41e37671bb4f379f34040d26a7edd1": "2026-11-05T02:02:30.680679+00:00",
            "7ee94c7de921473eb517a6d6b6dcce1f": "2026-11-05T02:01:38.530707+00:00",
            "839579bda217485fb4980639f7a6d612": "2026-11-05T02:01:31.335486+00:00",
            "870f37521e9d41dfb081cbd75ef35ffe": "2026-11-05T02:02:09.213906+00:00",
            "88f1a54e802b4088949edfbee09c3011": "2026-11-05T02:29:19.799666+00:00",
            "8be47b0aed5c4561acaf90439345c43c": "2026-11-05T12:01:43.021451+00:00",
            "8c19bff80f3d407992ce4211f685804a": "2026-11-05T02:02:06.846558+00:00",
            "8ddc169c220d4536a23013e1a032f1d0": "2026-11-05T12:01:36.878605+00:00",
            "8e55c5a39d1048e3baaf5ea4db53f44f": "2026-11-05T02:01:53.191812+00:00",
            "8f88ef3fdab24c838bff1c74610de82d": "2026-11-05T02:01:45.540636+00:00",
            "9078c1e4a2bf4e8897a3d3d07c05651d": "2026-11-05T12:02:19.401265+00:00",
            "9601b63751ac489f8029bfb68975518a": "2026-11-05T12:01:28.432032+00:00",
            "9d536e24f3814bfead3a9b25586880e3": "2026-11-05T02:29:19.632441+00:00",
            "9d9fa70225cd41fcaae43e09a78a5225": "2026-11-05T02:02:25.679606+00:00",
            "9fba4078357f4b03aa3c044c7be4f808": "2026-11-05T02:01:50.534975+00:00",
            "a19d3d795f3b4e118a41f26a5c2c3dc2": "2026-11-05T12:01:23.886871+00:00",
            "a3566eb1641b4dc9bb6a93d714278450": "2026-11-05T02:02:35.735239+00:00",
            "a38e052ba694402f872481e298debb06": "2026-11-05T12:02:01.407310+00:00",
            "a92eca88847b4bdb9b8ef532283b0d30": "2026-11-05T12:01:26.220488+00:00",
            "a9b3f02e1b3945f39185aa8959a6187f": "2026-11-05T12:00:21.437985+00:00",
            "abd584ecbc8d4a2a9ba3b1eecf6b6e70": "2026-11-05T02:02:21.757846+00:00",
            "b251da8c8c134972be24d253cf27247e": "2026-11-05T12:01:52.172969+00:00",
            "b9965865030e49c8ace6f773c7ae1e4a": "2026-11-05T12:00:26.133286+00:00",
            "bd1ae17665a94b7ca34347df15a87a63": "2026-11-05T12:02:08.184592+00:00",
            "be41ea748b1d4b38b992357087c970c4": "2026-11-05T02:01:43.358744+00:00",
            "c361267117714de4b71605434cece3ad": "2026-11-05T02:01:12.308129+00:00",
            "c3df4fc54adf4e79a3c121c1757932a1": "2026-11-05T02:29:19.339440+00:00",
            "c45f28d9cbf7473f96f1aa925b23a119": "2026-11-05T02:01:25.947079+00:00",
            "cd778b3921304ad9b3862a714790c77b": "2026-11-05T02:02:04.782982+00:00",
            "ce4308f737ae418bbc71e4c4795d8104": "2026-11-05T12:00:53.616743+00:00",
            "d0f3997f963a4d14a92c2ff5f5b344c0": "2026-11-05T02:01:14.729383+00:00",
            "d3e1e2da2cae4b099f4583c2481a41ce": "2026-11-05T12:01:47.418660+00:00",
            "d4fc75e2fc2e4ce9a89d8d939964827a": "2026-11-05T02:29:19.157687+00:00",
            "d6309092ff09403d8f846dd7c52b3c92": "2026-11-05T12:01:30.292936+00:00",
            "d6ae50d19ff141fe8bea1a32047214e1": "2026-11-05T02:00:50.330144+00:00",
            "d779385e86bc4accb8f6b0d5d38c19ba": "2026-11-05T02:01:09.572525+00:00",
            "d80321e833e54b06b8853b013a0c73f7": "2026-11-05T02:02:28.702758+00:00",
            "dafd7bbf333c4072bd367220e16d854f": "2026-11-05T02:01:41.235751+00:00",
            "dd947d5c23d8468b9a084b89c1545fbb": "2026-11-05T02:02:40.033372+00:00",
            "e41c22335d014b808c6faa454b4d94ec": "2026-11-05T02:01:16.718457+00:00",
            "e614331c3906464ba2c450426019abbe": "2026-11-05T12:02:05.473070+00:00",
            "e7dd188d863d40cf93d4a12b4cd971bf": "2026-11-05T12:01:47.765071+00:00",
            "e91bc2593ab34fa69f0df4c1b1e5422f": "2026-11-05T12:02:10.579957+00:00",
            "ecd5c8a964a543b8843537ebecad4fa8": "2026-11-05T12:01:16.170197+00:00",
            "ee087f7e2e3d4a4386b8eea16fc48ae9": "2026-11-05T02:00:59.209223+00:00",
            "f09a7b34775540749f9e1838577d2891": "2026-11-05T12:01:59.028160+00:00",
            "f1591fad37b54143a9e019a831810c0f": "2026-11-05T12:00:18.272078+00:00",
            "f1ba4b0f12a94f6e86c0767248b1f2d3": "2026-11-05T02:01:56.054023+00:00",
            "f37bd7d25e784203bc026671288fd3a8": "2026-11-05T02:02:23.714310+00:00",
            "f429dd93e63d4b50847df1f2652400c0": "2026-11-05T12:00:51.219980+00:00",
            "f5b519ffd763428c91497eaf8bbbf7bd": "2026-11-05T02:01:47.756001+00:00",
            "f653e866eb164046bd771009e5b9c027": "2026-11-05T12:00:58.115903+00:00",
            "fa1c1b84dece45b0bc6cd988763be62d": "2026-11-05T12:01:56.920082+00:00",
            "fa36b43cd9dd4fccab3794595384b2e9": "2026-11-05T02:01:23.690486+00:00",
            "fc9d3d7ff9b6448d990fb9b5a585d970": "2026-11-05T02:02:38.079004+00:00",
            "fd44caad44554936a0407291d4e2ebe9": "2026-11-05T02:29:19.014354+00:00",
        },
        "nodes": frozenset({
            "01f657b6892f4c5f9a2f13326056562e",  # agent_ram
            "031ebd75cfbe452d960e92462a67d621",  # agent_mahiru
            "0639c04b903a47b8894727e796fba63d",  # agent_ai
            "086e9545dedf4c1f9a5e6ebc5dddbb2f",  # agent_mashiro
            "0c3c2c6e52cd44f1adde3d73a25e7c65",  # agent_emilia
            "1157634d1a9a4a31b8e7a5f80a48c836",  # agent_sachi
            "12c64f890eaa4656ba977d12a7fa7833",  # agent_mashiro
            "137f1cee7b204e62885eacd5ea881307",  # agent_nino
            "14a4c94f4eb2496293e47c64a6992085",  # agent_nino
            "15e23dd0ac314e7caa5e1d56c30d6f82",  # agent_rio
            "166420be1093464c80d30b73125a5b00",  # agent_reze
            "16e9352ebcf84b0da73d1938d9640d92",  # agent_rio
            "1ef7aabcbe4b4abe988a677e9f67bcbb",  # agent_ichika
            "2363a4d1bb30488dbd5991dde586c827",  # agent_yue
            "26a2a186d4b24a99a870cc07f1d95a5e",  # agent_rem
            "2724e9b290ef4ecebad832640deb8a9f",  # agent_mahiru
            "28d875e22b184b969bbceefcaec3dfe2",  # agent_beatrice
            "2914053335174586b6b21c66b003b673",  # agent_asuna
            "3634c72894a3412cb0f047691060e295",  # agent_kaguya
            "3b12f59a46ae4a93ae6f5e3b3a6664a3",  # agent_shizuku
            "3ba3978254a1460bae615efb6164a59b",  # agent_rin
            "3ba8a53a7799447c8517db7add926088",  # agent_saki
            "3c7ac79afc1d4be4af3a784977992c35",  # agent_sakura
            "45a27dd5c04848ffb8e6d0904a4996a1",  # agent_hiro
            "45e2b74fa4754471a726e0eefea5377f",  # agent_yotsuba
            "46a6d93e6a7a4bd5918bb74172488f97",  # agent_yue
            "46f3812fc6824fcab989f3d64be2daa8",  # agent_shino
            "48fa34d57c374565b2014b797ea86f08",  # agent_chizuru
            "499216c124ef4fe9994e9c4e13c96d7d",  # agent_ram
            "49b53e7e5d6e47d287d0faefd42b0280",  # agent_anna
            "4a37aabbf71e4b299421fdfde96b74f1",  # agent_akane
            "4b426737b9654f4a9fc7b9da56010693",  # agent_mahiru
            "4b6cdffa4c0f4911a1a3870be680225f",  # agent_rika
            "4c216fd0b5ab40ce981a875f195ea6cc",  # agent_kurisu
            "4cacab40d2dc44bfb447bc1455a8e18e",  # agent_mochizuki_ai
            "52d4097ef47145e7accd586c8af27ed5",  # agent_anna
            "52dac488d29c4f42b83a8debd37b8bef",  # agent_hiro
            "565f1c9407884e9794848d219160be0d",  # agent_nagisa
            "56ac9358558643b5abf7c2bef8b7710d",  # agent_mami
            "574720388af2447d96defa6b640ee0ff",  # agent_sakura
            "5af262e3c8124a649c6e1c795c4a4b7a",  # agent_ram
            "5c30961348344745ab0182891aa2b206",  # agent_kirino
            "61cab369a36142fc8a9b45ff6613a6d5",  # agent_itsuki
            "68ee76999bf248e0a9e9588837d47c40",  # agent_mai
            "6b3a7fc4cc3a426ba67e30a98be6fe85",  # agent_akane
            "6cb39dd159aa40c6a556432de2e6d5e1",  # agent_sachi
            "70fa4fb7b0204ab1876440c3b208e1c3",  # agent_anna
            "735bcd3f961f41bfb6ab54c5267f9b26",  # agent_mai
            "7521666f4c9f44ed9d93c6cf859fdeff",  # agent_yotsuba
            "7592b254254b45f6a72a673b405ed879",  # agent_kana
            "786d87dabcef4b68885cd960c57f1929",  # agent_haruhi
            "78c4b71c09494d24b7efcd62e18879ba",  # agent_minami
            "799554fdfd5c44d3b2016598888247dc",  # agent_shizuku
            "7ba6aaa4d4b64eafa11f76c14ffe5419",  # agent_kaori
            "7dabc45bc78945aeb6e1fed60e758211",  # agent_yua
            "7e41e37671bb4f379f34040d26a7edd1",  # agent_asuna
            "7ee94c7de921473eb517a6d6b6dcce1f",  # agent_kaori
            "839579bda217485fb4980639f7a6d612",  # agent_iroha
            "870f37521e9d41dfb081cbd75ef35ffe",  # agent_tsukasa
            "88f1a54e802b4088949edfbee09c3011",  # agent_shino
            "8be47b0aed5c4561acaf90439345c43c",  # agent_minami
            "8c19bff80f3d407992ce4211f685804a",  # agent_aoi
            "8ddc169c220d4536a23013e1a032f1d0",  # agent_makima
            "8e55c5a39d1048e3baaf5ea4db53f44f",  # agent_reze
            "8f88ef3fdab24c838bff1c74610de82d",  # agent_makima
            "9078c1e4a2bf4e8897a3d3d07c05651d",  # agent_nagisa
            "9601b63751ac489f8029bfb68975518a",  # agent_kana
            "9d536e24f3814bfead3a9b25586880e3",  # agent_rika
            "9d9fa70225cd41fcaae43e09a78a5225",  # agent_itsuki
            "9fba4078357f4b03aa3c044c7be4f808",  # agent_minami
            "a19d3d795f3b4e118a41f26a5c2c3dc2",  # agent_iroha
            "a3566eb1641b4dc9bb6a93d714278450",  # agent_rika
            "a38e052ba694402f872481e298debb06",  # agent_tsukasa
            "a92eca88847b4bdb9b8ef532283b0d30",  # agent_kaguya
            "a9b3f02e1b3945f39185aa8959a6187f",  # agent_ruka
            "abd584ecbc8d4a2a9ba3b1eecf6b6e70",  # agent_emilia
            "b251da8c8c134972be24d253cf27247e",  # agent_sachi
            "b9965865030e49c8ace6f773c7ae1e4a",  # agent_rem
            "bd1ae17665a94b7ca34347df15a87a63",  # agent_chizuru
            "be41ea748b1d4b38b992357087c970c4",  # agent_kurisu
            "c361267117714de4b71605434cece3ad",  # agent_miku
            "c3df4fc54adf4e79a3c121c1757932a1",  # agent_mami
            "c45f28d9cbf7473f96f1aa925b23a119",  # agent_haruhi
            "cd778b3921304ad9b3862a714790c77b",  # agent_shea
            "ce4308f737ae418bbc71e4c4795d8104",  # agent_aoi
            "d0f3997f963a4d14a92c2ff5f5b344c0",  # agent_rem
            "d3e1e2da2cae4b099f4583c2481a41ce",  # agent_asuna
            "d4fc75e2fc2e4ce9a89d8d939964827a",  # agent_kirino
            "d6309092ff09403d8f846dd7c52b3c92",  # agent_kaori
            "d6ae50d19ff141fe8bea1a32047214e1",  # agent_ruka
            "d779385e86bc4accb8f6b0d5d38c19ba",  # agent_akane
            "d80321e833e54b06b8853b013a0c73f7",  # agent_mami
            "dafd7bbf333c4072bd367220e16d854f",  # agent_kirino
            "dd947d5c23d8468b9a084b89c1545fbb",  # agent_shino
            "e41c22335d014b808c6faa454b4d94ec",  # agent_ai
            "e614331c3906464ba2c450426019abbe",  # agent_beatrice
            "e7dd188d863d40cf93d4a12b4cd971bf",  # agent_rin
            "e91bc2593ab34fa69f0df4c1b1e5422f",  # agent_emilia
            "ecd5c8a964a543b8843537ebecad4fa8",  # agent_fuuka
            "ee087f7e2e3d4a4386b8eea16fc48ae9",  # agent_ruka
            "f09a7b34775540749f9e1838577d2891",  # agent_shizuku
            "f1591fad37b54143a9e019a831810c0f",  # agent_yua
            "f1ba4b0f12a94f6e86c0767248b1f2d3",  # agent_miku
            "f37bd7d25e784203bc026671288fd3a8",  # agent_ichika
            "f429dd93e63d4b50847df1f2652400c0",  # agent_miku
            "f5b519ffd763428c91497eaf8bbbf7bd",  # agent_mai
            "f653e866eb164046bd771009e5b9c027",  # agent_mochizuki_ai
            "fa1c1b84dece45b0bc6cd988763be62d",  # agent_shea
            "fa36b43cd9dd4fccab3794595384b2e9",  # agent_fuuka
            "fc9d3d7ff9b6448d990fb9b5a585d970",  # agent_saki
            "fd44caad44554936a0407291d4e2ebe9",  # agent_itsuki
        }),
    },
    {
        "date": "2026-10-30",
        "not_before": "2026-11-07T02:00:00+00:00",
        "bounds": {
            "0aa69667f5e64e90b8f844c0bbe6f913": "2026-11-06T12:02:43.552059+00:00",
            "0f0c4ad14b224f1caff72fde87521bee": "2026-11-06T02:02:04.950626+00:00",
            "1436ca76a3b346c1b94530450341481b": "2026-11-06T02:01:39.101551+00:00",
            "1619dd6240b84f8fb53784dd061d0689": "2026-11-06T02:01:42.610904+00:00",
            "17942c2929434e56b493ac445bd04a4c": "2026-11-06T12:03:22.798053+00:00",
            "1870f3e86960451897f6420ba537bb7e": "2026-11-06T02:02:56.222905+00:00",
            "1a21ab5a5be544acb1b8c52507e7c4e9": "2026-11-06T02:02:19.021469+00:00",
            "1d431b35dd0f469081dc076c4fe7faf7": "2026-11-06T12:01:27.075918+00:00",
            "222d874de8a945729a4929f1cc6455c7": "2026-11-06T12:03:36.300423+00:00",
            "27cf4b54e4c1410797eca659f5c558cd": "2026-11-06T02:02:32.812893+00:00",
            "2da8507359af40df9c07777c1c4ea39f": "2026-11-06T12:02:36.317476+00:00",
            "2f459cffba6a40bc9eb4fa9fee583501": "2026-11-06T02:02:18.714900+00:00",
            "2fcc7ec8a6de47cd9eca5a5c23b0264f": "2026-11-06T02:03:00.605174+00:00",
            "333891f5376a43218d6b1ccd74bd3e5d": "2026-11-06T12:02:11.685151+00:00",
            "3534563f9ad74b2ca2817d4e80b795ce": "2026-11-06T12:01:31.757291+00:00",
            "397e30dd125e46b4a0e1c1996194eedd": "2026-11-06T12:01:27.593822+00:00",
            "3a2f13c48e174a218eb08963e10bff4e": "2026-11-06T12:03:32.801876+00:00",
            "3a722e44f6ef43bd90deaf29e51cfccb": "2026-11-06T12:01:10.664350+00:00",
            "3af37ad7b5894a80bb78ae2be108a75c": "2026-11-06T12:03:29.878995+00:00",
            "3b50b8aa9eac49b2b718f31461f926f2": "2026-11-06T12:35:07.750906+00:00",
            "44d5d77ac7064d28981bd88d05e7d39a": "2026-11-06T12:00:35.020718+00:00",
            "45ee9e8a376546398dca5590c438fa82": "2026-11-06T12:03:21.732893+00:00",
            "465e006eeeac485e99740abc92b45a57": "2026-11-06T12:00:50.362192+00:00",
            "4a965c6c8087408bb541f88521e8a199": "2026-11-06T12:04:16.053791+00:00",
            "4e81b20d3ce14ab191fe8d094237637f": "2026-11-06T12:04:27.593651+00:00",
            "4f491605697c46579b1524adcee9f226": "2026-11-06T12:04:19.517749+00:00",
            "51405c9320214003b502d77422a5ed0a": "2026-11-06T12:01:05.836560+00:00",
            "5344effce5e4477699deff21bd71e712": "2026-11-06T12:02:56.460376+00:00",
            "54e0a67cacfd44158862c748113c23be": "2026-11-06T12:03:26.250493+00:00",
            "560a76974dc54dc1a1c65ceb1f8ee729": "2026-11-06T02:01:30.269380+00:00",
            "58e4ec60f4d646b58e6c9f87a783a6be": "2026-11-06T12:01:41.589254+00:00",
            "5990a250ba3d4dffb674c45b3061f372": "2026-11-06T12:01:37.182088+00:00",
            "5a605ecbaa6449ca91782ffc836a0b0e": "2026-11-06T02:01:34.227204+00:00",
            "61af70c067354f2cb4c8f573f2a6d354": "2026-11-06T12:05:04.660376+00:00",
            "63317aecda804e28bbbd408e12f7cf39": "2026-11-06T12:03:39.404824+00:00",
            "666677f4d710432ebaa65889e4b402d5": "2026-11-06T12:02:28.717219+00:00",
            "6ba73b2307bf4a9fa111b70fade1b970": "2026-11-06T02:02:37.007011+00:00",
            "7bf09405ede64f159dc5291b17767854": "2026-11-06T12:00:29.542652+00:00",
            "7d4fcc1789c849628bd2a0f42abacc7d": "2026-11-06T02:02:52.304596+00:00",
            "7e7ca43e5b0945908e48a519610a16f3": "2026-11-06T12:01:31.455650+00:00",
            "800dd7001b2342cca6dd9f9e3586135f": "2026-11-06T02:02:37.494092+00:00",
            "81ce117516f749ef8cb324306598bd4d": "2026-11-06T12:00:46.468758+00:00",
            "84c67984048849f8a7188e99025e4afe": "2026-11-06T12:04:23.780904+00:00",
            "865a9d742c36400092306e59da103270": "2026-11-06T02:02:23.026995+00:00",
            "8af4f44bca714d33af2f7b9decae8fea": "2026-11-06T02:02:40.990924+00:00",
            "8ea0648a91bc4e45906a5c57242b613e": "2026-11-06T12:02:21.152379+00:00",
            "90d8b7b31c31480c90449a030c46c27b": "2026-11-06T12:00:24.680152+00:00",
            "918195f6af3c4767a0c59139119700eb": "2026-11-06T02:01:54.796474+00:00",
            "92ffb93978114473ae4b8f05e0e63909": "2026-11-06T02:01:46.189848+00:00",
            "934816b33f0e4b7e8b3fdc00b80b3a24": "2026-11-06T12:01:46.021302+00:00",
            "93500795b14c4acb93e38ca32e51aeb0": "2026-11-06T02:02:26.211546+00:00",
            "9651223acfad47d1a474d50576f06799": "2026-11-06T12:01:36.874488+00:00",
            "971587a7bc8946e1aff08d85201231d5": "2026-11-06T02:03:54.317710+00:00",
            "972e0bc7daee42e182acc9b5af4300b8": "2026-11-06T12:02:29.629902+00:00",
            "98a87f8a9899445eaabb64acca3aafb8": "2026-11-06T02:01:39.258183+00:00",
            "9c3b54c4a6434b9f8c61bf5bf63c8844": "2026-11-06T02:02:52.492200+00:00",
            "a66d5d29516a409bae3506caf4af15f0": "2026-11-06T12:00:43.388688+00:00",
            "a6df8ebc9a1240119d9fc1a48257954c": "2026-11-06T12:01:13.061916+00:00",
            "a80306b3a1a94c2180bb1dfd2c60a249": "2026-11-06T12:02:12.025873+00:00",
            "a9b98eade3fc4a27a56a0f6fa5a20af4": "2026-11-06T12:00:39.381282+00:00",
            "ab112329ebec4ed3b30b126ffdfd63d4": "2026-11-06T02:01:45.691264+00:00",
            "ad98d6c00ce348f0b24cc861aa2d9265": "2026-11-06T02:02:44.336879+00:00",
            "b0f82a74835b4cedab4b6fcd6883a23c": "2026-11-06T12:02:48.794292+00:00",
            "b5e75afddd3040fb9779a5d4709ac646": "2026-11-06T12:00:33.743920+00:00",
            "b7687374f1df4ab08b135f39a798a854": "2026-11-06T12:03:45.557843+00:00",
            "b851c6ccd1fa410cb1f5e58be8cfd226": "2026-11-06T12:01:46.420736+00:00",
            "ba7cf1aab73a446fbfd6159d7ed5c5ff": "2026-11-06T02:02:01.894768+00:00",
            "bad64a6934524454840dc2513662c47c": "2026-11-06T02:01:58.681340+00:00",
            "bcbc16affb104152bf2bbc92b10b7a30": "2026-11-06T12:02:25.048315+00:00",
            "c10ad0cf4bfc40ca8b5b356de0d5c20a": "2026-11-06T12:03:42.273796+00:00",
            "c1f2961840f546068ff7e7f1585a3479": "2026-11-06T12:01:21.980482+00:00",
            "c40d520dfa6d4e6988a4014aceb4bac3": "2026-11-06T12:35:09.599491+00:00",
            "c66377cc9477417aa8efdf8a795d4fc9": "2026-11-06T12:00:57.423660+00:00",
            "c66434fcdb374f298abc421b780593da": "2026-11-06T12:02:52.118710+00:00",
            "d0188ec2b57b4ed4bf40c3ec0f725dc5": "2026-11-06T12:00:54.256884+00:00",
            "d40024dc42de49fdb5ca0b809eda04e3": "2026-11-06T12:04:34.890655+00:00",
            "d541aedf00f14297b03015ba4e6a2b9f": "2026-11-06T02:01:26.249631+00:00",
            "dd1c93cb18f14293a21c274a74455bf9": "2026-11-06T02:01:58.463592+00:00",
            "e1e3ab43ebab4ded91c7eac07574f794": "2026-11-06T02:02:47.585332+00:00",
            "e6eb500c734641e4975b7cddc7c6befd": "2026-11-06T02:02:29.713545+00:00",
            "e769b645939c4a6fabb4e26a04cee09c": "2026-11-06T02:01:51.161812+00:00",
            "e802bd4d68f5490aaff282eb1a1520e5": "2026-11-06T12:02:16.595917+00:00",
            "ea0c8484b1014f9ead872827d127aa0e": "2026-11-06T12:01:18.747103+00:00",
            "edc9c95189194e409671a8e013c08919": "2026-11-06T12:01:10.467510+00:00",
            "f0f6ec54e0cb41528a092894236afdf0": "2026-11-06T12:02:33.014726+00:00",
            "f13a141024b940c98f120b73a1e4f2fa": "2026-11-06T12:04:30.924566+00:00",
            "f19fc91c02b741df9c70361e19525a92": "2026-11-06T12:00:58.560456+00:00",
            "f7a13aca55074a0d8268d895192a94ea": "2026-11-06T12:04:23.462432+00:00",
            "fb1c22c3acab48a0a011abacccaab5c9": "2026-11-06T12:35:09.835246+00:00",
            "ff9abd5669c5492f8338837f8d53cbc4": "2026-11-06T12:04:11.676148+00:00",
            "ffe75bce7b624cc4a5ab382e761400a1": "2026-11-06T12:04:11.372974+00:00",
        },
        "nodes": frozenset({
            "0aa69667f5e64e90b8f844c0bbe6f913",  # agent_rio
            "0f0c4ad14b224f1caff72fde87521bee",  # agent_ai
            "1436ca76a3b346c1b94530450341481b",  # agent_rem
            "1619dd6240b84f8fb53784dd061d0689",  # agent_ram
            "17942c2929434e56b493ac445bd04a4c",  # agent_shizuku
            "1870f3e86960451897f6420ba537bb7e",  # agent_kurisu
            "1a21ab5a5be544acb1b8c52507e7c4e9",  # agent_ram
            "1d431b35dd0f469081dc076c4fe7faf7",  # agent_hiro
            "222d874de8a945729a4929f1cc6455c7",  # agent_chizuru
            "27cf4b54e4c1410797eca659f5c558cd",  # agent_hiro
            "2da8507359af40df9c07777c1c4ea39f",  # agent_rin
            "2f459cffba6a40bc9eb4fa9fee583501",  # agent_mochizuki_ai
            "2fcc7ec8a6de47cd9eca5a5c23b0264f",  # agent_makima
            "333891f5376a43218d6b1ccd74bd3e5d",  # agent_aoi
            "3534563f9ad74b2ca2817d4e80b795ce",  # agent_iroha
            "397e30dd125e46b4a0e1c1996194eedd",  # agent_mahiru
            "3a2f13c48e174a218eb08963e10bff4e",  # agent_beatrice
            "3a722e44f6ef43bd90deaf29e51cfccb",  # agent_rem
            "3af37ad7b5894a80bb78ae2be108a75c",  # agent_yue
            "3b50b8aa9eac49b2b718f31461f926f2",  # agent_mami
            "44d5d77ac7064d28981bd88d05e7d39a",  # agent_ruka
            "45ee9e8a376546398dca5590c438fa82",  # agent_kirino
            "465e006eeeac485e99740abc92b45a57",  # agent_anna
            "4a965c6c8087408bb541f88521e8a199",  # agent_nagisa
            "4e81b20d3ce14ab191fe8d094237637f",  # agent_saki
            "4f491605697c46579b1524adcee9f226",  # agent_nino
            "51405c9320214003b502d77422a5ed0a",  # agent_ai
            "5344effce5e4477699deff21bd71e712",  # agent_shea
            "54e0a67cacfd44158862c748113c23be",  # agent_tsukasa
            "560a76974dc54dc1a1c65ceb1f8ee729",  # agent_ruka
            "58e4ec60f4d646b58e6c9f87a783a6be",  # agent_kana
            "5990a250ba3d4dffb674c45b3061f372",  # agent_mai
            "5a605ecbaa6449ca91782ffc836a0b0e",  # agent_akane
            "61af70c067354f2cb4c8f573f2a6d354",  # agent_sachi
            "63317aecda804e28bbbd408e12f7cf39",  # agent_emilia
            "666677f4d710432ebaa65889e4b402d5",  # agent_asuna
            "6ba73b2307bf4a9fa111b70fade1b970",  # agent_anna
            "7bf09405ede64f159dc5291b17767854",  # agent_ruka
            "7d4fcc1789c849628bd2a0f42abacc7d",  # agent_kirino
            "7e7ca43e5b0945908e48a519610a16f3",  # agent_anna
            "800dd7001b2342cca6dd9f9e3586135f",  # agent_iroha
            "81ce117516f749ef8cb324306598bd4d",  # agent_mahiru
            "84c67984048849f8a7188e99025e4afe",  # agent_rika
            "865a9d742c36400092306e59da103270",  # agent_mahiru
            "8af4f44bca714d33af2f7b9decae8fea",  # agent_kaguya
            "8ea0648a91bc4e45906a5c57242b613e",  # agent_makima
            "90d8b7b31c31480c90449a030c46c27b",  # agent_yua
            "918195f6af3c4767a0c59139119700eb",  # agent_mai
            "92ffb93978114473ae4b8f05e0e63909",  # agent_mahiru
            "934816b33f0e4b7e8b3fdc00b80b3a24",  # agent_kaori
            "93500795b14c4acb93e38ca32e51aeb0",  # agent_fuuka
            "9651223acfad47d1a474d50576f06799",  # agent_kaguya
            "971587a7bc8946e1aff08d85201231d5",  # agent_miku
            "972e0bc7daee42e182acc9b5af4300b8",  # agent_minami
            "98a87f8a9899445eaabb64acca3aafb8",  # agent_ruka
            "9c3b54c4a6434b9f8c61bf5bf63c8844",  # agent_mai
            "a66d5d29516a409bae3506caf4af15f0",  # agent_ram
            "a6df8ebc9a1240119d9fc1a48257954c",  # agent_asuna
            "a80306b3a1a94c2180bb1dfd2c60a249",  # agent_kirino
            "a9b98eade3fc4a27a56a0f6fa5a20af4",  # agent_rem
            "ab112329ebec4ed3b30b126ffdfd63d4",  # agent_akane
            "ad98d6c00ce348f0b24cc861aa2d9265",  # agent_kana
            "b0f82a74835b4cedab4b6fcd6883a23c",  # agent_sachi
            "b5e75afddd3040fb9779a5d4709ac646",  # agent_akane
            "b7687374f1df4ab08b135f39a798a854",  # agent_itsuki
            "b851c6ccd1fa410cb1f5e58be8cfd226",  # agent_miku
            "ba7cf1aab73a446fbfd6159d7ed5c5ff",  # agent_aoi
            "bad64a6934524454840dc2513662c47c",  # agent_rem
            "bcbc16affb104152bf2bbc92b10b7a30",  # agent_mashiro
            "c10ad0cf4bfc40ca8b5b356de0d5c20a",  # agent_ichika
            "c1f2961840f546068ff7e7f1585a3479",  # agent_haruhi
            "c40d520dfa6d4e6988a4014aceb4bac3",  # agent_shino
            "c66377cc9477417aa8efdf8a795d4fc9",  # agent_akane
            "c66434fcdb374f298abc421b780593da",  # agent_sakura
            "d0188ec2b57b4ed4bf40c3ec0f725dc5",  # agent_mai
            "d40024dc42de49fdb5ca0b809eda04e3",  # agent_yotsuba
            "d541aedf00f14297b03015ba4e6a2b9f",  # agent_yua
            "dd1c93cb18f14293a21c274a74455bf9",  # agent_miku
            "e1e3ab43ebab4ded91c7eac07574f794",  # agent_kaori
            "e6eb500c734641e4975b7cddc7c6befd",  # agent_haruhi
            "e769b645939c4a6fabb4e26a04cee09c",  # agent_anna
            "e802bd4d68f5490aaff282eb1a1520e5",  # agent_kurisu
            "ea0c8484b1014f9ead872827d127aa0e",  # agent_ram
            "edc9c95189194e409671a8e013c08919",  # agent_mochizuki_ai
            "f0f6ec54e0cb41528a092894236afdf0",  # agent_reze
            "f13a141024b940c98f120b73a1e4f2fa",  # agent_shino
            "f19fc91c02b741df9c70361e19525a92",  # agent_miku
            "f7a13aca55074a0d8268d895192a94ea",  # agent_minami
            "fb1c22c3acab48a0a011abacccaab5c9",  # agent_shizuku
            "ff9abd5669c5492f8338837f8d53cbc4",  # agent_mami
            "ffe75bce7b624cc4a5ab382e761400a1",  # agent_kurisu
        }),
    },

)


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


def normalize_cohorts(cohorts=None) -> list:
    """把台帳正規化成 ``[{"date", "not_before", "nodes", "types", "bounds"}, ...]``（順序穩定）。

    🔴 型別中立化（Owner 2026-10-27 裁定）：台帳條目可帶 `"types"`（節點 id → 型別）。
    未列於 `types` 的節點 ⇒ 記載型別視為 `"value"`（向後相容：既有 cohort 全是 value）。
    """
    src = EXPECTED_RETIREMENT_COHORTS if cohorts is None else cohorts
    out = []
    for c in src or ():
        if not isinstance(c, dict):
            continue
        out.append({
            "date": c.get("date"),
            "not_before": c.get("not_before"),
            "nodes": frozenset(c.get("nodes") or ()),
            # 🔴 台帳**記載**的節點型別（Owner 2026-10-27 型別中立化）。
            "types": dict(c.get("types") or {}),
            # 🔴 台帳**記載**的逐節點下界（node_id → ISO 時刻）。
            "bounds": dict(c.get("bounds") or {}),
        })
    return out


def retirement_bounds(cohorts=None) -> dict:
    """節點 id → 該節點所屬 cohort 的**最早可退役槽**（逐節點）。

    🔴 判準層只比對**宣告式時刻常數**（`not_before`／逐節點 `bounds`），不做任何
    「距今 N 天」型算術（守衛 `test_cli_does_not_import_decay_thresholds`）。

    🔴 **逐節點下界覆寫**（Owner 2026-10-27 實測補丁）：cohort 級 `not_before`
    （建立日＋8 天 @02:00Z）對「建立後**從未被背書**」的成員會**晚一槽**——引擎按
    `last_support_ts + 7 天` 讓它們弱化，cohort 級下界卻寫在下一槽 ⇒ 它們會被誤判成
    「提前早衰」。⇒ 台帳可用 `"bounds": {node_id: ISO}` **逐節點**記錄真實下界
    （= 引擎自己的衰減門檻）；未記錄者沿用 cohort 級下界。
    ⇒ 7 天門檻本身**不動**：這裡只是把「早一槽」的事實記進台帳，**不放寬任何門檻**。
    """
    bounds = {}
    for c in normalize_cohorts(cohorts):
        cohort_bound = c.get("not_before")
        per_node = c.get("bounds") or {}
        for nid in c.get("nodes") or ():
            bound = per_node.get(nid) or cohort_bound
            # 若同一節點出現在多個 cohort ⇒ 取最早的下界（寬鬆＝保守放行）。
            if nid not in bounds or (bound and str(bound) < str(bounds[nid])):
                bounds[nid] = bound
    return bounds


def approved_retirement_ids(cohorts=None) -> set:
    """台帳核准的節點聯集（**expected 的資格來源**）。"""
    out = set()
    for c in normalize_cohorts(cohorts):
        out |= set(c.get("nodes") or ())
    return out


def approved_node_types(cohorts=None) -> dict:
    """台帳**記載**的節點型別（``node_id`` → 型別）。

    🔴 型別中立化（Owner 2026-10-27 裁定「白名單型別中立化」）：
      判定層**不再硬寫** `node_type == "value"`，改為比對「台帳記載的型別」。
      - 未在條目 `types` 中列出的節點 ⇒ 記載型別 = `"value"`（向後相容）。
      - `types` 內的記載**優先**於預設值（同一節點出現在多個 cohort 時亦然）。
      ⇒ **fail-closed 方向**：漏寫型別 ⇒ 非 value 的事件**不可能**被放行成 expected
        （只會更嚴、不會更鬆）。
    """
    out = {}
    for c in normalize_cohorts(cohorts):
        for nid in c.get("nodes") or ():
            out.setdefault(nid, "value")
        for nid, t in (c.get("types") or {}).items():
            out[nid] = t
    return out


def _recorded_node_type(node_id, types=None) -> str:
    """某節點在台帳中的**記載型別**（未記載 ⇒ `"value"`）。"""
    if types is None:
        types = approved_node_types()
    try:
        return (types or {}).get(node_id, "value")
    except AttributeError:
        return "value"


def _created_after(created: str, not_after) -> bool:
    """節點的建立時刻是否**晚於**評估時鐘。

    ⚠️ 時刻不可解析或無時區 ⇒ 回 **False**（＝保守**包含**該節點）⇒
    自我否決只可被收緊、不可被放寬（fail-closed）。
    """
    try:
        dt = datetime.fromisoformat(str(created))
        bound = (not_after if isinstance(not_after, datetime)
                 else datetime.fromisoformat(str(not_after)))
        if dt.tzinfo is None or bound.tzinfo is None:
            return False
        return dt > bound
    except Exception:
        return False


def expected_retirement_sets_by_cohort(nodes: list, edges: list,
                                        cohorts=None, *, not_after=None) -> dict:
    """**逐 cohort** 推導「四條件全中」的節點集合（建立日 → 集合）。

    每個 cohort 的四條件（**不得**放寬、**不得**加任何時間算術）：
      1. 節點型別 == **該 cohort 台帳記載的型別**（未記載 ⇒ 向後相容預設
         `"value"`；與 `approved_node_types()` / `_recorded_node_type()` 完全同源）
      2. created_ts 的日期部分 == 該 cohort 的 `date`
      3. last_support_ts == created_ts（建立後從未再被背書）
      4. 沒有任何 trigger_type == "support_propagation" 的邊指向它（祖先支持邊＝0）

    🔴 `not_after`＝**評估時鐘上界**（2026-10-25 主大腦修正 B）：推導集合必須只含
    「在評估時刻**已經存在**」的節點。
    ⇒ 理由（實測）：用「過去的槽時刻」評估「已成長的圖」時，圖裡晚於該時刻的
    新節點會被算進推導集合 ⇒ 逐 cohort 自我否決（推導 ⊆ 核准）因**圖的成長**而
    破裂 ⇒ 對**尚未發生的槽**假紅（2026-10-25 晨槽實測：48 顆建立於 12:00Z 的
    value 節點晚於評估時鐘 02:00Z ⇒ 誤判 FAIL/exit 1）。
    ⇒ 這是**時序一致性**要求，不是放寬判準：四條件不變、自我否決不變。

    🔴 這是**規則的自我描述**，用來證明「規則沒有被悄悄放寬」；
    **不是** expected 的必要條件（見 `classify_soul_retirements`）。
    """
    supported = {e.get("node_id") for e in (edges or [])
                 if isinstance(e, dict) and e.get("trigger_type") == "support_propagation"}
    out = {}
    for c in normalize_cohorts(cohorts):
        date = c.get("date")
        # 🔴 型別中立（2026-10-29 工單 LEDGER-REFRESH-TYPE-NEUTRAL-1）：
        #    條件①由硬寫 `node_type == "value"` 改為「節點型別 == **該 cohort
        #    台帳記載的型別**」（沿用既有 `_recorded_node_type()` helper）。
        #    🔴 **fail-closed 方向**：未記載型別者視為 `"value"` ⇒ 非 value 型
        #    的節點**推導不出來**（推導只會更嚴、不會更鬆）⇒ 逐 cohort 的
        #    自我否決（推導 ⊆ 核准）不受影響。
        cohort_types = c.get("types") or {}
        found = set()
        for n in nodes or ():
            if not isinstance(n, dict):
                continue
            nid = n.get("node_id")
            if n.get("node_type") != _recorded_node_type(nid, cohort_types):
                continue
            created = n.get("created_ts") or ""
            if not isinstance(created, str) or created[:10] != date:
                continue
            if not_after is not None and _created_after(created, not_after):
                continue          # 晚於評估時鐘 ⇒ 當時尚未存在 ⇒ 不列入推導
            if (n.get("last_support_ts") or "") != created:
                continue
            if not nid or nid in supported:
                continue
            found.add(nid)
        out[date] = found
    return out


def explain_approved_difference(cohort, derived, nodes, edges) -> dict:
    """解釋台帳「只增不減」留下的差額（``approved ⊋ derived``）—— 逐筆可解釋性。

    🔴 語意（Owner 2026-10-29 裁定「等號斷言 → 子集 ＋ 差額可解釋」）：
    台帳對封閉 cohort **刻意只增不減**（保留曾被背書後又老化的成員）⇒
    ``approved ⊋ derived`` 是**合法**的 ⇒ 守衛**不可**再斷言等號。
    但「合法」不等於「任意」⇒ 差額必須**逐筆可解釋**。

    一個差額成員 ``nid`` 可被解釋，**當且僅當**它同時滿足：
      ① 確實屬於這個 cohort（型別 == 該 cohort 台帳記載的型別〔條件①〕
         ＋ 建立日 == cohort 日期〔條件②〕）；
      ② 之所以**沒被推導出來**，是因為「被背書過才老化」——
         建立後曾被背書（``last_support_ts`` **有值**且 != ``created_ts``
         ⇒ 條件③不成立）**或** 有 ``support_propagation`` 祖先邊（⇒ 條件④不成立）。

    ⇒ **不可解釋**者 = 台帳與圖的**真實矛盾**（節點不在圖譜／型別與台帳記載不符／
    建立日不屬於此 cohort／四條件皆成立卻未被推導）⇒ 必須報紅。

    ⚠️ 這是**量測 + 判準**，不放寬任何門檻；純函式、唯讀、不寫任何檔案。

    Args:
        cohort: 台帳條目（需含 ``date``／``nodes``／可選 ``types``）。
        derived: 該 cohort 的四條件推導集合（``set``）。
        nodes: 圖譜節點清單（``list[dict]``）。
        edges: 圖譜邊清單（``list[dict]``）。

    Returns:
        ``{"difference": [node_id, ...],
            "explained": [(node_id, reason), ...],
            "unexplained": [(node_id, reason), ...]}``
        ⚠️ ``difference`` 是**完整的差額清單**（未被解釋的成員也列在內）⇒
        呼叫端可驗證「解釋結果**完整覆蓋**差額」⇒ 判準被掏空時會轉紅
        （否則「unexplained == 空」會變成**空轉**的綠燈）。
    """
    by_id = {n.get("node_id"): n for n in (nodes or []) if isinstance(n, dict)}
    supported = {e.get("node_id") for e in (edges or [])
                 if isinstance(e, dict) and e.get("trigger_type") == "support_propagation"}
    date = cohort.get("date")
    cohort_types = cohort.get("types") or {}
    derived = set(derived or ())
    difference = sorted(set(cohort.get("nodes") or ()) - derived)
    explained = []
    unexplained = []
    for nid in difference:
        node = by_id.get(nid)
        if node is None:
            unexplained.append((nid, "節點不在圖譜中"))
            continue
        recorded_type = _recorded_node_type(nid, cohort_types)
        if node.get("node_type") != recorded_type:
            unexplained.append((nid, "型別與台帳記載不符（圖譜=%r，台帳記載=%r）"
                                % (node.get("node_type"), recorded_type)))
            continue
        created = node.get("created_ts") or ""
        if not isinstance(created, str) or created[:10] != date:
            unexplained.append((nid, "建立日不屬於此 cohort（圖譜=%r，cohort=%r）"
                                % (str(created)[:10], date)))
            continue
        last_support = node.get("last_support_ts") or ""
        # 🔴 「建立後曾被背書」必須**有值**：``last_support_ts`` 缺席（空字串）
        #    代表「從未被背書」，不可被當成「與建立時刻不同 ⇒ 被背書過」
        #    （否則缺欄位的節點會被誤判為可解釋 ⇒ 假綠）。
        supported_after_creation = bool(last_support) and last_support != created
        has_ancestor_support_edge = nid in supported
        if supported_after_creation and has_ancestor_support_edge:
            explained.append((nid, "被背書過才老化（建立後曾被背書 ＋ 有 "
                                "support_propagation 祖先邊）"))
        elif supported_after_creation:
            explained.append((nid, "被背書過才老化（建立後曾被背書："
                                "last_support_ts != created_ts）"))
        elif has_ancestor_support_edge:
            explained.append((nid, "被背書過才老化（有 support_propagation 祖先邊）"))
        else:
            unexplained.append((nid, "四條件皆成立卻未被推導 —— 推導層與台帳互相矛盾"))
    return {"difference": difference, "explained": explained, "unexplained": unexplained}


def expected_retirement_set(nodes: list, edges: list) -> set:
    """推導「預期退役集合」（**所有 cohort 的聯集**）。

    ⚠️ 語意（2026-10-25 主大腦語意修正 A）：
    這是**四條件推導**出來的集合，**不是** expected 的資格來源。
    expected 的資格來源是**台帳的核准清單**（`approved_retirement_ids()`）——
    因為同一個 cohort 的成員並非全部「建立後從未被背書」（有些先被背書過、
    後來連那個支持也滿 7 天才退役），若把四條件當成 expected 的必要條件，
    那 9 顆會被誤判成破線 ⇒ 又回到假紅。
    """
    sets = expected_retirement_sets_by_cohort(nodes, edges)
    out = set()
    for s in sets.values():
        out |= s
    return out


def baseline_violation(expected, cohorts=None) -> list:
    """推導集合若超出 Owner 核准的清單 ⇒ 規則被放寬 ⇒ **執行期自我否決**。

    這是「規則不得被悄悄放寬」的**執行期**保險（不只靠 CI）。

    🔴 **逐 cohort 檢查**（`expected` 為 Mapping 時）：每個 cohort 的推導集合
    必須 ⊆ **該 cohort** 的核准 `nodes`；超出 ⇒ 自我否決。
    ⚠️ 方向是單向的：**推導 ⊊ 核准是正常且允許的**（核准清單通常比四條件
    推導多，因「被背書過才退役」的成員不會被推導出來）；**推導 ⊋ 核准才是違規**。

    `expected` 為平面集合（舊 API）時，退回「聯集」語意：該集合的每個元素
    都必須落在**某個** cohort 的核准清單內。
    """
    cohorts_n = normalize_cohorts(cohorts)
    approved_by_date = {c.get("date"): set(c.get("nodes") or ()) for c in cohorts_n}
    approved_all = approved_retirement_ids(cohorts_n)
    if isinstance(expected, dict):
        msgs = []
        for date, derived in expected.items():
            approved = approved_by_date.get(date)
            if approved is None:
                msgs.append("⓪ cohort %s 不在核准台帳內（規則被放寬）：%s"
                            % (date, sorted(derived)))
                continue
            extra = sorted(x for x in (derived or ()) if x not in approved)
            if extra:
                msgs.append("⓪ cohort %s 的預期退役集合超出核准基線"
                            "（規則被放寬 **或台帳待刷新**——新成員已入圖但未入台帳）：%s"
                            % (date, extra))
        return msgs
    extra = sorted(x for x in (expected or ()) if x not in approved_all)
    if extra:
        return ["⓪ 預期退役集合超出核准基線（規則被放寬）：%s" % extra]
    return []


# 🔴 弱化語氣裝飾的還原：`X` ⇒ `我隐约觉得「X」`（投影層渲染，見
#    `emergent_projection.project_emergent`）。去掉裝飾後即可比較「正文本身」
#    有沒有被改動。
#
# 🔴 2026-10-25 實測修正（假紅來源）：投影層的裝飾是加在**整個 content 的
#    頭尾**，而 content 本身**可能含換行**（實測：2026-10-18 cohort 的 value
#    內容有 2～4 個換行）⇒ 舊的 regex（`.` 不跨行）**抓不到** ⇒ 正文被誤判成
#    「被改動」⇒ 假紅（agent_akane / agent_ruka 每次夜槽都中招）。
#    ⇒ 改成「**逐投影項**」處理：投影層把每個 item 渲染成 `- [type] <content>`，
#    裝飾只加在**該 item 的頭尾** ⇒ 可精確還原（多行內容也正確）。
_PROJECTION_ITEM_RE = re.compile(r"(?m)^(- \[[a-z]+\] )")
_WEAKENING_DECORATION_RE = re.compile(re.escape(WEAKENING_TONE_PREFIX) + r"「(.*?)」",
                                      re.DOTALL)


def _strip_item_decoration(body: str) -> str:
    """還原**單一投影項**的弱化裝飾（精確：只去頭尾的 prefix 與「」）。

    投影層的渲染順序是 `content.strip()` → 加裝飾 → `- [type] <content>.strip()`，
    ⇒ 裝飾一定是 `<prefix>「` 開頭、`」` 結尾（含項目分隔的換行）。
    """
    opening = WEAKENING_TONE_PREFIX + "「"
    if not body.startswith(opening):
        return body
    rest = body[len(opening):]
    trimmed = rest.rstrip()
    if not trimmed.endswith("」"):
        return body
    # 去掉裝飾的收尾「」，保留其後的換行分隔符。
    return trimmed[:-1] + rest[len(trimmed):]


def strip_weakening_decoration(text: str) -> str:
    """把弱化語氣裝飾還原（`我隐约觉得「X」` ⇒ `X`）。

    用途：判斷「投影正文的差異是否**只**來自弱化裝飾」。
    🔴 這是**比較用**的正規化，不是判定規則本身。
    """
    if not text:
        return ""
    parts = _PROJECTION_ITEM_RE.split(text)
    if len(parts) == 1:
        # 沒有投影項標記（純文字 fixture）⇒ 用 regex 還原。
        return _WEAKENING_DECORATION_RE.sub(r"\1", text)
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        out.append(parts[i])
        out.append(_strip_item_decoration(parts[i + 1]))
    return "".join(out)


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


def is_expected_metabolism(event, expected_ids, not_before=None, types=None):
    """某一筆新轉變是否屬「預期生命週期代謝」（Owner 2026-10-23「改判準」）。

    四條件**全中**才成立（缺一 ⇒ 不是預期代謝 ⇒ 破線）：
      1. `node_type` **等於台帳記載的型別**（Owner 2026-10-27 型別中立化：
         未記載者視為 `"value"`；**不得**放寬成「只要在核准清單就放行」）
      2. `lifecycle_state == "weakening"`（**嚴格**；dormant／superseded 一律不是）
      3. `node_id` 落在**核准清單**（台帳該 cohort 的 `nodes`）內
      4. **轉變時刻 ≥ 該 cohort 的退役窗下界**（`not_before`）——
         早於該槽的弱化是「提前早衰」，必須熔斷報警。

    🔴 **時間下界是逐節點的**（2026-10-25 主大腦語意修正 A）：每個 cohort 有
    自己的 `not_before`，不再是全體共用一個常數。`not_before` 參數可為：
      - `None`（預設）⇒ 由台帳推導「節點 → 該 cohort 的 not_before」；
      - `str` ⇒ 舊 API：全體共用同一個下界；
      - `Mapping` ⇒ 節點 → 下界。

    🔴 時刻不可解析或無時區 ⇒ **不放行**（fail-closed）：
       判準層不做任何「距今 N 天」型算術，只比對**宣告式時刻常數**。
    """
    if not isinstance(event, dict):
        return False
    node_id = event.get("node_id")
    # 🔴 型別中立化：型別條件比對「**台帳記載的型別**」（未記載 ⇒ "value"）。
    if event.get("node_type") != _recorded_node_type(node_id, types):
        return False
    if event.get("lifecycle_state") != "weakening":
        return False
    if node_id not in (expected_ids or ()):
        return False
    if isinstance(not_before, str):
        bound = not_before
    else:
        mapping = retirement_bounds() if not_before is None else not_before
        bound = (mapping or {}).get(node_id)
        if not bound:
            # 🔴 fail-closed：查不到該節點的核准退役槽 ⇒ 不放行。
            return False
    if bound:
        try:
            dt = datetime.fromisoformat(str(event.get("ts") or ""))
            bound_dt = datetime.fromisoformat(str(bound))
        except Exception:
            return False
        if dt.tzinfo is None or bound_dt.tzinfo is None:
            return False
        if dt < bound_dt:
            return False
    return True


def select_expected_hits(events, expected_ids, not_before=None, types=None):
    """本次「命中」的預期代謝（核准清單內、**台帳記載型別相符**者，在其退役窗
    之後轉入 weakening）。

    🔴 與 `is_expected_metabolism` **同源**（不得各自實作條件）——
       守衛 `test_select_expected_hits_agrees_with_predicate` 會轉紅。
    """
    return [e for e in (events or [])
            if is_expected_metabolism(e, expected_ids, not_before, types)]


def _is_pending_retirement(event, node_by_id, supported, ledger_dates,
                            approved_ids=None) -> bool:
    """某一筆靈魂型弱化是否屬「**未核准**的退役批次」（pending／待審）。

    🔴 **窄版判準（Owner 2026-10-23 一票否決條款的延伸，不得放寬）**：
    pending **不是**「任何未被核准的弱化」——那會架空一票否決。
    pending 必須**同時**滿足下列全部條件：
      1. `node_type` 屬靈魂型（`belief`／`value`／`trait`／`essence`）
         —— Owner 2026-10-27 型別中立化：**不再只認 value**。
      2. `lifecycle_state == "weakening"`（**嚴格**）
      3. 節點**存在**於圖譜中（查不到 ⇒ fail-closed ⇒ 算 breaks）
      4. `last_support_ts == created_ts`（建立後從未被背書）
      5. 沒有任何 `trigger_type == "support_propagation"` 的邊指向它
      6. 事件 `ts` 可解析**且帶時區**
      7. **該節點尚未入帳**（已入帳者若未命中 expected，例如「提前早衰」
         `ts < not_before`，是**破線**，不得被 pending 掩蓋）
      8. 若型別是 `value` ⇒ 節點的**建立日不得在台帳內**（台帳對該日的 value
         集合已逐筆審過 ⇒ 未列入者視為被否決 ⇒ 判 breaks）。**非 value 型別
         不受此條約束**（Owner 裁定：belief／trait／essence **嚴禁自動入帳**，
         必須大聲回報待人工審定）。

    ⇒ 未來新 cohort 裡「被背書過才退役」的成員（不滿足條件 4/5）會被判成
    **breaks（FAIL）**——這是**刻意保留的嚴格性**，不是缺陷。
    """
    if not isinstance(event, dict):
        return False
    if event.get("node_type") not in SOUL_TYPES:
        return False
    if event.get("lifecycle_state") != "weakening":
        return False
    node_id = event.get("node_id")
    node = node_by_id.get(node_id)
    if not isinstance(node, dict):
        return False
    created = node.get("created_ts") or ""
    if not isinstance(created, str) or len(created) < 10:
        return False
    if (node.get("last_support_ts") or "") != created:
        return False
    if node_id in supported:
        return False
    # 🔴 條件 7a：已入帳者若未命中 expected（例如「提前早衰」）⇒ **破線**，
    #    不得被 pending 掩蓋。
    if approved_ids is not None and node_id in approved_ids:
        return False
    # 🔴 條件 7b：**value 的日期級一票否決條款**（Owner 2026-10-23）保留；
    #    非 value 型別不受此條約束（Owner 2026-10-27：嚴禁自動入帳、必須待審）。
    if event.get("node_type") == "value" and created[:10] in ledger_dates:
        return False
    try:
        dt = datetime.fromisoformat(str(event.get("ts") or ""))
    except Exception:
        return False
    if dt.tzinfo is None or dt.utcoffset() is None:
        return False
    return True


def classify_soul_retirements(events, nodes, edges, cohorts=None,
                               expected_ids=None, not_before=None):
    """把靈魂類轉變分成三類：``(expected, pending, breaks)``。

    - **expected**：已核准 cohort（台帳核准清單內）＋ **型別等於台帳記載的型別**
      ＋ `lifecycle_state == "weakening"`（嚴格）＋ `ts >= 該 cohort 的
      not_before`（時刻不可解析或無時區 ⇒ 不放行）⇒ 預期代謝。
    - **pending**：四條件**同構**（靈魂型 ＋ 節點存在 ＋ `last_support_ts ==
      created_ts` ＋ 無 `support_propagation` 祖先邊 ＋ 嚴格 weakening ＋
      ts 可解析帶時區）**且尚未入帳** ⇒ 「未核准的退役批次」
      （不是破線、也不是 PASS；必須**大聲回報**並等主大腦／Owner 核准）。
    - **breaks**：其餘任何**非 pattern** 轉變（型別與台帳記載不符、已入帳卻
      提前早衰、未知型別，含 dormant／superseded 目標）⇒ 真破線。

    ⚠️ `pattern` 轉變**不屬於**任何一類（它是正常演化，不是靈魂類退役）。
    ⚠️ `expected_ids` 為 None 時，由「台帳核准清單 ∩ 四條件推導」決定資格；
       呼叫端（`assess`／`main`）會明確傳入，以保證與判定同源。
    """
    cohorts_n = normalize_cohorts(cohorts)
    bounds = retirement_bounds(cohorts_n) if not_before is None else not_before
    node_by_id = {n.get("node_id"): n for n in (nodes or []) if isinstance(n, dict)}
    supported = {e.get("node_id") for e in (edges or [])
                 if isinstance(e, dict) and e.get("trigger_type") == "support_propagation"}
    ledger_dates = {c.get("date") for c in cohorts_n}
    # 🔴 台帳記載的型別（型別中立化）：expected 的型別條件以它為依歸。
    types = approved_node_types(cohorts_n)
    if expected_ids is None:
        derived = expected_retirement_sets_by_cohort(nodes, edges, cohorts_n)
        approved = {c.get("date"): set(c.get("nodes") or ()) for c in cohorts_n}
        expected_ids = set()
        for date, found in derived.items():
            expected_ids |= (found & approved.get(date, set()))
    expected, pending, breaks = [], [], []
    for e in events or ():
        if not isinstance(e, dict):
            continue
        if e.get("node_type") == "pattern":
            continue          # 正常演化：不屬於任何一類
        if is_expected_metabolism(e, expected_ids, bounds, types):
            expected.append(e)
        elif _is_pending_retirement(e, node_by_id, supported, ledger_dates,
                                    expected_ids):
            pending.append(e)
        else:
            breaks.append(e)
    return expected, pending, breaks


def pending_cohort_groups(pending_events, nodes) -> list:
    """把 pending 事件**依建立日分組** → ``[{"date", "node_ids", "count"}]``。"""
    node_by_id = {n.get("node_id"): n for n in (nodes or []) if isinstance(n, dict)}
    groups = {}
    for e in pending_events or ():
        node = node_by_id.get(e.get("node_id")) or {}
        created = node.get("created_ts") or ""
        date = created[:10] if isinstance(created, str) and len(created) >= 10 else "?"
        groups.setdefault(date, set()).add(e.get("node_id"))
    return [{"date": d, "node_ids": sorted(x for x in ids if x), "count": len(ids)}
            for d, ids in sorted(groups.items())]


class AssessmentResult(list):
    """`assess()` 的回傳值。

    它**是一個 list**（失敗項清單）——既有守衛的 `assertEqual(fails, [])` 與
    `any("…" in f for f in fails)` 契約**完全不變**；同時攜帶三分類明細：

      - `.pending_retirements`  ⇒ 待核准的退役批次（pending）
      - `.expected_retirements` ⇒ 已核准的預期代謝（expected）
      - `.break_retirements`    ⇒ 真破線（breaks）
    """

    def __init__(self, fails=(), pending_retirements=(),
                 expected_retirements=(), break_retirements=()):
        super().__init__(fails)
        self.pending_retirements = list(pending_retirements)
        self.expected_retirements = list(expected_retirements)
        self.break_retirements = list(break_retirements)


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
    retirement_not_before=None,
    elevation_nodes: list = None,
    elevation_edges: list = None,
    cohorts=None,
) -> AssessmentResult:
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

    🔴 **三分類（Owner 2026-10-23「改判準」＋ 2026-10-25 台帳化
    ＋ 2026-10-27 型別中立化）**：
      - **expected**：核准清單內、且**型別等於台帳記載型別**的節點轉入
        `weakening`（且落在其 cohort 的退役窗之後）⇒ 預期代謝，**不是**破線、
        **不**計入關鍵反例。
      - **pending**：四條件同構但**尚未入帳**的靈魂型弱化 ⇒「未核准的退役批次」
        ⇒ **不是**破線、**不是** PASS；必須大聲回報。
      - **breaks**：其餘任何靈魂類轉變（型別與台帳記載不符、已入帳卻提前早衰、
        value 且建立日已在台帳內、未知型別）⇒ **破線**。
    弱化包裝的**允許增量 ＝ expected ＋ pending 的筆數**（兩者都是「退役的
    投影後果」；等於則不報）。
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

    # ── 分類每一筆新轉變（三分類；Owner 2026-10-23「改判準」＋台帳化）──────
    #   pattern                    ⇒ 正常演化，永遠不是破線
    #   value→weakening 且在核准清單內且在退役窗之後 ⇒ EXPECTED（預期代謝）
    #   value→weakening 且四條件同構且建立日不在台帳 ⇒ PENDING（待核准）
    #   其餘靈魂類／未知類型／提前早衰 ⇒ BREAKS ⇒ 破線
    expected_ids = set(expected_retirement or ())
    expected_events, pending_events, break_events = classify_soul_retirements(
        new_events, elevation_nodes or [], elevation_edges or [],
        cohorts=cohorts, expected_ids=expected_ids, not_before=retirement_not_before)
    expected_count = len(expected_events)
    pending_count = len(pending_events)
    soul_hits = {}
    non_pattern = {}
    bad_target = {}
    key_hits = []
    # ① 靈魂類型的誤傷統計：只統計 **breaks**（expected 是預期代謝、
    #    pending 是待核准批次 ⇒ 兩者都不是「靈魂結構誤傷」）。
    for e in break_events:
        ntype = e.get("node_type")
        if ntype != "pattern":
            if ntype in soul_types:
                soul_hits[ntype] = soul_hits.get(ntype, 0) + 1
            non_pattern[ntype] = non_pattern.get(ntype, 0) + 1
    # ② 目標狀態 ＋ 關鍵反例：掃描**全部**新轉變（**含 pattern**），
    #    只有「預期代謝」才豁免。
    #    🔴 目標狀態一律檢查：退役＝弱化，不是休眠/取代（**即使在核准清單內**）。
    ledger_types = approved_node_types(cohorts)
    for e in new_events:
        if is_expected_metabolism(e, expected_ids, retirement_not_before,
                                  ledger_types):
            continue
        state = e.get("lifecycle_state")
        if state in ("dormant", "superseded"):
            bad_target[state] = bad_target.get(state, 0) + 1
        # 🔴 關鍵反例：只有**不屬於**預期代謝的轉變才報破線。
        if _matches_key_node(e.get("node_id"), key_node):
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
    # 🔴 弱化包裝的允許增量 ＝ EXPECTED ＋ PENDING 的筆數（等於則不報）。
    allowed_weak_delta = expected_count + pending_count
    if weak_after - weak_before != allowed_weak_delta:
        fails.append("④ 弱化包裝計數 %d → %d" % (weak_before, weak_after))
    return AssessmentResult(
        fails,
        pending_retirements=pending_events,
        expected_retirements=expected_events,
        break_retirements=break_events,
    )


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
            # 🔴 exit 2 ＝「硬拒絕」（判定中止、不產報告）；exit 3 保留給
            #    PENDING_COHORT ⇒ 這裡**不可**用 3，否則兩個語意會撞碼。
            print("[refuse] 沙盒不在 temp 之下（%s）—— 內部錯誤，中止。" % sandbox,
                  file=sys.stderr)
            return 2

        copy_graph(source, sandbox)
        sidecar = sandbox / LIFECYCLE_EVENTS_FILENAME
        nodes = read_jsonl(sandbox / NODES_FILENAME)
        edges = read_jsonl(sandbox / EDGES_FILENAME)
        # 🔴 預期退役 **cohort 台帳**（Owner 2026-10-23「改判準」＋ 2026-10-25 台帳化）：
        #   - `expected_retirement` ＝**台帳核准清單**（資格來源；expected 的依據）。
        #   - `derived_by_cohort` ＝四條件推導（規則的自我描述；用於自我否決）。
        #    `baseline_violation` 是**執行期**保險：規則若被放寬，CLI 會自己喊 FAIL。
        #    🔴 自我否決是**逐 cohort** 的：每個 cohort 的推導集合必須 ⊆ 該 cohort
        #       的核准清單（推導較小是正常的：核准清單通常還包含「被背書過才退役」
        #       的成員，那 9 顆不會被四條件推導出來）。
        expected_retirement = approved_retirement_ids()
        # 🔺 評估時鐘上界（2026-10-25 主大腦修正 B）：推導集合只含「在評估時刻
        #   **已經存在**」的節點 ⇒ 用過去的槽時刻評估「已成長的圖」時，不會把
        #   未來才存在的節點算進去（否則自我否決會因圖的成長而假紅）。
        _clock_bound = simulated_time or datetime.now().astimezone().isoformat()
        derived_by_cohort = expected_retirement_sets_by_cohort(
            nodes, edges, not_after=_clock_bound)
        expected_derived = set()
        for _s in derived_by_cohort.values():
            expected_derived |= _s
        baseline_fails = baseline_violation(derived_by_cohort)
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

        # 🔴 本次「命中」的預期代謝（核准清單內 value 轉入 weakening **且在退役窗之後**）。
        #    與 `expected_retirement`（集合）分開報：集合是「誰有資格」，
        #    命中是「這次真的轉了幾筆」—— 弱化包裝的允許增量看的是命中數。
        #    🔴 與 `assess()` 的分類**同源**（同一個 `is_expected_metabolism`）。
        expected_hits = select_expected_hits(new_events, expected_retirement,
                                              types=approved_node_types())
        # 🔴 三分類（Owner 2026-10-25）：pending（未核准的退役批次）必須被
        #    **獨立分類、大聲回報**，不得混進「破線」，也不得靜默算成 PASS。
        expected_events, pending_events, _break_events = classify_soul_retirements(
            new_events, nodes, edges, expected_ids=expected_retirement)
        # 每個 agent 各自命中幾筆（用來判斷投影差異能否被預期代謝**完全解釋**）
        node_agent = {n.get("node_id"): n.get("agent_id")
                      for n in nodes if isinstance(n, dict)}
        expected_by_agent = {}
        for e in expected_events:
            ag = node_agent.get(e.get("node_id"))
            expected_by_agent[ag] = expected_by_agent.get(ag, 0) + 1
        # 🔴 pending 與 expected 都是「退役的投影後果」⇒ 弱化包裝的允許增量
        #    與投影差異的窄放行，兩者**合併計算**（但放行條件本身不放寬）。
        pending_by_agent = {}
        for e in pending_events:
            ag = node_agent.get(e.get("node_id"))
            pending_by_agent[ag] = pending_by_agent.get(ag, 0) + 1

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
                #    🔴 允許計數 ＝ expected ＋ pending（兩者都是退役的投影後果）；
                #    但**放行條件本身不放寬**（命中數>0 ＋ 前綴增量==命中數 ＋
                #    去裝飾後正文逐位元相同，三者缺一仍報破線）。
                diff["explained_by_expected_metabolism"] = prompt_diff_is_expected(
                    diff, expected_by_agent.get(a, 0) + pending_by_agent.get(a, 0),
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

        assessment = assess(
            new_events=new_events,
            prompt_diffs=prompt_diffs,
            key_node=args.key_node,
            weak_before=weak_total_before,
            weak_after=weak_total_after,
            graph_nodes=graph_nodes,
            eval_error=eval_error,
            undelivered=undelivered,
            expected_retirement=expected_retirement,
            elevation_nodes=nodes,
            elevation_edges=edges,
        )
        fails = list(baseline_fails) + list(assessment)
        pending_retirements = assessment.pending_retirements
        pending_cohort_list = pending_cohort_groups(pending_retirements, nodes)

        # 🔴 **三態判定**（Owner 2026-10-23 裁定「改判準」＋ 2026-10-25 台帳化）：
        #   PASS (0)          ⇒ 沒有破線、沒有待核准批次
        #   FAIL (1)          ⇒ 真破線（一票否決，行為不變）
        #   PENDING_COHORT (3)⇒ 沒有破線，但有「未核准的退役批次」⇒ 大聲回報
        #   exit 2 保留給「硬拒絕」（時戳無時區等），**不挪用**。
        # ⚠️ pending **絕對不可**靜默算成 PASS，也**絕對不可**靜默算成破線。
        if fails:
            verdict = "FAIL"
            exit_code = 1
        elif pending_retirements:
            verdict = "PENDING_COHORT"
            exit_code = 3
        else:
            verdict = "PASS"
            exit_code = 0

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
            "expected_retirement_derived": sorted(expected_derived),
            "expected_retirement_derived_count": len(expected_derived),
            "expected_hits_count": len(expected_hits),
            "pending_cohorts": pending_cohort_list,
            "pending_retirements_count": len(pending_retirements),
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
            "verdict": verdict,
            "exit_code": exit_code,
            "failures": fails,
        }

        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            # 🔴 預期退役集合明細（Owner 2026-10-23「改判準」＋ 2026-10-25 台帳化）
            #    「預期退役集合」＝**台帳核准清單**（資格來源）；「推導集合」＝
            #    四條件推導（自我否決用，⊆ 核准清單）。
            retirement_lines = [
                "預期退役集合（EXPECTED_METABOLISM）= %d 筆（核准台帳 %d 個 cohort）"
                % (len(expected_retirement), len(normalize_cohorts())),
            ]
            for _c in normalize_cohorts():
                retirement_lines.append(
                    "  cohort %s : 核准 %d 筆  not_before=%s"
                    % (_c.get("date"), len(_c.get("nodes") or ()),
                       _c.get("not_before")))
            retirement_lines.append(
                "推導集合（四條件推導，自我否決用）= %d 筆" % len(expected_derived))
            node_info = {n.get("node_id"): n for n in nodes if isinstance(n, dict)}
            for _nid in sorted(expected_derived):
                _info = node_info.get(_nid) or {}
                retirement_lines.append(
                    "  %s  type=%s  agent=%s"
                    % (_nid, _info.get("node_type", "?"), _info.get("agent_id", "?")))
            retirement_lines.append(
                "本次命中（轉入 weakening）= %d 筆" % len(expected_hits))
            # 🔴 pending（未核准的退役批次）必須**大聲回報**（Owner 2026-10-23
            #    「改判準」：放行必須有界、可被推翻、不留死角）。
            if pending_cohort_list:
                retirement_lines.append(
                    "pending_cohorts = %s"
                    % json.dumps(pending_cohort_list, ensure_ascii=True))
                retirement_lines.append(
                    "核准指示（CLI 不得自行核准）：在 scripts/eval_lifecycle_cli.py 的 "
                    "EXPECTED_RETIREMENT_COHORTS 追加 "
                    '{"date": "<建立日>", "not_before": "<該 cohort 的退役槽 UTC 時刻>", '
                    '"nodes": frozenset({...})} —— 由主大腦依實測核准後生效。')
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
                "expected_hits         = %d" % len(expected_hits),
                "pending_retirements_count = %d" % len(pending_retirements),
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
            "- 預期退役集合（EXPECTED_METABOLISM，**台帳核准清單**）：%d 筆"
            % len(expected_retirement),
            "- 推導集合（四條件推導，自我否決用；必須 ⊆ 核准清單）：%d 筆"
            % len(expected_derived),
            "- 本次命中（轉入 weakening）：%d 筆" % len(expected_hits),
            "- pending（**未核准**的退役批次）：%d 筆" % len(pending_retirements),
            "- pending_cohorts = %s"
            % json.dumps(pending_cohort_list, ensure_ascii=True),
            "- 核准指示（CLI 不得自行核准）：在 scripts/eval_lifecycle_cli.py 的 "
            "EXPECTED_RETIREMENT_COHORTS 追加 "
            '{"date": "<建立日>", "not_before": "<該 cohort 的退役槽 UTC 時刻>", '
            '"nodes": frozenset({...})} —— 由主大腦依實測核准後生效。',
            "- 靈魂結構轉變（**非預期代謝者**必須全 0）：%s" % result["soul_type_transitions"],
            "- 關鍵反例 `%s` 轉變次數（**非預期代謝時**必須 0）：%d"
            % (args.key_node, result["key_node_transitions"]),
            "- 弱化包裝計數：%d → %d" % (weak_total_before, weak_total_after),
            "- Prompt 正文差異：%s" % (prompt_diffs or "無"),
            "- Prompt 正文差異（**無法由預期代謝解釋** ⇒ 觸發 ④）：%d 筆"
            % result["prompt_diffs_unexplained"],
            "- **判定：%s**（exit code %d；PASS=0 / FAIL=1 / PENDING_COHORT=3）"
            % (result["verdict"], exit_code),
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
        return exit_code
    finally:
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
