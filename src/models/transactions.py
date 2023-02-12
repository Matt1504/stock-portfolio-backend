from mongoengine import Document
from  mongoengine.fields import (
    DateTimeField,
    ReferenceField,
    IntField,
    DecimalField,
)
from models.stocks import Stocks
from models.platforms import Platforms
from models.activities import Activities

class Transactions(Document):
    meta = {"collection": "transactions"}
    stock = ReferenceField(Stocks)
    platform = ReferenceField(Platforms)
    price = DecimalField()
    shares = IntField()
    fee = DecimalField()
    transaction_date = DateTimeField()
    activity = ReferenceField(Activities)
    total = DecimalField()