# Review-Paket Track-Record-Audit 24.11.2026

Inhalt:
- AUDIT-TRACKRECORD-STRESS-2026-11-24.md — Bericht
- TRACK_RECORD_SPEC.md — Spezifikation v1.2 (Stand 03.07.2026, unverändert; aus ko-aggregator/docs, Commit b1728e8)
- scripts/ — acht rein lesende Auswerteskripte (Übersicht und Reproduzierbarkeitsstatus: Bericht, Abschnitt 10)
- inputs_derived/ — Zwischenstände aus den Rohdaten (enthalten Tickernamen, nur für das Review, nicht fürs öffentliche Repo)
  - provenance.txt: Commit-Hashes und SHA-256 der Rohquellen
  - snapshot_hashes.txt: SHA-256 aller 233 Archiv-Snapshots
  - archive_meta.csv und archive_prices.json.gz: Ausgaben von extract_archive.py
  - day_quality.csv: Ausgabe von classify_days.py
  - eval_flat.csv: Ausgabe von flatten_eval.py (11.737 Bewertungen)
  - market_series.json: VIX, VIX3M/VIX, Indexkurse je Handelstag
  - stale_share.csv: Anteil veralteter Kurse je Track-Record-Tag

Nachrechnen ohne Rohdaten (Abschnitte 4–6 des Berichts):
    mkdir out && python3 scripts/analyze.py inputs_derived/eval_flat.csv inputs_derived/market_series.json out
    python3 scripts/gate_effect.py out/eval_enriched.csv out
    python3 scripts/quality_sensitivity.py out/eval_enriched.csv inputs_derived/day_quality.csv out/gate_portfolio_h7.csv
    gunzip -k inputs_derived/archive_prices.json.gz && python3 scripts/stale_share.py inputs_derived/archive_prices.json inputs_derived/day_quality.csv out/stale_share.csv
    python3 scripts/crosscheck_stats.py <ko-aggregator>/backups/tr_backup_latest.json out   # braucht das KV-Backup

Vollständig ab Rohdaten (benötigt ko-aggregator/backups/tr_backup_latest.json, 105 MB, und uiq-archive/data/snapshots, 438 MB):
    python3 scripts/extract_archive.py <uiq-archive>/data/snapshots out
    python3 scripts/classify_days.py <ko-aggregator>/backups/tr_backup_latest.json out/archive_meta.csv out/day_quality.csv
    python3 scripts/flatten_eval.py <ko-aggregator>/backups/tr_backup_latest.json out/day_quality.csv out/eval_flat.csv

Alle im Bericht verwendeten Rechenschritte liegen als Skript vor. Geprüft: Paket-Lauf reproduziert die Abschnitte 2 (Empfindlichkeit, Kursdatum), 4, 5 und 6 identisch; Abgleich gegen tr:stats 60 Zellen × 6 Kennzahlen, 0 Abweichungen (braucht das KV-Backup).
