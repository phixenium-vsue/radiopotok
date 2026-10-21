#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Каталог radiopotok.ru: получение списка станций и проверка «вставляемости».

Используется веб-бэкендом (server.py) для:
  * fetch_catalog()   — актуальный список всех станций (порядок как на сайте);
  * fetch_embeddable()— id станций, для которых сайт публикует вставку;
  * check_station()   — играбелен ли прямой поток станции (для «Отсеять нерабочие»).

Страницы не сохраняем: разбираем ответ сервера в память.
"""
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://radiopotok.ru"
UA = "Mozilla/5.0 (Linux; Ubuntu Touch; RadioPotok)"
BROWSER_UA = ("Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36")
MOAR_URL = BASE + "/radio/moar_list"
EMBEDDABLE_URL = BASE + "/radio_on_site"
MOAR_PAGE_SIZE = 24

STATIONS_RE = re.compile(r"STATIONS\[(\d+)\]\s*=\s*(\{.*?\});", re.S)
WRAPPER_RE = re.compile(r"js--radio-player-list\s+js--radio-(\d+)\"")
A_TAG_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S)
HREF_RE = re.compile(r"\bhref=\"([^\"]+)\"")
CHECKBOX_RE = re.compile(r"name=\"script_stations\[\]\"\s+value=\"(\d+)\"")
TAG_RE = re.compile(r"<[^>]+>")
STREAM_RE = re.compile(r'"stream"\s*:\s*"((?:\\.|[^"\\])*)"')
YANDEX_RE = re.compile(r'"stream_yandex"\s*:\s*"((?:\\.|[^"\\])*)"')

_ssl = ssl.create_default_context()
_ssl.check_hostname = False
_ssl.verify_mode = ssl.CERT_NONE


def http(url, data=None, timeout=30, method=None, headers=None):
    h = {"User-Agent": UA, "Referer": BASE + "/"}
    if headers:
        h.update(headers)
    if data is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_items(text):
    """HTML каталога -> [{"id","name","path"}] в порядке появления."""
    store, order = {}, []

    def put(sid, name, url, anchored):
        sid = str(sid)
        if not sid:
            return
        cur = store.get(sid)
        if cur is None:
            store[sid] = {"id": sid, "name": (name or "").strip(),
                          "path": (url or "").strip(), "anch": bool(anchored)}
            order.append(sid)
            return
        if anchored and not cur["anch"]:
            if name:
                cur["name"] = name.strip()
            if url:
                cur["path"] = url.strip()
            cur["anch"] = True
        else:
            if not cur["name"] and name:
                cur["name"] = name.strip()
            if not cur["path"] and url:
                cur["path"] = url.strip()

    # 1) явные JSON-объекты STATIONS[id] = {...}
    for sid, blob in STATIONS_RE.findall(text):
        try:
            raw = json.loads(blob)
        except ValueError:
            continue
        put(sid, raw.get("name") or "", raw.get("url") or "", False)

    # 2) заголовки-ссылки (резерв для строк без JSON)
    spans = [(m.group(1), m.start()) for m in WRAPPER_RE.finditer(text)]
    for pos, (sid, start) in enumerate(spans):
        end = spans[pos + 1][1] if pos + 1 < len(spans) else len(text)
        block = text[start:end]
        for tag in A_TAG_RE.finditer(block):
            if "radio-list__name" not in tag.group(1):
                continue
            href = HREF_RE.search(tag.group(1))
            url = href.group(1) if href else ""
            put(sid, TAG_RE.sub("", tag.group(2)), url, True)
            break

    return [store[sid] for sid in order if store[sid]["name"] and store[sid]["path"]]


def fetch_catalog(cancel=None, progress=None, delay=0.12):
    """Весь каталог постранично. progress(page, total, fresh)."""
    store, order, page = {}, [], 1
    while True:
        if cancel and cancel.is_set():
            break
        data = urllib.parse.urlencode({"page": page}).encode("ascii")
        items = parse_items(http(MOAR_URL, data=data))
        fresh = 0
        for it in items:
            if it["id"] not in store:
                fresh += 1
                store[it["id"]] = {"id": it["id"], "name": it["name"], "path": it["path"]}
                order.append(it["id"])
        if progress:
            progress(page, len(order), fresh)
        if not items or fresh == 0 or len(items) < MOAR_PAGE_SIZE:
            break
        page += 1
        time.sleep(delay)
    return [store[sid] for sid in order]


def fetch_embeddable():
    """Множество id со страницы вставок, либо None если страница недоступна."""
    try:
        text = http(EMBEDDABLE_URL)
    except Exception:
        return None
    ids = set(CHECKBOX_RE.findall(text))
    return ids or None


def _decode(raw):
    """Значение поля вида "stream":"\\u005b...\\u005d" -> восстановленная строка."""
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return None


def _first_files(text):
    """(список url потоков, непустой stream_yandex?) из RP_RADIO скрипта."""
    files = []
    m = STREAM_RE.search(text)
    if m:
        val = _decode(m.group(1))
        try:
            files = [str(f.get("file") or "").strip()
                     for f in json.loads(val)
                     if isinstance(f, dict) and str(f.get("file") or "").strip()]
        except (ValueError, TypeError):
            files = []
    yandex = ""
    m = YANDEX_RE.search(text)
    if m:
        yandex = (_decode(m.group(1)) or "").strip()
    return files, yandex


def _probe_once(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Referer": BASE + "/",
        "Accept": "*/*", "Icy-MetaData": "1"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout,
                                      context=ssl.create_default_context())
        ct = (resp.headers.get("Content-Type") or "").lower()
        head = resp.read(512)
        if url.split("?")[0].lower().endswith(".m3u8") or "mpegurl" in ct:
            return ("alive", "") if head.lstrip()[:7] == b"#EXTM3U" else ("dead", "html")
        if ct.startswith("text/") or head[:1] == b"<":
            return "dead", "html"
        if (ct.startswith(("audio/", "video/", "application/ogg"))
                or head[:3] in (b"ID3", b"ICY") or head[:1] == b"\xff"
                or head[:4] == b"HTTP"):
            return "alive", ""
        return "unknown", "ct:" + ct[:24]
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return "dead", "http%d" % e.code
        return "unknown", "http%d" % e.code
    except ssl.SSLError:
        return "dead", "ssl"
    except socket.gaierror:
        return "dead", "dns"
    except (socket.timeout, TimeoutError):
        return "unknown", "timeout"
    except ConnectionRefusedError:
        return "unknown", "refused"
    except urllib.error.URLError as e:
        r = e.reason
        if isinstance(r, socket.gaierror):
            return "dead", "dns"
        if isinstance(r, ssl.SSLError):
            return "dead", "ssl"
        if isinstance(r, ConnectionRefusedError):
            return "unknown", "refused"
        if isinstance(r, (socket.timeout, TimeoutError)):
            return "unknown", "timeout"
        return "unknown", "urlerr"
    except Exception:
        return "unknown", "error"


def _probe(url, timeout=8, attempts=2):
    """Как _probe_once, но неоднозначный результат повторяем (борьба с флаки-таймаутами)."""
    verdict, reason = _probe_once(url, timeout)
    if verdict == "unknown" and attempts > 1:
        verdict, reason = _probe_once(url, timeout)
    return verdict, reason


def check_station(sid):
    """('alive'|'dead'|'unknown', причина) — играбелен ли поток станции.

    dead — только достоверные отказы: 404/410, мёртвый DNS, SSL, вместо аудио HTML,
    либо пустой stream без yandex. Всё неоднозначное (таймаут/отказ/5xx/403) — unknown.
    """
    try:
        text = http("%s/f/script6.1/%s.js" % (BASE, sid),
                    headers={"Range": "bytes=0-16383"})
    except urllib.error.HTTPError as e:
        return ("dead", "script404") if e.code in (404, 410) else ("unknown", "script")
    except Exception:
        return "unknown", "script"
    if "RP_RADIO" not in text:
        return "unknown", "script"
    files, yandex = _first_files(text)
    if not files:
        return ("unknown", "yandex-only") if yandex else ("dead", "empty")
    first_dead = None
    saw_unknown = False
    for url in files:
        verdict, reason = _probe(url)
        if verdict == "alive":
            return "alive", ""
        if verdict == "dead" and first_dead is None:
            first_dead = reason
        elif verdict == "unknown":
            saw_unknown = True
    if saw_unknown or first_dead is None:
        return "unknown", "unreachable"
    return "dead", first_dead
