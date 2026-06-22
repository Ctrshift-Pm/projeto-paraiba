from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from invoices.models import Classificacao, InvoiceExtraction, MovimentoContas, ParcelaContas, Pessoa
from invoices.utils import display_document_name, due_status


DEMO_PREFIX = "DEMO-"


class Command(BaseCommand):
    help = "Cria extrações e lançamentos financeiros demonstrativos para popular o RAG."

    supplier_names = [
        "CTVA PROTECAO DE CULTIVOS LTDA.",
        "AGREX DO BRASIL LTDA.",
        "AGRO NORDESTE INSUMOS LTDA",
        "POSTO SERTAO DIESEL LTDA",
        "HIDRAULICA PARAIBA COMERCIO LTDA",
        "PECAS E MOTORES CAMPINA LTDA",
        "ENERGIA SOLAR BREJO LTDA",
        "LOGISTICA CARIRI TRANSPORTES LTDA",
        "TECNO CAMPO SOFTWARE LTDA",
        "ARMAZEM RURAL JOAO PESSOA LTDA",
    ]
    customer_names = [
        "BELTRANO DE SOUZA",
        "FAZENDA BOA SAFRA LTDA",
        "COOPERATIVA VALE VERDE",
        "MERCADO ATACADO PARAIBA LTDA",
        "CLIENTE RURAL ALTO SERTAO",
        "DISTRIBUIDORA LITORAL LTDA",
    ]
    expense_categories = [
        "INSUMOS AGRICOLAS",
        "MANUTENCAO E OPERACAO",
        "INFRAESTRUTURA E UTILIDADES",
        "ADMINISTRATIVAS",
        "LOGISTICA E FRETE",
        "TECNOLOGIA E MONITORAMENTO",
    ]
    revenue_categories = [
        "RECEITA OPERACIONAL",
        "VENDAS",
        "PROVENTOS",
        "SERVICOS PRESTADOS",
    ]
    expense_products = [
        "Oleo Diesel S10",
        "Fertilizante ureia",
        "Material hidraulico",
        "Peca de reposicao",
        "Frete agricola",
        "Licenca de software agricola",
    ]
    revenue_products = [
        "Venda de safra",
        "Prestacao de servico agricola",
        "Consultoria operacional",
        "Locacao de equipamento",
    ]

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--count", type=int, default=200, help="Quantidade de extrações demonstrativas desejada.")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Remove apenas dados demonstrativos com prefixo DEMO- antes de recriar.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        count = max(1, int(options["count"]))
        if options["reset"]:
            self._delete_demo_data()

        existing = InvoiceExtraction.objects.filter(file_name__startswith=DEMO_PREFIX.lower()).count()
        to_create = max(0, count - existing)
        if to_create == 0:
            self.stdout.write(self.style.SUCCESS(f"Banco já possui {existing} extrações demonstrativas."))
            return

        classifications = self._ensure_classifications()
        suppliers = self._ensure_people(self.supplier_names, role="fornecedor", base_document=34000000000100)
        customers = self._ensure_people(self.customer_names, role="faturado", base_document=12000000000)
        supplier_map = {item.razao_social: item for item in suppliers}
        customer_map = {item.razao_social: item for item in customers}

        start_index = existing + 1
        for index in range(start_index, start_index + to_create):
            self._create_demo_invoice(index, suppliers, customers, classifications, supplier_map, customer_map)

        total = InvoiceExtraction.objects.filter(file_name__startswith=DEMO_PREFIX.lower()).count()
        movements = MovimentoContas.objects.filter(numero_documento__startswith=DEMO_PREFIX).count()
        self.stdout.write(self.style.SUCCESS(f"Criadas {to_create} extrações demonstrativas. Total demo: {total}; movimentos demo: {movements}."))

    def _delete_demo_data(self) -> None:
        movements = MovimentoContas.objects.filter(numero_documento__startswith=DEMO_PREFIX)
        ParcelaContas.objects.filter(movimento__in=movements).delete()
        InvoiceExtraction.objects.filter(file_name__startswith=DEMO_PREFIX.lower()).delete()
        movements.delete()

    def _ensure_classifications(self) -> dict[str, Classificacao]:
        result = {}
        for description in self.expense_categories:
            result[description] = self._get_or_create_classification(Classificacao.Tipo.DESPESA, description)
        for description in self.revenue_categories:
            result[description] = self._get_or_create_classification(Classificacao.Tipo.RECEITA, description)
        return result

    def _get_or_create_classification(self, classification_type: str, description: str) -> Classificacao:
        classification, _ = Classificacao.objects.get_or_create(
            tipo=classification_type,
            descricao=description,
            defaults={"ativo": True},
        )
        if not classification.ativo:
            classification.ativo = True
            classification.save(update_fields=["ativo", "updated_at"])
        return classification

    def _ensure_people(self, names: list[str], *, role: str, base_document: int) -> list[Pessoa]:
        people = []
        for offset, name in enumerate(names, start=1):
            document = self._document_for(role, offset)
            defaults = {
                "razao_social": name,
                "nome_fantasia": name[:60],
                "municipio": "Campina Grande" if offset % 2 else "Joao Pessoa",
                "uf": "PB",
                "ativo": True,
                "is_fornecedor": role == "fornecedor",
                "is_cliente": role == "cliente",
                "is_faturado": role == "faturado",
            }
            lookup = {"cnpj": document} if role == "fornecedor" else {"cpf": document}
            person = Pessoa.objects.filter(**lookup).first()
            if person is None:
                person = Pessoa.objects.create(**lookup, **defaults)
            else:
                updated = False
                for field, value in defaults.items():
                    if getattr(person, field) != value:
                        setattr(person, field, value)
                        updated = True
                if updated:
                    person.save()
            people.append(person)
        return people

    def _create_demo_invoice(
        self,
        index: int,
        suppliers: list[Pessoa],
        customers: list[Pessoa],
        classifications: dict[str, Classificacao],
        supplier_map: dict[str, Pessoa],
        customer_map: dict[str, Pessoa],
    ) -> None:
        profile = self._profile_for_index(index, supplier_map, customer_map)
        if profile is None:
            movement_type = MovimentoContas.Tipo.APAGAR if index % 5 != 0 else MovimentoContas.Tipo.ARECEBER
            is_payable = movement_type == MovimentoContas.Tipo.APAGAR
            supplier = suppliers[index % len(suppliers)]
            billed = customers[index % len(customers)]
            category = self.expense_categories[index % len(self.expense_categories)] if is_payable else self.revenue_categories[index % len(self.revenue_categories)]
            product = self.expense_products[index % len(self.expense_products)] if is_payable else self.revenue_products[index % len(self.revenue_products)]
            issue_date = date(2024, 1, 1) + timedelta(days=(index * 3) % 360)
            amount = self._amount_for(index, is_payable)
            invoice_number = f"{DEMO_PREFIX}{index:04d}"
            installments = self._installments(index, issue_date, amount)
        else:
            movement_type = profile["movement_type"]
            supplier = supplier_map[profile["supplier_name"]]
            billed = customer_map[profile["billed_name"]]
            category = profile["category"]
            product = profile["product"]
            issue_date = profile["issue_date"]
            amount = profile["amount"]
            invoice_number = profile["invoice_number"]
            installments = profile["installments"]
        data = self._result_json(
            document_number=invoice_number,
            issue_date=issue_date,
            supplier=supplier,
            billed=billed,
            product=product,
            amount=amount,
            category=category,
            movement_type=movement_type,
            installments=installments,
        )

        movement_number = f"{DEMO_PREFIX}{index:04d}"
        movement, _ = MovimentoContas.objects.update_or_create(
            tipo=movement_type,
            numero_documento=movement_number,
            defaults={
                "pessoa": supplier,
                "faturado": billed,
                "data_emissao": issue_date,
                "valor_total": amount,
                "nome_documento": display_document_name(supplier_name=supplier.razao_social, number=data["numero_nota_fiscal"], issue_date=issue_date),
                "observacoes": "Lançamento demonstrativo criado para popular o RAG.",
                "dados_extraidos": data,
                "ativo": True,
            },
        )
        movement.classificacoes.set([classifications[category]])
        self._sync_installments(movement, installments)

        InvoiceExtraction.objects.update_or_create(
            file_name=f"{DEMO_PREFIX.lower()}nota-fiscal-{index:04d}.pdf",
            defaults={
                "file_size": 180_000 + (index * 137),
                "provider": "demo",
                "status": InvoiceExtraction.Status.SUCCESS,
                "result_json": data,
                "error_message": "",
                "movement_type": movement_type,
                "movimento": movement,
            },
        )

    def _profile_for_index(self, index: int, supplier_map: dict[str, Pessoa], customer_map: dict[str, Pessoa]) -> dict | None:
        if index == 1:
            return {
                "supplier_name": "CTVA PROTECAO DE CULTIVOS LTDA.",
                "billed_name": "BELTRANO DE SOUZA",
                "movement_type": MovimentoContas.Tipo.APAGAR,
                "category": "INSUMOS AGRICOLAS",
                "product": "VESSARYA BOMBONA 10L FUNGICIDA UN3082, SUBSTANCIA QUE APRESENTA RISCO PARA O MEIO AMBIENTE, LIQUIDA, N.E. (Benzovindiflupir, Picoxistrobina)",
                "issue_date": date(2025, 4, 30),
                "amount": Decimal("163520.00"),
                "invoice_number": "000012776",
                "installments": [
                    {"numero": 1, "data_vencimento": "2025-05-30", "valor": "163520.00"},
                ],
            }
        if index == 2:
            return {
                "supplier_name": "AGREX DO BRASIL LTDA.",
                "billed_name": "FAZENDA BOA SAFRA LTDA",
                "movement_type": MovimentoContas.Tipo.APAGAR,
                "category": "MANUTENCAO E OPERACAO",
                "product": "Correia de transmissao, rolamento, filtro de oleo e peca de reposicao para colheitadeira",
                "issue_date": date(2025, 2, 15),
                "amount": Decimal("7050.00"),
                "invoice_number": "000005531",
                "installments": [
                    {"numero": 1, "data_vencimento": "2025-03-17", "valor": "3525.00"},
                    {"numero": 2, "data_vencimento": "2025-04-17", "valor": "3525.00"},
                ],
            }
        if index == 3:
            return {
                "supplier_name": "AGRO NORDESTE INSUMOS LTDA",
                "billed_name": "BELTRANO DE SOUZA",
                "movement_type": MovimentoContas.Tipo.APAGAR,
                "category": "INSUMOS AGRICOLAS",
                "product": "Fungicida para ferrugem asiatica e doencas foliares",
                "issue_date": date(2025, 5, 10),
                "amount": Decimal("8240.00"),
                "invoice_number": "000012777",
                "installments": [
                    {"numero": 1, "data_vencimento": "2025-06-10", "valor": "4120.00"},
                    {"numero": 2, "data_vencimento": "2025-07-10", "valor": "4120.00"},
                ],
            }
        return None

    def _amount_for(self, index: int, is_payable: bool) -> Decimal:
        base = Decimal("420.00") if is_payable else Decimal("950.00")
        variable = Decimal((index * 73) % 8500)
        cents = Decimal(index % 100) / Decimal("100")
        return base + variable + cents

    def _installments(self, index: int, issue_date: date, amount: Decimal) -> list[dict]:
        count = 1 + (index % 3)
        base = (amount / count).quantize(Decimal("0.01"))
        installments = []
        accumulated = Decimal("0.00")
        for number in range(1, count + 1):
            value = base if number < count else amount - accumulated
            accumulated += value
            installments.append(
                {
                    "numero": number,
                    "data_vencimento": (issue_date + timedelta(days=30 * number)).isoformat(),
                    "valor": str(value),
                }
            )
        return installments

    def _sync_installments(self, movement: MovimentoContas, installments: list[dict]) -> None:
        ParcelaContas.objects.filter(movimento=movement).delete()
        for item in installments:
            ParcelaContas.objects.create(
                movimento=movement,
                identificacao=f"{movement.numero_documento}-P{item['numero']}",
                numero=item["numero"],
                data_vencimento=date.fromisoformat(item["data_vencimento"]),
                valor=Decimal(item["valor"]),
                status_vencimento=due_status(date.fromisoformat(item["data_vencimento"])),
                ativo=True,
            )

    def _document_for(self, role: str, offset: int) -> str:
        if role == "fornecedor":
            base = f"34{offset:010d}"
            weights_first = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
            first_sum = sum(int(digit) * weight for digit, weight in zip(base, weights_first))
            first_digit = 0 if first_sum % 11 < 2 else 11 - (first_sum % 11)
            base_with_digit = f"{base}{first_digit}"
            weights_second = [6, *weights_first]
            second_sum = sum(int(digit) * weight for digit, weight in zip(base_with_digit, weights_second))
            second_digit = 0 if second_sum % 11 < 2 else 11 - (second_sum % 11)
            return f"{base}{first_digit}{second_digit}"

        base = f"12000000{offset:03d}"[-9:]
        first_sum = sum(int(base[index]) * (10 - index) for index in range(9))
        first_digit = 0 if first_sum % 11 < 2 else 11 - (first_sum % 11)
        base_with_digit = f"{base}{first_digit}"
        second_sum = sum(int(base_with_digit[index]) * (11 - index) for index in range(10))
        second_digit = 0 if second_sum % 11 < 2 else 11 - (second_sum % 11)
        return f"{base}{first_digit}{second_digit}"

    def _result_json(
        self,
        *,
        document_number: str,
        issue_date: date,
        supplier: Pessoa,
        billed: Pessoa,
        product: str,
        amount: Decimal,
        category: str,
        movement_type: str,
        installments: list[dict],
    ) -> dict:
        return {
            "numero_nota_fiscal": document_number,
            "data_emissao": issue_date.isoformat(),
            "natureza_operacao": "Compra demonstrativa" if movement_type == MovimentoContas.Tipo.APAGAR else "Venda demonstrativa",
            "fornecedor": {
                "razao_social": supplier.razao_social,
                "fantasia": supplier.nome_fantasia,
                "cnpj": supplier.cnpj,
                "municipio": supplier.municipio,
                "uf": supplier.uf,
            },
            "faturado": {
                "nome_completo": billed.razao_social,
                "cpf": billed.cpf,
                "municipio": billed.municipio,
                "uf": billed.uf,
            },
            "produtos": [{"descricao": product, "quantidade": 1}],
            "parcelas": installments,
            "valor_total": float(amount),
            "classificacoes_despesa": [
                {
                    "categoria": category,
                    "justificativa": "Classificação demonstrativa para carga de 200 PDFs.",
                }
            ],
            "movement_type": movement_type,
            "informacoes_complementares": "Registro demonstrativo equivalente a PDF processado.",
        }
