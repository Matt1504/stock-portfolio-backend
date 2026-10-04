import json

from database.database import client, DATABASE
from models.models import (
    Asset,
    Activity,
    Account,
    Platform,
    Currency,
    Profile
)

client.drop_database(DATABASE)

def init_db():
    with open("startup.json") as f:
        data = json.load(f)
    
    profile = Profile(name="Default").save()
    for elem in data["assets"]:
        Asset(name=elem["name"]).save()

    cad = None
    usd = None 

    accounts_by_code = {}

    for elem in data["currencies"]:
        currency = Currency(name=elem["name"], code=elem["code"])
        currency.save()
        if currency.code == "CAD":
            cad = currency
        else:
            usd = currency
    
    for elem in data["accounts"]:
        account = Account(name=elem["name"], code=elem["code"], has_contribution_limit=elem.get("has_contribution_limit", True))
        account.save()
        accounts_by_code[account.code] = account
    
    for elem in data["activities"]:
        activity = Activity(name=elem["name"])
        activity.save()
    
    for elem in data["platforms"]:
        curr = cad.to_dbref()
        if elem["currency"]["code"] == "USD":
            curr = usd.to_dbref()

        acc = accounts_by_code[elem["account"]["code"]].to_dbref()
        
        platform = Platform(name=elem["name"], account=acc, currency=curr, profile=profile)
        platform.save()

init_db()