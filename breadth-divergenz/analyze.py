#!/usr/bin/env python3
"""
breadth-divergenz/analyze.py — Breite-Divergenz & DCE-Konstanz aus dem Snapshot-Archiv

Version: 1.0.1 (29.09.2026, Claude + Axel)
         1.0.1: H6 → H12 (Umbenennung Roadmap 0fff17f); UIQ_SNAPSHOT_DIR für
         das private Archiv uiq-archive (Snapshots seit 29.09. nur noch dort).
Zweck:   Reproduzierbare Rechnung zu Forschungsnotiz H12 (Regime-Roadmap,
         UIQ-Suite/docs/REGIME-BACKTEST-ROADMAP-2026-09-27.md) und zum
         Befund, dass die DCE-Richtung im Archivzeitraum konstant war.
         Rein deskriptiv, read-only, kein Renditetest, keine Signale.

Quelle:  <snapshot-dir>/*.json.gz (gzip'd master_market_data). Standard:
         <arbeitsordner>/ko-aggregator/data/snapshots; ab 29.09.2026 liegen
         neue Snapshots nur noch im privaten Repo uiq-archive →
         UIQ_SNAPSHOT_DIR=/pfad/zu/uiq-archive/data/snapshots setzen.

Methodik (fest, keine Parameter-Suche):
  * Je Handelstag (meta.last_trading_day) zählt der zuletzt erzeugte
    Snapshot (meta.generated). Wochenend-/Wiederholungsläufe desselben
    Handelstags werden so zu einem Punkt zusammengefasst.
  * DCE-Konsens wird aus den Tickerdaten exakt wie in
    dce_layer._aggregate_ticker_signals() nachgerechnet (Mittel aus
    sMinervini/sSwing/sBreakout/confluenceScore je Titel, /100, dann
    Mittel über alle Titel; bullish = Anteil Titel > 0,55) und gegen
    das gespeicherte dce.aggregated verglichen (Selbsttest).
  * Breite = Anteil Titel mit dist50 > 0 bzw. dist200 > 0 (Nenner:
    Titel mit vorhandenem Feld).
  * Index-/Sektor-ETF-Vergleich über Schlusskurse (Feld price) zwischen
    VON und BIS. Standard: VON = Handelstag des höchsten SPY-Kurses im
    Archiv, BIS = letzter Handelstag.
  * Sektor-Zerlegung auf dem GEMEINSAMEN Universum beider Stichtage
    (Titel, die an beiden Tagen vorkommen). Ein Titel mit mehreren
    Sektor-Tags zählt in jedem seiner Tags (Mehrfachzugehörigkeit);
    Titel ohne Tag laufen als "(ohne Tag)". Sektoren mit n < 5 werden
    nicht einzeln ausgewiesen.

Nutzung:
    python3 breadth-divergenz/analyze.py [--von YYYY-MM-DD] [--bis YYYY-MM-DD]
    Abweichendes Layout: UIQ_WORKSPACE_ROOT=/pfad python3 ...
    Privates Archiv:     UIQ_SNAPSHOT_DIR=/pfad/uiq-archive/data/snapshots python3 ...
Ausgabe: breadth-divergenz/report.md + report.json (per .gitignore
         ausgeschlossen, Wegwerf-Artefakte pro Lauf).
Abhängigkeiten: nur Python-Standardbibliothek (>= 3.8).
"""

import argparse
import glob
import gzip
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

