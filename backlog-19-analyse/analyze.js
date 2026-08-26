#!/usr/bin/env node
/**
 * UIQ Backlog Punkt 19 — Statisches Analyse-Skript
 * ===================================================
 * Prüft den UIQ-"Monolithen" (axel-scanner/index.html + ko-modules/*.js
 * + ko-aggregator/workers/*.js) auf drei Klassen von Bugs, die bisher
 * nur zufällig als Nebenprodukt anderer Debugging-Sessions auftauchten:
 *
 *   1. Funktionsaufrufe vs. echte Aufrufstellen
 *      (a) definierte Funktionen ohne jeden Call-Nachweis (toter Code /
 *          verwaiste Namespaces, wie der "tote Namespace" vom 14.07.)
 *      (b) Aufrufe von Namen, für die keine Definition gefunden wurde
 *          (Tippfehler, falsche Ladereihenfolge, fehlende Module)
 *   2. getElementById()-Referenzen vs. tatsächlich existierende IDs im
 *      HTML (verwaiste DOM-Referenzen, wie beim VIX-Bug zwei
 *      Pipelines auf dasselbe Element)
 *   3. Duplizierte Codeblöcke (identische Funktionskörper an mehreren
 *      Stellen — wie die vier duplizierten Ampel-Renderer vom 14.07.)
 *
 * WICHTIGE EINSCHRÄNKUNG (ehrlich, siehe Backlog Punkt 19):
 * Vollständigkeit ist bei dieser Codegröße nicht erreichbar. Dies ist
 * ein Werkzeug für systematisches statt zufälliges Finden — kein
 * Ersatz für Verständnis. Jeder Fund muss manuell geprüft werden,
 * bevor daraus eine Fix-Entscheidung wird.
 *
 * Bekannte Lücken dieser ersten Version:
 * - index.html pinnt jedes ko-modules/*.js auf einen eigenen CDN-Commit-
 *   Hash (jsdelivr@<hash>). Dieses Skript analysiert den aktuellen
 *   main-Branch-Stand von ko-modules, NICHT die exakten historisch
 *   gepinnten Versionen. Bei Abweichungen zwischen Pin und main kann
 *   das zu falschen Treffern führen (siehe Report-Kopf für die
 *   tatsächlich gefundenen Pins).
 * - dynamisch per innerHTML/Template erzeugte IDs werden nicht erkannt
 *   (kein DOM-Rendering, nur statische Textsuche) — Treffer in
 *   Abschnitt 2 "nicht gefunden" können falsch-positiv sein.
 * - Aufruf-Erkennung ist namensbasiert, nicht scope-genau (kein echter
 *   Type-/Scope-Checker) — Methodennamen auf verschiedenen Objekten
 *   mit demselben Namen werden zusammengeworfen.
 */

const fs = require("fs");
const path = require("path");
const acorn = require("acorn");
const walk = require("acorn-walk");
const crypto = require("crypto");

// Erwartetes Layout (siehe README): dieses Repo (uiq-devtools) liegt als
// Geschwister-Ordner neben axel-scanner/, ko-modules/, ko-aggregator/ in
// einem gemeinsamen Arbeitsordner. Abweichender Pfad via UIQ_WORKSPACE_ROOT.
const ROOT = process.env.UIQ_WORKSPACE_ROOT || path.resolve(__dirname, "..", "..");
const AXEL_SCANNER_HTML = path.join(ROOT, "axel-scanner", "index.html");
const KO_MODULES_DIR = path.join(ROOT, "ko-modules");
const KO_AGGREGATOR_WORKERS_DIR = path.join(ROOT, "ko-aggregator", "workers");

