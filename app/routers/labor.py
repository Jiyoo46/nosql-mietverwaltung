"""Live-Experimente für die Präsentation. Jedes Ergebnis ist gemessen, nichts ist vorbereitet."""
import inspect
import time
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import docker
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import PyMongoError
from pymongo.write_concern import WriteConcern

from app import cache as cache_modul
from app.cache import cached
from app.config import settings
from app.db import db, redis
from app.routers import dashboard, mieter, system, vertraege, vorgaenge
from app.util import jsonify

router = APIRouter(prefix="/api/labor", tags=["Labor"])

KNOTEN = ("mongo1", "mongo2", "mongo3")
_docker = None


def docker_client():
    global _docker
    if _docker is None:
        _docker = docker.from_env()
    return _docker


def ms(start):
    return round((time.perf_counter() - start) * 1000, 2)


def primary():
    try:
        return next((m["host"] for m in system.replica_status() if m["state"] == "PRIMARY"), None)
    except PyMongoError:
        return None


# ---------------------------------------------------------------- 01 Replikation und Failover

@router.post("/failover")
def failover():
    """Hartes Abschalten des Primary (kill, wie ein Absturz) und Messung bis zum neuen Primary."""
    alt = primary()
    if alt is None:
        raise HTTPException(409, "Gerade gibt es keinen Primary, bitte kurz warten")
    start = time.perf_counter()
    docker_client().containers.get(alt).kill()

    neu = None
    while time.perf_counter() - start < 60:
        p = primary()
        if p and p != alt:
            neu = p
            break
        time.sleep(0.25)
    wahl_s = round(time.perf_counter() - start, 1)
    if neu is None:
        raise HTTPException(504, "Nach 60 s kein neuer Primary")

    # Die App schreibt ohne Neustart weiter: der Treiber kennt die neue Topologie
    t = time.perf_counter()
    db.labor.insert_one({"typ": "failover-test", "zeit": datetime.now(), "primary": neu})
    return {"gestoppt": alt, "neuer_primary": neu, "wahl_s": wahl_s, "schreibtest_ms": ms(t)}


@router.post("/knoten-starten")
def knoten_starten():
    gestartet = []
    for name in KNOTEN:
        c = docker_client().containers.get(name)
        if c.status != "running":
            c.start()
            gestartet.append(name)
    return {"gestartet": gestartet,
            "hinweis": "mongo1 hat die höchste Priorität und übernimmt nach dem Aufholen wieder als Primary"}


# ---------------------------------------------------------------- 02 Konsistenz

class Konsistenz(BaseModel):
    w: Literal["1", "majority"]
    lesen: Literal["primary", "secondary"]
    versuche: int = 50


@router.post("/konsistenz")
def konsistenz(body: Konsistenz):
    """Schreiben und sofort wieder lesen, mehrfach: wie oft sieht der Leser den alten Wert?"""
    schreiber = db.get_collection("labor", write_concern=WriteConcern(w=1 if body.w == "1" else "majority"))
    leser = db.get_collection("labor", read_preference=(
        ReadPreference.PRIMARY if body.lesen == "primary" else ReadPreference.SECONDARY))

    veraltet, schreibzeit, gelesen_von = 0, 0.0, set()
    for _ in range(body.versuche):
        t = time.perf_counter()
        neu = schreiber.find_one_and_update({"_id": "zaehler"}, {"$inc": {"wert": 1}}, upsert=True,
                                            return_document=ReturnDocument.AFTER)["wert"]
        schreibzeit += time.perf_counter() - t
        cursor = leser.find({"_id": "zaehler"}).limit(1)
        doc = next(cursor, None)
        gelesen_von.add(cursor.address[0] if cursor.address else "?")
        if doc is None or doc["wert"] < neu:
            veraltet += 1
    return {
        "versuche": body.versuche,
        "veraltet": veraltet,
        "schreiben_ms_schnitt": round(schreibzeit / body.versuche * 1000, 2),
        "gelesen_von": sorted(gelesen_von),
    }


# ---------------------------------------------------------------- 03 Redis-Cache

@router.post("/cache-messung")
def cache_messung():
    """Gleiche Abfrage zweimal: erst ohne Cache-Eintrag (MongoDB), dann aus Redis."""
    redis.delete("dashboard:kennzahlen")
    _, erst = cached("dashboard:kennzahlen", dashboard.TTL, dashboard.berechnen)
    _, dann = cached("dashboard:kennzahlen", dashboard.TTL, dashboard.berechnen)
    return {"erster_aufruf": erst, "zweiter_aufruf": dann}


def _offene_schaeden(daten):
    return sum(x["anzahl"] for x in daten["vorgaenge"]["offene_schaeden_nach_prioritaet"])


