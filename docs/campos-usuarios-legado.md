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
| `funcionario.cpf` | `cpf` | Identificação do funcionário; preservar zeros à esquerda. |
| `funcionario.nome` | `nome` | Nome do usuário. |
| `funcionario.cargo` | `cargo` | Nome bruto para futura localização/criação de cargo no workspace. |
| `funcionario.id_empresa` | `id_empresa` | Referência de origem da empresa do funcionário. |
| `empresa.nome` | `empresa_nome` | Nome candidato para workspace. |
| `empresa.cnpj` | `empresa_cnpj` | Identificação empresarial candidata para workspace. |
| `funcionario.id_departamento` | `id_departamento` | Referência organizacional de origem. |
| `departamento.nome` | `departamento_nome` | Informação para decidir a correspondência com unidade. |
| `departamento.id_empresa` | `departamento_id_empresa` | Permite detectar departamento vinculado a empresa diferente da do funcionário. |
| `email.id_email`, `email.email` | `emails` | Coleção de registros de e-mail, ordenada por `id_email`. |

Empresa → workspace e departamento → unidade são possibilidades de mapeamento,
não regras de negócio já implementadas. Cargo e departamento devem ser avaliados
no contexto da empresa; nomes iguais em empresas diferentes não significam a
mesma entidade. A definição de `tipo`, `modalidade`, `status`, `ativo`, datas e
Firebase UID ocorrerá nas etapas de transformação e persistência.

## E-mails e relações

O legado permite vários e-mails por funcionário, inclusive registros nulos,
vazios ou repetidos, sem indicar o principal. Todos são preservados sem escolher
arbitrariamente um login. Funcionários sem e-mail retornam `emails=()` e seguem
para avaliação futura. A etapa de validação definirá elegibilidade e escolha do
e-mail antes de criar a conta Firebase.

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
quantidades. Os lotes ainda não são persistidos nem enviados ao Firebase.

## Fora da seleção atual

Endereços ficam para o mapeamento de unidades: no legado, rua, cidade, bairro e
estado são inteiros, sem tabelas de referência fornecidas. Não há informação
suficiente para convertê-los em logradouro, cidade e UF do destino.
CNAE, contagens, riscos, NRs, certificados e treinamentos não são necessários
para esta coleta inicial de usuários e não foram incluídos.

## Validação

Os testes executam as mesmas consultas em SQLite em memória, com dados fictícios
e modo somente de leitura nos cenários de consulta. Cobrem paginação, múltiplos
e-mails, ausência de referências, empresas distintas, erros e logs sem dados
pessoais. Não substituem a execução contra o PostgreSQL legado real.
