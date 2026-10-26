"""Фото профиля (квадрат 512px) и картинки вопросов (до 1280px): проверка, поворот по EXIF, удаление метаданных, WebP."""

import logging
import re
import secrets
import threading
from io import BytesIO
from typing import BinaryIO

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from app.config import settings

register_heif_opener()  # фото с iPhone в формате HEIC/HEIF открываются как обычные картинки

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CHAT_UPLOAD_BYTES = 15 * 1024 * 1024  # картинка в чат — прямо с камеры телефона
AVATAR_MAX_BYTES = 400 * 1024  # потолок размера готового файла
AVATAR_SIZE = 512
PICTURE_MAX_SIDE = 1280  # длинная сторона картинки вопроса
WEBP_QUALITIES = (85, 75, 65, 55, 45, 35, 25, 15)  # по порядку, пока файл не уложится в AVATAR_MAX_BYTES
# Телефоны снимают до ~50 Мп (48 Мп точно проходят). Больше — отказ: полный кадр в памяти занимает 4 байта на пиксель
MAX_PIXELS = 50_000_000
PICTURE_NAME = re.compile(r"[0-9a-f]{12}\.webp")
_CHUNK = 256 * 1024
_BAND_ROWS = 16  # полоса при уменьшении — столько выходных строк за раз

# Один процесс на тесном сервере: кадры декодируем строго по одному, иначе память умножается на число потоков
_DECODE_LOCK = threading.BoundedSemaphore(1)

_ORIENTATION = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


class InvalidImage(Exception):
    """Не картинка, повреждённый или слишком большой файл."""


