#!/bin/sh
# Запуск: если через программу загружено обновление (/data/app_override/app.py) — используем его.
# Если новая версия не стартует 3 раза подряд — автоматически возвращаемся к версии из образа.
O=/data/app_override
[ -f /app/app.base.py ] || cp /app/app.py /app/app.base.py
if [ -s "$O/app.py" ]; then
  if [ -f "$O/pending" ]; then
    n=$(cat "$O/boots" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$O/boots"
    if [ "$n" -ge 3 ]; then echo "[start] обновление не запускается — возврат к версии из образа"; rm -f "$O/app.py" "$O/pending" "$O/boots"; cp /app/app.base.py /app/app.py; fi
  fi
  if [ -s "$O/app.py" ]; then cp "$O/app.py" /app/app.py; fi
fi
exec uvicorn app:app --host 0.0.0.0 --port 8000
