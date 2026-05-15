const form = document.querySelector("#upload-form");
const dropzone = document.querySelector("#dropzone");
const fileInput = document.querySelector("#pdf-input");
const selectedFile = document.querySelector("#selected-file");
const selectedFileName = document.querySelector("#selected-file-name");
const selectedFileSize = document.querySelector("#selected-file-size");
const extractButton = document.querySelector("#extract-button");
const analyzeButton = document.querySelector("#analyze-button");
const resultPanel = document.querySelector("#result-panel");
const providerBadge = document.querySelector("#provider-badge");
const formattedView = document.querySelector("#formatted-view");
const jsonView = document.querySelector("#json-view");
const jsonOutput = document.querySelector("#json-output");
const copyButton = document.querySelector("#copy-button");
const analysisSection = document.querySelector("#analysis-section");
const analysisSummary = document.querySelector("#analysis-summary");
const analysisGrid = document.querySelector("#analysis-grid");
const launchSection = document.querySelector("#launch-section");
const launchMessage = document.querySelector("#launch-message");
const launchGrid = document.querySelector("#launch-grid");
const toast = document.querySelector("#toast");

let latestExtractionId = null;
let latestExtractionPayload = null;
let latestAnalysisPayload = null;
let latestLaunchPayload = null;
let latestJson = null;

copyButton.disabled = true;
analyzeButton.disabled = true;
analyzeButton.hidden = true;

const EXTRACT_BUTTON_STATE = {
  EMPTY: "empty",
  READY: "ready",
  LOADING: "loading",
};

const ANALYZE_BUTTON_STATE = {
  READY: "ready",
  LOADING: "loading",
  DISABLED: "disabled",
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

function setAnalyzeButtonState(state) {
  switch (state) {
    case ANALYZE_BUTTON_STATE.READY:
      analyzeButton.hidden = false;
      analyzeButton.disabled = false;
      analyzeButton.textContent = "Analisar Dados";
      break;
    case ANALYZE_BUTTON_STATE.LOADING:
      analyzeButton.disabled = true;
      analyzeButton.textContent = "Analisando...";
      break;
    case ANALYZE_BUTTON_STATE.DISABLED:
    default:
      analyzeButton.disabled = true;
      analyzeButton.hidden = true;
      break;
  }
}

function setNoFileState() {
  selectedFile.hidden = true;
  selectedFileName.textContent = "";
  selectedFileSize.textContent = "";
  setExtractButtonState(EXTRACT_BUTTON_STATE.EMPTY);
  setAnalyzeButtonState(ANALYZE_BUTTON_STATE.DISABLED);
  clearAnalysisState(true);
  clearLaunchState(true);
}

function setFileState(file) {
  selectedFileName.textContent = file.name;
  selectedFileSize.textContent = formatBytes(file.size);
  selectedFile.hidden = false;
  setExtractButtonState(EXTRACT_BUTTON_STATE.READY);
}

function clearAnalysisState(resetActions = false) {
  analysisSection.hidden = true;
  analysisSummary.hidden = true;
  analysisSummary.textContent = "";
  analysisGrid.innerHTML = "";
  latestAnalysisPayload = null;
  if (resetActions) {
    latestExtractionId = null;
    latestExtractionPayload = null;
    latestLaunchPayload = null;
  }
}

function clearLaunchState(resetActions = false) {
  launchSection.hidden = true;
  launchMessage.textContent = "";
  launchGrid.innerHTML = "";
  if (resetActions) {
    latestLaunchPayload = null;
  }
}

function updateRawJson(payload) {
  latestJson = payload;
  jsonOutput.textContent = payload ? JSON.stringify(payload, null, 2) : "";
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
  setAnalyzeButtonState(ANALYZE_BUTTON_STATE.DISABLED);
  clearAnalysisState();
  clearLaunchState();

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

    latestExtractionPayload = payload;
    latestExtractionId = payload.id || null;

    if (!latestExtractionId) {
      throw new Error("Resposta de extracao sem identificador.");
    }

    updateRawJson(payload);
    const extractedData = payload.data || payload;
    renderFormatted(extractedData);
    renderProvider(payload.provider, payload.fallback_reason);

    resultPanel.hidden = false;
    analysisSection.hidden = true;
    launchSection.hidden = true;
    showTab("formatted");
    clearAnalysisState();
    clearLaunchState();
    setAnalyzeButtonState(ANALYZE_BUTTON_STATE.READY);

    copyButton.disabled = false;
    showToast(payload.provider ? `Dados extraídos via ${payload.provider}.` : "Dados extraídos.");
  } catch (error) {
    showToast(error.message);
    setAnalyzeButtonState(ANALYZE_BUTTON_STATE.DISABLED);
    if (latestExtractionId === null) {
      clearAnalysisState(true);
      clearLaunchState(true);
      resultPanel.hidden = true;
    }
  } finally {
    if (fileInput.files[0]) {
      setExtractButtonState(EXTRACT_BUTTON_STATE.READY);
    } else {
      setNoFileState();
    }
  }
});

