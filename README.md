# RadioPotok (Web)

Лёгкая веб-версия плеера RadioPotok для Ubuntu Touch. Работает локально через Python-вебсервер + `webapp-container`.

## Установка одной строкой (рекомендуется)

```sh
wget -qO- https://raw.githubusercontent.com/phixenium-vsue/radiopotok/main/install.sh | sh
```

После установки в лаунчере Ubuntu Touch появится **RadioPotok (Web)**.

## Удаление

```sh
~/.local/bin/radiopotok-web/uninstall.sh
```

## Ручная установка

1. Скачать `radiopotok-web.tar.gz` с [релизов](https://github.com/phixenium-vsue/radiopotok/releases/latest)
2. Распаковать в `~/.local/bin/radiopotok-web/app/` или использовать `RADIOPOTOK_TARBALL=... ./install.sh`
3. Запустить `~/.local/bin/radiopotok-web/run-web.sh` или ярлык

## Запуск из терминала

```sh
~/.local/bin/radiopotok-web/run-web.sh
```

## Второй способ (Click-пакет)

Планируется отдельным релизом. На данный момент основной способ — установщик выше.

## Лицензия

MIT License. См. [LICENSE](LICENSE).
