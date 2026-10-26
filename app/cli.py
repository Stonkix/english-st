"""Служебные команды: заведение админов, сброс паролей и снятие блокировок входа.

Запускать из корня проекта, в виртуальном окружении:
    python -m app.cli create-admin --login L --first-name Имя --last-name Фамилия
        создать администратора с временным паролем (при первом входе его нужно сменить);
    python -m app.cli reset-password --login L
        выдать любому пользователю новый временный пароль и завершить все его сессии;
    python -m app.cli unlock --login L
        снять блокировку входа по логину со всех IP;
    python -m app.cli unlock --all
        снять все блокировки входа.

Временный пароль печатается один раз, в консоль; в БД хранится только хеш.
Коды возврата: 0 — успех, 1 — ошибка ввода или конфликт (логин занят, пользователя нет).
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import accounts
from app.auth import login_guard
from app.auth.passwords import generate_temp_password, hash_password
from app.auth.sessions import revoke_user_sessions
from app.db import SessionLocal
from app.logins import is_valid_login, normalize_login
from app.models import LoginAttempt, User


def _error(message: str) -> None:
    print(f"Ошибка: {message}", file=sys.stderr)


def _login_or_error(raw: str) -> str | None:
    login = normalize_login(raw)
    if not is_valid_login(login):
        _error(accounts.LOGIN_INVALID)
        return None
    return login


def _print_credentials(login: str, password: str) -> None:
    print(f"Логин: {login}")
    print(f"Временный пароль: {password}")
    print("При первом входе пароль нужно сменить.")


def _create_admin(args: argparse.Namespace) -> int:
    login = _login_or_error(args.login)
    if login is None:
        return 1
    first_name, last_name = args.first_name.strip(), args.last_name.strip()
    errors = accounts.name_errors(first_name, last_name)
    if errors:
        _error(errors[0])
        return 1
    password = generate_temp_password()
    with SessionLocal() as db:
        if accounts.login_taken(db, login):
            _error(accounts.LOGIN_TAKEN)
            return 1
        db.add(
            User(
                login=login,
                password_hash=hash_password(password),
                role="admin",
                first_name=first_name,
                last_name=last_name,
                must_change_password=True,
            )
        )
        try:
            db.commit()
        except IntegrityError:  # логин заняли между проверкой и вставкой
            db.rollback()
            _error(accounts.LOGIN_TAKEN)
            return 1
    _print_credentials(login, password)
    return 0


def _reset_password(args: argparse.Namespace) -> int:
    login = _login_or_error(args.login)
    if login is None:
        return 1
    password = generate_temp_password()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == login))
        if user is None:
            _error(f"пользователь с логином {login} не найден")
            return 1
        user.password_hash = hash_password(password)
        user.must_change_password = True
        db.commit()
        revoke_user_sessions(db, user.id)
    _print_credentials(login, password)
    return 0


def _unlock(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        if args.all:
            removed = db.scalar(select(func.count()).select_from(LoginAttempt))
            login_guard.reset_all(db)
        else:
            login = _login_or_error(args.login)
            if login is None:
                return 1
            removed = login_guard.unlock(db, login)
    print(f"Снято блокировок: {removed}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Служебные команды сайта.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="команда")

    create = commands.add_parser("create-admin", help="создать администратора с временным паролем")
    create.add_argument("--login", required=True)
    create.add_argument("--first-name", required=True)
    create.add_argument("--last-name", required=True)

    reset = commands.add_parser("reset-password", help="выдать новый временный пароль и завершить сессии")
    reset.add_argument("--login", required=True)

    unlock = commands.add_parser("unlock", help="снять блокировку входа")
    target = unlock.add_mutually_exclusive_group(required=True)
    target.add_argument("--login", help="снять блокировку этого логина")
    target.add_argument("--all", action="store_true", help="снять все блокировки")
    return parser


_HANDLERS = {"create-admin": _create_admin, "reset-password": _reset_password, "unlock": _unlock}


def main(argv: Sequence[str]) -> int:
    args = _build_parser().parse_args(list(argv))
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
