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
    # Optional provider mapping; CAD defaults to <ticker>.TO, USD to <ticker>.
    market_symbol = StringField()
    market_exchange = StringField(choices=("XTSE", "XNYS"))
    currency = ReferenceField(Currency)
    asset = ReferenceField(Asset, db_field="asset_id")
