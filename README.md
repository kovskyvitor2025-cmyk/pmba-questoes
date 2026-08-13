# PMBA Questões V8

Versão com estrutura de edital: Divisão (SD/CFO) → Matéria → Conteúdo → Questão.

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Como a base foi reorganizada, recrie o banco e carregue os editais:

```powershell
python -m flask --app app reset-db
python -m flask --app app seed-edital
python -m flask --app app create-admin
python app.py
```

## Cadastro de questões

No Admin → Nova questão:

1. escolha SD ou CFO;
2. escolha a matéria;
3. o conteúdo é carregado automaticamente da matéria;
4. cadastre banca, ano, texto-base, enunciado, alternativas, gabarito e explicação.

O importador CSV continua aceitando o modelo antigo com `disciplina` e `assunto`: eles são usados para localizar a matéria e o conteúdo do edital. Para novos arquivos, prefira a mesma nomenclatura do edital.

## Estrutura

- Materia: categoria, nome e ordem.
- Conteudo: matéria, nome e ordem.
- Question: matéria_id e conteudo_id, mantendo disciplina/assunto como campos compatíveis.
- APIs: `/api/materias/<SD|CFO>` e `/api/conteudos/<materia_id>`.

A carga CFO usa o Anexo II do edital CFOPM 2024/2025; a carga SD usa o conteúdo programático fornecido pelo usuário nesta conversa.

## Fase nova — Home, desempenho e comunicação

### Estatísticas
- Pontos fortes: mínimo de 3 questões e >= 70%.
- Zona de atenção: 50% a 69,9%.
- Prioridade: mínimo de 3 questões e < 50%.
- Classificação também por conteúdo.

### WhatsApp
O projeto passou a registrar preferências de comunicação, histórico de mensagens e notícias.

Para habilitar envio pela WhatsApp Business Cloud API, configure:

```text
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_GRAPH_VERSION=v23.0
WHATSAPP_NUMBER=5575...
```

O `WHATSAPP_NUMBER` é o número usado nos links de contato; `WHATSAPP_PHONE_NUMBER_ID` é o identificador do número remetente na API da Meta.

Comandos disponíveis:

```powershell
python -m flask --app app send-whatsapp-questoes
python -m flask --app app send-whatsapp-desempenho
```

Para mensagens proativas fora da janela de atendimento do WhatsApp, configure templates aprovados pela Meta antes de colocar os comandos em uma rotina automática (cron/Render Cron Job). O sistema mantém histórico de cada tentativa em `message_log`.

### Notícias
- Público: `/noticias`
- Administração: `/admin/noticias`
- Publicação pode disparar WhatsApp para usuários que autorizaram notícias.

### Preferências do aluno
- `/preferencias`
- Número do WhatsApp
- autorização
- questões
- alertas de desempenho
- notícias
