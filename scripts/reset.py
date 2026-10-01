"""Setzt die Datenbank auf den festen Demo-Stand zurück und leert den Redis-Cache.

Aufruf:  docker exec app python -m scripts.reset
"""
from app.db import redis
from scripts import generate, import_angebote

import_angebote.main()
generate.main()
redis.flushdb()
print("Redis-Cache geleert")
