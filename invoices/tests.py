from __future__ import annotations
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from invoices.agents.extraction import ExtractionResult
from invoices.agents import ExpenseClassificationAgent, ValidationAgent
from invoices.agents.extraction import PdfExtractionAgent
from invoices.services import InvoiceExtractionService
from invoices.models import Classificacao, InvoiceExtraction, MovimentoContas, ParcelaContas, Pessoa


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

