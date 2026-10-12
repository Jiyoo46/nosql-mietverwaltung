# Mietverwaltung – NoSQL-Projekt

Verwaltung von Mietimmobilien mit MongoDB (Document Store) und Redis (Key-Value-Cache).
Modul NoSQL-Datenbanken, Hochschule Reutlingen.

Die Anwendung dient als Demonstrationsobjekt: Jede Seite zeigt neben den Daten auch die Abfrage dahinter,
und ein NoSQL-Labor führt sieben Konzepte live vor, jeweils mit Vorteil und Preis gegenüber einer
relationalen Datenbank.

> Crawling, Indexierung und die Nutzung dieses Repositorys für KI-Training oder KI-Suche sind nicht erwünscht
> (siehe [`robots.txt`](robots.txt) und [Ausschluss von Crawlern und KI-Agenten](#ausschluss-von-crawlern-und-ki-agenten)).

**Leitfrage:** Warum eignet sich ein Document Store für die Mietverwaltung, und was kostet er gegenüber
einem relationalen Datenbanksystem?

## Dokumente

| Dokument | Inhalt |
|---|---|
| [Projektdokumentation (PDF)](<docs/Mietverwaltung mit MongoDB und Redis – Projektdokumentation.pdf>) | Problemstellung, Architektur, Datenmodell, NoSQL-Konzepte mit Messwerten, Vergleich mit RDBMS, Fazit |
| [Präsentation (PPTX)](<docs/Mietverwaltung mit MongoDB und Redis.pptx>) | 10 Folien mit Sprechernotizen |

## Voraussetzungen

- Docker Desktop mit mindestens 4 GB Arbeitsspeicher
- freie Ports 8000 (App), 27017–27019 (MongoDB) und 6379 (Redis)
- der Kaggle-Datensatz (siehe unten); Python muss lokal nicht installiert sein, alles läuft im Container

## Ersteinrichtung

1. Datensatz [Apartment rental offers in Germany](https://www.kaggle.com/datasets/corrieaar/apartment-rental-offers-in-germany)
   bei Kaggle herunterladen (Kaggle-Konto nötig) und `immo_data.csv` (ca. 270 MB) nach `data/raw/` legen.
   Die Datei ist nur für Forschungszwecke freigegeben und deshalb nicht im Repository.
2. Im Projektordner starten:

   ```bash
   docker compose up -d --build
   ```

3. Datenstand herstellen (ca. 10 s; importiert die Inserate und erzeugt den Bestand):

   ```bash
   docker exec app python -m scripts.reset
   ```

Danach:

- App: http://localhost:8000 (Übersicht, Objekte, Vorgänge, Markt, NoSQL-Labor)
- API-Doku: http://localhost:8000/docs
- Zustand Replica Set + Redis: http://localhost:8000/health
- MongoDB vom Host (z. B. Compass): `mongodb://localhost:27017/?directConnection=true`
- Redis vom Host: `redis-cli -p 6379`

Stoppen mit `docker compose down`; die Daten bleiben in den Docker-Volumes erhalten.

## Architektur

| Dienst | Rolle |
|---|---|
| `mongo1`–`mongo3` | MongoDB 8.0 als Replica Set `rs0`; mongo1 ist mit Priorität 2 der bevorzugte Primary |
| `redis` | Redis 7 als Cache für Aggregationen (Cache-aside), 64 MB, LRU-Verdrängung |
| `app` | FastAPI (Python 3.12) mit Jinja2-Seiten und JSON-API, Hot Reload auf `app/`; darf über den Docker-Socket MongoDB-Container stoppen und starten (Failover-Demo) |

## Datenmodell

| Collection | Dokumente | Modellierung |
|---|---|---|
| `angebote` | 16.040 | echte Inserate (ImmoScout24, Baden-Württemberg, bereinigt); fehlende Angaben entfallen, 7 bis 20 Felder je Dokument |
| `objekte` | 250 (990 Einheiten) | Einheiten eingebettet (Wohnung, Gewerbe, Garage, Stellplatz); Adresse und erste Wohnung echt, weitere Einheiten abgeleitet |
| `mieter` | 2.409 | fiktiv (Faker), Personen und Firmen; per `oneOf` im Validator unterschieden |
| `vertraege` | 2.063 | Mietanpassungen und Übergabeprotokolle eingebettet, Mieter per `mieter_ids[]` referenziert, Mieternamen als bewusste Kopie |
| `vorgaenge` | 11.030 | eigene Collection, polymorph (Schaden, Mietminderung, Korrespondenz, Mieterhöhung, Kündigung, Frist) |

Jede Collection hat einen `$jsonSchema`-Validator. Der Generator arbeitet mit festem Seed (42): Jeder Reset
erzeugt exakt diesen Stand. Die Paketversionen in `requirements.txt` sind deshalb fest vorgegeben, denn eine
andere Faker-Version würde trotz Seed andere Daten erzeugen.

## NoSQL-Labor (`/labor`)

Sieben Live-Experimente, alle Werte werden beim Klick gemessen:

1. Failover: Primary hart beenden, Zeit bis zum neuen Primary, Schreibtest
2. Redis-Cache: Antwortzeit MongoDB gegen Redis, veraltete Daten ohne Invalidierung
3. Konsistenz: Write Concern `1`/`majority` × Read Preference `primary`/`secondary`, 50 Versuche
4. Index: `explain()` mit und ohne Index
5. Transaktion: Mieterwechsel mit simuliertem Abbruch und Rollback
6. Denormalisierung: Namensänderung mit und ohne Pflege der Kopien
7. Schema: Feldverteilung der echten Inserate, Ablehnung durch `$jsonSchema`

„Code zeigen“ klappt in jeder Karte den Quelltext auf, der beim Klick läuft, live aus den Dateien gelesen.
„Demo-Stand wiederherstellen“ startet alle Knoten und setzt Daten und Cache zurück (ca. 10 s).

## Projektstruktur

```
app/
  main.py              FastAPI-App
  db.py, cache.py      MongoDB- und Redis-Client, Cache-aside mit Invalidierung
  routers/             JSON-API (objekte, vertraege, mieter, vorgaenge, markt, dashboard),
                       HTML-Seiten (seiten), Labor-Experimente (labor), Statusabfrage (system)
  templates/, static/  Jinja2-Seiten, CSS, JavaScript, Schrift
scripts/
  import_angebote.py   Kaggle-CSV → Collection angebote
  generate.py          Objekte, Mieter, Verträge, Vorgänge; Validatoren und Indizes
  reset.py             Import + Generator + Redis leeren
data/raw/              Ablage für immo_data.csv (nicht versioniert)
docs/                  Projektdokumentation, Präsentation
docker-compose.yml     Replica Set, Redis, App
```

## Hinweise

- Das Projekt ist für den lokalen Betrieb gebaut: keine Authentifizierung, MongoDB ohne Benutzer.
- Für die Failover-Demo bindet die App den Docker-Socket ein und kann damit Container steuern.
  Lokal unkritisch, auf einem Server nicht so betreiben.
- Die Links „in VS Code öffnen“ im Labor brauchen den Projektpfad auf dem Rechner. Ihn übergibt
  `docker-compose.yml` aus `${PWD}`; dafür `docker compose up` im Projektordner im Terminal starten.
  Ohne ihn fehlen nur die Links.
- Alle Messwerte in den Dokumenten stammen aus dem lokalen Betrieb (Docker, ein Rechner) und lassen
  sich nicht auf echte Netzwerklatenz übertragen.

## Ausschluss von Crawlern und KI-Agenten

Suchmaschinen, Scraper und KI-Agenten sind ausgeschlossen. Die Regeln sperren alle Crawler und nennen
KI-Agenten und Scraper zusätzlich namentlich (u. a. GPTBot, ClaudeBot, Google-Extended, PerplexityBot,
CCBot, Bytespider).

| Ort | Umsetzung |
|---|---|
| Repository | [`robots.txt`](robots.txt) im Hauptverzeichnis |
| App: `/robots.txt` | dieselben Regeln, ausgeliefert von der App ([`app/static/robots.txt`](app/static/robots.txt)) |
| App: HTTP-Header | jede Antwort trägt `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex, noai, noimageai` (`app/main.py`) |
| App: HTML | jede Seite enthält dasselbe als `<meta name="robots">` (`app/templates/base.html`) |

## Quellen und Lizenzen

- Inserate: Kaggle, [Apartment rental offers in Germany](https://www.kaggle.com/datasets/corrieaar/apartment-rental-offers-in-germany)
  (Daten von ImmoScout24, nur für Forschungszwecke).
- Personendaten sind fiktiv (Faker); echte Mieterdaten wären auch gekürzt noch personenbezogen im Sinne der DSGVO.
- Schrift: IBM Plex Sans (SIL Open Font License, `app/static/fonts/OFL-LICENSE.txt`).
