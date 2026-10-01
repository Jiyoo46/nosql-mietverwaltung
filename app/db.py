from pymongo import MongoClient
from redis import Redis

from app.config import settings

# Ein Client je Prozess: PyMongo verwaltet Verbindungspool und Replica-Set-Topologie selbst.
# 15 s Wartezeit überbrückt eine Primary-Wahl (ca. 10–12 s), Anfragen warten statt zu scheitern.
mongo = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=15000)
db = mongo[settings.mongo_db]

redis = Redis.from_url(settings.redis_url, decode_responses=True)