@router.post("/cache-veraltet")
def cache_veraltet():
    """Schaden anlegen und Dashboard neu lesen: zeigt der Cache die Änderung?"""
    vorher, _ = cached("dashboard:kennzahlen", dashboard.TTL, dashboard.berechnen)
    o = db.objekte.find_one({}, {"_id": 1}, sort=[("bezeichnung", 1)])
    vorgaenge.schaden_melden(vorgaenge.NeuerSchaden(
        objekt_id=str(o["_id"]), kategorie="Rauchmelder defekt", gewerk="Elektro", prioritaet="mittel",
        beschreibung="Angelegt im NoSQL-Labor"))
    nachher, meta = cached("dashboard:kennzahlen", dashboard.TTL, dashboard.berechnen)
    return {
        "vorher": _offene_schaeden(vorher),
        "angezeigt": _offene_schaeden(nachher),
        "tatsaechlich": db.vorgaenge.count_documents({"typ": "schaden", "status": {"$ne": "erledigt"}}),
        "quelle": meta["quelle"],
        "ttl_s": redis.ttl("dashboard:kennzahlen"),
    }


# ---------------------------------------------------------------- 04 Index

def _stufen(plan):
    """Stufen eines Ausführungsplans von außen nach innen, z. B. FETCH → IXSCAN."""
    out = []
    while plan:
        plan = plan.get("queryPlan", plan)
        out.append(plan.get("stage"))
        plan = plan.get("inputStage")
    return [s for s in out if s]


@router.get("/index")
def index_vergleich(plz: str = "70178", wohnflaeche: float = 91.0):
    filter_ = {"adresse.plz": plz, "wohnflaeche": {"$gte": wohnflaeche * 0.8, "$lte": wohnflaeche * 1.2}}

    def erklaeren(hint):
        e = db.command({"explain": {"find": "angebote", "filter": filter_, "hint": hint},
                        "verbosity": "executionStats"})
        s = e["executionStats"]
        return {
            "plan": " → ".join(_stufen(e["queryPlanner"]["winningPlan"])),
            "treffer": s["nReturned"],
            "dokumente_gelesen": s["totalDocsExamined"],
            "indexeintraege_gelesen": s["totalKeysExamined"],
            "ms": s["executionTimeMillis"],
        }

    return {
        "abfrage": jsonify(filter_),
        "ohne_index": erklaeren({"$natural": 1}),
        "mit_index": erklaeren("adresse.plz_1_wohnflaeche_1"),
        "gesamt": db.angebote.estimated_document_count(),
    }


# ---------------------------------------------------------------- 05 Transaktion

@router.post("/transaktion")
def transaktion(abbruch: bool = False):
    """Mieterwechsel in einer vermieteten Wohnung, wahlweise mit Abbruch nach Schritt 3."""
    o = db.objekte.find_one(
        {"typ": "Mehrfamilienhaus", "einheiten": {"$elemMatch": {"typ": "wohnung", "status": "vermietet"}}},
        {"bezeichnung": 1, "einheiten.$": 1}, sort=[("bezeichnung", 1)])
    einheit = o["einheiten"][0]
    alt = db.vertraege.find_one({"_id": einheit["aktueller_vertrag_id"]}, {"miete": 1, "mieter_namen": 1})
    ergebnis = vertraege.mieterwechsel(vertraege.Mieterwechsel(
        objekt_id=str(o["_id"]), einheit_id=einheit["einheit_id"],
        mieter=vertraege.NeuerMieter(anrede="Frau", vorname="Lea", nachname="Beispiel", email="lea.beispiel@example.de"),
        beginn=date.today(), kalt=round(alt["miete"]["kalt"] * 1.05, 2),
        nebenkosten_vorauszahlung=alt["miete"]["nebenkosten_vorauszahlung"], fehler_simulieren=abbruch))
    nachher = db.vertraege.find_one({"_id": alt["_id"]}, {"status": 1})
    return {"objekt": o["bezeichnung"], "einheit": einheit["einheit_id"], "bisherige_mieter": alt["mieter_namen"],
            "alter_vertrag_status_danach": nachher["status"], **ergebnis}


# ---------------------------------------------------------------- 06 Denormalisierung

