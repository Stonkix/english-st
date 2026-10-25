from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow

LEVELS = ("A1", "A1+", "A2", "A2+", "B1", "B1+", "B2", "B2+", "C1", "C1+", "C2", "C2+")
QUESTION_TYPES = ("choice_text", "choice_image", "text_input", "word_bank", "match_pairs")


class Pool(Base):
    """Пул вопросов: из него собирается ежедневное задание группы."""

    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str]
    description: Mapped[str]
    level: Mapped[str]  # одно из LEVELS
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # только действующие вопросы; удалённые с ответами скрываются (is_active=False)
    questions: Mapped[list["Question"]] = relationship(
        primaryjoin="and_(Pool.id == Question.pool_id, Question.is_active.is_(True))",
        order_by="Question.id",
        viewonly=True,
    )

    def __str__(self) -> str:
        return self.title


class GroupPool(Base):
    """Какие пулы подключены к группе."""

    __tablename__ = "group_pools"

    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id", ondelete="CASCADE"), primary_key=True)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    pool_id: Mapped[int | None] = mapped_column(ForeignKey("pools.id", ondelete="SET NULL"))
    type: Mapped[str]  # одно из QUESTION_TYPES
    prompt: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # варианты и правильные ответы, зависят от типа
    image: Mapped[str | None]  # путь относительно media_dir
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Test(Base):
    __tablename__ = "tests"
    __test__ = False  # не тест для pytest

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str]
    description: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    questions: Mapped[list[Question]] = relationship(
        secondary="test_questions",
        order_by="TestQuestion.position",
        viewonly=True,
    )

    def __str__(self) -> str:
        return self.title


class TestQuestion(Base):
    __tablename__ = "test_questions"
    __test__ = False

    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    position: Mapped[int]


class Assignment(Base):
    """Тест, назначенный на день группе или одному ученику."""

    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL) <> (student_id IS NOT NULL)",
            name="ck_assignment_one_target",
        ),
        UniqueConstraint("group_id", "date", name="uq_assignment_group_date"),
        UniqueConstraint("student_id", "date", name="uq_assignment_student_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)


class DailySettings(Base):
    __tablename__ = "daily_settings"

    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    question_count: Mapped[int] = mapped_column(default=5)
    max_attempts: Mapped[int] = mapped_column(default=3)
    shuffle_questions: Mapped[bool] = mapped_column(default=True)
    shuffle_options: Mapped[bool] = mapped_column(default=True)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)  # дата по Москве
    source: Mapped[str]  # "test" | "pool"
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"))
    question_ids: Mapped[list] = mapped_column(JSON)  # id вопросов в порядке показа
    pair_mistakes: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    correct_count: Mapped[int] = mapped_column(default=0)
    total_count: Mapped[int]


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    answer: Mapped[dict | list | str | None] = mapped_column(JSON)
    is_correct: Mapped[bool]
    had_typo: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
