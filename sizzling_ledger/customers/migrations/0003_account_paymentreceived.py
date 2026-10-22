import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0002_vendor")]

    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True)),
                ("account_balance", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("cash_balance", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
            ],
        ),
        migrations.CreateModel(
            name="PaymentReceived",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("amount_type", models.CharField(choices=[("cash", "Cash"), ("account", "Account")], max_length=7)),
                ("date", models.DateField()),
                ("transaction_number", models.CharField(max_length=100, unique=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments_received", to="customers.account")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments_received", to="customers.customer")),
            ],
        ),
    ]
