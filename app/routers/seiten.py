"""HTML-Seiten. Sie nutzen dieselben Funktionen wie die JSON-API, damit Oberfläche und API nie auseinanderlaufen."""
import math
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import db
from app.format import FILTER, datum, euro, zahl
from app.routers import dashboard, markt, objekte, vertraege, vorgaenge
from app.util import oid

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")
templates.env.filters.update(FILTER)

EINHEIT_TYP = {"wohnung": "Wohnung", "gewerbe": "Gewerbe", "garage": "Garage", "stellplatz": "Stellplatz"}
VORGANG_TYP = {"schaden": "Schaden", "mietminderung": "Mietminderung", "korrespondenz": "Korrespondenz",
               "mieterhoehung": "Mieterhöhung", "kuendigung": "Kündigung", "frist": "Frist"}
STATUS = {"offen": "offen", "in_bearbeitung": "in Bearbeitung", "erledigt": "erledigt"}
VERTRAGSART = {"unbefristet": "unbefristet", "staffel": "Staffelmiete", "index": "Indexmiete"}
OBJEKT_TYPEN = ["Eigentumswohnung", "Mehrfamilienhaus", "Wohn- und Geschäftshaus"]
KATEGORIEN = [("Wasserschaden", "Sanitär"), ("Heizungsausfall", "Heizungsbau"), ("Schimmelbefall", "Maler/Gutachter"),
              ("Rauchmelder defekt", "Elektro"), ("Fenster undicht", "Fensterbau"), ("Abfluss verstopft", "Sanitär"),
              ("Aufzug ausgefallen", "Aufzugsservice")]
templates.env.globals.update(EINHEIT_TYP=EINHEIT_TYP, VORGANG_TYP=VORGANG_TYP, STATUS=STATUS, VERTRAGSART=VERTRAGSART)


def seite(request, name, aktiv, **kontext):
    return templates.TemplateResponse(request, name, {"aktiv": aktiv, **kontext})


# ---------------------------------------------------------------- Darstellungshilfen

def zimmer(z):
    return zahl(z, 0 if float(z).is_integer() else 1)


def ortsangabe(adresse):
    if adresse.get("stadtteil"):
        return f"{adresse['ort']}-{adresse['stadtteil']}"
    return adresse["ort"]


def mieter_text(namen):
    """['Giovanna Benthin', 'Kirsten Benthin'] → 'Giovanna und Kirsten Benthin'."""
    if len(namen) == 2:
        a, b = namen
        if a.split()[-1] == b.split()[-1]:
            return f"{a.rsplit(' ', 1)[0]} und {b}"
        return f"{a} und {b}"
    return ", ".join(namen)


def merkmale(e):
    if e["typ"] == "wohnung":
        return f"{e.get('lage', '')} · {zahl(e['wohnflaeche'])} m² · {zimmer(e['zimmer'])} Zi."
    if e["typ"] == "gewerbe":
        ust = "mit" if e.get("umsatzsteuer_option") else "ohne"
        return f"{e['nutzung']} · {zahl(e['nutzflaeche'])} m² Nutzfläche · {ust} USt-Option"
    if e["typ"] == "garage":
        return "mit E-Ladestation" if e.get("e_ladestation") else "ohne Ladestation"
    return "überdacht" if e.get("ueberdacht") else "nicht überdacht"


def vorgang_detail(v):
    """Polymorphe Vorgänge: jeder Typ hat andere Felder und damit eine andere Kurzinfo."""
    teile = [v["einheit_id"]] if v.get("einheit_id") else []
    t = v["typ"]
    if t == "schaden":
        teile += [f"Priorität {v['prioritaet']}", v["gewerk"]]
    elif t == "mietminderung":
        teile.append(f"{v['quote_prozent']} %")
    elif t == "kuendigung":
        teile.append(f"zum {datum(v['zum'])}")
    elif t == "korrespondenz":
        teile.append(v["kanal"])
    elif t == "mieterhoehung":
        teile.append(f"{euro(v['alt'])} → {euro(v['neu'])}")
    elif t == "frist":
        teile.append(f"fällig {datum(v['faellig_am'])}")
    return " · ".join(teile)


def vorgang_datum(v):
    if v["typ"] == "mietminderung" and v.get("zeitraum"):
        return f"ab {datum(v['zeitraum']['von'])}"
    return datum(v["datum"])


