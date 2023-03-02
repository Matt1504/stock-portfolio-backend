from mongoengine import Document
from  mongoengine.fields import (
    DateTimeField,
    IntField,
    DecimalField,
)
from models.stock import Stock
from models.platform import Platform
from models.activity import Activity

class Transaction(Document):
    meta = {"collection": "transactions"}
    stock = Stock
    platform = Platform
    price = DecimalField()
    shares = IntField()
    fee = DecimalField()
    transaction_date = DateTimeField()
    activity = Activity
    total = DecimalField()