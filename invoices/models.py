from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(ativo=True)


class SoftDeletableModel(TimeStampedModel):
    ativo = models.BooleanField(default=True, db_index=True)

    objects = models.Manager()
    ativos = ActiveManager()

    class Meta:
        abstract = True

    def inativar(self) -> None:
        if self.ativo:
            self.ativo = False
            self.save(update_fields=["ativo", "updated_at"])

    def reativar(self) -> None:
        if not self.ativo:
            self.ativo = True
            self.save(update_fields=["ativo", "updated_at"])


class Pessoa(SoftDeletableModel):
    razao_social = models.CharField(max_length=255)
    nome_fantasia = models.CharField(max_length=255, blank=True, default="")
    cpf = models.CharField(max_length=18, blank=True, null=True, unique=True)
    cnpj = models.CharField(max_length=18, blank=True, null=True, unique=True)
    inscricao_estadual = models.CharField(max_length=32, blank=True, default="")
    endereco = models.CharField(max_length=255, blank=True, default="")
    numero = models.CharField(max_length=32, blank=True, default="")
    bairro = models.CharField(max_length=128, blank=True, default="")
    municipio = models.CharField(max_length=128, blank=True, default="")
    uf = models.CharField(max_length=2, blank=True, default="")
    cep = models.CharField(max_length=16, blank=True, default="")
    telefone = models.CharField(max_length=32, blank=True, default="")
    is_cliente = models.BooleanField(default=False)
    is_fornecedor = models.BooleanField(default=False)
    is_faturado = models.BooleanField(default=False)

    class Meta:
        ordering = ["razao_social", "id"]

    def __str__(self) -> str:
        return self.razao_social


class Classificacao(SoftDeletableModel):
    class Tipo(models.TextChoices):
        DESPESA = "DESPESA", "Despesa"
        RECEITA = "RECEITA", "Receita"

    tipo = models.CharField(max_length=16, choices=Tipo.choices)
    descricao = models.CharField(max_length=255)

    class Meta:
        ordering = ["tipo", "descricao", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo", "descricao"],
                name="uniq_classificacao_tipo_descricao",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tipo}: {self.descricao}"


class MovimentoContas(SoftDeletableModel):
    class Tipo(models.TextChoices):
        APAGAR = "APAGAR", "Contas a pagar"
        ARECEBER = "ARECEBER", "Contas a receber"

    tipo = models.CharField(max_length=16, choices=Tipo.choices, db_index=True)
    pessoa = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        related_name="movimentos_principais",
    )
    faturado = models.ForeignKey(
        Pessoa,
        on_delete=models.PROTECT,
        related_name="movimentos_faturados",
    )
    numero_documento = models.CharField(max_length=64)
    data_emissao = models.DateField()
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    observacoes = models.TextField(blank=True, default="")
    dados_extraidos = models.JSONField(default=dict, blank=True)
    classificacoes = models.ManyToManyField(Classificacao, related_name="movimentos", blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo", "numero_documento"],
                name="uniq_movimento_tipo_numero_documento",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.numero_documento} ({self.tipo})"


class ParcelaContas(SoftDeletableModel):
    movimento = models.ForeignKey(MovimentoContas, on_delete=models.CASCADE, related_name="parcelas")
    identificacao = models.CharField(max_length=64, blank=True, default="")
    numero = models.PositiveIntegerField(default=1)
    data_vencimento = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["movimento_id", "numero", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["movimento", "numero"],
                name="uniq_parcela_movimento_numero",
            ),
            models.CheckConstraint(
                check=models.Q(numero__gte=1),
                name="chk_parcela_numero_positivo",
            ),
        ]

    def __str__(self) -> str:
        return self.identificacao or f"Parcela {self.numero}"


class InvoiceExtraction(TimeStampedModel):
    class Status(models.TextChoices):
        SUCCESS = "success", "Sucesso"
        ERROR = "error", "Erro"

    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    provider = models.CharField(max_length=32, default="mock")
    status = models.CharField(max_length=16, choices=Status.choices)
    result_json = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    movement_type = models.CharField(max_length=16, blank=True, default="")
    movimento = models.ForeignKey(
        MovimentoContas,
        on_delete=models.SET_NULL,
        related_name="extractions",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.status})"
