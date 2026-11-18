#!/usr/bin/env python3
"""c3_nacht_auswertung.py v1.2 (18.11.2026) - Auswertung der Non-US-Nachtmessung (c3, Modus nonus).

v1.2: V nur noch, wenn kein close_nan-Flag vorliegt UND der Close numerisch, endlich und > 0 ist;
ein ungueltiger Close ohne Flag wird als L gewertet (Detail "Close ungueltig ohne Flag") und je Nacht
als Flag-Verstoss gezaehlt; Stabilitaet numerisch statt als Zeichenkette verglichen; Selbsttest erweitert
(ungueltige Closes ohne Flag, numerische Stabilitaet, Kenngroessen der separaten Gruppe).

v1.1: gegenueber v1.0 (SHA-256 b321fcee36a4ca8e) nur Tickername in Kommentar, Aufrufbeispiel und
Selbsttest durch einen Platzhalter ersetzt; Auswertungslogik, Parameter und Ausgabe unveraendert.

Setzt die Auswertungsregeln aus UEBERGABE-2026-10-17.md, Abschnitt 24.4 um. Liest nur; schreibt
nur die angegebene Ausgabedatei. Kein Eingriff in c3, kein Netz.

Zustaende je Ticker und Slot (nur Tageszeilen, Quelle yahoo_daily):
  V  Abruf ok, versuch=2y_explicit, Zeile mit Datum D vorhanden, Close gueltig
  L  Abruf ok, versuch=2y_explicit, Zeile mit Datum D vorhanden, Close leer (close_nan)
  F  Abruf ok, versuch=2y_explicit, Zeile mit Datum D fehlt unter den gelieferten letzten drei Zeilen
     (F heisst nur: Datum D nicht geliefert; nicht: Yahoo besitzt keine Daten fuer D)
  U  Abruf ok, aber ueber einen Fallback (1y_fallback, 6mo_fallback); geht nicht in V/L/F ein
  A  Messausfall: Slot ohne Datenzeilen (uebersprungen, Prozessabbruch; Grund aus dem Log, soweit
     vorhanden) oder Abruf mit status ungleich ok (fetch_failed, empty, ...)
  X  Ticker fuer diese Nacht ausgeschlossen (Boersentag D ist an seiner Boerse kein Handelstag,
     nur ueber --kein-handelstag angegeben; wird namentlich aufgefuehrt)

Kenngroessen je Ticker: erster Slot mit V, letzter Slot mit L, Anzahl F-Slots, ob der Close nach dem
ersten V unveraendert bleibt (stabil), Rueckfaelle (Slot mit L oder F nach dem ersten V),
Anteil U an den Tagesabrufen, Anzahl A. Je Slot: Zaehlung je Zustand, getrennt fuer die
Hauptgruppe und die mit --separat angegebenen Ticker (z. B. Boerse mit abweichender Zeitzone). Mit zwei Naechten zusaetzlich:
Unterschied des ersten V-Slots je Ticker in Slots (beschreibend; Abweichungen ueber einen Slot
werden benannt).

Aufruf (eine Nacht je --nacht, Felder durch Komma getrennt):
  python3 -I c3_nacht_auswertung.py \
      --nacht A,2026-11-16,c3_serie_X.csv,c3_meta_X.json,c3_log_X.txt \
      [--nacht B,2026-11-17,...] --separat SYM [--kein-handelstag A:SYM ...] \
      --out bericht.txt [--matrix matrix.csv]
  python3 -I c3_nacht_auswertung.py selbsttest
"""
import csv
import datetime as dt
import json
import math
import os
import re
import sys

VERSION = "c3_nacht_auswertung v1.2 (18.11.2026)"
ORDER = "VLFUAX"


def slots_from_meta(meta):
    out = []
    for w in meta["fenster"]:
        m = re.match(r"(\S+)\.\.(\S+)/(\d+)min$", w)
        a = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ")
        b = dt.datetime.strptime(m.group(2), "%Y-%m-%dT%H:%M:%SZ")
        t = a
        while t <= b:
            out.append(t.strftime("%Y-%m-%dT%H:%M:%SZ"))
            t += dt.timedelta(minutes=int(m.group(3)))
    return out


