"""Фото профиля: проверка, поворот по EXIF, удаление метаданных (в т.ч. GPS), квадрат 512px в WebP."""

import logging
import secrets
import threading
from io import BytesIO
from typing import BinaryIO

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from app.config import settings

register_heif_opener()  # фото с iPhone в формате HEIC/HEIF открываются как обычные картинки

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
AVATAR_SIZE = 512
WEBP_QUALITY = 85
# Телефоны снимают до ~50 Мп (48 Мп точно проходят). Больше — отказ: полный кадр в памяти занимает 4 байта на пиксель
MAX_PIXELS = 50_000_000
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


def read_limited(stream: BinaryIO) -> bytes:
    """Читает порциями и бросает InvalidImage, как только набралось больше MAX_UPLOAD_BYTES."""
    parts: list[bytes] = []
    total = 0
    while total <= MAX_UPLOAD_BYTES:
        chunk = stream.read(min(_CHUNK, MAX_UPLOAD_BYTES + 1 - total))
        if not chunk:
            return b"".join(parts)
        parts.append(chunk)
        total += len(chunk)
    raise InvalidImage("Файл больше 15 МБ")


def _shrink(img: Image.Image) -> Image.Image:
    """Уменьшает целое число раз, пока короткая сторона не станет 512–1023 px, и возвращает RGBA.

    Идём полосами: в любом режиме (1, P, RGBA…) вместо полноразмерных копий в памяти — только маленькая полоса.
    """
    w, h = img.size
    k = max(1, min(w, h) // AVATAR_SIZE)
    ow, oh = w // k, h // k
    out = Image.new("RGBA", (ow, oh))
    step = _BAND_ROWS  # выходных строк в полосе
    for y in range(0, oh, step):
        rows = min(step, oh - y)
        band = img.crop((0, y * k, ow * k, (y + rows) * k)).convert("RGBA")
        out.paste(band.reduce(k) if k > 1 else band, (0, y))
    return out


def _decode_square(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage("Пустой файл или больше 15 МБ")
    try:
        img = Image.open(BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise InvalidImage("Слишком большое изображение")
        img.draft("RGB", (AVATAR_SIZE * 2, AVATAR_SIZE * 2))  # JPEG декодируется сразу уменьшенным
        img.load()  # единственный полноразмерный кадр в памяти
        orientation = img.getexif().get(0x0112)
        small = _shrink(img)
        img.close()
        del img
        if orientation in _ORIENTATION:
            small = small.transpose(_ORIENTATION[orientation])
        fitted = ImageOps.fit(small, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
        # прозрачность -> белый фон (иначе у PNG с альфой фон станет чёрным); новый кадр — без EXIF/XMP/GPS
        result = Image.new("RGB", fitted.size, "white")
        result.paste(fitted, mask=fitted.getchannel("A"))
    except InvalidImage:
        raise
    except Exception as exc:  # PIL, pillow_heif, DecompressionBombError, обрезанные файлы — всё это «не картинка»
        raise InvalidImage(str(exc)) from exc
    return result


def save_avatar(user_id: int, data: bytes) -> str:
    """Сохраняет аватар, возвращает путь относительно media_dir: avatars/<user_id>/<12 hex>.webp."""
    with _DECODE_LOCK:
        img = _decode_square(data)
    rel = f"avatars/{user_id}/{secrets.token_hex(6)}.webp"
    target = settings.media_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, "WEBP", quality=WEBP_QUALITY, method=4)
    return rel


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
