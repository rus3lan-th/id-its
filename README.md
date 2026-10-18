# ИД ИТС

Веб-программа для ведения исполнительной документации (ИД) на стройке: объекты и разделы (НВК, ТС и др.), перечень работ по проекту и смете, отметки выполнения прорабом, ведомость объёмов, проверка ПТО, журналы (ОЖР по 1026/пр, журнал входного контроля по СП 543), исполнительные схемы, лаборатория, справочники организаций и подписантов, резервные копии, обновление программы из интерфейса.

Версия кода в репозитории: **2026.10.17.86** (снята с работающего контейнера 18.10.2026); в ветке `phase1-matching` — **2026.10.18.87** (исправления сопоставления со сметой, на сервер не выложена).

## Стек

- Python 3.12, FastAPI + Uvicorn — всё приложение (API и HTML-интерфейс) в одном файле `app.py` (~12 800 строк, 216 маршрутов).
- SQLite (`/data/id_nvk.sqlite3` в Docker volume `mvp_id_nvk_data`).
- openpyxl, ezdxf, pypdf, python-docx; в образе tesseract-ocr (rus) и poppler-utils для распознавания PDF.
- Docker Compose: сервис `id-nvk` (контейнер `id-nvk-mvp`, порт `127.0.0.1:8000`) + `cloudflared` (контейнер `id-its-tunnel`, quick tunnel `*.trycloudflare.com`).

## Структура

```
app.py                 — приложение целиком (бэкенд + интерфейс)
Dockerfile             — образ python:3.12-slim + tesseract/poppler
compose.yaml           — сервис приложения и туннель Cloudflare
start.sh               — запуск; подхватывает обновление из /data/app_override, откат после 3 неудачных стартов
requirements.txt
.env.example           — имена переменных окружения (без значений)
seeds/                 — стартовые данные объектов (JSON; в git не хранятся)
inbox/                 — входящие файлы (монтируется только на чтение; в git не хранится)
deploy/autoupdate.sh   — cron-скрипт автообновления app.py с Google Drive
deploy/install-0.11.0.sh — исторический скрипт установки/обновления
docs/README-mvp-0.1.md — исходное описание прототипа 0.1
```

## Как развёрнуто на сервере (Timeweb, Ubuntu 24.04)

- Каталог: `/opt/id-its/mvp` (compose-проект `mvp`), там же `.env` с `APP_USER` и `APP_PASSWORD_HASH`.
- Запуск: `cd /opt/id-its/mvp && docker compose up -d --build`. Оба контейнера `restart: unless-stopped`.
- Данные: volume `mvp_id_nvk_data` → `/data` в контейнере: база, `files/` (загруженные документы), `photos/`, `backups/` (сжатые копии базы), `app_backups/` (прежние версии app.py), `app_override/` (текущий app.py).
- **Важно:** образ собран с версией 0.11.0, а актуальный код лежит в `/data/app_override/app.py` и копируется поверх при старте (`start.sh`). Обновление идёт двумя путями: кнопка «Обновление программы» в интерфейсе и cron раз в минуту `/opt/id-its/autoupdate.sh`, который скачивает `app.py` из Google Drive и перезапускает контейнер, если версия новее.
- Внешний адрес — временная ссылка quick tunnel, она меняется при перезапуске контейнера `id-its-tunnel`.

## Локальный запуск

```bash
pip install -r requirements.txt
APP_DB_PATH=./data/id_nvk.sqlite3 APP_USER=admin APP_PASSWORD_HASH='<итерации:соль_hex:хеш_hex>' \
  uvicorn app:app --reload
```

Или в Docker: `cp .env.example .env`, заполнить `APP_USER` и `APP_PASSWORD_HASH`, затем `docker compose up -d --build`.

Хеш пароля: `python -c "import hashlib,os;s=os.urandom(16);p=input();print(f'200000:{s.hex()}:{hashlib.pbkdf2_hmac(\"sha256\",p.encode(),s,200000).hex()}')"`.

## Что не попало в репозиторий

`.env`, базы SQLite и их копии, загруженные файлы и фото, `seeds/*.json` (данные объекта Грушевская), каталоги `backup-*` и `*.bak` с сервера.

## Проверка сопоставления на копии базы

`tests/grushevskaya_matching.py` копирует базу во временный каталог, запускает автоматическое сопоставление со сметой и печатает итоги (работы, материалы, объекты, колодцы, двойной учёт, межсетевые привязки). Рабочую базу не трогает.

```bash
python tests/grushevskaya_matching.py --db /path/to/copy.sqlite3 --files /path/to/files          # раздел Грушевской
python tests/grushevskaya_matching.py --db /path/to/copy.sqlite3 --files /path/to/files --all    # все разделы
python tests/grushevskaya_matching.py ... --app /dir/with/old/app.py                             # прогон старой версии для сравнения
```
