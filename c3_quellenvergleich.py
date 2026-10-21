#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C3-Quellenvergleich-Sonde (Diagnose, rein lesend)  -  Stand 17.10.2026, Version c3 v1.1

ZWECK
  Messen, WANN fuer Krypto (UTC-Tagesschluss) und Non-US-Aktien (regulaerer Boersenschluss)
  ein Tagesschlusskurs aus welcher Quelle verfuegbar ist, und wie er sich spaeter zum
  fertigen Yahoo-Tagesschluss verhaelt. Gemessen werden Verfuegbarkeit und Plausibilitaet,
  NICHT die Austauschbarkeit der Kurse (andere Handelsplaetze, Quote-Waehrungen).

MODI
  krypto   Quellen: Yahoo-Tageszeile (wie Aggregator: fetch_batch-Abruf), Binance (USDT-Paar),
           Kraken (USD-Paar), Coinbase Exchange (USD-Paar), jeweils die letzten 3 Tageskerzen.
  nonus    Quellen: Yahoo-Tageszeile (letzte 3 Zeilen) und Yahoo-Intraday (15 Min., ohne
           prepost, letzte 4 Balken) mit Zuordnung regulaere Sitzung / nicht eindeutig.

BEFEHLE
  trockenlauf  Pruefung der Logik OHNE Netz (Netz wird blockiert). Schreibt nur in ein Temp-Verzeichnis.
  kurztest     EIN Messpunkt jetzt, mit Netz (nur lesende Abrufe), Konsolen-Zusammenfassung.
  serie        Messreihe mit einem oder mehreren Fenstern (alles UTC), je Messpunkt ein frischer Prozess.
  auswerten    Auswertung einer oder mehrerer fertiger Serien.

AENDERUNG v1.1 gegenueber v1
  Nur die Auswertung (nonus): zusaetzlich der letzte Balken NACH der angenommenen Schlusszeit
  (z. B. Schlussauktion, in den Kurztests bei UCB.BR 17:30 und ORG.AX 16:00 beobachtet) mit
  Zeitpunkt der ersten Sichtung, Schlusskurs und Abweichung zum Yahoo-Tagesschluss. Der
  Abrufcode (Quellen, Endpunkte, Felder) ist gegenueber v1 unveraendert.

GRENZEN
  Keine Aenderung an Aggregator, Worker, Generator, KV, Archiv, SUITE.md, Workflows.
  Keine Konten, keine API-Schluessel, keine Geheimnisse (nur oeffentliche Endpunkte).
  Schreibt nur nach --out-dir (Standard ~/uiq-c2-lokal), verweigert den Lauf in einem
  Git-Arbeitsverzeichnis. Kein Hostname, Benutzername oder Pfad in den Dateien.
  Die Boersen-Endpunkte und Paarnamen stammen aus allgemeinem Wissen, nicht aus der Dokumentation
  geprueft; ein Fehlschlag wird protokolliert (HTTP-Status, Fehlerklasse), nie als Datenbefund gewertet.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCHEMA = "c3_quellenvergleich/1"
VERSION = "c3 v1.1 (17.10.2026)"
UA = "uiq-c3-probe/1 (read-only diagnostic)"
FIELDS = [
    "schema", "run_id", "mode", "slot_utc", "fetch_utc", "fetch_ms", "ticker", "source", "ident",
    "status", "http_status", "error_class", "error_text", "rec_kind", "rec_key", "rec_local",
    "open", "high", "low", "close", "volume", "is_closed", "session_class", "flags", "ctx",
    "raw_excerpt",
]
CRYPTO_SOURCES = ("yahoo", "binance", "kraken", "coinbase")
NONUS_SOURCES = ("yahoo_daily", "yahoo_intraday")
ENDPOINTS = {
    "binance": "https://api.binance.com/api/v3/klines?symbol={pair}&interval=1d&limit=3",
    "kraken": "https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440",
    "coinbase": "https://api.exchange.coinbase.com/products/{pair}/candles?granularity=86400",
    "yahoo": "yfinance yf.download(ticker, interval='1d', start=jetzt-730d, end=heutiges UTC-Datum exklusiv)",
    "yahoo_daily": "wie yahoo",
    "yahoo_intraday": "yfinance yf.download(ticker, period='5d', interval='15m', prepost=False)",
}
INTRADAY_MIN = 15


# ---------------------------------------------------------------------------- Hilfen
def _num(x):
    try:
        if x is None or x != x:
            return ""
        return repr(float(x))
    except Exception:
        return ""


def _isnan(x):
    try:
        return bool(x is None or x != x)
    except Exception:
        return True


