from mongoengine import Document
from  mongoengine.fields import (
    StringField,
    ObjectIdField,
    ReferenceField
)
from models.currency import Currency

class Stock(Document):
    meta = {"collection": "stocks"}
    ID = ObjectIdField()
    name = StringField()
    ticker = StringField()
    currency = ReferenceField(Currency)