// Bekannte Browser-/Bibliotheks-Globals, die nie "definiert" im eigenen
// Code auftauchen, aber legitim aufgerufen werden. Wird konservativ
// gehalten, um Abschnitt 1(b) nicht mit Rauschen zu fluten.
const KNOWN_GLOBALS = new Set([
  "console","fetch","setTimeout","setInterval","clearTimeout","clearInterval",
  "parseInt","parseFloat","isNaN","isFinite","encodeURIComponent","decodeURIComponent",
  "JSON","Object","Array","String","Number","Boolean","Math","Date","Map","Set",
  "Promise","RegExp","Error","TypeError","RangeError","Symbol","Reflect","Proxy",
  "Intl","WeakMap","WeakSet","Function","document","window","navigator","location",
  "localStorage","sessionStorage","history","alert","confirm","prompt","requestAnimationFrame",
  "cancelAnimationFrame","addEventListener","removeEventListener","dispatchEvent",
  "getComputedStyle","btoa","atob","structuredClone","queueMicrotask","performance",
  "Chart","XMLHttpRequest","FormData","Blob","File","FileReader","URL","URLSearchParams",
  "AbortController","Worker","CustomEvent","Event","Node","Element","HTMLElement",
  "self","globalThis","require","module","exports","process","Buffer","__dirname","__filename",
  // gängige Array/String/Object-Prototypmethoden, die als "Aufruf" auftauchen,
  // wenn Callee reines Identifier-Matching ohne Objekt-Kontext ist:
  "map","filter","reduce","forEach","find","findIndex","includes","some","every",
  "slice","splice","push","pop","shift","unshift","join","split","replace","replaceAll",
  "trim","toLowerCase","toUpperCase","toFixed","toString","valueOf","indexOf","concat",
  "sort","reverse","keys","values","entries","assign","freeze","then","catch","finally",
  "call","apply","bind","hasOwnProperty","addEventListener","querySelector","querySelectorAll",
  "getElementById","getElementsByClassName","createElement","appendChild","removeChild",
  "setAttribute","getAttribute","removeAttribute","classList","closest","matches",
]);

function readFile(p) {
  return fs.readFileSync(p, "utf8");
}

function extractInlineScripts(html) {
  // Nur <script>...</script> OHNE src= (die externen sind die gepinnten
  // ko-modules-Dateien, die wir separat vom main-Branch laden).
  const scripts = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m, idx = 0;
  while ((m = re.exec(html)) !== null) {
    idx++;
    scripts.push({ file: `index.html#inline-script-${idx}`, code: m[1] });
  }
  return scripts;
}

function extractHtmlIds(html) {
  const ids = new Set();
  const re = /\bid\s*=\s*["']([^"']+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) ids.add(m[1]);
  return ids;
}

function extractHtmlInlineHandlerCalls(html) {
  // onclick="foo(...)" etc. zählen als Aufrufstellen, sonst wären alle
  // per HTML-Attribut verdrahteten Handler-Funktionen "false positive tot".
  const calls = new Set();
  const attrRe = /\bon\w+\s*=\s*["']([^"']*)["']/gi;
  let m;
  while ((m = attrRe.exec(html)) !== null) {
    const body = m[1];
    const callRe = /\b([A-Za-z_$][\w$]*)\s*\(/g;
    let c;
    while ((c = callRe.exec(body)) !== null) calls.add(c[1]);
  }
  return calls;
}

function loadJsSources() {
  const sources = [];

  // ko-modules (main-Branch-Stand)
  if (fs.existsSync(KO_MODULES_DIR)) {
    for (const f of fs.readdirSync(KO_MODULES_DIR)) {
      if (f.endsWith(".js") && !f.startsWith("test-")) {
        sources.push({ file: `ko-modules/${f}`, code: readFile(path.join(KO_MODULES_DIR, f)) });
      }
    }
  }

  // ko-aggregator Worker (ko-ai.js, ko-watchdog.js) — Teil derselben
  // Aufruf-Kette (Morning Briefing -> AI-Prompt-Erzeugung)
  if (fs.existsSync(KO_AGGREGATOR_WORKERS_DIR)) {
    const walkDir = (dir, prefix) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walkDir(full, `${prefix}${entry.name}/`);
        else if (entry.name.endsWith(".js"))
          sources.push({ file: `ko-aggregator/workers/${prefix}${entry.name}`, code: readFile(full) });
      }
    };
    walkDir(KO_AGGREGATOR_WORKERS_DIR, "");
  }

  return sources;
}

// ---------------------------------------------------------------------
// AST-Analyse
// ---------------------------------------------------------------------

function parse(code, file) {
  try {
    return acorn.parse(code, { ecmaVersion: "latest", sourceType: "script", allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true });
  } catch (e) {
    return { __parseError: `${file}: ${e.message}` };
  }
}

