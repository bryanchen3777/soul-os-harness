"""
SAGE-lite vendored memory engine
Phase 2.0 從 hermes-sage-memory v0.1.3 vendor 進來
去掉了 Hermes MemoryProvider ABC 相依（見 provider.py）

對外公開的 API：
- SAGELiteProvider：給 MemoryMiddleware 用的高階介面
- GraphStore / MemoryWriter / MemoryReader / MemoryEvolution：低階元件
- Fact / ContextResult：資料模型
- TokenBudget / SummaryCompressor：token 控制工具
"""
from .writer import MemoryWriter, WriteResult
from .reader import MemoryReader
from .evolution import MemoryEvolution
from .graph_store import GraphStore
from .provider import SAGELiteProvider
from .models import Fact, ContextResult
from .token_utils import TokenBudget, SummaryCompressor, PrefetchCache

__all__ = [
    "SAGELiteProvider",
    "GraphStore",
    "MemoryWriter",
    "MemoryReader",
    "MemoryEvolution",
    "Fact",
    "ContextResult",
    "WriteResult",
    "TokenBudget",
    "SummaryCompressor",
    "PrefetchCache",
]
