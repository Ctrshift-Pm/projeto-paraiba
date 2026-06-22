const peopleOptions = JSON.parse(document.getElementById("people-options").textContent);
const classificationOptions = JSON.parse(document.getElementById("classification-options").textContent);
const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]").value;

const state = {
  resource: "",
  filterKind: "",
  filterValue: "",
  order: "",
  rows: [],
  hasSearched: false,
  lastQuery: "",
  editingId: null,
};

function redirectToGeminiGate(payload = {}) {
  window.location.href = payload.redirect_to || "/gemini/";
}

const resources = {
  contas: {
    label: "Conta",
    columns: [
      ["nome_documento", "Nome do documento"],
      ["tipo_label", "Tipo"],
      ["pessoa", "Pessoa"],
      ["faturado", "Faturado"],
      ["data_emissao", "Emissão"],
      ["valor_total", "Valor"],
      ["classificacoes", "Classificação"],
    ],
    fields: [
      { name: "tipo", label: "Tipo", type: "select", options: [["APAGAR", "Contas a pagar"], ["ARECEBER", "Contas a receber"]], required: true },
      { name: "pessoa_id", label: "Pessoa", type: "select", options: () => peopleOptions.map((item) => [item.id, item.label]), required: true },
      { name: "faturado_id", label: "Faturado", type: "select", options: () => peopleOptions.map((item) => [item.id, item.label]), required: true },
      { name: "data_emissao", label: "Data de emissão", type: "date", min: "1910-01-01", max: todayISO(), required: true },
      { name: "valor_total", label: "Valor total", type: "currency", required: true },
      { name: "classificacao_ids", label: "Classificações", type: "multiselect", options: () => classificationOptions.map((item) => [item.id, item.label]), required: true },
      { name: "observacoes", label: "Observações", type: "textarea" },
    ],
  },
  pessoas: {
    label: "Pessoa",
    columns: [
      ["razao_social", "Razão social"],
      ["roles", "Papéis"],
      ["cpf", "CPF"],
      ["cnpj", "CNPJ"],
      ["municipio", "Município"],
      ["uf", "UF"],
      ["telefone", "Telefone"],
    ],
    fields: [
      { name: "razao_social", label: "Razão social", type: "text", required: true },
      { name: "nome_fantasia", label: "Nome fantasia", type: "text" },
      { name: "cpf", label: "CPF", type: "text", mask: "cpf", maxlength: 14 },
      { name: "cnpj", label: "CNPJ", type: "text", mask: "cnpj", maxlength: 18 },
      { name: "inscricao_estadual", label: "Inscrição estadual", type: "text", mask: "ie" },
      { name: "telefone", label: "Telefone", type: "text", mask: "phone" },
      { name: "endereco", label: "Endereço", type: "text" },
      { name: "numero", label: "Número", type: "text", mask: "digits" },
      { name: "bairro", label: "Bairro", type: "text" },
      { name: "municipio", label: "Município", type: "text" },
      { name: "uf", label: "UF", type: "text", maxlength: 2 },
      { name: "cep", label: "CEP", type: "text", mask: "cep", maxlength: 9 },
      { name: "papel", label: "Papel", type: "radio", options: [["is_fornecedor", "Fornecedor"], ["is_cliente", "Cliente"], ["is_faturado", "Faturado"]], required: true },
    ],
  },
  classificacoes: {
    label: "Classificação",
    columns: [
      ["tipo_label", "Tipo"],
      ["descricao", "Descrição"],
    ],
    fields: [
      { name: "tipo", label: "Tipo", type: "select", options: [["DESPESA", "Despesa"], ["RECEITA", "Receita"]], required: true },
      { name: "descricao", label: "Descrição", type: "text", required: true },
    ],
  },
};

const resourceLabels = {
  contas: "Contas",
  pessoas: "Pessoas",
  classificacoes: "Classificações",
};

const filterLabels = {
  pessoas: {
    fornecedor: "Fornecedores",
    cliente: "Clientes",
    faturado: "Faturados",
  },
  classificacoes: {
    receita: "Classificação - Receitas",
    despesa: "Classificação - Despesas",
  },
};

