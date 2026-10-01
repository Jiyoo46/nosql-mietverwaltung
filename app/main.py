from fastapi import FastAPI

from app.db import mongo, redis

app = FastAPI(title="Mietverwaltung (NoSQL-Projekt)")


@app.get("/health")
def health():
    """Zustand der Infrastruktur: Rolle jedes Replica-Set-Knotens und Redis-Erreichbarkeit."""
    status = mongo.admin.command("replSetGetStatus")
    members = [
        {"host": m["name"], "state": m["stateStr"], "health": m["health"]}
        for m in status["members"]
    ]
    return {
        "replica_set": status["set"],
        "members": members,
        "redis": redis.ping(),
    }
