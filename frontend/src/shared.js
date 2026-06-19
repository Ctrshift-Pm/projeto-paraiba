export const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export function applyRemoteStyles() {
  const link = document.getElementById('app-css');
  if (link) {
    link.href = apiUrl('/static/invoices/styles.css');
  }
}

export function mapBackendRoute(path, fallback = '/index.html') {
  if (!path) return fallback;
  try {
    const url = new URL(path, window.location.origin);
    if (url.pathname === '/gemini/' || url.pathname === '/gemini') return `/gemini.html${url.search}`;
    if (url.pathname === '/rag/' || url.pathname === '/rag') return `/rag.html${url.search}`;
    if (url.pathname === '/cadastros/' || url.pathname === '/cadastros') return `/cadastros.html${url.search}`;
    if (url.pathname === '/' || url.pathname === '') return `/index.html${url.search}`;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function hasValue(value) {
  return value !== null && value !== undefined && value !== '';
}

export function currency(value) {
  if (!hasValue(value) || Number.isNaN(Number(value))) return '-';
  return Number(value).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

export function showToastFactory(element) {
  return (message) => {
    element.textContent = message;
    element.hidden = false;
    clearTimeout(showToastFactory.timeout);
    showToastFactory.timeout = setTimeout(() => { element.hidden = true; }, 3600);
  };
}
