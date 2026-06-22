from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.conf import settings
from django.db.models import Count, Sum

from .agents import gemini_usage
from .gemini_session import GeminiAccessError, is_gemini_auth_error
from .models import Classificacao, InvoiceExtraction, MovimentoContas, ParcelaContas, Pessoa


@dataclass(frozen=True)
class RetrievedDocument:
    source: str
    title: str
    content: str
    score: float
    rows: tuple[dict[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QueryIntent:
    kind: str
    target_input_tokens: int = 1500


class Agent3:
    """Agente RAG para consultas em linguagem natural sobre o banco financeiro."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = str(api_key or getattr(settings, "GEMINI_API_KEY", "") or "").strip()

    max_documents = 500
    max_context_documents = 20
    max_llm_evidence_documents = 3
    max_summary_rows = 5
    embedding_dimensions = 64

    def run_query(self, query: str, mode: str = "simple") -> dict:
        user_query = self._clean_text(query)
        if not user_query:
            raise ValueError("Digite uma pergunta para consultar o banco de dados.")

        retrieval_mode = "embeddings" if mode == "embeddings" else "simple"
        intent = self._classify_query_intent(user_query)
        documents = self._retrieve_data(user_query, retrieval_mode)
        context_documents = self._llm_context_documents(user_query, documents, intent)
        context = self._format_context(context_documents)
        direct_answer = self._direct_answer(user_query, documents)
        if direct_answer:
            answer, provider, usage = direct_answer, "local", {}
        else:
            answer, provider, usage = self._generate_response(user_query, context, documents, retrieval_mode)
        context_usage = self._context_usage_payload(context, context_documents, intent, usage)

        sources = [self._source_payload(item, retrieval_mode) for item in documents]
        summary_sources = [item for item in sources if self._is_analytic_source(str(item["source"]))]
        evidence_sources = [item for item in sources if not self._is_analytic_source(str(item["source"]))]
        return {
            "query": user_query,
            "mode": retrieval_mode,
            "intent": intent.kind,
            "answer": answer,
            "provider": provider,
            "sources": sources,
            "answer_documents": summary_sources,
            "summary_sources": summary_sources,
            "evidence_sources": evidence_sources,
            "usage": usage,
            "context_usage": context_usage,
        }

    def _retrieve_data(self, query: str, mode: str) -> list[RetrievedDocument]:
        analytics = self._analytics_documents(query)
        documents = analytics + self._database_documents()
        if not documents:
            return []
        if mode == "embeddings":
            retrieved = self._retrieve_with_embeddings(query, documents)
        else:
            retrieved = self._retrieve_simple(query, documents)
        return self._merge_priority_documents(analytics, retrieved)

    def _database_documents(self) -> list[RetrievedDocument]:
        documents: list[RetrievedDocument] = []

        for movement in (
            MovimentoContas.objects.select_related("pessoa", "faturado")
            .prefetch_related("classificacoes", "parcelas")
            .filter(ativo=True)
            .order_by("-created_at")[: self.max_documents]
        ):
            content = " | ".join(
                [
                    f"Documento: {movement.nome_documento or movement.numero_documento}",
                    f"Numero fiscal: {movement.numero_documento}",
                    f"Tipo: {movement.tipo}",
                    f"Tipo legivel: {self._movement_label(movement.tipo)}",
                    f"Pessoa: {movement.pessoa.razao_social}",
                    f"Faturado: {movement.faturado.razao_social}",
                    f"Data de emissao: {movement.data_emissao.isoformat()}",
                    f"Valor total: {self._money(movement.valor_total)}",
                    f"Classificacoes: {', '.join(item.descricao for item in movement.classificacoes.all())}",
                    f"Parcelas: {self._installments_text(movement.parcelas.all())}",
                    f"Observacoes: {movement.observacoes}",
                ]
            )
            documents.append(
                RetrievedDocument(
                    source=f"MovimentoContas:{movement.id}",
                    title=movement.nome_documento or f"Movimento {movement.id} - {movement.numero_documento}",
                    content=content,
                    score=0.0,
                    rows=self._rows(
                        ("Documento", movement.nome_documento or movement.numero_documento),
                        ("Número fiscal", movement.numero_documento),
                        ("Tipo", movement.tipo),
                        ("Tipo legível", self._movement_label(movement.tipo)),
                        ("Pessoa", movement.pessoa.razao_social),
                        ("Faturado", movement.faturado.razao_social),
                        ("Data de emissão", movement.data_emissao.isoformat()),
                        ("Valor total", f"R$ {self._money(movement.valor_total)}"),
                        ("Classificações", ", ".join(item.descricao for item in movement.classificacoes.all())),
                        ("Parcelas", self._installments_text(movement.parcelas.all())),
                        ("Observações", movement.observacoes),
                    ),
                )
            )

        for pessoa in Pessoa.ativos.order_by("-created_at")[: self.max_documents]:
            content = " | ".join(
                [
                    f"Pessoa: {pessoa.razao_social}",
                    f"Fantasia: {pessoa.nome_fantasia}",
                    f"Documento: {pessoa.cnpj or pessoa.cpf or ''}",
                    f"Municipio/UF: {pessoa.municipio}/{pessoa.uf}",
                    f"Fornecedor: {'sim' if pessoa.is_fornecedor else 'nao'}",
                    f"Cliente: {'sim' if pessoa.is_cliente else 'nao'}",
                    f"Faturado: {'sim' if pessoa.is_faturado else 'nao'}",
                ]
            )
            documents.append(
                RetrievedDocument(
                    source=f"Pessoa:{pessoa.id}",
                    title=f"Pessoa {pessoa.id} - {pessoa.razao_social}",
                    content=content,
                    score=0.0,
                    rows=self._rows(
                        ("Pessoa", pessoa.razao_social),
                        ("Fantasia", pessoa.nome_fantasia),
                        ("Documento", pessoa.cnpj or pessoa.cpf or ""),
                        ("Município/UF", f"{pessoa.municipio}/{pessoa.uf}"),
                        ("Fornecedor", "Sim" if pessoa.is_fornecedor else "Não"),
                        ("Cliente", "Sim" if pessoa.is_cliente else "Não"),
                        ("Faturado", "Sim" if pessoa.is_faturado else "Não"),
                    ),
                )
            )

        for classification in Classificacao.ativos.order_by("tipo", "descricao")[: self.max_documents]:
            documents.append(
                RetrievedDocument(
                    source=f"Classificacao:{classification.id}",
                    title=f"Classificação {classification.id}",
                    content=f"Classificacao: {classification.descricao} | Tipo: {classification.tipo}",
                    score=0.0,
                    rows=self._rows(
                        ("Classificação", classification.descricao),
                        ("Tipo", classification.tipo),
                    ),
                )
            )

        return documents

    def _analytics_documents(self, query: str) -> list[RetrievedDocument]:
        normalized = self._normalize(self._expand_query(query))
        movement_type = self._movement_type_from_query(normalized)
        documents = [self._financial_summary_document()]
        year = self._year_from_query(normalized)

        if movement_type:
            documents.append(self._movement_summary_document(movement_type))
            documents.append(self._movement_rows_document(movement_type))
        elif self._is_financial_question(normalized):
            documents.extend(
                [
                    self._movement_summary_document(MovimentoContas.Tipo.APAGAR),
                    self._movement_summary_document(MovimentoContas.Tipo.ARECEBER),
                    self._movement_rows_document(None),
                ]
            )

        if self._has_any(normalized, ["parcela", "parcelas", "vencimento", "vencimentos", "vencer", "vencidas"]):
            documents.append(self._installments_summary_document(movement_type))

        if year and self._has_any(normalized, ["nota", "notas", "nf", "nfe", "emitida", "emitidas", "emissao", "emissao"]):
            documents.append(self._invoice_year_summary_document(year, movement_type))

        if year and self._has_any(normalized, ["parcela", "parcelas", "vencimento", "vencerao", "vencera", "vencer"]):
            documents.append(self._installments_year_billed_document(year, query))
        elif self._has_any(normalized, ["parcela", "parcelas"]) and self._has_any(normalized, ["faturado", "cpf", "cnpj"]):
            documents.append(self._installments_billed_document(query))

        if self._has_any(normalized, ["nota", "notas", "nf", "nfe"]) and self._has_any(normalized, ["fornecedor", "deste fornecedor"]):
            documents.append(self._supplier_invoices_document(query))

        if self._has_any(normalized, ["fornecedor", "fornecedores", "cliente", "clientes", "pessoa", "pessoas", "faturado"]):
            documents.append(self._people_summary_document())

        if self._has_any(normalized, ["classificacao", "classificacoes", "categoria", "categorias", "despesa", "receita"]):
            documents.append(self._classification_summary_document(movement_type))

        if self._has_any(normalized, ["manutencao", "operacao", "solo", "corretivo", "corretivos", "neutralizador", "neutralizadores"]):
            documents.append(
                self._product_similarity_document(
                    "MANUTENCAO E OPERACAO",
                    [
                        "corretivo",
                        "corretivos",
                        "neutralizador",
                        "neutralizadores",
                        "peca",
                        "pecas",
                        "reparo",
                        "reparos",
                        "correia",
                        "correias",
                        "rolamento",
                        "rolamentos",
                        "filtro",
                        "filtros",
                        "lubrificante",
                        "lubrificantes",
                    ],
                )
            )

        if self._has_any(normalized, ["insumos agricolas", "pragas", "doencas", "fungicida", "herbicida", "inseticida", "defensivo"]):
            documents.append(self._largest_classified_invoice_document("INSUMOS AGRICOLAS"))

        if self._has_any(normalized, ["nota", "notas", "nf", "nfe", "danfe", "extracao", "extracoes", "importacao"]):
            documents.append(self._extraction_summary_document())

        return documents

    def _financial_summary_document(self) -> RetrievedDocument:
        payable = self._movement_total(MovimentoContas.Tipo.APAGAR)
        receivable = self._movement_total(MovimentoContas.Tipo.ARECEBER)
        payable_count = MovimentoContas.ativos.filter(tipo=MovimentoContas.Tipo.APAGAR).count()
        receivable_count = MovimentoContas.ativos.filter(tipo=MovimentoContas.Tipo.ARECEBER).count()
        content = (
            "Resumo financeiro autoritativo do banco | "
            f"Contas a pagar: {payable_count} movimento(s), total R$ {self._money(payable)} | "
            f"Contas a receber: {receivable_count} movimento(s), total R$ {self._money(receivable)} | "
            f"Saldo receber menos pagar: R$ {self._money(receivable - payable)} | "
            "Use estes totais para perguntas de soma, total, quantidade, saldo, pagar e receber."
        )
        return RetrievedDocument(
            "Analitico:ResumoFinanceiro",
            "Resumo financeiro do banco",
            content,
            999.0,
            rows=self._rows(
                ("Contas a pagar", f"{payable_count} movimento(s)"),
                ("Total a pagar", f"R$ {self._money(payable)}"),
                ("Contas a receber", f"{receivable_count} movimento(s)"),
                ("Total a receber", f"R$ {self._money(receivable)}"),
                ("Saldo receber menos pagar", f"R$ {self._money(receivable - payable)}"),
            ),
        )

    def _movement_summary_document(self, movement_type: str) -> RetrievedDocument:
        queryset = MovimentoContas.ativos.filter(tipo=movement_type)
        total = self._sum_decimal(queryset.aggregate(total=Sum("valor_total"))["total"])
        count = queryset.count()
        average = total / count if count else Decimal("0")
        label = self._movement_label(movement_type)
        biggest = queryset.order_by("-valor_total", "-created_at").first()
        smallest = queryset.order_by("valor_total", "-created_at").first()
        parts = [
            f"Resumo autoritativo de {label}",
            f"Tipo: {movement_type}",
            f"Quantidade: {count}",
            f"Total: R$ {self._money(total)}",
            f"Media: R$ {self._money(average)}",
        ]
        if biggest:
            parts.append(f"Maior movimento: {biggest.numero_documento} R$ {self._money(biggest.valor_total)}")
        if smallest:
            parts.append(f"Menor movimento: {smallest.numero_documento} R$ {self._money(smallest.valor_total)}")
        structured_rows = [
            ("Tipo", movement_type),
            ("Tipo legível", label),
            ("Quantidade", str(count)),
            ("Total", f"R$ {self._money(total)}"),
            ("Média", f"R$ {self._money(average)}"),
        ]
        if biggest:
            structured_rows.append(("Maior movimento", f"{biggest.numero_documento} - R$ {self._money(biggest.valor_total)}"))
        if smallest:
            structured_rows.append(("Menor movimento", f"{smallest.numero_documento} - R$ {self._money(smallest.valor_total)}"))
        return RetrievedDocument(
            f"Analitico:{movement_type}:Resumo",
            f"Resumo {label}",
            " | ".join(parts),
            998.0,
            rows=self._rows(*structured_rows),
        )

    def _movement_rows_document(self, movement_type: str | None) -> RetrievedDocument:
        queryset = (
            MovimentoContas.ativos.select_related("pessoa", "faturado")
            .prefetch_related("classificacoes")
            .order_by("-data_emissao", "-created_at")
        )
        if movement_type:
            queryset = queryset.filter(tipo=movement_type)
        total_count = queryset.count()
        rows = []
        structured_rows = []
        for item in queryset[: self.max_summary_rows]:
            rows.append(
                f"{item.numero_documento}: {self._movement_label(item.tipo)}, pessoa {item.pessoa.razao_social}, "
                f"faturado {item.faturado.razao_social}, data {item.data_emissao.isoformat()}, "
                f"valor R$ {self._money(item.valor_total)}, classificacoes "
                f"{', '.join(classification.descricao for classification in item.classificacoes.all()) or 'sem classificacao'}"
            )
            structured_rows.extend(
                [
                    (f"{item.numero_documento} - Tipo", self._movement_label(item.tipo)),
                    (f"{item.numero_documento} - Pessoa", item.pessoa.razao_social),
                    (f"{item.numero_documento} - Faturado", item.faturado.razao_social),
                    (f"{item.numero_documento} - Data", item.data_emissao.isoformat()),
                    (f"{item.numero_documento} - Valor", f"R$ {self._money(item.valor_total)}"),
                    (
                        f"{item.numero_documento} - Classificações",
                        ", ".join(classification.descricao for classification in item.classificacoes.all()) or "Sem classificação",
                    ),
                ]
            )
        label = self._movement_label(movement_type) if movement_type else "movimentos financeiros"
        content = "; ".join(rows) if rows else f"Nenhum movimento ativo encontrado para {label}."
        if rows:
            content = f"Total de movimentos encontrados: {total_count}. Exibindo {len(rows)} mais recentes. {content}"
            structured_rows.insert(0, ("Total encontrado", str(total_count)))
            structured_rows.insert(1, ("Exibindo", f"{len(rows)} movimento(s) mais recente(s)"))
        return RetrievedDocument(
            f"Analitico:{movement_type or 'Todos'}:Movimentos",
            f"Movimentos - {label}",
            content,
            997.0,
            rows=self._rows(*structured_rows) if structured_rows else self._rows(("Resultado", content)),
        )

    def _installments_summary_document(self, movement_type: str | None) -> RetrievedDocument:
        queryset = ParcelaContas.ativos.select_related("movimento").order_by("data_vencimento", "numero")
        if movement_type:
            queryset = queryset.filter(movimento__tipo=movement_type)
        total = self._sum_decimal(queryset.aggregate(total=Sum("valor"))["total"])
        count = queryset.count()
        rows = [
            f"{item.identificacao or item.numero}: {self._movement_label(item.movimento.tipo)}, vencimento "
            f"{item.data_vencimento.isoformat()}, status {item.status_vencimento}, valor R$ {self._money(item.valor)}"
            for item in queryset[:20]
        ]
        content = (
            f"Resumo de parcelas | Quantidade: {count} | Total: R$ {self._money(total)} | "
            f"Parcelas: {'; '.join(rows) if rows else 'nenhuma parcela ativa encontrada'}"
        )
        structured_rows = [("Quantidade", str(count)), ("Total", f"R$ {self._money(total)}")]
        for item in queryset[:20]:
            structured_rows.extend(
                [
                    (f"{item.identificacao or item.numero} - Tipo", self._movement_label(item.movimento.tipo)),
                    (f"{item.identificacao or item.numero} - Vencimento", item.data_vencimento.isoformat()),
                    (f"{item.identificacao or item.numero} - Status", item.status_vencimento),
                    (f"{item.identificacao or item.numero} - Valor", f"R$ {self._money(item.valor)}"),
                ]
            )
        return RetrievedDocument(
            "Analitico:Parcelas",
            "Resumo de parcelas",
            content,
            996.0,
            rows=self._rows(*structured_rows),
        )

    def _invoice_year_summary_document(self, year: int, movement_type: str | None) -> RetrievedDocument:
        queryset = MovimentoContas.ativos.select_related("pessoa").filter(data_emissao__year=year)
        if movement_type:
            queryset = queryset.filter(tipo=movement_type)
        movements = self._unique_invoice_movements(queryset)
        total = sum((movement.valor_total for movement in movements), Decimal("0"))
        count = len(movements)
        supplier_totals: dict[str, Decimal] = {}
        for movement in movements:
            supplier_totals[movement.pessoa.razao_social] = supplier_totals.get(movement.pessoa.razao_social, Decimal("0")) + movement.valor_total
        top_supplier = max(supplier_totals.items(), key=lambda item: item[1], default=("", Decimal("0")))
        label = self._movement_label(movement_type) if movement_type else "todos os movimentos"
        content = (
            f"Resumo autoritativo de notas fiscais emitidas em {year} para {label} | "
            f"Quantidade: {count} | Total: R$ {self._money(total)} | "
            f"Fornecedor de maior valor acumulado: {top_supplier[0] or 'nenhum'} R$ {self._money(top_supplier[1])}."
        )
        return RetrievedDocument(
            f"Analitico:Notas:{year}:{movement_type or 'Todos'}",
            f"Notas fiscais em {year}",
            content,
            996.5,
            rows=self._rows(
                ("Ano", str(year)),
                ("Tipo", movement_type or "Todos"),
                ("Quantidade de notas", str(count)),
                ("Valor total", f"R$ {self._money(total)}"),
                ("Fornecedor maior valor", top_supplier[0] or "Nenhum"),
                ("Valor do fornecedor", f"R$ {self._money(top_supplier[1])}"),
            ),
        )

    def _installments_year_billed_document(self, year: int, person_query: str) -> RetrievedDocument:
        queryset = ParcelaContas.ativos.select_related("movimento", "movimento__faturado").filter(data_vencimento__year=year)
        document = self._document_from_query(person_query)
        matched_name = ""
        if document:
            digits = self._only_digits(document)
            queryset = queryset.filter(movimento__faturado__cpf=digits) | queryset.filter(movimento__faturado__cnpj=digits)
        else:
            matched_name = self._person_name_from_query(person_query, billed_only=True)
            if matched_name:
                queryset = queryset.filter(movimento__faturado__razao_social__iexact=matched_name)
        installments = self._unique_installments(queryset.order_by("data_vencimento", "numero", "id"))
        total = sum((item.valor for item in installments), Decimal("0"))
        count = len(installments)
        billed = installments[0].movimento.faturado.razao_social if installments else "Nenhum faturado encontrado"
        consulted = document or matched_name or "Não informado"
        rows = [("Ano", str(year)), ("Faturado/documento consultado", consulted), ("Faturado encontrado", billed), ("Quantidade de parcelas", str(count)), ("Total das parcelas", f"R$ {self._money(total)}")]
        for item in installments[: self.max_summary_rows]:
            rows.append((item.identificacao or f"Parcela {item.numero}", f"{item.data_vencimento.isoformat()} - R$ {self._money(item.valor)}"))
        content = (
            f"Resumo autoritativo de parcelas com vencimento em {year} | "
            f"Faturado/documento: {consulted} | Quantidade: {count} | Total: R$ {self._money(total)}. "
            "O status informado e de vencimento, calculado pela data da parcela."
        )
        return RetrievedDocument(
            f"Analitico:Parcelas:{year}:{self._only_digits(document) or self._normalize(matched_name).replace(' ', '-') or 'Todos'}",
            f"Parcelas do faturado em {year}",
            content,
            996.4,
            rows=self._rows(*rows),
        )

    def _installments_billed_document(self, person_query: str) -> RetrievedDocument:
        queryset = ParcelaContas.ativos.select_related("movimento", "movimento__faturado").order_by("data_vencimento", "numero", "id")
        document = self._document_from_query(person_query)
        matched_name = ""
        if document:
            digits = self._only_digits(document)
            queryset = queryset.filter(movimento__faturado__cpf=digits) | queryset.filter(movimento__faturado__cnpj=digits)
        else:
            matched_name = self._person_name_from_query(person_query, billed_only=True)
            if matched_name:
                queryset = queryset.filter(movimento__faturado__razao_social__iexact=matched_name)
        installments = self._unique_installments(queryset)
        total = sum((item.valor for item in installments), Decimal("0"))
        billed = installments[0].movimento.faturado.razao_social if installments else "Nenhum faturado encontrado"
        consulted = document or matched_name or "Não informado"
        rows = [
            ("Faturado/documento consultado", consulted),
            ("Faturado encontrado", billed),
            ("Quantidade de parcelas", str(len(installments))),
            ("Total das parcelas", f"R$ {self._money(total)}"),
        ]
        for item in installments[:20]:
            rows.append((
                item.identificacao or f"Parcela {item.numero}",
                f"Documento {item.movimento.nome_documento or item.movimento.numero_documento} | vencimento {item.data_vencimento.isoformat()} | status {item.status_vencimento} | R$ {self._money(item.valor)}",
            ))
        content = (
            f"Parcelas autoritativas do faturado {billed} | Quantidade: {len(installments)} | "
            f"Total: R$ {self._money(total)} | "
            f"Itens: {'; '.join(f'{item.identificacao or item.numero} {item.data_vencimento.isoformat()} {item.status_vencimento} R$ {self._money(item.valor)}' for item in installments[:20]) or 'nenhum'}"
        )
        return RetrievedDocument(
            f"Analitico:Parcelas:Faturado:{self._only_digits(document) or self._normalize(matched_name).replace(' ', '-') or 'Todos'}",
            "Parcelas do faturado",
            content,
            996.35,
            rows=self._rows(*rows),
        )

    def _supplier_invoices_document(self, query: str) -> RetrievedDocument:
        supplier_name = self._person_name_from_query(query, supplier_only=True)
        if not supplier_name:
            supplier_name = self._supplier_name_from_previous_answer(query)
        queryset = (
            MovimentoContas.ativos.select_related("pessoa", "faturado")
            .prefetch_related("classificacoes")
            .filter(tipo=MovimentoContas.Tipo.APAGAR)
            .order_by("-valor_total", "-data_emissao", "-created_at")
        )
        if supplier_name:
            queryset = queryset.filter(pessoa__razao_social__iexact=supplier_name)
        movements = self._unique_invoice_movements(queryset)
        unique_movements: list[tuple[str, MovimentoContas]] = []
        seen_numbers: set[str] = set()
        for movement in movements:
            display_number = self._movement_invoice_number(movement)
            normalized_number = self._base_document_number(display_number)
            if normalized_number in seen_numbers:
                continue
            seen_numbers.add(normalized_number)
            unique_movements.append((display_number, movement))
        rows = [("Fornecedor consultado", supplier_name or "Não informado"), ("Quantidade de notas", str(len(unique_movements)))]
        for display_number, movement in unique_movements:
            rows.extend(
                [
                    (f"{display_number} - Documento", movement.nome_documento or display_number),
                    (f"{display_number} - Data", movement.data_emissao.isoformat()),
                    (f"{display_number} - Faturado", movement.faturado.razao_social),
                    (f"{display_number} - Valor", f"R$ {self._money(movement.valor_total)}"),
                    (f"{display_number} - Itens", ", ".join(self._products_from_movement(movement)) or "Sem itens"),
                    (f"{display_number} - Classificações", ", ".join(item.descricao for item in movement.classificacoes.all()) or "Sem classificação"),
                ]
            )
        content = (
            f"Notas fiscais deduplicadas do fornecedor {supplier_name or 'não informado'} | Quantidade: {len(unique_movements)} | "
            f"Notas: {'; '.join(f'{display_number} {movement.data_emissao.isoformat()} R$ {self._money(movement.valor_total)}' for display_number, movement in unique_movements) or 'nenhuma'}"
        )
        return RetrievedDocument(
            f"Analitico:FornecedorNotas:{self._normalize(supplier_name).replace(' ', '-') or 'NaoInformado'}",
            "Notas fiscais do fornecedor",
            content,
            996.45,
            rows=self._rows(*rows),
        )

    def _people_summary_document(self) -> RetrievedDocument:
        suppliers = Pessoa.ativos.filter(is_fornecedor=True).count()
        clients = Pessoa.ativos.filter(is_cliente=True).count()
        billed = Pessoa.ativos.filter(is_faturado=True).count()
        people = [
            f"{item.razao_social} ({item.cnpj or item.cpf or 'sem documento'})"
            for item in Pessoa.ativos.order_by("razao_social")[:20]
        ]
        content = (
            f"Resumo de pessoas | Fornecedores: {suppliers} | Clientes: {clients} | Faturados: {billed} | "
            f"Pessoas: {'; '.join(people) if people else 'nenhuma pessoa ativa encontrada'}"
        )
        structured_rows = [
            ("Fornecedores", str(suppliers)),
            ("Clientes", str(clients)),
            ("Faturados", str(billed)),
        ]
        for index, person in enumerate(people, start=1):
            structured_rows.append((f"Pessoa {index}", person))
        return RetrievedDocument(
            "Analitico:Pessoas",
            "Resumo de pessoas",
            content,
            995.0,
            rows=self._rows(*structured_rows),
        )

    def _classification_summary_document(self, movement_type: str | None) -> RetrievedDocument:
        queryset = Classificacao.ativos.all()
        if movement_type == MovimentoContas.Tipo.APAGAR:
            queryset = queryset.filter(tipo=Classificacao.Tipo.DESPESA)
        elif movement_type == MovimentoContas.Tipo.ARECEBER:
            queryset = queryset.filter(tipo=Classificacao.Tipo.RECEITA)
        grouped = queryset.values("tipo").annotate(total=Count("id")).order_by("tipo")
        rows = [f"{item['tipo']}: {item['total']}" for item in grouped]
        names = [f"{item.tipo} - {item.descricao}" for item in queryset.order_by("tipo", "descricao")[:30]]
        content = (
            f"Resumo de classificacoes | Totais por tipo: {', '.join(rows) if rows else 'nenhuma'} | "
            f"Classificacoes: {'; '.join(names) if names else 'nenhuma classificacao ativa encontrada'}"
        )
        structured_rows = [(f"Total {item['tipo']}", str(item["total"])) for item in grouped]
        for index, name in enumerate(names, start=1):
            structured_rows.append((f"Classificação {index}", name))
        return RetrievedDocument(
            "Analitico:Classificacoes",
            "Resumo de classificações",
            content,
            994.0,
            rows=self._rows(*structured_rows) if structured_rows else self._rows(("Resultado", "Nenhuma classificação ativa encontrada")),
        )

    def _product_similarity_document(self, category: str, semantic_terms: list[str]) -> RetrievedDocument:
        matches = []
        normalized_terms = [self._normalize(term) for term in semantic_terms]
        seen_documents: set[str] = set()
        for movement in MovimentoContas.ativos.prefetch_related("classificacoes").order_by("-valor_total"):
            classifications = [self._normalize(item.descricao) for item in movement.classificacoes.all()]
            if self._normalize(category) not in classifications:
                continue
            products = self._products_from_movement(movement)
            product_text = self._normalize(" ".join(products))
            matched_terms = [term for term in normalized_terms if term in product_text]
            if matched_terms:
                doc_key = self._compact_document_reference(self._movement_invoice_number(movement))
                if doc_key in seen_documents:
                    continue
                seen_documents.add(doc_key)
                matches.append((movement, products, matched_terms))

        rows = [("Classificação pesquisada", category), ("Termos semânticos", ", ".join(semantic_terms)), ("Notas compatíveis", str(len(matches)))]
        for movement, products, matched_terms in matches[: self.max_summary_rows]:
            rows.append((movement.numero_documento, f"R$ {self._money(movement.valor_total)} | termos: {', '.join(matched_terms)} | itens: {', '.join(products)}"))
        content = (
            f"Busca semântica autoritativa por notas classificadas como {category} com itens parecidos com {', '.join(semantic_terms)} | "
            f"Encontradas: {len(matches)}."
        )
        return RetrievedDocument(
            f"Analitico:Semantico:{category}",
            f"Notas {category} semelhantes a termos de solo",
            content,
            996.3,
            rows=self._rows(*rows),
        )

    def _direct_answer(self, query: str, documents: list[RetrievedDocument]) -> str:
        normalized = self._normalize(query)
        referenced_movements = self._movements_from_query_references(query)
        if referenced_movements and self._has_any(normalized, ["descreva", "descrever", "detalhe", "detalhes", "citar", "cite", "listar", "liste"]):
            lines = []
            for reference, movement in referenced_movements:
                if movement is None:
                    lines.append(f"{reference}: não encontrei uma nota ativa com esse número.")
                    continue
                products = self._products_from_movement(movement)
                classifications = ", ".join(item.descricao for item in movement.classificacoes.all()) or "Sem classificação"
                target = self._pest_or_disease_target(products)
                human_risk = "Não identificado pelo nome do produto."
                if target != "Não identificado pelo nome do produto":
                    human_risk = "O nome do produto não permite afirmar risco direto aos seres humanos; isso depende da ficha de segurança."
                lines.append(
                    (
                        f"{reference}: "
                        f"{movement.nome_documento or movement.numero_documento}; "
                        f"emissão {movement.data_emissao.isoformat()}; "
                        f"faturado {movement.faturado.razao_social}; "
                        f"valor R$ {self._money(movement.valor_total)}; "
                        f"classificações {classifications}; "
                        f"itens {', '.join(products) or 'Sem itens'}; "
                        f"pragas/doenças inferidas {target}; "
                        f"risco humano {human_risk}"
                    )
                )
            return "Detalhamento das notas solicitadas: " + "; ".join(lines) + "."

        supplier_notes = self._first_document(documents, "Analitico:FornecedorNotas:")
        if supplier_notes and self._has_any(normalized, ["cite", "citar", "liste", "listar", "descreva", "detalhe", "detalhes"]):
            values = self._row_values(supplier_notes)
            supplier = values.get("Fornecedor consultado", "Não informado")
            if supplier == "Não informado":
                return "Informe o nome do fornecedor para listar as notas fiscais dele."
            note_rows = [
                row for row in self._data_rows(supplier_notes)
                if " - Documento" in row["campo"]
            ]
            if not note_rows:
                return f"Não encontrei notas fiscais ativas para o fornecedor {supplier}."
            if self._has_any(normalized, ["descreva", "detalhe", "detalhes"]):
                details = []
                for row in note_rows:
                    number = row["campo"].replace(" - Documento", "")
                    data = values.get(f"{number} - Data", "-")
                    billed = values.get(f"{number} - Faturado", "-")
                    amount = values.get(f"{number} - Valor", "-")
                    products = values.get(f"{number} - Itens", "Sem itens")
                    classifications = values.get(f"{number} - Classificações", "Sem classificação")
                    details.append(
                        f"{number}: {row['valor']}; emissão {data}; faturado {billed}; valor {amount}; itens {products}; classificações {classifications}"
                    )
                return f"Notas fiscais do fornecedor {supplier}: " + "; ".join(details) + "."
            numbers = [row["campo"].replace(" - Documento", "") for row in note_rows]
            return f"As notas fiscais do fornecedor {supplier} são: {', '.join(numbers)}."

        billed_installments = self._first_document(documents, "Analitico:Parcelas:Faturado:")
        if billed_installments and self._has_any(normalized, ["cite", "citar", "liste", "listar"]):
            values = self._row_values(billed_installments)
            billed = values.get("Faturado encontrado", "consultado")
            rows = [
                row for row in self._data_rows(billed_installments)
                if row["campo"] not in {"Faturado/documento consultado", "Faturado encontrado", "Quantidade de parcelas", "Total das parcelas"}
            ]
            if not rows:
                return f"Não encontrei parcelas ativas para o faturado {billed}."
            return f"Parcelas do faturado {billed}: " + "; ".join(f"{row['campo']}: {row['valor']}" for row in rows) + "."

        if self._has_any(normalized, ["parcela", "parcelas", "vencimento", "vencerao", "vencer"]):
            document = self._first_document(documents, "Analitico:Parcelas:20")
            if document:
                values = self._row_values(document)
                return (
                    f"Para o faturado {values.get('Faturado encontrado', 'consultado')}, "
                    f"a soma das parcelas cadastradas com vencimento em {values.get('Ano', 'ano consultado')} é "
                    f"{values.get('Total das parcelas', 'R$ 0.00')}, em {values.get('Quantidade de parcelas', '0')} parcela(s). "
                    "O status usado é de vencimento, calculado pela data da parcela; a NFe não informa pagamento real."
                )

        if self._has_any(normalized, ["nota", "notas", "nf", "nfe"]) and self._year_from_query(normalized):
            document = self._first_document(documents, "Analitico:Notas:")
            if document:
                values = self._row_values(document)
                return (
                    f"Em {values.get('Ano', 'ano consultado')}, o valor total das notas fiscais "
                    f"({values.get('Tipo', 'Todos')}) foi {values.get('Valor total', 'R$ 0.00')}. "
                    f"O fornecedor com maior valor no período foi {values.get('Fornecedor maior valor', 'Nenhum')}, "
                    f"com {values.get('Valor do fornecedor', 'R$ 0.00')}. "
                    "Notas duplicadas por reimportação são consideradas uma única vez neste agregado."
                )

        if self._has_any(normalized, ["manutencao", "operacao", "solo", "corretivo", "neutralizador"]):
            document = self._first_document(documents, "Analitico:Semantico:MANUTENCAO E OPERACAO")
            if document:
                values = self._row_values(document)
                count = int(values.get("Notas compatíveis", "0") or 0)
                if count == 0:
                    return (
                        "Não encontrei notas classificadas como MANUTENCAO E OPERACAO com itens contendo "
                        "corretivo/corretivos, neutralizador/neutralizadores ou termos equivalentes de solo."
                    )
                items = [
                    f"{row['campo']}: {row['valor']}"
                    for row in self._data_rows(document)
                    if row["campo"] not in {"Classificação pesquisada", "Termos semânticos", "Notas compatíveis"}
                ]
                return "Sim. Encontrei " + str(count) + " nota(s): " + "; ".join(items)

        if self._has_any(normalized, ["maior", "maior valor", "maior nota", "qual e a despesa", "qual é a despesa"]) and self._has_any(
            normalized, ["insumos agricolas", "pragas", "doencas", "fungicida", "herbicida", "inseticida", "defensivo"]
        ):
            document = self._first_document(documents, "Analitico:MaiorNota:INSUMOS AGRICOLAS")
            if document:
                values = self._row_values(document)
                target = values.get("Pragas/doenças inferidas", "Não identificado pelo nome do produto")
                answer = (
                    f"A maior nota classificada como INSUMOS AGRICOLAS é {values.get('Nota', '-')}, "
                    f"com valor total de {values.get('Valor total', 'R$ 0.00')}. "
                    f"Itens: {values.get('Itens', 'Sem itens')}. "
                    f"Pragas/doenças inferidas pelo nome do produto: {target}."
                )
                if target == "Não identificado pelo nome do produto":
                    answer += " O agente não deve inventar alvo fitossanitário quando o produto não indica isso no nome."
                    target_note = values.get("Maior nota com alvo fitossanitário")
                    target_value = values.get("Valor da nota com alvo")
                    target_items = values.get("Itens da nota com alvo")
                    target_inference = values.get("Alvo fitossanitário inferido")
                    if target_note and target_note != "Nenhuma":
                        answer += (
                            f" Entre as notas de INSUMOS AGRICOLAS com alvo identificável no nome, a maior é {target_note}, "
                            f"com {target_value}; itens: {target_items}; alvo inferido: {target_inference}."
                        )
                return answer

        return ""

    def _movements_from_query_references(self, query: str) -> list[tuple[str, MovimentoContas | None]]:
        references = self._query_references(query)
        if not references:
            return []
        queryset = MovimentoContas.ativos.select_related("pessoa", "faturado").prefetch_related("classificacoes")
        matches: list[tuple[str, MovimentoContas | None]] = []
        seen_ids: set[int] = set()
        for display_reference, compact_reference in references:
            matched_movement = None
            for movement in self._unique_invoice_movements(queryset):
                if movement.id in seen_ids:
                    continue
                movement_number = self._compact_document_reference(self._movement_invoice_number(movement))
                if movement_number == compact_reference:
                    matched_movement = movement
                    seen_ids.add(movement.id)
                    break
            matches.append((display_reference, matched_movement))
        return matches

    def _query_references(self, query: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for match in re.findall(r"\b[0-9A-Z][0-9A-Z./-]{4,}\b", query.upper()):
            compact = self._compact_document_reference(match)
            if not compact or compact in seen:
                continue
            if compact.isdigit() and len(compact) in {11, 14}:
                continue
            if not any(char.isdigit() for char in compact):
                continue
            seen.add(compact)
            candidates.append((match.rstrip(".,;:)"), compact))
        return candidates

    def _compact_document_reference(self, value: object) -> str:
        return self._normalize(value).replace(" ", "").upper()

    def _first_document(self, documents: list[RetrievedDocument], source_prefix: str) -> RetrievedDocument | None:
        return next((item for item in documents if item.source.startswith(source_prefix)), None)

    def _row_values(self, document: RetrievedDocument) -> dict[str, str]:
        return {row["campo"]: row["valor"] for row in self._data_rows(document)}

    def _data_rows(self, document: RetrievedDocument) -> list[dict[str, str]]:
        return [row for row in document.rows if isinstance(row, dict)]

    def _largest_classified_invoice_document(self, category: str) -> RetrievedDocument:
        matching_movements = []
        for movement in self._unique_invoice_movements(MovimentoContas.ativos.prefetch_related("classificacoes").order_by("-valor_total")):
            classifications = [self._normalize(item.descricao) for item in movement.classificacoes.all()]
            if self._normalize(category) in classifications:
                matching_movements.append(movement)
        movements_with_products = [movement for movement in matching_movements if self._products_from_movement(movement)]
        best = (movements_with_products or matching_movements)[0] if matching_movements else None
        if best is None:
            return RetrievedDocument(
                f"Analitico:MaiorNota:{category}",
                f"Maior nota de {category}",
                f"Nenhuma nota ativa encontrada para a classificação {category}.",
                996.2,
                rows=self._rows(("Classificação", category), ("Resultado", "Nenhuma nota encontrada")),
            )

        products = self._products_from_movement(best)
        target = self._pest_or_disease_target(products)
        best_targeted = None
        best_targeted_products: list[str] = []
        best_targeted_target = ""
        for movement in matching_movements:
            candidate_products = self._products_from_movement(movement)
            candidate_target = self._pest_or_disease_target(candidate_products)
            if candidate_target != "Não identificado pelo nome do produto":
                best_targeted = movement
                best_targeted_products = candidate_products
                best_targeted_target = candidate_target
                break
        content = (
            f"Maior nota fiscal classificada como {category} | Nota: {self._movement_invoice_number(best)} | "
            f"Valor total: R$ {self._money(best.valor_total)} | Itens: {', '.join(products) or 'sem itens'} | "
            f"Indício de pragas/doenças pelo nome do produto: {target}."
        )
        rows = [
            ("Classificação", category),
            ("Nota", self._movement_invoice_number(best)),
            ("Valor total", f"R$ {self._money(best.valor_total)}"),
            ("Itens", ", ".join(products) or "Sem itens"),
            ("Pragas/doenças inferidas", target),
        ]
        if best_targeted:
            rows.extend(
                [
                    ("Maior nota com alvo fitossanitário", self._movement_invoice_number(best_targeted)),
                    ("Valor da nota com alvo", f"R$ {self._money(best_targeted.valor_total)}"),
                    ("Itens da nota com alvo", ", ".join(best_targeted_products) or "Sem itens"),
                    ("Alvo fitossanitário inferido", best_targeted_target),
                ]
            )
        else:
            rows.append(("Maior nota com alvo fitossanitário", "Nenhuma"))
        return RetrievedDocument(
            f"Analitico:MaiorNota:{category}",
            f"Maior nota de {category}",
            content,
            996.2,
            rows=self._rows(*rows),
        )

    def _extraction_summary_document(self) -> RetrievedDocument:
        grouped = InvoiceExtraction.objects.values("status", "provider").annotate(total=Count("id")).order_by("status", "provider")
        rows = [f"{item['status']}/{item['provider']}: {item['total']}" for item in grouped]
        latest = [
            f"{item.id} {item.file_name} status {item.status} origem {item.provider}"
            for item in InvoiceExtraction.objects.order_by("-created_at")[:20]
        ]
        content = (
            f"Resumo de importacoes de notas fiscais | Totais: {', '.join(rows) if rows else 'nenhuma'} | "
            f"Ultimas extrações: {'; '.join(latest) if latest else 'nenhuma extração encontrada'}"
        )
        structured_rows = [(f"{item['status']}/{item['provider']}", str(item["total"])) for item in grouped]
        for index, item in enumerate(latest, start=1):
            structured_rows.append((f"Extração {index}", item))
        return RetrievedDocument(
            "Analitico:Extracoes",
            "Resumo de notas importadas",
            content,
            993.0,
            rows=self._rows(*structured_rows) if structured_rows else self._rows(("Resultado", "Nenhuma extração encontrada")),
        )

    def _retrieve_simple(self, query: str, documents: list[RetrievedDocument]) -> list[RetrievedDocument]:
        query_terms = set(self._tokens(self._expand_query(query)))
        ranked = []
        for document in documents:
            doc_terms = self._tokens(self._expand_query(document.content))
            overlap = query_terms.intersection(doc_terms)
            score = (len(overlap) * 2.0) + sum(1 for term in query_terms if term in self._normalize(self._expand_query(document.content)))
            if self._is_analytic_source(document.source):
                score += 50.0
            if score > 0:
                ranked.append(self._replace_score(document, score))

        if not ranked:
            ranked = [self._replace_score(item, 0.1) for item in documents[: self.max_context_documents]]

        return sorted(ranked, key=lambda item: item.score, reverse=True)[: self.max_context_documents]

    def _retrieve_with_embeddings(self, query: str, documents: list[RetrievedDocument]) -> list[RetrievedDocument]:
        query_vector = self._embedding(self._expand_query(query))
        ranked = []
        for document in documents:
            score = self._cosine_similarity(query_vector, self._embedding(self._expand_query(document.content)))
            if self._is_analytic_source(document.source):
                score += 0.75
            if score > 0:
                ranked.append(self._replace_score(document, score))

        if not ranked:
            ranked = [self._replace_score(item, 0.01) for item in documents[: self.max_context_documents]]

        return sorted(ranked, key=lambda item: item.score, reverse=True)[: self.max_context_documents]

    def _generate_response(
        self,
        user_query: str,
        retrieved_data: str,
        documents: list[RetrievedDocument],
        mode: str,
    ) -> tuple[str, str, dict]:
        gemini_api_key = self.api_key
        if gemini_api_key:
            try:
                from google import genai

                client = genai.Client(api_key=gemini_api_key)
                response = client.models.generate_content(
                    model=getattr(settings, "GEMINI_RAG_MODEL", "gemini-2.5-flash-lite"),
                    config={
                        "max_output_tokens": getattr(settings, "GEMINI_RAG_MAX_OUTPUT_TOKENS", 900),
                    },
                    contents=self._prompt(user_query, retrieved_data, mode),
                )
                text = self._clean_text(response.text)
                if text:
                    return text, "gemini", self._usage_payload(gemini_usage.response_usage_metadata(response))
            except Exception as exc:
                if is_gemini_auth_error(exc):
                    return self._fallback_answer(user_query, documents, mode), "local", {}

        return self._fallback_answer(user_query, documents, mode), "local", {}

    def _usage_payload(self, usage_metadata: object) -> dict:
        payload = gemini_usage.usage_payload(usage_metadata)
        payload.pop("estimated", None)
        return payload

    def _metadata_value(self, value: object, *names: str) -> int:
        return gemini_usage.metadata_value(value, *names)

    def _prompt(self, user_query: str, retrieved_data: str, mode: str) -> str:
        return (
            "Voce e um assistente financeiro. Responda em portugues, com clareza, usando somente os dados abaixo. "
            "Fontes Analitico: sao calculadas pelo banco e tem prioridade. Nao invente valores. "
            "Para total, soma, quantidade, media, maior, menor e saldo, use os valores Analitico: e nao recalcule.\n\n"
            f"Modo RAG: {mode}\n"
            f"Dados:\n{retrieved_data or 'Nenhum dado recuperado.'}\n\n"
            f"Pergunta: {user_query}"
        )

    def _fallback_answer(self, user_query: str, documents: list[RetrievedDocument], mode: str) -> str:
        analytic_documents = [item for item in documents if self._is_analytic_source(item.source)]
        if not documents:
            return (
                "Não encontrei dados no banco para responder à pergunta. "
                "Cadastre ou importe notas fiscais antes de consultar o agente RAG."
            )

        lines = [
            f"Usei RAG {'com embeddings' if mode == 'embeddings' else 'simples'} para buscar os registros mais relacionados à pergunta.",
            f"Pergunta: {user_query}",
            f"Foram recuperadas {len(documents)} fonte(s) do banco.",
        ]
        if analytic_documents:
            lines.append("A resposta foi baseada nos agregados autoritativos exibidos nas tabelas abaixo.")
        else:
            lines.append("A resposta foi baseada nas evidências recuperadas e exibidas nas tabelas abaixo.")

        lines.append("Sem GEMINI_API_KEY ativa, esta resposta foi montada localmente a partir das fontes recuperadas.")
        return "\n".join(lines)

    def _format_context(self, documents: list[RetrievedDocument]) -> str:
        return "\n".join(
            f"[{index}] {document.title}\nFonte: {document.source}\nScore: {document.score:.4f}\n{document.content}"
            for index, document in enumerate(documents, start=1)
        )

    def _llm_context_documents(
        self,
        query: str,
        documents: list[RetrievedDocument],
        intent: QueryIntent,
    ) -> list[RetrievedDocument]:
        analytic_documents = [item for item in documents if self._is_analytic_source(item.source)]
        evidence_documents = [item for item in documents if not self._is_analytic_source(item.source)]
        normalized_query = self._normalize(self._expand_query(query))

        if not analytic_documents:
            return documents[: self.max_context_documents]

        if intent.kind in {"financeira_agregada", "financeira_filtrada"}:
            selected = self._prioritized_analytics_for_query(analytic_documents, normalized_query)
            evidence_limit = 1 if intent.kind == "financeira_agregada" else 2
            return selected + evidence_documents[:evidence_limit]

        if intent.kind == "semantica":
            selected = [
                item
                for item in analytic_documents
                if item.source.startswith("Analitico:Semantico:")
                or item.source.startswith("Analitico:MaiorNota:")
                or item.source == "Analitico:Classificacoes"
            ]
            if not selected:
                selected = analytic_documents[:3]
            return selected[:4] + evidence_documents[: self.max_llm_evidence_documents]

        return analytic_documents[:4] + evidence_documents[:6]

    def _prioritized_analytics_for_query(
        self,
        analytic_documents: list[RetrievedDocument],
        normalized_query: str,
    ) -> list[RetrievedDocument]:
        selected: list[RetrievedDocument] = []
        special_prefixes = (
            "Analitico:Notas:",
            "Analitico:Parcelas:20",
            "Analitico:Semantico:",
            "Analitico:MaiorNota:",
        )
        for document in analytic_documents:
            if document.source.startswith(special_prefixes):
                selected.append(document)

        if "receber" in normalized_query:
            selected.extend(item for item in analytic_documents if item.source == "Analitico:ARECEBER:Resumo")
        if any(term in normalized_query for term in ["pagar", "pago", "pagos", "pagas", "despesa"]):
            selected.extend(item for item in analytic_documents if item.source == "Analitico:APAGAR:Resumo")
        if any(term in normalized_query for term in ["saldo", "financeiro", "total", "soma", "quanto"]):
            selected.extend(item for item in analytic_documents if item.source == "Analitico:ResumoFinanceiro")
        if "parcela" in normalized_query and not any(item.source.startswith("Analitico:Parcelas:20") for item in selected):
            selected.extend(item for item in analytic_documents if item.source == "Analitico:Parcelas")

        if not selected:
            selected = analytic_documents[:3]

        deduplicated: list[RetrievedDocument] = []
        seen = set()
        for document in selected:
            if document.source in seen:
                continue
            seen.add(document.source)
            deduplicated.append(document)
        return deduplicated[:5]

    def _context_usage_payload(
        self,
        context: str,
        context_documents: list[RetrievedDocument],
        intent: QueryIntent,
        usage: dict,
    ) -> dict:
        estimated_input_tokens = self._estimate_tokens(context)
        actual_input_tokens = int(usage.get("input_tokens") or 0) if isinstance(usage, dict) else 0
        measured_input_tokens = actual_input_tokens or estimated_input_tokens
        return {
            "intent": intent.kind,
            "target_input_tokens": intent.target_input_tokens,
            "estimated_input_tokens": estimated_input_tokens,
            "document_count": len(context_documents),
            "status": self._token_budget_status(measured_input_tokens, intent.target_input_tokens),
        }

    def _token_budget_status(self, input_tokens: int, target: int) -> str:
        if input_tokens <= target:
            return "OTIMO"
        if input_tokens <= 2500:
            return "ACEITAVEL"
        if input_tokens <= 3000:
            return "ALTO"
        return "MUITO_ALTO"

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / 4))

    def _embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.embedding_dimensions
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.embedding_dimensions
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    def _tokens(self, text: str) -> list[str]:
        normalized = self._normalize(text)
        return [token for token in normalized.split() if len(token) >= 3]

    def _normalize(self, text: str) -> str:
        value = self._clean_text(text).lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return " ".join(value.split())

    def _replace_score(self, document: RetrievedDocument, score: float) -> RetrievedDocument:
        return RetrievedDocument(
            source=document.source,
            title=document.title,
            content=document.content,
            score=score,
            rows=document.rows,
        )

    def _source_payload(self, document: RetrievedDocument, mode: str) -> dict:
        return {
            "source": document.source,
            "title": document.title,
            "score": round(document.score, 4),
            "score_label": self._score_label(document, mode),
            "score_status": self._score_status(document, mode),
            "content": document.content,
            "rows": list(document.rows),
        }

    def _score_status(self, document: RetrievedDocument, mode: str) -> str:
        if self._is_analytic_source(document.source):
            return "ALTO"
        if mode == "embeddings":
            if document.score >= 0.55:
                return "ALTO"
            if document.score >= 0.35:
                return "MÉDIO"
            return "BAIXO"
        if document.score >= 30:
            return "ALTO"
        if document.score >= 10:
            return "MÉDIO"
        return "BAIXO"

    def _score_label(self, document: RetrievedDocument, mode: str) -> str:
        if self._is_analytic_source(document.source):
            return "AUTORITATIVO"
        if mode == "embeddings":
            if document.score >= 0.55:
                return "ALTA"
            if document.score >= 0.35:
                return "MÉDIA"
            return "BAIXA"
        if document.score >= 30:
            return "ALTA"
        if document.score >= 10:
            return "MÉDIA"
        return "BAIXA"

    def _rows(self, *items: tuple[object, object]) -> tuple[dict[str, str], ...]:
        return tuple(
            {"campo": self._clean_text(field), "valor": self._clean_text(value) or "-"}
            for field, value in items
            if self._clean_text(field)
        )

    def _is_analytic_source(self, source: str) -> bool:
        return source.startswith("Analitico:")

    def _merge_priority_documents(
        self,
        analytics: list[RetrievedDocument],
        retrieved: list[RetrievedDocument],
    ) -> list[RetrievedDocument]:
        merged: list[RetrievedDocument] = []
        seen = set()
        for document in analytics + retrieved:
            if document.source in seen:
                continue
            seen.add(document.source)
            merged.append(document)
        return merged[: self.max_context_documents + len(analytics)]

    def _classify_query_intent(self, query: str) -> QueryIntent:
        normalized = self._normalize(query)
        has_financial_metric = self._is_financial_question(normalized)
        has_filter = bool(self._year_from_query(normalized) or self._document_from_query(query)) or self._has_any(
            normalized,
            [
                "fornecedor",
                "fornecedores",
                "faturado",
                "cpf",
                "cnpj",
                "classificacao",
                "categoria",
                "vencimento",
                "vencera",
                "vencerao",
                "emitida",
                "emitidas",
            ],
        )
        has_semantic_terms = self._has_any(
            normalized,
            [
                "semelhante",
                "semelhantes",
                "assemelha",
                "parecido",
                "parecidos",
                "melhorar",
                "solo",
                "corretivo",
                "corretivos",
                "neutralizador",
                "neutralizadores",
                "pragas",
                "doencas",
                "combater",
            ],
        )

        if has_semantic_terms:
            return QueryIntent("semantica")
        if has_financial_metric and has_filter:
            return QueryIntent("financeira_filtrada")
        if has_financial_metric:
            return QueryIntent("financeira_agregada")
        return QueryIntent("exploratoria", target_input_tokens=2000)

    def _expand_query(self, text: str) -> str:
        normalized = self._normalize(text)
        additions = []
        if self._has_any(normalized, ["contas a pagar", "pagar", "pagamento", "pago", "pagos", "pagas", "despesa", "despesas", "compra", "compras"]):
            additions.append("APAGAR contas a pagar despesa despesas fornecedor fornecedores compra compras pagamento pago pagos debito saida")
        if self._has_any(normalized, ["contas a receber", "receber", "recebimento", "receita", "receitas", "venda", "vendas", "faturamento"]):
            additions.append("ARECEBER contas a receber receita receitas cliente clientes venda vendas faturamento credito entrada")
        if self._has_any(normalized, ["total", "soma", "somar", "valor", "quanto", "saldo"]):
            additions.append("valor_total total soma somatorio agregado resumo financeiro")
        if self._has_any(normalized, ["quantos", "quantas", "quantidade", "numero"]):
            additions.append("quantidade contagem count numero total")
        if self._has_any(normalized, ["maior", "menor", "media", "média"]):
            additions.append("maior menor media minimo maximo valor_total")
        return f"{text} {' '.join(additions)}"

    def _movement_type_from_query(self, normalized_query: str) -> str | None:
        payable = self._has_any(normalized_query, ["contas a pagar", "pagar", "pagamento", "pago", "pagos", "pagas", "despesa", "despesas", "compras"])
        receivable = self._has_any(normalized_query, ["contas a receber", "receber", "recebimento", "receita", "receitas", "vendas", "faturamento"])
        if payable and not receivable:
            return MovimentoContas.Tipo.APAGAR
        if receivable and not payable:
            return MovimentoContas.Tipo.ARECEBER
        return None

    def _year_from_query(self, normalized_query: str) -> int | None:
        match = re.search(r"\b(20\d{2})\b", normalized_query)
        if not match:
            return None
        return int(match.group(1))

    def _document_from_query(self, query: str) -> str:
        match = re.search(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", query)
        return match.group(0) if match else ""

    def _person_name_from_query(self, query: str, billed_only: bool = False, supplier_only: bool = False) -> str:
        normalized_query = self._normalize(query)
        queryset = Pessoa.ativos.all()
        if billed_only:
            queryset = queryset.filter(is_faturado=True)
        if supplier_only:
            queryset = queryset.filter(is_fornecedor=True)
        for person in queryset.order_by("-razao_social"):
            normalized_name = self._normalize(person.razao_social)
            if normalized_name and normalized_name in normalized_query:
                return person.razao_social
        return ""

    def _supplier_name_from_previous_answer(self, query: str) -> str:
        normalized_query = self._normalize(query)
        for person in Pessoa.ativos.filter(is_fornecedor=True).order_by("-razao_social"):
            normalized_name = self._normalize(person.razao_social)
            if normalized_name and normalized_name in normalized_query:
                return person.razao_social
        match = re.search(r"fornecedor(?: com maior valor(?: no periodo)? foi)? ([A-Z0-9 .,&/-]+?)(?:,| com R\$|\. |\n|$)", str(query))
        return self._clean_text(match.group(1)) if match else ""

    def _is_financial_question(self, normalized_query: str) -> bool:
        return self._has_any(
            normalized_query,
            [
                "total",
                "soma",
                "saldo",
                "valor",
                "movimento",
                "movimentos",
                "financeiro",
                "quanto",
                "quantos",
                "quantas",
                "maior",
                "menor",
                "media",
            ],
        )

    def _has_any(self, text: str, candidates: list[str]) -> bool:
        return any(candidate in text for candidate in candidates)

    def _only_digits(self, value: object) -> str:
        return re.sub(r"\D+", "", self._clean_text(value))

    def _unique_invoice_movements(self, queryset: Iterable[MovimentoContas]) -> list[MovimentoContas]:
        unique: list[MovimentoContas] = []
        seen: set[tuple[object, ...]] = set()
        movements = list(queryset)
        movements.sort(
            key=lambda movement: (
                -movement.valor_total,
                1 if self._has_reimport_suffix(movement.numero_documento) else 0,
                movement.created_at,
                movement.id,
            )
        )
        for movement in movements:
            key = self._invoice_dedupe_key(movement, include_type=True)
            if key in seen:
                continue
            seen.add(key)
            unique.append(movement)
        return unique

    def _unique_installments(self, queryset: Iterable[ParcelaContas]) -> list[ParcelaContas]:
        unique: list[ParcelaContas] = []
        seen: set[tuple[object, ...]] = set()
        for installment in queryset:
            key = (
                self._invoice_dedupe_key(installment.movimento, include_type=False),
                installment.numero,
                installment.data_vencimento.isoformat(),
                self._money(installment.valor),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(installment)
        return unique

    def _invoice_dedupe_key(self, movement: MovimentoContas, include_type: bool) -> tuple[object, ...]:
        parts: list[object] = [
            movement.pessoa_id,
            movement.faturado_id,
            movement.data_emissao.isoformat(),
            self._base_document_number(self._movement_invoice_number(movement)),
            self._money(movement.valor_total),
        ]
        if include_type:
            parts.insert(0, movement.tipo)
        return tuple(parts)

    def _base_document_number(self, value: object) -> str:
        raw = self._clean_text(value)
        without_suffix = re.sub(r"-\d+$", "", raw)
        digits = self._only_digits(without_suffix)
        return digits or self._normalize(without_suffix)

    def _movement_invoice_number(self, movement: MovimentoContas) -> str:
        raw_name = self._clean_text(getattr(movement, "nome_documento", ""))
        match = re.search(r"\bNF\s+(.+?)\s+-\s+\d{4}-\d{2}-\d{2}$", raw_name)
        if match:
            return self._clean_text(match.group(1))
        extracted = ""
        if isinstance(movement.dados_extraidos, dict):
            extracted = self._clean_text(movement.dados_extraidos.get("numero_nota_fiscal"))
        return extracted or self._clean_text(movement.numero_documento)

    def _has_reimport_suffix(self, value: object) -> bool:
        return bool(re.search(r"-\d+$", self._clean_text(value)))

    def _products_from_movement(self, movement: MovimentoContas) -> list[str]:
        products = movement.dados_extraidos.get("produtos", []) if isinstance(movement.dados_extraidos, dict) else []
        if not isinstance(products, list):
            return []
        return [self._clean_text(item.get("descricao", "")) for item in products if isinstance(item, dict) and self._clean_text(item.get("descricao", ""))]

    def _pest_or_disease_target(self, products: list[str]) -> str:
        text = self._normalize(" ".join(products))
        mappings = [
            ("fungicida", "doenças fúngicas"),
            ("fungo", "doenças fúngicas"),
            ("herbicida", "plantas daninhas"),
            ("inseticida", "insetos/pragas"),
            ("nematicida", "nematoides"),
            ("acaricida", "ácaros"),
            ("defensivo", "pragas e doenças em geral"),
        ]
        found = [label for token, label in mappings if token in text]
        return ", ".join(dict.fromkeys(found)) if found else "Não identificado pelo nome do produto"

    def _movement_total(self, movement_type: str) -> Decimal:
        return self._sum_decimal(
            MovimentoContas.ativos.filter(tipo=movement_type).aggregate(total=Sum("valor_total"))["total"]
        )

    def _sum_decimal(self, value: object) -> Decimal:
        if value in (None, ""):
            return Decimal("0")
        try:
            return Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def _movement_label(self, movement_type: str | None) -> str:
        if movement_type == MovimentoContas.Tipo.APAGAR:
            return "Contas a pagar"
        if movement_type == MovimentoContas.Tipo.ARECEBER:
            return "Contas a receber"
        if movement_type == "MISTO":
            return "Contas a pagar e contas a receber"
        return "Não definido"

    def _join_names(self, rows: object, field: str) -> str:
        if not isinstance(rows, list):
            return ""
        return ", ".join(str(item.get(field, "")).strip() for item in rows if isinstance(item, dict) and item.get(field))

    def _installments_text(self, parcelas: Iterable[ParcelaContas]) -> str:
        return ", ".join(
            f"{item.identificacao or item.numero} vencimento {item.data_vencimento.isoformat()} status {item.status_vencimento} valor {self._money(item.valor)}"
            for item in parcelas
        )

    def _money(self, value: Decimal) -> str:
        return f"{Decimal(value):.2f}"

    def _money_value(self, value: object) -> str:
        decimal_value = self._sum_decimal(value)
        return f"R$ {self._money(decimal_value)}" if decimal_value else self._clean_text(value)

    def _clean_text(self, value: object) -> str:
        return str(value or "").strip()
