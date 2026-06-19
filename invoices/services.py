from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q

from .agents import ExpenseClassificationAgent, PdfExtractionAgent, PersistenceAgent, ValidationAgent
from .models import Classificacao, InvoiceExtraction, MovimentoContas, ParcelaContas, Pessoa
from .utils import display_document_name, due_status, only_alnum, only_digits


@dataclass
class PessoaLookupResult:
    pessoa: Pessoa | None
    existed: bool
    reactivated: bool


@dataclass
class ClassificacaoLookupResult:
    classificacao: Classificacao | None
    existed: bool
    reactivated: bool


class InvoiceExtractionService:
    _MAYBE = "MAYBE"
    _PAGAR_CONTEXT_TOKENS = (
        "compra",
        "comp",
        "fornecedor",
        "insumo",
        "despesa",
        "manutencao",
        "administrativo",
    )
    _RECEBER_CONTEXT_TOKENS = (
        "venda",
        "receita",
        "cliente",
        "faturamento",
        "prestacao",
        "servico",
    )
    _PAGAR_CLASSIFICATION_TOKENS = (
        "despesa",
        "custo",
        "fornecedor",
        "insumo",
        "manutencao",
        "salario",
        "frete",
    )
    _RECEBER_CLASSIFICATION_TOKENS = (
        "receita",
        "faturamento",
        "prestacao",
        "servico",
        "honorario",
    )

    def __init__(self, gemini_api_key: str | None = None) -> None:
        self.pdf_agent = PdfExtractionAgent(api_key=gemini_api_key)
        self.classification_agent = ExpenseClassificationAgent()
        self.validation_agent = ValidationAgent()
        self.persistence_agent = PersistenceAgent()

    def extract(self, uploaded_file) -> dict:
        extraction = self.pdf_agent.extract(uploaded_file)
        data = self.validation_agent.normalize(extraction.data)
        self._apply_due_date_fallback(data)

        if not self._safe_str(data.get("data_emissao")) and self._safe_str(data.get("data_saida_entrada")):
            data["data_emissao"] = self._safe_str(data.get("data_saida_entrada"))

        if not self._should_preserve_gemini_classification(data):
            data["classificacoes_despesa"] = self.classification_agent.classify(data.get("produtos", []))

        self._apply_due_date_fallback(data)

        record = self.persistence_agent.save_success(
            uploaded_file,
            data,
            extraction.provider,
            movement_type="",
            movimento=None,
        )

        payload = {
            "success": True,
            "id": record.id,
            "provider": extraction.provider,
            "data": data,
            "metadata": {
                "file_name": record.file_name,
                "file_size": record.file_size,
                "created_at": record.created_at.isoformat(),
            },
        }
        usage = self._usage_for_extraction(extraction, data)
        if usage:
            payload["metadata"]["usage"] = usage
        if extraction.fallback_reason:
            payload["fallback_reason"] = extraction.fallback_reason
        return payload

    def _usage_for_extraction(self, extraction, data: dict) -> dict:
        if extraction.usage:
            return extraction.usage
        if extraction.provider == "gemini":
            return self._estimated_usage_from_payload(data)
        return {}

    def _estimated_usage_from_payload(self, data: dict) -> dict:
        output = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        output_tokens = max(1, int(len(output) / 4))
        return {
            "input_tokens": 0,
            "output_tokens": output_tokens,
            "total_tokens": output_tokens,
            "estimated": True,
            "note": "Estimativa parcial: o SDK nao retornou metadata de tokens para esta chamada.",
        }

    def analyze(self, extraction_id: int) -> dict:
        extraction = self._get_extraction(extraction_id)
        data = extraction.result_json

        supplier = self._lookup_person(data["fornecedor"], "fornecedor")
        billed = self._lookup_person(data["faturado"], "faturado")

        movement_blocks = self._infer_movement_blocks(data)

        analyzed_blocks = []
        for block in movement_blocks:
            block_type = block["movement_type"]
            block_classifications = [
                self._classification_analysis_from_raw(item, block_type)
                for item in block["raw_classifications"]
            ]
            analyzed_blocks.append({"movement_type": block_type, "classificacoes": block_classifications})

        movement_type = analyzed_blocks[0]["movement_type"] if len(analyzed_blocks) == 1 else "MISTO"
        unique_classifications = self._unique_classification_payloads(
            item
            for block in analyzed_blocks
            for item in block["classificacoes"]
        )

        return {
            "success": True,
            "extraction_id": extraction.id,
            "movement_type": movement_type,
            "metadata": {
                "provider": extraction.provider,
                "created_at": extraction.created_at.isoformat(),
            },
            "analysis": {
                "fornecedor": self._person_analysis_payload(supplier),
                "faturado": self._person_analysis_payload(billed),
                "classificacoes": unique_classifications,
                "blocks": analyzed_blocks,
            },
        }

    @transaction.atomic
    def launch(self, extraction_id: int) -> dict:
        extraction = self._get_extraction(extraction_id)
        data = extraction.result_json

        analysis = self.analyze(extraction_id)
        analysis_blocks = analysis["analysis"]["blocks"]

        supplier = self._get_or_create_person(data["fornecedor"], role="fornecedor")
        billed = self._get_or_create_person(data["faturado"], role="faturado")
        supplier_payload = self._person_launch_payload(supplier.pessoa)
        billed_payload = self._person_launch_payload(billed.pessoa)

        launches = []
        launch_classifications = []
        launch_classification_ids: set[int] = set()
        launch_parcels = []
        for block in analysis_blocks:
            movement_type = block["movement_type"]
            raw_classifications = self._extract_classification_payload(data.get("classificacoes_despesa", []))
            if not raw_classifications:
                raw_classifications = self._default_classifications(movement_type)

            existing_names = [item.get("descricao", "") for item in block.get("classificacoes", [])]
            selected = [
                item
                for item in raw_classifications
                if item["categoria"] in existing_names
            ]
            if not selected:
                selected = self._default_classifications(movement_type)

            classifications = self._get_or_create_classifications(
                selected,
                self._classification_type_from_movement_type(movement_type),
            )
            movement = self._create_movement(
                movement_type=movement_type,
                data=data,
                pessoa=supplier.pessoa,
                faturado=billed.pessoa,
                classifications=classifications,
            )
            parcels = self._create_installments(movement, data["parcelas"])
            readable_classifications = [self._classification_launch_payload(item.classificacao) for item in classifications if item.classificacao is not None]
            readable_parcels = [self._parcel_launch_payload(item) for item in parcels]
            for parcel_item in readable_parcels:
                launch_parcels.append(parcel_item)
            for item in readable_classifications:
                if item["id"] not in launch_classification_ids:
                    launch_classification_ids.add(item["id"])
                    launch_classifications.append(item)
            launches.append(
                {
                    "movement_type": movement_type,
                    "movement_id": movement.id,
                    "pessoa_id": supplier.pessoa.id,
                    "faturado_id": billed.pessoa.id,
                    "pessoa": supplier_payload,
                    "faturado": billed_payload,
                    "classificacoes": readable_classifications,
                    "classificacao_ids": [item.classificacao.id for item in classifications if item.classificacao is not None],
                    "parcelas": readable_parcels,
                    "parcelas_ids": [item.id for item in parcels],
                }
            )

        if launches:
            extraction.movimento_id = launches[0]["movement_id"]
            extraction.save(update_fields=["movimento", "updated_at"])

        return {
            "success": True,
            "extraction_id": extraction.id,
            "movement_type": analysis["movement_type"],
            "launch": {
                "message": "Lancamentos concluidos com sucesso.",
                "fornecedor": supplier_payload,
                "faturado": billed_payload,
                "classificacoes": launch_classifications,
                "parcelas": launch_parcels,
                "movements": launches,
            },
        }

    def _get_extraction(self, extraction_id: int) -> InvoiceExtraction:
        try:
            return InvoiceExtraction.objects.get(id=extraction_id)
        except InvoiceExtraction.DoesNotExist as exc:
            raise ValueError("Registro de extracao nao encontrado.") from exc

    def _classification_analysis_from_raw(self, item: dict, movement_type: str) -> dict:
        class_lookup = self._lookup_classification(
            item["categoria"],
            self._classification_type_from_movement_type(movement_type),
        )
        if class_lookup.classificacao is None:
            return {
                "descricao": item["categoria"],
                "exists": False,
                "id": None,
                "reactivated": False,
            }
        return self._classification_analysis_payload(class_lookup)

    def _unique_classification_payloads(self, classifications) -> list[dict]:
        seen = set()
        unique = []
        for item in classifications:
            key = (item["descricao"], item["id"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _extract_classification_payload(self, classifications: list[dict]) -> list[dict]:
        return [self._normalize_classification(item) for item in classifications if isinstance(item, dict)]

    def _classification_type_from_movement_type(self, movement_type: str) -> str:
        if movement_type == MovimentoContas.Tipo.APAGAR:
            return Classificacao.Tipo.DESPESA
        return Classificacao.Tipo.RECEITA

    def _default_classifications(self, movement_type: str) -> list[dict]:
        if movement_type == MovimentoContas.Tipo.APAGAR:
            return [
                {
                    "categoria": "ADMINISTRATIVAS",
                    "justificativa": "Classificacao padrao para lancamento de contas a pagar.",
                }
            ]
        return [
            {
                "categoria": "RECEITA OPERACIONAL",
                "justificativa": "Classificacao padrao para lancamento de contas a receber.",
            }
        ]

    def _apply_due_date_fallback(self, data: dict) -> None:
        issue_date = self._safe_str(data.get("data_emissao"))
        if not issue_date:
            return
        for item in data.get("parcelas", []):
            if isinstance(item, dict) and not self._safe_str(item.get("data_vencimento")):
                item["data_vencimento"] = issue_date

    def _should_preserve_gemini_classification(self, raw_data: object) -> bool:
        if not isinstance(raw_data, dict):
            return False

        classification = raw_data.get("classificacoes_despesa")
        if not isinstance(classification, list) or not classification:
            return False

        for item in classification:
            if not isinstance(item, dict):
                return False
            categoria = str(item.get("categoria", "")).strip()
            justificativa = str(item.get("justificativa", "")).strip()
            if not categoria or not justificativa:
                return False
            if not self.classification_agent.is_official_category(categoria):
                return False
        return True

    def _infer_movement_blocks(self, data: dict) -> list[dict[str, Any]]:
        classification_blocks: dict[str, list[dict]] = {
            MovimentoContas.Tipo.APAGAR: [],
            MovimentoContas.Tipo.ARECEBER: [],
        }

        raw_classifications = self._extract_classification_payload(data.get("classificacoes_despesa", []))
        overall = self._infer_overall_movement_type(data)

        if not raw_classifications:
            raw_classifications = self._default_classifications(overall if overall in classification_blocks else MovimentoContas.Tipo.APAGAR)

        for item in raw_classifications:
            inferred = self._infer_movement_for_classification(data, item)
            if inferred == self._MAYBE:
                inferred = MovimentoContas.Tipo.APAGAR if overall != "MISTO" else self._pick_split_side(classification_blocks)
            classification_blocks[inferred].append(item)

        if overall == "MISTO" and not (classification_blocks[MovimentoContas.Tipo.APAGAR] and classification_blocks[MovimentoContas.Tipo.ARECEBER]):
            if not classification_blocks[MovimentoContas.Tipo.APAGAR]:
                classification_blocks[MovimentoContas.Tipo.APAGAR] = self._default_classifications(MovimentoContas.Tipo.APAGAR)
            if not classification_blocks[MovimentoContas.Tipo.ARECEBER]:
                classification_blocks[MovimentoContas.Tipo.ARECEBER] = self._default_classifications(MovimentoContas.Tipo.ARECEBER)

        active_blocks: list[tuple[str, list[dict]]] = [
            (movement_type, raw)
            for movement_type, raw in classification_blocks.items()
            if raw
        ]

        if len(active_blocks) == 1:
            movement_type, raw_classifications = active_blocks[0]
            return [{"movement_type": movement_type, "raw_classifications": raw_classifications}]

        return [
            {"movement_type": MovimentoContas.Tipo.APAGAR, "raw_classifications": classification_blocks[MovimentoContas.Tipo.APAGAR]},
            {"movement_type": MovimentoContas.Tipo.ARECEBER, "raw_classifications": classification_blocks[MovimentoContas.Tipo.ARECEBER]},
        ]

    def _pick_split_side(self, classification_blocks: dict[str, list[dict]]) -> str:
        pagar = len(classification_blocks[MovimentoContas.Tipo.APAGAR])
        receber = len(classification_blocks[MovimentoContas.Tipo.ARECEBER])
        return MovimentoContas.Tipo.APAGAR if pagar <= receber else MovimentoContas.Tipo.ARECEBER

    def _infer_overall_movement_type(self, data: dict) -> str:
        context = " ".join(
            [
                self._safe_str(data.get("natureza_operacao")),
                self._safe_str(data.get("fornecedor", {}).get("razao_social")),
                self._safe_str(data.get("faturado", {}).get("nome_completo") or data.get("faturado", {}).get("razao_social")),
                " ".join(self._safe_str(item.get("descricao", "")).lower() for item in data.get("produtos", [])),
            ]
        ).lower()

        pagar_score = self._token_score(context, self._PAGAR_CONTEXT_TOKENS)
        receber_score = self._token_score(context, self._RECEBER_CONTEXT_TOKENS)

        if pagar_score and receber_score:
            return "MISTO"
        if receber_score > pagar_score:
            return MovimentoContas.Tipo.ARECEBER
        return MovimentoContas.Tipo.APAGAR

    def _infer_movement_for_classification(self, data: dict, item: dict) -> str:
        text = " ".join(
            [
                self._safe_str(data.get("natureza_operacao")),
                self._safe_str(item.get("categoria")),
                self._safe_str(item.get("justificativa")),
            ]
        ).lower()

        pagar_score = self._token_score(text, self._PAGAR_CLASSIFICATION_TOKENS)
        receber_score = self._token_score(text, self._RECEBER_CLASSIFICATION_TOKENS)

        if pagar_score > receber_score:
            return MovimentoContas.Tipo.APAGAR
        if receber_score > pagar_score:
            return MovimentoContas.Tipo.ARECEBER
        return self._MAYBE

    def _token_score(self, text: str, tokens: tuple[str, ...]) -> int:
        return sum(1 for token in tokens if token in text)

    def _lookup_person(self, raw_data: dict, role: str) -> PessoaLookupResult:
        pessoa = self._find_person(raw_data)
        if pessoa is None:
            return PessoaLookupResult(pessoa=None, existed=False, reactivated=False)

        return PessoaLookupResult(pessoa=pessoa, existed=True, reactivated=not pessoa.ativo)

    def _get_or_create_person(self, raw_data: dict, role: str) -> PessoaLookupResult:
        pessoa = self._find_person(raw_data)
        existed = pessoa is not None

        if pessoa is None:
            pessoa = Pessoa.objects.create(**self._person_defaults(raw_data, role))
            return PessoaLookupResult(pessoa=pessoa, existed=False, reactivated=False)

        reactivated = self._update_person_from_raw(pessoa, raw_data, role)
        return PessoaLookupResult(pessoa=pessoa, existed=existed, reactivated=reactivated)

    def _find_person(self, raw_data: dict) -> Pessoa | None:
        lookup_candidates = self._person_lookup_candidates(raw_data)
        pessoa = None
        if lookup_candidates["document_field"] and lookup_candidates["document_value"]:
            filters = Q(**{lookup_candidates["document_field"]: lookup_candidates["document_value"]})
            if raw_document := lookup_candidates.get("document_value_raw"):
                filters |= Q(**{lookup_candidates["document_field"]: raw_document})
            pessoa = Pessoa.objects.filter(filters).first()
        if pessoa is None and lookup_candidates["name"]:
            pessoa = Pessoa.objects.filter(razao_social__iexact=lookup_candidates["name"]).first()
        return pessoa

    def _update_person_from_raw(self, pessoa: Pessoa, raw_data: dict, role: str) -> bool:
        updates = self._person_defaults(raw_data, role)
        activate = bool(updates.pop("ativo", False))
        for field, value in updates.items():
            current = getattr(pessoa, field)
            if isinstance(current, bool):
                setattr(pessoa, field, bool(value))
            elif value and not current:
                setattr(pessoa, field, value)

        reactivated = False
        if activate and not pessoa.ativo:
            pessoa.ativo = True
            pessoa.save(update_fields=["ativo", "updated_at"])
            reactivated = True
        else:
            pessoa.save()

        return reactivated

    def _lookup_classification(self, description: str, classification_type: str) -> ClassificacaoLookupResult:
        normalized = self._safe_str(description)
        if not normalized:
            return ClassificacaoLookupResult(classificacao=None, existed=False, reactivated=False)

        classificacao = Classificacao.objects.filter(tipo=classification_type, descricao__iexact=normalized).first()
        if classificacao is None:
            return ClassificacaoLookupResult(classificacao=None, existed=False, reactivated=False)
        return ClassificacaoLookupResult(classificacao=classificacao, existed=True, reactivated=not classificacao.ativo)

    def _get_or_create_classifications(
        self,
        raw_classifications: list[dict],
        classification_type: str,
    ) -> list[ClassificacaoLookupResult]:
        results: list[ClassificacaoLookupResult] = []
        for item in raw_classifications:
            category = self._safe_str(item.get("categoria"))
            if not category:
                continue
            justificativa = self._safe_str(item.get("justificativa"))
            if not justificativa:
                justificativa = "Classificacao informada pelo documento importado."

            classificacao = Classificacao.objects.filter(tipo=classification_type, descricao__iexact=category).first()
            existed = classificacao is not None
            reactivated = False

            if classificacao is None:
                classificacao = Classificacao.objects.create(tipo=classification_type, descricao=category, ativo=True)
            elif not classificacao.ativo:
                classificacao.ativo = True
                classificacao.save(update_fields=["ativo", "updated_at"])
                reactivated = True

            if not justificativa and justificativa != item.get("justificativa"):
                item["justificativa"] = justificativa

            results.append(
                ClassificacaoLookupResult(
                    classificacao=classificacao,
                    existed=existed,
                    reactivated=reactivated,
                )
            )

        if not results:
            for item in self._default_classifications(
                MovimentoContas.Tipo.APAGAR if classification_type == Classificacao.Tipo.DESPESA else MovimentoContas.Tipo.ARECEBER
            ):
                classificacao = Classificacao.objects.create(tipo=classification_type, descricao=item["categoria"], ativo=True)
                results.append(
                    ClassificacaoLookupResult(
                        classificacao=classificacao,
                        existed=False,
                        reactivated=False,
                    )
                )

        return results

    def _person_lookup_candidates(self, raw_data: dict) -> dict[str, str]:
        cnpj = self._only_alnum(raw_data.get("cnpj"))
        cpf = self._only_digits(raw_data.get("cpf"))
        if cnpj:
            return {
                "document_field": "cnpj",
                "document_value": cnpj,
                "document_value_raw": self._safe_str(raw_data.get("cnpj")),
                "name": self._person_name(raw_data),
            }
        if cpf:
            return {
                "document_field": "cpf",
                "document_value": cpf,
                "document_value_raw": self._safe_str(raw_data.get("cpf")),
                "name": self._person_name(raw_data),
            }
        return {
            "document_field": "",
            "document_value": "",
            "document_value_raw": "",
            "name": self._person_name(raw_data),
        }

    def _person_defaults(self, raw_data: dict, role: str) -> dict:
        name = self._person_name(raw_data)
        return {
            "razao_social": name,
            "nome_fantasia": self._safe_str(raw_data.get("fantasia")),
            "cpf": self._only_digits(raw_data.get("cpf")),
            "cnpj": self._only_alnum(raw_data.get("cnpj")),
            "inscricao_estadual": only_digits(raw_data.get("inscricao_estadual")),
            "endereco": self._safe_str(raw_data.get("endereco")),
            "numero": only_digits(raw_data.get("numero")),
            "bairro": self._safe_str(raw_data.get("bairro")),
            "municipio": self._safe_str(raw_data.get("municipio")),
            "uf": self._safe_str(raw_data.get("uf"))[:2],
            "cep": only_digits(raw_data.get("cep")),
            "telefone": self._safe_str(raw_data.get("telefone")),
            "is_cliente": role == "cliente",
            "is_fornecedor": role == "fornecedor",
            "is_faturado": role == "faturado",
            "ativo": True,
        }

    def _person_name(self, raw_data: dict) -> str:
        return self._safe_str(raw_data.get("razao_social") or raw_data.get("nome_completo") or raw_data.get("fantasia"))

    def _person_analysis_payload(self, result: PessoaLookupResult) -> dict:
        if result.pessoa is None:
            return {
                "nome": None,
                "documento": None,
                "exists": False,
                "id": None,
                "reactivated": False,
            }

        return {
            "nome": result.pessoa.razao_social,
            "documento": result.pessoa.cnpj or result.pessoa.cpf,
            "exists": result.existed,
            "id": result.pessoa.id,
            "reactivated": result.reactivated,
        }

    def _person_launch_payload(self, person: Pessoa) -> dict:
        return {
            "id": person.id,
            "nome": person.razao_social,
            "documento": person.cnpj or person.cpf,
        }

    def _classification_analysis_payload(self, result: ClassificacaoLookupResult) -> dict:
        if result.classificacao is None:
            return {
                "descricao": None,
                "exists": False,
                "id": None,
                "reactivated": False,
            }
        return {
            "descricao": result.classificacao.descricao,
            "exists": result.existed,
            "id": result.classificacao.id,
            "reactivated": result.reactivated,
        }

    def _classification_launch_payload(self, classificacao: Classificacao) -> dict:
        return {
            "id": classificacao.id,
            "descricao": classificacao.descricao,
        }

    def _create_movement(
        self,
        movement_type: str,
        data: dict,
        pessoa: Pessoa,
        faturado: Pessoa,
        classifications: list[ClassificacaoLookupResult],
    ) -> MovimentoContas:
        numero_documento = self._resolve_document_number(
            movement_type=movement_type,
            raw_number=data.get("numero_nota_fiscal"),
        )

        issue_date = self._parse_date(data.get("data_emissao"), field_name="data_emissao")
        movement = MovimentoContas.objects.create(
            tipo=movement_type,
            pessoa=pessoa,
            faturado=faturado,
            numero_documento=numero_documento,
            nome_documento=display_document_name(
                supplier_name=pessoa.razao_social,
                number=numero_documento,
                issue_date=issue_date,
            ),
            data_emissao=issue_date,
            valor_total=self._decimal(data.get("valor_total")),
            observacoes=self._safe_str(data.get("informacoes_complementares")),
            dados_extraidos=data,
            ativo=True,
        )
        movement.classificacoes.set([item.classificacao for item in classifications if item.classificacao is not None])
        return movement

    def _parcel_launch_payload(self, parcela: ParcelaContas) -> dict:
        return {
            "id": parcela.id,
            "identificacao": parcela.identificacao,
            "numero": parcela.numero,
            "vencimento": parcela.data_vencimento.isoformat(),
            "valor": str(parcela.valor),
        }

    def _resolve_document_number(self, movement_type: str, raw_number: object) -> str:
        base_number = self._safe_str(raw_number)
        if not base_number:
            candidate = self._fallback_document_number(movement_type)
            while self._document_number_exists(movement_type, candidate):
                candidate = self._fallback_document_number(movement_type)
            return candidate

        if not self._document_number_exists(movement_type, base_number):
            return base_number

        suffix = 2
        while True:
            candidate = self._append_document_suffix(base_number, suffix)
            if not self._document_number_exists(movement_type, candidate):
                return candidate
            suffix += 1

    def _append_document_suffix(self, base_number: str, suffix: int) -> str:
        suffix_text = f"-{suffix}"
        max_length = MovimentoContas._meta.get_field("numero_documento").max_length
        trimmed_base = base_number[: max_length - len(suffix_text)]
        return f"{trimmed_base}{suffix_text}"

    def _document_number_exists(self, movement_type: str, number: str) -> bool:
        return MovimentoContas.objects.filter(tipo=movement_type, numero_documento=number).exists()

    def _fallback_document_number(self, movement_type: str) -> str:
        return f"SEM-NUMERO-{movement_type}-{uuid.uuid4().hex[:12]}"

    def _create_installments(self, movement: MovimentoContas, installments: list[dict]) -> list[ParcelaContas]:
        created: list[ParcelaContas] = []
        for item in installments:
            number = int(item.get("numero") or 1)
            parcela = ParcelaContas.objects.create(
                movimento=movement,
                identificacao=f"MOV-{movement.id}-P{number}",
                numero=number,
                data_vencimento=self._parse_date(item.get("data_vencimento"), field_name="data_vencimento"),
                valor=self._decimal(item.get("valor")),
                ativo=True,
            )
            parcela.status_vencimento = due_status(parcela.data_vencimento)
            parcela.save(update_fields=["status_vencimento", "updated_at"])
            created.append(parcela)
        return created

    def _normalize_classification(self, item: dict) -> dict:
        return {
            "categoria": self._safe_str(item.get("categoria")),
            "justificativa": self._safe_str(item.get("justificativa")),
        }

    def _parse_date(self, value, field_name: str) -> date:
        raw = self._safe_str(value)
        if not raw:
            raise ValueError(f"Campo obrigatorio ausente: {field_name}.")
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"Campo {field_name} deve estar no formato YYYY-MM-DD.") from exc

    def _decimal(self, value) -> Decimal:
        if isinstance(value, Decimal):
            return value
        raw = self._safe_str(value) or "0"
        return Decimal(str(raw))

    def _only_digits(self, value) -> str:
        value = re.sub(r"\D+", "", self._safe_str(value))
        return value if value else None

    def _only_alnum(self, value) -> str:
        value = re.sub(r"[^0-9A-Za-z]+", "", self._safe_str(value)).upper()
        return value if value else None

    def _safe_str(self, value) -> str:
        if value is None:
            return ""
        return str(value).strip()
