from django.urls import path

from . import views

app_name = "invoices"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/invoices/extract/", views.extract_invoice, name="extract_invoice"),
]
