#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C2-Zeitverlauf-Sonde (Diagnose, rein lesend)  -  Stand 09.10.2026

ZWECK
  Zeitverlauf der Vervollstaendigung der letzten Tageszeile (D26) und der fehlenden
  Schlusszeile (D27) fuer eine FESTE Ticker-Teilmenge messen: wann ist die Zeile
  vollstaendig, bleibt sie es, gibt es Rueckfaelle. Ursachen werden nicht vorausgesetzt.
  Zusaetzlich (getrennt, eigene Datei): D27-Label-Probe mit SPY.

BEFEHLE
  serie     Messreihe: Erstlauf zum Startzeitpunkt, danach auf halbe Stunden ausgerichtet
            (Intervall einstellbar) bis einschliesslich Endzeitpunkt (alles UTC).
  auswerten Auswertung einer fertigen Serie (pro Boerse/Gruppe, mit Rueckfaellen).

DATENQUELLE
  Yahoo Finance ueber `yfinance` (oeffentliche Kursdaten, kein Login). Abruf je Ticker wie
  im Aggregator (fetch_one): yf.download(ticker, interval="1d", auto_adjust=True,
  progress=False, threads=False, start=<jetzt-730d>, end=<heutiges UTC-Datum, exklusiv>),
  mit den Fallbacks period="1y"/"6mo".
  D27-Label-Probe: bildet get_last_trading_day() des Aggregators (655d649, Zeilen 1704-1707)
  nach: yf.download("SPY", period="5d", interval="1d", auto_adjust=True, progress=False,
  threads=False) und last_date = spy.index[-1].date().

HINWEIS ZUM ZWISCHENSPEICHER
  yfinance 1.5.2 speichert Abfragen mit end >= 30 Min in der Vergangenheit prozessintern
  zwischen (data.py cache_get, LRU 64). Deshalb laeuft jeder Messpunkt in einem frischen
  Prozess. Die Spalte fetch_ms (Abrufdauer je Ticker) macht Zwischenspeicher-Treffer sichtbar.
  yfinance legt ausserdem eigene Cookie-/Zeitzonen-Caches im Benutzer-Cache an (nicht im out-dir).

GRENZEN
  Keine Aenderung an Aggregator, Worker, Generator, KV, Archiv, SUITE.md, Workflows.
  Keine Scores/Breadth/Track-Records, keine Bereinigung. Schreibt nur nach --out-dir
  (Standard ~/uiq-c2-lokal), verweigert den Lauf in einem Git-Arbeitsverzeichnis.
  Kein Hostname, Benutzername oder Pfad in den Dateien.
