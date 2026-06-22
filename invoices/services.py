from __future__ import annotations

from collections.abc import Mapping

from .agents import (
    ExpenseClassificationAgent,
    PdfExtractionAgent,
    PersistenceAgent,
    ValidationAgent,
)


class InvoiceExtractionService:
    def __init__(self) -> None:
        self.pdf_agent = PdfExtractionAgent()
        self.classification_agent = ExpenseClassificationAgent()
        self.validation_agent = ValidationAgent()
        self.persistence_agent = PersistenceAgent()

    def extract(self, uploaded_file) -> dict:
        # Perceber: o PdfExtractionAgent interpreta o PDF (texto + Gemini ou mock).
        extraction = self.pdf_agent.extract(uploaded_file)

        # Processar: normalizar estrutura mínima retornada pelo agente de extração.
        data = self.validation_agent.normalize(extraction.data)

        # Decidir: manter classificação do agente Gemini apenas se ela for válida e oficial.
        if not self._should_preserve_gemini_classification(extraction.data):
            data["classificacoes_despesa"] = self.classification_agent.classify(data["produtos"])

        # Agir: garantir contrato final e persistir o resultado estruturado.
        data = self.validation_agent.normalize(data)
        record = self.persistence_agent.save_success(uploaded_file, data, extraction.provider)
        payload = {
            "success": True,
            "id": record.id,
            "provider": extraction.provider,
            "data": data,
        }
        if extraction.fallback_reason:
            payload["fallback_reason"] = extraction.fallback_reason
        return payload

    def _should_preserve_gemini_classification(self, raw_data: object) -> bool:
        if not isinstance(raw_data, Mapping):
            return False

        classification = raw_data.get("classificacoes_despesa")
        if not isinstance(classification, list) or not classification:
            return False

        for item in classification:
            if not isinstance(item, Mapping):
                return False
            categoria = str(item.get("categoria", "")).strip()
            justificativa = str(item.get("justificativa", "")).strip()
            if not categoria:
                return False
            if not justificativa:
                return False
            if not self.classification_agent.is_official_category(categoria):
                return False

        return True
