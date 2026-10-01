from fastapi import APIRouter
from pydantic import BaseModel
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.cache import invalidieren
from app.db import db, mongo
from app.util import gefunden, jsonify, oid

router = APIRouter(prefix="/api/mieter", tags=["Mieter"])


@router.get("")
def mieter_liste(q: str | None = None, seite: int = 1, pro_seite: int = 25):
    filter_ = {}
    if q:
        filter_["$or"] = [{"nachname": {"$regex": q, "$options": "i"}},
                          {"vorname": {"$regex": q, "$options": "i"}},
                          {"firmenname": {"$regex": q, "$options": "i"}}]
    gesamt = db.mieter.count_documents(filter_)
    docs = db.mieter.find(filter_, {"bankverbindung": 0}).sort("nachname", 1).skip((seite - 1) * pro_seite).limit(pro_seite)
    return {"gesamt": gesamt, "seite": seite, "daten": jsonify(list(docs))}


@router.get("/{mieter_id}")
def mieter(mieter_id: str):
    m = gefunden(db.mieter.find_one({"_id": oid(mieter_id)}), "Mieter")
    # Multikey-Index auf vertraege.mieter_ids: alle Verträge, in denen der Mieter steht
    m["vertraege"] = list(db.vertraege.find(
        {"mieter_ids": m["_id"]},
        {"nummer": 1, "objekt_bezeichnung": 1, "einheit_id": 1, "einheit_typ": 1, "status": 1, "beginn": 1, "ende": 1, "miete": 1},
    ).sort("beginn", -1))
    return jsonify(m)


class Namensaenderung(BaseModel):
    nachname: str
    kopien_aktualisieren: bool = True  # False zeigt, wie denormalisierte Kopien veralten


@router.patch("/{mieter_id}/name")
def name_aendern(mieter_id: str, body: Namensaenderung):
    """Nachname ändern, z. B. nach Heirat.

    Der Name steht doppelt: im Mieter-Dokument und als Kopie in `vertraege.mieter_namen`.
    Beides wird in einer Transaktion geändert; mit `kopien_aktualisieren=false` bleibt die
    Kopie bewusst stehen, um die Inkonsistenz der Denormalisierung vorzuführen.
    """
    _id = oid(mieter_id)
    alt = gefunden(db.mieter.find_one({"_id": _id, "art": "person"}), "Mieter (Person)")
    alter_name = f"{alt['vorname']} {alt['nachname']}"
    neuer_name = f"{alt['vorname']} {body.nachname}"

    def schritte(session):
        db.mieter.update_one({"_id": _id}, {"$set": {"nachname": body.nachname,
                                                     "bankverbindung.kontoinhaber": neuer_name}}, session=session)
        if not body.kopien_aktualisieren:
            return 0
        # Update-Pipeline: Name an der Position setzen, an der die Mieter-ID steht.
        # Repariert auch Kopien, die bei einer früheren Änderung veraltet sind.
        r = db.vertraege.update_many(
            {"mieter_ids": _id},
            [{"$set": {"mieter_namen": {"$map": {
                "input": {"$range": [0, {"$size": "$mieter_ids"}]},
                "as": "i",
                "in": {"$cond": [{"$eq": [{"$arrayElemAt": ["$mieter_ids", "$$i"]}, _id]},
                                 neuer_name,
                                 {"$arrayElemAt": ["$mieter_namen", "$$i"]}]},
            }}}}],
            session=session,
        )
        return r.modified_count

    with mongo.start_session() as s:
        geaenderte_vertraege = s.with_transaction(
            schritte, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
    invalidieren("dashboard:*")

    veraltet = db.vertraege.count_documents({"mieter_ids": _id, "mieter_namen": {"$ne": neuer_name}})
    return {"alt": alter_name, "neu": neuer_name, "vertraege_aktualisiert": geaenderte_vertraege,
            "vertraege_mit_veraltetem_namen": veraltet}
