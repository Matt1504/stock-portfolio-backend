from mongoengine import Document
from  mongoengine.fields import (
    StringField,
    ObjectIdField
)

class Activity(Document):
    meta = {"collection": "activities"}
    ID = ObjectIdField()
    name = StringField()
