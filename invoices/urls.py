from django.urls import path

from . import views

app_name = "invoices"

urlpatterns = [
    path("", views.index, name="index"),
    path("gemini/", views.gemini_gate, name="gemini_gate"),
    path("cadastros/", views.manage_records, name="manage_records"),
    path("rag/", views.rag_query, name="rag_query"),
    path("api/cadastros/<str:resource>/", views.manage_collection, name="manage_collection"),
    path("api/cadastros/<str:resource>/<int:record_id>/", views.manage_detail, name="manage_detail"),
    path("api/invoices/extract/", views.extract_invoice, name="extract_invoice"),
    path("api/invoices/analyze/<int:extraction_id>/", views.analyze_invoice, name="analyze_invoice"),
    path("api/invoices/launch/<int:extraction_id>/", views.launch_invoice, name="launch_invoice"),
]
