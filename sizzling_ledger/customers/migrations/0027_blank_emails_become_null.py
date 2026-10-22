from django.db import migrations


def blank_to_null(apps, schema_editor):
    """Email is unique, and '' counts as a value, so two records with no email would
    clash. After 0026 the column accepts NULL; turn every existing '' into NULL."""
    for name in ('Customer', 'Vendor', 'Employee'):
        apps.get_model('customers', name).objects.filter(email='').update(email=None)


def null_to_blank(apps, schema_editor):
    for name in ('Customer', 'Vendor', 'Employee'):
        apps.get_model('customers', name).objects.filter(email__isnull=True).update(email='')


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0026_contact_fields_optional'),
    ]

    operations = [
        migrations.RunPython(blank_to_null, null_to_blank),
    ]
