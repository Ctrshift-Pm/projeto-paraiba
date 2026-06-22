from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from django.conf import settings
from pypdf import PdfReader

from . import gemini_usage
from ..gemini_session import GeminiAccessError, is_gemini_auth_error

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    data: dict
    provider: str
    fallback_reason: str | None = None
    usage: dict = field(default_factory=dict)


class PdfExtractionAgent:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = str(api_key or getattr(settings, "GEMINI_API_KEY", "") or "").strip()

    def extract(self, uploaded_file) -> ExtractionResult:
        pdf_text = self._perceive(uploaded_file)
        return self._process_and_interpret(pdf_text)

    def _perceive(self, uploaded_file) -> str:
        return self._read_pdf_text(uploaded_file)

    def _process_and_interpret(self, pdf_text: str) -> ExtractionResult:
        gemini_api_key = self.api_key
        if gemini_api_key:
            try:
                gemini_result = self._extract_with_gemini(pdf_text)
                data, usage = self._gemini_result_payload(gemini_result)
                return ExtractionResult(
                    data=data,
                    provider="gemini",
                    usage=usage,
                )
            except Exception as exc:
                if is_gemini_auth_error(exc):
                    self._log_gemini_failure(exc)
                    return ExtractionResult(
                        data=self._mock_data(pdf_text),
                        provider="mock",
                        fallback_reason="Chave do Gemini invalida ou expirada.",
                    )
                self._log_gemini_failure(exc)
                return ExtractionResult(
                    data=self._mock_data(pdf_text),
                    provider="mock",
                    fallback_reason=self._safe_fallback_reason(exc),
                )

        return ExtractionResult(
            data=self._mock_data(pdf_text),
            provider="mock",
            fallback_reason="GEMINI_API_KEY nao foi configurada.",
        )

    def _read_pdf_text(self, uploaded_file) -> str:
        uploaded_file.seek(0)
        payload = uploaded_file.read()
        uploaded_file.seek(0)
        try:
            reader = PdfReader(BytesIO(payload), strict=False)
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
        except Exception as exc:
            raise ValueError("Falha ao ler PDF. Envie um arquivo PDF válido e não corrompido.") from exc

        if not text:
            raise ValueError("Nao foi possivel extrair texto do PDF enviado.")

        return text

    def _extract_with_gemini(self, pdf_text: str) -> dict:
        from google import genai

        client = genai.Client(api_key=self.api_key)
        prompt = self._prompt(pdf_text)
        response = client.models.generate_content(
            model=settings.GEMINI_EXTRACTION_MODEL,
            config=self._gemini_config(),
            contents=prompt,
        )
        data, usage = self._gemini_response_payload(response)
        if not usage:
            usage = gemini_usage.estimated_usage_payload(prompt, data)
        return data, usage

    def _gemini_response_payload(self, response) -> tuple[dict, dict]:
        usage = gemini_usage.usage_payload(gemini_usage.response_usage_metadata(response))
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, dict):
            return parsed, usage
        if hasattr(parsed, "model_dump"):
            dumped = parsed.model_dump()
            if isinstance(dumped, dict):
                return dumped, usage

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            text = self._candidate_text(response).strip()
        if not text.strip():
            logger.warning("Gemini retornou resposta vazia na extracao. %s", self._response_debug_summary(response))
        return self._parse_json(text), usage

    def _response_usage_metadata(self, response) -> object:
        return gemini_usage.response_usage_metadata(response)

    def _candidate_text(self, response) -> str:
        chunks = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", "")
                if text:
                    chunks.append(text)
        return "\n".join(chunks)

    def _response_debug_summary(self, response) -> str:
        parts = []
        prompt_feedback = getattr(response, "prompt_feedback", None)
        if prompt_feedback is not None:
            parts.append(f"prompt_feedback={prompt_feedback}")
        for index, candidate in enumerate(getattr(response, "candidates", None) or [], start=1):
            finish_reason = getattr(candidate, "finish_reason", "")
            parts.append(f"candidate_{index}_finish_reason={finish_reason}")
            content = getattr(candidate, "content", None)
            if content is not None:
                parts.append(f"candidate_{index}_content={content}")
        return " | ".join(parts) or "sem detalhes da resposta"

    def _gemini_config(self) -> dict:
        return {
            "max_output_tokens": getattr(settings, "GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS", 8192),
            "response_mime_type": "application/json",
            "response_json_schema": self._response_json_schema(),
            "temperature": 0,
        }

    def _response_json_schema(self) -> dict:
        string_fields = {
            "type": "object",
            "additionalProperties": True,
        }
        return {
            "type": "object",
            "additionalProperties": True,
            "required": [
                "fornecedor",
                "faturado",
                "numero_nota_fiscal",
                "data_emissao",
                "produtos",
                "parcelas",
                "valor_total",
                "classificacoes_despesa",
            ],
            "properties": {
                "fornecedor": string_fields,
                "faturado": string_fields,
                "numero_nota_fiscal": {"type": "string"},
                "serie": {"type": "string"},
                "chave_acesso": {"type": "string"},
                "natureza_operacao": {"type": "string"},
                "protocolo_autorizacao": {"type": "string"},
                "data_emissao": {"type": "string"},
                "data_saida_entrada": {"type": "string"},
                "hora_saida": {"type": "string"},
                "produtos": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "parcelas": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "valor_total": {"type": "number"},
                "classificacoes_despesa": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
            },
        }

    def _gemini_result_payload(self, gemini_result) -> tuple[dict, dict]:
        if isinstance(gemini_result, tuple) and len(gemini_result) == 2:
            data, usage = gemini_result
            return data, usage if isinstance(usage, dict) else {}
        return gemini_result, {}

    def _usage_payload(self, usage_metadata: object) -> dict:
        return gemini_usage.usage_payload(usage_metadata)

    def _estimated_usage_payload(self, prompt: str, data: dict) -> dict:
        return gemini_usage.estimated_usage_payload(prompt, data)

    def _estimate_tokens(self, text: str) -> int:
        return gemini_usage.estimate_tokens(text)

    def _metadata_value(self, value: object, *names: str) -> int:
        return gemini_usage.metadata_value(value, *names)

    def _prompt(self, pdf_text: str) -> str:
        contract = """
Formato esperado do contrato (apenas JSON):
{
  "fornecedor": {
    "razao_social":"", "fantasia":"", "cnpj":"", "inscricao_estadual":"",
    "endereco":"", "numero":"", "bairro":"", "municipio":"", "uf":"", "cep":"", "telefone":""
  },
  "faturado": {
    "nome_completo":"", "cpf":"", "cnpj":"", "inscricao_estadual":"",
    "endereco":"", "numero":"", "bairro":"", "municipio":"", "uf":"", "cep":"", "telefone":""
  },
  "numero_nota_fiscal":"", "serie":"", "chave_acesso":"", "natureza_operacao":"",
  "protocolo_autorizacao":"", "data_emissao":"YYYY-MM-DD", "data_saida_entrada":"YYYY-MM-DD", "hora_saida":"",
  "produtos":[{"codigo":"", "descricao":"", "ncm":"", "cst":"", "cfop":"", "unidade":"", "quantidade":0, "valor_unitario":0.0, "valor_total":0.0}],
  "parcelas":[{"numero":1, "descricao":"", "data_vencimento":"YYYY-MM-DD", "valor":0.0}],
  "valor_total":0.0,
  "valor_produtos":0.0, "valor_frete":0.0, "valor_desconto":0.0, "valor_seguro":0.0, "outras_despesas":0.0,
  "base_calculo_icms":0.0, "valor_icms":0.0, "base_calculo_icms_st":0.0, "valor_icms_st":0.0,
  "valor_ipi":0.0, "valor_pis":0.0, "valor_cofins":0.0,
  "local_entrega": {"nome_razao_social":"", "cpf_cnpj":"", "inscricao_estadual":"", "endereco":"", "numero":"", "bairro":"", "municipio":"", "uf":"", "cep":"", "telefone":""},
  "transportador": {"razao_social":"", "cpf_cnpj":"", "inscricao_estadual":"", "endereco":"", "municipio":"", "uf":"", "placa_veiculo":"", "frete_por_conta":"", "quantidade":"", "especie":"", "peso_bruto":"", "peso_liquido":""},
  "informacoes_complementares":"",
  "classificacoes_despesa":[{"categoria":"", "justificativa":""}]
}
"""
        return f"""
Voce e um extrator especialista em DANFE/NF-e para automacao financeira.
Sua tarefa e ler o texto cru do documento e devolver SOMENTE JSON valido, contendo apenas um objeto JSON.

PROIBICOES ABSOLUTAS:
- nao escreva markdown
- nao escreva comentarios
- nao escreva explicacoes fora do JSON
- nao use chaves extras fora do contrato
- nao invente valores que nao estejam no documento; quando faltar dado, devolva string vazia, lista vazia ou 0 conforme o contrato

Entrada: texto cru da nota fiscal:
\"\"\"{pdf_text[:12000]}\"\"\"

{contract}
INSTRUCOES DE EXTRAÇÃO:
- `fornecedor` = emitente/remetente/prestador que vende ou presta o servico.
- `faturado` = destinatario/comprador/recebedor/tomador.
- `numero_nota_fiscal` = numero da NF/NF-e/DANFE. Nao usar serie no lugar do numero.
- Extrair serie, chave de acesso, natureza da operacao, protocolo/autorizacao, datas e horarios quando estiverem presentes.
- Datas devem ser normalizadas para `YYYY-MM-DD` sempre que possivel.
- Preencher dados cadastrais completos de fornecedor e faturado quando existirem: CNPJ/CPF, IE, endereco, numero, bairro, municipio, UF, CEP, telefone.
- Extrair TODOS os itens de `produtos` identificados no documento com codigo, descricao, NCM, CST/CSOSN, CFOP, unidade, quantidade, valor unitario e valor total quando disponiveis.
- Se houver apenas 1 produto, ainda assim `produtos` deve ser lista.
- `parcelas` deve ser lista. Extrair parcelas e vencimentos somente quando o documento trouxer isso de forma explicita como `vencimento`, `vencto`, `vcto`, `duplicata`, `fatura` ou secao equivalente.
- Nao invente `data_vencimento`: se o vencimento nao estiver explicitamente identificado, deixe vazio.
- Extrair `valor_total` da nota e tambem os totais/impostos: produtos, frete, desconto, seguro, outras despesas, base/valor ICMS, ICMS ST, IPI, PIS e COFINS.
- Extrair `local_entrega`, `transportador` e `informacoes_complementares` quando estiverem presentes.

INSTRUCOES DE INTERPRETACAO DA DESPESA:
- `DESPESA` nao e campo literal da nota. Ela deve ser INTERPRETADA a partir dos produtos/servicos descritos.
- Classifique a despesa somente a partir dos itens/servicos efetivamente encontrados no documento.
- Nunca copie texto livre como categoria. Use somente categorias oficiais.
- Gere `classificacoes_despesa` como lista de objetos com `categoria` e `justificativa`.
- A justificativa deve citar objetivamente os itens/termos do documento que motivaram a classificacao.
- Se houver varios itens com naturezas claramente diferentes, voce pode retornar mais de uma classificacao.
- Prefira a categoria mais especifica possivel.
- Exemplos de mapeamento:
  - diesel, lubrificante, filtro, peca, pneu -> MANUTENCAO E OPERACAO
  - material hidraulico, tubo, torneira, bomba submersa, energia -> INFRAESTRUTURA E UTILIDADES
  - semente, adubo, fertilizante, fungicida, herbicida -> INSUMOS AGRICOLAS
  - frete, armazenagem, secagem, colheita terceirizada -> SERVICOS OPERACIONAIS
  - honorario, contabilidade, taxa bancaria, software administrativo -> ADMINISTRATIVAS
  - racao, suplemento mineral, vacina animal -> NUTRICAO E SAUDE ANIMAL
  - drone, telemetria, gps agricola, software agricola -> TECNOLOGIA E MONITORAMENTO

CATEGORIAS OFICIAIS PERMITIDAS:
  - INSUMOS AGRICOLAS
  - MANUTENCAO E OPERACAO
  - RECURSOS HUMANOS
  - SERVICOS OPERACIONAIS
  - INFRAESTRUTURA E UTILIDADES
  - ADMINISTRATIVAS
  - SEGUROS E PROTECAO
  - IMPOSTOS E TAXAS
  - INVESTIMENTOS
  - NUTRICAO E SAUDE ANIMAL
  - TECNOLOGIA E MONITORAMENTO
  - ARMAZENAGEM E POS-COLHEITA

VALIDACAO FINAL ANTES DE RESPONDER:
- responda apenas com JSON valido
- preserve exatamente os nomes das chaves do contrato
- `classificacoes_despesa` deve conter apenas categorias oficiais
- se houver duvida entre duas categorias, escolha a mais sustentada pelos itens descritos
"""

    def _parse_json(self, text: str) -> dict:
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)

        start = text.find("{")
        end = text.rfind("}")
        if start == -1:
            preview = re.sub(r"\s+", " ", text[:260]).strip() if text else ""
            logger.warning("Gemini nao retornou JSON na extracao. preview=%s", preview or "resposta vazia")
            raise ValueError("Gemini nao retornou JSON.")

        candidate = text[start : end + 1 if end != -1 else len(text)].strip()
        try:
            parsed: dict[str, Any] = json.loads(candidate)
        except json.JSONDecodeError as exc:
            parsed = self._parse_json_repair(candidate, exc)

        if not isinstance(parsed, dict):
            raise ValueError("Gemini nao retornou objeto JSON.")

        return parsed

    def _parse_json_repair(self, candidate: str, original_error: json.JSONDecodeError) -> dict:
        decoder = json.JSONDecoder()
        try:
            parsed, _ = decoder.raw_decode(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        repaired = self._repair_common_json_issues(candidate)
        if repaired != candidate:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        balanced = self._balance_json_suffix(repaired)
        if balanced != repaired:
            try:
                parsed = json.loads(self._repair_common_json_issues(balanced))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        snippet = re.sub(r"\s+", " ", candidate[:220]).strip()
        around_error = self._json_error_context(candidate, original_error.pos)
        logger.warning(
            "Gemini retornou JSON invalido na extracao. pos=%s msg=%s trecho=%s",
            original_error.pos,
            original_error.msg,
            around_error,
        )
        raise ValueError(
            f"Gemini retornou um texto que nao e JSON valido. "
            f"Erro na posicao {original_error.pos}: {original_error.msg}. Trecho inicial: {snippet}"
        ) from original_error

    def _json_error_context(self, candidate: str, position: int, radius: int = 180) -> str:
        start = max(0, position - radius)
        end = min(len(candidate), position + radius)
        return re.sub(r"\s+", " ", candidate[start:end]).strip()

    def _repair_common_json_issues(self, candidate: str) -> str:
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        repaired = re.sub(r"([}\]])\s+(?=[{\[])", r"\1, ", repaired)
        repaired = re.sub(r"([}\]])\s+(?=\"[A-Za-z_][A-Za-z0-9_]*\"\s*:)", r"\1, ", repaired)
        repaired = re.sub(r"(\d|true|false|null|\"[^\"]*\")\s+(?=\"[A-Za-z_][A-Za-z0-9_]*\"\s*:)", r"\1, ", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        repaired = re.sub(r"\bTrue\b", "true", repaired)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        return repaired

    def _balance_json_suffix(self, candidate: str) -> str:
        stack: list[str] = []
        in_string = False
        escaped = False
        for char in candidate:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif char == "]" and stack and stack[-1] == "[":
                stack.pop()

        if in_string:
            candidate += '"'

        closers = {"{": "}", "[": "]"}
        return candidate + "".join(closers[item] for item in reversed(stack))

    def _log_gemini_failure(self, exc: Exception) -> None:
        logger.warning("Falha ao usar Gemini na extracao: %s: %s", exc.__class__.__name__, self._safe_fallback_reason(exc))

    def _safe_fallback_reason(self, exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        gemini_api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
        if gemini_api_key:
            message = message.replace(gemini_api_key, "[redacted]")
        message = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[redacted]", message)
        message = re.sub(r"\s+", " ", message)[:240]
        return f"Falha ao usar Gemini ({exc.__class__.__name__}): {message}"

    def _mock_data(self, pdf_text: str) -> dict:
        lowered = pdf_text.lower()
        product = "Oleo Diesel S10" if "diesel" in lowered or not lowered else "Material Hidraulico"
        due_date = self._extract_due_date(pdf_text)
        return {
            "fornecedor": {
                "razao_social": "EMPRESA FORNECEDORA LTDA",
                "fantasia": "FORNECEDORA",
                "cnpj": "12.345.678/0001-90",
                "inscricao_estadual": "123.456.789.012",
                "endereco": "Rua das Empresas",
                "numero": "100",
                "bairro": "Centro",
                "municipio": "Joao Pessoa",
                "uf": "PB",
                "cep": "58000-000",
                "telefone": "(83) 3333-0000",
            },
            "faturado": {
                "nome_completo": "CLIENTE EXEMPLO",
                "cpf": "123.456.789-00",
                "cnpj": "",
                "inscricao_estadual": "",
                "endereco": "Avenida Cliente",
                "numero": "200",
                "bairro": "Bairro Exemplo",
                "municipio": "Campina Grande",
                "uf": "PB",
                "cep": "58400-000",
                "telefone": "",
            },
            "numero_nota_fiscal": "000123456",
            "serie": "1",
            "chave_acesso": "25240112345678000190550010001234561000000010",
            "natureza_operacao": "VENDA DE MERCADORIA",
            "protocolo_autorizacao": "325240000000000",
            "data_emissao": "2024-01-15",
            "data_saida_entrada": "2024-01-15",
            "hora_saida": "10:30:00",
            "produtos": [
                {
                    "codigo": "001",
                    "descricao": product,
                    "ncm": "27101921" if "diesel" in product.lower() else "39174090",
                    "cst": "060",
                    "cfop": "5102",
                    "unidade": "L" if "diesel" in product.lower() else "UN",
                    "quantidade": 100,
                    "valor_unitario": 15.0,
                    "valor_total": 1500.0,
                }
            ],
            "parcelas": [
                {
                    "numero": 1,
                    "descricao": "Duplicata 001",
                    "data_vencimento": due_date,
                    "valor": 1500.0,
                }
            ],
            "valor_total": 1500.0,
            "valor_produtos": 1500.0,
            "valor_frete": 0.0,
            "valor_desconto": 0.0,
            "valor_seguro": 0.0,
            "outras_despesas": 0.0,
            "base_calculo_icms": 1500.0,
            "valor_icms": 270.0,
            "base_calculo_icms_st": 0.0,
            "valor_icms_st": 0.0,
            "valor_ipi": 0.0,
            "valor_pis": 0.0,
            "valor_cofins": 0.0,
            "local_entrega": {
                "nome_razao_social": "CLIENTE EXEMPLO",
                "cpf_cnpj": "123.456.789-00",
                "inscricao_estadual": "",
                "endereco": "Avenida Cliente",
                "numero": "200",
                "bairro": "Bairro Exemplo",
                "municipio": "Campina Grande",
                "uf": "PB",
                "cep": "58400-000",
                "telefone": "",
            },
            "transportador": {
                "razao_social": "TRANSPORTE EXEMPLO LTDA",
                "cpf_cnpj": "98.765.432/0001-10",
                "inscricao_estadual": "987.654.321.000",
                "endereco": "Rua do Transporte",
                "municipio": "Joao Pessoa",
                "uf": "PB",
                "placa_veiculo": "ABC1D23",
                "frete_por_conta": "Emitente",
                "quantidade": "1",
                "especie": "Volume",
                "peso_bruto": "100,000 KG",
                "peso_liquido": "98,000 KG",
            },
            "informacoes_complementares": "Documento gerado em modo demonstrativo quando Gemini nao esta disponivel.",
            "classificacoes_despesa": [],
        }

    def _extract_due_date(self, pdf_text: str) -> str:
        patterns = (
            r"\b(?:data\s+de\s+)?(?:vencimento|vencto\.?|vcto\.?)\b\D{0,40}(\d{2}[/-]\d{2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",
        )
        for pattern in patterns:
            match = re.search(pattern, pdf_text, re.IGNORECASE | re.DOTALL)
            if match:
                return self._normalize_date(match.group(1))
        return ""

    def _normalize_date(self, raw_date: str) -> str:
        value = str(raw_date).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value

        match = re.fullmatch(r"(\d{2})[/-](\d{2})[/-](\d{2,4})", value)
        if not match:
            return ""

        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        return f"{year}-{month}-{day}"
