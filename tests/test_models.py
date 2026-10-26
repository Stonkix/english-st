import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Group, User


def test_login_unique(db):
    db.add(User(login="ivan", password_hash="x", role="student", first_name="И", last_name="П"))
    db.commit()
    db.add(User(login="ivan", password_hash="x", role="student", first_name="И", last_name="П"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_group_students(db):
    t = User(login="t1", password_hash="x", role="teacher", first_name="Анна", last_name="С")
    db.add(t)
    db.flush()
    g = Group(name="5А", teacher_id=t.id)
    db.add(g)
    db.flush()
    db.add(User(login="s1", password_hash="x", role="student", first_name="Петя", last_name="К", group_id=g.id))
    db.commit()
    db.refresh(g)
    assert [s.login for s in g.students] == ["s1"] and g.teacher.login == "t1"


def test_migration_matches_models():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db import Base, engine

    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
