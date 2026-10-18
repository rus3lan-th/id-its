#!/usr/bin/env bash
# ИД ИТС 0.11.0: обновление программы из интерфейса (кнопка «Обновление программы»), роли по объектам, схемы, справочники.
# Логин и пароль не меняются. Данные (volume id_nvk_data) сохраняются.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DIR="${ID_ITS_DIR:-/opt/id-its/mvp}"
cd "$DIR"
echo "=== Обновление ИД ИТС до 0.11.0 ==="
if [ ! -s .env ]; then echo "ОШИБКА: не найден .env с логином и паролем. Сначала выполните обновление 0.2.0."; exit 1; fi
for f in app.py start.sh Dockerfile compose.yaml requirements.txt; do [ -s "$SRC/$f" ] || { echo "ОШИБКА: в архиве нет $f"; exit 1; }; done
python3 -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$SRC/app.py"
B="backup-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$B"
cp -f app.py compose.yaml Dockerfile requirements.txt .env "$B"/ 2>/dev/null || true
echo "Резервная копия базы..."
docker compose exec -T id-nvk python -c "import sqlite3;s=sqlite3.connect('/data/id_nvk.sqlite3');d=sqlite3.connect('/data/pre-0.11.0.sqlite3');s.backup(d);d.close();print('копия базы: /data/pre-0.11.0.sqlite3')" || echo "(копия базы не создана — контейнер не запущен, продолжаем)"
docker compose exec -T id-nvk rm -rf /data/app_override || true
mkdir -p seeds inbox
cp -f "$SRC/app.py" app.py; cp -f "$SRC/start.sh" start.sh; cp -f "$SRC/Dockerfile" Dockerfile; cp -f "$SRC/compose.yaml" compose.yaml; cp -f "$SRC/requirements.txt" requirements.txt
cp -f "$SRC"/seeds/*.json seeds/ 2>/dev/null || true
docker compose up -d --build --no-deps id-nvk
echo "Ждём запуска..."
OK=""
for i in $(seq 1 30); do
  sleep 2
  if docker compose exec -T id-nvk python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then OK=1; break; fi
done
URL=$(docker compose logs cloudflared 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
echo
echo "============================================"
if [ -n "$OK" ]; then echo "ПРИЛОЖЕНИЕ ЗАПУЩЕНО (версия 0.11.0)"; else echo "ВНИМАНИЕ: приложение не ответило. Пришлите вывод: docker compose logs --tail=40 id-nvk"; fi
echo "Ссылка: ${URL:-не найдена}"
echo "Логин и пароль прежние."
echo "Новое: дальше программа обновляется загрузкой файла: меню пользователя → «Обновление программы» (консоль больше не нужна). Также: роли по объектам, исполнительные схемы, справочники."
echo "Резервная копия файлов: $DIR/$B"
echo "============================================"
