import time

from fastapi.templating import Jinja2Templates

from app.auth.passwords import RULE_TEXT
from app.config import BASE_DIR, settings
from app.stamps import icon_url, tint
from app.timeutil import moscow_str

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

def avatar_url(user) -> str | None:
    """Адрес фото (его отдаёт app/routes/media.py с проверкой прав) или None — тогда рисуем инициалы."""
    return f"/media/{user.avatar}" if user is not None and user.avatar else None


templates.env.filters["avatar_url"] = avatar_url
templates.env.filters["msk"] = moscow_str
templates.env.filters["stamp_icon"] = icon_url
templates.env.filters["stamp_tint"] = tint
templates.env.globals.update(
    settings=settings,
    password_rules=RULE_TEXT,  # для partials/password_rules.html
    static_v=int(time.time()),  # сброс кэша статики при каждом рестарте
)
