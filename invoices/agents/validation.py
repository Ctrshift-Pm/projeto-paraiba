from __future__ import annotations

class ValidationAgent:
    required_top_level = [
        "fornecedor",
        "faturado",
        "numero_nota_fiscal",
        "data_emissao",
        "produtos",
        "parcelas",
        "valor_total",
        "classificacoes_despesa",
    ]
    required_fornecedor_fields = ("razao_social", "fantasia", "cnpj")
    required_faturado_fields = ("nome_completo", "cpf")
    required_product_fields = ("descricao", "quantidade")
    required_installment_fields = ("numero", "data_vencimento", "valor")

    def normalize(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Resultado da extracao deve ser um objeto JSON.")

        normalized = {
            "fornecedor": self._normalize_nested(
                data.get("fornecedor"),
                {
                    "razao_social": ("razao_social",),
                    "fantasia": ("fantasia", "nome_fantasia"),
                    "cnpj": ("cnpj",),
                },
            ),
            "faturado": self._normalize_nested(
                data.get("faturado"),
                {
                    "nome_completo": ("nome_completo", "nome", "razao_social"),
                    "cpf": ("cpf", "cnpj"),
                },
            ),
            "numero_nota_fiscal": self._safe_str(data.get("numero_nota_fiscal") or data.get("numero")),
            "data_emissao": self._safe_str(data.get("data_emissao") or data.get("dataEmissao")),
            "produtos": self._normalize_products(data.get("produtos") or data.get("itens")),
            "parcelas": self._normalize_installments(data.get("parcelas")),
            "valor_total": self._number(data.get("valor_total") or data.get("valorTotal")),
            "classificacoes_despesa": self._normalize_classifications(
                data.get("classificacoes_despesa") or data.get("tipoDespesa") or data.get("despesas")
            ),
        }

        if not normalized["produtos"]:
            normalized["produtos"] = [{"descricao": "", "quantidade": 0.0}]
        if not normalized["parcelas"]:
            normalized["parcelas"] = [{"numero": 1, "data_vencimento": "", "valor": normalized["valor_total"]}]
        if not normalized["classificacoes_despesa"]:
            normalized["classificacoes_despesa"] = []

        self.validate(normalized)
        return normalized

    def validate(self, data: dict) -> None:
        missing = [field for field in self.required_top_level if field not in data]
        if missing:
            raise ValueError(f"Campos obrigatorios ausentes: {', '.join(missing)}")

        if not isinstance(data["produtos"], list):
            raise ValueError("Campo produtos deve ser uma lista.")
        if not isinstance(data["parcelas"], list):
            raise ValueError("Campo parcelas deve ser uma lista.")
        if not isinstance(data["classificacoes_despesa"], list):
            raise ValueError("Campo classificacoes_despesa deve ser uma lista.")

        for field in self.required_fornecedor_fields:
            if not isinstance(data["fornecedor"].get(field), str):
                raise ValueError("Campo fornecedor deve conter strings.")
            if not data["fornecedor"].get(field).strip():
                data["fornecedor"][field] = ""

        for field in self.required_faturado_fields:
            if not isinstance(data["faturado"].get(field), str):
                raise ValueError("Campo faturado deve conter strings.")
            if not data["faturado"].get(field).strip():
                data["faturado"][field] = ""

        for item in data["produtos"]:
            if not isinstance(item, dict):
                raise ValueError("Itens de produtos devem ser objetos.")
            for field in self.required_product_fields:
                if field not in item:
                    raise ValueError("Itens de produtos devem conter descricao e quantidade.")

        for item in data["parcelas"]:
            if not isinstance(item, dict):
                raise ValueError("Itens de parcelas devem ser objetos.")
            for field in self.required_installment_fields:
                if field not in item:
                    raise ValueError("Itens de parcelas devem conter numero, data_vencimento e valor.")

        for item in data["classificacoes_despesa"]:
            if not isinstance(item, dict) or "categoria" not in item or "justificativa" not in item:
                raise ValueError("Classificacoes de despesa devem conter categoria e justificativa.")

    def _normalize_nested(self, value, mapping: dict[str, tuple[str, ...]]) -> dict[str, str]:
        value = value if isinstance(value, dict) else {}
        normalized = {}
        for field, aliases in mapping.items():
            normalized[field] = self._safe_str(self._find_first(value, aliases))
        return normalized

    def _find_first(self, value: dict, aliases: tuple[str, ...]) -> str:
        for alias in aliases:
            if alias in value and value.get(alias) not in (None, ""):
                return str(value.get(alias))
        return ""

    def _normalize_products(self, value) -> list[dict]:
        if value is None:
            return []
        items = self._as_list(value)
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                normalized.append({"descricao": str(item), "quantidade": 0.0})
                continue
            normalized.append(
                {
                    "descricao": self._safe_str(item.get("descricao") or item.get("item")),
                    "quantidade": self._number(item.get("quantidade") or item.get("qtd") or item.get("qtd_item")),
                }
            )
        return normalized

    def _normalize_installments(self, value) -> list[dict]:
        if value is None:
            return []
        items = self._as_list(value)
        normalized: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "numero": self._integer(item.get("numero") or item.get("parcela") or 1, default=1),
                    "data_vencimento": self._safe_str(item.get("data_vencimento") or item.get("vencimento") or item.get("data")),
                    "valor": self._number(item.get("valor") or item.get("valor_total") or 0.0),
                }
            )
        return normalized

    def _normalize_classifications(self, value) -> list[dict]:
        items = self._as_list(value)
        if not items:
            return []
        normalized = []
        for item in items:
            if isinstance(item, str):
                normalized.append({"categoria": self._safe_str(item), "justificativa": ""})
                continue
            if isinstance(item, dict):
                normalized.append(
                    {
                        "categoria": self._safe_str(item.get("categoria") or item.get("classificacao") or item.get("category")),
                        "justificativa": self._safe_str(
                            item.get("justificativa") or item.get("motivo") or item.get("reason")
                        ),
                    }
                )
        return normalized

    def _as_list(self, value) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _safe_str(self, value) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _number(self, value) -> float:
        if value in (None, ""):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        cleaned = str(value).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def _integer(self, value, default: int = 0) -> int:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        cleaned = str(value).strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return default
