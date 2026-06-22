from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from .agent_RAG import Agent3
from .gemini_session import (
    GeminiAccessError,
    clear_gemini_api_key,
    has_session_gemini_key,
    resolve_gemini_api_key,
    store_gemini_api_key,
    validate_gemini_api_key,
)
from .models import Classificacao, MovimentoContas, Pessoa
from .services import InvoiceExtractionService
from .utils import (
    display_document_name,
    is_valid_cnpj,
    is_valid_cpf,
    mask_cep,
    mask_cnpj,
    mask_cpf,
    mask_ie,
    mask_phone,
    only_alnum,
    only_digits,
)


MIN_EMISSION_DATE = date(1910, 1, 1)
MAX_TOTAL_VALUE = Decimal("999999999999.00")


def _gemini_validation_model() -> str:
    return str(
        getattr(settings, "GEMINI_GATE_MODEL", "")
        or getattr(settings, "GEMINI_RAG_MODEL", "")
        or getattr(settings, "GEMINI_EXTRACTION_MODEL", "")
        or "gemini-2.5-flash-lite"
    ).strip()


def _gemini_gate_url(*, next_path: str = "/", error: str = "") -> str:
    params = {"next": next_path}
    if error:
        params["error"] = error
    return f"{reverse('invoices:gemini_gate')}?{urlencode(params)}"


def _render_gemini_gate(request: HttpRequest, *, next_path: str, error: str = "") -> HttpResponse:
    return render(
        request,
        "invoices/gemini_gate.html",
        {
            "next_path": next_path or reverse("invoices:index"),
            "error": error or request.GET.get("error", ""),
            "has_key": has_session_gemini_key(request),
        },
    )


def _wants_json(request: HttpRequest) -> bool:
    accept = str(request.headers.get("Accept", "")).lower()
    requested_with = str(request.headers.get("X-Requested-With", "")).lower()
    content_type = str(request.headers.get("Content-Type", "")).lower()
    return "application/json" in accept or requested_with == "xmlhttprequest" or "application/json" in content_type


def _require_gemini_access_page(request: HttpRequest, *, next_path: str) -> HttpResponse | None:
    if has_session_gemini_key(request):
        return None
    return _render_gemini_gate(request, next_path=next_path)


def _gemini_access_denied_json(request: HttpRequest, *, next_path: str, error: str) -> JsonResponse:
    clear_gemini_api_key(request)
    return JsonResponse({"error": error, "redirect_to": _gemini_gate_url(next_path=next_path, error=error)}, status=401)

@never_cache
def index(request: HttpRequest) -> HttpResponse:
    gate = _require_gemini_access_page(request, next_path=request.get_full_path() or reverse("invoices:index"))
    if gate is not None:
        return gate
    return render(request, "invoices/index.html")


@never_cache
def manage_records(request: HttpRequest) -> HttpResponse:
    gate = _require_gemini_access_page(request, next_path=request.get_full_path() or reverse("invoices:manage_records"))
    if gate is not None:
        return gate
    people = [
        {"id": item.id, "label": item.razao_social}
        for item in Pessoa.ativos.order_by("razao_social", "id")
    ]
    classifications = [
        {"id": item.id, "label": f"{item.get_tipo_display()} - {item.descricao}", "tipo": item.tipo}
        for item in Classificacao.ativos.order_by("tipo", "descricao", "id")
    ]
    return render(
        request,
        "invoices/manage.html",
        {
            "people_options": people,
            "classification_options": classifications,
            "active_counts": {
                "contas": MovimentoContas.ativos.count(),
                "pessoas": Pessoa.ativos.count(),
                "classificacoes": Classificacao.ativos.count(),
            },
        },
    )


