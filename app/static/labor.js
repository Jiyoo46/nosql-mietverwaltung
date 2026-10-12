// NoSQL-Labor: jede Schaltfläche löst ein echtes Experiment über /api/labor aus

const $ = (s, el = document) => el.querySelector(s);
const zeigen = (el, text, warn = false) => {
  el.hidden = false;
  el.textContent = text;
  el.classList.toggle("warn", warn);
};

// Knopf sperren, solange das Experiment läuft; Fehler sichtbar machen statt verschlucken
async function ausfuehren(knopf, arbeit, fehlerZiel) {
  knopf.disabled = true;
  try {
    await arbeit();
  } catch (e) {
    if (fehlerZiel) zeigen(fehlerZiel, "Fehler: " + e.message, true);
  } finally {
    knopf.disabled = false;
  }
}

// ---------- 01 Replikation und Failover ----------
const protokoll = $('[data-protokoll="failover"]');
const zeit = () => new Date().toLocaleTimeString("de-DE");
const log = (zeile, neu = false) => {
  protokoll.textContent = (neu ? "" : protokoll.textContent + "\n") + `${zeit()}  ${zeile}`;
};

window.statusHoerer.push((s) => {
  for (const m of s.members) {
    const karte = $(`[data-karte="${m.host}"]`);
    if (!karte) continue;
    const ok = m.health === 1 && (m.state === "PRIMARY" || m.state === "SECONDARY");
    karte.className = m.state === "PRIMARY" ? "primary" : ok ? "" : "weg";
    karte.querySelector(".punkt").className = "punkt" + (ok ? "" : " aus");
    karte.querySelector(".rolle").textContent = ok ? m.state : "NICHT ERREICHBAR";
    karte.querySelector("[data-text]").textContent =
      m.state === "PRIMARY" ? "nimmt alle Schreibzugriffe an" : ok ? "repliziert vom Primary" : m.state.toLowerCase();
  }
});

$("[data-failover]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    log("Primary wird hart beendet …", true);
    const start = Date.now();
    const uhr = setInterval(() => {
      const zeilen = protokoll.textContent.split("\n");
      zeilen[zeilen.length - 1] = `${zeit()}  Warte auf Wahl … ${((Date.now() - start) / 1000).toFixed(0)} s`;
      protokoll.textContent = zeilen.join("\n");
    }, 500);
    log("Warte auf Wahl …");
    const warteZeileEntfernen = () => {
      clearInterval(uhr);
      protokoll.textContent = protokoll.textContent.split("\n").slice(0, -1).join("\n");
    };
    try {
      const r = await api("POST", "/api/labor/failover");
      warteZeileEntfernen();
      log(`${r.gestoppt} abgeschaltet, ${r.neuer_primary} ist neuer Primary nach ${zahl(r.wahl_s, 1)} s`);
      log(`Schreibtest über den Treiber: erfolgreich in ${zahl(r.schreibtest_ms, 1)} ms, ohne Neustart der App`);
    } catch (err) {
      warteZeileEntfernen();
      log("Fehler: " + err.message);
    }
  }));

$("[data-knoten-starten]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const r = await api("POST", "/api/labor/knoten-starten");
    log(r.gestartet.length ? `${r.gestartet.join(", ")} gestartet. ${r.hinweis}.` : "Alle Knoten laufen bereits.");
  }));

// ---------- 02 Redis-Cache ----------
async function cacheStatus() {
  const s = await api("GET", "/api/cache");
  document.querySelectorAll("[data-schalter]").forEach((c) => (c.checked = s[c.dataset.schalter]));
  $("[data-cache-keys]").innerHTML = "";
  for (const k of s.keys) {
    const zeile = document.createElement("div");
    zeile.style.cssText = "display:flex;justify-content:space-between;gap:12px";
    zeile.innerHTML = `<span></span><span>TTL ${zahl(k.ttl_s)} s</span>`;
    zeile.firstChild.textContent = k.key;
    $("[data-cache-keys]").append(zeile);
  }
  if (!s.keys.length) $("[data-cache-keys]").textContent = "Keine Keys im Cache.";
}
cacheStatus();
setInterval(cacheStatus, 5000);

document.querySelectorAll("[data-schalter]").forEach((c) =>
  c.addEventListener("change", async () => {
    await api("PUT", "/api/cache/schalter", { name: c.dataset.schalter, an: c.checked });
    cacheStatus();
  }));

