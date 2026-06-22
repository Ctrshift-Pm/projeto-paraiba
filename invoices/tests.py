from __future__ import annotations
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from invoices.agents.extraction import ExtractionResult
from invoices.agents import ExpenseClassificationAgent, ValidationAgent
from invoices.agents.extraction import PdfExtractionAgent
from invoices.agent_RAG import Agent3
from invoices.management.commands.seed_demo_invoices import DEMO_PREFIX
from invoices.gemini_session import SESSION_KEY, validate_gemini_api_key
from invoices.services import InvoiceExtractionService
from invoices.models import Classificacao, InvoiceExtraction, MovimentoContas, ParcelaContas, Pessoa
from invoices.utils import is_valid_cnpj, is_valid_cpf, mask_cep, mask_cnpj, mask_cpf, mask_ie, mask_phone, only_alnum, only_digits


MINIMUM_CONTRACT_FIELDS = (
    "fornecedor",
    "faturado",
    "numero_nota_fiscal",
    "data_emissao",
    "produtos",
    "parcelas",
    "valor_total",
    "classificacoes_despesa",
)

FORNECEDOR_REQUIRED_FIELDS = ("razao_social", "fantasia", "cnpj")
FATURADO_REQUIRED_FIELDS = ("nome_completo", "cpf")
PRODUTO_REQUIRED_FIELDS = ("descricao", "quantidade")
PARCELA_REQUIRED_FIELDS = ("numero", "data_vencimento", "valor")
CLASSIFICACAO_REQUIRED_FIELDS = ("categoria", "justificativa")


@override_settings(GEMINI_API_KEY="")
class InvoiceExtractApiTests(TestCase):
    def post_pdf(self, text: str, *, filename: str = "nota_fiscal.pdf") -> dict:
        uploaded_pdf = SimpleUploadedFile(filename, b"%PDF-1.4 mock", content_type="application/pdf")
        with patch("invoices.agents.extraction.PdfExtractionAgent._read_pdf_text", return_value=text):
            response = self.client.post(reverse("invoices:extract_invoice"), {"pdf": uploaded_pdf})
        return response

    def test_extract_without_file_returns_error(self) -> None:
        response = self.client.post(reverse("invoices:extract_invoice"))

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["error"], "Arquivo PDF é obrigatório.")
        self.assertIn("Envie o campo 'pdf'", payload["detail"])

    def test_extract_with_non_pdf_file_returns_error(self) -> None:
        response = self.client.post(
            reverse("invoices:extract_invoice"),
            {"pdf": SimpleUploadedFile("nota.txt", b"nao e pdf", content_type="text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["error"], "Formato do arquivo inválido.")

    def test_extract_valid_pdf_uses_mock_and_returns_minimum_contract(self) -> None:
        response = self.post_pdf("Compra de Oleo Diesel S10")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["provider"], "mock")
        data = payload["data"]
        self.assertEqual(payload["provider"], "mock")

        for field in MINIMUM_CONTRACT_FIELDS:
            self.assertIn(field, data)
        self.assertTrue(data["produtos"])
        self.assertTrue(data["parcelas"])
        self.assertTrue(data["classificacoes_despesa"])

        self.assertIsInstance(data["fornecedor"], dict)
        self.assertIsInstance(data["faturado"], dict)
        self.assertIsInstance(data["produtos"], list)
        self.assertIsInstance(data["parcelas"], list)
        self.assertIsInstance(data["classificacoes_despesa"], list)
        self.assertIsInstance(data["valor_total"], (int, float))
        self.assertEqual(data["classificacoes_despesa"][0]["categoria"], "MANUTENCAO E OPERACAO")

    def test_extract_returns_required_contract_fields(self) -> None:
        response = self.post_pdf("Compra de Oleo Diesel S10")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertIn("id", payload)
        self.assertIn("provider", payload)
        self.assertIn("data", payload)
        self.assertIsInstance(payload["id"], int)

        data = payload["data"]
        fornecedor = data["fornecedor"]
        faturado = data["faturado"]

        self.assertIsInstance(data, dict)
        self.assertIsInstance(fornecedor, dict)
        self.assertIsInstance(faturado, dict)
        self.assertIsInstance(data["produtos"], list)
        self.assertIsInstance(data["parcelas"], list)
        self.assertIsInstance(data["classificacoes_despesa"], list)
        self.assertTrue(data["produtos"])
        self.assertTrue(data["parcelas"])
        self.assertTrue(data["classificacoes_despesa"])

        for field in MINIMUM_CONTRACT_FIELDS:
            self.assertIn(field, data)

        for field in FORNECEDOR_REQUIRED_FIELDS:
            self.assertIn(field, fornecedor)
            self.assertIsInstance(fornecedor[field], str)

        for field in FATURADO_REQUIRED_FIELDS:
            self.assertIn(field, faturado)
            self.assertIsInstance(faturado[field], str)

        for field in PRODUTO_REQUIRED_FIELDS:
            for product in data["produtos"]:
                self.assertIn(field, product)

        for field in PARCELA_REQUIRED_FIELDS:
            for parcela in data["parcelas"]:
                self.assertIn(field, parcela)

        for field in CLASSIFICACAO_REQUIRED_FIELDS:
            for classificacao in data["classificacoes_despesa"]:
                self.assertIn(field, classificacao)

    def test_extract_valid_pdf_classifies_hydraulic_material(self) -> None:
        response = self.post_pdf("Material hidráulico para tubulação")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["provider"], "mock")
        self.assertEqual(payload["data"]["classificacoes_despesa"][0]["categoria"], "INFRAESTRUTURA E UTILIDADES")

    def test_successful_extraction_is_persisted(self) -> None:
        before_count = InvoiceExtraction.objects.count()
        response = self.post_pdf("Compra de óleo diesel para o trator")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(InvoiceExtraction.objects.count(), before_count + 1)

        record = InvoiceExtraction.objects.order_by("-id").first()
        self.assertIsNotNone(record)
        self.assertEqual(record.file_name, "nota_fiscal.pdf")
        self.assertEqual(record.status, InvoiceExtraction.Status.SUCCESS)
        self.assertEqual(record.provider, "mock")
        self.assertEqual(record.result_json, payload["data"])

    @override_settings(GEMINI_API_KEY="test-key")
    @patch("invoices.agents.extraction.PdfExtractionAgent._extract_with_gemini", side_effect=RuntimeError("indisponivel"))
    def test_extract_with_gemini_key_set_still_uses_mock_fallback(self, _mock_gemini_extract) -> None:
        response = self.post_pdf("Compra de filtro hidráulico")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["provider"], "mock")
        self.assertIn("fallback_reason", payload)
        self.assertIn("Falha ao usar Gemini", payload["fallback_reason"])


@override_settings(GEMINI_API_KEY="")
class GeminiGateTests(TestCase):
    def post_pdf(self, text: str, *, filename: str = "nota_fiscal.pdf") -> dict:
        uploaded_pdf = SimpleUploadedFile(filename, b"%PDF-1.4 mock", content_type="application/pdf")
        with patch("invoices.agents.extraction.PdfExtractionAgent._read_pdf_text", return_value=text):
            response = self.client.post(reverse("invoices:extract_invoice"), {"pdf": uploaded_pdf})
        return response

    def test_index_without_session_key_shows_gate(self) -> None:
        response = self.client.get(reverse("invoices:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe a chave da API")
        self.assertContains(response, "A aplicação usa Gemini")

    @patch("invoices.views.validate_gemini_api_key", return_value=(True, ""))
    def test_gate_stores_key_and_redirects_to_next(self, _mock_validate) -> None:
        response = self.client.post(
            reverse("invoices:gemini_gate"),
            {"api_key": "test-key", "next": "/cadastros/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/cadastros/")
        self.assertEqual(self.client.session.get("gemini_api_key"), "test-key")

    @patch("invoices.views.validate_gemini_api_key", return_value=(False, "Chave invalida."))
    def test_gate_rejects_invalid_key(self, _mock_validate) -> None:
        session = self.client.session
        session["gemini_api_key"] = "old-key"
        session.save()

        response = self.client.post(
            reverse("invoices:gemini_gate"),
            {"api_key": "bad-key", "next": "/"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chave invalida.")
        self.assertIsNone(self.client.session.get("gemini_api_key"))

    @patch("google.genai.Client")
    def test_validate_gemini_api_key_uses_minimal_smoke_call(self, mock_client_cls) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.return_value = object()

        is_valid, error = validate_gemini_api_key("test-key", "gemini-test-model")

        self.assertTrue(is_valid)
        self.assertEqual(error, "")
        mock_client.models.generate_content.assert_called_once()
        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-test-model")
        self.assertEqual(kwargs["contents"], "Responda somente OK.")
        self.assertEqual(kwargs["config"]["max_output_tokens"], 5)
        self.assertEqual(kwargs["config"]["temperature"], 0)

    @patch("google.genai.Client")
    def test_validate_gemini_api_key_rejects_auth_error(self, mock_client_cls) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.side_effect = RuntimeError("400 INVALID_ARGUMENT. API key not valid.")

        is_valid, error = validate_gemini_api_key("bad-key", "gemini-test-model")

        self.assertFalse(is_valid)
        self.assertEqual(error, "Chave do Gemini invalida. Passe uma chave valida.")

    def test_api_returns_success_id_provider_and_data(self) -> None:
        response = self.post_pdf("Compra de óleo diesel")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("success", payload)
        self.assertIn("id", payload)
        self.assertIn("provider", payload)
        self.assertIn("data", payload)
        self.assertTrue(payload["success"])
        self.assertIsInstance(payload["id"], int)

    def test_extract_then_analyze_then_launch_flow(self) -> None:
        response = self.post_pdf("Compra de Oleo Diesel S10")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("analysis", payload)
        self.assertNotIn("launch", payload)
        analysis_response = self.client.post(reverse("invoices:analyze_invoice", args=[payload["id"]]))
        analysis_payload = analysis_response.json()

        self.assertEqual(analysis_response.status_code, 200)
        self.assertFalse(analysis_payload["analysis"]["fornecedor"]["exists"])
        self.assertFalse(analysis_payload["analysis"]["faturado"]["exists"])
        self.assertFalse(analysis_payload["analysis"]["classificacoes"][0]["exists"])

        launch_response = self.client.post(reverse("invoices:launch_invoice", args=[payload["id"]]))
        launch_payload = launch_response.json()

        self.assertEqual(launch_response.status_code, 200)
        self.assertIn("launch", launch_payload)
        self.assertEqual(launch_payload["launch"]["message"], "Lancamentos concluidos com sucesso.")
        self.assertTrue(len(launch_payload["launch"]["movements"]) >= 1)
        self.assertEqual(launch_payload["launch"]["movements"][0]["movement_type"], "APAGAR")
        self.assertIn("fornecedor", launch_payload["launch"])
        self.assertIn("faturado", launch_payload["launch"])
        self.assertIn("classificacoes", launch_payload["launch"])
        self.assertIn("parcelas", launch_payload["launch"])
        self.assertIsInstance(launch_payload["launch"]["fornecedor"]["id"], int)
        self.assertIsInstance(launch_payload["launch"]["faturado"]["id"], int)
        self.assertIn("nome", launch_payload["launch"]["fornecedor"])
        self.assertIn("documento", launch_payload["launch"]["fornecedor"])
        self.assertIn("nome", launch_payload["launch"]["faturado"])
        self.assertIn("documento", launch_payload["launch"]["faturado"])
        self.assertIsInstance(launch_payload["launch"]["classificacoes"][0]["descricao"], str)
        self.assertIsInstance(launch_payload["launch"]["parcelas"][0]["id"], int)
        self.assertIn("identificacao", launch_payload["launch"]["parcelas"][0])
        self.assertIn("numero", launch_payload["launch"]["parcelas"][0])
        self.assertIn("vencimento", launch_payload["launch"]["parcelas"][0])
        self.assertIn("valor", launch_payload["launch"]["parcelas"][0])

    def test_launch_can_be_executed_without_explicit_prior_analyze(self) -> None:
        response = self.post_pdf("Compra de Oleo Diesel S10")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        launch_response = self.client.post(reverse("invoices:launch_invoice", args=[payload["id"]]))
        launch_payload = launch_response.json()

        self.assertEqual(launch_response.status_code, 200)
        self.assertTrue(launch_payload["success"])
        self.assertIn(launch_payload["movement_type"], {MovimentoContas.Tipo.APAGAR, MovimentoContas.Tipo.ARECEBER, "MISTO"})
        self.assertEqual(launch_payload["launch"]["movements"][0]["movement_type"], "APAGAR")
        self.assertIn("fornecedor", launch_payload["launch"])
        self.assertIn("faturado", launch_payload["launch"])

    @patch("invoices.services.PdfExtractionAgent.extract", side_effect=ValueError("Nao foi possivel extrair texto do PDF enviado."))
    def test_extract_pdf_without_readable_text_returns_400(self, _mock_read) -> None:
        response = self.client.post(
            reverse("invoices:extract_invoice"),
            {"pdf": SimpleUploadedFile("nota_vazia.pdf", b"%PDF-1.4 mock", content_type="application/pdf")},
        )
        payload = response.json()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"], "Falha ao extrair dados do PDF.")
        self.assertEqual(payload["detail"], "Nao foi possivel extrair texto do PDF enviado.")

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA MOCK",
                    "fantasia": "FORN MOCK",
                    "cnpj": "11.111.111/0001-11",
                },
                "faturado": {"nome_completo": "CLIENTE MOCK", "cpf": "222.222.222-22"},
                "numero_nota_fiscal": "999",
                "data_emissao": "2024-01-01",
                "produtos": [{"descricao": "Item administrativo sem regra", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "forca-bruta"}],
            },
            provider="gemini",
        ),
    )
    def test_classification_from_gemini_official_category_is_preserved(self, _mock_extract) -> None:
        response = self.post_pdf("Texto genérico não confiável")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        classifications = payload["data"]["classificacoes_despesa"]
        self.assertEqual(classifications[0]["categoria"], "MANUTENCAO E OPERACAO")
        self.assertEqual(classifications[0]["justificativa"], "forca-bruta")

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA MOCK",
                    "fantasia": "FORN MOCK",
                    "cnpj": "11.111.111/0001-11",
                },
                "faturado": {"nome_completo": "CLIENTE MOCK", "cpf": "222.222.222-22"},
                "numero_nota_fiscal": "999",
                "data_emissao": "2024-01-01",
                "produtos": [{"descricao": "Item de escritórios sem categoria conhecida", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "CATEGORIA INESPERADA", "justificativa": "forca-bruta"}],
            },
            provider="gemini",
        ),
    )
    def test_classification_falls_back_to_local_rules_when_gemini_category_is_unknown(self, _mock_extract) -> None:
        response = self.post_pdf("Texto genérico não confiável")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        classifications = payload["data"]["classificacoes_despesa"]
        self.assertEqual(classifications[0]["categoria"], "ADMINISTRATIVAS")
        self.assertEqual(classifications[0]["justificativa"], "Nao foi possivel identificar um padrao de categoria conhecido para os produtos informados.")
        self.assertNotEqual(classifications[0]["categoria"], "CATEGORIA INESPERADA")


