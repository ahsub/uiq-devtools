#!/usr/bin/env node
/**
 * UIQ KI-Prompt-Audit — Statisches Analyse-Skript
 * ===================================================
 * Entstanden am 29.08.2026 als Reaktion auf den HVP/IVR-Label-Bug
 * (Legal-Review-Zyklus 4/5): das Label-Präfix "IVR:" war in
 * runOptionsKiBriefing() fest verdrahtet, auch im HVP-Fallback-Zweig —
 * der an die KI gesendete Prompt-Text enthielt dadurch wörtlich
 * "IVR:HVP96%" (zwei Indikator-Namen unaufgelöst im selben Feld). Die KI
 * hat das im Output korrekt zitiert ("Extreme IV-Percentile (HVP96%)") —
 * kein Sprachfehler der KI, sondern ein Daten-Serialisierungs-Bug. Ein
 * zweiter, unabhängiger Fundort derselben Logik (runBestOptionsOpportunityKI())
 * wurde erst durch gezielte Nachsuche gefunden, nicht durch den ersten Fix.
 *
 * Dieses Skript prüft `axel-scanner/index.html` + `ko-modules/*.js` auf
 * zwei zusammenhängende Fragen:
 *
 *   1. KI-Prompt-Aufrufstellen-Katalog — WO im Code wird Scan-/Ticker-
 *      Rohdaten in Text serialisiert, der an eine KI (koAiCall / KoPrompts.get)
 *      geht? Eine vollständige Fundliste ist Voraussetzung dafür, systematisch
 *      statt zufällig zu prüfen, ob dort Begriffs-Integrität verletzt wird
 *      (s. UIQ-REGULATORY-LANGUAGE-SPEC.md §1.3).
 *   2. Doppel-Label-Fallback-Muster — WO existiert ein Fallback-Ternary
 *      (`a.feldA != null ? ... : (a.feldB ? 'LABEL' + ... : '--')`), bei dem
 *      ein AUSSERHALB fest verdrahtetes Label-Präfix mit einem ANDEREN,
 *      innerhalb der Fallback-Verzweigung eingebetteten Label kollidiert?
 *      Das ist exakt die Bug-Signatur vom 29.08.2026.
 *
 * WICHTIGE EINSCHRÄNKUNG (ehrlich, analog Backlog Punkt 19):
 * Dies ist ein Werkzeug für systematisches statt zufälliges Finden — kein
 * Ersatz für Verständnis. Abschnitt 2 ist eine Heuristik über den AST, kein
 * vollständiger Beweis der Abwesenheit ähnlicher Bugs (z.B. Bugs über
 * mehrere Funktionsaufrufe hinweg, oder Label-Konflikte in Template-Strings
 * statt Binärkonkatenation, werden nicht erkannt). Jeder Fund muss manuell
 * geprüft werden, bevor daraus eine Fix-Entscheidung wird.
 */

const fs = require("fs");
const path = require("path");
const acorn = require("acorn");
const walk = require("acorn-walk");

const ROOT = process.env.UIQ_WORKSPACE_ROOT || path.resolve(__dirname, "..", "..");
const AXEL_SCANNER_HTML = path.join(ROOT, "axel-scanner", "index.html");
const KO_MODULES_DIR = path.join(ROOT, "ko-modules");

function readFile(p) {
  return fs.readFileSync(p, "utf8");
}

