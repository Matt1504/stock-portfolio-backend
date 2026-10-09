"""Reviewed Yahoo closing-price backfill for linked asset transfers only.

Run with the market-data dependencies and configured MongoDB connection:
  python scripts/backfill_transfer_market_values.py preview --profile ID --plan /private/plan.json
  python scripts/backfill_transfer_market_values.py apply --profile ID --plan /private/plan.json
The plan is a private before-image backup. Apply rechecks it inside a MongoDB
transaction and never edits book costs, cash, dates, shares, or activity.
"""
import argparse
import hashlib
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bson import ObjectId, json_util
from database.database import client
from models.models import Transaction, Platform, Activity
from query_loading import materialize_references
from market_data.quotes import mapping
from cache.queries import invalidate_transactions

FIELDS = {'transfer_market_value', 'transfer_market_value_source', 'transfer_market_price'}


def fingerprint(rows):
    return hashlib.sha256(json_util.dumps(sorted(rows, key=lambda r: str(r['_id'])), sort_keys=True).encode()).hexdigest()


def raw_transfers(profile, session=None):
    platforms = [p['_id'] for p in Platform._get_collection().find({'profile': ObjectId(profile)}, session=session)]
    activities = [a.id for a in Activity.objects(name__in=['Transfer In', 'Transfer Out'])]
    return list(Transaction._get_collection().find({'platform': {'$in': platforms}, 'activity': {'$in': activities},
        'stock': {'$ne': None}, 'transfer_pair': {'$ne': None}}, session=session))


def preview(profile, path):
    import yfinance as yf
    rows = raw_transfers(profile)
    objects = materialize_references(Transaction._from_son(r) for r in rows)
    pairs, quotes, updates = {}, {}, []
    for row in objects:
        pairs.setdefault(row.transfer_pair, []).append(row)
    for pair, legs in sorted(pairs.items()):
        assert len(legs) == 2, 'Incomplete transfer pair: ' + pair
        a, b = legs
        assert {a.activity.name, b.activity.name} == {'Transfer In', 'Transfer Out'}
        assert a.stock.id == b.stock.id and a.shares == b.shares and a.total == b.total
        assert a.transaction_date == b.transaction_date and a.platform.currency.id == b.platform.currency.id
        assert a.transfer_counterparty and b.transfer_counterparty
        assert a.transfer_counterparty.id == b.platform.id and b.transfer_counterparty.id == a.platform.id
        # Leave previously valued pairs alone; reject partial backfills.
        if any(r.transfer_market_value is not None for r in legs):
            assert all(r.transfer_market_value is not None for r in legs)
            assert a.transfer_market_value == b.transfer_market_value
            continue
        assert a.shares and a.shares > 0, 'Amount-only transfers require a manual valuation.'
        spec = mapping(a.stock)
        assert spec and spec[2] == a.platform.currency.code, 'Foreign/unsupported assets require a manual valuation.'
        key = (spec[0], a.transaction_date)
        if key not in quotes:
            bars = yf.Ticker(spec[0]).history(start=a.transaction_date.isoformat(),
                end=(a.transaction_date + timedelta(days=1)).isoformat(), auto_adjust=False, actions=False)
            exact = [(i, r) for i, r in bars.iterrows() if i.date() == a.transaction_date]
            assert len(exact) == 1, 'No exact transfer-date close: ' + str(key)
            # Remove binary floating-point noise; preserve prices to four decimals.
            close = Decimal(str(round(float(exact[0][1]['Close']), 4)))
            assert close.is_finite() and close > 0
            quotes[key] = close
        close = quotes[key]
        total = (close * a.shares).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        updates.append({'pair': pair, 'ids': [r.id for r in legs], 'ticker': a.stock.ticker,
            'symbol': spec[0], 'date': a.transaction_date.isoformat(), 'currency': spec[2],
            'shares': str(a.shares), 'book_cost': str(a.total), 'closing_price': str(close), 'market_value': str(total)})
    plan = {'profile': profile, 'created_at': datetime.now(timezone.utc), 'before': rows,
        'fingerprint': fingerprint(rows), 'updates': updates, 'source': 'Yahoo Finance daily Close (auto_adjust=False)'}
    with path.open('x') as f:
        path.chmod(0o600)
        f.write(json_util.dumps(plan, indent=2))
    print(json_util.dumps({'plan': str(path), 'updates': updates}, indent=2))


def apply(profile, path):
    plan = json_util.loads(path.read_text())
    assert plan['profile'] == profile
    assert fingerprint(plan['before']) == plan['fingerprint']
    collection = Transaction._get_collection()
    db = Transaction._get_db()
    def write(session):
        current = raw_transfers(profile, session)
        assert fingerprint(current) == plan['fingerprint'], 'Transfers changed since preview; create a new plan.'
        before = {r['_id']: r for r in current}
        for item in plan['updates']:
            for rid in item['ids']:
                row = before[rid]
                assert row['transfer_pair'] == item['pair'] and row.get('transfer_market_value') is None
                result = collection.update_one({'_id': rid, 'transfer_market_value': None}, {'$set': {
                    'transfer_market_value': Transaction._fields['transfer_market_value'].to_mongo(Decimal(item['market_value'])),
                    'transfer_market_value_source': 'yahoo_close',
                    'transfer_market_price': Transaction._fields['transfer_market_price'].to_mongo(Decimal(item['closing_price']))}}, session=session)
                assert result.modified_count == 1
        after = raw_transfers(profile, session)
        stripped = [{k: v for k, v in r.items() if k not in FIELDS} for r in after]
        original = [{k: v for k, v in r.items() if k not in FIELDS} for r in current]
        assert fingerprint(stripped) == fingerprint(original), 'Ledger fields changed unexpectedly.'
        db.migration_journal.insert_one({'_id': 'transfer-market-values:' + plan['fingerprint'],
            'profile': ObjectId(profile), 'at': datetime.now(timezone.utc), 'pairs': len(plan['updates']),
            'source': plan['source']}, session=session)
    with db.client.start_session() as session:
        session.with_transaction(write)
    invalidate_transactions()
    print('Backfilled {} transfer pairs ({} rows); all ledger fields preserved.'.format(len(plan['updates']), len(plan['updates']) * 2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preview', 'apply'])
    parser.add_argument('--profile', required=True)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    (preview if args.mode == 'preview' else apply)(args.profile, args.plan)