const tableHead = document.getElementById("manage-table-head");
const tableBody = document.getElementById("manage-table-body");
const searchInput = document.getElementById("manage-search");
const allButton = document.getElementById("manage-all-button");
const dialog = document.getElementById("manage-dialog");
const form = document.getElementById("manage-form");
const formFields = document.getElementById("manage-form-fields");
const formError = document.getElementById("manage-form-error");
const dialogTitle = document.getElementById("manage-dialog-title");
const saveButton = document.getElementById("manage-save-button");
const resourceTitle = document.getElementById("manage-resource-title");
const heroTitle = document.getElementById("manage-title");
const heroDescription = document.getElementById("manage-description");
const resultCount = document.getElementById("manage-result-count");
const toast = document.getElementById("toast");

document.querySelectorAll(".manage-tab").forEach((button) => {
  button.addEventListener("click", () => {
    setView(button.dataset.resource, button.dataset.filterKind || "", button.dataset.filterValue || "");
  });
});

document.querySelectorAll("[data-resource]").forEach((button) => {
  if (button.classList.contains("manage-tab")) return;
  button.addEventListener("click", () => {
    setView(button.dataset.resource, button.dataset.filterKind || "", button.dataset.filterValue || "");
  });
});

document.getElementById("manage-search-button").addEventListener("click", () => loadRows());
allButton.addEventListener("click", () => {
  if (!state.resource) {
    showToast("Selecione uma seção antes de buscar.");
    return;
  }
  searchInput.value = "";
  loadRows(true);
});
document.getElementById("manage-new-button").addEventListener("click", () => openForm());
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => dialog.close()));
searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadRows();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!isFormComplete()) {
    updateSaveState();
    showToast("Complete os campos obrigatórios antes de salvar.");
    return;
  }
  const payload = formPayload();
  const url = state.editingId ? `/api/cadastros/${state.resource}/${state.editingId}/` : `/api/cadastros/${state.resource}/`;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
    body: JSON.stringify(payload),
  });
  const data = await readJsonResponse(response);
  if (response.status === 401 || data.redirect_to) {
    redirectToGeminiGate(data);
    return;
  }
  if (!response.ok) {
    showFormError(data.error || "Não foi possível salvar.");
    return;
  }
  clearFormError();
  syncOptions(data.record);
  dialog.close();
  showToast("Registro salvo.");
  loadRows();
});

function renderTable() {
  const definition = resources[state.resource];
  if (!definition) {
    heroTitle.textContent = "Cadastros";
    heroDescription.textContent = "Selecione uma seção para carregar registros.";
    resourceTitle.textContent = "Registros";
    searchInput.placeholder = "Selecione uma seção...";
    tableHead.innerHTML = "";
    tableBody.innerHTML = '<tr><td class="empty-table" colspan="1">Escolha uma seção na navegação lateral.</td></tr>';
    return;
  }
  heroTitle.textContent = currentHeroTitle();
  heroDescription.textContent = currentHeroDescription();
  resourceTitle.textContent = currentPluralLabel();
  searchInput.placeholder = searchPlaceholder();
  const headers = definition.columns
    .map(([key, label]) => `<th scope="col"><button type="button" data-sort="${key}">${label}</button></th>`)
    .join("");
  tableHead.innerHTML = `<tr>${headers}<th scope="col">Ações</th></tr>`;
  tableHead.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      state.order = state.order === key ? `-${key}` : key;
      state.rows = sortRows(state.rows, state.order);
      renderBody();
    });
  });
  renderBody();
}

function renderBody() {
  const definition = resources[state.resource];
  resultCount.textContent = state.rows.length
    ? `${state.rows.length} registro(s) carregado(s)`
    : state.hasSearched
      ? "0 registro(s) encontrado(s)"
      : "Aguardando busca";
  if (!state.rows.length) {
    tableBody.innerHTML = `<tr><td class="empty-table" colspan="${definition.columns.length + 1}">${emptyTableMessage()}</td></tr>`;
    return;
  }
  tableBody.innerHTML = state.rows
    .map((row) => {
      const cells = definition.columns.map(([key, label]) => `<td>${cellValue(row, key, label)}</td>`).join("");
      return `<tr>${cells}<td class="row-actions"><button class="row-action edit" type="button" data-edit="${row.id}">Editar</button><button class="row-action delete" type="button" data-delete="${row.id}">Excluir</button></td></tr>`;
    })
    .join("");
  tableBody.querySelectorAll("[data-edit]").forEach((button) => button.addEventListener("click", () => openForm(Number(button.dataset.edit))));
  tableBody.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", () => deleteRow(Number(button.dataset.delete))));
}

