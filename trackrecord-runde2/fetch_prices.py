#!/usr/bin/env python3
"""Eingefrorene Kursdatei für Runde 2 erzeugen (§5a Nr. 10 der Präregistrierung).

Lädt bereinigte Tagesschlusskurse (yfinance, auto_adjust=True) für alle Long-Titel der
Signaltage im Zeitraum und den Benchmark RSP. Schreibt
  <out>.csv        date,ticker,close
  <out>.meta.json  Benchmark, Ticker, Datumsbereich, Abrufzeitpunkt, auto_adjust,
                   yfinance-Version, SHA-256 der CSV
Liest aus dem Backup nur tr:snap (Ticker-Listen), keine Renditen.
Erst nach Ende der Stichprobe für die konfirmatorische Auswertung ausführen.
Version 1.1.0 (26.11.2026): robuste Spaltenauswahl für alle yfinance-Rückgabeformen, testbar
"""
import argparse, hashlib, json
from datetime import datetime, timezone

import pandas as pd

BENCHMARK = "RSP"
STRATS = {"long_minervini", "long_breakout", "long_swing", "ko_long", "options_csp", "options_atmna",
          "options_weekly", "options_cc", "long_value", "long_dividend", "long_mr"}


BATCH = 50


def extract_close(df, ticker):
    """Schlusskurse eines Tickers aus jeder Rückgabeform von yfinance.download:
    MultiIndex (Ticker, Feld) bei group_by='ticker', MultiIndex (Feld, Ticker) ohne group_by,
    einfache Spalten bei manchen Versionen für einen einzelnen Ticker. Fehlt er: None."""
    if df is None or len(df) == 0:
        return None
    cols = df.columns
    if isinstance(cols, pd.MultiIndex):
        if ticker in cols.get_level_values(0) and "Close" in df[ticker].columns:
            s = df[ticker]["Close"]
        elif "Close" in cols.get_level_values(0) and ticker in df["Close"].columns:
            s = df["Close"][ticker]
        else:
            return None
    else:
        if "Close" not in cols:
            return None
        s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = s.dropna()
    return s if len(s) else None


def fetch_rows(tickers, start, end, downloader, batch=BATCH):
    rows = []
    for i in range(0, len(tickers), batch):
        part = tickers[i:i + batch]
        df = downloader(part, start=start, end=end, auto_adjust=True, progress=False,
                        threads=False, group_by="ticker")
        for t in part:
            s = extract_close(df, t)
            if s is not None:
                rows += [(pd.Timestamp(d).strftime("%Y-%m-%d"), t, float(c)) for d, c in s.items()]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--first-day", required=True, help="erster Signaltag (z. B. 2026-11-09)")
    ap.add_argument("--last-day", required=True, help="letzter Signaltag der Stichprobe")
    ap.add_argument("--start", required=True, help="Kursbeginn, mind. 61 Sitzungen vor dem ersten Signaltag")
    ap.add_argument("--end", required=True, help="Kursende (exklusiv), mind. 30 Sitzungen + Puffer nach dem letzten Signaltag")
    ap.add_argument("--out", required=True, help="Pfad ohne Endung")
    a = ap.parse_args()
    import yfinance as yf

    K = json.load(open(a.backup))["keys"]
    tickers = set()
    for k, v in K.items():
        if k.startswith("tr:snap:"):
            v = json.loads(v) if isinstance(v, str) else v
            if a.first_day <= v["tday"] <= a.last_day:
                tickers |= {r["sym"] for r in v.get("recs", []) if r.get("strat") in STRATS and r.get("dir", 1) == 1}
    del K
    tickers = sorted(tickers | {BENCHMARK})
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = fetch_rows(tickers, a.start, a.end, yf.download)
    out = pd.DataFrame(rows, columns=["date", "ticker", "close"]).sort_values(["ticker", "date"])
    if BENCHMARK not in set(out.ticker):
        raise SystemExit(f"Abbruch: keine Kurse für den Benchmark {BENCHMARK}")
    out.to_csv(a.out + ".csv", index=False, float_format="%.6f")
    sha = hashlib.sha256(open(a.out + ".csv", "rb").read()).hexdigest()
    got = set(out.ticker)
    meta = {"benchmark": BENCHMARK, "tickers_requested": len(tickers), "tickers_with_data": len(got),
            "tickers_missing": sorted(set(tickers) - got), "signal_days": [a.first_day, a.last_day],
            "price_range": [a.start, a.end], "fetched_utc": fetched, "auto_adjust": True,
            "yfinance_version": yf.__version__, "csv_sha256": sha}
    json.dump(meta, open(a.out + ".meta.json", "w"), indent=2)
    print(json.dumps({k: meta[k] for k in ("tickers_requested", "tickers_with_data", "csv_sha256")}))


if __name__ == "__main__":
    main()
