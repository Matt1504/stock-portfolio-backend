from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Account(Document):
    meta = {"collection": "accounts"}
    name = StringField()
    code = StringField()