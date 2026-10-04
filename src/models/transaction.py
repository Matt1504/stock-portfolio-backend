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
from models.currency import Currency

class Transaction(Document):
    meta = {"collection": "transactions", "indexes": ["platform", ("platform", "transaction_date"), ("platform", "stock"), {"fields": ["gic_purchase"], "unique": True, "partialFilterExpression": {"gic_purchase": {"$type": "objectId"}}}]}
    ID = ObjectIdField()
    stock = ReferenceField(Stock)
    spinoff_source = ReferenceField(Stock)
    allocated_book_cost = DecimalField()
    platform = ReferenceField(Platform)
    price = DecimalField(precision=8)
    price_currency = ReferenceField(Currency)
    total_currency = ReferenceField(Currency)
    exchange_rate = DecimalField(precision=8)
    shares = DecimalField(precision=8)
    # Legacy read/schema compatibility only; mutations never persist this field.
    description = StringField()
    fee = DecimalField()
    transaction_date = DateField()
    activity = ReferenceField(Activity)
    account = ReferenceField(Account)
    rate = DecimalField()
    maturity_date = DateField()
    total = DecimalField()
    gic_purchase = ReferenceField("self")
    principal_returned = DecimalField()
    interest_earned = DecimalField()
    interest_calculation = StringField(choices=("simple", "annual_compound"), default="simple")