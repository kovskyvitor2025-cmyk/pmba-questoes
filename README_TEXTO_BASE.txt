CORREÇÃO DO TEXTO-BASE

Esta versão corrige textos importados de PDF/CSV que possuem <br> usados como quebra visual de linha.

- Um <br> isolado vira espaço, evitando: "Amaro <br>Branco".
- Dois ou mais <br> consecutivos viram separação de parágrafo.
- O texto é exibido como leitura/redação, com fonte serifada, justificado e espaçamento confortável.
- Nenhum HTML arbitrário do texto é renderizado.
- Não é necessário recriar o banco.

Reinicie o Flask depois de substituir os arquivos:
    python app.py
