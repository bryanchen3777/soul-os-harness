"""
src/work/artifact_store.py
Artifact store — content-addressed durable artifact 寫入/驗證（DSH P1-B D3/D6/D10）。

Core boundary（P1-B §0 / §2，Domain Core 是 artifact authority）：
- **refs 是 content-addressed identity**（`sha256:<hex>`），由 Domain Core
  從 artifact content 計算（D1）——adapter 不能宣告任意 ref。
- **canonical writer 是 Domain Core**（D2/D3）：寫入受 single-writer 保護，
  只有 kernel（`is_durable_writer`）能寫；adapter 只 transport content。
- **寫入是原子的**（D6）：write temp + `os.replace`（atomic rename）。
  crash 於 temp 階段 → canonical 路徑從未出現，重跑重寫；rename 原子 →
  canonical 路徑要嘛完整存在、要嘛不存在，無 partial 狀態。
- **claimed ref → verify**（D10）：`verify_artifact_ref` 檢查存在性 + hash
  相符，供 claim→verify 三層的 content 層使用。
- **staging**（P1-B §3.1 選項 B）：`data_root()/work/staging/` 是 canonical
  store 之外的中轉區（adapter 可寫，非 canonical store）；Domain Core
  `ingest_staging` 驗證 hash 後**移入** canonical store 並清理。

本模組零 DSH import（Domain Core 永久不變）：content 是純 bytes，
ref 是 `sha256:<hex>` 字串，與 DSH 無關。
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from src.paths import data_root

from .bridge import DURABLE_WRITER, is_durable_writer
from .store import NotDurableWriterError

# content-addressed ref 格式：sha256:<64 位 hex>（大小寫均可——hex 大小寫是
# 表示法，不是語義；寫入 canonical 時統一小寫，verify 時正規化比較）。
_REF_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


class ArtifactStoreError(RuntimeError):
    """artifact store 操作失敗（寫入衝突 / 驗證不符 / staging ingest 失敗）。"""


class ArtifactStore:
    """content-addressed artifact store（canonical writer = Domain Core）。

    路徑約定：
    - canonical：`data_root()/work/artifacts/<sha256 hex>`
    - staging：`data_root()/work/staging/`（canonical store 之外）
    傳入 data_dir 可覆寫（測試隔離用），預設 data_root() / "work"。

    寫入 single-writer enforcement：`write_artifact` / `ingest_staging` 的
    actor 必須明確提供且是 durable writer（kernel），否則
    `NotDurableWriterError`（與 WorkStore.append 同一套 enforcement，
    P1-B D3：artifact store 受 single-writer 保護）。
    """

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            data_dir = data_root() / "work"
        self.data_dir = Path(data_dir).resolve()
        self.artifacts_dir = self.data_dir / "artifacts"
        self._staging_dir = self.data_dir / "staging"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    # ── 路徑 ──

    def staging_dir(self) -> Path:
        """staging 中轉區（canonical store 之外；adapter 可寫，非 canonical）。"""
        return self._staging_dir

    def artifact_path(self, ref: str) -> Path:
        """ref → canonical 檔案路徑；非法 ref → ArtifactStoreError。"""
        return self.artifacts_dir / self._hex_of(ref)

    # ── 寫入（single-writer：只有 kernel 能寫） ──

    def write_artifact(self, content: bytes, actor: str) -> str:
        """把 content 寫入 canonical artifact store，回傳 `sha256:<hex>` ref。

        single-writer（D3）：actor 必須是 durable writer（kernel），否則
        `NotDurableWriterError`。寫入是 write temp + atomic rename（D6）：
        - temp 寫入 `<hex>.tmp` → 完整寫入後 `os.replace` 原子 rename。
        - crash 於 temp 階段 → 孤兒 temp，canonical 路徑從未出現 → 重跑重寫。
        - 同 content → 同 hash → 同 ref，不產生第二份（dedup 冪等）。
        """
        self._check_writer(actor)
        digest = hashlib.sha256(content).hexdigest()
        target = self.artifacts_dir / digest
        if target.exists():
            # 內容定址冪等：同 content 同路徑。若既有檔 hash 不符 → 資料衝突。
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ArtifactStoreError(
                    f"artifact collision at {target}: existing content hash "
                    f"does not match {digest}"
                )
            return f"sha256:{digest}"

        tmp = target.with_name(digest + ".tmp")
        tmp.write_bytes(content)
        try:
            os.replace(tmp, target)  # 原子 rename（D6）
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        return f"sha256:{digest}"

    def ingest_staging(self, staged_path: Path | str, actor: str) -> str:
        """staging → canonical ingest（P1-B §3.1 選項 B）：驗證 hash 後移入並清理。

        只接受 staging 目錄內的檔案（canonical store 之外的中轉區）；讀取
        staged content → `write_artifact`（Domain Core 計算 ref）→ 刪除
        staged 檔案（ingest 後清理）。
        """
        self._check_writer(actor)
        staged = Path(staged_path).resolve()
        if not self._is_within(staged, self._staging_dir.resolve()):
            raise ArtifactStoreError(
                f"staged path {staged} is outside staging dir "
                f"{self._staging_dir}; only staging content may be ingested"
            )
        if not staged.is_file():
            raise ArtifactStoreError(f"staged artifact not found: {staged}")
        content = staged.read_bytes()
        ref = self.write_artifact(content, actor)
        try:
            staged.unlink(missing_ok=True)  # ingest 後清理
        except OSError:
            pass  # 清理失敗不影響 canonical 落盤（孤兒 staging 由清理機制處理）
        return ref

    # ── 驗證（claimed ref → 存在性 + hash，D10） ──

    def verify_artifact_ref(self, ref: str) -> bool:
        """claimed ref → canonical 檔案存在性 + content hash 相符（P1-B D10）。

        非 `sha256:<hex>` 格式 / 檔案不存在 / hash 不符 → False（fail-closed
        由呼叫端決定）。這是 claim→verify 三層的 content 層。
        """
        if not _REF_RE.match(ref):
            return False
        digest = self._hex_of(ref)
        target = self.artifacts_dir / digest
        if not target.is_file():
            return False
        try:
            return hashlib.sha256(target.read_bytes()).hexdigest() == digest
        except OSError:
            return False

    # ── internal ──

    @staticmethod
    def _hex_of(ref: str) -> str:
        if not _REF_RE.match(ref):
            raise ArtifactStoreError(
                f"invalid content-addressed ref {ref!r}; expected sha256:<64 hex>"
            )
        return ref[len("sha256:"):].lower()

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        return path.is_relative_to(root)

    @staticmethod
    def _check_writer(actor: str) -> None:
        if not is_durable_writer(actor):
            raise NotDurableWriterError(
                f"actor={actor!r} is not the durable writer; "
                f"only {DURABLE_WRITER!r} may write the artifact store "
                f"(P1-B D3 single-writer)"
            )
