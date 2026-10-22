from django.db import migrations


def backfill_expense_payee_type(apps, schema_editor):
    """Expenses saved before expense_payee_type was stored have '' in that column.
    Work the type out from what the row holds. Uses the historical model, so the
    custom save() (which moves account balances) is NOT triggered."""
    PurchaseExpense = apps.get_model('customers', 'PurchaseExpense')
    for pe in PurchaseExpense.objects.filter(payee_type='employee', expense_payee_type=''):
        if pe.vendor_id:
            pe.expense_payee_type = 'vendor'
        elif pe.employee_id:
            pe.expense_payee_type = 'employee'
        else:
            pe.expense_payee_type = 'other'
        pe.save(update_fields=['expense_payee_type'])


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0023_salary_amount_type'),
    ]

    operations = [
        migrations.RunPython(backfill_expense_payee_type, migrations.RunPython.noop),
    ]
