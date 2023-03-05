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
    Currency
)
from type.stock import StockType 

class StockInput(InputObjectType):
    id = String()
    name = String()
    ticker = String()
    currency = String()

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
            currency=stock_data.currency
        ) 
        stock.save()

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

        stock.save()
    
        return UpdateStockMutation(stock=stock)

class DeleteStockMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id):
        try:
            Stock.objects.get(pk=id).delete()
            success = True
        except Exception:
            success = False
    
        return DeleteStockMutation(success=success)