"""
test_persona_fix2_yua_action_tag_carveout.py — PERSONA-FIX-2 (Yua 括號行為標籤)

稽核定案（直接採用，不重查）:
- Yua 招牌語法「（視線低下去，像是怕被看穿那一瞬間的動搖）」
  （personas/agent_yua.md:432-435，靈魂升溫協議第 2 條）在生產環境從未出現:
  2026-07-30~09-03 共 138 筆 `生成完成.*agent_yua` 樣本, 含（ = 0 筆。
- 壓制來源 = src/voice/build_system_prompt.py FORMAT_RULES_TEMPLATE:
  - L254「違反此規則 = 回應失敗」
  - L299「[输出格式规则 — 覆盖在角色设定之上]」→ 格式契約壓過 persona
  - L317-321「text 必須跟 audio_text 完全一致、純中文、都不帶 tag」
  - L361「5. tag 格式是 [方括號],不是 (圓括號)」
- 修法: build_system_prompt() 對 agent_yua 注入 carve-out（模板佔位
  {yua_action_tag_carveout}）, 只開 agent_yua, 其餘角色 byte-identical。

驗收:
1. agent_yua / 生產 inject 形式 "yua" 的 system prompt **含** carve-out 文字
2. agent_rem / agent_ruka / agent_mahiru **不含** carve-out, 且與
   空 carve-out 的模板輸出 byte-identical（0 行為變更硬證明）
3. carve-out 措辭必須明確蓋過 L299 與 L361, 允許 text != audio_text,
   audio_text 仍不得含括號
"""
import sys
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')  # PowerShell cp950 不能編碼中文
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm._agent_constants import get_short_id  # noqa: E402
from src.voice.build_system_prompt import (  # noqa: E402
    FORMAT_RULES_TEMPLATE,
    _YUA_ACTION_TAG_CARVEOUT,
    _is_yua_agent,
    build_system_prompt,
    extract_jp_rules_section,
    get_emotion_tags,
)

# soul 含「日文語言規則」章節 → extract_jp_rules_section 確定性回傳, 測試可複算參考值
_SOUL = "## 人格\n測試角色內容。\n\n## 日文語言規則\n規則內容\n"
_JP = extract_jp_rules_section(_SOUL)

_CARVEOUT_MARKER = "Yua 專屬例外"
_NON_YUA_AGENTS = ["rem", "ruka", "mahiru"]


def _reference_prompt(agent_name: str, with_carveout: bool) -> str:
    """依 FORMAT_RULES_TEMPLATE 直接複算參考 prompt（carve-out 開關可控）。"""
    carveout = _YUA_ACTION_TAG_CARVEOUT + "\n\n" if with_carveout else ""
    return FORMAT_RULES_TEMPLATE.format(
        emotion_tags_line="、".join(get_emotion_tags(agent_name)),
        per_agent_jp_rules=_JP,
        yua_action_tag_carveout=carveout,
        soul_content=_SOUL,
    )


class TestYuaCarveoutPresent(unittest.TestCase):
    """驗收 1: agent_yua 的 system prompt 含 carve-out 文字"""

    def test_yua_short_id_contains_carveout(self):
        """生產用的短 id 形式 "yua" → 含 carve-out（proxy.py:2500 strip 前綴後傳入）"""
        prompt = build_system_prompt(soul_content=_SOUL, agent_name="yua")
        self.assertIn(_CARVEOUT_MARKER, prompt)
        self.assertIn("（行為標籤）", prompt)
        self.assertIn("（視線低下去，像是怕被看穿那一瞬間的動搖）", prompt)

    def test_yua_full_id_contains_carveout(self):
        """完整 id 形式 "agent_yua" → 一樣含 carve-out（雙形式相容）"""
        prompt = build_system_prompt(soul_content=_SOUL, agent_name="agent_yua")
        self.assertIn(_CARVEOUT_MARKER, prompt)

    def test_yua_production_wiring_short_id(self):
        """生產接線: proxy 層短 id 契約 (_get_agent_short_id 同義) 命中 yua"""
        self.assertEqual(get_short_id("agent_yua"), "yua")
        self.assertTrue(_is_yua_agent(get_short_id("agent_yua")))
        prompt = build_system_prompt(
            soul_content=_SOUL, agent_name=get_short_id("agent_yua")
        )
        self.assertIn(_CARVEOUT_MARKER, prompt)

    def test_yua_prompt_byte_matches_reference_with_carveout(self):
        """yua 輸出 == 模板 + carve-out 的參考值（carve-out 只以預期方式注入）"""
        self.assertEqual(
            build_system_prompt(soul_content=_SOUL, agent_name="yua"),
            _reference_prompt("yua", with_carveout=True),
        )


