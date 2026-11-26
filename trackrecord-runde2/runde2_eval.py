#!/usr/bin/env python3
"""Auswertung Track-Record-Runde 2 nach Präregistrierung
regime-test/docs/preregistration/PRAEREG-TR-RUNDE2-GATES-2026-11-24.md
(Fassung 8, Commit d9c382d, SHA-256 9ce6279a890938ab…).

Drei Modi:
  blind         Nur Struktur: Einheiten, Sperren, auswertbare Tage, Ausschlussgründe,
                Stand des Stichprobenendes. Renditen werden direkt nach dem Laden
                verworfen und nie gelesen. Für die laufende Überwachung.
  exploratory   Runde-1-Zeitraum (Signaltage bis 06.11.2026), in-sample, nicht
                entscheidend. Zum Test des Skripts an bereits gesichteten Daten.
  confirmatory  Primäre Auswertung ab 09.11.2026. Nur mit eingefrorener
                Präregistrierung (Hash-Prüfung) und eingefrorener Kursdatei.

Verweise in Kommentaren: „§5a Nr. x“ bezieht sich auf die Präregistrierung.
Version 1.1.1 (26.11.2026): pending nur aus Long-Einheiten; Provenienz ergänzt; RSP-Prüfung beim Einlesen; Test Kursdatei-Hash
"""
import argparse, ast, hashlib, json, math, platform, sys
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

VERSION = "1.1.1"
PREREG_SHA256 = "9ce6279a890938ab8ceb7627560c61c4a22a6800fe809810d05ce4d185392b37"
SEED = 20261124
N_DRAWS = 10_000
BENCHMARK = "RSP"                       # §5 / §5a Nr. 10
CONF_START = "2026-11-09"               # §2
CONF_CAP = "2027-02-26"                 # §2: spätester Signaltag
EXPL_START, EXPL_END = "2026-07-02", "2026-11-06"
N_PRIMARY_DAYS = 60                     # §2, §6
N_H30_MIN = 30                          # §5 Nr. 2
BETA_DIFF_MAX = 0.10                    # §6
COVERAGE_MIN = 0.80                     # §5a Nr. 9
BETA_WINDOW, BETA_MIN_PAIRS = 60, 40    # §5a Nr. 10
ALPHA = 0.05
NW_LAGS_H30 = 30
HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15"}  # §2