class InvoiceExtractionServiceTests(TestCase):
    @staticmethod
    def _payload_with_installments_and_classifications(*, movement_type: str = MovimentoContas.Tipo.APAGAR) -> dict:
        return {
            "fornecedor": {
                "razao_social": "FORNECEDORA AGRI",
                "fantasia": "AGRI FORNECEDORA",
                "cnpj": "99.999.999/0001-99",
            },
            "faturado": {"nome_completo": "CLIENTE EXEMPLO", "cpf": "123.456.789-00"},
            "numero_nota_fiscal": "NF-9001",
            "data_emissao": "2024-01-15",
            "produtos": [
                {"descricao": "Oleo Diesel S10", "quantidade": 1},
                {"descricao": "Fertilizante ureia", "quantidade": 2},
            ],
            "parcelas": [
                {"numero": 1, "data_vencimento": "", "valor": 400.0},
                {"numero": 2, "data_vencimento": "2024-03-15", "valor": 600.0},
            ],
            "valor_total": 1000.0,
            "classificacoes_despesa": [
                {"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Importada do Gemini."},
            ]
            if movement_type == MovimentoContas.Tipo.APAGAR
            else [
                {"categoria": "PROVENTOS", "justificativa": "Receita operacional mensal."},
                {"categoria": "VENDAS", "justificativa": "Faturamento recorrente."},
            ],
        }

    def test_extract_creates_financial_movement_with_multiple_installments_and_classifications(self) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        with patch.object(service.pdf_agent, "extract", return_value=ExtractionResult(data=self._payload_with_installments_and_classifications(), provider="gemini")):
            payload = service.extract(file)

        service.analyze(payload["id"])
        launch = service.launch(payload["id"])
        movement = MovimentoContas.objects.get(id=launch["launch"]["movements"][0]["movement_id"])
        self.assertEqual(launch["launch"]["movements"][0]["movement_type"], MovimentoContas.Tipo.APAGAR)
        self.assertEqual(movement.tipo, MovimentoContas.Tipo.APAGAR)
        self.assertEqual(movement.classificacoes.count(), 1)
        self.assertEqual(movement.parcelas.count(), 2)
        self.assertEqual(movement.parcelas.first().numero, 1)
        self.assertIn("fornecedor", launch["launch"])
        self.assertIn("faturado", launch["launch"])
        self.assertIn("classificacoes", launch["launch"])
        self.assertIn("parcelas", launch["launch"])
        self.assertEqual(launch["launch"]["fornecedor"]["nome"], movement.pessoa.razao_social)
        self.assertEqual(launch["launch"]["faturado"]["nome"], movement.faturado.razao_social)
        self.assertEqual(launch["launch"]["classificacoes"][0]["descricao"], "MANUTENCAO E OPERACAO")
        self.assertGreaterEqual(len(launch["launch"]["parcelas"]), 2)
        self.assertIn("identificacao", launch["launch"]["parcelas"][0])
        self.assertIn("numero", launch["launch"]["parcelas"][0])
        self.assertIn("vencimento", launch["launch"]["parcelas"][0])
        self.assertIn("valor", launch["launch"]["parcelas"][0])
        self.assertIsInstance(launch["launch"]["movements"][0]["classificacoes"][0]["id"], int)
        self.assertIsInstance(launch["launch"]["movements"][0]["parcelas"][0]["id"], int)
        self.assertEqual(
            launch["launch"]["movements"][0]["parcelas_ids"],
            [item.id for item in movement.parcelas.order_by("numero")],
        )
        self.assertFalse(launch["launch"]["movements"][0]["movement_id"] is None)

    @patch("invoices.services.uuid.uuid4")
    def test_extract_generates_stable_unique_number_for_missing_document(self, mock_uuid4) -> None:
        mock_uuid4.side_effect = [
            SimpleNamespace(hex="a" * 12),
            SimpleNamespace(hex="b" * 12),
            SimpleNamespace(hex="c" * 12),
            SimpleNamespace(hex="d" * 12),
        ]
        service = InvoiceExtractionService()
        payload = self._payload_with_installments_and_classifications()
        payload["numero_nota_fiscal"] = ""

        with patch.object(
            service.pdf_agent,
            "extract",
            return_value=ExtractionResult(data=payload, provider="gemini"),
        ):
            first_payload = service.extract(SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf"))
            second_payload = service.extract(SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf"))

        first_launch = service.launch(first_payload["id"])
        second_launch = service.launch(second_payload["id"])

        first_movement = MovimentoContas.objects.get(id=first_launch["launch"]["movements"][0]["movement_id"])
        second_movement = MovimentoContas.objects.get(id=second_launch["launch"]["movements"][0]["movement_id"])
        self.assertNotEqual(first_movement.numero_documento, second_movement.numero_documento)
        self.assertTrue(first_movement.numero_documento.startswith("SEM-NUMERO-APAGAR-"))
        self.assertTrue(second_movement.numero_documento.startswith("SEM-NUMERO-APAGAR-"))

    def test_launch_rewrites_duplicate_document_number_for_same_movement_type(self) -> None:
        service = InvoiceExtractionService()
        payload = self._payload_with_installments_and_classifications()
        payload["numero_nota_fiscal"] = "000.011.111"

        with patch.object(
            service.pdf_agent,
            "extract",
            return_value=ExtractionResult(data=payload, provider="gemini"),
        ):
            first_payload = service.extract(SimpleUploadedFile("nota_fiscal_1.pdf", b"%PDF-1.4 mock", content_type="application/pdf"))
            second_payload = service.extract(SimpleUploadedFile("nota_fiscal_2.pdf", b"%PDF-1.4 mock", content_type="application/pdf"))

        first_launch = service.launch(first_payload["id"])
        second_launch = service.launch(second_payload["id"])

        first_movement = MovimentoContas.objects.get(id=first_launch["launch"]["movements"][0]["movement_id"])
        second_movement = MovimentoContas.objects.get(id=second_launch["launch"]["movements"][0]["movement_id"])

        self.assertEqual(first_movement.numero_documento, "000.011.111")
        self.assertEqual(second_movement.numero_documento, "000.011.111-2")
        self.assertEqual(first_movement.tipo, second_movement.tipo)

    def test_extract_reatives_multiple_revenue_classifications_and_parcels(self) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        payload_input = self._payload_with_installments_and_classifications(movement_type=MovimentoContas.Tipo.ARECEBER)

        with patch.object(service.pdf_agent, "extract", return_value=ExtractionResult(data=payload_input, provider="gemini")):
            payload = service.extract(file)

        launch = service.launch(payload["id"])
        movement_types = [item["movement_type"] for item in launch["launch"]["movements"]]
        movement_ids = [item["movement_id"] for item in launch["launch"]["movements"]]

        self.assertIn(MovimentoContas.Tipo.ARECEBER, movement_types)
        self.assertGreaterEqual(len(launch["launch"]["movements"]), 1)
        for movement in MovimentoContas.objects.filter(id__in=movement_ids):
            self.assertIn(movement.tipo, movement_types)

    def test_extract_and_launch_supports_misto_movements(self) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        payload_input = self._payload_with_installments_and_classifications()
        payload_input["natureza_operacao"] = "Compra e faturamento de serviços"
        payload_input["classificacoes_despesa"] = [
            {"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Despesa para manutenção."},
            {"categoria": "PROVENTOS", "justificativa": "Faturamento de serviços mensais."},
        ]

        with patch.object(service.pdf_agent, "extract", return_value=ExtractionResult(data=payload_input, provider="gemini")):
            payload = service.extract(file)

        analysis = service.analyze(payload["id"])
        launch = service.launch(payload["id"])

        self.assertEqual(analysis["movement_type"], "MISTO")
        self.assertEqual(len(analysis["analysis"]["blocks"]), 2)
        self.assertEqual(len(launch["launch"]["movements"]), 2)
        self.assertEqual(launch["movement_type"], "MISTO")
        movement_types = sorted(item["movement_type"] for item in launch["launch"]["movements"])
        self.assertEqual(movement_types, [MovimentoContas.Tipo.APAGAR, MovimentoContas.Tipo.ARECEBER])

    def test_extract_fills_missing_due_dates_with_issue_date(self) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        payload_input = self._payload_with_installments_and_classifications()
        payload_input["parcelas"][0]["data_vencimento"] = ""
        payload_input["parcelas"][1]["data_vencimento"] = ""

        with patch.object(service.pdf_agent, "extract", return_value=ExtractionResult(data=payload_input, provider="gemini")):
            payload = service.extract(file)

        launch = service.launch(payload["id"])

        due_dates = [item["data_vencimento"] for item in payload["data"]["parcelas"]]
        self.assertEqual(due_dates, ["2024-01-15", "2024-01-15"])

        movement = MovimentoContas.objects.get(id=launch["launch"]["movements"][0]["movement_id"])
        self.assertEqual(
            [str(item["data_vencimento"]) for item in movement.parcelas.values("data_vencimento").order_by("numero")],
            ["2024-01-15", "2024-01-15"],
        )

    def test_extract_reactivates_inactive_people_and_classifications(self) -> None:
        inactive_supplier = Pessoa.objects.create(
            razao_social="FORNECEDORA INATIVA",
            nome_fantasia="FORN INATIVA",
            cnpj="88.888.888/0001-88",
            ativo=False,
            is_fornecedor=True,
        )
        inactive_billed = Pessoa.objects.create(
            razao_social="CLIENTE INATIVO",
            nome_fantasia="CLI INATIVO",
            cpf="999.999.999-99",
            ativo=False,
            is_faturado=True,
        )
        inactive_classification = Classificacao.objects.create(
            tipo=Classificacao.Tipo.DESPESA,
            descricao="MANUTENCAO E OPERACAO",
            ativo=False,
        )

        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        payload_input = self._payload_with_installments_and_classifications()
        payload_input["fornecedor"]["cnpj"] = inactive_supplier.cnpj
        payload_input["fornecedor"]["razao_social"] = inactive_supplier.razao_social
        payload_input["faturado"]["cpf"] = inactive_billed.cpf
        payload_input["faturado"]["nome_completo"] = inactive_billed.razao_social
        payload_input["classificacoes_despesa"] = [{"categoria": inactive_classification.descricao, "justificativa": "Reativado no teste."}]

        with patch.object(service.pdf_agent, "extract", return_value=ExtractionResult(data=payload_input, provider="gemini")):
            payload = service.extract(file)
        analysis = service.analyze(payload["id"])
        launch = service.launch(payload["id"])
        self.assertEqual(launch["launch"]["movements"][0]["movement_type"], MovimentoContas.Tipo.APAGAR)

        inactive_supplier.refresh_from_db()
        inactive_billed.refresh_from_db()
        inactive_classification.refresh_from_db()
        self.assertTrue(analysis["analysis"]["fornecedor"]["reactivated"])
        self.assertTrue(analysis["analysis"]["faturado"]["reactivated"])
        self.assertTrue(analysis["analysis"]["classificacoes"][0]["reactivated"])
        self.assertTrue(inactive_supplier.ativo)
        self.assertTrue(inactive_billed.ativo)
        self.assertTrue(inactive_classification.ativo)

    def test_extract_rolls_back_when_installment_creation_fails(self) -> None:
        inactive_supplier = Pessoa.objects.create(
            razao_social="FORNECEDORA ROLLBACK",
            nome_fantasia="ROLLBACK FORN",
            cnpj="77.777.777/0001-77",
            ativo=False,
            is_fornecedor=True,
        )
        inactive_classification = Classificacao.objects.create(
            tipo=Classificacao.Tipo.DESPESA,
            descricao="MANUTENCAO E OPERACAO",
            ativo=False,
        )
        baseline = {
            "invoice_count": InvoiceExtraction.objects.count(),
            "movement_count": MovimentoContas.objects.count(),
            "parcel_count": ParcelaContas.objects.count(),
        }

        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        payload_input = self._payload_with_installments_and_classifications()
        payload_input["fornecedor"]["cnpj"] = inactive_supplier.cnpj

        with patch.object(service, "_create_installments", side_effect=RuntimeError("Falha no lote de parcelas")), \
            patch.object(service.pdf_agent, "extract", return_value=ExtractionResult(data=payload_input, provider="gemini")):
            payload = service.extract(file)
            service.analyze(payload["id"])
            with self.assertRaises(RuntimeError):
                service.launch(payload["id"])

        self.assertEqual(InvoiceExtraction.objects.count(), baseline["invoice_count"] + 1)
        self.assertEqual(MovimentoContas.objects.count(), baseline["movement_count"])
        self.assertEqual(ParcelaContas.objects.count(), baseline["parcel_count"])
        inactive_supplier.refresh_from_db()
        inactive_classification.refresh_from_db()
        self.assertFalse(inactive_supplier.ativo)
        self.assertFalse(inactive_classification.ativo)
    @override_settings(GEMINI_API_KEY="")
    def test_extract_uses_mock_without_gemini_key(self) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        with patch("invoices.services.PdfExtractionAgent._read_pdf_text", return_value="Nota Fiscal: Oleo Diesel S10"):
            payload = service.extract(file)

        self.assertEqual(payload["provider"], "mock")
        self.assertEqual(payload["fallback_reason"], "GEMINI_API_KEY nao foi configurada.")
        self.assertEqual(payload["data"]["classificacoes_despesa"][0]["categoria"], "MANUTENCAO E OPERACAO")
        self.assertEqual(payload["success"], True)

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA GEMINI",
                    "fantasia": "FORNECEDORA",
                    "cnpj": "22.222.222/0002-22",
                },
                "faturado": {"nome_completo": "CLIENTE GEMINI", "cpf": "333.333.333-33"},
                "numero_nota_fiscal": "777",
                "data_emissao": "2024-01-01",
                "produtos": [{"descricao": "Material sem regra", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Classificação oficial do Gemini."}],
            },
            provider="gemini",
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_preserve_official_gemini_category(self, _mock_extract) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        with patch("invoices.services.ExpenseClassificationAgent.classify", side_effect=AssertionError("Fallback não deve ser chamado")):
            payload = service.extract(file)

        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["data"]["classificacoes_despesa"][0]["categoria"], "MANUTENCAO E OPERACAO")
        self.assertEqual(payload["data"]["classificacoes_despesa"][0]["justificativa"], "Classificação oficial do Gemini.")

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA GEMINI",
                    "fantasia": "FORNECEDORA",
                    "cnpj": "22.222.222/0002-22",
                },
                "faturado": {"nome_completo": "CLIENTE GEMINI", "cpf": "333.333.333-33"},
                "numero_nota_fiscal": "778",
                "data_emissao": "2024-01-01",
                "produtos": [{"descricao": "Material sem regra", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Classificação oficial do Gemini."}],
            },
            provider="gemini",
            usage={"input_tokens": 1200, "output_tokens": 350, "total_tokens": 1550},
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_extract_response_includes_gemini_usage_metadata(self, _mock_extract) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        payload = service.extract(file)

        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["metadata"]["usage"]["input_tokens"], 1200)
        self.assertEqual(payload["metadata"]["usage"]["output_tokens"], 350)
        self.assertEqual(payload["metadata"]["usage"]["total_tokens"], 1550)

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA GEMINI",
                    "fantasia": "FORNECEDORA",
                    "cnpj": "22.222.222/0002-22",
                },
                "faturado": {"nome_completo": "CLIENTE GEMINI", "cpf": "333.333.333-33"},
                "numero_nota_fiscal": "779",
                "data_emissao": "2024-01-01",
                "produtos": [{"descricao": "Material sem regra", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Classificação oficial do Gemini."}],
            },
            provider="gemini",
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_extract_response_estimates_usage_when_gemini_metadata_is_missing(self, _mock_extract) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        payload = service.extract(file)

        self.assertEqual(payload["provider"], "gemini")
        self.assertTrue(payload["metadata"]["usage"]["estimated"])
        self.assertGreater(payload["metadata"]["usage"]["total_tokens"], 0)

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA GEMINI",
                    "fantasia": "FORNECEDORA",
                    "cnpj": "22.222.222/0002-22",
                },
                "faturado": {"nome_completo": "CLIENTE GEMINI", "cpf": "333.333.333-33"},
                "numero_nota_fiscal": "777",
                "data_emissao": "2024-01-01",
                "produtos": [{"descricao": "Material sem regra", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "CATEGORIA INESPERADA", "justificativa": "Sem padrão conhecido."}],
            },
            provider="gemini",
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_unknown_gemini_category_falls_back_to_local_rules(self, _mock_extract) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        payload = service.extract(file)

        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["data"]["classificacoes_despesa"][0]["categoria"], "ADMINISTRATIVAS")
        self.assertEqual(
            payload["data"]["classificacoes_despesa"][0]["justificativa"],
            "Nao foi possivel identificar um padrao de categoria conhecido para os produtos informados.",
        )

    def test_service_orchestrates_agents_in_pptx_cycle(self) -> None:
        service = InvoiceExtractionService()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        steps: list[str] = []

        original_normalize = service.validation_agent.normalize
        original_classify = service.classification_agent.classify
        original_save_success = service.persistence_agent.save_success

        def fake_extract(uploaded_file) -> ExtractionResult:
            steps.append("PdfExtractionAgent.extract")
            return ExtractionResult(
                data={
                    "fornecedor": {"razao_social": "FORN", "fantasia": "FORN", "cnpj": "11.111.111/0001-11"},
                    "faturado": {"nome_completo": "CLIENTE", "cpf": "222.222.222-22"},
                    "numero_nota_fiscal": "111",
                    "data_emissao": "2024-01-01",
                    "produtos": [{"descricao": "Material sem regra conhecida", "quantidade": 1}],
                    "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                    "valor_total": 100.0,
                    "classificacoes_despesa": [{"categoria": "CATEGORIA INVISIVEL", "justificativa": "forca-bruta"}],
                },
                provider="gemini",
            )

        def fake_normalize(data):
            steps.append("ValidationAgent.normalize")
            return original_normalize(data)

        def fake_classify(products):
            steps.append("ExpenseClassificationAgent.classify")
            return original_classify(products)

        def fake_save_success(uploaded_file, data, provider, movement_type="", movimento=None):
            steps.append("PersistenceAgent.save_success")
            return original_save_success(uploaded_file, data, provider)

        with patch.object(service.pdf_agent, "extract", side_effect=fake_extract), \
            patch.object(service.validation_agent, "normalize", side_effect=fake_normalize), \
            patch.object(service.classification_agent, "classify", side_effect=fake_classify), \
            patch.object(service.persistence_agent, "save_success", side_effect=fake_save_success):
            payload = service.extract(file)

        self.assertTrue(payload["success"])
        self.assertIsInstance(payload["id"], int)
        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(
            steps,
            [
                "PdfExtractionAgent.extract",
                "ValidationAgent.normalize",
                "ExpenseClassificationAgent.classify",
                "PersistenceAgent.save_success",
            ],
        )


