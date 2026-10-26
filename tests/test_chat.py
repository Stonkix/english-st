import pytest

from app import chat
from app.chat import ChatError, RateLimiter
from app.models import ChatMessage, ChatRoom
from tests.factories import make_group, make_user, seed_filter


@pytest.fixture(autouse=True)
def _no_rate_limit():
    chat.limiter.reset()
    yield
    chat.limiter.reset()


def _school(db):
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t, name="5А")
    s1 = make_user(db, login="s1", group=g, first_name="Петя", last_name="Котов")
    s2 = make_user(db, login="s2", group=g, first_name="Аня", last_name="Белова")
    return t, g, s1, s2


def test_student_rooms_created_once(db):
    t, g, s1, _ = _school(db)
    rooms = chat.rooms_for(db, s1)
    assert [r.kind for r in rooms] == ["group", "direct"]
    assert rooms[0].group_id == g.id and rooms[1].student_id == s1.id
    assert [r.id for r in chat.rooms_for(db, s1)] == [r.id for r in rooms]
    assert db.query(ChatRoom).count() == 2


def test_student_without_group_has_no_rooms(db):
    assert chat.rooms_for(db, make_user(db, login="lonely")) == []


def test_teacher_sees_group_and_each_student(db):
    t, g, s1, s2 = _school(db)
    make_user(db, login="off", group=g, is_active=False)
    other = make_user(db, role="teacher", login="other")
    make_group(db, other, name="Чужая")
    rooms = chat.rooms_for(db, t)
    assert [(r.kind, r.group_id or r.student_id) for r in rooms] == [
        ("group", g.id), ("direct", s2.id), ("direct", s1.id),  # ученики по фамилии
    ]
    admin = make_user(db, role="admin", login="adm")
    assert len(chat.rooms_for(db, admin)) == 4


def test_access_rules(db):
    t, g, s1, s2 = _school(db)
    other_t = make_user(db, role="teacher", login="other")
    g2 = make_group(db, other_t, name="6Б")
    stranger = make_user(db, login="s3", group=g2)
    group_room, d1 = chat.group_room(db, g.id), chat.direct_room(db, s1.id)
    assert chat.can_access(db, s1, group_room) and chat.can_access(db, s2, group_room)
    assert chat.can_access(db, s1, d1) and not chat.can_access(db, s2, d1)
    assert not chat.can_access(db, stranger, group_room) and not chat.can_access(db, stranger, d1)
    assert chat.can_access(db, t, d1) and not chat.can_access(db, other_t, d1)
    assert chat.can_moderate(db, t, group_room) and not chat.can_moderate(db, s1, group_room)
    # ученика перевели в другую группу: старый чат группы закрыт, личный — с новым учителем
    s1.group_id = g2.id
    db.commit()
    assert not chat.can_access(db, s1, group_room)
    assert chat.can_access(db, other_t, d1) and not chat.can_access(db, t, d1)


def test_participants(db):
    t, g, s1, s2 = _school(db)
    make_user(db, login="off", group=g, is_active=False)
    admin = make_user(db, role="admin", login="adm")
    assert chat.participants(db, chat.group_room(db, g.id)) == {s1.id: False, s2.id: False, t.id: True, admin.id: True}
    assert chat.participants(db, chat.direct_room(db, s1.id)) == {s1.id: False, t.id: True, admin.id: True}


def test_post_text_filters_and_hides_original_from_students(db):
    seed_filter(db)
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    m = chat.post_text(db, s1, room, "  ну ты  сука \r\n\n\n\nпривет ")
    assert m.was_filtered and "сука" not in m.text_shown and m.text_original == "ну ты  сука \n\nпривет"
    assert "original" not in chat.serialize(m, moderator=False)
    assert chat.serialize(m, moderator=True)["original"] == m.text_original
    clean = chat.post_text(db, t, room, "Hello!")
    assert not clean.was_filtered and chat.serialize(clean, False)["text"] == "Hello!"