VERSION = "1.0.1"
SCORE_KEYS = ("sMinervini", "sSwing", "sBreakout", "confluenceScore")
ETFS = [
    ("SPY", "S&P 500 (kapitalgewichtet)"), ("RSP", "S&P 500 (gleichgewichtet)"),
    ("QQQ", "Nasdaq-100"), ("DIA", "Dow Jones"), ("MDY", "S&P MidCap 400"),
    ("IWM", "Russell 2000"), ("XLK", "Technologie"), ("SMH", "Halbleiter"),
    ("XLC", "Kommunikation"), ("XLY", "Zyklischer Konsum"), ("XLP", "Basiskonsum"),
    ("XLE", "Energie"), ("XLF", "Finanzen"), ("XLV", "Gesundheit"),
    ("XLI", "Industrie"), ("XLB", "Grundstoffe"), ("XLU", "Versorger"),
    ("XLRE", "Immobilien"), ("ITA", "Luftfahrt & Verteidigung"),
    ("GLD", "Gold"), ("TLT", "US-Staatsanl. 20J+"), ("IEF", "US-Staatsanl. 7–10J"),
]
MIN_SECTOR_N = 5
DCE_BUY_THRESHOLD = 0.55   # dce_layer._derive_action()
DCE_SELL_THRESHOLD = 0.45


def workspace_root():
    env = os.environ.get("UIQ_WORKSPACE_ROOT")
    if env:
        return os.path.abspath(env)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_archive(snap_dir):
    files = sorted(glob.glob(os.path.join(snap_dir, "*.json.gz")))
    if not files:
        sys.exit(f"Keine Snapshots gefunden in {snap_dir}")
    per_day = {}
    unreadable = []
    for f in files:
        try:
            with gzip.open(f) as fh:
                d = json.load(fh)
        except Exception as e:  # defekte Datei sichtbar machen, nicht still überspringen
            unreadable.append((os.path.basename(f), str(e)))
            continue
        meta = d.get("meta") or {}
        day = meta.get("last_trading_day")
        gen = meta.get("generated") or ""
        if not day:
            unreadable.append((os.path.basename(f), "meta.last_trading_day fehlt"))
            continue
        if day not in per_day or gen > per_day[day][0]:
            per_day[day] = (gen, os.path.basename(f), d)
    return files, per_day, unreadable


def ticker_consensus(tickers):
    scores = []
    for r in tickers:
        vals = [r[k] for k in SCORE_KEYS if r.get(k) is not None]
        if vals:
            scores.append(sum(vals) / len(vals) / 100.0)
    if not scores:
        return None, None, 0
    mean = statistics.mean(scores)
    bull = sum(1 for s in scores if s > DCE_BUY_THRESHOLD) / len(scores)
    return mean, bull, len(scores)


def share_positive(tickers, key):
    vals = [r[key] for r in tickers if r.get(key) is not None]
    return (sum(1 for v in vals if v > 0) / len(vals)) if vals else None


def by_sym(tickers):
    return {r["sym"]: r for r in tickers if r.get("sym")}


def daily_rows(per_day):
    rows = []
    for day in sorted(per_day):
        gen, fname, d = per_day[day]
        T = d.get("tickers") or []
        cons, bull, n_sc = ticker_consensus(T)
        dce = d.get("dce") or {}
        agg = dce.get("aggregated") or {}
        syms = by_sym(T)
        rows.append({
            "trading_day": day, "snapshot": fname, "generated": gen,
            "n_tickers": len(T), "n_scored": n_sc,
            "consensus_recalc": round(cons, 3) if cons is not None else None,
            "bullish_recalc": round(bull, 3) if bull is not None else None,
            "dce_consensus": agg.get("consensus"),
            "dce_direction": dce.get("direction"), "dce_mode": dce.get("mode"),
            "dce_confidence": dce.get("confidence"),
            "share_above_ema50": share_positive(T, "dist50"),
            "share_above_ema200": share_positive(T, "dist200"),
            "regime": (d.get("meta") or {}).get("regimeUsed"),
            "spy": (syms.get("SPY") or {}).get("price"),
            "rsp": (syms.get("RSP") or {}).get("price"),
        })
    return rows


def sector_tags(r):
    s = r.get("sectors")
    if isinstance(s, list) and s:
        return s
    if isinstance(s, str) and s:
        return [s]
    return ["(ohne Tag)"]


