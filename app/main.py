from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers import dashboard, labor, markt, mieter, objekte, seiten, system, vertraege, vorgaenge

app = FastAPI(title="Mietverwaltung (NoSQL-Projekt)",
              description="MongoDB-Replica-Set als Document Store, Redis als Cache.")

# Keine Indexierung und keine KI-Nutzung: gilt auch für Crawler, die robots.txt nicht lesen
KEINE_INDEXIERUNG = "noindex, nofollow, noarchive, nosnippet, noimageindex, noai, noimageai"


@app.middleware("http")
async def robots_header(request: Request, call_next):
    antwort = await call_next(request)
    antwort.headers["X-Robots-Tag"] = KEINE_INDEXIERUNG
    return antwort


@app.get("/robots.txt", include_in_schema=False)
def robots():
    """Sperrt alle Crawler, KI-Agenten und Scraper sind zusätzlich namentlich ausgeschlossen."""
    return FileResponse("app/static/robots.txt", media_type="text/plain")


app.mount("/static", StaticFiles(directory="app/static"), name="static")
for r in (seiten, system, dashboard, objekte, vertraege, mieter, vorgaenge, markt, labor):
    app.include_router(r.router)
