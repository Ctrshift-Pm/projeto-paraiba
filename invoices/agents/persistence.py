from __future__ import annotations

from invoices.models import InvoiceExtraction, MovimentoContas


class PersistenceAgent:
    def save_success(
        self,
        uploaded_file,
        data: dict,
        provider: str,
        movement_type: str = "",
        movimento: MovimentoContas | None = None,
    ) -> InvoiceExtraction:
        return InvoiceExtraction.objects.create(
            file_name=uploaded_file.name,
            file_size=uploaded_file.size,
            provider=provider,
            status=InvoiceExtraction.Status.SUCCESS,
            result_json=data,
            movement_type=movement_type,
            movimento=movimento,
        )

    def save_error(self, uploaded_file, error_message: str, provider: str = "system", movement_type: str = "") -> InvoiceExtraction:
        return InvoiceExtraction.objects.create(
            file_name=getattr(uploaded_file, "name", "arquivo-desconhecido"),
            file_size=getattr(uploaded_file, "size", 0),
            provider=provider,
            status=InvoiceExtraction.Status.ERROR,
            error_message=error_message,
            movement_type=movement_type,
        )