class PdfExtractionAgentTests(TestCase):
    @override_settings(GEMINI_API_KEY="")
    def test_extract_without_gemini_key_uses_mock_with_missing_key_reason(self) -> None:
        agent = PdfExtractionAgent()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        with patch.object(agent, "_read_pdf_text", return_value="Nota Fiscal: Oleo Diesel S10"):
            result = agent.extract(file)

        self.assertEqual(result.provider, "mock")
        self.assertEqual(result.fallback_reason, "GEMINI_API_KEY nao foi configurada.")
        self.assertIn("fornecedor", result.data)

    @override_settings(GEMINI_API_KEY="test-key")
    def test_extract_with_gemini_key_and_valid_json_uses_gemini_without_fallback_reason(self) -> None:
        agent = PdfExtractionAgent()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        gemini_data = {
            "fornecedor": {"razao_social": "FORNECEDORA GEMINI", "fantasia": "FORNECEDORA", "cnpj": "22.222.222/0002-22"},
            "faturado": {"nome_completo": "CLIENTE GEMINI", "cpf": "333.333.333-33"},
            "numero_nota_fiscal": "777",
            "data_emissao": "2024-01-01",
            "produtos": [{"descricao": "Material sem regra", "quantidade": 1}],
            "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
            "valor_total": 100.0,
            "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Classificacao oficial."}],
        }

        with patch.object(agent, "_read_pdf_text", return_value="Nota Fiscal Gemini"), \
            patch.object(agent, "_extract_with_gemini", return_value=gemini_data) as mock_gemini:
            result = agent.extract(file)

        mock_gemini.assert_called_once_with("Nota Fiscal Gemini")
        self.assertEqual(result.provider, "gemini")
        self.assertIsNone(result.fallback_reason)
        self.assertEqual(result.data, gemini_data)

    @override_settings(GEMINI_API_KEY="test-key")
    def test_extract_with_gemini_usage_metadata_returns_token_debug(self) -> None:
        agent = PdfExtractionAgent()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")
        gemini_data = {
            "fornecedor": {"razao_social": "FORNECEDORA GEMINI", "fantasia": "FORNECEDORA", "cnpj": "22.222.222/0002-22"},
            "faturado": {"nome_completo": "CLIENTE GEMINI", "cpf": "333.333.333-33"},
            "numero_nota_fiscal": "777",
            "data_emissao": "2024-01-01",
            "produtos": [{"descricao": "Material sem regra", "quantidade": 1}],
            "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
            "valor_total": 100.0,
            "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Classificacao oficial."}],
        }
        usage = {"input_tokens": 1200, "output_tokens": 350, "total_tokens": 1550}

        with patch.object(agent, "_read_pdf_text", return_value="Nota Fiscal Gemini"), \
            patch.object(agent, "_extract_with_gemini", return_value=(gemini_data, usage)):
            result = agent.extract(file)

        self.assertEqual(result.provider, "gemini")
        self.assertEqual(result.usage, usage)

    @override_settings(GEMINI_API_KEY="test-key")
    def test_extract_with_gemini_failure_uses_mock_with_safe_fallback_reason(self) -> None:
        agent = PdfExtractionAgent()
        file = SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")

        with patch.object(agent, "_read_pdf_text", return_value="Nota Fiscal: filtro hidraulico"), \
            patch.object(agent, "_extract_with_gemini", side_effect=RuntimeError("modelo indisponivel para test-key")):
            result = agent.extract(file)

        self.assertEqual(result.provider, "mock")
        self.assertIsNotNone(result.fallback_reason)
        self.assertIn("Falha ao usar Gemini", result.fallback_reason)
        self.assertIn("modelo indisponivel", result.fallback_reason)
        self.assertNotIn("test-key", result.fallback_reason)
        self.assertIn("fornecedor", result.data)

    def test_usage_payload_normalizes_gemini_usage_metadata(self) -> None:
        usage = SimpleNamespace(prompt_token_count=1200, candidates_token_count=350, total_token_count=1550)

        payload = PdfExtractionAgent()._usage_payload(usage)

        self.assertEqual(payload["input_tokens"], 1200)
        self.assertEqual(payload["output_tokens"], 350)
        self.assertEqual(payload["total_tokens"], 1550)

    def test_usage_payload_accepts_pydantic_camel_case_metadata(self) -> None:
        usage = SimpleNamespace(
            model_dump=lambda: {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 350,
            }
        )

        payload = PdfExtractionAgent()._usage_payload(usage)

        self.assertEqual(payload["input_tokens"], 1200)
        self.assertEqual(payload["output_tokens"], 350)
        self.assertEqual(payload["total_tokens"], 1550)

    def test_response_usage_metadata_reads_model_dump(self) -> None:
        response = SimpleNamespace(
            model_dump=lambda: {
                "usageMetadata": {
                    "promptTokenCount": 900,
                    "candidatesTokenCount": 100,
                    "totalTokenCount": 1000,
                }
            }
        )

        usage = PdfExtractionAgent()._usage_payload(PdfExtractionAgent()._response_usage_metadata(response))

        self.assertEqual(usage["input_tokens"], 900)
        self.assertEqual(usage["output_tokens"], 100)
        self.assertEqual(usage["total_tokens"], 1000)
        self.assertFalse(usage["estimated"])

    def test_extract_with_gemini_estimates_usage_when_sdk_omits_metadata(self) -> None:
        agent = PdfExtractionAgent()
        gemini_data = {"fornecedor": {"razao_social": "CTVA"}, "produtos": []}

        with patch.object(agent, "_gemini_response_payload", return_value=(gemini_data, {})), \
            patch("google.genai.Client") as mock_client:
            mock_client.return_value.models.generate_content.return_value = SimpleNamespace()
            data, usage = agent._extract_with_gemini("Nota fiscal CTVA")

        self.assertEqual(data, gemini_data)
        self.assertGreater(usage["input_tokens"], 0)
        self.assertGreater(usage["output_tokens"], 0)
        self.assertGreater(usage["total_tokens"], 0)
        self.assertTrue(usage["estimated"])

    @override_settings(GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS=8192)
    def test_gemini_config_requests_json_response(self) -> None:
        config = PdfExtractionAgent()._gemini_config()

        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["temperature"], 0)
        self.assertEqual(config["max_output_tokens"], 8192)
        self.assertIn("response_json_schema", config)
        self.assertIn("fornecedor", config["response_json_schema"]["required"])

    def test_gemini_response_payload_uses_structured_parsed_response(self) -> None:
        parsed = {"fornecedor": {"razao_social": "ELBA CALCARIO LTDA"}}
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=20, total_token_count=120)
        response = SimpleNamespace(parsed=parsed, text="", usage_metadata=usage)

        payload, usage_payload = PdfExtractionAgent()._gemini_response_payload(response)

        self.assertEqual(payload, parsed)
        self.assertEqual(usage_payload["total_tokens"], 120)

    def test_gemini_response_payload_uses_pydantic_like_parsed_response(self) -> None:
        parsed = SimpleNamespace(model_dump=lambda: {"fornecedor": {"razao_social": "ELBA CALCARIO LTDA"}})
        response = SimpleNamespace(parsed=parsed, text="", usage_metadata=None)

        payload, usage_payload = PdfExtractionAgent()._gemini_response_payload(response)

        self.assertEqual(payload["fornecedor"]["razao_social"], "ELBA CALCARIO LTDA")
        self.assertEqual(usage_payload, {})

    def test_gemini_response_payload_reads_candidate_parts_when_text_is_empty(self) -> None:
        part = SimpleNamespace(text='{"fornecedor": {"razao_social": "ELBA CALCARIO LTDA"}}')
        content = SimpleNamespace(parts=[part])
        candidate = SimpleNamespace(content=content, finish_reason="STOP")
        response = SimpleNamespace(parsed=None, text="", candidates=[candidate], usage_metadata=None)

        payload, _usage_payload = PdfExtractionAgent()._gemini_response_payload(response)

        self.assertEqual(payload["fornecedor"]["razao_social"], "ELBA CALCARIO LTDA")

    def test_parse_json_accepts_valid_object_before_extra_text(self) -> None:
        payload = PdfExtractionAgent()._parse_json('{"fornecedor": {"razao_social": "ELBA"}}\ntexto extra')

        self.assertEqual(payload["fornecedor"]["razao_social"], "ELBA")

    def test_parse_json_repairs_trailing_commas(self) -> None:
        payload = PdfExtractionAgent()._parse_json('{"fornecedor": {"razao_social": "ELBA",}, "produtos": [],}')

        self.assertEqual(payload["fornecedor"]["razao_social"], "ELBA")
        self.assertEqual(payload["produtos"], [])

    def test_parse_json_repairs_missing_comma_between_array_objects(self) -> None:
        payload = PdfExtractionAgent()._parse_json(
            '{"parcelas": [{"numero": 1, "valor": 163520.00} {"numero": 2, "valor": 38356.54}]}'
        )

        self.assertEqual(len(payload["parcelas"]), 2)
        self.assertEqual(payload["parcelas"][1]["valor"], 38356.54)

    def test_parse_json_repairs_missing_comma_between_fields(self) -> None:
        payload = PdfExtractionAgent()._parse_json(
            '{"fornecedor": {"razao_social": "CTVA"} "parcelas": [{"numero": 1, "valor": 163520.00}]}'
        )

        self.assertEqual(payload["fornecedor"]["razao_social"], "CTVA")
        self.assertEqual(payload["parcelas"][0]["valor"], 163520.0)

    def test_parse_json_repairs_truncated_array_suffix(self) -> None:
        payload = PdfExtractionAgent()._parse_json(
            '{"fornecedor": {"razao_social": "CTVA"}, "valor_total": 201876.54, '
            '"parcelas": [{"numero": 1, "data_vencimento": "2025-05-05", "valor": 163520.00}'
        )

        self.assertEqual(payload["fornecedor"]["razao_social"], "CTVA")
        self.assertEqual(payload["parcelas"][0]["valor"], 163520.0)

    def test_parse_json_repairs_truncated_string_suffix(self) -> None:
        payload = PdfExtractionAgent()._parse_json('{"fornecedor": {"razao_social": "CTVA')

        self.assertEqual(payload["fornecedor"]["razao_social"], "CTVA")

    @staticmethod
    def _extract_text_from_local_pdf_file(filename: str) -> str:
        pdf_path = Path(__file__).resolve().parents[1] / filename
        assert pdf_path.exists(), f"Arquivo de referência não encontrado: {pdf_path}"

        with pdf_path.open("rb") as file:
            uploaded_pdf = SimpleUploadedFile(pdf_path.name, file.read(), content_type="application/pdf")

        agent = PdfExtractionAgent()
        return agent._read_pdf_text(uploaded_pdf)

    def test_read_real_pdf_beltrano_with_pypdf(self) -> None:
        extracted_text = self._extract_text_from_local_pdf_file("danfe (beltrano - insumos).pdf")

        self.assertIsInstance(extracted_text, str)
        self.assertGreater(len(extracted_text.strip()), 0)

    def test_read_real_pdf_materiais_with_pypdf(self) -> None:
        extracted_text = self._extract_text_from_local_pdf_file("danfe (materiais).pdf")

        self.assertIsInstance(extracted_text, str)
        self.assertGreater(len(extracted_text.strip()), 0)

    def test_read_real_pdf_pecas_with_pypdf(self) -> None:
        extracted_text = self._extract_text_from_local_pdf_file("danfe (peças).pdf")

        self.assertIsInstance(extracted_text, str)
        self.assertGreater(len(extracted_text.strip()), 0)

    def test_mock_data_extracts_due_date_only_when_label_is_explicit(self) -> None:
        agent = PdfExtractionAgent()
        pdf_text = """
        Data de vencimento: 05/05/2025
        """

        result = agent._mock_data(pdf_text)

        self.assertEqual(result["parcelas"][0]["data_vencimento"], "2025-05-05")

    def test_mock_data_keeps_due_date_empty_without_explicit_label(self) -> None:
        agent = PdfExtractionAgent()
        pdf_text = """
        FATURA/DUPLICATAS
        001: 05/05/2025 R$163.520,00;
        CÁLCULO DO IMPOSTO
        """

        result = agent._mock_data(pdf_text)

        self.assertEqual(result["parcelas"][0]["data_vencimento"], "")

    def test_gemini_prompt_targets_danfe_and_official_categories(self) -> None:
        agent = PdfExtractionAgent()
        prompt = agent._prompt("Documento de teste DANFE com produtos de insumos agrícolas")

        self.assertIn("DANFE/NF-e", prompt)
        self.assertIn("fornecedor", prompt)
        self.assertIn("faturado", prompt)
        self.assertIn("numero_nota_fiscal", prompt)
        self.assertIn("chave_acesso", prompt)
        self.assertIn("transportador", prompt)
        self.assertIn("valor_icms", prompt)
        self.assertIn("classificacoes_despesa", prompt)
        self.assertIn("vencimento", prompt.lower())
        self.assertIn("INSUMOS AGRICOLAS", prompt)
        self.assertIn("classifique", prompt.lower())
        self.assertIn("somente json", prompt.lower())
        self.assertIn("nao invente valores", prompt.lower())
        self.assertIn("NUTRICAO E SAUDE ANIMAL", prompt)
        self.assertIn("TECNOLOGIA E MONITORAMENTO", prompt)
        self.assertIn("ARMAZENAGEM E POS-COLHEITA", prompt)