@never_cache
def rag_query(request: HttpRequest) -> HttpResponse:
    has_query = False
    if request.method in {"GET", "POST"}:
        data = request.GET if request.method == "GET" else request.POST
        has_query = bool(data.get("query", "").strip())

    gate = _require_gemini_access_page(request, next_path=request.get_full_path() or reverse("invoices:rag_query"))
    if gate is not None and not has_query:
        return gate
    context = {
        "query": "",
        "mode": "simple",
        "result": None,
        "movement_catalog": [],
        "error": "",
    }

    if request.method in {"GET", "POST"}:
        data = request.GET if request.method == "GET" else request.POST
        context["query"] = data.get("query", "").strip()
        context["mode"] = data.get("mode", "simple")
        if context["query"]:
            try:
                context["result"] = Agent3(api_key=resolve_gemini_api_key(request)).run_query(context["query"], context["mode"])
                context["movement_catalog"] = _build_movement_catalog()
            except GeminiAccessError as exc:
                clear_gemini_api_key(request)
                if _wants_json(request):
                    return JsonResponse({"error": str(exc), "redirect_to": reverse("invoices:gemini_gate")})
                return _render_gemini_gate(request, next_path=request.get_full_path() or reverse("invoices:rag_query"), error=str(exc))
            except ValueError as exc:
                context["error"] = str(exc)
            except Exception as exc:
                context["error"] = f"Erro interno ao consultar o agente RAG: {exc}"

    if _wants_json(request):
        return JsonResponse(context)

    return render(request, "invoices/rag_query.html", context)


@csrf_exempt
def manage_collection(request: HttpRequest, resource: str) -> JsonResponse:
    try:
        if request.method == "GET":
            return JsonResponse({"results": _search_resource(resource, request)})
        if request.method == "POST":
            payload = _json_payload(request)
            instance = _create_resource(resource, payload)
            return JsonResponse({"success": True, "record": _serialize_resource(resource, instance)}, status=201)
        return JsonResponse({"error": "Metodo nao permitido."}, status=405)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
def manage_detail(request: HttpRequest, resource: str, record_id: int) -> JsonResponse:
    try:
        model = _resource_model(resource)
        instance = get_object_or_404(model.objects, id=record_id)

        if request.method == "GET":
            return JsonResponse({"record": _serialize_resource(resource, instance, include_inactive=True)})
        if request.method in {"POST", "PUT", "PATCH"}:
            payload = _json_payload(request)
            _update_resource(resource, instance, payload)
            return JsonResponse({"success": True, "record": _serialize_resource(resource, instance, include_inactive=True)})
        if request.method == "DELETE":
            instance.inativar()
            return JsonResponse({"success": True, "record": _serialize_resource(resource, instance, include_inactive=True)})
        return JsonResponse({"error": "Metodo nao permitido."}, status=405)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def _json_payload(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalido: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Envie um objeto JSON.")
    return payload


def _resource_model(resource: str):
    models = {
        "pessoas": Pessoa,
        "classificacoes": Classificacao,
        "contas": MovimentoContas,
    }
    if resource not in models:
        raise ValueError("Recurso invalido.")
    return models[resource]


def _search_resource(resource: str, request: HttpRequest) -> list[dict]:
    show_all = request.GET.get("all") == "1"
    query = request.GET.get("q", "").strip()
    if not show_all and not query:
        return []

    if resource == "pessoas":
        queryset = Pessoa.ativos.all()
        queryset = _apply_terms(queryset, query, ["razao_social", "nome_fantasia", "cpf", "cnpj", "municipio", "uf"])
        queryset = _apply_order(queryset, request.GET.get("order"), {"razao_social", "cpf", "cnpj", "municipio", "uf", "id"})
    elif resource == "classificacoes":
        queryset = Classificacao.ativos.all()
        queryset = _apply_terms(queryset, query, ["tipo", "descricao"])
        queryset = _apply_order(queryset, request.GET.get("order"), {"tipo", "descricao", "id"})
    elif resource == "contas":
        queryset = MovimentoContas.ativos.select_related("pessoa", "faturado").prefetch_related("classificacoes")
        queryset = _apply_movement_terms(queryset, query)
        queryset = _apply_order(queryset, request.GET.get("order"), {"tipo", "numero_documento", "nome_documento", "data_emissao", "valor_total", "id"})
    else:
        raise ValueError("Recurso invalido.")
    return [_serialize_resource(resource, item) for item in queryset[:300]]


def _apply_terms(queryset, query: str, fields: list[str]):
    for term in _search_terms(query):
        condition = Q()
        for field in fields:
            condition |= Q(**{f"{field}__icontains": term})
        queryset = queryset.filter(condition)
    return queryset


def _apply_movement_terms(queryset, query: str):
    normalized_query = " ".join(_search_terms(query))
    if "contas pagar" in normalized_query or "conta pagar" in normalized_query or normalized_query == "pagar":
        queryset = queryset.filter(tipo=MovimentoContas.Tipo.APAGAR)
    if "contas receber" in normalized_query or "conta receber" in normalized_query or normalized_query == "receber":
        queryset = queryset.filter(tipo=MovimentoContas.Tipo.ARECEBER)
    skip_type_terms = {"conta", "contas", "pagar", "receber"}
    for term in [item for item in _search_terms(query) if item not in skip_type_terms]:
        queryset = queryset.filter(
            Q(tipo__icontains=term)
            | Q(numero_documento__icontains=term)
            | Q(nome_documento__icontains=term)
            | Q(observacoes__icontains=term)
            | Q(pessoa__razao_social__icontains=term)
            | Q(faturado__razao_social__icontains=term)
            | Q(classificacoes__descricao__icontains=term)
        ).distinct()
    return queryset


def _search_terms(query: str) -> list[str]:
    stopwords = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "para"}
    return [item.lower() for item in str(query or "").split() if item and item.lower() not in stopwords]


