from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from .services import InvoiceExtractionService

def index(request: HttpRequest) -> HttpResponse:
    return render(request, "invoices/index.html")


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

    service = InvoiceExtractionService()
    try:
        payload = service.extract(uploaded_pdf)
        return JsonResponse(payload)
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


def analyze_invoice(request: HttpRequest, extraction_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"error": "Metodo nao permitido.", "detail": "Utilize POST em /api/invoices/analyze/<id>/."},
            status=405,
        )

    service = InvoiceExtractionService()
    try:
        payload = service.analyze(extraction_id)
        return JsonResponse(payload)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except Exception as exc:
        return JsonResponse({"error": "Erro interno ao analisar documento.", "detail": str(exc)}, status=500)


def launch_invoice(request: HttpRequest, extraction_id: int) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse(
            {"error": "Metodo nao permitido.", "detail": "Utilize POST em /api/invoices/launch/<id>/."},
            status=405,
        )

    service = InvoiceExtractionService()
    try:
        payload = service.launch(extraction_id)
        return JsonResponse(payload)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except Exception as exc:
        return JsonResponse({"error": "Erro interno ao executar lancamento.", "detail": str(exc)}, status=500)
