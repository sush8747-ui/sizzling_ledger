# Generated migration for adding can_edit_employees field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0021_employee_salary'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='can_edit_employees',
            field=models.BooleanField(default=False),
        ),
    ]
