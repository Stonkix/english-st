# Импорт этого пакета регистрирует модели в Base.metadata
from app.models.accounts import AvatarHistory, Group, User
from app.models.chat import (
    BadWord,
    BadWordException,
    ChatMessage,
    ChatReadMark,
    ChatRoom,
    ReplacementWord,
)
from app.models.security import LoginAttempt, Session
from app.models.shop import Order, ShopItem
from app.models.stamps import ConversionRule, DailyReward, StampTransaction, StampType, StampVariant
from app.models.tasks import (
    LEVELS,
    QUESTION_TYPES,
    Assignment,
    Attempt,
    AttemptAnswer,
    DailySettings,
    GroupPool,
    Pool,
    Question,
    Test,
    TestQuestion,
)

__all__ = [
    "LEVELS",
    "QUESTION_TYPES",
    "Assignment",
    "Attempt",
    "AttemptAnswer",
    "AvatarHistory",
    "BadWord",
    "BadWordException",
    "ChatMessage",
    "ChatReadMark",
    "ChatRoom",
    "ConversionRule",
    "DailyReward",
    "DailySettings",
    "Group",
    "GroupPool",
    "LoginAttempt",
    "Order",
    "Pool",
    "Question",
    "ReplacementWord",
    "Session",
    "ShopItem",
    "StampTransaction",
    "StampType",
    "StampVariant",
    "Test",
    "TestQuestion",
    "User",
]
