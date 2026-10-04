import unittest
import test_transaction_validation as fixtures
from models.models import Transaction, Activity, Stock, Asset
from add_stock_spinoff_activity import add_stock_spinoff_activity


class SpinoffTests(unittest.TestCase):
    record = fixtures.TransactionValidationTests.record
    mutation = fixtures.TransactionValidationTests.mutation
    tearDown = fixtures.TransactionValidationTests.tearDown
    def setUp(self):
        fixtures.TransactionValidationTests.setUp(self)
        self.activities['Stock Spinoff'] = add_stock_spinoff_activity(True)
        self.child = Stock(name='Solventum', ticker='SOLV', asset=self.stock.asset).save()

    def spinoff(self, allocation=77.60, shares=1, edit=None, **changes):
        data = {'activity': str(self.activities['Stock Spinoff'].id), 'stock': str(self.child.id),
                'spinoffSource': str(self.stock.id), 'allocatedBookCost': str(allocation),
                'shares': str(shares), 'transactionDate': '2026-01-02', 'platform': str(self.platform.id), 'total': '0'}
        data.update(changes)
        if edit: data['id'] = str(edit.id)
        operation, field = ('updateTransaction', 'trans') if edit else ('createTransaction', 'transaction')
        return __import__('schemas.schema', fromlist=['schema']).schema.execute(
            'mutation($profile: ID!, $trans: TransactionInput!) { '+operation+'(profileId: $profile, transData: $trans) { '+field+' { id shares total allocatedBookCost spinoffSource { id } } warnings { code message } } }',
            variable_values={'profile': str(self.profile.id), 'trans': data})

    def test_atomic_event_and_queries_both_stocks(self):
        self.record('Buy', 4, 500)
        result = self.spinoff()
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(Transaction.objects.count(), 2)
        record = Transaction.objects.get(id=result.data['createTransaction']['transaction']['id'])
        self.assertEqual(float(record.allocated_book_cost), 77.60)
        self.assertEqual(record.total, 0)
        from schemas.schema import schema
        for stock in (self.stock, self.child):
            query = schema.execute('query($p: ID!, $s: ID!) { transactionsByStock(profileId:$p, stock:$s) { id } }', variable_values={'p':str(self.profile.id),'s':str(stock.id)})
            self.assertIsNone(query.errors)
            self.assertIn(str(record.id), [row['id'] for row in query.data['transactionsByStock']])

    def test_source_and_cost_validation(self):
        self.assertEqual(self.spinoff().errors[0].extensions['code'], 'SPINOFF_SOURCE_NOT_OWNED')
        self.record('Buy', 4, 500)
        for values, code in [({'allocation':501}, 'INSUFFICIENT_SPINOFF_COST'), ({'allocation':-1}, 'INVALID_SPINOFF_COST'), ({'shares':0}, 'INVALID_SHARES'), ({'stock':str(self.stock.id)}, 'INVALID_SPINOFF_STOCKS'), ({'total':'10'}, 'INVALID_SPINOFF_FIELDS'), ({'platform':str(self.foreign.id)}, None)]:
            result = self.spinoff(**values)
            self.assertTrue(result.errors)
            if code: self.assertEqual(result.errors[0].extensions['code'], code)
        self.assertEqual(Transaction.objects.count(),1)

    def test_child_sale_and_editing_source_purchase(self):
        purchase = self.record('Buy', 4, 500)
        self.assertIsNone(self.spinoff().errors)
        from schemas.schema import schema
        result = schema.execute('mutation($p:ID!, $t:TransactionInput!) { createTransaction(profileId:$p, transData:$t) { transaction { id } } }', variable_values={'p':str(self.profile.id), 't':{'activity':str(self.activities['Sell'].id),'stock':str(self.child.id),'platform':str(self.platform.id),'shares':'1','total':'100','transactionDate':'2026-01-03'}})
        self.assertIsNone(result.errors, result.errors)
        result = self.mutation(edit=purchase, total=50)
        self.assertEqual(result.errors[0].extensions['code'],'INSUFFICIENT_SPINOFF_COST')
        self.assertEqual(float(Transaction.objects.get(id=purchase.id).total),500)

    def test_delete_event_protects_later_sale(self):
        self.record('Buy',4,500)
        self.spinoff()
        event = Transaction.objects(activity=self.activities['Stock Spinoff']).first()
        Transaction(activity=self.activities['Sell'],stock=self.child,platform=self.platform,account=self.account,shares=1,total=100,transaction_date=__import__('datetime').date(2026,1,3)).save()
        from schemas.schema import schema
        result = schema.execute('mutation($p:ID!,$id:ID!) { deleteTransaction(profileId:$p,id:$id) { success } }',variable_values={'p':str(self.profile.id),'id':str(event.id)})
        self.assertTrue(result.errors)
        self.assertIsNotNone(Transaction.objects(id=event.id).first())

    def test_spinoff_allows_parent_and_child_income_after_disposal(self):
        from datetime import date
        self.record('Buy', 4, 500, day='2022-07-18')
        self.record('Sell', 4, 600, day='2025-02-14')
        self.record('Dividends', total=3, day='2025-03-12')
        self.record('Withholding Tax', total=1, day='2025-03-12')
        for activity, shares, total, day in (
            ('Sell', 1, 100, '2024-12-11'),
            ('Dividends', 0, 3, '2025-03-12'),
            ('Withholding Tax', 0, 1, '2025-03-12'),
        ):
            Transaction(activity=self.activities[activity], stock=self.child,
                        platform=self.platform, account=self.account, shares=shares,
                        total=total, transaction_date=date.fromisoformat(day)).save()
        result = self.spinoff(transactionDate='2024-04-01')
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data['createTransaction']['warnings'], [])
        self.assertTrue(self.spinoff(edit=Transaction.objects(activity=self.activities['Stock Spinoff']).first(), shares='0.5', transactionDate='2024-04-01').errors)

    def test_spinoff_shares_support_sale_and_split_without_affecting_source_shares(self):
        from datetime import date
        self.record('Buy', 4, 500)
        self.assertIsNone(self.spinoff().errors)
        Transaction(activity=self.activities['Stock Split'], stock=self.child,
                    platform=self.platform, account=self.account, shares=1,
                    transaction_date=date(2026, 1, 3)).save()
        from schemas.schema import schema
        query = 'mutation($p:ID!, $t:TransactionInput!) { createTransaction(profileId:$p, transData:$t) { transaction { id } } }'
        data = {'activity':str(self.activities['Sell'].id), 'stock':str(self.child.id),
                'platform':str(self.platform.id), 'shares':'2.1', 'total':'100', 'transactionDate':'2026-01-04'}
        self.assertTrue(schema.execute(query, variable_values={'p':str(self.profile.id), 't':data}).errors)
        data['shares'] = '2'
        self.assertIsNone(schema.execute(query, variable_values={'p':str(self.profile.id), 't':data}).errors)
        self.assertIsNone(self.mutation('Sell', shares=4, day='2026-01-05').errors)

    def test_seed_is_idempotent(self):
        self.assertEqual(add_stock_spinoff_activity(True).id, self.activities['Stock Spinoff'].id)


    def test_history_edit_uses_remaining_cost_after_partial_sale(self):
        self.record('Buy', 4, 500)
        self.record('Sell', 2, 300, day='2026-01-02')
        result = self.spinoff(allocation=251, transactionDate='2026-01-03')
        self.assertEqual(result.errors[0].extensions['code'], 'INSUFFICIENT_SPINOFF_COST')
        result = self.spinoff(allocation=100, transactionDate='2026-01-03')
        self.assertIsNone(result.errors)
        event = Transaction.objects(activity=self.activities['Stock Spinoff']).first()
        result = self.spinoff(edit=event, transactionDate='2025-12-31')
        self.assertEqual(result.errors[0].extensions['code'], 'SPINOFF_SOURCE_NOT_OWNED')

    def test_removing_source_purchase_is_rejected_and_event_delete_is_atomic(self):
        purchase = self.record('Buy', 4, 500)
        self.spinoff()
        event = Transaction.objects(activity=self.activities['Stock Spinoff']).first()
        from schemas.schema import schema
        query = 'mutation($p:ID!,$id:ID!) { deleteTransaction(profileId:$p,id:$id) { success } }'
        result = schema.execute(query, variable_values={'p':str(self.profile.id),'id':str(purchase.id)})
        self.assertTrue(result.errors)
        result = schema.execute(query, variable_values={'p':str(self.profile.id),'id':str(event.id)})
        self.assertIsNone(result.errors)
        self.assertTrue(result.data['deleteTransaction']['success'])
        self.assertEqual(Transaction.objects.count(),1)

    def test_zero_allocation_and_nonspinoff_field_rejection(self):
        self.record('Buy',4,500)
        self.assertIsNone(self.spinoff(allocation=0).errors)
        from schemas.schema import schema
        result = schema.execute('mutation($p:ID!, $t:TransactionInput!) { createTransaction(profileId:$p,transData:$t) { transaction { id } } }', variable_values={'p':str(self.profile.id),'t':{'activity':str(self.activities['Buy'].id),'stock':str(self.child.id),'platform':str(self.platform.id),'shares':'1','total':'100','allocatedBookCost':'10','transactionDate':'2026-01-03'}})
        self.assertEqual(result.errors[0].extensions['code'],'INVALID_SPINOFF_FIELDS')