async function loadRows(forceAll = false) {
  if (!state.resource) {
    showToast("Selecione uma seção antes de carregar registros.");
    return;
  }
  const params = new URLSearchParams();
  const query = searchInput.value.trim();
  state.hasSearched = true;
  state.lastQuery = query;
  appendActiveFilter(params, forceAll);
  if (query) params.set("q", query);
  else if (forceAll || !hasActiveFilter()) params.set("all", "1");
  const response = await fetch(`/api/cadastros/${state.resource}/?${params.toString()}`);
  const data = await readJsonResponse(response);
  if (response.status === 401 || data.redirect_to) {
    redirectToGeminiGate(data);
    return;
  }
  state.rows = sortRows(data.results || [], state.order);
  renderTable();
}

function emptyTableMessage() {
  if (!state.hasSearched) {
    return "Use Buscar para carregar registros ativos.";
  }
  if (state.lastQuery) {
    return `Nenhum registro encontrado para "${escapeHtml(state.lastQuery)}".`;
  }
  return "Nenhum registro ativo encontrado.";
}

function setView(resource, filterKind = "", filterValue = "") {
  state.resource = resource;
  state.filterKind = filterKind;
  state.filterValue = filterValue;
  state.order = "";
  state.rows = [];
  state.hasSearched = false;
  state.lastQuery = "";
  state.editingId = null;
  searchInput.value = "";
  updateActiveNav();
  renderTable();
  loadRows(false);
}

function currentPluralLabel() {
  if (!state.resource) return "Registros";
  if (state.resource === "pessoas" && state.filterKind === "role" && state.filterValue) {
    return filterLabels.pessoas[state.filterValue] || "Pessoas";
  }
  if (state.resource === "classificacoes" && state.filterKind === "tipo" && state.filterValue) {
    return filterLabels.classificacoes[state.filterValue] || "Classificações";
  }
  return resourceLabels[state.resource] || "Registros";
}

function searchPlaceholder() {
  if (!state.resource) return "Selecione uma seção...";
  if (state.resource === "pessoas") return "Buscar por nome, CPF ou CNPJ...";
  if (state.resource === "classificacoes") return "Buscar por descrição...";
  return "Buscar registros ativos...";
}

function currentHeroTitle() {
  if (!state.resource) return "Cadastros";
  if (state.resource === "pessoas" && state.filterKind === "role" && state.filterValue) {
    return currentPluralLabel();
  }
  if (state.resource === "classificacoes" && state.filterKind === "tipo" && state.filterValue) {
    return currentPluralLabel();
  }
  return resourceLabels[state.resource] || "Registros";
}

function currentHeroDescription() {
  if (!state.resource) {
    return "Selecione uma seção para carregar registros.";
  }
  if (state.resource === "pessoas" && state.filterKind === "role" && state.filterValue) {
    return `Gerencie ${currentPluralLabel().toLowerCase()}.`;
  }
  if (state.resource === "classificacoes" && state.filterKind === "tipo" && state.filterValue) {
    return `Gerencie ${currentPluralLabel().toLowerCase()}.`;
  }
  if (state.resource === "pessoas") {
    return "Gerencie as pessoas cadastradas.";
  }
  if (state.resource === "classificacoes") {
    return "Gerencie as classificações cadastradas.";
  }
  return "Gerencie contas com busca, ordenação e exclusão lógica.";
}

function updateActiveNav() {
  document.querySelectorAll("[data-resource]").forEach((button) => {
    const isActive = button.dataset.resource === state.resource
      && (button.dataset.filterKind || "") === state.filterKind
      && (button.dataset.filterValue || "") === state.filterValue;
    button.classList.toggle("active", isActive);
  });
}

