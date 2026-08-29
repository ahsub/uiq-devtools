# uiq-devtools

Interne Analyse-Werkzeuge für den UIQ-Suite-Codebestand. Kein Teil der
Produktions-App, kein Deployment-Ziel — reines Entwickler-Werkzeug für
Axel/Claude-Sessions.

## Enthalten

### `backlog-19-analyse/` — Statisches Analyse-Skript zu SUITE.md Backlog Punkt 19

Entstanden am 26.08.2026 als Reaktion auf den VIX-Datenkonsistenz-Bug vom
25.08.2026 (zwei unabhängige Pipelines schrieben unkoordiniert denselben
Wert in dasselbe DOM-Element). Backlog Punkt 19 (SUITE.md) fordert ein
Werkzeug, das solche Muster systematisch statt zufällig findet.

Prüft `axel-scanner/index.html` + `ko-modules/*.js` + `ko-aggregator/workers/**/*.js`
auf drei Bug-Klassen:

1. **Funktionsaufrufe vs. echte Aufrufstellen** — definierte Funktionen ohne
   jeden Aufruf-Nachweis (toter Code) und Aufrufe ohne Definition (Tippfehler,
   Ladereihenfolge-Probleme).
2. **`getElementById()` vs. echte IDs** — verwaiste DOM-Referenzen, und
   (verfeinert in `refine.js`) IDs, auf die mehrere **automatisch erreichbare**
   Funktionen unkoordiniert schreiben — das eigentliche VIX-Bug-Muster.
3. **Duplizierte Funktionskörper** — identische Funktionen an mehreren
   Stellen (wie die vier duplizierten Ampel-Renderer vom 14.07.2026).

#### Nutzung

Erwartetes Verzeichnis-Layout — dieses Repo liegt als Geschwister-Ordner
neben den zu analysierenden Repos in einem gemeinsamen Arbeitsordner:

```
irgendein-arbeitsordner/
├── axel-scanner/
├── ko-modules/
├── ko-aggregator/
└── uiq-devtools/          ← dieses Repo
```

```bash
cd uiq-devtools
npm install
npm run backlog19          # → backlog-19-analyse/report.md + report.json
npm run backlog19:refine   # → backlog-19-analyse/report-2c.md + report-2c.json
```

Abweichendes Layout: `UIQ_WORKSPACE_ROOT=/pfad/zum/arbeitsordner npm run backlog19`

Reports werden lokal neben den Skripten erzeugt und sind per `.gitignore`
ausgeschlossen (Wegwerf-Artefakte pro Lauf, kein Commit-Ziel).

#### Bekannte Einschränkungen (Stand 26.08.2026)

- **`ko-modules` wird im main-Branch-Stand analysiert, nicht in den
  CDN-gepinnten Versionen**, die `index.html` tatsächlich per
  `jsdelivr@<commit-hash>` lädt. Bei Abweichung zwischen Pin und main
  können Treffer falsch sein. Das Skript listet die gefundenen Pins im
  Report-Kopf zur manuellen Gegenprüfung.
- **Dynamisch per `innerHTML`/Template erzeugte IDs** werden nicht erkannt
  (keine DOM-Ausführung, nur statische Textsuche) — Treffer in Abschnitt 2a
  können falsch-positiv sein.
- **Namensbasierter Call-Graph, nicht scope-exakt** — Methodennamen auf
  verschiedenen Objekten mit demselben Namen werden zusammengeworfen.
- **TODO / bekannte Lücke aus der ersten Anwendung (26.08.2026):**
  `refine.js`s Automatik-Erkennung ist **branch-unsensitiv** bei
  Dispatcher-Funktionen. Beispiel: `showPanel(id)` wird einmalig beim
  Seitenladen automatisch aufgerufen (`showPanel('home')`), wodurch das
  Skript **alle** Aufrufe irgendwo im `showPanel`-Körper als "automatisch
  erreichbar" markiert — auch die, die nur in einem `if(id==='makro')`-
  o.ä. Zweig stehen, der beim Boot-Aufruf gar nicht durchlaufen wird und
  in der Praxis nur bei Nutzer-Klick auf den jeweiligen Tab läuft. Das
  erzeugte in der ersten Anwendung ca. 12 falsch-positive Kollisions-
  kandidaten. Eine zukünftige Version müsste den Aufruf-Kontext
  argumentsensitiv (welcher `if(id===X)`-Zweig aktiv ist) statt nur
  namensbasiert verfolgen, um diese Klasse von Fehlalarmen zu vermeiden.
  Bis dahin: jeder §2c-Treffer aus `showPanel`-artigen Dispatchern manuell
  gegenprüfen, bevor er als echter Kandidat gilt.
- Ergebnisse sind **Kandidatenlisten, keine Bug-Listen** — jeder Fund
  braucht manuelle Prüfung vor jeder Fix-Entscheidung (siehe SUITE.md
  Backlog Punkt 19, Grundsatz: Vollständigkeit ist bei dieser Codegröße
  nicht erreichbar, systematisches Suchen ersetzt kein Verständnis).

#### Historie

#### Historie

- 29.08.2026: `extractInlineScripts()` gefixt (HTML-Kommentar-Stripping vor
  Script-Extraktion, s. `ki-prompt-audit/`-Historie unten für die volle
  Herleitung) — behebt stillen Coverage-Verlust bei Blöcken, die durch
  Prosa-Kommentare mit script-artigem Text fehlklassifiziert wurden.