class ExpenseClassificationAgentTests(TestCase):
    def setUp(self) -> None:
        self.classifier = ExpenseClassificationAgent()

    def test_classifies_oil_diesel(self) -> None:
        result = self.classifier.classify([{"descricao": "Oleo Diesel S10"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["categoria"], "MANUTENCAO E OPERACAO")
        self.assertIn("Classificacao baseada", result[0]["justificativa"])

    def test_classifies_hydraulic_material(self) -> None:
        result = self.classifier.classify([{"descricao": "Torneira e material hidráulico"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["categoria"], "INFRAESTRUTURA E UTILIDADES")
        self.assertIn("Classificacao baseada", result[0]["justificativa"])

    def test_classifies_fungicida_as_insumos_agricolas(self) -> None:
        result = self.classifier.classify([{"descricao": "VESSARYA BOMBONA 10L FUNGICIDA"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["categoria"], "INSUMOS AGRICOLAS")
        self.assertIn("fungicida", result[0]["justificativa"].lower())

    def test_classifies_agrotecnicos_products_as_insumos_agricolas(self) -> None:
        for term in [
            "herbicida",
            "inseticida",
            "pesticida",
            "defensivo agricola",
            "defensivo agrícola",
            "fertilizante",
            "adubo",
            "sementes",
        ]:
            with self.subTest(term=term):
                result = self.classifier.classify([{"descricao": f"{term} concentrado"}])
                self.assertEqual(result[0]["categoria"], "INSUMOS AGRICOLAS")

    def test_classifies_accented_terms_and_synonyms(self) -> None:
        result = self.classifier.classify([{"descricao": "Fertilizante complexo e corretivos para cultura de milho"}])
        self.assertEqual(result[0]["categoria"], "INSUMOS AGRICOLAS")
        self.assertIn("Classificacao baseada", result[0]["justificativa"])

    def test_classifies_animal_nutrition_items(self) -> None:
        result = self.classifier.classify([{"descricao": "Ração com suplemento mineral para gado"}])
        self.assertEqual(result[0]["categoria"], "NUTRICAO E SAUDE ANIMAL")
        self.assertIn("racao", result[0]["justificativa"].lower())

    def test_classifies_technology_monitoring_items(self) -> None:
        result = self.classifier.classify([{"descricao": "Licença de software agrícola com telemetria e GPS"}])
        self.assertEqual(result[0]["categoria"], "TECNOLOGIA E MONITORAMENTO")
        self.assertIn("software", result[0]["justificativa"].lower())

    def test_classifies_post_harvest_items(self) -> None:
        result = self.classifier.classify([{"descricao": "Big bag e sacaria para armazenagem pós-colheita"}])
        self.assertEqual(result[0]["categoria"], "ARMAZENAGEM E POS-COLHEITA")
        self.assertIn("big bag", result[0]["justificativa"].lower())


@override_settings(GEMINI_API_KEY="")
class RAGAgentTests(TestCase):
    def setUp(self) -> None:
        session = self.client.session
        session[SESSION_KEY] = True
        session["gemini_api_key"] = "test-key"
        session.save()

        self.fornecedor = Pessoa.objects.create(
            razao_social="FORNECEDOR RAG",
            cnpj="11111111000111",
            is_fornecedor=True,
        )
        self.faturado = Pessoa.objects.create(
            razao_social="CLIENTE RAG",
            cpf="22222222222",
            is_faturado=True,
        )
        self.classificacao = Classificacao.objects.create(
            tipo=Classificacao.Tipo.DESPESA,
            descricao="MANUTENCAO E OPERACAO",
        )
        self.receita = Classificacao.objects.create(
            tipo=Classificacao.Tipo.RECEITA,
            descricao="RECEITA OPERACIONAL",
        )
        self.insumos = Classificacao.objects.create(
            tipo=Classificacao.Tipo.DESPESA,
            descricao="INSUMOS AGRICOLAS",
        )
        self.movimento = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=self.fornecedor,
            faturado=self.faturado,
            numero_documento="RAG-001",
            data_emissao=date(2024, 1, 15),
            valor_total=Decimal("1500.00"),
            dados_extraidos={"produtos": [{"descricao": "Oleo Diesel S10"}]},
        )
        self.movimento.classificacoes.add(self.classificacao)
        self.movimento_receber = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.ARECEBER,
            pessoa=self.fornecedor,
            faturado=self.faturado,
            numero_documento="RAG-002",
            data_emissao=date(2024, 1, 20),
            valor_total=Decimal("2500.00"),
            dados_extraidos={"produtos": [{"descricao": "Servico de consultoria"}]},
        )
        self.movimento_receber.classificacoes.add(self.receita)
        ParcelaContas.objects.create(
            movimento=self.movimento,
            identificacao="MOV-RAG-P1",
            numero=1,
            data_vencimento=date(2024, 2, 15),
            valor=Decimal("1500.00"),
        )
        ParcelaContas.objects.create(
            movimento=self.movimento_receber,
            identificacao="MOV-RAG-P2",
            numero=1,
            data_vencimento=date(2024, 2, 20),
            valor=Decimal("2500.00"),
        )
        beltrano = Pessoa.objects.create(
            razao_social="BELTRANO DE SOUZA",
            cpf="11111111111",
            is_faturado=True,
        )
        supplier_2025 = Pessoa.objects.create(
            razao_social="FORNECEDOR 2025",
            cnpj="33333333000133",
            is_fornecedor=True,
        )
        supplier_2025_top = Pessoa.objects.create(
            razao_social="FORNECEDOR MAIOR 2025",
            cnpj="44444444000144",
            is_fornecedor=True,
        )
        self.movimento_2025_a = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=supplier_2025,
            faturado=beltrano,
            numero_documento="RAG-2025-A",
            data_emissao=date(2025, 3, 10),
            valor_total=Decimal("1000.00"),
            dados_extraidos={"produtos": [{"descricao": "Correia de transmissao e rolamento do eixo"}]},
        )
        self.movimento_2025_a.classificacoes.add(self.classificacao)
        self.movimento_2025_b = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=supplier_2025_top,
            faturado=beltrano,
            numero_documento="RAG-2025-B",
            data_emissao=date(2025, 4, 10),
            valor_total=Decimal("3500.00"),
            dados_extraidos={"produtos": [{"descricao": "Fungicida para ferrugem asiatica"}]},
        )
        self.movimento_2025_b.classificacoes.add(self.insumos)
        ParcelaContas.objects.create(
            movimento=self.movimento_2025_a,
            identificacao="RAG-2025-A-P1",
            numero=1,
            data_vencimento=date(2025, 8, 15),
            valor=Decimal("1000.00"),
        )
        ParcelaContas.objects.create(
            movimento=self.movimento_2025_b,
            identificacao="RAG-2025-B-P1",
            numero=1,
            data_vencimento=date(2025, 9, 15),
            valor=Decimal("3500.00"),
        )
        InvoiceExtraction.objects.create(
            file_name="nota-rag.pdf",
            file_size=123,
            provider="mock",
            status=InvoiceExtraction.Status.SUCCESS,
            result_json={
                "numero_nota_fiscal": "RAG-001",
                "data_emissao": "2024-01-15",
                "fornecedor": {"razao_social": "FORNECEDOR RAG"},
                "faturado": {"nome_completo": "CLIENTE RAG"},
                "produtos": [{"descricao": "Oleo Diesel S10"}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-15", "valor": 1500.0}],
                "valor_total": 1500.0,
                "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO", "justificativa": "diesel"}],
            },
        )

    def _source_by_id(self, payload: dict, source_id: str) -> dict:
        return next(source for source in payload["sources"] if source["source"] == source_id)

    def _row_value(self, source: dict, field: str) -> str:
        return next(row["valor"] for row in source["rows"] if row["campo"] == field)

    def test_simple_rag_returns_database_sources_and_local_answer(self) -> None:
        payload = Agent3().run_query("Qual nota de diesel esta em contas a pagar?", "simple")
        payable_summary = self._source_by_id(payload, "Analitico:APAGAR:Resumo")

        self.assertEqual(payload["mode"], "simple")
        self.assertEqual(payload["intent"], "exploratoria")
        self.assertEqual(payload["provider"], "local")
        self.assertIn("RAG simples", payload["answer"])
        self.assertEqual(payload["context_usage"]["status"], "OTIMO")
        self.assertEqual(self._row_value(payable_summary, "Total"), "R$ 6000.00")
        self.assertEqual(payable_summary["score_status"], "ALTO")
        self.assertTrue(payload["sources"])
        self.assertTrue(payload["answer_documents"])
        self.assertTrue(payload["summary_sources"])
        self.assertTrue(payload["evidence_sources"])
        self.assertTrue(any("RAG-001" in source["content"] for source in payload["evidence_sources"]))

    def test_embeddings_rag_returns_semantic_sources(self) -> None:
        payload = Agent3().run_query("Qual total de contas a pagar?", "embeddings")
        payable_summary = self._source_by_id(payload, "Analitico:APAGAR:Resumo")

        self.assertEqual(payload["mode"], "embeddings")
        self.assertEqual(payload["intent"], "financeira_agregada")
        self.assertEqual(payload["provider"], "local")
        self.assertIn("RAG com embeddings", payload["answer"])
        self.assertLessEqual(payload["context_usage"]["document_count"], 3)
        self.assertEqual(self._row_value(payable_summary, "Total"), "R$ 6000.00")
        self.assertEqual(payable_summary["score_status"], "ALTO")
        self.assertTrue(payload["sources"])
        self.assertTrue(payload["evidence_sources"])

    def test_payable_and_receivable_totals_are_consistent_across_modes(self) -> None:
        simple_payable = Agent3().run_query("total de contas a pagar", "simple")
        embedding_payable = Agent3().run_query("total de contas a pagar", "embeddings")
        simple_receivable = Agent3().run_query("total de contas a receber", "simple")
        embedding_receivable = Agent3().run_query("total de contas a receber", "embeddings")

        self.assertEqual(self._row_value(self._source_by_id(simple_payable, "Analitico:APAGAR:Resumo"), "Total"), "R$ 6000.00")
        self.assertEqual(self._row_value(self._source_by_id(embedding_payable, "Analitico:APAGAR:Resumo"), "Total"), "R$ 6000.00")
        self.assertEqual(self._row_value(self._source_by_id(simple_receivable, "Analitico:ARECEBER:Resumo"), "Total"), "R$ 2500.00")
        self.assertEqual(self._row_value(self._source_by_id(embedding_receivable, "Analitico:ARECEBER:Resumo"), "Total"), "R$ 2500.00")

    @patch(
        "invoices.views.Agent3.run_query",
        return_value={
            "mode": "simple",
            "provider": "local",
            "usage": {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0},
            "context_usage": {"status": "OTIMO", "document_count": 1, "estimated_input_tokens": 10, "intent": "exploratoria"},
            "answer": "Resposta de teste.",
            "answer_documents": [
                {
                    "title": "Resumo",
                    "source": "MovimentoContas:1",
                    "score": 1,
                    "score_label": "ALTO",
                    "score_status": "ALTO",
                    "rows": [{"campo": "Documento", "valor": "RAG-001"}],
                    "content": "Resumo",
                }
            ],
            "sources": [{"source": "MovimentoContas:1", "title": "Resumo", "rows": []}],
            "summary_sources": [{"source": "MovimentoContas:1"}],
            "evidence_sources": [{"source": "MovimentoContas:1", "content": "RAG-001"}],
        },
    )
    def test_rag_query_view_renders_answer(self, _mock_run_query) -> None:
        response = self.client.get(
            reverse("invoices:rag_query"),
            {"query": "Quais movimentos existem?", "mode": "simple"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resposta do Agente")
        self.assertContains(response, "FORNECEDOR RAG")
        self.assertContains(response, "Resumo do que foi usado pelo agente")
        self.assertContains(response, "Status do Score")
        self.assertContains(response, "Mostrar mais resumos")
        self.assertContains(response, "Documentos do banco")
        self.assertContains(response, "Mais detalhes")
        self.assertContains(response, "rag-movement-catalog")
        self.assertContains(response, "Cadastros")
        self.assertContains(response, "Carregando RAG")
        self.assertNotContains(response, "Documentos recuperados")

    def test_rag_adds_yearly_invoice_supplier_analytics(self) -> None:
        payload = Agent3().run_query("Qual foi o valor total pago em Notas Fiscais emitidas no ano de 2025, e qual foi o fornecedor responsável pelo maior valor nesse período?", "simple")
        source = self._source_by_id(payload, "Analitico:Notas:2025:APAGAR")

        self.assertEqual(payload["intent"], "financeira_filtrada")
        self.assertEqual(self._row_value(source, "Valor total"), "R$ 4500.00")
        self.assertEqual(self._row_value(source, "Fornecedor maior valor"), "FORNECEDOR MAIOR 2025")
        self.assertEqual(self._row_value(source, "Valor do fornecedor"), "R$ 3500.00")

    def test_rag_adds_billed_installments_year_analytics(self) -> None:
        payload = Agent3().run_query("Para o Faturado 'BELTRANO DE SOUZA' (CPF 111.111.111-11), qual é a soma total dos valores das parcelas que ainda vencerão dentro do ano de 2025?", "simple")
        source = self._source_by_id(payload, "Analitico:Parcelas:2025:11111111111")

        self.assertEqual(payload["intent"], "financeira_filtrada")
        self.assertEqual(self._row_value(source, "Total das parcelas"), "R$ 4500.00")
        self.assertEqual(self._row_value(source, "Quantidade de parcelas"), "2")

    def test_rag_adds_semantic_product_analytics(self) -> None:
        payload = Agent3().run_query(
            "Existem Notas Fiscais classificadas como MANUTENCAO E OPERACAO cujos itens se assemelham a pecas de reposicao, correias ou rolamentos?",
            "embeddings",
        )
        source = self._source_by_id(payload, "Analitico:Semantico:MANUTENCAO E OPERACAO")

        self.assertEqual(payload["intent"], "semantica")
        self.assertEqual(self._row_value(source, "Notas compatíveis"), "1")
        self.assertIn("Correia de transmissao", source["content"] + str(source["rows"]))

    def test_rag_adds_largest_insumos_invoice_analytics(self) -> None:
        payload = Agent3().run_query("Qual é a despesa de 'INSUMOS AGRÍCOLAS' que tem o maior valor de Nota Fiscal total, e que tipo de 'pragas e doenças' o item se destina a combater, de acordo com o nome do produto?", "embeddings")
        source = self._source_by_id(payload, "Analitico:MaiorNota:INSUMOS AGRICOLAS")

        self.assertEqual(payload["intent"], "semantica")
        self.assertEqual(self._row_value(source, "Nota"), "RAG-2025-B")
        self.assertEqual(self._row_value(source, "Valor total"), "R$ 3500.00")
        self.assertEqual(self._row_value(source, "Pragas/doenças inferidas"), "doenças fúngicas")

    def test_rag_yearly_invoice_analytics_deduplicates_reimported_notes(self) -> None:
        duplicate = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=self.movimento_2025_b.pessoa,
            faturado=self.movimento_2025_b.faturado,
            numero_documento="RAG-2025-B-2",
            data_emissao=self.movimento_2025_b.data_emissao,
            valor_total=self.movimento_2025_b.valor_total,
            dados_extraidos=self.movimento_2025_b.dados_extraidos,
        )
        duplicate.classificacoes.add(self.insumos)

        payload = Agent3().run_query(
            "Qual foi o valor total pago em Notas Fiscais emitidas no ano de 2025, e qual foi o fornecedor responsável pelo maior valor nesse período?",
            "simple",
        )
        source = self._source_by_id(payload, "Analitico:Notas:2025:APAGAR")

        self.assertEqual(payload["provider"], "local")
        self.assertIn("duplicadas por reimportação", payload["answer"])
        self.assertEqual(self._row_value(source, "Quantidade de notas"), "2")
        self.assertEqual(self._row_value(source, "Valor total"), "R$ 4500.00")
        self.assertEqual(self._row_value(source, "Fornecedor maior valor"), "FORNECEDOR MAIOR 2025")

    def test_rag_billed_installments_filter_by_cpf_and_deduplicate_reimports(self) -> None:
        duplicate = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.ARECEBER,
            pessoa=self.movimento_2025_b.pessoa,
            faturado=self.movimento_2025_b.faturado,
            numero_documento="RAG-2025-B-9",
            data_emissao=self.movimento_2025_b.data_emissao,
            valor_total=self.movimento_2025_b.valor_total,
            dados_extraidos=self.movimento_2025_b.dados_extraidos,
        )
        duplicate.classificacoes.add(self.insumos)
        ParcelaContas.objects.create(
            movimento=duplicate,
            identificacao="RAG-2025-B-DUP-P1",
            numero=1,
            data_vencimento=date(2025, 9, 15),
            valor=Decimal("3500.00"),
        )

        payload = Agent3().run_query(
            "Para o Faturado BELTRANO DE SOUZA CPF 111.111.111-11, qual é a soma total dos valores das parcelas que ainda vencerão dentro do ano de 2025?",
            "simple",
        )
        source = self._source_by_id(payload, "Analitico:Parcelas:2025:11111111111")

        self.assertEqual(payload["provider"], "local")
        self.assertEqual(self._row_value(source, "Faturado encontrado"), "BELTRANO DE SOUZA")
        self.assertEqual(self._row_value(source, "Quantidade de parcelas"), "2")
        self.assertEqual(self._row_value(source, "Total das parcelas"), "R$ 4500.00")

    def test_rag_billed_installments_can_filter_by_faturado_name_without_document(self) -> None:
        payload = Agent3().run_query(
            "Para o Faturado BELTRANO DE SOUZA, qual é a soma total dos valores das parcelas que ainda vencerão dentro do ano de 2025?",
            "simple",
        )
        source = self._source_by_id(payload, "Analitico:Parcelas:2025:beltrano-de-souza")

        self.assertEqual(self._row_value(source, "Faturado/documento consultado"), "BELTRANO DE SOUZA")
        self.assertEqual(self._row_value(source, "Total das parcelas"), "R$ 4500.00")

    def test_rag_semantic_maintenance_does_not_match_limestone_without_requested_terms(self) -> None:
        self.movimento_2025_a.dados_extraidos = {"produtos": [{"descricao": "Calcario agricola para melhorar o solo"}]}
        self.movimento_2025_a.save(update_fields=["dados_extraidos"])

        payload = Agent3().run_query(
            "Existem Notas Fiscais classificadas como MANUTENCAO E OPERACAO cujos itens se assemelham a produtos usados para melhorar o solo, como corretivos ou neutralizadores?",
            "embeddings",
        )
        source = self._source_by_id(payload, "Analitico:Semantico:MANUTENCAO E OPERACAO")

        self.assertEqual(payload["provider"], "local")
        self.assertEqual(self._row_value(source, "Notas compatíveis"), "0")
        self.assertNotIn("calcário por aproximação", payload["answer"])

    def test_rag_largest_insumos_reports_targeted_note_when_largest_has_no_pest_target(self) -> None:
        fertilizer = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=self.movimento_2025_b.pessoa,
            faturado=self.movimento_2025_b.faturado,
            numero_documento="RAG-FERT-2025",
            data_emissao=date(2025, 5, 10),
            valor_total=Decimal("8000.00"),
            dados_extraidos={"produtos": [{"descricao": "Fertilizante NPK 15-15-15"}]},
        )
        fertilizer.classificacoes.add(self.insumos)

        payload = Agent3().run_query(
            "Qual é a despesa de INSUMOS AGRICOLAS que tem o maior valor de Nota Fiscal total, e que tipo de pragas e doenças o item se destina a combater, de acordo com o nome do produto?",
            "embeddings",
        )
        source = self._source_by_id(payload, "Analitico:MaiorNota:INSUMOS AGRICOLAS")

        self.assertEqual(payload["provider"], "local")
        self.assertEqual(self._row_value(source, "Nota"), "RAG-FERT-2025")
        self.assertEqual(self._row_value(source, "Pragas/doenças inferidas"), "Não identificado pelo nome do produto")
        self.assertEqual(self._row_value(source, "Maior nota com alvo fitossanitário"), "RAG-2025-B")
        self.assertEqual(self._row_value(source, "Alvo fitossanitário inferido"), "doenças fúngicas")
        self.assertIn("não deve inventar alvo fitossanitário", payload["answer"])

    def test_rag_document_detail_query_uses_explicit_references_instead_of_largest_summary(self) -> None:
        payload = Agent3().run_query(
            "Descreva a parcela 000.005.531, e a 000.012.773. Descreva em detalhes as pragas e doenças que os itens combatem, e se faz mal aos seres humanos.",
            "embeddings",
        )

        self.assertEqual(payload["provider"], "local")
        self.assertIn("Detalhamento das notas solicitadas", payload["answer"])
        self.assertIn("000.005.531", payload["answer"])
        self.assertIn("não encontrei uma nota ativa com esse número", payload["answer"])
        self.assertNotIn("A maior nota classificada como INSUMOS AGRICOLAS", payload["answer"])

    def test_rag_can_describe_explicit_invoice_number(self) -> None:
        invoice = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=self.movimento_2025_b.pessoa,
            faturado=self.movimento_2025_b.faturado,
            numero_documento="000005531",
            data_emissao=date(2025, 2, 15),
            valor_total=Decimal("7050.00"),
            dados_extraidos={"produtos": [{"descricao": "Correia de transmissao, rolamento e filtro de oleo"}]},
        )
        invoice.classificacoes.add(self.classificacao)

        payload = Agent3().run_query("Poderia descrever a nota 000005531?", "embeddings")

        self.assertEqual(payload["provider"], "local")
        self.assertIn("000005531", payload["answer"])
        self.assertIn("Detalhamento das notas solicitadas", payload["answer"])
        self.assertIn("CORREIA", payload["answer"].upper())
        self.assertNotIn("não é possível descrever", payload["answer"])

    def test_rag_supplier_invoice_listing_deduplicates_reimports(self) -> None:
        duplicate = MovimentoContas.objects.create(
            tipo=MovimentoContas.Tipo.APAGAR,
            pessoa=self.movimento_2025_b.pessoa,
            faturado=self.movimento_2025_b.faturado,
            numero_documento="RAG-2025-B-2",
            data_emissao=self.movimento_2025_b.data_emissao,
            valor_total=self.movimento_2025_b.valor_total,
            dados_extraidos=self.movimento_2025_b.dados_extraidos,
        )
        duplicate.classificacoes.add(self.insumos)

        payload = Agent3().run_query("Cite as notas fiscais do fornecedor FORNECEDOR MAIOR 2025", "simple")

        self.assertEqual(payload["provider"], "local")
        self.assertIn("RAG-2025-B", payload["answer"])
        self.assertNotIn("RAG-2025-B-2", payload["answer"])

    def test_rag_supplier_invoice_details_do_not_use_raw_extraction_duplicates(self) -> None:
        InvoiceExtraction.objects.create(
            file_name="duplicada.pdf",
            file_size=123,
            provider="mock",
            status=InvoiceExtraction.Status.SUCCESS,
            result_json={
                "numero_nota_fiscal": "RAG-2025-B",
                "fornecedor": {"razao_social": "FORNECEDOR MAIOR 2025"},
            },
        )

        payload = Agent3().run_query("Descreva em detalhes as notas fiscais do fornecedor FORNECEDOR MAIOR 2025", "simple")

        self.assertEqual(payload["provider"], "local")
        self.assertIn("RAG-2025-B", payload["answer"])
        self.assertNotIn("Extração", payload["answer"])

    def test_rag_can_list_installments_by_billed_person_without_year(self) -> None:
        payload = Agent3().run_query("Cite as parcelas do Faturado BELTRANO DE SOUZA CPF 111.111.111-11", "simple")

        self.assertEqual(payload["provider"], "local")
        self.assertIn("BELTRANO DE SOUZA", payload["answer"])
        self.assertIn("RAG-2025-A-P1", payload["answer"])
        self.assertIn("RAG-2025-B-P1", payload["answer"])


