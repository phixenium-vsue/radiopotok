#!/bin/sh
# RadioPotok (Web) launcher — отдельная лёгкая версия на webapp-container
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$APP_DIR/web"
PORT="${RP_WEB_PORT:-8888}"
URL="http://127.0.0.1:$PORT/"

mkdir -p "$APP_DIR/logs"
LOG="$APP_DIR/logs/web.log"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 300000 ]; then
    mv "$LOG" "$LOG.old"
fi
{
    echo "---- $(date '+%Y-%m-%d %H:%M:%S') web start ----"
} >> "$LOG" 2>&1

# один экземпляр веб-плеера
pkill -f "webapp-container.*$URL" 2>/dev/null
sleep 1

# поднять сервер, если ещё не работает
STARTED_SERVER=0
if ! wget -q -O /dev/null "$URL/api/ping" 2>/dev/null; then
    RP_WEB_PORT="$PORT" python3 "$WEB_DIR/server.py" >> "$LOG" 2>&1 &
    SERVER_PID=$!
    STARTED_SERVER=1
    # дождаться готовности
    i=0
    while [ $i -lt 20 ]; do
        wget -q -O /dev/null "$URL/api/ping" 2>/dev/null && break
        sleep 0.2
        i=$((i+1))
    done
fi

# UA: просим десктопную вёрстку (у нас адаптивная, но пусть тянет полную)
UA="Mozilla/5.0 (Linux; Ubuntu; Mobile) AppleWebKit/537.36 QtWebEngine/5.15.19 Chrome/87.0 Safari/537.36"

exec webapp-container \
    --fullscreen \
    --enable-media-hub-audio \
    --local-content-can-access-remote-urls \
    --store-session-cookies \
    --user-agent-string="$UA" \
    "$URL" >> "$LOG" 2>&1
RC=$?

# при выходе убрать сервер, если поднимали сами
if [ "$STARTED_SERVER" = "1" ] && [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null
fi
exit $RC
