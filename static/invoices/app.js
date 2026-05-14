const form = document.querySelector("#upload-form");
const dropzone = document.querySelector("#dropzone");
const fileInput = document.querySelector("#pdf-input");
const selectedFile = document.querySelector("#selected-file");
const selectedFileName = document.querySelector("#selected-file-name");
const selectedFileSize = document.querySelector("#selected-file-size");
const extractButton = document.querySelector("#extract-button");
const resultPanel = document.querySelector("#result-panel");
const providerBadge = document.querySelector("#provider-badge");
const formattedView = document.querySelector("#formatted-view");
const jsonView = document.querySelector("#json-view");
const jsonOutput = document.querySelector("#json-output");
const copyButton = document.querySelector("#copy-button");
const toast = document.querySelector("#toast");

let latestJson = null;
let latestProvider = "";

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
      extractButton.querySelector("span").textContent = "Extrair Dados";
      break;
    case EXTRACT_BUTTON_STATE.LOADING:
      extractButton.disabled = true;
      extractButton.querySelector("span").textContent = "Extraindo...";
      break;
    case EXTRACT_BUTTON_STATE.EMPTY:
    default:
      extractButton.disabled = true;
      extractButton.querySelector("span").textContent = "Extrair Dados";
      break;
  }
}

function setNoFileState() {
  selectedFile.hidden = true;
  selectedFileName.textContent = "";
  selectedFileSize.textContent = "";
  setExtractButtonState(EXTRACT_BUTTON_STATE.EMPTY);
  copyButton.disabled = !latestJson;
}

function setFileState(file) {
  selectedFileName.textContent = file.name;
  selectedFileSize.textContent = formatBytes(file.size);
  selectedFile.hidden = false;
  setExtractButtonState(EXTRACT_BUTTON_STATE.READY);
  copyButton.disabled = !latestJson;
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) {
    setNoFileState();
    return;
  }

  setFileState(file);
});

["dragenter", "dragover"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
  });
});

dropzone.addEventListener("drop", (event) => {
  const file = Array.from(event.dataTransfer.files).find((item) => item.type === "application/pdf" || item.name.toLowerCase().endsWith(".pdf"));
  if (!file) {
    showToast("Envie um arquivo PDF.");
    return;
  }

  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  setFileState(file);
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

    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Falha ao extrair dados.");
    }

    const extractedData = payload.data || payload;
    const provider = payload.provider || payload.source || payload.fallback || "";
    latestJson = extractedData;
    latestProvider = provider;

    renderFormatted(extractedData);
    renderProvider(provider, payload.fallback_reason);
    jsonOutput.textContent = JSON.stringify(extractedData, null, 2);
    resultPanel.hidden = false;
    showTab("formatted");
    copyButton.disabled = false;
    showToast(provider ? `Dados extraídos via ${provider}.` : "Dados extraídos.");
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
  const isJson = name === "json";
  document.querySelectorAll(".tab").forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });

  formattedView.hidden = isJson;
  jsonView.hidden = !isJson;
}

function renderProvider(provider, fallbackReason = "") {
  if (!provider && !fallbackReason) {
    providerBadge.hidden = true;
    providerBadge.textContent = "";
    return;
  }

  providerBadge.textContent = fallbackReason ? `Origem: ${provider || "mock"} - ${fallbackReason}` : `Origem: ${provider}`;
  providerBadge.hidden = false;
}

