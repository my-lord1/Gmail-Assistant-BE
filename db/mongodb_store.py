from datetime import datetime
from pymongo import ASCENDING
from typing import Any, Optional
from pymongo.errors import DuplicateKeyError


class MongoDBStore:
    """
    A persistent key-value store.
    Each record is stored with a namespace and key, allowing you to store
    multiple users or agents memories separately.
    """

    def __init__(self, db):
        self.collection = db["agent_memory"]
        self.collection.create_index(
            [("namespace", ASCENDING), ("key", ASCENDING)],
            unique=True
        )

    def put(self, namespace: str, key: str, value: Any):
        """Insert or update a memory record (atomic, no retry loop)."""
        try:
            self.collection.update_one(
                {"namespace": namespace, "key": key},
                {
                    "$set": {
                        "value": value,
                        "updated_at": datetime.utcnow(),
                    },
                    "$setOnInsert": {"created_at": datetime.utcnow()},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            return


    def get(self, namespace: str, key: str) -> Optional[Any]:
        """Retrieve a memory record."""
        doc = self.collection.find_one({"namespace": namespace, "key": key})
        return doc.get("value") if doc else None

    def delete(self, namespace: str, key: str):
        """Delete a memory record."""
        self.collection.delete_one({"namespace": namespace, "key": key})

    def list(self, namespace: str):
        """List all memory keys within a namespace."""
        cursor = self.collection.find({"namespace": namespace}, {"key": 1, "_id": 0})
        return [doc["key"] for doc in cursor]
