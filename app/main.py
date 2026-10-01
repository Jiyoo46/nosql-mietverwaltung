from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import dashboard, labor, markt, mieter, objekte, seiten, system, vertraege, vorgaenge

app = FastAPI(title="Mietverwaltung (NoSQL-Projekt)",
              description="MongoDB-Replica-Set als Document Store, Redis als Cache.")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
for r in (seiten, system, dashboard, objekte, vertraege, mieter, vorgaenge, markt, labor):
    app.include_router(r.router)