function renderFormatted(data) {
  const fornecedor = data.fornecedor || {};
  const faturado = data.faturado || {};
  const localEntrega = data.local_entrega || {};
  const transportador = data.transportador || {};
  const produtos = Array.isArray(data.produtos) ? data.produtos : [];
  const parcelas = Array.isArray(data.parcelas) ? data.parcelas : [];
  const classificacoes = Array.isArray(data.classificacoes_despesa) ? data.classificacoes_despesa : [];

  const cards = [
    card("Fornecedor", [
      ["Razão Social", fornecedor.razao_social],
      ["Nome Fantasia", fornecedor.fantasia],
      ["CNPJ", fornecedor.cnpj],
      ["Inscrição Estadual", fornecedor.inscricao_estadual],
      ["Endereço", joinAddress(fornecedor)],
      ["Bairro", fornecedor.bairro],
      ["Município/UF", joinCityState(fornecedor)],
      ["CEP", fornecedor.cep],
      ["Telefone", fornecedor.telefone],
    ], "supplier"),
    card("Faturado", [
      ["Nome/Razão Social", faturado.nome_completo || faturado.razao_social],
      ["CPF", faturado.cpf],
      ["CNPJ", faturado.cnpj],
      ["Inscrição Estadual", faturado.inscricao_estadual],
      ["Endereço", joinAddress(faturado)],
      ["Bairro", faturado.bairro],
      ["Município/UF", joinCityState(faturado)],
      ["CEP", faturado.cep],
      ["Telefone", faturado.telefone],
    ], "billed", "yellow"),
    card("Nota Fiscal", [
      ["Número", data.numero_nota_fiscal],
      ["Série", data.serie],
      ["Chave de Acesso", data.chave_acesso],
      ["Natureza da Operação", data.natureza_operacao],
      ["Protocolo de Autorização", data.protocolo_autorizacao],
      ["Data de Emissão", data.data_emissao],
      ["Data Saída/Entrada", data.data_saida_entrada],
      ["Hora Saída", data.hora_saida],
      ["Vencimento", primaryDueDate(parcelas)],
      ["Valor Total", currency(data.valor_total)],
    ], "invoice"),
    card("Totais e Impostos", [
      ["Valor dos Produtos", currency(data.valor_produtos)],
      ["Valor do Frete", currency(data.valor_frete)],
      ["Desconto", currency(data.valor_desconto)],
      ["Seguro", currency(data.valor_seguro)],
      ["Outras Despesas", currency(data.outras_despesas)],
      ["Base ICMS", currency(data.base_calculo_icms)],
      ["Valor ICMS", currency(data.valor_icms)],
      ["Base ICMS ST", currency(data.base_calculo_icms_st)],
      ["Valor ICMS ST", currency(data.valor_icms_st)],
      ["Valor IPI", currency(data.valor_ipi)],
      ["Valor PIS", currency(data.valor_pis)],
      ["Valor COFINS", currency(data.valor_cofins)],
    ], "totals"),
    productsCard(produtos),
    installmentsCard(parcelas),
    classificationCard(classificacoes),
  ];

  if (hasAnyObjectValue(localEntrega)) {
    cards.splice(4, 0, card("Local de Entrega", [
      ["Nome/Razão Social", localEntrega.nome_razao_social],
      ["CPF/CNPJ", localEntrega.cpf_cnpj],
      ["Inscrição Estadual", localEntrega.inscricao_estadual],
      ["Endereço", joinAddress(localEntrega)],
      ["Bairro", localEntrega.bairro],
      ["Município/UF", joinCityState(localEntrega)],
      ["CEP", localEntrega.cep],
      ["Telefone", localEntrega.telefone],
    ], "delivery", "green"));
  }

  if (hasAnyObjectValue(transportador)) {
    cards.splice(5, 0, card("Transportador/Volumes", [
      ["Razão Social", transportador.razao_social],
      ["CPF/CNPJ", transportador.cpf_cnpj],
      ["Inscrição Estadual", transportador.inscricao_estadual],
      ["Endereço", transportador.endereco],
      ["Município/UF", joinCityState(transportador)],
      ["Placa", transportador.placa_veiculo],
      ["Frete por Conta", transportador.frete_por_conta],
      ["Quantidade", transportador.quantidade],
      ["Espécie", transportador.especie],
      ["Peso Bruto", transportador.peso_bruto],
      ["Peso Líquido", transportador.peso_liquido],
    ], "carrier", "rose"));
  }

  if (hasValue(data.informacoes_complementares)) {
    cards.push(card("Informações Complementares", [
      ["Descrição", data.informacoes_complementares],
    ], "additional", "dark"));
  }

  formattedView.innerHTML = cards.join("");
}