def _in_git(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return True
        q = os.path.dirname(p)
        if q == p:
            return False
        p = q


def _utc(s):
    s = s.strip().replace("Z", "")
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def slots_for(windows):
    """windows: Liste (first, end, interval_s). Erstlauf + auf Vielfache des Intervalls ab
    UTC-Mitternacht ausgerichtete Slots bis einschliesslich end; alle Fenster vereinigt."""
    out = set()
    for first, end, iv in windows:
        out.add(first)
        midnight = first.replace(hour=0, minute=0, second=0, microsecond=0)
        k = int((first - midnight).total_seconds() // iv) + 1
        while True:
            t = midnight + dt.timedelta(seconds=k * iv)
            if t > end:
                break
            out.add(t)
            k += 1
    return sorted(out)


def parse_window(s):
    parts = s.split(",")
    if len(parts) != 3:
        sys.exit(f"ABBRUCH: Fenster '{s}' nicht im Format START,ENDE,MINUTEN.")
    return _utc(parts[0]), _utc(parts[1]), int(round(float(parts[2]) * 60))


def load_watchlist(path, mode):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    rows = [r for r in rows if r.get("sym")]
    if not rows:
        sys.exit("ABBRUCH: Watchlist leer.")
    need = {"krypto": ["sym", "binance", "kraken", "coinbase"],
            "nonus": ["sym", "tz", "open_local", "close_local"]}[mode]
    for c in need:
        if c not in rows[0]:
            sys.exit(f"ABBRUCH: Spalte '{c}' fehlt in der Watchlist fuer Modus {mode}.")
    return rows


# ------------------------------------------------------------------------- Ergebnisse
def res(status, http="", eclass="", etext="", recs=None, raw=""):
    return {"status": status, "http_status": http, "error_class": eclass,
            "error_text": str(etext)[:200].replace("\n", " ").replace("\r", " "),
            "recs": recs or [], "raw": str(raw)[:500].replace("\n", " ")}


def rec(kind, key, o="", h="", l="", c="", v="", closed="", local="", sess="", flags=None):
    return {"kind": kind, "key": key, "local": local, "o": _num(o), "h": _num(h), "l": _num(l),
            "c": _num(c), "v": _num(v), "closed": closed, "sess": sess,
            "flags": "|".join(flags or [])}


# ------------------------------------------------------------------------------ HTTP
def http_get(url, timeout=20):
    """-> (status|None, body, fehlertext). Nur GET, oeffentliche Endpunkte, kein Schluessel."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, ""
    except Exception as e:
        return None, "", f"{type(e).__name__}: {str(e)[:150]}"


HTTP = {"get": http_get}   # im Trockenlauf ersetzt


def _problem(st, body, err):
    if st is None:
        return res("network_error", "", "network", err)
    if st in (451, 403):
        return res("geo_blocked", st, "geo_oder_verboten", body[:120], raw=body)
    if st in (429, 418):
        return res("rate_limited", st, "rate_limit", body[:120], raw=body)
    return None


# ------------------------------------------------------------------------ Boersen-APIs
def _flag_align(t):
    return [] if (t.hour == 0 and t.minute == 0 and t.second == 0) else ["nicht_UTC_ausgerichtet"]


def fetch_binance(pair, now, row=None):
    st, body, err = HTTP["get"](ENDPOINTS["binance"].format(pair=pair))
    p = _problem(st, body, err)
    if p:
        return p
    if st == 400 and "-1121" in body:
        return res("pair_unsupported", st, "invalid_symbol", body[:120], raw=body)
    if st != 200:
        return res("http_error", st, "http", body[:120], raw=body)
    try:
        arr = json.loads(body)
        out = []
        for k in arr[-3:]:
            t = dt.datetime.fromtimestamp(int(k[0]) / 1000, dt.timezone.utc)
            closed = (int(k[6]) / 1000.0) < now.timestamp()
            out.append(rec("candle", _iso(t), k[1], k[2], k[3], k[4], k[5], closed, flags=_flag_align(t)))
    except Exception as e:
        return res("parse_error", st, type(e).__name__, str(e), raw=body)
    if not out:
        return res("empty", st, "leer", "", raw=body)
    return res("ok", st, raw=body[-400:], recs=out)


def fetch_kraken(pair, now, row=None):
    st, body, err = HTTP["get"](ENDPOINTS["kraken"].format(pair=pair))
    p = _problem(st, body, err)
    if p:
        return p
    if st != 200:
        return res("http_error", st, "http", body[:120], raw=body)
    try:
        j = json.loads(body)
        if j.get("error"):
            msg = ";".join(str(x) for x in j["error"])
            if "Unknown asset pair" in msg:
                return res("pair_unsupported", st, "unknown_pair", msg, raw=body)
            if "Too many" in msg or "Rate limit" in msg:
                return res("rate_limited", st, "rate_limit", msg, raw=body)
            return res("api_error", st, "api_error", msg, raw=body)
        result = j["result"]
        keys = [k for k in result if k != "last"]
        arr = result[keys[0]]
        out = []
        for k in arr[-3:]:
            t = dt.datetime.fromtimestamp(int(k[0]), dt.timezone.utc)
            closed = (int(k[0]) + 86400) <= now.timestamp()
            out.append(rec("candle", _iso(t), k[1], k[2], k[3], k[4], k[6], closed, flags=_flag_align(t)))
    except Exception as e:
        return res("parse_error", st, type(e).__name__, str(e), raw=body)
    if not out:
        return res("empty", st, "leer", "", raw=body)
    return res("ok", st, raw=body[-400:], recs=out)


def fetch_coinbase(pair, now, row=None):
    st, body, err = HTTP["get"](ENDPOINTS["coinbase"].format(pair=pair))
    p = _problem(st, body, err)
    if p:
        return p
    if st == 404:
        return res("pair_unsupported", st, "not_found", body[:120], raw=body)
    if st != 200:
        return res("http_error", st, "http", body[:120], raw=body)
    try:
        arr = json.loads(body)             # [time, low, high, open, close, volume], neueste zuerst
        out = []
        for k in reversed(arr[:3]):
            t = dt.datetime.fromtimestamp(int(k[0]), dt.timezone.utc)
            closed = (int(k[0]) + 86400) <= now.timestamp()
            out.append(rec("candle", _iso(t), k[3], k[2], k[1], k[4], k[5], closed, flags=_flag_align(t)))
    except Exception as e:
        return res("parse_error", st, type(e).__name__, str(e), raw=body)
    if not out:
        return res("empty", st, "leer", "", raw=body)
    return res("ok", st, raw=body[:400], recs=out)


# ------------------------------------------------------------------------------ Yahoo
def _flat(df):
    if df is not None and hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    return df


def df_to_daily(df, attempt):
    """Letzte 3 Tageszeilen als Datensaetze. Zeilendatum = Indexdatum (Krypto: UTC-Tag)."""
    out = []
    for idx in range(max(0, len(df) - 3), len(df)):
        row = df.iloc[idx]
        vals = {c: (row[c] if c in df.columns else float("nan")) for c in ("Open", "High", "Low", "Close", "Volume")}
        nan = [c for c, x in vals.items() if _isnan(x)]
        flags = ["versuch=" + attempt]
        if _isnan(vals["Close"]):
            flags.append("close_nan")
        if nan:
            flags.append("nan=" + "+".join(nan))
        out.append(rec("row", str(df.index[idx])[:10], vals["Open"], vals["High"], vals["Low"],
                       vals["Close"], vals["Volume"], "", flags=flags))
    return out


def fetch_yahoo_daily(sym, now, row=None):
    try:
        import yfinance as yf
    except ImportError as e:
        return res("fetch_failed", "", "import", e)
    start_s = (now - dt.timedelta(days=730)).strftime("%Y-%m-%d")
    end_s = now.strftime("%Y-%m-%d")
    err = ""
    for name, kw in (("2y_explicit", {"start": start_s, "end": end_s}),
                     ("1y_fallback", {"period": "1y"}), ("6mo_fallback", {"period": "6mo"})):
        try:
            df = _flat(yf.download(sym, interval="1d", auto_adjust=True, progress=False,
                                   threads=False, **kw))
            if df is not None and len(df) >= 20:
                return res("ok", "", recs=df_to_daily(df, name),
                           raw="; ".join(f"{str(i)[:10]}:{_num(c)}" for i, c in zip(df.index[-3:], df['Close'].iloc[-3:])))
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:160]}"
    return res("fetch_failed", "", "yfinance", err or "keine Daten")


def df_to_intraday(df, row, now=None):
    """Letzte 4 Balken; Sitzungszuordnung nach tz/open_local/close_local der Watchlist."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(row["tz"])
    oh, om = [int(x) for x in row["open_local"].split(":")]
    ch, cm = [int(x) for x in row["close_local"].split(":")]
    open_t, close_t = dt.time(oh, om), dt.time(ch, cm)
    last_reg_start = (dt.datetime.combine(dt.date(2000, 1, 1), close_t)
                      - dt.timedelta(minutes=INTRADAY_MIN)).time()
    idx = df.index
    def to_local(ts):
        t = ts.to_pydatetime()
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.astimezone(tz)
    locs = [to_local(x) for x in idx]
    out = []
    for i in range(max(0, len(df) - 4), len(df)):
        r = df.iloc[i]
        loc = locs[i]
        tt = loc.time().replace(second=0, microsecond=0)
        if open_t <= tt < close_t:
            sess = "regulaer"
        elif tt >= close_t:
            sess = "nach_Schluss_nicht_eindeutig"
        else:
            sess = "vor_Eroeffnung_nicht_eindeutig"
        flags = []
        if sess == "regulaer" and tt == last_reg_start:
            flags.append("letzter_regulaerer_balken")
        if _isnan(r["Close"] if "Close" in df.columns else float("nan")):
            flags.append("close_nan")
        vol = r["Volume"] if "Volume" in df.columns else float("nan")
        if _isnan(vol) or float(vol) == 0.0:
            flags.append("volume_leer")
        n_day = sum(1 for x in locs if x.date() == loc.date())
        flags.append(f"balken_am_tag={n_day}")
        utc = loc.astimezone(dt.timezone.utc)
        out.append(rec("bar", _iso(utc), r.get("Open"), r.get("High"), r.get("Low"), r.get("Close"),
                       vol, "", local=loc.strftime("%Y-%m-%dT%H:%M:%S%z"), sess=sess, flags=flags))
    return out


def fetch_yahoo_intraday(sym, now, row):
    try:
        import yfinance as yf
    except ImportError as e:
        return res("fetch_failed", "", "import", e)
    try:
        df = _flat(yf.download(sym, period="5d", interval=f"{INTRADAY_MIN}m", auto_adjust=True,
                               progress=False, threads=False, prepost=False))
        if df is None or len(df) == 0:
            return res("empty", "", "leer", "keine Intraday-Balken")
        recs = df_to_intraday(df, row)
        return res("ok", "", recs=recs,
                   raw="; ".join(f"{r['local']}:{r['c']}" for r in recs))
    except Exception as e:
        return res("fetch_failed", "", "yfinance", f"{type(e).__name__}: {str(e)[:160]}")


FETCHERS = {"yahoo": fetch_yahoo_daily, "yahoo_daily": fetch_yahoo_daily,
            "yahoo_intraday": fetch_yahoo_intraday, "binance": fetch_binance,
            "kraken": fetch_kraken, "coinbase": fetch_coinbase}


# --------------------------------------------------------------------------- Messpunkt
def run_messpunkt(mode, wl, slot, run_id, serie_csv, workers=4, fetchers=None):
    fetchers = fetchers or FETCHERS
    tasks = []
    if mode == "krypto":
        for r in wl:
            tasks.append((r, "yahoo", r["sym"]))
            for s in ("binance", "kraken", "coinbase"):
                tasks.append((r, s, r.get(s, "")))
    else:
        for r in wl:
            tasks.append((r, "yahoo_daily", r["sym"]))
            tasks.append((r, "yahoo_intraday", r["sym"]))

    def one(t):
        r, src, ident = t
        now = _now()
        t0 = time.perf_counter()
        if not ident:
            result = res("pair_nicht_zugeordnet", "", "keine_zuordnung", "kein Paarname in der Watchlist")
        else:
            try:
                result = fetchers[src](ident, now, r)
            except Exception as e:
                result = res("fetch_failed", "", type(e).__name__, str(e))
        ms = int(round((time.perf_counter() - t0) * 1000))
        return r, src, ident, now, ms, result

    with ThreadPoolExecutor(max_workers=workers) as ex:
        done = list(ex.map(one, tasks))
    counts = {}
    n_rows = 0
    with open(serie_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        for r, src, ident, now, ms, result in done:
            counts[(src, result["status"])] = counts.get((src, result["status"]), 0) + 1
            ctx = f"{r.get('tz', '')}|{r.get('open_local', '')}|{r.get('close_local', '')}" if mode == "nonus" else ""
            base = {"schema": SCHEMA, "run_id": run_id, "mode": mode, "slot_utc": _iso(slot),
                    "fetch_utc": _iso(now), "fetch_ms": ms, "ticker": r["sym"], "source": src,
                    "ident": ident, "status": result["status"], "http_status": result["http_status"],
                    "error_class": result["error_class"], "error_text": result["error_text"],
                    "ctx": ctx, "raw_excerpt": result["raw"]}
            if result["recs"]:
                for q in result["recs"]:
                    row = dict(base)
                    row.update({"rec_kind": q["kind"], "rec_key": q["key"], "rec_local": q["local"],
                                "open": q["o"], "high": q["h"], "low": q["l"], "close": q["c"],
                                "volume": q["v"], "is_closed": q["closed"], "session_class": q["sess"],
                                "flags": q["flags"]})
                    w.writerow(row)
                    n_rows += 1
            else:
                w.writerow(base)
                n_rows += 1
    return len(tasks), n_rows, counts


def cmd_messpunkt(a):
    wl = load_watchlist(a.watchlist, a.mode)
    n, rows, counts = run_messpunkt(a.mode, wl, _utc(a.slot), a.run_id, a.serie_csv, a.workers)
    bad = sum(v for (s, st), v in counts.items() if st != "ok")
    print(f"RESULT {n} {rows} {bad}")


# ------------------------------------------------------------------------------ Serie
def _prepare(a):
    out_dir = os.path.abspath(os.path.expanduser(a.out_dir))
    if _in_git(out_dir):
        sys.exit(f"ABBRUCH: {out_dir} liegt in einem Git-Arbeitsverzeichnis.")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _write_meta(path, a, run_id, wl_path, windows):
    try:
        import yfinance as yf
        yfv = getattr(yf, "__version__", "?")
    except Exception:
        yfv = "nicht installiert"
    meta = {"schema": SCHEMA, "version": VERSION, "run_id": run_id, "mode": a.mode,
            "start_utc": _iso(_now()), "script_sha256": _sha(os.path.abspath(__file__)),
            "watchlist_name": os.path.basename(wl_path), "watchlist_sha256": _sha(wl_path),
            "python": platform.python_version(), "yfinance": yfv,
            "system": f"{platform.system()} {platform.release()}",
            "fenster": [f"{_iso(f)}..{_iso(e)}/{iv // 60}min" for f, e, iv in windows],
            "workers": getattr(a, "workers", None), "endpoints": ENDPOINTS,
            "geheimnisse": "keine (nur oeffentliche Endpunkte, kein Schluessel, kein Konto)"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)


def cmd_serie(a):
    out_dir = _prepare(a)
    import subprocess
    wl = load_watchlist(a.watchlist, a.mode)
    windows = [parse_window(s) for s in a.fenster]
    sl = slots_for(windows)
    run_id = _now().strftime("%Y%m%dT%H%M%SZ")
    p_s = os.path.join(out_dir, f"c3_serie_{run_id}.csv")
    p_m = os.path.join(out_dir, f"c3_meta_{run_id}.json")
    p_l = os.path.join(out_dir, f"c3_log_{run_id}.txt")
    print(f"Serie {run_id} ({a.mode}): {len(wl)} Ticker, {len(sl)} Slots {sl[0]:%d.%m. %H:%M}Z bis "
          f"{sl[-1]:%d.%m. %H:%M}Z (lokale Zeit des Erstlaufs: {sl[0].astimezone():%d.%m. %H:%M})", flush=True)
    log = open(p_l, "a", encoding="utf-8")

    def L(msg):
        line = f"{_iso(_now())} {msg}"
        log.write(line + "\n")
        log.flush()
        print(line, flush=True)

    _write_meta(p_m, a, run_id, os.path.abspath(a.watchlist), windows)
    with open(p_s, "w", newline="", encoding="utf-8") as fs:
        csv.DictWriter(fs, fieldnames=FIELDS).writeheader()
    L(f"start {VERSION} python {platform.python_version()} modus {a.mode} ticker {len(wl)} slots {len(sl)} "
      f"(je Messpunkt ein frischer Prozess)")
    me = os.path.abspath(__file__)
    for i, slot in enumerate(sl):
        while True:
            rest = (slot - _now()).total_seconds()
            if rest <= 0:
                break
            time.sleep(min(rest, 20))
        late = (_now() - slot).total_seconds()
        nxt = sl[i + 1] if i + 1 < len(sl) else None
        if nxt is not None and _now() >= nxt:
            L(f"slot {slot:%H:%M:%S}Z uebersprungen (vorherige Messung dauerte laenger als das Intervall "
              f"oder der Rechner war im Ruhezustand; {late:.0f}s nach dem Slot)")
            continue
        try:
            r = subprocess.run(
                [sys.executable, "-P", me, "_messpunkt", "--mode", a.mode, "--watchlist",
                 os.path.abspath(a.watchlist), "--slot", slot.strftime("%Y-%m-%dT%H:%M:%S"),
                 "--run-id", run_id, "--serie-csv", p_s, "--workers", str(a.workers)],
                capture_output=True, text=True, timeout=1200)
            res_ln = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
            if r.returncode == 0 and res_ln:
                _, n, rows, bad = res_ln.split()
                L(f"slot {slot:%H:%M:%S}Z fertig: {n} Abrufe, {rows} Zeilen, nicht ok {bad}, Verspaetung {late:.0f}s")
            else:
                tail = (r.stderr.strip().splitlines() or ["?"])[-1][:160]
                L(f"slot {slot:%H:%M:%S}Z FEHLER (Exitcode {r.returncode}): {tail}")
        except Exception as e:
            L(f"slot {slot:%H:%M:%S}Z FEHLER: {type(e).__name__}: {str(e)[:160]}")
    L("ende")
    print(f"\nFertig.\n  {p_s}\n  {p_m}\n  {p_l}")


# ---------------------------------------------------------------------------- Kurztest
def cmd_kurztest(a):
    out_dir = _prepare(a)
    wl = load_watchlist(a.watchlist, a.mode)
    run_id = "kurztest_" + _now().strftime("%Y%m%dT%H%M%SZ")
    p_s = os.path.join(out_dir, f"c3_serie_{run_id}.csv")
    p_m = os.path.join(out_dir, f"c3_meta_{run_id}.json")
    _write_meta(p_m, a, run_id, os.path.abspath(a.watchlist), [])
    with open(p_s, "w", newline="", encoding="utf-8") as fs:
        csv.DictWriter(fs, fieldnames=FIELDS).writeheader()
    slot = _now().replace(microsecond=0)
    n, rows, counts = run_messpunkt(a.mode, wl, slot, run_id, p_s, a.workers)
    print(f"Kurztest {VERSION}, Modus {a.mode}, {n} Abrufe, {rows} Zeilen, {_iso(slot)}")
    print("\nStatus je Quelle:")
    for (s, st), v in sorted(counts.items()):
        print(f"  {s:15s} {st:22s} {v}")
    rows_ = list(csv.DictReader(open(p_s, newline="", encoding="utf-8")))
    print("\nJe Ticker und Quelle: Status, letzte Kerze/Zeile/Balken (Schluss, geschlossen/Sitzung/Flags)")
    seen = []
    for r in rows_:
        k = (r["ticker"], r["source"])
        if k not in seen:
            seen.append(k)
    for t, s in seen:
        rs = [r for r in rows_ if r["ticker"] == t and r["source"] == s]
        last = rs[-1]
        if last["status"] != "ok":
            print(f"  {t:10s} {s:15s} {last['status']} {last['http_status']} {last['error_class']} {last['error_text'][:60]}")
        else:
            extra = last["is_closed"] or last["session_class"]
            print(f"  {t:10s} {s:15s} ok  {last['rec_key'][:16]:16s} close={last['close'][:10]:10s} {extra} {last['flags'][:60]}")
    print(f"\nDateien:\n  {p_s}\n  {p_m}")


# --------------------------------------------------------------------------- Auswertung
def _f(x):
    try:
        return float(x)
    except Exception:
        return None


def _dev(a, b):
    return "" if a is None or b is None or b == 0 else f"{(a / b - 1) * 100:.3f}"


def eval_krypto(rows, tag_day=None):
    slots_ = sorted({r["slot_utc"] for r in rows})
    if tag_day is None:
        t0 = dt.datetime.fromisoformat(slots_[0].replace("Z", ""))
        tag_day = (t0.date() if t0.hour >= 12 else t0.date() - dt.timedelta(days=1)).isoformat()
    by = {}
    for r in rows:
        by.setdefault((r["ticker"], r["source"]), {}).setdefault(r["slot_utc"], []).append(r)
    res_ = {}
    for (t, s), per in by.items():
        z, closes, stat = "", {}, set()
        for sl_ in slots_:
            rs = per.get(sl_)
            if not rs:
                z += "?"
                continue
            if rs[0]["status"] != "ok":
                z += "?"
                stat.add(rs[0]["status"])
                continue
            hit = [x for x in rs if x["rec_key"][:10] == tag_day]
            if not hit:
                z += "-"
                continue
            x = hit[-1]
            closes[sl_] = _f(x["close"])
            if "close_nan" in x["flags"]:
                z += "n"
            elif s == "yahoo":
                z += "p"
            else:
                z += "c" if x["is_closed"] == "True" else "o"
        pres = [sl_ for sl_ in slots_ if sl_ in closes]
        final = closes[pres[-1]] if pres else None
        first_ok = next((slots_[i] for i, ch in enumerate(z) if ch in "pc"), "")
        stable = ""
        for i in range(len(z)):
            tail = [slots_[j] for j in range(i, len(z)) if z[j] != "?"]
            if tail and all(z[slots_.index(x)] in "pc" and closes.get(x) == final for x in tail) \
                    and z[i] in "pc":
                stable = slots_[i]
                break
        changes = 0
        seen_first = False
        prev = None
        for sl_ in slots_:
            if sl_ in closes and z[slots_.index(sl_)] in "pc":
                if seen_first and closes[sl_] != prev:
                    changes += 1
                seen_first, prev = True, closes[sl_]
        res_[(t, s)] = {"ticker": t, "source": s, "zustaende": z,
                        "status_nicht_ok": "|".join(sorted(stat)),
                        "erster_gueltig": first_ok[11:16], "stabil_ab": stable[11:16] if stable else "",
                        "final_close": "" if final is None else repr(final), "aenderungen_nach_erstem": changes}
    for (t, s), v in res_.items():
        y = res_.get((t, "yahoo"))
        v["abw_vs_yahoo_pct"] = _dev(_f(v["final_close"]), _f(y["final_close"])) if y and s != "yahoo" else ""
    return tag_day, slots_, list(res_.values())


def eval_nonus(rows):
    daily = [r for r in rows if r["source"] == "yahoo_daily" and r["status"] == "ok"]
    intra = [r for r in rows if r["source"] == "yahoo_intraday" and r["status"] == "ok"]
    final = {}
    firstp = {}
    stable = {}
    for t in sorted({r["ticker"] for r in daily}):
        rs = [r for r in daily if r["ticker"] == t]
        sl_ = sorted({r["slot_utc"] for r in rs})
        days = sorted({r["rec_key"][:10] for r in rs})
        for d in days:
            ser = []
            for s in sl_:
                x = [r for r in rs if r["slot_utc"] == s and r["rec_key"][:10] == d]
                ser.append((s, None if not x else (None if "close_nan" in x[-1]["flags"] else _f(x[-1]["close"]))))
            valid = [(s, c) for s, c in ser if c is not None]
            if not valid:
                continue
            fin = valid[-1][1]
            final[(t, d)] = fin
            firstp[(t, d)] = valid[0][0]
            st = ""
            for i in range(len(ser)):
                if all(c == fin for _, c in ser[i:]) and ser[i][1] is not None:
                    st = ser[i][0]
                    break
            stable[(t, d)] = st
    out_daily = [{"ticker": t, "tag": d, "erster_gueltig_slot": firstp[(t, d)][11:16],
                  "stabil_ab_slot": stable[(t, d)][11:16] if stable[(t, d)] else "",
                  "final_close": repr(final[(t, d)])} for (t, d) in sorted(final)]
    out_intra = []
    for t in sorted({r["ticker"] for r in intra}):
        rs = [r for r in intra if r["ticker"] == t and "letzter_regulaerer_balken" in r["flags"]]
        for d in sorted({r["rec_local"][:10] for r in rs}):
            x = sorted([r for r in rs if r["rec_local"][:10] == d], key=lambda r: r["slot_utc"])[0]
            tz, o, c = x["ctx"].split("|")
            from zoneinfo import ZoneInfo
            close_loc = dt.datetime.combine(dt.date.fromisoformat(d), dt.time(int(c[:2]), int(c[3:])),
                                            ZoneInfo(tz))
            slot_t = dt.datetime.fromisoformat(x["slot_utc"].replace("Z", "+00:00"))
            lat = (slot_t - close_loc.astimezone(dt.timezone.utc)).total_seconds() / 60.0
            fc = final.get((t, d))
            out_intra.append({"ticker": t, "boersentag": d, "erster_slot_mit_letztem_regulaeren_balken": x["slot_utc"][11:16],
                              "minuten_nach_schluss": f"{lat:.0f}", "balken_close": x["close"],
                              "yahoo_daily_final_close": "" if fc is None else repr(fc),
                              "abw_pct": _dev(_f(x["close"]), fc)})
            ab = [r for r in intra if r["ticker"] == t and r["session_class"] == "nach_Schluss_nicht_eindeutig"
                  and r["rec_local"][:10] == d]
            if ab:
                kmax = max(r["rec_key"] for r in ab)
                y = sorted([r for r in ab if r["rec_key"] == kmax], key=lambda r: r["slot_utc"])[0]
                sl_t = dt.datetime.fromisoformat(y["slot_utc"].replace("Z", "+00:00"))
                latn = (sl_t - close_loc.astimezone(dt.timezone.utc)).total_seconds() / 60.0
                out_intra[-1].update({"nach_schluss_balken_start_lokal": y["rec_local"][11:16],
                                      "nach_schluss_erster_slot": y["slot_utc"][11:16],
                                      "nach_schluss_minuten": f"{latn:.0f}", "nach_schluss_close": y["close"],
                                      "nach_schluss_abw_pct": _dev(_f(y["close"]), fc)})
            else:
                out_intra[-1].update({"nach_schluss_balken_start_lokal": "", "nach_schluss_erster_slot": "",
                                      "nach_schluss_minuten": "", "nach_schluss_close": "",
                                      "nach_schluss_abw_pct": ""})
    return out_daily, out_intra


def cmd_auswerten(a):
    rows = []
    for p in a.serie:
        rows += list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    if not rows:
        sys.exit("ABBRUCH: keine Zeilen.")
    modes = {r["mode"] for r in rows}
    if len(modes) != 1:
        sys.exit("ABBRUCH: Dateien enthalten verschiedene Modi.")
    mode = modes.pop()
    base = a.serie[0]
    stem = os.path.basename(base).replace("c3_serie_", "c3_auswertung_")
    if stem == os.path.basename(base):
        stem += ".auswertung.csv"
    outp = os.path.join(os.path.dirname(os.path.abspath(base)), stem)
    if mode == "krypto":
        tag, sl_, res_ = eval_krypto(rows, a.tag_day)
        with open(outp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(res_[0].keys()))
            w.writeheader()
            w.writerows(sorted(res_, key=lambda r: (r["ticker"], r["source"])))
        print(f"Tag (UTC-Tageskerze): {tag}; Slots: {' '.join(s[11:16] for s in sl_)}")
        print("Zustand: c geschlossen (Boerse), p Zeile da mit Close (Yahoo), o Kerze offen, n Close leer, - Tag fehlt, ? kein Datenabruf\n")
        for s in ("yahoo", "binance", "kraken", "coinbase"):
            v = [r for r in res_ if r["source"] == s]
            ok = [r for r in v if r["erster_gueltig"]]
            nok = sorted({r["status_nicht_ok"] for r in v if r["status_nicht_ok"]})
            fs = sorted(r["erster_gueltig"] for r in ok)
            ab = [abs(float(r["abw_vs_yahoo_pct"])) for r in v if r["abw_vs_yahoo_pct"]]
            print(f"{s:9s} n={len(v):2d} mit gueltigem Wert {len(ok):2d}  erster gueltig (min/median/max): "
                  f"{fs[0] if fs else '-'} / {fs[len(fs)//2] if fs else '-'} / {fs[-1] if fs else '-'}  "
                  f"nicht ok: {','.join(nok) or '-'}  |Abw. zu Yahoo final| max {max(ab):.3f} %" if ab else
                  f"{s:9s} n={len(v):2d} mit gueltigem Wert {len(ok):2d}  erster gueltig (min/median/max): "
                  f"{fs[0] if fs else '-'} / {fs[len(fs)//2] if fs else '-'} / {fs[-1] if fs else '-'}  nicht ok: {','.join(nok) or '-'}")
    else:
        d_, i_ = eval_nonus(rows)
        with open(outp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["# yahoo_daily"])
            w.writerow(["ticker", "tag", "erster_gueltig_slot", "stabil_ab_slot", "final_close"])
            for r in d_:
                w.writerow(list(r.values()))
            w.writerow([])
            w.writerow(["# yahoo_intraday"])
            w.writerow(["ticker", "boersentag", "erster_slot_letzter_regulaerer_balken",
                        "minuten_nach_schluss", "balken_close", "yahoo_daily_final_close", "abw_pct",
                        "nach_schluss_balken_start_lokal", "nach_schluss_erster_slot",
                        "nach_schluss_minuten", "nach_schluss_close", "nach_schluss_abw_pct"])
            for r in i_:
                w.writerow(list(r.values()))
        print(f"Yahoo-Tageszeile: {len(d_)} Ticker/Tage; Intraday mit letztem regulaeren Balken: {len(i_)}")
        for r in d_[:60]:
            print(f"  daily  {r['ticker']:10s} {r['tag']} erster {r['erster_gueltig_slot']} stabil {r['stabil_ab_slot'] or '-'} close {r['final_close'][:10]}")
        for r in i_[:60]:
            print(f"  intra  {r['ticker']:10s} {r['boersentag']} erster Slot {r['erster_slot_mit_letztem_regulaeren_balken']} "
                  f"({r['minuten_nach_schluss']} min nach Schluss) abw {r['abw_pct'] or '-'} %"
                  + (f" | Balken nach Schluss {r['nach_schluss_balken_start_lokal']} (erster Slot "
                     f"{r['nach_schluss_erster_slot']}) abw {r['nach_schluss_abw_pct'] or '-'} %"
                     if r["nach_schluss_balken_start_lokal"] else " | kein Balken nach Schluss"))
    print(f"\nDetail: {outp}")


# ------------------------------------------------------------------------- Trockenlauf
def cmd_trockenlauf(a):
    import socket
    import tempfile
    import pandas as pd

    class _Blocked(socket.socket):
        def connect(self, *args, **kw):
            raise RuntimeError("Netz im Trockenlauf blockiert")
    socket.socket = _Blocked

    n_ok = [0]

    def check(cond, msg):
        if not cond:
            print("FEHLER:", msg)
            sys.exit(1)
        n_ok[0] += 1

    tmp = tempfile.mkdtemp(prefix="c3_trocken_")
    now0 = _utc("2026-10-18T00:30:00")
    # --- Boersen-Parser mit festen Antworten
    def ts(t):
        return int(_utc(t).timestamp())

    def ms(t):
        return ts(t) * 1000
    kl = [[ms("2026-10-16T00:00:00"), "1", "2", "0.5", "1.5", "10", ms("2026-10-16T23:59:59") + 999],
          [ms("2026-10-17T00:00:00"), "1.5", "2", "1", "1.8", "11", ms("2026-10-17T23:59:59") + 999],
          [ms("2026-10-18T00:00:00"), "1.8", "1.9", "1.7", "1.75", "2", ms("2026-10-18T23:59:59") + 999]]
    fake = {"BTCUSDT": (200, json.dumps(kl), ""), "ETHUSDT": (400, '{"code":-1121,"msg":"Invalid symbol."}', ""),
            "SOLUSDT": (451, "restricted", ""), "BNBUSDT": (429, "slow", ""), "DOTUSDT": (None, "", "URLError: x"),
            "ADAUSDT": (200, "kein json", ""), "XRPUSDT": (200, "[]", ""), "LTCUSDT": (500, "err", "")}
    def fk(url):
        for key, v in fake.items():
            if f"symbol={key}&" in url:
                return v
        return (200, json.dumps(kl), "")
    HTTP["get"] = lambda url, timeout=20: (
        fk(url) if "binance" in url else
        (200, json.dumps({"error": [], "result": {"XXBTZUSD": [
            [ts("2026-10-16T00:00:00"), "1", "2", "0.5", "1.5", "1.2", "10", 5],
            [ts("2026-10-17T00:00:00"), "1.5", "2", "1", "1.8", "1.6", "11", 6],
            [ts("2026-10-18T00:00:00"), "1.8", "1.9", "1.7", "1.75", "1.8", "2", 3]],
            "last": ts("2026-10-18T00:00:00")}}), "") if "pair=XBTUSD" in url else
        (200, json.dumps({"error": ["EQuery:Unknown asset pair"]}), "") if "kraken" in url and "XDGUSD" in url else
        (200, json.dumps({"error": ["EGeneral:Too many requests"]}), "") if "kraken" in url and "LINKUSD" in url else
        (200, json.dumps([[ts("2026-10-18T00:00:00"), 1.7, 1.9, 1.8, 1.75, 2],
                          [ts("2026-10-17T00:00:00"), 1, 2, 1.5, 1.8, 11],
                          [ts("2026-10-16T00:00:00"), 0.5, 2, 1, 1.5, 10]]), "")
        if "coinbase" in url and "BTC-USD" in url else (404, '{"message":"NotFound"}', "") if "coinbase" in url else (200, "{}", ""))
    r = fetch_binance("BTCUSDT", now0)
    check(r["status"] == "ok" and [x["closed"] for x in r["recs"]] == [True, True, False], "Binance Kerzen/closed")
    check(r["recs"][0]["key"] == "2026-10-16T00:00:00Z" and r["recs"][2]["flags"] == "", "Binance Schluessel/Ausrichtung")
    for pair, want in (("ETHUSDT", "pair_unsupported"), ("SOLUSDT", "geo_blocked"), ("BNBUSDT", "rate_limited"),
                       ("DOTUSDT", "network_error"), ("ADAUSDT", "parse_error"), ("XRPUSDT", "empty"),
                       ("LTCUSDT", "http_error")):
        check(fetch_binance(pair, now0)["status"] == want, f"Binance {pair} -> {want}")
    r = fetch_kraken("XBTUSD", now0)
    check(r["status"] == "ok" and [x["closed"] for x in r["recs"]] == [True, True, False]
          and r["recs"][2]["v"] == "2.0", "Kraken Kerzen (Volumen Index 6)")
    check(fetch_kraken("XDGUSD", now0)["status"] == "pair_unsupported", "Kraken unknown pair")
    check(fetch_kraken("LINKUSD", now0)["status"] == "rate_limited", "Kraken rate limit")
    r = fetch_coinbase("BTC-USD", now0)
    check(r["status"] == "ok" and r["recs"][0]["key"] == "2026-10-16T00:00:00Z"
          and r["recs"][2]["c"] == "1.75" and r["recs"][2]["closed"] is False and r["recs"][0]["o"] == "1.0", "Coinbase Reihenfolge/Felder")
    check(fetch_coinbase("ETH-USD", now0)["status"] == "pair_unsupported", "Coinbase 404")
    # --- Yahoo-Adapter
    idx = pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-17"])
    df = pd.DataFrame({"Open": [1.0, 2.0, 3.0], "High": [1, 2, 3], "Low": [1, 2, 3],
                       "Close": [1.0, 2.0, float("nan")], "Volume": [5, 6, 7]}, index=idx)
    q = df_to_daily(df, "2y_explicit")
    check(q[2]["flags"].startswith("versuch=2y_explicit|close_nan") and q[2]["key"] == "2026-10-17"
          and q[1]["c"] == "2.0", "Yahoo daily NaN-Kennzeichnung")
    row = {"sym": "BMW.DE", "tz": "Europe/Berlin", "open_local": "09:00", "close_local": "17:30"}
    bars = pd.date_range("2026-10-16 16:30", "2026-10-16 17:45", freq="15min", tz="Europe/Berlin")
    di = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [1, 2, 3, 4, 5, 6], "Volume": [9, 9, 9, 9, 0, 9]},
                      index=bars)
    q = df_to_intraday(di, row)
    cls = [(x["local"][11:16], x["sess"], "letzter_regulaerer_balken" in x["flags"]) for x in q]
    check(cls == [("17:00", "regulaer", False), ("17:15", "regulaer", True),
                  ("17:30", "nach_Schluss_nicht_eindeutig", False), ("17:45", "nach_Schluss_nicht_eindeutig", False)],
          f"Intraday Sitzungszuordnung {cls}")
    check("volume_leer" in q[2]["flags"] and q[1]["key"] == "2026-10-16T15:15:00Z", "Intraday Volumen/UTC-Zeit (MESZ)")
    row_l = {"sym": "VOD.L", "tz": "Europe/London", "open_local": "08:00", "close_local": "16:30"}
    bl = pd.date_range("2026-10-16 15:45", "2026-10-16 16:30", freq="15min", tz="Europe/London")
    dl = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [1, 2, 3, 4], "Volume": 5}, index=bl)
    q = df_to_intraday(dl, row_l)
    check([("letzter_regulaerer_balken" in x["flags"]) for x in q] == [False, False, True, False], "London letzter Balken 16:15")
    # --- Gesamtablauf Krypto mit Slots, die sich entwickeln
    wl_k = os.path.join(tmp, "wl_k.csv")
    with open(wl_k, "w", encoding="utf-8") as f:
        f.write("sym,homeMarket,group,binance,kraken,coinbase\nBTC-USD,,c,BTCUSDT,XBTUSD,BTC-USD\n"
                "ETH-USD,,c,ETHUSDT,,\n")
    wl = load_watchlist(wl_k, "krypto")
    p_s = os.path.join(tmp, "c3_serie_T.csv")
    with open(p_s, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writeheader()
    state = {"slot": 0}
    def yahoo_fake(sym, now, row=None):
        k = state["slot"]
        rows_ = [rec("row", "2026-10-16", 1, 1, 1, 1.5, 5)]
        if k >= 1:
            rows_.append(rec("row", "2026-10-17", 1, 1, 1, float("nan") if k == 1 else 1.8, 5, flags=["close_nan"] if k == 1 else []))
        return res("ok", "", recs=rows_)
    def bin_fake(pair, now, row=None):
        k = state["slot"]
        if pair == "ETHUSDT":
            return res("pair_unsupported", 400, "invalid_symbol", "x")
        return res("ok", 200, recs=[rec("candle", "2026-10-17T00:00:00Z", 1, 2, 1, 1.8 if k >= 1 else 1.7, 3, k >= 1)])
    fk_ = {"yahoo": yahoo_fake, "binance": bin_fake,
           "kraken": lambda p, n, r=None: res("pair_unsupported", 200, "unknown_pair", "q"),
           "coinbase": lambda p, n, r=None: res("network_error", "", "network", "x")}
    slots_k = ["2026-10-17T23:50:00", "2026-10-18T00:10:00", "2026-10-18T07:00:00"]
    for i, s in enumerate(slots_k):
        state["slot"] = i
        run_messpunkt("krypto", wl, _utc(s), "T", p_s, 2, fetchers=fk_)
    rows = list(csv.DictReader(open(p_s, newline="", encoding="utf-8")))
    check(all(list(r.keys()) == FIELDS for r in rows), "CSV-Spalten")
    check(sum(1 for r in rows if r["ticker"] == "ETH-USD" and r["source"] == "kraken"
              and r["status"] == "pair_nicht_zugeordnet") == 3, "Paar ohne Zuordnung getrennt protokolliert")
    tag, sl_, ev = eval_krypto(rows)
    check(tag == "2026-10-17", f"Ereignistag {tag}")
    e = {(x["ticker"], x["source"]): x for x in ev}
    check(e[("BTC-USD", "binance")]["zustaende"] == "occ",
          "Binance Zustaende " + e[("BTC-USD", "binance")]["zustaende"])
    check(e[("BTC-USD", "binance")]["erster_gueltig"] == "00:10" and e[("BTC-USD", "binance")]["stabil_ab"] == "00:10",
          "Binance erster gueltig/stabil")
    check(e[("BTC-USD", "yahoo")]["zustaende"] == "-np" and e[("BTC-USD", "yahoo")]["erster_gueltig"] == "07:00",
          "Yahoo Zustaende " + e[("BTC-USD", "yahoo")]["zustaende"])
    check(e[("BTC-USD", "kraken")]["zustaende"] == "???" and e[("BTC-USD", "kraken")]["status_nicht_ok"] == "pair_unsupported",
          "unsupported nicht als verspaetet gewertet")
    check(e[("BTC-USD", "binance")]["abw_vs_yahoo_pct"] == "0.000", "Abweichung zu Yahoo final")
    # --- Gesamtablauf Non-US
    wl_n = os.path.join(tmp, "wl_n.csv")
    with open(wl_n, "w", encoding="utf-8") as f:
        f.write("sym,homeMarket,group,tz,open_local,close_local\nBMW.DE,DE,x,Europe/Berlin,09:00,17:30\n")
    wln = load_watchlist(wl_n, "nonus")
    p_n = os.path.join(tmp, "c3_serie_N.csv")
    with open(p_n, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writeheader()
    def dly(sym, now, row=None):
        return res("ok", "", recs=[rec("row", "2026-10-16", 1, 1, 1, 100.0 if now.day >= 17 else float("nan"), 5,
                                       flags=[] if now.day >= 17 else ["close_nan"])])
    def itd(sym, now, row=None):
        b = pd.date_range("2026-10-16 17:00", "2026-10-16 17:30", freq="15min", tz="Europe/Berlin")
        d2 = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [99.9, 100.1, 100.0], "Volume": 7}, index=b)
        return res("ok", "", recs=df_to_intraday(d2, row))
    fn = {"yahoo_daily": lambda s, n, r=None: dly(s, state["now"], r), "yahoo_intraday": itd}
    for s, hh in (("2026-10-16T15:45:00", 15), ("2026-10-17T08:15:00", 8), ("2026-10-17T09:15:00", 9)):
        state["now"] = _utc(s)
        run_messpunkt("nonus", wln, _utc(s), "N", p_n, 1, fetchers=fn)
    d_, i_ = eval_nonus(list(csv.DictReader(open(p_n, newline="", encoding="utf-8"))))
    check(d_[0]["erster_gueltig_slot"] == "08:15" and d_[0]["stabil_ab_slot"] == "08:15", "Non-US daily erster/stabil")
    check(i_[0]["erster_slot_mit_letztem_regulaeren_balken"] == "15:45" and i_[0]["minuten_nach_schluss"] == "15"
          and i_[0]["abw_pct"] == "0.100", f"Non-US intraday Latenz/Abw {i_[0]}")
    check(i_[0]["nach_schluss_balken_start_lokal"] == "17:30" and i_[0]["nach_schluss_abw_pct"] == "0.000"
          and i_[0]["nach_schluss_erster_slot"] == "15:45", f"Balken nach Schluss {i_[0]}")
    # --- Zeitfenster
    sl = slots_for([(_utc("2026-10-17T23:50"), _utc("2026-10-18T00:10"), 300), (_utc("2026-10-18T06:30"), _utc("2026-10-18T06:50"), 600)])
    check([x.strftime("%H:%M") for x in sl] == ["23:50", "23:55", "00:00", "00:05", "00:10", "06:30", "06:40", "06:50"],
          "Fenster/Slots " + str([x.strftime('%H:%M') for x in sl]))
    # --- Kein Geheimnis in Meta
    class A:
        mode = "krypto"
        workers = 2
    mp = os.path.join(tmp, "m.json")
    _write_meta(mp, A, "T", wl_k, [(_utc("2026-10-17T23:50"), _utc("2026-10-18T00:10"), 300)])
    txt = open(mp, encoding="utf-8").read()
    check(os.path.expanduser("~") not in txt and "key" not in txt.lower().replace("keine", ""), "Meta ohne Pfad/Schluessel")
    print(f"TROCKENLAUF OK: {n_ok[0]} Pruefungen, kein Netz benutzt, Temp-Ordner {os.path.basename(tmp)}")


