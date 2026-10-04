from mongoengine import connect
import os
from urllib.parse import quote_plus

DATABASE = os.getenv("MONGODB_DATABASE", "stock_portfolio")


def mongodb_uri():
    uri = os.getenv("MONGODB_URI")
    if uri:
        return uri
    # Keep existing local launches working; passwords.py is excluded from images.
    try:
        from database import passwords
    except ImportError as exc:
        raise RuntimeError("Set MONGODB_URI to configure the MongoDB connection.") from exc
    return (
        f"mongodb+srv://{quote_plus(passwords.USER)}:{quote_plus(passwords.PASSWORD)}"
        f"@{passwords.CLUSTER}.mongodb.net/?ssl=true"
    )

client = connect(
    DATABASE,
    host=mongodb_uri(),
    serverSelectionTimeoutMS=5000,
    alias="default"
)
