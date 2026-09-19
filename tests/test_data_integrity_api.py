import unittest
from tests import test_transactions_stats_api as fixtures


class DataIntegrityApiTest(unittest.TestCase):
    setUp = fixtures.WalletAnalyticsApiTest.setUp
    tearDown = fixtures.WalletAnalyticsApiTest.tearDown
    request = fixtures.WalletAnalyticsApiTest.request
    add_transaction = fixtures.WalletAnalyticsApiTest.add_transaction

    def test_cannot_convert_ordinary_transaction_to_unlinked_repayment(self):
        row = self.add_transaction()
        response = self.request('PUT', '/transactions/' + row['id'], {'transaction_kind': 'credit_repayment'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.request('DELETE', '/transactions/' + row['id']).status_code, 200)

    def test_kind_sign_and_category_are_consistent_for_create_and_edit(self):
        original = self.add_transaction()
        for overrides in [dict(amount=1, transaction_kind='expense'), dict(amount=-1, transaction_kind='income'),
                          dict(amount=-1, transaction_kind='refund'), dict(category_id='income_salary_salary'),
                          dict(amount=1, transaction_kind='income', category_id='expense_dining_meal'),
                          dict(amount=1, transaction_kind='refund', category_id='income_salary_salary')]:
            with self.subTest(overrides=overrides):
                payload = dict(account_id='cash', amount=-1, timestamp='2026-07-12', description='test',
                               transaction_kind='expense', category_id='expense_dining_meal', excluded_from_stats=False)
                payload.update(overrides)
                self.assertEqual(self.request('POST', '/transactions', payload).status_code, 400)
                self.assertEqual(self.request('PUT', '/transactions/' + original['id'], overrides).status_code, 400)
        self.assertEqual(self.request('GET', '/overview?year=2026&month=7').json['expense'], 120)

    def test_idempotent_creates_replay_after_new_app_and_reject_changed_payload(self):
        for endpoint, payload in [('/transactions', dict(account_id='cash', amount=-5, description='test')),
                                  ('/credit-card-repayments', dict(source_account_id='cash', credit_account_id='card', amount=5))]:
            with self.subTest(endpoint=endpoint):
                headers = self.headers | {'Idempotency-Key': endpoint}
                first = self.client.post(endpoint, json=payload, headers=headers)
                self.assertEqual(first.status_code, 201, first.json)
                second_client = fixtures.create_app(dict(self.app.config)).test_client()
                second = second_client.post(endpoint, json=payload, headers=headers)
                self.assertEqual(second.status_code, 201)
                self.assertEqual(second.json, first.json)
                changed = second_client.post(endpoint, json=payload | {'description': 'different'}, headers=headers)
                self.assertEqual(changed.status_code, 409)
        self.assertEqual(len(self.request('GET', '/transactions').json), 3)

    def test_invalid_keys_and_failed_validation_do_not_reserve_key(self):
        payload = dict(account_id='cash', amount=-5, description='test')
        for key in ('', ' ', 'x' * 129):
            response = self.client.post('/transactions', json=payload, headers=self.headers | {'Idempotency-Key': key})
            self.assertEqual(response.status_code, 400)
        headers = self.headers | {'Idempotency-Key': 'retry-after-validation'}
        self.assertEqual(self.client.post('/transactions', json=payload | {'amount': 0}, headers=headers).status_code, 400)
        self.assertEqual(self.client.post('/transactions', json=payload, headers=headers).status_code, 201)

    def test_legacy_client_without_key_still_creates_separate_transactions(self):
        payload = dict(account_id='cash', amount=-5, description='test')
        first = self.request('POST', '/transactions', payload)
        second = self.request('POST', '/transactions', payload)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.json['id'], second.json['id'])

    def test_inactive_original_account_remains_editable_but_not_a_move_target(self):
        old = self.add_transaction()
        other = self.add_transaction(account_id='cash')
        self.assertEqual(self.request('DELETE', '/accounts/card').status_code, 200)
        self.assertEqual(self.request('PUT', '/transactions/' + old['id'], {'description': 'corrected', 'amount': -10}).status_code, 200)
        self.assertEqual(self.request('PUT', '/transactions/' + other['id'], {'account_id': 'card'}).status_code, 400)

    def test_concurrent_same_key_applies_balance_once(self):
        from concurrent.futures import ThreadPoolExecutor
        payload = dict(account_id='cash', amount=-5, description='test')
        def send(_index):
            with self.app.test_client() as client:
                response = client.post('/transactions', json=payload,
                                       headers=self.headers | {'Idempotency-Key': 'concurrent-request'})
                return response.status_code, response.json
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(send, range(4)))
        self.assertTrue(all(status == 201 for status, _body in responses))
        self.assertTrue(all(body == responses[0][1] for _status, body in responses))
        accounts = self.request('GET', '/accounts').json
        self.assertEqual(next(row['current_balance'] for row in accounts if row['id'] == 'cash'), -5)

    def test_cache_write_failure_rolls_back_balance_and_transaction(self):
        import sqlite3
        conn = fixtures.connect_database(self.app.config['DB_PATH'])
        conn.execute("CREATE TRIGGER fail_cache BEFORE INSERT ON idempotency_requests BEGIN SELECT RAISE(ABORT, 'test cache failure'); END")
        conn.commit()
        payload = dict(account_id='cash', amount=-5, description='test')
        headers = self.headers | {'Idempotency-Key': 'retry-after-rollback'}
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post('/transactions', json=payload, headers=headers)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT current_balance FROM accounts WHERE id = 'cash'").fetchone()[0], 0)
        conn.execute('DROP TRIGGER fail_cache')
        conn.commit()
        conn.close()
        self.assertEqual(self.client.post('/transactions', json=payload, headers=headers).status_code, 201)

    def test_v5_database_upgrades_and_preserves_records(self):
        row = self.add_transaction()
        conn = fixtures.connect_database(self.app.config['DB_PATH'])
        conn.execute('DROP TABLE idempotency_requests')
        conn.execute("UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        upgraded = fixtures.create_app(dict(self.app.config)).test_client()
        self.assertEqual(upgraded.get('/transactions', headers=self.headers).json[0]['id'], row['id'])
        response = upgraded.post('/transactions', headers=self.headers | {'Idempotency-Key': 'after-upgrade'},
                                 json=dict(account_id='cash', amount=-5, description='test'))
        self.assertEqual(response.status_code, 201)

    def test_positive_refund_and_bidirectional_special_kinds_remain_valid(self):
        self.add_transaction(amount=10, transaction_kind='refund', category_id='expense_dining_meal')
        for kind in ('transfer', 'topup_withdrawal', 'balance_adjustment'):
            for amount in (-10, 10):
                self.add_transaction(amount=amount, transaction_kind=kind, category_id=None, excluded_from_stats=True)

    def test_idempotency_key_cannot_be_reused_across_creation_endpoints(self):
        headers = self.headers | {'Idempotency-Key': 'same-user-operation'}
        first = self.client.post('/transactions', headers=headers,
                                 json=dict(account_id='cash', amount=-5, description='test'))
        self.assertEqual(first.status_code, 201)
        second = self.client.post('/credit-card-repayments', headers=headers,
                                  json=dict(source_account_id='cash', credit_account_id='card', amount=5))
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json['error']['field'], 'idempotency_key')
        self.assertEqual(len(self.request('GET', '/transactions').json), 1)
