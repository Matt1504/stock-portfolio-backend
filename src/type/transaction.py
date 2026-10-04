from type.custom_node import CustomNode as Node
from graphene import Float, Field
from type.currency import CurrencyType
from type.stock import StockType
from graphene_mongo import MongoengineObjectType

from models.models import Transaction as TransactionModel 

class TransactionType(MongoengineObjectType):
    spinoff_source = Field(StockType)
    allocated_book_cost = Float()
    expected_maturity_total = Float()
    price_currency = Field(CurrencyType)
    total_currency = Field(CurrencyType)
    exchange_rate = Float()

    def resolve_price_currency(self, info):
        # Historical prices were entered in the platform's currency.
        return self.price_currency or self.platform.currency

    def resolve_total_currency(self, info):
        return self.total_currency or self.platform.currency

    def resolve_exchange_rate(self, info):
        return float(self.exchange_rate) if self.exchange_rate is not None else 1.0

    def resolve_expected_maturity_total(self, info):
        from schemas.gic import expected_payout, is_gic
        value = expected_payout(self) if is_gic(self) and self.activity and self.activity.name == "Buy" else None
        return float(value) if value is not None else None

    class Meta:
        description = "Transactions"
        model = TransactionModel
        interfaces = (Node,)
