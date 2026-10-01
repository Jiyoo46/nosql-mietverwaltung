"""Deutsche Zahlen- und Datumsformate sowie die Shell-Darstellung von Dokumenten für die Templates."""
import json
from datetime import datetime

from bson import ObjectId

MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def zahl(x, dez=0):
    if x is None:
        return "—"
    s = f"{x:,.{dez}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def euro(x, dez=2):
    return "—" if x is None else f"{zahl(x, dez)} €"


def _dt(v):
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


def datum(v):
    return "—" if v is None else _dt(v).strftime("%d.%m.%Y")


def monat_jahr(v):
    d = _dt(v)
    return f"{['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'][d.month - 1]} {d.year}"


def kurzmonat(schluessel):
    """'2026-03' → 'Mär'."""
    return MONATE[int(schluessel[5:7]) - 1]


def shell(doc, tiefe=0, max_liste=3):
    """Dokument so darstellen, wie es die MongoDB-Shell zeigt (ObjectId, ISODate).

    Lange Listen werden nach `max_liste` Einträgen mit „…“ gekürzt."""
    pad, pad2 = "  " * tiefe, "  " * (tiefe + 1)
    if isinstance(doc, ObjectId):
        return f'ObjectId("{doc}")'
    if isinstance(doc, datetime):
        return f'ISODate("{doc.strftime("%Y-%m-%d")}")'
    if isinstance(doc, dict):
        if not doc:
            return "{}"
        zeilen = [f"{pad2}{k}: {shell(v, tiefe + 1, max_liste)}" for k, v in doc.items()]
        return "{\n" + ",\n".join(zeilen) + f"\n{pad}}}"
    if isinstance(doc, list):
        if not doc:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in doc):
            return "[" + ", ".join(shell(x, tiefe + 1, max_liste) for x in doc) + "]"
        teile = [f"{pad2}{shell(x, tiefe + 1, max_liste)}" for x in doc[:max_liste]]
        if len(doc) > max_liste:
            teile.append(f"{pad2}… {len(doc) - max_liste} weitere")
        return "[\n" + ",\n".join(teile) + f"\n{pad}]"
    if isinstance(doc, str):
        return json.dumps(doc, ensure_ascii=False)
    if isinstance(doc, bool):
        return "true" if doc else "false"
    return str(doc)


# Heizungsarten kommen aus der Quelle als englische Schlüssel
HEIZUNG = {
    "central_heating": "Zentralheizung", "district_heating": "Fernwärme", "gas_heating": "Gasheizung",
    "floor_heating": "Fußbodenheizung", "oil_heating": "Ölheizung", "self_contained_central_heating": "Etagenheizung",
    "heat_pump": "Wärmepumpe", "electric_heating": "Elektroheizung", "night_storage_heater": "Nachtspeicherheizung",
    "combined_heat_and_power_plant": "Blockheizkraftwerk", "wood_pellet_heating": "Pelletheizung",
    "stove_heating": "Ofenheizung", "solar_heating": "Solarheizung",
}


def heizung(v):
    return HEIZUNG.get(v, v.replace("_", " "))


FILTER = {"heizung": heizung, "zahl": zahl, "euro": euro, "datum": datum, "monat_jahr": monat_jahr, "kurzmonat": kurzmonat, "shell": shell}
