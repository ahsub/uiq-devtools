"""Tests für runde2_eval.py mit synthetischen Daten (keine echten Renditen).
Aufruf: python3 -m pytest -q test_runde2.py   oder   python3 test_runde2.py"""
import json, math, os, sys, tempfile
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runde2_eval as R
import fetch_prices as F

TABLE = {"BULL_QUIET": {"momentum": "green", "breakout": "green", "swing": "green", "ko": "green",
                        "csp_wheel": "green", "atmna": "green", "weekly_income": "green", "cc": "green",
                        "value": "amber", "dividend": "amber", "meanrev": "red", "fading_short": "red"}}


def _day(v, rel, b=None, fam=None):
    n = len(v)
    return {"tday": "x", "v": np.array(v, float), "rel": np.array(rel, bool),
            "b": np.array(b if b is not None else [1.0] * n, float),
            "fam": fam or ["Trend"] * n, "strat": [f"s{i}" for i in range(n)], "n_recs": [1] * n}


def test_identical_values_give_T0_p1():
    D = [_day([1, 1, 1, 1], [1, 1, 0, 0]) for _ in range(5)]
    res = R.analyse(D, R.draw_masks(D, np.random.default_rng(R.SEED)))
    assert abs(res["T"]) < 1e-12 and res["p_perm"] == 1.0


def test_lock_count_preserved_and_centering():
    D = [_day([0, 1, 2, 3, 4], [1, 1, 1, 0, 0]), _day([5, 6, 7], [1, 0, 1])]
    masks = R.draw_masks(D, np.random.default_rng(R.SEED))
    for d, m in zip(D, masks):
        assert m.shape == (R.N_DRAWS, len(d["v"]))
        assert np.all(m.sum(1) == d["rel"].sum())           # gleiche Freigabezahl wie B (§4)
    res = R.analyse(D, masks)
    # Erwartung von C je Tag ≈ Mittel aller Einheiten (Zufallsauswahl)
    assert abs(res["C_mean"] - np.mean([np.mean(d["v"]) for d in D])) < 0.05
    # B = Mittel der freigegebenen Einheiten
    assert abs(res["B_mean"] - np.mean([1.0, 6.0])) < 1e-12


def test_T_by_hand_and_p_formula():
    D = [_day([10, 0], [1, 0]) for _ in range(3)]          # B wählt immer den besseren
    res = R.analyse(D, R.draw_masks(D, np.random.default_rng(R.SEED)))
    assert abs(res["B_mean"] - 10) < 1e-12 and abs(res["C_mean"] - 5) < 0.2
    # Nur 2^3 = 8 mögliche Ziehungsmuster; |T_j| >= |T| nur, wenn alle drei Tage die 10 freigeben
    assert 0.08 < res["p_perm"] < 0.17


def test_reproducible_seed():
    D = [_day(np.arange(6), [1, 1, 1, 0, 0, 1]) for _ in range(4)]
    r1 = R.analyse(D, R.draw_masks(D, np.random.default_rng(R.SEED)))
    r2 = R.analyse(D, R.draw_masks(D, np.random.default_rng(R.SEED)))
    assert r1["p_perm"] == r2["p_perm"] and r1["T"] == r2["T"]


def test_beta_nv_and_coverage():
    D = [_day([1, 2, 3], [1, 0, 1], b=[1.0, np.nan, np.nan])]
    res = R.analyse(D, R.draw_masks(D, np.random.default_rng(R.SEED)))
    assert res["beta"]["beta_B"] == 1.0                       # nan nie als 0
    assert abs(res["beta"]["coverage_B"] - 0.5) < 1e-12
    assert abs(res["beta"]["coverage_C_mean"] - 1 / 3) < 0.02


def test_beta_ols_pairs_and_window():
    sess = R.us_sessions("2026-07-01", "2026-11-06")
    rng = np.random.default_rng(1)
    rb = rng.normal(0, 0.01, len(sess)); bench = pd.Series(100 * np.cumprod(1 + rb), index=sess)
    stock = pd.Series(50 * np.cumprod(1 + 1.5 * rb + rng.normal(0, 0.001, len(sess))), index=sess)
    b = R.beta_for(stock, bench, "2026-11-06")
    assert abs(b - 1.5) < 0.05
    # Fenster endet vor dem Signaltag: Kurs am Signaltag darf Beta nicht ändern
    s2 = stock.copy(); s2["2026-11-06"] *= 3
    assert R.beta_for(s2, bench, "2026-11-06") == b
    # < 40 Paare → None: jede zweite Sitzung fehlt → keine aufeinanderfolgenden Paare
    sparse = stock[stock.index.isin(sess[::2])]
    assert R.beta_for(sparse, bench, "2026-11-06") is None


