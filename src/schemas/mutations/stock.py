from graphene import (
    InputObjectType, 
    ID, 
    Mutation, 
    Field, 
    String,
    Boolean,
)
from models.models import (
    Stock,
    Asset,
)
from type.stock import StockType 
from cache.queries import invalidate_transactions
from graphql import GraphQLError

def stock_asset(asset_id):
    if asset_id:
        asset = Asset.objects(pk=asset_id).first()
        if not asset:
            raise GraphQLError("Asset type does not exist.")
        return asset
    return Asset.objects(name="Stock").modify(upsert=True, new=True, set_on_insert__name="Stock")


class StockInput(InputObjectType):
    id = ID()
    name = String()
    ticker = String()
    currency = ID()
    asset_id = ID()
    market_symbol = String()
    market_exchange = String()

class CreateStockMutation(Mutation):
    stock = Field(StockType)

    class Arguments:
        stock_data = StockInput(required=True)

    def mutate(self, info, stock_data=None):
        if Stock.objects.filter(name=stock_data.name):
            return
        stock = Stock(
            name=stock_data.name,
            ticker=stock_data.ticker,
            currency=stock_data.currency,
            asset=stock_asset(stock_data.asset_id),
            market_symbol=stock_data.market_symbol,
            market_exchange=stock_data.market_exchange,
        ) 
        stock.save()
        invalidate_transactions()

        return CreateStockMutation(stock=stock)

class UpdateStockMutation(Mutation):
    stock = Field(StockType)

    class Arguments:
        stock_data = StockInput(required=True)

    def mutate(self, info, stock_data=None):
        stock = Stock.objects.get(pk=stock_data.id)
        if (stock_data.name):
            stock.name = stock_data.name
        if (stock_data.ticker):
            stock.ticker = stock_data.ticker
        if (stock_data.currency):
            stock.currency = stock_data.currency

        if stock_data.asset_id:
            stock.asset = stock_asset(stock_data.asset_id)
        for field in ('market_symbol', 'market_exchange'):
            if field in stock_data:
                setattr(stock, field, stock_data[field] or None)
        stock.save()
        invalidate_transactions()
    
        return UpdateStockMutation(stock=stock)

class DeleteStockMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id):
        from mongoengine import Q
        from models.models import Transaction
        from graphql import GraphQLError
        if Transaction.objects(Q(stock=id) | Q(spinoff_source=id), spinoff_source__ne=None).first():
            raise GraphQLError("Remove linked spinoff transactions before deleting this stock.")
        try:
            Stock.objects.get(pk=id).delete()
            invalidate_transactions()
            success = True
        except Exception:
            success = False
    
        return DeleteStockMutation(success=success)
