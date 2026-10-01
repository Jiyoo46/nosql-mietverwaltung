// Gemeinsame Helfer für alle Seiten: Statusleiste, Datenbank-Spur, Tabellenzeilen, Dialog "Schaden melden"

const api = async (methode, url, body) => {
  const antwort = await fetch(url, {
    method: methode,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const daten = await antwort.json().catch(() => ({}));
  if (!antwort.ok) {
    const detail = daten.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? daten));
  }
  return daten;
};
window.api = api;

const zahl = (x, dez = 0) => Number(x).toLocaleString("de-DE", { minimumFractionDigits: dez, maximumFractionDigits: dez });
window.zahl = zahl;

// ---------- Statusleiste: Rollen der Replica-Set-Knoten und Redis, alle 2 s ----------
const KURZ = { PRIMARY: "P", SECONDARY: "S" };
window.statusHoerer = [];

async function statusAbfragen() {
  try {
    const s = await api("GET", "/health");
    for (const m of s.members) {
      const el = document.querySelector(`[data-knoten="${m.host}"]`);
      if (!el) continue;
      const ok = m.health === 1 && (m.state === "PRIMARY" || m.state === "SECONDARY");
      el.querySelector(".punkt").className = "punkt" + (ok ? "" : m.state === "UNBEKANNT" ? " unklar" : " aus");
      el.querySelector(".rolle").textContent = KURZ[m.state] ?? "–";
    }
    const r = document.querySelector("[data-redis] .punkt");
    if (r) r.className = "punkt " + (s.redis ? "redis" : "aus");
    window.statusHoerer.forEach((f) => f(s));
  } catch (e) {
    document.querySelectorAll("[data-knoten] .punkt").forEach((p) => (p.className = "punkt unklar"));
  }
}
statusAbfragen();
setInterval(statusAbfragen, 2000);

// ---------- Datenbank-Spur ein-/ausblenden (Präsentationsmodus) ----------
const spurKnopf = document.querySelector("[data-spur-schalter]");
const spurSetzen = (an) => {
  document.body.classList.toggle("ohne-spur", !an);
  spurKnopf?.setAttribute("aria-pressed", String(an));
};
try { spurSetzen(localStorage.getItem("spur") !== "aus"); } catch { spurSetzen(true); }
spurKnopf?.addEventListener("click", () => {
  const an = document.body.classList.contains("ohne-spur");
  spurSetzen(an);
  try { localStorage.setItem("spur", an ? "an" : "aus"); } catch {}
});

// ---------- Ganze Tabellenzeile anklickbar (der Link in der Zeile bleibt für Tastatur) ----------
document.querySelectorAll("tr[data-link]").forEach((tr) => {
  tr.addEventListener("click", (e) => {
    if (e.target.closest("a, button")) return;
    location.href = tr.dataset.link;
  });
});

// ---------- Übersicht: Cache verwerfen und neu berechnen ----------
document.querySelector("[data-neu-berechnen]")?.addEventListener("click", async (e) => {
  e.target.disabled = true;
  await api("DELETE", "/api/cache");
  location.reload();
});

// ---------- Dialog "Schaden melden" ----------
document.querySelectorAll("[data-dialog-oeffnen]").forEach((b) =>
  b.addEventListener("click", () => document.getElementById(b.dataset.dialogOeffnen).showModal()));
document.querySelectorAll("[data-dialog-schliessen]").forEach((b) =>
  b.addEventListener("click", () => b.closest("dialog").close()));

const schadenForm = document.querySelector("[data-schaden-form]");
schadenForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(schadenForm);
  const kategorie = schadenForm.querySelector("#s-kategorie");
  const fehler = schadenForm.querySelector("[data-fehler]");
  try {
    await api("POST", "/api/vorgaenge/schaden", {
      objekt_id: schadenForm.dataset.objekt,
      einheit_id: f.get("einheit_id") || null,
      kategorie: f.get("kategorie"),
      gewerk: kategorie.selectedOptions[0].dataset.gewerk,
      prioritaet: f.get("prioritaet"),
      beschreibung: f.get("beschreibung") || null,
    });
    location.reload();
  } catch (err) {
    fehler.hidden = false;
    fehler.textContent = "Nicht gespeichert: " + err.message;
  }
});
