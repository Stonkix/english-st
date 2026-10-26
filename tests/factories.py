from itertools import count

from app.models import Group, User

_login_counter = count(1)


def make_user(
    db,
    role="student",
    login=None,
    password="Passw0rd12",
    group=None,
    must_change=False,
    is_active=True,
    first_name="Иван",
    last_name="Петров",
) -> User:
    from app.auth.passwords import hash_password  # появляется в Task 4, поэтому импорт ленивый

    user = User(
        login=login or f"user{next(_login_counter)}",
        password_hash=hash_password(password),
        role=role,
        first_name=first_name,
        last_name=last_name,
        group_id=group.id if group else None,
        must_change_password=must_change,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_group(db, teacher, name="Группа 1") -> Group:
    group = Group(name=name, teacher_id=teacher.id)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def login_as(client, login, password="Passw0rd12"):
    return client.post("/login", data={"login": login, "password": password}, follow_redirects=False)


def seed_filter(db) -> None:
    """Начальный словарь фильтра мата (миграция кладёт его в БД, но тесты чистят таблицы)."""
    from app.models import BadWord, BadWordException, ReplacementWord
    from app.profanity import is_cyrillic
    from app.profanity_defaults import BAD_WORDS, EXCEPTIONS, REPLACEMENTS

    for model in (BadWord, BadWordException, ReplacementWord):  # первый тест сессии видит словарь из миграции
        db.query(model).delete()
    db.add_all([BadWord(pattern=w, lang="ru" if is_cyrillic(w) else "en") for w in BAD_WORDS])
    db.add_all([BadWordException(word=w) for w in EXCEPTIONS])
    db.add_all([ReplacementWord(word=w) for w in REPLACEMENTS])
    db.commit()
