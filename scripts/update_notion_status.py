"""
scripts/update_notion_status.py — 更新 Soul OS Notion 主頁面狀態

用法:
    python scripts/update_notion_status.py
    python scripts/update_notion_status.py "自訂更新文字"
"""
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

NOTION_KEY_PATH = Path(r"C:\Users\bbfcc\.config\notion\api_key")
PAGE_ID = "3ae5ab1a-15bb-8104-bd2f-c2c954161258"

def update_notion(text: str) -> bool:
    if not NOTION_KEY_PATH.exists():
        print(f"ERROR: Notion key not found at {NOTION_KEY_PATH}")
        return False

    notion_key = NOTION_KEY_PATH.read_text().strip()
    headers = {
        "Authorization": f"Bearer {notion_key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    body = {
        "children": [
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                },
            }
        ]
    }

    req = urllib.request.Request(
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="PATCH",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            r = json.loads(resp.read())
            new_id = r["results"][0]["id"]
            print(f"SUCCESS: Notion block created id={new_id}")
            return True
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        print(f"FAIL: {e.code} {err_msg[:300]}")
        return False
    except Exception as e:
        print(f"FAIL: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        custom_text = " ".join(sys.argv[1:])
    else:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        custom_text = (
            f"{now_str} [Soul OS 里程碑進展] TL-6 (Social Lounge Stability) 驗收全綠 "
            f"(commit 7d0ebbb, 213/213 tests PASS)。SI-3 (Selective Social Attention & Volition) "
            f"架構定錨完成 (docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md)。"
            f"確立三大共識: Opportunity TTL、Compact Social State (0 Vector DB)、Volition-before-Arbitration。"
        )
    print(f"Posting to Notion:\n{custom_text}\n")
    success = update_notion(custom_text)
    sys.exit(0 if success else 1)