class SeedDemoInvoicesCommandTests(TestCase):
    def test_seed_demo_invoices_creates_requested_database_volume(self) -> None:
        call_command("seed_demo_invoices", count=200)

        extractions = InvoiceExtraction.objects.filter(file_name__startswith=DEMO_PREFIX.lower())
        movements = MovimentoContas.objects.filter(numero_documento__startswith=DEMO_PREFIX)

        self.assertEqual(extractions.count(), 200)
        self.assertEqual(movements.count(), 200)
        self.assertEqual(extractions.filter(movimento__isnull=False).count(), 200)
        self.assertGreaterEqual(ParcelaContas.objects.filter(movimento__in=movements).count(), 200)
        self.assertTrue(movements.filter(tipo=MovimentoContas.Tipo.APAGAR).exists())
        self.assertTrue(movements.filter(tipo=MovimentoContas.Tipo.ARECEBER).exists())

    def test_seed_demo_invoices_is_idempotent_without_reset(self) -> None:
        call_command("seed_demo_invoices", count=12)
        call_command("seed_demo_invoices", count=12)

        self.assertEqual(InvoiceExtraction.objects.filter(file_name__startswith=DEMO_PREFIX.lower()).count(), 12)
        self.assertEqual(MovimentoContas.objects.filter(numero_documento__startswith=DEMO_PREFIX).count(), 12)


