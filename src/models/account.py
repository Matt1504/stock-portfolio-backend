from mongoengine import Document
from  mongoengine.fields import (
    StringField,
    ObjectIdField,
)

class Account(Document):
    meta = {"collection": "accounts"}
    ID = ObjectIdField()
    name = StringField()
    code = StringField()