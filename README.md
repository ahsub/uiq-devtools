# survivorship_gap.py (v1.1.1)

Misst, wie groß die Survivorship-Lücke wäre, wenn ein **heutiges** US-Aktienuniversum rückwärts für Backtests verwendet wird. Datenquelle: Alpha Vantage `LISTING_STATUS` (Listings zu einem Stichtag plus Liste aller delisteten Titel). Optional wird zusätzlich eine **Large-Cap-Kennzahl** über eine Indexmitgliedschafts-Datei berechnet.

Hintergrund: SUITE-Backlog „Quellenbasierte Regelwerke“, Methodik A.5 und Reihenfolge F.2 (Universum klären, bevor große Backtests laufen).

## Aufruf

```bash
export ALPHA_VANTAGE_KEY=...          # bestehender UIQ-Key
python3 survivorship_gap.py                                    # Gesamtmarkt, SPAC-Filter an
python3 survivorship_gap.py --index-members sp500_history.csv  # + Large-Cap-Kennzahl
python3 survivorship_gap.py --uiq-universe uiq_us_tickers.txt  # + Abgleich UIQ-Universum
python3 survivorship_gap.py --include-spacs                    # Vergleich wie v1.0
python3 survivorship_gap.py --offline                          # nur aus dem Cache
```

- **API-Verbrauch:** 2 + Anzahl Stichtage (Standard: 6 Aufrufe), mit 15 s Pause zwischen den Aufrufen.
- **Cache:** Rohdaten liegen in `survivorship_cache/` (kompatibel mit v1.0). Weitere Läufe mit anderen Optionen kosten keine Aufrufe.
- **Abhängigkeiten:** nur die Python-Standardbibliothek.

## Indexmitgliedschafts-Datei (`--index-members`)

Zwei Formate werden automatisch erkannt:

**A) Zeiträume**
```
symbol,start_date,end_date
XXX,2005-06-01,
YYY,2010-02-16,2018-03-19
```
`end_date` bleibt leer, wenn der Titel noch Mitglied ist.

**B) Snapshots**
```
date,tickers
2014-01-02,"XXX,YYY,ZZZ"
```
Für jeden Stichtag wird der letzte Snapshot davor verwendet. Datum `YYYY-MM-DD` oder `MM/DD/YYYY`.

Symbole werden normalisiert (`.` und `/` werden zu `-`), passend zur Schreibweise von Alpha Vantage.

**Mögliche Quellen** (nicht im Repo ablegen, enthalten Ticker):
- öffentlich gepflegte GitHub-Listen der historischen S&P-500-Zusammensetzung, meist im Snapshot-Format B;
- die kostenlose S&P-500-Stufe von Valuein (Herstellerangabe: aktuelle und frühere Mitglieder), umgewandelt in Format A.

Vor der Nutzung lohnt sich eine Stichprobe gegen eine zweite Quelle. Rekonstruierte Listen enthalten für ältere Jahre oft weniger als 500 Mitglieder; der Bericht warnt dann (Schwelle über `--expected-members`, Standard 500). Die Spalte „nicht in AV-Liste damals“ im Bericht dient als Plausibilitätsprüfung.

## Ausgabe

| Datei | Inhalt | Committen? |
|---|---|---|
| `survivorship_out/survivorship_report_JJJJ-MM-TT.md` | Zählwerte: Gesamtmarkt, Large Caps, nach Börse, nach Jahr, UIQ-Abgleich | ja (ticker-frei) |
| `survivorship_out/detail_*.csv`, `detail_index_*.csv` | Symbol-Ebene | **nein** |
| `survivorship_cache/*.csv` | Rohdaten von Alpha Vantage | **nein** |

`.gitignore`:
```
survivorship_cache/
survivorship_out/detail_*.csv
```

## Kategorien

- **überlebt:** heute unter demselben Symbol gelistet, und das Unternehmen ist dasselbe (Börsengang nicht nach dem Stichtag)
- **delistet:** laut Delisted-Liste nach dem Stichtag eingestellt
- **Symbol neu vergeben:** Das Symbol ist heute gelistet, gehört aber einem Unternehmen mit Börsengang nach dem Stichtag.
- **ungeklärt:** fehlt heute, ohne Eintrag in der Delisted-Liste (meist Symbolwechsel)

Zusätzlich bei Large Caps:
- **Lücke (Listing):** damalige Indexmitglieder, die heute nicht mehr gelistet sind
- **Lücke (Index):** damalige Indexmitglieder, die heute nicht mehr im Index sind. Relevant für Backtests auf der heutigen Indexliste.

## Grenzen

- Nur USA. Die Delisting-Gründe sind nicht unterscheidbar: Übernahmen zählen genauso wie Insolvenzen.
- Die Gesamtmarkt-Zahl ist für das Large-Cap-Universum von UIQ eine **Obergrenze**. Passender ist die Indexkennzahl.
- **SPAC-Filter:** Namensheuristik („Acquisition Corp“, „Blank Check“, „Merger Corp“, Serien mit römischer Nummer am Namensende). Fehlklassifikationen sind möglich. Für die Indexanalyse ist der Filter bewusst abgeschaltet.
- Die Stammaktien-Heuristik (Gesamtmarkt) schließt Symbole mit `-` oder `.` aus. Für die Indexanalyse ist sie abgeschaltet.
- Stichtage werden laut Alpha Vantage erst ab 2010 unterstützt.
