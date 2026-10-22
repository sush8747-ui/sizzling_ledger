from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Vendor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("phone", models.CharField(max_length=30)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address", models.TextField(blank=True)),
                ("location", models.CharField(blank=True, max_length=255)),
                ("gst", models.CharField(blank=True, max_length=50)),
            ],
        ),
    ]
