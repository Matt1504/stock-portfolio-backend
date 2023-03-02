from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Currency(Document):
    meta = {"collection": "currencies"}
    name = StringField()
    code = StringField()