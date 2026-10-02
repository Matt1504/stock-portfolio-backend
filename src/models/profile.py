from mongoengine import Document, StringField


class Profile(Document):
    meta = {"collection": "profiles"}
    name = StringField(required=True, max_length=100)
