# Aligns the DB column with models.py: PurchaseExpense.invoice_ledger -> account
# (models.py has always declared this field as `account`; migration 0007
# had renamed the actual DB column to `invoice_ledger`, which is what was
# silently crashing every Purchase & Expense creation.)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0007_rename_account_to_invoice'),
    ]

    operations = [
        migrations.RenameField(
            model_name='purchaseexpense',
            old_name='invoice_ledger',
            new_name='account',
        ),
    ]
