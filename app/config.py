from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    debug: bool = False
    base_url: str = "http://127.0.0.1:8002"  # без слеша в конце; с ним сверяется Origin
    secret_key: str = "change-me"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'site.db').as_posix()}"
    media_dir: Path = BASE_DIR / "media"


settings = Settings()
