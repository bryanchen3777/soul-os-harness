"""
tests/agent/test_agent_registry_land0_batch2.py
LAND-0 Batch 2 — 12 隻第二批角色落地的「註冊面一致性守衛」。

工單：logs/LAND-0-BATCH2-IMPLEMENTATION.md（本票）／logs/DSH-SHADOW-0-WORK-ORDER.md（陰影層規格）
風格比照：tests/agent/test_agent_registry_land0_batch1.py

守衛（本票 §6）：
  1. 註冊面一致性：12 隻在六個面（personas/ 檔、consciousness 子類、AGENT_CLASS_MAP、
     configs/default.yaml 的 agents、BASE_SCORES、proxy 三張表）全部齊備。
  2. 總數：全庫註冊數 = 46（既有 34 ＋ 本批 12）。
  3. 類名規則：`Agent` ＋ CamelCase(agent_id) 逐字成立。
  4. 位元等價：personas/agent_<id>.md 剝掉「## 陰影條款」段後，與素材位元相同。
  5. 陰影條款形狀：`^## 陰影條款` 恰 1 個、含「陰影不劇場化」、紅線 ≥ 3 條、①②③④⑤⑥ 齊備。
  6. NO_DIARY_AGENTS 恰為 5 筆（含 agent_ram；不含 agent_emilia）。
  7. 既有 34 隻零回歸（configs / class map / BASE_SCORES / proxy 三表的既有鍵與值未動）。
  8. proxy 三表鍵數 46/46/46 且鍵集完全相同（雙向相等）。

⚠️ 本檔不得使用 subprocess / os.spawn / os.system / os.popen（R5 護欄），
   也不得硬寫生產服務位址（R3 守衛）。`src.llm.proxy` 的 import 必須在測試函式內
   （延後到 autouse 的資料根隔離 fixture 生效之後），避免匯入期碰到生產資料根。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DRAFTS_DIR = REPO_ROOT / "docs" / "persona-drafts"
PERSONAS_DIR = REPO_ROOT / "personas"

SHADOW_HEADING = "## 陰影條款"
SHADOW_ATTRIBUTION = "陰影條款為 Owner 裁定 2026-10-04 新增（工單 SHADOW-0——logs/DSH-SHADOW-0-WORK-ORDER.md）。"

# ─────────────────────────────────────────────────────────────────────────────
# 資料表
# ─────────────────────────────────────────────────────────────────────────────

#: (agent_id, 類別名, (base, jitter), display-name, short-name, 中文名)
BATCH2 = [
    ("agent_beatrice", "AgentBeatrice", (0.30, 0.20), "Beatrice", "beatrice", "碧翠絲"),
    ("agent_chizuru", "AgentChizuru", (0.52, 0.30), "Chizuru", "chizuru", "水原千鶴"),
    ("agent_emilia", "AgentEmilia", (0.58, 0.33), "Emilia", "emilia", "艾蜜莉亞"),
    ("agent_ichika", "AgentIchika", (0.60, 0.32), "Ichika", "ichika", "中野一花"),
    ("agent_itsuki", "AgentItsuki", (0.50, 0.30), "Itsuki", "itsuki", "中野五月"),
    ("agent_mami", "AgentMami", (0.35, 0.25), "Mami", "mami", "七海麻美"),
    ("agent_nagisa", "AgentNagisa", (0.45, 0.30), "Nagisa", "nagisa", "水瀬渚"),
    ("agent_nino", "AgentNino", (0.52, 0.30), "Nino", "nino", "中野二乃"),
    ("agent_rika", "AgentRika", (0.62, 0.35), "Rika", "rika", "星崎理香"),
    ("agent_saki", "AgentSaki", (0.68, 0.38), "Saki", "saki", "佐木咲"),
    ("agent_shino", "AgentShino", (0.50, 0.30), "Shino", "shino", "桐生紫乃"),
    ("agent_yotsuba", "AgentYotsuba", (0.72, 0.40), "Yotsuba", "yotsuba", "中野四葉"),
]

BATCH2_IDS = [row[0] for row in BATCH2]
BATCH2_CLASSES = {row[0]: row[1] for row in BATCH2}
BATCH2_SCORES = {row[0]: row[2] for row in BATCH2}
BATCH2_DISPLAY = {row[0]: row[3] for row in BATCH2}
BATCH2_SHORT = {row[0]: row[4] for row in BATCH2}
BATCH2_CN = {row[0]: row[5] for row in BATCH2}

#: 本票（PROXY-TABLE-COMPLETE）補齊的既有 7 隻：原本不在 AGENT_NAMES / AGENT_SHORT_NAMES，
#: 值依 personas/agent_<id>.md 素材首行推導（Romaji given name，與既有 "Akane" 式一致）。
FILLED_7 = {
    "agent_anna": "Anna",
    "agent_aoi": "Aoi",
    "agent_mahiru": "Mahiru",
    "agent_mai": "Mai",
    "agent_miku": "Miku",
    "agent_ram": "Ram",
    "agent_rem": "Rem",
}

#: NO_DIARY_AGENTS 最終 5 筆（素材「待實作登記」段的 Diary 政策行逐份核對）。
NO_DIARY_5 = {
    "agent_ram", "agent_beatrice", "agent_mami", "agent_nino", "agent_saki",
}

#: 既有 34 隻在 HEAD（48ade60）的 display-name（27 筆；另 7 筆本票才補齊）。
HEAD_27_DISPLAY = {
    "agent_ai": "星野Ai",
    "agent_akane": "Akane",
    "agent_asuna": "Asuna",
    "agent_fuuka": "Fuuka",
    "agent_haruhi": "Haruhi",
    "agent_hiro": "Hiro",
    "agent_iroha": "Iroha",
    "agent_kaguya": "Kaguya",
    "agent_kana": "Kana",
    "agent_kaori": "Kaori",
    "agent_kirino": "Kirino",
    "agent_kurisu": "Kurisu",
    "agent_makima": "Makima",
    "agent_mashiro": "Mashiro",
    "agent_minami": "Minami",
    "agent_mochizuki_ai": "望月愛",
    "agent_reze": "Reze",
    "agent_rin": "Rin",
    "agent_rio": "Rio",
    "agent_ruka": "Ruka",
    "agent_sachi": "Sachi",
    "agent_sakura": "Sakura",
    "agent_shea": "Shea",
    "agent_shizuku": "Shizuku",
    "agent_tsukasa": "Tsukasa",
    "agent_yua": "Yua",
    "agent_yue": "Yue",
}

#: 既有 34 隻在 HEAD（48ade60）的 short-name（27 筆；另 7 筆本票才補齊）。
HEAD_27_SHORT = {
    "agent_ai": "ai",
    "agent_akane": "akane",
    "agent_asuna": "asuna",
    "agent_fuuka": "fuuka",
    "agent_haruhi": "haruhi",
    "agent_hiro": "hiro",
    "agent_iroha": "iroha",
    "agent_kaguya": "kaguya",
    "agent_kana": "kana",
    "agent_kaori": "kaori",
    "agent_kirino": "kirino",
    "agent_kurisu": "kurisu",
    "agent_makima": "makima",
    "agent_mashiro": "mashiro",
    "agent_minami": "minami",
    "agent_mochizuki_ai": "mochizuki_ai",
    "agent_reze": "reze",
    "agent_rin": "rin",
    "agent_rio": "rio",
    "agent_ruka": "ruka",
    "agent_sachi": "sachi",
    "agent_sakura": "sakura",
    "agent_shea": "shea",
    "agent_shizuku": "shizuku",
    "agent_tsukasa": "tsukasa",
    "agent_yua": "yua",
    "agent_yue": "yue",
}

#: 既有 34 隻在 HEAD（48ade60）的 configs 條目（class / intimacy_level / enabled）。
HEAD_34_CONFIG = {
    "agent_ai": {'id': 'agent_ai', 'class': 'AgentAi', 'intimacy_level': 25, 'enabled': True},
    "agent_akane": {'id': 'agent_akane', 'class': 'AgentAkane', 'intimacy_level': 50, 'enabled': True},
    "agent_anna": {'id': 'agent_anna', 'class': 'AgentAnna', 'intimacy_level': 55, 'enabled': True},
    "agent_aoi": {'id': 'agent_aoi', 'class': 'AgentAoi', 'intimacy_level': 46, 'enabled': True},
    "agent_asuna": {'id': 'agent_asuna', 'class': 'AgentAsuna', 'intimacy_level': 25, 'enabled': True},
    "agent_fuuka": {'id': 'agent_fuuka', 'class': 'AgentFuuka', 'intimacy_level': 25, 'enabled': True},
    "agent_haruhi": {'id': 'agent_haruhi', 'class': 'AgentHaruhi', 'intimacy_level': 25, 'enabled': True},
    "agent_hiro": {'id': 'agent_hiro', 'class': 'AgentHiro', 'intimacy_level': 25, 'enabled': True},
    "agent_iroha": {'id': 'agent_iroha', 'class': 'AgentIroha', 'intimacy_level': 25, 'enabled': True},
    "agent_kaguya": {'id': 'agent_kaguya', 'class': 'AgentKaguya', 'intimacy_level': 25, 'enabled': True},
    "agent_kana": {'id': 'agent_kana', 'class': 'AgentKana', 'intimacy_level': 25, 'enabled': True},
    "agent_kaori": {'id': 'agent_kaori', 'class': 'AgentKaori', 'intimacy_level': 25, 'enabled': True},
    "agent_kirino": {'id': 'agent_kirino', 'class': 'AgentKirino', 'intimacy_level': 25, 'enabled': True},
    "agent_kurisu": {'id': 'agent_kurisu', 'class': 'AgentKurisu', 'intimacy_level': 25, 'enabled': True},
    "agent_mahiru": {'id': 'agent_mahiru', 'class': 'AgentMahiru', 'intimacy_level': 45, 'enabled': True},
    "agent_mai": {'id': 'agent_mai', 'class': 'AgentMai', 'intimacy_level': 60, 'enabled': True},
    "agent_makima": {'id': 'agent_makima', 'class': 'AgentMakima', 'intimacy_level': 25, 'enabled': True},
    "agent_mashiro": {'id': 'agent_mashiro', 'class': 'AgentMashiro', 'intimacy_level': 25, 'enabled': True},
    "agent_miku": {'id': 'agent_miku', 'class': 'AgentMiku', 'intimacy_level': 60, 'enabled': True},
    "agent_minami": {'id': 'agent_minami', 'class': 'AgentMinami', 'intimacy_level': 25, 'enabled': True},
    "agent_mochizuki_ai": {'id': 'agent_mochizuki_ai', 'class': 'AgentMochizukiAi', 'intimacy_level': 25, 'enabled': True},
    "agent_ram": {'id': 'agent_ram', 'class': 'AgentRam', 'intimacy_level': 40, 'enabled': True},
    "agent_rem": {'id': 'agent_rem', 'class': 'AgentRem', 'intimacy_level': 45, 'enabled': True},
    "agent_reze": {'id': 'agent_reze', 'class': 'AgentReze', 'intimacy_level': 25, 'enabled': True},
    "agent_rin": {'id': 'agent_rin', 'class': 'AgentRin', 'intimacy_level': 25, 'enabled': True},
    "agent_rio": {'id': 'agent_rio', 'class': 'AgentRio', 'intimacy_level': 25, 'enabled': True},
    "agent_ruka": {'id': 'agent_ruka', 'class': 'AgentRuka', 'intimacy_level': 60, 'enabled': True},
    "agent_sachi": {'id': 'agent_sachi', 'class': 'AgentSachi', 'intimacy_level': 25, 'enabled': True},
    "agent_sakura": {'id': 'agent_sakura', 'class': 'AgentSakura', 'intimacy_level': 25, 'enabled': True},
    "agent_shea": {'id': 'agent_shea', 'class': 'AgentShea', 'intimacy_level': 25, 'enabled': True},
    "agent_shizuku": {'id': 'agent_shizuku', 'class': 'AgentShizuku', 'intimacy_level': 25, 'enabled': True},
    "agent_tsukasa": {'id': 'agent_tsukasa', 'class': 'AgentTsukasa', 'intimacy_level': 25, 'enabled': True},
    "agent_yua": {'id': 'agent_yua', 'class': 'AgentYua', 'intimacy_level': 80, 'enabled': True},
    "agent_yue": {'id': 'agent_yue', 'class': 'AgentYue', 'intimacy_level': 25, 'enabled': True},
}

#: 既有 34 隻在 HEAD（48ade60）的 BASE_SCORES。
HEAD_34_SCORES = {
    "agent_ai": (0.38,0.26),
    "agent_akane": (0.35,0.28),
    "agent_anna": (0.55,0.30),
    "agent_aoi": (0.62,0.32),
    "agent_asuna": (0.50,0.30),
    "agent_fuuka": (0.38,0.26),
    "agent_haruhi": (0.68,0.35),
    "agent_hiro": (0.45,0.30),
    "agent_iroha": (0.62,0.35),
    "agent_kaguya": (0.48,0.30),
    "agent_kana": (0.62,0.32),
    "agent_kaori": (0.65,0.33),
    "agent_kirino": (0.55,0.32),
    "agent_kurisu": (0.40,0.30),
    "agent_mahiru": (0.65,0.35),
    "agent_mai": (0.58,0.35),
    "agent_makima": (0.42,0.28),
    "agent_mashiro": (0.42,0.28),
    "agent_miku": (0.55,0.30),
    "agent_minami": (0.65,0.35),
    "agent_mochizuki_ai": (0.58,0.33),
    "agent_ram": (0.30,0.20),
    "agent_rem": (0.40,0.30),
    "agent_reze": (0.45,0.30),
    "agent_rin": (0.40,0.30),
    "agent_rio": (0.45,0.30),
    "agent_ruka": (0.75,0.35),
    "agent_sachi": (0.58,0.33),
    "agent_sakura": (0.50,0.30),
    "agent_shea": (0.65,0.33),
    "agent_shizuku": (0.42,0.28),
    "agent_tsukasa": (0.52,0.30),
    "agent_yua": (0.80,0.40),
    "agent_yue": (0.38,0.25),
}

#: 既有 34 隻在 HEAD（48ade60）的 _AGENT_IDENTITY_RULES 值 sha256（逐 agent）。
HEAD_34_IDENTITY_SHA256 = {
    "agent_ai": "2894e0c274945893c02e05f2ffc0b09810fa2fe91fe2438a069167af55f5d75b",
    "agent_akane": "d77149aa8a49998a59a04e5b0a45e2b8f153e9761f695c6a28ada364a3ab6775",
    "agent_anna": "e1331a51be0c25488b5aeca61464d2bb2e267b7bb626e50c5708a5d6bb75fe55",
    "agent_aoi": "c7ed425593e8af68eabb8f83709894ae9c3c2244f5274eaca91c7f2d7f7ed4dd",
    "agent_asuna": "447f4bdbb515b430bf9cbf4a918aee8c1baba50203d74d4bca2407fe805b53ec",
    "agent_fuuka": "aeea32ec8e1fd1e9d92a225a0a7e9ba1d4186eb873797339197d6a4ec3d38b91",
    "agent_haruhi": "2a96b9d0b929c181b4838774a0f6c03a14896a150b261ac00eff62153a73b311",
    "agent_hiro": "8914ce158ba9bd6f31e24394c2fb82edcf7b4d14742925f918758181d24f3866",
    "agent_iroha": "5ee98f80da91d46d3b86cd656b3dc59fe117d4abc9457466ebd6fb5806f49dbc",
    "agent_kaguya": "147c4f662962571b27bfd1e0387bdcbd41b553fd4fcb533e653db97753e7e52e",
    "agent_kana": "cb6ee3da3790546b30ae33d43ea26ebe9a155b7d7a8f0c961d9f518df15ce5fe",
    "agent_kaori": "9dcdaa76e02dd9eef2f2ef2aa5a8559b6d49ed64a9310e6858f6f59ee3eaeae6",
    "agent_kirino": "bd8e22b56012e33d30765906204c2b446fe67b306d0f8749862713eb85ee9c38",
    "agent_kurisu": "d7b7aae16fa01134d1862d3ce316f1851fdb951fe84245ff2e5ef1a4759f55d4",
    "agent_mahiru": "a0cc27d3d03231a18cec8b2f38d5aee19b7c98445e421912140f81197f43ef56",
    "agent_mai": "713b85e3de164c7791b94c232286329f746f519dabc48565975d72689e9eb965",
    "agent_makima": "9335d167d830b4f4ff6b688c3da9300e5080bef653b8a97982fb4223d8eb96ee",
    "agent_mashiro": "ca847c94d35f4669bceb1b103cd065e1fd400e5d55db4651beee7d0c7dd1a4a3",
    "agent_miku": "88f0a6d38f0f286c3174a203621c789a8f817d8cc839572cf17ab1877bead231",
    "agent_minami": "a27bbaf979b3d330c354bf7bbe1bc5481a87becf426267f1ff30dca0ea2cf3f3",
    "agent_mochizuki_ai": "c48b899ae9bc4d30b7916ba03c2c656b2341e02a9b78bb043ed642c2bb6fe1c9",
    "agent_ram": "55399d245eec13075841d507b017b20cbf1f8195e90264afde9880a7fdc83c54",
    "agent_rem": "0e06fb0a3799b5ab0c031d0a884529e8540854ea647511b0b5172bd7e0ad77f5",
    "agent_reze": "4d894fd08c779819f6f84e3ae320506ab8b5f2c19bd57ac30f8f346255d51dc7",
    "agent_rin": "89989357fe31a227f2f303fc09e16897832f3f879a56d049df4716b489f39950",
    "agent_rio": "6708ac79c111880d7151e41747aeae3b252f3f0fe691494526102b6df3b9abed",
    "agent_ruka": "fb55d1449f74bdd243fc1d6631dfac5c82407df430846d0599890f3134658ac2",
    "agent_sachi": "025f74927e9c651af66eb55fe93cae57804c1f3feedaec2190314ddafd876fb3",
    "agent_sakura": "7b5aa3f36a57e0120cd8bb9c33dd41cb307fae999d04c621de172843f777a633",
    "agent_shea": "745fa804c05262f1d03699f3864b3c4aeb4343e28b298176b63fc15d4a88b31e",
    "agent_shizuku": "f32bf429a18efc8cf3e1269834da832b79cfcc76b1347d533a9a635ac0d731cf",
    "agent_tsukasa": "986179a12d9e5b0bb662eb60a4d1e170d97184de0edbe8b364a90dabec5eddff",
    "agent_yua": "c45ea1d614aeb12873c7e1971d375c90328224c933fa4d28ca01936435b0781c",
    "agent_yue": "ed17a943557b7270addea43c113f2453c75a5f334c8cd2872a6f61bcc2af52b8",
}

#: 既有 34 隻的 agent_id（HEAD 全庫註冊面；不含 AgentConsciousness 抽象基底）。
HEAD_34_IDS = [
    "agent_ai",
    "agent_akane",
    "agent_anna",
    "agent_aoi",
    "agent_asuna",
    "agent_fuuka",
    "agent_haruhi",
    "agent_hiro",
    "agent_iroha",
    "agent_kaguya",
    "agent_kana",
    "agent_kaori",
    "agent_kirino",
    "agent_kurisu",
    "agent_mahiru",
    "agent_mai",
    "agent_makima",
    "agent_mashiro",
    "agent_miku",
    "agent_minami",
    "agent_mochizuki_ai",
    "agent_ram",
    "agent_rem",
    "agent_reze",
    "agent_rin",
    "agent_rio",
    "agent_ruka",
    "agent_sachi",
    "agent_sakura",
    "agent_shea",
    "agent_shizuku",
    "agent_tsukasa",
    "agent_yua",
    "agent_yue",
]


def _proxy_tables():
    """在測試函式內匯入 proxy（延後到資料根隔離 fixture 生效後）。"""
    from src.llm import proxy as proxy_mod

    return (
        proxy_mod.AGENT_NAMES,
        proxy_mod.AGENT_SHORT_NAMES,
        proxy_mod._AGENT_IDENTITY_RULES,
    )


def _config_agents():
    import yaml

    with open(REPO_ROOT / "configs" / "default.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("agents", [])


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strip_shadow_clause(raw: bytes) -> bytes:
    """剝掉「## 陰影條款」段（含其前的空行），回傳其餘全文的位元組。

    規則：自 `## 陰影條款` 標題行起至檔尾全數移除，並把標題前的空行一併收掉，
    保留單一結尾換行 —— 與落地檔「素材逐字 ＋ 尾端追加一段」的組裝方式對稱。
    """
    idx = raw.find(SHADOW_HEADING.encode("utf-8"))
    if idx < 0:
        return raw
    return raw[:idx].rstrip(b"\r\n") + b"\r\n"


def _shadow_section(raw: bytes) -> str:
    """取出「## 陰影條款」段的文字（不含標題行前的空行）。"""
    idx = raw.find(SHADOW_HEADING.encode("utf-8"))
    assert idx >= 0, "找不到陰影條款段"
    return raw[idx:].decode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 1：註冊面一致性（六面齊備）
