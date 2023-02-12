from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Accounts(Document):
    meta = {"collection": "accounts"}
    name = StringField()
    code = StringField()