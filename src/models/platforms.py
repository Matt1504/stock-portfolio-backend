from mongoengine import Document
from  mongoengine.fields import (
    ReferenceField,
    StringField,
)
from models.accounts import Accounts
from models.currencies import Currencies

class Platforms(Document):
    meta = {"collection": "platforms"}
    name = StringField()
    account = ReferenceField(Accounts)
    currency = ReferenceField(Currencies)