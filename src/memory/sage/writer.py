import asyncio
import logging
import os
import re
import time
import datetime
from pathlib import Path  # Bry §21: 修 Path import bug, Bry §12 _mirror_to_v1_store 用 Path 但 module 沒 import
from typing import Any, Dict, List, Optional, Tuple

from .models import Fact
from .graph_store import GraphStore
from .token_utils import TokenBudget, SummaryCompressor

logger = logging.getLogger("soul_os.memory.writer")

# Predicate 同義詞正規化表（W-2）
_PREDICATE_SYNONYMS: dict[str, str] = {
    "enjoys":       "likes",
    "loves":        "likes",
    "adores":       "likes",
    "prefers":      "likes",
    "dislikes":     "hates",
    "despises":     "hates",
    "loathes":      "hates",
    "is_at":        "lives_in",
    "located_in":   "lives_in",
    "resides_in":   "lives_in",
    "employed_at":  "works_at",
    "employed_by":  "works_at",
    "works_for":    "works_at",
    "喜愛":         "喜歡",
    "熱愛":         "喜歡",
    "厭惡":         "討厭",
    "居住在":       "住在",
    "任職於":       "工作於",
}

# 反義 predicate 對（用於 contradiction detection）
_ANTONYM_PREDICATES: list[frozenset[str]] = [
    frozenset({"likes",    "hates"}),
    frozenset({"likes",    "dislikes"}),
    frozenset({"loves",    "hates"}),
    frozenset({"lives_in", "not_in"}),
    frozenset({"is",       "is_not"}),
    frozenset({"喜歡",      "討厭"}),
]

_RELATION_PATTERNS: list[tuple[str, str, float]] = [
    (r"(.+?)\s+is\s+(?:a\s+|an\s+|the\s+)?(.+)",           "is",          1.0),
    (r"(.+?)\s+are\s+(.+)",                                  "is",          1.0),
    (r"(.+?)\s+was\s+(?:a\s+|an\s+)?(.+)",                  "was",         0.8),
    (r"(.+?)\s+(?:really\s+)?likes?\s+(.+)",                 "likes",       1.0),
    (r"(.+?)\s+loves?\s+(.+)",                               "likes",       1.0),
    (r"(.+?)\s+(?:really\s+)?hates?\s+(.+)",                 "hates",       1.0),
    (r"(.+?)\s+(?:enjoys?|enjoyed)\s+(.+)",                  "likes",       0.9),
    (r"(.+?)\s+(?:prefers?)\s+(.+)",                         "likes",       0.9),
    (r"(.+?)\s+(?:dislikes?|doesn'?t like)\s+(.+)",          "hates",       1.0),
    (r"(.+?)\s+(?:works?|worked)\s+(?:at|for|in)\s+(.+)",   "works_at",    1.0),
    (r"(.+?)\s+(?:lives?|lived)\s+(?:in|at|near)\s+(.+)",   "lives_in",    1.0),
    (r"(.+?)\s+(?:is\s+from|comes?\s+from)\s+(.+)",         "from",        0.9),
    (r"(.+?)\s+(?:knows?|knew)\s+(.+)",                      "knows",       0.9),
    (r"(.+?)\s+(?:has|have|had)\s+(?:a\s+|an\s+)?(.+)",     "has",         0.9),
    (r"(.+?)\s+(?:wants?|wanted|need[s]?)\s+(.+)",           "wants",       0.8),
    (r"(.+?)\s+(?:plans?\s+to|going\s+to)\s+(.+)",           "plans_to",    0.8),
    (r"(.+?)喜歡(.+)",   "喜歡",  1.0),
    (r"(.+?)討厭(.+)",   "討厭",  1.0),
    (r"(.+?)住在(.+)",   "住在",  1.0),
    (r"(.+?)在(.+?)工作", "工作於", 1.0),
    (r"(.+?)是(.+)",     "是",    1.0),
    (r"(.+?)有(.+)",     "有",    0.9),

    # ── Phase 2.3 擴充：口語句型 ──
    # 分類 A：移動 / 行為動詞（weight 0.9，雜訊比正式句型高）
    (r"(.{1,10})去了?(.{1,20})",   "去",    0.9),
    (r"(.{1,10})來了?(.{1,20})",   "來",    0.9),
    (r"(.{1,10})到了?(.{1,20})",   "到",    0.9),
    (r"(.{1,10})買了?(.{1,20})",   "買",    0.9),
    (r"(.{1,10})吃了?(.{1,20})",   "吃",    0.9),
    (r"(.{1,10})見了?(.{1,20})",   "見",    0.9),
    (r"(.{1,10})用了?(.{1,20})",   "使用",  0.9),

    # 分類 B：狀態描述（weight 0.8-1.0）
    (r"(.{1,10})覺得(.{1,20})",    "覺得",  0.8),
    (r"(.{1,10})感覺(.{1,20})",    "感覺",  0.8),
    (r"(.{1,10})住(.{1,15})",      "住在",  1.0),  # 補「我住台北」漏網

    # 分類 C：時間 / 承諾（weight 1.0，subject 至少 1 字）
    # 注：原 spec 寫 3 字下限會擋掉 "你說過" 這種 1 字 subject 的正常句型
    #     實際上 1 字下限對 "上次見到→去" 這種 false positive 沒實質幫助
    #     （keyword 不在 position 1 就對不上），所以降為 1
    (r"(.{1,15})說過(.{1,20})",    "說過",  1.0),
    (r"(.{1,15})答應(.{1,20})",    "答應",  1.0),
    (r"(.{1,15})上次(.{1,20})",    "上次",  1.0),
    (r"(.{1,15})下次(.{1,20})",    "下次要", 1.0),
    (r"(.{1,15})記得(.{1,20})",    "記得",  1.0),
]

