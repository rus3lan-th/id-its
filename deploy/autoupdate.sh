#!/bin/bash
# ИД ИТС: автообновление программы. Раз в минуту (cron) забирает app.py из Google Drive и, если версия новее запущенной, устанавливает.
# Установка: см. инструкцию в чате / README. Журнал: /opt/id-its/autoupdate/update.log
URL="${ID_UPDATE_URL:-https://drive.usercontent.google.com/download?confirm=t&export=download&id=1A3bIpXiM-saRdQu_6xJO5IkPVNmcgU1z}"
CONT="${ID_CONTAINER:-id-nvk-mvp}"
HEALTH="${ID_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
DIR="${ID_UPDATE_DIR:-/opt/id-its/autoupdate}"
mkdir -p "$DIR"; LOG="$DIR/update.log"
exec 9>"$DIR/lock"; flock -n 9 || exit 0
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
[ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 500000 ] && tail -n 300 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
tmp=$(mktemp "$DIR/dl.XXXXXX"); trap 'rm -f "$tmp"' EXIT
chmod 644 "$tmp"

curl -fsSL --max-time 60 -o "$tmp" "$URL" 2>/dev/null || exit 0          # нет сети или Drive недоступен — тихо ждём следующей минуты
[ "$(stat -c%s "$tmp")" -gt 100000 ] || exit 0                           # страница ошибки вместо файла

new=$(python3 - "$tmp" <<'PY'
import ast, re, sys
t = open(sys.argv[1], encoding="utf-8", errors="strict").read()
ast.parse(t)
assert "FastAPI(" in t and "/api/health" in t
m = re.search(r'FastAPI\(title="[^"]*",\s*version="([\d.]+)"', t)
print(m.group(1) if m else "")
PY
) || { log "ПРОПУСК: файл не прошёл проверку (не Python или повреждён)"; exit 0; }
[ -n "$new" ] || { log "ПРОПУСК: в файле нет версии"; exit 0; }

cur=$(curl -fsS --max-time 10 "$HEALTH" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
[ -n "$cur" ] || { log "ПРОПУСК: программа сейчас не отвечает, обновление отложено"; exit 0; }

newer=$(python3 -c 'import sys
v=lambda s: tuple(int(x) for x in s.split("."))
print(1 if v(sys.argv[1]) > v(sys.argv[2]) else 0)' "$new" "$cur" 2>/dev/null)
[ "$newer" = "1" ] || exit 0

log "НАЙДЕНО обновление: $cur -> $new"
docker exec "$CONT" sh -c "mkdir -p /data/app_backups /data/app_override && cp /app/app.py /data/app_backups/app-$cur-\$(date +%Y%m%d-%H%M%S).py" >> "$LOG" 2>&1
docker cp "$tmp" "$CONT:/data/app_override/app.py" >> "$LOG" 2>&1 || { log "ОШИБКА: не удалось скопировать файл в контейнер"; exit 1; }
docker exec "$CONT" sh -c 'rm -f /data/app_override/boots; date > /data/app_override/pending' >> "$LOG" 2>&1
docker restart "$CONT" >> "$LOG" 2>&1
for i in $(seq 1 30); do
  sleep 3
  v=$(curl -fsS --max-time 5 "$HEALTH" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version",""))' 2>/dev/null)
  if [ "$v" = "$new" ]; then log "ГОТОВО: работает версия $new"; exit 0; fi
done
log "ВНИМАНИЕ: версия $new не ответила за 90 секунд; при трёх неудачных запусках контейнер сам вернётся к прежней версии"
exit 1
