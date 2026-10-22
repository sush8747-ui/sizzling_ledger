from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    """
    One row per logged-in user. Created automatically on first Google
    login. is_main_admin is set (and re-asserted) automatically whenever
    the logged-in email matches settings.MAIN_ADMIN_EMAIL — no one can
    revoke it through the API. Everyone else starts fully read-only;
    the main admin grants edit access per resource via /api/users/.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    is_main_admin = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)
    can_edit_customers = models.BooleanField(default=False)
    can_edit_vendors = models.BooleanField(default=False)
    can_edit_accounts = models.BooleanField(default=False)
    can_edit_sales = models.BooleanField(default=False)
    can_edit_payments_received = models.BooleanField(default=False)
    can_edit_purchases_expenses = models.BooleanField(default=False)
    can_edit_salaries = models.BooleanField(default=False)
    can_edit_employees = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user.email} ({'admin' if self.is_main_admin else 'user'})"


class Customer(models.Model):
    name = models.CharField(max_length=255)
    # Text, not a number: keeps leading zeros / +91, never overflows, and may be empty.
    phone = models.CharField(max_length=20, blank=True, default='')
    # Optional but unique once filled in: stored as NULL (not '') when blank, so any
    # number of records may have no email without clashing on the unique rule.
    email = models.EmailField(unique=True, blank=True, null=True)
    address = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    gst = models.CharField(max_length=50, blank=True)

    # These values are shown by the Customer API but not accepted as API input.
    invoice_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    @property
    def total_balance(self):
        """Calculated at runtime; this value has no database column."""
        return self.invoice_balance + self.cash_balance

    def __str__(self):
        return self.name


class Vendor(models.Model):
    name = models.CharField(max_length=255)
    # Text, not a number: keeps leading zeros / +91, never overflows, and may be empty.
    phone = models.CharField(max_length=20, blank=True, default='')
    # Optional but unique once filled in: stored as NULL (not '') when blank, so any
    # number of records may have no email without clashing on the unique rule.
    email = models.EmailField(unique=True, blank=True, null=True)
    address = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    gst = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.name


class Employee(models.Model):
    name = models.CharField(max_length=255)
    # Text, not a number: keeps leading zeros / +91, never overflows, and may be empty.
    phone = models.CharField(max_length=20, blank=True, default='')
    # Optional but unique once filled in: stored as NULL (not '') when blank, so any
    # number of records may have no email without clashing on the unique rule.
    email = models.EmailField(unique=True, blank=True, null=True)
    address = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return self.name


class Invoice(models.Model):
    name = models.CharField(max_length=255)
    invoice_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    post_account_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Account'
        verbose_name_plural = 'Accounts'
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='unique_invoice_name_per_user'),
        ]

    def __str__(self):
        return self.name


class PaymentReceived(models.Model):
    AMOUNT_TYPE_CHOICES = (('cash', 'Cash'), ('invoice', 'Invoice'))

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments_received')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments_received', verbose_name='Account')
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_type = models.CharField(max_length=10, choices=AMOUNT_TYPE_CHOICES)
    # Free text the user can attach to the payment (reference, cheque no., remark...).
    note = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    def _move_balances(self, sign):
        """sign=+1 applies this payment to the account and customer, sign=-1 undoes it.
        F() expressions are used so the result is right even if the in-memory
        account/customer objects are stale."""
        field = 'invoice_balance' if self.amount_type == 'invoice' else 'cash_balance'
        Invoice.objects.filter(pk=self.invoice_id).update(**{field: models.F(field) + sign * self.amount})
        Customer.objects.filter(pk=self.customer_id).update(**{field: models.F(field) - sign * self.amount})

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old = None if is_new else PaymentReceived.objects.filter(pk=self.pk).first()
        super().save(*args, **kwargs)
        if not is_new and old is not None:
            # An edit: undo what the old values did, then apply the new ones. This is
            # what makes changing the amount, type, account or customer safe.
            old._move_balances(-1)
            self._move_balances(+1)
        if is_new:
            if self.amount_type == 'invoice':
                self.invoice.invoice_balance += self.amount
                self.invoice.save(update_fields=['invoice_balance'])
                self.customer.invoice_balance -= self.amount
                self.customer.save(update_fields=['invoice_balance'])
            else:
                self.invoice.cash_balance += self.amount
                self.invoice.save(update_fields=['cash_balance'])
                self.customer.cash_balance -= self.amount
                self.customer.save(update_fields=['cash_balance'])
    def delete(self, *args, **kwargs):
        # Reverse exactly what save() applied, so deleting a payment doesn't
        # leave the account/customer balances permanently off.
        if self.amount_type == 'invoice':
            self.invoice.invoice_balance -= self.amount
            self.invoice.save(update_fields=['invoice_balance'])
            self.customer.invoice_balance += self.amount
            self.customer.save(update_fields=['invoice_balance'])
        else:
            self.invoice.cash_balance -= self.amount
            self.invoice.save(update_fields=['cash_balance'])
            self.customer.cash_balance += self.amount
            self.customer.save(update_fields=['cash_balance'])
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.customer.name} paid {self.amount} ({self.amount_type})"


from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Sale(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='sales')
    date = models.DateField()
    invoice = models.CharField(max_length=100)
    invoice_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    # Free text the user can attach to the sale.
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old = None if is_new else Sale.objects.filter(pk=self.pk).first()
        super().save(*args, **kwargs)
        if not is_new and old is not None:
            # An edit: take the old amounts off the old customer, then add the new
            # amounts to the (possibly different) customer.
            Customer.objects.filter(pk=old.customer_id).update(
                invoice_balance=models.F('invoice_balance') - old.invoice_amount,
                cash_balance=models.F('cash_balance') - old.cash_amount,
            )
            Customer.objects.filter(pk=self.customer_id).update(
                invoice_balance=models.F('invoice_balance') + self.invoice_amount,
                cash_balance=models.F('cash_balance') + self.cash_amount,
            )
        if is_new:
            self.customer.invoice_balance += self.invoice_amount
            self.customer.cash_balance += self.cash_amount
            self.customer.save(update_fields=['invoice_balance', 'cash_balance'])
    def delete(self, *args, **kwargs):
        # Reverse exactly what save() applied, so deleting a sale doesn't
        # leave the customer's balances permanently off.
        self.customer.invoice_balance -= self.invoice_amount
        self.customer.cash_balance -= self.cash_amount
        self.customer.save(update_fields=['invoice_balance', 'cash_balance'])
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.customer.name} - Invoice {self.invoice}"


class PurchaseExpense(models.Model):
    AMOUNT_TYPE_CHOICES = (('cash', 'Cash'), ('invoice', 'Invoice'))

    PAYEE_CHOICES = (('vendor', 'Purchase'), ('employee', 'Expense'))

    payee_type = models.CharField(max_length=10, choices=PAYEE_CHOICES, default='vendor')
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, related_name='purchases_expenses',
        null=True, blank=True,
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name='purchases_expenses',
        null=True, blank=True,
    )
    expense_payee_type = models.CharField(max_length=10, choices=(
        ('employee', 'Employee'), ('vendor', 'Vendor'), ('other', 'Other')
    ), blank=True, default='')
    account = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='purchases_expenses',
        null=True, blank=True,
    )
    date = models.DateField(auto_now_add=True)
    invoice = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_type = models.CharField(max_length=10, choices=AMOUNT_TYPE_CHOICES)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    post_date = models.DateField(blank=True, null=True)
    processed = models.BooleanField(default=False)  # prevents double-deduction once Paid is applied

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        if not self.post_date:
            self.post_date = timezone.localdate()

        if is_new:
            self.processed = self.post_date <= timezone.localdate()
            super().save(*args, **kwargs)
            if self.processed:
                self._apply_deduction()                     # due already -> old logic, unchanged
            else:
                self._adjust_account(self.account_id, post_account_balance=self.amount)  # not due -> park as pending
            return

        # ---- Editing an existing record ----
        # Fetch what this record looked like in the DB *before* this edit,
        # so we can undo exactly what it applied, then re-apply with the
        # new amount/account/amount_type. Without this, editing an
        # already-saved record changes the record but leaves the account
        # balance stuck at the old value.
        old = PurchaseExpense.objects.get(pk=self.pk)
        super().save(*args, **kwargs)

        if old.processed:
            if old.amount_type == 'invoice':
                self._adjust_account(old.account_id, invoice_balance=old.amount)   # give old deduction back
            else:
                self._adjust_account(old.account_id, cash_balance=old.amount)
        else:
            self._adjust_account(old.account_id, post_account_balance=-old.amount)  # remove old pending amount

        if self.processed:
            self._apply_deduction()
        else:
            self._adjust_account(self.account_id, post_account_balance=self.amount)

    @staticmethod
    def _adjust_account(account_id, invoice_balance=0, cash_balance=0, post_account_balance=0):
        """Add the given deltas to an account, always re-reading it fresh
        from the DB first (never trusts a possibly-stale cached FK)."""
        if not account_id:
            return
        account = Invoice.objects.get(pk=account_id)
        if invoice_balance:
            account.invoice_balance += invoice_balance
            account.save(update_fields=['invoice_balance'])
        if cash_balance:
            account.cash_balance += cash_balance
            account.save(update_fields=['cash_balance'])
        if post_account_balance:
            account.post_account_balance += post_account_balance
            account.save(update_fields=['post_account_balance'])

    @property
    def payee_name(self):
        # For payee_type == 'vendor', show vendor name. For payee_type == 'employee',
        # consult expense_payee_type: vendor -> vendor name; employee -> employee name;
        # other -> note/Other.
        if self.payee_type == 'vendor':
            return self.vendor.name if self.vendor else ''
        # payee_type == 'employee'
        if self.expense_payee_type == 'vendor':
            return self.vendor.name if self.vendor else ''
        if self.expense_payee_type == 'employee':
            return self.employee.name if self.employee else ''
        # other
        if self.note:
            return (self.note.split('\n', 1)[0])[:255]
        return 'Other'

    def clean(self):
        # payee_type 'vendor' (Purchase): vendor + invoice + amount_type required
        if self.payee_type == 'vendor':
            if not self.vendor_id:
                raise ValidationError({'vendor': 'Select a vendor.'})
            if not self.invoice:
                raise ValidationError({'invoice': 'Invoice is required for a vendor.'})
            if not self.amount_type:
                raise ValidationError({'amount_type': 'Amount type is required for a vendor.'})
            # clear expense-only fields
            self.employee = None
            self.expense_payee_type = ''
            return

        # payee_type == 'employee' (Expense): expense_payee_type required
        if not self.expense_payee_type:
            raise ValidationError({'expense_payee_type': 'Select an expense payee type.'})

        if self.expense_payee_type == 'vendor':
            if not self.vendor_id:
                raise ValidationError({'vendor': 'Select a vendor.'})
            if not self.invoice:
                raise ValidationError({'invoice': 'Invoice is required for a vendor.'})
            if not self.amount_type:
                raise ValidationError({'amount_type': 'Amount type is required for a vendor.'})
            self.employee = None
        elif self.expense_payee_type == 'employee':
            if not self.employee_id:
                raise ValidationError({'employee': 'Select an employee.'})
            if not self.amount_type:
                raise ValidationError({'amount_type': 'Amount type is required.'})
            self.vendor = None
            self.invoice = ''
        else:  # other
            if not self.note:
                raise ValidationError({'note': 'Provide a note for Other payee type.'})
            if not self.amount_type:
                raise ValidationError({'amount_type': 'Amount type is required.'})
            self.vendor = None
            self.employee = None
            self.invoice = ''

    def _apply_deduction(self):
        if self.amount_type == 'invoice':
            self._adjust_account(self.account_id, invoice_balance=-self.amount)
        else:
            self._adjust_account(self.account_id, cash_balance=-self.amount)

    def delete(self, *args, **kwargs):
        # Reverse whatever this record applied to its account before it's
        # removed — otherwise the amount stays stuck on the account forever
        # with no record left to explain it.
        if self.processed:
            if self.amount_type == 'invoice':
                self._adjust_account(self.account_id, invoice_balance=self.amount)
            else:
                self._adjust_account(self.account_id, cash_balance=self.amount)
        elif self.account_id:
            self._adjust_account(self.account_id, post_account_balance=-self.amount)
        super().delete(*args, **kwargs)

    def finalize_if_due(self):
        """Called by the daily scheduled job. Moves the pending amount out of
        post_account_balance and applies the real deduction, once, on the day post_date arrives."""
        if not self.processed and self.post_date and self.post_date <= timezone.localdate():
            self._adjust_account(self.account_id, post_account_balance=-self.amount)
            self._apply_deduction()
            # Update the DB row directly rather than calling self.save() —
            # save() now does full old-vs-new balance reconciliation, which
            # we've already done by hand above, so calling it here would
            # apply the same adjustment a second time.
            self.processed = True
            PurchaseExpense.objects.filter(pk=self.pk).update(processed=True)

    @property
    def payment_status(self):
        # Once a deduction has actually been applied (processed=True), the
        # record stays "Paid" even if post_date is later edited to a future
        # date — otherwise the status would contradict the fact that the
        # money is already gone from the account.
        if self.processed:
            return 'Paid'
        if self.post_date and self.post_date <= timezone.localdate():
            return 'Paid'
        return 'Unpaid'

    @property
    def due_tomorrow(self):
        """True for exactly one day: the day before post_date arrives and
        this record flips from Unpaid to Paid. Used to trigger the
        reminder popup in the app."""
        if self.processed or not self.post_date:
            return False
        return self.post_date == timezone.localdate() + timezone.timedelta(days=1)

    def __str__(self):
        return f"{self.payee_name} - {self.invoice}"


def finalize_due_purchase_expenses():                     # <-- ADD THIS (not indented — module level, not inside the class)
    """Settle every PurchaseExpense whose post_date has arrived but
    hasn't been processed yet. Call this before returning any
    Invoice/PurchaseExpense data so post_account_balance and
    payment_status never drift apart."""
    today = timezone.localdate()
    due_qs = PurchaseExpense.objects.filter(
    processed=False, post_date__lte=today
    ).select_related('account')
    for pe in due_qs:
        pe.finalize_if_due()
    # Also finalize any due salaries so post_account_balance stays correct
    try:
        finalize_due_salaries()
    except NameError:
        # finalize_due_salaries may be defined later in the file; if not, ignore
        pass


class Salary(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name='salaries'
    )
    account = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='salaries'
    )
    AMOUNT_TYPE_CHOICES = (('cash', 'Cash'), ('invoice', 'Invoice'))

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Which balance of the account the salary is paid from. Defaults to cash,
    # which is how every salary behaved before this field existed.
    amount_type = models.CharField(max_length=10, choices=AMOUNT_TYPE_CHOICES, default='cash')
    remark = models.TextField(blank=True)
    date = models.DateField(auto_now_add=True)
    post_date = models.DateField(blank=True, null=True)
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        if not self.post_date:
            self.post_date = timezone.localdate()

        if is_new:
            self.processed = self.post_date <= timezone.localdate()
            super().save(*args, **kwargs)
            if self.processed:
                self._apply_deduction()
            else:
                PurchaseExpense._adjust_account(self.account_id, post_account_balance=self.amount)
            return

        old = Salary.objects.get(pk=self.pk)
        super().save(*args, **kwargs)

        if old.processed:
            old._give_back()   # returns the money to whichever balance it came from
        else:
            PurchaseExpense._adjust_account(old.account_id, post_account_balance=-old.amount)

        if self.processed:
            self._apply_deduction()
        else:
            PurchaseExpense._adjust_account(self.account_id, post_account_balance=self.amount)

    @property
    def employee_name(self):
        return self.employee.name if self.employee else ''

    def _apply_deduction(self):
        if self.amount_type == 'invoice':
            PurchaseExpense._adjust_account(self.account_id, invoice_balance=-self.amount)
        else:
            PurchaseExpense._adjust_account(self.account_id, cash_balance=-self.amount)

    def _give_back(self):
        """Undo a deduction that was already applied (used on edit and delete)."""
        if self.amount_type == 'invoice':
            PurchaseExpense._adjust_account(self.account_id, invoice_balance=self.amount)
        else:
            PurchaseExpense._adjust_account(self.account_id, cash_balance=self.amount)

    def delete(self, *args, **kwargs):
        if self.processed:
            self._give_back()
        elif self.account_id:
            PurchaseExpense._adjust_account(self.account_id, post_account_balance=-self.amount)
        super().delete(*args, **kwargs)

    def finalize_if_due(self):
        if not self.processed and self.post_date and self.post_date <= timezone.localdate():
            PurchaseExpense._adjust_account(self.account_id, post_account_balance=-self.amount)
            self._apply_deduction()
            self.processed = True
            Salary.objects.filter(pk=self.pk).update(processed=True)

    @property
    def payment_status(self):
        if self.processed:
            return 'Paid'
        if self.post_date and self.post_date <= timezone.localdate():
            return 'Paid'
        return 'Unpaid'

    @property
    def due_tomorrow(self):
        if self.processed or not self.post_date:
            return False
        return self.post_date == timezone.localdate() + timezone.timedelta(days=1)


def finalize_due_salaries():
    today = timezone.localdate()
    due_qs = Salary.objects.filter(processed=False, post_date__lte=today).select_related('account')
    for s in due_qs:
        s.finalize_if_due()


# Extend finalize_due_purchase_expenses to also finalize salaries
def _finalize_purchase_and_salaries():
    finalize_due_purchase_expenses()
    finalize_due_salaries()


class AuditLog(models.Model):
    ACTION_CHOICES = (
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('deleted', 'Deleted'),
        ('viewed', 'Viewed'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='audit_logs',
    )
    # Snapshot of who did it, so the log stays readable even if the user is deleted.
    actor_name = models.CharField(max_length=255, blank=True)
    actor_email = models.EmailField(blank=True)
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    module = models.CharField(max_length=50, db_index=True)
    description = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.actor_name or self.actor_email}: {self.description}"