# Zuordnung wie Runde 1 (§3; uiq-devtools/trackrecord-audit/analyze.py, GATE)
STRAT_TO_GATE = {
    "long_minervini": "momentum", "long_breakout": "breakout", "long_swing": "swing",
    "ko_long": "ko", "options_csp": "csp_wheel", "options_atmna": "atmna",
    "options_weekly": "weekly_income", "options_cc": "cc", "long_value": "value",
    "long_dividend": "dividend", "long_mr": "meanrev", "short_fading": "fading_short",
}
# §4: Strategiefamilien (nur explorative Sensitivitätsanalyse)
FAMILY = {
    "momentum": "Trend", "breakout": "Trend", "swing": "Trend", "ko": "Trend",
    "meanrev": "MeanReversion", "fading_short": "MeanReversion",
    "csp_wheel": "Einkommen/Optionen", "atmna": "Einkommen/Optionen",
    "weekly_income": "Einkommen/Optionen", "cc": "Einkommen/Optionen",
    "value": "Value/Dividende", "dividend": "Value/Dividende",
}
# SHA-256 der Basistabelle (kanonisches JSON) laut market_aggregator.py @ b1728e8
GATE_TABLE_SHA256 = "b607f4a06675bcaae95220186dfdb20c0e545585086a36c700d0eb49dc414df6"


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ───────────────────────── Gate-Tabelle ─────────────────────────
def load_gate_table(aggregator_py):
    """Liest _MCM_REGIME_GATES per AST aus market_aggregator.py (kein Import, kein Codeausführen)."""
    tree = ast.parse(open(aggregator_py, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "_MCM_REGIME_GATES" for t in node.targets):
            raw = ast.literal_eval(node.value)
            table = {reg: dict(v["strategies"]) for reg, v in raw.items()}
            canon = json.dumps(table, sort_keys=True, ensure_ascii=False)
            return table, hashlib.sha256(canon.encode()).hexdigest()
    raise SystemExit("_MCM_REGIME_GATES nicht gefunden")


# ───────────────────────── Daten laden ─────────────────────────
def load_backup(path, blind_from=None):
    """tr:snap und tr:eval aus dem KV-Backup. Bei blind_from werden für Signaltage
    >= blind_from alle Rendite-Felder sofort verworfen; erhalten bleibt nur der
    Bewertungsstatus je Horizont: 'pending' (Schlüssel fehlt), 'nodata' (None), 'ok'."""
    K = json.load(open(path))["keys"]
    snaps, evals = {}, {}
    for k, v in K.items():
        if not k.startswith(("tr:snap:", "tr:eval:")):
            continue
        v = json.loads(v) if isinstance(v, str) else v
        td = v["tday"]
        if k.startswith("tr:snap:"):
            snaps[td] = {"regime": v.get("regime"), "recs": [
                {"sym": r.get("sym"), "strat": r.get("strat"), "dir": r.get("dir", 1)} for r in v.get("recs", [])]}
        else:
            recs = {}
            for key, r in v.get("recs", {}).items():
                e = {}
                for H in (7, 30):
                    rk = f"r{H}"
                    if rk not in r:
                        e[f"st{H}"] = "pending"
                    elif r[rk] is None:
                        e[f"st{H}"] = "nodata"
                    else:
                        e[f"st{H}"] = "ok"
                        if blind_from is None or td < blind_from:
                            e[rk] = float(r[rk])
                recs[key] = e
            evals[td] = recs
    del K
    return snaps, evals


def load_prices(csv_path):
    """Eingefrorene Kursdatei (§5a Nr. 10): CSV date,ticker,close (bereinigt)."""
    df = pd.read_csv(csv_path, dtype={"date": str, "ticker": str})
    df = df.dropna(subset=["close"])
    out = {t: g.set_index("date")["close"].sort_index() for t, g in df.groupby("ticker")}
    if BENCHMARK not in out or out[BENCHMARK].empty:
        raise SystemExit(f"Abbruch: Benchmark {BENCHMARK} fehlt in der Kursdatei {csv_path}")
    return out


def load_archive_index(market_series_json):
    """NUR explorativ: Benchmark-Schlusskurse aus den Archiv-Snapshots (Runde 1)."""
    ms = json.load(open(market_series_json))
    s = pd.Series({d: v for d, v in ms["rsp"].items() if v}).sort_index()
    return {BENCHMARK: s}


# ───────────────────────── Kalender, Renditen, Beta ─────────────────────────
def us_sessions(start, end):
    d = pd.bdate_range(start, end).strftime("%Y-%m-%d")
    return [x for x in d if x not in HOLIDAYS]


def bench_return(series, tday, H):
    """Richtungsneutrale Benchmark-Rendite in % über H Bars der eigenen Reihe, Start = Bar am
    oder vor tday (gleiche Logik wie tr_layer._eval_horizon)."""
    idx = series.index
    pos = idx.searchsorted(tday, side="right") - 1
    if pos < 0 or pos + H >= len(series):
        return None
    c0, c1 = series.iloc[pos], series.iloc[pos + H]
    return (c1 / c0 - 1) * 100 if c0 else None


def beta_for(stock, bench, tday):
    """§5a Nr. 10: 61 Benchmark-Sitzungen vor tday (t−61 … t−1) → bis zu 60 Renditen;
    nur gepaarte Renditen (beide Reihen an beiden aufeinanderfolgenden Sitzungen vorhanden);
    OLS mit Achsenabschnitt; < 40 Paare → None."""
    if stock is None:
        return None
    sess = bench.index[bench.index < tday]
    if len(sess) < 2:
        return None
    sess = sess[-(BETA_WINDOW + 1):]
    xs, ys = [], []
    for a, b in zip(sess[:-1], sess[1:]):
        if a in stock.index and b in stock.index:
            sa, sb = stock[a], stock[b]
            ba, bb = bench[a], bench[b]
            if sa and ba:
                ys.append(sb / sa - 1); xs.append(bb / ba - 1)
    if len(xs) < BETA_MIN_PAIRS:
        return None
    x, y = np.asarray(xs), np.asarray(ys)
    vx = np.var(x)
    if vx == 0:
        return None
    return float(np.mean((x - x.mean()) * (y - y.mean())) / vx)


# ───────────────────────── Einheiten und auswertbare Tage ─────────────────────────
def build_units(snaps, evals, table, days, H, released_colors, with_returns, prices, beta_needed, strict_bench=False):
    """Liefert je Signaltag die Long-Einheiten (§5a Nr. 2) und den Tagesstatus (§5a Nr. 6).
    Status: 'auswertbar' oder Ausschlussgrund."""
    bench = prices.get(BENCHMARK) if prices else None
    out = []
    for td in days:
        rec = {"tday": td, "status": None, "reason": None, "units": [], "short_units": [], "nodata_strats": []}
        s = snaps.get(td)
        if s is None:
            rec.update(status="excluded", reason="(a) kein Snapshot"); out.append(rec); continue
        gates = table.get(s["regime"])
        if gates is None:
            rec.update(status="excluded", reason=f"Regime ohne Basistabelle ({s['regime']})"); out.append(rec); continue
        ev = evals.get(td, {})
        by = {}
        for r in s["recs"]:
            gk = STRAT_TO_GATE.get(r["strat"])
            if gk is None:
                continue                                    # außerhalb des Universums (§3)
            by.setdefault((r["strat"], r["dir"]), []).append(r["sym"])
        pending = False; bench_missing = False
        for (strat, d), syms in sorted(by.items()):
            gk = STRAT_TO_GATE[strat]
            color = gates.get(gk)
            u = {"strat": strat, "gate": gk, "color": color, "dir": d,
                 "released": color in released_colors, "n_recs": len(syms),
                 "values": [], "betas": [], "n_ok": 0, "n_nodata": 0, "n_pending": 0}
            for sym in syms:
                e = ev.get(f"{sym}|{strat}", {"st7": "pending", "st30": "pending"})
                st = e[f"st{H}"]
                u[f"n_{'ok' if st == 'ok' else st}"] += 1
                if st == "ok" and with_returns:
                    br = bench_return(bench, td, H) if bench is not None else None
                    if br is None:
                        # Benchmark-Kurs fehlt: kein noData des Titels, sondern Datenfehler der Kursdatei.
                        if strict_bench:
                            raise SystemExit(f"Abbruch: Benchmark-Rendite h{H} für {td} fehlt in der Kursdatei")
                        bench_missing = True
                        continue
                    u["values"].append(e[f"r{H}"] - d * br)              # §5a Nr. 1, richtungsgerecht
                if beta_needed and d == 1 and prices:
                    b = beta_for(prices.get(sym), bench, td)
                    if b is not None:
                        u["betas"].append(b)
            if d == 1:
                pending |= u["n_pending"] > 0          # nur Long bestimmt die Auswertbarkeit (Primärtest nur Long)
            (rec["units"] if d == 1 else rec["short_units"]).append(u)
        L = rec["units"]
        if not L:
            rec.update(status="excluded", reason="keine Long-Einheit"); out.append(rec); continue
        nrel = sum(u["released"] for u in L)
        if nrel == 0 or nrel == len(L):
            rec.update(status="excluded", reason="(b) nicht randomisierbar (" + ("keine freigegebene" if nrel == 0 else "keine gesperrte") + " Long-Einheit)")
            out.append(rec); continue
        if pending:
            rec.update(status="pending", reason=f"h{H} noch nicht fällig"); out.append(rec); continue
        if bench_missing:
            rec.update(status="excluded", reason="Benchmark-Kurs fehlt (nur explorativ möglich)"); out.append(rec); continue
        lost = [u["strat"] for u in L if u["n_ok"] == 0]
        if lost:
            rec.update(status="excluded", reason="(c) Long-Einheit ohne bewertbare Empfehlung (noData)", nodata_strats=lost)
            out.append(rec); continue
        rec["status"] = "auswertbar"
        out.append(rec)
    return out


def primary_sample_days(day_recs):
    """§2: Stichprobe endet mit dem 60. auswertbaren Signaltag (h7, primär), spätestens CONF_CAP."""
    n, last = 0, None
    for r in day_recs:
        if r["status"] == "auswertbar":
            n += 1; last = r["tday"]
            if n == N_PRIMARY_DAYS:
                break
    return n, last


# ───────────────────────── Statistik ─────────────────────────
def newey_west_se(x, L):
    x = np.asarray(x, float); n = len(x)
    if n < 2:
        return float("nan")
    e = x - x.mean()
    s = e @ e / n
    for l in range(1, min(L, n - 1) + 1):
        w = 1 - l / (L + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    return math.sqrt(max(s, 0) / n)


def day_arrays(day_recs):
    """Je auswertbarem Tag: Unit-Werte, Unit-Betas (nan = n. v.), Freigabe in B."""
    D = []
    for r in day_recs:
        if r["status"] != "auswertbar":
            continue
        L = r["units"]
        D.append({"tday": r["tday"],
                  "v": np.array([np.mean(u["values"]) for u in L]),                 # §5a Nr. 4
                  "b": np.array([np.mean(u["betas"]) if u["betas"] else np.nan for u in L]),
                  "rel": np.array([u["released"] for u in L]),
                  "fam": [FAMILY[u["gate"]] for u in L],
                  "strat": [u["strat"] for u in L],
                  "n_recs": [u["n_recs"] for u in L]})
    return D


def draw_masks(D, rng, by_family=False):
    """§4/§5a Nr. 7: je Tag (und optional Familie) genau so viele zufällige Sperren wie in B.
    Ziehungsreihenfolge: Tage chronologisch; je Tag (bzw. je Familie in alphabetischer Ordnung)
    eine Matrix rng.random((N_DRAWS, n)); gesperrt = die k kleinsten Zufallszahlen."""
    masks = []
    for d in D:
        n = len(d["v"]); rel = np.ones((N_DRAWS, n), bool)
        groups = [np.arange(n)] if not by_family else [np.array([i for i in range(n) if d["fam"][i] == f]) for f in sorted(set(d["fam"]))]
        for g in groups:
            k = int((~d["rel"][g]).sum())
            if len(g) < 2:
                rel[:, g] = d["rel"][g]            # nicht randomisierbar → C = B
                continue
            if k == 0:
                continue
            keys = rng.random((N_DRAWS, len(g)))
            locked = np.argsort(keys, axis=1)[:, :k]
            sub = np.ones((N_DRAWS, len(g)), bool)
            np.put_along_axis(sub, locked, False, axis=1)
            rel[:, g] = sub
        masks.append(rel)
    return masks


def analyse(D, masks, nw_lags=None):
    """Teststatistik §5a Nr. 7, Beta §5a Nr. 9, Risiko §5 Nr. 3."""
    nd = len(D)
    B = np.array([d["v"][d["rel"]].mean() for d in D])
    A = np.array([d["v"].mean() for d in D])
    C = np.stack([(m * d["v"]).sum(1) / m.sum(1) for d, m in zip(D, masks)], axis=1)   # (J, nd)
    Cbar = C.mean(0)
    T = float(np.mean(B - Cbar))
    Tj = (C - Cbar).mean(1)
    p = float((1 + np.sum(np.abs(Tj) >= abs(T))) / (N_DRAWS + 1))
    diff = B - Cbar
    se_day = float(diff.std(ddof=1) / math.sqrt(nd)) if nd > 1 else float("nan")
    res = {"n_days": nd, "B_mean": float(B.mean()), "C_mean": float(Cbar.mean()), "A_mean": float(A.mean()),
           "T": T, "p_perm": p, "se_cluster_day": se_day}
    if nw_lags:
        res["se_newey_west"] = newey_west_se(diff, nw_lags)
    # B − A beschreibend (§5 Nr. 2)
    ba = B - A
    res["B_minus_A"] = float(ba.mean())
    res["B_minus_A_se"] = float(ba.std(ddof=1) / math.sqrt(nd)) if nd > 1 else float("nan")
    if nw_lags:
        res["B_minus_A_se_nw"] = newey_west_se(ba, nw_lags)
    # Risiko je Arm (C: je Ziehung berechnet, dann gemittelt)
    def risk(x):
        return {"sd": float(np.std(x, ddof=1)) if len(x) > 1 else float("nan"), "worst": float(np.min(x)), "share_neg": float(np.mean(x < 0))}
    res["risk"] = {"A": risk(A), "B": risk(B),
                   "C": {"sd": float(np.mean(C.std(1, ddof=1))) if nd > 1 else float("nan"),
                         "worst": float(np.mean(C.min(1))), "share_neg": float(np.mean((C < 0).mean(1)))}}
    # Beta (§5a Nr. 9): je Tag gleichgewichtet über freigegebene Einheiten mit Beta, dann über Tage;
    # Einheit ohne Beta = n. v. (nie null); Abdeckung = Einheiten mit Beta / freigegebene Einheiten.
    def arm_beta_vec(relstack):
        """relstack: Liste je Tag von bool-Arrays (J, n). Liefert je Ziehung Armbeta und Abdeckung."""
        J = relstack[0].shape[0]
        num_days = np.zeros(J); valid_days = np.zeros(J); cov_num = np.zeros(J); cov_den = np.zeros(J)
        for d, m in zip(D, relstack):
            has = ~np.isnan(d["b"]); bz = np.where(has, d["b"], 0.0)
            cnt = m @ has.astype(float); sm = m @ bz
            ok = cnt > 0
            num_days += np.where(ok, sm / np.where(ok, cnt, 1), 0.0); valid_days += ok
            cov_num += cnt; cov_den += m.sum(1)
        beta = np.where(valid_days > 0, num_days / np.where(valid_days > 0, valid_days, 1), np.nan)
        cov = np.where(cov_den > 0, cov_num / np.where(cov_den > 0, cov_den, 1), np.nan)
        return beta, cov
    bB, covB = arm_beta_vec([d["rel"][None, :] for d in D])
    bCj, covCj = arm_beta_vec(masks)
    res["beta"] = {"beta_B": float(bB[0]), "beta_C": float(np.nanmean(bCj)) if np.any(~np.isnan(bCj)) else float("nan"),
                   "coverage_B": float(covB[0]), "coverage_C_mean": float(np.nanmean(covCj)),
                   "coverage_C_dist": {"min": float(np.nanmin(covCj)), "p5": float(np.nanpercentile(covCj, 5)),
                                       "p95": float(np.nanpercentile(covCj, 95)), "max": float(np.nanmax(covCj))}}
    res["beta"]["abs_diff"] = abs(res["beta"]["beta_B"] - res["beta"]["beta_C"])
    return res


def decide(res, n_auswertbar):
    """§6 Entscheidungsregel."""
    b = res["beta"]
    cov_ok = (b["coverage_B"] >= COVERAGE_MIN) and (b["coverage_C_mean"] >= COVERAGE_MIN)
    beta_ok = cov_ok and not math.isnan(b["abs_diff"]) and b["abs_diff"] < BETA_DIFF_MAX
    days_ok = n_auswertbar >= N_PRIMARY_DAYS
    sig = res["p_perm"] < ALPHA
    reasons = {"coverage_ok": bool(cov_ok), "beta_ok": bool(beta_ok), "days_ok": bool(days_ok), "significant": bool(sig)}
    if sig and beta_ok and days_ok and res["T"] > 0:
        return "Vorteil der dokumentierten Basis-Gate-Auswahl", reasons
    if sig and beta_ok and days_ok and res["T"] < 0:
        return "Dokumentierte Basis-Gate-Auswahl schlechter als Zufall", reasons
    return "nicht entscheidbar", reasons


def diagnostics(day_recs):
    """§5 Nr. 4 und Ausschlussbericht §5a Nr. 6 (ohne Renditen)."""
    from collections import Counter
    st = Counter(r["status"] for r in day_recs)
    reasons = Counter(r["reason"] for r in day_recs if r["status"] == "excluded")
    nd_str = Counter(s for r in day_recs for s in r["nodata_strats"])
    ev = [r for r in day_recs if r["status"] == "auswertbar"]
    units = [u for r in ev for u in r["units"]]
    rel = [u for u in units if u["released"]]
    mix = lambda us: dict(Counter(u["strat"] for u in us))
    return {"days_by_status": dict(st), "excluded_by_reason": dict(reasons),
            "excluded_noData_by_strategy": dict(nd_str),
            "long_units": len(units), "long_recs": sum(u["n_recs"] for u in units),
            "lock_rate_long": (1 - len(rel) / len(units)) if units else None,
            "mix_A": mix(units), "mix_B": mix(rel),
            "short_units": sum(len(r["short_units"]) for r in ev),
            "short_released": sum(u["released"] for r in ev for u in r["short_units"]),
            "recs_nodata_long": sum(u["n_nodata"] for u in units)}


def short_descriptive(day_recs):
    """§4: Short-Seite nur deskriptiv (B gegen A, Tagesmittel)."""
    A, B = [], []
    for r in day_recs:
        if r["status"] != "auswertbar":
            continue
        S = [u for u in r["short_units"] if u["values"]]
        if not S:
            continue
        A.append(np.mean([np.mean(u["values"]) for u in S]))
        rel = [np.mean(u["values"]) for u in S if u["released"]]
        if rel:
            B.append(np.mean(rel))
    return {"days_A": len(A), "A_mean": float(np.mean(A)) if A else None,
            "days_B": len(B), "B_mean": float(np.mean(B)) if B else None}


# ───────────────────────── Ablauf ─────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=["blind", "exploratory", "confirmatory"])
    ap.add_argument("--backup", required=True, help="tr-Backup (JSON mit 'keys')")
    ap.add_argument("--gates-from", required=True, help="market_aggregator.py @ b1728e8")
    ap.add_argument("--prices", help="eingefrorene Kursdatei (CSV date,ticker,close), §5a Nr. 10")
    ap.add_argument("--archive-index", help="NUR exploratory: market_series.json aus Runde 1")
    ap.add_argument("--prereg", help="Präregistrierung (Pflicht für confirmatory, Hash-Prüfung)")
    ap.add_argument("--day-quality", help="CSV tday,quality für D26-Empfindlichkeit (§6)")
    ap.add_argument("--asof", default=None, help="Stichtag des Laufs (Standard: heute)")
    ap.add_argument("--expl-range", nargs=2, default=[EXPL_START, EXPL_END], help="nur exploratory: Signaltage von bis (innerhalb Runde 1)")
    ap.add_argument("--out", required=True, help="Ausgabedatei (JSON)")
    a = ap.parse_args(argv)

    prov = {"script_version": VERSION, "script_sha256": sha256_file(__file__),
            "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "rng": "numpy.random.default_rng (PCG64)", "seed": SEED, "draws": N_DRAWS,
            "mode": a.mode, "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "backup_sha256": sha256_file(a.backup),
            "args": {"asof": a.asof, "expl_range": a.expl_range if a.mode == "exploratory" else None,
                     "backup": a.backup, "gates_from": a.gates_from, "prices": a.prices,
                     "archive_index": a.archive_index, "day_quality": a.day_quality, "prereg": a.prereg}}
    if a.archive_index:
        prov["archive_index_sha256"] = sha256_file(a.archive_index)
    if a.day_quality:
        prov["day_quality_sha256"] = sha256_file(a.day_quality)
    table, table_hash = load_gate_table(a.gates_from)
    prov["gates_from_sha256"] = sha256_file(a.gates_from); prov["gate_table_sha256"] = table_hash
    if table_hash != GATE_TABLE_SHA256:
        msg = f"Basistabelle weicht vom Stand b1728e8 ab ({table_hash[:16]} statt {GATE_TABLE_SHA256[:16]})"
        if a.mode == "confirmatory":
            raise SystemExit("Abbruch: " + msg)
        prov["warning"] = msg

    if a.mode == "confirmatory":
        if not a.prereg or sha256_file(a.prereg) != PREREG_SHA256:
            raise SystemExit("Abbruch: Präregistrierung fehlt oder Hash weicht ab.")
        if not a.prices:
            raise SystemExit("Abbruch: eingefrorene Kursdatei fehlt (§5a Nr. 10).")
        if a.archive_index:
            raise SystemExit("Abbruch: Archiv-Benchmark ist nur explorativ zulässig.")
        prov["prereg_sha256"] = PREREG_SHA256
    if a.mode == "exploratory" and not (a.prices or a.archive_index):
        raise SystemExit("exploratory braucht --prices oder --archive-index")

    blind_from = CONF_START if a.mode in ("blind", "exploratory") else None
    snaps, evals = load_backup(a.backup, blind_from=blind_from)
    prices = None
    if a.prices:
        prices = load_prices(a.prices)
        prov["prices_sha256"] = sha256_file(a.prices)          # Hash der eingefrorenen Kursdatei
    elif a.archive_index:
        prices = load_archive_index(a.archive_index); prov["benchmark_source"] = "Archiv-Snapshots (nur explorativ)"
    if a.mode == "exploratory":
        lo, hi = max(a.expl_range[0], EXPL_START), min(a.expl_range[1], EXPL_END)
        days = [d for d in sorted(snaps) if lo <= d <= hi]
    else:
        asof = a.asof or date.today().isoformat()
        prov["args"]["asof_effective"] = asof
        days = us_sessions(CONF_START, min(CONF_CAP, asof))

    report = {"provenance": prov}
    with_ret = a.mode != "blind"
    for H, colors, label in [(7, {"green"}, "primary_h7_green"), (7, {"green", "amber"}, "secondary_h7_green_amber"),
                             (30, {"green"}, "secondary_h30_green")]:
        dr = build_units(snaps, evals, table, days, H, colors, with_ret, prices,
                         beta_needed=with_ret and label == "primary_h7_green", strict_bench=a.mode == "confirmatory")
        if label == "primary_h7_green":
            n_aus, last = primary_sample_days(dr)
            report["sample"] = {"auswertbar": n_aus, "last_sample_day": last, "needed": N_PRIMARY_DAYS, "cap": CONF_CAP,
                                "complete": n_aus >= N_PRIMARY_DAYS}
            if a.mode != "exploratory" and last:
                end = last if n_aus >= N_PRIMARY_DAYS else None
                sample_days = [d for d in days if end is None or d <= end]
            else:
                sample_days = days
        dr = [r for r in dr if r["tday"] in set(sample_days)]
        sec = {"diagnostics": diagnostics(dr)}
        if with_ret:
            D = day_arrays(dr)
            if (H == 30 and len(D) < N_H30_MIN) or len(D) < 2:
                sec["result"] = f"nicht berichtet: {len(D)} auswertbare Tage"
            else:
                rng = np.random.default_rng(SEED)            # §5a Nr. 11, je Analyse neu
                res = analyse(D, draw_masks(D, rng), nw_lags=NW_LAGS_H30 if H == 30 else None)
                sec["result"] = res
                if label == "primary_h7_green":
                    verdict, why = decide(res, len(D))
                    if a.mode == "exploratory":
                        sec["verdict"] = f"(explorativ, nicht entscheidend) {verdict}"
                    elif not report["sample"]["complete"]:
                        sec["verdict"] = "kein Urteil: Stichprobe unvollständig (Zwischenstand, deskriptiv)"
                    else:
                        sec["verdict"] = verdict
                    sec["verdict_conditions"] = why
                    sec["short_descriptive"] = short_descriptive(dr)
                    rngf = np.random.default_rng(SEED)
                    sec["exploratory_family_strata"] = analyse(D, draw_masks(D, rngf, by_family=True))
                    if a.day_quality:
                        q = pd.read_csv(a.day_quality, dtype=str).set_index("tday")["quality"].to_dict()
                        D2 = [d for d in D if q.get(d["tday"]) != "D26_stale"]
                        if len(D2) >= 2:
                            rng2 = np.random.default_rng(SEED)
                            sec["sensitivity_without_D26"] = analyse(D2, draw_masks(D2, rng2))
        report[label] = sec
    json.dump(report, open(a.out, "w"), indent=2, ensure_ascii=False, default=str)
    print(json.dumps({"mode": a.mode, "sample": report["sample"],
                      "primary_days": report["primary_h7_green"]["diagnostics"]["days_by_status"]}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