$("[data-cache-messen]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const r = await api("POST", "/api/labor/cache-messung");
    const a = r.erster_aufruf, b = r.zweiter_aufruf;
    const max = Math.max(a.dauer_ms, b.dauer_ms);
    $("[data-cache-ergebnis]").hidden = false;
    $('[data-balken="1"]').style.width = (a.dauer_ms / max) * 100 + "%";
    $('[data-balken="2"]').style.width = (b.dauer_ms / max) * 100 + "%";
    $('[data-wert="1"]').textContent = `${zahl(a.dauer_ms, 1)} ms`;
    $('[data-wert="2"]').textContent = `${zahl(b.dauer_ms, 1)} ms`;
    $('[data-balken="2"]').style.background = b.quelle === "redis" ? "var(--redis)" : "var(--petrol)";
    zeigen($("[data-cache-text]"), b.quelle === "redis"
      ? `1. Aufruf aus MongoDB, 2. aus Redis: rund ${zahl(a.dauer_ms / b.dauer_ms)}-mal schneller.`
      : "Cache ist aus: beide Aufrufe rechnen in MongoDB.");
    cacheStatus();
  }, $("[data-cache-text]")));

$("[data-cache-veraltet]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const r = await api("POST", "/api/labor/cache-veraltet");
    const veraltet = r.angezeigt !== r.tatsaechlich;
    zeigen($("[data-cache-text]"), veraltet
      ? `Veraltet: Dashboard zeigt ${r.angezeigt} offene Schäden aus Redis, tatsächlich sind es ${r.tatsaechlich}. Korrektur erst in ${r.ttl_s} s (TTL).`
      : `Aktuell: ${r.vorher} → ${r.angezeigt} offene Schäden, neu berechnet aus ${r.quelle === "redis" ? "Redis" : "MongoDB"}.`, veraltet);
    cacheStatus();
  }, $("[data-cache-text]")));

// ---------- 03 Konsistenz ----------
const auswahl = { w: "majority", lesen: "primary" };
document.querySelectorAll("[data-gruppe]").forEach((gruppe) =>
  gruppe.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      auswahl[gruppe.dataset.gruppe] = b.dataset.wert;
      gruppe.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    })));

$("[data-konsistenz]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const r = await api("POST", "/api/labor/konsistenz", { ...auswahl, versuche: 50 });
    const quelle = r.gelesen_von.join(", ");
    zeigen($("[data-konsistenz-text]"),
      `${r.veraltet} von ${r.versuche} Lesezugriffen sahen den alten Wert. Gelesen von ${quelle}, ` +
      `Schreiben im Schnitt ${zahl(r.schreiben_ms_schnitt, 2)} ms (w: ${auswahl.w}).`, r.veraltet > 0);
  }, $("[data-konsistenz-text]")));

// ---------- 04 Index ----------
$("[data-index]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const r = await api("GET", "/api/labor/index");
    $("[data-index-ergebnis]").hidden = false;
    for (const [art, p] of [["ohne", r.ohne_index], ["mit", r.mit_index]]) {
      $(`[data-plan="${art}"]`).textContent = p.plan;
      $(`[data-docs="${art}"]`).textContent = zahl(p.dokumente_gelesen);
      $(`[data-info="${art}"]`).textContent =
        `Dokumente gelesen · ${p.ms < 1 ? "unter 1" : zahl(p.ms)} ms · ${zahl(p.indexeintraege_gelesen)} Indexeinträge`;
    }
    $("[data-index-fuss]").textContent =
      `${r.mit_index.treffer} Treffer in beiden Fällen · ${zahl(r.gesamt)} Dokumente in angebote · Index adresse.plz_1_wohnflaeche_1`;
  }));

