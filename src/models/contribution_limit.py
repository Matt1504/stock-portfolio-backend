from mongoengine import Document
from  mongoengine.fields import (
    IntField,
    ReferenceField,
    ObjectIdField,
    DecimalField
)
from models.account import Account

class ContributionLimit(Document):
    meta = {"collection": "contributionLimits"}
    ID = ObjectIdField()
    year = IntField()
    account = ReferenceField(Account)
    amount = DecimalField()