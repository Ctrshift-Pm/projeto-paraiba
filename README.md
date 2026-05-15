# Projeto Administrativo-Financeiro N2

Aplicação web em Django para upload de PDF de nota fiscal, extração dos dados com Gemini ou mock local, classificação automatica de despesa e exibição do JSON na tela.

## Arquitetura com Agents

Esta etapa usa os agents do código no fluxo replanejado:

- PdfExtractionAgent: percebe e processa o PDF enviado, com chamada ao Gemini para interpretação quando houver chave.
- ValidationAgent: valida o JSON extraido e normaliza no contrato esperado.
- ExpenseClassificationAgent: decide as classificações de despesa com base nos dados dos produtos.
- PersistenceAgent: persiste o resultado da extração no banco (sucesso ou erro).
- InvoiceExtractionService: orquestra os agents e expõe os endpoints de extração, análise e lançamento.

Fluxo operacional replanejado:

1. Usuário envia PDF em `POST /api/invoices/extract/`.
2. O backend valida e persiste o JSON bruto no banco em `InvoiceExtraction`.
3. Usuário aciona `POST /api/invoices/analyze/<id>/` para inferir tipo de movimento e validar pessoas/classificações.
4. Usuário aciona `POST /api/invoices/launch/<id>/` para lançar as parcelas financeiras (se necessário, pode ser chamado direto após extração, pois o serviço recalcula a análise internamente).
5. A inferência de movimento pode retornar `APAGAR`, `ARECEBER` ou `MISTO`.

## Etapa 2 - Importação financeira

- O endpoint inicial é `POST /api/invoices/extract/`.
- Resposta da extração: retorna `success`, `id`, `provider`, `data` e `fallback_reason` se aplicável.
- Análise em `POST /api/invoices/analyze/<id>/` retorna:
  - `movement_type`
  - `analysis`
  - `metadata`
- Lançamento em `POST /api/invoices/launch/<id>/` retorna:
  - `success`
  - `movement_type`
  - `launch`
  - `message` em `launch`
- A inferência `MISTO` gera múltiplos movimentos e o lançamento será feito para cada bloco inferido.

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

## Comandos de validação da Etapa 2

```powershell
$env:DATABASE_URL=''
python manage.py test invoices
npm run test:e2e
docker compose config
```

Para forçar SQLite local durante testes com PostgreSQL configurado:

```powershell
$env:DATABASE_URL=''; python manage.py test invoices
```

Para validar cenário de fallback com Gemini definido:

```powershell
python manage.py test invoices.tests.InvoiceExtractApiTests
```

## Validação com PDFs reais de referência

A validação local de extração de texto com `pypdf` deve usar os arquivos abaixo:

- `danfe (beltrano - insumos).pdf`
- `danfe (materiais).pdf`
- `danfe (peças).pdf`

Os testes `PdfExtractionAgentTests.test_read_real_pdf_*_with_pypdf` percorrem esses arquivos.

## Testes E2E com Playwright

```powershell
npm install
npx playwright install
npm run test:e2e
```

Os testes de Playwright validam o fluxo em três passos: extração, análise e lançamento.

## Notas de variaveis de ambiente

- O arquivo `.env` eh usado para execucao real/local.
- `GEMINI_API_KEY` deve ficar apenas no `.env`.
- Testes Django e Playwright usam mocks/fallbacks e nao exigem `GEMINI_API_KEY` real.
