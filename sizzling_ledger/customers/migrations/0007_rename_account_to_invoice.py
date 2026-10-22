# Generated migration to rename Account model to Invoice and update foreign keys

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0006_rename_account_balance_to_invoice_balance'),
    ]

    operations = [
        # Rename Account model to Invoice
        migrations.RenameModel(
            old_name='Account',
            new_name='Invoice',
        ),
        
        # Update PaymentReceived ForeignKey
        migrations.AlterField(
            model_name='paymentreceived',
            name='account',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments_received', to='customers.invoice'),
        ),
        migrations.RenameField(
            model_name='paymentreceived',
            old_name='account',
            new_name='invoice',
        ),
        migrations.AlterField(
            model_name='paymentreceived',
            name='amount_type',
            field=models.CharField(choices=[('cash', 'Cash'), ('invoice', 'Invoice')], max_length=10),
        ),
        
        # Update PurchaseExpense ForeignKey and rename
        migrations.AddField(
            model_name='purchaseexpense',
            name='invoice_ledger',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='purchases_expenses', to='customers.invoice'),
        ),
        migrations.RemoveField(
            model_name='purchaseexpense',
            name='account',
        ),
        migrations.AlterField(
            model_name='purchaseexpense',
            name='amount_type',
            field=models.CharField(choices=[('cash', 'Cash'), ('invoice', 'Invoice')], max_length=10),
        ),
    ]
