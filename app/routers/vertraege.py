from datetime import date, datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.cache import invalidieren
from app.db import db, mongo
from app.routers.markt import vergleich
from app.util import gefunden, jsonify, oid

router = APIRouter(prefix="/api/vertraege", tags=["Verträge"])


@router.get("/{vertrag_id}")
def vertrag(vertrag_id: str):
    """Ein Lesezugriff liefert den kompletten Vertrag inkl. Mietanpassungen und Übergabeprotokollen."""
    v = gefunden(db.vertraege.find_one({"_id": oid(vertrag_id)}), "Vertrag")
    return jsonify(v)


@router.get("/{vertrag_id}/marktvergleich")
def marktvergleich(vertrag_id: str):
    """Ist-Miete des Vertrags gegen echte Angebote mit ähnlicher Fläche in derselben PLZ."""
    v = gefunden(db.vertraege.find_one({"_id": oid(vertrag_id)}, {"objekt_id": 1, "einheit_id": 1, "miete": 1}), "Vertrag")
    o = db.objekte.find_one({"_id": v["objekt_id"]},
                            {"adresse": 1, "einheiten": {"$elemMatch": {"einheit_id": v["einheit_id"]}}})
    einheit = o["einheiten"][0]
    if einheit["typ"] != "wohnung":
        raise HTTPException(400, "Marktvergleich nur für Wohnungen")

    markt, meta = vergleich(o["adresse"]["plz"], o["adresse"]["kreis"], einheit["wohnflaeche"])
    ist_qm = v["miete"]["kalt"] / einheit["wohnflaeche"]
    abweichung = None
    if markt.get("median_qm"):
        abweichung = round((ist_qm / markt["median_qm"] - 1) * 100, 1)
    return {
        "wohnflaeche": einheit["wohnflaeche"],
        "ist_kalt": v["miete"]["kalt"],
        "ist_qm": round(ist_qm, 2),
        "markt": markt,
        "abweichung_prozent": abweichung,
        "hinweis": "Angebotsmieten ImmoScout24 2018–2020, keine ortsübliche Vergleichsmiete im Sinne des Mietspiegels",
        "cache": meta,
    }


# ---------------------------------------------------------------- Mieterwechsel als Transaktion

class NeuerMieter(BaseModel):
    anrede: str = Field(pattern="^(Herr|Frau|Divers)$")
    vorname: str
    nachname: str
    email: EmailStr
    telefon: str | None = None


class Mieterwechsel(BaseModel):
    objekt_id: str
    einheit_id: str
    mieter: NeuerMieter
    beginn: date
    kalt: float = Field(gt=0)
    nebenkosten_vorauszahlung: float = Field(ge=0)
    kaution_art: str = "Kautionskonto"
    fehler_simulieren: bool = False  # bricht nach den ersten Schreibvorgängen ab → Rollback


class SimulierterFehler(Exception):
    pass


