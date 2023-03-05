from mongoengine import Document
from  mongoengine.fields import (
    StringField,
    ObjectIdField
)

class Currency(Document):
    meta = {"collection": "currencies"}
    ID = ObjectIdField()
    name = StringField()
    code = StringField()