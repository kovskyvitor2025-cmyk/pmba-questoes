# PMBA Questões — Deploy online

## O que foi preparado
- Flask pronto para Gunicorn.
- PostgreSQL via `DATABASE_URL`.
- SQLite continua funcionando localmente.
- Conversão automática de `postgres://` para `postgresql+psycopg2://`.
- `SECRET_KEY` por variável de ambiente.
- `FLASK_DEBUG=0` em produção.
- endpoint `/health`.
- `requirements.txt` com Gunicorn e psycopg2-binary.
- `Procfile` e `render.yaml`.

## Desenvolvimento local
Com o banco SQLite atual:
```powershell
python app.py
```

## Produção
Configure estas variáveis:
```text
SECRET_KEY=<uma-chave-aleatoria-grande>
DATABASE_URL=<URL do PostgreSQL>
WHATSAPP_NUMBER=<numero em formato internacional>
FLASK_ENV=production
FLASK_DEBUG=0
```

Comandos:
```text
Build: pip install -r requirements.txt
Start: gunicorn --workers 1 --threads 4 --timeout 120 app:app
```

## Importante sobre o banco
O `instance/questoes.db` foi mantido neste pacote para você continuar desenvolvendo localmente.
Ele NÃO deve ser usado como banco persistente na hospedagem.

Antes do primeiro deploy com dados, migre os dados do SQLite para o PostgreSQL e confirme a quantidade de:
- usuários
- questões
- matérias
- conteúdos
- respostas
- comentários
- pagamentos
- feedbacks

Não execute `reset-db` no banco de produção.
