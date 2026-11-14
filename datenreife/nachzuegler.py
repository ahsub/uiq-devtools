#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nachzuegler.py  -  Nachzuegler-Auswertung (Diagnose, rein lesend)  -  Version v1.1 (14.11.2026)

ZWECK
  Beschreibt die Gruppe G der US-Ticker, deren letzte Tageszeile in den Produktions-Snapshots
  in ALLEN angegebenen Laeufen unvollstaendig war (`_lastRowIncomplete` wahr), gegenueber der
  Kontrollgruppe C (in allen Laeufen vollstaendig). Je vorab festgelegtem Merkmal werden Anteile
  in G und C und ein zweiseitiger exakter Test nach Fisher ausgegeben (rein deskriptiv,
  univariat, keine Korrektur fuer Mehrfachtests, keine Ursachenaussage).
  Hintergrund: UEBERGABE-2026-10-17.md, Abschnitte 21 bis 23.

EINGABE
  Ein lokaler, schreibgeschuetzter Klon des privaten Archivs `ahsub/uiq-archive` mit
  `data/snapshots/JJJJ-MM-TT_HH.json.gz`. US-Ticker = Symbole ohne '.', '-' und '^'.
  Zugehoerigkeit zu Marktlisten und Sektor-Watchlists stammt aus dem LETZTEN angegebenen Snapshot.

GRENZEN
  Kein Netz, keine Konten, keine Schluessel. Veraendert weder Archiv noch Produktion.
  Die Ausgabe enthaelt standardmaessig KEINE Tickernamen; mit --mit-ticker werden Tickernamen
  ausgegeben (nur fuer den lokalen Gebrauch; Ausgabe und Gruppendateien gehoeren nicht in ein
  oeffentliches Repo). Schreibt nur auf Wunsch (--gruppen-dateien) und verweigert das Schreiben
  in ein Git-Arbeitsverzeichnis.

AUFRUF
  python3 -I nachzuegler.py --archiv PFAD_ZUM_ARCHIVKLON [--snapshots A B C] [--mit-ticker]
          [--pruefticker T1 T2 ...] [--gruppen-dateien ORDNER]
  Standard fuer --snapshots: 2026-11-07_01 2026-11-11_01 2026-11-14_02.
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys

VERSION = "nachzuegler v1.1 (14.11.2026)"
DEFAULT_SNAPS = ["2026-11-07_01", "2026-11-11_01", "2026-11-14_02"]
DEFAULT_PRUEF = ["ETSY", "FIVE", "META", "SHEL"]