def read_limited(stream: BinaryIO, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Читает порциями и бросает InvalidImage, как только набралось больше limit байт."""
    parts: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = stream.read(min(_CHUNK, limit + 1 - total))
        if not chunk:
            return b"".join(parts)
        parts.append(chunk)
        total += len(chunk)
    raise InvalidImage(f"Файл больше {limit // (1024 * 1024)} МБ")


def _shrink(img: Image.Image) -> Image.Image:
    """Уменьшает целое число раз, пока короткая сторона не станет 512–1023 px, и возвращает RGBA.

    Идём полосами: в любом режиме (1, P, RGBA…) вместо полноразмерных копий в памяти — только маленькая полоса.
    """
    w, h = img.size
    # Сразу берём центральный квадрат (в исходной ориентации — он «коммутирует» с поворотом по EXIF),
    # поэтому буфер out не больше (короткая сторона)², даже для вытянутых кадров вроде 800x62000
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    k = max(1, side // AVATAR_SIZE)
    n = side // k
    out = Image.new("RGBA", (n, n))
    for y in range(0, n, _BAND_ROWS):
        rows = min(_BAND_ROWS, n - y)
        box = (left, top + y * k, left + n * k, top + (y + rows) * k)
        band = img.crop(box).convert("RGBA")
        out.paste(band.reduce(k) if k > 1 else band, (0, y))
    return out


def _shrink_fit(img: Image.Image) -> Image.Image:
    """Как _shrink, но целиком и без обрезки: уменьшает в целое число раз, пока длинная сторона не станет 1280–2559 px."""
    w, h = img.size
    k = max(1, max(w, h) // PICTURE_MAX_SIDE)
    nw, nh = w // k, h // k
    out = Image.new("RGBA", (nw, nh))
    for y in range(0, nh, _BAND_ROWS):
        rows = min(_BAND_ROWS, nh - y)
        band = img.crop((0, y * k, nw * k, (y + rows) * k)).convert("RGBA")
        out.paste(band.reduce(k) if k > 1 else band, (0, y))
    return out


def _decode(data: bytes, draft_side: int, shrink, finish, limit: int = MAX_UPLOAD_BYTES) -> Image.Image:
    """Общая часть аватара и картинки вопроса: открыть, уменьшить полосами, повернуть по EXIF, убрать прозрачность."""
    if not data or len(data) > limit:
        raise InvalidImage("Пустой файл или слишком большой")
    try:
        img = Image.open(BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise InvalidImage("Слишком большое изображение")
        img.draft("RGB", (draft_side, draft_side))  # JPEG декодируется сразу уменьшенным
        img.load()  # единственный полноразмерный кадр в памяти
        orientation = img.getexif().get(0x0112)
        small = shrink(img)
        img.close()
        del img
        if orientation in _ORIENTATION:
            small = small.transpose(_ORIENTATION[orientation])
        fitted = finish(small)
        # прозрачность -> белый фон (иначе у PNG с альфой фон станет чёрным); новый кадр — без EXIF/XMP/GPS
        result = Image.new("RGB", fitted.size, "white")
        result.paste(fitted, mask=fitted.getchannel("A"))
    except InvalidImage:
        raise
    except Exception as exc:  # PIL, pillow_heif, DecompressionBombError, обрезанные файлы — всё это «не картинка»
        raise InvalidImage(str(exc)) from exc
    return result


def _decode_square(data: bytes) -> Image.Image:
    return _decode(
        data, AVATAR_SIZE * 2, _shrink,
        lambda small: ImageOps.fit(small, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS),
    )


def _fit_picture(small: Image.Image) -> Image.Image:
    small.thumbnail((PICTURE_MAX_SIDE, PICTURE_MAX_SIDE), Image.Resampling.LANCZOS)
    return small


def _decode_picture(data: bytes, limit: int = MAX_UPLOAD_BYTES) -> Image.Image:
    return _decode(data, PICTURE_MAX_SIDE * 2, _shrink_fit, _fit_picture, limit)


def _encode(img: Image.Image) -> tuple[bytes, bool]:
    """WebP с шагом качества вниз, пока не уложится в потолок; второй элемент — уложился ли."""
    for quality in WEBP_QUALITIES:
        buf = BytesIO()
        img.save(buf, "WEBP", quality=quality, method=4)
        if buf.tell() <= AVATAR_MAX_BYTES:
            return buf.getvalue(), True
    return buf.getvalue(), False


def _write(rel: str, content: bytes) -> str:
    target = settings.media_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return rel


def save_avatar(user_id: int, data: bytes) -> str:
    """Сохраняет аватар, возвращает путь относительно media_dir: avatars/<user_id>/<12 hex>.webp."""
    with _DECODE_LOCK:
        img = _decode_square(data)
    return _write(f"avatars/{user_id}/{secrets.token_hex(6)}.webp", _encode(img)[0])


def _save_fitted(folder: str, data: bytes, limit: int) -> str:
    with _DECODE_LOCK:
        img = _decode_picture(data, limit)
    content, fits = _encode(img)
    while not fits:  # шумная картинка не уложилась даже при низком качестве — уменьшаем размер
        img = img.resize((max(1, img.width * 4 // 5), max(1, img.height * 4 // 5)), Image.Resampling.LANCZOS)
        content, fits = _encode(img)
    return _write(f"{folder}/{secrets.token_hex(6)}.webp", content)


def save_picture(owner_id: int, data: bytes) -> str:
    """Сохраняет картинку вопроса (до 1280px, WebP до 400 КБ): questions/<owner_id>/<12 hex>.webp."""
    return _save_fitted(f"questions/{owner_id}", data, MAX_UPLOAD_BYTES)


def save_chat_image(room_id: int, data: bytes) -> str:
    """Картинка в чат (вход до 15 МБ, выход как у вопросов): chat/<room_id>/<12 hex>.webp."""
    return _save_fitted(f"chat/{room_id}", data, CHAT_UPLOAD_BYTES)


def is_picture_of(owner_id: int, rel: object) -> bool:
    """Путь вида questions/<owner_id>/<12 hex>.webp, и файл есть на диске."""
    if not isinstance(rel, str):
        return False
    prefix = f"questions/{owner_id}/"
    if not rel.startswith(prefix) or not PICTURE_NAME.fullmatch(rel[len(prefix):]):
        return False
    return (settings.media_dir / rel).is_file()


def delete_media(rel_path: str | None) -> None:
    """Удаляет файл из media_dir; пути вне media_dir игнорирует. Ошибка диска не должна ронять запрос."""
    if not rel_path:
        return
    root = settings.media_dir.resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logging.exception("Не удалось удалить файл %s", target)
