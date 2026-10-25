"""Фото профиля: проверка, поворот по EXIF, удаление метаданных (в т.ч. GPS), квадрат 512px в WebP."""

import logging
import secrets
from io import BytesIO
from typing import BinaryIO

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from app.config import settings

register_heif_opener()  # фото с iPhone в формате HEIC/HEIF открываются как обычные картинки

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
AVATAR_SIZE = 512
WEBP_QUALITY = 85
MAX_PIXELS = 64_000_000  # больше любой камеры телефона; защита от «бомб» (мало байт, гигантская картинка)
_CHUNK = 256 * 1024


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


def _decode_square(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage("Пустой файл или больше 15 МБ")
    try:
        img = Image.open(BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise InvalidImage("Слишком большое изображение")
        img.draft("RGB", (AVATAR_SIZE * 2, AVATAR_SIZE * 2))  # JPEG декодируется сразу уменьшенным
        img = ImageOps.exif_transpose(img).convert("RGB")  # convert отбрасывает EXIF
        img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    except InvalidImage:
        raise
    except Exception as exc:  # PIL, pillow_heif, DecompressionBombError, обрезанные файлы — всё это «не картинка»
        raise InvalidImage(str(exc)) from exc
    img.info.clear()  # ни EXIF, ни XMP, ни GPS в файл не попадут
    return img


def save_avatar(user_id: int, data: bytes) -> str:
    """Сохраняет аватар, возвращает путь относительно media_dir: avatars/<user_id>/<12 hex>.webp."""
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
