from django.conf import settings
from django.utils import timezone 
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from .models import finalize_due_purchase_expenses
from .models import (
    Employee, Invoice, Customer, PaymentReceived, PurchaseExpense, Sale, UserProfile, Vendor, AuditLog, Salary,
)
from .serializers import (
    InvoiceSerializer,
    CustomerSerializer,
    EmployeeSerializer,
    PaymentReceivedSerializer,
    PurchaseExpenseSerializer,
    SalarySerializer,
    SaleSerializer,
    UserProfileSerializer,
    VendorSerializer,
    AuditLogSerializer,
    
)
from .utils import get_visible_accounts, get_listable_accounts
from .gst import (
    GstNotFound,
    GstUnavailable,
    gstin_problem,
    lookup_configured,
    lookup_taxpayer,
    normalize_gstin,
    pan_from_gstin,
    state_name,
)


class SerializerContextMixin:
    """Lets DRF's browsable API render a proper HTML form, not just raw JSON."""
    serializer_class = None

    def get_serializer(self, *args, **kwargs):
        return self.serializer_class(*args, **kwargs)


# ---------------------------------------------------------------------------
# Permissions
#
# Everything here is enforced server-side, on purpose: the frontend never
# decides who can see or write what — it only reflects whatever this layer
# allows or the 403 it returns. Each resource (customers, vendors, accounts,
# sales, payments received, purchases & expenses) is gated by its own
# can_edit_* flag on the user's UserProfile for viewing, creating, and
# deleting: a user with can_edit_sales=True can view, add, and delete
# Sales; a user without it gets a 403 on Sales entirely, including GET.
# EDITING an existing record (PUT/PATCH) is different: no can_edit_* flag
# grants it. Only is_main_admin (set automatically for MAIN_ADMIN_EMAIL,
# can't be revoked through the API) or is_admin (a temporary admin
# promoted by the main admin) may edit anything, anywhere.
# ---------------------------------------------------------------------------
def get_profile(request):
    """Fetch (or create) the UserProfile for the current request's user,
    re-asserting is_main_admin if their email matches the configured admin
    email. Safe to call on every request."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    should_be_admin = (
        request.user.email
        and request.user.email.lower() == settings.MAIN_ADMIN_EMAIL.lower()
    )
    if should_be_admin and not profile.is_main_admin:
        profile.is_main_admin = True
        profile.save(update_fields=['is_main_admin'])
    return profile


def can_edit(request, flag):
    if not request.user or not request.user.is_authenticated:
        return False
    profile = get_profile(request)
    # is_admin (a temporary admin promoted by the main admin) gets full
    # write access everywhere too, same as is_main_admin.
    return profile.is_main_admin or profile.is_admin or getattr(profile, flag, False)


def can_view_any(request, *flags):
    return any(can_edit(request, f) for f in flags)


def is_admin_user(request):
    """True only for is_main_admin or is_admin — used to gate PUT/PATCH
    (editing an existing record) on every resource. No can_edit_* flag can
    grant edit access; only the two admin levels can edit."""
    if not request.user or not request.user.is_authenticated:
        return False
    profile = get_profile(request)
    return profile.is_main_admin or profile.is_admin


def can_manage_permissions(profile):
    """True if this profile is allowed to open /api/users/ at all — the
    main admin, or a temporary admin the main admin has promoted."""
    return profile.is_main_admin or profile.is_admin


def forbidden(resource):
    return Response(
        {"detail": f"You do not have permission to modify {resource}."},
        status=status.HTTP_403_FORBIDDEN,
    )


def forbidden_not_owner(resource):
    """Transactions are VISIBLE to everyone with the module permission, but
    can only be changed/deleted on accounts the caller created."""
    return Response(
        {"detail": f"You can only modify {resource} on accounts you created."},
        status=status.HTTP_403_FORBIDDEN,
    )


def owns_account(request, account):
    return account is not None and account.user_id == request.user.id


def duplicate_account_name_response(user, name, exclude_pk=None):
    """One account name per user (case-insensitive). Returns a readable 400
    response when `name` is already used by this user, else None. Without this
    the DB unique constraint would surface as a 500 server error."""
    name = (name or '').strip()
    if not name:
        return None
    qs = Invoice.objects.filter(user=user, name__iexact=name)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        return Response(
            {"detail": f"You already have an account named '{name}'. Choose a different name."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def invalid_body_response(request):
    """A JSON body that is a list or a bare string (not an object) used to crash
    views that read it like a dictionary. Answer 400 instead."""
    if hasattr(request.data, 'get') and hasattr(request.data, 'items'):
        return None
    return Response({"detail": "The request body must be a JSON object."}, status=status.HTTP_400_BAD_REQUEST)


def delete_blocked_response(name, records):
    """Deleting a customer/vendor/employee that still has records would either crash
    (employees are protected) or silently delete their sales, payments or expenses
    without giving the money back to the accounts. Refuse with a clear reason.
    `records` is a list of (label, count)."""
    parts = [f"{count} {label}" for label, count in records if count]
    if not parts:
        return None
    return Response(
        {"detail": f"{name} can't be deleted because it still has {' and '.join(parts)}. Delete those first."},
        status=status.HTTP_409_CONFLICT,
    )


def forbidden_view(resource):
    return Response(
        {"detail": f"You do not have permission to view {resource}."},
        status=status.HTTP_403_FORBIDDEN,
    )


class CustomerListCreateView(SerializerContextMixin, APIView):
    """GET all customers or POST a new customer."""
    serializer_class = CustomerSerializer

    def get(self, request):
        if not can_view_any(request, 'can_edit_customers', 'can_edit_sales', 'can_edit_payments_received'):
            return forbidden_view('customers')
        customers = Customer.objects.all()
        return Response(CustomerSerializer(customers, many=True).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_customers'):
            return forbidden('customers')
        serializer = CustomerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)


class CustomerDetailView(SerializerContextMixin, APIView):
    """GET, PATCH, PUT, or DELETE one customer."""
    serializer_class = CustomerSerializer

    def get_object(self, pk):
        try:
            return Customer.objects.get(pk=pk)
        except Customer.DoesNotExist:
            return None

    def get(self, request, pk):
        if not can_view_any(request, 'can_edit_customers', 'can_edit_sales', 'can_edit_payments_received'):
            return forbidden_view('customers')
        customer = self.get_object(pk)
        if customer is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(CustomerSerializer(customer).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('customers')
        customer = self.get_object(pk)
        if customer is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = CustomerSerializer(customer, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('customers')
        customer = self.get_object(pk)
        if customer is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        blocked = delete_blocked_response(customer.name, [
            ('sale(s)', Sale.objects.filter(customer=customer).count()),
            ('payment(s) received', PaymentReceived.objects.filter(customer=customer).count()),
        ])
        if blocked:
            return blocked
        customer.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VendorListCreateView(SerializerContextMixin, APIView):
    """GET all vendors or POST a new vendor."""
    serializer_class = VendorSerializer

    def get(self, request):
        if not can_view_any(request, 'can_edit_vendors', 'can_edit_purchases_expenses'):
            return forbidden_view('vendors')
        vendors = Vendor.objects.all()
        return Response(VendorSerializer(vendors, many=True).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_vendors'):
            return forbidden('vendors')
        serializer = VendorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vendor = serializer.save()
        return Response(VendorSerializer(vendor).data, status=status.HTTP_201_CREATED)


class VendorDetailView(SerializerContextMixin, APIView):
    """GET, PUT, PATCH, or DELETE one vendor."""
    serializer_class = VendorSerializer

    def get_object(self, pk):
        try:
            return Vendor.objects.get(pk=pk)
        except Vendor.DoesNotExist:
            return None

    def get(self, request, pk):
        if not can_view_any(request, 'can_edit_vendors', 'can_edit_purchases_expenses'):
            return forbidden_view('vendors')
        vendor = self.get_object(pk)
        if vendor is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(VendorSerializer(vendor).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('vendors')
        vendor = self.get_object(pk)
        if vendor is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = VendorSerializer(vendor, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('vendors')
        vendor = self.get_object(pk)
        if vendor is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        blocked = delete_blocked_response(vendor.name, [
            ('purchase/expense record(s)', PurchaseExpense.objects.filter(vendor=vendor).count()),
        ])
        if blocked:
            return blocked
        vendor.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeListCreateView(SerializerContextMixin, APIView):
    """GET all employees or POST a new employee."""
    serializer_class = EmployeeSerializer

    def get(self, request):
        # Users can VIEW employees if they are admin, can_edit_employees, or can_edit_purchases_expenses
        profile = get_profile(request)
        can_view = profile.is_main_admin or profile.is_admin or profile.can_edit_employees or profile.can_edit_purchases_expenses
        if not can_view:
            return forbidden_view('employees')
        return Response(EmployeeSerializer(Employee.objects.all(), many=True).data)

    def post(self, request):
        # Users can ADD employees only if they are admin or have can_edit_employees
        profile = get_profile(request)
        can_add = profile.is_main_admin or profile.is_admin or profile.can_edit_employees
        if not can_add:
            return forbidden('employees')
        serializer = EmployeeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee = serializer.save()
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)


class EmployeeDetailView(SerializerContextMixin, APIView):
    """GET, PUT, PATCH, or DELETE one employee (edit/delete: admins only)."""
    serializer_class = EmployeeSerializer

    def get_object(self, pk):
        return Employee.objects.filter(pk=pk).first()

    def get(self, request, pk):
        # Users can VIEW a single employee if they are admin, can_edit_employees, or can_edit_purchases_expenses
        profile = get_profile(request)
        can_view = profile.is_main_admin or profile.is_admin or profile.can_edit_employees or profile.can_edit_purchases_expenses
        if not can_view:
            return forbidden_view('employees')
        employee = self.get_object(pk)
        if employee is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(EmployeeSerializer(employee).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('employees')
        employee = self.get_object(pk)
        if employee is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = EmployeeSerializer(employee, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('employees')
        employee = self.get_object(pk)
        if employee is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        blocked = delete_blocked_response(employee.name, [
            ('salary record(s)', Salary.objects.filter(employee=employee).count()),
            ('expense record(s)', PurchaseExpense.objects.filter(employee=employee).count()),
        ])
        if blocked:
            return blocked
        employee.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvoiceListCreateView(SerializerContextMixin, APIView):
    """GET all accounts (ledger 'Invoice' rows) or POST a new one."""
    serializer_class = InvoiceSerializer

    def get(self, request):
        # Determine which accounts to return based on query param
        scope = request.query_params.get('scope', 'all')
        
        if scope == 'mine':
            # Allow ?scope=mine if user has any of: can_edit_accounts, can_edit_payments_received,
            # can_edit_purchases_expenses, can_edit_salaries
            if not can_view_any(request, 'can_edit_accounts', 'can_edit_payments_received', 
                                'can_edit_purchases_expenses', 'can_edit_salaries'):
                return forbidden_view('accounts')
            accounts = get_visible_accounts(request)  # own accounts only
        else:
            # Default: return all accounts, but only if user has can_edit_accounts
            if not can_edit(request, 'can_edit_accounts'):
                return forbidden_view('accounts')
            accounts = get_listable_accounts(request)  # all accounts for authenticated users
        
        finalize_due_purchase_expenses()
        return Response(InvoiceSerializer(accounts, many=True, context={'request': request}).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_accounts'):
            return forbidden('accounts')
        bad_body = invalid_body_response(request)
        if bad_body:
            return bad_body
        duplicate = duplicate_account_name_response(request.user, request.data.get('name'))
        if duplicate is not None:
            return duplicate
        serializer = InvoiceSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        invoice = serializer.save(user=request.user)
        return Response(InvoiceSerializer(invoice, context={'request': request}).data, status=status.HTTP_201_CREATED)


class InvoiceDetailView(SerializerContextMixin, APIView):
    """GET, PATCH or PUT one account (Invoice row). There is no DELETE.

    GET: anyone with an accounts/payments/expenses/salaries permission can view any account.
    PUT/PATCH (rename): only the account's creator, and only while they still have
    the Accounts permission. Nobody else can edit it — admins and the main admin included.
    DELETE: not supported (405). Deleting an account would cascade-delete its payments,
    expenses and salaries without reversing customer balances.
    """
    serializer_class = InvoiceSerializer

    def get_object(self, pk):
        """Get any invoice by ID, without user filtering."""
        try:
            return Invoice.objects.get(pk=pk)
        except Invoice.DoesNotExist:
            return None

    def get(self, request, pk):
        if not can_view_any(request, 'can_edit_accounts', 'can_edit_payments_received', 'can_edit_purchases_expenses', 'can_edit_salaries'):
            return forbidden_view('accounts')
        finalize_due_purchase_expenses()
        invoice = self.get_object(pk)
        if invoice is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(InvoiceSerializer(invoice, context={'request': request}).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not can_edit(request, 'can_edit_accounts'):
            return forbidden('accounts')
        invoice = self.get_object(pk)
        if invoice is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if invoice.user_id != request.user.id:
            return Response(
                {"detail": "You can only edit accounts you created."},
                status=status.HTTP_403_FORBIDDEN,
            )
        bad_body = invalid_body_response(request)
        if bad_body:
            return bad_body
        if 'name' in request.data:
            duplicate = duplicate_account_name_response(request.user, request.data.get('name'), exclude_pk=invoice.pk)
            if duplicate is not None:
                return duplicate
        serializer = InvoiceSerializer(invoice, data=request.data, partial=partial, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PaymentReceivedListCreateView(SerializerContextMixin, APIView):
    serializer_class = PaymentReceivedSerializer

    def get(self, request):
        if not can_edit(request, 'can_edit_payments_received'):
            return forbidden_view('payments received')
        # Show every payment; the own-account rule applies only when ADDING
        # (see PaymentReceivedSerializer.validate) and when changing/deleting.
        qs = PaymentReceived.objects.all()
        return Response(PaymentReceivedSerializer(qs, many=True, context={'request': request}).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_payments_received'):
            return forbidden('payments received')
        serializer = PaymentReceivedSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        payment = serializer.save()
        return Response(PaymentReceivedSerializer(payment, context={'request': request}).data, status=status.HTTP_201_CREATED)


class PaymentReceivedDetailView(SerializerContextMixin, APIView):
    serializer_class = PaymentReceivedSerializer

    def get_object(self, request, pk):
        return PaymentReceived.objects.filter(pk=pk).first()

    def get(self, request, pk):
        if not can_edit(request, 'can_edit_payments_received'):
            return forbidden_view('payments received')
        payment = self.get_object(request, pk)
        if payment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PaymentReceivedSerializer(payment, context={'request': request}).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('payments received')
        payment = self.get_object(request, pk)
        if payment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not owns_account(request, payment.invoice):
            return forbidden_not_owner('payments received')
        serializer = PaymentReceivedSerializer(
            payment, data=request.data, partial=partial, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('payments received')
        payment = self.get_object(request, pk)
        if payment is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not owns_account(request, payment.invoice):
            return forbidden_not_owner('payments received')
        payment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SaleListCreateView(SerializerContextMixin, APIView):
    serializer_class = SaleSerializer

    def get(self, request):
        if not can_edit(request, 'can_edit_sales'):
            return forbidden_view('sales')
        return Response(SaleSerializer(Sale.objects.all(), many=True).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_sales'):
            return forbidden('sales')
        serializer = SaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = serializer.save()
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)


class SaleDetailView(SerializerContextMixin, APIView):
    serializer_class = SaleSerializer

    def get_object(self, pk):
        try:
            return Sale.objects.get(pk=pk)
        except Sale.DoesNotExist:
            return None

    def get(self, request, pk):
        if not can_edit(request, 'can_edit_sales'):
            return forbidden_view('sales')
        sale = self.get_object(pk)
        if sale is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(SaleSerializer(sale).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('sales')
        sale = self.get_object(pk)
        if sale is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = SaleSerializer(sale, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('sales')
        sale = self.get_object(pk)
        if sale is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        sale.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PurchaseExpenseListCreateView(SerializerContextMixin, APIView):
    serializer_class = PurchaseExpenseSerializer

    def get(self, request):
        if not can_edit(request, 'can_edit_purchases_expenses'):
            return forbidden_view('purchases & expenses')
        finalize_due_purchase_expenses()
        qs = PurchaseExpense.objects.all()
        return Response(PurchaseExpenseSerializer(qs, many=True, context={'request': request}).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_purchases_expenses'):
            return forbidden('purchases & expenses')
        serializer = PurchaseExpenseSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        purchase = serializer.save()
        return Response(PurchaseExpenseSerializer(purchase, context={'request': request}).data, status=status.HTTP_201_CREATED)


class PurchaseExpenseDueTomorrowView(APIView):
    """GET: every unpaid PurchaseExpense whose post_date is tomorrow —
    i.e. the ones that are about to flip from Unpaid to Paid. The app
    calls this (e.g. on launch / dashboard load) and shows a popup for
    whatever comes back."""

    def get(self, request):
        if not can_edit(request, 'can_edit_purchases_expenses'):
            return forbidden_view('purchases & expenses')
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        due = PurchaseExpense.objects.filter(processed=False, post_date=tomorrow, account__in=get_visible_accounts(request))
        return Response(PurchaseExpenseSerializer(due, many=True, context={'request': request}).data)


class PurchaseExpenseDetailView(SerializerContextMixin, APIView):
    serializer_class = PurchaseExpenseSerializer

    def get_object(self, request, pk):
        return PurchaseExpense.objects.filter(pk=pk).first()

    def get(self, request, pk):
        if not can_edit(request, 'can_edit_purchases_expenses'):
            return forbidden_view('purchases & expenses')
        finalize_due_purchase_expenses()
        purchase = self.get_object(request, pk)
        if purchase is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PurchaseExpenseSerializer(purchase, context={'request': request}).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('purchases & expenses')
        purchase = self.get_object(request, pk)
        if purchase is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not owns_account(request, purchase.account):
            return forbidden_not_owner('purchases & expenses')
        serializer = PurchaseExpenseSerializer(
            purchase, data=request.data, partial=partial, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('purchases & expenses')
        purchase = self.get_object(request, pk)
        if purchase is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not owns_account(request, purchase.account):
            return forbidden_not_owner('purchases & expenses')
        purchase.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SalaryListCreateView(SerializerContextMixin, APIView):
    serializer_class = SalarySerializer

    def get(self, request):
        if not can_edit(request, 'can_edit_salaries'):
            return forbidden_view('salaries')
        finalize_due_purchase_expenses()
        qs = Salary.objects.all()
        return Response(self.serializer_class(qs, many=True, context={'request': request}).data)

    def post(self, request):
        if not can_edit(request, 'can_edit_salaries'):
            return forbidden('salaries')
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        salary = serializer.save()
        return Response(self.serializer_class(salary, context={'request': request}).data, status=status.HTTP_201_CREATED)


class SalaryDueTomorrowView(APIView):
    def get(self, request):
        if not can_edit(request, 'can_edit_salaries'):
            return forbidden_view('salaries')
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        due = Salary.objects.filter(processed=False, post_date=tomorrow, account__in=get_visible_accounts(request))
        return Response(SalarySerializer(due, many=True, context={'request': request}).data)


class SalaryDetailView(SerializerContextMixin, APIView):
    serializer_class = SalarySerializer

    def get_object(self, request, pk):
        return Salary.objects.filter(pk=pk).first()

    def get(self, request, pk):
        if not can_edit(request, 'can_edit_salaries'):
            return forbidden_view('salaries')
        finalize_due_purchase_expenses()
        salary = self.get_object(request, pk)
        if salary is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(self.serializer_class(salary, context={'request': request}).data)

    def put(self, request, pk):
        return self.update(request, pk, partial=False)

    def patch(self, request, pk):
        return self.update(request, pk, partial=True)

    def update(self, request, pk, partial):
        if not is_admin_user(request):
            return forbidden('salaries')
        salary = self.get_object(request, pk)
        if salary is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not owns_account(request, salary.account):
            return forbidden_not_owner('salaries')
        serializer = self.serializer_class(salary, data=request.data, partial=partial, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not is_admin_user(request):
            return forbidden('salaries')
        salary = self.get_object(request, pk)
        if salary is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not owns_account(request, salary.account):
            return forbidden_not_owner('salaries')
        salary.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserMeView(APIView):
    """GET: the current logged-in user's own profile + permission flags.
    Any authenticated user may call this for themselves — used on app
    relaunch, where the token is restored from secure storage but we
    still need to know this user's current permissions."""

    def get(self, request):
        profile = get_profile(request)
        return Response(UserProfileSerializer(profile).data)


