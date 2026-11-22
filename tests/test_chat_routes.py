import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app import chat
from app.config import settings
from app.main import app
from app.models import BadWord, ChatMessage, ChatReaction, ChatReadMark, ReplacementWord
from tests.factories import login_as, make_group, make_user, seed_filter


@pytest.fixture(autouse=True)
def _no_rate_limit():
    chat.limiter.reset()
    yield
    chat.limiter.reset()


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "PNG")
    return buf.getvalue()


def _school(db):
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t, name="5А")
    s1 = make_user(db, login="stud1", group=g, first_name="Петя", last_name="Котов")
    s2 = make_user(db, login="stud2", group=g, first_name="Аня", last_name="Белова")
    return t, g, s1, s2


def _client(login):
    c = TestClient(app, headers={"Origin": "http://testserver"})
    c.__enter__()
    login_as(c, login)
    return c


def _data(html: str) -> dict:
    start = html.index('id="chat-data">') + len('id="chat-data">')
    return json.loads(html[start:html.index("</script>", start)])


# ---------- страницы ----------


def test_rooms_page_and_nav_badge(client, db):
    t, g, s1, s2 = _school(db)
    room = chat.group_room(db, g.id)
    chat.post_text(db, s2, room, "привет")
    login_as(client, "stud1")
    html = client.get("/chat").text
    assert "Group “5А”" in html and t.full_name in html and "привет" in html
    assert 'class="chat-unread"' in html and "tabbar-badge" in html
    client.get(f"/chat/{room.id}")  # открыл — прочитал
    assert "tabbar-badge" not in client.get("/chat").text


def test_student_without_group(client, db):
    make_user(db, login="lonely")
    login_as(client, "lonely")
    assert "Chat will be available" in client.get("/chat").text


def test_room_page_data_and_access(client, db):
    t, g, s1, s2 = _school(db)
    room, direct2 = chat.group_room(db, g.id), chat.direct_room(db, s2.id)
    chat.post_sticker(db, s2, room, "cat")
    login_as(client, "stud1")
    r = client.get(f"/chat/{room.id}")
    data = _data(r.text)
    assert data["room_id"] == room.id and data["me"] == s1.id and not data["moderator"]
    assert [m["kind"] for m in data["messages"]] == ["sticker"] and len(data["stickers"]) == len(chat.STICKERS)
    assert '<body class="has-nav chat-room-page">' in r.text
    assert 'class="tabbar"' in r.text and 'href="/chat" aria-current="page"' in r.text
    assert '<dialog class="confirm-dialog" id="action-confirm"' in r.text
    assert client.get(f"/chat/{direct2.id}").status_code == 404  # чужой личный чат
    assert client.get(f"/chat/{direct2.id}/messages").json() == {"error": "Something went wrong"}
    assert client.get("/chat/999999").status_code == 404