analyzeButton.addEventListener("click", async () => {
  if (!latestExtractionId) {
    showToast("Faça a extração antes de analisar.");
    return;
  }

  setAnalyzeButtonState(ANALYZE_BUTTON_STATE.LOADING);

  try {
    const response = await fetch(`/api/invoices/analyze/${latestExtractionId}/`, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken(),
      },
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Falha ao analisar dados.");
    }

    latestAnalysisPayload = payload;
    updateRawJson(payload);
    renderAnalysis(payload);
    await executeLaunch();
    showToast("Análise concluída e lançamento automático executado.");
  } catch (error) {
    clearLaunchState(true);
    analysisSection.hidden = true;
    showToast(error.message);
  } finally {
    if (latestExtractionId) {
      setAnalyzeButtonState(ANALYZE_BUTTON_STATE.READY);
    } else {
      setAnalyzeButtonState(ANALYZE_BUTTON_STATE.DISABLED);
    }
  }
});

async function executeLaunch() {
  if (!latestExtractionId) {
    showToast("Faça a análise antes de lançar o documento.");
    return;
  }

  setAnalyzeButtonState(ANALYZE_BUTTON_STATE.DISABLED);

  try {
    const response = await fetch(`/api/invoices/launch/${latestExtractionId}/`, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken(),
      },
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Falha ao executar lançamento.");
    }

    latestLaunchPayload = payload;
    updateRawJson(payload);
    renderLaunch(payload);
    showToast("Lançamento automático concluído.");
  } catch (error) {
    showToast(error.message);
    throw error;
  } finally {
    setAnalyzeButtonState(latestExtractionId ? ANALYZE_BUTTON_STATE.READY : ANALYZE_BUTTON_STATE.DISABLED);
  }
}

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
  const rows = produtos
    .map((item, index) => `
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
    `)
    .join("");

  return tableCard("Produtos/Serviços", "products", ["#", "Código", "Descrição", "NCM", "CST", "CFOP", "Qtd.", "Unidade", "Valor Unit.", "Total"], rows);
}

function installmentsCard(parcelas) {
  const rows = parcelas
    .map((item) => `
      <tr>
        <td>${escapeHtml(item.numero ?? "-")}</td>
        <td>${escapeHtml(item.descricao || "-")}</td>
        <td>${escapeHtml(dueDateLabel(item.data_vencimento))}</td>
        <td>${escapeHtml(currency(item.valor))}</td>
      </tr>
    `)
    .join("");

  return tableCard("Parcelas", "installments", ["Parcela", "Documento", "Vencimento", "Valor"], rows);
}

