"""Redis als Cache-aside vor teuren MongoDB-Aggregationen.

Ablauf: Key in Redis nachsehen → Treffer zurückgeben, sonst in MongoDB berechnen und mit TTL ablegen.
Schreibende Endpunkte invalidieren die betroffenen Keys. Für die Demo lassen sich Cache und
Invalidierung über Redis-Keys abschalten, um Vorteil (Antwortzeit) und Nachteil (veraltete Daten) zu zeigen.
"""
import json
from time import perf_counter

from app.db import redis
from app.util import jsonify

SCHALTER_CACHE = "einstellung:cache"
SCHALTER_INVALIDIERUNG = "einstellung:invalidierung"


def schalter(key: str) -> bool:
    return redis.get(key) != "aus"


def cached(key: str, ttl: int, berechnen):
    """Liefert (daten, meta); meta zeigt Quelle und Dauer für die Anzeige im UI."""
    start = perf_counter()
    if schalter(SCHALTER_CACHE):
        treffer = redis.get(key)
        if treffer is not None:
            return json.loads(treffer), _meta("redis", start, key, redis.ttl(key))
    daten = jsonify(berechnen())
    if schalter(SCHALTER_CACHE):
        redis.set(key, json.dumps(daten), ex=ttl)
    return daten, _meta("mongodb", start, key, ttl)


def invalidieren(*muster: str) -> int:
    """Löscht alle Keys, die auf eines der Muster passen (z. B. 'dashboard:*')."""
    if not schalter(SCHALTER_INVALIDIERUNG):
        return 0
    keys = [k for m in muster for k in redis.scan_iter(match=m)]
    return redis.delete(*keys) if keys else 0


def _meta(quelle, start, key, ttl):
    return {"quelle": quelle, "dauer_ms": round((perf_counter() - start) * 1000, 2), "key": key, "ttl_s": ttl}