function analyzeSource(file, code, defs, calls, getElCalls, funcBodies, parseErrors) {
  const ast = parse(code, file);
  if (ast.__parseError) {
    parseErrors.push(ast.__parseError);
    return;
  }

  const lines = code.split("\n");
  const lineOf = (node) => code.slice(0, node.start).split("\n").length;

  walk.simple(ast, {
    FunctionDeclaration(node) {
      if (node.id) {
        const name = node.id.name;
        const bodySrc = code.slice(node.body.start, node.body.end);
        const key = `${file}:${lineOf(node)}`;
        defs.set(name, defs.get(name) || []);
        defs.get(name).push({ file, line: lineOf(node) });
        funcBodies.push({ name, file, line: lineOf(node), body: bodySrc });
      }
    },
    VariableDeclarator(node) {
      if (
        node.id.type === "Identifier" &&
        node.init &&
        (node.init.type === "FunctionExpression" || node.init.type === "ArrowFunctionExpression")
      ) {
        const name = node.id.name;
        defs.set(name, defs.get(name) || []);
        defs.get(name).push({ file, line: lineOf(node) });
        const bodySrc = code.slice(node.init.body.start, node.init.body.end);
        funcBodies.push({ name, file, line: lineOf(node), body: bodySrc });
      }
    },
    AssignmentExpression(node) {
      // window.X = function(){...}  ODER  window.X = someName;
      if (
        node.left.type === "MemberExpression" &&
        node.left.object.type === "Identifier" &&
        node.left.object.name === "window" &&
        node.left.property.type === "Identifier"
      ) {
        const name = node.left.property.name;
        if (node.right.type === "FunctionExpression" || node.right.type === "ArrowFunctionExpression") {
          defs.set(name, defs.get(name) || []);
          defs.get(name).push({ file, line: lineOf(node) });
          const bodySrc = code.slice(node.right.body.start, node.right.body.end);
          funcBodies.push({ name, file, line: lineOf(node), body: bodySrc });
        } else if (node.right.type === "Identifier") {
          // Alias: window.Foo = Foo  -> zählt als Referenz auf Foo, nicht als neue Def.
          calls.set(node.right.name, calls.get(node.right.name) || []);
          calls.get(node.right.name).push({ file, line: lineOf(node) });
        }
      }
    },
    CallExpression(node) {
      let name = null;
      if (node.callee.type === "Identifier") {
        name = node.callee.name;
      } else if (node.callee.type === "MemberExpression" && node.callee.property.type === "Identifier") {
        name = node.callee.property.name;
        if (name === "getElementById" && node.arguments[0] && node.arguments[0].type === "Literal") {
          getElCalls.push({ id: node.arguments[0].value, file, line: lineOf(node) });
        }
      }
      if (name) {
        calls.set(name, calls.get(name) || []);
        calls.get(name).push({ file, line: lineOf(node) });
      }
    },
  });
}

