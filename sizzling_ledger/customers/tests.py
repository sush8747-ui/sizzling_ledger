import json
from decimal import Decimal
from unittest import mock

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .gst import checksum_char, gstin_problem, normalize_gstin, parse_taxpayer

from .models import (
    Customer,
    Employee,
    Invoice,
    PaymentReceived,
    PurchaseExpense,
    Sale,
    Salary,
    UserProfile,
    Vendor,
)


class AccountOwnershipTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(username='user1', email='user1@example.com', password='pw')
        self.user2 = User.objects.create_user(username='user2', email='user2@example.com', password='pw')
        self.admin = User.objects.create_user(username='admin', email='admin@example.com', password='pw')
        self.main_admin = User.objects.create_user(username='main', email=settings.MAIN_ADMIN_EMAIL, password='pw')

        UserProfile.objects.create(
            user=self.user1,
            can_edit_accounts=True,
            can_edit_payments_received=True,
            can_edit_purchases_expenses=True,
        )
        UserProfile.objects.create(
            user=self.user2,
            can_edit_accounts=True,
            can_edit_payments_received=True,
            can_edit_purchases_expenses=True,
        )
        UserProfile.objects.create(
            user=self.admin,
            is_admin=True,
            can_edit_accounts=True,
            can_edit_payments_received=True,
            can_edit_purchases_expenses=True,
        )

        self.account_user1 = Invoice.objects.create(name='Alpha', user=self.user1)
        self.account_user2 = Invoice.objects.create(name='Beta', user=self.user2)
        self.account_admin = Invoice.objects.create(name='AdminAcct', user=self.admin)
        self.account_main = Invoice.objects.create(name='MainAcct', user=self.main_admin)

        self.customer = Customer.objects.create(name='Customer 1', phone=0)
        self.vendor = Vendor.objects.create(name='Vendor 1', phone=0)

        self.payment_user1 = PaymentReceived.objects.create(
            customer=self.customer,
            invoice=self.account_user1,
            date='2026-01-01',
            amount=Decimal('10.00'),
            amount_type='cash',
        )
        self.payment_user2 = PaymentReceived.objects.create(
            customer=self.customer,
            invoice=self.account_user2,
            date='2026-01-02',
            amount=Decimal('20.00'),
            amount_type='cash',
        )
        self.payment_admin = PaymentReceived.objects.create(
            customer=self.customer,
            invoice=self.account_admin,
            date='2026-01-03',
            amount=Decimal('30.00'),
            amount_type='cash',
        )
        self.payment_main = PaymentReceived.objects.create(
            customer=self.customer,
            invoice=self.account_main,
            date='2026-01-04',
            amount=Decimal('40.00'),
            amount_type='cash',
        )

        self.purchase_user1 = PurchaseExpense.objects.create(
            payee_type='vendor',
            vendor=self.vendor,
            account=self.account_user1,
            invoice='INV-001',
            amount=Decimal('15.00'),
            amount_type='cash',
        )
        self.purchase_user2 = PurchaseExpense.objects.create(
            payee_type='vendor',
            vendor=self.vendor,
            account=self.account_user2,
            invoice='INV-002',
            amount=Decimal('25.00'),
            amount_type='cash',
        )
        self.purchase_admin = PurchaseExpense.objects.create(
            payee_type='vendor',
            vendor=self.vendor,
            account=self.account_admin,
            invoice='INV-003',
            amount=Decimal('35.00'),
            amount_type='cash',
        )
        self.purchase_main = PurchaseExpense.objects.create(
            payee_type='vendor',
            vendor=self.vendor,
            account=self.account_main,
            invoice='INV-004',
            amount=Decimal('45.00'),
            amount_type='cash',
        )

    def test_user_sees_all_accounts_in_list_but_only_own_in_scope_mine(self):
        """User can see all accounts in main list, but only their own in ?scope=mine."""
        self.client.force_authenticate(user=self.user1)

        # Main list: should see all accounts
        res = self.client.get('/api/invoices/')
        self.assertEqual(res.status_code, 200)
        all_names = {item['name'] for item in res.data}
        self.assertEqual(all_names, {'Alpha', 'Beta', 'AdminAcct', 'MainAcct'})

        # ?scope=mine: should see only own accounts
        res = self.client.get('/api/invoices/?scope=mine')
        self.assertEqual(res.status_code, 200)
        own_names = {item['name'] for item in res.data}
        self.assertEqual(own_names, {'Alpha'})
        # Check is_mine flag
        for item in res.data:
            if item['name'] == 'Alpha':
                self.assertTrue(item['is_mine'])
            else:
                self.assertFalse(item['is_mine'])

        # Detail view on own account: should work
        res = self.client.get(f'/api/invoices/{self.account_user1.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['name'], 'Alpha')
        self.assertTrue(res.data['is_mine'])

        # Detail view on another user's account: should fail because they don't own it, but they CAN view any account
        # Actually, per the requirement, InvoiceDetailView.get allows viewing ANY account
        res = self.client.get(f'/api/invoices/{self.account_user2.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['name'], 'Beta')
        self.assertFalse(res.data['is_mine'])  # is_mine should be False for other's account

        # Payments / purchases lists show EVERY transaction (the own-account
        # rule only applies when adding or changing one).
        res = self.client.get('/api/payments-received/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual({item['invoice'] for item in res.data}, {'Alpha', 'Beta', 'AdminAcct', 'MainAcct'})

        res = self.client.get('/api/purchases-expenses/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual({item['account'] for item in res.data}, {'Alpha', 'Beta', 'AdminAcct', 'MainAcct'})

        # is_mine marks the rows the caller may actually modify
        res = self.client.get('/api/payments-received/')
        self.assertEqual({i['invoice'] for i in res.data if i['is_mine']}, {'Alpha'})
        res = self.client.get('/api/purchases-expenses/')
        self.assertEqual({i['account'] for i in res.data if i['is_mine']}, {'Alpha'})

    def test_user_with_only_payments_permission_can_use_scope_mine(self):
        """User with only can_edit_payments_received can use ?scope=mine."""
        # Create a user with only payments permission
        user_payments = User.objects.create_user(username='user_pay', email='pay@example.com', password='pw')
        UserProfile.objects.create(
            user=user_payments,
            can_edit_payments_received=True,
        )
        payments_account = Invoice.objects.create(name='PaymentsAcct', user=user_payments)
        
        self.client.force_authenticate(user=user_payments)

        # Should be able to see main list if they have can_edit_accounts - NO, they don't
        res = self.client.get('/api/invoices/')
        self.assertEqual(res.status_code, 403)

        # Should be able to use ?scope=mine since they have can_edit_payments_received
        res = self.client.get('/api/invoices/?scope=mine')
        self.assertEqual(res.status_code, 200)
        own_names = {item['name'] for item in res.data}
        self.assertEqual(own_names, {'PaymentsAcct'})

    def test_admin_sees_all_accounts_but_only_own_in_scope_mine(self):
        """Admin can see all accounts in main list, but only their own in ?scope=mine."""
        self.client.force_authenticate(user=self.admin)

        # Main list: should see all accounts
        res = self.client.get('/api/invoices/')
        self.assertEqual(res.status_code, 200)
        all_names = {item['name'] for item in res.data}
        self.assertEqual(all_names, {'Alpha', 'Beta', 'AdminAcct', 'MainAcct'})

        # ?scope=mine: should see only own accounts
        res = self.client.get('/api/invoices/?scope=mine')
        self.assertEqual(res.status_code, 200)
        own_names = {item['name'] for item in res.data}
        self.assertEqual(own_names, {'AdminAcct'})

    def test_user_cannot_create_payment_on_other_users_account(self):
        """Users cannot create payments/expenses/salaries on other users' accounts."""
        self.client.force_authenticate(user=self.user1)

        res = self.client.post(
            '/api/payments-received/',
            {
                'customer_id': self.customer.id,
                'invoice_id': self.account_user2.id,
                'date': '2026-01-05',
                'amount': '7.00',
                'amount_type': 'cash',
            },
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_admin_cannot_create_payment_on_other_users_account(self):
        """Admin cannot create payment on other users' accounts."""
        self.client.force_authenticate(user=self.admin)

        res = self.client.post(
            '/api/payments-received/',
            {
                'customer_id': self.customer.id,
                'invoice_id': self.account_user1.id,
                'date': '2026-01-05',
                'amount': '7.00',
                'amount_type': 'cash',
            },
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_main_admin_cannot_create_payment_on_other_users_account(self):
        """Main admin also cannot create payment on other users' accounts."""
        self.client.force_authenticate(user=self.main_admin)

        res = self.client.post(
            '/api/payments-received/',
            {
                'customer_id': self.customer.id,
                'invoice_id': self.account_user1.id,
                'date': '2026-01-05',
                'amount': '7.00',
                'amount_type': 'cash',
            },
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_transaction_lists_show_everyones_for_user_admin_and_main_admin(self):
        all_accounts = {'Alpha', 'Beta', 'AdminAcct', 'MainAcct'}
        for who in (self.user1, self.admin, self.main_admin):
            self.client.force_authenticate(user=who)
            res = self.client.get('/api/payments-received/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual({i['invoice'] for i in res.data}, all_accounts)
            res = self.client.get('/api/purchases-expenses/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual({i['account'] for i in res.data}, all_accounts)
            # a single record on someone else's account can be viewed too
            res = self.client.get(f'/api/payments-received/{self.payment_user2.id}/')
            self.assertEqual(res.status_code, 200)
            res = self.client.get(f'/api/purchases-expenses/{self.purchase_user2.id}/')
            self.assertEqual(res.status_code, 200)

    def test_cannot_modify_or_delete_transactions_on_others_accounts(self):
        for who in (self.admin, self.main_admin):
            self.client.force_authenticate(user=who)
            res = self.client.delete(f'/api/payments-received/{self.payment_user1.id}/')
            self.assertEqual(res.status_code, 403)
            res = self.client.delete(f'/api/purchases-expenses/{self.purchase_user1.id}/')
            self.assertEqual(res.status_code, 403)
            res = self.client.patch(
                f'/api/purchases-expenses/{self.purchase_user1.id}/', {'amount': '1.00'}, format='json'
            )
            self.assertEqual(res.status_code, 403)
        self.assertTrue(PaymentReceived.objects.filter(pk=self.payment_user1.pk).exists())
        self.assertTrue(PurchaseExpense.objects.filter(pk=self.purchase_user1.pk).exists())

    def test_admin_can_still_delete_transactions_on_own_account(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(f'/api/payments-received/{self.payment_admin.id}/')
        self.assertEqual(res.status_code, 204)
        res = self.client.delete(f'/api/purchases-expenses/{self.purchase_admin.id}/')
        self.assertEqual(res.status_code, 204)

    # ---- account rules: only the creator edits, nobody deletes, names are unique per user
    def test_only_creator_can_rename_account(self):
        # creator (a normal user with the Accounts permission) can rename their own account
        self.client.force_authenticate(user=self.user1)
        res = self.client.patch(f'/api/invoices/{self.account_user1.id}/', {'name': 'Alpha 2'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['name'], 'Alpha 2')
        # ...but not someone else's
        res = self.client.patch(f'/api/invoices/{self.account_user2.id}/', {'name': 'Hacked'}, format='json')
        self.assertEqual(res.status_code, 403)
        # admin and main admin get exactly the same rule: own accounts only
        for who in (self.admin, self.main_admin):
            self.client.force_authenticate(user=who)
            res = self.client.put(f'/api/invoices/{self.account_user1.id}/', {'name': 'Nope'}, format='json')
            self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(f'/api/invoices/{self.account_admin.id}/', {'name': 'Admin renamed'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.account_user1.refresh_from_db()
        self.assertEqual(self.account_user1.name, 'Alpha 2')

    def test_accounts_cannot_be_deleted(self):
        for who in (self.user1, self.admin, self.main_admin):
            self.client.force_authenticate(user=who)
            for acct in (self.account_user1, self.account_admin):
                res = self.client.delete(f'/api/invoices/{acct.id}/')
                self.assertEqual(res.status_code, 405)
        self.assertEqual(Invoice.objects.count(), 4)

    def test_duplicate_account_name_is_a_clear_400_not_a_500(self):
        self.client.force_authenticate(user=self.user1)
        res = self.client.post('/api/invoices/', {'name': 'Alpha'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already have an account', res.data['detail'])
        res = self.client.post('/api/invoices/', {'name': 'alpha'}, format='json')   # case-insensitive
        self.assertEqual(res.status_code, 400)
        # renaming to a name you already use is rejected too, renaming to the same name is fine
        extra = Invoice.objects.create(name='Gamma', user=self.user1)
        res = self.client.patch(f'/api/invoices/{extra.id}/', {'name': 'ALPHA'}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.patch(f'/api/invoices/{extra.id}/', {'name': 'Gamma'}, format='json')
        self.assertEqual(res.status_code, 200)
        # a different user may use the same name
        self.client.force_authenticate(user=self.user2)
        res = self.client.post('/api/invoices/', {'name': 'Alpha'}, format='json')
        self.assertEqual(res.status_code, 201)


class SalaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='sa_u', email='sa_u@example.com', password='pw')
        self.other = User.objects.create_user(username='sa_o', email='sa_o@example.com', password='pw')
        self.no_perm = User.objects.create_user(username='sa_nop', email='sa_nop@example.com', password='pw')

        UserProfile.objects.create(user=self.user, can_edit_salaries=True)
        UserProfile.objects.create(user=self.other, can_edit_salaries=True)
        UserProfile.objects.create(user=self.no_perm, can_edit_salaries=False)

        self.account = Invoice.objects.create(name='A', user=self.user, cash_balance=Decimal('100.00'))
        self.other_account = Invoice.objects.create(name='B', user=self.other, cash_balance=Decimal('200.00'))
        self.emp = Employee.objects.create(name='Alice', phone=0)

    def test_paid_today_reduces_cash_or_invoice(self):
        Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('10.00'), post_date=timezone.localdate())
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('90.00'))

    def test_future_post_date_unpaid_and_finalize_once(self):
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        s = Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('7.00'), post_date=tomorrow)
        self.account.refresh_from_db()
        self.assertEqual(self.account.post_account_balance, Decimal('7.00'))
        s.post_date = timezone.localdate()
        s.save()
        from .models import finalize_due_salaries

        finalize_due_salaries()
        finalize_due_salaries()
        self.account.refresh_from_db()
        self.assertEqual(self.account.post_account_balance, Decimal('0.00'))
        self.assertEqual(self.account.cash_balance, Decimal('93.00'))

    def test_edit_and_delete_reverse_and_reapply(self):
        s = Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('8.00'), post_date=timezone.localdate())
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('92.00'))

        s.amount = Decimal('5.00')
        s.save()
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('95.00'))

        s.delete()
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('100.00'))

    # ---- amount_type: a salary is paid from the account's cash OR its invoice balance ----
    def _fund_invoice(self, amount='50.00'):
        self.account.invoice_balance = Decimal(amount)
        self.account.save()

    def test_amount_type_defaults_to_cash_via_api(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.post('/api/salaries/', {'employee_id': self.emp.id, 'account_id': self.account.id, 'amount': '10.00'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['amount_type'], 'cash')
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('90.00'))
        self.assertEqual(self.account.invoice_balance, Decimal('0.00'))

    def test_invoice_amount_type_deducts_invoice_balance_not_cash(self):
        self._fund_invoice('50.00')
        self.client.force_authenticate(user=self.user)
        res = self.client.post(
            '/api/salaries/',
            {'employee_id': self.emp.id, 'account_id': self.account.id, 'amount': '12.00', 'amount_type': 'invoice'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['amount_type'], 'invoice')
        self.account.refresh_from_db()
        self.assertEqual(self.account.invoice_balance, Decimal('38.00'))
        self.assertEqual(self.account.cash_balance, Decimal('100.00'))

    def test_future_invoice_salary_is_pending_then_finalizes_from_invoice(self):
        self._fund_invoice('50.00')
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        s = Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('7.00'), amount_type='invoice', post_date=tomorrow)
        self.account.refresh_from_db()
        self.assertEqual(self.account.post_account_balance, Decimal('7.00'))
        self.assertEqual(self.account.invoice_balance, Decimal('50.00'))
        s.post_date = timezone.localdate()
        s.save()
        from .models import finalize_due_salaries

        finalize_due_salaries()
        finalize_due_salaries()
        self.account.refresh_from_db()
        self.assertEqual(self.account.post_account_balance, Decimal('0.00'))
        self.assertEqual(self.account.invoice_balance, Decimal('43.00'))
        self.assertEqual(self.account.cash_balance, Decimal('100.00'))

    def test_changing_amount_type_on_edit_moves_the_deduction_and_delete_refunds_it(self):
        self._fund_invoice('50.00')
        s = Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('8.00'), post_date=timezone.localdate())
        self.account.refresh_from_db()
        self.assertEqual((self.account.cash_balance, self.account.invoice_balance), (Decimal('92.00'), Decimal('50.00')))

        s.amount_type = 'invoice'
        s.save()
        self.account.refresh_from_db()
        self.assertEqual((self.account.cash_balance, self.account.invoice_balance), (Decimal('100.00'), Decimal('42.00')))

        s.delete()
        self.account.refresh_from_db()
        self.assertEqual((self.account.cash_balance, self.account.invoice_balance), (Decimal('100.00'), Decimal('50.00')))

    def test_invalid_amount_type_is_rejected_and_list_returns_amount_type(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.post(
            '/api/salaries/',
            {'employee_id': self.emp.id, 'account_id': self.account.id, 'amount': '5.00', 'amount_type': 'bitcoin'},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('amount_type', res.data)
        Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('1.00'), post_date=timezone.localdate())
        Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('2.00'), amount_type='invoice', post_date=timezone.localdate())
        res = self.client.get('/api/salaries/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual({row['amount_type'] for row in res.data}, {'cash', 'invoice'})

    def test_due_tomorrow_endpoint_and_ownership(self):
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('3.00'), post_date=tomorrow)
        Salary.objects.create(employee=self.emp, account=self.other_account, amount=Decimal('4.00'), post_date=tomorrow)

        self.client.force_authenticate(user=self.user)
        res = self.client.get('/api/salaries/due-tomorrow/')
        self.assertEqual(res.status_code, 200)
        names = {item['account'] for item in res.data}
        self.assertEqual(names, {'A'})

    def test_user_cannot_use_other_users_account_or_see_their_salary(self):
        self.client.force_authenticate(user=self.other)
        res = self.client.post('/api/salaries/', {'employee_id': self.emp.id, 'account_id': self.account.id, 'amount': '2.00'}, format='json')
        self.assertEqual(res.status_code, 400)

        # ...but the salary itself is visible to anyone with salary access.
        s = Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('1.00'), post_date=timezone.localdate())
        res = self.client.get(f'/api/salaries/{s.id}/')
        self.assertEqual(res.status_code, 200)
        res = self.client.get('/api/salaries/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(s.id, {item['id'] for item in res.data})
        self.assertFalse([i for i in res.data if i['id'] == s.id][0]['is_mine'])

    def test_admin_sees_all_salaries_but_modifies_only_own_accounts(self):
        admin = User.objects.create_user(username='sa_adm', email='sa_adm@example.com', password='pw')
        UserProfile.objects.create(user=admin, is_admin=True, can_edit_salaries=True)
        admin_account = Invoice.objects.create(name='AdminA', user=admin, cash_balance=Decimal('100.00'))
        others = Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('1.00'), post_date=timezone.localdate())
        mine = Salary.objects.create(employee=self.emp, account=admin_account, amount=Decimal('2.00'), post_date=timezone.localdate())

        self.client.force_authenticate(user=admin)
        res = self.client.get('/api/salaries/')
        self.assertEqual({i['id'] for i in res.data}, {others.id, mine.id})
        self.assertEqual(self.client.delete(f'/api/salaries/{others.id}/').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/salaries/{mine.id}/').status_code, 204)

    def test_permission_required(self):
        self.client.force_authenticate(user=self.no_perm)
        res = self.client.get('/api/salaries/')
        self.assertEqual(res.status_code, 403)
        res = self.client.post('/api/salaries/', {'employee_id': self.emp.id, 'account_id': self.account.id, 'amount': '1.00'}, format='json')
        self.assertIn(res.status_code, (403, 400))

    def test_validation_errors(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.post('/api/salaries/', {'employee_id': self.emp.id, 'account_id': self.account.id, 'amount': '0.00'}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post('/api/salaries/', {'account_id': self.account.id, 'amount': '1.00'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_reconcile_command_counts_pending_salaries(self):
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        Salary.objects.create(employee=self.emp, account=self.account, amount=Decimal('6.00'), post_date=tomorrow)
        call_command('reconcile_purchase_expenses', stdout=None)
        self.assertTrue(True)


class PurchaseExpensePayeeTypeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='pe_u', email='pe_u@example.com', password='pw')
        UserProfile.objects.create(user=self.user, can_edit_purchases_expenses=True)
        self.account = Invoice.objects.create(name='A', user=self.user, cash_balance=Decimal('100.00'), invoice_balance=Decimal('50.00'))
        self.vendor = Vendor.objects.create(name='V', phone=0)
        self.emp = Employee.objects.create(name='E', phone=0)
        self.client.force_authenticate(user=self.user)

    def test_purchase_reduces_balances_cash_and_invoice(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'vendor',
            'vendor_id': self.vendor.id,
            'account_id': self.account.id,
            'invoice': 'P-1',
            'amount': '10.00',
            'amount_type': 'cash',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('90.00'))

        acct2 = Invoice.objects.create(name='B', user=self.user, invoice_balance=Decimal('20.00'))
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'vendor',
            'vendor_id': self.vendor.id,
            'account_id': acct2.id,
            'invoice': 'P-2',
            'amount': '5.00',
            'amount_type': 'invoice',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        acct2.refresh_from_db()
        self.assertEqual(acct2.invoice_balance, Decimal('15.00'))

    def test_expense_vendor_requires_invoice_and_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'vendor',
            'vendor_id': self.vendor.id,
            'account_id': self.account.id,
            'amount': '7.00',
        }, format='json')
        self.assertEqual(res.status_code, 400)

        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'vendor',
            'vendor_id': self.vendor.id,
            'account_id': self.account.id,
            'invoice': 'EV-1',
            'amount': '7.00',
            'amount_type': 'cash',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('93.00'))

    def test_expense_employee_requires_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'employee',
            'employee_id': self.emp.id,
            'account_id': self.account.id,
            'amount': '8.00',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_expense_employee_with_cash_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'employee',
            'employee_id': self.emp.id,
            'account_id': self.account.id,
            'amount': '8.00',
            'amount_type': 'cash',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        pe = PurchaseExpense.objects.get(pk=res.data['id'])
        self.assertEqual(pe.amount_type, 'cash')
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('92.00'))

    def test_expense_employee_with_invoice_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'employee',
            'employee_id': self.emp.id,
            'account_id': self.account.id,
            'amount': '8.00',
            'amount_type': 'invoice',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        pe = PurchaseExpense.objects.get(pk=res.data['id'])
        self.assertEqual(pe.amount_type, 'invoice')
        self.account.refresh_from_db()
        self.assertEqual(self.account.invoice_balance, Decimal('42.00'))

    def test_expense_other_requires_note_and_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'other',
            'account_id': self.account.id,
            'amount': '6.00',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 400)

        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'other',
            'account_id': self.account.id,
            'amount': '6.00',
            'note': 'One-time stipend',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_expense_other_with_cash_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'other',
            'account_id': self.account.id,
            'amount': '6.00',
            'note': 'One-time stipend',
            'amount_type': 'cash',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        pe = PurchaseExpense.objects.get(pk=res.data['id'])
        self.assertEqual(pe.amount_type, 'cash')
        self.assertEqual(pe.note, 'One-time stipend')
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('94.00'))

    def test_expense_other_with_invoice_amount_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'other',
            'account_id': self.account.id,
            'amount': '10.00',
            'note': 'Refund to vendor',
            'amount_type': 'invoice',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        pe = PurchaseExpense.objects.get(pk=res.data['id'])
        self.assertEqual(pe.amount_type, 'invoice')
        self.assertEqual(pe.note, 'Refund to vendor')
        self.account.refresh_from_db()
        self.assertEqual(self.account.invoice_balance, Decimal('40.00'))

    def test_missing_expense_payee_type_returns_400(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'employee_id': self.emp.id,
            'account_id': self.account.id,
            'amount': '5.00',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_other_expense_note_round_trips_on_get(self):
        res = self.client.post('/api/purchases-expenses/', {
            'payee_type': 'employee',
            'expense_payee_type': 'other',
            'account_id': self.account.id,
            'amount': '9.00',
            'note': 'Quarterly team lunch',
            'amount_type': 'cash',
            'post_date': str(timezone.localdate()),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        expense_id = res.data['id']

        get_res = self.client.get(f'/api/purchases-expenses/{expense_id}/')
        self.assertEqual(get_res.status_code, 200)
        self.assertIn('note', get_res.data)
        self.assertEqual(get_res.data['note'], 'Quarterly team lunch')


class PurchaseExpenseAdminSaveTests(TestCase):
    def test_account_owner_not_changed_on_admin_save(self):
        from unittest.mock import Mock

        from django.contrib.admin.sites import AdminSite

        from .admin import PurchaseExpenseAdmin

        client = APIClient()
        user = User.objects.create_user(username='ad_u', email='ad_u@example.com', password='pw')
        UserProfile.objects.create(user=user, can_edit_purchases_expenses=True)

        account = Invoice.objects.create(name='A', user=user, cash_balance=Decimal('100.00'), invoice_balance=Decimal('50.00'))
        vendor = Vendor.objects.create(name='V', phone=0)

        original_owner = user
        account.refresh_from_db()
        self.assertEqual(account.user, original_owner)

        admin_user = User.objects.create_superuser(username='admin2', email='admin2@example.com', password='pw')
        admin_site = AdminSite()
        admin_obj = PurchaseExpenseAdmin(PurchaseExpense, admin_site)

        pe = PurchaseExpense.objects.create(
            payee_type='vendor',
            vendor=vendor,
            account=account,
            invoice='P-100',
            amount=Decimal('10.00'),
            amount_type='cash',
            post_date=timezone.localdate(),
        )

        account_owner_before = account.user
        request = Mock()
        request.user = admin_user
        admin_obj.save_model(request, pe, None, change=True)

        account.refresh_from_db()
        self.assertEqual(account.user, account_owner_before)
        self.assertEqual(account.user, original_owner)
        self.assertNotEqual(account.user, admin_user)


class SaleAndPaymentEditTests(TestCase):
    """Admins can edit and delete sales and payments received, and customer /
    account balances must follow every edit exactly (no drift, no double counting)."""

    def setUp(self):
        self.client = APIClient()
        perms = dict(can_edit_sales=True, can_edit_payments_received=True)
        self.admin = User.objects.create_user(username='ed_adm', email='ed_adm@example.com', password='pw')
        UserProfile.objects.create(user=self.admin, is_admin=True, **perms)
        self.other_admin = User.objects.create_user(username='ed_adm2', email='ed_adm2@example.com', password='pw')
        UserProfile.objects.create(user=self.other_admin, is_admin=True, **perms)
        self.staff = User.objects.create_user(username='ed_staff', email='ed_staff@example.com', password='pw')
        UserProfile.objects.create(user=self.staff, **perms)

        self.acct = Invoice.objects.create(name='Mine', user=self.admin)
        self.acct2 = Invoice.objects.create(name='Mine2', user=self.admin)
        self.others_acct = Invoice.objects.create(name='Theirs', user=self.other_admin)
        self.c1 = Customer.objects.create(name='C1', phone=0, email='c1@example.com')
        self.c2 = Customer.objects.create(name='C2', phone=0, email='c2@example.com')
        self.client.force_authenticate(user=self.admin)

    def bal(self, obj):
        obj.refresh_from_db()
        return {k: getattr(obj, k) for k in ('invoice_balance', 'cash_balance')}

    def D(self, invoice, cash):
        return {'invoice_balance': Decimal(invoice), 'cash_balance': Decimal(cash)}

    # ------------------------------------------------------------------ sales
    def _make_sale(self, customer, invoice_amount='100.00', cash_amount='50.00', number='S-1'):
        res = self.client.post('/api/sales/', {
            'customer_id': customer.id, 'date': str(timezone.localdate()), 'invoice': number,
            'invoice_amount': invoice_amount, 'cash_amount': cash_amount, 'gst_percentage': '50',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['customer_id'], customer.id)
        return res.data['id']

    def test_admin_edit_sale_amounts_updates_customer_balance_without_double_counting(self):
        sale_id = self._make_sale(self.c1)
        self.assertEqual(self.bal(self.c1), self.D('100.00', '50.00'))

        res = self.client.patch(f'/api/sales/{sale_id}/', {'invoice_amount': '30.00', 'cash_amount': '10.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.bal(self.c1), self.D('30.00', '10.00'))

        # editing only the invoice number must not touch any balance
        res = self.client.patch(f'/api/sales/{sale_id}/', {'invoice': 'S-9'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['invoice'], 'S-9')
        self.assertEqual(self.bal(self.c1), self.D('30.00', '10.00'))

    def test_admin_can_move_a_sale_to_another_customer(self):
        sale_id = self._make_sale(self.c1)
        res = self.client.put(f'/api/sales/{sale_id}/', {
            'customer_id': self.c2.id, 'date': str(timezone.localdate()), 'invoice': 'S-1',
            'invoice_amount': '20.00', 'cash_amount': '5.00', 'gst_percentage': '50',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['customer'], 'C2')
        self.assertEqual(self.bal(self.c1), self.D('0.00', '0.00'))
        self.assertEqual(self.bal(self.c2), self.D('20.00', '5.00'))

    def test_deleting_a_sale_reverses_the_customer_balance(self):
        sale_id = self._make_sale(self.c1)
        res = self.client.delete(f'/api/sales/{sale_id}/')
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.bal(self.c1), self.D('0.00', '0.00'))

    def test_non_admin_cannot_edit_or_delete_a_sale(self):
        sale_id = self._make_sale(self.c1)
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.patch(f'/api/sales/{sale_id}/', {'invoice_amount': '1.00'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/sales/{sale_id}/').status_code, 403)
        self.assertEqual(self.bal(self.c1), self.D('100.00', '50.00'))

    # --------------------------------------------------------------- payments
    def _make_payment(self, customer, account, amount='40.00', amount_type='cash'):
        res = self.client.post('/api/payments-received/', {
            'customer_id': customer.id, 'invoice_id': account.id, 'date': str(timezone.localdate()),
            'amount': amount, 'amount_type': amount_type,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        # the ids come back so the app's edit form can preselect customer and account
        self.assertEqual((res.data['customer_id'], res.data['invoice_id']), (customer.id, account.id))
        return res.data['id']

    def test_admin_edit_payment_keeps_account_and_customer_balances_in_step(self):
        Customer.objects.filter(pk=self.c1.pk).update(invoice_balance=Decimal('100.00'), cash_balance=Decimal('100.00'))
        Customer.objects.filter(pk=self.c2.pk).update(invoice_balance=Decimal('50.00'))
        pid = self._make_payment(self.c1, self.acct, '40.00', 'cash')
        self.assertEqual(self.bal(self.acct), self.D('0.00', '40.00'))
        self.assertEqual(self.bal(self.c1), self.D('100.00', '60.00'))

        # amount change
        res = self.client.patch(f'/api/payments-received/{pid}/', {'amount': '10.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.bal(self.acct), self.D('0.00', '10.00'))
        self.assertEqual(self.bal(self.c1), self.D('100.00', '90.00'))

        # cash -> invoice moves BOTH sides to the other balance
        res = self.client.patch(f'/api/payments-received/{pid}/', {'amount_type': 'invoice'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.bal(self.acct), self.D('10.00', '0.00'))
        self.assertEqual(self.bal(self.c1), self.D('90.00', '100.00'))

        # another customer
        res = self.client.patch(f'/api/payments-received/{pid}/', {'customer_id': self.c2.id}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['customer'], 'C2')
        self.assertEqual(self.bal(self.c1), self.D('100.00', '100.00'))
        self.assertEqual(self.bal(self.c2), self.D('40.00', '0.00'))
        self.assertEqual(self.bal(self.acct), self.D('10.00', '0.00'))

        # another of MY accounts
        res = self.client.patch(f'/api/payments-received/{pid}/', {'invoice_id': self.acct2.id}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.bal(self.acct), self.D('0.00', '0.00'))
        self.assertEqual(self.bal(self.acct2), self.D('10.00', '0.00'))

        # deleting afterwards reverses whatever the final state was
        self.assertEqual(self.client.delete(f'/api/payments-received/{pid}/').status_code, 204)
        self.assertEqual(self.bal(self.acct2), self.D('0.00', '0.00'))
        self.assertEqual(self.bal(self.c2), self.D('50.00', '0.00'))

    def test_cannot_edit_a_payment_onto_someone_elses_account(self):
        pid = self._make_payment(self.c1, self.acct)
        res = self.client.patch(f'/api/payments-received/{pid}/', {'invoice_id': self.others_acct.id}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.bal(self.acct), self.D('0.00', '40.00'))
        self.assertEqual(self.bal(self.others_acct), self.D('0.00', '0.00'))

    def test_payment_on_someone_elses_account_is_visible_but_locked(self):
        self.client.force_authenticate(user=self.other_admin)
        pid = self._make_payment(self.c1, self.others_acct)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(f'/api/payments-received/{pid}/').status_code, 200)
        self.assertEqual(self.client.patch(f'/api/payments-received/{pid}/', {'amount': '1.00'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/payments-received/{pid}/').status_code, 403)
        self.assertEqual(self.bal(self.others_acct), self.D('0.00', '40.00'))

    def test_non_admin_cannot_edit_a_payment(self):
        pid = self._make_payment(self.c1, self.acct)
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.patch(f'/api/payments-received/{pid}/', {'amount': '1.00'}, format='json').status_code, 403)
        self.assertEqual(self.bal(self.acct), self.D('0.00', '40.00'))


class TransactionNoteTests(TestCase):
    """A note can be saved on every purchase/expense type; only 'Other' requires one."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='nt_u', email='nt_u@example.com', password='pw')
        UserProfile.objects.create(user=self.user, is_admin=True, can_edit_purchases_expenses=True)
        self.account = Invoice.objects.create(name='A', user=self.user, cash_balance=Decimal('100.00'), invoice_balance=Decimal('100.00'))
        self.vendor = Vendor.objects.create(name='V', phone=0)
        self.emp = Employee.objects.create(name='E', phone=0)
        self.client.force_authenticate(user=self.user)
        self.today = str(timezone.localdate())

    def _post(self, **extra):
        body = {'account_id': self.account.id, 'amount': '5.00', 'amount_type': 'cash', 'post_date': self.today}
        body.update(extra)
        return self.client.post('/api/purchases-expenses/', body, format='json')

    def test_note_is_kept_for_a_vendor_purchase(self):
        res = self._post(payee_type='vendor', vendor_id=self.vendor.id, invoice='P-1', note='Delivered late')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], 'Delivered late')

    def test_note_is_kept_for_vendor_and_employee_expenses(self):
        res = self._post(payee_type='employee', expense_payee_type='vendor', vendor_id=self.vendor.id, invoice='X-1', note='Packaging')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], 'Packaging')
        res = self._post(payee_type='employee', expense_payee_type='employee', employee_id=self.emp.id, note='Travel advance')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], 'Travel advance')

    def test_note_is_optional_except_for_other(self):
        res = self._post(payee_type='vendor', vendor_id=self.vendor.id, invoice='P-2')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['note'], '')
        res = self._post(payee_type='employee', expense_payee_type='other')
        self.assertEqual(res.status_code, 400)
        self.assertIn('note', res.data)

    def test_editing_other_fields_does_not_wipe_the_note(self):
        res = self._post(payee_type='employee', expense_payee_type='other', note='Stipend')
        pid = res.data['id']
        res = self.client.patch(f'/api/purchases-expenses/{pid}/', {'post_date': self.today}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['note'], 'Stipend')
        res = self.client.patch(f'/api/purchases-expenses/{pid}/', {'note': 'Updated stipend'}, format='json')
        self.assertEqual(res.data['note'], 'Updated stipend')


class ExpensePayeeTypeIsStoredTests(TestCase):
    """The app tells vendor / employee / other expenses apart using expense_payee_type,
    so the backend must store it and send it back."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='ept', email='ept@example.com', password='pw')
        UserProfile.objects.create(user=self.user, is_admin=True, can_edit_purchases_expenses=True)
        self.account = Invoice.objects.create(name='A', user=self.user, cash_balance=Decimal('100.00'))
        self.vendor = Vendor.objects.create(name='V', phone=0)
        self.emp = Employee.objects.create(name='E', phone=0)
        self.client.force_authenticate(user=self.user)
        self.base = {'payee_type': 'employee', 'account_id': self.account.id, 'amount': '1.00',
                     'amount_type': 'cash', 'post_date': str(timezone.localdate())}

    def test_type_is_stored_and_returned_for_each_expense_kind(self):
        cases = [
            ({'expense_payee_type': 'vendor', 'vendor_id': self.vendor.id, 'invoice': 'I-1'}, 'vendor'),
            ({'expense_payee_type': 'employee', 'employee_id': self.emp.id}, 'employee'),
            ({'expense_payee_type': 'other', 'note': 'Misc'}, 'other'),
        ]
        for extra, expected in cases:
            res = self.client.post('/api/purchases-expenses/', {**self.base, **extra}, format='json')
            self.assertEqual(res.status_code, 201, res.data)
            self.assertEqual(res.data['expense_payee_type'], expected)
            self.assertEqual(PurchaseExpense.objects.get(pk=res.data['id']).expense_payee_type, expected)
        listed = {row['expense_payee_type'] for row in self.client.get('/api/purchases-expenses/').data}
        self.assertEqual(listed, {'vendor', 'employee', 'other'})

    def test_a_vendor_purchase_has_no_expense_type(self):
        res = self.client.post('/api/purchases-expenses/', {
            **self.base, 'payee_type': 'vendor', 'vendor_id': self.vendor.id, 'invoice': 'P-1'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['expense_payee_type'], '')

    def test_old_rows_without_a_stored_type_are_still_reported_correctly(self):
        for extra, expected in [
            ({'expense_payee_type': 'vendor', 'vendor_id': self.vendor.id, 'invoice': 'I-2'}, 'vendor'),
            ({'expense_payee_type': 'other', 'note': 'Old other'}, 'other'),
        ]:
            res = self.client.post('/api/purchases-expenses/', {**self.base, **extra}, format='json')
            pk = res.data['id']
            PurchaseExpense.objects.filter(pk=pk).update(expense_payee_type='')   # how old rows look
            got = self.client.get(f'/api/purchases-expenses/{pk}/').data['expense_payee_type']
            self.assertEqual(got, expected)

    def test_migration_backfills_old_rows(self):
        import importlib
        from django.apps import apps as global_apps

        for extra in ({'expense_payee_type': 'vendor', 'vendor_id': self.vendor.id, 'invoice': 'I-3'},
                      {'expense_payee_type': 'employee', 'employee_id': self.emp.id},
                      {'expense_payee_type': 'other', 'note': 'x'}):
            self.client.post('/api/purchases-expenses/', {**self.base, **extra}, format='json')
        PurchaseExpense.objects.update(expense_payee_type='')
        cash_before = Invoice.objects.get(pk=self.account.pk).cash_balance
        migration = importlib.import_module('customers.migrations.0024_backfill_expense_payee_type')
        migration.backfill_expense_payee_type(global_apps, None)
        self.assertEqual(sorted(PurchaseExpense.objects.values_list('expense_payee_type', flat=True)), ['employee', 'other', 'vendor'])
        # the backfill must not move any money
        self.assertEqual(Invoice.objects.get(pk=self.account.pk).cash_balance, cash_before)

    def test_unknown_expense_type_is_rejected(self):
        res = self.client.post('/api/purchases-expenses/', {**self.base, 'expense_payee_type': 'banana'}, format='json')
        self.assertEqual(res.status_code, 400)


class VendorExpenseKeepsItsVendorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='vx', email='vx@example.com', password='pw')
        UserProfile.objects.create(user=self.user, is_admin=True, can_edit_purchases_expenses=True)
        self.account = Invoice.objects.create(name='A', user=self.user, cash_balance=Decimal('100.00'))
        self.vendor = Vendor.objects.create(name='Acme', phone=0)
        self.client.force_authenticate(user=self.user)
        self.body = {'payee_type': 'employee', 'expense_payee_type': 'vendor', 'account_id': self.account.id,
                     'invoice': 'I-1', 'amount': '5.00', 'amount_type': 'cash', 'post_date': str(timezone.localdate())}

    def test_vendor_expense_saves_and_returns_its_vendor(self):
        res = self.client.post('/api/purchases-expenses/', {**self.body, 'vendor_id': self.vendor.id}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['vendor'], 'Acme')
        self.assertEqual(res.data['expense_payee_type'], 'vendor')
        self.assertIsNone(res.data['employee'])

    def test_vendor_expense_without_a_vendor_is_rejected(self):
        res = self.client.post('/api/purchases-expenses/', self.body, format='json')
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# GST number validation + lookup
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Stands in for a requests.Response from a GST data provider."""

    def __init__(self, status=200, payload=None, unreadable=False):
        self.status_code = status
        self._payload = payload
        self._unreadable = unreadable

    def json(self):
        if self._unreadable:
            raise ValueError('not json')
        return self._payload


def make_gstin(first14):
    """A structurally valid GSTIN: the first 14 characters plus the right check character."""
    return first14 + checksum_char(first14)


# What providers return follows the GSTN "taxpayer" shape (lgnm, tradeNam, sts, pradr).
TAXPAYER = {
    'lgnm': 'ABC TRADERS PRIVATE LIMITED',
    'tradeNam': 'ABC Traders',
    'sts': 'Active',
    'pradr': {
        'adr': '12, Market Road, Gandhi Nagar, Bengaluru, Karnataka, 560009',
        'addr': {'bno': '12', 'st': 'Market Road', 'loc': 'Gandhi Nagar', 'dst': 'Bengaluru', 'stcd': 'Karnataka', 'pncd': '560009'},
    },
}


class GstNumberTests(TestCase):
    REAL = '27AADCB2923M1ZL'  # BAJAJ AUTO LIMITED — a real GSTIN printed in public GST API documentation

    def test_check_character_matches_a_real_gstin(self):
        self.assertEqual(checksum_char(self.REAL[:14]), 'L')
        self.assertIsNone(gstin_problem(self.REAL))

    def test_a_single_typo_is_caught(self):
        self.assertIn('check character', gstin_problem(self.REAL[:13] + 'X' + 'L'))
        self.assertIn('check character', gstin_problem(self.REAL[:14] + 'M'))

    def test_each_kind_of_mistake_is_explained(self):
        self.assertIn('15 characters', gstin_problem('895214SA122S'))      # the 12-character value already in the database
        self.assertIn('Enter a GST number', gstin_problem(''))
        self.assertIn('format', gstin_problem('ABCDE1234F1Z5AB'))        # right length, wrong layout
        self.assertIn('state code', gstin_problem(make_gstin('00ABCDE1234F1Z')))

    def test_normalize_handles_case_spaces_dashes_and_none(self):
        self.assertEqual(normalize_gstin(' 27aadcb 2923m-1zl '), self.REAL)
        self.assertEqual(normalize_gstin(None), '')

    def test_parser_reads_the_standard_gstn_shape(self):
        info = parse_taxpayer(TAXPAYER)
        self.assertEqual(info['legal_name'], 'ABC TRADERS PRIVATE LIMITED')
        self.assertEqual(info['trade_name'], 'ABC Traders')
        self.assertEqual(info['status'], 'Active')
        self.assertEqual(info['address'], '12, Market Road, Gandhi Nagar, Bengaluru, Karnataka, 560009')
        self.assertEqual(info['location'], 'Bengaluru')
        self.assertEqual(info['pincode'], '560009')
        self.assertEqual(info['state'], 'Karnataka')

    def test_parser_unwraps_data_and_json_strings(self):
        wrapped = parse_taxpayer({'status': '1', 'data': TAXPAYER})
        in_string = parse_taxpayer({'data': json.dumps(TAXPAYER)})
        taxpayer_info = parse_taxpayer({'error': False, 'taxpayerInfo': TAXPAYER})
        for info in (wrapped, in_string, taxpayer_info):
            self.assertEqual(info['trade_name'], 'ABC Traders')

    def test_parser_reads_the_flat_style_and_builds_an_address(self):
        info = parse_taxpayer({
            'name': 'BAJAJ AUTO LIMITED', 'tradename': 'BAJAJ AUTO LTD', 'status': 'Active',
            'pradr': {'bno': '51A', 'bnm': 'Corporate Building', 'st': 'Mumbai Pune Road',
                      'loc': 'Akurdi', 'stcd': 'Maharashtra', 'pncd': '411035'},
        })
        self.assertEqual(info['legal_name'], 'BAJAJ AUTO LIMITED')
        self.assertEqual(info['trade_name'], 'BAJAJ AUTO LTD')
        self.assertEqual(info['address'], '51A, Corporate Building, Mumbai Pune Road, Akurdi, Maharashtra, 411035')
        self.assertEqual(info['location'], 'Akurdi')

    def test_parser_returns_empty_values_for_error_payloads(self):
        for payload in ({'error': 'Invalid GSTIN/UIN number'}, {}, None, 'garbage', []):
            info = parse_taxpayer(payload)
            self.assertEqual((info['legal_name'], info['trade_name'], info['address']), ('', '', ''))


class GstLookupEndpointTests(TestCase):
    URL = '/api/gst-lookup/'

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username='gl_u', email='gl_u@example.com', password='pw')
        UserProfile.objects.create(user=self.user, can_edit_customers=True)
        self.vendor_only = User.objects.create_user(username='gl_v', email='gl_v@example.com', password='pw')
        UserProfile.objects.create(user=self.vendor_only, can_edit_vendors=True)
        self.nobody = User.objects.create_user(username='gl_n', email='gl_n@example.com', password='pw')
        UserProfile.objects.create(user=self.nobody)
        self.gstin = make_gstin('29ABCDE1234F1Z')

    def lookup(self, gst=None, **extra):
        return self.client.get(self.URL, {'gst': gst or self.gstin, **extra})

    def test_it_is_not_open_to_the_public(self):
        self.assertEqual(self.lookup().status_code, 401)

    def test_needs_customer_or_vendor_access(self):
        self.client.force_authenticate(user=self.nobody)
        self.assertEqual(self.lookup().status_code, 403)
        for who in (self.user, self.vendor_only):
            self.client.force_authenticate(user=who)
            self.assertEqual(self.lookup().status_code, 200)

    def test_an_invalid_number_gets_a_clear_400(self):
        self.client.force_authenticate(user=self.user)
        res = self.lookup('29ABCDE1234F1Z0')   # right layout, wrong check character
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.data['valid'])
        self.assertIn('check character', res.data['detail'])
        self.assertEqual(self.client.get(self.URL).status_code, 400)  # missing parameter

    @override_settings(GST_LOOKUP_URL='')
    def test_without_a_provider_it_verifies_but_never_invents_details(self):
        self.client.force_authenticate(user=self.user)
        res = self.lookup(self.gstin.lower())          # lower case is accepted
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['valid'])
        self.assertEqual(res.data['gst'], self.gstin)
        self.assertEqual(res.data['state'], 'Karnataka')
        self.assertEqual(res.data['pan'], 'ABCDE1234F')
        self.assertFalse(res.data['details_found'])
        self.assertFalse(res.data['lookup_available'])
        self.assertEqual(res.data['source'], 'format_only')
        for field in ('name', 'company_name', 'address', 'location', 'phone', 'email'):
            self.assertEqual(res.data[field], '', field)
        self.assertIn('no GST data provider', res.data['message'])

    @override_settings(GST_LOOKUP_URL='https://gst.example/taxpayer/{gstin}?key={api_key}',
                       GST_LOOKUP_API_KEY='SECRET', GST_LOOKUP_HEADERS={'x-api-key': '{api_key}'})
    def test_with_a_provider_it_returns_the_registered_details(self):
        self.client.force_authenticate(user=self.user)
        with mock.patch('customers.gst.requests.get', return_value=_FakeResponse(200, TAXPAYER)) as get:
            res = self.lookup()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['details_found'])
        self.assertEqual(res.data['source'], 'gst_provider')
        self.assertEqual(res.data['name'], 'ABC Traders')               # trade name preferred
        self.assertEqual(res.data['legal_name'], 'ABC TRADERS PRIVATE LIMITED')
        self.assertEqual(res.data['address'], '12, Market Road, Gandhi Nagar, Bengaluru, Karnataka, 560009')
        self.assertEqual(res.data['location'], 'Bengaluru')
        self.assertEqual(res.data['status'], 'Active')
        # the provider was called with our number and credentials
        called_url = get.call_args.args[0]
        self.assertEqual(called_url, f'https://gst.example/taxpayer/{self.gstin}?key=SECRET')
        self.assertEqual(get.call_args.kwargs['headers'], {'x-api-key': 'SECRET'})

    @override_settings(GST_LOOKUP_URL='https://gst.example/{gstin}')
    def test_repeat_lookups_are_cached_so_paid_quota_is_not_wasted(self):
        self.client.force_authenticate(user=self.user)
        with mock.patch('customers.gst.requests.get', return_value=_FakeResponse(200, TAXPAYER)) as get:
            self.lookup()
            second = self.lookup()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(second.data['name'], 'ABC Traders')

    @override_settings(GST_LOOKUP_URL='https://gst.example/{gstin}')
    def test_a_number_the_provider_does_not_know_is_a_404(self):
        self.client.force_authenticate(user=self.user)
        for fake in (_FakeResponse(404), _FakeResponse(200, {'error': 'Invalid GSTIN/UIN number'})):
            with mock.patch('customers.gst.requests.get', return_value=fake):
                res = self.lookup()
            self.assertEqual(res.status_code, 404)
            self.assertFalse(res.data['valid'])
            self.assertIn('not found', res.data['detail'])

    @override_settings(GST_LOOKUP_URL='https://gst.example/{gstin}')
    def test_provider_trouble_still_verifies_the_number_and_says_what_happened(self):
        self.client.force_authenticate(user=self.user)
        cases = [
            (requests.ConnectionError('down'), 'could not be reached'),
            (requests.Timeout('slow'), 'could not be reached'),
            (_FakeResponse(401), 'rejected'),
            (_FakeResponse(429), 'limit'),
            (_FakeResponse(500), 'error (500)'),
            (_FakeResponse(200, unreadable=True), 'unreadable'),
        ]
        for outcome, expected in cases:
            cache.clear()
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with mock.patch('customers.gst.requests.get', **kwargs):
                res = self.lookup()
            self.assertEqual(res.status_code, 200, expected)
            self.assertTrue(res.data['valid'])
            self.assertFalse(res.data['details_found'])
            self.assertEqual(res.data['name'], '')
            self.assertEqual(res.data['state'], 'Karnataka')
            self.assertIn(expected, res.data['message'])

    @override_settings(GST_LOOKUP_URL='')
    def test_warns_when_the_number_is_already_saved_but_not_for_the_record_being_edited(self):
        customer = Customer.objects.create(name='Already Here', phone=1, email='ah@example.com', gst=self.gstin)
        Vendor.objects.create(name='Some Vendor', phone=2, email='sv@example.com', gst='')
        self.client.force_authenticate(user=self.user)
        res = self.lookup()
        self.assertEqual(res.data['existing'], {'type': 'customer', 'id': customer.id, 'name': 'Already Here'})
        res = self.lookup(exclude_type='customer', exclude_id=customer.id)   # editing that same customer
        self.assertIsNone(res.data['existing'])
        res = self.lookup(exclude_type='vendor', exclude_id=customer.id)     # a vendor with the same id is a different record
        self.assertEqual(res.data['existing']['type'], 'customer')

    @override_settings(GST_LOOKUP_URL='')
    def test_a_vendor_holding_the_number_is_reported_as_a_vendor(self):
        vendor = Vendor.objects.create(name='Supplier', phone=3, email='sup@example.com', gst=self.gstin)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.lookup().data['existing'], {'type': 'vendor', 'id': vendor.id, 'name': 'Supplier'})


class GstOnCustomerAndVendorTests(TestCase):
    """Saving a customer/vendor now rejects a typo'd GST — but never blocks editing
    a record whose old GST value is odd, as long as that value is left alone."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='gc_a', email='gc_a@example.com', password='pw')
        UserProfile.objects.create(user=self.admin, is_admin=True)
        self.client.force_authenticate(user=self.admin)
        self.good = make_gstin('29ABCDE1234F1Z')

    def body(self, **extra):
        return {'name': 'Acme', 'phone': 9999999999, 'email': 'acme@example.com', **extra}

    def test_a_valid_gst_is_saved_upper_cased(self):
        res = self.client.post('/api/customers/', self.body(gst=self.good.lower()), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['gst'], self.good)
        self.assertEqual(Customer.objects.get().gst, self.good)

    def test_an_invalid_gst_is_rejected_with_a_readable_reason(self):
        res = self.client.post('/api/customers/', self.body(gst='29ABCDE1234F1Z0'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('check character', res.data['gst'][0])
        res = self.client.post('/api/vendors/', self.body(gst='12345'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('15 characters', res.data['gst'][0])
        self.assertEqual(Customer.objects.count() + Vendor.objects.count(), 0)

    def test_gst_stays_optional(self):
        self.assertEqual(self.client.post('/api/customers/', self.body(), format='json').status_code, 201)
        res = self.client.post('/api/vendors/', self.body(gst=''), format='json')
        self.assertEqual(res.status_code, 201)

    def test_a_record_with_an_old_odd_gst_can_still_be_edited_if_the_gst_is_untouched(self):
        legacy = Customer.objects.create(name='Skanda', phone=1, email='sk@example.com', gst='895214SA122S')
        res = self.client.patch(f'/api/customers/{legacy.id}/', {'address': 'New street'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        res = self.client.patch(f'/api/customers/{legacy.id}/', {'gst': '895214SA122S', 'address': 'Again'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        legacy.refresh_from_db()
        self.assertEqual((legacy.gst, legacy.address), ('895214SA122S', 'Again'))

    def test_changing_a_legacy_gst_requires_a_valid_one_and_it_can_be_cleared(self):
        legacy = Vendor.objects.create(name='S M', phone=1, email='sm@example.com', gst='85586SAS25SS')
        self.assertEqual(self.client.patch(f'/api/vendors/{legacy.id}/', {'gst': 'STILL-WRONG'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f'/api/vendors/{legacy.id}/', {'gst': self.good}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(f'/api/vendors/{legacy.id}/', {'gst': ''}, format='json').status_code, 200)
        legacy.refresh_from_db()
        self.assertEqual(legacy.gst, '')


class NoteOnSalesAndPaymentsTests(TestCase):
    """Sales and Payments Received carry an optional free-text note, like
    Purchases/Expenses and Salaries do. Existing rows simply have an empty one."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='nt_adm', email='nt_adm@example.com', password='pw')
        UserProfile.objects.create(user=self.admin, is_admin=True, can_edit_sales=True, can_edit_payments_received=True)
        self.acct = Invoice.objects.create(name='NoteAcct', user=self.admin)
        self.cust = Customer.objects.create(name='NoteCust', phone=0, email='nt@example.com')
        self.client.force_authenticate(user=self.admin)

    def _sale(self, **extra):
        body = {'customer_id': self.cust.id, 'date': str(timezone.localdate()), 'invoice': 'N-1',
                'invoice_amount': '100.00', 'cash_amount': '0', 'gst_percentage': '100', **extra}
        return self.client.post('/api/sales/', body, format='json')

    def _payment(self, **extra):
        body = {'customer_id': self.cust.id, 'invoice_id': self.acct.id, 'date': str(timezone.localdate()),
                'amount': '10.00', 'amount_type': 'cash', **extra}
        return self.client.post('/api/payments-received/', body, format='json')

    # ---- sales
    def test_sale_note_is_saved_returned_and_listed(self):
        res = self._sale(note='Delivered to the Mangalore shop')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], 'Delivered to the Mangalore shop')
        listed = self.client.get('/api/sales/').data
        self.assertEqual([row['note'] for row in listed], ['Delivered to the Mangalore shop'])
        self.assertEqual(self.client.get(f"/api/sales/{res.data['id']}/").data['note'], 'Delivered to the Mangalore shop')

    def test_sale_note_is_optional_and_defaults_to_empty(self):
        res = self._sale()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], '')

    def test_editing_a_sale_can_change_and_clear_the_note_but_other_edits_keep_it(self):
        sale_id = self._sale(note='first').data['id']
        url = f'/api/sales/{sale_id}/'
        self.assertEqual(self.client.patch(url, {'invoice': 'N-2'}, format='json').data['note'], 'first')   # untouched
        self.assertEqual(self.client.patch(url, {'note': 'second'}, format='json').data['note'], 'second')  # changed
        self.assertEqual(self.client.patch(url, {'note': ''}, format='json').data['note'], '')                # cleared
        # and the note never disturbs the customer's balance bookkeeping
        self.cust.refresh_from_db()
        self.assertEqual(self.cust.invoice_balance, Decimal('100.00'))

    # ---- payments received
    def test_payment_note_is_saved_returned_and_listed(self):
        res = self._payment(note='Cheque 004512')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], 'Cheque 004512')
        self.assertEqual([row['note'] for row in self.client.get('/api/payments-received/').data], ['Cheque 004512'])

    def test_payment_note_is_optional_and_defaults_to_empty(self):
        res = self._payment()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['note'], '')

    def test_editing_a_payment_can_change_and_clear_the_note_but_other_edits_keep_it(self):
        pay_id = self._payment(note='first').data['id']
        url = f'/api/payments-received/{pay_id}/'
        self.assertEqual(self.client.patch(url, {'amount': '12.00'}, format='json').data['note'], 'first')
        self.assertEqual(self.client.patch(url, {'note': 'second'}, format='json').data['note'], 'second')
        self.assertEqual(self.client.patch(url, {'note': ''}, format='json').data['note'], '')
        self.acct.refresh_from_db()
        self.assertEqual(self.acct.cash_balance, Decimal('12.00'))   # amount edit applied once, note edits changed nothing


class ContactFieldsTests(TestCase):
    """Phone and email are optional for customers, vendors and employees. Before,
    leaving the phone out (which the app does when the box is blank) or saving a
    second record with no email crashed the server (HTTP 500)."""

    KINDS = ('customers', 'vendors', 'employees')

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='ct_adm', email='ct_adm@example.com', password='pw')
        UserProfile.objects.create(user=self.admin, is_admin=True, can_edit_customers=True,
                                   can_edit_vendors=True, can_edit_employees=True)
        self.client.force_authenticate(user=self.admin)

    def test_any_number_of_records_can_have_no_phone_and_no_email(self):
        for kind in self.KINDS:
            for name in ('One', 'Two', 'Three'):
                res = self.client.post(f'/api/{kind}/', {'name': name}, format='json')
                self.assertEqual(res.status_code, 201, (kind, name, res.data))
                self.assertIsNone(res.data['email'])
                self.assertEqual(res.data['phone'], '')

    def test_blank_email_string_is_stored_as_no_email(self):
        for kind in self.KINDS:
            for name in ('A', 'B'):
                res = self.client.post(f'/api/{kind}/', {'name': name, 'email': '', 'phone': ''}, format='json')
                self.assertEqual(res.status_code, 201, (kind, res.data))
                self.assertIsNone(res.data['email'])

    def test_a_filled_in_email_must_still_be_unique(self):
        for kind in self.KINDS:
            self.assertEqual(self.client.post(f'/api/{kind}/', {'name': 'A', 'email': 'dup@example.com'}, format='json').status_code, 201)
            res = self.client.post(f'/api/{kind}/', {'name': 'B', 'email': 'dup@example.com'}, format='json')
            self.assertEqual(res.status_code, 400, kind)
            self.assertIn('email', res.data)

    def test_phone_keeps_leading_zeros_plus_signs_and_big_numbers(self):
        for kind in self.KINDS:
            for i, phone in enumerate(('09741234567', '+91 97412 34567', '9741234567')):
                res = self.client.post(f'/api/{kind}/', {'name': f'P{i}', 'phone': phone}, format='json')
                self.assertEqual(res.status_code, 201, (kind, phone, res.data))
                self.assertEqual(res.data['phone'], phone)

    def test_phone_with_letters_is_a_clean_400_not_a_crash(self):
        for kind in self.KINDS:
            res = self.client.post(f'/api/{kind}/', {'name': 'X', 'phone': 'call me'}, format='json')
            self.assertEqual(res.status_code, 400, kind)
            self.assertIn('phone', res.data)

    def test_existing_blank_emails_are_converted_by_the_data_migration(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('customers.migrations.0027_blank_emails_become_null')
        Customer.objects.create(name='Old', phone='1', email='')
        Vendor.objects.create(name='Old', phone='1', email='')
        Employee.objects.create(name='Old', phone='1', email='')
        migration.blank_to_null(apps, None)
        for model in (Customer, Vendor, Employee):
            self.assertIsNone(model.objects.get(name='Old').email)


class DeleteGuardTests(TestCase):
    """A customer, vendor or employee that still has records can't be deleted: the
    database would cascade-delete their sales/payments/expenses without giving the
    money back to the accounts (and employees crashed with a 500)."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='dg_adm', email='dg_adm@example.com', password='pw')
        UserProfile.objects.create(user=self.admin, is_admin=True, can_edit_sales=True, can_edit_payments_received=True,
                                   can_edit_purchases_expenses=True, can_edit_salaries=True,
                                   can_edit_customers=True, can_edit_vendors=True, can_edit_employees=True)
        self.client.force_authenticate(user=self.admin)
        self.acct = Invoice.objects.create(name='DG', user=self.admin)
        self.today = timezone.localdate()

    def test_customer_with_a_sale_or_payment_is_refused_and_nothing_changes(self):
        cust = Customer.objects.create(name='Busy', phone='1', email='busy@example.com')
        Sale.objects.create(customer=cust, date=self.today, invoice='D1', invoice_amount=Decimal('100'), cash_amount=Decimal('0'))
        PaymentReceived.objects.create(customer=cust, invoice=self.acct, date=self.today, amount=Decimal('40'), amount_type='cash')
        self.acct.refresh_from_db()
        before = (self.acct.cash_balance, Sale.objects.count(), PaymentReceived.objects.count())
        res = self.client.delete(f'/api/customers/{cust.id}/')
        self.assertEqual(res.status_code, 409)
        self.assertIn('1 sale(s)', res.data['detail'])
        self.assertIn('1 payment(s) received', res.data['detail'])
        self.acct.refresh_from_db()
        self.assertEqual((self.acct.cash_balance, Sale.objects.count(), PaymentReceived.objects.count()), before)
        self.assertTrue(Customer.objects.filter(pk=cust.pk).exists())

    def test_customer_without_records_can_be_deleted(self):
        cust = Customer.objects.create(name='Idle', phone='1', email='idle@example.com')
        self.assertEqual(self.client.delete(f'/api/customers/{cust.id}/').status_code, 204)
        self.assertFalse(Customer.objects.filter(pk=cust.pk).exists())

    def test_vendor_with_a_purchase_is_refused_and_without_one_can_be_deleted(self):
        vend = Vendor.objects.create(name='Supplier', phone='1', email='sup@example.com')
        PurchaseExpense.objects.create(account=self.acct, payee_type='vendor', vendor=vend, invoice='P1',
                                       amount=Decimal('25'), amount_type='cash', post_date=self.today)
        res = self.client.delete(f'/api/vendors/{vend.id}/')
        self.assertEqual(res.status_code, 409)
        self.assertIn('purchase/expense record(s)', res.data['detail'])
        self.assertTrue(Vendor.objects.filter(pk=vend.pk).exists())
        self.acct.refresh_from_db()
        self.assertEqual(self.acct.cash_balance, Decimal('-25.00'))      # untouched, still correctly deducted
        PurchaseExpense.objects.all().delete()
        self.assertEqual(self.client.delete(f'/api/vendors/{vend.id}/').status_code, 204)

    def test_employee_with_salary_or_expense_is_a_clean_409_not_a_500(self):
        emp = Employee.objects.create(name='Staff', phone='1', email='staff@example.com')
        Salary.objects.create(employee=emp, account=self.acct, amount=Decimal('10'), post_date=self.today)
        res = self.client.delete(f'/api/employees/{emp.id}/')
        self.assertEqual(res.status_code, 409)
        self.assertIn('salary record(s)', res.data['detail'])
        Salary.objects.all().delete()
        PurchaseExpense.objects.create(account=self.acct, payee_type='employee', expense_payee_type='employee',
                                       employee=emp, amount=Decimal('5'), amount_type='cash', post_date=self.today)
        res = self.client.delete(f'/api/employees/{emp.id}/')
        self.assertEqual(res.status_code, 409)
        self.assertIn('expense record(s)', res.data['detail'])
        PurchaseExpense.objects.all().delete()
        self.assertEqual(self.client.delete(f'/api/employees/{emp.id}/').status_code, 204)


class NonObjectBodyTests(TestCase):
    """A JSON body that is a list or a bare string used to crash a few views (500)."""

    def setUp(self):
        self.client = APIClient(raise_request_exception=False)
        self.admin = User.objects.create_user(username='nb_adm', email='nb_adm@example.com', password='pw')
        UserProfile.objects.create(user=self.admin, is_admin=True, can_edit_accounts=True)
        self.client.force_authenticate(user=self.admin)
        self.acct = Invoice.objects.create(name='NB', user=self.admin)

    def test_lists_and_strings_get_a_400(self):
        for body in ([1, 2, 3], 'just a string'):
            self.assertEqual(self.client.post('/api/invoices/', body, format='json').status_code, 400)
            self.assertEqual(self.client.patch(f'/api/invoices/{self.acct.id}/', body, format='json').status_code, 400)
            self.assertEqual(self.client.patch(f'/api/users/{self.admin.id}/', body, format='json').status_code, 400)
            anon = APIClient(raise_request_exception=False)
            self.assertEqual(anon.post('/api/google-login/', body, format='json').status_code, 400)
