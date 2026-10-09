import graphene


class MarketQuote(graphene.ObjectType):
    price = graphene.Decimal(required=True)
    currency = graphene.String(required=True)
    symbol = graphene.String(required=True)
    exchange = graphene.String(required=True)
    source = graphene.String(required=True)
    quote_time = graphene.DateTime(required=True)
    last_updated = graphene.DateTime(required=True)
    price_kind = graphene.String(required=True)
    age_seconds = graphene.Int(required=True)
    refresh_overdue = graphene.Boolean(required=True)


def resolve_market_quote(stock):
    from datetime import datetime
    from decimal import Decimal
    from market_data.quotes import read_quote
    quote = read_quote(stock)
    if quote is None:
        return None
    return MarketQuote(price=Decimal(quote['price']), currency=quote['currency'],
                       symbol=quote['symbol'], exchange=quote['exchange'], source=quote['source'],
                       quote_time=datetime.fromisoformat(quote['quote_time']),
                       last_updated=datetime.fromisoformat(quote['last_updated']),
                       price_kind=quote['price_kind'], age_seconds=quote['age_seconds'],
                       refresh_overdue=quote['refresh_overdue'])
