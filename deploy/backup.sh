#!/bin/sh
# Ежедневный бэкап БД и фото. cron (sudo crontab -e):
#   40 3 * * * /srv/englishst/deploy/backup.sh
# (минуты отличаются от соседних сайтов, чтобы бэкапы не шли одновременно на 1 ГБ памяти)
# Бэкап на том же сервере не спасёт от потери VDS — копируйте $DEST наружу (rclone в S3/Яндекс Object Storage).
# В архиве фото детей: каталог и файлы доступны только root (umask 077).
set -eu
umask 077

APP=/srv/englishst
DEST=/var/backups/englishst
KEEP_DAYS=14
STAMP=$(date +%F)

# sqlite3.connect на несуществующем пути молча создаёт пустую БД — бэкап «успешно» сохранил бы пустышку
[ -f "$APP/data/site.db" ] || { echo "Нет файла $APP/data/site.db: бэкапить нечего" >&2; exit 1; }

mkdir -p "$DEST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chown englishst "$TMP"

# Онлайн-бэкап SQLite: корректен даже во время записи. Запускаем от englishst: от root при открытии БД
# могли бы появиться site.db-wal и site.db-shm с владельцем root, и сайт перестал бы писать в БД.
runuser -u englishst -- "$APP/.venv/bin/python" -c "import sqlite3, sys; sqlite3.connect(sys.argv[1]).backup(sqlite3.connect(sys.argv[2]))" \
    "$APP/data/site.db" "$TMP/site.db"
mv "$TMP/site.db" "$DEST/site-$STAMP.db"
chown root:root "$DEST/site-$STAMP.db"

tar -czf "$DEST/media-$STAMP.tar.gz" -C "$APP" media
find "$DEST" -type f -mtime +$KEEP_DAYS -delete
