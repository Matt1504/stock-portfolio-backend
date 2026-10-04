from mongoengine import Document
from mongoengine.fields import StringField


class Asset(Document):
    meta = {"collection": "assets"}
    name = StringField(required=True, unique=True)