def test_post_text_validation(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    with pytest.raises(ChatError):
        chat.post_text(db, s1, room, " \n\x00 ")
    with pytest.raises(ChatError):
        chat.post_text(db, s1, room, "a" * 1001)
    assert chat.post_text(db, s1, room, "a" * 1000).text_shown == "a" * 1000


def test_sticker(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    with pytest.raises(ChatError):
        chat.post_sticker(db, s1, room, "../../etc/passwd")
    data = chat.serialize(chat.post_sticker(db, s1, room, "heart"), False)
    assert data["sticker"] == "/static/stickers/heart.svg" and data["kind"] == "sticker"


def test_rate_limiter():
    lim = RateLimiter()
    assert lim.hit(1, now=0.0) and not lim.hit(1, now=0.5) and lim.hit(2, now=0.5)
    assert all(lim.hit(1, now=1.0 + i) for i in range(19))  # 20 за минуту
    assert not lim.hit(1, now=30.0)
    assert lim.hit(1, now=60.5)  # первое вышло из окна


def test_post_rate_limited(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    chat.post_text(db, s1, room, "раз")
    with pytest.raises(ChatError, match="Слишком много"):
        chat.post_text(db, s1, room, "два")
    assert db.query(ChatMessage).count() == 1


def test_delete_rules(db):
    t, g, s1, s2 = _school(db)
    room = chat.group_room(db, g.id)
    m = chat.post_text(db, s1, room, "моё")
    assert not chat.delete_message(db, s2, m, moderator=False)
    assert chat.delete_message(db, s1, m, moderator=False)
    assert not chat.delete_message(db, s1, m, moderator=False)  # уже удалено
    data = chat.serialize(m, moderator=True)
    assert data["deleted"] and "text" not in data and "original" not in data
    chat.limiter.reset()
    m2 = chat.post_text(db, s2, room, "чужое")
    assert chat.delete_message(db, t, m2, moderator=True) and m2.deleted_by == t.id


def test_history_pages(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    for i in range(60):
        db.add(ChatMessage(room_id=room.id, author_id=s1.id, kind="text", text_original=str(i), text_shown=str(i)))
    db.commit()
    ids = [m.id for m in db.query(ChatMessage).order_by(ChatMessage.id)]
    last = chat.history(db, room)
    assert [m.id for m in last] == ids[10:]
    assert [m.id for m in chat.history(db, room, before=ids[10])] == ids[:10]
    assert [m.id for m in chat.history(db, room, after=ids[57])] == ids[58:]


def test_unread_and_mark_read(db):
    t, g, s1, s2 = _school(db)
    room, direct = chat.group_room(db, g.id), chat.direct_room(db, s1.id)
    m1 = chat.post_text(db, s1, room, "раз")
    chat.limiter.reset()
    chat.post_text(db, s1, direct, "учителю")
    chat.limiter.reset()
    m3 = chat.post_text(db, s2, room, "два")
    assert chat.unread_counts(db, s1, [room.id, direct.id]) == {room.id: 1}  # своё прочитано
    assert chat.unread_total(db, t) == 3 and chat.unread_total(db, s2) == 0  # написал после — значит, видел
    chat.mark_read(db, t.id, room.id, m3.id)
    chat.mark_read(db, t.id, room.id, m1.id)  # назад не откатывается
    assert chat.unread_counts(db, t, [room.id, direct.id]) == {direct.id: 1}
    chat.delete_message(db, s2, m3, moderator=False)
    assert chat.unread_total(db, s1) == 0  # удалённое не считается


def test_preview(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    assert chat.preview(None, s1) == "Сообщений пока нет"
    m = chat.post_sticker(db, s1, room, "cat")
    assert chat.preview(m, s1) == "Вы: Стикер" and chat.preview(m, t) == "Стикер"
    assert chat.last_messages(db, [room.id])[room.id].id == m.id
