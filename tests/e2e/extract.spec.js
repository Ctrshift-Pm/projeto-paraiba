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
    },
    faturado: {
      nome_completo: "CLIENTE EXEMPLO",
      cpf: "123.456.789-00",
    },
    numero_nota_fiscal: "000123456",
    data_emissao: "2024-01-15",
    produtos: [
      {
        descricao: "Oleo Diesel S10",
        quantidade: 100,
      },
    ],
    parcelas: [
      {
        numero: 1,
        data_vencimento: "2024-02-15",
        valor: 1500.0,
      },
    ],
    valor_total: 1500.0,
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
  await expect(formattedView.getByRole("heading", { name: "Produtos/Serviços" })).toBeVisible();
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
