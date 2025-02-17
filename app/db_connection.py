from pymongo import MongoClient
from datetime import datetime, timedelta

CONNECTION_STRING = "mongodb://localhost:27017"
client = MongoClient(CONNECTION_STRING)

db = client["SpotyGraph"]

def get_collection(collection_name):
    print(f"Getting collection: {collection_name}")
    try:
        return db[collection_name]
    except Exception as e:
        print(f"Error getting collection {collection_name}: {e}")
        return None

def set_collection(collection_name, data):
    print(f"Setting collection: {collection_name}")
    try:
        collection = get_collection(collection_name)
        if "_id" in data:
            collection.update_one({"_id": data["_id"]}, {"$set": data}, upsert=True)
        else:
            collection.insert_one(data)
    except Exception as e:
        print(f"Error setting collection {collection_name}: {e}")

def is_timestamp_stale(last_updated, max_age_days=1):
    current_time = datetime.utcnow()
    if not last_updated:
        return True

    try:
        if isinstance(last_updated, datetime):
            last_updated_time = last_updated
        else:
            last_updated_time = datetime.strptime(last_updated, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        print("Invalid timestamp format, treating as stale.")
        return True

    max_age = timedelta(days=max_age_days)
    # max_age = timedelta(seconds=10)
    return (current_time - last_updated_time) > max_age