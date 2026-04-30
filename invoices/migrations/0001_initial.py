from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="InvoiceExtraction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_name", models.CharField(max_length=255)),
                ("file_size", models.PositiveIntegerField(default=0)),
                ("provider", models.CharField(default="mock", max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[("success", "Sucesso"), ("error", "Erro")],
                        max_length=16,
                    ),
                ),
                ("result_json", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