// ---------- 05 Transaktion ----------
const schritte = [...document.querySelectorAll("[data-schritte] li")];
$("[data-transaktion]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const abbruch = $("[data-abbruch]").checked;
    schritte.forEach((li) => (li.className = ""));
    const r = await api("POST", `/api/labor/transaktion?abbruch=${abbruch}`);
    const mieter = r.bisherige_mieter.join(" und ");
    if (abbruch) {
      schritte.forEach((li, i) => (li.className = i < 3 ? "zurueck" : "nie"));
      zeigen($("[data-transaktion-text]"),
        `${r.objekt}, ${r.einheit}: abgebrochen nach Schritt 3, Rollback. Neuer Mieter existiert: ${r.mieter_existiert ? "ja" : "nein"}, ` +
        `neuer Vertrag existiert: ${r.vertrag_existiert ? "ja" : "nein"}, alter Vertrag von ${mieter} ist weiter ${r.alter_vertrag_status_danach}.`, true);
    } else {
      schritte.forEach((li) => (li.className = "ok"));
      zeigen($("[data-transaktion-text]"),
        `${r.objekt}, ${r.einheit}: alle fünf Schritte gemeinsam gespeichert. Vertrag von ${mieter} ist jetzt ${r.alter_vertrag_status_danach}, Lea Beispiel ist neue Mieterin.`);
    }
  }, $("[data-transaktion-text]")));

// ---------- 06 Doppelt gespeicherte Namen ----------
$("[data-namensaenderung]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    const kopien = $("[data-kopien]").checked;
    const r = await api("POST", `/api/labor/namensaenderung?kopien=${kopien}`);
    $("[data-namen]").hidden = false;
    $("[data-name-mieter]").textContent = r.neu;
    $("[data-name-kopie]").textContent = r.kopien_in_vertraegen.join(", ");
    const veraltet = r.vertraege_mit_veraltetem_namen > 0;
    $("[data-kopie-box]").className = veraltet ? "alt" : "";
    zeigen($("[data-namen-text]"), veraltet
      ? `${r.alt} → ${r.neu}, aber ${r.vertraege_mit_veraltetem_namen === 1 ? "1 Vertrag zeigt" : r.vertraege_mit_veraltetem_namen + " Verträge zeigen"} noch einen alten Namen: die Kopie wurde nicht gepflegt.`
      : `${r.alt} → ${r.neu}. ${r.vertraege_aktualisiert === 1 ? "1 Vertrag" : r.vertraege_aktualisiert + " Verträge"} in derselben Transaktion mitgeändert, alle Kopien stimmen.`, veraltet);
  }, $("[data-namen-text]")));

// ---------- 07 Schema ----------
(async () => {
  const { daten } = await api("GET", "/api/labor/schema");
  const max = Math.max(...daten.verteilung.map((v) => v.dokumente));
  for (const v of daten.verteilung) {
    const saeule = document.createElement("div");
    saeule.style.height = Math.max(2, (v.dokumente / max) * 100) + "%";
    saeule.title = `${v.felder} Felder: ${zahl(v.dokumente)} Dokumente`;
    $("[data-histo]").append(saeule);
  }
  $("[data-histo-min]").textContent = `${daten.verteilung[0].felder} Felder`;
  $("[data-histo-max]").textContent = `${daten.verteilung.at(-1).felder} Felder`;
  $("[data-schema-lead]").textContent = `${zahl(daten.gesamt)} echte Inserate: jedes speichert nur, was der Anbieter angegeben hat.`;
  $("[data-fehlt]").textContent = "Fehlt bei: " + daten.fehlt.map((f) => `${f.feld} ${f.prozent} %`).join(" · ");
})();

$("[data-einfuegen]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    let doc;
    try {
      doc = JSON.parse($("[data-roh]").value);
    } catch {
      zeigen($("[data-roh-text]"), "Kein gültiges JSON.", true);
      return;
    }
    try {
      const r = await api("POST", "/api/vorgaenge/roh", doc);
      zeigen($("[data-roh-text]"), `Angenommen, _id ${r._id}. Das Dokument erfüllt die Regeln.`);
    } catch (err) {
      const regeln = [];
      const info = JSON.parse(err.message).details?.details?.schemaRulesNotSatisfied ?? [];
      for (const r of info) {
        if (r.missingProperties) regeln.push("fehlt: " + r.missingProperties.join(", "));
        for (const p of r.propertiesNotSatisfied ?? []) regeln.push(`${p.propertyName}: ${p.details.map((d) => d.reason || d.operatorName).join(", ")}`);
      }
      zeigen($("[data-roh-text]"), "Von MongoDB abgelehnt (Code 121). " + (regeln.join(" · ") || err.message), true);
    }
  }));

