from pymongo_get_database import get_database
dbname = get_database()
collection_name = dbname["accounts"]

item_1 = {
  "account_platform" : "CanadaLife",
  "account_type" : "RRSP",
  "currency_type" : "CAD",
}

collection_name.insert_many([item_1])