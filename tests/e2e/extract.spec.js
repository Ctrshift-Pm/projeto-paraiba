const path = require("path");
const { test, expect } = require("@playwright/test");

const extractedPayload = {
  success: true,
  id: 1001,
  provider: "mock",
  fallback_reason: "GEMINI_API_KEY nao foi configurada.",
  metadata: {
    file_name: "nota-fiscal-teste.pdf",
    file_size: 123456,
    created_at: "2024-01-01T00:00:00",
  },
  data: {
    fornecedor: {
      razao_social: "EMPRESA FORNECEDORA LTDA",
      fantasia: "FORNECEDORA",
      cnpj: "12.345.678/0001-90",
      inscricao_estadual: "123.456.789.012",
      endereco: "Rua das Empresas",
      numero: "100",
      bairro: "Centro",
      municipio: "Joao Pessoa",
      uf: "PB",
      cep: "58000-000",
    },
    faturado: {
      nome_completo: "CLIENTE EXEMPLO",
      cpf: "123.456.789-00",
      cnpj: "",
      endereco: "Avenida Cliente",
      numero: "200",
      municipio: "Campina Grande",
      uf: "PB",
      cep: "58400-000",
    },
    numero_nota_fiscal: "000123456",
    serie: "1",
    chave_acesso: "25240112345678000190550010001234561000000010",
    natureza_operacao: "VENDA DE MERCADORIA",
    protocolo_autorizacao: "325240000000000",
    data_emissao: "2024-01-15",
    produtos: [
      {
        codigo: "001",
        descricao: "Oleo Diesel S10",
        ncm: "27101921",
        cst: "060",
        cfop: "5102",
        unidade: "L",
        quantidade: 100,
        valor_unitario: 15,
        valor_total: 1500,
      },
    ],
    parcelas: [
      {
        numero: 1,
        descricao: "Duplicata 001",
        data_vencimento: "2024-02-15",
        valor: 1500.0,
      },
    ],
    valor_total: 1500.0,
    valor_produtos: 1500.0,
    valor_frete: 0.0,
    valor_desconto: 0.0,
    base_calculo_icms: 1500.0,
    valor_icms: 270.0,
    classificacoes_despesa: [
      {
        categoria: "MANUTENCAO E OPERACAO",
        justificativa: "Produto relacionado a combustiveis e lubrificantes.",
      },
    ],
  },
};

const analyzedPayload = {
  success: true,
  extraction_id: 1001,
  movement_type: "APAGAR",
  analysis: {
    fornecedor: {
      nome: "EMPRESA FORNECEDORA LTDA",
      documento: "12.345.678/0001-90",
      id: 12,
      status: "EXISTE",
      reactivated: false,
      exists: true,
    },
    faturado: {
      nome: "CLIENTE EXEMPLO",
      documento: "123.456.789-00",
      id: 34,
      status: "EXISTE",
      reactivated: false,
      exists: true,
    },
    blocks: [
      {
        movement_type: "APAGAR",
        classificacoes: [
          {
            descricao: "MANUTENCAO E OPERACAO",
            id: 44,
            status: "EXISTE",
            reactivated: false,
            exists: true,
          },
        ],
      },
    ],
  },
};

const launchedPayload = {
  success: true,
  extraction_id: 1001,
  movement_type: "APAGAR",
  launch: {
    message: "Lancamentos concluidos com sucesso.",
    fornecedor: {
      id: 12,
      nome: "EMPRESA FORNECEDORA LTDA",
      documento: "12.345.678/0001-90",
    },
    faturado: {
      id: 34,
      nome: "CLIENTE EXEMPLO",
      documento: "123.456.789-00",
    },
    classificacoes: [
      {
        id: 44,
        descricao: "MANUTENCAO E OPERACAO",
      },
    ],
    parcelas: [
      {
        id: 2001,
        identificacao: "MOV-2000-P1",
        numero: 1,
        vencimento: "2024-02-15",
        valor: "1500.00",
      },
    ],
    movements: [
      {
        movement_type: "APAGAR",
        movement_id: 2000,
        pessoa_id: 12,
        faturado_id: 34,
        pessoa: {
          id: 12,
          nome: "EMPRESA FORNECEDORA LTDA",
          documento: "12.345.678/0001-90",
        },
        faturado: {
          id: 34,
          nome: "CLIENTE EXEMPLO",
          documento: "123.456.789-00",
        },
        classificacao_ids: [44],
        classificacoes: [
          {
            id: 44,
            descricao: "MANUTENCAO E OPERACAO",
          },
        ],
        parcelas: [
          {
            id: 2001,
            identificacao: "MOV-2000-P1",
            numero: 1,
            vencimento: "2024-02-15",
            valor: "1500.00",
          },
        ],
        parcelas_ids: [2001],
      },
    ],
  },
};

