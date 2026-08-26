#!/usr/bin/env node
/**
 * Verfeinerung von §2b (Backlog Punkt 19, Runde 2)
 * ===================================================
 * Erste Version (analyze.js) zeigte 43 IDs mit datei-übergreifendem
 * Zugriff — Stichprobe (#ticker-preset, #custom-input, #scan-container)
 * ergab: alle Schreibzugriffe dort sind an Nutzeraktionen (Klick/Input)
 * gebunden. Das ist harmlos, weil sequenziell und beabsichtigt.
 *
 * Der VIX-Bug war gefährlich, weil zwei AUTOMATISCHE Hintergrund-
 * funktionen (Teil derselben Pipeline, ohne Koordination) auf
 * dasselbe Element schrieben. Diese Verfeinerung filtert §2b auf genau
 * dieses Muster:
 *
 *   Ein "automatischer Schreib-Kollisionskandidat" ist eine ID, auf die
 *   aus MINDESTENS 2 VERSCHIEDENEN Funktionen geschrieben wird, die
 *   beide automatisch erreichbar sind (Timer, load-Event, Top-Level-
 *   Skriptausführung, oder transitiv von einer solchen Quelle
 *   aufgerufen) — NICHT nur von Klick-/Change-/Input-Handlern.
 *
 * Klassifikation von "automatisch":
 *   - Callback von setInterval(...)/setTimeout(...) → immer automatisch
 *     (auch wenn der set...-Aufruf selbst in einem Klick-Handler steht:
 *     der Callback selbst läuft später unbeaufsichtigt)
 *   - Callback von addEventListener('load'|'DOMContentLoaded', ...)
 *   - Direkter Top-Level-Aufruf im <script>-Block (läuft beim Laden der
 *     Seite, ohne Nutzerinteraktion)
 *   - Transitiv: jede Funktion, die von einer automatischen Funktion
 *     aufgerufen wird (namensbasierte Call-Graph-Suche, wie in analyze.js)
 *
 * EINSCHRÄNKUNGEN (wie in analyze.js):
 * - Call-Graph ist namensbasiert, nicht scope-exakt.
 * - "Schreiben" wird erkannt für: (a) direktes Ketten-Muster
 *   `getElementById(x).prop = wert`, (b) Variable-dann-Schreiben-Muster
 *   `var el = getElementById(x); ...; el.prop = wert;` INNERHALB
 *   derselben Funktion. Schreiben über mehrere Funktionsebenen hinweg
 *   (Variable wird an eine andere Funktion weitergereicht) wird NICHT
 *   erkannt.
 * - main-Branch-Stand von ko-modules, nicht die CDN-gepinnten Versionen.
 */

const fs = require("fs");
const path = require("path");
const acorn = require("acorn");
const walk = require("acorn-walk");

// Erwartetes Layout (siehe README): dieses Repo (uiq-devtools) liegt als
// Geschwister-Ordner neben axel-scanner/, ko-modules/, ko-aggregator/ in
// einem gemeinsamen Arbeitsordner. Abweichender Pfad via UIQ_WORKSPACE_ROOT.
const ROOT = process.env.UIQ_WORKSPACE_ROOT || path.resolve(__dirname, "..", "..");
const AXEL_SCANNER_HTML = path.join(ROOT, "axel-scanner", "index.html");
const KO_MODULES_DIR = path.join(ROOT, "ko-modules");
const KO_AGGREGATOR_WORKERS_DIR = path.join(ROOT, "ko-aggregator", "workers");

function readFile(p) { return fs.readFileSync(p, "utf8"); }

function extractInlineScripts(html) {
  const scripts = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m, idx = 0;
  while ((m = re.exec(html)) !== null) { idx++; scripts.push({ file: `index.html#inline-script-${idx}`, code: m[1] }); }
  return scripts;
}

