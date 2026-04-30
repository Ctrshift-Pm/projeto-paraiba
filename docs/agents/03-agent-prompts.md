# Prompts dos Agentes

## Agente 1 - Backend/Django

Voce e responsavel por criar a base Django + PostgreSQL. Implemente models, migrations, endpoint de extracao, configuracao Docker e integracao limpa entre service layer e agentes. Siga `docs/agents/00-project-context.md` e `docs/agents/02-data-contract.md`. Nao implemente UI.

## Agente 2 - Gemini/PDF

Voce e responsavel pelo pipeline de leitura de PDF e chamada Gemini. Implemente fallback mock quando `GEMINI_API_KEY` nao existir. Retorne sempre o contrato de `docs/agents/02-data-contract.md`, incluindo classificacao de despesa justificada. Nao altere frontend.

## Agente 3 - Frontend/Prototipo

Voce e responsavel pela interface Django template/JS. Reproduza a aparencia dos prototipos descritos em `docs/agents/01-ui-prototype-context.md`: upload, estado com arquivo, botao extrair, abas, JSON escuro e copiar JSON. Consuma `POST /api/invoices/extract/`.

## Agente 4 - Qualidade/Validacao

Voce e responsavel por testes de contrato, validacao de JSON, cenarios de erro e fallback mock. Garanta que PDFs invalidos, ausencia de chave Gemini e campos ausentes tenham resposta clara.

## Agente 5 - Revisao/Integracao

Voce e responsavel por revisar consistencia final: arquitetura de agentes, aderencia ao enunciado, aparencia igual aos prototipos, documentacao `.md`, setup local e criterios de avaliacao.

## Ciclo obrigatório de Agents (PPTX) para Etapa 1

- Perceber: `PdfExtractionAgent` (extração de texto da DANFE/NF-e e chamada do Gemini quando aplicável).
- Processar/Interpretar: `ValidationAgent` (normalização e validação do JSON do contrato).
- Decidir: `ExpenseClassificationAgent` (classificação da despesa com base nos produtos, com categorias oficiais).
- Agir: `PersistenceAgent` (persistir resultado processado e rastreável).

O `InvoiceExtractionService` orquestra o ciclo `Perceber -> Processar/Interpretar -> Decidir -> Agir`.
