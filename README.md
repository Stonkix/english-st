# Английский для детей — сайт школы

FastAPI + SQLite + Jinja2 + HTMX + sqladmin. Без Node, без сборки фронтенда, без внешних CDN.
Адрес сайта: https://english-st.ru (`english-st.online` перенаправляет на него).

## Что есть сейчас (этапы 0–1)

Готов каркас и аккаунты. Задания, чат, штампики и магазин — следующие этапы.

| Что | Адрес |
|---|---|
| Вход по логину и паролю; блокировка после неверных попыток | `/login` |
| Первая смена временного пароля (обязательна после выдачи или сброса пароля) | `/password/new` |
| Профиль: смена пароля, загрузка и удаление аватара | `/profile` |
| Кабинет учителя: группы, ученики, выдача и сброс паролей, снятие блокировки, деактивация, аватар ученика и история его прежних фото | `/teacher` |
| Кабинет администратора: учителя школы (создание, сброс пароля, деактивация) | `/school` |
| Админка базы данных (sqladmin): пользователи и группы | `/admin` |
| Фото (только вошедшим и только тем, кому разрешено) | `/media/avatars/…` |
| Проверка, что сайт жив | `/healthz` |

### Роли

- **admin** — заводит учителей (раздел «Школа»), видит базу в `/admin`. Первого админа создаёт команда `create-admin` (см. ниже).
- **teacher** — создаёт группы и учеников, выдаёт временные пароли, видит аватары своих учеников и историю прежних фото.
- **student** — входит, меняет пароль, ставит себе аватар (если учитель не запретил).

Пароли детям выдаёт учитель или админ: временный пароль показывается один раз, при первом входе его нужно сменить. В базе хранится только хеш.

### Фото

Аватар загружается до 10 МБ (JPEG, PNG, HEIC и др.), перекодируется в квадрат 512 px WebP, метаданные (EXIF, GPS) удаляются. Публичной раздачи фото нет: Python проверяет права и отвечает nginx заголовком `X-Accel-Redirect`, файл отдаёт закрытый `internal` location.

## Запуск локально (Windows)

В папке проекта, в командной строке (cmd):

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt -c constraints.txt
copy .env.example .env
```

Откройте `.env` в блокноте (`notepad .env`) и поменяйте `DEBUG=false` на `DEBUG=true`. Остальное для локальной работы трогать не нужно. Дальше:

```bat
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m app.cli create-admin --login admin --first-name Анна --last-name Иванова
.venv\Scripts\uvicorn app.main:app --reload --port 8002
```

`create-admin` печатает временный пароль — скопируйте его, он показывается один раз. Откройте http://127.0.0.1:8002 , войдите под этим логином и паролем, придумайте новый пароль. Дальше: `/school` — создать учителя, у учителя в `/teacher` — группу и учеников.

В режиме `DEBUG=true` база лежит в `data/site.db`, фото — в `media/` (обе папки не попадают в git). Чтобы начать с чистого листа, остановите сервер и удалите их.

Тесты (работают на временной базе, `.env` не нужен): `.venv\Scripts\python -m pytest -q`

## Деплой на VDS (Ubuntu 24.04)

Сайт ставится рядом с уже работающими `rieltor` (порт 8000) и `artsite` (порт 8001): свой пользователь, свой порт **8002**, свой конфиг nginx. Общий nginx и certbot используются те же, чужие конфиги не трогаем.

Перед началом: в DNS у `english-st.ru`, `www.english-st.ru`, `english-st.online`, `www.english-st.online` должны быть A-записи на IP сервера (проверка: `dig +short english-st.ru`).

Все команды — по SSH на сервере, от пользователя с `sudo`.

```bash
# 1. Пакеты (если уже стоят — apt ничего не сломает) и пользователь сайта
sudo apt update && sudo apt install -y python3-venv git nginx certbot python3-certbot-nginx
sudo adduser --system --group --home /srv/englishst englishst

