# Controle de alterações — SCRUM-181

## Estratégia adotada

O script fornecido do legado não tem data de alteração em `funcionario`,
`empresa`, `departamento` ou `email`. Filtrar por último ID encontraria novos
funcionários, mas perderia alterações nos existentes. Por isso, a leitura continua
percorrendo o legado em lotes e compara um SHA-256 dos campos da SCRUM-180 com o
último hash confirmado no PostgreSQL destino.

| Comparação | Classificação |
| --- | --- |
| Sem hash confirmado para o ID da origem | Novo / ainda não sincronizado |
| Hash atual diferente do confirmado | Alterado |
| Hash atual igual ao confirmado | Sem alteração; não chama o processador |

O hash considera nome, CPF, cargo, empresa, departamento, seus identificadores e
todos os e-mails. Os e-mails são ordenados pelo ID, portanto uma simples mudança
na ordem da coleção não causa reprocessamento. Valores nulos, vazios, repetições
e espaços são preservados no hash. A normalização da SCRUM-182 é aplicada depois
da comparação, sem modificar o retrato bruto usado pelo controle.
O conteúdo inclui uma versão do contrato (2 desde a SCRUM-182); uma alteração desse contrato deverá
incrementar a versão para reprocessar os registros antigos.

O controle evita repetir o processamento de dados confirmados, mas não elimina a
leitura completa do legado. `last_synced_at` é informação de conclusão, não um
filtro SQL. Mudanças em campos não selecionados não alteram o hash. Exclusões no
legado não desativam usuários no destino nesta entrega.

## Tabelas no destino

`rpa_sync_control` contém uma linha por integração (`usuarios_legado`) e
`last_synced_at`, data UTC da última execução de sincronização totalmente concluída.

`rpa_sync_users` contém a chave da integração, `legacy_id`, `data_hash` e
`synced_at`, data UTC da última confirmação daquele usuário. A chave primária
composta impede duplicar o estado do mesmo funcionário. Não são armazenados
nome, CPF, e-mail ou credenciais nessas tabelas. O hash não é criptografia nem
substitui as permissões de acesso do banco.

O controle pressupõe uma origem estável por destino e chave de integração. Não
reutilize esse histórico com outro banco legado cujos IDs representem pessoas
diferentes. A limpeza ou migração desse histórico exige uma decisão específica.

## Preparação do ambiente

Com o `.env` preenchido, execute uma vez na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m app.database.init_sync_control
```

O comando exige `SYNC_DRY_RUN=false` e permissão de criação no PostgreSQL destino.
Cria apenas as duas tabelas de controle, se ausentes; não acessa Firebase nem
legado e não migra usuários. Repetir o comando preserva o histórico existente.
Não é um mecanismo de atualização de schema: futuras mudanças das tabelas
precisarão de migração específica.

Depois, a execução normal é:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

As tabelas devem existir também para uma simulação. A execução normal não cria
tabelas automaticamente. Sem acesso ou sem schema, retorna erro em vez de
assumir que todos os usuários são novos.

## Detecção e confirmação são etapas distintas

O `main` atual calcula as quantidades de novos, alterados e inalterados e consulta
a última sincronização confirmada. Ainda não fornece um processador de usuários,
portanto não grava hashes nem avança datas, inclusive com `SYNC_DRY_RUN=false`.
Até a implementação da persistência, um usuário sem histórico continua aparecendo
como novo nas próximas execuções. Isso evita considerar sincronizado um usuário
que foi apenas lido.

`ChangeTrackingService.run(..., process_user=...)` oferece o ponto de integração
para as próximas subtarefas. O processador recebe `PreparedUser` validado pela
SCRUM-182 e deverá concluir a operação Firebase e
a persistência do usuário e relações usando a conexão destino recebida. Só deve
retornar em caso de sucesso; em falhas, deve lançar uma exceção.

O chamador deve usar `with target_engine.begin() as target:` e deixar qualquer
exceção sair desse bloco, sem capturá-la para fazer commit. O processador não pode
fazer commit próprio. Hashes, dados PostgreSQL e data final pertencem à mesma
transação. Se qualquer usuário, consulta ou atualização do controle falhar, toda
essa transação é revertida; na próxima execução os dados continuam pendentes.
Esse desenho prioriza consistência e mantém uma transação durante a execução;
uma estratégia futura de commits por usuário precisará também adaptar o controle.

Com escrita habilitada, a linha de `rpa_sync_control` é bloqueada com `FOR UPDATE`
antes da leitura dos hashes. Execuções que respeitam esse protocolo ficam
serializadas até commit/rollback; uma espera acima do timeout do banco falha e
pode ser tentada novamente. Uma prévia usa somente consultas e não pega esse lock.

`SYNC_DRY_RUN=true` nunca chama o processador e não grava controle ou datas.
Sem processador, o comportamento é igualmente de prévia. Depois de uma execução
real bem-sucedida sem alterações, a data global avança, mas as datas por usuário
permanecem na última vez em que cada um foi processado.

Firebase não participa da transação PostgreSQL. Se uma operação Firebase funcionar
e o banco falhar depois, a integração futura deve localizar/reutilizar essa conta
na tentativa seguinte. O controle não tenta desfazer contas Firebase.
A SCRUM-183 fornece `FirebaseUserService` e `UserSyncProcessor` para esse fluxo;
veja [Usuários no Firebase](usuarios-firebase.md). O adaptador exige resolução de
identidade e persistência antes de devolver sucesso ao controle.

A varredura atual não é um snapshot do legado: alterações concorrentes podem
aparecer entre páginas ou entre a consulta de funcionário e e-mails. Uma mudança
que deixe os dados diferentes do último hash confirmado volta a ser detectada
na próxima varredura; a data global não elimina candidatos.

## Validação

Testes com SQLite em memória verificam escrita real do histórico, consulta em
execuções posteriores, campos relacionados, simulação, falhas parciais, rollback
do destino e retomada. Serviços externos são simulados. O bloqueio concorrente
específico de PostgreSQL e o acesso ao ambiente real ainda exigem validação em
PostgreSQL; o teste SQLite não comprova esse comportamento de concorrência.

Referências: [transações SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/connections.html#using-transactions)
e [bloqueios PostgreSQL](https://www.postgresql.org/docs/current/explicit-locking.html).