def _apply_order(queryset, order: str | None, allowed: set[str]):
    order = (order or "").strip()
    field = order[1:] if order.startswith("-") else order
    if field in allowed:
        return queryset.order_by(order)
    return queryset


def _create_resource(resource: str, payload: dict):
    if resource == "pessoas":
        return Pessoa.objects.create(**_person_values(payload), ativo=True)
    if resource == "classificacoes":
        return Classificacao.objects.create(**_classification_values(payload), ativo=True)
    if resource == "contas":
        classification_ids = _classification_ids(payload)
        movement = MovimentoContas.objects.create(**_movement_values(payload), ativo=True)
        movement.classificacoes.set(Classificacao.ativos.filter(id__in=classification_ids))
        return movement
    raise ValueError("Recurso invalido.")


def _update_resource(resource: str, instance, payload: dict) -> None:
    if resource == "pessoas":
        values = _person_values(payload)
    elif resource == "classificacoes":
        values = _classification_values(payload)
    elif resource == "contas":
        values = _movement_values(payload, instance=instance)
    else:
        raise ValueError("Recurso invalido.")

    for field, value in values.items():
        setattr(instance, field, value)
    instance.save()
    if resource == "contas":
        instance.classificacoes.set(Classificacao.ativos.filter(id__in=_classification_ids(payload)))


def _person_values(payload: dict) -> dict:
    cpf = only_digits(payload.get("cpf")) or None
    cnpj = only_alnum(payload.get("cnpj")) or None
    is_fornecedor = bool(payload.get("is_fornecedor"))
    is_cliente = bool(payload.get("is_cliente"))
    is_faturado = bool(payload.get("is_faturado"))
    if not (cpf or cnpj):
        raise ValueError("Informe CPF ou CNPJ da pessoa.")
    if cpf and not is_valid_cpf(cpf):
        raise ValueError("CPF invalido.")
    if cnpj and not is_valid_cnpj(cnpj):
        raise ValueError("CNPJ invalido.")
    role_count = sum(1 for item in [is_fornecedor, is_cliente, is_faturado] if item)
    if role_count != 1:
        raise ValueError("Selecione exatamente um papel: fornecedor, cliente ou faturado.")
    number = only_digits(payload.get("numero"))
    cep = only_digits(payload.get("cep"))
    if _text(payload, "numero") and not number:
        raise ValueError("Numero deve conter apenas digitos.")
    if cep and len(cep) != 8:
        raise ValueError("CEP deve conter 8 digitos.")
    return {
        "razao_social": _required(payload, "razao_social"),
        "nome_fantasia": _text(payload, "nome_fantasia"),
        "cpf": cpf,
        "cnpj": cnpj,
        "inscricao_estadual": only_digits(payload.get("inscricao_estadual")),
        "endereco": _text(payload, "endereco"),
        "numero": number,
        "bairro": _text(payload, "bairro"),
        "municipio": _text(payload, "municipio"),
        "uf": _text(payload, "uf").upper()[:2],
        "cep": cep,
        "telefone": only_digits(payload.get("telefone")),
        "is_fornecedor": is_fornecedor,
        "is_cliente": is_cliente,
        "is_faturado": is_faturado,
    }


