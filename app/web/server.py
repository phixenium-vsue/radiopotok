#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отдельный (лёгкий) бэкенд для веб-версии RadioPotok.

Служит web/index.html и API:
  GET  /api/stations     -> [[id,name,path], ...]  (все станции; скрытые отсекает ротация)
  GET  /api/favorites    -> [id, ...]
  POST /api/favorites    {id}   -> переключить избранное, вернуть [id,...]
  GET  /api/hidden       -> [id, ...]
  POST /api/hidden       {id}   -> переключить скрытые, вернуть [id,...]
  POST /api/purge-hidden        -> вырезать скрытые из stations.json (резерв .orig), вернуть {removed,total}
  POST /api/refresh             -> обновить список всех станций с сайта (фоновая задача)
  POST /api/scan                -> отсеять нерабочие (фоновая задача, скрывает 100% мёртвые)
  GET  /api/refresh-status      -> прогресс обновления
  GET  /api/scan-status         -> прогресс проверки
  POST /api/{refresh,scan}-cancel -> отмена
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
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import catalog

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATIONS = os.path.join(APP, "stations.json")
ANCHOR = os.path.join(APP, "stations.full.json")  # якорный резерв полного списка
DATA = os.path.join(APP, "data")

HOST = "0.0.0.0"  # слушаем все интерфейсы (включая loopback) — доступно по сети
PORT = int(os.environ.get("RP_WEB_PORT", "8888"))

_lock = threading.Lock()

# ---- фоновая задача (обновление списка / проверка станций) ----
_job_lock = threading.Lock()
_job_cancel = threading.Event()
_job_thread = None
_job = {"kind": None, "running": False, "phase": "", "note": "", "page": 0,
        "done": 0, "total": 0, "alive": 0, "gone": 0, "hidden": 0,
        "unknown": 0, "current": "", "error": "", "result": None}


def _job_snapshot():
    with _job_lock:
        return dict(_job)


def _job_set(**kw):
    with _job_lock:
        _job.update(kw)


def _job_start(kind, target):
    global _job_thread
    with _job_lock:
        if _job["running"]:
            return False
        _job_cancel.clear()
        _job.update({"kind": kind, "running": True, "phase": "start", "note": "",
                     "page": 0, "done": 0, "total": 0, "alive": 0, "gone": 0,
                     "hidden": 0, "unknown": 0, "current": "", "error": "",
                     "result": None})
    _job_thread = threading.Thread(target=target, daemon=True)
    _job_thread.start()
    return True


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


def _refresh_worker():
    try:
        _job_set(phase="catalog", note="Получаем список станций с сайта")
        items = catalog.fetch_catalog(
            cancel=_job_cancel,
            progress=lambda page, total, fresh: _job_set(page=page, done=total))
        if _job_cancel.is_set():
            return _job_set(phase="cancelled", note="Обновление отменено")
        _job_set(phase="embed", note="Проверяем вставляемость", page=0, done=0)
        embeddable = catalog.fetch_embeddable()
        # Каталог — источник истины: список НЕ сокращаем по вставляемости
        # (страница вставок может отдать неполный набор и «съесть» рабочие станции).
        # Вставляемые id лишь добавляем, если их вдруг нет в каталоге.
        have = {it["id"] for it in items}
        items = list(items)
        if embeddable:
            for sid in embeddable:
                if sid not in have:
                    items.append({"id": sid, "name": "Станция %s" % sid,
                                  "path": "/radio/%s" % sid})
        if not items:
            return _job_set(phase="error", error="каталог пуст")
        new_list = [{"id": it["id"], "name": it["name"], "path": it["path"]} for it in items]
        ids = {it["id"] for it in new_list}
        with _lock:
            _write_json(STATIONS, new_list)
            try:
                import shutil
                shutil.copyfile(STATIONS, ANCHOR)
            except OSError:
                pass
            status = _read_json(_status_path(), {})
            pruned = {k: v for k, v in status.items() if str(k) in ids}
            if pruned != status:
                _write_json(_status_path(), pruned)
            # Полное обновление возвращает ВСЕ исключённые станции в список и ротацию:
            # за месяц-полгода часть «мёртвых» может ожить, поэтому сбрасываем скрытых.
            _write_json(_hid_path(), [])
            _write_json(os.path.join(DATA, "scan.json"), {})
        _job_set(phase="done", done=len(new_list), total=len(new_list),
                 note="Список обновлён: %d станций" % len(new_list),
                 result={"total": len(new_list)})
    except Exception as e:
        _job_set(phase="error", error=str(e))
    finally:
        with _job_lock:
            _job["running"] = False


