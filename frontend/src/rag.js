import { applyRemoteStyles, apiUrl, mapBackendRoute, escapeHtml } from './shared.js';

applyRemoteStyles();

const form = document.querySelector('[data-rag-form]');
const queryInput = document.querySelector('#query');
const loading = document.querySelector('[data-rag-loading]');
const resultPanel = document.querySelector('[data-rag-result]');
const answer = document.querySelector('[data-rag-answer]');
const meta = document.querySelector('[data-rag-meta]');
const summary = document.querySelector('[data-rag-summary]');
const summaryBody = document.querySelector('[data-rag-summary-body]');
const exampleButtons = document.querySelectorAll('[data-question]');

exampleButtons.forEach((button) => {
  button.addEventListener('click', () => {
    queryInput.value = button.dataset.question || button.textContent.trim();
    queryInput.focus();
  });
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;

  loading.hidden = false;
  resultPanel.hidden = true;

  const formData = new FormData(form);
  formData.set('query', query);

  try {
    const response = await fetch(apiUrl('/rag/'), {
      method: 'POST',
      headers: { Accept: 'application/json' },
      body: formData,
    });
    const payload = await response.json();
    if (response.status === 401 || payload.redirect_to) {
      window.location.href = mapBackendRoute(payload.redirect_to, '/gemini.html?next=/rag.html');
      return;
    }
    if (!response.ok) {
      throw new Error(payload.error || 'Falha ao consultar o RAG.');
    }
    renderResult(payload.result || payload);
  } catch (error) {
    answer.textContent = error.message;
    resultPanel.hidden = false;
    summary.hidden = true;
  } finally {
    loading.hidden = true;
  }
});

function renderResult(result) {
  const usage = result.usage || {};
  const context = result.context_usage || {};
  meta.textContent = `Modo: ${result.mode || '-'} | Geração: ${result.provider || '-'}${usage.total_tokens ? ` | Tokens: ${usage.total_tokens} total (${usage.input_tokens || 0} entrada / ${usage.output_tokens || 0} saída)` : ''}${context.document_count ? ` | Contexto: ${context.document_count} fonte(s), ~${context.estimated_input_tokens || 0} tokens, ${context.status || '-'}` : ''}`;
  answer.innerHTML = escapeHtml(result.answer || 'Sem resposta.').replaceAll('\n', '<br>');
  const docs = Array.isArray(result.answer_documents) ? result.answer_documents : [];
  if (!docs.length) {
    summary.hidden = true;
    summaryBody.innerHTML = '';
  } else {
    summary.hidden = false;
    summaryBody.innerHTML = docs.map((doc) => `
      <tr>
        <td>${escapeHtml(doc.title || '-')}</td>
        <td>${escapeHtml(doc.source || '-')}</td>
        <td>${escapeHtml(doc.score ?? '-')}</td>
        <td>${escapeHtml(doc.score_status || '-')}</td>
        <td>${escapeHtml(Array.isArray(doc.rows) ? doc.rows.map((row) => `${row.campo}: ${row.valor}`).join(' | ') : doc.content || '-')}</td>
      </tr>
    `).join('');
  }
  resultPanel.hidden = false;
}
