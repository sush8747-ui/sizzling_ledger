import re

from django.db import transaction
from django.db.models import F
from rest_framework import serializers

from .gst import gstin_problem, normalize_gstin

from .models import (
    Employee,
    Invoice,
    Customer,
    PaymentReceived,
    PurchaseExpense,
    Sale,
    UserProfile,
    Vendor,
    AuditLog,
    Salary,
)
from .utils import get_visible_accounts


class UserProfileSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source='user.id', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    name = serializers.CharField(source='user.first_name', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'id', 'email', 'name', 'is_main_admin', 'is_admin',
                'can_edit_customers', 'can_edit_vendors', 'can_edit_accounts',
                'can_edit_sales', 'can_edit_payments_received', 'can_edit_purchases_expenses', 'can_edit_salaries', 'can_edit_employees',
        ]
        read_only_fields = ['id', 'email', 'name', 'is_main_admin']

    def validate(self, attrs):
        if 'is_main_admin' in attrs:
            raise serializers.ValidationError({
                'is_main_admin': 'The main admin flag is system-managed and cannot be changed via the API.'
            })
        return super().validate(attrs)


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ['id', 'action', 'module', 'description', 'actor_name', 'actor_email', 'created_at']
        read_only_fields = ['id', 'actor_name', 'actor_email', 'created_at']


class ContactFieldsMixin:
    """Optional phone / email handling shared by Customer, Vendor and Employee."""

    def validate_phone(self, value):
        value = (value or '').strip()
        if value and not re.fullmatch(r'[0-9+\-\s()]{1,20}', value):
            raise serializers.ValidationError('Enter a valid phone number (digits, +, spaces and dashes only).')
        return value

    def validate_email(self, value):
        # Blank means "no email": stored as NULL so many records may have none
        # (a unique column would otherwise reject the second '').
        return (value or '').strip() or None


class CustomerSerializer(ContactFieldsMixin, serializers.ModelSerializer):
    total_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "address",
            "location",
            "invoice_balance",
            "cash_balance",
            "total_balance",
            "gst",
        ]
        read_only_fields = ("invoice_balance", "cash_balance", "total_balance")

    def validate_gst(self, value):
        """Normalise to upper case and require a real GSTIN, but only when the value
        is new or changed. A record saved earlier with an odd GST value stays
        editable (its other fields) without being forced to fix the GST first."""
        value = normalize_gstin(value)
        if not value:
            return ''
        if self.instance is not None and normalize_gstin(self.instance.gst) == value:
            return value
        problem = gstin_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value


