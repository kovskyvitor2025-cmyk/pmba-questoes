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