class DocumentUtilsTests(TestCase):
    def test_cpf_cnpj_cep_masks_and_validation(self) -> None:
        self.assertEqual(only_digits("529.982.247-25"), "52998224725")
        self.assertEqual(only_alnum("61.UV0.OYL/0001-96"), "61UV0OYL000196")
        self.assertTrue(is_valid_cpf("529.982.247-25"))
        self.assertFalse(is_valid_cpf("111.111.111-11"))
        self.assertTrue(is_valid_cnpj("11.222.333/0001-81"))
        self.assertTrue(is_valid_cnpj("61.UV0.OYL/0001-96"))
        self.assertFalse(is_valid_cnpj("11.111.111/1111-11"))
        self.assertEqual(mask_cpf("52998224725"), "529.982.247-25")
        self.assertEqual(mask_cnpj("11222333000181"), "11.222.333/0001-81")
        self.assertEqual(mask_cnpj("61UV0OYL000196"), "61.UV0.OYL/0001-96")
        self.assertEqual(mask_cep("58400000"), "58400-000")
        self.assertEqual(mask_ie("123456789012"), "123.456.789.012")
        self.assertEqual(mask_phone("8333330000"), "(83) 3333-0000")


class Stage4ManageApiTests(TestCase):
    def setUp(self) -> None:
        session = self.client.session
        session[SESSION_KEY] = True
        session["gemini_api_key"] = "test-key"
        session.save()

        self.supplier = Pessoa.objects.create(
            razao_social="FORNECEDOR API",
            cnpj="12000111000190",
            municipio="Joao Pessoa",
            uf="PB",
            is_fornecedor=True,
        )
        self.billed = Pessoa.objects.create(
            razao_social="FATURADO API",
            cpf="12345678901",
            municipio="Campina Grande",
            uf="PB",
            is_faturado=True,
        )
        self.classification = Classificacao.objects.create(
            tipo=Classificacao.Tipo.DESPESA,
            descricao="INSUMOS AGRICOLAS",
        )

    def _post_json(self, url: str, payload: dict):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_manage_page_renders_tables_without_preloading_records(self) -> None:
        response = self.client.get(reverse("invoices:manage_records"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastros")
        self.assertContains(response, "Use Buscar ou Todos para carregar registros.")

        api_response = self.client.get(reverse("invoices:manage_collection", args=["pessoas"]))
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.json()["results"], [])

        all_response = self.client.get(reverse("invoices:manage_collection", args=["pessoas"]), {"all": "1"})
        self.assertEqual(len(all_response.json()["results"]), 2)

    def test_people_create_search_multiple_terms_and_logical_delete(self) -> None:
        response = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {
                "razao_social": "CLIENTE MULTI BUSCA",
                "cpf": "529.982.247-25",
                "municipio": "Joao Pessoa",
                "uf": "PB",
                "is_cliente": True,
            },
        )

        self.assertEqual(response.status_code, 201)
        created = Pessoa.objects.get(cpf="52998224725")
        self.assertTrue(created.ativo)
        self.assertTrue(created.is_cliente)
        self.assertFalse(created.is_faturado)

        search_response = self.client.get(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {"q": "CLIENTE Pessoa", "order": "razao_social"},
        )
        self.assertEqual(len(search_response.json()["results"]), 1)
        self.assertEqual(search_response.json()["results"][0]["razao_social"], "CLIENTE MULTI BUSCA")

        delete_response = self.client.delete(reverse("invoices:manage_detail", args=["pessoas", created.id]))
        created.refresh_from_db()
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(created.ativo)

        all_response = self.client.get(reverse("invoices:manage_collection", args=["pessoas"]), {"all": "1"})
        self.assertNotIn(created.id, [item["id"] for item in all_response.json()["results"]])

    def test_people_reject_duplicate_cpf_with_clear_message(self) -> None:
        response = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {
                "razao_social": "CPF DUPLICADO",
                "cpf": "123.456.789-01",
                "is_cliente": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("CPF já cadastrado", response.json()["error"])

    def test_people_reject_missing_document_or_role(self) -> None:
        missing_document = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {"razao_social": "PESSOA SEM DOCUMENTO", "is_cliente": True},
        )
        missing_role = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {"razao_social": "PESSOA SEM PAPEL", "cpf": "529.982.247-25"},
        )

        self.assertEqual(missing_document.status_code, 400)
        self.assertEqual(missing_role.status_code, 400)
        self.assertIn("CPF ou CNPJ", missing_document.json()["error"])
        self.assertIn("papel", missing_role.json()["error"])

    def test_people_reject_invalid_document_and_multiple_roles(self) -> None:
        invalid_cpf = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {"razao_social": "CPF INVALIDO", "cpf": "111.111.111-11", "is_cliente": True},
        )
        multiple_roles = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {"razao_social": "PAPEIS DUPLOS", "cpf": "529.982.247-25", "is_cliente": True, "is_faturado": True},
        )

        self.assertEqual(invalid_cpf.status_code, 400)
        self.assertEqual(multiple_roles.status_code, 400)
        self.assertIn("CPF invalido", invalid_cpf.json()["error"])
        self.assertIn("exatamente um papel", multiple_roles.json()["error"])

    def test_people_accept_alphanumeric_cnpj_and_mask_documents(self) -> None:
        response = self._post_json(
            reverse("invoices:manage_collection", args=["pessoas"]),
            {
                "razao_social": "CNPJ ALFA",
                "cnpj": "61.UV0.OYL/0001-96",
                "inscricao_estadual": "123456789012",
                "telefone": "(83) 99999-0000",
                "cep": "58000-000",
                "is_fornecedor": True,
            },
        )

        self.assertEqual(response.status_code, 201)
        record = response.json()["record"]
        self.assertEqual(record["cnpj"], "61.UV0.OYL/0001-96")
        self.assertEqual(record["inscricao_estadual"], "123.456.789.012")
        self.assertEqual(record["telefone"], "(83) 99999-0000")
        self.assertEqual(record["cep"], "58000-000")

    def test_classification_update_keeps_status_hidden_and_active(self) -> None:
        response = self._post_json(
            reverse("invoices:manage_detail", args=["classificacoes", self.classification.id]),
            {"tipo": "DESPESA", "descricao": "MANUTENCAO E OPERACAO", "ativo": False},
        )

        self.classification.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.classification.ativo)
        self.assertEqual(self.classification.descricao, "MANUTENCAO E OPERACAO")

    def test_accounts_create_list_sort_and_logical_delete(self) -> None:
        response = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {
                "tipo": "APAGAR",
                "pessoa_id": self.supplier.id,
                "faturado_id": self.billed.id,
                "numero_documento": "CRUD-001",
                "data_emissao": "2026-06-17",
                "valor_total": "1234.56",
                "classificacao_ids": [self.classification.id],
                "observacoes": "Conta criada pela etapa 4",
            },
        )

        self.assertEqual(response.status_code, 201)
        movement = MovimentoContas.objects.get(nome_documento__contains="FORNECEDOR API - NF MANUAL")
        self.assertTrue(movement.ativo)
        self.assertIn("FORNECEDOR API - NF", movement.nome_documento)
        self.assertEqual(movement.classificacoes.get(), self.classification)

        list_response = self.client.get(
            reverse("invoices:manage_collection", args=["contas"]),
            {"all": "1", "order": "-valor_total"},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertIn("FORNECEDOR API - NF", list_response.json()["results"][0]["nome_documento"])

        delete_response = self.client.delete(reverse("invoices:manage_detail", args=["contas", movement.id]))
        movement.refresh_from_db()
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(movement.ativo)

    def test_accounts_accept_brazilian_currency_mask_and_max_value(self) -> None:
        response = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {
                "tipo": "APAGAR",
                "pessoa_id": self.supplier.id,
                "faturado_id": self.billed.id,
                "numero_documento": "CRUD-MAX",
                "data_emissao": date.today().isoformat(),
                "valor_total": "R$ 999.999.999.999,00",
                "classificacao_ids": [self.classification.id],
            },
        )

        self.assertEqual(response.status_code, 201)
        movement = MovimentoContas.objects.get(nome_documento__contains="FORNECEDOR API - NF MANUAL")
        self.assertEqual(movement.valor_total, Decimal("999999999999.00"))

    def test_accounts_reject_incomplete_invalid_dates_and_excessive_values(self) -> None:
        base_payload = {
            "tipo": "APAGAR",
            "pessoa_id": self.supplier.id,
            "faturado_id": self.billed.id,
            "numero_documento": "CRUD-INVALID",
            "data_emissao": date.today().isoformat(),
            "valor_total": "100.00",
            "classificacao_ids": [self.classification.id],
        }

        missing_person = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {**base_payload, "pessoa_id": ""},
        )
        old_date = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {**base_payload, "data_emissao": "1909-12-31"},
        )
        future_date = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {**base_payload, "data_emissao": "2099-01-01"},
        )
        excessive_value = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {**base_payload, "valor_total": "R$ 1.000.000.000.000,00"},
        )
        missing_classification = self._post_json(
            reverse("invoices:manage_collection", args=["contas"]),
            {**base_payload, "classificacao_ids": []},
        )

        self.assertEqual(missing_person.status_code, 400)
        self.assertEqual(old_date.status_code, 400)
        self.assertEqual(future_date.status_code, 400)
        self.assertEqual(excessive_value.status_code, 400)
        self.assertEqual(missing_classification.status_code, 400)
        self.assertIn("1910", old_date.json()["error"])
        self.assertIn("futura", future_date.json()["error"])
        self.assertIn("999.999.999.999,00", excessive_value.json()["error"])
        self.assertIn("classificacao", missing_classification.json()["error"])


