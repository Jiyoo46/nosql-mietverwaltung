from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from pymongo import ReadPreference
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app.cache import SCHALTER_CACHE, SCHALTER_INVALIDIERUNG, schalter
from app.db import mongo, redis

router = APIRouter(tags=["System"])


def replica_status():
    """Rolle je Knoten. NEAREST: funktioniert auch, solange gerade kein Primary existiert."""
    status = mongo.admin.command("replSetGetStatus", read_preference=ReadPreference.NEAREST)
    return [{"host": m["name"].split(":")[0], "state": m["stateStr"], "health": m["health"]}
            for m in status["members"]]


@router.get("/health")
def health():
    """Zustand der Infrastruktur, wird von der Statusleiste alle 2 s abgefragt."""
    try:
        members = replica_status()
    except PyMongoError as e:
        members = [{"host": h, "state": "UNBEKANNT", "health": 0} for h in ("mongo1", "mongo2", "mongo3")]
        members[0]["fehler"] = str(e)[:200]
    try:
        redis_ok = redis.ping()
    except RedisError:
        redis_ok = False
    return {"replica_set": "rs0", "members": members, "redis": redis_ok}


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