def test_bench_return_bar_logic():
    s = pd.Series([100, 101, 102, 103, 104, 105, 106, 107, 110], index=[f"2026-11-{d:02d}" for d in range(2, 11)])
    assert abs(R.bench_return(s, "2026-11-02", 7) - 7.0) < 1e-9
    assert R.bench_return(s, "2026-11-05", 7) is None


def _backup(snaps, evals):
    K = {}
    for td, (reg, recs) in snaps.items():
        K[f"tr:snap:{td}"] = {"tday": td, "regime": reg, "recs": recs}
    for td, recs in evals.items():
        K[f"tr:eval:{td}"] = {"tday": td, "recs": recs}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump({"keys": K}, f); f.close()
    return f.name


def test_blind_drops_returns_and_day_status():
    td = "2026-11-10"
    recs = [{"sym": "X01", "strat": "long_minervini", "dir": 1}, {"sym": "X02", "strat": "long_mr", "dir": 1},
            {"sym": "X03", "strat": "long_swing", "dir": 1}]
    ev = {"X01|long_minervini": {"r7": 5.0}, "X02|long_mr": {"r7": None}, "X03|long_swing": {"r7": 1.0}}
    p = _backup({td: ("BULL_QUIET", recs)}, {td: ev})
    snaps, evals = R.load_backup(p, blind_from="2026-11-09")
    assert all("r7" not in e for e in evals[td].values())    # keine Renditen im Blindmodus
    dr = R.build_units(snaps, evals, TABLE, [td, "2026-11-11"], 7, {"green"}, False, None, False)
    assert dr[0]["status"] == "excluded" and dr[0]["reason"].startswith("(c)") and dr[0]["nodata_strats"] == ["long_mr"]
    assert dr[1]["reason"] == "(a) kein Snapshot"


def test_not_randomisable_and_pending():
    td = "2026-11-12"
    recs = [{"sym": "X01", "strat": "long_minervini", "dir": 1}, {"sym": "X02", "strat": "long_swing", "dir": 1}]
    p = _backup({td: ("BULL_QUIET", recs)}, {})
    snaps, evals = R.load_backup(p, blind_from="2026-11-09")
    dr = R.build_units(snaps, evals, TABLE, [td], 7, {"green"}, False, None, False)
    assert dr[0]["reason"].startswith("(b)")
    recs.append({"sym": "X03", "strat": "long_mr", "dir": 1})
    p = _backup({td: ("BULL_QUIET", recs)}, {})
    snaps, evals = R.load_backup(p, blind_from="2026-11-09")
    dr = R.build_units(snaps, evals, TABLE, [td], 7, {"green"}, False, None, False)
    assert dr[0]["status"] == "pending"


def test_decision_rule():
    base = {"T": 0.5, "p_perm": 0.01, "beta": {"coverage_B": 0.9, "coverage_C_mean": 0.9, "abs_diff": 0.05}}
    assert R.decide(base, 60)[0].startswith("Vorteil")
    assert R.decide(dict(base, T=-0.5), 60)[0].startswith("Dokumentierte")
    assert R.decide(base, 59)[0] == "nicht entscheidbar"
    assert R.decide(dict(base, p_perm=0.06), 60)[0] == "nicht entscheidbar"
    assert R.decide(dict(base, beta=dict(base["beta"], abs_diff=0.10)), 60)[0] == "nicht entscheidbar"
    assert R.decide(dict(base, beta=dict(base["beta"], coverage_B=0.79)), 60)[0] == "nicht entscheidbar"


def test_newey_west_equals_iid_for_lag0():
    x = np.random.default_rng(2).normal(size=200)
    assert abs(R.newey_west_se(x, 0) - x.std(ddof=0) / math.sqrt(len(x))) < 1e-12


def test_short_pending_does_not_block_long_day():
    """Regressionstest (Review 26.11.): nicht fällige Short-Empfehlung darf einen Long-Tag nicht auf pending setzen."""
    td = "2026-11-13"
    recs = [{"sym": "X01", "strat": "long_minervini", "dir": 1}, {"sym": "X02", "strat": "long_mr", "dir": 1},
            {"sym": "X03", "strat": "short_fading", "dir": -1}]
    ev = {"X01|long_minervini": {"r7": 1.0}, "X02|long_mr": {"r7": 2.0}}      # Short-Bewertung fehlt
    p = _backup({td: ("BULL_QUIET", recs)}, {td: ev})
    snaps, evals = R.load_backup(p, blind_from="2026-11-09")
    dr = R.build_units(snaps, evals, TABLE, [td], 7, {"green"}, False, None, False)
    assert dr[0]["status"] == "auswertbar"
    assert dr[0]["short_units"][0]["n_pending"] == 1