class TestCarveoutWording(unittest.TestCase):
    """驗收 3: carve-out 措辭必須蓋過 L299 與 L361"""

    def test_overrides_format_rules_layer(self):
        """明文宣告效力高於「覆盖在角色设定之上」(L299)"""
        prompt = build_system_prompt(soul_content=_SOUL, agent_name="yua")
        self.assertIn("输出格式规则 — 覆盖在角色设定之上", prompt)
        self.assertIn("一律以本段為準", prompt)
        # carve-out 自身（非全域規則）宣告覆蓋 → 出現兩處即可證「例外優先」
        self.assertGreaterEqual(prompt.count("一律以本段為準"), 1)

    def test_overrides_bracket_only_rule(self):
        """明文推翻「tag 格式是 [方括號],不是 (圓括號)」(L361) → 允許 （行為標籤）"""
        prompt = build_system_prompt(soul_content=_SOUL, agent_name="yua")
        self.assertIn("不是 (圓括號)", prompt)
        self.assertIn("不准改成方括號", prompt)

    def test_allows_text_differs_from_audio_text(self):
        """明文允許 text != audio_text, 且 audio_text 仍不得含括號"""
        prompt = build_system_prompt(soul_content=_SOUL, agent_name="yua")
        self.assertIn("text` 允許與 `audio_text` 不一致", prompt)
        self.assertIn("audio_text` 仍然絕對不得包含", prompt)
        self.assertIn("`（…）` 或 `(...)`", prompt)

    def test_carveout_scoped_to_yua_in_text(self):
        """carve-out 內文明文限定只給 agent_yua"""
        self.assertIn("本段只適用於 agent_yua", _YUA_ACTION_TAG_CARVEOUT)
        self.assertIn("只給 agent_yua", _YUA_ACTION_TAG_CARVEOUT)


class TestOtherAgentsUnchanged(unittest.TestCase):
    """驗收 2: 其餘角色不含 carve-out, 0 行為變更（byte-identical 硬證明）"""

    def test_other_agents_missing_carveout(self):
        for name in _NON_YUA_AGENTS:
            with self.subTest(agent=name):
                self.assertFalse(_is_yua_agent(name))
                prompt = build_system_prompt(soul_content=_SOUL, agent_name=name)
                self.assertNotIn(_CARVEOUT_MARKER, prompt)
                self.assertNotIn("（行為標籤）", prompt)
                self.assertNotIn("一律以本段為準", prompt)

    def test_other_agents_byte_identical_to_baseline(self):
        """rem/ruka/mahiru 的輸出 == 空 carve-out 模板參考值 (byte-for-byte)"""
        for name in _NON_YUA_AGENTS:
            with self.subTest(agent=name):
                self.assertEqual(
                    build_system_prompt(soul_content=_SOUL, agent_name=name),
                    _reference_prompt(name, with_carveout=False),
                )

    def test_akane_and_none_also_unchanged(self):
        """額外抽查: akane / agent_name=None 同樣不含 carve-out"""
        for name in ["akane", None]:
            with self.subTest(agent=name):
                prompt = build_system_prompt(soul_content=_SOUL, agent_name=name)
                self.assertNotIn(_CARVEOUT_MARKER, prompt)

    def test_legacy_text_identity_rule_still_present_for_others(self):
        """全域「text 必須跟 audio_text 完全一致」規則對其他角色原樣保留"""
        prompt = build_system_prompt(soul_content=_SOUL, agent_name="rem")
        self.assertIn("text 字段必須跟 audio_text 內容**完全一致**", prompt)


if __name__ == "__main__":
    unittest.main()