from mongoengine import Document
from  mongoengine.fields import (
    DateTimeField,
    IntField,
    DecimalField,
    ObjectIdField
)
from models.models import Stock
from models.platform import Platform
from models.activity import Activity

class Transaction(Document):
    meta = {"collection": "transactions"}
    ID = ObjectIdField()
    stock = Stock
    platform = Platform
    price = DecimalField()
    shares = IntField()
    fee = DecimalField()
    transaction_date = DateTimeField()
    activity = Activity
    total = DecimalField()