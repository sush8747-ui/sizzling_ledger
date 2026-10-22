from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0015_alter_customer_phone_alter_employee_phone_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invoice',
            name='name',
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name='invoice',
            constraint=models.UniqueConstraint(fields=('user', 'name'), name='unique_invoice_name_per_user'),
        ),
    ]
