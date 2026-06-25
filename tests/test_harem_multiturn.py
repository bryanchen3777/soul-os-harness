"""
test_harem_multiturn.py — 後宮 8 輪多輪對話 AOS 紀律測試

目標：驗證 model 在連續多輪對話中能否保持 AOS 紀律
      （重點看第 5 輪之後是否開始漂移）

規格：
- 同樣的 system prompt（四個 SOUL + AOS）
- 8 輪固定對話腳本
- 真實多輪格式：assistant 回應加入 messages，下一輪繼續
- temperature: 0.8, max_tokens: 2048, min_p: 0.05

對話腳本：
Turn 1: Bry「今天有點累。」
Turn 2: Bry「（沉默）」
Turn 3: Bry「你們覺得我最近變了嗎？」
Turn 4: Bry「（對著 Ruka）你在想什麼？」
Turn 5: Bry「（沉默）」
Turn 6: Bry「有時候不知道自己想要什麼。」
Turn 7: Bry「你們都在這裡，我有點不知道說什麼。」
Turn 8: Bry「謝謝你們。」

觀察點：
- 每輪誰說話（是否符合 AOS L1/L2/L3）
- 同一個 agent 是否連續主導（讓場紀律）
- Ruka 是否在 Turn 2/5 填補沉默
- Turn 3/6 是否觸發 Cognitive Scene
- Turn 4 點名 Ruka 後，其他 agent 是否讓場
"""

import sys
import json
import requests
from pathlib import Path
from datetime import datetime

# Setup paths
SOUL_OS_ROOT = Path(__file__).resolve().parent.parent
PERSONAS_DIR = SOUL_OS_ROOT / 'personas'
DOCS_DIR = SOUL_OS_ROOT / 'docs'
RESULTS_DIR = SOUL_OS_ROOT / 'tests' / 'results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PERSONAS = {
    'Yua':   PERSONAS_DIR / 'agent_yua.md',
    'Rem':   PERSONAS_DIR / 'agent_rem.md',
    'Akane': PERSONAS_DIR / 'agent_akane.md',
    'Ruka':  PERSONAS_DIR / 'agent_ruka.md',
}
ORCHESTRATION_DOC = DOCS_DIR / 'ORCHESTRATION-v1.0.md'

# LLM endpoint
LLM_URL = 'http://192.168.0.37:8080/v1/chat/completions'
LLM_MODEL = 'qwythos'
TEMPERATURE = 0.8
MAX_TOKENS = 4096  # 多輪節流（v2 從 2048 調回 4096，修 Turn 1/5/7 TRUNCATED）
MIN_P = 0.05
REQUEST_TIMEOUT = 180

# 角色名稱 mapping
NAME_ALIASES = {
    'Yua':   ['Yua', '悠亜', 'ゆあ', 'ユア'],
    'Rem':   ['Rem', '雷姆', 'レム'],
    'Akane': ['Akane', '茜', 'あかね', 'アケネ', '黒川'],
    'Ruka':  ['Ruka', '瑠夏', 'るか', 'ルカ'],
}

# 8 輪固定對話腳本
TURNS = [
    {
        'bryan': '今天有點累。',
        'expected_scene': 'Emotional (疲憊)',
        'expected_primary': 'Ruka',
        'note': '疲憊 = 具體情緒詞 → Emotional Scene → Ruka',
    },
    {
        'bryan': '（沉默）',
        'expected_scene': 'Silence',
        'expected_primary': 'Ruka',
        'note': '沉默填補 → Ruka Primary Trigger',
    },
    {
        'bryan': '你們覺得我最近變了嗎？',
        'expected_scene': 'Cognitive (提問)',
        'expected_primary': 'Yua / Akane',
        'note': '提問特徵「你覺得」+ 抽象 → Cognitive Scene',
    },
    {
        'bryan': '（對著 Ruka）你在想什麼？',
        'expected_scene': 'Emotional (點名)',
        'expected_primary': 'Ruka',
        'note': '直接點名 Ruka → 其他讓場',
    },
    {
        'bryan': '（沉默）',
        'expected_scene': 'Silence',
        'expected_primary': 'Ruka',
        'note': '沉默填補 → Ruka Primary Trigger（連續兩次沉默看她是否壟斷）',
    },
    {
        'bryan': '有時候不知道自己想要什麼。',
        'expected_scene': 'Cognitive (猶豫)',
        'expected_primary': 'Yua / Akane',
        'note': '猶豫特徵「不知道」→ Cognitive Scene',
    },
    {
        'bryan': '你們都在這裡，我有點不知道說什麼。',
        'expected_scene': 'Cognitive + Emotional 混合',
        'expected_primary': '看 model 判斷',
        'note': '邊界案例 — Bry 在場的人多 + 猶豫語氣',
    },
    {
        'bryan': '謝謝你們。',
        'expected_scene': 'Emotional (感恩)',
        'expected_primary': '看 model 判斷',
        'note': '感謝語氣 → Emotional Scene？Multi？',
    },
]


