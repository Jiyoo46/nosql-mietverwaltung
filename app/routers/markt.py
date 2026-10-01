"""Marktmieten aus den echten Angeboten (ImmoScout24, 2018–2020)."""
from fastapi import APIRouter, HTTPException

from app.cache import cached
from app.db import db
from app.util import jsonify

router = APIRouter(prefix="/api/markt", tags=["Markt"])

QM_PREIS = {"$divide": ["$miete.kalt", "$wohnflaeche"]}
MIN_VERGLEICHE = 5


def _statistik(filter_):
    ergebnis = list(db.angebote.aggregate([
        {"$match": filter_},
        {"$group": {
            "_id": None,
            "anzahl": {"$sum": 1},
            "durchschnitt_qm": {"$avg": QM_PREIS},
            "median_qm": {"$median": {"input": QM_PREIS, "method": "approximate"}},
            "min_qm": {"$min": QM_PREIS},
            "max_qm": {"$max": QM_PREIS},
        }},
        {"$project": {"_id": 0}},
    ]))
    return ergebnis[0] if ergebnis else {"anzahl": 0}


def vergleich(plz: str, kreis: str, wohnflaeche: float):
    """Vergleichsangebote mit ±20 % Wohnfläche; erst gleiche PLZ, bei zu wenigen Treffern der Kreis."""
    flaeche = {"$gte": wohnflaeche * 0.8, "$lte": wohnflaeche * 1.2}
    # Fläche auf 5 m² runden, damit ähnliche Wohnungen denselben Cache-Eintrag nutzen
    bucket = round(wohnflaeche / 5) * 5

    def berechnen():
        stat = _statistik({"adresse.plz": plz, "wohnflaeche": flaeche})
        if stat["anzahl"] >= MIN_VERGLEICHE:
            return {"ebene": "PLZ", "gebiet": plz, **stat}
        stat = _statistik({"adresse.kreis": kreis, "wohnflaeche": flaeche})
        return {"ebene": "Kreis", "gebiet": kreis, **stat}

    return cached(f"markt:vergleich:{plz}:{bucket}", 3600, berechnen)


@router.get("/vergleich")
def markt_vergleich(plz: str, wohnflaeche: float, kreis: str | None = None):
    if kreis is None:
        beispiel = db.angebote.find_one({"adresse.plz": plz}, {"adresse.kreis": 1})
        if not beispiel:
            raise HTTPException(404, f"Keine Angebote in PLZ {plz}")
        kreis = beispiel["adresse"]["kreis"]
    daten, meta = vergleich(plz, kreis, wohnflaeche)
    return {"daten": daten, "cache": meta}


@router.get("/kreise")
def markt_kreise():
    """Mietniveau je Kreis über alle 16.000 Angebote, absteigend nach Median."""
    def berechnen():
        return list(db.angebote.aggregate([
            {"$group": {
                "_id": "$adresse.kreis",
                "anzahl": {"$sum": 1},
                "median_qm": {"$median": {"input": QM_PREIS, "method": "approximate"}},
                "durchschnitt_flaeche": {"$avg": "$wohnflaeche"},
            }},
            {"$match": {"anzahl": {"$gte": 50}}},
            {"$sort": {"median_qm": -1}},
            {"$project": {"_id": 0, "kreis": "$_id", "anzahl": 1, "median_qm": {"$round": ["$median_qm", 2]},
                          "durchschnitt_flaeche": {"$round": ["$durchschnitt_flaeche", 1]}}},
        ]))

    daten, meta = cached("markt:kreise", 3600, berechnen)
    return {"daten": daten, "cache": meta}


@router.get("/angebote")
def angebote(kreis: str | None = None, plz: str | None = None, ausstattung: str | None = None,
             seite: int = 1, pro_seite: int = 20):
    """Angebote durchsuchen. `ausstattung` kommagetrennt, z. B. 'balkon,aufzug' (alle müssen vorhanden sein)."""
    filter_ = {}
    if kreis:
        filter_["adresse.kreis"] = kreis
    if plz:
        filter_["adresse.plz"] = plz
    if ausstattung:
        filter_["ausstattung"] = {"$all": ausstattung.split(",")}
    gesamt = db.angebote.count_documents(filter_)
    docs = list(db.angebote.find(filter_, {"beschreibung": 0, "ausstattung_text": 0})
                .sort("erfasst", -1).skip((seite - 1) * pro_seite).limit(pro_seite))
    return {"gesamt": gesamt, "seite": seite, "daten": jsonify(docs)}


@router.get("/angebote/{scout_id}")
def angebot(scout_id: int):
    doc = db.angebote.find_one({"_id": scout_id})
    if not doc:
        raise HTTPException(404, "Angebot nicht gefunden")
    return jsonify(doc)

