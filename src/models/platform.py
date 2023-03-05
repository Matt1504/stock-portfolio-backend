from mongoengine import Document
from  mongoengine.fields import (
    ReferenceField,
    StringField,
    ObjectIdField
)
from models.account import Account
from models.currency import Currency

class Platform(Document):
    meta = {"collection": "platforms"}
    ID = ObjectIdField()
    name = StringField()
    account = ReferenceField(Account)
    currency = ReferenceField(Currency)