@router.post("/mieterwechsel")
def mieterwechsel(body: Mieterwechsel):
    """Fünf Schreibvorgänge in drei Collections, alles oder nichts.

    1. alten Vertrag beenden  2. neuen Mieter anlegen  3. neuen Vertrag anlegen
    4. Status der eingebetteten Einheit im Objekt umstellen  5. Vorgang dokumentieren
    """
    objekt_id = oid(body.objekt_id)
    o = gefunden(db.objekte.find_one({"_id": objekt_id, "einheiten.einheit_id": body.einheit_id},
                                     {"bezeichnung": 1, "einheiten.$": 1}), "Objekt/Einheit")
    einheit = o["einheiten"][0]
    beginn = datetime.combine(body.beginn, datetime.min.time())
    alter_vertrag_id = einheit.get("aktueller_vertrag_id")
    mieter_id, vertrag_id = ObjectId(), ObjectId()
    name = f"{body.mieter.vorname} {body.mieter.nachname}"
    protokoll = []

    def schritte(session):
        protokoll.clear()
        if alter_vertrag_id:
            # Liegt der Mietbeginn in der Zukunft, ist der alte Vertrag bis dahin nur gekündigt
            alter_status = "beendet" if beginn <= datetime.now() else "gekuendigt"
            db.vertraege.update_one({"_id": alter_vertrag_id},
                                    {"$set": {"status": alter_status, "ende": beginn}}, session=session)
            protokoll.append(f"alter Vertrag {alter_status}")

        db.mieter.insert_one({
            "_id": mieter_id, "art": "person", "anrede": body.mieter.anrede,
            "vorname": body.mieter.vorname, "nachname": body.mieter.nachname,
            "kontakt": {"email": body.mieter.email, **({"telefon": body.mieter.telefon} if body.mieter.telefon else {})},
        }, session=session)
        protokoll.append("Mieter angelegt")

        vertrag_doc = {
            "_id": vertrag_id, "nummer": f"V-{beginn.year}-{str(vertrag_id)[-4:].upper()}",
            "objekt_id": objekt_id, "einheit_id": body.einheit_id, "einheit_typ": einheit["typ"],
            "mieter_ids": [mieter_id], "mieter_namen": [name], "objekt_bezeichnung": o["bezeichnung"],
            "art": "unbefristet", "status": "aktiv", "beginn": beginn,
            "miete": {"kalt": body.kalt, "kalt_bei_beginn": body.kalt,
                      "nebenkosten_vorauszahlung": body.nebenkosten_vorauszahlung},
            "mietanpassungen": [],
        }
        if einheit["typ"] == "wohnung":
            vertrag_doc["kaution"] = {"betrag": round(body.kalt * 3, 2), "art": body.kaution_art}
        db.vertraege.insert_one(vertrag_doc, session=session)
        protokoll.append("Vertrag angelegt")

        if body.fehler_simulieren:
            raise SimulierterFehler()

        # Positionaler Operator mit arrayFilters: nur die betroffene eingebettete Einheit ändern
        db.objekte.update_one(
            {"_id": objekt_id},
            {"$set": {"einheiten.$[e].status": "vermietet", "einheiten.$[e].aktueller_vertrag_id": vertrag_id}},
            array_filters=[{"e.einheit_id": body.einheit_id}],
            session=session,
        )
        protokoll.append("Einheit auf vermietet gesetzt")

        db.vorgaenge.insert_one({
            "typ": "korrespondenz", "objekt_id": objekt_id, "einheit_id": body.einheit_id,
            "vertrag_id": vertrag_id, "datum": datetime.now(), "status": "erledigt",
            "titel": "Mietvertrag abgeschlossen", "richtung": "aus", "kanal": "Brief", "partner": name,
        }, session=session)
        protokoll.append("Vorgang dokumentiert")

    try:
        with mongo.start_session() as s:
            s.with_transaction(schritte, read_concern=ReadConcern("snapshot"),
                               write_concern=WriteConcern("majority"))
    except SimulierterFehler:
        # Nachweis des Rollbacks: nichts aus der Transaktion ist sichtbar
        return {
            "ergebnis": "abgebrochen, Rollback",
            "ausgefuehrt_vor_abbruch": protokoll,
            "mieter_existiert": db.mieter.count_documents({"_id": mieter_id}) > 0,
            "vertrag_existiert": db.vertraege.count_documents({"_id": vertrag_id}) > 0,
            "alter_vertrag_status": alter_vertrag_id and db.vertraege.find_one({"_id": alter_vertrag_id})["status"],
        }
    except OperationFailure as e:
        raise HTTPException(422, f"Von MongoDB abgelehnt: {e.details.get('errmsg', e)}")

    invalidieren("dashboard:*")
    return {"ergebnis": "gespeichert", "schritte": protokoll,
            "mieter_id": str(mieter_id), "vertrag_id": str(vertrag_id)}
