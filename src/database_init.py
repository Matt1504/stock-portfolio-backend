import json

from database.database import client, DATABASE
from models import models

client.drop_database(DATABASE)

def init_db():
    with open("startup.json") as f:
        data = json.load(f)

    print(data)
    
    cad = None
    usd = None 

    tfsa = None
    rrsp = None 

    for elem in data["currencies"]:
        currency = models.Currencies(name=elem["name"], code=elem["code"])
        currency.save()
        if currency.code == "CAD":
            cad = currency
        else:
            usd = currency
    
    for elem in data["accounts"]:
        account = models.Accounts(name=elem["name"], code=elem["code"])
        account.save()
        if account.code == "TFSA":
            tfsa = account
        else:
            rrsp = account
    
    for elem in data["activities"]:
        activity = models.Activities(name=elem["name"])
        activity.save()
    
    for elem in data["platforms"]:
        curr = cad
        if elem["currency"]["code"] == "USD":
            curr = usd

        acc = tfsa
        if elem["account"]["code"] == "RRSP":
            acc = rrsp
        
        platform = models.Platforms(name=elem["name"], account=acc, currency=curr)
        platform.save()

init_db()