from fastapi import APIRouter

from app.db import db
from app.util import gefunden, jsonify, oid

router = APIRouter(prefix="/api/objekte", tags=["Objekte"])


def _anzahl(status=None, typ=None):
    """Zählt eingebettete Einheiten direkt im Dokument, ohne zweite Abfrage."""
    bedingungen = []
    if status:
        bedingungen.append({"$eq": ["$$e.status", status]})
    if typ:
        bedingungen.append({"$eq": ["$$e.typ", typ]})
    return {"$size": {"$filter": {"input": "$einheiten", "as": "e", "cond": {"$and": bedingungen}}}}


@router.get("")
def objekte(kreis: str | None = None, typ: str | None = None, q: str | None = None,
            seite: int = 1, pro_seite: int = 25):
    filter_ = {}
    if kreis:
        filter_["adresse.kreis"] = kreis
    if typ:
        filter_["typ"] = typ
    if q:
        filter_["$or"] = [{"bezeichnung": {"$regex": q, "$options": "i"}},
                          {"adresse.ort": {"$regex": q, "$options": "i"}}]
    gesamt = db.objekte.count_documents(filter_)
    daten = db.objekte.aggregate([
        {"$match": filter_},
        {"$sort": {"bezeichnung": 1}},
        {"$skip": (seite - 1) * pro_seite},
        {"$limit": pro_seite},
        # Offene Vorgänge je Objekt: Lookup mit Pipeline nutzt den Index (objekt_id, status, datum)
        {"$lookup": {
            "from": "vorgaenge",
            "let": {"id": "$_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [{"$eq": ["$objekt_id", "$$id"]}, {"$ne": ["$status", "erledigt"]}]}}},
                {"$count": "n"},
            ],
            "as": "offen",
        }},
        {"$project": {
            "bezeichnung": 1, "typ": 1, "adresse": 1, "baujahr": 1,
            "einheiten_gesamt": {"$size": "$einheiten"},
            "wohnungen": _anzahl(typ="wohnung"),
            "leer": _anzahl(status="leer"),
            "offene_vorgaenge": {"$ifNull": [{"$first": "$offen.n"}, 0]},
        }},
    ])
    return {"gesamt": gesamt, "seite": seite, "daten": jsonify(list(daten))}


@router.get("/{objekt_id}")
def objekt(objekt_id: str):
    o = gefunden(db.objekte.find_one({"_id": oid(objekt_id)}), "Objekt")

    # Anwendungsseitiger Join: aktuelle Verträge aller Einheiten mit einer einzigen $in-Abfrage
    vertrag_ids = [e["aktueller_vertrag_id"] for e in o["einheiten"] if "aktueller_vertrag_id" in e]
    vertraege = {v["_id"]: v for v in db.vertraege.find(
        {"_id": {"$in": vertrag_ids}},
        {"nummer": 1, "mieter_namen": 1, "miete": 1, "beginn": 1, "ende": 1, "status": 1, "art": 1},
    )}
    for e in o["einheiten"]:
        if "aktueller_vertrag_id" in e:
            e["vertrag"] = vertraege.get(e["aktueller_vertrag_id"])

    o["soll_miete_monat"] = round(sum(
        v["miete"]["kalt"] + v["miete"].get("nebenkosten_vorauszahlung", 0) for v in vertraege.values()), 2)
    o["offene_vorgaenge"] = list(db.vorgaenge.find(
        {"objekt_id": o["_id"], "status": {"$ne": "erledigt"}},
        {"verlauf": 0},
    ).sort("datum", -1).limit(50))
    return jsonify(o)


@router.get("/{objekt_id}/vertraege")
def objekt_vertraege(objekt_id: str, einheit_id: str | None = None):
    """Vertragshistorie, neueste zuerst (Index objekt_id, einheit_id, beginn)."""
    filter_ = {"objekt_id": oid(objekt_id)}
    if einheit_id:
        filter_["einheit_id"] = einheit_id
    docs = db.vertraege.find(filter_, {"uebergaben": 0}).sort([("einheit_id", 1), ("beginn", -1)])
    return jsonify(list(docs))
