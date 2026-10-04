"""Read Wealthsimple CSV using the standard library, without storing source files."""
import csv
import io
import re
from decimal import Decimal, InvalidOperation
from .parser import parse_transaction, decimal, ACTIVITIES


def parse_csv_statement(text):
    result = {'transactions': [], 'skipped': [], 'errors': []}
    try:
        records = list(csv.reader(io.StringIO(text.lstrip('\ufeff')), strict=True))
    except csv.Error as error:
        result['errors'].append({'row': 0, 'message': 'Invalid CSV: {}'.format(error)})
        return result
    records = [record for record in records if any(cell.strip() for cell in record)]
    if not records:
        result['errors'].append({'row': 0, 'message': 'CSV is empty.'})
        return result
    headers = [value.strip().lower() for value in records[0]]
    required = ('date', 'transaction', 'description', 'amount', 'balance', 'currency')
    if any(headers.count(key) != 1 for key in required) or len(headers) != len(set(headers)):
        result['errors'].append({'row': 0, 'message': 'Paste Wealthsimple CSV with date, transaction, description, amount, balance, currency headers. Headers must be unique. PDF/statement text is not supported.'})
        return result
    previous_transaction = None
    for row, record in enumerate(records[1:], 1):
        preceding = previous_transaction
        previous_transaction = None
        try:
            if len(record) != len(headers):
                raise ValueError('CSV row has {} cells; expected {}'.format(len(record), len(headers)))
            values = dict(zip(headers, record))
            code = values['transaction'].strip().upper()
            description = ' '.join(values['description'].split())
            transaction_date = values['date'].strip()
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', transaction_date):
                raise ValueError('CSV dates must use YYYY-MM-DD')
            if code not in ('ROC', 'NCDIS') and code not in ACTIVITIES:
                raise ValueError('Unsupported activity {}; add an explicit parsing rule first'.format(code))
            if code in ('ROC', 'NCDIS'):
                # ACB-only rows may use a ticker as currency; skip before amount/currency checks.
                category, item = parse_transaction(transaction_date, code, description, Decimal(0))
            else:
                if values['currency'].strip().upper() != 'CAD':
                    raise ValueError('This import format requires CAD settlement amounts; found {}'.format(values['currency']))
                raw_amount = values['amount'].strip().removeprefix('$').strip().replace(',', '')
                try:
                    amount = Decimal(raw_amount)
                except InvalidOperation:
                    raise ValueError('Amount must be a finite number')
                if not amount.is_finite():
                    raise ValueError('Amount must be a finite number')
                if code not in ('BUY', 'FEE', 'NRT') and amount < 0:
                    raise ValueError('Expected a positive amount for an incoming activity')
                total = decimal(str(abs(amount)), 'Amount', 2)
                category, item = parse_transaction(transaction_date, code, description, total)
            if category == 'transactions':
                if code == 'NRT' and not item.get('ticker') and preceding and preceding['activity'] == 'Dividends' and preceding['transactionDate'] == transaction_date and preceding.get('ticker'):
                    item.update(ticker=preceding['ticker'], stockName=preceding['stockName'], stockInferred=True, dividendRow=preceding['row'])
                    if preceding.get('stockCurrency'):
                        item['stockCurrency'] = preceding['stockCurrency']
                previous_transaction = dict(item, row=row)
            result[category].append(dict(item, row=row))
        except ValueError as error:
            result['errors'].append({'row': row, 'message': str(error)})
    if len(records) == 1:
        result['errors'].append({'row': 0, 'message': 'CSV has a header but no transaction rows.'})
    return result