def test_history_json(client, db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    for i in range(55):
        db.add(ChatMessage(room_id=room.id, author_id=s1.id, kind="text", text_original=str(i), text_shown=str(i)))
    db.commit()
    login_as(client, "teach")
    data = _data(client.get(f"/chat/{room.id}").text)
    assert data["moderator"] and data["has_more"] and len(data["messages"]) == 50
    older = client.get(f"/chat/{room.id}/messages", params={"before": data["messages"][0]["id"]}).json()
    assert [m["text"] for m in older["messages"]] == ["0", "1", "2", "3", "4"] and not older["has_more"]
    newer = client.get(f"/chat/{room.id}/messages", params={"after": data["messages"][-2]["id"]}).json()
    assert [m["text"] for m in newer["messages"]] == ["54"]
    assert client.get(f"/chat/{room.id}/messages", params={"before": "x"}).status_code == 422


def test_reactions_persist_and_broadcast_personalized_counts(client, db):
    teacher, group, student, peer = _school(db)
    room = chat.group_room(db, group.id)
    message = chat.post_text(db, teacher, room, "Welcome")
    url = f"/chat/messages/{message.id}/reaction"
    reaction_list = f"/chat/{room.id}/reactions?first={message.id}&last={message.id}"
    login_as(client, student.login)

    with TestClient(app, headers={"Origin": "http://testserver"}) as other:
        login_as(other, peer.login)
        with client.websocket_connect("/ws/chat") as own_ws, other.websocket_connect("/ws/chat") as peer_ws:
            def check_events(counts, mine, theirs):
                event = {"type": "reaction", "room_id": room.id, "id": message.id}
                assert own_ws.receive_json() == {**event, "state": {"counts": counts, "mine": mine}}
                assert peer_ws.receive_json() == {**event, "state": {"counts": counts, "mine": theirs}}

            for _ in range(2):
                assert client.put(url, json={"kind": "heart"}).json() == {"counts": {"heart": 1}, "mine": "heart"}
            check_events({"heart": 1}, "heart", None)
            assert other.put(url, json={"kind": "like"}).json() == {"counts": {"heart": 1, "like": 1}, "mine": "like"}
            check_events({"heart": 1, "like": 1}, "heart", "like")

            page = _data(client.get(f"/chat/{room.id}").text)
            assert {item["id"] for item in page["reactions"]} == {kind.value for kind in chat.ReactionKind}
            assert page["messages"][0]["reaction"] == {"counts": {"heart": 1, "like": 1}, "mine": "heart"}
            history = other.get(f"/chat/{room.id}/messages").json()["messages"]
            assert history[0]["reaction"] == {"counts": {"heart": 1, "like": 1}, "mine": "like"}

            assert client.put(url, json={"kind": "like"}).json() == {"counts": {"like": 2}, "mine": "like"}
            check_events({"like": 2}, "like", "like")
            assert client.put(url, json={"kind": None}).json() == {"counts": {"like": 1}, "mine": None}
            check_events({"like": 1}, None, "like")
            assert client.get(reaction_list).json() == {str(message.id): {"counts": {"like": 1}, "mine": None}}
            assert other.put(url, json={"kind": None}).json() == {"counts": {}, "mine": None}
            check_events({}, None, None)
            assert client.get(reaction_list).json() == {}


def test_reactions_require_room_access_and_disappear_on_delete(client, db):
    _, _, student, peer = _school(db)
    room = chat.direct_room(db, peer.id)
    message = chat.post_sticker(db, peer, room, "cat")
    url = f"/chat/messages/{message.id}/reaction"
    reaction_list = f"/chat/{room.id}/reactions?first={message.id}&last={message.id}"
    login_as(client, student.login)
    assert client.put(url, json={"kind": "heart"}).status_code == 404
    assert client.get(reaction_list).status_code == 404

    login_as(client, peer.login)
    assert client.put(url, json={"kind": "heart"}, headers={"Origin": "http://evil.test"}).status_code == 403
    assert client.put(url, json={"kind": "unknown"}).status_code == 422
    assert client.put(url, json={"kind": "heart"}).status_code == 200
    assert client.post(f"/chat/messages/{message.id}/delete").status_code == 200
    assert client.get(reaction_list).json() == {}
    assert client.put(url, json={"kind": "heart"}).status_code == 404
    assert db.get(ChatReaction, (message.id, peer.id)) is None


# ---------- картинки ----------


def test_image_upload_and_access(client, db):
    t, g, s1, s2 = _school(db)
    room, direct1 = chat.group_room(db, g.id), chat.direct_room(db, s1.id)
    login_as(client, "stud1")
    r = client.post(f"/chat/{room.id}/image", files={"image": ("a.png", _png(), "image/png")})
    url = r.json()["message"]["image"]
    assert r.status_code == 200 and url.startswith(f"/media/chat/{room.id}/")
    image = client.get(url)
    assert image.headers["content-type"] == "image/webp"
    assert image.headers["cache-control"] == "private, no-cache"
    # не картинка
    chat.limiter.reset()
    r = client.post(f"/chat/{room.id}/image", files={"image": ("a.png", b"nope", "image/png")})
    assert r.status_code == 400 and "not a valid image" in r.json()["error"]
    # чужая комната
    other = make_user(db, role="teacher", login="other")
    g2 = make_group(db, other, name="6Б")
    make_user(db, login="stud3", group=g2)
    c3 = _client("stud3")
    assert c3.get(url).status_code == 404
    assert c3.post(f"/chat/{direct1.id}/image", files={"image": ("a.png", _png(), "image/png")}).status_code == 404
    # После удаления файл доступен только модератору своей комнаты.
    message_id = db.query(ChatMessage).filter_by(kind="image").one().id
    assert client.post(f"/chat/messages/{message_id}/delete").json() == {"ok": True}
    assert client.get(url).status_code == 404
    assert (settings.media_dir / url.removeprefix("/media/")).is_file()
    login_as(client, "stud2")
    assert client.get(url).status_code == 404
    login_as(client, "other")
    assert client.get(url).status_code == 404
    login_as(client, "teach")
    retained = client.get(url)
    assert retained.content == image.content
    assert retained.headers["cache-control"] == "private, no-store"


def test_image_rate_limited(client, db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    login_as(client, "stud1")
    assert client.post(f"/chat/{room.id}/image", files={"image": ("a.png", _png(), "image/png")}).status_code == 200
    r = client.post(f"/chat/{room.id}/image", files={"image": ("a.png", _png(), "image/png")})
    assert r.status_code == 429 and "Too many messages" in r.json()["error"]


# ---------- удаление ----------


def test_delete_permissions(client, db):
    t, g, s1, s2 = _school(db)
    room = chat.group_room(db, g.id)
    m1 = chat.post_text(db, s1, room, "раз")
    login_as(client, "stud2")
    assert client.post(f"/chat/messages/{m1.id}/delete").status_code == 403
    client.post("/logout")
    login_as(client, "teach")
    assert client.post(f"/chat/messages/{m1.id}/delete").json() == {"ok": True}
    assert client.post("/chat/messages/999999/delete").status_code == 404


def test_deleted_student_content_in_room_and_history_is_moderator_only(client, db):
    seed_filter(db)
    teacher, group, student, peer = _school(db)
    room = chat.group_room(db, group.id)
    message = chat.post_text(db, student, room, "ну ты сука")
    sticker = chat.post_sticker(db, peer, room, "cat")

    login_as(client, student.login)
    assert client.post(f"/chat/messages/{message.id}/delete").json() == {"ok": True}
    login_as(client, teacher.login)
    assert client.post(f"/chat/messages/{sticker.id}/delete").json() == {"ok": True}

    page = client.get(f"/chat/{room.id}")
    assert page.headers["cache-control"] == "private, no-store"
    deleted_text, deleted_sticker = _data(page.text)["messages"]
    assert deleted_text["deleted"] and deleted_text["original"] == "ну ты сука"
    assert deleted_text["text"] == message.text_shown
    assert deleted_sticker["deleted"] and deleted_sticker["sticker"] == "/static/stickers/cat.svg"
    history = client.get(f"/chat/{room.id}/messages", params={"before": sticker.id + 1})
    assert history.headers["cache-control"] == "private, no-store"
    assert history.json()["messages"] == [deleted_text, deleted_sticker]

    login_as(client, peer.login)
    student_page = _data(client.get(f"/chat/{room.id}").text)["messages"]
    student_history = client.get(f"/chat/{room.id}/messages").json()["messages"]
    assert student_page == student_history
    for item in student_history:
        assert item["deleted"] and not {"text", "original", "sticker", "image"} & item.keys()


def test_delete_needs_same_origin(db):
    t, g, s1, _ = _school(db)
    m1 = chat.post_text(db, s1, chat.group_room(db, g.id), "раз")
    r = _client("stud1").post(f"/chat/messages/{m1.id}/delete", headers={"Origin": "http://evil.test"})
    assert r.status_code == 403
    db.refresh(m1)
    assert m1.deleted_at is None


# ---------- WebSocket ----------


def test_ws_rejects_foreign_origin_and_anonymous(db):
    _school(db)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws/chat", headers={"Origin": "http://testserver"}) as ws:
                ws.receive_text()
    c = _client("stud1")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/chat", headers={"Origin": "http://evil.test"}) as ws:
            ws.receive_text()


def test_ws_send_delivers_to_room_members_only(db):
    seed_filter(db)
    t, g, s1, s2 = _school(db)
    other = make_user(db, role="teacher", login="other")
    make_user(db, login="stud3", group=make_group(db, other, name="6Б"))
    room = chat.group_room(db, g.id)
    c1, c2, ct, c3 = _client("stud1"), _client("stud2"), _client("teach"), _client("stud3")
    with c1.websocket_connect("/ws/chat") as w1, c2.websocket_connect("/ws/chat") as w2, \
            ct.websocket_connect("/ws/chat") as wt, c3.websocket_connect("/ws/chat") as w3:
        w1.send_json({"type": "send", "room_id": room.id, "text": "ну ты сука", "client_id": "abc"})
        own, peer, teacher = w1.receive_json(), w2.receive_json(), wt.receive_json()
        assert own["client_id"] == "abc" and own["message"]["author"]["id"] == s1.id
        assert "сука" not in peer["message"]["text"] and "original" not in peer["message"]
        assert teacher["message"]["original"] == "ну ты сука"
        # чужой ученик ничего не получил: следующее, что придёт ему, — ответ на его собственный ping
        w3.send_json({"type": "ping"})
        assert w3.receive_json() == {"type": "pong"}
        # и писать в чужую комнату не может
        w3.send_json({"type": "send", "room_id": room.id, "text": "привет", "client_id": "x"})
        assert w3.receive_json() == {"type": "error", "client_id": "x", "error": chat.NO_ACCESS}


def test_ws_errors_and_rate_limit(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    c1 = _client("stud1")
    with c1.websocket_connect("/ws/chat") as w1:
        w1.send_text("не json")
        w1.send_json({"type": "send", "room_id": room.id, "text": "   ", "client_id": "e"})
        assert w1.receive_json()["error"] == chat.EMPTY_TEXT
        w1.send_json({"type": "send", "room_id": room.id, "sticker": "heart", "client_id": "s"})
        assert w1.receive_json()["message"]["kind"] == "sticker"
        w1.send_json({"type": "send", "room_id": room.id, "text": "быстро", "client_id": "f"})
        assert w1.receive_json() == {"type": "error", "client_id": "f", "error": chat.TOO_FAST}


def test_ws_delete_and_image_broadcast(client, db):
    t, g, s1, s2 = _school(db)
    room = chat.group_room(db, g.id)
    m = chat.post_text(db, s1, room, "удалю")
    c2 = _client("stud2")
    login_as(client, "stud1")
    with c2.websocket_connect("/ws/chat") as w2:
        client.post(f"/chat/messages/{m.id}/delete")
        assert w2.receive_json() == {"type": "deleted", "room_id": room.id, "id": m.id}
        chat.limiter.reset()  # «удалю» было меньше секунды назад
        assert client.post(f"/chat/{room.id}/image", files={"image": ("a.png", _png(), "image/png")}).status_code == 200
        assert w2.receive_json()["message"]["kind"] == "image"


def test_ws_read_mark(db):
    t, g, s1, s2 = _school(db)
    room = chat.group_room(db, g.id)
    m = chat.post_text(db, s2, room, "раз")
    c1 = _client("stud1")
    with c1.websocket_connect("/ws/chat") as w1:
        w1.send_json({"type": "read", "room_id": room.id, "message_id": 10**6})  # больше последнего — обрежется
        w1.send_json({"type": "ping"})
        w1.receive_json()
    db.expire_all()
    assert chat.unread_total(db, s1) == 0
    chat.limiter.reset()
    chat.post_text(db, s2, room, "два")
    assert chat.unread_total(db, s1) == 1  # отметка не «убежала» вперёд
    assert db.get(ChatReadMark, (room.id, s1.id)).last_read_message_id == m.id


def test_ws_closed_after_session_revoked(db):
    t, g, s1, _ = _school(db)
    room = chat.group_room(db, g.id)
    c1 = _client("stud1")
    from app.auth.sessions import revoke_user_sessions
    with c1.websocket_connect("/ws/chat") as w1:
        revoke_user_sessions(db, s1.id)
        w1.send_json({"type": "send", "room_id": room.id, "text": "ещё тут?", "client_id": "z"})
        with pytest.raises(WebSocketDisconnect):
            w1.receive_json()
    assert db.query(ChatMessage).count() == 0


# ---------- словарь фильтра ----------


def test_filter_page_admin_only(client, db):
    seed_filter(db)
    make_user(db, role="teacher", login="teach")
    login_as(client, "teach")
    assert client.get("/school/filter").status_code == 403
    client.post("/logout")
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    html = client.get("/school/filter").text
    assert "^сука" in html and "ромашка" in html
    assert "Shown in chat" in client.get("/school/filter", params={"check": "ну ты сука"}).text


def test_filter_save(client, db):
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    r = client.post("/school/filter", data={"bad_words": "Кот\n\nкот\n^пёс", "exceptions": "скотина", "replacements": "мяч"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert sorted(db.scalars(BadWord.__table__.select().with_only_columns(BadWord.pattern))) == ["^пёс", "кот"]
    r = client.post("/school/filter", data={"bad_words": "кот", "exceptions": "", "replacements": "котик"})
    assert "These replacement words are flagged by the filter: котик" in r.text
    r = client.post("/school/filter", data={"bad_words": "кот", "exceptions": "", "replacements": " "})
    assert "Enter at least one replacement word" in r.text
    assert [w.word for w in db.query(ReplacementWord)] == ["мяч"]
