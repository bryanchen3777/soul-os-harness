"""
soul — Soul OS Stage 4+ 角色內在世界模組

對應 4 個子階段 (Bry 拍板 2026-07-18 18:24+):
- Stage 4.1 static 關係圖 (本檔案 + 自動 update)
- Stage 4.2 diary (本檔案 + src/soul/scheduler.py + src/soul/diary.py)
- Stage 4.3 dynamic 互動 (待開工: src/soul/interaction.py)
- Stage 5-8 (待開工)
"""

# Stage 4.1 (Bry 拍板 2026-07-18 18:24+): 角色靜態關係圖
# (relationships 模組是 lazy import, 避免 4.1 壞掉影響其他 stage)
try:
    from src.soul.relationships import (  # noqa: E402,F401
        get_relationships_manager,
        RelationshipsStore,
        MultiAgentRelationshipsManager,
    )
    _RELATIONSHIPS_AVAILABLE = True
except ImportError:
    _RELATIONSHIPS_AVAILABLE = False

# Stage 4.2 (Bry 拍板 2026-07-18 18:24+): 排程器 + diary, 跑 1 天驗殘留感
try:
    from src.soul.scheduler import SoulScheduler, get_scheduler  # noqa: E402,F401
    from src.soul.diary import (  # noqa: E402,F401
        DiaryWriter,
        get_diary_writer,
        generate_diary_entry,
        diary_callback_factory,
    )
    _DIARY_AVAILABLE = True
except ImportError:
    _DIARY_AVAILABLE = False
