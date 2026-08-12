# PMBA Questões - versão corrigida

Correção principal:
- Matérias do edital carregam no cadastro de questões.
- Conteúdos carregam de acordo com a matéria.
- Validação garante que matéria e conteúdo pertencem à divisão SD/CFO escolhida.
- Cadastro e edição de questões usam a estrutura oficial Matéria -> Conteúdo.
- Erros inesperados no cadastro/edição agora são registrados no log do Flask.

O banco `instance/questoes.db` foi preservado da versão enviada pelo usuário. Não é necessário recriar o banco para esta correção.

Execute normalmente:
    python app.py

Se precisar recarregar matérias/conteúdos do edital:
    python -m flask --app app seed-edital

Não execute `reset-db` a menos que queira apagar os dados do banco.
