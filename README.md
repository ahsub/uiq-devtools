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

- 26.08.2026: Erste Version + erste Anwendung. Ergebnis siehe SUITE.md
  Backlog №57 (renderHomeLanding-Altcode) und №58
  (kvToScannerState/loadScannerFromKV-Fallback-Sync-Hinweis). Kein zweiter
  VIX-Klon gefunden; alle anderen Kandidaten als Fehlalarme erklärt
  (bewusste Fallback-Ketten oder Dispatcher-Branch-Unsensitivität, siehe
  oben).
