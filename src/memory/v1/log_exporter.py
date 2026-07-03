"""
src/memory/v1/log_exporter.py
v1 Log Exporter — append-only JSONL writer。

Constitution:
- 寫入:append-only(只能新增,不可改)
- 格式:JSONL(每行一筆)
- 不索引 / 不壓縮 / 不加密(v1 簡單)

Bry 規範:corrupt row 不修改,跳過;這個 exporter 永遠只寫入,不管讀。
"""
import json
from pathlib import Path
from .schema import RetrievalLog


class LogExporter:
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def append(self, log: RetrievalLog) -> None:
        """append 一筆 retrieval log。"""
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log.to_jsonl() + "\n")

    def all(self) -> list:
        """讀回所有 log(給分析用)。"""
        if not self.log_file.exists():
            return []
        logs = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    logs.append(data)
                except json.JSONDecodeError:
                    # Constitution: corrupt row 不修改,跳過
                    print(f"[LogExporter] corrupt log row in {self.log_file}")
        return logs
