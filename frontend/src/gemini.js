import { applyRemoteStyles, apiUrl } from './shared.js';

applyRemoteStyles();

const form = document.querySelector('[data-gate-form]');
const errorBox = document.querySelector('[data-gate-error]');
const successBox = document.querySelector('[data-gate-success]');
const nextField = form.querySelector('input[name="next"]');
const params = new URLSearchParams(window.location.search);
const nextPath = params.get('next') || nextField.value || '/index.html';
const message = params.get('error') || '';
nextField.value = nextPath;

if (message) {
  showMessage(errorBox, message);
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  hideMessage(errorBox);
  hideMessage(successBox);

  const formData = new FormData(form);
  formData.set('next', nextPath);

  const response = await fetch(apiUrl('/gemini/'), {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: formData,
  });
  const payload = await response.json();

  if (!response.ok) {
    showMessage(errorBox, payload.error || 'Chave do Gemini invalida. Passe uma chave valida.');
    return;
  }

  showMessage(successBox, 'Chave ativa. Redirecionando...');
  window.location.href = payload.next_path || nextPath;
});

function showMessage(element, text) {
  element.textContent = text;
  element.hidden = false;
}

function hideMessage(element) {
  element.hidden = true;
  element.textContent = '';
}
