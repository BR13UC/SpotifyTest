from pymongo import MongoClient

CONNECTION_STRING = "mongodb://localhost:27017"
client = MongoClient(CONNECTION_STRING)

db = client["SpotyGraph"]

def get_collection(collection_name):
    print(f"Getting collection: {collection_name}")
    try:
        return db[collection_name]
    except Exception as e:
        print(f"Error getting collection: {e}")
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
        print(f"Error setting collection: {e}")

from datetime import datetime, timedelta

def is_timestamp_stale(last_updated, max_age_days=1):
    current_time = datetime.utcnow()
    if not last_updated:
        return True

    try:
        last_updated_time = datetime.strptime(last_updated, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        print("Invalid timestamp format, treating as stale.")
        return True

    max_age = timedelta(days=max_age_days)
    return (current_time - last_updated_time) > max_age
