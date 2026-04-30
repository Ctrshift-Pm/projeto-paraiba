from django.db import models


class InvoiceExtraction(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Sucesso"
        ERROR = "error", "Erro"

    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    provider = models.CharField(max_length=32, default="mock")
    status = models.CharField(max_length=16, choices=Status.choices)
    result_json = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.status})"