function classificationCard(classificacoes) {
  const rows = classificacoes
    .map((item) => `
      <tr>
        <td>${escapeHtml(item.categoria || "-")}</td>
        <td>${escapeHtml(item.justificativa || "-")}</td>
      </tr>
    `)
    .join("");

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

function renderAnalysis(payload) {
  const analysis = payload?.analysis || {};
  const movementType = payload?.movement_type || "";

  if (!payload || !analysis || !Object.keys(analysis).length) {
    analysisSection.hidden = true;
    analysisSummary.hidden = true;
    analysisSummary.textContent = "";
    analysisGrid.innerHTML = "";
    showToast("Não foi possível ler o resultado da análise.");
    return;
  }

  const cards = [];

  if (analysis.fornecedor) {
    cards.push(renderAnalysisEntityCard("Fornecedor", analysis.fornecedor));
  }

  if (analysis.faturado) {
    cards.push(renderAnalysisEntityCard("Faturado", analysis.faturado));
  }

  const blocks = Array.isArray(analysis.blocks) ? analysis.blocks : [];
  if (blocks.length) {
    blocks.forEach((block) => {
      cards.push(renderMovementAnalysisBlock(block));
    });
  } else if (Array.isArray(analysis.classificacoes)) {
    cards.push(renderClassificationsAnalysisCard("Classificações consultadas", analysis.classificacoes));
  }

  if (!cards.length) {
    analysisSection.hidden = true;
    analysisSummary.hidden = true;
    analysisGrid.innerHTML = "";
    return;
  }

  const prettyMovement = prettyMovementType(movementType);
  analysisSummary.hidden = false;
  analysisSummary.textContent = `Tipo de movimento inferido: ${prettyMovement}`;
  analysisGrid.innerHTML = cards.join("");
  analysisSection.hidden = false;
}

function renderAnalysisEntityCard(label, item) {
  const nome = item?.nome || "-";
  const documento = item?.documento || "-";
  const id = item?.id;
  const exists = resolveExists(item);
  const status = exists ? "EXISTE" : "NAO CADASTRADO NO BANCO";
  return `
    <article class="analysis-card">
      <h3>${escapeHtml(label)}</h3>
      <p><strong>Extraído do documento:</strong> ${escapeHtml(nome || "-")} | ${escapeHtml(documento || "-")}</p>
      <p><strong>Cadastro no banco:</strong> <span class="analysis-status ${analysisStatusClass(exists)}">${status}</span></p>
      <p><strong>ID no banco:</strong> ${escapeHtml(id ? String(id) : "-")}</p>
    </article>
  `;
}

function renderMovementAnalysisBlock(block) {
  const movementType = block?.movement_type || "";
  const movementLabel = prettyMovementType(movementType);
  const classifications = Array.isArray(block?.classificacoes) ? block.classificacoes : [];

  if (!classifications.length) {
    return `
      <article class="analysis-card">
        <h3>${escapeHtml(movementLabel)}</h3>
        <p>Sem classificação cadastrada para este bloco.</p>
      </article>
    `;
  }

  const rows = classifications
    .map((classification, index) => {
      const nome = classification?.descricao || classification?.nome || `Classificação ${index + 1}`;
      const id = classification?.id;
      const exists = resolveExists(classification);
      const status = exists ? "EXISTE" : "NAO CADASTRADO NO BANCO";

      return `
        <div class="analysis-item">
          <p><strong>Despesa interpretada:</strong> ${escapeHtml(nome)}</p>
          <p><strong>Cadastro no banco:</strong> <span class="analysis-status ${analysisStatusClass(exists)}">${status}</span></p>
          <p><strong>ID no banco:</strong> ${escapeHtml(id ? String(id) : "-")}</p>
        </div>
      `;
    })
    .join("");

  return `
    <article class="analysis-card">
      <h3>${escapeHtml(movementLabel)}</h3>
      <div class="analysis-items">${rows}</div>
    </article>
  `;
}

function renderClassificationsAnalysisCard(label, classifications) {
  const rows = classifications
    .map((classification, index) => {
      const descricao = classification?.descricao || classification?.nome || `Classificação ${index + 1}`;
      const exists = resolveExists(classification);
      const status = exists ? "EXISTE" : "NAO CADASTRADO NO BANCO";

      return `
        <div class="analysis-item">
          <p><strong>Despesa interpretada:</strong> ${escapeHtml(descricao)}</p>
          <p><strong>Cadastro no banco:</strong> <span class="analysis-status ${analysisStatusClass(exists)}">${status}</span></p>
          <p><strong>ID no banco:</strong> ${escapeHtml(classification?.id ? String(classification.id) : "-")}</p>
        </div>
      `;
    })
    .join("");

  return `
    <article class="analysis-card">
      <h3>${escapeHtml(label)}</h3>
      <div class="analysis-items">${rows || "<p>Nenhuma classificação encontrada.</p>"}</div>
    </article>
  `;
}

function renderLaunch(payload) {
  const launch = payload?.launch || {};
  const movements = Array.isArray(launch.movements) ? launch.movements : [];
  const movementType = payload?.movement_type || "";
  const launchFornecedor = launch?.fornecedor || {};
  const launchFaturado = launch?.faturado || {};
  const launchClassificacoes = Array.isArray(launch?.classificacoes) ? launch.classificacoes : [];
  const launchParcelas = Array.isArray(launch?.parcelas) ? launch.parcelas : [];

  if (!payload || (!Array.isArray(payload?.launch?.movements) && !launchClassificacoes.length && !launchParcelas.length)) {
    launchSection.hidden = true;
    launchMessage.textContent = "";
    launchGrid.innerHTML = "";
    return;
  }

  const movementSummary = prettyMovementType(movementType);
  const movementCards = movements
    .map((movement, index) => {
      const movementLabel = prettyMovementType(movement?.movement_type);
      const movementFornecedor = movement?.pessoa || launchFornecedor;
      const movementFaturado = movement?.faturado || launchFaturado;
      const movementClassificacoes = Array.isArray(movement?.classificacoes) ? movement.classificacoes : [];
      const movementParcelas = Array.isArray(movement?.parcelas) ? movement.parcelas : [];

      const movementClassificationsText = movementClassificacoes.length
        ? movementClassificacoes
            .map(
              (item) =>
                `${escapeHtml(item?.descricao || "Classificação")} (ID ${escapeHtml(String(item?.id ? item.id : "-"))})`
            )
            .join("<br>")
        : "-";
      const movementParcelsText = movementParcelas.length
        ? movementParcelas
            .map(
              (item) =>
                `${escapeHtml(item?.identificacao || "-")} | Parcela ${escapeHtml(String(item?.numero || "-"))} |`
                + ` Vencimento ${escapeHtml(dueDateLabel(item?.vencimento || item?.data_vencimento))} |`
                + ` Valor ${escapeHtml(currency(item?.valor))}`
            )
            .join("<br>")
        : "-";

      return `
        <article class="launch-card">
          <h3>${escapeHtml(`Movimento ${index + 1} (${movementLabel})`)}</h3>
          <p><strong>ID do movimento:</strong> ${escapeHtml(String(movement?.movement_id || "-"))}</p>
          <p><strong>Fornecedor:</strong> ${escapeHtml(movementFornecedor?.nome || "-")} (${escapeHtml(movementFornecedor?.documento || "-")})</p>
          <p><strong>Faturado:</strong> ${escapeHtml(movementFaturado?.nome || "-")} (${escapeHtml(movementFaturado?.documento || "-")})</p>
          <p><strong>Classificações:</strong> ${movementClassificationsText}</p>
          <p><strong>Parcelas:</strong> ${movementParcelsText}</p>
        </article>
      `;
    })
    .join("");

  const summaryCards = [
    renderLaunchEntityCard("Fornecedor", launchFornecedor),
    renderLaunchEntityCard("Faturado", launchFaturado),
    launchClassificacoes.length
      ? `<article class="launch-card"><h3>Classificações confirmadas</h3><p>${launchClassificacoes
          .map((item) => `${escapeHtml(item?.descricao || "Sem descrição")} (ID ${escapeHtml(String(item?.id || "-"))})`)
          .join("<br>")}</p></article>`
      : "",
    launchParcelas.length
      ? `<article class="launch-card"><h3>Parcelas confirmadas</h3><p>${launchParcelas
          .map(
            (item) =>
              `${escapeHtml(item?.identificacao || "-")} | Parcela ${escapeHtml(String(item?.numero || "-"))} |`
              + ` Vencimento ${escapeHtml(dueDateLabel(item?.vencimento || item?.data_vencimento))} |`
              + ` Valor ${escapeHtml(currency(item?.valor))}`
          )
          .join("<br>")}</p></article>`
      : "",
  ].join("");

  launchMessage.textContent = `${launch.message || "Lançamento concluído."} - ${movementSummary}`;
  launchGrid.innerHTML = `${summaryCards}${movementCards}`;
  launchSection.hidden = false;
}

function renderLaunchEntityCard(label, item) {
  if (!item || typeof item !== "object") {
    return "";
  }

  return `
    <article class="launch-card">
      <h3>${escapeHtml(label)}</h3>
      <p><strong>Nome:</strong> ${escapeHtml(item?.nome || "-")}</p>
      <p><strong>Documento:</strong> ${escapeHtml(item?.documento || "-")}</p>
      <p><strong>ID:</strong> ${escapeHtml(item?.id ? String(item.id) : "-")}</p>
    </article>
  `;
}

function resolveExists(item) {
  if (!item || typeof item !== "object") {
    return false;
  }

  if (typeof item.exists === "boolean") {
    return item.exists;
  }

  const status = String(item.status || "").toUpperCase();
  return status === "EXISTE" || status === "REATIVADO";
}

function analysisStatusClass(exists) {
  return exists ? "status-exists" : "status-missing";
}

function prettyMovementType(movementType) {
  switch (String(movementType || "").toUpperCase()) {
    case "APAGAR":
      return "Contas a Pagar";
    case "ARECEBER":
      return "Contas a Receber";
    case "MISTO":
      return "Contas a Pagar e Contas a Receber";
    default:
      return "Não definido";
  }
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
showTab("formatted");
