from django import forms
from django.contrib import admin
from django.utils.safestring import mark_safe

from .models import Employee, Invoice, Customer, PaymentReceived, PurchaseExpense, Sale, UserProfile, Vendor, AuditLog, Salary


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_email', 'is_main_admin', 'can_edit_customers', 'can_edit_vendors',
                     'can_edit_accounts', 'can_edit_sales', 'can_edit_payments_received',
                     'can_edit_purchases_expenses', 'can_edit_salaries', 'can_edit_employees')
    list_filter = ('is_main_admin',)
    search_fields = ('user__email',)

    def get_email(self, obj):
        return obj.user.email
    get_email.short_description = 'Email'


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'email', 'location', 'address', 'invoice_balance', 'cash_balance', 'total_balance', 'gst')
    search_fields = ('name', 'phone', 'email', 'gst')
    list_filter = ('location',)
    readonly_fields = ('invoice_balance', 'cash_balance', 'total_balance')

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'email', 'location', 'address', 'gst')
    search_fields = ('name', 'phone', 'email', 'gst')
    list_filter = ('location',)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'email', 'location', 'address')
    search_fields = ('name', 'phone', 'email')
    list_filter = ('location',)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'invoice_balance', 'cash_balance', 'post_account_balance', 'get_user_name')
    search_fields = ('name',)
    readonly_fields = ('get_user_name', 'post_account_balance', 'user', 'invoice_balance', 'cash_balance')
    fields = ('name', 'invoice_balance', 'cash_balance', 'post_account_balance', 'user')

    def get_user_name(self, obj):
        if obj.user:
            return obj.user.first_name or obj.user.email
        return "-"
    get_user_name.short_description = 'User'

    def save_model(self, request, obj, form, change):
        """When creating a new Account, automatically set it to the current user."""
        if not change:  # Creating a new object
            obj.user = request.user
        super().save_model(request, obj, form, change)

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_customer_name', 'date', 'invoice', 'invoice_amount', 'cash_amount', 'gst_percentage')
    search_fields = ('customer__name', 'invoice')
    list_filter = ('date',)

    def get_customer_name(self, obj):
        return obj.customer.name
    get_customer_name.short_description = 'Customer'


@admin.register(PaymentReceived)
class PaymentReceivedAdmin(admin.ModelAdmin):
    list_display = ('id', 'get_customer_name', 'get_invoice_name', 'date', 'amount', 'amount_type', )
    search_fields = ('customer__name', 'invoice__name', )
    list_filter = ('date', 'amount_type')

    def get_customer_name(self, obj):
        return obj.customer.name
    get_customer_name.short_description = 'Customer'

    def get_invoice_name(self, obj):
        return obj.invoice.name
    get_invoice_name.short_description = 'Account'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Restrict Account dropdown to show only Accounts created by the current user."""
        if db_field.name == 'invoice':
            kwargs['queryset'] = Invoice.objects.filter(user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class PayeeTypeSelect(forms.Select):
    """'Payee type' dropdown that shows Purchase OR Expense (no separate JS file needed)."""
    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        script = """
