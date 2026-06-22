from __future__ import annotations

import unicodedata


class ExpenseClassificationAgent:
    categories = [
        (
            "INSUMOS AGRICOLAS",
            [
                "semente",
                "sementes",
                "fertilizante",
                "fertilizantes",
                "defensivo agricola",
                "defensivos agricola",
                "fertilizante agricola",
                "fertilizantes agricola",
                "defensivo",
                "fungicida",
                "fungicidas",
                "herbicida",
                "herbicidas",
                "inseticida",
                "inseticidas",
                "pesticida",
                "pesticidas",
                "adubo",
                "adubos",
                "corretivo",
            ],
        ),
        ("MANUTENCAO E OPERACAO", [
            "oleo diesel",
            "diesel",
            "combustivel",
            "combustível",
            "lubrificante",
            "peca",
            "parafuso",
            "pneu",
            "filtro",
            "correia",
            "ferramenta",
        ]),
        ("RECURSOS HUMANOS", ["mao de obra", "salario", "encargo", "folha", "admissao"]),
        (
            "SERVICOS OPERACIONAIS",
            ["frete", "transporte", "colheita", "secagem", "armazenagem", "pulverizacao", "pulverização"],
        ),
        (
            "INFRAESTRUTURA E UTILIDADES",
            ["energia", "arrendamento", "construcao", "construção", "reforma", "material de construcao", "material hidráulico", "material hidraulico", "hidraulico", "hidraulica"],
        ),
        ("ADMINISTRATIVAS", ["honorario", "contabil", "advocaticio", "advocati", "bancaria", "financeira", "taxa", "imposto"]),
        ("SEGUROS E PROTECAO", ["seguro agricola", "seguro de ativos", "seguro prestamista", "seguros"]),
        ("IMPOSTOS E TAXAS", ["itr", "iptu", "ipva", "incra", "ccir", "sustentabilidade", "taxa"]),
        ("INVESTIMENTOS", ["maquina", "implemento", "veiculo", "imovel", "infraestrutura rural"]),
    ]

    default_category = {
        "categoria": "ADMINISTRATIVAS",
        "justificativa": "Nao foi possivel identificar um padrao de categoria conhecido para os produtos informados.",
    }

    @property
    def official_categories(self) -> set[str]:
        return {self._normalize(category) for category, _ in self.categories}

    def classify(self, products: list[dict]) -> list[dict]:
        descriptions = [str(item.get("descricao", "")) for item in products if isinstance(item, dict)]
        if not descriptions:
            return [self.default_category]

        normalized = self._normalize(" ".join(descriptions))

        for category, keywords in self.categories:
            for keyword in keywords:
                if self._normalize(keyword) in normalized:
                    return [{"categoria": category, "justificativa": f"Produto relacionado a {keyword}."}]

        return [self.default_category]

    def is_official_category(self, category: str) -> bool:
        return self._normalize(category) in self.official_categories

    def has_only_official(self, classifications: list[dict]) -> bool:
        if not isinstance(classifications, list) or not classifications:
            return False
        for item in classifications:
            if not isinstance(item, dict):
                return False
            if not self.is_official_category(item.get("categoria", "")):
                return False
        return True

    @staticmethod
    def _normalize(value: str) -> str:
        value = value.lower()
        value = unicodedata.normalize("NFKD", value)
        return "".join(char for char in value if not unicodedata.combining(char))
