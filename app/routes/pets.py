"""Питомцы ученика: уход (покормить, убрать) и покупки в магазине (питомец, корм)."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app import pets
from app.auth.deps import require_role
from app.db import get_db
from app.models import User
from app.params import Id, form_int
from app.routes.shop import student_shop_page  # покупки питомцев — на странице магазина
from app.templating import templates
from app.timeutil import moscow_today

router = APIRouter()
student_only = require_role("student")

OK_MESSAGES = {
    "new": "Ура, у тебя новый питомец! Не забывай кормить его 2 раза в день и убирать за ним.",
    "fed": "Ням! Питомец поел.",
    "cleaned": "Чисто! Питомец доволен.",
}


def pets_page(request: Request, db: DbSession, user: User, ok: str | None = None, error: str | None = None):
    today = moscow_today()
    cfg = pets.config(db)
    context = {
        "user": user,
        "statuses": pets.statuses(db, user.id, today),
        "gone": pets.runaways(db, user.id, today - timedelta(days=7)),
        "food": pets.food(db, user.id),
        "cfg": cfg,
        "feedings_per_day": pets.FEEDINGS_PER_DAY,
        "ok": ok,
        "error": error,
    }
    return templates.TemplateResponse(request, "pets/pets.html", context, status_code=400 if error else 200)


@router.get("/pets")
def my_pets(request: Request, ok: str = "", user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    return pets_page(request, db, user, ok=OK_MESSAGES.get(ok))


@router.post("/pets/{pet_id}/feed")
def feed(pet_id: Id, request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    try:
        pets.feed(db, user, pet_id)
    except pets.PetError as exc:
        return pets_page(request, db, user, error=str(exc))
    return RedirectResponse(f"/pets?ok=fed#pet-{pet_id}", status_code=303)


@router.post("/pets/{pet_id}/clean")
def clean(pet_id: Id, request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    try:
        pets.clean(db, user, pet_id)
    except pets.PetError as exc:
        return pets_page(request, db, user, error=str(exc))
    return RedirectResponse(f"/pets?ok=cleaned#pet-{pet_id}", status_code=303)


# ---------- покупки (из магазина) ----------


@router.post("/shop/pets/{species_id}/buy")
def buy_pet(species_id: Id, request: Request, name: str = Form(""), user: User = Depends(student_only),
            db: DbSession = Depends(get_db)):
    try:
        pets.buy_pet(db, user, species_id, name)
    except pets.PetError as exc:
        return student_shop_page(request, db, user, error=str(exc))
    return RedirectResponse("/pets?ok=new", status_code=303)


@router.post("/shop/food")
def buy_food(request: Request, packs: str = Form("1"), user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    try:
        pets.buy_food(db, user, form_int(packs, 1, pets.PACKS_MAX) or 0)
    except pets.PetError as exc:
        return student_shop_page(request, db, user, error=str(exc))
    return RedirectResponse("/shop?ok=food#food", status_code=303)
