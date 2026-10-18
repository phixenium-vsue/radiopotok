#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отдельный (лёгкий) бэкенд для веб-версии RadioPotok.

Служит web/index.html и API:
  GET  /api/stations     -> [[id,name,path], ...]  (только живые, без скрытых)
  GET  /api/favorites    -> [id, ...]
  POST /api/favorites    {id}   -> переключить избранное, вернуть [id,...]
  GET  /api/hidden       -> [id, ...]
  POST /api/hidden       {id}   -> переключить скрытые, вернуть [id,...]
  GET  /api/track?id=NNN -> {raw,artist,title}  (ICY StreamTitle, запасной источник)

Читает/пишет те же файлы приложения, что и QML-версия:
  <app>/stations.json, <app>/data/{favorites,hidden,status}.json
Ничего не делает с backend.py — приложения разделяемы.
"""
import json
import os
import re
import ssl
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIONS = os.path.join(APP, "stations.json")
DATA = os.path.join(APP, "data")

HOST = "0.0.0.0"  # слушаем все интерфейсы (включая loopback) — доступно по сети
PORT = int(os.environ.get("RP_WEB_PORT", "8888"))

_lock = threading.Lock()


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, path)


def _fav_path():
    return os.path.join(DATA, "favorites.json")


def _hid_path():
    return os.path.join(DATA, "hidden.json")


def _status_path():
    return os.path.join(DATA, "status.json")


def _stations_list():
    return _read_json(STATIONS, [])


def _as_pairs(raw):
    """stations.json -> [[id,name,path], ...] (устойчиво к формату объектов)."""
    out = []
    for s in raw:
        if isinstance(s, dict):
            out.append([str(s.get("id", "")), s.get("name", ""), s.get("path", "")])
        elif isinstance(s, (list, tuple)) and len(s) >= 3:
            out.append([str(s[0]), s[1], s[2]])
    return out


def _alive_pairs():
    # Все живые станции (включая скрытые): скрытые остаются в списке с меткой,
    # чтобы ✕ было обратимым (вернуть в ротацию). Из самой ротации их убирает
    # логика колеса/случайного переключения, а не этот список.
    pairs = _as_pairs(_stations_list())
    status = _read_json(_status_path(), {})
    has_status = bool(status)
    out = []
    for pid, name, path in pairs:
        if has_status:
            st = status.get(pid) or {}
            if st.get("status") != "alive":
                continue
        out.append([pid, name, path])
    return out


# ---------------------------------------------------------------- ICY (названия песен, запасной источник «а»)
# Основной источник трека — iframe плеера сайта (WebSocket). Этот раздел — запасной
# для прямых mp3/aac потоков, где есть icy-metaint. Для HLS (m3u8) метаданных нет.
UA = "Mozilla/5.0 (Linux; Ubuntu Touch; RadioPotok)"
RP_BASE = "https://radiopotok.ru"
_ssl = ssl.create_default_context()
_ssl.check_hostname = False
_ssl.verify_mode = ssl.CERT_NONE

_ST_TITLE_RE = re.compile(r"StreamTitle='(.*?)'", re.S)

_mon_lock = threading.Lock()
_mon_id = None
_mon_title = {"raw": "", "artist": "", "title": ""}
_mon_thread = None
_mon_stop = None


def _split_title(t):
    for sep in (" - ", " – ", " — ", " | "):
        if sep in t:
            a, b = t.split(sep, 1)
            return a.strip(), b.strip()
    return "", t.strip()


def _icy_worker(url, stop):
    global _mon_title
    try:
        h = {"User-Agent": UA, "Referer": RP_BASE + "/", "Icy-MetaData": "1"}
        req = urllib.request.Request(url, headers=h)
        r = urllib.request.urlopen(req, context=_ssl, timeout=15)
        try:
            metaint = r.headers.get("icy-metaint")
            if not metaint:
                return
            metaint = int(metaint)
            last = ""
            while not stop.is_set():
                left = metaint
                while left > 0 and not stop.is_set():
                    chunk = r.read(min(8192, left))
                    if not chunk:
                        return
                    left -= len(chunk)
                ln = r.read(1)
                if not ln:
                    return
                blen = ln[0] * 16
                if blen == 0:
                    continue
                data = r.read(blen).decode("utf-8", "ignore")
                m = _ST_TITLE_RE.search(data)
                if not m:
                    continue
                raw = m.group(1).strip()
                if raw and raw != last and raw.lower() not in ("unknown", "none"):
                    last = raw
                    artist, title = _split_title(raw)
                    with _mon_lock:
                        _mon_title = {"raw": raw, "artist": artist, "title": title}
        finally:
            r.close()
    except Exception:
        return


def _stop_monitor():
    global _mon_thread, _mon_stop, _mon_id
    if _mon_stop is not None:
        _mon_stop.set()
    _mon_thread = None
    _mon_stop = None
    _mon_id = None


def _start_monitor(sid, url):
    global _mon_thread, _mon_stop, _mon_id, _mon_title
    _stop_monitor()
    with _mon_lock:
        _mon_title = {"raw": "", "artist": "", "title": ""}
    _mon_id = sid
    _mon_stop = threading.Event()
    _mon_thread = threading.Thread(target=_icy_worker, args=(url, _mon_stop), daemon=True)
    _mon_thread.start()


def get_track(sid):
    """Текущий трек для station id: ICY StreamTitle прямого потока (запасной «а»)."""
    status = _read_json(_status_path(), {})
    st = status.get(sid) or {}
    url = st.get("stream") or ""
    if not url:
        return {"raw": "", "artist": "", "title": ""}
    alive = _mon_thread is not None and _mon_thread.is_alive()
    if sid != _mon_id or not alive:
        _start_monitor(sid, url)
    with _mon_lock:
        return dict(_mon_title)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # тихий режим

    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) or {}
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_index()
        if path == "/api/stations":
            return self._send(200, _alive_pairs())
        if path == "/api/favorites":
            return self._send(200, _read_json(_fav_path(), []))
        if path == "/api/hidden":
            return self._send(200, _read_json(_hid_path(), []))
        if path == "/api/track":
            return self._send(200, get_track(self._qs("id")))
        if path == "/api/ping":
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def _qs(self, key):
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        for kv in q.split("&"):
            k, _, v = kv.partition("=")
            if k == key:
                return v.strip()
        return ""

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        data = self._read_body()
        sid = str(data.get("id", "")).strip()
        if not sid:
            return self._send(400, {"error": "no id"})
        if path == "/api/favorites":
            return self._toggle_list(_fav_path(), sid)
        if path == "/api/hidden":
            return self._toggle_list(_hid_path(), sid)
        return self._send(404, {"error": "not found"})

    def _toggle_list(self, path, sid):
        with _lock:
            cur = [str(x) for x in _read_json(path, [])]
            if sid in cur:
                cur = [x for x in cur if x != sid]
            else:
                cur.append(sid)
            _write_json(path, cur)
        return self._send(200, cur)

    def _serve_index(self):
        idx = os.path.join(os.path.dirname(__file__), "index.html")
        try:
            with open(idx, "rb") as f:
                body = f.read()
        except OSError:
            return self._send(404, {"error": "index.html missing"})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
