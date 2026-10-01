from fastapi import FastAPI

from app.routers import dashboard, markt, mieter, objekte, system, vertraege, vorgaenge

app = FastAPI(title="Mietverwaltung (NoSQL-Projekt)",
              description="MongoDB-Replica-Set als Document Store, Redis als Cache.")

for r in (system, dashboard, objekte, vertraege, mieter, vorgaenge, markt):
    app.include_router(r.router)
