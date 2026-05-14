# Contrato de Dados

## JSON de Saida

```json
{
  "fornecedor": {
    "razao_social": "EMPRESA FORNECEDORA LTDA",
    "fantasia": "FORNECEDORA",
    "cnpj": "12.345.678/0001-90"
  },
  "faturado": {
    "nome_completo": "CLIENTE EXEMPLO",
    "cpf": "123.456.789-00"
  },
  "numero_nota_fiscal": "000123456",
  "data_emissao": "2024-01-15",
  "produtos": [
    {
      "descricao": "Oleo Diesel S10",
      "quantidade": 100
    }
  ],
  "parcelas": [
    {
      "numero": 1,
      "data_vencimento": "2024-02-15",
      "valor": 1500.0
    }
  ],
  "valor_total": 1500.0,
  "classificacoes_despesa": [
    {
      "categoria": "MANUTENCAO E OPERACAO",
      "justificativa": "Produto relacionado a combustiveis e lubrificantes."
    }
  ]
}
```

## Regras de conformidade (Etapa 1)

- Campos obrigatorios: `fornecedor`, `faturado`, `numero_nota_fiscal`, `data_emissao`, `produtos`, `parcelas`, `valor_total`, `classificacoes_despesa`.
- Campos importantes de DANFE/NF-e devem ser preservados quando existirem: `serie`, `chave_acesso`, `natureza_operacao`, `protocolo_autorizacao`, datas/horarios, totais/impostos, `local_entrega`, `transportador` e `informacoes_complementares`.
- `fornecedor` e `faturado` podem incluir dados cadastrais adicionais: inscrição estadual, endereço, número, bairro, município, UF, CEP e telefone.
- Listas sempre devem ser retornadas para `produtos`, `parcelas` e `classificacoes_despesa` (mesmo com apenas 1 elemento).
- Tipo mínimo de cada item em `produtos`: `{"descricao": string, "quantidade": number}`; quando houver, preservar também código, NCM, CST/CSOSN, CFOP, unidade, valor unitário e valor total.
- Tipo mínimo de cada item em `parcelas`: `{"numero": number, "data_vencimento": string, "valor": number}`; quando houver, preservar também documento/descrição da duplicata.
- Tipo de cada item em `classificacoes_despesa`: `{"categoria": string, "justificativa": string}`.
- Para `GEMINI_API_KEY` ausente ou falha diagnosticável do Gemini: retornar mock local compatível com o mesmo contrato.
- Quando houver fallback mock, a resposta da API pode incluir metadado seguro `fallback_reason` fora do JSON de dados extraídos. Esse campo não deve conter segredos e deve indicar ausência de chave, chave inválida, modelo indisponível, resposta não JSON ou outro erro diagnosticável.
- O Gemini deve ser instruido a retornar somente JSON valido, sem texto extra.
- A saída final em `classificacoes_despesa` deve sempre utilizar categorias oficiais abaixo.

## Categorias de Despesa

- INSUMOS AGRICOLAS: sementes, fertilizantes, defensivos agricolas, corretivos.
- MANUTENCAO E OPERACAO: combustiveis, lubrificantes, pecas, componentes mecanicos, pneus, filtros, ferramentas.
- RECURSOS HUMANOS: mao de obra temporaria, salarios e encargos.
- SERVICOS OPERACIONAIS: frete, transporte, colheita terceirizada, secagem, armazenagem, pulverizacao.
- INFRAESTRUTURA E UTILIDADES: energia eletrica, arrendamento, construcoes, reformas, material de construcao, material hidraulico.
- ADMINISTRATIVAS: honorarios, despesas bancarias e financeiras.
- SEGUROS E PROTECAO: seguro agricola, seguro de ativos, seguro prestamista.
- IMPOSTOS E TAXAS: ITR, IPTU, IPVA, INCRA-CCIR.
- INVESTIMENTOS: maquinas, implementos, veiculos, imoveis, infraestrutura rural.

## Exemplos

- Compra de Oleo Diesel: `MANUTENCAO E OPERACAO`.
- Compra de Material Hidraulico: `INFRAESTRUTURA E UTILIDADES`.

## Orquestração da classificação de despesa

- O Gemini pode retornar `classificacoes_despesa` dentro do contrato, mas só é preservada se:
  - `categoria` e `justificativa` estiverem presentes e não vazias;
  - `categoria` for uma das categorias oficiais listadas neste documento.
- Quando a classificação vier ausente, inválida ou fora da lista oficial, `InvoiceExtractionService` chama `ExpenseClassificationAgent` para classificar novamente com base nos produtos e retorna só categoria oficial.

## Palavras-chave de validação para INSUMOS AGRICOLAS

- `fungicida`, `herbicida`, `inseticida`, `pesticida`, `defensivo agricola`, `fertilizante`, `adubo`, `sementes`, além de `semente`.

## Ciclo de agentes (PPTX)

- Perceber: `PdfExtractionAgent`
- Processar/Interpretar: `ValidationAgent`
- Decidir: `ExpenseClassificationAgent`
- Agir: `PersistenceAgent`

Este ciclo também aparece no `InvoiceExtractionService`, que orquestra:
`extrair -> normalizar -> decidir classificação -> persistir`.