@router.post("/namensaenderung")
def namensaenderung(kopien: bool = True):
    """Nachname eines Demo-Mieters wechselt reihum: Original → Ring → Ring-Original → Original.

    Drei Stufen, damit der zweite Klick (mit Kopien) nie zufällig auf dem Namen einer veralteten Kopie landet."""
    demo = db.labor.find_one({"_id": "namensdemo"})
    if demo is None:
        v = db.vertraege.find_one({"status": "aktiv", "einheit_typ": "wohnung", "mieter_ids": {"$size": 1}},
                                  sort=[("nummer", 1)])
        m = db.mieter.find_one({"_id": v["mieter_ids"][0]})
        demo = {"_id": "namensdemo", "mieter_id": m["_id"], "original": m["nachname"]}
        db.labor.insert_one(demo)
    m = db.mieter.find_one({"_id": demo["mieter_id"]})
    folge = [demo["original"], "Ring", f"Ring-{demo['original']}"]
    neu = folge[(folge.index(m["nachname"]) + 1) % 3 if m["nachname"] in folge else 1]
    ergebnis = mieter.name_aendern(str(demo["mieter_id"]), mieter.Namensaenderung(nachname=neu, kopien_aktualisieren=kopien))
    kopien_jetzt = sorted({n for v in db.vertraege.find({"mieter_ids": demo["mieter_id"]}, {"mieter_namen": 1})
                           for n in v["mieter_namen"]})
    return {**ergebnis, "kopien_in_vertraegen": kopien_jetzt}


# ---------------------------------------------------------------- 07 Schema

FELDER = {"miete.heizkosten": "Heizkosten", "energie.effizienzklasse": "Energieklasse",
          "letzte_sanierung": "Sanierungsjahr", "baujahr": "Baujahr"}


