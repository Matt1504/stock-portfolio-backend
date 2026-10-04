from mongoengine import Document
from  mongoengine.fields import (
    StringField,
    BooleanField,
    ObjectIdField,
)

class Account(Document):
    meta = {"collection": "accounts"}
    ID = ObjectIdField()
    name = StringField()
    code = StringField()
    has_contribution_limit = BooleanField(default=True, required=True)