def _classification_values(payload: dict) -> dict:
    classification_type = _required(payload, "tipo").upper()
    if classification_type not in {Classificacao.Tipo.RECEITA, Classificacao.Tipo.DESPESA}:
        raise ValueError("Tipo de classificacao invalido.")
    return {"tipo": classification_type, "descricao": _required(payload, "descricao").upper()}


def _movement_values(payload: dict, instance: MovimentoContas | None = None) -> dict:
    movement_type = _required(payload, "tipo").upper()
    if movement_type not in {MovimentoContas.Tipo.APAGAR, MovimentoContas.Tipo.ARECEBER}:
        raise ValueError("Tipo de conta invalido.")
    pessoa = get_object_or_404(Pessoa.ativos, id=payload.get("pessoa_id"))
    issue_date = _date(payload.get("data_emissao"))
    document_number = instance.numero_documento if instance else _manual_document_number(movement_type, issue_date, pessoa.id)
    document_name = display_document_name(supplier_name=pessoa.razao_social, number=document_number, issue_date=issue_date)
    return {
        "tipo": movement_type,
        "pessoa": pessoa,
        "faturado": get_object_or_404(Pessoa.ativos, id=payload.get("faturado_id")),
        "numero_documento": document_number,
        "nome_documento": document_name,
        "data_emissao": issue_date,
        "valor_total": _decimal(payload.get("valor_total")),
        "observacoes": _text(payload, "observacoes"),
    }


def _manual_document_number(movement_type: str, issue_date: date, person_id: int) -> str:
    prefix = f"MANUAL-{issue_date:%Y%m%d}-{person_id}"
    candidate = prefix
    suffix = 2
    while MovimentoContas.objects.filter(tipo=movement_type, numero_documento=candidate).exists():
        candidate = f"{prefix}-{suffix}"
        suffix += 1
    return candidate


def _classification_ids(payload: dict) -> list[int]:
    raw_ids = payload.get("classificacao_ids") or []
    if not raw_ids:
        raise ValueError("Selecione ao menos uma classificacao.")
    return raw_ids


def _required(payload: dict, field: str) -> str:
    value = _text(payload, field)
    if not value:
        raise ValueError(f"Campo obrigatorio: {field}.")
    return value


def _text(payload: dict, field: str) -> str:
    return str(payload.get(field) or "").strip()


def _nullable_text(payload: dict, field: str) -> str | None:
    return _text(payload, field) or None


def _decimal(value: object) -> Decimal:
    raw = str(value or "").strip()
    if raw.startswith("R$"):
        raw = raw[2:].strip()
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        decimal_value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Valor total invalido.") from exc
    if decimal_value <= 0:
        raise ValueError("Valor total deve ser maior que zero.")
    if decimal_value > MAX_TOTAL_VALUE:
        raise ValueError("Valor total deve ser no maximo R$ 999.999.999.999,00.")
    return decimal_value.quantize(Decimal("0.01"))


def _date(value: object) -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("Data de emissao invalida.") from exc
    if parsed < MIN_EMISSION_DATE:
        raise ValueError("Data de emissao nao pode ser anterior a 01/01/1910.")
    if parsed > date.today():
        raise ValueError("Data de emissao nao pode ser futura.")
    return parsed


