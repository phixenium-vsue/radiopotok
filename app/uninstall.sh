#!/bin/sh
# RadioPotok (Web) — удаление установленного приложения.
# Убирает ровно четыре следа (строго имена radiopotok-web):
#   1) папку приложения  2) ярлык в лаунчере
#   3) кэш webapp-container  4) профиль webapp-container
# Тестовый ярлык (radiopotok-web-test) и нативное приложение (radiopotok) не трогает.

DEST="$HOME/.local/bin/radiopotok-web"
APPS="$HOME/.local/share/applications"
DESKTOP="$APPS/radiopotok-web.desktop"
CACHE="$HOME/.cache/radiopotok-web"
SHARE="$HOME/.local/share/radiopotok-web"
URL="http://127.0.0.1:8888/"

echo "== RadioPotok (Web): удаление =="

# --- 1. Остановить приложение целиком, даже если оно зависло и не отображается.
#     Паттерны содержат полный путь установленной папки и адрес :8888 —
#     поэтому тестовый экземпляр (:8889) и нативное приложение не пострадают.
pkill -f "$DEST/run-web.sh" 2>/dev/null
pkill -f "$DEST/web/server.py" 2>/dev/null
pkill -f "webapp-container.*$URL" 2>/dev/null

alive() {
    pgrep -f "$DEST/web/server.py" >/dev/null 2>&1 && return 0
    pgrep -f "$DEST/run-web.sh" >/dev/null 2>&1 && return 0
    pgrep -f "webapp-container.*$URL" >/dev/null 2>&1 && return 0
    return 1
}

# ждём до 10 с, пока процессы отработают и выйдут
i=0
while alive && [ $i -lt 10 ]; do
    sleep 1
    i=$((i + 1))
done
if alive; then
    pkill -9 -f "$DEST/run-web.sh" 2>/dev/null
    pkill -9 -f "$DEST/web/server.py" 2>/dev/null
    pkill -9 -f "webapp-container.*$URL" 2>/dev/null
    sleep 1
    echo "процессы приложения остановлены (принудительно)"
else
    echo "процессы приложения остановлены"
fi

# --- 2. Удалить папку, ярлык и следы профиля
rm -rf "$DEST"
rm -f "$DESKTOP"
rm -rf "$CACHE"
rm -rf "$SHARE"

# --- 3. Проверка: каждая точка отдельно
echo ""
fail=0
for p in "$DEST" "$DESKTOP" "$CACHE" "$SHARE"; do
    if [ -e "$p" ]; then
        echo "  ОСТАЛОСЬ: $p"
        fail=1
    else
        echo "  удалено: $p"
    fi
done
echo ""
if [ "$fail" = "0" ]; then
    if alive; then
        echo "Внимание: процесс приложения ещё жив — закройте его вручную."
    else
        echo "Готово: приложение удалено, мусора нет."
    fi
else
    echo "Что-то не удалилось — покажите этот вывод."
    exit 1
fi
