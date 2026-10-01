"""Live-Experimente für die Präsentation. Jedes Ergebnis ist gemessen, nichts ist vorbereitet."""
import time
from datetime import date, datetime
from typing import Literal

import docker
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import PyMongoError
from pymongo.write_concern import WriteConcern

from app.cache import cached
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
