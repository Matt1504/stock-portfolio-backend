from mongoengine import Document
from  mongoengine.fields import (
    DateField,
    ReferenceField,
    ObjectIdField,
    DecimalField
)
from models.account import Account

class ContributionLimit(Document):
    meta = {"collection": "contributionLimits"}
    ID = ObjectIdField()
    yearEnd = DateField()
    account = ReferenceField(Account)
    amount = DecimalField()