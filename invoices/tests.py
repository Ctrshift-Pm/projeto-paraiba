from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from invoices.agents.extraction import ExtractionResult
from invoices.agents import ExpenseClassificationAgent, ValidationAgent
from invoices.agents.extraction import PdfExtractionAgent
from invoices.services import InvoiceExtractionService
from invoices.models import InvoiceExtraction


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

        def fake_save_success(uploaded_file, data, provider):
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
                "ValidationAgent.normalize",
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

    def test_read_real_pdf_with_pypdf(self) -> None:
        pdf_path = Path(r"C:\Users\pmgam\Downloads\danfe (beltrano - insumos).pdf")
        if not pdf_path.exists():
            self.skipTest("Arquivo local ausente. Consulte README para validação manual com esse PDF.")

        with pdf_path.open("rb") as file:
            uploaded_pdf = SimpleUploadedFile("danfe (beltrano - insumos).pdf", file.read(), content_type="application/pdf")

        agent = PdfExtractionAgent()
        extracted_text = agent._read_pdf_text(uploaded_pdf)
        self.assertTrue(extracted_text)
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


class ExpenseClassificationAgentTests(TestCase):
    def setUp(self) -> None:
        self.classifier = ExpenseClassificationAgent()

    def test_classifies_oil_diesel(self) -> None:
        result = self.classifier.classify([{"descricao": "Oleo Diesel S10"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["categoria"], "MANUTENCAO E OPERACAO")
        self.assertIn("Produto relacionado", result[0]["justificativa"])

    def test_classifies_hydraulic_material(self) -> None:
        result = self.classifier.classify([{"descricao": "Torneira e material hidráulico"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["categoria"], "INFRAESTRUTURA E UTILIDADES")
        self.assertIn("Produto relacionado", result[0]["justificativa"])

    def test_classifies_fungicida_as_insumos_agricolas(self) -> None:
        result = self.classifier.classify([{"descricao": "VESSARYA BOMBONA 10L FUNGICIDA"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["categoria"], "INSUMOS AGRICOLAS")
        self.assertIn("fungicida", result[0]["justificativa"])

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
