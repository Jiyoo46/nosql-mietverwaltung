"""Importiert echte Mietangebote (Kaggle/ImmoScout24) aus Baden-Württemberg in die Collection `angebote`.

Aufruf im Container:  docker exec app python -m scripts.import_angebote

Jede CSV-Zeile wird zu einem Dokument. Leere CSV-Zellen werden nicht als null gespeichert,
sondern das Feld entfällt: Genau diese uneinheitliche Befüllung ist ein Kernargument für den
Document Store (in SQL wären es 49 Spalten mit vielen NULL-Werten).
"""
import html
import math
from datetime import datetime

import pandas as pd
from pymongo import ASCENDING

from app.db import db

CSV = "data/raw/immo_data.csv"
BUNDESLAND = "Baden_Württemberg"

ERFASST = {
    "Sep18": datetime(2018, 9, 1),
    "May19": datetime(2019, 5, 1),
    "Oct19": datetime(2019, 10, 1),
    "Feb20": datetime(2020, 2, 1),
}

# Boolesche CSV-Spalten werden zu einem Array: nur vorhandene Merkmale werden gespeichert
AUSSTATTUNG = {
    "balcony": "balkon",
    "hasKitchen": "einbaukueche",
    "cellar": "keller",
    "lift": "aufzug",
    "garden": "garten",
}

VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "adresse", "miete", "wohnflaeche", "zimmer", "erfasst"],
        "properties": {
            "_id": {"bsonType": ["int", "long"], "description": "scoutId aus ImmoScout24"},
            "adresse": {
                "bsonType": "object",
                "required": ["plz", "kreis", "ort"],
                "properties": {"plz": {"bsonType": "string", "pattern": "^[0-9]{5}$"}},
            },
            "miete": {
                "bsonType": "object",
                "required": ["kalt"],
                "properties": {"kalt": {"bsonType": "double", "minimum": 100, "maximum": 10000}},
            },
            "wohnflaeche": {"bsonType": "double", "minimum": 10},
            "zimmer": {"bsonType": "double", "minimum": 1, "maximum": 20},
            "ausstattung": {"bsonType": "array", "items": {"enum": list(AUSSTATTUNG.values())}},
        },
    }
}


def wert(v):
    """Normalisiert eine CSV-Zelle; None bedeutet: Feld weglassen."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str):
        v = html.unescape(v).strip()
        return v or None
    if hasattr(v, "item"):  # numpy-Typen in Python-Typen umwandeln
        v = v.item()
    return v


def ort(v):
    return v.replace("_Kreis", " (Kreis)").replace("_", " ") if v else None


def kompakt(d):
    """Entfernt leere Felder und leere Unterdokumente rekursiv."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = kompakt(v)
        if v is None or v == {} or v == []:
            continue
        out[k] = v
    return out


def plausibel(r):
    """Offensichtliche Erfassungsfehler aussortieren (ca. 60 Zeilen)."""
    return 100 <= r.baseRent <= 10000 and r.livingSpace >= 10 and r.noRooms <= 20


def zu_dokument(r):
    baujahr = wert(r.yearConstructed)
    if baujahr and not 1500 <= baujahr <= 2021:  # Tippfehler wie 1063 oder 2090
        baujahr = None
    warm = wert(r.totalRent)
    if warm and warm < r.baseRent:  # Warmmiete kleiner als Kaltmiete ist ein Erfassungsfehler
        warm = None
    traeger = wert(r.firingTypes)

    return kompakt({
        "_id": int(r.scoutId),
        "erfasst": ERFASST[r.date],
        "adresse": {
            # ImmoScout blendet die Adresse aus, wenn der Anbieter sie nicht freigibt
            "strasse": wert(r.streetPlain) and " ".join(wert(r.streetPlain).replace("_", " ").split()),
            "hausnummer": wert(r.houseNumber),
            "plz": f"{int(r.geo_plz):05d}",
            # Kreisfreie Städte: regio3 ist der Stadtteil, nicht der Ort
            "ort": ort(wert(r.regio3)) if r.regio2.endswith("_Kreis") else ort(wert(r.regio2)),
            "stadtteil": None if r.regio2.endswith("_Kreis") else ort(wert(r.regio3)),
            "kreis": ort(wert(r.regio2)),
            "bundesland": "Baden-Württemberg",
        },
        "miete": {
            "kalt": float(r.baseRent),
            "nebenkosten": wert(r.serviceCharge),
            "heizkosten": wert(r.heatingCosts),
            "warm": warm,
        },
        "wohnflaeche": float(r.livingSpace),
        "zimmer": float(r.noRooms),
        "typ": wert(r.typeOfFlat),
        "etage": wert(r.floor),
        "etagen_gesamt": wert(r.numberOfFloors),
        "baujahr": baujahr,
        "letzte_sanierung": wert(r.lastRefurbish),
        "neubau": bool(r.newlyConst),
        "zustand": wert(r.condition),
        "ausstattungsqualitaet": wert(r.interiorQual),
        "ausstattung": [name for spalte, name in AUSSTATTUNG.items() if getattr(r, spalte)],
        "stellplaetze": wert(r.noParkSpaces),
        "haustiere": wert(r.petsAllowed),
        "energie": {
            "heizungsart": wert(r.heatingType),
            "energietraeger": traeger.split(":") if traeger else None,
            "effizienzklasse": wert(r.energyEfficiencyClass),
            "kennwert": wert(r.thermalChar),
        },
        "beschreibung": wert(r.description),
        "ausstattung_text": wert(r.facilities),
    })


def main():
    df = pd.read_csv(CSV, low_memory=False)
    bw = df[df.regio1 == BUNDESLAND]
    gueltig = [r for r in bw.itertuples(index=False) if plausibel(r)]
    docs = [zu_dokument(r) for r in gueltig]

    db.drop_collection("angebote")
    db.create_collection("angebote", validator=VALIDATOR)
    for i in range(0, len(docs), 2000):
        db.angebote.insert_many(docs[i : i + 2000], ordered=False)

    # Unterstützt den Marktvergleich: gleiche PLZ, ähnliche Wohnfläche
    db.angebote.create_index([("adresse.plz", ASCENDING), ("wohnflaeche", ASCENDING)])
    db.angebote.create_index([("adresse.kreis", ASCENDING), ("miete.kalt", ASCENDING)])

    print(f"{len(bw)} Angebote in BW, {len(bw) - len(docs)} aussortiert, {db.angebote.count_documents({})} importiert")


if __name__ == "__main__":
    main()