# ─────────────────────────────────────────────────────────────────────────────


def test_guard1_registration_surfaces_complete():
    """12 隻在六個面全部齊備（personas / consciousness / class map / configs / scores / proxy 三表）。"""
    from src.agent import consciousness as cons_mod
    from src.agent.registry import AGENT_CLASS_MAP
    from src.agent.speaker_token import BASE_SCORES

    display, short, identity = _proxy_tables()
    config_agents = {a["id"]: a for a in _config_agents()}

    for agent_id, class_name, score, disp, shrt, cn in BATCH2:
        assert (PERSONAS_DIR / f"{agent_id}.md").is_file(), f"{agent_id}: personas 檔不存在"
        cls = getattr(cons_mod, class_name, None)
        assert cls is not None, f"{agent_id}: consciousness 缺子類 {class_name}"
        assert AGENT_CLASS_MAP.get(class_name) is cls, f"{agent_id}: AGENT_CLASS_MAP 未綁 {class_name}"
        assert agent_id in config_agents, f"{agent_id}: configs 缺條目"
        assert config_agents[agent_id]["class"] == class_name, f"{agent_id}: configs class 不符"
        assert config_agents[agent_id]["intimacy_level"] == 25, (
            f"{agent_id}: intimacy_level 應為 25，實際 {config_agents[agent_id]['intimacy_level']}"
        )
        assert config_agents[agent_id].get("enabled", True) is True, f"{agent_id}: 被停用"
        assert BASE_SCORES.get(agent_id) == score, (
            f"{agent_id}: BASE_SCORES 應為 {score}，實際 {BASE_SCORES.get(agent_id)}"
        )
        assert display.get(agent_id) == disp, f"{agent_id}: display-name 應為 {disp}，實際 {display.get(agent_id)}"
        assert short.get(agent_id) == shrt, f"{agent_id}: short-name 應為 {shrt}，實際 {short.get(agent_id)}"
        assert agent_id in identity, f"{agent_id}: identity rules 缺條目"
        assert identity[agent_id].strip(), f"{agent_id}: identity rule 變空"
        assert f"你是{cn}(" in identity[agent_id], f"{agent_id}: identity rule 首行不含中文名"


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 2：總數 = 46
# ─────────────────────────────────────────────────────────────────────────────


