"""
test_soul_md_loader.py — SOUL.md 載入器測試

3 個場景：
1. Yua soul.md（小寫）載入成功
2. Ruka SOUL.md（大寫）載入成功
3. 未知 agent_id fallback 到 DEFAULT_PERSONAS
"""

import sys
import os
from pathlib import Path

# 確保 src/ 可 import（從 repo 根目錄執行時需要）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.proxy import load_persona


def test_load_yua_soul_md():
    """場景一：Yua soul.md（小寫）載入成功"""
    persona = load_persona("agent_yua")
    # 真實 SOUL.md 有 "Yua" 或 "yua"
    assert "Yua" in persona or "yua" in persona.lower(), f"Yua keyword not found in persona:\n{persona[:200]}"
    # 比 hardcode 長很多
    assert len(persona) > 200, f"Persona too short ({len(persona)} chars): {persona[:100]}"
    # 確認不是 fallback（DEFAULT_PERSONAS 內容不在）
    assert "你是Yua，一個聰明、冷靜" not in persona, "Got DEFAULT_PERSONA instead of SOUL.md"
    # 確認有共享對話規則（_AGENT_DIALOGUE_RULES append 到所有 persona）
    assert "【語言分工 - 跟上面 FORMAT_RULES 一致】" in persona, "Missing dialogue rules"
    print(f"[OK] Yua persona loaded: {len(persona)} chars")


def test_load_ruka_soul_md():
    """場景二：Ruka SOUL.md（大寫檔名）載入成功"""
    persona = load_persona("agent_ruka")
    assert "瑠夏" in persona or "Ruka" in persona or "Sarashina" in persona, \
        f"Ruka keyword not found in persona:\n{persona[:200]}"
    assert len(persona) > 200, f"Persona too short ({len(persona)} chars): {persona[:100]}"
    # 確認不是 fallback
    assert "你是瑠夏，活潑、愛撒嬌" not in persona, "Got DEFAULT_PERSONA instead of SOUL.md"
    assert "【語言分工 - 跟上面 FORMAT_RULES 一致】" in persona, "Missing dialogue rules"
    print(f"[OK] Ruka persona loaded: {len(persona)} chars")


def test_unknown_agent_fallback():
    """場景三：未知 agent_id fallback"""
    persona = load_persona("agent_unknown_xyz")
    assert "agent_unknown_xyz" in persona, f"Unknown agent fallback wrong:\n{persona}"
    assert "【語言分工 - 跟上面 FORMAT_RULES 一致】" in persona, "Missing dialogue rules on fallback"
    print(f"[OK] Unknown agent fallback OK")


def test_akane_soul_md():
    """Bonus：akane SOUL.md 也能載入"""
    persona = load_persona("agent_akane")
    # 只要長度合理且有 override 就算通過
    assert len(persona) > 50, f"Akane persona too short: {len(persona)}"
    assert "【語言分工 - 跟上面 FORMAT_RULES 一致】" in persona
    print(f"[OK] Akane persona loaded: {len(persona)} chars")


if __name__ == "__main__":
    print("=== SOUL.md Loader Tests ===\n")
    test_load_yua_soul_md()
    test_load_ruka_soul_md()
    test_unknown_agent_fallback()
    test_akane_soul_md()
    print("\n[OK] All tests passed!")