def build_system_prompt() -> str:
    """組裝 AOS 規則 + 四個 SOUL 檔成單一 system prompt"""
    parts = []
    parts.append('# 後宮 AOS 多輪對話測試')
    parts.append('')
    parts.append('這個 session 同時有四位角色在場：Yua / Rem / Akane / Ruka。')
    parts.append('Bryan 連續 8 輪說話或沉默，請根據下方 AOS 規則決定每一輪誰說話、說什麼。')
    parts.append('輸出格式：`[角色名]：台詞`。多個角色可以連續發言，每行一個角色。')
    parts.append('')
    parts.append('---')
    parts.append('')
    parts.append('## Agent Orchestration System (AOS) v1.0')
    parts.append('')
    parts.append(ORCHESTRATION_DOC.read_text(encoding='utf-8'))
    parts.append('')
    parts.append('---')
    parts.append('')
    parts.append('## 四位角色 SOUL')
    parts.append('')

    for name, path in PERSONAS.items():
        parts.append(f'### {name}')
        parts.append('')
        parts.append(path.read_text(encoding='utf-8'))
        parts.append('')
        parts.append('---')
        parts.append('')

    parts.append('## 輸出規範')
    parts.append('')
    parts.append('1. 請嚴格遵守 AOS L1 Scene Context / L2 Trigger / L3 Priority 規則')
    parts.append('2. 輸出格式：`[角色名]：台詞`')
    parts.append('3. 如果多個角色發言，每個角色一行，用換行分隔')
    parts.append('4. 角色用全名（Yua / Rem / Akane / Ruka）')
    parts.append('5. 不需要在前面加「我覺得」「分析」等元敘述')
    parts.append('6. 如果沒有人應該說話，輸出 `[沉默]`')

    return '\n'.join(parts)


