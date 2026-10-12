# Mietverwaltung – NoSQL-Projekt

Verwaltung von Mietimmobilien mit MongoDB (Document Store) und Redis (Key-Value-Cache).
Modul NoSQL-Datenbanken, Hochschule Reutlingen.

## Start

```bash
docker compose up -d --build
```

- App: http://localhost:8000 (Übersicht, Objekte, Vorgänge, Markt, NoSQL-Labor)
- API-Doku: http://localhost:8000/docs
- Zustand Replica Set + Redis: http://localhost:8000/health
- MongoDB vom Host (z. B. Compass): `mongodb://localhost:27017/?directConnection=true`
- Redis vom Host: `redis-cli -p 6379`

## Architektur

| Dienst | Rolle |
|---|---|
| `mongo1`–`mongo3` | Replica Set `rs0` (mongo1 bevorzugter Primary) |
| `redis` | Cache für Aggregationen, 64 MB, LRU-Verdrängung |
| `app` | FastAPI (Python 3.12) mit Jinja2-Seiten, Hot Reload auf `app/`; darf über den Docker-Socket MongoDB-Container stoppen und starten (Failover-Demo) |

## NoSQL-Labor (`/labor`)

Sieben Live-Experimente, alle Werte werden beim Klick gemessen:

1. Failover: Primary hart beenden, Zeit bis zum neuen Primary, Schreibtest
2. Redis-Cache: Antwortzeit MongoDB gegen Redis, veraltete Daten ohne Invalidierung
3. Konsistenz: Write Concern `1`/`majority` × Read Preference `primary`/`secondary`, 50 Versuche
4. Index: `explain()` mit und ohne Index
5. Transaktion: Mieterwechsel mit simuliertem Abbruch und Rollback
6. Denormalisierung: Namensänderung mit und ohne Pflege der Kopien
7. Schema: Feldverteilung der echten Inserate, Ablehnung durch `$jsonSchema`

„Demo-Stand wiederherstellen“ startet alle Knoten und setzt Daten und Cache zurück (ca. 10 s).

Schrift: IBM Plex Sans (SIL Open Font License, `app/static/fonts/OFL-LICENSE.txt`).

## Daten

- `angebote`: echte Mietangebote aus dem Kaggle-Datensatz
  [Apartment rental offers in Germany](https://www.kaggle.com/datasets/corrieaar/apartment-rental-offers-in-germany)
  (ImmoScout24, nur für Forschungszwecke). `immo_data.csv` nach `data/raw/` legen, wird nicht versioniert.
- Mieter, Verträge, Vorgänge: fiktiv generiert (Faker), aufgebaut auf den echten Objekten.

Datenstand herstellen bzw. zurücksetzen (ca. 10 s, fester Seed):

```bash
docker exec app python -m scripts.reset
```

| Collection | Dokumente | Quelle |
|---|---|---|
| `angebote` | 16.040 | echt (ImmoScout24, Baden-Württemberg, bereinigt) |
| `objekte` | 250 (990 eingebettete Einheiten) | Adresse und erste Wohnung echt, weitere Einheiten abgeleitet |
| `mieter` | 2.409 | fiktiv (Personen und Firmen) |
| `vertraege` | 2.063 | fiktiv, mit eingebetteten Mietanpassungen und Übergabeprotokollen |
| `vorgaenge` | 11.030 | fiktiv, polymorph (Schaden, Mietminderung, Korrespondenz, Mieterhöhung, Kündigung, Frist) |