def sector_breakdown(ta, tb):
    a, b = by_sym(ta), by_sym(tb)
    common = sorted(set(a) & set(b))
    groups = defaultdict(list)
    for s in common:
        for tag in sector_tags(b[s]):
            groups[tag].append(s)
    out = []
    for tag, syms in groups.items():
        if len(syms) < MIN_SECTOR_N:
            continue

        def share(D, key):
            vals = [D[x].get(key) for x in syms if D[x].get(key) is not None]
            return (sum(1 for v in vals if v > 0) / len(vals)) if vals else None

        e50a, e50b = share(a, "dist50"), share(b, "dist50")
        out.append({
            "sector": tag, "n": len(syms),
            "ema50_von": e50a, "ema50_bis": e50b,
            "ema50_delta": (e50b - e50a) if None not in (e50a, e50b) else None,
            "ema200_von": share(a, "dist200"), "ema200_bis": share(b, "dist200"),
        })
    out.sort(key=lambda x: (x["ema50_delta"] is None, x["ema50_delta"] or 0))
    untagged = sum(1 for s in common if sector_tags(b[s]) == ["(ohne Tag)"])
    return out, len(common), untagged


def etf_table(ta, tb):
    a, b = by_sym(ta), by_sym(tb)
    rows = []
    for sym, name in ETFS:
        pa, pb = (a.get(sym) or {}).get("price"), (b.get(sym) or {}).get("price")
        rows.append({
            "sym": sym, "name": name, "von": pa, "bis": pb,
            "ret": (pb / pa - 1) if pa and pb else None,
            "dist50_bis": (b.get(sym) or {}).get("dist50"),
        })
    return rows


def pct(x, digits=0, sign=False):
    if x is None:
        return "n/v"
    return f"{x * 100:+.{digits}f} %" if sign else f"{x * 100:.{digits}f} %"


