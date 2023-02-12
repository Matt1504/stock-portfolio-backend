from mongoengine import connect
from database import passwords

DATABASE = "stock_portfolio"

client = connect(
    DATABASE,
    host=f"mongodb+srv://{passwords.USER}:{passwords.PASSWORD}@{passwords.CLUSTER}.mongodb.net/?ssl=true",
    alias="default"
)
