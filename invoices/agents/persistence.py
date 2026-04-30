from __future__ import annotations

from invoices.models import InvoiceExtraction


class PersistenceAgent:
    def save_success(self, uploaded_file, data: dict, provider: str) -> InvoiceExtraction:
        return InvoiceExtraction.objects.create(
            file_name=uploaded_file.name,
            file_size=uploaded_file.size,
            provider=provider,
            status=InvoiceExtraction.Status.SUCCESS,
            result_json=data,
        )

    def save_error(self, uploaded_file, error_message: str, provider: str = "system") -> InvoiceExtraction:
        return InvoiceExtraction.objects.create(
            file_name=getattr(uploaded_file, "name", "arquivo-desconhecido"),
            file_size=getattr(uploaded_file, "size", 0),
            provider=provider,
            status=InvoiceExtraction.Status.ERROR,
            error_message=error_message,
        )
