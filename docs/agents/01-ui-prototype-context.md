# Contexto Visual dos Prototipos

## Layout

- Fundo claro, quase branco.
- Titulo grande centralizado: `Extracao de Dados de Nota Fiscal`.
- Subtitulo centralizado: `Carregue um PDF de nota fiscal e extraia os dados automaticamente usando IA`.
- Conteudo principal em largura ampla, com cards brancos, borda cinza clara e raio discreto.

## Card de Upload

- Cabecalho com icone de upload e texto `Upload do PDF`.
- Label em negrito: `Selecione o arquivo PDF da nota fiscal`.
- Campo visual cinza claro para selecao de arquivo.
- Quando arquivo for escolhido, mostrar uma linha azul muito clara com icone de documento, nome do arquivo e tamanho.
- Botao largo: `EXTRAIR DADOS`.
- Sem arquivo, botao cinza/desabilitado; com arquivo, botao preto/azul escuro.

## Card de Resultado

- Titulo `Dados Extraidos`.
- Controle segmentado com abas `Visualizacao Formatada` e `JSON`.
- Aba JSON deve mostrar titulo `Dados em JSON`, botao `Copiar JSON` e bloco escuro com texto verde.
- A visualizacao formatada deve apresentar secoes limpas de fornecedor, faturado, nota, produtos, parcelas e classificacoes.

## Responsividade

- Em mobile, cards ocupam quase toda a largura.
- Botoes e controles nao devem quebrar texto.
- O JSON deve ter rolagem horizontal/vertical quando necessario.
