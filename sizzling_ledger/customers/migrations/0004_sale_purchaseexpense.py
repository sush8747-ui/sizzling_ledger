import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0003_account_paymentreceived")]

    operations = [
        migrations.CreateModel(
            name="Sale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("invoice", models.CharField(max_length=100)),
                ("invoice_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("cash_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("gst_percentage", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales", to="customers.customer")),
            ],
        ),
        migrations.CreateModel(
            name="PurchaseExpense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("invoice", models.CharField(max_length=100)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("amount_type", models.CharField(choices=[("cash", "Cash"), ("account", "Account")], max_length=7)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases_expenses", to="customers.account")),
                ("vendor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases_expenses", to="customers.vendor")),
            ],
        ),
    ]
