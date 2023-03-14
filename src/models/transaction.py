from mongoengine import Document
from  mongoengine.fields import (
    DateField,
    IntField,
    DecimalField,
    StringField,
    ReferenceField,
    ObjectIdField
)
from models.stock import Stock
from models.platform import Platform
from models.activity import Activity
from models.account import Account

class Transaction(Document):
    meta = {"collection": "transactions"}
    ID = ObjectIdField()
    stock = ReferenceField(Stock)
    platform = ReferenceField(Platform)
    price = DecimalField()
    shares = IntField()
    description = StringField()
    fee = DecimalField()
    transaction_date = DateField()
    activity = ReferenceField(Activity)
    account = ReferenceField(Account)
    total = DecimalField()