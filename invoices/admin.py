from django.contrib import admin

from .models import InvoiceExtraction


@admin.register(InvoiceExtraction)
class InvoiceExtractionAdmin(admin.ModelAdmin):
    list_display = ("file_name", "status", "provider", "created_at")
    list_filter = ("status", "provider", "created_at")
    search_fields = ("file_name",)
    readonly_fields = ("created_at", "updated_at")
