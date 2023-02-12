from mongoengine import Document
from  mongoengine.fields import (
    StringField,
)

class Activities(Document):
    meta = {"collection": "activities"}
    name = StringField()
