# Contexto do Projeto

## Objetivo

Implementar a Etapa 1 do projeto administrativo-financeiro N2: uma aplicacao web que recebe PDF de nota fiscal, extrai dados de contas a pagar com Gemini ou fallback mock, classifica a despesa e mostra o JSON ao usuario.

## Stack

- Backend: Django.
- Banco: PostgreSQL via Docker Compose.
- Desenvolvimento local: SQLite permitido quando `DATABASE_URL` nao estiver definido.
- LLM: Gemini quando `GEMINI_API_KEY` existir; mock local quando nao existir.
- Frontend: templates Django, CSS e JavaScript simples.

## Escopo da Etapa 1

- Upload de PDF.
- Botao para extrair dados.
- Agentes para extracao, classificacao, validacao e persistencia.
- JSON exibido na tela.
- Classificacao de despesa interpretada a partir dos produtos da nota fiscal.

## Ciclo de Agentes exigido pelo PPTX

Sequencia operacional obrigatória no backend:

- Perceber: `PdfExtractionAgent`
  - receber o arquivo PDF
  - extrair texto da nota
  - disparar interpretação com Gemini quando disponível, com fallback local quando não disponível
- Processar/Interpretar: `PdfExtractionAgent` + `ValidationAgent`
  - converter o texto extraído em estrutura JSON do contrato
  - normalizar chaves e tipos esperados
- Decidir: `ExpenseClassificationAgent`
  - inferir `classificacoes_despesa` a partir de `produtos`
  - garantir que somente categorias oficiais sejam usadas
- Agir: `PersistenceAgent`
  - persistir o resultado processado para trilha de auditoria e retorno da extração

O `InvoiceExtractionService` orquestra esse fluxo em sequência explícita:
`Usuario -> PdfExtractionAgent -> ValidationAgent -> ExpenseClassificationAgent (fallback/validação de decisão) -> PersistenceAgent`.

## Regras do Enunciado

- O JSON deve conter fornecedor, faturado, numero da nota fiscal, data de emissao, produtos, parcelas, valor total e classificacoes de despesa.
- Produtos nao precisam virar entidade propria.
- Nesta etapa ha uma parcela e uma classificacao principal, mas o formato deve aceitar listas.
- A classificacao de despesa nao e extraida literalmente: ela deve ser interpretada.

## Criterios de Qualidade

- Demonstrar estrutura de agentes.
- Retornar JSON completo e consistente.
- Classificar despesas com justificativa.
- Interface com a mesma aparencia geral dos prototipos.