<script>
document.addEventListener("DOMContentLoaded", function () {
  var type = document.getElementById("id_payee_type");
  var vendorRow = document.querySelector(".field-vendor");
  var employeeRow = document.querySelector(".field-employee");
  var invoiceRow = document.querySelector(".field-invoice");
  var amountTypeRow = document.querySelector(".field-amount_type");
    var expenseTypeRow = document.querySelector(".field-expense_payee_type");
    var noteRow = document.querySelector(".field-note");
  if (!type || !vendorRow || !employeeRow) return;
    function toggle() {
        var isEmployee = type.value === "employee";
        // First dropdown: if Purchase -> show vendor + invoice + amount_type
        // if Expense -> show expense_payee_type and then decide
        expenseTypeRow && (expenseTypeRow.style.display = isEmployee ? "" : "none");
        if (!isEmployee) {
            vendorRow.style.display = "";
            employeeRow.style.display = "none";
            invoiceRow && (invoiceRow.style.display = "");
            amountTypeRow && (amountTypeRow.style.display = "");
            noteRow && (noteRow.style.display = "none");
            return;
        }
        // Expense selected: look at expense_payee_type
        var expenseType = document.getElementById("id_expense_payee_type");
        var et = expenseType ? expenseType.value : '';
        // default hide all
        vendorRow.style.display = "none";
        employeeRow.style.display = "none";
        invoiceRow && (invoiceRow.style.display = "none");
        amountTypeRow && (amountTypeRow.style.display = "none");
        noteRow && (noteRow.style.display = "none");
        if (et === 'vendor') {
            vendorRow.style.display = "";
            invoiceRow && (invoiceRow.style.display = "");
            amountTypeRow && (amountTypeRow.style.display = "");
        } else if (et === 'employee') {
            employeeRow.style.display = "";
            amountTypeRow && (amountTypeRow.style.display = "");
            // account always visible; invoice hidden
        } else if (et === 'other') {
            noteRow && (noteRow.style.display = "");
            amountTypeRow && (amountTypeRow.style.display = "");
            // account visible, vendor/employee/invoice hidden
        }
    }
  type.addEventListener("change", toggle);
    var expenseSelector = document.getElementById("id_expense_payee_type");
    if (expenseSelector) expenseSelector.addEventListener("change", toggle);
  toggle();
});
</script>"""
        return mark_safe(html + script)


class PurchaseExpenseAdminForm(forms.ModelForm):
    class Meta:
        model = PurchaseExpense
        fields = '__all__'
        widgets = {'payee_type': PayeeTypeSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Invoice is hidden for employees, so the form must allow it empty.
        # (models.py clean() still makes it required for vendors.)
        self.fields['invoice'].required = False
        self.fields['amount_type'].required = False 
        # Hide label for the first payee_type dropdown
        self.fields['payee_type'].label = ''
        self.fields['expense_payee_type'].label = 'Payee type'  
        self.fields['amount_type'].help_text = "Required for all payee types (Cash or Invoice)."


@admin.register(PurchaseExpense)
class PurchaseExpenseAdmin(admin.ModelAdmin):
    form = PurchaseExpenseAdminForm
    list_display = (
        'id', 'payee_type', 'get_vendor', 'get_employee', 'get_other', 'get_account_name',
        'date', 'invoice', 'amount', 'amount_type', 'post_date', 'get_payment_status',
    )
    search_fields = ('vendor__name', 'employee__name', 'note', 'account__name', 'invoice')
    list_filter = ('date', 'amount_type', 'payee_type')
    readonly_fields = ('date',)
    fields = ('payee_type', 'expense_payee_type', 'vendor', 'employee', 'account', 'invoice', 'amount',
              'amount_type', 'note', 'created_at', 'post_date', 'processed', 'date')

    def save_model(self, request, obj, form, change):
        # Capture the pre-save post_date so we know if it actually changed.
        old_post_date = None
        if change:
            old_post_date = PurchaseExpense.objects.get(pk=obj.pk).post_date

        super().save_model(request, obj, form, change)

        # Process deduction if post_date is now due (on update only, to avoid double-processing)
        if change and obj.post_date != old_post_date:
            obj.finalize_if_due()

    def get_vendor(self, obj):
        return obj.vendor.name if obj.vendor else '-'
    get_vendor.short_description = 'Vendor'

    def get_employee(self, obj):
        return obj.employee.name if obj.employee else '-'
    get_employee.short_description = 'Employee'

    def get_other(self, obj):
        # The note is shown only for "Other" expenses, never under Vendor/Employee.
        if obj.payee_type == 'employee' and obj.expense_payee_type == 'other' and obj.note:
            return obj.note
        return '-'
    get_other.short_description = 'Other'

    def get_account_name(self, obj):
        return obj.account.name if obj.account else "-"
    get_account_name.short_description = "Account"

    def get_payment_status(self, obj):
        return obj.payment_status
    get_payment_status.short_description = 'Payment Status'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Restrict Account dropdown to show only Accounts created by the current user."""
        if db_field.name == 'account':
            kwargs['queryset'] = Invoice.objects.filter(user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Salary)
class SalaryAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'get_account_name', 'date', 'amount', 'post_date', 'processed')
    search_fields = ('employee__name', 'account__name')
    list_filter = ('date',)
    readonly_fields = ('date',)
    fields = ('employee', 'account', 'amount', 'remark', 'created_at', 'post_date', 'processed')

    def get_account_name(self, obj):
        return obj.account.name if obj.account else "-"
    get_account_name.short_description = 'Account'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Restrict Account dropdown to show only Accounts created by the current user."""
        if db_field.name == 'account':
            kwargs['queryset'] = Invoice.objects.filter(user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'actor_name', 'actor_email', 'action', 'module', 'description', 'created_at')
    list_filter = ('action', 'module', 'created_at')
    search_fields = ('actor_name', 'actor_email', 'module', 'description')
    readonly_fields = ('actor_name', 'actor_email', 'action', 'module', 'description', 'created_at')
    ordering = ('-created_at', '-id')