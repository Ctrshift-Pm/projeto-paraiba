from django.urls import path

from . import views

app_name = "invoices"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/invoices/extract/", views.extract_invoice, name="extract_invoice"),
    path("api/invoices/analyze/<int:extraction_id>/", views.analyze_invoice, name="analyze_invoice"),
    path("api/invoices/launch/<int:extraction_id>/", views.launch_invoice, name="launch_invoice"),
]
