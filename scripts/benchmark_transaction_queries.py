"""Read-only GraphQL benchmark; never prints credentials or transaction details."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=ROOT.parent / 'stock-portfolio')
    parser.add_argument('--profile', required=True, help='Tracked profile name')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--responses', type=Path, help='Optional local response snapshots for comparison')
    args = parser.parse_args()
    # Measure cold MongoDB execution, independently of Redis/Apollo/HTTP caches.
    os.environ['CACHE_ENABLED'] = 'false'
    from pymongo import monitoring

    class Commands(monitoring.CommandListener):
        def __init__(self):
            self.counts = Counter()
            self.elapsed_ms = 0
        def started(self, event):
            if event.command_name in ('find', 'getMore', 'aggregate', 'count', 'distinct'):
                self.counts[event.command_name] += 1
        def succeeded(self, event):
            if event.command_name in ('find', 'getMore', 'aggregate', 'count', 'distinct'):
                self.elapsed_ms += event.duration_micros / 1000
        def failed(self, event):
            pass

    commands = Commands()
    monitoring.register(commands)
    from database.database import client, DATABASE
    from models import models
    # Querying a MongoEngine model normally may ensure indexes. Disable that
    # for this benchmark so all live operations remain read-only.
    from mongoengine import Document
    for model in vars(models).values():
        if isinstance(model, type) and issubclass(model, Document):
            model._meta['auto_create_index'] = False
    from schemas.schema import schema
    db = client[DATABASE]
    profile = db.profiles.find_one({'name': args.profile}, {'_id': 1})
    if not profile:
        raise SystemExit('Profile not found')
    profile_id = profile['_id']
    fhsa = db.accounts.find_one({'code': 'FHSA'}, {'_id': 1})['_id']
    platforms = list(db.platforms.find({'profile': profile_id}))
    small = next(p for p in platforms if p['account'] == fhsa and p['name'] == 'TD Multi-Holding')
    medium = next(p for p in platforms if p['account'] == fhsa and p['name'] == 'Wealthsimple')
    stock = db.stocks.find_one({'ticker': 'XEQT'}, {'_id': 1})
    scopes = [('FHSA TD Multi-Holding', 'platform', str(small['_id'])),
              ('FHSA Wealthsimple', 'platform', str(medium['_id'])),
              ('XEQT', 'stock', str(stock['_id']))]
    def query_for(kind):
        file = args.frontend / ('src/views/MyStocksView/gql.tsx' if kind == 'stock' else 'src/views/AccountView/gql.tsx')
        name = 'TRANSACTIONS_BY_STOCK' if kind == 'stock' else 'TRANSACTIONS_BY_PLATFORM'
        return re.search(r'export const ' + name + r' = gql\(`(.*?)`\);', file.read_text(), re.S).group(1)
    results, responses = [], {}
    # Connect before timing to keep DNS/TLS startup distinct from query work.
    client.admin.command('ping')
    for label, kind, record_id in scopes:
        commands.counts.clear()
        commands.elapsed_ms = 0
        started = time.perf_counter()
        response = schema.execute(query_for(kind), variables={
            'profileId': str(profile_id), 'stock' if kind == 'stock' else 'platform_one': record_id,
        })
        elapsed = (time.perf_counter() - started) * 1000
        if response.errors:
            raise RuntimeError('GraphQL query failed: ' + '; '.join(str(e) for e in response.errors))
        payload = json.dumps(response.data, separators=(',', ':')).encode()
        row = {'scope': label, 'rows': len(response.data['transactions']),
               'graphql_ms': round(elapsed, 2), 'mongo_commands': sum(commands.counts.values()),
               'mongo_ms': round(commands.elapsed_ms, 2), 'commands': dict(commands.counts),
               'response_bytes': len(payload)}
        results.append(row)
        responses[label] = response.data
        print(json.dumps(row), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + '\n')
    if args.responses:
        args.responses.parent.mkdir(parents=True, exist_ok=True)
        args.responses.write_text(json.dumps(responses))
    client.close()


if __name__ == '__main__':
    main()
