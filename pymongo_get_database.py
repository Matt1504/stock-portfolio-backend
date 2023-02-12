from pymongo import MongoClient
import src.passwords as passwords

def get_database():
 
   # Provide the mongodb atlas url to connect python to mongodb using pymongo
   CONNECTION_STRING = "mongodb+srv://{0}:{1}@{2}.mongodb.net/?retryWrites=true&w=majority".format(passwords.USER, passwords.PASSWORD, passwords.CLUSTER)
   
   # Create a connection using MongoClient. You can import MongoClient or use pymongo.MongoClient
   client = MongoClient(CONNECTION_STRING)
 
   # Create the database for our example (we will use the same database throughout the tutorial
   return client['stock_portfolio']
  
# This is added so that many files can reuse the function get_database()
if __name__ == "__main__":   
  
   # Get the database
   dbname = get_database()