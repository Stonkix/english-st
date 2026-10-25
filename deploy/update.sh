#!/bin/sh
# Обновление сайта: sudo /srv/englishst/deploy/update.sh
# Код и зависимости обновляются от имени englishst, перезапуск сервиса — от root.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите через sudo: sudo /srv/englishst/deploy/update.sh" >&2
    exit 1
fi

cd /srv/englishst
sudo -u englishst git pull --ff-only
sudo -u englishst .venv/bin/pip install -r requirements.txt -c constraints.txt
sudo -u englishst .venv/bin/alembic upgrade head
systemctl restart englishst
systemctl is-active englishst