def log_reasons(path, slots):
    """Slot -> Grund aus dem Log (FEHLER/uebersprungen). Uhrzeit im Log ohne Datum: Zuordnung ueber
    die Slotliste der Nacht (eindeutig, solange ein Fenster keinen Tageswechsel enthaelt)."""
    by_hm = {s[11:19]: s for s in slots}
    out = {}
    if not path:
        return out
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\S+ slot (\d\d:\d\d:\d\d)Z (FEHLER: (\w+)|uebersprungen)", line)
        if m and m.group(1) in by_hm:
            out[by_hm[m.group(1)]] = m.group(3) or "uebersprungen"
    return out


def close_ok(x):
    """True, wenn der Close numerisch, endlich und groesser als 0 ist."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def flag(fl, name):
    return name in fl.split("|")


def versuch(fl):
    for p in fl.split("|"):
        if p.startswith("versuch="):
            return p[8:]
    return ""


def classify(rows, day, slots, reasons, excluded):
    """Liefert states[ticker][slot] = (Zustand, close_oder_None, detail) und Abrufzaehlung."""
    daily = [r for r in rows if r["source"] == "yahoo_daily"]
    tickers = sorted({r["ticker"] for r in rows})
    by = {}
    for r in daily:
        by.setdefault((r["ticker"], r["slot_utc"]), []).append(r)
    slots_with_rows = {r["slot_utc"] for r in rows}
    states = {t: {} for t in tickers}
    for t in tickers:
        for s in slots:
            if t in excluded:
                states[t][s] = ("X", None, "kein Handelstag")
                continue
            rs = by.get((t, s), [])
            if s not in slots_with_rows:
                states[t][s] = ("A", None, reasons.get(s, "keine Zeilen"))
            elif not rs:
                states[t][s] = ("A", None, "kein Tagesabruf")
            elif rs[0]["status"] != "ok":
                states[t][s] = ("A", None, rs[0]["status"] + ":" + rs[0]["error_class"])
            elif any(versuch(r["flags"]) != "2y_explicit" for r in rs):
                states[t][s] = ("U", None, versuch(rs[0]["flags"]))
            else:
                hit = [r for r in rs if r["rec_key"][:10] == day]
                if not hit:
                    states[t][s] = ("F", None, "letzte=" + max(r["rec_key"][:10] for r in rs))
                elif flag(hit[-1]["flags"], "close_nan"):
                    states[t][s] = ("L", None, "")
                elif not close_ok(hit[-1]["close"]):
                    states[t][s] = ("L", None, "Close ungueltig ohne Flag")
                else:
                    states[t][s] = ("V", hit[-1]["close"], "")
    fetches = {}
    seen = set()
    for r in rows:
        k = (r["ticker"], r["slot_utc"], r["source"])
        if k in seen:
            continue
        seen.add(k)
        fetches[(r["source"], r["status"])] = fetches.get((r["source"], r["status"]), 0) + 1
    return tickers, states, fetches


def kennzahlen(st, slots):
    seq = [st[s] for s in slots]
    v_idx = [i for i, x in enumerate(seq) if x[0] == "V"]
    l_idx = [i for i, x in enumerate(seq) if x[0] == "L"]
    first_v = v_idx[0] if v_idx else None
    closes = {float(seq[i][1]) for i in v_idx}
    rueck = [slots[i][11:16] for i in range(first_v + 1, len(seq)) if seq[i][0] in "LF"] if first_v is not None else []
    daily_ok = sum(1 for x in seq if x[0] in "VLFU")
    n_u = sum(1 for x in seq if x[0] == "U")
    return {"erster_V": slots[first_v][11:16] if first_v is not None else "-",
            "erster_V_idx": first_v,
            "letzter_L": slots[l_idx[-1]][11:16] if l_idx else "-",
            "F": sum(1 for x in seq if x[0] == "F"),
            "A": sum(1 for x in seq if x[0] == "A"),
            "stabil": ("ja" if len(closes) == 1 else "nein") if v_idx else "-",
            "rueckfaelle": ",".join(rueck) if rueck else "-",
            "U_anteil": f"{n_u}/{daily_ok}"}


def auswerten(nacht, separat, excluded, out, matrix_rows):
    label, day, serie, meta_p, log_p = nacht
    meta = json.load(open(meta_p, encoding="utf-8"))
    rows = list(csv.DictReader(open(serie, encoding="utf-8")))
    rows = [r for r in rows if r["run_id"] == meta["run_id"]]
    slots = slots_from_meta(meta)
    reasons = log_reasons(log_p, slots)
    tickers, states, fetches = classify(rows, day, slots, reasons, excluded)
    P = out.append
    P(f"=== Nacht {label}: Boersentag D = {day}, Lauf {meta['run_id']}, Messdatum(e) {sorted({s[:10] for s in slots})}, "
      f"{len(slots)} Slots, {len(tickers)} Ticker ===")
    P(f"Werkzeug laut Meta: {meta['version']}, Skript {meta['script_sha256'][:16]}, Watchlist {meta['watchlist_sha256'][:16]}, "
      f"yfinance {meta['yfinance']}")
    P("Ausgeschlossen (kein Handelstag): " + (", ".join(sorted(excluded)) if excluded else "keine"))
    P("Abrufe je Quelle und Status (je Ticker und Slot einmal gezaehlt): " +
      "; ".join(f"{k[0]} {k[1]}={v}" for k, v in sorted(fetches.items())))
    verstoesse = sum(1 for t in tickers for s in slots if states[t][s][2] == "Close ungueltig ohne Flag")
    P(f"Flag-Verstöße: {verstoesse}")
    with_rows = {r["slot_utc"] for r in rows}
    a_slots = [s for s in slots if s not in with_rows or s in reasons]
    P("Slots ohne Messung (Kategorie A, ganzer Slot): " +
      (", ".join(f"{s[11:16]} ({reasons.get(s, 'keine Zeilen')})" for s in a_slots) if a_slots else "keine"))
    groups = [("Hauptgruppe", [t for t in tickers if t not in separat]), ("Separat", [t for t in tickers if t in separat])]
    for gname, ts in groups:
        if not ts:
            continue
        tot = {k: 0 for k in ORDER}
        for t in ts:
            for s in slots:
                tot[states[t][s][0]] += 1
        P(f"-- {gname} ({len(ts)} Ticker): Ticker-Slots je Zustand " + " ".join(f"{k}={tot[k]}" for k in ORDER))
        P("   Slot   " + " ".join(f"{k:>3s}" for k in ORDER))
        for s in slots:
            c = {k: 0 for k in ORDER}
            for t in ts:
                c[states[t][s][0]] += 1
            P(f"   {s[11:16]}  " + " ".join(f"{c[k]:3d}" for k in ORDER))
        P(f"   {'Ticker':10s} {'erst V':>6s} {'letzt L':>7s} {'F':>3s} {'A':>3s} {'stabil':>6s} {'U':>6s}  Rueckfaelle")
        for t in ts:
            k = kennzahlen(states[t], slots)
            P(f"   {t:10s} {k['erster_V']:>6s} {k['letzter_L']:>7s} {k['F']:3d} {k['A']:3d} {k['stabil']:>6s} "
              f"{k['U_anteil']:>6s}  {k['rueckfaelle']}")
        P("   Zustandsfolge je Ticker (ein Zeichen je Slot, ab " + slots[0][11:16] + "Z, 10-min-Raster):")
        for t in ts:
            P(f"   {t:10s} " + "".join(states[t][s][0] for s in slots))
    for t in tickers:
        for s in slots:
            st = states[t][s]
            matrix_rows.append({"nacht": label, "boersentag_D": day, "messdatum_utc": s[:10], "slot_utc": s,
                                "ticker": t, "gruppe": "separat" if t in separat else "haupt",
                                "zustand": st[0], "close": st[1] or "", "detail": st[2]})
    return {t: kennzahlen(states[t], slots) for t in tickers}


def vergleich(res, out, separat):
    labels = list(res)
    if len(labels) < 2:
        return
    a, b = labels[0], labels[1]
    out.append(f"=== Reproduzierbarkeit: erster V-Slot Nacht {a} gegen Nacht {b} (beschreibend) ===")
    for t in sorted(set(res[a]) & set(res[b])):
        x, y = res[a][t]["erster_V_idx"], res[b][t]["erster_V_idx"]
        d = "-" if x is None or y is None else y - x
        note = " <- mehr als ein Slot" if isinstance(d, int) and abs(d) > 1 else ""
        out.append(f"   {t:10s} {'(separat) ' if t in separat else ''}{res[a][t]['erster_V']:>6s} {res[b][t]['erster_V']:>6s}  "
                   f"Diff {d} Slot(s){note}")


def main(argv):
    if argv[:1] == ["selbsttest"]:
        return selbsttest()
    nachte, separat, excl, outp, matp = [], set(), {}, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--nacht":
            p = argv[i + 1].split(",")
            if len(p) != 5:
                sys.exit("ABBRUCH: --nacht braucht LABEL,D,serie.csv,meta.json,log.txt")
            nachte.append(p)
            i += 2
        elif a == "--separat":
            separat.add(argv[i + 1]); i += 2
        elif a == "--kein-handelstag":
            lab, sym = argv[i + 1].split(":"); excl.setdefault(lab, set()).add(sym); i += 2
        elif a == "--out":
            outp = argv[i + 1]; i += 2
        elif a == "--matrix":
            matp = argv[i + 1]; i += 2
        else:
            sys.exit(f"ABBRUCH: unbekanntes Argument {a}")
    if not nachte or not outp:
        sys.exit(__doc__)
    for p in [outp] + ([matp] if matp else []):
        if os.path.exists(p):
            sys.exit(f"ABBRUCH: Ausgabedatei existiert schon: {p}")
    out, mrows, res = [VERSION], [], {}
    for n in nachte:
        res[n[0]] = auswerten(n, separat, excl.get(n[0], set()), out, mrows)
    vergleich(res, out, separat)
    open(outp, "w", encoding="utf-8").write("\n".join(out) + "\n")
    if matp:
        with open(matp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(mrows[0]))
            w.writeheader(); w.writerows(mrows)
    print("\n".join(out))


def selbsttest():
    """Kuenstliche Daten, deckt alle Zustaende ab; kein Netz, keine echten Messdaten."""
    import tempfile
    d = tempfile.mkdtemp(prefix="c3aus_")
    meta = {"run_id": "T", "version": "test", "script_sha256": "0" * 64, "watchlist_sha256": "0" * 64,
            "yfinance": "-", "fenster": ["2026-11-17T00:10:00Z..2026-11-17T00:50:00Z/10min"]}
    json.dump(meta, open(os.path.join(d, "m.json"), "w"))
    F = ["run_id", "slot_utc", "ticker", "source", "status", "error_class", "rec_key", "close", "flags"]
    rows = []

    def add(slot, t, st="ok", recs=(), src="yahoo_daily", ec=""):
        base = {"run_id": "T", "slot_utc": f"2026-11-17T00:{slot}:00Z", "ticker": t, "source": src,
                "status": st, "error_class": ec, "rec_key": "", "close": "", "flags": ""}
        if not recs:
            rows.append(base)
        for k, c, fl in recs:
            x = dict(base); x.update(rec_key=k, close=c, flags=fl); rows.append(x)
    two = "versuch=2y_explicit"
    # Ticker E1: F, L, V, V(gleich) ; Slot 00:30 fehlt ganz (uebersprungen)
    add("10", "E1", recs=[("2026-11-13", "1", two), ("2026-11-14", "1", two)])
    add("20", "E1", recs=[("2026-11-16", "", two + "|close_nan")])
    add("40", "E1", recs=[("2026-11-16", "5", two)])
    add("50", "E1", recs=[("2026-11-16", "5", two)])
    # Ticker E2: U, A(fetch_failed), V, L (Rueckfall), V mit anderem Close
    add("10", "E2", recs=[("2026-11-16", "2", "versuch=1y_fallback")])
    add("20", "E2", st="fetch_failed", ec="yfinance")
    add("40", "E2", recs=[("2026-11-16", "3", two)])
    add("50", "E2", recs=[("2026-11-16", "", two + "|close_nan")])
    # S1 (separater Ticker): V ab Beginn
    for s in ("10", "20", "40", "50"):
        add(s, "S1", recs=[("2026-11-16", "7", two)])
    # E4: Close leer / "nan" / "inf" jeweils OHNE close_nan-Flag -> L (Flag-Verstoss), dann gueltig -> V
    add("10", "E4", recs=[("2026-11-16", "", two)])
    add("20", "E4", recs=[("2026-11-16", "nan", two)])
    add("40", "E4", recs=[("2026-11-16", "inf", two)])
    add("50", "E4", recs=[("2026-11-16", "4", two)])
    # E5: derselbe Zahlenwert in zwei Schreibweisen -> numerisch stabil
    add("40", "E5", recs=[("2026-11-16", "5", two)])
    add("50", "E5", recs=[("2026-11-16", "5.0", two)])
    # E3 ausgeschlossen
    add("10", "E3", recs=[("2026-11-13", "1", two)])
    with open(os.path.join(d, "s.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=F); w.writeheader(); w.writerows(rows)
    open(os.path.join(d, "l.txt"), "w").write("2026-11-17T00:40:00Z slot 00:30:00Z uebersprungen (x)\n")
    nacht = ["A", "2026-11-16", os.path.join(d, "s.csv"), os.path.join(d, "m.json"), os.path.join(d, "l.txt")]
    out, mrows = [], []
    k = auswerten(nacht, {"S1"}, {"E3"}, out, mrows)
    seq = {t: "".join(r["zustand"] for r in mrows if r["ticker"] == t) for t in ("E1", "E2", "S1", "E3", "E4", "E5")}
    s1 = k.get("S1", {})
    ok = 0
    checks = [
        (seq["E1"] == "FLAVV", f"E1 Folge {seq['E1']}"),
        (seq["E2"] == "UAAVL", f"E2 Folge {seq['E2']}"),
        (seq["S1"] == "VVAVV", f"S1 Folge {seq['S1']}"),
        (seq["E3"] == "XXXXX", f"E3 Folge {seq['E3']}"),
        (k["E1"]["erster_V"] == "00:40" and k["E1"]["letzter_L"] == "00:20" and k["E1"]["stabil"] == "ja", "E1 Kennzahlen"),
        (k["E1"]["F"] == 1 and k["E1"]["A"] == 1, "E1 F/A"),
        (k["E2"]["rueckfaelle"] == "00:50" and k["E2"]["stabil"] == "ja" and k["E2"]["U_anteil"] == "1/3", "E2 Kennzahlen"),
        ([r["detail"] for r in mrows if r["ticker"] == "E1" and r["slot_utc"].endswith("00:30:00Z")] == ["uebersprungen"], "Grund aus Log"),
        (any("Hauptgruppe (5 Ticker)" in x for x in out) and any("Separat (1 Ticker)" in x for x in out), "Gruppen"),
        (seq["E4"] == "LLALV" and [r["detail"] for r in mrows if r["ticker"] == "E4"][:2] == ["Close ungueltig ohne Flag"] * 2,
         f"E4 ungueltiger Close ohne Flag -> L, Folge {seq['E4']}"),
        ("Flag-Verstöße: 3" in out, "Zaehlung Flag-Verstoesse"),
        (k["E4"]["erster_V"] == "00:50" and k["E4"]["letzter_L"] == "00:40", "E4 Kennzahlen"),
        (seq["E5"] == "AAAVV" and k["E5"]["stabil"] == "ja", "E5 numerische Stabilitaet (5 / 5.0)"),
        (s1.get("erster_V") == "00:10" and s1.get("A") == 1 and s1.get("stabil") == "ja" and s1.get("U_anteil") == "0/4"
         and s1.get("rueckfaelle") == "-", "Kenngroessen der separaten Gruppe"),
        (any("00:30 (uebersprungen)" in x for x in out), "Slot ohne Messung im Bericht"),
    ]
    for c, msg in checks:
        print(("OK   " if c else "FAIL ") + msg)
        ok += bool(c)
    print(f"SELBSTTEST {'OK' if ok == len(checks) else 'FEHLGESCHLAGEN'}: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
