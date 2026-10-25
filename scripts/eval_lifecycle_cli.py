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
        "nodes": frozenset({
            "171be517538945abb126ea66af34aa50",  # agent_aoi
            "33194d9da2fb4bd1860c69638d4321d2",  # agent_mahiru
            "536049d72ce44c9cb065b3c3575cdd75",  # agent_ram
            "6d6de3b3f24041a4ac2c4f246c749aeb",  # agent_mai
            "74406ee863574f0398b02bed1de3e9d5",  # agent_yua
            "7906aef95d55426789ed21c996d4050c",  # agent_akane
            "9a1daf40ee9f4103a3e2e380a64ea9e2",  # agent_rem
            "b031a9fb32f64bf5a7418073e3adab34",  # agent_anna
            "b82bd21bcf1c44e38c52ff72361309d1",  # agent_miku
            "cbd28b1afed34da484b93c666c51dcaa",  # agent_ruka
        }),
    },
    {
        "date": "2026-10-21",
        "not_before": "2026-10-29T02:00:00+00:00",
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
        }),
    },
    {
        "date": "2026-10-22",
        "not_before": "2026-10-30T02:00:00+00:00",
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
            "37cd3ff1ab2d4ef7a3aaeb4c79ce2cee",  # agent_rio
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
        "nodes": frozenset({
            "002232c610504a13a9cebcd2a34bf37d",  # agent_rio
            "0567db7ab97940e8b9f7457d547aac45",  # agent_kaori
            "10e4ca38ec8c4024ace5ffe6f8eec26d",  # agent_anna
            "1382b88a10374e35b29f8a07ded74ad8",  # agent_mahiru
            "240a059732284414a4b522702024cd7e",  # agent_ai
            "28eb6e3216714b17a3aae0f64b55406e",  # agent_reze
            "2bf15a92392a4362a618db172bea0bd4",  # agent_tsukasa
            "38a0651fdce546709f544f03bbdb793a",  # agent_kurisu
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
            "fe4484bef142463dbe62dd55b9f56dd8",  # agent_iroha
        }),
    },
    {
        "date": "2026-10-24",
        "not_before": "2026-11-01T02:00:00+00:00",
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
    """把台帳正規化成 ``[{"date", "not_before", "nodes"}, ...]``（順序穩定）。"""
    src = EXPECTED_RETIREMENT_COHORTS if cohorts is None else cohorts
    out = []
    for c in src or ():
        if not isinstance(c, dict):
            continue
        out.append({
            "date": c.get("date"),
            "not_before": c.get("not_before"),
            "nodes": frozenset(c.get("nodes") or ()),
        })
    return out


def retirement_bounds(cohorts=None) -> dict:
    """節點 id → 該節點所屬 cohort 的**最早可退役槽**（逐節點）。

    🔴 判準層只比對**宣告式時刻常數**（`not_before`），不做任何
    「距今 N 天」型算術（守衛 `test_cli_does_not_import_decay_thresholds`）。
    """
    bounds = {}
    for c in normalize_cohorts(cohorts):
        bound = c.get("not_before")
        for nid in c.get("nodes") or ():
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
      1. node_type == "value"
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
        found = set()
        for n in nodes or ():
            if not isinstance(n, dict) or n.get("node_type") != "value":
                continue
            created = n.get("created_ts") or ""
            if not isinstance(created, str) or created[:10] != date:
                continue
            if not_after is not None and _created_after(created, not_after):
                continue          # 晚於評估時鐘 ⇒ 當時尚未存在 ⇒ 不列入推導
            if (n.get("last_support_ts") or "") != created:
                continue
            nid = n.get("node_id")
            if not nid or nid in supported:
                continue
            found.add(nid)
        out[date] = found
    return out


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


def is_expected_metabolism(event, expected_ids, not_before=None):
    """某一筆新轉變是否屬「預期生命週期代謝」（Owner 2026-10-23「改判準」）。

    四條件**全中**才成立（缺一 ⇒ 不是預期代謝 ⇒ 破線）：
      1. `node_type == "value"`
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
    if event.get("node_type") != "value":
        return False
    if event.get("lifecycle_state") != "weakening":
        return False
    node_id = event.get("node_id")
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


def select_expected_hits(events, expected_ids, not_before=None):
    """本次「命中」的預期代謝（核准清單內 value 在其退役窗之後轉入 weakening）。

    🔴 與 `is_expected_metabolism` **同源**（不得各自實作條件）——
       守衛 `test_select_expected_hits_agrees_with_predicate` 會轉紅。
    """
    return [e for e in (events or [])
            if is_expected_metabolism(e, expected_ids, not_before)]


def _is_pending_retirement(event, node_by_id, supported, ledger_dates) -> bool:
    """某一筆 value 弱化是否屬「**未核准**的退役批次」（pending）。

    🔴 **窄版判準（Owner 2026-10-23 一票否決條款的延伸，不得放寬）**：
    pending **不是**「任何未被核准的 value 弱化」——那會架空一票否決。
    pending 必須**同時**滿足下列全部條件：
      1. `node_type == "value"`
      2. `lifecycle_state == "weakening"`（**嚴格**）
      3. 節點**存在**於圖譜中（查不到 ⇒ fail-closed ⇒ 算 breaks）
      4. `last_support_ts == created_ts`（建立後從未被背書）
      5. 沒有任何 `trigger_type == "support_propagation"` 的邊指向它
      6. 事件 `ts` 可解析**且帶時區**
      7. 節點的**建立日不在台帳內**（台帳內的 cohort 要嘛已核准、要嘛就是破線）

    ⇒ 未來新 cohort 裡「被背書過才退役」的成員（不滿足條件 4/5）會被判成
    **breaks（FAIL）**——這是**刻意保留的嚴格性**，不是缺陷。
    """
    if not isinstance(event, dict):
        return False
    if event.get("node_type") != "value":
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
    if created[:10] in ledger_dates:
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

    - **expected**：已核准 cohort（台帳核准清單內）＋ `node_type == "value"`
      ＋ `lifecycle_state == "weakening"`（嚴格）＋ `ts >= 該 cohort 的
      not_before`（時刻不可解析或無時區 ⇒ 不放行）⇒ 預期代謝。
    - **pending**：四條件**同構**（value ＋ 節點存在 ＋ `last_support_ts ==
      created_ts` ＋ 無 `support_propagation` 祖先邊 ＋ 嚴格 weakening ＋
      ts 可解析帶時區）**且建立日不在台帳內** ⇒ 「未核准的退役批次」
      （不是破線、也不是 PASS；必須**大聲回報**並等主大腦核准）。
    - **breaks**：其餘任何**非 pattern** 轉變（belief/value/trait/essence、
      未知型別，含 dormant／superseded 目標）⇒ 真破線。

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
        if is_expected_metabolism(e, expected_ids, bounds):
            expected.append(e)
        elif _is_pending_retirement(e, node_by_id, supported, ledger_dates):
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

    🔴 **三分類（Owner 2026-10-23「改判準」＋ 2026-10-25 台帳化）**：
      - **expected**：核准清單內的 value 節點轉入 `weakening`（且落在其
        cohort 的退役窗之後）⇒ 預期代謝，**不是**破線、**不**計入關鍵反例。
      - **pending**：四條件同構但**建立日不在台帳內**的 value 弱化 ⇒
        「未核准的退役批次」⇒ **不是**破線、**不是** PASS；必須大聲回報。
      - **breaks**：其餘任何靈魂類（belief/value/trait/essence，含未核准的
        value）與未知型別轉變 ⇒ **破線**。
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
    for e in new_events:
        if is_expected_metabolism(e, expected_ids, retirement_not_before):
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
        expected_hits = select_expected_hits(new_events, expected_retirement)
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