function loadJsSources() {
  const sources = [];
  if (fs.existsSync(KO_MODULES_DIR)) {
    for (const f of fs.readdirSync(KO_MODULES_DIR)) {
      if (f.endsWith(".js") && !f.startsWith("test-"))
        sources.push({ file: `ko-modules/${f}`, code: readFile(path.join(KO_MODULES_DIR, f)) });
    }
  }
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

const FN_TYPES = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"]);

function nearestEnclosingFunction(ancestors) {
  // ancestors[ancestors.length-1] is the node itself
  for (let i = ancestors.length - 2; i >= 0; i--) {
    if (FN_TYPES.has(ancestors[i].type)) return ancestors[i];
  }
  return null; // top-level
}

function fnKey(file, node) {
  return node ? `${file}@${node.start}` : `${file}@top-level`;
}

function main() {
  const html = readFile(AXEL_SCANNER_HTML);
  const sources = [...extractInlineScripts(html), ...loadJsSources()];

  // Pass 1 per file: parse, collect function name map (fnKey -> name),
  // automatic-entry fnKeys, call edges (callerKey -> Set(calleeName)),
  // getElementById occurrences with enclosing fnKey + variable binding,
  // and write sites (fnKey -> Set(varNameOrDirect)).
  const fnName = new Map();       // fnKey -> name|null
  const fnFile = new Map();       // fnKey -> file
  const autoEntry = new Set();    // fnKeys that are automatic entry points
  const callEdges = new Map();    // callerKey -> Set(calleeName)
  const geIdAccess = [];          // {id, fnKey, file, line, varName|null, isDirectChain}
  const writesInFn = new Map();   // fnKey -> Set(varName)  (varName === '::direct::<id>' for chained writes)

  for (const src of sources) {
    let ast;
    try {
      ast = acorn.parse(src.code, { ecmaVersion: "latest", sourceType: "script", allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true });
    } catch (e) { continue; }

    const lineOf = (node) => src.code.slice(0, node.start).split("\n").length;

    // 1) register all function nodes + names
    walk.ancestor(ast, {
      FunctionDeclaration(node, anc) {
        fnName.set(fnKey(src.file, node), node.id ? node.id.name : null);
        fnFile.set(fnKey(src.file, node), src.file);
      },
      FunctionExpression(node, anc) {
        // Named via VariableDeclarator or window.X = or object property
        let name = null;
        const parent = anc[anc.length - 2];
        if (parent) {
          if (parent.type === "VariableDeclarator" && parent.id.type === "Identifier") name = parent.id.name;
          else if (parent.type === "AssignmentExpression" && parent.left.type === "MemberExpression" && parent.left.property.type === "Identifier") name = parent.left.property.name;
          else if (parent.type === "Property" && parent.key.type === "Identifier") name = parent.key.name;
        }
        fnName.set(fnKey(src.file, node), name);
        fnFile.set(fnKey(src.file, node), src.file);
      },
      ArrowFunctionExpression(node, anc) {
        let name = null;
        const parent = anc[anc.length - 2];
        if (parent) {
          if (parent.type === "VariableDeclarator" && parent.id.type === "Identifier") name = parent.id.name;
          else if (parent.type === "AssignmentExpression" && parent.left.type === "MemberExpression" && parent.left.property.type === "Identifier") name = parent.left.property.name;
        }
        fnName.set(fnKey(src.file, node), name);
        fnFile.set(fnKey(src.file, node), src.file);
      },
    });

    // 2) find automatic entries: setInterval/setTimeout(callback,...),
    //    addEventListener('load'|'DOMContentLoaded', callback),
    //    and top-level (no enclosing function) call expressions.
    walk.ancestor(ast, {
      CallExpression(node, anc) {
        const callee = node.callee;
        let calleeName = null;
        if (callee.type === "Identifier") calleeName = callee.name;
        else if (callee.type === "MemberExpression" && callee.property.type === "Identifier") calleeName = callee.property.name;

        if ((calleeName === "setInterval" || calleeName === "setTimeout") && node.arguments[0]) {
          markAutomatic(node.arguments[0], src.file);
        }
        if (calleeName === "addEventListener" && node.arguments.length >= 2) {
          const evtArg = node.arguments[0];
          const handlerArg = node.arguments[1];
          if (evtArg.type === "Literal" && (evtArg.value === "load" || evtArg.value === "DOMContentLoaded")) {
            markAutomatic(handlerArg, src.file);
          }
        }
        // top-level call: enclosing function is null
        const enc = nearestEnclosingFunction(anc);
        if (!enc) {
          if (calleeName) autoEntry.add(`NAME::${calleeName}`); // mark by name too (best-effort)
        }
      },
    });

    function markAutomatic(argNode, file) {
      if (argNode.type === "Identifier") {
        autoEntry.add(`NAME::${argNode.name}`);
      } else if (FN_TYPES.has(argNode.type)) {
        autoEntry.add(fnKey(file, argNode));
      }
    }

    // 3) call graph edges: for every function, which names does it call?
    walk.ancestor(ast, {
      CallExpression(node, anc) {
        const callee = node.callee;
        let calleeName = null;
        if (callee.type === "Identifier") calleeName = callee.name;
        else if (callee.type === "MemberExpression" && callee.property.type === "Identifier") calleeName = callee.property.name;
        if (!calleeName) return;
        const enc = nearestEnclosingFunction(anc);
        const callerKey = fnKey(src.file, enc);
        if (!callEdges.has(callerKey)) callEdges.set(callerKey, new Set());
        callEdges.get(callerKey).add(calleeName);
      },
    });

    // 4) getElementById occurrences (with enclosing fn + optional var binding)
    walk.ancestor(ast, {
      CallExpression(node, anc) {
        if (node.callee.type === "MemberExpression" && node.callee.property.type === "Identifier" && node.callee.property.name === "getElementById") {
          const idArg = node.arguments[0];
          if (!idArg || idArg.type !== "Literal") return;
          const enc = nearestEnclosingFunction(anc);
          const callerKey = fnKey(src.file, enc);
          const parent = anc[anc.length - 2];
          let varName = null;
          let isDirectChain = false;
          if (parent && parent.type === "VariableDeclarator" && parent.id.type === "Identifier") {
            varName = parent.id.name;
          } else if (parent && parent.type === "AssignmentExpression" && parent.left.type === "Identifier") {
            varName = parent.left.name;
          } else if (parent && parent.type === "MemberExpression" && parent.object === node) {
            // direct chain: getElementById(x).prop ...
            const grandparent = anc[anc.length - 3];
            if (grandparent && grandparent.type === "AssignmentExpression" && grandparent.left === parent) {
              isDirectChain = true;
            }
          }
          geIdAccess.push({ id: idArg.value, file: src.file, line: lineOf(node), fnKey: callerKey, varName, isDirectChain });
        }
      },
    });

    // 5) writes: AssignmentExpression where left is MemberExpression on
    //    a tracked variable, OR direct chain (already flagged above).
    walk.ancestor(ast, {
      AssignmentExpression(node, anc) {
        if (node.left.type !== "MemberExpression") return;
        const enc = nearestEnclosingFunction(anc);
        const callerKey = fnKey(src.file, enc);
        if (node.left.object.type === "Identifier") {
          if (!writesInFn.has(callerKey)) writesInFn.set(callerKey, new Set());
          writesInFn.get(callerKey).add(node.left.object.name);
        }
      },
    });
  }

  // Resolve automatic-reachable set via BFS over callEdges, seeded by
  // autoEntry (both fnKey-based and NAME::-based entries).
  // Build name -> fnKeys map for propagation through named calls.
  const nameToKeys = new Map();
  for (const [key, name] of fnName) {
    if (name) { if (!nameToKeys.has(name)) nameToKeys.set(name, []); nameToKeys.get(name).push(key); }
  }

  const automaticKeys = new Set();
  const queue = [];
  for (const e of autoEntry) {
    if (e.startsWith("NAME::")) {
      const nm = e.slice(6);
      for (const k of (nameToKeys.get(nm) || [])) { if (!automaticKeys.has(k)) { automaticKeys.add(k); queue.push(k); } }
    } else {
      if (!automaticKeys.has(e)) { automaticKeys.add(e); queue.push(e); }
    }
  }
  while (queue.length) {
    const cur = queue.shift();
    const callees = callEdges.get(cur);
    if (!callees) continue;
    for (const calleeName of callees) {
      for (const k of (nameToKeys.get(calleeName) || [])) {
        if (!automaticKeys.has(k)) { automaticKeys.add(k); queue.push(k); }
      }
    }
  }

  // Now determine, for each getElementById access, whether it's a WRITE
  // and whether its enclosing function is "automatic".
  const writeAccesses = [];
  for (const acc of geIdAccess) {
    let isWrite = acc.isDirectChain;
    if (!isWrite && acc.varName) {
      const vars = writesInFn.get(acc.fnKey);
      if (vars && vars.has(acc.varName)) isWrite = true;
    }
    if (!isWrite) continue;
    const isAutomatic = automaticKeys.has(acc.fnKey) || acc.fnKey.endsWith("@top-level");
    writeAccesses.push({ ...acc, isAutomatic });
  }

  // Group by id -> collect automatic writers (distinct fnKey+file)
  const byId = new Map();
  for (const w of writeAccesses) {
    if (!byId.has(w.id)) byId.set(w.id, []);
    byId.get(w.id).push(w);
  }

  const collisionCandidates = [];
  for (const [id, writers] of byId) {
    const autoWriters = writers.filter((w) => w.isAutomatic);
    const distinctAutoFnKeys = new Set(autoWriters.map((w) => w.fnKey));
    if (distinctAutoFnKeys.size >= 2) {
      collisionCandidates.push({
        id,
        totalWrites: writers.length,
        autoWrites: autoWriters.length,
        sites: autoWriters.map((w) => ({ file: w.file, line: w.line, fnName: fnName.get(w.fnKey) || "(anonym/top-level)" })),
      });
    }
  }
  collisionCandidates.sort((a, b) => b.autoWrites - a.autoWrites);

  // Report
  const lines = [];
  lines.push("# §2c — Verfeinerung: Automatische Schreib-Kollisionskandidaten");
  lines.push(`Erzeugt: ${new Date().toISOString()}`);
  lines.push("");
  lines.push(`Von ${byId.size} IDs mit mind. einem Schreibzugriff erfüllen `
    + `${collisionCandidates.length} das VIX-Bug-Kriterium: mindestens 2 `
    + `verschiedene AUTOMATISCH erreichbare Funktionen schreiben auf dieselbe ID `
    + `(nicht nur Klick-/Change-Handler).`);
  lines.push("");
  if (collisionCandidates.length === 0) {
    lines.push("**Keine Treffer.** Das würde bedeuten: der VIX-Bug war (nach diesem "
      + "Kriterium) ein Einzelfall, kein systemisches Muster — mit den bekannten "
      + "Einschränkungen der namensbasierten Call-Graph-Analyse im Hinterkopf.");
  }
  for (const c of collisionCandidates) {
    lines.push(`## #${c.id}`);
    lines.push(`${c.autoWrites} automatische Schreibzugriffe aus ${new Set(c.sites.map(s=>s.file)).size} Datei(en):`);
    for (const s of c.sites) lines.push(`- ${s.file}:${s.line} — Funktion: ${s.fnName}`);
    lines.push("");
  }

  fs.writeFileSync(path.join(__dirname, "report-2c.md"), lines.join("\n"));
  fs.writeFileSync(path.join(__dirname, "report-2c.json"), JSON.stringify({ collisionCandidates, totalIdsWithWrites: byId.size }, null, 2));
  console.log(`§2c: ${collisionCandidates.length} Kollisionskandidaten von ${byId.size} IDs mit Schreibzugriff. Report: report-2c.md`);
}

main();
