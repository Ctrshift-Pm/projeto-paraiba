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

## Consulta RAG do banco de dados

A tela `GET /rag/` permite digitar perguntas sobre os dados persistidos no banco, como notas fiscais importadas, pessoas, classificações, movimentos e parcelas.

Modos disponíveis:

- RAG simples: recupera registros por correspondência textual entre a pergunta e os dados do banco.
- RAG embeddings: vetoriza localmente a pergunta e os registros, compara por similaridade de cosseno e injeta as fontes mais próximas no prompt.
- Para perguntas financeiras como total, soma, quantidade, média, maior/menor movimento, saldo, contas a pagar e contas a receber, o `Agent3` cria fontes analíticas calculadas diretamente pelo ORM. Assim, os dois modos usam os mesmos totais autoritativos e diferem apenas na recuperação das evidências complementares.

Fluxo:

1. Usuário faz uma pergunta na tela `/rag/`.
2. `Agent3` recupera fontes relevantes do banco.
3. As fontes são injetadas em um prompt enriquecido.
4. Gemini gera a resposta quando `GEMINI_API_KEY` está configurada.
5. Sem chave Gemini, o app retorna uma resposta local baseada nas fontes recuperadas.

Otimização de tokens no RAG:

- Perguntas financeiras agregadas usam primeiro cálculos do ORM/SQL.
- Perguntas com filtros explícitos, como ano, CPF/CNPJ, faturado, fornecedor, vencimento e classificação, são filtradas antes da chamada ao Gemini.
- Perguntas semânticas usam recuperação por similaridade textual/embeddings e recebem fontes já resumidas.
- O Gemini recebe fontes `Analitico:` prioritárias e poucas evidências complementares, para redigir a resposta sem recalcular valores financeiros.
- Meta operacional: até 1.500 tokens de entrada por pergunta; até 2.500 é aceitável.


## Separacao para deploy

A publicacao agora fica dividida assim:

- PythonAnywhere: backend Python/Django original, incluindo extração, persistência e RAG.
- Render: proxy backend em JavaScript, em `backend-js/`, com a variavel `PYTHON_BACKEND_URL` apontando para o Django publicado.
- Vercel: frontend JavaScript, em `frontend/`, com `VITE_API_BASE_URL` apontando para o Render.

O frontend Vercel chama apenas o backend do Render. O Render repassa as requisições para o Django publicado em PythonAnywhere.

Comandos do frontend:

```powershell
cd frontend
npm install
npm run build
```

Comandos do backend proxy:

```powershell
cd backend-js
node server.mjs
```

## Etapa 4 - Interface e hospedagem

A tela `GET /cadastros/` atende aos cadastros administrativo-financeiros da 4ª etapa:

- Manter contas: contas a pagar e contas a receber, com pessoa, faturado, data, valor e classificações.
- Manter pessoas: fornecedor, cliente e faturado no mesmo cadastro, marcados por papéis.
- Manter classificação: receita e despesa.

Regras implementadas na interface:

- A tabela inicia vazia.
- `Buscar` carrega registros por um ou mais termos.
- `Todos ativos` carrega somente registros com `ativo=True`.
- Os cabeçalhos da tabela ordenam os registros carregados por coluna.
- Cada linha tem as ações `Editar` e `Excluir`.
- `Excluir` faz exclusão lógica, alterando `ativo=False`.
- O campo de status não aparece no create/update; novos registros entram como ativos e updates não mudam o status.

Endpoints JSON usados pela tela:

```text
GET  /api/cadastros/<pessoas|classificacoes|contas>/
POST /api/cadastros/<pessoas|classificacoes|contas>/
GET  /api/cadastros/<pessoas|classificacoes|contas>/<id>/
POST /api/cadastros/<pessoas|classificacoes|contas>/<id>/
DELETE /api/cadastros/<pessoas|classificacoes|contas>/<id>/
```

Para gerar os 200 registros de navegação e RAG:

```powershell
python manage.py seed_demo_invoices --count 200
```

### Entrega

- GitHub: publicar este repositório sem o arquivo `.env`.
- Servidor: publicar o Django em PythonAnywhere ou serviço equivalente com PostgreSQL configurado por `DATABASE_URL`.
- Frontend separado em Vercel só faz sentido se o projeto for dividido em outro app; hoje a interface está no próprio Django.
- Produção com Django:
  - `DJANGO_DEBUG=0`
  - `DJANGO_SECRET_KEY` definido no painel do host
  - `DJANGO_ALLOWED_HOSTS` com o domínio publicado
  - `python manage.py collectstatic --noinput`
  - `gunicorn config.wsgi:application`
- Docker: `docker compose up --build` sobe web + PostgreSQL localmente.
- Login e senha: a aplicação usa a tela interna de acesso com `admin` / `admin` por padrão. Se quiser trocar, configure `DOCEXTRACT_ADMIN_USER` e `DOCEXTRACT_ADMIN_PASSWORD` no `.env`.
- Chave LLM: não commitar `GEMINI_API_KEY`. Em produção, configurar a variável no painel do servidor. Se estiver vazia, extração usa fallback/mock e RAG usa resposta local quando possível.

## Debug de tokens na extração de PDF

Quando a extração usa Gemini, a resposta de `POST /api/invoices/extract/` inclui `metadata.usage` com:

- `input_tokens`
- `output_tokens`
- `total_tokens`

A interface mostra esse consumo junto da origem da extração. Em modo `mock`, esse bloco não aparece.

O limite de saída da extração é configurado por:

```powershell
GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS=8192
```

Use essa métrica para comparar PDFs reais antes de reduzir prompt, trocar modelo ou cortar partes do texto extraído.

## Carga demonstrativa com 200 PDFs processados

Para popular o banco com registros equivalentes a PDFs ja processados, use:

```powershell
python manage.py seed_demo_invoices --count 200
```

O comando cria dados com prefixo `DEMO-` em extrações, movimentos e parcelas, sem depender de arquivos PDF reais. Ele e idempotente: se ja existirem 200 extrações demonstrativas, nao duplica registros.

Para recriar apenas a carga demonstrativa:

```powershell
python manage.py seed_demo_invoices --count 200 --reset
```

O `--reset` remove somente dados demonstrativos com prefixo `DEMO-`.

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
- Para economizar limite, use modelos separados:
  - `GEMINI_EXTRACTION_MODEL=gemini-2.5-flash`
  - `GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS=8192`
  - `GEMINI_RAG_MODEL=gemini-2.5-flash-lite`
  - `GEMINI_RAG_MAX_OUTPUT_TOKENS=900`
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
