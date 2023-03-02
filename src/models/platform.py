from mongoengine import Document
from  mongoengine.fields import (
    ReferenceField,
    StringField,
)
from models.account import Account
from models.currency import Currency

class Platform(Document):
    meta = {"collection": "platforms"}
    name = StringField()
    account = ReferenceField(Account)
    currency = ReferenceField(Currency)