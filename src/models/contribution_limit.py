from mongoengine import Document
from  mongoengine.fields import (
    DateField,
    ReferenceField,
    ObjectIdField,
    DecimalField
)
from models.account import Account
from models.profile import Profile

class ContributionLimit(Document):
    meta = {"collection": "contributionLimits", "indexes": [("profile", "account")]}
    ID = ObjectIdField()
    yearEnd = DateField()
    account = ReferenceField(Account)
    amount = DecimalField()
    profile = ReferenceField(Profile, required=True)
