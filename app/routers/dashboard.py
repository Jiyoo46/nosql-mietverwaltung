"""Kennzahlen über den gesamten Bestand: mehrere Aggregationen über vier Collections, per Redis gecacht."""
from datetime import datetime, timedelta

from fastapi import APIRouter

from app.cache import cached
from app.db import db

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

TTL = 300  # 5 Minuten; Schreibzugriffe invalidieren zusätzlich sofort


def _einheiten():
    return list(db.objekte.aggregate([
        {"$unwind": "$einheiten"},
        {"$group": {"_id": {"typ": "$einheiten.typ", "status": "$einheiten.status"}, "anzahl": {"$sum": 1}}},
        {"$project": {"_id": 0, "typ": "$_id.typ", "status": "$_id.status", "anzahl": 1}},
        {"$sort": {"typ": 1, "status": 1}},
    ]))


def _mieten():
    r = list(db.vertraege.aggregate([
        {"$match": {"status": {"$in": ["aktiv", "gekuendigt"]}}},
        {"$group": {
            "_id": None,
            "vertraege": {"$sum": 1},
            "kalt": {"$sum": "$miete.kalt"},
            "nebenkosten": {"$sum": "$miete.nebenkosten_vorauszahlung"},
            "gekuendigt": {"$sum": {"$cond": [{"$eq": ["$status", "gekuendigt"]}, 1, 0]}},
        }},
        {"$project": {"_id": 0, "vertraege": 1, "gekuendigt": 1,
                      "kalt": {"$round": ["$kalt", 2]}, "nebenkosten": {"$round": ["$nebenkosten", 2]}}},
    ]))
    return r[0] if r else {}


def _vorgaenge():
    heute = datetime.now()
    # $facet: mehrere Auswertungen in einem Durchlauf über die Collection
    return list(db.vorgaenge.aggregate([{"$facet": {
        "offen_nach_typ": [
            {"$match": {"status": {"$ne": "erledigt"}}},
            {"$group": {"_id": "$typ", "anzahl": {"$sum": 1}}},
            {"$sort": {"anzahl": -1}},
        ],
        "offene_schaeden_nach_prioritaet": [
            {"$match": {"typ": "schaden", "status": {"$ne": "erledigt"}}},
            {"$group": {"_id": "$prioritaet", "anzahl": {"$sum": 1}}},
        ],
        "fristen_90_tage": [
            {"$match": {"typ": "frist", "status": "offen", "faellig_am": {"$lte": heute + timedelta(days=90)}}},
            {"$group": {"_id": "$titel", "anzahl": {"$sum": 1}, "naechste": {"$min": "$faellig_am"}}},
            {"$sort": {"naechste": 1}},
        ],
        "schaeden_je_monat": [
            {"$match": {"typ": "schaden", "datum": {"$gte": heute - timedelta(days=365)}}},
            {"$group": {"_id": {"$dateToString": {"date": "$datum", "format": "%Y-%m"}},
                        "anzahl": {"$sum": 1}, "kosten": {"$sum": {"$ifNull": ["$kosten.rechnung", "$kosten.schaetzung"]}}}},
            {"$sort": {"_id": 1}},
        ],
    }}]))[0]


def _top_objekte():
    return list(db.vorgaenge.aggregate([
        {"$match": {"status": {"$ne": "erledigt"}, "typ": {"$ne": "frist"}}},
        {"$group": {"_id": "$objekt_id", "offen": {"$sum": 1}}},
        {"$sort": {"offen": -1}},
        {"$limit": 5},
        {"$lookup": {"from": "objekte", "localField": "_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"bezeichnung": 1, "adresse.ort": 1}}], "as": "objekt"}},
        {"$unwind": "$objekt"},
        {"$project": {"_id": 0, "objekt_id": "$_id", "offen": 1,
                      "bezeichnung": "$objekt.bezeichnung", "ort": "$objekt.adresse.ort"}},
    ]))


def _bestand_vs_markt():
    """Teuerste Aggregation: €/m² des Bestands je Kreis (Vertrag → Objekt → eingebettete Einheit)
    gegen den Median der echten Angebote im selben Kreis."""
    bestand = {r["_id"]: r for r in db.vertraege.aggregate([
        {"$match": {"status": {"$in": ["aktiv", "gekuendigt"]}, "einheit_typ": "wohnung"}},
        {"$lookup": {"from": "objekte", "localField": "objekt_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"adresse.kreis": 1, "einheiten": 1}}], "as": "o"}},
        {"$unwind": "$o"},
        {"$set": {"einheit": {"$first": {"$filter": {
            "input": "$o.einheiten", "cond": {"$eq": ["$$this.einheit_id", "$einheit_id"]}}}}}},
        {"$group": {"_id": "$o.adresse.kreis", "wohnungen": {"$sum": 1},
                    "median_qm": {"$median": {"input": {"$divide": ["$miete.kalt", "$einheit.wohnflaeche"]},
                                              "method": "approximate"}}}},
    ])}
    markt = {r["_id"]: r for r in db.angebote.aggregate([
        {"$match": {"adresse.kreis": {"$in": list(bestand)}}},
        {"$group": {"_id": "$adresse.kreis", "angebote": {"$sum": 1},
                    "median_qm": {"$median": {"input": {"$divide": ["$miete.kalt", "$wohnflaeche"]},
                                              "method": "approximate"}}}},
    ])}
    return sorted([{
        "kreis": k,
        "wohnungen": b["wohnungen"],
        "bestand_median_qm": round(b["median_qm"], 2),
        "angebote": markt[k]["angebote"],
        "markt_median_qm": round(markt[k]["median_qm"], 2),
    } for k, b in bestand.items() if k in markt], key=lambda r: -r["wohnungen"])


def berechnen():
    einheiten = _einheiten()
    wohnungen = [e for e in einheiten if e["typ"] == "wohnung"]
    leer = sum(e["anzahl"] for e in wohnungen if e["status"] == "leer")
    gesamt = sum(e["anzahl"] for e in wohnungen)
    return {
        "stand": datetime.now(),
        "objekte": db.objekte.estimated_document_count(),
        "einheiten": einheiten,
        "leerstandsquote_wohnungen": round(leer / gesamt * 100, 1) if gesamt else 0,
        "mieten_monat": _mieten(),
        "vorgaenge": _vorgaenge(),
        "top_objekte_offene_vorgaenge": _top_objekte(),
        "bestand_vs_markt": _bestand_vs_markt(),
    }


@router.get("")
def dashboard():
    daten, meta = cached("dashboard:kennzahlen", TTL, berechnen)
    return {"daten": daten, "cache": meta}