def vorgang_warnung(v):
    return v["typ"] in ("schaden", "mietminderung")


templates.env.globals.update(zimmer=zimmer, ortsangabe=ortsangabe, mieter_text=mieter_text, merkmale=merkmale,
                             vorgang_detail=vorgang_detail, vorgang_datum=vorgang_datum, vorgang_warnung=vorgang_warnung)


def kreis_kurz(k):
    return k.replace(" (Kreis)", "")


# ---------------------------------------------------------------- Übersicht

@router.get("/", response_class=HTMLResponse)
def uebersicht(request: Request):
    antwort = dashboard.dashboard()
    d, cache = antwort["daten"], antwort["cache"]

    typen = {}
    for e in d["einheiten"]:
        t = typen.setdefault(e["typ"], {"gesamt": 0, "vermietet": 0})
        t["gesamt"] += e["anzahl"]
        if e["status"] == "vermietet":
            t["vermietet"] += e["anzahl"]
    einheiten = [{"name": n, **typen.get(k, {"gesamt": 0, "vermietet": 0})}
                 for k, n in [("wohnung", "Wohnungen"), ("garage", "Garagen"), ("stellplatz", "Stellplätze"), ("gewerbe", "Gewerbe")]]
    wohnungen = typen.get("wohnung", {"gesamt": 0, "vermietet": 0})

    skala = math.ceil(max(max(k["bestand_median_qm"], k["markt_median_qm"]) for k in d["bestand_vs_markt"]) / 5) * 5
    kreise = [{**k, "kreis": kreis_kurz(k["kreis"]),
               "breite_bestand": k["bestand_median_qm"] / skala * 100,
               "breite_markt": k["markt_median_qm"] / skala * 100} for k in d["bestand_vs_markt"]]

    # Die zwölf vollständigen Monate vor dem aktuellen
    heute = date.today()
    schluessel = []
    for i in range(12, 0, -1):
        j, m = divmod(heute.year * 12 + heute.month - 1 - i, 12)
        schluessel.append(f"{j}-{m + 1:02d}")
    werte = {x["_id"]: x for x in d["vorgaenge"]["schaeden_je_monat"]}
    monate = [{"schluessel": s, "anzahl": werte.get(s, {}).get("anzahl", 0), "kosten": werte.get(s, {}).get("kosten", 0)}
              for s in schluessel]
    spitze = max(m["anzahl"] for m in monate) or 1
    for m in monate:
        m["hoehe"] = m["anzahl"] / spitze * 100

    prio = {x["_id"]: x["anzahl"] for x in d["vorgaenge"]["offene_schaeden_nach_prioritaet"]}
    offen = [x for x in d["vorgaenge"]["offen_nach_typ"] if x["_id"] != "frist"]
    fristen = d["vorgaenge"]["fristen_120_tage"]

    return seite(request, "uebersicht.html", "uebersicht", d=d, cache=cache, kreise=kreise, einheiten=einheiten,
                 wohnungen=wohnungen, monate=monate, prio=prio, offen=offen, frist=fristen[0] if fristen else None,
                 einheiten_gesamt=sum(t["gesamt"] for t in typen.values()),
                 schaeden_summe=sum(m["anzahl"] for m in monate), kosten_summe=sum(m["kosten"] for m in monate))


# ---------------------------------------------------------------- Objekte

@router.get("/objekte", response_class=HTMLResponse)
def objektliste(request: Request, q: str | None = None, kreis: str | None = None, typ: str | None = None, seite_nr: int = 1):
    pro_seite = 25
    daten = objekte.objekte(kreis=kreis or None, typ=typ or None, q=q or None, seite=seite_nr, pro_seite=pro_seite)
    basis = {"adresse.kreis": kreis} if kreis else {}
    typ_anzahl = {r["_id"]: r["n"] for r in db.objekte.aggregate([{"$match": basis}, {"$group": {"_id": "$typ", "n": {"$sum": 1}}}])}
    return seite(request, "objekte.html", "objekte", daten=daten, q=q or "", kreis=kreis or "", typ=typ or "",
                 kreise=sorted(db.objekte.distinct("adresse.kreis")), typen=OBJEKT_TYPEN, typ_anzahl=typ_anzahl,
                 seiten=math.ceil(daten["gesamt"] / pro_seite), seite_nr=seite_nr)


