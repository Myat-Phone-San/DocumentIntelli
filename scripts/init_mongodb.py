"""Idempotently create Atlas collections, validators, and indexes; no sample user data."""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.mongo_store import get_database, ensure_indexes


def initialize(database):
    validators = {
        "conversations": {"bsonType": "object", "required": ["_id", "anonymous_id", "title", "created_at", "updated_at", "message_count"],
            "properties": {"_id": {"bsonType": "string"}, "anonymous_id": {"bsonType": "string"},
                "title": {"bsonType": "string", "maxLength": 255}, "message_count": {"bsonType": ["int", "long"], "minimum": 0},
                "created_at": {"bsonType": "date"}, "updated_at": {"bsonType": "date"}}},
        "messages": {"bsonType": "object", "required": ["_id", "conversation_id", "sequence", "role", "content", "created_at"],
            "properties": {"_id": {"bsonType": "string"}, "conversation_id": {"bsonType": "string"},
                "sequence": {"bsonType": ["int", "long"], "minimum": 1}, "role": {"enum": ["user", "assistant"]},
                "content": {"bsonType": "string"}, "created_at": {"bsonType": "date"},
                "sources": {"bsonType": ["array", "null"]}, "retrieved": {"bsonType": ["array", "null"]},
                "follow_up": {"bsonType": ["string", "null"], "maxLength": 500}}},
    }
    existing = set(database.list_collection_names())
    for name, validator in validators.items():
        if name not in existing:
            database.create_collection(name, validator={"$jsonSchema": validator})
        else:
            database.command("collMod", name, validator={"$jsonSchema": validator}, validationLevel="strict")
    for name in ("managed_documents", "managed_chunks", "telegram_inbox", "conversation_heads",
                 "messenger_inbox", "messenger_threads", "messenger_history"):
        if name not in existing:
            database.create_collection(name)
    ensure_indexes(database)


def describe_failure(exc, stage):
    code = getattr(exc, "code", None)
    details = getattr(exc, "details", None) or {}
    server_message = str(details.get("errmsg", "")).lower()
    authentication_failure = any(term in server_message for term in
                                 ("bad auth", "authentication failed", "auth failed"))
    code_label = f", code {code}" if isinstance(code, int) else ""
    summary = f"MongoDB failed during {stage} ({type(exc).__name__}{code_label})."
    if code == 18 or (code == 8000 and authentication_failure):
        hint = ("Authentication failed. Use the Atlas DATABASE user's username and password, not your Atlas login. "
                "Check the URI password and URL-encode reserved characters. If needed, reset the database user's password.")
    elif code == 13:
        hint = ("Permission denied. Give the database user readWrite access to the database named by "
                "MONGODB_DATABASE. Existing validators may require collMod privileges; have a database administrator apply them.")
    elif code == 8000:
        hint = ("Atlas rejected the request. First check the database username/password and that the URI belongs "
                "to your cluster. This Atlas code alone does not identify the exact cause.")
    elif code in (85, 86):
        hint = "An existing index conflicts with this schema. Inspect the collection indexes; do not delete data."
    elif code == 121:
        hint = "A document does not match its collection validation rules. Check the existing schema."
    elif type(exc).__name__ == "ServerSelectionTimeoutError":
        hint = "Connection timed out. Check Atlas Network Access, cluster availability, DNS, and your network."
    else:
        hint = "Check Atlas database permissions, connection settings, and cluster availability."
    return summary + "\n" + hint


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-connection", action="store_true", help="Only check authentication/connectivity; do not change collections.")
    arguments = parser.parse_args()
    stage = "connection/authentication"
    try:
        database = get_database()
        if arguments.check_connection:
            print("MongoDB connection and authentication succeeded. No collections were changed.")
        else:
            stage = "collection and index initialization"
            initialize(database)
            print("MongoDB collections, validators, and indexes are ready. No sample messages were inserted.")
    except Exception as exc:
        # Never print exception bodies: they may contain URI credentials.
        print(describe_failure(exc, stage))
        raise SystemExit(1) from None
