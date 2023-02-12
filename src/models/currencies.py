from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Currencies(Document):
    meta = {"collection": "currencies"}
    name = StringField()
    code = StringField()