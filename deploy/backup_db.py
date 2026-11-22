"""Write a database dump and media archive from the application's configuration."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

from sqlalchemy.engine import make_url

from app.config import settings


def backup(directory: Path) -> None:
    url = make_url(settings.database_url)
    if url.get_backend_name() == "postgresql":
        connection = {}
        for name, value in (("host", url.host), ("port", url.port), ("user", url.username),
                            ("dbname", url.database), ("password", url.password)):
            if value is not None:
                connection[name] = str(value)
        for name in ("host", "port", "user", "dbname", "sslmode", "sslrootcert", "sslcert", "sslkey", "service"):
            if name in url.query:
                connection[name] = url.query[name]
        # Use libpq environment variables so credentials do not appear in process arguments.
        names = {"user": "PGUSER", "dbname": "PGDATABASE", "password": "PGPASSWORD"}
        env = os.environ | {names.get(key, "PG" + key.upper()): value for key, value in connection.items()}
        dump = directory / "database.dump"
        subprocess.run(["pg_dump", "--no-password", "--format=custom", "--file", dump], env=env, check=True)
        subprocess.run(["pg_restore", "--list", dump], stdout=subprocess.DEVNULL, check=True)
    elif url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        source = Path(url.database).resolve()
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
            with sqlite3.connect(directory / "site.db") as out:
                db.backup(out)
    else:
        raise ValueError("Unsupported database for backup")

    if not settings.media_dir.is_dir():
        raise FileNotFoundError("Media directory is missing")
    with tarfile.open(directory / "media.tar.gz", "w:gz") as archive:
        archive.add(settings.media_dir, arcname="media")


if __name__ == "__main__":
    backup(Path(sys.argv[1]))
