"""Erzeugt den verwalteten Bestand: Objekte, Mieter, Verträge und Vorgänge.

Aufruf im Container:  docker exec app python -m scripts.generate
Voraussetzung: `angebote` ist importiert (scripts.import_angebote).

Objekte und ihre erste Wohnung stammen aus echten Angeboten (Adresse, Fläche, Miete, Baujahr).
Alles Personenbezogene ist fiktiv (Faker, de_DE); echte Mieterdaten werden bewusst nicht verwendet.
Fester Seed: Jeder Lauf erzeugt denselben Datenstand (wichtig für die Demo).
"""
import random
from datetime import datetime, timedelta

from bson import ObjectId
from faker import Faker
from pymongo import ASCENDING, DESCENDING

from app.db import db

SEED = 42
HEUTE = datetime(2026, 10, 1)
ANZAHL_OBJEKTE = 250
KREISE = ["Stuttgart", "Böblingen (Kreis)", "Esslingen (Kreis)", "Ludwigsburg (Kreis)", "Reutlingen (Kreis)"]

random.seed(SEED)
Faker.seed(SEED)
fake = Faker("de_DE")


# ---------------------------------------------------------------- Hilfsfunktionen

def datum(von, bis):
    return von + timedelta(days=random.randint(0, max(0, (bis - von).days)))


def monatsanfang(d):
    return datetime(d.year, d.month, 1)


def jahre(d, n):
    return d.replace(year=d.year + n, day=min(d.day, 28))


def euro(x):
    return round(x, 2)


def ja(p):
    return random.random() < p


# ---------------------------------------------------------------- Personen

def neuer_mieter(mieter, nachname=None, firma=False):
    """Mieter sind polymorph: Privatpersonen und Firmen haben unterschiedliche Felder."""
    if firma:
        name = fake.company()
        doc = {
            "_id": ObjectId(),
            "art": "firma",
            "firmenname": name,
            "ansprechpartner": fake.name(),
            "ust_id": f"DE{random.randint(100000000, 999999999)}",
            "kontakt": {"email": f"info@{slug(name)}.example", "telefon": fake.phone_number()},
            "bankverbindung": {"iban": fake.iban(), "kontoinhaber": name},
        }
    else:
        maennlich = ja(0.5)
        vorname = fake.first_name_male() if maennlich else fake.first_name_female()
        nachname = nachname or fake.last_name()
        doc = {
            "_id": ObjectId(),
            "art": "person",
            "anrede": "Herr" if maennlich else "Frau",
            "vorname": vorname,
            "nachname": nachname,
            "geburtsdatum": datum(datetime(1950, 1, 1), datetime(2003, 12, 31)),
            "kontakt": {
                "email": f"{slug(vorname)}.{slug(nachname)}@example.de",
                "telefon": fake.phone_number(),
            },
            "bankverbindung": {"iban": fake.iban(), "kontoinhaber": f"{vorname} {nachname}"},
        }
    mieter.append(doc)
    return doc


