# Projeto Administrativo-Financeiro N2 - Etapa 1

Aplicacao web em Django para upload de PDF de nota fiscal, extração dos dados com Gemini ou mock local, classificação automatica de despesa e exibição do JSON na tela.

## Arquitetura com Agents

Esta etapa usa os agents do código no fluxo de extração:

- PdfExtractionAgent: percebe e processa o PDF enviado, com chamada ao Gemini para interpretação quando houver chave.
- ValidationAgent: valida o JSON extraido e normaliza no contrato esperado.
- ExpenseClassificationAgent: decide as classificações de despesa com base nos dados dos produtos.
- PersistenceAgent: persiste o resultado da extração no banco (sucesso ou erro).
- InvoiceExtractionService: orquestra os agents e retorna a estrutura final para a API.

Fluxo operacional alinhado ao PPTX:

1. Perceber: Usuário envia PDF em `POST /api/invoices/extract/`.
2. Processar e interpretar: PdfExtractionAgent interpreta o documento com Gemini ou fallback mock.
3. Decidir: ValidationAgent valida o JSON e ExpenseClassificationAgent adiciona classificacoes.
4. Agir: PersistenceAgent grava no banco e a resposta é retornada para a interface mostrar o JSON.

## Como rodar com Docker + PostgreSQL

```powershell
copy .env.example .env
# Abra o arquivo .env e preencha GEMINI_API_KEY
get-content .env
# Exemplo:
# GEMINI_API_KEY=seu_token_aqui
docker compose up --build
```

O servico PostgreSQL do container fica exposto em `localhost:5433` por padrão (via `POSTGRES_PORT` no `.env`).

Acesse `http://localhost:8000`.

## Como rodar localmente para desenvolvimento rápido

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Sem `DATABASE_URL`, o Django usa SQLite local para facilitar testes.

## Onde colocar GEMINI_API_KEY

- Copie `.env.example` para `.env`.
- Coloque o valor real em `GEMINI_API_KEY` dentro do `.env` para ativar Gemini.
- Para funcionar em modo mock/fallback, deixe `GEMINI_API_KEY` vazio.

## .env não deve ser commitado

NUNCA commite o arquivo `.env` no repositório.

## Testes Django

```powershell
python manage.py test invoices
```

Para forçar SQLite local durante testes com PostgreSQL configurado:

```powershell
$env:DATABASE_URL=''; python manage.py test invoices
```

Para validar o cenário de fallback com Gemini definido:

```powershell
python manage.py test invoices.tests.InvoiceExtractApiTests
```

### Validação de PDF real de referência

Para validar texto extraível com `pypdf` no PDF real indicado pelo critério, abra no ambiente com acesso ao arquivo:

`C:\Users\pmgam\Downloads\danfe (beltrano - insumos).pdf`

O teste não versiona esse PDF no repositório. Se o arquivo não estiver disponível, execute a verificação manualmente no seu ambiente local e registre o resultado.

## Testes E2E com Playwright

```powershell
npm install
npx playwright install
npm run test:e2e
```

Os testes de Playwright usam mocks/fallbacks e nao dependem de `GEMINI_API_KEY` real. Eles podem ser executados com `DATABASE_URL=""` e `GEMINI_API_KEY=""` em ambiente isolado.

### Notas de variaveis de ambiente

- O arquivo `.env` eh usado para execucao real/local.
- `GEMINI_API_KEY` deve ficar apenas no `.env`.
- Testes Django e Playwright usam mocks/fallbacks e nao exigem `GEMINI_API_KEY` real.
