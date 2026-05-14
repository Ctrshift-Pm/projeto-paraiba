const path = require("node:path");
const { test, expect } = require("@playwright/test");

const mockedPayload = {
  success: true,
  id: 1001,
  provider: "mock",
  fallback_reason: "GEMINI_API_KEY nao foi configurada.",
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
    local_entrega: {
      nome_razao_social: "CLIENTE EXEMPLO",
      cpf_cnpj: "123.456.789-00",
      endereco: "Avenida Cliente",
      numero: "200",
      municipio: "Campina Grande",
      uf: "PB",
      cep: "58400-000",
    },
    transportador: {
      razao_social: "TRANSPORTE EXEMPLO LTDA",
      cpf_cnpj: "98.765.432/0001-10",
      municipio: "Joao Pessoa",
      uf: "PB",
      placa_veiculo: "ABC1D23",
      frete_por_conta: "Emitente",
      quantidade: "1",
      especie: "Volume",
      peso_bruto: "100,000 KG",
      peso_liquido: "98,000 KG",
    },
    informacoes_complementares: "Documento demonstrativo.",
    classificacoes_despesa: [
      {
        categoria: "MANUTENCAO E OPERACAO",
        justificativa: "Produto relacionado a combustiveis e lubrificantes.",
      },
    ],
  },
};

test("fluxo de extracao via interface web", async ({ page }) => {
  await page.route("**/api/invoices/extract/", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockedPayload),
    });
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
  await expect(formattedView.getByRole("heading", { name: "Local de Entrega" })).toBeVisible();
  await expect(formattedView.getByRole("heading", { name: "Transportador/Volumes" })).toBeVisible();
  await expect(formattedView.getByRole("heading", { name: "Produtos/Serviços" })).toBeVisible();
  await expect(formattedView.getByRole("columnheader", { name: "NCM" })).toBeVisible();
  await expect(formattedView.getByRole("columnheader", { name: "CFOP" })).toBeVisible();
  await expect(formattedView.getByRole("cell", { name: "Oleo Diesel S10" })).toBeVisible();

  await jsonButton.click();
  await expect(formattedView).toBeHidden();
  await expect(jsonView).toBeVisible();
  await expect(page.locator("#json-output")).toContainText('"fornecedor"');
  await expect(page.getByRole("button", { name: "Copiar JSON" })).toBeVisible();

  await formattedButton.click();
  await expect(formattedView).toBeVisible();
  await expect(jsonView).toBeHidden();
});