class ValidationAgentTests(TestCase):
    def setUp(self) -> None:
        self.validator = ValidationAgent()

    def test_validation_normalizes_minimum_contract_shape(self) -> None:
        normalized = self.validator.normalize(
            {
                "fornecedor": {"razao_social": "Fornecedor", "fantasia": "Fantasia", "cnpj": "00.000.000/0001-00"},
                "faturado": {"nome": "Cliente Exemplo", "cpf": "123.456.789-00"},
                "numero": "123",
                "dataEmissao": "2024-01-01",
                "itens": [{"item": "Peças de manutenção", "qtd": 2}],
                "parcelas": {"parcela": 1, "vencimento": "2024-01-30", "valor_total": 100},
                "valorTotal": "100.50",
                "tipoDespesa": "MANUTENCAO E OPERACAO",
            }
        )

        self.assertEqual(normalized["numero_nota_fiscal"], "123")
        self.assertEqual(normalized["data_emissao"], "2024-01-01")
        self.assertEqual(normalized["produtos"][0]["descricao"], "Peças de manutenção")
        self.assertIsInstance(normalized["produtos"], list)
        self.assertIsInstance(normalized["parcelas"], list)
        self.assertIsInstance(normalized["classificacoes_despesa"], list)
        self.assertEqual(normalized["classificacoes_despesa"][0]["categoria"], "MANUTENCAO E OPERACAO")

    def test_validation_preserves_important_danfe_fields(self) -> None:
        normalized = self.validator.normalize(
            {
                "fornecedor": {
                    "razao_social": "Fornecedor Completo",
                    "fantasia": "Fornecedor",
                    "cnpj": "00.000.000/0001-00",
                    "ie": "123456789",
                    "logradouro": "Rua Fiscal",
                    "numero": "10",
                    "bairro": "Centro",
                    "cidade": "Joao Pessoa",
                    "uf": "PB",
                    "cep": "58000-000",
                },
                "destinatario": {
                    "nome": "Cliente Completo",
                    "cpf": "123.456.789-00",
                    "logradouro": "Avenida Cliente",
                    "numero": "20",
                    "cidade": "Campina Grande",
                    "uf": "PB",
                },
                "numero": "123",
                "serie": "1",
                "chave_de_acesso": "25240112345678000190550010001234561000000010",
                "natureza_da_operacao": "Venda",
                "protocolo": "325240000000000",
                "dataEmissao": "2024-01-01",
                "data_saida": "2024-01-01",
                "hora_saida": "10:30:00",
                "itens": [
                    {
                        "codigo_produto": "001",
                        "item": "Fungicida agricola",
                        "ncm": "38089291",
                        "cst": "060",
                        "cfop": "5102",
                        "un": "UN",
                        "qtd": "2",
                        "valor_unit": "100,00",
                        "total": "200,00",
                    }
                ],
                "parcelas": {"parcela": 1, "duplicata": "001", "vencimento": "2024-02-01", "valor_total": "200,00"},
                "valorTotal": "200,00",
                "valor_total_produtos": "200,00",
                "bc_icms": "200,00",
                "icms": "36,00",
                "entrega": {"nome": "Local Entrega", "cnpj": "11.111.111/0001-11", "cidade": "Sousa", "uf": "PB"},
                "transporte": {
                    "transportador": "Transportadora",
                    "cnpj": "22.222.222/0001-22",
                    "placa": "ABC1D23",
                    "modalidade_frete": "Emitente",
                    "peso_bruto": "10 KG",
                },
                "informacoes_adicionais": "Dados adicionais da nota.",
                "classificacoes_despesa": [{"categoria": "INSUMOS AGRICOLAS", "justificativa": "Produto agricola."}],
            }
        )

        self.assertEqual(normalized["fornecedor"]["inscricao_estadual"], "123456789")
        self.assertEqual(normalized["fornecedor"]["endereco"], "Rua Fiscal")
        self.assertEqual(normalized["faturado"]["municipio"], "Campina Grande")
        self.assertEqual(normalized["serie"], "1")
        self.assertEqual(normalized["chave_acesso"], "25240112345678000190550010001234561000000010")
        self.assertEqual(normalized["natureza_operacao"], "Venda")
        self.assertEqual(normalized["protocolo_autorizacao"], "325240000000000")
        self.assertEqual(normalized["produtos"][0]["codigo"], "001")
        self.assertEqual(normalized["produtos"][0]["ncm"], "38089291")
        self.assertEqual(normalized["produtos"][0]["cfop"], "5102")
        self.assertEqual(normalized["produtos"][0]["valor_unitario"], 100.0)
        self.assertEqual(normalized["parcelas"][0]["descricao"], "001")
        self.assertEqual(normalized["valor_produtos"], 200.0)
        self.assertEqual(normalized["valor_icms"], 36.0)
        self.assertEqual(normalized["local_entrega"]["municipio"], "Sousa")
        self.assertEqual(normalized["transportador"]["razao_social"], "Transportadora")
        self.assertEqual(normalized["informacoes_complementares"], "Dados adicionais da nota.")

    def test_validation_rejects_invalid_classification_shape(self) -> None:
        invalid_data = {
            "fornecedor": {"razao_social": "Fornecedor", "fantasia": "Fantasia", "cnpj": "00.000.000/0001-00"},
            "faturado": {"nome_completo": "Cliente", "cpf": "123.456.789-00"},
            "numero_nota_fiscal": "123",
            "data_emissao": "2024-01-01",
            "produtos": [{"descricao": "item", "quantidade": 1}],
            "parcelas": [{"numero": 1, "data_vencimento": "2024-01-10", "valor": 10}],
            "valor_total": 10,
            "classificacoes_despesa": [{"categoria": "MANUTENCAO E OPERACAO"}],
        }

        with self.assertRaises(ValueError):
            self.validator.validate(invalid_data)