def _serialize_resource(resource: str, instance, include_inactive: bool = False) -> dict:
    if not include_inactive and not instance.ativo:
        return {}
    if resource == "pessoas":
        roles = []
        if instance.is_fornecedor:
            roles.append("Fornecedor")
        if instance.is_cliente:
            roles.append("Cliente")
        if instance.is_faturado:
            roles.append("Faturado")
        return {
            "id": instance.id,
            "razao_social": instance.razao_social,
            "nome_fantasia": instance.nome_fantasia,
            "cpf": mask_cpf(instance.cpf),
            "cnpj": mask_cnpj(instance.cnpj),
            "inscricao_estadual": mask_ie(instance.inscricao_estadual),
            "endereco": instance.endereco,
            "numero": instance.numero,
            "bairro": instance.bairro,
            "municipio": instance.municipio,
            "uf": instance.uf,
            "cep": mask_cep(instance.cep),
            "telefone": mask_phone(instance.telefone),
            "is_fornecedor": instance.is_fornecedor,
            "is_cliente": instance.is_cliente,
            "is_faturado": instance.is_faturado,
            "roles": ", ".join(roles),
            "ativo": instance.ativo,
        }
    if resource == "classificacoes":
        return {
            "id": instance.id,
            "tipo": instance.tipo,
            "tipo_label": instance.get_tipo_display(),
            "descricao": instance.descricao,
            "ativo": instance.ativo,
        }
    if resource == "contas":
        classifications = list(instance.classificacoes.all())
        return {
            "id": instance.id,
            "tipo": instance.tipo,
            "tipo_label": instance.get_tipo_display(),
            "pessoa_id": instance.pessoa_id,
            "pessoa": instance.pessoa.razao_social,
            "faturado_id": instance.faturado_id,
            "faturado": instance.faturado.razao_social,
            "numero_documento": instance.numero_documento,
            "nome_documento": instance.nome_documento or instance.numero_documento,
            "data_emissao": instance.data_emissao.isoformat(),
            "valor_total": f"{instance.valor_total:.2f}",
            "observacoes": instance.observacoes,
            "classificacao_ids": [item.id for item in classifications],
            "classificacoes": ", ".join(item.descricao for item in classifications),
            "ativo": instance.ativo,
        }
    raise ValueError("Recurso invalido.")


def _build_movement_catalog() -> list[dict]:
    catalog = []
    queryset = (
        MovimentoContas.ativos.select_related("pessoa", "faturado")
        .prefetch_related("classificacoes", "parcelas")
        .order_by("-created_at")[:500]
    )
    for movement in queryset:
        classificacoes = ", ".join(item.descricao for item in movement.classificacoes.all()) or "Sem classificação"
        rows = [
            {"campo": "Documento", "valor": movement.nome_documento or movement.numero_documento},
            {"campo": "Número fiscal", "valor": movement.numero_documento},
            {"campo": "Tipo", "valor": movement.get_tipo_display()},
            {"campo": "Pessoa", "valor": movement.pessoa.razao_social},
            {"campo": "Faturado", "valor": movement.faturado.razao_social},
            {"campo": "Data de emissão", "valor": movement.data_emissao.isoformat()},
            {"campo": "Valor total", "valor": f"R$ {movement.valor_total:.2f}"},
            {"campo": "Classificações", "valor": classificacoes},
            {
                "campo": "Parcelas",
                "valor": ", ".join(
                    f"{item.identificacao or item.numero} - {item.data_vencimento.isoformat()} - {item.status_vencimento} - R$ {item.valor:.2f}"
                    for item in movement.parcelas.all()
                ) or "Sem parcelas",
            },
            {"campo": "Observações", "valor": movement.observacoes or "-"},
        ]
        catalog.append(
            {
                "id": movement.id,
                "title": movement.nome_documento or f"Movimento {movement.id} - {movement.numero_documento}",
                "source": f"MovimentoContas:{movement.id}",
                "movement_type": movement.tipo,
                "movement_label": movement.get_tipo_display(),
                "pessoa": movement.pessoa.razao_social,
                "faturado": movement.faturado.razao_social,
                "data_emissao": movement.data_emissao.isoformat(),
                "valor_total": f"R$ {movement.valor_total:.2f}",
                "classificacoes": classificacoes,
                "rows": rows,
                "content": movement.observacoes or "Sem observações adicionais.",
            }
        )
    return catalog


