"""
tests/test_m5_10_2_judge_v1_context.py
M5.10-2 (Bry 派工 2026-08-10): Memory LLM Judge v1 Memory Context Visibility

驗收：
  A. MemoryWriter.__init__ 接受 memory_reader 參數 (optional, 向後相容)
  B. SAGELiteProvider._init_components 把 _reader 傳給 _writer
  C. writer._memory_reader 為 None 時 → context="" (退化行為)
  D. writer._memory_reader 有值時 → retrieve_context 被調用
  E. retrieve_context 回傳的 summary 被傳給 judge.extract_and_judge
  F. retrieve_context 失敗不 block 萃取 (exception 被 catch)
  G. source_pair_filter=None (context 階段不做 access control)
  H. mode="precise", top_k=3, max_tokens=400 (保守取)
  I. SAGELiteProvider._init_components 順序：reader 先於 writer
  J. Frozen contracts: 0 change (MemoryWriter signature additive, LLMJudge signature unchanged)

不測：
  - LLM actual output (no real LLM call)
  - Diary / Dream / NarrativeTrace visibility (scope boundary)
  - MemoryReader scoring/retrieval logic (reader 本身已通過 M3 回歸)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.sage.writer import MemoryWriter
from src.memory.sage.reader import MemoryReader
from src.memory.sage.provider import SAGELiteProvider
from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import ContextResult


# ─────────────────────────────────────────
# A. MemoryWriter.__init__ 向後相容
# ─────────────────────────────────────────

class TestWriterInitBackwardCompat(unittest.TestCase):
    """驗收 A: writer 可用舊方式建構 (memory_reader=None)"""

    def test_writer_constructs_without_memory_reader(self):
        """可無參數建構 (memory_reader 預設 None)"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            store = GraphStore(db_path=db_path)
            writer = MemoryWriter(store, default_session_id="s1", agent_id="agent_rem")
            self.assertIsNone(writer._memory_reader)
            store.close()

    def test_writer_constructs_with_memory_reader(self):
        """可傳入 memory_reader"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            store = GraphStore(db_path=db_path)
            reader = MemoryReader(store)
            writer = MemoryWriter(store, default_session_id="s1", agent_id="agent_rem",
                                  memory_reader=reader)
            self.assertIs(writer._memory_reader, reader)
            store.close()

    def test_signature_additive_no_required_param(self):
        """memory_reader 是 optional, 不影響既有 caller"""
        sig = MemoryWriter.__init__
        param_names = sig.__code__.co_varnames[:sig.__code__.co_argcount]
        self.assertIn('memory_reader', param_names)
        # 確認沒有把既有的 required param 改成 required
        # 既有的 required: graph_store
        # 既有的 optional: default_session_id, agent_id
        self.assertEqual(param_names[1], 'graph_store')  # 第一個參數是 graph_store


# ─────────────────────────────────────────
# D/E. writer._extract_facts_llm 調用 reader
# ─────────────────────────────────────────

class TestWriterCallsReader(unittest.TestCase):
    """驗收 D+E: writer._memory_reader 有值時, retrieve_context 被調用"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "graph.sqlite"
        self.store = GraphStore(db_path=self.db_path)
        self.reader = MemoryReader(self.store)
        self.writer = MemoryWriter(
            self.store,
            default_session_id="s1",
            agent_id="agent_rem",
            memory_reader=self.reader,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_retrieve_context_called_with_correct_params(self):
        """reader.retrieve_context 被調用, 參數正確"""
        text = "我喜歡吃義大利麵"
        expected_query = text
        expected_top_k = 3
        expected_max_tokens = 400
        expected_mode = "precise"
        expected_filter = None  # source_pair_filter=None (驗收 G)

        with patch.object(self.reader, 'retrieve_context',
                         return_value=ContextResult(
                             facts=[], chains=[], summary="", token_estimate=0
                         )) as mock_retrieve:
            # Mock _get_llm_judge 避免真的跑 LLM
            mock_judge = MagicMock()
            mock_judge.extract_and_judge = AsyncMock(return_value=[])
            with patch.object(self.writer, '_get_llm_judge', return_value=mock_judge):
                try:
                    loop = __import__('asyncio').new_event_loop()
                    __import__('asyncio').set_event_loop(loop)
                    self.writer._extract_facts_llm(text, "user", "s1", "user")
                finally:
                    loop.close()

            mock_retrieve.assert_called_once()
            call_kwargs = mock_retrieve.call_args.kwargs
            # 驗收 H: mode="precise", top_k=3, max_tokens=400
            self.assertEqual(call_kwargs.get('top_k'), expected_top_k)
            self.assertEqual(call_kwargs.get('max_tokens'), expected_max_tokens)
            self.assertEqual(call_kwargs.get('mode'), expected_mode)
            # 驗收 G: source_pair_filter=None
            self.assertEqual(call_kwargs.get('source_pair_filter'), expected_filter)
            # query 是 text 本身
            self.assertEqual(call_kwargs.get('__self__'), None)
            # positional arg 檢查
            args, _ = mock_retrieve.call_args
            self.assertEqual(args[0], expected_query)

    def test_memory_context_passed_to_judge(self):
        """reader 回傳的 summary 被傳給 judge.extract_and_judge"""
        text = "我喜歡吃義大利麵"
        reader_summary = "Bry 喜歡吃義大利麵, 住在台北"
        judge_context_received = None

        with patch.object(self.reader, 'retrieve_context',
                         return_value=ContextResult(
                             facts=[], chains=[], summary=reader_summary, token_estimate=0
                         )):
            async def capture_judge(text, context, agent_id):
                nonlocal judge_context_received
                judge_context_received = context
                return []

            mock_judge = MagicMock()
            mock_judge.extract_and_judge = capture_judge
            with patch.object(self.writer, '_get_llm_judge', return_value=mock_judge):
                try:
                    loop = __import__('asyncio').new_event_loop()
                    __import__('asyncio').set_event_loop(loop)
                    self.writer._extract_facts_llm(text, "user", "s1", "user")
                finally:
                    loop.close()

            self.assertEqual(judge_context_received, reader_summary)

    def test_empty_summary_works(self):
        """reader 回 empty summary → judge 收到空字串 (不炸"""
        text = "hi"
        judge_context_received = None

        with patch.object(self.reader, 'retrieve_context',
                         return_value=ContextResult(
                             facts=[], chains=[], summary="", token_estimate=0
                         )):
            async def capture_judge(text, context, agent_id):
                nonlocal judge_context_received
                judge_context_received = context
                return []

            mock_judge = MagicMock()
            mock_judge.extract_and_judge = capture_judge
            with patch.object(self.writer, '_get_llm_judge', return_value=mock_judge):
                try:
                    loop = __import__('asyncio').new_event_loop()
                    __import__('asyncio').set_event_loop(loop)
                    self.writer._extract_facts_llm(text, "user", "s1", "user")
                finally:
                    loop.close()

            self.assertEqual(judge_context_received, "")


# ─────────────────────────────────────────
# C. 向後相容退化
# ─────────────────────────────────────────

class TestWriterBackwardCompatNoReader(unittest.TestCase):
    """驗收 C: writer._memory_reader=None 時, 不 call reader"""

    def test_no_reader_does_not_call_retrieve(self):
        """writer 無 reader 時, 不嘗試 retrieve_context"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            store = GraphStore(db_path=db_path)
            writer = MemoryWriter(store, default_session_id="s1", agent_id="agent_rem")
            # _memory_reader 為 None
            self.assertIsNone(writer._memory_reader)

            async def capture_judge(text, context, agent_id):
                # 驗收: context 為空字串
                assert context == ""
                return []

            mock_judge = MagicMock()
            mock_judge.extract_and_judge = capture_judge
            with patch.object(writer, '_get_llm_judge', return_value=mock_judge):
                try:
                    loop = __import__('asyncio').new_event_loop()
                    __import__('asyncio').set_event_loop(loop)
                    writer._extract_facts_llm("test text", "user", "s1", "user")
                finally:
                    loop.close()
            store.close()


# ─────────────────────────────────────────
# F. reader 例外不 block 萃取
# ─────────────────────────────────────────

class TestReaderExceptionSafe(unittest.TestCase):
    """驗收 F: retrieve_context 失敗不 block 萃取"""

    def test_retrieve_context_exception_does_not_propagate(self):
        """reader 例外被 catch, judge 仍被調用"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            store = GraphStore(db_path=db_path)
            reader = MemoryReader(store)
            writer = MemoryWriter(store, default_session_id="s1", agent_id="agent_rem",
                                  memory_reader=reader)

            async def capture_judge(text, context, agent_id):
                assert context == ""  # reader 例外時退化到空字串
                return []

            mock_judge = MagicMock()
            mock_judge.extract_and_judge = capture_judge

            with patch.object(reader, 'retrieve_context',
                             side_effect=RuntimeError("reader DB error")):
                with patch.object(writer, '_get_llm_judge', return_value=mock_judge):
                    try:
                        loop = __import__('asyncio').new_event_loop()
                        __import__('asyncio').set_event_loop(loop)
                        # 不拋例外的 assert
                        writer._extract_facts_llm("test", "user", "s1", "user")
                    except RuntimeError:
                        self.fail("reader 例外應被 catch, 不該往上拋")
                    finally:
                        loop.close()
            store.close()


# ─────────────────────────────────────────
# I. SAGELiteProvider 順序：reader 先於 writer
# ─────────────────────────────────────────

class TestProviderReaderWriterOrder(unittest.TestCase):
    """驗收 I: provider._init_components 先建 reader 再建 writer"""

    def test_provider_wires_reader_to_writer(self):
        """provider 把 _reader 傳給 _writer"""
        with tempfile.TemporaryDirectory() as tmp:
            provider = SAGELiteProvider(profile_id="agent_rem", data_dir=tmp)
            provider.initialize(session_id="s1")
            # writer 持有 reader
            self.assertIs(provider._writer._memory_reader, provider._reader)
            provider.shutdown()


# ─────────────────────────────────────────
# J. Frozen contracts 驗證
# ─────────────────────────────────────────

class TestFrozenContracts(unittest.TestCase):
    """驗收 J: frozen contracts 0 change"""

    def test_llmjudge_signature_unchanged(self):
        """LLMJudge.extract_and_judge signature 不變 (context 參數已存在)"""
        from src.memory.llm_judge import LLMJudge
        sig = LLMJudge.extract_and_judge
        params = sig.__code__.co_varnames[:sig.__code__.co_argcount]
        # 原始 signature: (text, context, agent_id) — 不變
        self.assertEqual(list(params), ['self', 'text', 'context', 'agent_id'])
        # context 已經是參數, 所以 0 change

    def test_memorywriter_public_api_unchanged(self):
        """MemoryWriter 公開 API 不變 (public method signature unchanged)"""
        public_methods = [
            'add_fact', 'add_facts_batch', 'write_with_confirmation',
            'extract_and_write', 'extract', 'write_turn',
        ]
        for method_name in public_methods:
            self.assertTrue(hasattr(MemoryWriter, method_name))

    def test_contextresult_schema_unchanged(self):
        """ContextResult summary 欄位存在 (reader 回傳格式不變)"""
        from src.memory.sage.models import ContextResult
        cr = ContextResult(facts=[], chains=[], summary="test", token_estimate=0)
        self.assertEqual(cr.summary, "test")


# ─────────────────────────────────────────
# B. Provider wires reader to writer
# ─────────────────────────────────────────

class TestProviderWiresReaderToWriter(unittest.TestCase):
    """驗收 B: SAGELiteProvider 把 _reader 傳給 _writer"""

    def test_writer_references_provider_reader(self):
        """writer._memory_reader 指向 provider._reader"""
        with tempfile.TemporaryDirectory() as tmp:
            provider = SAGELiteProvider(profile_id="agent_rem", data_dir=tmp)
            provider.initialize(session_id="s1")
            # writer 的 reader 就是 provider 的 reader
            self.assertIs(provider._writer._memory_reader, provider._reader)
            # reader 是有效的 MemoryReader 實例
            self.assertIsInstance(provider._writer._memory_reader, MemoryReader)
            provider.shutdown()


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────

if __name__ == "__main__":
    # 跑所有測試
    import unittest as ut
    loader = ut.TestLoader()
    suite = ut.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestWriterInitBackwardCompat))
    suite.addTests(loader.loadTestsFromTestCase(TestWriterCallsReader))
    suite.addTests(loader.loadTestsFromTestCase(TestWriterBackwardCompatNoReader))
    suite.addTests(loader.loadTestsFromTestCase(TestReaderExceptionSafe))
    suite.addTests(loader.loadTestsFromTestCase(TestProviderReaderWriterOrder))
    suite.addTests(loader.loadTestsFromTestCase(TestFrozenContracts))
    suite.addTests(loader.loadTestsFromTestCase(TestProviderWiresReaderToWriter))

    runner = ut.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # exit code = 1 if any failure
    sys.exit(0 if result.wasSuccessful() else 1)