class InvoiceWorkflowApiTests(TestCase):
    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDOR PAGO",
                    "fantasia": "FORN PAGO",
                    "cnpj": "11.111.111/0001-11",
                },
                "faturado": {"nome_completo": "PESSOA FISCAL", "cpf": "222.222.222-22"},
                "numero_nota_fiscal": "995",
                "data_emissao": "2024-01-01",
                "natureza_operacao": "Compra de insumo",
                "produtos": [{"descricao": "Peça de reposição e reparo", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "ADMINISTRATIVAS", "justificativa": "Despesa administrativa."}],
            },
            provider="gemini",
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_analyze_infers_apagar_movement_type(self, _mock_extract) -> None:
        response = self.client.post(
            reverse("invoices:extract_invoice"),
            {"pdf": SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")},
        )

        payload = response.json()
        analysis_response = self.client.post(reverse("invoices:analyze_invoice", args=[payload["id"]]), {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(analysis_response.status_code, 200)
        analysis_payload = analysis_response.json()
        self.assertIn("movement_type", analysis_payload)
        self.assertEqual(analysis_payload["analysis"]["blocks"][0]["movement_type"], MovimentoContas.Tipo.APAGAR)
        self.assertEqual(analysis_payload["movement_type"], MovimentoContas.Tipo.APAGAR)

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {"razao_social": "CLIENTE", "fantasia": "CLIENTE", "cnpj": "11.111.111/0001-11"},
                "faturado": {"nome_completo": "CLIENTE FINAL", "cpf": "222.222.222-22"},
                "numero_nota_fiscal": "990",
                "data_emissao": "2024-01-01",
                "natureza_operacao": "Prestacao de servicos faturados",
                "produtos": [{"descricao": "Honorarios de consultoria", "quantidade": 1}],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [{"categoria": "PROVENTOS", "justificativa": "Faturamento de serviço"}],
            },
            provider="gemini",
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_analyze_infers_areceber_movement_type(self, _mock_extract) -> None:
        response = self.client.post(
            reverse("invoices:extract_invoice"),
            {"pdf": SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")},
        )

        payload = response.json()
        analysis_response = self.client.post(reverse("invoices:analyze_invoice", args=[payload["id"]]), {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(analysis_response.status_code, 200)
        analysis_payload = analysis_response.json()
        self.assertIn("movement_type", analysis_payload)
        self.assertEqual(analysis_payload["analysis"]["blocks"][0]["movement_type"], MovimentoContas.Tipo.ARECEBER)
        self.assertEqual(analysis_payload["movement_type"], MovimentoContas.Tipo.ARECEBER)

    @patch(
        "invoices.services.PdfExtractionAgent.extract",
        return_value=ExtractionResult(
            data={
                "fornecedor": {
                    "razao_social": "FORNECEDORA MISTA",
                    "fantasia": "FORN MISTA",
                    "cnpj": "55.555.555/0001-55",
                },
                "faturado": {
                    "nome_completo": "CLIENTE MISTO",
                    "cpf": "111.111.111-11",
                },
                "numero_nota_fiscal": "992",
                "data_emissao": "2024-01-01",
                "natureza_operacao": "Venda e manutenção",
                "produtos": [
                    {"descricao": "Servico de consultoria", "quantidade": 1},
                    {"descricao": "Oleo Diesel S10", "quantidade": 2},
                ],
                "parcelas": [{"numero": 1, "data_vencimento": "2024-02-01", "valor": 100.0}],
                "valor_total": 100.0,
                "classificacoes_despesa": [
                    {"categoria": "PROVENTOS", "justificativa": "Receita por serviço."},
                    {"categoria": "MANUTENCAO E OPERACAO", "justificativa": "Despesa de manutenção."},
                ],
            },
            provider="gemini",
        ),
    )
    @override_settings(GEMINI_API_KEY="test-key")
    def test_analyze_infers_misto_movement_type(self, _mock_extract) -> None:
        response = self.client.post(
            reverse("invoices:extract_invoice"),
            {"pdf": SimpleUploadedFile("nota_fiscal.pdf", b"%PDF-1.4 mock", content_type="application/pdf")},
        )

        payload = response.json()
        analysis_response = self.client.post(reverse("invoices:analyze_invoice", args=[payload["id"]]), {})
        analysis_payload = analysis_response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(analysis_response.status_code, 200)
        self.assertEqual(analysis_payload["movement_type"], "MISTO")
        self.assertEqual(len(analysis_payload["analysis"]["blocks"]), 2)