# ------------------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="C3-Quellenvergleich-Sonde (nur lesen, nur Diagnose-CSV)")
    s = p.add_subparsers(dest="cmd", required=True)
    t = s.add_parser("trockenlauf")
    t.set_defaults(fn=cmd_trockenlauf)
    k = s.add_parser("kurztest")
    k.add_argument("--mode", required=True, choices=["krypto", "nonus"])
    k.add_argument("--watchlist", required=True)
    k.add_argument("--workers", type=int, default=4)
    k.add_argument("--out-dir", default="~/uiq-c2-lokal")
    k.set_defaults(fn=cmd_kurztest)
    r = s.add_parser("serie")
    r.add_argument("--mode", required=True, choices=["krypto", "nonus"])
    r.add_argument("--watchlist", required=True)
    r.add_argument("--fenster", required=True, action="append",
                   help="START,ENDE,MINUTEN (UTC, z. B. 2026-10-17T23:50,2026-10-18T02:00,5); mehrfach moeglich")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--out-dir", default="~/uiq-c2-lokal")
    r.set_defaults(fn=cmd_serie)
    m = s.add_parser("_messpunkt")
    m.add_argument("--mode", required=True)
    m.add_argument("--watchlist", required=True)
    m.add_argument("--slot", required=True)
    m.add_argument("--run-id", required=True)
    m.add_argument("--serie-csv", required=True)
    m.add_argument("--workers", type=int, default=4)
    m.set_defaults(fn=cmd_messpunkt)
    q = s.add_parser("auswerten")
    q.add_argument("serie", nargs="+")
    q.add_argument("--tag-day", default=None, help="UTC-Tageskerze (JJJJ-MM-TT), sonst aus dem ersten Slot abgeleitet")
    q.set_defaults(fn=cmd_auswerten)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