// Wiederverwendet aus backlog-19-analyse/analyze.js (identische Logik,
// bewusst dupliziert statt cross-Modul-importiert — dieses Repo ist reines
// Wegwerf-Tooling, keine Bibliothek mit eigener Modul-Hygiene-Pflicht).
function extractInlineScripts(html) {
  // FIX (29.08.2026, waehrend Erstverifikation dieses Skripts gefunden):
  // HTML-Kommentare mit Prosa-Text, der zufaellig "<script"/"</script>"-
  // aehnliche Zeichenfolgen enthaelt (z.B. Changelog-Kommentare, die ueber
  // Script-Tags SPRECHEN), verwirren die reine Regex-Grenzerkennung — ein
  // Kommentar kann so faelschlich als Script-Block-Ende/-Anfang gelesen
  // werden. Ergebnis: ein Block verschluckt mehrere echte <script>-Tags
  // inkl. deren Inhalt (im Test: ein 92KB-Block, der u.a. echte
  // KoPrompts.get(...)-Aufrufe enthielt, aber wegen Parse-Fehler komplett
  // uebersprungen wurde — genau die Art von stillem Coverage-Verlust, den
  // dieses Skript eigentlich verhindern soll). Fix: HTML-Kommentare vor der
  // Script-Extraktion entfernen. Sicher, da modernes ES6 keine
  // HTML-Kommentar-Syntax (<!-- -->) in Script-Inhalten braucht.
  const htmlWithoutComments = html.replace(/<!--[\s\S]*?-->/g, "");
  const scripts = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m, idx = 0;
  while ((m = re.exec(htmlWithoutComments)) !== null) {
    idx++;
    scripts.push({ file: `index.html#inline-script-${idx}`, code: m[1] });
  }
  return scripts;
}

function loadSources() {
  const sources = [];
  if (fs.existsSync(AXEL_SCANNER_HTML)) {
    sources.push(...extractInlineScripts(readFile(AXEL_SCANNER_HTML)));
  } else {
    console.error(`WARNUNG: ${AXEL_SCANNER_HTML} nicht gefunden.`);
  }
  if (fs.existsSync(KO_MODULES_DIR)) {
    for (const f of fs.readdirSync(KO_MODULES_DIR)) {
      if (f.endsWith(".js") && !f.startsWith("test-")) {
        sources.push({ file: `ko-modules/${f}`, code: readFile(path.join(KO_MODULES_DIR, f)) });
      }
    }
  } else {
    console.error(`WARNUNG: ${KO_MODULES_DIR} nicht gefunden.`);
  }
  return sources;
}

function parse(code, file) {
  try {
    return acorn.parse(code, { ecmaVersion: 2022, sourceType: "script", allowReturnOutsideFunction: true, locations: true });
  } catch (e) {
    try {
      return acorn.parse(code, { ecmaVersion: 2022, sourceType: "module", allowReturnOutsideFunction: true, locations: true });
    } catch (e2) {
      console.error(`PARSE-FEHLER in ${file}: ${e2.message}`);
      return null;
    }
  }
}

// ── Teil 1: KI-Prompt-Aufrufstellen-Katalog ──────────────────────────────
// Heuristik: (a) direkte Aufrufe von koAiCall(...) / KoPrompts.get(...),
// (b) umschließende benannte Funktion (falls vorhanden) als Kontext.
function findKiCallSites(sources) {
  const sites = [];
  for (const { file, code } of sources) {
    const ast = parse(code, file);
    if (!ast) continue;

    // Funktionsgrenzen vorab sammeln, um jeden Fund seiner umschließenden
    // Funktion zuzuordnen (grob, nicht scope-exakt — reicht für einen
    // Katalog-Zweck völlig aus).
    const fnRanges = [];
    walk.simple(ast, {
      FunctionDeclaration(n) { if (n.id) fnRanges.push({ name: n.id.name, start: n.start, end: n.end }); },
      FunctionExpression(n) {
        // Name aus VariableDeclarator oder Property ableiten, falls anonym zugewiesen
      },
    });

    function enclosingFn(pos) {
      let best = null;
      for (const r of fnRanges) {
        if (pos >= r.start && pos <= r.end) {
          if (!best || (r.end - r.start) < (best.end - best.start)) best = r;
        }
      }
      return best ? best.name : "(top-level/anonym)";
    }

    walk.simple(ast, {
      CallExpression(node) {
        const callee = node.callee;
        let label = null;
        if (callee.type === "Identifier" && callee.name === "koAiCall") {
          label = "koAiCall(...)";
        } else if (
          callee.type === "MemberExpression" &&
          callee.property.type === "Identifier" &&
          callee.property.name === "get" &&
          callee.object.type === "Identifier" &&
          callee.object.name === "KoPrompts"
        ) {
          label = "KoPrompts.get(...)";
        }
        if (label) {
          const line = ast.locations ? undefined : undefined;
          const loc = code.slice(0, node.start).split("\n").length;
          sites.push({
            file,
            line: loc,
            call: label,
            enclosingFn: enclosingFn(node.start),
            // Erstes Argument (Action-Name / Strategie-ID) falls Literal
            firstArg: node.arguments[0] && node.arguments[0].type === "Literal" ? node.arguments[0].value : null,
          });
        }
      },
    });
  }
  return sites;
}

