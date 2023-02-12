from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Stocks(Document):
    meta = {"collection": "stocks"}
    name = StringField()
    ticker = StringField()