// ---------- Demo-Stand ----------
$("[data-reset]").addEventListener("click", (e) =>
  ausfuehren(e.target, async () => {
    $("[data-reset-info]").textContent = "läuft, ca. 10 s …";
    const r = await api("POST", "/api/labor/reset");
    $("[data-reset-info]").textContent = `wiederhergestellt in ${zahl(r.dauer_s, 1)} s`;
    cacheStatus();
  }));

// ---------- Code hinter den Experimenten ----------
// Kleine Python-Hervorhebung ohne Bibliothek (die App läuft offline): Schlüsselwörter, Strings, Kommentare, Dekoratoren
const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const TOKEN = /("""[\s\S]*?(?:"""|$)|f?"(?:\\.|[^"\\])*"|f?'(?:\\.|[^'\\])*'|#.*$|@\w+(?:\.\w+)*|\b(?:def|return|if|elif|else|for|while|in|not|and|or|is|None|True|False|try|except|finally|raise|with|as|import|from|class|lambda|pass|break|continue|global)\b)/g;

function zeileFaerben(text, zustand) {
  let html = "";
  if (zustand.docstring) {
    const ende = text.indexOf('"""');
    if (ende < 0) return `<span class="s">${esc(text)}</span>`;
    html = `<span class="s">${esc(text.slice(0, ende + 3))}</span>`;
    text = text.slice(ende + 3);
    zustand.docstring = false;
  }
  let pos = 0;
  for (const m of text.matchAll(TOKEN)) {
    const t = m[0];
    html += esc(text.slice(pos, m.index));
    const art = t.startsWith("#") ? "c" : t.startsWith("@") ? "d" : /^f?["']/.test(t) ? "s" : "k";
    html += `<span class="${art}">${esc(t)}</span>`;
    if (t.startsWith('"""') && (t.length < 6 || !t.endsWith('"""'))) zustand.docstring = true;
    pos = m.index + t.length;
  }
  return html + esc(text.slice(pos));
}

function ausschnittZeigen(a) {
  const box = document.createElement("div");
  box.className = "ausschnitt";
  const kopf = document.createElement("div");
  kopf.className = "ausschnitt-kopf";
  const titel = document.createElement("b");
  titel.textContent = a.titel;
  const fundstelle = document.createElement("span");
  fundstelle.className = "fundstelle";
  fundstelle.textContent = `${a.datei}:${a.zeile}`;
  const links = document.createElement("span");
  links.style.cssText = "display:flex;gap:12px;align-items:baseline;flex-wrap:wrap";
  links.append(fundstelle);
  if (a.vscode) {
    const vs = document.createElement("a");
    vs.href = a.vscode;
    vs.textContent = "in VS Code öffnen";
    links.append(vs);
  }
  kopf.append(titel, links);
  const hinweis = document.createElement("p");
  hinweis.textContent = a.hinweis;
  const pre = document.createElement("pre");
  pre.className = "quelltext";
  pre.style.setProperty("--start", a.zeile - 1);
  const zustand = { docstring: false };
  const markiert = new Set(a.markiert);
  pre.innerHTML = a.code.split("\n")
    .map((z, i) => `<span class="z${markiert.has(i) ? " mark" : ""}">${zeileFaerben(z, zustand) || " "}</span>`)
    .join("");
  box.append(kopf, hinweis, pre);
  return box;
}

document.querySelectorAll("details[data-code]").forEach((d) =>
  d.addEventListener("toggle", async () => {
    const summary = d.querySelector("summary");
    summary.textContent = d.open ? "Code ausblenden" : "Code zeigen";
    d.closest(".karte").classList.toggle("code-offen", d.open);
    if (!d.open) return;
    // Code nach oben holen: Er soll beim Aufklappen sofort lesbar sein
    const hochholen = () => summary.scrollIntoView({ block: "start", behavior: "smooth" });
    if (d.dataset.geladen) return hochholen();
    const ziel = d.querySelector("[data-code-inhalt]");
    try {
      const r = await api("GET", `/api/labor/code/${d.dataset.code}`);
      ziel.textContent = "";
      r.ausschnitte.forEach((a) => ziel.append(ausschnittZeigen(a)));
      d.dataset.geladen = "1";
      hochholen();
    } catch (e) {
      ziel.textContent = "Code konnte nicht geladen werden: " + e.message;
    }
  }));