@router.get("/schema")
def schema():
    def berechnen():
        gesamt = db.angebote.estimated_document_count()
        verteilung = list(db.angebote.aggregate([
            {"$project": {"n": {"$size": {"$objectToArray": "$$ROOT"}}}},
            {"$group": {"_id": "$n", "dokumente": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
            {"$project": {"_id": 0, "felder": "$_id", "dokumente": 1}},
        ]))
        fehlt = [{"feld": name, "prozent": round((1 - db.angebote.count_documents({f: {"$exists": True}}) / gesamt) * 100)}
                 for f, name in FELDER.items()]
        return {"gesamt": gesamt, "verteilung": verteilung, "fehlt": sorted(fehlt, key=lambda x: -x["prozent"])}

    daten, meta = cached("labor:schema", 3600, berechnen)
    return {"daten": daten, "cache": meta}


# ---------------------------------------------------------------- Reset

@router.post("/reset")
def reset():
    """Alle Knoten starten, Datenbank auf den Demo-Stand setzen, Cache leeren (ca. 10 s)."""
    knoten_starten()
    for _ in range(60):
        if primary():
            break
        time.sleep(0.5)
    from scripts import reset as reset_skript
    t = time.perf_counter()
    reset_skript.main()
    db.labor.drop()
    for name in ("cache", "invalidierung"):
        redis.delete(f"einstellung:{name}")
    return {"dauer_s": round(time.perf_counter() - t, 1)}


# ---------------------------------------------------------------- Code zu den Experimenten

PROJEKT = Path(__file__).resolve().parents[2]  # /code im Container


def _ausschnitt(titel, datei, zeilen, start, hinweis, markiert):
    """Ein Code-Ausschnitt mit Fundstelle. Markiert werden Zeilen, die einen der Suchtexte enthalten."""
    rel = Path(datei).resolve().relative_to(PROJEKT).as_posix()
    zeilen = [z.rstrip("\n") for z in zeilen]
    while zeilen and not zeilen[-1].strip():
        zeilen.pop()
    # Der Rechnerpfad ist absolut und beginnt selbst mit „/“: vscode://file/Users/…/datei.py:zeile
    host = "/" + settings.host_projekt_dir.strip("/")
    vscode = f"vscode://file{host}/{rel}:{start}" if settings.host_projekt_dir else None
    return {"titel": titel, "datei": rel, "zeile": start, "code": "\n".join(zeilen), "hinweis": hinweis,
            "markiert": [i for i, z in enumerate(zeilen) if any(m in z for m in markiert)], "vscode": vscode}


def funktion(fn, titel, hinweis, markiert=()):
    """Quelltext einer Funktion, live gelesen: gezeigt wird genau der Code, der beim Klick läuft."""
    zeilen, start = inspect.getsourcelines(fn)
    return _ausschnitt(titel, inspect.getsourcefile(fn), zeilen, start, hinweis, markiert)


def bereich(datei, beginn, ende, titel, hinweis, markiert=()):
    """Zeilen ab der ersten mit `beginn` bis vor die erste folgende mit `ende` (für Code außerhalb von Funktionen)."""
    pfad = PROJEKT / datei
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    von = next(i for i, z in enumerate(zeilen) if beginn in z)
    bis = next(i for i in range(von + 1, len(zeilen)) if ende in zeilen[i])
    return _ausschnitt(titel, pfad, zeilen[von:bis], von + 1, hinweis, markiert)


def _code(nr):
    if nr == "01":
        return [
            funktion(failover, "Primary hart beenden und Wahl messen",
                     "kill() beendet den Container wie ein Absturz. Danach wird gewartet, bis ein anderer Knoten "
                     "PRIMARY meldet, und ohne Neustart der App geschrieben.",
                     ["kill()", "p != alt", "insert_one"]),
            bereich("app/db.py", "# Ein Client je Prozess", "redis = Redis", "Ein Client für alle drei Knoten",
                    "Die Verbindungs-URI nennt alle drei Knoten (docker-compose.yml). Der Treiber erkennt den neuen "
                    "Primary selbst; während der Wahl warten Anfragen bis zu 15 s.",
                    ["serverSelectionTimeoutMS"]),
            funktion(system.replica_status, "Rollen der Knoten für die Statusleiste",
                     "Gelesen mit NEAREST, damit die Leiste auch funktioniert, solange es keinen Primary gibt.",
                     ["ReadPreference.NEAREST"]),
        ]
    if nr == "02":
        return [
            funktion(cache_modul.cached, "Cache-aside",
                     "Erst Redis fragen. Bei einem Fehltreffer rechnet MongoDB, das Ergebnis wird mit TTL abgelegt.",
                     ["redis.get", "redis.set", "berechnen()"]),
            funktion(cache_modul.invalidieren, "Invalidierung bei Schreibzugriffen",
                     "Schreibende Endpunkte löschen alle passenden Keys, z. B. dashboard:*. Ohne diesen Schritt "
                     "bleibt der Cache bis zum Ablauf der TTL veraltet.",
                     ["scan_iter", "redis.delete"]),
            funktion(dashboard.dashboard, "Das Dashboard nutzt den Cache",
                     "Key dashboard:kennzahlen, TTL 300 s; berechnen() enthält die Aggregationen.",
                     ["cached("]),
        ]
    if nr == "03":
        return [
            funktion(konsistenz, "Schreiben und sofort lesen, 50-mal",
                     "Write Concern und Read Preference werden je Collection-Objekt gesetzt, also pro Abfrage wählbar. "
                     "Veraltet ist ein Lesezugriff, der einen kleineren Zählerstand sieht als gerade geschrieben.",
                     ["WriteConcern(", "ReadPreference.", "veraltet += 1"]),
        ]
    if nr == "04":
        return [
            funktion(index_vergleich, "explain() mit und ohne Index",
                     "hint {$natural: 1} erzwingt den Vollscan (COLLSCAN). Verglichen wird totalDocsExamined, "
                     "nicht die Zeit, weil die Zahl gelesener Dokumente stabil ist.",
                     ["$natural", "adresse.plz_1_wohnflaeche_1", "totalDocsExamined"]),
            bereich("scripts/import_angebote.py", "# Unterstützt den Marktvergleich", "print(f\"", "Index anlegen",
                    "Compound-Index nach der ESR-Regel: zuerst das Gleichheitsfeld (PLZ), dann der Bereich (Wohnfläche).",
                    ["create_index"]),
        ]
    if nr == "05":
        return [
            funktion(vertraege.mieterwechsel, "Mieterwechsel als Transaktion",
                     "Alle fünf Schreibvorgänge erhalten dieselbe session. with_transaction committet nur, wenn "
                     "schritte() ohne Fehler durchläuft; der simulierte Fehler nach Schritt 3 löst den Rollback aus.",
                     ["with_transaction", "raise SimulierterFehler", "array_filters", "session=session)"]),
        ]
    if nr == "06":
        return [
            funktion(mieter.name_aendern, "Namensänderung mit und ohne Pflege der Kopien",
                     "Der Name steht im Mieter und als Kopie in vertraege.mieter_namen. Die Update-Pipeline setzt "
                     "den neuen Namen genau an der Position, an der die Mieter-ID steht.",
                     ["kopien_aktualisieren", "$map", "with_transaction"]),
        ]
    if nr == "07":
        return [
            bereich("scripts/generate.py", "VALIDATOREN = {", "def collections_anlegen", "$jsonSchema-Validatoren",
                    "Pflichtfelder, erlaubte Werte und Typen. Für Mieter verlangt oneOf je nach Art Namen oder "
                    "Firmennamen. Zusätzliche Felder bleiben erlaubt.",
                    ["\"required\"", "oneOf"]),
            funktion(vorgaenge.roh_einfuegen, "Einfügen ohne Prüfung in der App",
                     "Die App reicht das Dokument unverändert weiter. Die Ablehnung kommt von MongoDB selbst (Code 121).",
                     ["insert_one", "WriteError"]),
            funktion(schema, "Feldanzahl je Inserat",
                     "$objectToArray macht aus dem Dokument eine Liste seiner Felder, $size zählt sie.",
                     ["$objectToArray", "$exists"]),
        ]
    raise HTTPException(404, "Experiment unbekannt")


@router.get("/code/{nr}")
def code(nr: str):
    """Quelltext hinter einem Experiment, live aus den Dateien gelesen."""
    return {"nr": nr, "ausschnitte": _code(nr)}