def slug(s):
    for a, b in {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", " ": "-", ".": "", ",": "", "&": "und"}.items():
        s = s.lower().replace(a, b)
    return s


def anzeigename(m):
    return m["firmenname"] if m["art"] == "firma" else f"{m['vorname']} {m['nachname']}"


# ---------------------------------------------------------------- Objekte und Einheiten

LAGEN = ["EG links", "EG rechts", "1. OG links", "1. OG rechts", "2. OG links", "2. OG rechts",
         "3. OG links", "3. OG rechts", "DG"]
NUTZUNGEN = ["Laden", "Arztpraxis", "Büro", "Friseursalon", "Bäckerei-Filiale", "Physiotherapie"]


def wohnung(nr, lage, flaeche, zimmer, kalt, nebenkosten, ausstattung):
    return {
        "einheit_id": f"W{nr:02d}",
        "typ": "wohnung",
        "lage": lage,
        "wohnflaeche": flaeche,
        "zimmer": zimmer,
        "ausstattung": ausstattung,
        "markt_kalt": euro(kalt),  # Ausgangsmiete aus Angebot bzw. davon abgeleitet
        "markt_nebenkosten": euro(nebenkosten),
    }


def neues_objekt(angebot):
    a = angebot
    typ = random.choices(["Eigentumswohnung", "Mehrfamilienhaus", "Wohn- und Geschäftshaus"], [55, 35, 10])[0]
    qm_preis = a["miete"]["kalt"] / a["wohnflaeche"]
    # Fehlende oder 0-€-Nebenkosten im Angebot: übliche 2,20 €/m² ansetzen
    nk = a["miete"].get("nebenkosten") or a["wohnflaeche"] * 2.2
    nk_qm = nk / a["wohnflaeche"]

    # Erste Wohnung: unverändert aus dem echten Angebot
    einheiten = [wohnung(1, LAGEN[max(0, min(int(a.get("etage", 1)) * 2, 8))] if typ != "Eigentumswohnung" else "Wohnung",
                         a["wohnflaeche"], a["zimmer"], a["miete"]["kalt"], nk, a.get("ausstattung", []))]

    if typ != "Eigentumswohnung":
        # Weitere Wohnungen im selben Haus: aus der echten Wohnung abgeleitet
        lagen = [l for l in LAGEN if l != einheiten[0]["lage"]]
        for nr in range(2, random.randint(3, 8) + 1):
            flaeche = round(a["wohnflaeche"] * random.uniform(0.7, 1.3))
            zimmer = max(1.0, round(flaeche / 28 * 2) / 2)
            einheiten.append(wohnung(nr, lagen[nr - 2], float(flaeche), zimmer,
                                     flaeche * qm_preis * random.uniform(0.92, 1.08), flaeche * nk_qm,
                                     a.get("ausstattung", [])))
        for nr in range(1, random.randint(0, 4) + 1):
            if ja(0.6):
                einheiten.append({"einheit_id": f"G{nr:02d}", "typ": "garage", "nummer": nr,
                                  "e_ladestation": ja(0.25), "markt_kalt": float(random.choice([60, 70, 80, 90, 100]))})
            else:
                einheiten.append({"einheit_id": f"S{nr:02d}", "typ": "stellplatz", "nummer": nr,
                                  "ueberdacht": ja(0.4), "markt_kalt": float(random.choice([30, 40, 50]))})
    if typ == "Wohn- und Geschäftshaus":
        flaeche = float(random.randint(45, 180))
        einheiten.insert(0, {"einheit_id": "L01", "typ": "gewerbe", "lage": "EG",
                             "nutzung": random.choice(NUTZUNGEN), "nutzflaeche": flaeche,
                             "umsatzsteuer_option": ja(0.7), "markt_kalt": euro(flaeche * random.uniform(11, 18))})

    strasse = f"{a['adresse']['strasse']} {a['adresse']['hausnummer']}"
    return {
        "_id": ObjectId(),
        "bezeichnung": strasse if typ == "Eigentumswohnung" else f"{'MFH' if typ == 'Mehrfamilienhaus' else 'WGH'} {strasse}",
        "typ": typ,
        "adresse": a["adresse"],
        "baujahr": a.get("baujahr"),
        "energie": a.get("energie"),
        "eigentuemer": {"name": fake.name(), "telefon": fake.phone_number()},
        "verwaltet_seit": monatsanfang(datum(datetime(2010, 1, 1), datetime(2022, 12, 31))),
        "quelle": {"angebot_id": a["_id"], "erfasst": a["erfasst"]},
        "einheiten": einheiten,
    }


# ---------------------------------------------------------------- Übergabeprotokolle

MAENGEL = ["Dübellöcher in der Wand", "Kratzer im Bodenbelag", "Silikonfuge verfärbt", "Türklinke locker",
           "Wandfarbe vergilbt", "Fliese gesprungen", "Fensterdichtung porös", "Steckdose ohne Abdeckung"]


def raeume(einheit):
    namen = ["Wohnzimmer", "Schlafzimmer", "Kinderzimmer", "Arbeitszimmer", "Gästezimmer"][: max(1, int(einheit["zimmer"]))]
    namen += ["Küche", "Bad", "Flur"]
    if "balkon" in einheit.get("ausstattung", []):
        namen.append("Balkon")
    return namen


def uebergabe(art, tag, einheit, zaehler_basis, abnutzung):
    protokoll_raeume = []
    for name in raeume(einheit):
        maengel = random.sample(MAENGEL, k=random.choices([0, 1, 2], [70 - abnutzung, 20 + abnutzung // 2, 10 + abnutzung // 2])[0])
        protokoll_raeume.append({
            "name": name,
            "zustand": "einwandfrei" if not maengel else ("Gebrauchsspuren" if len(maengel) == 1 else "mangelhaft"),
            **({"maengel": maengel} if maengel else {}),
        })
    schluessel = [{"art": "Haustür", "anzahl": 2}, {"art": "Wohnungstür", "anzahl": random.choice([2, 3])},
                  {"art": "Briefkasten", "anzahl": 2}]
    if "keller" in einheit.get("ausstattung", []):
        schluessel.append({"art": "Keller", "anzahl": 1})
    return {
        "art": art,
        "datum": tag,
        "zaehlerstaende": [
            {"art": "Strom", "einheit": "kWh", "stand": round(zaehler_basis * 2.1)},
            {"art": "Kaltwasser", "einheit": "m³", "stand": round(zaehler_basis * 0.035, 1)},
            {"art": "Warmwasser", "einheit": "m³", "stand": round(zaehler_basis * 0.015, 1)},
        ],
        "schluessel": schluessel,
        "raeume": protokoll_raeume,
    }


# ---------------------------------------------------------------- Verträge

def vertragsart(einheit):
    if einheit["typ"] != "wohnung":
        return "unbefristet"
    return random.choices(["unbefristet", "staffel", "index"], [60, 25, 15])[0]


def mietverlauf(art, beginn, ende, kalt_start):
    """Mietanpassungen bis Vertragsende bzw. heute; liefert Historie und aktuelle Kaltmiete."""
    anpassungen, kalt, d = [], kalt_start, beginn
    schritt = 3 if art == "unbefristet" else 1
    while True:
        d = jahre(d, schritt)
        if d >= min(ende or HEUTE, HEUTE):
            break
        if art == "unbefristet" and not ja(0.6):
            continue
        faktor = {"unbefristet": random.uniform(1.05, 1.15), "staffel": random.uniform(1.02, 1.03),
                  "index": random.uniform(1.015, 1.07)}[art]
        neu = euro(kalt * faktor)
        anpassungen.append({
            "gueltig_ab": monatsanfang(d),
            "alt": kalt,
            "neu": neu,
            "grund": {"unbefristet": "Anpassung an die ortsübliche Vergleichsmiete (§ 558 BGB)",
                      "staffel": "Staffelmiete (§ 557a BGB)",
                      "index": "Indexmiete, Anpassung an den Verbraucherpreisindex (§ 557b BGB)"}[art],
        })
        kalt = neu
    return anpassungen, kalt


def vertraege_fuer_einheit(objekt, einheit, mieter, aktive_mieter_im_haus):
    """Vertragshistorie einer Einheit von Verwaltungsbeginn bis heute."""
    vertraege = []
    beginn = objekt["verwaltet_seit"]
    leerstand_am_ende = ja(0.05)
    zaehler = random.randint(1000, 5000)
    dauer_jahre = {"wohnung": (2, 9), "gewerbe": (5, 12), "garage": (3, 14), "stellplatz": (3, 14)}[einheit["typ"]]

    while beginn < HEUTE:
        ende = monatsanfang(jahre(beginn, random.randint(*dauer_jahre)) + timedelta(days=random.randint(0, 300)))
        ist_letzter = ende >= HEUTE
        if ist_letzter and leerstand_am_ende:
            break
        art = vertragsart(einheit)
        alter = (HEUTE - beginn).days / 365
        kalt_start = euro(einheit["markt_kalt"] / (1.025 ** alter))

        # Mieter: Familien/Paare, Firmen für Gewerbe, Garagen oft an Mieter aus dem Haus
        if einheit["typ"] == "gewerbe":
            vertragsmieter = [neuer_mieter(mieter, firma=True)]
        elif einheit["typ"] in ("garage", "stellplatz") and aktive_mieter_im_haus and ja(0.5):
            vertragsmieter = [random.choice(aktive_mieter_im_haus)]
        else:
            erster = neuer_mieter(mieter)
            vertragsmieter = [erster]
            if einheit["typ"] == "wohnung" and ja(0.3):
                vertragsmieter.append(neuer_mieter(mieter, nachname=erster["nachname"] if ja(0.6) else None))

        anpassungen, kalt_aktuell = mietverlauf(art, beginn, None if ist_letzter else ende, kalt_start)

        if ist_letzter:
            gekuendigt = ja(0.04)
            status = "gekuendigt" if gekuendigt else "aktiv"
            vertragsende = monatsanfang(HEUTE + timedelta(days=random.randint(40, 100))) if gekuendigt else None
        else:
            status, vertragsende = "beendet", ende

        vertrag = {
            "_id": ObjectId(),
            "nummer": f"V-{beginn.year}-{random.randint(1000, 9999)}",
            "objekt_id": objekt["_id"],
            "einheit_id": einheit["einheit_id"],
            "einheit_typ": einheit["typ"],
            "mieter_ids": [m["_id"] for m in vertragsmieter],
            # Bewusste Denormalisierung: Namen zum Vertragszeitpunkt, erspart Lookups in Listen
            "mieter_namen": [anzeigename(m) for m in vertragsmieter],
            "objekt_bezeichnung": objekt["bezeichnung"],
            "art": art,
            "status": status,
            "beginn": beginn,
            "miete": {
                "kalt": kalt_aktuell,
                "kalt_bei_beginn": kalt_start,
                "nebenkosten_vorauszahlung": euro(einheit.get("markt_nebenkosten", 0) * random.uniform(0.9, 1.1)),
            },
            "mietanpassungen": anpassungen,
        }
        if vertragsende:
            vertrag["ende"] = vertragsende
        if einheit["typ"] == "wohnung":
            vertrag["kaution"] = {"betrag": euro(kalt_start * 3),
                                  "art": random.choices(["Barkaution", "Kautionskonto", "Bürgschaft"], [30, 55, 15])[0]}
            abnutzung = min(40, int((min(ende, HEUTE) - beginn).days / 365 * 5))
            vertrag["uebergaben"] = [uebergabe("einzug", beginn, einheit, zaehler, 0)]
            zaehler += int((min(ende, HEUTE) - beginn).days * random.uniform(5, 9))
            if status == "beendet":
                vertrag["uebergaben"].append(uebergabe("auszug", ende - timedelta(days=1), einheit, zaehler, abnutzung))
        if einheit["typ"] == "gewerbe" and einheit.get("umsatzsteuer_option"):
            vertrag["miete"]["umsatzsteuer_prozent"] = 19

        vertraege.append(vertrag)
        beginn = monatsanfang(ende + timedelta(days=random.choice([0, 0, 0, 31, 62])))
    return vertraege


# ---------------------------------------------------------------- Vorgänge

SCHAEDEN = [
    # kategorie, gewerk, prioritaet, kosten von/bis, versicherungsfall möglich, minderung möglich
    ("Wasserschaden", "Sanitär", "hoch", 800, 9000, True, True),
    ("Heizungsausfall", "Heizungsbau", "hoch", 200, 3500, False, True),
    ("Schimmelbefall", "Maler/Gutachter", "mittel", 400, 4000, False, True),
    ("Rauchmelder defekt", "Elektro", "mittel", 30, 120, False, False),
    ("Fenster undicht", "Fensterbau", "mittel", 150, 1800, False, False),
    ("Rollladen defekt", "Rollladenbau", "niedrig", 80, 600, False, False),
    ("Abfluss verstopft", "Sanitär", "mittel", 90, 450, False, False),
    ("Sturmschaden am Dach", "Dachdecker", "hoch", 600, 12000, True, False),
    ("Klingel/Gegensprechanlage defekt", "Elektro", "niedrig", 80, 900, False, False),
    ("Aufzug ausgefallen", "Aufzugsservice", "hoch", 300, 5000, False, True),
]
KORRESPONDENZ = [
    ("ein", "E-Mail", "Frage zur Nebenkostenabrechnung"),
    ("ein", "E-Mail", "Anfrage Untervermietung"),
    ("ein", "Telefon", "Beschwerde über Lärm im Haus"),
    ("ein", "Brief", "Änderung der Bankverbindung"),
    ("aus", "Brief", "Ankündigung Treppenhausrenovierung"),
    ("aus", "E-Mail", "Termin Ablesung Heizkostenverteiler"),
    ("aus", "Brief", "Erinnerung Hausordnung Mülltrennung"),
    ("ein", "E-Mail", "Anfrage Haustierhaltung"),
]


def status_nach_alter(tag):
    tage = (HEUTE - tag).days
    if tage > 120:
        return "erledigt"
    if tage > 30:
        return random.choices(["erledigt", "in_bearbeitung"], [70, 30])[0]
    return random.choices(["offen", "in_bearbeitung", "erledigt"], [50, 30, 20])[0]


def verlauf(tag, status, eintraege):
    out, d = [{"datum": tag, "eintrag": "Vorgang angelegt"}], tag
    for e in eintraege:
        d = d + timedelta(days=random.randint(1, 10))
        if d > HEUTE:
            break
        out.append({"datum": d, "eintrag": e})
    if status == "erledigt":
        out.append({"datum": min(HEUTE, d + timedelta(days=random.randint(1, 14))), "eintrag": "Vorgang abgeschlossen"})
    return out


def basis(typ, objekt, tag, vertrag=None, einheit_id=None):
    doc = {"_id": ObjectId(), "typ": typ, "objekt_id": objekt["_id"], "datum": tag, "status": status_nach_alter(tag)}
    if vertrag:
        doc["vertrag_id"] = vertrag["_id"]
        doc["einheit_id"] = vertrag["einheit_id"]
    elif einheit_id:
        doc["einheit_id"] = einheit_id
    return doc


def vorgaenge_fuer_objekt(objekt, vertraege):
    out = []
    wohn_vertraege = [v for v in vertraege if v["einheit_typ"] in ("wohnung", "gewerbe")]
    hat_aufzug = any("aufzug" in e.get("ausstattung", []) for e in objekt["einheiten"])

    for v in wohn_vertraege:
        laufzeit_ende = min(v.get("ende", HEUTE), HEUTE)
        jahre_laufzeit = max(0.2, (laufzeit_ende - v["beginn"]).days / 365)

        # Schäden: im Schnitt etwa einer pro Wohnung alle zwei Jahre, dazu aktuelle in laufenden Verträgen
        tage = [datum(v["beginn"], laufzeit_ende)
                for _ in range(int(jahre_laufzeit * random.uniform(0.2, 0.8)) + (1 if ja(0.3) else 0))]
        if v["status"] != "beendet" and ja(0.1):
            tage.append(datum(HEUTE - timedelta(days=45), HEUTE))
        for tag in tage:
            kat, gewerk, prio, kmin, kmax, versicherung, minderung = random.choice(
                [s for s in SCHAEDEN if hat_aufzug or s[0] != "Aufzug ausgefallen"])
            doc = basis("schaden", objekt, tag, v)
            schaetzung = euro(random.uniform(kmin, kmax))
            doc.update({
                "titel": kat,
                "kategorie": kat,
                "gewerk": gewerk,
                "prioritaet": prio,
                "gemeldet_von": v["mieter_ids"][0],
                "handwerker": {"firma": f"{fake.last_name()} {gewerk.split('/')[0]} GmbH", "telefon": fake.phone_number()},
                "kosten": {"schaetzung": schaetzung},
                "verlauf": verlauf(tag, doc["status"], ["Handwerker beauftragt", "Termin mit Mieter vereinbart",
                                                        "Reparatur durchgeführt"]),
            })
            if doc["status"] == "erledigt":
                doc["kosten"]["rechnung"] = euro(schaetzung * random.uniform(0.8, 1.3))
            if versicherung and ja(0.7):
                doc["versicherungsfall"] = {"versicherer": "Wohngebäudeversicherung",
                                            "schadennummer": f"S-{random.randint(100000, 999999)}"}
            out.append(doc)

            # Mietminderung als eigener Vorgang, verweist auf den auslösenden Schaden
            if minderung and ja(0.35):
                m = basis("mietminderung", objekt, tag + timedelta(days=random.randint(1, 7)), v)
                m.update({"titel": f"Mietminderung wegen {kat}", "schaden_id": doc["_id"],
                          "quote_prozent": random.choice([10, 15, 20, 25, 30]),
                          "zeitraum": {"von": tag, "bis": tag + timedelta(days=random.randint(14, 120))},
                          "einigung": random.choice(["anerkannt", "Vergleich", "abgelehnt", "offen"])})
                out.append(m)

        # Korrespondenz
        for _ in range(int(jahre_laufzeit * random.uniform(0.3, 1.2))):
            richtung, kanal, betreff = random.choice(KORRESPONDENZ)
            tag = datum(v["beginn"], laufzeit_ende)
            doc = basis("korrespondenz", objekt, tag, v)
            doc.update({"titel": betreff, "richtung": richtung, "kanal": kanal,
                        "partner": v["mieter_namen"][0]})
            out.append(doc)

        # Mieterhöhungsverlangen nach § 558 BGB für jede Anpassung an die Vergleichsmiete
        for a in v["mietanpassungen"]:
            if v["art"] != "unbefristet":
                continue
            tag = a["gueltig_ab"] - timedelta(days=random.randint(95, 120))
            doc = basis("mieterhoehung", objekt, tag, v)
            doc.update({"titel": "Mieterhöhungsverlangen", "status": "erledigt",
                        "alt": a["alt"], "neu": a["neu"], "gueltig_ab": a["gueltig_ab"],
                        "zustimmung": random.choices(["erteilt", "nach Rücksprache erteilt", "teilweise"], [80, 15, 5])[0]})
            out.append(doc)

        # Kündigung bei beendeten oder gekündigten Verträgen
        if "ende" in v:
            tag = v["ende"] - timedelta(days=random.randint(90, 100))
            doc = basis("kuendigung", objekt, tag, v)
            doc.update({"titel": "Kündigung des Mietvertrags", "zum": v["ende"],
                        "durch": random.choices(["Mieter", "Vermieter"], [90, 10])[0],
                        "status": "erledigt" if v["status"] == "beendet" else "in_bearbeitung"})
            out.append(doc)

    # Wiederkehrende Fristen je Objekt (die nächsten Fälligkeiten)
    for titel, monat in [("Nebenkostenabrechnung erstellen", 12), ("Rauchmelderwartung", 3), ("Heizungswartung", 9)]:
        faellig = datetime(HEUTE.year + (monat < HEUTE.month), monat, 28 if monat != 12 else 31)
        doc = basis("frist", objekt, faellig - timedelta(days=60))
        doc.update({"titel": titel, "faellig_am": faellig, "wiederkehrend": "jährlich", "status": "offen"})
        out.append(doc)
    return out


# ---------------------------------------------------------------- Schema-Validierung

VALIDATOREN = {
    "objekte": {"$jsonSchema": {
        "bsonType": "object",
        "required": ["bezeichnung", "typ", "adresse", "einheiten"],
        "properties": {
            "typ": {"enum": ["Eigentumswohnung", "Mehrfamilienhaus", "Wohn- und Geschäftshaus"]},
            "einheiten": {"bsonType": "array", "minItems": 1, "items": {
                "bsonType": "object",
                "required": ["einheit_id", "typ"],
                "properties": {"typ": {"enum": ["wohnung", "gewerbe", "garage", "stellplatz"]},
                               "status": {"enum": ["vermietet", "leer"]}},
            }},
        },
    }},
    "mieter": {"$jsonSchema": {
        "bsonType": "object",
        "required": ["art", "kontakt"],
        # Polymorphie mit Regeln: Personen brauchen Namen, Firmen einen Firmennamen
        "oneOf": [
            {"properties": {"art": {"enum": ["person"]}}, "required": ["vorname", "nachname"]},
            {"properties": {"art": {"enum": ["firma"]}}, "required": ["firmenname"]},
        ],
    }},
    "vertraege": {"$jsonSchema": {
        "bsonType": "object",
        "required": ["objekt_id", "einheit_id", "mieter_ids", "status", "beginn", "miete"],
        "properties": {
            "mieter_ids": {"bsonType": "array", "minItems": 1, "items": {"bsonType": "objectId"}},
            "status": {"enum": ["aktiv", "gekuendigt", "beendet"]},
            "beginn": {"bsonType": "date"},
            "miete": {"bsonType": "object", "required": ["kalt"],
                      "properties": {"kalt": {"bsonType": ["double", "int"], "minimum": 0}}},
        },
    }},
    "vorgaenge": {"$jsonSchema": {
        "bsonType": "object",
        "required": ["typ", "objekt_id", "datum", "status", "titel"],
        "properties": {
            "typ": {"enum": ["schaden", "mietminderung", "korrespondenz", "mieterhoehung", "kuendigung", "frist"]},
            "status": {"enum": ["offen", "in_bearbeitung", "erledigt"]},
            "datum": {"bsonType": "date"},
        },
    }},
}


def collections_anlegen():
    for name, validator in VALIDATOREN.items():
        db.drop_collection(name)
        db.create_collection(name, validator=validator)

    db.objekte.create_index("einheiten.typ")
    db.mieter.create_index("nachname")
    db.vertraege.create_index([("objekt_id", ASCENDING), ("einheit_id", ASCENDING), ("beginn", DESCENDING)])
    db.vertraege.create_index("mieter_ids")  # Multikey-Index über das Array
    db.vertraege.create_index("status")
    db.vorgaenge.create_index([("objekt_id", ASCENDING), ("status", ASCENDING), ("datum", DESCENDING)])
    db.vorgaenge.create_index([("typ", ASCENDING), ("status", ASCENDING)])


# ---------------------------------------------------------------- Ablauf

def main():
    # Bestand: echte Angebote mit vollständiger Adresse aus der Region, je Adresse nur eines
    kandidaten = list(db.angebote.find(
        # Hausnummer muss eine Ziffer enthalten (Platzhalter wie "--" ausschließen)
        {"adresse.kreis": {"$in": KREISE}, "adresse.hausnummer": {"$regex": "[0-9]"}},
        sort=[("_id", ASCENDING)],
    ))
    gesehen, eindeutig = set(), []
    for a in kandidaten:
        key = (a["adresse"]["plz"], a["adresse"]["strasse"], a["adresse"]["hausnummer"])
        if key not in gesehen:
            gesehen.add(key)
            eindeutig.append(a)
    auswahl = random.sample(eindeutig, ANZAHL_OBJEKTE)

    objekte, mieter, vertraege, vorgaenge = [], [], [], []
    for a in auswahl:
        objekt = neues_objekt(a)
        objekt_vertraege = []
        # Wohnungen zuerst, damit Garagen an bestehende Mieter im Haus gehen können
        for einheit in sorted(objekt["einheiten"], key=lambda e: e["typ"] in ("garage", "stellplatz")):
            aktive_im_haus = [m for v in objekt_vertraege if v["status"] != "beendet" and v["einheit_typ"] == "wohnung"
                              for m in mieter if m["_id"] in v["mieter_ids"]]
            ev = vertraege_fuer_einheit(objekt, einheit, mieter, aktive_im_haus)
            objekt_vertraege += ev
            aktuell = next((v for v in ev if v["status"] != "beendet"), None)
            # Denormalisierter Status an der Einheit, wird per Transaktion mit dem Vertrag gepflegt
            einheit["status"] = "vermietet" if aktuell else "leer"
            if aktuell:
                einheit["aktueller_vertrag_id"] = aktuell["_id"]
        objekte.append(objekt)
        vertraege += objekt_vertraege
        vorgaenge += vorgaenge_fuer_objekt(objekt, objekt_vertraege)

    collections_anlegen()
    db.objekte.insert_many(objekte)
    db.mieter.insert_many(mieter)
    db.vertraege.insert_many(vertraege)
    for i in range(0, len(vorgaenge), 5000):
        db.vorgaenge.insert_many(vorgaenge[i : i + 5000])

    einheiten = sum(len(o["einheiten"]) for o in objekte)
    print(f"objekte {len(objekte)} (einheiten {einheiten}), mieter {len(mieter)}, "
          f"vertraege {len(vertraege)}, vorgaenge {len(vorgaenge)}")


if __name__ == "__main__":
    main()
