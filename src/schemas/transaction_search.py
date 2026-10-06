"""Explicit, bounded transaction searches scoped to the active profile."""
import base64
import json
from datetime import datetime
from bson.errors import InvalidId
from mongoengine import Q
import graphene
from graphql import GraphQLError
from bson import ObjectId
from models.models import Transaction
from schemas.profiles import personal_records, owned_platform
from query_loading import materialize_references
from type.type import TransactionType


class TransactionSearchResult(graphene.ObjectType):
    transactions = graphene.List(TransactionType, required=True)
    next_cursor = graphene.String()


def search_transactions(profile_id, account=None, platform=None, stock=None, activity=None,
                        currency=None, start_date=None, end_date=None, first=100, after=None):
    first = 100 if first is None else first
    if not 1 <= first <= 100:
        raise GraphQLError('Search page size must be between 1 and 100.')
    if start_date and end_date and start_date > end_date:
        raise GraphQLError('Start date must be on or before end date.')
    records = personal_records(Transaction, profile_id)
    for name, value in [('account', account), ('platform', platform), ('stock', stock), ('activity', activity)]:
        if value:
            if not ObjectId.is_valid(value):
                raise GraphQLError('Invalid {} ID.'.format(name))
            records = records.filter(**{name: value})
    if platform:
        owned_platform(profile_id, platform)
    if currency:
        from models.models import Platform
        if not ObjectId.is_valid(currency):
            raise GraphQLError('Invalid currency ID.')
        platforms = personal_records(Platform, profile_id).filter(currency=currency).scalar('id')
        records = records.filter(platform__in=platforms)
    if start_date:
        records = records.filter(transaction_date__gte=start_date)
    if end_date:
        records = records.filter(transaction_date__lte=end_date)
    # Keyset pagination stays bounded and deterministic even for equal dates.
    if after:
        try:
            boundary = json.loads(base64.b64decode(after, validate=True).decode('ascii'))
            date = datetime.fromisoformat(boundary['date'])
            identifier = ObjectId(boundary['id'])
            records = records.filter(Q(transaction_date__lt=date) | Q(transaction_date=date, id__lt=identifier))
        except (ValueError, UnicodeError, TypeError, KeyError, InvalidId):
            raise GraphQLError('Invalid transaction search cursor.')
    rows = materialize_references(records.order_by('-transaction_date', '-id').limit(first + 1))
    more = len(rows) > first
    rows = rows[:first]
    cursor = base64.b64encode(json.dumps({'date': rows[-1].transaction_date.isoformat(), 'id': str(rows[-1].id)}).encode()).decode() if more else None
    return TransactionSearchResult(transactions=rows, next_cursor=cursor)
