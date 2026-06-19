from __future__ import annotations

from datetime import date

from django.db import migrations, models


def backfill_document_names_and_status(apps, schema_editor):
    MovimentoContas = apps.get_model("invoices", "MovimentoContas")
    ParcelaContas = apps.get_model("invoices", "ParcelaContas")
    Pessoa = apps.get_model("invoices", "Pessoa")

    for movement in MovimentoContas.objects.select_related("pessoa").all():
        supplier = " ".join((movement.pessoa.razao_social or "DOCUMENTO").upper().split())
        movement.nome_documento = f"{supplier} - NF {movement.numero_documento} - {movement.data_emissao.isoformat()}"
        movement.save(update_fields=["nome_documento"])

    today = date.today()
    for parcel in ParcelaContas.objects.all():
        if parcel.data_vencimento < today:
            parcel.status_vencimento = "VENCIDA"
        elif parcel.data_vencimento > today:
            parcel.status_vencimento = "A_VENCER"
        else:
            parcel.status_vencimento = "ABERTA"
        parcel.save(update_fields=["status_vencimento"])

    for person in Pessoa.objects.all():
        roles = [person.is_faturado, person.is_fornecedor, person.is_cliente]
        if sum(1 for item in roles if item) <= 1:
            continue
        keep_faturado = person.is_faturado
        keep_fornecedor = not keep_faturado and person.is_fornecedor
        keep_cliente = not keep_faturado and not keep_fornecedor and person.is_cliente
        person.is_faturado = keep_faturado
        person.is_fornecedor = keep_fornecedor
        person.is_cliente = keep_cliente
        person.save(update_fields=["is_faturado", "is_fornecedor", "is_cliente"])


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0003_remove_pessoa_chk_pessoa_is_cliente_bool_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimentocontas",
            name="nome_documento",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="parcelacontas",
            name="status_vencimento",
            field=models.CharField(
                choices=[("ABERTA", "Aberta"), ("A_VENCER", "A vencer"), ("VENCIDA", "Vencida")],
                default="ABERTA",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_document_names_and_status, migrations.RunPython.noop),
    ]
