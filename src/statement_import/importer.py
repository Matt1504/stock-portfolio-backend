"""Preview and apply parsed statement rows using the public GraphQL API only."""
import json
from collections import Counter
from decimal import Decimal
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class GraphQLClient:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def execute(self, query, variables=None):
        request = Request(self.endpoint, data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
                          headers={'Content-Type': 'application/json', 'X-Cache-Bypass': 'true'})
        try:
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            # Never automatically retry a mutation: it may already have committed.
            raise RuntimeError('API request failed; verify saved records before rerunning: {}'.format(error))
        if result.get('errors'):
            raise RuntimeError('; '.join(error['message'] for error in result['errors']))
        if not result.get('data'):
            raise RuntimeError('API returned no data')
        return result['data']


def nodes(connection):
    if connection.get('pageInfo', {}).get('hasNextPage'):
        raise ValueError('API metadata is paginated; refusing an incomplete stock/platform lookup')
    return [edge['node'] for edge in connection['edges']]


def choose(records, value, label):
    matches = [record for record in records if record['id'] == value or record.get('name', '').casefold() == value.casefold()]
    if len(matches) != 1:
        raise ValueError('Select an unambiguous {} by ID or name. Available: {}'.format(label, ', '.join('{} ({})'.format(r.get('name'), r['id']) for r in records)))
    return matches[0]


