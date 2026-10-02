from mongoengine import Document
from  mongoengine.fields import (
    DateField,
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
    meta = {"collection": "transactions", "indexes": ["platform", ("platform", "transaction_date"), ("platform", "stock")]}
    ID = ObjectIdField()
    stock = ReferenceField(Stock)
    platform = ReferenceField(Platform)
    price = DecimalField()
    shares = DecimalField(precision=8)
    description = StringField()
    fee = DecimalField()
    transaction_date = DateField()
    activity = ReferenceField(Activity)
    account = ReferenceField(Account)
    rate = DecimalField()
    maturity_date = DateField()
    total = DecimalField()