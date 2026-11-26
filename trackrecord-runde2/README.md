# Track-Record-Runde 2 — Auswertungsskript (Version 1.1.1, 26.11.2026)

Setzt die Präregistrierung `regime-test/docs/preregistration/PRAEREG-TR-RUNDE2-GATES-2026-11-24.md` um
(Fassung 8, Commit `d9c382d`, SHA-256 `9ce6279a890938ab…`). Laut §5a Nr. 11 wird dieses Skript **vor** der
ersten Auswertung von Signalen ab dem 09.11.2026 committet und gegen §5a geprüft.

| Datei | Zweck |
|---|---|
| `runde2_eval.py` | Auswertung (Modi `blind`, `exploratory`, `confirmatory`) |
| `fetch_prices.py` | eingefrorene Kursdatei nach §5a Nr. 10 erzeugen (yfinance, `auto_adjust=True`) |
| `test_runde2.py` | Tests mit synthetischen Daten (19 Fälle, darunter Abbruchbedingungen, yfinance-Rückgabeformen, Regressionstest Short-pending) |

## Modi

| Modus | Zeitraum | Renditen | Zweck |
|---|---|---|---|
| `blind` | ab 09.11.2026 bis `--asof` | numerische Werte werden nicht übernommen oder ausgewertet | Überwachung: auswertbare Tage, Ausschlussgründe, Stand des Stichprobenendes |
| `exploratory` | Signaltage 02.07.–06.11.2026 (Runde 1) | nur für diesen Zeitraum | Test des Skripts an bereits gesichteten Daten; nicht entscheidend |
| `confirmatory` | ab 09.11.2026 | ja | primäre Auswertung; bricht ab ohne Präregistrierung mit passendem Hash, ohne Kursdatei, bei abweichender Gate-Tabelle oder fehlendem Benchmark-Kurs |

In `blind` und `exploratory` werden numerische Renditewerte von Signaltagen ab 09.11.2026 im Blind-Modus nicht
übernommen oder ausgewertet (`load_backup(blind_from=…)`). Verwendet werden ausschließlich Vorhandensein und
Nullstatus der Bewertungsfelder `r7`/`r30` zur Bestimmung des Bewertungsstatus (`pending` / `nodata` / `ok`).

## Aufruf

```bash
# laufende Überwachung (ohne Renditen)
python3 runde2_eval.py --mode blind --backup tr_backup_latest.json \
  --gates-from ko-aggregator/market_aggregator.py --out blind.json

# nach Ende der Stichprobe: Kursdatei einfrieren, dann primäre Auswertung
python3 fetch_prices.py --backup tr_backup_latest.json --first-day 2026-11-09 --last-day <60. auswertbarer Tag> \
  --start <≥ 61 Sitzungen vor 09.11.> --end <≥ 30 Sitzungen + Puffer nach letztem Tag> --out prices_runde2
python3 runde2_eval.py --mode confirmatory --backup tr_backup_latest.json \
  --gates-from ko-aggregator/market_aggregator.py --prereg PRAEREG-TR-RUNDE2-GATES-2026-11-24.md \
  --prices prices_runde2.csv --day-quality day_quality.csv --out runde2_result.json
```

`--gates-from` muss `market_aggregator.py` im Stand `b1728e8` sein (oder ein Stand mit unveränderter Basistabelle):
Das Skript liest `_MCM_REGIME_GATES` per AST und prüft den SHA-256 der Tabelle (`b607f4a06675bcaa…`).

## Zuordnung zur Präregistrierung

