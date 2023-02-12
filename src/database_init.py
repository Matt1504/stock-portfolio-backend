import json

from database.database import client, DATABASE
from models import models

client.drop_database(DATABASE)

def init_db():
    with open("startup.json") as f:
        data = json.load(f)
        
    for elem in data["currencies"]:
        currency = models.Currencies(name=elem["name"], code=elem["code"])
        currency.save()
    
    for elem in data["accounts"]:
        account = models.Accounts(name=elem["name"], code=elem["code"])
        account.save()
    
    for elem in data["platforms"]:
        curr = elem["currency"]
        acc = elem["account"]
        platform = models.Platforms(name=elem["name"], account=acc, currency=curr)
        platform.save()

    for elem in data["activities"]:
        activity = models.Activities(name=elem["name"])
        activity.save()

init_db()