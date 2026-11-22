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
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$DEST"
chmod 700 "$DEST"
# Сервисному пользователю нужен доступ к временной папке, но итоговый архив доступен только root.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chown englishst "$TMP"
cd "$APP"
runuser -u englishst -- "$APP/.venv/bin/python" -m deploy.backup_db "$TMP"
STAGED=$(mktemp -d "$DEST/.partial-XXXXXXXX")
trap 'rm -rf "$TMP" "$STAGED"' EXIT
mv "$TMP/"* "$STAGED/"
chown root:root "$STAGED/"*
chmod 600 "$STAGED/"*
mv -T "$STAGED" "$DEST/backup-$STAMP-${STAGED##*-}"
find "$DEST" -mindepth 1 -maxdepth 1 -type d -name 'backup-????????T??????Z-*' -mtime "+$KEEP_DAYS" -exec rm -rf -- {} +
