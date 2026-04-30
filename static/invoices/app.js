const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#pdf-input");
const filePickerText = document.querySelector("#file-picker-text");
const selectedFile = document.querySelector("#selected-file");
const selectedFileName = document.querySelector("#selected-file-name");
const selectedFileSize = document.querySelector("#selected-file-size");
const extractButton = document.querySelector("#extract-button");
const resultPanel = document.querySelector("#result-panel");
const formattedView = document.querySelector("#formatted-view");
const jsonView = document.querySelector("#json-view");
const jsonOutput = document.querySelector("#json-output");
const copyButton = document.querySelector("#copy-button");
const toast = document.querySelector("#toast");

let latestJson = null;
let latestProvider = "IA local";

copyButton.disabled = true;

const EXTRACT_BUTTON_STATE = {
  EMPTY: "empty",
  READY: "ready",
  LOADING: "loading",
};

function setExtractButtonState(state) {
  switch (state) {
    case EXTRACT_BUTTON_STATE.READY:
      extractButton.disabled = false;
      extractButton.querySelector("span").textContent = "EXTRAIR DADOS";
      break;
    case EXTRACT_BUTTON_STATE.LOADING:
      extractButton.disabled = true;
      extractButton.querySelector("span").textContent = "EXTRAINDO...";
      break;
    case EXTRACT_BUTTON_STATE.EMPTY:
    default:
      extractButton.disabled = true;
      extractButton.querySelector("span").textContent = "EXTRAIR DADOS";
      break;
  }
}

function setNoFileState() {
  filePickerText.textContent = "Nenhum arquivo escolhido";
  selectedFile.hidden = true;
  setExtractButtonState(EXTRACT_BUTTON_STATE.EMPTY);
  copyButton.disabled = !latestJson;
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) {
    setNoFileState();
    return;
  }

  filePickerText.textContent = file.name;
  selectedFileName.textContent = file.name;
  selectedFileSize.textContent = formatBytes(file.size);
  selectedFile.hidden = false;
  setExtractButtonState(EXTRACT_BUTTON_STATE.READY);
  copyButton.disabled = !latestJson;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) {
    showToast("Selecione um arquivo PDF.");
    return;
  }

  const formData = new FormData();
  formData.append("pdf", file);

  setExtractButtonState(EXTRACT_BUTTON_STATE.LOADING);

  try {
    const response = await fetch("/api/invoices/extract/", {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken(),
      },
      body: formData,
    });

    const payload = await response.json();
    const extractedData = payload.data || payload;
    const provider = payload.provider || payload.source || latestProvider;
    latestProvider = provider || "IA local";

    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Falha ao extrair dados.");
    }

    latestJson = extractedData;
    renderFormatted(extractedData, latestProvider);
    jsonOutput.textContent = JSON.stringify(extractedData, null, 2);
    resultPanel.hidden = false;
    showTab("formatted");
    copyButton.disabled = false;
    showToast(`Dados extraídos com ${latestProvider}.`);
  } catch (error) {
    showToast(error.message);
  } finally {
    const currentFile = fileInput.files[0];
    if (currentFile) {
      setExtractButtonState(EXTRACT_BUTTON_STATE.READY);
    } else {
      setNoFileState();
    }
  }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});

copyButton.addEventListener("click", async () => {
  if (!latestJson) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(latestJson, null, 2));
    showToast("JSON copiado.");
  } catch {
    showToast("Não foi possível copiar o JSON.");
  }
});

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  formattedView.hidden = name !== "formatted";
  jsonView.hidden = name !== "json";
}

function renderFormatted(data, provider) {
  const fornecedor = data.fornecedor || {};
  const faturado = data.faturado || {};
  const produtos = data.produtos || [];
  const parcelas = data.parcelas || [];
  const classificacoes = data.classificacoes_despesa || [];

  formattedView.innerHTML = [
    section("Fornecedor", [
      ["Razão Social", fornecedor.razao_social],
      ["Fantasia", fornecedor.fantasia],
      ["CNPJ", fornecedor.cnpj],
    ]),
    section("Faturado", [
      ["Nome Completo", faturado.nome_completo],
      ["CPF", faturado.cpf],
    ]),
    section("Nota Fiscal", [
      ["Número", data.numero_nota_fiscal],
      ["Data de Emissão", data.data_emissao],
      ["Valor Total", currency(data.valor_total)],
      ["Origem", provider],
    ]),
    section("Produtos", produtos.map((item, index) => [
      `Produto ${index + 1}`,
      `${item.descricao || "-"}${item.quantidade ? ` - qtd. ${item.quantidade}` : ""}`,
    ])),
    section("Parcelas", parcelas.map((item) => [
      `Parcela ${item.numero || 1}`,
      `${item.data_vencimento || "-"} - ${currency(item.valor)}`,
    ])),
    section("Classificação", classificacoes.map((item) => [
      item.categoria || "Categoria",
      item.justificativa || "-",
    ])),
  ].join("");
}

function section(title, rows) {
  const renderedRows = rows
    .map(([label, value]) => `<div class="data-row"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value || "-")}</span></div>`)
    .join("");
  return `<article class="data-section"><h3>${escapeHtml(title)}</h3>${renderedRows}</article>`;
}

function getCsrfToken() {
  return document.querySelector("input[name='csrfmiddlewaretoken']").value;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function currency(value) {
  const number = Number(value || 0);
  return number.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

setNoFileState();
