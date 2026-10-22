# Generated migration to rename account_balance to invoice_balance

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0005_account_created_at_account_updated_at_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='customer',
            old_name='account_balance',
            new_name='invoice_balance',
        ),
        migrations.RenameField(
            model_name='account',
            old_name='account_balance',
            new_name='invoice_balance',
        ),
    ]
