import re
import time
import datetime
from typing import Optional
from .models import Fact
from .graph_store import GraphStore

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