@csrf_exempt
def extract_invoice(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"error": "Metodo nao permitido.", "detail": "Utilize POST em /api/invoices/extract/."},
            status=405,
        )

    uploaded_pdf = request.FILES.get("pdf")
    if uploaded_pdf is None:
        return JsonResponse(
            {
                "error": "Arquivo PDF é obrigatório.",
                "detail": "Envie o campo 'pdf' com o arquivo para extracao.",
            },
            status=400,
        )

    content_type = (uploaded_pdf.content_type or "").lower()
    name = uploaded_pdf.name or ""
    if content_type != "application/pdf" and not name.lower().endswith(".pdf"):
        return JsonResponse({"error": "Formato do arquivo inválido.", "detail": "Envie um arquivo PDF no campo 'pdf'."}, status=400)

    service = InvoiceExtractionService(gemini_api_key=resolve_gemini_api_key(request))
    try:
        payload = service.extract(uploaded_pdf)
        return JsonResponse(payload)
    except GeminiAccessError as exc:
        clear_gemini_api_key(request)
        return _gemini_access_denied_json(request, next_path=reverse("invoices:index"), error=str(exc))
    except ValueError as exc:
        service.persistence_agent.save_error(uploaded_pdf, str(exc))
        return JsonResponse(
            {"error": "Falha ao extrair dados do PDF.", "detail": str(exc)},
            status=400,
        )
    except Exception as exc:
        service.persistence_agent.save_error(uploaded_pdf, str(exc), provider="system")
        return JsonResponse(
            {"error": "Erro interno ao processar o arquivo.", "detail": str(exc)},
            status=500,
        )


@csrf_exempt
def analyze_invoice(request: HttpRequest, extraction_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"error": "Metodo nao permitido.", "detail": "Utilize POST em /api/invoices/analyze/<id>/."},
            status=405,
        )

    service = InvoiceExtractionService(gemini_api_key=resolve_gemini_api_key(request))
    try:
        payload = service.analyze(extraction_id)
        return JsonResponse(payload)
    except GeminiAccessError as exc:
        clear_gemini_api_key(request)
        return _gemini_access_denied_json(request, next_path=reverse("invoices:index"), error=str(exc))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except Exception as exc:
        return JsonResponse({"error": "Erro interno ao analisar documento.", "detail": str(exc)}, status=500)


@csrf_exempt
def launch_invoice(request: HttpRequest, extraction_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"error": "Metodo nao permitido.", "detail": "Utilize POST em /api/invoices/launch/<id>/."},
            status=405,
        )

    service = InvoiceExtractionService(gemini_api_key=resolve_gemini_api_key(request))
    try:
        payload = service.launch(extraction_id)
        return JsonResponse(payload)
    except GeminiAccessError as exc:
        clear_gemini_api_key(request)
        return _gemini_access_denied_json(request, next_path=reverse("invoices:index"), error=str(exc))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except Exception as exc:
        return JsonResponse({"error": "Erro interno ao executar lancamento.", "detail": str(exc)}, status=500)


@csrf_exempt
@never_cache
def gemini_gate(request: HttpRequest) -> HttpResponse:
    next_path = request.POST.get("next") if request.method == "POST" else request.GET.get("next")
    next_path = next_path or reverse("invoices:index")

    if _wants_json(request):
        if request.method == "GET":
            return JsonResponse({
                "has_key": has_session_gemini_key(request),
                "next_path": next_path,
            })
        if request.method != "POST":
            return JsonResponse({"error": "Metodo nao permitido."}, status=405)
        api_key = request.POST.get("api_key", "").strip()
        if not api_key:
            return JsonResponse({"error": "Informe uma chave do Gemini."}, status=400)
        is_valid, message = validate_gemini_api_key(api_key, _gemini_validation_model())
        if not is_valid:
            clear_gemini_api_key(request)
            return JsonResponse({"error": message or "Chave do Gemini invalida."}, status=401)
        store_gemini_api_key(request, api_key)
        return JsonResponse({"success": True, "next_path": next_path})

    if request.method == "POST":
        api_key = request.POST.get("api_key", "").strip()
        if not api_key:
            return _render_gemini_gate(request, next_path=next_path, error="Informe uma chave do Gemini.")

        is_valid, message = validate_gemini_api_key(api_key, _gemini_validation_model())
        if not is_valid:
            clear_gemini_api_key(request)
            return _render_gemini_gate(request, next_path=next_path, error=message or "Chave do Gemini invalida.")

        store_gemini_api_key(request, api_key)
        return redirect(next_path)

    if has_session_gemini_key(request):
        return render(
            request,
            "invoices/gemini_gate.html",
            {
                "next_path": next_path,
                "error": request.GET.get("error", ""),
                "has_key": True,
                "success": "Chave ativa na sessão. Você pode trocar a chave abaixo.",
            },
        )

    return _render_gemini_gate(request, next_path=next_path)