function hasActiveFilter() {
  return Boolean((state.resource === "pessoas" && state.filterKind === "role" && state.filterValue)
    || (state.resource === "classificacoes" && state.filterKind === "tipo" && state.filterValue));
}

function appendActiveFilter(params, forceAll) {
  if (forceAll) return;
  if (state.resource === "pessoas" && state.filterKind === "role" && state.filterValue) {
    params.set("role", state.filterValue);
  }
  if (state.resource === "classificacoes" && state.filterKind === "tipo" && state.filterValue) {
    params.set("tipo", state.filterValue);
  }
}

async function openForm(id = null) {
  if (!state.resource) {
    showToast("Selecione uma seção antes de inserir registros.");
    return;
  }
  state.editingId = id;
  const row = id ? await fetchRecord(id) : {};
  dialogTitle.textContent = `${id ? "Editar" : "Novo"} ${resources[state.resource].label}`;
  formFields.innerHTML = resources[state.resource].fields.map((field) => fieldHtml(field, row)).join("");
  clearFormError();
  configureFormControls();
  updateSaveState();
  dialog.showModal();
}

async function fetchRecord(id) {
  const response = await fetch(`/api/cadastros/${state.resource}/${id}/`);
  const data = await readJsonResponse(response);
  if (response.status === 401 || data.redirect_to) {
    redirectToGeminiGate(data);
    return {};
  }
  return data.record || {};
}

async function deleteRow(id) {
  if (!window.confirm("Inativar este registro?")) return;
  const response = await fetch(`/api/cadastros/${state.resource}/${id}/`, {
    method: "DELETE",
    headers: { "X-CSRFToken": csrfToken },
  });
  const data = await readJsonResponse(response);
  if (response.status === 401 || data.redirect_to) {
    redirectToGeminiGate(data);
    return;
  }
  if (!response.ok) {
    showToast(data.error || "Não foi possível excluir.");
    return;
  }
  showToast("Registro inativado.");
  removeOption(id);
  state.rows = state.rows.filter((item) => item.id !== id);
  renderBody();
}

