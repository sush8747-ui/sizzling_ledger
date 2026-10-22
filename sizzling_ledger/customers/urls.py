from django.urls import path

from .views import (
    CustomerListCreateView,
    CustomerDetailView,
    GSTLookupView,
    GoogleLoginView,
    VendorListCreateView,
    VendorDetailView,
    EmployeeListCreateView,
    EmployeeDetailView,
    InvoiceListCreateView,
    InvoiceDetailView,
    SaleListCreateView,
    SaleDetailView,
    PaymentReceivedListCreateView,
    PaymentReceivedDetailView,
    PurchaseExpenseListCreateView,
    PurchaseExpenseDetailView,
    PurchaseExpenseDueTomorrowView,
    SalaryListCreateView,
    SalaryDetailView,
    SalaryDueTomorrowView,
    UserListView,
    UserMeView,
    UserPermissionDetailView,
    AuditLogListCreateView,
)

urlpatterns = [
    # Customer
    path('customers/', CustomerListCreateView.as_view(), name='customer-list-create'),
    path('customers/<int:pk>/', CustomerDetailView.as_view(), name='customer-detail'),

    # GST Lookup
    path('gst-lookup/', GSTLookupView.as_view(), name='gst-lookup'),

    # Vendor
    path('vendors/', VendorListCreateView.as_view(), name='vendor-list-create'),
    path('vendors/<int:pk>/', VendorDetailView.as_view(), name='vendor-detail'),

    # Employee
    path('employees/', EmployeeListCreateView.as_view(), name='employee-list-create'),
    path('employees/<int:pk>/', EmployeeDetailView.as_view(), name='employee-detail'),

    # Invoice
    path('invoices/', InvoiceListCreateView.as_view(), name='invoice-list-create'),
    path('invoices/<int:pk>/', InvoiceDetailView.as_view(), name='invoice-detail'),

    # Sale
    path('sales/', SaleListCreateView.as_view(), name='sale-list-create'),
    path('sales/<int:pk>/', SaleDetailView.as_view(), name='sale-detail'),

    # Payment Received
    path('payments-received/', PaymentReceivedListCreateView.as_view(), name='payment-received-list-create'),
    path('payments-received/<int:pk>/', PaymentReceivedDetailView.as_view(), name='payment-received-detail'),

    # Purchase & Expense
    path('purchases-expenses/', PurchaseExpenseListCreateView.as_view(), name='purchase-expense-list-create'),
    path('purchases-expenses/due-tomorrow/', PurchaseExpenseDueTomorrowView.as_view(), name='purchase-expense-due-tomorrow'),
    path('purchases-expenses/<int:pk>/', PurchaseExpenseDetailView.as_view(), name='purchase-expense-detail'),
    # Salaries
    path('salaries/', SalaryListCreateView.as_view(), name='salary-list-create'),
    path('salaries/due-tomorrow/', SalaryDueTomorrowView.as_view(), name='salary-due-tomorrow'),
    path('salaries/<int:pk>/', SalaryDetailView.as_view(), name='salary-detail'),
    # Google Login
    path('google-login/', GoogleLoginView.as_view(), name='google-login'),

    # Users / permissions (admin-only, except /users/me/)
    path('users/me/', UserMeView.as_view(), name='user-me'),
    path('users/', UserListView.as_view(), name='user-list'),
    path('users/<int:pk>/', UserPermissionDetailView.as_view(), name='user-permission-detail'),
    # Audit Log

    path('audit-log/', AuditLogListCreateView.as_view(), name='audit-log'),
]