class VendorSerializer(ContactFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = ["id", "name", "phone", "email", "address", "location", "gst"]

    def validate_gst(self, value):
        """Normalise to upper case and require a real GSTIN, but only when the value
        is new or changed. A record saved earlier with an odd GST value stays
        editable (its other fields) without being forced to fix the GST first."""
        value = normalize_gstin(value)
        if not value:
            return ''
        if self.instance is not None and normalize_gstin(self.instance.gst) == value:
            return value
        problem = gstin_problem(value)
        if problem:
            raise serializers.ValidationError(problem)
        return value


class EmployeeSerializer(ContactFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ["id", "name", "phone", "email", "address", "location", "salary"]


def _owns_account(request, account):
    """True when the logged-in user created `account`. Drives `is_mine` on the
    transaction serializers so the app can hide Edit/Delete on rows that sit on
    someone else's account (the backend enforces the same rule with a 403)."""
    user = getattr(request, 'user', None)
    if account is None or not user or not user.is_authenticated:
        return False
    return account.user_id == user.id


class InvoiceSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField(read_only=True)
    user = serializers.IntegerField(source='user.id', read_only=True)
    is_mine = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Invoice
        fields = ['id', 'name', 'invoice_balance', 'cash_balance', 'post_account_balance', 'user', 'user_name', 'is_mine']
        read_only_fields = ['invoice_balance', 'cash_balance', 'post_account_balance', 'user', 'user_name', 'is_mine']

    def get_user_name(self, obj):
        if obj.user:
            return obj.user.first_name or obj.user.email
        return None

    def get_is_mine(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.user_id == request.user.id
        return False


class PaymentReceivedSerializer(serializers.ModelSerializer):
    # customer_id / invoice_id are accepted on input AND returned, so an edit form can preselect them
    customer_id = serializers.IntegerField(required=False)
    customer_name = serializers.CharField(write_only=True, required=False)
    invoice_id = serializers.IntegerField(required=False)
    invoice_name = serializers.CharField(write_only=True, required=False)
    customer = serializers.CharField(source='customer.name', read_only=True)
    invoice = serializers.CharField(source='invoice.name', read_only=True)
    is_mine = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PaymentReceived
        fields = ['id', 'customer', 'customer_id', 'customer_name', 'invoice', 'invoice_id',
                  'invoice_name', 'date', 'amount', 'amount_type', 'note', 'is_mine']

    def get_is_mine(self, obj):
        return _owns_account(self.context.get('request'), obj.invoice)

    def validate(self, attrs):
        customer_id, customer_name = attrs.get('customer_id'), attrs.get('customer_name')
        invoice_id, invoice_name = attrs.get('invoice_id'), attrs.get('invoice_name')

        editing = self.instance is not None   # an edit may leave customer/account unchanged
        if not editing and not customer_id and not customer_name:
            raise serializers.ValidationError("Provide either customer_id or customer_name.")
        if not editing and not invoice_id and not invoice_name:
            raise serializers.ValidationError("Provide either invoice_id or invoice_name.")

        if customer_id or customer_name:
            try:
                customer = Customer.objects.get(id=customer_id) if customer_id else Customer.objects.get(name=customer_name)
            except Customer.DoesNotExist:
                raise serializers.ValidationError("No customer found with the given id/name.")
            attrs['customer'] = customer

        request = self.context.get('request') if hasattr(self, 'context') else None
        accounts_qs = get_visible_accounts(request) if request is not None else Invoice.objects.none()

        if invoice_id or invoice_name:
            try:
                if invoice_id:
                    invoice = accounts_qs.get(id=invoice_id)
                else:
                    matches = list(accounts_qs.filter(name=invoice_name))
                    if not matches:
                        raise Invoice.DoesNotExist()
                    invoice = matches[0]
            except Invoice.DoesNotExist:
                raise serializers.ValidationError("No invoice found with the given id/name.")
            attrs['invoice'] = invoice
        for f in ('customer_id', 'customer_name', 'invoice_id', 'invoice_name'):
            attrs.pop(f, None)
        return attrs

    def create(self, validated_data):
        return PaymentReceived.objects.create(**validated_data)


class SaleSerializer(serializers.ModelSerializer):
    # accepted on input AND returned, so an edit form can preselect the customer
    customer_id = serializers.IntegerField(required=False)
    customer_name = serializers.CharField(write_only=True, required=False)
    customer = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model = Sale
        fields = ['id', 'customer', 'customer_id', 'customer_name', 'date', 'invoice',
                  'invoice_amount', 'cash_amount', 'gst_percentage', 'note']

    def validate(self, attrs):
        customer_id = attrs.get('customer_id')
        customer_name = attrs.get('customer_name')
        if self.instance is None and not customer_id and not customer_name:
            raise serializers.ValidationError("Provide either customer_id or customer_name.")
        if customer_id or customer_name:
            try:
                customer = Customer.objects.get(id=customer_id) if customer_id else Customer.objects.get(name=customer_name)
            except Customer.DoesNotExist:
                raise serializers.ValidationError("No customer found with the given id/name.")
            attrs['customer'] = customer
        attrs.pop('customer_id', None)
        attrs.pop('customer_name', None)
        return attrs

    def create(self, validated_data):
        return Sale.objects.create(**validated_data)


class PurchaseExpenseSerializer(serializers.ModelSerializer):
    vendor_id = serializers.IntegerField(write_only=True, required=False)
    vendor_name = serializers.CharField(write_only=True, required=False)
    employee_id = serializers.IntegerField(write_only=True, required=False)
    employee_name = serializers.CharField(write_only=True, required=False)
    account_id = serializers.IntegerField(write_only=True, required=False)
    account_name = serializers.CharField(write_only=True, required=False)
    vendor = serializers.SerializerMethodField(read_only=True)
    employee = serializers.SerializerMethodField(read_only=True)
    account = serializers.SerializerMethodField(read_only=True)
    payment_status = serializers.SerializerMethodField(read_only=True)
    due_tomorrow = serializers.SerializerMethodField(read_only=True)
    # Stored and returned, so the app can tell a vendor / employee / other expense apart.
    expense_payee_type = serializers.CharField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    is_mine = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PurchaseExpense
        fields = [
            'id', 'payee_type', 'expense_payee_type', 'vendor', 'vendor_id', 'vendor_name',
            'employee', 'employee_id', 'employee_name',
            'account', 'account_id', 'account_name',
            'date', 'invoice', 'amount', 'amount_type', 'note',
            'post_date', 'payment_status', 'due_tomorrow', 'is_mine'
        ]
        read_only_fields = ['payment_status', 'due_tomorrow']
        extra_kwargs = {
            'invoice': {'required': False, 'allow_blank': True},
            'amount_type': {'required': False},
        }

    def get_is_mine(self, obj):
        return _owns_account(self.context.get('request'), obj.account)

    def validate_expense_payee_type(self, value):
        if value not in ('', 'employee', 'vendor', 'other'):
            raise serializers.ValidationError('Must be one of: employee, vendor, other.')
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Rows saved before this field was stored: work the type out from what they hold.
        if instance.payee_type == 'employee' and not data.get('expense_payee_type'):
            data['expense_payee_type'] = 'vendor' if instance.vendor_id else ('employee' if instance.employee_id else 'other')
        return data

    def get_vendor(self, obj):
        return obj.vendor.name if obj.vendor else None

    def get_employee(self, obj):
        return obj.employee.name if obj.employee else None

    def get_account(self, obj):
        return obj.account.name if obj.account else None

    def get_payment_status(self, obj):
        return obj.payment_status

    def get_due_tomorrow(self, obj):
        return obj.due_tomorrow

    def validate(self, attrs):
        vendor_id, vendor_name = attrs.get('vendor_id'), attrs.get('vendor_name')
        employee_id, employee_name = attrs.get('employee_id'), attrs.get('employee_name')
        account_id, account_name = attrs.get('account_id'), attrs.get('account_name')

        payee_type = attrs.get('payee_type') or (self.instance.payee_type if self.instance else 'vendor')
        expense_payee_type = attrs.get('expense_payee_type') or (self.instance.expense_payee_type if self.instance else '')
        note_val = attrs.get('note')

        if self.instance is None:
            if payee_type == 'vendor' and not vendor_id and not vendor_name:
                raise serializers.ValidationError("Provide either vendor_id or vendor_name.")
            if payee_type == 'employee' and not expense_payee_type:
                raise serializers.ValidationError({'expense_payee_type': ['This field is required for an Expense.']})
            if payee_type == 'employee' and expense_payee_type == 'employee' and not employee_id and not employee_name:
                raise serializers.ValidationError("Provide either employee_id or employee_name.")
            if payee_type == 'employee' and expense_payee_type == 'vendor' and not vendor_id and not vendor_name:
                raise serializers.ValidationError("Provide either vendor_id or vendor_name.")
            if not account_id and not account_name:
                raise serializers.ValidationError("Provide either account_id or account_name.")

        is_vendor_kind = payee_type == 'vendor' or (payee_type == 'employee' and expense_payee_type == 'vendor')
        if is_vendor_kind and (vendor_id or vendor_name):
            try:
                attrs['vendor'] = Vendor.objects.get(id=vendor_id) if vendor_id else Vendor.objects.get(name=vendor_name)
            except Vendor.DoesNotExist:
                raise serializers.ValidationError("No vendor found with the given id/name.")

        # If expense and employee subtype chosen, resolve employee
        if payee_type == 'employee' and expense_payee_type == 'employee' and (employee_id or employee_name):
            try:
                attrs['employee'] = Employee.objects.get(id=employee_id) if employee_id else Employee.objects.get(name=employee_name)
            except Employee.DoesNotExist:
                raise serializers.ValidationError("No employee found with the given id/name.")

        # Enforce rules mirroring models.clean()
        if payee_type == 'vendor':
            # Purchase: vendor + invoice + amount_type required
            attrs['employee'] = None
            attrs['expense_payee_type'] = ''
            # leave vendor as resolved above
        else:
            # Expense: ensure expense_payee_type present
            if not expense_payee_type:
                raise serializers.ValidationError({'expense_payee_type': ['This field is required for an Expense.']})
            attrs['expense_payee_type'] = expense_payee_type
            if expense_payee_type == 'vendor':
                # vendor + invoice + amount_type required
                attrs['employee'] = None
            elif expense_payee_type == 'employee':
                # employee required, amount_type required
                attrs['vendor'] = None
                attrs['invoice'] = ''
            else:  # other
                attrs['vendor'] = None
                attrs['employee'] = None
                attrs['invoice'] = ''
                # on a partial edit that doesn't resend the note, keep the stored one
                effective_note = note_val if note_val is not None else (self.instance.note if self.instance else '')
                if not (effective_note or '').strip():
                    raise serializers.ValidationError({'note': ['This field is required for expense_payee_type "other".']})
                attrs['note'] = effective_note

        request = self.context.get('request') if hasattr(self, 'context') else None
        accounts_qs = get_visible_accounts(request) if request is not None else Invoice.objects.none()
        if account_id or account_name:
            try:
                if account_id:
                    attrs['account'] = accounts_qs.get(id=account_id)
                else:
                    matches = list(accounts_qs.filter(name=account_name))
                    if not matches:
                        raise Invoice.DoesNotExist()
                    attrs['account'] = matches[0]
            except Invoice.DoesNotExist:
                raise serializers.ValidationError("No account found with the given id/name.")

        if payee_type == 'vendor':
            if self.instance is None:
                if not attrs.get('invoice'):
                    raise serializers.ValidationError({'invoice': ['This field is required.']})
                if not attrs.get('amount_type'):
                    raise serializers.ValidationError({'amount_type': ['This field is required.']})
        elif payee_type == 'employee':
            # Expense: all subtypes require amount_type
            if self.instance is None:
                if expense_payee_type == 'vendor':
                    if not attrs.get('invoice'):
                        raise serializers.ValidationError({'invoice': ['This field is required.']})
                if not attrs.get('amount_type'):
                    raise serializers.ValidationError({'amount_type': ['This field is required.']})

        for f in ('vendor_id', 'vendor_name', 'employee_id', 'employee_name', 'account_id', 'account_name'):
            attrs.pop(f, None)
        return attrs

    def create(self, validated_data):
        return PurchaseExpense.objects.create(**validated_data)

    def update(self, instance, validated_data):
        old_post_date = instance.post_date
        instance = super().update(instance, validated_data)
        if instance.post_date != old_post_date:
            instance.finalize_if_due()
        return instance


class SalarySerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField(write_only=True, required=False)
    employee_name = serializers.CharField(write_only=True, required=False)
    account_id = serializers.IntegerField(write_only=True, required=False)
    account_name = serializers.CharField(write_only=True, required=False)
    employee = serializers.CharField(source='employee.name', read_only=True)
    account = serializers.CharField(source='account.name', read_only=True)
    payment_status = serializers.SerializerMethodField(read_only=True)
    due_tomorrow = serializers.SerializerMethodField(read_only=True)
    is_mine = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Salary
        fields = [
            'id', 'employee', 'employee_id', 'employee_name', 'account', 'account_id', 'account_name',
            'amount', 'amount_type', 'remark', 'date', 'post_date', 'payment_status', 'due_tomorrow', 'is_mine'
        ]
        read_only_fields = ['payment_status', 'due_tomorrow']
        extra_kwargs = {}

    def get_is_mine(self, obj):
        return _owns_account(self.context.get('request'), obj.account)

    def get_payment_status(self, obj):
        return obj.payment_status

    def get_due_tomorrow(self, obj):
        return obj.due_tomorrow

    def validate(self, attrs):
        employee_id, employee_name = attrs.get('employee_id'), attrs.get('employee_name')
        account_id, account_name = attrs.get('account_id'), attrs.get('account_name')

        if self.instance is None:
            if not employee_id and not employee_name:
                raise serializers.ValidationError("Provide either employee_id or employee_name.")
            if not account_id and not account_name:
                raise serializers.ValidationError("Provide either account_id or account_name.")

        if employee_id or employee_name:
            try:
                attrs['employee'] = Employee.objects.get(id=employee_id) if employee_id else Employee.objects.get(name=employee_name)
            except Employee.DoesNotExist:
                raise serializers.ValidationError("No employee found with the given id/name.")

        request = self.context.get('request') if hasattr(self, 'context') else None
        accounts_qs = get_visible_accounts(request) if request is not None else Invoice.objects.none()
        if account_id or account_name:
            try:
                if account_id:
                    attrs['account'] = accounts_qs.get(id=account_id)
                else:
                    matches = list(accounts_qs.filter(name=account_name))
                    if not matches:
                        raise Invoice.DoesNotExist()
                    attrs['account'] = matches[0]
            except Invoice.DoesNotExist:
                raise serializers.ValidationError("No account found with the given id/name.")

        # Only check the amount when one is being sent: a partial edit (say, just a
        # new post date) leaves it out and keeps the saved amount. A create without
        # an amount is already refused earlier, as a required field.
        if 'amount' in attrs and (not attrs['amount'] or attrs['amount'] <= 0):
            raise serializers.ValidationError({'amount': ['Amount must be greater than zero.']})

        for f in ('employee_id', 'employee_name', 'account_id', 'account_name'):
            attrs.pop(f, None)
        return attrs

    def create(self, validated_data):
        return globals()['Salary'].objects.create(**validated_data)

    def update(self, instance, validated_data):
        old_post_date = instance.post_date
        instance = super().update(instance, validated_data)
        if instance.post_date != old_post_date:
            instance.finalize_if_due()
        return instance
