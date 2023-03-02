from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Activity(Document):
    meta = {"collection": "activities"}
    name = StringField()