# 2. Код: клонируется от имени englishst в его (пока пустую) домашнюю папку
cd /srv/englishst
sudo -u englishst git clone <АДРЕС_РЕПОЗИТОРИЯ> .
# Приватный репозиторий: нужен доступ без пароля - токен в адресе (https://<токен>@github.com/...) или deploy-ключ в /srv/englishst/.ssh.
# Тот же доступ нужен update.sh (git pull от имени englishst).
sudo -u englishst python3 -m venv .venv
sudo -u englishst .venv/bin/pip install -r requirements.txt -c constraints.txt
sudo -u englishst mkdir -p data media
# Права: БД и фото детей закрыты от посторонних пользователей сервера; nginx (www-data) читает media через группу
sudo chmod 711 /srv/englishst
sudo chmod 700 /srv/englishst/data
sudo chown englishst:www-data /srv/englishst/media && sudo chmod 2750 /srv/englishst/media
sudo chmod -R g+rX,o-rwx /srv/englishst/media     # если в media уже были файлы

# 3. Настройки: .env создаётся из примера, секрет генерируется сразу
sudo -u englishst cp .env.example .env
sudo chmod 600 /srv/englishst/.env
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
sudo -u englishst sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|; s|^BASE_URL=.*|BASE_URL=https://english-st.ru|" .env
sudo grep -E '^(DEBUG|BASE_URL)=' /srv/englishst/.env     # должно быть: DEBUG=false и BASE_URL=https://english-st.ru

# 4. База и первый администратор (пароль печатается один раз — запишите)
sudo -u englishst .venv/bin/alembic upgrade head
sudo -u englishst .venv/bin/python -m app.cli create-admin --login admin --first-name Имя --last-name Фамилия

# 5. Сервис
sudo cp deploy/englishst.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now englishst
sleep 2; systemctl is-active englishst && curl -s http://127.0.0.1:8002/healthz   # active и {"ok":true}

# 6. nginx рядом с соседними сайтами
sudo cp deploy/nginx.conf /etc/nginx/sites-available/englishst
sudo ln -s /etc/nginx/sites-available/englishst /etc/nginx/sites-enabled/englishst
ls /etc/nginx/sites-enabled/          # rieltor и artsite на месте; default здесь не удаляйте и не добавляйте
sudo nginx -t && sudo systemctl reload nginx

# 7. HTTPS для всех четырёх имён (certbot сам допишет 443 и редиректы)
sudo certbot --nginx -d english-st.ru -d www.english-st.ru -d english-st.online -d www.english-st.online
sudo nginx -t && sudo systemctl reload nginx

# 8. Бэкап по cron (от root)
sudo chmod +x deploy/backup.sh deploy/update.sh
sudo crontab -e     # добавьте строку:  40 3 * * * /srv/englishst/deploy/backup.sh
```

Замечания к шагам:

- **Шаг 2.** Если `/srv/englishst` уже содержит код (например, залили через scp), клонировать не нужно — достаточно, чтобы файлы принадлежали `englishst`: `sudo chown -R englishst:englishst /srv/englishst`.
- **Шаг 6.** Файл `/etc/nginx/sites-enabled/default` удаляйте, только если он ещё есть **и** соседние сайты не выложены (на этом сервере они уже работают — не трогайте). Если `nginx -t` ругается на `proxy_params` — это файл из пакета nginx Ubuntu (`/etc/nginx/proxy_params`); его используют и соседние сайты.
- **Шаг 7.** Вход возможен только по HTTPS: cookie сессии помечена `Secure`, поэтому до certbot по http войти не получится.
- Проверка после выкладки: https://english-st.ru/login открывается; `http://english-st.online/login` ведёт на `https://english-st.ru/login`; `https://english-st.ru/_protected/x` и `/media/x` отвечают 404.
- Обновление делает `git pull` от имени `englishst`, поэтому у этого пользователя должен быть доступ к репозиторию без пароля (см. шаг 2). Проверка: `cd /srv/englishst && sudo -u englishst git pull --ff-only`.
- Воркер ровно один (на VDS с 1 ГБ памяти это и экономно, а чат на этапе 3 живёт в памяти процесса, ему нужен один процесс). Не увеличивайте `--workers` в `englishst.service`.

