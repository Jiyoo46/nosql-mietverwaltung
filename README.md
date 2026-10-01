# Mietverwaltung – NoSQL-Projekt

Verwaltung von Mietimmobilien mit MongoDB (Document Store) und Redis (Key-Value-Cache).
Modul NoSQL-Datenbanken, Hochschule Reutlingen.

## Start

```bash
docker compose up -d --build
```

- App: http://localhost:8000 (API-Doku: http://localhost:8000/docs)
- Zustand Replica Set + Redis: http://localhost:8000/health
- MongoDB vom Host (z. B. Compass): `mongodb://localhost:27017/?directConnection=true`
- Redis vom Host: `redis-cli -p 6379`

## Architektur

| Dienst | Rolle |
|---|---|
| `mongo1`–`mongo3` | Replica Set `rs0` (mongo1 bevorzugter Primary) |
| `redis` | Cache für Aggregationen, 64 MB, LRU-Verdrängung |
| `app` | FastAPI (Python 3.12), Hot Reload auf `app/` |

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
| `objekte` | 250 (955 eingebettete Einheiten) | Adresse und erste Wohnung echt, weitere Einheiten abgeleitet |
| `mieter` | 2.326 | fiktiv (Personen und Firmen) |
| `vertraege` | 1.981 | fiktiv, mit eingebetteten Mietanpassungen und Übergabeprotokollen |
| `vorgaenge` | 11.078 | fiktiv, polymorph (Schaden, Mietminderung, Korrespondenz, Mieterhöhung, Kündigung, Frist) |