def test_guard2_total_registration_is_46():
    """全庫註冊數 = 46（既有 34 ＋ 本批 12）。"""
    from src.agent.registry import AGENT_CLASS_MAP
    from src.agent.speaker_token import BASE_SCORES

    display, short, identity = _proxy_tables()
    config_agents = _config_agents()
    persona_files = sorted(PERSONAS_DIR.glob("agent_*.md"))

    assert len(config_agents) == 46, f"configs agents 應 46 筆，實際 {len(config_agents)}"
    assert len(AGENT_CLASS_MAP) == 46, f"AGENT_CLASS_MAP 應 46 筆，實際 {len(AGENT_CLASS_MAP)}"
    assert len(BASE_SCORES) == 46, f"BASE_SCORES 應 46 筆，實際 {len(BASE_SCORES)}"
    assert len(display) == 46, f"AGENT_NAMES 應 46 筆，實際 {len(display)}"
    assert len(short) == 46, f"AGENT_SHORT_NAMES 應 46 筆，實際 {len(short)}"
    assert len(identity) == 46, f"_AGENT_IDENTITY_RULES 應 46 筆，實際 {len(identity)}"
    assert len(persona_files) == 46, f"personas/ 應 46 份，實際 {len(persona_files)}"


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 3：類名規則 = Agent ＋ CamelCase(agent_id)
# ─────────────────────────────────────────────────────────────────────────────