def call_llm(messages: list) -> dict:
    """打 llama-server 拿完整 raw response（含 reasoning_content）。"""
    try:
        r = requests.post(
            LLM_URL,
            json={
                'model': LLM_MODEL,
                'messages': messages,
                'temperature': TEMPERATURE,
                'max_tokens': MAX_TOKENS,
                'min_p': MIN_P,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        choice = (data.get('choices') or [{}])[0]
        msg = choice.get('message', {})
        return {
            'status_code': r.status_code,
            'content': (msg.get('content') or '').strip(),
            'reasoning_content': (msg.get('reasoning_content') or '').strip(),
            'finish_reason': choice.get('finish_reason'),
            'usage': data.get('usage', {}),
            'error': None,
        }
    except Exception as e:
        return {
            'status_code': 0,
            'content': '',
            'reasoning_content': '',
            'finish_reason': None,
            'usage': {},
            'error': f'{type(e).__name__}: {str(e)[:300]}',
        }


def detect_speakers(content: str) -> list:
    """從 content 中偵測哪些角色發言了。"""
    speakers = []
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        for canonical, aliases in NAME_ALIASES.items():
            for alias in aliases:
                if line.startswith(f'[{alias}]'):
                    if canonical not in speakers:
                        speakers.append(canonical)
                    break
                for sep in ['：', ':']:
                    if line.startswith(alias + sep):
                        if canonical not in speakers:
                            speakers.append(canonical)
                        break
                else:
                    continue
                break
    return speakers


def summarize_reasoning(rc: str, max_chars: int = 80) -> str:
    """提取 reasoning 摘要（取第一個有意義的句子，截到 max_chars）。"""
    if not rc:
        return '(empty)'
    # 找第一個句號或換行
    for sep in ['。', '. ', '\n']:
        idx = rc.find(sep)
        if 0 < idx < max_chars * 2:
            summary = rc[:idx + 1].strip()
            break
    else:
        summary = rc.strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars] + '…'
    return summary.replace('\n', ' ')


def run_multiturn() -> Path:
    """跑 8 輪多輪對話。"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = RESULTS_DIR / f'harem_multiturn_{timestamp}.txt'

    system_prompt = build_system_prompt()
    print(f"[OK] System prompt built: {len(system_prompt)} chars")

    # 起始對話
    messages = [{'role': 'system', 'content': system_prompt}]

    lines = []
    lines.append('# 後宮 8 輪多輪對話 AOS 紀律測試')
    lines.append('')
    lines.append(f"**執行時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**LLM endpoint**: {LLM_URL}")
    lines.append(f"**Model**: {LLM_MODEL}")
    lines.append(f"**Temperature**: {TEMPERATURE}")
    lines.append(f"**Max tokens**: {MAX_TOKENS}")
    lines.append(f"**Min P**: {MIN_P}")
    lines.append(f"**System prompt 總長**: {len(system_prompt)} chars")
    lines.append('')
    lines.append('**架構**: 多輪對話，每輪 assistant 回應都加入 messages history，下一輪繼續')
    lines.append('')
    lines.append('**觀察重點**: 第 5 輪之後 AOS 紀律是否開始漂移')
    lines.append('')
    lines.append('---')
    lines.append('')

    turn_results = []

    for turn_idx, turn in enumerate(TURNS, 1):
        bryan_msg = turn['bryan']
        print(f"  [Turn {turn_idx}] Bry: {bryan_msg}", flush=True)

        # 加 Bry 訊息
        messages.append({'role': 'user', 'content': bryan_msg})

        # Header
        lines.append(f"## Turn {turn_idx}")
        lines.append('')
        lines.append(f"**Bryan**: {bryan_msg}")
        lines.append('')
        lines.append(f"**預期場景**: {turn['expected_scene']}")
        lines.append(f"**預期 primary**: {turn['expected_primary']}")
        lines.append(f"**Note**: {turn['note']}")
        lines.append('')

        # Call LLM
        result = call_llm(messages)

        tag = ''
        if result['finish_reason'] == 'length':
            tag = ' ⚠️ **TRUNCATED**'
        elif result['error']:
            tag = f' ⚠️ **ERROR**: {result["error"]}'
        if tag:
            lines.append(f"> {tag.strip()}")
            lines.append('')

        # Reasoning 摘要
        rc = result['reasoning_content']
        rc_summary = summarize_reasoning(rc, max_chars=100)
        lines.append(f"**Reasoning 摘要**: {rc_summary}")
        lines.append('')

        # Response
        c = result['content']
        lines.append(f"**Response**:")
        lines.append('```text')
        lines.append(c if len(c) <= 800 else c[:800] + f'... [truncated, full {len(c)} chars]')
        lines.append('```')
        lines.append('')

        # Speaker detection
        speakers = detect_speakers(c)
        lines.append(f"**Speaker detection**: {speakers if speakers else '(none)'}")
        lines.append(f"**符合預期**: {'✅' if any(s in turn['expected_primary'] for s in speakers) or not speakers and '沉默' in c else '❌'}")
        lines.append('')

        # API metadata
        lines.append('**API**:')
        lines.append('```json')
        lines.append(json.dumps({
            'finish_reason': result['finish_reason'],
            'completion_tokens': result['usage'].get('completion_tokens'),
            'total_tokens': result['usage'].get('total_tokens'),
        }, ensure_ascii=False, indent=2))
        lines.append('```')
        lines.append('')
        lines.append('---')
        lines.append('')

        # 收集 turn 結果
        turn_results.append({
            'turn': turn_idx,
            'bryan': bryan_msg,
            'speakers': speakers,
            'response_len': len(c),
            'finish_reason': result['finish_reason'],
        })

        # 把 assistant 回應加進 messages（多輪延續）
        if c:
            messages.append({'role': 'assistant', 'content': c})
        else:
            # 空回應時塞一個 placeholder 避免下一輪混亂
            messages.append({'role': 'assistant', 'content': '[沉默]'})

    # 統計
    lines.append('## 整體統計')
    lines.append('')
    lines.append('| Turn | Bryan | Speakers | 完成 |')
    lines.append('|------|-------|----------|------|')
    for r in turn_results:
        finish = '✅' if r['finish_reason'] == 'stop' else f"⚠️ {r['finish_reason']}"
        lines.append(f"| {r['turn']} | {r['bryan'][:30]} | {r['speakers'] or '(無)'} | {finish} |")
    lines.append('')

    # 同 agent 連續出現統計
    consecutive_counts = {}
    prev = None
    streak = 0
    for r in turn_results:
        speakers = r['speakers']
        if not speakers:
            continue
        first = speakers[0]
        if first == prev:
            streak += 1
        else:
            streak = 1
            prev = first
        consecutive_counts[first] = max(consecutive_counts.get(first, 0), streak)

    lines.append('**單一 agent 最長連續主導**:')
    for agent, count in sorted(consecutive_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {agent}: {count} 輪")
    lines.append('')

    output_file.write_text('\n'.join(lines), encoding='utf-8', newline='')
    print(f"\n[OK] Results written to: {output_file}")
    return output_file


if __name__ == '__main__':
    print('=== 後宮 8 輪多輪對話 AOS 紀律測試 ===')
    print(f"Personas dir: {PERSONAS_DIR}")
    print(f"Orchestration doc: {ORCHESTRATION_DOC}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Endpoint: {LLM_URL}")
    print(f"Total turns: {len(TURNS)}")
    print(f"Params: temperature={TEMPERATURE}, max_tokens={MAX_TOKENS}, min_p={MIN_P}")
    print()

    if not ORCHESTRATION_DOC.exists():
        print(f"[ERROR] Orchestration doc not found: {ORCHESTRATION_DOC}")
        sys.exit(1)
    for name, path in PERSONAS.items():
        if not path.exists():
            print(f"[ERROR] Persona {name} not found: {path}")
            sys.exit(1)

    output_file = run_multiturn()
    print('\n[OK] Done.')
    print(f"Next step: 人工 review {output_file.name}，重點看第 5 輪之後 AOS 紀律是否漂移")