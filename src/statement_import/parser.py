"""Parse Wealthsimple CSV and structured descriptions without accessing services."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

ACTIVITIES = {'TRFIN': 'Contribution', 'EFT': 'Contribution', 'CONT': 'Contribution', 'DIV': 'Dividends',
              'BUY': 'Buy', 'NRT': 'Withholding Tax', 'FEE': 'Service Fee', 'REIMB': 'ETF Rebate'}
STOCK = re.compile(r'^([A-Za-z0-9][A-Za-z0-9.\-]*)\s*-\s*(.+?):\s*(.*)$')
BUY = re.compile(r'\bBought\s+([\d,]+(?:\.\d+)?)\s+shares\s+at\s+\$\s*([\d,]+(?:\.\d+)?)\s+per\s+share\b', re.I)
FX = re.compile(r'FX\s+Rate:\s*([\d,]+(?:\.\d+)?)', re.I)


def decimal(value, label, places=None):
    try:
        number = Decimal(value.replace(',', ''))
        if not number.is_finite() or number < 0:
            raise InvalidOperation()
        if places is not None and number != number.quantize(Decimal(1).scaleb(-places)):
            raise ValueError('{} exceeds {} decimal places'.format(label, places))
        return number
    except InvalidOperation:
        raise ValueError('{} must be a nonnegative number'.format(label))


def parse_transaction(transaction_date, code, description, total):
    """Interpret one validated CSV row; no PDF/text row reconstruction."""
    date.fromisoformat(transaction_date)
    if code in ('ROC', 'NCDIS'):
        return 'skipped', {'date': transaction_date, 'code': code, 'reason': '{} is an adjusted-cost-base-only entry, excluded from current cash/purchase tracking.'.format(code)}
    if code not in ACTIVITIES:
        raise ValueError('Unsupported activity {}; add an explicit parsing rule first'.format(code))
    if total <= 0:
        raise ValueError('Transaction amount must be positive')
    entry = {'transactionDate': transaction_date, 'activity': ACTIVITIES[code], 'total': str(total),
             'totalCurrency': 'CAD', 'description': description}
    stock = STOCK.match(description)
    if code in ('BUY', 'DIV') and not stock:
        raise ValueError('Expected ticker - stock name: description')
    if stock and code in ('BUY', 'DIV', 'NRT'):
        entry['ticker'], entry['stockName'] = stock.group(1).upper(), stock.group(2).strip()
        if FX.search(description):
            entry['stockCurrency'] = 'USD'
        elif code == 'BUY':
            entry['stockCurrency'] = 'CAD'
        # Dividends/tax often omit FX; resolve their stock currency from metadata.
    if code == 'BUY':
        trade = BUY.search(stock.group(3))
        if not trade:
            raise ValueError('Expected Bought <shares> shares at $<price> per share')
        shares = decimal(trade.group(1), 'Shares', 8)
        price = decimal(trade.group(2), 'Price', 8)
        fx = FX.search(description)
        if re.search(r'FX\s+Rate', description, re.I) and not fx:
            raise ValueError('FX Rate is present but its value cannot be parsed')
        rate = decimal(fx.group(1), 'Exchange rate', 8) if fx else Decimal(1)
        if shares <= 0 or price <= 0 or rate <= 0:
            raise ValueError('Shares, price, and exchange rate must be positive')
        entry.update(shares=str(shares), price=str(price), exchangeRate=str(rate), priceCurrency=entry['stockCurrency'], fee='0')
    return 'transactions', entry


def parse_statement(text):
    """Compatibility entry point for CLI/API: only CSV is accepted."""
    from .csv_parser import parse_csv_statement
    return parse_csv_statement(text.replace('\r\n', '\n').replace('\u00a0', ' '))
