from __future__ import annotations

from django import forms

from .models import Classificacao, Pessoa
from .utils import only_alnum, only_digits


class PessoaForm(forms.ModelForm):
    class Meta:
        model = Pessoa
        fields = [
            "razao_social",
            "nome_fantasia",
            "cpf",
            "cnpj",
            "inscricao_estadual",
            "endereco",
            "numero",
            "bairro",
            "municipio",
            "uf",
            "cep",
            "telefone",
            "ativo",
        ]

    def clean_cpf(self):
        value = self.cleaned_data.get("cpf")
        value = only_digits(value)
        return value or None

    def clean_cnpj(self):
        value = self.cleaned_data.get("cnpj")
        value = only_alnum(value)
        return value or None


class ClassificacaoForm(forms.ModelForm):
    class Meta:
        model = Classificacao
        fields = ["tipo", "descricao", "ativo"]
