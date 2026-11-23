#!/usr/bin/env python3
"""
survivorship_gap.py — UIQ devtools
Version 1.1.1 (23.11.2026)

Zweck
-----
Misst, wie groß der Survivorship-Bias wäre, wenn man ein HEUTIGES Aktienuniversum
rückwärts für Backtests verwendet. Grundlage ist die Alpha-Vantage-Funktion
LISTING_STATUS, die für ein Stichtagsdatum die damals gelisteten US-Titel liefert
(sowie die Liste aller delisteten Titel mit Delisting-Datum).

Für jeden Stichtag wird ermittelt:
  * wie viele Titel damals gelistet waren (N),
  * wie viele davon heute noch unter demselben Symbol gelistet sind (überlebt),
  * wie viele seither delistet wurden (bestätigt über die Delisted-Liste),
  * wie viele Symbole inzwischen von einem ANDEREN Unternehmen wiederverwendet werden,
  * wie viele ohne Erklärung fehlen (meist Symbolwechsel nach Fusion/Umbenennung),
  * daraus: Lückenquote und annualisierte Ausfallrate.

Fix in 1.1.1
  * SPAC-Filter wirkt nur noch auf das Stichtags-Universum. Heutige Listings und
    Delisted-Liste bleiben für den Abgleich ungefiltert (sonst landeten umbenannte
    Börsenmäntel fälschlich in „ungeklärt“).
  * Bericht warnt, wenn die Indexdatei zum Stichtag deutlich weniger Mitglieder als
    erwartet enthält (rekonstruierte Listen unterschätzen dann die Lücke).

Neu in 1.1.0
  * SPAC-Filter (Standard: an). Börsenmäntel ("Acquisition Corp" u. ä.) werden
    über Namensmuster ausgeschlossen, damit der SPAC-Boom 2020/21 die Ausfallraten
    nicht verzerrt. Abschalten mit --include-spacs. Ausgeschlossene Anzahl steht im Bericht.
  * Large-Cap-Kennzahl über eine Indexmitgliedschafts-Datei (--index-members), z. B.
    S&P 500 inkl. früherer Mitglieder. Ermittelt je Stichtag, wie viele der damaligen
    Indexmitglieder heute noch gelistet sind (Survivorship-Lücke für Large Caps) und wie
    viele heute noch im Index sind (Lücke durch Indexwechsel). Zwei Dateiformate werden
    automatisch erkannt:
      A) Zeiträume:  symbol,start_date,end_date   (end_date leer = noch Mitglied)
      B) Snapshots:  date,tickers                 (tickers = kommagetrennte Liste)
    Datum jeweils YYYY-MM-DD (B akzeptiert auch MM/DD/YYYY). Symbole werden
    normalisiert ('.' und '/' → '-'), passend zur Schreibweise von Alpha Vantage.

Optional: Abgleich mit einer UIQ-Tickerliste (eine Zeile je Symbol, nur US-Titel).

Grenzen (bitte bei der Interpretation beachten)
  * Nur USA (NYSE, NASDAQ, NYSE MKT/American, NYSE ARCA, BATS).
  * Delisting-Gründe sind nicht unterscheidbar: Übernahmen (oft mit Prämie) zählen
    genauso wie Insolvenzen. Die Lücke zeigt das AUSMASS, nicht die RICHTUNG des Bias.
  * Der Gesamtmarktwert ist eine Obergrenze für ein Large-Cap-Universum wie UIQ;
    die Indexkennzahl ist die passendere Zahl.
  * Symbolwechsel werden nicht verfolgt (Alpha Vantage bietet dafür keine Historie).
  * SPAC-Erkennung ist eine Namensheuristik (Fehlklassifikationen möglich).

Ablageregel
  Das Skript ist ticker-frei. Die Detail-CSV-Dateien im Ausgabeordner enthalten
  Symbole und dürfen NICHT in ein öffentliches Repo committet werden. Der Markdown-
  Bericht enthält nur Zählwerte und ist zur Ablage geeignet.

Nutzung
  export ALPHA_VANTAGE_KEY=...        # oder --api-key
  python3 survivorship_gap.py                                   # Standard-Stichtage
  python3 survivorship_gap.py --index-members sp500_history.csv
  python3 survivorship_gap.py --uiq-universe uiq_us_tickers.txt
  python3 survivorship_gap.py --include-spacs                   # Vergleich wie v1.0
  python3 survivorship_gap.py --offline                         # nur Cache, kein API-Call

API-Verbrauch: 2 + Anzahl Stichtage Aufrufe (Standard: 6). Rohdaten werden im
Cache-Ordner gespeichert und bei erneutem Lauf wiederverwendet (v1.0-Cache ist kompatibel).

Nur Python-Standardbibliothek.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

VERSION = "1.1.1"
AV_URL = "https://www.alphavantage.co/query"
DEFAULT_DATES = ["2012-01-03", "2015-01-02", "2018-01-02", "2021-01-04"]
EARLIEST_SUPPORTED = date(2010, 1, 2)  # Alpha Vantage: Stichtage nach 2010-01-01
DEFAULT_EXCHANGES = {"NYSE", "NASDAQ", "NYSE MKT", "NYSE AMERICAN", "NYSE ARCA", "BATS"}
CALL_PAUSE_SECONDS = 15  # Rücksicht auf Rate-Limits des Free-Tiers

# Namensmuster für Börsenmäntel (SPACs). Bewusst eng gehalten.
SPAC_PATTERNS = [
    re.compile(r"\bACQUISITION\s+(CORP|CORPORATION|CO|COMPANY|INC|LTD|LIMITED|HOLDINGS?)\b"),
    re.compile(r"\bACQUISITION\s+(CORP|CORPORATION|CO)\.?\s+(I|II|III|IV|V|VI|VII|VIII|IX|X|\d+)\b"),
    # SPAC-Serien mit römischer Nummer am Namensende (z. B. "... Capital Corp IV")
    re.compile(r"\b(CORP|CORPORATION|CO|INC|LTD)\.?\s+(II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\s*$"),
    re.compile(r"\bMERGER\s+(CORP|CORPORATION|SUB)\b"),
    re.compile(r"\bBLANK\s+CHECK\b"),
    re.compile(r"\bSPAC\b"),
]


# ---------------------------------------------------------------------------
# Datenstrukturen
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    symbol: str
    name: str
    exchange: str
    asset_type: str
    ipo_date: date | None
    delisting_date: date | None
    status: str


@dataclass
class DateResult:
    stichtag: date
    n_then: int = 0
    survived: int = 0
    gone_delisted: int = 0
    gone_reused: int = 0
    gone_unexplained: int = 0
    spacs_excluded: int = 0
    by_exchange: dict = field(default_factory=lambda: defaultdict(Counter))
    delist_years: Counter = field(default_factory=Counter)
    details: list = field(default_factory=list)  # (symbol, exchange, kategorie, delisting_date)
    uiq_total: int = 0
    uiq_listed_then: int = 0
    uiq_ipo_after: int = 0
    uiq_not_found: int = 0

    @property
    def gone(self) -> int:
        return self.gone_delisted + self.gone_reused + self.gone_unexplained

    def gap_share(self) -> float:
        return self.gone / self.n_then if self.n_then else 0.0

    def annual_rate(self, today: date) -> float:
        return annual_rate(self.survived, self.n_then, self.stichtag, today)


@dataclass
class IndexResult:
    stichtag: date
    snapshot_used: date | None = None
    n_members: int = 0
    survived: int = 0
    gone_delisted: int = 0
    gone_reused: int = 0
    gone_unexplained: int = 0
    still_in_index: int = 0
    not_in_av_then: int = 0
    details: list = field(default_factory=list)  # (symbol, kategorie, delisting_date, heute_im_index)

    @property
    def gone(self) -> int:
        return self.gone_delisted + self.gone_reused + self.gone_unexplained

    def gap_share(self) -> float:
        return self.gone / self.n_members if self.n_members else 0.0

    def index_gap_share(self) -> float:
        return 1 - self.still_in_index / self.n_members if self.n_members else 0.0

    def annual_rate(self, today: date) -> float:
        return annual_rate(self.survived, self.n_members, self.stichtag, today)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def annual_rate(survived: int, n: int, start: date, today: date) -> float:
    years = (today - start).days / 365.25
    if n == 0 or years <= 0:
        return 0.0
    survival = survived / n
    if survival <= 0:
        return 1.0
    return 1 - survival ** (1 / years)


def parse_date(value: str | None) -> date | None:
    if not value or value.strip().lower() in {"null", "none", ""}:
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def norm_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-").replace("/", "-")


def is_common_stock_symbol(symbol: str) -> bool:
    """Heuristik: Vorzugsaktien, Warrants, Units u. ä. tragen bei Alpha Vantage
    meist '-' oder '.' im Symbol. Diese werden ausgeschlossen."""
    return "-" not in symbol and "." not in symbol


def is_spac(name: str) -> bool:
    n = name.upper()
    return any(p.search(n) for p in SPAC_PATTERNS)


def parse_listing_csv(text: str) -> list[Listing]:
    text = text.strip()
    if not text:
        raise ValueError("Leere Antwort.")
    if text.startswith("{"):
        # Alpha Vantage meldet Fehler/Limits als JSON
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            msg = text
        raise ValueError(f"Alpha Vantage meldet: {msg}")
    reader = csv.DictReader(io.StringIO(text))
    required = {"symbol", "exchange", "assetType", "ipoDate", "status"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError(f"Unerwartetes CSV-Format, Spalten: {reader.fieldnames}")
    out = []
    for row in reader:
        out.append(Listing(
            symbol=(row.get("symbol") or "").strip().upper(),
            name=(row.get("name") or "").strip(),
            exchange=(row.get("exchange") or "").strip().upper(),
            asset_type=(row.get("assetType") or "").strip(),
            ipo_date=parse_date(row.get("ipoDate")),
            delisting_date=parse_date(row.get("delistingDate")),
            status=(row.get("status") or "").strip(),
        ))
    return out


def filter_stocks(listings: list[Listing], exchanges: set[str], common_only: bool,
                  exclude_spacs: bool) -> tuple[list[Listing], int]:
    """Gibt (gefilterte Liste, Anzahl ausgeschlossener SPACs) zurück."""
    out = []
    spacs = 0
    for l in listings:
        if not l.symbol or l.asset_type.lower() != "stock":
            continue
        if exchanges and l.exchange not in exchanges:
            continue
        if common_only and not is_common_stock_symbol(l.symbol):
            continue
        if exclude_spacs and is_spac(l.name):
            spacs += 1
            continue
        out.append(l)
    return out, spacs


# ---------------------------------------------------------------------------
# Datenbeschaffung mit Cache
# ---------------------------------------------------------------------------

class Fetcher:
    def __init__(self, api_key: str | None, cache_dir: Path, offline: bool, refresh: bool):
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.offline = offline
        self.refresh = refresh
        self._last_call = 0.0
        self.calls = 0
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, state: str, stichtag: str | None) -> Path:
        tag = stichtag or "latest"
        return self.cache_dir / f"listing_{state}_{tag}.csv"

    def get(self, state: str, stichtag: str | None = None) -> list[Listing]:
        path = self._cache_path(state, stichtag)
        if path.exists() and not self.refresh:
            return parse_listing_csv(path.read_text(encoding="utf-8"))
        if self.offline:
            raise FileNotFoundError(f"Offline-Modus: Cache fehlt ({path.name}).")
        if not self.api_key:
            raise RuntimeError("Kein API-Key. ALPHA_VANTAGE_KEY setzen oder --api-key angeben.")

        wait = CALL_PAUSE_SECONDS - (time.time() - self._last_call)
        if self._last_call and wait > 0:
            time.sleep(wait)

        params = {"function": "LISTING_STATUS", "state": state, "apikey": self.api_key}
        if stichtag:
            params["date"] = stichtag
        url = AV_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": f"uiq-devtools/survivorship_gap {VERSION}"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        self._last_call = time.time()
        self.calls += 1

        listings = parse_listing_csv(text)  # validiert vor dem Cachen
        path.write_text(text, encoding="utf-8")
        return listings


# ---------------------------------------------------------------------------
# Indexmitgliedschaft
# ---------------------------------------------------------------------------

class IndexMembers:
    """Indexmitgliedschaft aus Zeitraum- (A) oder Snapshot-Datei (B)."""

    def __init__(self, path: Path):
        self.path = path
        self.mode = ""
        self.intervals: dict[str, list[tuple[date, date | None]]] = defaultdict(list)
        self.snapshot_dates: list[date] = []
        self.snapshots: dict[date, set[str]] = {}
        self._load()

    def _load(self) -> None:
        text = self.path.read_text(encoding="utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        try:
            header = [h.strip().lower() for h in next(reader)]
        except StopIteration:
            raise ValueError("Indexdatei ist leer.")

        if {"symbol", "start_date"}.issubset(header):
            self.mode = "Zeiträume"
            i_sym, i_start = header.index("symbol"), header.index("start_date")
            i_end = header.index("end_date") if "end_date" in header else None
            for row in reader:
                if not row or len(row) <= max(i_sym, i_start):
                    continue
                start = parse_date(row[i_start])
                if not start:
                    continue
                end = parse_date(row[i_end]) if i_end is not None and i_end < len(row) else None
                self.intervals[norm_symbol(row[i_sym])].append((start, end))
            if not self.intervals:
                raise ValueError("Indexdatei (Zeiträume) enthält keine gültigen Zeilen.")
        elif {"date", "tickers"}.issubset(header):
            self.mode = "Snapshots"
            i_date, i_tick = header.index("date"), header.index("tickers")
            for row in reader:
                if not row or len(row) <= max(i_date, i_tick):
                    continue
                d = parse_date(row[i_date])
                if not d:
                    continue
                # Ticker-Feld kann wegen CSV-Quoting in einer Zelle stehen oder über Restspalten verteilt sein
                raw = ",".join(row[i_tick:])
                syms = {norm_symbol(s) for s in raw.split(",") if s.strip()}
                self.snapshots[d] = syms
            self.snapshot_dates = sorted(self.snapshots)
            if not self.snapshot_dates:
                raise ValueError("Indexdatei (Snapshots) enthält keine gültigen Zeilen.")
        else:
            raise ValueError("Indexdatei: Kopfzeile muss 'symbol,start_date[,end_date]' oder 'date,tickers' enthalten.")

    def coverage(self) -> tuple[date, date]:
        if self.mode == "Snapshots":
            return self.snapshot_dates[0], self.snapshot_dates[-1]
        starts = [s for iv in self.intervals.values() for s, _ in iv]
        ends = [e for iv in self.intervals.values() for _, e in iv if e]
        return min(starts), max(ends) if ends else date.today()

    def members_at(self, d: date) -> tuple[set[str], date | None]:
        if self.mode == "Snapshots":
            i = bisect.bisect_right(self.snapshot_dates, d) - 1
            if i < 0:
                return set(), None
            snap = self.snapshot_dates[i]
            return set(self.snapshots[snap]), snap
        out = set()
        for sym, ivs in self.intervals.items():
            for start, end in ivs:
                if start <= d and (end is None or end > d):
                    out.add(sym)
                    break
        return out, d


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

def classify(symbol: str, stichtag: date, active_now: dict[str, Listing],
             delisted_by_symbol: dict[str, list[Listing]]) -> tuple[str, date | None]:
    now = active_now.get(symbol)
    reused = bool(now and now.ipo_date and now.ipo_date > stichtag)
    if now and not reused:
        return "überlebt", None
    dl_dates = [d.delisting_date for d in delisted_by_symbol.get(symbol, [])
                if d.delisting_date and d.delisting_date > stichtag]
    first_dl = min(dl_dates) if dl_dates else None
    if reused:
        return "Symbol neu vergeben", first_dl
    if dl_dates:
        return "delistet", first_dl
    return "ungeklärt", None


def analyse(stichtag: date,
            then: list[Listing],
            current_active: list[Listing],
            delisted: list[Listing],
            uiq_symbols: set[str] | None) -> DateResult:
    res = DateResult(stichtag=stichtag)
    active_now = {l.symbol: l for l in current_active}
    delisted_by_symbol: dict[str, list[Listing]] = defaultdict(list)
    for l in delisted:
        delisted_by_symbol[l.symbol].append(l)

    seen = set()
    for l in then:
        if l.symbol in seen:
            continue
        seen.add(l.symbol)
        res.n_then += 1
        category, first_dl = classify(l.symbol, stichtag, active_now, delisted_by_symbol)
        res.by_exchange[l.exchange][category] += 1
        if category == "überlebt":
            res.survived += 1
            continue
        if category == "Symbol neu vergeben":
            res.gone_reused += 1
        elif category == "delistet":
            res.gone_delisted += 1
        else:
            res.gone_unexplained += 1
        if first_dl:
            res.delist_years[first_dl.year] += 1
        res.details.append((l.symbol, l.exchange, category, first_dl.isoformat() if first_dl else ""))

    if uiq_symbols is not None:
        then_symbols = {l.symbol for l in then}
        res.uiq_total = len(uiq_symbols)
        for s in uiq_symbols:
            cur = active_now.get(s)
            if s in then_symbols:
                res.uiq_listed_then += 1
            elif cur and cur.ipo_date and cur.ipo_date > stichtag:
                res.uiq_ipo_after += 1
            else:
                res.uiq_not_found += 1
    return res


def analyse_index(stichtag: date, today: date, index: IndexMembers,
                  then_all: list[Listing], current_all: list[Listing],
                  delisted_all: list[Listing]) -> IndexResult:
    """Für die Indexanalyse werden UNGEFILTERTE Listings (alle Aktiengattungen,
    keine SPAC-Filterung) mit normalisierten Symbolen verwendet."""
    res = IndexResult(stichtag=stichtag)
    members, snap = index.members_at(stichtag)
    res.snapshot_used = snap
    members_today, _ = index.members_at(today)

    active_now = {norm_symbol(l.symbol): l for l in current_all}
    delisted_by_symbol: dict[str, list[Listing]] = defaultdict(list)
    for l in delisted_all:
        delisted_by_symbol[norm_symbol(l.symbol)].append(l)
    then_symbols = {norm_symbol(l.symbol) for l in then_all}

    for sym in sorted(members):
        res.n_members += 1
        if sym not in then_symbols:
            res.not_in_av_then += 1
        in_index_today = sym in members_today
        if in_index_today:
            res.still_in_index += 1
        category, first_dl = classify(sym, stichtag, active_now, delisted_by_symbol)
        if category == "überlebt":
            res.survived += 1
        elif category == "Symbol neu vergeben":
            res.gone_reused += 1
        elif category == "delistet":
            res.gone_delisted += 1
        else:
            res.gone_unexplained += 1
        res.details.append((sym, category, first_dl.isoformat() if first_dl else "",
                            "ja" if in_index_today else "nein"))
    return res


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

def pct(x: float) -> str:
    return f"{x * 100:.1f} %".replace(".", ",")


def write_details(res: DateResult, out_dir: Path) -> None:
    path = out_dir / f"detail_{res.stichtag.isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "exchange", "kategorie", "delisting_date"])
        for row in sorted(res.details):
            w.writerow(row)


def write_index_details(res: IndexResult, out_dir: Path) -> None:
    path = out_dir / f"detail_index_{res.stichtag.isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "kategorie", "delisting_date", "heute_im_index"])
        for row in res.details:
            w.writerow(row)


def build_report(results: list[DateResult], index_results: list[IndexResult], index: IndexMembers | None,
                 today: date, args, n_current: int, n_delisted: int) -> str:
    lines = []
    lines.append("# UIQ — Survivorship-Lücke (Alpha Vantage LISTING_STATUS)")
    lines.append("")
    lines.append(f"**Erstellt:** {today.strftime('%d.%m.%Y')} · **Skript:** survivorship_gap.py v{VERSION}")
    lines.append(f"**Universum:** US-Aktien, Börsen: {', '.join(sorted(args.exchange_set))}; "
                 f"{'nur Stammaktien-Heuristik' if args.common_only else 'alle Aktiengattungen'}; "
                 f"SPAC-Filter: {'an' if args.exclude_spacs else 'aus'}")
    lines.append(f"**Heute gelistet:** {n_current} · **Delistet (Gesamtliste):** {n_delisted}")
    lines.append("")

    lines.append("## Gesamtmarkt")
    lines.append("")
    lines.append("| Stichtag | damals gelistet | heute noch gelistet | delistet | Symbol neu vergeben | ungeklärt | Lücke | Ausfallrate p. a. | SPACs ausgeschlossen |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        lines.append(f"| {r.stichtag.strftime('%d.%m.%Y')} | {r.n_then} | {r.survived} | {r.gone_delisted} | "
                     f"{r.gone_reused} | {r.gone_unexplained} | {pct(r.gap_share())} | {pct(r.annual_rate(today))} | "
                     f"{r.spacs_excluded} |")
    lines.append("")

    if index_results:
        cov_from, cov_to = index.coverage()
        lines.append("## Large Caps (Indexmitglieder zum Stichtag)")
        lines.append("")
        lines.append(f"**Indexdatei:** Format {index.mode}, Abdeckung {cov_from.strftime('%d.%m.%Y')} bis {cov_to.strftime('%d.%m.%Y')}. "
                     "Ungefilterte Listings (alle Aktiengattungen, ohne SPAC-Filter), Symbole normalisiert.")
        lines.append("")
        lines.append("| Stichtag | Mitglieder | heute noch gelistet | delistet | Symbol neu vergeben | ungeklärt | Lücke (Listing) | Ausfallrate p. a. | heute noch im Index | Lücke (Index) | nicht in AV-Liste damals |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in index_results:
            snap = f" (Snapshot {r.snapshot_used.strftime('%d.%m.%Y')})" if index.mode == "Snapshots" and r.snapshot_used else ""
            lines.append(f"| {r.stichtag.strftime('%d.%m.%Y')}{snap} | {r.n_members} | {r.survived} | {r.gone_delisted} | "
                         f"{r.gone_reused} | {r.gone_unexplained} | {pct(r.gap_share())} | {pct(r.annual_rate(today))} | "
                         f"{r.still_in_index} | {pct(r.index_gap_share())} | {r.not_in_av_then} |")
        lines.append("")
        lines.append("- **Lücke (Listing):** Indexmitglieder vom Stichtag, die heute nicht mehr gelistet sind → fehlen in jedem Backtest mit heutiger Tickerliste.")
        lines.append("- **Lücke (Index):** Indexmitglieder vom Stichtag, die heute nicht mehr im Index sind (inkl. Abstieg) → fehlen in einem Backtest auf der heutigen Indexliste.")
        lines.append("- **Nicht in AV-Liste damals:** Plausibilitätsprüfung; hohe Werte deuten auf abweichende Symbolschreibweisen in der Indexdatei hin.")
        low = [r for r in index_results if 0 < r.n_members < args.expected_members * 0.95]
        if low:
            dates_txt = ", ".join(f"{r.stichtag.strftime('%d.%m.%Y')} ({r.n_members})" for r in low)
            lines.append(f"- **Warnung Datenqualität:** Die Indexdatei enthält an folgenden Stichtagen deutlich weniger als "
                         f"{args.expected_members} Mitglieder: {dates_txt}. Rekonstruierte Listen verlieren vor allem später "
                         "ausgeschiedene Titel — die Large-Cap-Lücke ist für diese Stichtage daher eher UNTERschätzt.")
        if any(r.n_members == 0 for r in index_results):
            lines.append("- Hinweis: Für mindestens einen Stichtag enthält die Indexdatei keine Mitglieder (außerhalb der Abdeckung).")
        lines.append("")

    lines.append("## Gesamtmarkt nach Börse")
    for r in results:
        lines.append("")
        lines.append(f"**Stichtag {r.stichtag.strftime('%d.%m.%Y')}**")
        lines.append("")
        lines.append("| Börse | überlebt | delistet | Symbol neu vergeben | ungeklärt | Lücke |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for ex in sorted(r.by_exchange):
            c = r.by_exchange[ex]
            total = sum(c.values())
            gone = total - c["überlebt"]
            lines.append(f"| {ex} | {c['überlebt']} | {c['delistet']} | {c['Symbol neu vergeben']} | "
                         f"{c['ungeklärt']} | {pct(gone / total if total else 0)} |")

    lines.append("")
    lines.append("## Delistings nach Jahr (Gesamtmarkt, bezogen auf den frühesten Stichtag)")
    if results:
        first = min(results, key=lambda r: r.stichtag)
        lines.append("")
        lines.append("| Jahr | Anzahl |")
        lines.append("|---|---:|")
        for y in sorted(first.delist_years):
            lines.append(f"| {y} | {first.delist_years[y]} |")

    if any(r.uiq_total for r in results):
        lines.append("")
        lines.append("## Abgleich UIQ-Universum (nur US-Titel)")
        lines.append("")
        lines.append("| Stichtag | UIQ-Titel | damals gelistet | erst später an die Börse | nicht zuordenbar | UIQ-Anteil am damaligen Universum |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for r in results:
            share = r.uiq_listed_then / r.n_then if r.n_then else 0
            lines.append(f"| {r.stichtag.strftime('%d.%m.%Y')} | {r.uiq_total} | {r.uiq_listed_then} | "
                         f"{r.uiq_ipo_after} | {r.uiq_not_found} | {pct(share)} |")

    lines.append("")
    lines.append("## Lesart")
    lines.append("")
    lines.append("- **Lücke** = Anteil der damals gelisteten Titel, die ein heutiges Universum rückwärts nicht enthält. "
                 "Genau diese Titel fehlen in einem Backtest, der mit heutigen Tickerlisten arbeitet.")
    lines.append("- **Richtung offen:** Delistings umfassen Übernahmen (häufig mit Prämie) und Insolvenzen. "
                 "Die Zahl zeigt das Ausmaß der Lücke, nicht, ob Backtests dadurch zu gut oder zu schlecht aussehen. "
                 "Bei Qualitäts- und Dividendenfiltern wirkt sie erfahrungsgemäß beschönigend (gescheiterte Dividendenzahler fehlen).")
    lines.append("- **Gesamtmarkt vs. Large Caps:** Der Gesamtmarktwert ist eine Obergrenze für ein Large-Cap-Universum; "
                 "für UIQ ist die Indexkennzahl die passendere Zahl.")
    lines.append("- **SPAC-Filter:** Namensheuristik, Fehlklassifikationen möglich. Zum Vergleich mit v1.0: --include-spacs.")
    lines.append("- **„Ungeklärt“** sind meist Symbolwechsel nach Fusion oder Umbenennung; Alpha Vantage liefert dafür keine Historie.")
    lines.append("")
    lines.append("_Detaildateien mit Symbolen liegen nur lokal im Ausgabeordner (nicht committen)._")
    return "\n".join(lines) + "\n"


def print_summary(results: list[DateResult], index_results: list[IndexResult], today: date) -> None:
    print()
    print("Gesamtmarkt")
    print(f"{'Stichtag':<12}{'N damals':>10}{'überlebt':>10}{'delistet':>10}{'neu verg.':>11}{'ungeklärt':>11}{'Lücke':>9}{'p. a.':>9}{'SPACs':>8}")
    for r in results:
        print(f"{r.stichtag.isoformat():<12}{r.n_then:>10}{r.survived:>10}{r.gone_delisted:>10}"
              f"{r.gone_reused:>11}{r.gone_unexplained:>11}{pct(r.gap_share()):>9}{pct(r.annual_rate(today)):>9}"
              f"{r.spacs_excluded:>8}")
    if index_results:
        print()
        print("Large Caps (Indexmitglieder)")
        print(f"{'Stichtag':<12}{'Mitgl.':>8}{'überlebt':>10}{'delistet':>10}{'neu verg.':>11}{'ungeklärt':>11}{'Lücke':>9}{'p. a.':>9}{'im Index':>10}{'Lücke Idx':>11}")
        for r in index_results:
            print(f"{r.stichtag.isoformat():<12}{r.n_members:>8}{r.survived:>10}{r.gone_delisted:>10}"
                  f"{r.gone_reused:>11}{r.gone_unexplained:>11}{pct(r.gap_share()):>9}{pct(r.annual_rate(today)):>9}"
                  f"{r.still_in_index:>10}{pct(r.index_gap_share()):>11}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_uiq_symbols(path: Path) -> set[str]:
    symbols = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip().upper()
        if s:
            symbols.add(s)
    return symbols


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Misst die Survivorship-Lücke eines rückwärts verwendeten US-Universums.")
    p.add_argument("--dates", nargs="+", default=DEFAULT_DATES, help="Stichtage YYYY-MM-DD (nach 2010-01-01)")
    p.add_argument("--api-key", default=os.environ.get("ALPHA_VANTAGE_KEY"), help="Alpha-Vantage-Key (Standard: $ALPHA_VANTAGE_KEY)")
    p.add_argument("--uiq-universe", type=Path, help="Textdatei mit UIQ-US-Tickern, eine Zeile je Symbol")
    p.add_argument("--index-members", type=Path, help="Indexmitgliedschaft (symbol,start_date,end_date ODER date,tickers)")
    p.add_argument("--expected-members", type=int, default=500, help="Erwartete Mitgliederzahl des Index (für die Qualitätswarnung, Standard: 500)")
    p.add_argument("--exchanges", nargs="+", default=sorted(DEFAULT_EXCHANGES), help="Börsen-Filter (Großschreibung wie bei Alpha Vantage)")
    p.add_argument("--all-share-classes", action="store_true", help="Vorzugsaktien/Warrants/Units nicht ausschließen")
    p.add_argument("--include-spacs", action="store_true", help="SPAC-Filter abschalten (Vergleich mit v1.0)")
    p.add_argument("--cache-dir", type=Path, default=Path("survivorship_cache"), help="Ordner für Rohdaten (Standard: ./survivorship_cache)")
    p.add_argument("--out-dir", type=Path, default=Path("survivorship_out"), help="Ordner für Berichte (Standard: ./survivorship_out)")
    p.add_argument("--offline", action="store_true", help="Keine API-Aufrufe, nur Cache verwenden")
    p.add_argument("--refresh", action="store_true", help="Cache ignorieren und neu abrufen")
    p.add_argument("--today", help="Referenzdatum für 'heute' (nur für Tests)")
    args = p.parse_args(argv)

    args.exchange_set = {e.upper() for e in args.exchanges}
    args.common_only = not args.all_share_classes
    args.exclude_spacs = not args.include_spacs
    today = parse_date(args.today) if args.today else date.today()

    stichtage = []
    for d in args.dates:
        pd_ = parse_date(d)
        if not pd_:
            print(f"Ungültiges Datum: {d}", file=sys.stderr)
            return 2
        if pd_ < EARLIEST_SUPPORTED or pd_ >= today:
            print(f"Stichtag {d} liegt außerhalb des unterstützten Bereichs (2010-01-02 bis gestern).", file=sys.stderr)
            return 2
        stichtage.append(pd_)
    stichtage.sort()

    uiq = load_uiq_symbols(args.uiq_universe) if args.uiq_universe else None
    index = None
    if args.index_members:
        try:
            index = IndexMembers(args.index_members)
        except (ValueError, OSError) as e:
            print(f"Abbruch (Indexdatei): {e}", file=sys.stderr)
            return 1
    fetcher = Fetcher(args.api_key, args.cache_dir, args.offline, args.refresh)

    try:
        print("Lade aktuelle Listings …")
        current_raw = fetcher.get("active")
        # Abgleichslisten ohne SPAC-Filter: der Filter gilt nur für das Stichtags-Universum
        current, _ = filter_stocks(current_raw, args.exchange_set, args.common_only, False)
        print("Lade Delisted-Liste …")
        delisted_raw = fetcher.get("delisted")
        delisted, _ = filter_stocks(delisted_raw, args.exchange_set, args.common_only, False)
        # Für die Indexanalyse: alle Aktiengattungen, ohne SPAC-Filter
        current_all, _ = filter_stocks(current_raw, args.exchange_set, False, False)
        delisted_all, _ = filter_stocks(delisted_raw, args.exchange_set, False, False)

        results, index_results = [], []
        for st in stichtage:
            print(f"Lade Listings zum Stichtag {st.isoformat()} …")
            then_raw = fetcher.get("active", st.isoformat())
            then, spacs = filter_stocks(then_raw, args.exchange_set, args.common_only, args.exclude_spacs)
            r = analyse(st, then, current, delisted, uiq)
            r.spacs_excluded = spacs
            results.append(r)
            if index:
                then_all, _ = filter_stocks(then_raw, args.exchange_set, False, False)
                index_results.append(analyse_index(st, today, index, then_all, current_all, delisted_all))
    except (ValueError, RuntimeError, FileNotFoundError, OSError) as e:
        print(f"Abbruch: {e}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        write_details(r, args.out_dir)
    for r in index_results:
        write_index_details(r, args.out_dir)
    report = build_report(results, index_results, index, today, args,
                          len({l.symbol for l in current}), len({l.symbol for l in delisted}))
    report_path = args.out_dir / f"survivorship_report_{today.isoformat()}.md"
    report_path.write_text(report, encoding="utf-8")

    print_summary(results, index_results, today)
    print(f"API-Aufrufe in diesem Lauf: {fetcher.calls}")
    print(f"Bericht (ticker-frei): {report_path}")
    print(f"Detaildateien (enthalten Symbole, nicht committen): {args.out_dir}/detail_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