def _camel(agent_id: str) -> str:
    return "Agent" + "".join(w.capitalize() for w in agent_id[len("agent_"):].split("_"))


def test_guard3_class_name_rule_holds_verbatim():
    """類名規則逐字成立（既有 34 隻 ＋ 本批 12 隻）：`Agent` ＋ CamelCase(agent_id)。"""
    from src.agent import consciousness as cons_mod
    from src.agent.registry import AGENT_CLASS_MAP

    for agent_id in HEAD_34_IDS + BATCH2_IDS:
        expected = _camel(agent_id)
        assert getattr(cons_mod, expected, None) is not None, f"{agent_id}: consciousness 缺 {expected}"
        assert AGENT_CLASS_MAP.get(expected) is getattr(cons_mod, expected), (
            f"{agent_id}: AGENT_CLASS_MAP 未綁 {expected}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 4：位元等價（剝掉陰影條款段後 ≡ 素材）
# ─────────────────────────────────────────────────────────────────────────────


def test_guard4_persona_is_draft_plus_shadow_clause_byte_identical():
    """12 份落地檔剝掉「## 陰影條款」段後，與素材位元相同（最強驗收條件）。"""
    for agent_id in BATCH2_IDS:
        draft = DRAFTS_DIR / f"{agent_id}.md"
        landed = PERSONAS_DIR / f"{agent_id}.md"
        assert draft.is_file(), f"素材缺檔：{draft.name}"
        assert landed.is_file(), f"落地缺檔：{landed.name}"
        draft_bytes = draft.read_bytes()
        landed_bytes = landed.read_bytes()
        stripped = _strip_shadow_clause(landed_bytes)
        assert stripped == draft_bytes, (
            f"{agent_id}: 剝掉陰影條款段後與素材位元不同"
            f"（draft sha256={_sha256_bytes(draft_bytes)} / stripped sha256={_sha256_bytes(stripped)}）"
        )
        # 落地檔必須比素材多出陰影條款段（不得只是素材的複製）
        assert len(landed_bytes) > len(draft_bytes), f"{agent_id}: 落地檔未追加陰影條款段"


def test_guard4_shadow_clause_appended_at_tail_after_special_notes():
    """陰影條款段必須在檔尾（素材「特別注意(不可違反)」之後），不得插入素材中段。"""
    for agent_id in BATCH2_IDS:
        landed = (PERSONAS_DIR / f"{agent_id}.md").read_text(encoding="utf-8")
        special_idx = landed.index("## 特別注意(不可違反)")
        shadow_idx = landed.index(SHADOW_HEADING)
        assert shadow_idx > special_idx, f"{agent_id}: 陰影條款段未置於特別注意段之後"


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 5：陰影條款形狀
# ─────────────────────────────────────────────────────────────────────────────


def test_guard5_shadow_clause_heading_count_is_exactly_one():
    """12 份的 `^## 陰影條款` 計數 = 1。"""
    for agent_id in BATCH2_IDS:
        text = (PERSONAS_DIR / f"{agent_id}.md").read_text(encoding="utf-8")
        count = sum(1 for line in text.splitlines() if line.startswith(SHADOW_HEADING))
        assert count == 1, f"{agent_id}: 陰影條款標題行計數 = {count}（應為 1）"


def test_guard5_shadow_clause_has_six_clauses_and_red_lines():
    """陰影條款段必須含 ①②③④⑤⑥ 六條、≥3 條紅線、且含「陰影不劇場化」聲明。"""
    for agent_id in BATCH2_IDS:
        section = _shadow_section((PERSONAS_DIR / f"{agent_id}.md").read_bytes())
        for mark in ("①記仇", "②冷戰", "③狠話的形態", "④測試行為", "⑤最壞的自己", "⑥修復不是成長"):
            assert mark in section, f"{agent_id}: 陰影條款缺 {mark}"
        assert "解鈴句只有一句" in section, f"{agent_id}: 陰影條款缺唯一解鈴句"
        assert "陰影不劇場化" in section, f"{agent_id}: 陰影條款缺「陰影不劇場化」聲明"
        red = re.search(r"陰影的紅線（連陰影也不越過）：(.+)", section)
        assert red is not None, f"{agent_id}: 陰影條款缺紅線段"
        red_items = [s for s in red.group(1).split("；") if s.strip()]
        assert len(red_items) >= 3, f"{agent_id}: 紅線僅 {len(red_items)} 條（應 ≥ 3）"
        assert any("陰影不劇場化" in s for s in red_items), f"{agent_id}: 紅線段未含「陰影不劇場化」"
        assert SHADOW_ATTRIBUTION in section, f"{agent_id}: 陰影條款缺出處行"


def test_guard5_identity_rule_canon_quote_is_wellformed():
    """12 筆 identity rule 的 Canon Lock 行必須是單一「…」包裹（不得「」」重複結尾）。"""
    _, _, identity = _proxy_tables()
    for agent_id in BATCH2_IDS:
        rule = identity[agent_id]
        canon_lines = [l for l in rule.splitlines() if l.startswith("「")]
        assert len(canon_lines) == 1, f"{agent_id}: Canon Lock 行數 = {len(canon_lines)}（應為 1）"
        line = canon_lines[0]
        assert line.endswith("」"), f"{agent_id}: Canon Lock 行未以「」收尾"
        assert not line.endswith("」」"), f"{agent_id}: Canon Lock 行出現重複結尾「」」"
        assert line.count("「") == 1 and line.count("」") == 1, (
            f"{agent_id}: Canon Lock 行的引號不成對（{line}）"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 6：NO_DIARY_AGENTS 恰為 5 筆
# ─────────────────────────────────────────────────────────────────────────────


def test_guard6_no_diary_agents_is_exactly_five():
    """provider.py 的 NO_DIARY_AGENTS 恰為那 5 筆（含 agent_ram；不含 agent_emilia）。"""
    from src.memory.sage.provider import NO_DIARY_AGENTS

    assert NO_DIARY_AGENTS == NO_DIARY_5, f"NO_DIARY_AGENTS 不符：{sorted(NO_DIARY_AGENTS)}"
    assert "agent_emilia" not in NO_DIARY_AGENTS, "agent_emilia 素材為「建議寫日記」⇒ 不應在 NO_DIARY 名單"


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 7：既有 34 隻零回歸
# ─────────────────────────────────────────────────────────────────────────────


def test_guard7_existing_34_config_entries_untouched():
    """既有 34 隻的 configs 條目（class / intimacy_level / enabled）未動。"""
    config_agents = {a["id"]: a for a in _config_agents()}
    for agent_id, expected in HEAD_34_CONFIG.items():
        assert agent_id in config_agents, f"既有 {agent_id} 從 configs 消失"
        for field in ("class", "intimacy_level", "enabled"):
            assert config_agents[agent_id].get(field) == expected.get(field), (
                f"既有 {agent_id} 的 configs {field} 被改："
                f"{config_agents[agent_id].get(field)} != {expected.get(field)}"
            )


def test_guard7_existing_34_scores_untouched():
    """既有 34 隻的 BASE_SCORES 未動。"""
    from src.agent.speaker_token import BASE_SCORES

    for agent_id, expected in HEAD_34_SCORES.items():
        assert BASE_SCORES.get(agent_id) == expected, (
            f"既有 {agent_id} 的 BASE_SCORES 被改：{BASE_SCORES.get(agent_id)} != {expected}"
        )


def test_guard7_existing_34_proxy_keys_and_values_untouched():
    """既有 34 隻在 proxy 三表的鍵與值未動（identity rule 以 sha256 比對）。"""
    display, short, identity = _proxy_tables()

    for agent_id, expected in HEAD_27_DISPLAY.items():
        assert display.get(agent_id) == expected, (
            f"既有 display-name {agent_id} 被改：{display.get(agent_id)} != {expected}"
        )
    for agent_id, expected in HEAD_27_SHORT.items():
        assert short.get(agent_id) == expected, (
            f"既有 short-name {agent_id} 被改：{short.get(agent_id)} != {expected}"
        )
    for agent_id, expected_sha in HEAD_34_IDENTITY_SHA256.items():
        assert agent_id in identity, f"既有 {agent_id} 的 identity rule 被移除"
        actual_sha = _sha256_bytes(identity[agent_id].encode("utf-8"))
        assert actual_sha == expected_sha, (
            f"既有 {agent_id} 的 identity rule 被改（sha256 {actual_sha} != {expected_sha}）"
        )


def test_guard7_filled_7_display_and_short_names():
    """本票補齊的既有 7 隻：display-name 與 short-name 已進表且值正確。"""
    display, short, _ = _proxy_tables()
    for agent_id, name in FILLED_7.items():
        assert display.get(agent_id) == name, (
            f"{agent_id}: display-name 應為 {name}，實際 {display.get(agent_id)}"
        )
        assert short.get(agent_id) == agent_id[len("agent_"):], (
            f"{agent_id}: short-name 應為 {agent_id[len('agent_'):]}，實際 {short.get(agent_id)}"
        )


def test_guard7_existing_34_persona_files_still_exist():
    """既有 34 份 persona 檔仍在（不得被本票擠掉）。"""
    for agent_id in HEAD_34_IDS:
        assert (PERSONAS_DIR / f"{agent_id}.md").is_file(), f"既有 persona 消失：{agent_id}"


# ─────────────────────────────────────────────────────────────────────────────
# 守衛 8：proxy 三表鍵數與鍵集
# ─────────────────────────────────────────────────────────────────────────────


def test_guard8_proxy_three_tables_have_46_keys_and_identical_key_sets():
    """proxy 三表各 46 鍵，且鍵集完全相同（雙向相等）。"""
    display, short, identity = _proxy_tables()
    assert len(display) == len(short) == len(identity) == 46
    assert set(display) == set(short), (
        f"display/short 鍵集不一致：display-only={set(display) - set(short)} "
        f"short-only={set(short) - set(display)}"
    )
    assert set(display) == set(identity), (
        f"display/identity 鍵集不一致：display-only={set(display) - set(identity)} "
        f"identity-only={set(identity) - set(display)}"
    )
    expected = set(HEAD_34_IDS) | set(BATCH2_IDS)
    assert set(display) == expected, (
        f"鍵集與預期不符：多={set(display) - expected} 少={expected - set(display)}"
    )


def test_guard8_display_names_are_unique():
    """46 個 display-name 不得重名（否則 prompt 內的角色會混淆）。"""
    display, _, _ = _proxy_tables()
    seen = {}
    for agent_id, name in display.items():
        assert name not in seen, f"display-name 重名：{name}（{agent_id} 與 {seen.get(name)}）"
        seen[name] = agent_id