def _df_multi_ticker_first(tickers):
    idx = pd.to_datetime(["2026-11-02", "2026-11-03"])
    cols = pd.MultiIndex.from_product([tickers, ["Open", "Close"]])
    return pd.DataFrame(np.arange(len(idx) * len(cols), dtype=float).reshape(len(idx), -1) + 1, index=idx, columns=cols)


def test_extract_close_all_shapes():
    idx = pd.to_datetime(["2026-11-02", "2026-11-03"])
    flat = pd.DataFrame({"Open": [1.0, 2.0], "Close": [3.0, 4.0]}, index=idx)
    assert list(F.extract_close(flat, "X01")) == [3.0, 4.0]
    m1 = _df_multi_ticker_first(["X01"])
    assert len(F.extract_close(m1, "X01")) == 2
    m2 = _df_multi_ticker_first(["X01", "X02"])
    assert len(F.extract_close(m2, "X02")) == 2 and F.extract_close(m2, "X09") is None
    fld = pd.DataFrame(np.ones((2, 2)), index=idx, columns=pd.MultiIndex.from_product([["Close"], ["X01", "X02"]]))
    assert len(F.extract_close(fld, "X02")) == 2
    assert F.extract_close(pd.DataFrame(), "X01") is None


def test_fetch_rows_batches_1_2_50_51():
    calls = []
    def fake(part, **kw):
        calls.append(len(part)); assert kw["auto_adjust"] is True
        return _df_multi_ticker_first(part) if len(part) > 1 else pd.DataFrame(
            {"Close": [10.0, 11.0]}, index=pd.to_datetime(["2026-11-02", "2026-11-03"]))
    for n, expect in [(1, [1]), (2, [2]), (50, [50]), (51, [50, 1])]:
        calls.clear()
        rows = F.fetch_rows([f"X{i:02d}" for i in range(n)], "2026-11-01", "2026-11-04", fake)
        assert calls == expect and len(rows) == 2 * n


def test_load_prices_requires_benchmark():
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    f.write("date,ticker,close\n2026-11-02,X01,10\n"); f.close()
    try:
        R.load_prices(f.name); assert False
    except SystemExit as e:
        assert "Benchmark" in str(e)


def _mini_inputs(gates_text):
    agg = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False); agg.write(gates_text); agg.close()
    bk = _backup({"2026-11-10": ("BULL_QUIET", [{"sym": "X01", "strat": "long_minervini", "dir": 1}])}, {})
    px = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False); px.write("date,ticker,close\n2026-11-02,RSP,1\n"); px.close()
    pre = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False); pre.write("falsche Fassung"); pre.close()
    return agg.name, bk, px.name, pre.name


GATES_SRC = "_MCM_REGIME_GATES = " + repr({k: {"strategies": v} for k, v in TABLE.items()}) + "\n"


def test_confirmatory_aborts_on_wrong_prereg_hash():
    agg, bk, px, pre = _mini_inputs(GATES_SRC)
    R.GATE_TABLE_SHA256, saved = R.load_gate_table(agg)[1], R.GATE_TABLE_SHA256   # Tabelle hier passend machen
    try:
        R.main(["--mode", "confirmatory", "--backup", bk, "--gates-from", agg, "--prices", px,
                "--prereg", pre, "--out", os.devnull]); assert False
    except SystemExit as e:
        assert "Präregistrierung" in str(e)
    finally:
        R.GATE_TABLE_SHA256 = saved


def test_confirmatory_aborts_on_gate_table_mismatch():
    agg, bk, px, pre = _mini_inputs(GATES_SRC)            # Tabelle ≠ Stand b1728e8
    try:
        R.main(["--mode", "confirmatory", "--backup", bk, "--gates-from", agg, "--prices", px,
                "--prereg", pre, "--out", os.devnull]); assert False
    except SystemExit as e:
        assert "Basistabelle" in str(e)


def test_provenance_records_inputs_and_args():
    agg, bk, px, pre = _mini_inputs(GATES_SRC)
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    R.main(["--mode", "blind", "--asof", "2026-11-12", "--backup", bk, "--gates-from", agg, "--out", out])
    prov = json.load(open(out))["provenance"]
    assert prov["args"]["asof"] == "2026-11-12" and prov["args"]["asof_effective"] == "2026-11-12"
    assert "warning" in prov and prov["backup_sha256"] and prov["gate_table_sha256"]


def test_provenance_records_prices_hash():
    agg, bk, px, pre = _mini_inputs(GATES_SRC)
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    R.main(["--mode", "blind", "--asof", "2026-11-12", "--backup", bk, "--gates-from", agg, "--prices", px, "--out", out])
    prov = json.load(open(out))["provenance"]
    assert prov["prices_sha256"] == R.sha256_file(px) and prov["args"]["prices"] == px


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
