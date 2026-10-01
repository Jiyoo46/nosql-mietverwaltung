from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pymongo import ReturnDocument
from pymongo.errors import WriteError

from app.cache import invalidieren
from app.db import db
from app.util import gefunden, jsonify, oid

router = APIRouter(prefix="/api/vorgaenge", tags=["Vorgänge"])

Status = Literal["offen", "in_bearbeitung", "erledigt"]


@router.get("")
def vorgaenge(typ: str | None = None, status: Status | None = None, offen: bool = False,
              objekt_id: str | None = None, ohne_fristen: bool = False, seite: int = 1, pro_seite: int = 25):
    filter_ = {}
    if typ:
        filter_["typ"] = typ
    elif ohne_fristen:
        filter_["typ"] = {"$ne": "frist"}
    if status:
        filter_["status"] = status
    elif offen:
        filter_["status"] = {"$ne": "erledigt"}
    if objekt_id:
        filter_["objekt_id"] = oid(objekt_id)
    gesamt = db.vorgaenge.count_documents(filter_)
    docs = list(db.vorgaenge.aggregate([
        {"$match": filter_},
        {"$sort": {"datum": -1}},
        {"$skip": (seite - 1) * pro_seite},
        {"$limit": pro_seite},
        {"$lookup": {"from": "objekte", "localField": "objekt_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"bezeichnung": 1}}], "as": "objekt"}},
        {"$set": {"objekt_bezeichnung": {"$first": "$objekt.bezeichnung"}}},
        {"$project": {"objekt": 0, "verlauf": 0}},
    ]))
    return {"gesamt": gesamt, "seite": seite, "daten": jsonify(docs)}


@router.get("/{vorgang_id}")
def vorgang(vorgang_id: str):
    return jsonify(gefunden(db.vorgaenge.find_one({"_id": oid(vorgang_id)}), "Vorgang"))


class NeuerSchaden(BaseModel):
    objekt_id: str
    einheit_id: str | None = None
    kategorie: str
    gewerk: str
    prioritaet: Literal["niedrig", "mittel", "hoch"]
    beschreibung: str | None = None


@router.post("/schaden", status_code=201)
def schaden_melden(body: NeuerSchaden):
    objekt_id = oid(body.objekt_id)
    o = gefunden(db.objekte.find_one({"_id": objekt_id}, {"einheiten": 1}), "Objekt")
    jetzt = datetime.now()
    doc = {
        "typ": "schaden", "objekt_id": objekt_id, "datum": jetzt, "status": "offen",
        "titel": body.kategorie, "kategorie": body.kategorie, "gewerk": body.gewerk,
        "prioritaet": body.prioritaet, "verlauf": [{"datum": jetzt, "eintrag": "Vorgang angelegt"}],
    }
    if body.einheit_id:
        einheit = next((e for e in o["einheiten"] if e["einheit_id"] == body.einheit_id), None)
        if einheit is None:
            raise HTTPException(404, "Einheit nicht gefunden")
        doc["einheit_id"] = body.einheit_id
        if "aktueller_vertrag_id" in einheit:
            doc["vertrag_id"] = einheit["aktueller_vertrag_id"]
    if body.beschreibung:
        doc["beschreibung"] = body.beschreibung

    _id = db.vorgaenge.insert_one(doc).inserted_id
    geloescht = invalidieren("dashboard:*")
    return {"_id": str(_id), "cache_keys_invalidiert": geloescht}


class Statuswechsel(BaseModel):
    status: Status
    eintrag: str | None = None


@router.patch("/{vorgang_id}/status")
def status_aendern(vorgang_id: str, body: Statuswechsel):
    """Status setzen und Verlaufseintrag anhängen: ein atomares Update auf ein Dokument."""
    jetzt = datetime.now()
    eintrag = body.eintrag or f"Status geändert auf {body.status}"
    doc = db.vorgaenge.find_one_and_update(
        {"_id": oid(vorgang_id)},
        {"$set": {"status": body.status}, "$push": {"verlauf": {"datum": jetzt, "eintrag": eintrag}}},
        return_document=ReturnDocument.AFTER,
    )
    gefunden(doc, "Vorgang")
    invalidieren("dashboard:*")
    return jsonify(doc)


@router.post("/roh", tags=["Demo"])
def roh_einfuegen(doc: dict):
    """Beliebiges Dokument einfügen, um die serverseitige $jsonSchema-Validierung zu zeigen."""
    try:
        _id = db.vorgaenge.insert_one(doc).inserted_id
    except WriteError as e:
        # Code 121: Document failed validation, MongoDB liefert die verletzte Regel mit
        raise HTTPException(422, {"fehler": "Validierung durch MongoDB fehlgeschlagen",
                                  "details": jsonify(e.details.get("errInfo"))})
    return {"_id": str(_id), "hinweis": "Dokument angenommen"}
