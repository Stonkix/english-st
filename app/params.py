"""Общие типы параметров запроса."""

from typing import Annotated

from fastapi import Path

MAX_ID = 2**31 - 1  # больше SQLite не умеет, а настоящих записей столько не бывает

# Числовой id в адресе. Нечисло, ноль, минус и «огромное» дают ошибку проверки пути, а она — страницу 404
Id = Annotated[int, Path(ge=1, le=MAX_ID)]