function fieldHtml(field, row) {
  const value = row[field.name] ?? "";
  const required = field.required ? "required" : "";
  if (field.type === "textarea") {
    return `<label>${field.label}<textarea name="${field.name}" ${required}>${escapeHtml(value)}</textarea></label>`;
  }
  if (field.type === "select") {
    const options = optionPairs(field).map(([optionValue, label]) => `<option value="${optionValue}" ${String(value) === String(optionValue) ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
    return `<label>${field.label}<select name="${field.name}" ${required}>${options}</select></label>`;
  }
  if (field.type === "multiselect") {
    const selected = new Set((row[field.name] || []).map(String));
    const options = optionPairs(field).map(([optionValue, label]) => `<option value="${optionValue}" ${selected.has(String(optionValue)) ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
    return `<label>${field.label}<select name="${field.name}" multiple>${options}</select></label>`;
  }
  if (field.type === "checkbox") {
    return `<label class="checkbox-field"><input type="checkbox" name="${field.name}" ${value ? "checked" : ""}>${field.label}</label>`;
  }
  if (field.type === "radio") {
    const selected = optionPairs(field).find(([optionValue]) => row[optionValue])?.[0] || "";
    const options = optionPairs(field)
      .map(([optionValue, label]) => `<label class="checkbox-field"><input type="radio" name="${field.name}" value="${optionValue}" ${selected === optionValue ? "checked" : ""} ${required}>${escapeHtml(label)}</label>`)
      .join("");
    return `<fieldset class="radio-field"><legend>${field.label}</legend>${options}</fieldset>`;
  }
  if (field.type === "currency") {
    return `<label>${field.label}<input name="${field.name}" type="text" inputmode="numeric" value="${escapeHtml(formatCurrencyInput(value))}" ${required} data-currency="true" placeholder="R$ 0,00"></label>`;
  }
  return `<label>${field.label}<input name="${field.name}" type="${field.type}" value="${escapeHtml(value)}" ${required} ${field.min ? `min="${field.min}"` : ""} ${field.max ? `max="${field.max}"` : ""} ${field.step ? `step="${field.step}"` : ""} ${field.maxlength ? `maxlength="${field.maxlength}"` : ""} ${field.mask ? `data-mask="${field.mask}"` : ""}></label>`;
}

function formPayload() {
  const payload = {};
  resources[state.resource].fields.forEach((field) => {
    const control = form.elements[field.name];
    if (!control) return;
    if (field.type === "checkbox") {
      payload[field.name] = control.checked;
    } else if (field.type === "radio") {
      const checked = form.querySelector(`input[name="${field.name}"]:checked`);
      optionPairs(field).forEach(([optionValue]) => {
        payload[optionValue] = checked?.value === optionValue;
      });
    } else if (field.type === "multiselect") {
      payload[field.name] = Array.from(control.selectedOptions).map((option) => option.value);
    } else if (field.type === "currency") {
      payload[field.name] = normalizeCurrency(control.value);
    } else {
      payload[field.name] = control.value;
    }
  });
  return payload;
}

function configureFormControls() {
  form.querySelectorAll("input, select, textarea").forEach((control) => {
    control.addEventListener("input", updateSaveState);
    control.addEventListener("change", updateSaveState);
  });
  form.querySelectorAll("[data-currency='true']").forEach((control) => {
    control.addEventListener("input", () => {
      control.value = currencyMask(control.value);
      updateSaveState();
    });
    control.value = currencyMask(control.value);
  });
  form.querySelectorAll("[data-mask]").forEach((control) => {
    const apply = () => {
      control.value = maskValue(control.value, control.dataset.mask);
      updateSaveState();
    };
    control.addEventListener("input", apply);
    apply();
  });
}

function isFormComplete() {
  const requiredControls = Array.from(form.querySelectorAll("[required]"));
  const requiredOk = requiredControls.every((control) => {
    if (control.dataset.currency === "true") return currencyCents(control.value) > 0 && currencyCents(control.value) <= 99999999999900;
    return control.checkValidity() && String(control.value || "").trim() !== "";
  });
  return requiredOk && resourceSpecificFormComplete() && form.checkValidity();
}

function resourceSpecificFormComplete() {
  if (state.resource === "pessoas") {
    const hasDocument = String(form.elements.cpf?.value || "").trim() || String(form.elements.cnpj?.value || "").trim();
    const hasRole = Boolean(form.querySelector('input[name="papel"]:checked'));
    return Boolean(hasDocument && hasRole);
  }
  if (state.resource === "contas") {
    return Array.from(form.elements.classificacao_ids?.selectedOptions || []).length > 0;
  }
  return true;
}

function maskValue(value, mask) {
  const raw = String(value || "").toUpperCase();
  const digits = raw.replace(/\D/g, "");
  if (mask === "digits") return digits;
  if (mask === "cep") return digits.slice(0, 8).replace(/^(\d{5})(\d)/, "$1-$2");
  if (mask === "ie") {
    return digits.slice(0, 32).replace(/(\d{3})(?=\d)/g, "$1.");
  }
  if (mask === "phone") {
    const limited = digits.slice(0, 11);
    if (limited.length === 11) return limited.replace(/^(\d{2})(\d{5})(\d{4})$/, "($1) $2-$3");
    if (limited.length === 10) return limited.replace(/^(\d{2})(\d{4})(\d{4})$/, "($1) $2-$3");
    return limited;
  }
  if (mask === "cpf") {
    return digits
      .slice(0, 11)
      .replace(/^(\d{3})(\d)/, "$1.$2")
      .replace(/^(\d{3})\.(\d{3})(\d)/, "$1.$2.$3")
      .replace(/^(\d{3})\.(\d{3})\.(\d{3})(\d)/, "$1.$2.$3-$4");
  }
  if (mask === "cnpj") {
    const chars = raw.replace(/[^0-9A-Z]/g, "").slice(0, 14);
    return chars
      .replace(/^(.{2})(.)/, "$1.$2")
      .replace(/^(.{2}\..{3})(.)/, "$1.$2")
      .replace(/^(.{2}\..{3}\..{3})(.)/, "$1/$2")
      .replace(/^(.{2}\..{3}\..{3}\/.{4})(.)/, "$1-$2");
  }
  return value;
}

function updateSaveState() {
  saveButton.disabled = !isFormComplete();
}

function currencyMask(value) {
  const cents = Math.min(currencyCents(value), 99999999999900);
  const integerPart = Math.floor(cents / 100).toLocaleString("pt-BR");
  const centsPart = String(cents % 100).padStart(2, "0");
  return `R$ ${integerPart},${centsPart}`;
}

function currencyCents(value) {
  const digits = String(value || "").replace(/\D/g, "");
  if (!digits) return 0;
  return Number(digits.slice(0, 14));
}

function normalizeCurrency(value) {
  const cents = currencyCents(value);
  const reais = Math.floor(cents / 100);
  const centavos = String(cents % 100).padStart(2, "0");
  return `${reais}.${centavos}`;
}

function formatCurrencyInput(value) {
  if (!value) return "";
  const normalized = String(value).replace(/[^\d.,]/g, "");
  if (normalized.includes(",")) return currencyMask(normalized);
  const [integer = "0", decimal = ""] = normalized.split(".");
  return currencyMask(`${integer}${decimal.padEnd(2, "0").slice(0, 2)}`);
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function optionPairs(field) {
  return typeof field.options === "function" ? field.options() : field.options;
}

function sortRows(rows, order) {
  if (!order) return [...rows];
  const direction = order.startsWith("-") ? -1 : 1;
  const key = order.replace("-", "");
  return [...rows].sort((a, b) => String(a[key] || "").localeCompare(String(b[key] || ""), "pt-BR", { numeric: true }) * direction);
}

function pluralLabel() {
  return currentPluralLabel();
}

function cellValue(row, key, label) {
  const value = row[key];
  const display = value === undefined || value === null || String(value).trim() === "" ? `Sem ${String(label || key).toLowerCase()}` : value;
  if (key === "tipo_label") {
    const tone = row.tipo === "ARECEBER" || row.tipo === "RECEITA" ? "income" : "expense";
    return `<span class="manage-badge ${tone}">${escapeHtml(display)}</span>`;
  }
  if (key === "classificacoes" && !String(display).startsWith("Sem ")) {
    return `<span class="manage-badge neutral">${escapeHtml(display)}</span>`;
  }
  if (key === "valor_total") {
    return `<span class="money-cell">R$ ${escapeHtml(display)}</span>`;
  }
  return escapeHtml(display);
}

function syncOptions(record) {
  if (!record) return;
  if (state.resource === "pessoas") {
    const option = { id: record.id, label: record.razao_social };
    const index = peopleOptions.findIndex((item) => item.id === record.id);
    if (index >= 0) peopleOptions[index] = option;
    else peopleOptions.push(option);
    peopleOptions.sort((a, b) => a.label.localeCompare(b.label, "pt-BR"));
  }
  if (state.resource === "classificacoes") {
    const option = { id: record.id, label: `${record.tipo_label} - ${record.descricao}`, tipo: record.tipo };
    const index = classificationOptions.findIndex((item) => item.id === record.id);
    if (index >= 0) classificationOptions[index] = option;
    else classificationOptions.push(option);
    classificationOptions.sort((a, b) => a.label.localeCompare(b.label, "pt-BR"));
  }
}

function removeOption(id) {
  if (state.resource === "pessoas") {
    const index = peopleOptions.findIndex((item) => item.id === id);
    if (index >= 0) peopleOptions.splice(index, 1);
  }
  if (state.resource === "classificacoes") {
    const index = classificationOptions.findIndex((item) => item.id === id);
    if (index >= 0) classificationOptions.splice(index, 1);
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

function showFormError(message) {
  if (!formError) {
    showToast(message);
    return;
  }
  formError.textContent = message;
  formError.hidden = false;
  showToast(message);
}

function clearFormError() {
  if (!formError) return;
  formError.textContent = "";
  formError.hidden = true;
}

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_error) {
    return { error: text };
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[char]));
}

updateActiveNav();
renderTable();