@router.get("/objekte/{objekt_id}", response_class=HTMLResponse)
def objektseite(request: Request, objekt_id: str, einheit: str | None = None, ansicht: str = "ansicht"):
    o = objekte.objekt(objekt_id)
    if einheit is None:
        einheit = next((e["einheit_id"] for e in o["einheiten"] if e["typ"] == "wohnung" and e.get("vertrag")),
                       o["einheiten"][0]["einheit_id"])
    gewaehlt = next((e for e in o["einheiten"] if e["einheit_id"] == einheit), None)
    if gewaehlt is None:
        raise HTTPException(404, "Einheit nicht gefunden")

    vertrag = vertrag_roh = markt_vgl = None
    if gewaehlt.get("vertrag"):
        vertrag = vertraege.vertrag(gewaehlt["vertrag"]["_id"])
        vertrag_roh = db.vertraege.find_one({"_id": oid(vertrag["_id"])})
        if gewaehlt["typ"] == "wohnung":
            markt_vgl = vertraege.marktvergleich(vertrag["_id"])
    historie = [v for v in objekte.objekt_vertraege(objekt_id, einheit) if not vertrag or v["_id"] != vertrag["_id"]]

    vorgaenge_offen = [v for v in o["offene_vorgaenge"] if v["typ"] != "frist"]
    fristen = sorted([v for v in o["offene_vorgaenge"] if v["typ"] == "frist"], key=lambda v: v["faellig_am"])
    return seite(request, "objekt.html", "objekte", o=o, gewaehlt=gewaehlt, vertrag=vertrag, vertrag_roh=vertrag_roh,
                 markt=markt_vgl, historie=historie, ansicht=ansicht, vorgaenge=vorgaenge_offen, fristen=fristen,
                 kategorien=KATEGORIEN)


# ---------------------------------------------------------------- Vorgänge

@router.get("/vorgaenge", response_class=HTMLResponse)
def vorgangsliste(request: Request, typ: str | None = None, status: str = "offen", seite_nr: int = 1):
    pro_seite = 30
    filter_status = None if status in ("offen", "alle") else status
    # Ohne Typfilter bleiben die wiederkehrenden Fristen außen vor, sie hätten sonst die Liste überdeckt
    daten = vorgaenge.vorgaenge(typ=typ or None, status=filter_status, offen=(status == "offen"),
                                ohne_fristen=True, seite=seite_nr, pro_seite=pro_seite)
    anzahl = {r["_id"]: r["n"] for r in db.vorgaenge.aggregate([
        {"$match": {"status": {"$ne": "erledigt"}} if status == "offen" else {}},
        {"$group": {"_id": "$typ", "n": {"$sum": 1}}}])}
    return seite(request, "vorgaenge.html", "vorgaenge", daten=daten, typ=typ or "", status=status, anzahl=anzahl,
                 seiten=math.ceil(daten["gesamt"] / pro_seite), seite_nr=seite_nr)


# ---------------------------------------------------------------- Markt

AUSSTATTUNG = ["balkon", "einbaukueche", "keller", "aufzug", "garten"]


@router.get("/markt", response_class=HTMLResponse)
def marktseite(request: Request, kreis: str = "Stuttgart", ausstattung: list[str] = Query(default=[]), seite_nr: int = 1):
    pro_seite = 20
    kreise = markt.markt_kreise()
    filter_ = {"adresse.kreis": kreis}
    if ausstattung:
        filter_["ausstattung"] = {"$all": ausstattung}
    gesamt = db.angebote.count_documents(filter_)
    docs = list(db.angebote.find(filter_).sort([("erfasst", -1), ("_id", 1)]).skip((seite_nr - 1) * pro_seite).limit(pro_seite))
    return seite(request, "markt.html", "markt", kreise=kreise, kreis=kreis, ausstattung=ausstattung or [],
                 alle_ausstattung=AUSSTATTUNG, gesamt=gesamt, docs=docs, seiten=math.ceil(gesamt / pro_seite),
                 seite_nr=seite_nr, filter_=filter_)


# ---------------------------------------------------------------- Labor

@router.get("/labor", response_class=HTMLResponse)
def laborseite(request: Request):
    return seite(request, "labor.html", "labor")
