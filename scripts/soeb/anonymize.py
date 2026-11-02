"""SOEB 匿名化（設計文件第六節第 5 步）——把 model_id 換成 MODEL_A…MODEL_F。

為什麼要自己做，而不是交給評分者手工改名：

1. **盲評的前提是評分者在看到答案前不知道誰是誰。** 只要 observations.jsonl 還帶著
   `model_id`，任何「先看一下」都會污染後續所有判斷。
2. **光改名不夠。** 有些欄位會洩漏身分，必須一起移除：
   - `protocol`：本輪六顆模型分屬三種協定，`responses` 只有 muse、`messages` 只有 qwen
     ⇒ 看一眼協定就知道是誰，等於沒匿名。
   - `effort_sent`：各模型支援的 effort 集合不同，同樣是身分指紋。
   - `error` / `request`：錯誤訊息常含模型名或端點。
3. **映射檔必須與盲評檔分開存放**，而且檔名要擋得住手滑（DO_NOT_OPEN...）。
   盲評目錄裡只留不含任何身分資訊的檔案。

用法（不需要金鑰、不發任何請求）：

    .venv\\Scripts\\python.exe scripts\\soeb\\anonymize.py --run-dir tests\\results\\soeb\\v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 會洩漏身分的欄位。不是「順手清掉」，是每一項都有具體理由（見模組說明）。
#: `channel` / `api_model` 是 2026-11-01 加入第二通道（Ollama Cloud）後的新指紋：
#: 只有 deepseek 走 ollama-chat，留著等於直接點名。
LEAKY_FIELDS = (
    "protocol",
    "endpoint",
    "channel",
    "api_model",
    "effort_sent",
    "request",
    "response_raw",
    "usage_raw",
)


def assert_not_under_data(path: Path) -> Path:
    """寫入位置不得落在 repo 的 data/（生產狀態）之下。

    這裡不用「比對 data_root() 的解析值」——那個值會漂移（見 AGENTS.md 鐵律 12）。
    改成結構性判斷：只要路徑在 repo 下的 data/ 子樹裡就拒絕。
    """
    resolved = path.resolve()
    data_dir = (REPO_ROOT / "data").resolve()
    if resolved == data_dir or data_dir in resolved.parents:
        raise SystemExit(f"拒絕寫入 data/ 之下（生產狀態）：{resolved}")
    return resolved


def blind_label_map(model_ids: list[str]) -> dict[str, str]:
    """決定 MODEL_A… 的順序。

    刻意**不照字母序**：照字母序的話，知道參賽名單的人可以直接推出誰是 MODEL_A。
    改用 sha256(model_id) 排序——同一份名單永遠得到同一個映射（可重現），
    但只看名單推不出來。
    """
    ordered = sorted(model_ids, key=lambda m: hashlib.sha256(m.encode()).hexdigest())
    return {mid: f"MODEL_{chr(ord('A') + i)}" for i, mid in enumerate(ordered)}


def anonymize_record(rec: dict, label_map: dict[str, str]) -> dict:
    out = {k: v for k, v in rec.items() if k not in LEAKY_FIELDS}
    real = rec.get("model_id")
    out["model_id"] = label_map.get(real, "MODEL_UNKNOWN")
    # 錯誤訊息可能含模型名／端點路徑，一律換成通用描述，避免「靠錯誤訊息認人」。
    if out.get("error"):
        out["error"] = f"(呼叫失敗，細節已於匿名化時移除：status={rec.get('status_code')})"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SOEB 匿名化：model_id → MODEL_A…F")
    ap.add_argument("--run-dir", required=True, help="observations.jsonl 所在目錄")
    ap.add_argument("--out-dir", default=None, help="盲評輸出目錄（預設 <run-dir>/blind）")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    obs_path = run_dir / "observations.jsonl"
    if not obs_path.exists():
        print(f"Error: 找不到 {obs_path}", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in obs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    model_ids = sorted({r["model_id"] for r in rows})
    label_map = blind_label_map(model_ids)

    out_dir = assert_not_under_data(Path(args.out_dir) if args.out_dir else run_dir / "blind")
    out_dir.mkdir(parents=True, exist_ok=True)

    blind_path = out_dir / "observations_blind.jsonl"
    with open(blind_path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in rows:
            fh.write(json.dumps(anonymize_record(rec, label_map), ensure_ascii=False) + "\n")

    # 映射檔與答案相關的內容**放在盲評目錄之外**，並在檔名上寫明何時才能開。
    mapping_path = assert_not_under_data(run_dir / "MAPPING_DO_NOT_OPEN_BEFORE_SCORING.json")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["model_id"]] = counts.get(r["model_id"], 0) + 1
    payload = {
        "warning": "盲評完成前不要打開這個檔。打開後請在評分紀錄上註明。",
        "run_dir": str(run_dir),
        "label_to_model": {v: k for k, v in label_map.items()},
        "calls_per_model": {label_map[k]: v for k, v in counts.items()},
        "total_records": len(rows),
    }
    with open(mapping_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("=" * 70)
    print(f"來源   : {obs_path}  ({len(rows)} 列)")
    print(f"盲評檔 : {blind_path}")
    print(f"映射檔 : {mapping_path}   ← 盲評完成前不要開")
    print(f"標籤   : {len(label_map)} 個（{', '.join(sorted(label_map.values()))}）")
    print("-" * 70)
    print(f"已移除欄位：{', '.join(LEAKY_FIELDS)}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
