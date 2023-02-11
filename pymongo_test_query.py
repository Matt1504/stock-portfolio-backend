# Get the database using the method we defined in pymongo_test_insert file
from pymongo_get_database import get_database
dbname = get_database()
 
# Create a new collection
collection_name = dbname["accounts"]
 
accounts = collection_name.find()
for acc in accounts:
   # This does not give a very readable output
   print(acc)