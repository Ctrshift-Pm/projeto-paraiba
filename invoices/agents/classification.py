from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


class ExpenseClassificationAgent:
    categories = [
        (
            "INSUMOS AGRICOLAS",
            [
                "semente",
                "sementes",
                "sementinha",
                "insumo",
                "insumos",
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
                "corretivos",
                "calcario",
                "calcário",
                "ureia",
                "nitrato",
                "fosfato",
                "potassio",
                "inoculante",
                "substrato",
                "muda",
                "mudas",
                "adubacao",
                "micronutriente",
                "biologico",
                "bioinsumo",
                "bioinsumos",
                "adubacao",
                "adubacao de plantio",
                "bioestimulante",
                "nucleo alimentar",
                "nucleo",
                "foliar",
                "adubo organico",
                "adubo químico",
                "defensivo agricola",
                "defensivos agricola",
                "fungicida",
                "fungicidas",
                "herbicida",
                "herbicidas",
                "inseticida",
                "inseticidas",
                "pesticida",
                "pesticidas",
                "biofertilizante",
                "corretivo",
                "corretivos",
                "calcario",
                "calcário",
                "ureia",
                "nitrato",
                "fosfato",
                "potassio",
                "inoculante",
                "micronutriente",
                "fertilizante agricola",
                "fertilizantes agricola",
                "adubos",
                "sementinha",
                "sementes",
            ],
        ),
        (
            "MANUTENCAO E OPERACAO",
            [
                "oleo diesel",
                "diesel",
                "diesel s10",
                "diesel s500",
                "combustivel",
                "combustível",
                "gasolina",
                "etanol",
                "aditivo",
                "lubrificante",
                "lubrificantes",
                "graxa",
                "peca",
                "peça",
                "pecas",
                "peças",
                "parafuso",
                "porca",
                "arruela",
                "rolamento",
                "pneu",
                "pneus",
                "camara de ar",
                "camara",
                "filtro",
                "filtros",
                "correia",
                "retentor",
                "engrenagem",
                "embreagem",
                "eixo",
                "ferramenta",
                "ferramentas",
                "motor",
                "bomba",
                "mangueira",
                "trator",
                "trator agricola",
                "colheitadeira",
                "pulverizador",
                "manutencao",
                "manutenção",
                "reparo",
                "reparos",
                "conserto",
                "consertos",
                "revisao",
                "revisão",
                "retificacao",
                "troca de oleo",
                "troca de filtro",
                "troca de oleo do motor",
                "filtro de ar",
                "filtro de oleo",
                "fluido hidraulico",
                "fluido hidráulico",
                "oleo hidraulico",
                "óleo hidráulico",
                "hidraulica",
                "hidráulica",
                "bomba de agua",
                "sistema hidraulico",
                "sistema hidráulico",
                "oficina mecanica",
                "oficina mecânica",
                "servico mecanico",
                "serviço mecânico",
            ],
        ),
        (
            "RECURSOS HUMANOS",
            [
                "mao de obra",
                "mão de obra",
                "salario",
                "salário",
                "encargo",
                "folha",
                "admissao",
                "admissão",
                "terceirizacao",
                "terceirização",
                "diaria",
                "diária",
                "adicional noturno",
                "beneficio",
                "benefícios",
                "fretamento",
                "encargos trabalhistas",
                "férias",
                "ferias",
                "13o salario",
                "decimo terceiro",
                "uniforme",
                "vale alimentacao",
                "vale alimentação",
                "vale transporte",
                "treinamento operacional",
            ],
        ),
        (
            "SERVICOS OPERACIONAIS",
            [
                "frete",
                "transporte",
                "colheita",
                "secagem",
                "armazenagem",
                "pulverizacao",
                "pulverização",
                "servico de transporte",
                "serviço de transporte",
                "servico operacional",
                "serviço operacional",
                "locacao de maquina",
                "locação de máquina",
                "terceirizacao agricola",
                "preparo de campo",
                "logistica",
                "frete internacional",
                "frete interno",
                "terceirizacao",
                "analise tecnica",
                "análise tecnica",
                "assistencia tecnica",
                "assistência técnica",
                "consultoria operacional",
                "serviço mecanico",
                "servicos mecanicos",
                "servico de colheita",
                "armazem",
                "processamento",
                "beneficiamento",
                "carga e descarga",
                "movimentacao de graos",
                "movimentação de grãos",
                "transbordo",
                "frete agricola",
                "frete agrícola",
            ],
        ),
        (
            "INFRAESTRUTURA E UTILIDADES",
            [
                "energia",
                "arrendamento",
                "construcao",
                "construção",
                "reforma",
                "cimento",
                "tijolo",
                "material de construcao",
                "material de construção",
                "material hidráulico",
                "material hidraulico",
                "hidraulico",
                "hidráulico",
                "hidraulica",
                "hidráulica",
                "cano",
                "tubo",
                "torneira",
                "conexao",
                "conexão",
                "canalizacao",
                "canalização",
                "placa de cimento",
                "cimento queimado",
                "energia",
                "agua",
                "água",
                "conta de luz",
                "conta de energia",
                "energia eletrica",
                "energia elétrica",
                "internet",
                "telefone",
                "telefonia",
                "cimento armado",
                "cimento simples",
                "poste",
                "cabo eletrico",
                "cabo elétrico",
                "disjuntor",
                "quadro de energia",
                "bomba submersa",
                "reservatorio",
                "reservatório",
                "caixa d agua",
                "caixa d'agua",
                "irrigacao",
                "irrigação",
                "aspersor",
                "gotejamento",
                "tubulacao",
                "tubulação",
            ],
        ),
        (
            "ADMINISTRATIVAS",
            [
                "honorario",
                "honorário",
                "contabil",
                "advocaticio",
                "advocati",
                "advocacia",
                "bancaria",
                "financeira",
                "papelaria",
                "consultoria",
                "cartorio",
                "cartório",
                "consultoria juridica",
                "consultoria jurídica",
                "advogado",
                "auditoria",
                "contabilidade",
                "material de escritorio",
                "material de escritório",
                "papelaria",
                "taxa bancária",
                "assessoria",
                "software",
                "licenca",
                "licença",
                "assinatura",
                "certificado digital",
                "servico de internet",
                "serviço de internet",
            ],
        ),
        (
            "SEGUROS E PROTECAO",
            [
                "seguro agricola",
                "seguro agrícola",
                "seguro de ativos",
                "seguro prestamista",
                "seguros",
                "seguradora",
                "protecao",
                "proteção",
                "rastreador",
                "rastreamento",
                "rastreador veicular",
                "alarme",
                "monitoramento",
                "vigia",
                "epi",
                "equipamento de protecao individual",
                "equipamento de proteção individual",
                "bota de seguranca",
                "bota de segurança",
                "luva de protecao",
                "luva de proteção",
                "capacete",
            ],
        ),
        (
            "IMPOSTOS E TAXAS",
            [
                "itr",
                "iptu",
                "ipva",
                "incra",
                "ccir",
                "taxa",
                "taxas",
                "tributo",
                "imposto",
                "emolumento",
                "iss",
                "icms",
                "pis",
                "cofins",
                "simples nacional",
                "fiscal",
                "juros",
                "multa",
                "taxa de licenciamento",
                "taxa cartorial",
                "emissao de guia",
                "emissão de guia",
            ],
        ),
        (
            "INVESTIMENTOS",
            [
                "maquina",
                "máquina",
                "implemento",
                "veiculo",
                "veículo",
                "imovel",
                "imóvel",
                "infraestrutura rural",
                "colheitadeira",
                "equipamento novo",
                "equipamentos novos",
                "terra",
                "terreno",
                "obra de ampliacao",
                "obra de ampliação",
                "tratores novos",
                "implementos novos",
                "aquisição de máquinas",
                "aquisição de veículos",
                "aquisição de imóveis",
            ],
        ),
        (
            "NUTRICAO E SAUDE ANIMAL",
            [
                "racao",
                "ração",
                "suplemento mineral",
                "nucleo proteico",
                "núcleo proteico",
                "sal mineral",
                "medicamento veterinario",
                "medicamento veterinário",
                "vacina animal",
                "vermifugo",
                "vermífugo",
                "nutricao animal",
                "nutrição animal",
                "concentrado animal",
                "premix",
            ],
        ),
        (
            "TECNOLOGIA E MONITORAMENTO",
            [
                "software agricola",
                "software agrícola",
                "telemetria",
                "gps agricola",
                "gps agrícola",
                "drone",
                "estacao meteorologica",
                "estação meteorológica",
                "sensor",
                "monitoramento remoto",
                "licenca de software",
                "licença de software",
                "assinatura digital",
                "plataforma de gestao",
                "plataforma de gestão",
            ],
        ),
        (
            "ARMAZENAGEM E POS-COLHEITA",
            [
                "silo",
                "secador",
                "beneficiadora",
                "moega",
                "armazenamento",
                "classificacao de graos",
                "classificação de grãos",
                "embalagem agricola",
                "embalagem agrícola",
                "big bag",
                "sacaria",
                "palete",
                "pallet",
                "pos colheita",
                "pós-colheita",
            ],
        ),
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

        category_scores: dict[str, int] = {}
        category_keywords: dict[str, set[str]] = {}
        category_samples: dict[str, list[str]] = {}

        for product_text in descriptions:
            normalized_product = self._normalize_text(product_text)
            if not normalized_product:
                continue
            for category, keywords in self.categories:
                for keyword in keywords:
                    score = self._match_score(normalized_product, self._normalize(keyword))
                    if score <= 0:
                        continue
                    category_scores[category] = category_scores.get(category, 0) + score
                    category_keywords.setdefault(category, set()).add(keyword)
                    category_samples.setdefault(category, [])
                    if product_text not in category_samples[category]:
                        category_samples[category].append(product_text)

        if not category_scores:
            return [self.default_category]

        category = self._select_best_category(category_scores)
        if not category:
            return [self.default_category]

        matched_keywords = sorted(category_keywords.get(category, set()))
        evidence_terms = ", ".join(matched_keywords[:3]) if matched_keywords else "classificacao textual"
        source_text = (category_samples.get(category) or descriptions)[0]
        return [
            {
                "categoria": category,
                "justificativa": f"Classificacao baseada nos termos identificados: {evidence_terms}. Origem: {self._shorten_text(source_text)}.",
            }
        ]

    def _select_best_category(self, scores: dict[str, int]) -> str | None:
        if not scores:
            return None

        top_score = max(scores.values())
        if top_score <= 0:
            return None

        tied = {category for category, score in scores.items() if score == top_score}
        if len(tied) == 1:
            return next(iter(tied))

        # Em caso de empate, seleciona ordem deterministica por total de palavras da categoria.
        return sorted(tied, key=lambda item: (len(item.split()), item))[0]

    def is_official_category(self, category: str) -> bool:
        return self._normalize(category) in self.official_categories

    def official_descriptions(self) -> list[str]:
        return [category for category, _ in self.categories]

    def has_only_official(self, classifications: list[dict]) -> bool:
        if not isinstance(classifications, list) or not classifications:
            return False
        for item in classifications:
            if not isinstance(item, dict):
                return False
            if not self.is_official_category(item.get("categoria", "")):
                return False
        return True

    def _match_score(self, normalized_text: str, normalized_keyword: str) -> int:
        if not normalized_text or not normalized_keyword:
            return 0
        if not normalized_keyword:
            return 0
        if " " in normalized_keyword:
            if normalized_keyword in normalized_text:
                words = len(normalized_keyword.split())
                return 100 + (words * 15)
            return 0

        tokens = set(normalized_text.split())
        if normalized_keyword in tokens:
            return 60
        if self._fuzzy_match_in_tokens(normalized_keyword, tokens):
            return 35
        return 0

    @classmethod
    def _fuzzy_match_in_tokens(cls, normalized_keyword: str, tokens: set[str]) -> bool:
        for token in tokens:
            if abs(len(token) - len(normalized_keyword)) > 2:
                continue
            if len(token) < 4:
                continue
            if SequenceMatcher(None, token, normalized_keyword).ratio() >= 0.85:
                return True
        return False

    @staticmethod
    def _normalize(value: str) -> str:
        value = value.lower()
        value = unicodedata.normalize("NFKD", value)
        return "".join(char for char in value if not unicodedata.combining(char))

    @classmethod
    def _normalize_text(cls, value: str) -> str:
        normalized = cls._normalize(value)
        normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
        return " ".join(normalized.split())

    @classmethod
    def _shorten_text(cls, value: str, limit: int = 90) -> str:
        value = (value or "").strip()
        if len(value) <= limit:
            return value
        return value[: limit - 3].strip() + "..."