function card(title, rows, className = "", bar = "") {
  const renderedRows = rows
    .map(([label, value]) => dataRow(label, value))
    .join("");
  const cardBars = {
    yellow: "var(--yellow-bar)",
    green: "var(--green-bar)",
    rose: "var(--rose-bar)",
    dark: "var(--dark-bar)",
  };
  const style = cardBars[bar] ? ` style="--card-bar: ${cardBars[bar]}"` : "";

  return `
    <article class="data-card ${escapeHtml(className)}"${style}>
      <div class="data-card-header"><h3>${escapeHtml(title)}</h3></div>
      <div class="data-card-body"><dl class="data-grid">${renderedRows}</dl></div>
    </article>
  `;
}

function productsCard(produtos) {
  const rows = produtos.map((item, index) => `
    <tr>
      <td>${escapeHtml(index + 1)}</td>
      <td>${escapeHtml(item.codigo || "-")}</td>
      <td>${escapeHtml(item.descricao || item.nome || "-")}</td>
      <td>${escapeHtml(item.ncm || "-")}</td>
      <td>${escapeHtml(item.cst || "-")}</td>
      <td>${escapeHtml(item.cfop || "-")}</td>
      <td>${escapeHtml(item.quantidade ?? "-")}</td>
      <td>${escapeHtml(item.unidade || "-")}</td>
      <td>${escapeHtml(currency(item.valor_unitario))}</td>
      <td>${escapeHtml(currency(item.valor_total ?? item.total))}</td>
    </tr>
  `).join("");

  return tableCard("Produtos/Serviços", "products", ["#", "Código", "Descrição", "NCM", "CST", "CFOP", "Qtd.", "Unidade", "Valor Unit.", "Total"], rows);
}

function installmentsCard(parcelas) {
  const rows = parcelas.map((item) => `
    <tr>
      <td>${escapeHtml(item.numero ?? "-")}</td>
      <td>${escapeHtml(item.descricao || "-")}</td>
      <td>${escapeHtml(dueDateLabel(item.data_vencimento))}</td>
      <td>${escapeHtml(currency(item.valor))}</td>
    </tr>
  `).join("");

  return tableCard("Parcelas", "installments", ["Parcela", "Documento", "Vencimento", "Valor"], rows);
}

function classificationCard(classificacoes) {
  const rows = classificacoes.map((item) => `
    <tr>
      <td>${escapeHtml(item.categoria || "-")}</td>
      <td>${escapeHtml(item.justificativa || "-")}</td>
    </tr>
  `).join("");

  return tableCard("Classificação de Despesa", "classification", ["Categoria", "Justificativa"], rows);
}

function tableCard(title, className, headers, rows) {
  const headerCells = headers.map((header) => `<th scope="col">${escapeHtml(header)}</th>`).join("");
  const body = rows || `<tr><td colspan="${headers.length}">Nenhum dado encontrado.</td></tr>`;

  return `
    <article class="data-card ${escapeHtml(className)}">
      <div class="data-card-header"><h3>${escapeHtml(title)}</h3></div>
      <div class="data-card-body">
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr>${headerCells}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </div>
    </article>
  `;
}

function dataRow(label, value) {
  return `
    <div class="data-row">
      <dt>${escapeHtml(label)}</dt>
      <dd>${escapeHtml(hasValue(value) ? value : "-")}</dd>
    </div>
  `;
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
  if (!hasValue(value) || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function hasValue(value) {
  return value !== null && value !== undefined && value !== "";
}

function hasAnyObjectValue(value) {
  if (!value || typeof value !== "object") return false;
  return Object.values(value).some((item) => hasValue(item));
}

function primaryDueDate(parcelas) {
  const firstWithDueDate = parcelas.find((item) => hasValue(item?.data_vencimento));
  return dueDateLabel(firstWithDueDate?.data_vencimento);
}

function dueDateLabel(value) {
  return hasValue(value) ? value : "Não tem";
}

function joinAddress(value) {
  return [value.endereco, value.numero].filter(hasValue).join(", ");
}

function joinCityState(value) {
  return [value.municipio, value.uf].filter(hasValue).join(" / ");
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
