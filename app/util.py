from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException


def jsonify(v):
    """BSON-Typen (ObjectId, datetime) rekursiv in JSON-taugliche Werte umwandeln."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonify(x) for x in v]
    return v


def oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except (InvalidId, TypeError):
        raise HTTPException(404, f"Ungültige ID: {s}")


def gefunden(doc, was="Dokument"):
    if doc is None:
        raise HTTPException(404, f"{was} nicht gefunden")
    return doc