def weekday_gaps(days):
    """Werktage ohne Snapshot zwischen erstem und letztem Handelstag.
    Enthält auch US-Börsenfeiertage (z. B. Labor Day) – bewusst ohne
    Feiertagskalender, damit echte Archivlücken nicht verdeckt werden."""
    from datetime import date, timedelta
    have = set(days)
    d, end = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    gaps = []
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in have:
            gaps.append(d.isoformat())
        d += timedelta(days=1)
    return gaps


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--von", help="Start-Handelstag (Standard: SPY-Hoch im Archiv)")
    ap.add_argument("--bis", help="End-Handelstag (Standard: letzter Handelstag)")
    args = ap.parse_args()

    root = workspace_root()
    snap_dir = os.environ.get("UIQ_SNAPSHOT_DIR") or os.path.join(root, "ko-aggregator", "data", "snapshots")
    files, per_day, unreadable = load_archive(snap_dir)
    rows = daily_rows(per_day)
    days = [r["trading_day"] for r in rows]

    # ── Selbsttest: Nachrechnung == gespeicherter DCE-Konsens ──────────────
    mismatches = [r for r in rows if r["dce_consensus"] is not None
                  and r["consensus_recalc"] is not None
                  and abs(r["dce_consensus"] - r["consensus_recalc"]) > 0.0015]

    # ── DCE-Konstanz ───────────────────────────────────────────────────────
    dce_rows = [r for r in rows if r["dce_direction"] is not None]
    dir_count = Counter(r["dce_direction"] for r in dce_rows)
    mode_count = Counter(r["dce_mode"] for r in dce_rows)
    combo_count = Counter((r["dce_direction"], r["dce_mode"]) for r in dce_rows)
    cons_vals = [r["dce_consensus"] for r in dce_rows if r["dce_consensus"] is not None]
    conf_vals = [r["dce_confidence"] for r in dce_rows if r["dce_confidence"] is not None]
    regime_count = Counter(r["regime"] for r in rows)

    # ── Stichtage ──────────────────────────────────────────────────────────
    spy_rows = [r for r in rows if r["spy"] is not None]
    von = args.von or max(spy_rows, key=lambda r: r["spy"])["trading_day"]
    bis = args.bis or days[-1]
    for label, d in (("--von", von), ("--bis", bis)):
        if d not in per_day:
            sys.exit(f"{label} {d}: kein Snapshot für diesen Handelstag. Verfügbar: {days[0]} … {days[-1]}")
    ta, tb = per_day[von][2]["tickers"], per_day[bis][2]["tickers"]
    etfs = etf_table(ta, tb)
    sectors, n_common, n_untagged = sector_breakdown(ta, tb)
    r_von = next(r for r in rows if r["trading_day"] == von)
    r_bis = next(r for r in rows if r["trading_day"] == bis)

    report = {
        "version": VERSION, "snapshot_dir": snap_dir,
        "files_total": len(files), "trading_days": len(rows),
        "unreadable": unreadable, "window": [days[0], days[-1]],
        "selftest_consensus_mismatches": [r["trading_day"] for r in mismatches],
        "dce": {"days_with_dce": len(dce_rows), "direction": dict(dir_count),
                "mode": dict(mode_count),
                "combo": {f"{k[0]}/{k[1]}": v for k, v in combo_count.items()},
                "consensus_min": min(cons_vals) if cons_vals else None,
                "consensus_max": max(cons_vals) if cons_vals else None,
                "confidence_min": min(conf_vals) if conf_vals else None,
                "confidence_max": max(conf_vals) if conf_vals else None},
        "regime_days": dict(regime_count),
        "von": von, "bis": bis, "etfs": etfs,
        "breadth_von": {"ema50": r_von["share_above_ema50"], "ema200": r_von["share_above_ema200"]},
        "breadth_bis": {"ema50": r_bis["share_above_ema50"], "ema200": r_bis["share_above_ema200"]},
        "sectors": sectors, "sector_common_universe": n_common,
        "sector_untagged": n_untagged, "daily": rows,
    }

    # ── Markdown ───────────────────────────────────────────────────────────
    L = []
    L.append(f"# Breite-Divergenz & DCE-Konstanz — Report (analyze.py {VERSION})\n")
    L.append(f"Archiv: `{snap_dir}` · {len(files)} Dateien · {len(rows)} Handelstage "
             f"({days[0]} … {days[-1]})")
    gaps = weekday_gaps(days)
    report["weekday_gaps"] = gaps
    if gaps:
        L.append(f"\nWerktage ohne Snapshot ({len(gaps)}, inkl. evtl. US-Feiertage): " + ", ".join(gaps))
    if unreadable:
        L.append(f"\n**Nicht lesbar ({len(unreadable)}):** " + "; ".join(f"{a} ({b})" for a, b in unreadable))
    L.append(f"\n**Selbsttest DCE-Konsens (Nachrechnung vs. gespeichert, Toleranz 0,0015):** "
             + ("✅ identisch an allen Tagen mit DCE" if not mismatches
                else f"❌ Abweichung an {len(mismatches)} Tagen: " + ", ".join(r['trading_day'] for r in mismatches)))

    L.append("\n## 1 · DCE-Ausgabe im Archivzeitraum\n")
    L.append(f"Tage mit DCE-Objekt: {len(dce_rows)}")
    L.append(f"- Richtung/Ampel: " + ", ".join(f"{k} = {v}" for k, v in report['dce']['combo'].items()))
    if cons_vals:
        L.append(f"- Konsens-Spanne: {min(cons_vals):.3f} … {max(cons_vals):.3f} "
                 f"(BUY erst > {DCE_BUY_THRESHOLD}, SELL < {DCE_SELL_THRESHOLD})")
    if conf_vals:
        L.append(f"- Confidence-Spanne: {min(conf_vals)} … {max(conf_vals)}")
    L.append(f"- Regime (meta.regimeUsed) je Handelstag: " + ", ".join(f"{k} = {v}" for k, v in regime_count.items()))
    if len(combo_count) == 1:
        L.append("\n**Befund:** Richtung und Ampel waren an allen Tagen identisch – im Archivzeitraum "
                 "trägt die DCE-Richtung keine Information (Messartefakt, s. Befundregister D4).")

    L.append(f"\n## 2 · Index- und Sektor-ETFs {von} → {bis}\n")
    L.append("| ETF | Bezeichnung | Veränderung | Abstand EMA50 (bis) |")
    L.append("|---|---|---|---|")
    for e in etfs:
        d50 = "n/v" if e["dist50_bis"] is None else f"{e['dist50_bis']:+.2f} %"
        L.append(f"| {e['sym']} | {e['name']} | {pct(e['ret'], 1, sign=True)} | {d50} |")
    spy = next(e for e in etfs if e["sym"] == "SPY")
    rsp = next(e for e in etfs if e["sym"] == "RSP")
    if spy["ret"] is not None and rsp["ret"] is not None:
        L.append(f"\nSpread SPY − RSP: **{(spy['ret'] - rsp['ret']) * 100:+.1f} Pp** "
                 "(positiv = kapitalgewichteter Index läuft dem Durchschnittstitel voraus)")

    L.append(f"\n## 3 · Breite des Universums\n")
    L.append(f"| Stichtag | Titel | > EMA50 | > EMA200 | SPY |")
    L.append("|---|---|---|---|---|")
    for r in (r_von, r_bis):
        L.append(f"| {r['trading_day']} | {r['n_tickers']} | {pct(r['share_above_ema50'])} | "
                 f"{pct(r['share_above_ema200'])} | {r['spy']} |")
    if r_von["n_tickers"] != r_bis["n_tickers"]:
        L.append(f"\n⚠ Universum verändert ({r_von['n_tickers']} → {r_bis['n_tickers']} Titel) – "
                 "Breitenwerte sind nur eingeschränkt vergleichbar; Sektor-Zerlegung unten nutzt das gemeinsame Universum.")

    L.append(f"\n## 4 · Sektor-Zerlegung (gemeinsames Universum, n = {n_common})\n")
    L.append(f"Ohne Sektor-Tag: **{n_untagged} von {n_common} ({n_untagged / n_common * 100:.0f} %)** – "
             "für diese Titel ist keine Sektor-Zuordnung möglich. Mehrfach-Tags zählen je Tag.\n")
    L.append("| Sektor-Tag | n | > EMA50 von → bis | Δ | > EMA200 von → bis |")
    L.append("|---|---|---|---|---|")
    for s in sectors:
        delta = "n/v" if s["ema50_delta"] is None else f"{s['ema50_delta'] * 100:+.0f} Pp"
        L.append(f"| {s['sector']} | {s['n']} | {pct(s['ema50_von'])} → {pct(s['ema50_bis'])} | "
                 f"{delta} | {pct(s['ema200_von'])} → {pct(s['ema200_bis'])} |")

    L.append("\n## 5 · Tagesreihe\n")
    L.append("| Handelstag | n | Konsens | bullish | Richtung | Ampel | Conf | > EMA50 | > EMA200 | SPY | RSP | Regime |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append(f"| {r['trading_day']} | {r['n_tickers']} | {r['consensus_recalc']} | {r['bullish_recalc']} | "
                 f"{r['dce_direction'] or '–'} | {r['dce_mode'] or '–'} | {r['dce_confidence'] if r['dce_confidence'] is not None else '–'} | "
                 f"{pct(r['share_above_ema50'])} | {pct(r['share_above_ema200'])} | {r['spy']} | {r['rsp']} | {r['regime']} |")

    L.append("\n---\nRein deskriptiv. Keine Prognose, kein Renditetest, keine Handlungsempfehlung. "
             "Ob eine Breite-Divergenz Prognosewert hat, ist Gegenstand von H12 (Regime-Roadmap) "
             "und nur nach Präregistrierung zu testen.")

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(os.path.join(here, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print(f"analyze.py {VERSION}: {len(rows)} Handelstage, Fenster {von} → {bis}")
    print(f"  Selbsttest DCE-Konsens: {'OK' if not mismatches else f'{len(mismatches)} Abweichungen'}")
    print(f"  DCE Richtung/Ampel: {report['dce']['combo']}")
    print(f"  SPY {pct(spy['ret'], 1, sign=True)} · RSP {pct(rsp['ret'], 1, sign=True)} · "
          f"> EMA50 {pct(r_von['share_above_ema50'])} → {pct(r_bis['share_above_ema50'])}")
    print(f"  Report: {os.path.join(here, 'report.md')}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