_NOISE_OBJECTS = {
    "it", "this", "that", "things", "something", "anything",
    "him", "her", "them", "there", "here", "now", "then",
}
_FIRST_PERSON = {"i", "me", "my", "myself", "i'm", "i've", "i'd"}

_DATE_PATTERNS = [
    (r"(\d{1,2})[月/\-](\d{1,2})[日號]?", "month_day"),
    (r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})", "full_date"),
    (r"明天|tomorrow",    "tomorrow"),
    (r"後天",              "day_after_tomorrow"),
    (r"下週|next week",    "next_week"),
    (r"下個月|next month", "next_month"),
]


class WriteResult:
    """write 操作的確認回傳（A-2：write confirmation）"""
    def __init__(self):
        self.written: list[str] = []
        self.merged:  list[str] = []
        self.rejected: list[tuple[str, list[str]]] = []
        self.contradictions: list[tuple[Fact, Fact]] = []

    @property
    def success_count(self) -> int:
        return len(self.written) + len(self.merged)

    @property
    def has_failures(self) -> bool:
        return len(self.rejected) > 0


class MemoryWriter:
    """Writer v4：schema gate + predicate 正規化 + write confirmation"""

    ANCHOR_WEIGHT_THRESHOLD = 1.8

    def __init__(self, graph_store: GraphStore, default_session_id: str = ""):
        self.store = graph_store
        self.default_session_id = default_session_id

    # ── 公開 API ──────────────────────────────────────────────

    def add_fact(self, fact: Fact) -> str:
        result = self._write_single(fact)
        if result.written:
            return result.written[0]
        if result.merged:
            return result.merged[0]
        return ""

    def add_facts_batch(self, facts: list[Fact]) -> list[str]:
        return [fid for f in facts for fid in [self.add_fact(f)] if fid]

    def write_with_confirmation(self, fact: Fact) -> WriteResult:
        result = WriteResult()
        self._write_single(fact, result)
        return result

    def extract_and_write(
        self,
        text: str,
        subject_hint: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "user",
    ) -> list[str]:
        sid = session_id or self.default_session_id
        facts = self._extract_facts(text, subject_hint, sid, source)
        return self.add_facts_batch(facts)

    def extract(
        self,
        text: str,
        subject_hint: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "user",
    ) -> list[Fact]:
        """只抽取事實、不寫入 graph。測試用與下游預處理層用。"""
        sid = session_id or self.default_session_id
        return self._extract_facts(text, subject_hint, sid, source)

    def write_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: Optional[str] = None,
    ) -> list[str]:
        sid = session_id or self.default_session_id
        user_ids = self.extract_and_write(
            user_content, subject_hint="user",
            session_id=sid, source="user"
        )
        assistant_ids = self.extract_and_write(
            assistant_content, subject_hint="assistant",
            session_id=sid, source="inference"
        )
        return user_ids + assistant_ids

    # ── 內部核心 ──────────────────────────────────────────────

    def _write_single(
        self, fact: Fact, result: Optional[WriteResult] = None
    ) -> WriteResult:
        if result is None:
            result = WriteResult()

        if not fact.session_id:
            fact.session_id = self.default_session_id

        # 1. Schema gate
        errors = fact.validate()
        if errors:
            result.rejected.append((str(fact.to_dict()), errors))
            return result

        # 2. Predicate 正規化
        fact.predicate = _PREDICATE_SYNONYMS.get(
            fact.predicate, fact.predicate
        )

        # 3. Entity 對齊
        fact.subject = self._align_entity(fact.subject)
        fact.object  = self._align_entity(fact.object)

        # 4. Contradiction detection
        contradiction = self._find_contradiction(fact)
        if contradiction:
            result.contradictions.append((fact, contradiction))
            if fact.confidence > contradiction.confidence:
                self.store.update_weight(
                    contradiction.fact_id,
                    max(0.0, contradiction.weight - 0.3)
                )
            else:
                fact.confidence *= 0.5

        # 5. 重複偵測與合併
        existing = self._find_similar(fact)
        if existing:
            new_weight = min(2.0, existing.weight + fact.weight * 0.3)
            if new_weight >= self.ANCHOR_WEIGHT_THRESHOLD and not existing.is_anchor:
                self.store.set_anchor(existing.fact_id, True)
            else:
                self.store.update_weight(existing.fact_id, new_weight)
            result.merged.append(existing.fact_id)
            return result

        # 6. 寫入
        fid = self.store.add_fact(fact)
        if fid:
            result.written.append(fid)
        else:
            result.rejected.append((str(fact.to_dict()), ["store write failed"]))
        return result

    def _find_contradiction(self, fact: Fact) -> Optional[Fact]:
        norm_pred = _PREDICATE_SYNONYMS.get(fact.predicate, fact.predicate)
        existing = self.store.search_by_entity(fact.subject)
        for e in existing:
            if e.subject.lower() != fact.subject.lower():
                continue
            if e.object.lower() != fact.object.lower():
                continue
            e_norm = _PREDICATE_SYNONYMS.get(e.predicate, e.predicate)
            ep = frozenset({e_norm})
            fp = frozenset({norm_pred})
            for antonym_pair in _ANTONYM_PREDICATES:
                if (norm_pred in antonym_pair
                        and e_norm in antonym_pair
                        and e_norm != norm_pred):
                    return e
        return None

    def _align_entity(self, name: str) -> str:
        similar = self.store.find_similar_entity(name, threshold=0.75)
        return similar if similar else name

    def _extract_event_time(self, text: str) -> Optional[float]:
        now = time.time()
        for pattern, tag in _DATE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                if tag == "tomorrow":
                    return now + 86400
                elif tag == "day_after_tomorrow":
                    return now + 86400 * 2
                elif tag == "next_week":
                    return now + 86400 * 7
                elif tag == "next_month":
                    return now + 86400 * 30
                elif tag == "month_day":
                    m = re.search(pattern, text)
                    if m:
                        try:
                            t = datetime.datetime(
                                datetime.datetime.now().year,
                                int(m.group(1)), int(m.group(2))
                            )
                            return t.timestamp()
                        except ValueError:
                            pass
                elif tag == "full_date":
                    m = re.search(pattern, text)
                    if m:
                        try:
                            t = datetime.datetime(
                                int(m.group(1)), int(m.group(2)), int(m.group(3))
                            )
                            return t.timestamp()
                        except ValueError:
                            pass
        return None

    def _normalize_entity(self, raw: str, subject_hint: Optional[str]) -> str:
        cleaned = raw.strip()
        cleaned_lower = cleaned.lower()
        if cleaned_lower in _FIRST_PERSON:
            return subject_hint or "user"
        cleaned = re.sub(r"^(the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        has_chinese = bool(re.search(r"[一-鿿]", cleaned))
        return cleaned if has_chinese else cleaned.capitalize()

    def _normalize_object(self, raw: str) -> str:
        cleaned = raw.strip()
        cleaned = re.sub(r"[,.;:]+$", "", cleaned)
        cleaned = re.sub(r"^(the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned.lower() in _NOISE_OBJECTS:
            return ""
        return cleaned

    def _find_similar(self, fact: Fact) -> Optional[Fact]:
        existing = self.store.search_by_entity(fact.subject, min_weight=0.01)
        for e in existing:
            if (e.subject.lower() == fact.subject.lower()
                    and e.predicate == fact.predicate
                    and e.object.lower() == fact.object.lower()):
                return e
        return None

    def _extract_facts(
        self, text: str, subject_hint: Optional[str],
        session_id: str, source: str
    ) -> list[Fact]:
        # Feature flag: USE_LLM_JUDGE=true/false 切換 LLM judge 跟 regex heuristic
        # 預設開啟 LLM judge;失敗 fallback 舊 regex
        use_llm = os.environ.get("USE_LLM_JUDGE", "true").lower() == "true"
        if use_llm:
            try:
                return self._extract_facts_llm(text, subject_hint, session_id, source)
            except Exception as e:
                logger.warning(
                    f"[MemoryWriter] LLM judge 失敗,fallback heuristic: {e}"
                )
        # fallback 舊 regex
        return self._extract_facts_heuristic_fallback(
            text, subject_hint, session_id, source
        )

    def _extract_facts_llm(
        self, text: str, subject_hint: Optional[str],
        session_id: str, source: str
    ) -> list[Fact]:
        """
        LLM-as-judge 版的 fact 萃取。
        同步介面但內部呼叫 asyncio.run() 跑 async LLMJudge。
        失敗拋 exception,由 _extract_facts 統一 fallback。
        """
        # 取得 LLMJudge(從 _llm_judge 屬性或 lazy init)
        judge = self._get_llm_judge()
        if judge is None:
            raise RuntimeError("LLMJudge not available")
        # 跑 async extract_and_judge
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        results = loop.run_until_complete(
            judge.extract_and_judge(text, context="", agent_id=subject_hint or "")
        )
        # 轉成 Fact 物件
        facts = []
        seen: set[tuple[str, str, str]] = set()
        for r in results:
            subj = self._normalize_entity(r["subject"], subject_hint)
            obj = self._normalize_object(r["object"])
            if not subj or not obj:
                continue
            key = (subj.lower(), r["predicate"].lower(), obj.lower())
            if key in seen:
                continue
            seen.add(key)
            weight = 0.5 * (0.8 if source == "inference" else 1.0)
            facts.append(Fact(
                subject=subj, predicate=r["predicate"], object=obj,
                timestamp=time.time(), event_time=None,
                weight=weight, confidence=r["confidence"],
                source=source, session_id=session_id,
            ))

        # Perplexity 拍板 (Bry 轉, 2026-07-02): 並行寫進 v1 store
        # - 只把 v6 judge 輸出的 (text, category, confidence, tags) 寫進去
        # - metadata-only, 不做任何格式轉換 / 語意解讀 / 額外判斷
        # - 主路徑(SAGE Graph write) 不變, v1 store 是平行鏡像,供 Loader 讀
        # - Bry §23 spec (2026-07-02): log 從 warning 升 info + 顯式記錄 memory_id / agent_id / content
        #   給 Bry 真實對話後, 直接從 log 跟 jsonl 看 mirror 真實寫入
        try:
            mirror_count = self._mirror_to_v1_store(
                text=text,
                results=results,
                subject_hint=subject_hint,
                session_id=session_id,
                source=source,
            )
            # 顯式記錄: Bry §23 spec 要求 log 讓人一眼看出 mirror 寫了什麼
            if mirror_count and mirror_count > 0:
                logger.info(
                    f"[MemoryWriter] v1 store mirror 成功 | "
                    f"agent={subject_hint} | "
                    f"source={source} | "
                    f"n_facts_mirrored={mirror_count}/{len(results)} | "
                    f"text={text[:50]!r}"
                )
            else:
                logger.info(
                    f"[MemoryWriter] v1 store mirror 0 筆 | "
                    f"agent={subject_hint} | "
                    f"source={source} | "
                    f"v6 judge 抽出 {len(results)} 筆, 但 mirror 過濾後 0 筆 | "
                    f"text={text[:50]!r}"
                )
        except Exception as e:
            # Bry §23 spec: writer 寫進 v1 不失敗主路徑, 但 log 從 warning 升到 ERROR (仍是 try/except 不中斷)
            logger.error(
                f"[MemoryWriter] v1 store mirror 失敗 | "
                f"agent={subject_hint} | "
                f"source={source} | "
                f"error={e!r}"
            )

        return facts

    def _mirror_to_v1_store(
        self,
        text: str,
        results: list,
        subject_hint: Optional[str],
        session_id: str,
        source: str,
    ) -> None:
        """Perplexity 拍板 (Bry 轉, 2026-07-02): 把 v6 judge 結果鏡像到 v1 store。

        Bry §12 spec:
        - 寫 (text, category, confidence, tags) per fact
        - 不做語意解讀 / 不做格式轉換
        - 既有資料沒 category/confidence → None (Loader fail-safe 自然排除)

        Data dir 路徑 (Perplexity spec: metadata-only, 不動 SAGE 主路徑):
        - GraphStore 的 db_path = $data_dir/<agent>.db (SQLite)
        - v1 Store 跟 GraphStore 共享同一個 data_dir
        - 但檔案格式不同: GraphStore 用 .db, V1Store 用 .jsonl
        - 所以從 self.store.db_path 反推 self.store.db_path.parent 當 v1 data_dir
        """
        import uuid as _uuid
        from src.memory.v1.store import V1Store as _V1Store
        from src.memory.v1.schema import Memory as _Memory

        # 取得 v1 store data_dir (跟 GraphStore 共用 parent 目錄)
        if not hasattr(self, "store") or self.store is None:
            raise RuntimeError("V1 store 鏡像失敗: writer.store 不存在")
        graph_db_path = getattr(self.store, "db_path", None)
        if graph_db_path is None:
            raise RuntimeError("V1 store 鏡像失敗: GraphStore.db_path 不存在")
        v1_data_dir = Path(graph_db_path).parent

        v1_store = _V1Store(v1_data_dir, subject_hint or "unknown")
        # Perplexity Bry §15 拍板: 補齊 tags 的檢索用途缺陷
        # - content 字面切詞追加進 tags (跟 middleware._derive_query_tags 同套邏輯)
        # - category 標籤保留不刪
        # - 沒牽動 judge 的 category/confidence 判斷邏輯
        # - 純機械動作, 不用 LLM, 不做語意判斷
        from src.memory.v1.loader import derive_query_tags as _derive_tags
        # Bry §23 spec: 回傳 mirror 筆數給 caller log 用
        mirror_count = 0
        for r in results:
            subj = self._normalize_entity(r["subject"], subject_hint)
            obj = self._normalize_object(r["object"])
            if not subj or not obj:
                continue
            # 組裝 content: 簡單三元組文字 (Perplexity 拍: 不做語意解讀, 純組裝)
            content = f"{subj} {r['predicate']} {obj}"
            # Bry §15: tags = 既有 tags (含 category) + content 切詞
            # 兩者共存, 不覆蓋
            existing_tags = list(r.get("tags", []))
            content_tags = _derive_tags(content)
            merged_tags = existing_tags + content_tags
            v1_store.add(_Memory(
                memory_id=str(_uuid.uuid4()),
                agent_id=subject_hint or "unknown",
                content=content,
                tags=merged_tags,
                created_at=time.time(),
                # v1.1 schema 加的兩個 Optional 欄位, Perplexity (b)
                category=r.get("category"),
                confidence=r.get("confidence"),
            ))
            mirror_count += 1
        return mirror_count

    def _get_llm_judge(self):
        """Lazy init LLMJudge。需要 LLMProxy 跟 LLM backend 已經 set up。"""
        if hasattr(self, "_llm_judge_cached") and self._llm_judge_cached is not None:
            return self._llm_judge_cached
        # 從 GraphStore 路徑反向找 LLMProxy(從 process-global 變數)
        llm_proxy = globals().get("_global_llm_proxy") or _find_llm_proxy()
        if llm_proxy is None:
            return None
        from src.memory.llm_judge import LLMJudge
        self._llm_judge_cached = LLMJudge(llm_proxy)
        return self._llm_judge_cached

    def _extract_facts_heuristic_fallback(
        self, text: str, subject_hint: Optional[str],
        session_id: str, source: str
    ) -> list[Fact]:
        facts: list[Fact] = []
        seen: set[tuple[str, str, str]] = set()
        # Primary split: sentence-ending punctuation or newlines
        primary = re.split(r"(?<=[.!?。！？])\s+|[\n]+", text)
        for segment in primary:
            # Secondary split: conjunctions after sentence boundary
            sub_parts = re.split(
                r"(?<=[.!?])\s+(?:but|however|although|so|then)\s+",
                segment, flags=re.IGNORECASE,
            )
            for sentence in sub_parts:
                sentence = sentence.strip()
                if len(sentence) < 4:
                    continue
                event_time = self._extract_event_time(sentence)
                for pattern, predicate, base_weight in _RELATION_PATTERNS:
                    m = re.search(pattern, sentence, re.IGNORECASE)
                    if not m or len(m.groups()) < 2:
                        continue
                    subj_raw = m.group(1).strip()
                    obj_raw  = m.group(2).strip()
                    subj = self._normalize_entity(subj_raw, subject_hint)
                    obj  = self._normalize_object(obj_raw)
                    if not subj or not obj:
                        continue
                    if obj.lower() in _NOISE_OBJECTS:
                        continue
                    if len(subj) > 60 or len(obj) > 100:
                        continue
                    norm_pred = _PREDICATE_SYNONYMS.get(predicate, predicate)
                    key = (subj.lower(), norm_pred, obj.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    weight = base_weight * (0.8 if source == "inference" else 1.0)
                    facts.append(Fact(
                        subject=subj, predicate=norm_pred, object=obj,
                        timestamp=time.time(), event_time=event_time,
                        weight=weight, confidence=base_weight,
                        source=source, session_id=session_id,
                    ))
        return facts


def _find_llm_proxy():
    """從 process-global 或 runner 變數找 LLMProxy。

    為什麼需要:MemoryWriter._get_llm_judge() 需要 LLMProxy 實例,
    但 MemoryWriter 是由 SAGELiteProvider 在 lifespan 內建構,
    沒辦法直接 import run_server 的 proxy 全域變數。
    解法:MemoryMiddleware 或 run_server 透過 set_llm_proxy() 設定,
    這裡 fallback 到 process-global 變數查詢。
    """
    return globals().get("_soul_os_llm_proxy")


def set_llm_proxy(llm_proxy):
    """設定 process-global LLMProxy reference,讓 MemoryWriter 可以拿到。"""
    globals()["_soul_os_llm_proxy"] = llm_proxy