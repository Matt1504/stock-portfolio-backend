from mongoengine import Document
from  mongoengine.fields import (
    StringField,
    ObjectIdField,
    ReferenceField
)
from models.currency import Currency
from models.asset import Asset

class Stock(Document):
    meta = {"collection": "stocks"}
    ID = ObjectIdField()
    name = StringField()
    ticker = StringField()
    currency = ReferenceField(Currency)
    asset = ReferenceField(Asset, db_field="asset_id")