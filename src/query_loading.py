"""Load a reference graph in batches, without per-row lazy database reads."""
from collections import defaultdict

from bson import DBRef, ObjectId
from mongoengine import Document, ReferenceField


def materialize_references(queryset):
    # A comprehension avoids QuerySet.__len__ issuing a separate count query.
    documents = [document for document in queryset]
    known = {(type(document), document.id): document for document in documents}
    frontier = documents
    visited = set()
    missing = set()
    while frontier:
        pending = defaultdict(set)
        references = []
        for document in frontier:
            key = (type(document), document.id)
            if key in visited:
                continue
            visited.add(key)
            for name, field in document._fields.items():
                if not isinstance(field, ReferenceField):
                    continue
                # getattr would dereference each individual row here.
                value = document._data.get(name)
                if isinstance(value, Document):
                    key = (field.document_type, value.id)
                    known.setdefault(key, value)
                elif isinstance(value, (DBRef, ObjectId)):
                    key = (field.document_type, value.id if isinstance(value, DBRef) else value)
                else:
                    continue
                references.append((document, name, key))
                if key not in known and key not in missing:
                    pending[key[0]].add(key[1])
        for model, ids in pending.items():
            loaded = model.objects.in_bulk(list(ids))
            known.update({(model, identifier): document for identifier, document in loaded.items()})
            missing.update((model, identifier) for identifier in ids if identifier not in loaded)
        frontier = []
        for document, name, key in references:
            reference = known.get(key)
            if reference is not None:
                # Reuse one object per reference and traverse its nested fields
                # in the next batch (stock.asset, platform.currency, GIC buy...).
                document._data[name] = reference
                if key not in visited:
                    frontier.append(reference)
            # Leave dangling references intact: normal GraphQL resolution still
            # reports the existing missing-reference error instead of hiding it.
    return documents
