from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.cache import SCHALTER_CACHE, SCHALTER_INVALIDIERUNG, schalter
from app.db import mongo, redis

router = APIRouter(tags=["System"])


@router.get("/health")
def health():
    """Zustand der Infrastruktur: Rolle jedes Replica-Set-Knotens und Redis-Erreichbarkeit."""
    status = mongo.admin.command("replSetGetStatus")
    members = [
        {"host": m["name"], "state": m["stateStr"], "health": m["health"]}
        for m in status["members"]
    ]
    return {"replica_set": status["set"], "members": members, "redis": redis.ping()}


@router.get("/api/cache")
def cache_status():
    keys = sorted(redis.scan_iter(match="*:*"))
    return {
        "cache": schalter(SCHALTER_CACHE),
        "invalidierung": schalter(SCHALTER_INVALIDIERUNG),
        "keys": [{"key": k, "ttl_s": redis.ttl(k)} for k in keys if not k.startswith("einstellung:")],
        "speicher": redis.info("memory")["used_memory_human"],
    }


class Schalter(BaseModel):
    name: Literal["cache", "invalidierung"]
    an: bool


@router.put("/api/cache/schalter")
def cache_schalter(body: Schalter):
    key = SCHALTER_CACHE if body.name == "cache" else SCHALTER_INVALIDIERUNG
    redis.set(key, "an" if body.an else "aus")
    return cache_status()


@router.delete("/api/cache")
def cache_leeren():
    keys = [k for k in redis.scan_iter(match="*:*") if not k.startswith("einstellung:")]
    return {"geloescht": redis.delete(*keys) if keys else 0}