| Präregistrierung | Umsetzung |
|---|---|
| §3 Universum, Zuordnung wie Runde 1 | `STRAT_TO_GATE` (identisch mit `trackrecord-audit/analyze.py`, geprüft) |
| §3 Gate-Quelle: Basistabelle nach `tr:snap.regime` | `load_gate_table`, `build_units` |
| §4 Arm C, Schichtung Tag × Richtung | `draw_masks`: je Tag genau so viele zufällige Sperren wie in B; nur Long-Schicht (Primärtest nur Long) |
| §4 Strategiefamilie explorativ | `draw_masks(by_family=True)`, Ausgabe `exploratory_family_strata` |
| §5 Nr. 1 / §5a Nr. 1 Überschuss | `r7` aus `tr:eval` minus richtungsgerechte RSP-Rendite |
| §5 Nr. 2 sekundär | `secondary_h7_green_amber`, `secondary_h30_green` (ab 30 Tagen), B − A mit SE (h30 zusätzlich Newey-West, 30 Lags) |
| §5 Nr. 3 Risiko | `analyse` → `risk` je Arm |
| §5a Nr. 2–6 Einheiten, auswertbare Tage | `build_units`; Ausschlussgründe (a), (b), (c) mit betroffenen Strategien |
| §5a Nr. 7 Teststatistik, p-Wert | `analyse`: T, T_j, p = (1 + #{abs(T_j) ≥ abs(T)}) / 10 001 |
| §5a Nr. 9 Beta, Abdeckung | `analyse` → `arm_beta_vec`; n. v. nie null; Abdeckung je Ziehung, Verteilung min/p5/p95/max |
| §5a Nr. 10 Beta-Schätzung | `beta_for`: 61 Benchmark-Sitzungen vor dem Signaltag, gepaarte Renditen, OLS, ≥ 40 Paare |
| §5a Nr. 11 Zufallszahlen | `numpy.random.default_rng(20261124)`; Versionen in `provenance` |
| §2 Stichprobenende | `primary_sample_days`: 60. auswertbarer h7-Tag, spätestens 26.02.2027 |
| §6 Entscheidungsregel | `decide` |
| §6 Empfindlichkeit ohne D26-Tage | `--day-quality` (CSV `tday,quality`, Klasse `D26_stale` wie im Audit) |

## Umsetzungsentscheidungen, die die Präregistrierung nicht ausdrücklich regelt

Zur Prüfung vor dem ersten konfirmatorischen Lauf (Nr. 10 seit Version 1.1.0):

1. **Benchmark-Rendite:** aus der eingefrorenen Kursdatei, mit derselben Bar-Logik wie der Evaluator
   (`ko-aggregator/tr_layer.py` @ `b1728e8`, `_eval_horizon` Z. 315–325: Start am Bar am oder vor dem Signaltag,
   Ende H Bars der eigenen Reihe; zu wenig Bars → keine Rendite).
2. **Fälligkeit und `noData`:** Horizont fällig ⇔ Schlüssel `r7` bzw. `r30` in `tr:eval` vorhanden; `noData` ⇔ Wert `None`
   (`tr_layer.py` @ `b1728e8`: Z. 540 schreibt `None`, Z. 542 den Wert; vorher fehlt der Schlüssel). Ein Tag, an dem eine
   **Long**-Einheit noch nicht fällige Empfehlungen hat, heißt `pending` und zählt weder als auswertbar noch als ausgeschlossen.
   Short-Einheiten beeinflussen die Auswertbarkeit nicht (Primärtest nur Long; seit Version 1.1.0, Regressionstest).
3. **Fehlender Benchmark-Kurs** ist kein `noData` des Titels: im konfirmatorischen Modus Abbruch, explorativ eigener Ausschlussgrund.
4. **Regime ohne Basistabelle:** Tag ausgeschlossen mit eigenem Grund. Die Tabelle in `b1728e8` enthält alle fünf Regime einschließlich `NEUTRAL`.
5. **Zufallsfolge:** Tage chronologisch; je Tag `rng.random((10000, n))`, gesperrt sind die k kleinsten Werte. Jede Analyse (primär, Variante, h30, Familien, D26-Empfindlichkeit) startet einen neuen Generator mit demselben Seed.
6. **Sekundäre Analysen** verwenden dieselben Stichprobentage wie die primäre (bis zum 60. auswertbaren h7-Tag); innerhalb dieser Tage gilt „auswertbar“ je Variante bzw. Horizont.
7. **Risiko für Arm C:** je Ziehung berechnet, dann gemittelt.
8. **Zwischenstand:** Ist die Stichprobe noch unvollständig, gibt der konfirmatorische Modus Zahlen, aber kein Urteil aus („Zwischenstand, deskriptiv“), passend zur beschreibenden Auswertung nach 30 Tagen (§2).
9. **Beta** wird nur für Long-Einheiten der primären Analyse berechnet (§5a Nr. 10, letzter Punkt).

10. **Provenienz:** Das Ergebnis-JSON enthält Hashes aller Eingabedateien (Backup, Gate-Quelle und -Tabelle, Kursdatei,
   Archiv-Index, Tagesqualität, Präregistrierung), alle Aufrufparameter einschließlich des wirksamen Stichtags,
   Skript-Hash und -Version sowie Python-, numpy- und pandas-Version.

## Änderungen in Version 1.1.0 (nach Review vom 26.11.2026)

- `pending` wird nur noch aus Long-Einheiten abgeleitet; vorher konnte eine nicht fällige Short-Empfehlung einen
  auswertbaren Long-Tag blockieren (Regressionstest `test_short_pending_does_not_block_long_day`).
- Provenienz um `--archive-index`, `--day-quality` und alle Laufparameter ergänzt.
- `load_prices` und `fetch_prices.py` brechen ab, wenn der Benchmark RSP fehlt.
- `fetch_prices.py`: Spaltenauswahl für alle Rückgabeformen von `yfinance.download` (`extract_close`), gemockte Tests für Batchgrößen 1, 2, 50, 51.
- Tests für die Abbruchbedingungen (falscher Präregistrierungs-Hash, abweichende Gate-Tabelle).
- Blind-Modus im README präziser beschrieben.

## Änderung in Version 1.1.1

- Test `test_provenance_records_prices_hash`: Der in `provenance` gespeicherte Hash entspricht exakt der verwendeten Kursdatei.
  (Der Hash wurde schon in 1.1.0 gespeichert; die Zeile ist jetzt zur besseren Lesbarkeit getrennt.)
- Tests laufen mit `python3 test_runde2.py` oder `pytest`; `unittest discover` findet sie nicht (einfache Testfunktionen).

## Strukturbefund aus dem Blindlauf (ohne Renditen)

In der Basistabelle hat `STRESS_UNSTABLE` keine einzige freigegebene („grüne“) Long-Strategie; grün ist dort nur Fading Short.
Jeder STRESS-Tag ist im Primärtest deshalb nach §5a Nr. 6 (b) nicht randomisierbar. Hält die Stressphase an, kann die
Stichprobe die 60 auswertbaren Tage bis zum 26.02.2027 verfehlen; das Ergebnis lautet dann laut §2 „nicht entscheidbar“.
Das folgt aus der eingefrorenen Präregistrierung und wird nicht nachträglich geändert.

## Explorative Läufe (Runde 1, Signaltage bis 06.11.2026)

- Mit Benchmark aus den Archiv-Snapshots: 74 auswertbare Tage; 12 Tage ohne Benchmark-Kurs im Archiv, 2 nicht randomisierbar.
- Mit gefetchten yfinance-Kursen (Signaltage 01.–26.10.): Beta-Abdeckung 100 % in B und C.
- Familien-Schichtung: In Runde 1 ist nur das Paar Value/Dividende innerhalb einer Familie randomisierbar; die Analyse
  entartet daher zu diesem Vergleich und ist entsprechend vorsichtig zu lesen.
