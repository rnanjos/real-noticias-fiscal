# Real Notícias Fiscal — Coletor RSS

Projeto para consolidar notícias fiscais/tributárias de 7 fontes em um único `rss.xml`, mantendo apenas as últimas 24 horas e ignorando uma fonte quando ela falhar.

## Fontes

1. Receita Federal
2. Portal Contábeis
3. IOB Online
4. Rota da Jurisprudência
5. Jornada Tax
6. CONFAZ
7. Portal da Reforma Tributária

> Observação: Jornada Tax atualmente não apresenta uma área pública de notícias com o mesmo formato das demais. O coletor mantém a fonte configurada e retorna zero itens quando não encontra conteúdo datado, sem interromper as outras fontes.

## Agendamento

O arquivo `.github/workflows/atualizar-rss.yml` executa o projeto uma vez ao dia às **12:00 (horário de Brasília/São Paulo, UTC-3)**, além de permitir execução manual pelo botão **Run workflow**.

## Como publicar

1. Crie um repositório GitHub chamado `real-noticias-fiscal`.
2. Envie todos os arquivos desta pasta para a raiz do repositório.
3. Em **Settings > Actions > General > Workflow permissions**, permita **Read and write permissions**.
4. Em **Settings > Pages**, selecione **Deploy from a branch**.
5. Selecione a branch `main` e a pasta `/docs`.
6. Execute manualmente o workflow uma vez para testar.

O RSS ficará, normalmente, em:

`https://SEU_USUARIO.github.io/real-noticias-fiscal/rss.xml`

O diagnóstico da última execução ficará em:

`https://SEU_USUARIO.github.io/real-noticias-fiscal/status.json`

## Power Automate

O pacote em `power-automate/RSSFiscal_7_Fontes.zip` já foi preparado para:

- executar diariamente às 12:00;
- ler um RSS consolidado;
- exibir a fonte dinamicamente usando a primeira categoria do item;
- não enviar e-mail quando o RSS estiver vazio.

Antes de importar, substitua no pacote a URL `https://SEU_USUARIO.github.io/real-noticias-fiscal/rss.xml` pela URL real do seu GitHub Pages, ou altere o campo **A URL do RSS feed** após a importação.

## Regra das 24 horas

Quando uma página oferece data e hora, a janela é exata. Quando a fonte publica apenas a data, sem horário, o coletor usa o fim do dia como referência para evitar perder uma publicação potencialmente recente.