// ── Teil 2: Doppel-Label-Fallback-Muster ─────────────────────────────────
// Sucht BinaryExpression-Ketten der Form:
//   'LABEL1:' + ( a.feldA != null ? ... : ( a.feldB ? 'LABEL2' + ... : '--' ) )
// und meldet einen Fund, wenn LABEL1 (der äußere, fest verdrahtete Präfix)
// und LABEL2 (ein String-Literal irgendwo im Fallback-Zweig) unterschiedliche
// "label-artige" Strings sind (Heuristik: 2-6 Grossbuchstaben, ggf. mit ':').
function isLabelLike(str) {
  return typeof str === "string" && /^[A-Z]{2,6}:?$/.test(str.trim());
}

// '+'-Ketten sind linksassoziativ verschachtelt: `a + 'IVR:' + (cond ? ...)`
// parst als `((a + 'IVR:') + (cond ? ...))`. Das Label-Literal steckt daher
// meist NICHT direkt in node.left, sondern im rechtesten Blatt der
// node.left-Teilkette. Diese Funktion läuft die Kette bis zum rechtesten
// Operanden durch, egal wie tief verschachtelt.
function getRightmostOperand(node) {
  if (node.type === "BinaryExpression" && node.operator === "+") {
    return getRightmostOperand(node.right);
  }
  return node;
}

function collectStringLiterals(node, acc) {
  if (!node || typeof node !== "object") return;
  if (node.type === "Literal" && typeof node.value === "string") acc.push(node.value);
  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "start" || key === "end" || key === "range") continue;
    const val = node[key];
    if (Array.isArray(val)) val.forEach((v) => collectStringLiterals(v, acc));
    else if (val && typeof val.type === "string") collectStringLiterals(val, acc);
  }
}

function findDoubleLabelPatterns(sources) {
  const findings = [];
  for (const { file, code } of sources) {
    const ast = parse(code, file);
    if (!ast) continue;

    walk.simple(ast, {
      BinaryExpression(node) {
        if (node.operator !== "+") return;
        // Nur an der "obersten" '+' in einer Kette auswerten, sonst wird
        // derselbe Fund pro Verschachtelungsebene mehrfach gemeldet.
        if (node.left.type === "BinaryExpression" && node.left.operator === "+") {
          // Diese Ebene wird von der Elternebene (oder als Startpunkt weiter
          // unten in der Kette) ohnehin über getRightmostOperand erfasst —
          // aber wir wollen trotzdem JEDE Ebene prüfen, bei der rechts ein
          // ConditionalExpression folgt, weil das Label ueberall in der Kette
          // sitzen kann. Daher: kein Skip, nur die Literal-Extraktion ändert
          // sich (s.u.).
        }
        if (node.right.type !== "ConditionalExpression") return;

        const leftmostLiteralNode = getRightmostOperand(node.left);
        if (leftmostLiteralNode.type !== "Literal" || typeof leftmostLiteralNode.value !== "string") return;
        const outerLabel = leftmostLiteralNode.value.trim();
        if (!isLabelLike(outerLabel)) return;

        // Alle String-Literale im Fallback-Baum (rechte Seite) einsammeln
        const innerLiterals = [];
        collectStringLiterals(node.right, innerLiterals);

        const conflicting = innerLiterals.filter((s) => {
          const t = s.trim();
          return isLabelLike(t) && t.replace(":", "") !== outerLabel.replace(":", "");
        });

        if (conflicting.length > 0) {
          const line = code.slice(0, node.start).split("\n").length;
          findings.push({
            file,
            line,
            outerLabel,
            conflictingInnerLabels: [...new Set(conflicting)],
            snippet: code.slice(node.start, Math.min(node.end, node.start + 200)).replace(/\s+/g, " "),
          });
        }
      },
    });
  }
  return findings;
}