function normalizeForHash(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

function main() {
  const htmlExists = fs.existsSync(AXEL_SCANNER_HTML);
  if (!htmlExists) {
    console.error("axel-scanner/index.html nicht gefunden unter " + AXEL_SCANNER_HTML);
    process.exit(1);
  }
  const html = readFile(AXEL_SCANNER_HTML);
  const htmlIds = extractHtmlIds(html);
  const handlerCalls = extractHtmlInlineHandlerCalls(html);

  const cdnPinRe = /ko-modules@([\w]+)\/([\w.-]+\.js)/g;
  const pins = [];
  let pm;
  while ((pm = cdnPinRe.exec(html)) !== null) pins.push({ file: pm[2], pin: pm[1] });

  const sources = [
    ...extractInlineScripts(html),
    ...loadJsSources(),
  ];

  const defs = new Map();   // name -> [{file, line}]
  const calls = new Map();  // name -> [{file, line}]
  const getElCalls = [];    // [{id, file, line}]
  const funcBodies = [];    // [{name, file, line, body}]
  const parseErrors = [];

  for (const s of sources) analyzeSource(s.file, s.code, defs, calls, getElCalls, funcBodies, parseErrors);

  // HTML-Inline-Handler zählen als zusätzliche Call-Nachweise
  for (const name of handlerCalls) {
    calls.set(name, calls.get(name) || []);
    calls.get(name).push({ file: "index.html", line: 0, via: "on*-Attribut" });
  }

  // ---- 1a: definierte Funktionen ohne jeden Aufruf-Nachweis ----
  const uncalledFns = [];
  for (const [name, defLocs] of defs) {
    if (!calls.has(name)) {
      uncalledFns.push({ name, defLocs });
    }
  }
  uncalledFns.sort((a, b) => a.name.localeCompare(b.name));

  // ---- 1b: aufgerufene Namen ohne jede Definition ----
  const undefinedCalls = [];
  for (const [name, callLocs] of calls) {
    if (!defs.has(name) && !KNOWN_GLOBALS.has(name)) {
      undefinedCalls.push({ name, callLocs });
    }
  }
  undefinedCalls.sort((a, b) => b.callLocs.length - a.callLocs.length);

  // ---- 2: getElementById gegen echte IDs ----
  const missingIds = [];
  const idUsage = new Map();
  for (const c of getElCalls) {
    idUsage.set(c.id, idUsage.get(c.id) || []);
    idUsage.get(c.id).push({ file: c.file, line: c.line });
  }
  for (const [id, locs] of idUsage) {
    if (!htmlIds.has(id)) missingIds.push({ id, locs });
  }
  // Zusätzlich: IDs, auf die aus MEHREREN verschiedenen Dateien
  // geschrieben/gelesen wird (Kandidaten für den VIX-Bug-Musterfall:
  // "zwei Datenquellen schreiben ins selbe Element")
  const multiFileIds = [];
  for (const [id, locs] of idUsage) {
    const files = new Set(locs.map((l) => l.file));
    if (files.size > 1) multiFileIds.push({ id, files: [...files], count: locs.length });
  }
  multiFileIds.sort((a, b) => b.files.length - a.files.length);

  // ---- 3: duplizierte Funktionskörper ----
  const byHash = new Map();
  for (const fb of funcBodies) {
    const norm = normalizeForHash(fb.body);
    if (norm.length < 60) continue; // triviale/leere Bodies ignorieren
    const hash = crypto.createHash("sha1").update(norm).digest("hex");
    byHash.set(hash, byHash.get(hash) || []);
    byHash.get(hash).push(fb);
  }
  const duplicateGroups = [...byHash.values()].filter((g) => g.length > 1);
  duplicateGroups.sort((a, b) => b.length - a.length);

  // ---- Report ----
  const lines = [];
  lines.push("# UIQ Backlog Punkt 19 — Analyse-Report");
  lines.push(`Erzeugt: ${new Date().toISOString()}`);
  lines.push("");
  lines.push("## Analysierte Quellen");
  lines.push(`- axel-scanner/index.html (${extractInlineScripts(html).length} Inline-Script-Blöcke)`);
  lines.push(`- ko-modules/*.js: ${fs.readdirSync(KO_MODULES_DIR).filter(f=>f.endsWith('.js')&&!f.startsWith('test-')).length} Dateien (main-Branch-Stand, NICHT die CDN-gepinnten Versionen)`);
  lines.push(`- ko-aggregator/workers/**/*.js`);
  lines.push("");
  lines.push("### Gefundene CDN-Pins in index.html (main-Branch kann abweichen!)");
  for (const p of pins) lines.push(`- ${p.file} @ ${p.pin}`);
  lines.push("");
  if (parseErrors.length) {
    lines.push("## ⚠️ Parse-Fehler (Datei wurde NICHT analysiert)");
    for (const e of parseErrors) lines.push(`- ${e}`);
    lines.push("");
  }

  lines.push("## 1a. Definierte Funktionen ohne jeden gefundenen Aufruf");
  lines.push(`(${uncalledFns.length} Treffer — Kandidaten für toten Code / verwaiste Namespaces. `
    + `Manuell prüfen: evtl. dynamisch aufgerufen (z.B. via window[name]()), evtl. echt tot.)`);
  lines.push("");
  for (const f of uncalledFns) {
    lines.push(`- **${f.name}** — definiert in ${f.defLocs.map(l=>`${l.file}:${l.line}`).join(", ")}`);
  }
  lines.push("");

  lines.push("## 1b. Aufgerufene Namen ohne gefundene Definition");
  lines.push(`(${undefinedCalls.length} Treffer, nach Häufigkeit sortiert — Kandidaten für Tippfehler, `
    + `falsche Ladereihenfolge oder Bibliotheksfunktionen, die nicht in der Globals-Liste stehen. `
    + `Viele davon sind erwartungsgemäß harmlos — bitte gegenlesen.)`);
  lines.push("");
  for (const c of undefinedCalls.slice(0, 80)) {
    lines.push(`- **${c.name}** — ${c.callLocs.length}x aufgerufen, z.B. ${c.callLocs.slice(0,3).map(l=>`${l.file}:${l.line}`).join(", ")}`);
  }
  if (undefinedCalls.length > 80) lines.push(`- … und ${undefinedCalls.length - 80} weitere (siehe JSON-Rohdaten)`);
  lines.push("");

  lines.push("## 2a. getElementById()-Aufrufe auf nicht gefundene IDs");
  lines.push(`(${missingIds.length} Treffer — ACHTUNG: dynamisch per innerHTML/Template erzeugte IDs `
    + `werden hier fälschlich als "fehlend" auftauchen. Jeder Treffer braucht manuelle Prüfung.)`);
  lines.push("");
  for (const m of missingIds) {
    lines.push(`- **#${m.id}** — referenziert in ${m.locs.map(l=>`${l.file}:${l.line}`).join(", ")}`);
  }
  lines.push("");

  lines.push("## 2b. IDs, auf die aus mehreren verschiedenen Dateien zugegriffen wird");
  lines.push(`(${multiFileIds.length} Treffer — das ist genau das VIX-Bug-Muster: mehrere unabhängige `
    + `Quellen schreiben/lesen dasselbe DOM-Element. Nicht automatisch ein Bug, aber Prio-Kandidaten `
    + `für eine gezielte Durchsicht.)`);
  lines.push("");
  for (const m of multiFileIds.slice(0, 40)) {
    lines.push(`- **#${m.id}** — ${m.count}x aus: ${m.files.join(", ")}`);
  }
  lines.push("");

  lines.push("## 3. Duplizierte Funktionskörper (identisch nach Normalisierung)");
  lines.push(`(${duplicateGroups.length} Gruppen — das ist genau das Ampel-Renderer-Muster vom 14.07. `
    + `Nur EXAKTE Duplikate nach Whitespace/Kommentar-Normalisierung; ähnliche-aber-nicht-identische `
    + `Duplikate werden hier NICHT erkannt (siehe Empfehlung unten).)`);
  lines.push("");
  for (const g of duplicateGroups) {
    lines.push(`- Funktionsname(n): ${[...new Set(g.map(x=>x.name))].join(", ")} — ${g.length}x identisch:`);
    for (const fb of g) lines.push(`  - ${fb.file}:${fb.line}`);
  }
  lines.push("");

  lines.push("## Zusammenfassung");
  lines.push(`- ${uncalledFns.length} Funktionen ohne Aufruf-Nachweis`);
  lines.push(`- ${undefinedCalls.length} Aufrufe ohne Definitions-Nachweis`);
  lines.push(`- ${missingIds.length} getElementById-Aufrufe auf nicht gefundene IDs`);
  lines.push(`- ${multiFileIds.length} IDs mit datei-übergreifendem Zugriff`);
  lines.push(`- ${duplicateGroups.length} Gruppen exakt duplizierter Funktionskörper`);
  lines.push("");
  lines.push("**Nächster Schritt:** jede Kategorie ist eine Kandidatenliste, keine Bug-Liste. "
    + "Manuelle Sichtung priorisiert nach: (1) 2b (Mehrfachzugriff auf ein Element — höchste "
    + "Trefferquote fürs VIX-Bug-Muster), (2) 3 (exakte Duplikate — höchste Trefferquote fürs "
    + "Ampel-Renderer-Muster), (3) 1a (toter Code — Aufräum-Kandidaten, kein akuter Bug), "
    + "(4) 1b (am meisten Rauschen, zuletzt prüfen).");

  fs.writeFileSync(path.join(__dirname, "report.md"), lines.join("\n"));
  fs.writeFileSync(
    path.join(__dirname, "report.json"),
    JSON.stringify({ uncalledFns, undefinedCalls, missingIds, multiFileIds, duplicateGroups, pins, parseErrors }, null, 2)
  );

  console.log("Report geschrieben: analyse-skript/report.md und report.json");
  console.log(`1a uncalled: ${uncalledFns.length} | 1b undefined calls: ${undefinedCalls.length} | 2a missing ids: ${missingIds.length} | 2b multi-file ids: ${multiFileIds.length} | 3 dup groups: ${duplicateGroups.length}`);
}

main();