def _scan_worker():
    try:
        pairs = _as_pairs(_stations_list())
        total = len(pairs)
        _job_set(phase="scan", done=0, total=total, alive=0, gone=0, hidden=0,
                 unknown=0, current="", note="Проверка станций")
        dead, alive, unknown, done, reasons = [], 0, 0, 0, {}

        def check(pair):
            if _job_cancel.is_set():
                return pair[0], ("cancelled", "")
            return pair[0], catalog.check_station(pair[0])

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(check, p) for p in pairs]
            for fut in as_completed(futs):
                sid, (verdict, reason) = fut.result()
                if verdict == "cancelled" or _job_cancel.is_set():
                    break
                done += 1
                if verdict == "alive":
                    alive += 1
                elif verdict == "dead":
                    dead.append(sid)
                    reasons[sid] = reason
                else:
                    unknown += 1
                _job_set(done=done, alive=alive, hidden=len(dead),
                         unknown=unknown, current=sid)
        cancelled = _job_cancel.is_set()
        with _lock:
            hid = [str(x) for x in _read_json(_hid_path(), [])]
            for sid in dead:
                if sid not in hid:
                    hid.append(sid)
            if dead:
                _write_json(_hid_path(), hid)
            _write_json(os.path.join(DATA, "scan.json"), {
                "ts": int(time.time()), "total": total, "alive": alive,
                "dead": len(dead), "unknown": unknown, "cancelled": cancelled,
                "dead_ids": dead, "reasons": reasons})
        _job_set(phase="cancelled" if cancelled else "done",
                 note="Скрыто нерабочих: %d" % len(dead),
                 result={"dead": len(dead), "hidden": len(hid)})
    except Exception as e:
        _job_set(phase="error", error=str(e))
    finally:
        with _job_lock:
            _job["running"] = False


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
            return self._send(200, _as_pairs(_stations_list()))
        if path == "/api/favorites":
            return self._send(200, _read_json(_fav_path(), []))
        if path == "/api/hidden":
            return self._send(200, _read_json(_hid_path(), []))
        if path == "/api/track":
            return self._send(200, get_track(self._qs("id")))
        if path in ("/api/refresh-status", "/api/scan-status"):
            return self._send(200, _job_snapshot())
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
        if path == "/api/purge-hidden":
            return self._purge_hidden()
        if path in ("/api/refresh", "/api/scan"):
            kind = path.rsplit("/", 1)[1]
            ok = _job_start(kind, _refresh_worker if kind == "refresh" else _scan_worker)
            return self._send(200 if ok else 409, {"started": ok, "job": _job_snapshot()})
        if path in ("/api/refresh-cancel", "/api/scan-cancel"):
            _job_cancel.set()
            return self._send(200, {"cancelled": True})
        sid = str(data.get("id", "")).strip()
        if not sid:
            return self._send(400, {"error": "no id"})
        if path == "/api/favorites":
            return self._toggle_list(_fav_path(), sid)
        if path == "/api/hidden":
            return self._toggle_list(_hid_path(), sid)
        return self._send(404, {"error": "not found"})

    def _purge_hidden(self):
        """Удаляет скрытые станции из stations.json.

        Оригинал один раз сохраняется как stations.full.json (якорный резерв),
        hidden.json очищается. Возвращает счётчики.
        """
        with _lock:
            raw = _as_pairs(_stations_list())
            hidden = set(str(x) for x in _read_json(_hid_path(), []))
            if not hidden:
                return self._send(200, {"removed": 0, "total": len(raw)})
            keep = [p for p in raw if p[0] not in hidden]
            removed = len(raw) - len(keep)
            orig = ANCHOR
            if not os.path.exists(orig):
                try:
                    with open(STATIONS, "rb") as fsrc, open(orig, "wb") as fdst:
                        fdst.write(fsrc.read())
                except OSError:
                    pass  # резерв копия best-effort: чистка важнее
            _write_json(STATIONS, keep)
            _write_json(_hid_path(), [])
        return self._send(200, {"removed": removed, "total": len(keep)})

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
