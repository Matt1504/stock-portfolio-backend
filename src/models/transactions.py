from mongoengine import Document
from  mongoengine.fields import (
    DateTimeField,
    StringField,
    IntField,
    DecimalField,
)
from models.stocks import Stocks
from models.platforms import Platforms
from models.activities import Activities

class Transactions(Document):
    meta = {"collection": "transactions"}
    stock = Stocks
    platform = Platforms
    price = DecimalField()
    shares = IntField()
    fee = DecimalField()
    transaction_date = DateTimeField()
    activity = StringField
    total = DecimalField()