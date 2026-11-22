from app.models import StampTransaction, StampType
from tests.factories import login_as, make_group, make_user


def test_ranking_counts_earned_stamps_and_stays_within_student_group(client, db):
    teacher = make_user(db, role="teacher")
    group = make_group(db, teacher, "Blue group")
    other_group = make_group(db, teacher, "Green group")
    alice = make_user(db, group=group, first_name="Alice", last_name="Bennett")
    ben = make_user(db, group=group, first_name="Ben", last_name="Carter")
    zero = make_user(db, group=group, first_name="Cora", last_name="Dale")
    inactive = make_user(db, group=group, is_active=False)
    outsider = make_user(db, group=other_group)
    common = StampType(name="Class star", icon="star", color="yellow", ranking_points=1)
    rare = StampType(name="Class gem", icon="gem", color="blue", ranking_points=10)
    legendary = StampType(name="Class crown", icon="crown", color="purple", ranking_points=25)
    db.add_all([common, rare, legendary])
    db.flush()
    db.add_all([
        StampTransaction(student_id=student.id, stamp_type_id=stamp_type.id, amount=amount, reason=reason)
        for student, stamp_type, amount, reason in [
            (alice, legendary, 1, "manual"),
            (alice, rare, 1, "daily"),
            (alice, common, 1, "daily"),
            (alice, common, 25, "conversion"),
            (alice, legendary, -1, "conversion"),
            (alice, rare, -1, "order"),
            (alice, rare, 1, "refund"),
            (alice, common, -1, "penalty"),
            (ben, rare, 3, "manual"),
            (ben, common, 4, "daily"),
            (ben, common, 2, "manual"),
            (inactive, legendary, 5, "manual"),
            (outsider, legendary, 10, "manual"),
        ]
    ])
    legendary.name = "Crown stamp"
    db.commit()

    login_as(client, alice.login)
    response = client.get("/ranking", params={"group_id": other_group.id})
    assert response.status_code == 200
    assert [
        (row["rank"], row["student_id"], row["name"], row["points"],
         {stamp.id: amount for stamp, amount in row["stamps"]})
        for row in response.context["rows"]
    ] == [
        (1, alice.id, "Alice Bennett", 36, {common.id: 1, rare.id: 1, legendary.id: 1}),
        (1, ben.id, "Ben Carter", 36, {common.id: 6, rare.id: 3}),
        (3, zero.id, "Cora Dale", 0, {}),
    ]
    assert "Blue group" in response.text and "Green group" not in response.text
    assert 'href="/ranking" aria-current="page"' in response.text and "ranking-self" in response.text
    assert 'alt="Class star" width="22" height="22"><strong>: 6</strong>' in response.text
    assert "0 stamps" in response.text


def test_staff_see_only_accessible_group_rankings(client, db):
    teacher = make_user(db, role="teacher")
    group = make_group(db, teacher, "Teacher group")
    other = make_group(db, make_user(db, role="teacher"), "Other group")
    admin = make_user(db, role="admin")
    login_as(client, teacher.login)

    own = client.get("/ranking")
    assert own.context["group"].id == group.id and "Other group" not in own.text
    assert client.get("/ranking", params={"group_id": other.id}).status_code == 404
    assert f'href="/ranking?group_id={group.id}"' in client.get(f"/teacher/groups/{group.id}").text

    login_as(client, admin.login)
    selected = client.get("/ranking", params={"group_id": other.id})
    assert selected.context["group"].id == other.id and "Teacher group" in selected.text


def test_student_without_a_group_gets_an_empty_ranking(client, db):
    student = make_user(db)
    login_as(client, student.login)
    response = client.get("/ranking")
    assert response.status_code == 200
    assert "Your group ranking will appear" in response.text and response.context["rows"] == []