class UserListView(APIView):
    """GET: list every user + their permission flags. Viewable by the
    main admin and by any temporary admin the main admin has promoted."""

    def get(self, request):
        profile = get_profile(request)
        if not can_manage_permissions(profile):
            return Response(
                {"detail": "You do not have permission to view the user list."},
                status=status.HTTP_403_FORBIDDEN,
            )
        profiles = UserProfile.objects.select_related('user').all()
        return Response(UserProfileSerializer(profiles, many=True).data)


class UserPermissionDetailView(APIView):
    """PATCH: update one user's role/per-resource edit flags.

    Rules:
      - is_main_admin can NEVER be changed via this endpoint, by anyone.
        The only way to change who the main admin is is to edit
        settings.MAIN_ADMIN_EMAIL.
      - is_admin (temporary admin) can only be granted or revoked by the
        main admin. A temporary admin cannot promote or demote anyone.
      - A temporary admin can grant/revoke the ordinary can_edit_* flags,
        but only on plain users — never on the main admin, and never on
        another admin. Only the main admin can touch another admin's
        permissions.
    """

    def patch(self, request, pk):
        profile = get_profile(request)
        if not can_manage_permissions(profile):
            return Response(
                {"detail": "You do not have permission to change permissions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            target = UserProfile.objects.get(user_id=pk)
        except UserProfile.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        bad_body = invalid_body_response(request)
        if bad_body:
            return bad_body
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)

        # is_main_admin: never changeable. Only error if someone is
        # actually trying to flip it — if it's present but unchanged (e.g.
        # a client PATCHing back a full copy of a GET response), just
        # drop it instead of failing the whole request.
        if 'is_main_admin' in data:
            if bool(data['is_main_admin']) != target.is_main_admin:
                return Response(
                    {"detail": "The main admin flag is system-managed and cannot be changed via the API."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            data.pop('is_main_admin')

        if target.is_main_admin:
            return Response(
                {"detail": "The main admin's own permissions cannot be changed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only the main admin may promote/demote a temporary admin.
        if 'is_admin' in data and not profile.is_main_admin:
            return Response(
                {"detail": "Only the main admin can grant or revoke admin access."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # A temporary admin (not the main admin) may only manage plain
        # users — never another admin's permissions.
        if not profile.is_main_admin and target.is_admin:
            return Response(
                {"detail": "Only the main admin can change another admin's permissions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UserProfileSerializer(target, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


_google_request = google_requests.Request()


class GoogleLoginView(APIView):
    # This endpoint must be reachable WITHOUT already having a token —
    # it overrides the global IsAuthenticated/TokenAuthentication defaults.
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        bad_body = invalid_body_response(request)
        if bad_body:
            return bad_body
        id_token_str = request.data.get('id_token')
        if not id_token_str:
            return Response({"error": "id_token is required"}, status=status.HTTP_400_BAD_REQUEST)

        allowed_client_ids = [
            c
            for c in [
                getattr(settings, "GOOGLE_CLIENT_ID", ""),
                *getattr(settings, "GOOGLE_EXTRA_CLIENT_IDS", []),
            ]
            if c
        ]
        if not allowed_client_ids:
            return Response(
                {"error": "Google sign-in is not set up on the server. Add GOOGLE_CLIENT_ID to the .env file."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            idinfo = id_token.verify_oauth2_token(
                id_token_str,
                _google_request,
                allowed_client_ids,
            )
        except ValueError as e:
            print("GOOGLE TOKEN VERIFY FAILED:", str(e))
            return Response({"error": "Invalid or expired Google token"}, status=status.HTTP_401_UNAUTHORIZED)
        email = idinfo.get('email')
        name = idinfo.get('name', '')
        if not email:
            return Response({"error": "Google account has no email"}, status=status.HTTP_400_BAD_REQUEST)
        email = email.strip().lower()
        user, created = User.objects.get_or_create(
            username=email,
            defaults={"email": email, "first_name": name},
        )
        token, _ = Token.objects.get_or_create(user=user)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        is_admin_email = email.lower() == settings.MAIN_ADMIN_EMAIL.lower()
        if is_admin_email and not profile.is_main_admin:
            profile.is_main_admin = True
            profile.save(update_fields=['is_main_admin'])

        return Response({
            "token": token.key,
            "created": created,
            "email": user.email,
            "name": user.first_name,
            "is_main_admin": profile.is_main_admin,
            "is_admin": profile.is_admin,
            "can_edit_customers": profile.can_edit_customers,
            "can_edit_vendors": profile.can_edit_vendors,
            "can_edit_accounts": profile.can_edit_accounts,
            "can_edit_sales": profile.can_edit_sales,
            "can_edit_payments_received": profile.can_edit_payments_received,
            "can_edit_purchases_expenses": profile.can_edit_purchases_expenses,
            "can_edit_salaries": profile.can_edit_salaries,
            "can_edit_employees": profile.can_edit_employees,
        }, status=status.HTTP_200_OK)


class GSTLookupView(APIView):
    """Check a GST number and, when a GST data provider is configured, fetch the
    business details a form can auto-fill.

    GET /api/gst-lookup/?gst=<GSTIN>[&exclude_type=customer|vendor&exclude_id=<id>]

    * Always: validates the format, state code and check character (so typos are
      caught), and returns the state and PAN.
    * With a provider configured (see settings.GST_LOOKUP_URL): also returns the
      registered legal / trade name, address, location and status.
    * Nothing is ever made up. With no provider, or if it is down, `details_found`
      is false and `message` says why.

    Needs a logged-in user who can manage customers or vendors (a configured
    provider is a paid service, so this must not be open to the public).
    """

    def get(self, request):
        if not (can_edit(request, 'can_edit_customers') or can_edit(request, 'can_edit_vendors')):
            return forbidden_view('GST details')

        gstin = normalize_gstin(request.query_params.get('gst') or request.query_params.get('gstin'))
        problem = gstin_problem(gstin)
        if problem:
            return Response(
                {"valid": False, "gst": gstin, "detail": problem, "message": problem},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = {
            "valid": True,
            "gst": gstin,
            "state": state_name(gstin),
            "state_code": gstin[:2],
            "pan": pan_from_gstin(gstin),
            "source": "format_only",
            "details_found": False,
            "lookup_available": lookup_configured(),
            "name": "",
            "company_name": "",
            "legal_name": "",
            "trade_name": "",
            "address": "",
            "location": "",
            "pincode": "",
            "status": "",
            "phone": "",   # only filled when the GST provider returns contact details
            "email": "",
            "existing": self._existing_holder(
                gstin, request.query_params.get('exclude_type'), request.query_params.get('exclude_id')
            ),
            "message": "",
        }

        if not lookup_configured():
            result["message"] = (
                "Format, check character and state verified. The registered name and "
                "address were not fetched because no GST data provider is set up on the server."
            )
            return Response(result)

        try:
            info = lookup_taxpayer(gstin)
        except GstNotFound:
            message = "This GST number was not found on the GST portal. Check it and try again."
            return Response(
                {**result, "valid": False, "detail": message, "message": message},
                status=status.HTTP_404_NOT_FOUND,
            )
        except GstUnavailable as exc:
            result["message"] = (
                f"Format, check character and state verified, but {exc}. "
                "Try again later or enter the details by hand."
            )
            return Response(result)

        result.update(
            source="gst_provider",
            details_found=True,
            name=info["trade_name"] or info["legal_name"],
            company_name=info["legal_name"] or info["trade_name"],
            legal_name=info["legal_name"],
            trade_name=info["trade_name"],
            address=info["address"],
            location=info["location"],
            pincode=info["pincode"],
            status=info["status"],
            phone=info.get("phone", ""),
            email=info.get("email", ""),
            message="GST details fetched.",
        )
        if info["state"]:
            result["state"] = info["state"]
        return Response(result)

    @staticmethod
    def _existing_holder(gstin, exclude_type, exclude_id):
        """The saved customer/vendor that already uses this GSTIN, if any, so the
        form can warn about a duplicate. When editing, the record being edited is
        left out (exclude_type + exclude_id)."""
        try:
            exclude_id = int(exclude_id)
        except (TypeError, ValueError):
            exclude_id = None
        customers = Customer.objects.filter(gst__iexact=gstin)
        vendors = Vendor.objects.filter(gst__iexact=gstin)
        if exclude_id is not None and exclude_type == 'customer':
            customers = customers.exclude(pk=exclude_id)
        if exclude_id is not None and exclude_type == 'vendor':
            vendors = vendors.exclude(pk=exclude_id)
        found = customers.first()
        if found:
            return {"type": "customer", "id": found.id, "name": found.name}
        found = vendors.first()
        if found:
            return {"type": "vendor", "id": found.id, "name": found.name}
        return None


class AuditLogListCreateView(APIView):
    """GET: audit entries, newest first. Admins see everyone's activity;
    everyone else sees only their own.
    POST: record one entry. Who did it is taken from the auth token, never
    from the request body, so the actor can't be spoofed."""

    def get(self, request):
        profile = get_profile(request)
        qs = AuditLog.objects.all()
        if not (profile.is_main_admin or profile.is_admin):
            qs = qs.filter(user=request.user)
        module = request.query_params.get('module')
        if module:
            qs = qs.filter(module=module)
        try:
            limit = min(max(int(request.query_params.get('limit', 200)), 1), 500)
        except ValueError:
            limit = 200
        return Response(AuditLogSerializer(qs[:limit], many=True).data)

    def post(self, request):
        serializer = AuditLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        serializer.save(
            user=user,
            actor_name=user.first_name or user.email or user.username,
            actor_email=user.email or '',
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)