def numeric(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.00000001'))


def economic_key(entry):
    return (entry['transactionDate'][:10], entry['activity'], (entry.get('ticker') or '').upper() or None,
            numeric(entry.get('total')), numeric(entry.get('price')), numeric(entry.get('shares')), numeric(entry.get('fee')))


def exact_key(entry):
    return economic_key(entry) + (entry.get('priceCurrency', 'CAD'), entry.get('totalCurrency', 'CAD'), numeric(entry.get('exchangeRate', 1)))


def prepare_import(client, parsed, profile, platform, account_code='NRSA'):
    if parsed['errors']:
        raise ValueError('Fix all parsing errors before preparing an import')
    data = client.execute('''{ profiles { edges { node { id name } } pageInfo { hasNextPage } }
      activities { edges { node { id name } } pageInfo { hasNextPage } }
      stocks { edges { node { id name ticker asset { name } currency { id code } } } pageInfo { hasNextPage } }
      currencies { edges { node { id code } } pageInfo { hasNextPage } }
      assets { edges { node { id name } } pageInfo { hasNextPage } } }''')
    owner = choose(nodes(data['profiles']), profile, 'profile')
    platform_data = client.execute('''query($profile: ID!) { platforms(profileId: $profile) {
      edges { node { id name account { id code } currency { id code } } } pageInfo { hasNextPage } } }''', {'profile': owner['id']})
    candidates = [p for p in nodes(platform_data['platforms']) if p['account']['code'] == account_code and p['currency']['code'] == 'CAD']
    broker = choose(candidates, platform, 'CAD {} platform'.format(account_code))
    history = client.execute('''query($profile: ID!, $platform: ID!) { transactionsByPlatform(profileId: $profile, platform: $platform) {
      id transactionDate total price shares fee exchangeRate priceCurrency { code } totalCurrency { code }
      stock { ticker asset { name } } activity { name } } }''', {'profile': owner['id'], 'platform': broker['id']})['transactionsByPlatform']
    existing = []
    for row in history:
        existing.append(dict(row, activity=row['activity']['name'], ticker=(row.get('stock') or {}).get('ticker'),
                             priceCurrency=(row.get('priceCurrency') or {}).get('code', 'CAD'),
                             totalCurrency=(row.get('totalCurrency') or {}).get('code', 'CAD'), exchangeRate=row.get('exchangeRate') or 1))
    counts = Counter(exact_key(row) for row in existing)
    economics = set(economic_key(row) for row in existing)
    exacts = set(counts)
    stocks = nodes(data['stocks'])
    quoted_currencies = {entry['ticker']: entry['stockCurrency'] for entry in parsed['transactions'] if entry.get('ticker') and entry.get('stockCurrency')}
    activities = {a['name']: a['id'] for a in nodes(data['activities'])}
    currencies = {c['code']: c['id'] for c in nodes(data['currencies'])}
    asset = choose(nodes(data['assets']), 'Stock', 'asset type')
    plan = {'profile': owner, 'platform': broker, 'rows': [], 'stocksToCreate': [], 'skipped': parsed['skipped'], 'errors': []}
    missing = {}
    for entry in parsed['transactions']:
        entry = dict(entry)
        try:
            if entry['activity'] not in activities:
                raise ValueError('Activity {} is missing; run its setup script first'.format(entry['activity']))
            if counts[exact_key(entry)] > 0:
                counts[exact_key(entry)] -= 1
                plan['rows'].append({'entry': entry, 'status': 'existing'})
                continue
            if economic_key(entry) in economics and exact_key(entry) not in exacts:
                raise ValueError('Possible existing transaction with different FX/currency metadata; review it before importing')
            if entry.get('stockInferred') and any(row['activity'] == 'Withholding Tax' and not row.get('ticker') and economic_key(row)[:2] + economic_key(row)[3:] == economic_key(entry)[:2] + economic_key(entry)[3:] for row in existing):
                raise ValueError('Possible existing unlinked withholding tax for this dividend; review/edit it before importing')
            ticker = entry.get('ticker')
            stock = None
            if ticker:
                matches = [s for s in stocks if (s.get('ticker') or '').upper() == ticker]
                if len(matches) > 1:
                    raise ValueError('Ticker {} matches multiple stocks; resolve the ambiguity first'.format(ticker))
                stock = matches[0] if matches else None
                if not entry.get('stockCurrency'):
                    entry['stockCurrency'] = (stock.get('currency') or {}).get('code') if stock else quoted_currencies.get(ticker, 'CAD')
                if stock:
                    if not stock.get('currency') or stock['currency']['code'] != entry['stockCurrency']:
                        raise ValueError('Stock {} currency conflicts with the statement'.format(ticker))
                    if (stock.get('asset') or {}).get('name') == 'GIC':
                        raise ValueError('GIC statements require a separate parsing rule')
                    if (stock.get('asset') or {}).get('name') in ('Index Fund', 'Mutual Fund') and any(((r.get('stock') or {}).get('ticker') or '').upper() == ticker and r['activity']['name'] in ('Buy', 'Sell') and not r.get('shares') for r in history):
                        raise ValueError('Fund {} has amount-only history; cannot mix share-based imports'.format(ticker))
                elif ticker not in missing:
                    if any(s['name'].casefold() == entry['stockName'].casefold() for s in stocks) or any(s['name'].casefold() == entry['stockName'].casefold() for s in missing.values()):
                        raise ValueError('Stock name already belongs to another ticker; resolve it before importing')
                    missing[ticker] = {'name': entry['stockName'], 'ticker': ticker, 'currency': currencies[entry['stockCurrency']], 'assetId': asset['id']}
                elif missing[ticker]['currency'] != currencies[entry['stockCurrency']]:
                    raise ValueError('Conflicting currencies for new stock {}'.format(ticker))
            payload = {'account': broker['account']['id'], 'platform': broker['id'], 'activity': activities[entry['activity']],
                       'transactionDate': entry['transactionDate'], 'total': entry['total'], 'totalCurrency': currencies['CAD']}
            if stock:
                payload['stock'] = stock['id']
            for field in ('price', 'shares', 'fee', 'exchangeRate'):
                if field in entry:
                    payload[field] = entry[field]
            if 'priceCurrency' in entry:
                payload['priceCurrency'] = currencies[entry['priceCurrency']]
            plan['rows'].append({'entry': entry, 'status': 'pending', 'payload': payload})
        except (ValueError, KeyError) as error:
            plan['errors'].append({'row': entry['row'], 'message': str(error)})
    plan['stocksToCreate'] = list(missing.values())
    plan['summary'] = {'transactionsToCreate': sum(row['status'] == 'pending' for row in plan['rows']),
                       'existingTransactions': sum(row['status'] == 'existing' for row in plan['rows']),
                       'stocksToCreate': len(missing), 'skippedRows': len(plan['skipped'])}
    return plan


def apply_import(client, plan, progress=None):
    """Sequential writes with an audit report; stop at the first error, no retries."""
    if plan['errors']:
        raise ValueError('Fix all preview errors before importing')
    report = {'profile': plan['profile'], 'platform': plan['platform'], 'createdStocks': [], 'createdTransactions': [], 'existingRows': [r['entry']['row'] for r in plan['rows'] if r['status'] == 'existing'],
              'skipped': plan['skipped'], 'errors': [], 'complete': False}
    def save():
        if progress:
            progress(report)
    save()
    stock_ids = {}
    try:
        for stock in plan['stocksToCreate']:
            report['inFlight'] = {'operation': 'createStock', 'ticker': stock['ticker']}
            save()
            created = client.execute('''mutation($input: StockInput!) { createStock(stockData: $input) { stock { id ticker } } }''', {'input': stock})['createStock']
            if not created or not created.get('stock'):
                raise RuntimeError('Stock was not created; refresh metadata and review the existing stock name')
            stock_ids[stock['ticker']] = created['stock']['id']
            report['createdStocks'].append(created['stock'])
            report.pop('inFlight', None)
            save()
        for row in plan['rows']:
            if row['status'] != 'pending':
                continue
            payload = dict(row['payload'])
            if row['entry'].get('ticker') in stock_ids:
                payload['stock'] = stock_ids[row['entry']['ticker']]
            report['inFlight'] = {'operation': 'createTransaction', 'row': row['entry']['row']}
            save()
            created = client.execute('''mutation($profile: ID!, $input: TransactionInput!) {
              createTransaction(profileId: $profile, transData: $input) { transaction { id } warnings { code message } } }''', {'profile': plan['profile']['id'], 'input': payload})['createTransaction']
            if not created or not created.get('transaction'):
                raise RuntimeError('Transaction was not saved')
            report['createdTransactions'].append({'row': row['entry']['row'], 'id': created['transaction']['id'], 'warnings': created.get('warnings', [])})
            report.pop('inFlight', None)
            save()
        report['complete'] = True
    except Exception as error:
        report['errors'].append(str(error))
    save()
    return report
