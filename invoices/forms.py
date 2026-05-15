from __future__ import annotations

from django import forms

from .models import Classificacao, Pessoa


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
        value = (value or "").strip()
        return value or None

    def clean_cnpj(self):
        value = self.cleaned_data.get("cnpj")
        value = (value or "").strip()
        return value or None


class ClassificacaoForm(forms.ModelForm):
    class Meta:
        model = Classificacao
        fields = ["tipo", "descricao", "ativo"]
