from mongoengine import Document
from  mongoengine.fields import (
    ReferenceField,
    StringField,
    ObjectIdField
)
from models.account import Account
from models.profile import Profile
from models.currency import Currency

class Platform(Document):
    meta = {"collection": "platforms", "indexes": ["profile", ("profile", "account", "currency")]}
    ID = ObjectIdField()
    name = StringField()
    account = ReferenceField(Account)
    currency = ReferenceField(Currency)
    profile = ReferenceField(Profile, required=True)
