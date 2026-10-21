#!/bin/sh
# Собирает radiopotok-setup.run — офлайн-самоустановщик (один файл).
# Внутри: sh-заголовок + метка __PAYLOAD__ + gzip-тарболл {install.sh, radiopotok-web.tar.gz}.
# Запуск на устройстве: sh radiopotok-setup.run  (сеть не нужна, ставит по стандартным путям)
set -e
cd "$(dirname "$0")"

OUT="radiopotok-setup.run"
[ -f install.sh ] || { echo "нет install.sh"; exit 1; }
[ -f radiopotok-web.tar.gz ] || { echo "нет radiopotok-web.tar.gz"; exit 1; }

STUB="$(mktemp)"
PAYD="$(mktemp -d)"
trap 'rm -f "$STUB" "$PAYD.tarball"; rm -rf "$PAYD"' EXIT INT TERM

# --- заголовок (выполняется как sh-скрипт; метка — последняя строка заголовка)
cat > "$STUB" <<'STUB_EOF'
#!/bin/sh
# RadioPotok (Web) — самоустановщик. Офлайн, без бинарников: только sh + gzip + tar.
# Использование: sh radiopotok-setup.run
set -e
SELF="$0"
LINE="$(LC_ALL=C grep -a -n -m1 '^__PAYLOAD__$' "$SELF" | cut -d: -f1)"
[ -n "$LINE" ] || { echo "ошибка: повреждённый файл (нет метки payload)"; exit 1; }
PAY="$(mktemp -d -t radiopotok-setup-XXXX)"
cleanup() { rm -rf "$PAY"; }
trap cleanup EXIT INT TERM
tail -n "+$((LINE + 1))" "$SELF" | tar xzf - -C "$PAY"
echo "Найден пакет: install.sh + radiopotok-web.tar.gz"
RADIOPOTOK_TARBALL="$PAY/radiopotok-web.tar.gz" sh "$PAY/install.sh"
exit 0
__PAYLOAD__
STUB_EOF

sh -n "$STUB"

# --- payload
cp install.sh radiopotok-web.tar.gz "$PAYD/"
( cd "$PAYD" && tar czf - install.sh radiopotok-web.tar.gz ) > "$PAYD.tarball"

cat "$STUB" "$PAYD.tarball" > "$OUT"
chmod 755 "$OUT"
echo "Собрано: $OUT ($(wc -c < "$OUT") байт)"