def _in_git(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return True
        q = os.path.dirname(p)
        if q == p:
            return False
        p = q


def is_us(s):
    return '.' not in s and '-' not in s and not s.startswith('^')


def fisher(a, b, c, dd):
    # zweiseitiger exakter Test nach Fisher
    n = a + b + c + dd
    r1 = a + b
    c1 = a + c

    def pr(x):
        return math.comb(r1, x) * math.comb(n - r1, c1 - x) / math.comb(n, c1)
    p0 = pr(a)
    lo = max(0, c1 - (n - r1))
    hi = min(r1, c1)
    return sum(pr(x) for x in range(lo, hi + 1) if pr(x) <= p0 * (1 + 1e-9))


def qbins(vals, nq=4):
    v = sorted(x for x in vals if x is not None)
    return [v[int(len(v) * i / nq)] for i in range(1, nq)]


def main():
    ap = argparse.ArgumentParser(description="Nachzuegler-Auswertung (rein lesend).")
    ap.add_argument("--archiv", required=True, help="Pfad zum Klon von ahsub/uiq-archive")
    ap.add_argument("--snapshots", nargs="+", default=DEFAULT_SNAPS,
                    help="Snapshot-Namen ohne .json.gz (mindestens zwei)")
    ap.add_argument("--mit-ticker", action="store_true", help="Tickernamen in der Ausgabe (nur lokal)")
    ap.add_argument("--pruefticker", nargs="*", default=DEFAULT_PRUEF,
                    help="Ticker, deren Einordnung ausgegeben wird (nur mit --mit-ticker)")
    ap.add_argument("--gruppen-dateien", default=None,
                    help="Ordner, in den G.txt und C.txt geschrieben werden (nicht in ein Git-Verzeichnis)")
    a = ap.parse_args()
    if len(a.snapshots) < 2:
        sys.exit("ABBRUCH: mindestens zwei Snapshots angeben.")
    ARC = os.path.join(os.path.expanduser(a.archiv), "data", "snapshots") + os.sep
    SNAPS = a.snapshots
    print(f"# {VERSION}; Snapshots: {', '.join(SNAPS)}", file=sys.stderr)

    def load(n):
        with gzip.open(ARC + n + '.json.gz') as f:
            return json.load(f)

    snap = {n: load(n) for n in SNAPS}
    inc = {}
    us = {}
    for n, d in snap.items():
        T = {o['sym']: o for o in d['tickers'] if is_us(o['sym'])}
        us[n] = T
        inc[n] = {s for s, o in T.items() if o['_lastRowIncomplete']}
    common = set.intersection(*[set(us[n]) for n in SNAPS])
    G = {s for s in common if all(s in inc[n] for n in SNAPS)}
    C = {s for s in common if all(s not in inc[n] for n in SNAPS)}
    X = common - G - C
    if a.mit_ticker:
        print('Universum gemeinsam', len(common), 'Gruppe G', len(G), 'Kontrolle C', len(C), 'uneindeutig', len(X), sorted(X))
    else:
        print('Universum gemeinsam', len(common), 'Gruppe G', len(G), 'Kontrolle C', len(C), 'uneindeutig', len(X))
    d = snap[SNAPS[-1]]
    T = us[SNAPS[-1]]
    mem = collections.defaultdict(set)
    for k, v in d['markets'].items():
        for o in v:
            if isinstance(o, dict) and 'sym' in o:
                mem[o['sym']].add(k)
    sw = collections.defaultdict(set)
    for k, v in d['sectorWatchlists'].items():
        for o in v:
            s = o['sym'] if isinstance(o, dict) else o
            sw[s].add(k)

    def report_bool(name, f):
        g = sum(1 for s in G if f(s))
        c = sum(1 for s in C if f(s))
        p = fisher(g, len(G) - g, c, len(C) - c)
        print(f'{name:38s} G {g:4d}/{len(G)} = {100*g/len(G):5.1f} %   C {c:4d}/{len(C)} = {100*c/len(C):5.1f} %   Fisher p={p:.2g}')

    allv = [T[s].get('avgVol20') for s in G | C]
    dv = [(T[s]['price'] * T[s]['avgVol20']) if T[s].get('avgVol20') and T[s].get('price') else None for s in G | C]
    qv = qbins(allv)
    qd = qbins(dv)
    print('Quartilgrenzen Volumen', qv, 'Dollarvolumen', [round(x) for x in qd])
    print('\n== Marktlisten-Zugehörigkeit (Snapshot 14.11.) ==' if SNAPS[-1] == "2026-11-14_02"
          else f'\n== Marktlisten-Zugehörigkeit (Snapshot {SNAPS[-1]}) ==')
    for k in ['sp500', 'nasdaq100', 'intl', 'etfs', 'etfs_exus', 'bear_us', 'bear_eu']:
        report_bool('in ' + k, lambda s, k=k: k in mem[s])
    report_bool('in keiner Marktliste', lambda s: len(mem[s]) == 0)
    report_bool('in mind. einer Sektor-Watchlist', lambda s: len(sw[s]) > 0)
    for k in sorted({x for v in sw.values() for x in v}):
        report_bool('Watchlist ' + k, lambda s, k=k: k in sw[s])
    print('\n== Kurs und Liquidität ==')
    for lo, hi in [(0, 5), (5, 20), (20, 100), (100, 500), (500, 1e9)]:
        report_bool(f'Kurs {lo}-{hi}', lambda s, lo=lo, hi=hi: lo <= T[s]['price'] < hi)
    for i in range(4):
        def inq(s, i=i):
            v = T[s].get('avgVol20')
            if v is None:
                return False
            b = sum(1 for q in qv if v >= q)
            return b == i
        report_bool(f'avgVol20 Quartil {i+1} (1=niedrig)', inq)
    for i in range(4):
        def inqd(s, i=i):
            if not (T[s].get('avgVol20') and T[s].get('price')):
                return False
            v = T[s]['price'] * T[s]['avgVol20']
            b = sum(1 for q in qd if v >= q)
            return b == i
        report_bool(f'Dollarvolumen Quartil {i+1}', inqd)
    print('\n== Weitere vorab festgelegte Merkmale ==')
    report_bool('5 Buchstaben, endet auf Y/F (OTC-ADR)', lambda s: len(s) == 5 and s[-1] in 'YF')
    report_bool('Optionsdaten (ivArchiveDays>0)', lambda s: (T[s].get('ivArchiveDays') or 0) > 0)
    report_bool('IV ATM vorhanden', lambda s: T[s].get('ivAtm') is not None)
    report_bool('Historie < 500 Bars', lambda s: (T[s].get('bars') or 0) < 500)
    report_bool('Earnings-Status vorhanden', lambda s: bool(T[s].get('earningsStatus')))
    for st in sorted({T[s].get('earningsStatus') for s in G | C}, key=str):
        report_bool('earningsStatus ' + str(st), lambda s, st=st: T[s].get('earningsStatus') == st)
    report_bool('mit Sektor-Tag (sectors nicht leer)', lambda s: bool(T[s].get('sectors')))
    for k in sorted({x for s in G | C for x in (T[s].get('sectors') or [])}):
        report_bool('sectors enthält ' + k, lambda s, k=k: k in (T[s].get('sectors') or []))
    print('\n== Kontrolle auf Verarbeitungsreihenfolge (Feld updated, Sekunden nach Laufstart) ==')

    def sec(s):
        u = T[s]['updated']
        return int(u[11:13]) * 3600 + int(u[14:16]) * 60 + int(u[17:19])
    gs = sorted(sec(s) for s in G)
    cs = sorted(sec(s) for s in C)

    def med(v):
        return v[len(v) // 2]
    print('updated Median G', med(gs), 'C', med(cs), 'min/max G', gs[0], gs[-1], 'C', cs[0], cs[-1])
    if a.mit_ticker:
        print('\n== Nachzügler aus c2 ==')
        for t in a.pruefticker:
            print(t, 'in common' if t in common else 'nicht im US-Universum', 'G' if t in G else 'C' if t in C else '-',
                  sorted(mem[t]), sorted(sw[t]), T.get(t, {}).get('price'), T.get(t, {}).get('avgVol20'))
        print('\nBeispiele G (zufällig, aufsteigend):', sorted(G)[::6])
    print('\nGroesse Listen gesamt (14.11.): ' if SNAPS[-1] == "2026-11-14_02" else '\nGroesse Listen gesamt: ',
          {k: len(v) for k, v in d['markets'].items()})
    if a.gruppen_dateien:
        outd = os.path.expanduser(a.gruppen_dateien)
        if _in_git(outd):
            sys.exit("ABBRUCH: Gruppendateien nicht in ein Git-Arbeitsverzeichnis schreiben.")
        os.makedirs(outd, exist_ok=True)
        with open(os.path.join(outd, 'G.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(sorted(G)))
        with open(os.path.join(outd, 'C.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(sorted(C)))


if __name__ == "__main__":
    main()
