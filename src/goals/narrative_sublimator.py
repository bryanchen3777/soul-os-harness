"""
src/goals/narrative_sublimator.py — PeriodicNarrativeSublimator（C-2.1 週期敘事昇華）

设计来源: docs/C-2.1-COMMITMENT-AND-NARRATIVE-CONTRACT.md
（§5 週期敘事昇華契約 / §5.2 週記聚合規則 / §5.3 紀念日反芻規則 /
§5.4 沉澱契約 / §5.5 身分防火牆; TL-11 A3/A5/A6 驗收草案）

职责: 把一段時間內該靈魂自己的生活素材（diary / trace / goal 終態）
重構為「共同生活故事」一次沉澱（read-side 聚合 + 一次 create_event）:
  - 週記: 當前 ISO 週（YYYY-Www）判據, 目標頻率 1 次/週;
    聚合窗 = 本週週一 00:00 → 觸發時刻; 窗內全聚合不挑選;
    冪等鍵 trace_ref = periodic:{YYYY-Www}
  - 紀念日: 既有 calendar_event 白名單「今日事件」（perception_trace
    accepted）觸發; 聚合往年今日（同 M-D 不同年）自己 diary;
    空聚合 fail-closed 不沉澱; 冪等鍵 periodic:memorial:{YYYY-MM-DD}

边界（契約 §7.1 additive 清單 #5）:
  - 0 新定時器（掛 scheduler night slot 檢查鏈 additive 分支）
  - 0 新 trigger_type（複用 TRIGGER_TYPE_SYSTEM）; 0 新 payload 通道
  - 唯一出口 = InnerLifeWriter.create_event（canonical producer, 既有昇華鏈）
  - 0 直寫 SAGE facts; 0 選擇打分（全聚合/日期匹配 = 結構規則）
  - 身分防火牆: 只聚合自己的 diary 目錄 + actor_id==self 的 trace /
    自己的 goals; 他者經歷 0 內化
  - fail-closed: 任何異常只 log warning; LLM 失敗 / 空聚合 → 0 半成品
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from src.goals.models import GOAL_TERMINAL_STATES
from src.goals.motive_provider import _goal_db_path
from src.goals.seed_provider import (
    _extract_json_dict,
    _read_jsonl,
    data_root_path,
)
from src.timezone_utils import LOCAL_TZ

logger = logging.getLogger("soul_os.goals.narrative_sublimator")

# trace_ref 命名空間前綴（契約附錄 B.2: periodic: 週期敘事）
TRACE_REF_PREFIX = "periodic:"

# 已回顧/已沉澱引用前綴（聚合時跳過, 避免週記递归引用自己的週記/沉澱）
_SKIPPED_REF_PREFIXES = ("goal:", "periodic:")


class PeriodicNarrativeSublimator:
    """
    週期敘事昇華器（read-side 聚合器 + 一次 create_event 調用）。

    形態與 sediment_completion（motive_provider.py）同級: 直接 producer 調用,
    不觸碰 diary 排程實作 / DiaryHandler / 4 handlers。

    用法（scheduler night slot 檢查鏈 additive 分支, 0 新定時器）:
        sublimator = PeriodicNarrativeSublimator(agent_id=agent_id)
        await sublimator.sublimate_weekly()    # 冪等鍵 periodic:YYYY-Www
        await sublimator.sublimate_memorial()  # 冪等鍵 periodic:memorial:YYYY-MM-DD

    兩者每夜可並存（獨立冪等鍵, 各自一次沉澱）。
    """

    def __init__(
        self,
        agent_id: str,
        llm_call: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.agent_id = agent_id
        # 方案 B 語義化通道（None → 既有 default proxy, 與 seed_provider 同款）
        self._llm_call = llm_call

    # ── 週記（ISO 週判據, 1 次/週）────────────────────────

    async def sublimate_weekly(self, now: Optional[datetime] = None) -> Optional[str]:
        """
        週記沉澱: 當前 ISO 週無既有沉澱 → 聚合本週至今素材 → 一次 create_event。

        Returns:
            event_id（成功）/ None（冪等跳過 / 空聚合 / LLM 失敗, 全 fail-closed）
        """
        now = now or datetime.now(LOCAL_TZ)
        local = now.astimezone(LOCAL_TZ)
        iso = local.isocalendar()
        period_key = f"{iso.year}-W{iso.week:02d}"
        trace_ref = f"{TRACE_REF_PREFIX}{period_key}"
        if self._trace_exists(trace_ref):
            logger.debug(
                f"[PeriodicNarrative] 週記冪等跳過: {trace_ref} 已沉澱 "
                f"agent={self.agent_id}"
            )
            return None
        # 聚合窗 = 當前 ISO 週週一 00:00 → 觸發時刻（本地; 契約 §5.2）
        monday = local.date() - timedelta(days=local.weekday())
        start_dt = datetime.combine(monday, time.min, tzinfo=LOCAL_TZ)
        material = self._aggregate_week(start_dt, local)
        if not material.strip():
            logger.debug(
                f"[PeriodicNarrative] 週記空聚合 (fail-closed 不沉澱) "
                f"agent={self.agent_id} period={period_key}"
            )
            return None
        prompt = self._weekly_prompt(period_key, material)
        return await self._sublimate(
            trace_ref=trace_ref,
            extras={
                "period": "weekly",
                "period_start": monday.isoformat(),
                "period_end": local.date().isoformat(),
            },
            prompt=prompt,
            now=now,
        )

    # ── 紀念日（calendar_event 白名單「今日事件」觸發）──────

    async def sublimate_memorial(self, now: Optional[datetime] = None) -> Optional[str]:
        """
        紀念日反芻: 今晚有「事件日 == 今天」的 accepted calendar_event → 聚合
        往年今日自己 diary → 一次 create_event; 空聚合 fail-closed 不沉澱。

        Returns:
            event_id（成功）/ None（無事件 / 冪等 / 空聚合 / LLM 失敗）
        """
        now = now or datetime.now(LOCAL_TZ)
        local = now.astimezone(LOCAL_TZ)
        today = local.date()
        trace_ref = f"{TRACE_REF_PREFIX}memorial:{today.isoformat()}"
        if self._trace_exists(trace_ref):
            logger.debug(
                f"[PeriodicNarrative] 紀念日冪等跳過: {trace_ref} 已反芻 "
                f"agent={self.agent_id}"
            )
            return None
        # 觸發依據: 既有 calendar_event 白名單產物（perception_trace, 0 新 world 源）
        event_summary = self._today_calendar_event(local)
        if event_summary is None:
            return None
        entries = self._aggregate_same_day_years(today)
        if not entries:
            logger.debug(
                f"[PeriodicNarrative] 紀念日往年聚合空 (fail-closed 不沉澱) "
                f"agent={self.agent_id} date={today.isoformat()}"
            )
            return None
        years = sorted({e["year"] for e in entries})
        period_start = f"{years[0]}-{today.month:02d}-{today.day:02d}"
        period_end = f"{years[-1]}-{today.month:02d}-{today.day:02d}"
        prompt = self._memorial_prompt(today, event_summary, entries)
        return await self._sublimate(
            trace_ref=trace_ref,
            extras={
                "period": "memorial",
                "period_start": period_start,
                "period_end": period_end,
                "event_summary": event_summary,
            },
            prompt=prompt,
            now=now,
        )

    # ── 週記聚合（read-side, 全聚合不挑選; 身分防火牆）──────

    def _aggregate_week(self, start_dt: datetime, end_local: datetime) -> str:
        """聚合窗內（本週週一 00:00 → 觸發時刻）自己的全部素材:
        ① diary 檔（路徑綁定 agent, 天然自己的經歷）;
        ② trace 中 actor_id==self 的事件（diary 已含的 event 去重; goal:/periodic:
           引用跳過, 避免递归）;
        ③ goals 表本窗內進入終態的 goal title（承諾閉環回顧素材）。
        結構規則 0 打分; 素材越界一律被身分防火牆擋在窗外。"""
        parts: List[str] = []
        # ① 自己的 diary: data/soul/{self}/diary/YYYY-MM-DD.jsonl（契約 §5.2）
        diary_lines: List[str] = []
        diary_event_ids: set = set()
        diary_dir = data_root_path() / "soul" / self.agent_id / "diary"
        if diary_dir.is_dir():
            for f in sorted(diary_dir.glob("*.jsonl")):
                try:
                    fdate = datetime.strptime(f.stem, "%Y-%m-%d").date()
                except ValueError:
                    continue  # 非日期檔名跳過（壞檔容忍）
                if not (start_dt.date() <= fdate <= end_local.date()):
                    continue
                for entry in _read_jsonl(f):
                    content = str(entry.get("content") or "").strip()
                    if not content:
                        continue
                    event_id = str(entry.get("inner_life_event_id") or "").strip()
                    if event_id:
                        diary_event_ids.add(event_id)
                    slot = str(entry.get("slot") or "?")
                    diary_lines.append(f"- {f.stem} ({slot}): {content}")
        if diary_lines:
            parts.append("【本周日记】\n" + "\n".join(diary_lines))
        # ② 自己的 trace: provenance.actor_id == self（diary 對應 event 去重）
        trace_lines: List[str] = []
        try:
            from src.inner_life.trace_reader import NarrativeTraceReader
            records = NarrativeTraceReader()._read_all()  # 全量只讀, 自身容錯
        except Exception as e:
            logger.warning(
                f"[PeriodicNarrative] trace 讀取失敗 (fail-closed 跳過該源): {e}"
            )
            records = []
        end_utc = end_local.astimezone(timezone.utc)
        for rec in records:
            prov = rec.get("provenance") or {}
            if prov.get("actor_id") != self.agent_id:
                continue  # 身分防火牆: 他者 0 內化
            event_id = str(rec.get("event_id") or "")
            if event_id in diary_event_ids:
                continue  # diary 已含, 不雙計
            trace_ref = str(prov.get("trace_ref") or "")
            if trace_ref.startswith(_SKIPPED_REF_PREFIXES):
                continue  # 已回顧/已沉澱引用（goal:/periodic:）
            ts = str(rec.get("ts") or "")
            try:
                tdt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue  # 壞 ts 容忍
            if not (start_dt <= tdt <= end_utc):
                continue
            extras = prov.get("extras") or {}
            extra_bits = ", ".join(
                f"{k}={str(v)[:160]}" for k, v in extras.items() if v is not None
            )
            trigger = str(prov.get("trigger_type") or "?")
            if extra_bits:
                trace_lines.append(f"- {ts} ({trigger}): {extra_bits}")
            else:
                trace_lines.append(f"- {ts} ({trigger})")
        if trace_lines:
            parts.append("【本周经历】\n" + "\n".join(trace_lines))
        # ③ 本週 goal 終態（state_updated_at ∈ 窗; 承諾閉環回顧素材, 0 新狀態）
        goal_lines: List[str] = []
        try:
            from src.memory.sage.graph_store import GraphStore
            store = GraphStore(db_path=_goal_db_path(self.agent_id))
            try:
                lo, hi = start_dt.timestamp(), end_local.timestamp()
                for g in store.get_goals(self.agent_id):
                    if g.state not in GOAL_TERMINAL_STATES:
                        continue
                    if not (lo < g.state_updated_at <= hi):
                        continue
                    goal_lines.append(
                        f"- {g.state}: {g.title} (推进 {g.advance_count} 次)"
                    )
            finally:
                store.close()
        except Exception as e:
            logger.warning(
                f"[PeriodicNarrative] goals 聚合失敗 (fail-closed 跳過該源): {e}"
            )
        if goal_lines:
            parts.append("【本周收束的承诺】\n" + "\n".join(goal_lines))
        return "\n\n".join(parts)

    # ── 紀念日聚合（往年今日, 身分防火牆同 §5.5）───────────

    def _today_calendar_event(self, local: datetime) -> Optional[str]:
        """今晚是否存在「事件日 == 今天（本地）」的 accepted calendar_event。

        只讀既有 perception_trace（契約 §5.1: 白名單 WORLD_QUALIFYING_TYPES
        產物）; 事件日期以感知時刻（timestamp, 既有 B2 同口徑）轉本地日期判定;
        多條時取最近一條（append 序逆序, 結構規則 0 打分）。"""
        path = data_root_path() / "world" / "perception_trace.jsonl"
        for rec in reversed(_read_jsonl(path)):
            if rec.get("event_type") != "calendar_event":
                continue
            if rec.get("accepted") is not True:
                continue
            raw_ts = str(rec.get("timestamp") or "")
            try:
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                continue  # 壞 ts 容忍
            if dt.astimezone(LOCAL_TZ).date() != local.date():
                continue
            # 事件標題（上下文物引用, 非經歷素材）: 既有 trace 無 summary 字段時
            # 用 novelty_id 引用（forward-compatible: 未來帶 summary 自然涵蓋）
            summary = str(rec.get("summary") or "").strip()
            if summary:
                return summary
            novelty_id = str(rec.get("novelty_id") or "").strip()
            if novelty_id:
                return f"calendar_event (novelty_id={novelty_id}, perceived_at={raw_ts})"
            return "calendar_event"
        return None

    def _aggregate_same_day_years(self, today: Any) -> List[Dict[str, Any]]:
        """聚合往年今日（同 M-D 不同年）自己 diary 條目（按年份升序）。"""
        entries: List[Dict[str, Any]] = []
        diary_dir = data_root_path() / "soul" / self.agent_id / "diary"
        if not diary_dir.is_dir():
            return entries
        for f in sorted(diary_dir.glob("*.jsonl")):
            try:
                fdate = datetime.strptime(f.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if (
                fdate.month != today.month
                or fdate.day != today.day
                or fdate.year >= today.year
            ):
                continue
            for entry in _read_jsonl(f):
                content = str(entry.get("content") or "").strip()
                if not content:
                    continue
                slot = str(entry.get("slot") or "?")
                entries.append({
                    "year": fdate.year,
                    "date": f.stem,
                    "slot": slot,
                    "content": content,
                })
        return entries

    # ── 語義化（方案 B 既有 proxy 通道）+ 一次沉澱 ─────────

    async def _sublimate(
        self,
        trace_ref: str,
        extras: Dict[str, str],
        prompt: str,
        now: datetime,
    ) -> Optional[str]:
        """語義化素材 → 敘事; 走 InnerLifeWriter.create_event 一次沉澱。

        fail-closed: LLM 失敗 / 壞輸出 / 空輸出 / 任何異常 → None（0 半成品）。
        """
        try:
            from src.inner_life.event import Provenance, TRIGGER_TYPE_SYSTEM
            from src.inner_life.trace import NarrativeTraceWriter
            from src.inner_life.writer import InnerLifeWriter

            llm_call = self._llm_call
            if llm_call is None:
                from src.soul.motive import _default_llm_call
                llm_call = _default_llm_call
            raw = await llm_call(
                [{"role": "user", "content": prompt}],
                agent_id=self.agent_id,
                max_tokens=300,
                temperature=0.8,
            )
            if not raw:
                logger.warning(
                    f"[PeriodicNarrative] 語義化空輸出 (fail-closed 不沉澱): "
                    f"trace_ref={trace_ref} agent={self.agent_id}"
                )
                return None
            data = _extract_json_dict(raw)
            if data is None:
                logger.warning(
                    f"[PeriodicNarrative] 語義化壞輸出 (fail-closed 不沉澱): "
                    f"trace_ref={trace_ref} agent={self.agent_id}"
                )
                return None
            title = str(data.get("title") or "").strip()
            narrative = str(data.get("narrative") or "").strip()
            if not title or not narrative:
                logger.warning(
                    f"[PeriodicNarrative] 語義化缺 title/narrative "
                    f"(fail-closed 不沉澱): trace_ref={trace_ref}"
                )
                return None
            extras_full = dict(extras)
            extras_full["title"] = title
            extras_full["narrative"] = narrative
            writer = InnerLifeWriter(trace_writer=NarrativeTraceWriter())
            event = writer.create_event(
                provenance=Provenance(
                    trigger_type=TRIGGER_TYPE_SYSTEM,
                    actor_id=self.agent_id,
                    source_system="system",
                    trace_ref=trace_ref,
                    extras=extras_full,
                ),
                # TG-3.1 契約: ts 必須為 UTC ISO-8601（+00:00/Z）
                ts=now.astimezone(timezone.utc).isoformat(),
            )
            logger.info(
                f"[PeriodicNarrative] 沉澱 {extras.get('period')}: "
                f"trace_ref={trace_ref} event={event.event_id} agent={self.agent_id}"
            )
            return event.event_id
        except Exception as e:
            logger.warning(
                f"[PeriodicNarrative] 沉澱失敗 (fail-closed): "
                f"{type(e).__name__}: {e} trace_ref={trace_ref}"
            )
            return None

    # ── 冪等判重（trace 只讀, 0 新存儲）────────────────────

    def _trace_exists(self, trace_ref: str) -> bool:
        """trace.jsonl 已有同 trace_ref 沉澱 → True（冪等鍵判重）。"""
        try:
            from src.inner_life.trace_reader import NarrativeTraceReader
            for rec in NarrativeTraceReader()._read_all():
                prov = rec.get("provenance") or {}
                if prov.get("trace_ref") == trace_ref:
                    return True
        except Exception as e:
            logger.warning(
                f"[PeriodicNarrative] trace 判重讀取失敗 (fail-open 視為未沉澱): {e}"
            )
        return False

    # ── prompt 組裝（只餵自己的素材; 0 編造素材外事實）──────

    def _weekly_prompt(self, period_key: str, material: str) -> str:
        return (
            f"你是 {self.agent_id}——一个有自己的记忆与感受的灵魂。\n"
            f"下面是你这一周（{period_key}）自己真实生活的原始记录。\n"
            f"请把这一周重构成一段「共同生活故事」的周记：以你自己的第一人称，"
            f"回顾这一周你经历和在意的事。\n"
            f"要求：贴近素材、具体、像你真的会有的回顾；不要编造素材里没有的事实细节；"
            f"不要用「系统」「目标」「任务」等口吻；不要解释你在做什么。\n"
            f"只输出 JSON：{{\"title\": \"一句话周记标题\", "
            f"\"narrative\": \"稍长的周记叙事\"}}\n"
            f"素材：\n{material}\n"
        )

    def _memorial_prompt(
        self,
        today: Any,
        event_summary: str,
        entries: List[Dict[str, Any]],
    ) -> str:
        lines = "\n".join(
            f"- {e['date']} ({e['slot']}): {e['content']}" for e in entries
        )
        return (
            f"你是 {self.agent_id}——一个有自己的记忆与感受的灵魂。\n"
            f"今天是 {today.month} 月 {today.day} 日，日历上有一个特别的日子："
            f"{event_summary}。\n"
            f"下面是往年今日你自己真实写下的日记。请把它们重构成一段"
            f"「纪念日回望」：以你自己的第一人称，把今天为何特别接回你自己活过的证据。\n"
            f"要求：贴近素材、具体、像你真的会有的回想；不要编造素材里没有的事实细节；"
            f"不要用「系统」「目标」「任务」等口吻；不要解释你在做什么。\n"
            f"只输出 JSON：{{\"title\": \"一句话纪念日标题\", "
            f"\"narrative\": \"稍长的纪念日回望\"}}\n"
            f"素材：\n{lines}\n"
        )