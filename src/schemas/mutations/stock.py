from graphene import (
    InputObjectType, 
    ID, 
    Mutation, 
    Field, 
    String,
    Boolean
)
from models.stock import Stock
from type.stock import StockType 

class StockInput(InputObjectType):
    name = String()
    ticker = String()

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
        ) 
        stock.save()

        return CreateStockMutation(stock=stock)

class UpdateStockMutation(Mutation):
    stock = Field(StockType)

    class Arugments:
        stock_data = StockInput(required=True)

    @staticmethod
    def get_object(id):
        return Stock.objects.get(pk=id)

    def mutate(self, info, stock_data=None):
        stock = UpdateStockMutation.get_object(stock_data.id)
        if (stock_data.name):
            stock.name = stock_data.name
        if (stock_data.ticker):
            stock.ticker = stock_data.ticker

        stock.save()
    
        return UpdateStockMutation(stock=stock)

class DeleteStockMutation(Mutation):
    class Arugments:
        id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id):
        try:
            Stock.objects.get(pk=id).delete()
            success = True
        except Exception:
            success = False
    
        return DeleteStockMutation(success=success)