"""
import argparse
import csv
import datetime as dt
import os
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor

SCHEMA = "c2_zeitverlauf/1"
PRICE = ["Open", "High", "Low", "Close"]
SERIE_FIELDS = [
    "schema", "run_id", "slot_utc", "fetch_utc", "fetch_ms", "ticker", "homeMarket", "group",
    "fetch_status", "attempt_used", "error_text", "n_rows", "last_idx_date",
    "last_open", "last_high", "last_low", "last_close", "last_volume",
    "last_row_nan_cols", "last_row_close_nan", "last_valid_close_date",
    "last_valid_close", "prev_idx_date", "prev_close",
]
D27_FIELDS = [
    "schema", "run_id", "slot_utc", "fetch_utc", "spy5d_status", "spy5d_error",
    "spy5d_n_rows", "spy5d_dates", "spy5d_label_date", "spy5d_last_row_close_nan",
    "spy5d_last_valid_close_date", "spy_startend_last_idx_date",
    "d27_group_last_idx_min", "d27_group_last_idx_max", "label_minus_spy_startend",
    "versions",
]


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
    if len(s) == 5:  # HH:MM -> heute
        today = dt.datetime.now(dt.timezone.utc).date()
        return dt.datetime.combine(today, dt.time(int(s[:2]), int(s[3:])), dt.timezone.utc)
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)


def slots(first, end, interval_s):
    """Erstlauf + auf Vielfache des Intervalls (ab UTC-Mitternacht) ausgerichtete Slots bis end."""
    out = [first]
    midnight = first.replace(hour=0, minute=0, second=0, microsecond=0)
    k = int((first - midnight).total_seconds() // interval_s) + 1
    while True:
        t = midnight + dt.timedelta(seconds=k * interval_s)
        if t > end:
            break
        out.append(t)
        k += 1
    return out


def load_watchlist(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("sym") or "").strip():
                rows.append((r["sym"].strip(), (r.get("homeMarket") or "").strip(),
                             (r.get("group") or "").strip()))
    if not rows:
        sys.exit("ABBRUCH: Watchlist leer.")
    return rows


def fetch_one(yf, ticker, now):
    start_s = (now - dt.timedelta(days=730)).strftime("%Y-%m-%d")
    end_s = now.strftime("%Y-%m-%d")
    err = ""
    for name, kw in [("2y_explicit", {"start": start_s, "end": end_s}),
                     ("1y_fallback", {"period": "1y"}), ("6mo_fallback", {"period": "6mo"})]:
        try:
            df = yf.download(ticker, interval="1d", auto_adjust=True, progress=False,
                             threads=False, **kw)
            if df is not None and len(df) >= 20:
                if hasattr(df.columns, "levels"):
                    df.columns = df.columns.get_level_values(0)
                return df, name, err
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:160]}".replace("\n", " ")
    return None, "", err


def describe(df):
    o = {"n_rows": len(df), "last_idx_date": str(df.index[-1])[:10]}
    last = df.iloc[-1]
    v = {c: (last[c] if c in df.columns else float("nan")) for c in PRICE + ["Volume"]}
    o.update(last_open=_num(v["Open"]), last_high=_num(v["High"]), last_low=_num(v["Low"]),
             last_close=_num(v["Close"]), last_volume=_num(v["Volume"]))
    o["last_row_nan_cols"] = "|".join(c for c in PRICE + ["Volume"] if _isnan(v[c]))
    o["last_row_close_nan"] = _isnan(v["Close"])
    if "Close" in df.columns:
        valid = df["Close"].dropna()
        o["last_valid_close_date"] = str(valid.index[-1])[:10] if len(valid) else ""
        o["last_valid_close"] = _num(valid.iloc[-1]) if len(valid) else ""
        if len(df) >= 2:
            o["prev_idx_date"] = str(df.index[-2])[:10]
            o["prev_close"] = _num(df["Close"].iloc[-2])
    return o


def spy_5d(yf):
    """Nachbildung von get_last_trading_day() (Aggregator 655d649, Zeilen 1704-1707)."""
    try:
        spy = yf.download("SPY", period="5d", interval="1d", auto_adjust=True,
                          progress=False, threads=False)
        if spy is None or len(spy) == 0:
            return {"spy5d_status": "empty", "spy5d_error": ""}
        if hasattr(spy.columns, "levels"):
            spy.columns = spy.columns.get_level_values(0)
        close = spy["Close"] if "Close" in spy.columns else None
        valid = close.dropna() if close is not None else None
        return {"spy5d_status": "ok", "spy5d_error": "", "spy5d_n_rows": len(spy),
                "spy5d_dates": "|".join(str(i)[:10] for i in spy.index),
                "spy5d_label_date": str(spy.index[-1].date()),   # wie spy.index[-1].date()
                "spy5d_last_row_close_nan": _isnan(close.iloc[-1]) if close is not None else "",
                "spy5d_last_valid_close_date": str(valid.index[-1])[:10]
                if valid is not None and len(valid) else ""}
    except Exception as e:
        return {"spy5d_status": "failed",
                "spy5d_error": f"{type(e).__name__}: {str(e)[:160]}".replace("\n", " ")}


# -------------------------------------------------------------------------------- serie
def messung(yf, wl, slot, run_id, workers, wr_s, wr_d):
    now = dt.datetime.now(dt.timezone.utc)

    def one(item):
        sym, hm, grp = item
        ft = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        t0 = time.perf_counter()
        df, used, err = fetch_one(yf, sym, now)
        ms = int(round((time.perf_counter() - t0) * 1000))
        row = {"schema": SCHEMA, "run_id": run_id,
               "slot_utc": slot.strftime("%Y-%m-%dT%H:%M:%SZ"), "fetch_utc": ft, "fetch_ms": ms,
               "ticker": sym, "homeMarket": hm, "group": grp, "error_text": err}
        if df is None:
            row["fetch_status"] = "failed"
        else:
            row["fetch_status"] = "ok" if used == "2y_explicit" else "ok_fallback"
            row["attempt_used"] = used
            try:
                row.update(describe(df))
            except Exception as e:
                row["fetch_status"] = "analyse_error"
                row["error_text"] = f"{type(e).__name__}: {str(e)[:160]}"
        return row

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(one, wl))
    for r in rows:
        wr_s.writerow(r)
    d27 = {"schema": SCHEMA, "run_id": run_id, "slot_utc": slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "fetch_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    d27.update(spy_5d(yf))
    spy_row = next((r for r in rows if r["ticker"] == "SPY"), None)
    d27["spy_startend_last_idx_date"] = (spy_row or {}).get("last_idx_date", "")
    g = [r.get("last_idx_date", "") for r in rows if r["group"] == "d27" and r.get("last_idx_date")]
    d27["d27_group_last_idx_min"], d27["d27_group_last_idx_max"] = (min(g), max(g)) if g else ("", "")
    lab, se = d27.get("spy5d_label_date", ""), d27["spy_startend_last_idx_date"]
    d27["label_minus_spy_startend"] = ((dt.date.fromisoformat(lab) - dt.date.fromisoformat(se)).days
                                       if lab and se else "")
    d27["versions"] = f"yfinance {getattr(yf, '__version__', '?')}"
    wr_d.writerow(d27)
    bad = sum(1 for r in rows if r["fetch_status"] in ("failed", "analyse_error"))
    inc = sum(1 for r in rows if r.get("last_row_close_nan") is True)
    ms = sorted(r["fetch_ms"] for r in rows)
    return len(rows), bad, inc, ms[len(ms) // 2]


def cmd_messpunkt(a):
    """INTERN: genau ein Messpunkt in einem frischen Prozess (haengt Zeilen an die CSVs an).
    Grund: yfinance 1.5.2 haelt Abfragen mit end >= 30 Min in der Vergangenheit in einem
    prozessinternen LRU-Cache (data.py cache_get, maxsize 64). In einem Dauerprozess lieferten
    alle Folge-Messpunkte dieselben zwischengespeicherten Antworten wie der erste."""
    import yfinance as yf
    wl = load_watchlist(a.watchlist)
    slot = _utc(a.slot)
    with open(a.serie_csv, "a", newline="", encoding="utf-8") as fs, \
            open(a.d27_csv, "a", newline="", encoding="utf-8") as fd:
        ws = csv.DictWriter(fs, fieldnames=SERIE_FIELDS, extrasaction="ignore")
        wd = csv.DictWriter(fd, fieldnames=D27_FIELDS, extrasaction="ignore")
        n, bad, inc, ms = messung(yf, wl, slot, a.run_id, a.workers, ws, wd)
    print(f"RESULT {n} {bad} {inc} {ms}")


def cmd_serie(a):
    out_dir = os.path.abspath(os.path.expanduser(a.out_dir))
    if _in_git(out_dir):
        sys.exit(f"ABBRUCH: {out_dir} liegt in einem Git-Arbeitsverzeichnis.")
    os.makedirs(out_dir, exist_ok=True)
    try:
        import yfinance as yf
        import pandas as pd  # noqa: F401
    except ImportError as e:
        sys.exit(f"ABBRUCH: Paket fehlt ({e}).")
    import subprocess
    wl = load_watchlist(a.watchlist)
    first, end = _utc(a.first_utc), _utc(a.end_utc)
    sl = slots(first, end, int(round(a.interval_min * 60)))
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p_s = os.path.join(out_dir, f"c2_serie_{run_id}.csv")
    p_d = os.path.join(out_dir, f"c2_d27_{run_id}.csv")
    p_l = os.path.join(out_dir, f"c2_serie_log_{run_id}.txt")
    print(f"Serie {run_id}: {len(wl)} Ticker, {len(sl)} Slots {sl[0]:%d.%m. %H:%M}Z bis {sl[-1]:%H:%M}Z "
          f"(lokale Zeit des Erstlaufs: {sl[0].astimezone():%d.%m. %H:%M})", flush=True)
    log = open(p_l, "a", encoding="utf-8")

    def L(msg):
        line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
        log.write(line + "\n")
        log.flush()
        print(line, flush=True)

    L(f"start python {platform.python_version()} yfinance {getattr(yf, '__version__', '?')} "
      f"watchlist {len(wl)} slots {len(sl)} (je Messpunkt ein frischer Prozess)")
    with open(p_s, "w", newline="", encoding="utf-8") as fs, \
            open(p_d, "w", newline="", encoding="utf-8") as fd:
        csv.DictWriter(fs, fieldnames=SERIE_FIELDS).writeheader()
        csv.DictWriter(fd, fieldnames=D27_FIELDS).writeheader()
    me = os.path.abspath(__file__)
    for i, slot in enumerate(sl):
        while True:  # auf den Slot warten (absolute Zeit, keine Drift)
            rest = (slot - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if rest <= 0:
                break
            time.sleep(min(rest, 20))
        late = (dt.datetime.now(dt.timezone.utc) - slot).total_seconds()
        nxt = sl[i + 1] if i + 1 < len(sl) else None
        if nxt is not None and dt.datetime.now(dt.timezone.utc) >= nxt:
            L(f"slot {slot:%H:%M:%S}Z uebersprungen (die vorherige Messung dauerte laenger als das "
              f"Intervall oder der Rechner war im Ruhezustand; {late:.0f}s nach dem Slot)")
            continue
        try:
            r = subprocess.run(
                [sys.executable, "-P", me, "_messpunkt", "--watchlist", os.path.abspath(a.watchlist),
                 "--slot", slot.strftime("%Y-%m-%dT%H:%M:%S"), "--run-id", run_id,
                 "--serie-csv", p_s, "--d27-csv", p_d, "--workers", str(a.workers)],
                capture_output=True, text=True, timeout=1200)
            res = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
            if r.returncode == 0 and res:
                _, n, bad, inc, ms = res.split()
                L(f"slot {slot:%H:%M:%S}Z fertig: {n} Ticker, Abruf fehlgeschlagen {bad}, "
                  f"letzte Zeile Close=NaN {inc}, Median Abrufdauer {ms} ms, Verspaetung {late:.0f}s")
            else:
                tail = (r.stderr.strip().splitlines() or ["?"])[-1][:160]
                L(f"slot {slot:%H:%M:%S}Z FEHLER (Exitcode {r.returncode}): {tail}")
        except Exception as e:  # ein Messpunkt darf die Serie nie beenden
            L(f"slot {slot:%H:%M:%S}Z FEHLER: {type(e).__name__}: {str(e)[:160]}")
    L("ende")
    print(f"\nFertig.\n  {p_s}\n  {p_d}\n  {p_l}")


# ----------------------------------------------------------------------------- auswerten
def cmd_auswerten(a):
    rows = list(csv.DictReader(open(a.serie, newline="", encoding="utf-8")))
    slots_ = sorted({r["slot_utc"] for r in rows})
    by = {}
    ref = {}
    for r in rows:
        by.setdefault(r["ticker"], {})[r["slot_utc"]] = r
        if r["ticker"] == "SPY" and r.get("last_idx_date"):
            ref[r["slot_utc"]] = r["last_idx_date"]

    def zustand(r, slot):
        if r["fetch_status"] in ("failed", "analyse_error"):
            return "?"                       # Abrufstatus, kein Datenbefund
        if r["last_row_close_nan"] == "True":
            return "L"                       # letzte Zeile leer (D26-Muster)
        if slot in ref and r["last_idx_date"] and r["last_idx_date"] < ref[slot]:
            return "F"                       # Zeile fehlt (hinter SPY, D27-Muster)
        return "V"                           # vollstaendig

    res = []
    for t, per in by.items():
        z = "".join(zustand(per[s], s) if s in per else "-" for s in slots_)
        first_v = next((i for i, c in enumerate(z) if c == "V"), None)
        stable = None
        for i in range(len(z)):
            if all(c in "V?-" for c in z[i:]) and "V" in z[i:]:
                stable = i
                break
        relapse = sum(1 for i in range(1, len(z)) if z[i] in "LF" and "V" in z[:i])
        g = next(iter(per.values()))
        sfx = t.rsplit(".", 1)[1] if "." in t else ("-USD" if t.endswith("-USD") else "US")
        res.append({"ticker": t, "group": g["group"], "boerse": sfx, "zustaende": z,
                    "erster_V": slots_[first_v][11:16] if first_v is not None else "",
                    "stabil_ab": slots_[stable][11:16] if stable is not None else "",
                    "rueckfaelle": relapse})
    out = a.serie.replace("c2_serie_", "c2_auswertung_")
    if out == a.serie:                      # nie die Eingabedatei ueberschreiben
        out = a.serie + ".auswertung.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0].keys()))
        w.writeheader()
        w.writerows(sorted(res, key=lambda r: (r["group"], r["boerse"], r["ticker"])))
    print("Slots (UTC):", " ".join(s[11:16] for s in slots_))
    print("Zustand: V vollstaendig, L letzte Zeile leer, F Zeile fehlt, ? Abrufstatus, - kein Messpunkt\n")
    groups = {}
    for r in res:
        groups.setdefault((r["group"], r["boerse"]), []).append(r)
    for (grp, b), v in sorted(groups.items()):
        st = sorted(x["stabil_ab"] for x in v if x["stabil_ab"])
        nv = sum(1 for x in v if not x["stabil_ab"])
        print(f"{grp:9s} {b:5s} n={len(v):2d}  stabil vollstaendig ab (min/median/max): "
              f"{st[0] if st else '-'} / {st[len(st)//2] if st else '-'} / {st[-1] if st else '-'}  "
              f"nie stabil: {nv}  Rueckfaelle: {sum(x['rueckfaelle'] for x in v)}")
    print(f"\nDetail je Ticker: {out}")


def main():
    p = argparse.ArgumentParser(description="C2-Zeitverlauf-Sonde (nur lesen, nur Diagnose-CSV)")
    s = p.add_subparsers(dest="cmd", required=True)
    r = s.add_parser("serie")
    r.add_argument("--watchlist", required=True)
    r.add_argument("--first-utc", required=True, help="z. B. 2026-10-10T01:40")
    r.add_argument("--end-utc", required=True, help="z. B. 2026-10-10T12:00 (einschliesslich)")
    r.add_argument("--interval-min", type=float, default=30.0)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--out-dir", default="~/uiq-c2-lokal")
    r.set_defaults(fn=cmd_serie)
    m = s.add_parser("_messpunkt")          # intern, wird von "serie" aufgerufen
    m.add_argument("--watchlist", required=True)
    m.add_argument("--slot", required=True)
    m.add_argument("--run-id", required=True)
    m.add_argument("--serie-csv", required=True)
    m.add_argument("--d27-csv", required=True)
    m.add_argument("--workers", type=int, default=4)
    m.set_defaults(fn=cmd_messpunkt)
    q = s.add_parser("auswerten")
    q.add_argument("serie")
    q.set_defaults(fn=cmd_auswerten)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
