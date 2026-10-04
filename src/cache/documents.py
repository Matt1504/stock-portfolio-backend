"""BSON snapshots with materialized references, never pickled querysets."""

from bson import json_util
from bson.errors import BSONError
from mongoengine import ReferenceField
from mongoengine.errors import InvalidDocumentError, ValidationError


def snapshot(document):
    references = {}
    for name, field in document._fields.items():
        if isinstance(field, ReferenceField):
            reference = getattr(document, name)
            references[name] = snapshot(reference) if reference is not None else None
    return {"data": document.to_mongo().to_dict(), "references": references}


def restore(model, value):
    document = model._from_son(value["data"])
    for name, field in model._fields.items():
        if isinstance(field, ReferenceField):
            reference = value["references"].get(name)
            # Install real document instances so GraphQL's normal attribute
            # resolvers do not perform MongoEngine lazy dereferencing queries.
            document._data[name] = restore(field.document_type, reference) if reference is not None else None
    return document


def encode_documents(documents):
    return json_util.dumps([snapshot(document) for document in documents])


def decode_documents(model, payload):
    try:
        values = json_util.loads(payload)
        if not isinstance(values, list):
            raise ValueError("Cached documents must be a list")
        return [restore(model, value) for value in values]
    except (BSONError, InvalidDocumentError, ValidationError) as error:
        raise ValueError("Invalid document snapshot") from error