### Обновление

```bash
sudo /srv/englishst/deploy/update.sh
```

Скрипт делает `git pull --ff-only`, ставит зависимости, применяет миграции (`alembic upgrade head`), перезапускает сервис и печатает `active`. Если вместо `active` другое слово — `sudo journalctl -u englishst -n 50`.

Изменился `deploy/nginx.conf` или `englishst.service`? Скопируйте файл заново (шаги 5–6, без `ln` и certbot) и `sudo nginx -t && sudo systemctl reload nginx` / `sudo systemctl daemon-reload && sudo systemctl restart englishst`. Внимание: `nginx.conf` в репозитории — шаблон до certbot; при повторном копировании certbot-овые строки (443, сертификаты) слетят, и нужно снова выполнить шаг 7.

### Бэкап

`deploy/backup.sh` каждую ночь кладёт в `/var/backups/englishst` копию базы (онлайн, без остановки сайта) и архив `media/`, хранит 14 дней. Каталог и файлы доступны только root — там фото детей. Копия на том же сервере не спасёт от потери VDS: периодически забирайте `/var/backups/englishst` себе (например, `scp` или `rclone`).

Восстановление базы: остановить сервис, положить `site-ГГГГ-ММ-ДД.db` как `/srv/englishst/data/site.db` (владелец `englishst`), запустить сервис.

## Служебные команды

Запускаются в папке проекта в виртуальном окружении. На сервере:

```bash
cd /srv/englishst
sudo -u englishst .venv/bin/python -m app.cli <команда>
```

| Команда | Что делает |
|---|---|
| `create-admin --login L --first-name Имя --last-name Фамилия` | создаёт администратора и печатает временный пароль |
| `reset-password --login L` | выдаёт любому пользователю новый временный пароль и завершает все его сессии (забыли пароль админа — так) |
| `unlock --login L` | снимает блокировку входа по логину |
| `unlock --all` | снимает все блокировки входа |

Временный пароль печатается один раз; при первом входе его нужно сменить. Код возврата 0 — успех, 1 — ошибка (логин занят, пользователя нет).

## Структура

```
app/
  main.py        приложение, middleware (CSRF по Origin, продление сессии), страницы ошибок
  config.py      настройки из .env
  db.py          движок SQLite, сессии
  models/        User, Group, сессии, попытки входа, история аватаров
  auth/          пароли, сессии, защита от подбора (login_guard), права доступа
  accounts.py    общие правила: имена, логины
  avatars.py, images.py   загрузка и обработка фото (Pillow)
  admin.py       sqladmin на /admin
  cli.py         служебные команды
  routes/        auth, profile, teacher, school, media
  templates/     Jinja2; sqladmin/ — переопределения шаблонов админки
  static/        css, js, шрифты, vendor (htmx)
alembic/         миграции
deploy/          englishst.service, nginx.conf, backup.sh, update.sh
tests/           pytest
docs/            спецификация и планы
```

## Безопасность в двух словах

- CSP без внешних источников; для `/admin` (sqladmin с встроенным скриптом уведомлений) в nginx отдельная, более мягкая политика только для этого адреса.
- nginx ограничивает частоту входа (20 попыток в минуту на IP: класс входит с одного школьного Wi-Fi; при превышении показывается страница «Слишком много попыток») и загрузок аватара; приложение дополнительно блокирует подбор паролей по логину.
- Лимит загрузки — 10 МБ (в nginx `client_max_body_size 11m`).
- При `DEBUG=false` приложение не стартует с `SECRET_KEY=change-me`.