test("fluxo de extracao via interface web", async ({ page }) => {
  const callCounters = {
    extract: 0,
    analyze: 0,
    launch: 0,
  };

  await page.route("**/api/invoices/**", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }

    const url = request.url();
    const requestBodyBuffer = request.postDataBuffer ? request.postDataBuffer() : null;
    const requestBody = requestBodyBuffer ? requestBodyBuffer.toString("utf8") : request.postData() || "";

    if (url.includes("/api/invoices/extract/")) {
      callCounters.extract += 1;
      expect(requestBody).toContain("name=\"pdf\"");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(extractedPayload),
      });
      return;
    }

    if (url.includes("/api/invoices/analyze/")) {
      callCounters.analyze += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(analyzedPayload),
      });
      return;
    }

    if (url.includes("/api/invoices/launch/")) {
      callCounters.launch += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(launchedPayload),
      });
      return;
    }

    await route.continue();
  });

  const fixturePath = path.join(__dirname, "fixtures", "nota-fiscal-teste.pdf");

  await page.goto("/");
  await expect(page.getByRole("link", { name: "DocExtract" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Extração de Dados de Nota Fiscal" })).toBeVisible();
  await expect(page.getByText("Carregue sua nota fiscal")).toBeVisible();
  await expect(page.getByText("Arraste e solte o arquivo PDF aqui ou clique para procurar.")).toBeVisible();
  await expect(page.getByText("Enviar Arquivo")).toBeVisible();

  const extractButton = page.getByRole("button", { name: "Extrair Dados" });
  await expect(extractButton).toBeDisabled();

  await page.setInputFiles("#pdf-input", fixturePath);

  await expect(page.locator("#selected-file")).toBeVisible();
  await expect(page.locator("#selected-file-name")).toHaveText("nota-fiscal-teste.pdf");
  await expect(page.locator("#selected-file-size")).toContainText(/(B|KB|MB)/);
  await expect(extractButton).toBeEnabled();

  await extractButton.click();

  const resultPanel = page.locator("#result-panel");
  const formattedView = page.locator("#formatted-view");
  const jsonView = page.locator("#json-view");
  const formattedButton = page.getByRole("tab", { name: "Visualização Formatada" });
  const jsonButton = page.getByRole("tab", { name: "JSON Bruto" });
  const analyzeButton = page.getByRole("button", { name: "Analisar Dados" });
  const analysisSection = page.locator("#analysis-section");
  const launchSection = page.locator("#launch-section");
  const analysisSummary = page.locator("#analysis-summary");
  const launchMessage = page.locator("#launch-message");

  await expect(resultPanel).toBeVisible();
  await expect(resultPanel.getByRole("heading", { name: "Dados Extraídos" })).toBeVisible();
  await expect(page.locator("#provider-badge")).toContainText("Origem: mock");
  await expect(page.locator("#provider-badge")).toContainText("GEMINI_API_KEY nao foi configurada.");
  await expect(formattedView).toBeVisible();
  await expect(jsonView).toBeHidden();
  await expect(formattedView.getByRole("heading", { name: "Fornecedor" })).toBeVisible();
  await expect(formattedView.getByText("Chave de Acesso")).toBeVisible();
  await expect(formattedView.getByText("Natureza da Operação")).toBeVisible();
  await expect(formattedView.getByRole("heading", { name: "Totais e Impostos" })).toBeVisible();
  await expect(formattedView.getByRole("heading", { name: "Produtos/Serviços" })).toBeVisible();
  await expect(formattedView.getByText("MANUTENCAO E OPERACAO")).toBeVisible();
  await expect(analyzeButton).toBeVisible();

  await jsonButton.click();
  await expect(formattedView).toBeHidden();
  await expect(jsonView).toBeVisible();
  await expect(page.locator("#json-output")).toContainText('"fornecedor"');
  await expect(page.locator("#json-output")).toContainText('"valor_total"');
  await expect(page.locator("#json-output")).not.toContainText('"analysis"');
  await expect(page.locator("#json-output")).not.toContainText('"launch"');
  await expect(page.getByRole("button", { name: "Copiar JSON" })).toBeVisible();

  await formattedButton.click();
  await expect(formattedView).toBeVisible();
  await expect(jsonView).toBeHidden();

  await analyzeButton.click();
  await expect(analysisSection).toBeVisible();
  await expect(analysisSummary).toContainText("Tipo de movimento inferido");
  await expect(page.locator("#analysis-grid")).toContainText("Contas a Pagar");
  await expect(page.locator("#json-output")).toContainText('"movement_type"');
  await expect(page.locator("#analysis-grid")).toContainText("EMPRESA FORNECEDORA LTDA");
  await expect(page.locator("#analysis-grid")).toContainText("CLIENTE EXEMPLO");
  await expect(page.locator("#analysis-grid")).toContainText("MANUTENCAO E OPERACAO");

  await expect(launchSection).toBeVisible();
  await expect(launchMessage).toContainText("Lancamentos concluidos com sucesso.");
  await expect(page.locator("#launch-grid")).toContainText("Fornecedor");
  await expect(page.locator("#launch-grid")).toContainText("EMPRESA FORNECEDORA LTDA");
  await expect(page.locator("#launch-grid")).toContainText("12.345.678/0001-90");
  await expect(page.locator("#launch-grid")).toContainText("CLIENTE EXEMPLO");
  await expect(page.locator("#launch-grid")).toContainText("MANUTENCAO E OPERACAO");
  await expect(page.locator("#launch-grid")).toContainText("MOV-2000-P1");

  await expect(callCounters.extract).toBe(1);
  await expect(callCounters.analyze).toBe(1);
  await expect(callCounters.launch).toBe(1);
});
