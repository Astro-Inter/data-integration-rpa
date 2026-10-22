# Consulta de usuários do legado — SCRUM-180

Fonte: script do banco legado fornecido nesta conversa. O usuário de origem é
`funcionario`. Nesta etapa, todos os funcionários são candidatos à sincronização:
o script não tem campo de situação, perfil de acesso ou data de alteração para
filtrar com segurança. A consulta apenas coleta dados brutos; não aprova cadastros
nem escreve em Firebase ou PostgreSQL destino.

## Campos selecionados

| Origem | Campo retornado | Finalidade nas próximas etapas |
| --- | --- | --- |
| `funcionario.id_funcionario` | `id_funcionario` | Identificação na origem e paginação. Não representa o ID do destino. |
| `funcionario.cpf` | `cpf` | Texto original ou resultado de `pgp_sym_decrypt` para `BYTEA`; preservar zeros à esquerda. |
| `funcionario.nome` | `nome` | Nome do usuário. |
| `funcionario.cargo` | `cargo` | Nome bruto para futura localização/criação de cargo no workspace. |
| `funcionario.id_empresa` | `id_empresa` | Referência de origem da empresa do funcionário. |
| `empresa.nome` | `empresa_nome` | Nome candidato para workspace. |
| `empresa.cnpj` | `empresa_cnpj` | Identificação empresarial candidata para workspace. |
| `funcionario.id_departamento` | `id_departamento` | Referência organizacional de origem. |
| `departamento.nome` | `departamento_nome` | Informação para decidir a correspondência com unidade. |
| `departamento.id_empresa` | `departamento_id_empresa` | Permite detectar departamento vinculado a empresa diferente da do funcionário. |
| `email.id_email`, `email.email` | `emails` | Coleção de registros de e-mail, ordenada por `id_email`. |
| `funcionario.email` (modelo atualizado) | `emails` | Quando presente, substitui a consulta à tabela `email`; usa o ID do funcionário como identificador estável do único e-mail. |

A SCRUM-182 definiu empresa → workspace e departamento → unidade, com cargo e
unidade vinculados ao workspace da empresa. Nomes iguais em empresas diferentes
não significam a mesma entidade. Veja as regras de criação em
[Mapeamento e validação](mapeamento-e-validacao.md).

## E-mails e relações

No modelo atualizado, o e-mail fica em `funcionario.email`. Python e Worker
detectam essa coluna uma vez por varredura, sem consultar a antiga tabela
`email`. Nulos e vazios seguem para a mesma validação existente. No modelo
anterior, permanece a coleção descrita abaixo. Se os dois formatos coexistirem,
`funcionario.email` é a fonte utilizada.

O legado permite vários e-mails por funcionário, inclusive registros nulos,
vazios ou repetidos, sem indicar o principal. Todos são preservados sem escolher
arbitrariamente um login. Funcionários sem e-mail retornam `emails=()` e seguem
para validação. A SCRUM-182 exige um único e-mail válido após normalização e
deduplicação, sem escolher arbitrariamente entre endereços distintos.

Empresa e departamento usam `LEFT JOIN`: uma referência ausente não elimina
silenciosamente o funcionário. Dados da relação ausente ficam como `None`;
os IDs do funcionário são preservados. O script possui FKs, mas não garante que
a empresa do departamento seja a mesma empresa informada em `funcionario`.

## Paginação e execução

`LegacyUserRepository.iter_batches(SYNC_BATCH_SIZE)` devolve lotes de `LegacyUser`.
O chamador fornece uma conexão SQLAlchemy e é responsável por encerrá-la. A consulta
usa apenas `SELECT`, com parâmetros vinculados. E-mails são buscados por lote em
consulta separada para não multiplicar funcionários nem dividir seus e-mails entre
páginas. O limite do lote conta funcionários, não a quantidade total de e-mails.

A leitura ordena por `id_funcionario` e continua após o último ID da página,
sem `OFFSET`. O maior ID no início limita a varredura, evitando que inserções com
IDs maiores prolonguem a execução. IDs zero, negativos e intervalos entre IDs são
aceitos, pois o script permite inserir valores explícitos na coluna identity.

Cada execução começa novamente: a paginação lê todos os funcionários. Não existe
`updated_at` no script fornecido. A SCRUM-181 adiciona comparação com hashes
confirmados no destino; veja [Controle de sincronização](controle-sincronizacao.md).
O limite inicial não cria um snapshot: alterações e exclusões concorrentes podem
aparecer entre consultas; a consistência necessária será tratada com essa estratégia.

`python -m app.main` verifica as integrações e percorre a consulta, exibindo somente
quantidades. Desde a SCRUM-184, os candidatos validados são sincronizados com
Firebase e destino quando `SYNC_DRY_RUN=false`; `true` mantém apenas a simulação.

## Fora da seleção atual

Endereços ficam para o mapeamento de unidades: no legado, rua, cidade, bairro e
estado são inteiros, sem tabelas de referência fornecidas. Não há informação
suficiente para convertê-los em logradouro, cidade e UF do destino.
CNAE, contagens, riscos, NRs, certificados e treinamentos não são necessários
para esta coleta inicial de usuários e não foram incluídos.

## Criptografia do CPF e senha de administrador

Os adaptadores inspecionam o tipo de `funcionario.cpf` antes da paginação.
Para `BYTEA`, selecionam `pgp_sym_decrypt(f.cpf, chave) AS cpf` com a chave
vinculada como parâmetro, fornecida pelo segredo `LEGACY_CPF_KEY`. Use o mesmo
valor do `pgp_sym_encrypt` do legado no `.env` local ou nos secrets do ambiente
de execução. A chave não tem valor padrão no código. Para colunas textuais,
a leitura anterior continua sem exigir a chave.

`pgcrypto` deve estar instalado e acessível pelo `search_path`. Chave incorreta,
ausente ou dados inválidos interrompem a leitura sem confirmar a sincronização;
os erros não incluem o CPF, a chave ou a mensagem bruta do driver. O CPF já
descriptografado alimenta a validação, o hash de alterações e a persistência;
recriptografar o mesmo CPF não cria uma alteração lógica no usuário.

`administrador.senha` usa `crypt(..., gen_salt('bf', 12))`: trata-se de hash
bcrypt, sem descriptografia. Essa tabela não é consultada pelo RPA e suas
senhas não são enviadas ao Firebase.

## Validação

Os testes executam as mesmas consultas em SQLite em memória, com dados fictícios
e modo somente de leitura nos cenários de consulta. Cobrem paginação, múltiplos
e-mails, ausência de referências, empresas distintas, erros e logs sem dados
pessoais. Não substituem a execução contra o PostgreSQL legado real.

Os testes opt-in em PostgreSQL descartável também cobrem PGP real, chave
ausente/incorreta, CPF com zero à esquerda, ambos os formatos de e-mail,
recriptografia sem mudança lógica e transação somente de leitura. O CI fornece
`TEST_POSTGRES_URL` e executa os testes Python e Worker contra esse banco isolado.