// ── Report ────────────────────────────────────────────────────────────────
function buildReport(kiSites, doubleLabelFindings) {
  const lines = [];
  lines.push("# UIQ KI-Prompt-Audit — Report");
  lines.push("");
  lines.push(`Stand: ${new Date().toISOString()}`);
  lines.push(`Workspace-Root: ${ROOT}`);
  lines.push("");
  lines.push("## 1. KI-Prompt-Aufrufstellen-Katalog");
  lines.push("");
  lines.push(`${kiSites.length} Aufrufstellen gefunden.`);
  lines.push("");
  lines.push("| Datei | Zeile | Call | Umschließende Funktion | Erstes Argument |");
  lines.push("|---|---|---|---|---|");
  for (const s of kiSites) {
    lines.push(`| ${s.file} | ${s.line} | ${s.call} | ${s.enclosingFn} | ${s.firstArg ?? "–"} |`);
  }
  lines.push("");
  lines.push("**Verwendung:** Diese Liste ist die Checkliste für künftige Begriffs-/");
  lines.push("Kausalitäts-Integritäts-Prüfungen (UIQ-REGULATORY-LANGUAGE-SPEC.md §1.3/§1.4).");
  lines.push("Jede neue oder geänderte Aufrufstelle sollte hier wieder auftauchen — ein");
  lines.push("Rückgang der Anzahl ohne bekannten Grund ist ein Warnsignal für versehentlich");
  lines.push("entfernte Prompt-Bauten.");
  lines.push("");
  lines.push("## 2. Doppel-Label-Fallback-Muster (HVP/IVR-Bug-Signatur)");
  lines.push("");
  if (doubleLabelFindings.length === 0) {
    lines.push("**Keine Funde.** Alle geprüften Fallback-Ternaries verwenden ein konsistentes Label.");
  } else {
    lines.push(`⚠️ **${doubleLabelFindings.length} Fund(e)** — jeder muss manuell geprüft werden, bevor er als Bug gilt (Heuristik, kein Beweis).`);
    lines.push("");
    lines.push("| Datei | Zeile | Äußeres Label | Kollidierende innere Labels | Ausschnitt |");
    lines.push("|---|---|---|---|---|");
    for (const f of doubleLabelFindings) {
      lines.push(`| ${f.file} | ${f.line} | \`${f.outerLabel}\` | \`${f.conflictingInnerLabels.join(", ")}\` | \`${f.snippet}\` |`);
    }
  }
  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push("## Bekannte Einschränkungen");
  lines.push("");
  lines.push("- Analysiert `ko-modules` im main-Branch-Stand, nicht die exakt CDN-gepinnte Version von `index.html` (identische Einschränkung wie `backlog-19-analyse`).");
  lines.push("- Teil 2 erkennt nur Binärkonkatenation (`'X:' + (...)`), keine Template-Strings (`` `X:${...}` ``) und keine Label-Konflikte, die über mehrere Funktionsaufrufe verteilt sind.");
  lines.push("- Teil 1 ordnet Funde ihrer *nächsten umschließenden* benannten Funktion zu (grob, nicht scope-exakt) — anonyme Top-Level-Handler erscheinen als \"(top-level/anonym)\".");
  lines.push("- Ergebnisse sind Kandidatenlisten, keine Bug-Listen — s. Kopf-Kommentar dieses Skripts.");
  lines.push("");
  return lines.join("\n");
}

function main() {
  const sources = loadSources();
  console.log(`Geladen: ${sources.length} Quell-Blöcke.`);

  const kiSites = findKiCallSites(sources);
  const doubleLabelFindings = findDoubleLabelPatterns(sources);

  const report = buildReport(kiSites, doubleLabelFindings);
  const outMd = path.join(__dirname, "report.md");
  const outJson = path.join(__dirname, "report.json");
  fs.writeFileSync(outMd, report, "utf8");
  fs.writeFileSync(outJson, JSON.stringify({ kiSites, doubleLabelFindings }, null, 2), "utf8");

  console.log(`\nKI-Prompt-Aufrufstellen: ${kiSites.length}`);
  console.log(`Doppel-Label-Funde: ${doubleLabelFindings.length}`);
  console.log(`\nReport geschrieben: ${outMd}`);
  console.log(`JSON geschrieben:   ${outJson}`);
}

main();
