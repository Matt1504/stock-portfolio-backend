from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Stock(Document):
    meta = {"collection": "stocks"}
    name = StringField()
    ticker = StringField()