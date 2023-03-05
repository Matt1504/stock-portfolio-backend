import json

from database.database import client, DATABASE
from models.models import (
    Activity,
    Account,
    Platform,
    Currency
)

client.drop_database(DATABASE)

def init_db():
    with open("startup.json") as f:
        data = json.load(f)
    
    cad = None
    usd = None 

    tfsa = None
    rrsp = None 

    for elem in data["currencies"]:
        currency = Currency(name=elem["name"], code=elem["code"])
        currency.save()
        if currency.code == "CAD":
            cad = currency
        else:
            usd = currency
    
    for elem in data["accounts"]:
        account = Account(name=elem["name"], code=elem["code"])
        account.save()
        if account.code == "TFSA":
            tfsa = account
        else:
            rrsp = account
    
    for elem in data["activities"]:
        activity = Activity(name=elem["name"])
        activity.save()
    
    for elem in data["platforms"]:
        curr = cad.to_dbref()
        if elem["currency"]["code"] == "USD":
            curr = usd.to_dbref()

        acc = tfsa.to_dbref()
        if elem["account"]["code"] == "RRSP":
            acc = rrsp.to_dbref()
        
        platform = Platform(name=elem["name"], account=acc, currency=curr)
        platform.save()

init_db()