- 26.08.2026: Erste Version + erste Anwendung. Ergebnis siehe SUITE.md
  Backlog №57 (renderHomeLanding-Altcode) und №58
  (kvToScannerState/loadScannerFromKV-Fallback-Sync-Hinweis). Kein zweiter
  VIX-Klon gefunden; alle anderen Kandidaten als Fehlalarme erklärt
  (bewusste Fallback-Ketten oder Dispatcher-Branch-Unsensitivität, siehe
  oben).

### `ki-prompt-audit/` — Statisches Analyse-Skript zu HVP/IVR-Label-Bug (29.08.2026)

Entstanden als Reaktion auf den HVP/IVR-Label-Bug (Legal-Review-Zyklus 4/5,
SUITE.md Backlog №65/№68): das Label-Präfix `"IVR:"` war in
`runOptionsKiBriefing()` fest verdrahtet, auch im HVP-Fallback-Zweig — der
an die KI gesendete Prompt-Text enthielt dadurch wörtlich `"IVR:HVP96%"`
(zwei Indikator-Namen unaufgelöst im selben Feld). Die KI hat das im Output
korrekt zitiert — kein Sprachfehler der KI, sondern ein
Daten-Serialisierungs-Bug. Ein zweiter, unabhängiger Fundort derselben
Logik (`runBestOptionsOpportunityKI()`) wurde erst durch gezielte Nachsuche
gefunden, nicht durch den ersten Fix — genau das Muster, das dieses Skript
künftig systematisch statt zufällig finden soll.

Prüft `axel-scanner/index.html` + `ko-modules/*.js` (AST-basiert via
`acorn`, nicht Regex) auf:

1. **KI-Prompt-Aufrufstellen-Katalog** — vollständige Liste aller
   `koAiCall(...)`/`KoPrompts.get(...)`-Aufrufe mit Datei, Zeile,
   umschließender Funktion und Action-/Strategie-Name. Dient als Checkliste
   für UIQ-REGULATORY-LANGUAGE-SPEC.md §6 (Modul-Rollout-Status) und für
   künftige Begriffs-/Kausalitäts-Integritäts-Prüfungen (Spec §1.3/§1.4).
2. **Doppel-Label-Fallback-Muster** — findet Ausdrücke der Form
   `'LABEL1:' + (a.feldA != null ? ... : (a.feldB ? 'LABEL2' + ... : '--'))`,
   bei denen ein fest verdrahtetes äußeres Label mit einem abweichenden
   inneren Label im Fallback-Zweig kollidiert — exakt die Bug-Signatur vom
   29.08.2026.

#### Nutzung

Gleiches Verzeichnis-Layout wie `backlog-19-analyse/` (s.o.).

```bash
cd uiq-devtools
npm install
npm run ki-prompt-audit    # → ki-prompt-audit/report.md + report.json
```

#### Verifikation (29.08.2026, vor erstem produktiven Einsatz)

- **True-Positive-Test:** synthetische Datei mit dem exakten Bug-Muster
  gebaut — vom Skript korrekt gefunden, nachdem ein erster Implementierungs-
  fehler (Label-Erkennung griff nicht bei verschachtelten `+`-Ketten wie
  `a + 'IVR:' + (...)`, da linksassoziative Verkettung das Literal nicht
  direkt in `node.left` sondern im rechtesten Blatt der `node.left`-Teilkette
  platziert) behoben wurde.
- **Regressionstest gegen echten, bereits gefixten Code:** 0 Funde — beide
  bekannten Bugs (inkl. des zweiten, hier erst entdeckten Fundorts in
  `runBestOptionsOpportunityKI()`) sind behoben, keine dritte Stelle
  gefunden.
- **Extraktions-Bug während der Verifikation gefunden+behoben:** die
  wiederverwendete `extractInlineScripts()`-Funktion (identisch zu
  `backlog-19-analyse/analyze.js`) scheiterte an HTML-Kommentaren, deren
  Prosa-Text zufällig `<script`/`</script>`-ähnliche Zeichenfolgen enthält
  (z.B. Changelog-Kommentare, die über Script-Tags *sprechen*) — ein
  92-KB-Block mit echten `KoPrompts.get(...)`-Aufrufen wurde dadurch
  komplett übersprungen, ohne dass ein Fehler das sichtbar gemacht hätte.
  Fix hier: HTML-Kommentare vor der Extraktion entfernen. **Diese
  Einschränkung besteht vermutlich unverändert in
  `backlog-19-analyse/analyze.js`** (dort noch nicht nachgezogen) — bei
  nächster Gelegenheit dort ebenfalls beheben.
- **Ground-Truth-Abgleich:** 18 gefundene Aufrufstellen manuell gegen eine
  einfache String-Suche gegengeprüft (unter Ausschluss von Kommentar-/
  JSDoc-Vorkommen) — exakte Übereinstimmung.

#### Bekannte Einschränkungen

- Erkennt nur Binärkonkatenation (`'X:' + (...)`), keine Template-Strings
  (`` `X:${...}` ``).
- Erkennt keine Label-Konflikte, die über mehrere Funktionsaufrufe verteilt
  sind (z.B. Label in einer Hilfsfunktion, Fallback-Wert im Aufrufer).
- Funktionszuordnung ist grob (nächste umschließende benannte Funktion),
  nicht scope-exakt.
- Wie `backlog-19-analyse/`: analysiert `ko-modules` im main-Branch-Stand,
  nicht die exakt CDN-gepinnte Version.
- Ergebnisse sind Kandidatenlisten, keine Bug-Listen — jeder Fund braucht
  manuelle Prüfung.
