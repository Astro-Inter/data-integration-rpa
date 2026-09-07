# Persistência no destino — SCRUM-184

O `main` agora executa o fluxo completo quando `SYNC_DRY_RUN=false`:
consulta legado → compara hashes → valida candidato → verifica identidade no
destino → localiza/cria Firebase → persiste relações e usuário → confirma controle.
O modo `true` continua apenas consultando e validando os candidatos, sem criar
contas, relações, vínculos ou histórico. A simulação não verifica os conflitos de
identidade do destino nem comprova permissões de escrita.

## Preparação e execução

O banco destino deve conter as tabelas e constraints da aplicação fornecidas
nesta conversa. O RPA não cria nem altera esse schema de negócio.
Execute novamente a preparação do controle, inclusive se já usou a SCRUM-181:

```powershell
# Com variáveis preenchidas e SYNC_DRY_RUN=false:
.\.venv\Scripts\python.exe -m app.database.init_sync_control
```

O comando adiciona `rpa_user_links` se ausente e preserva as tabelas e dados
anteriores. Essa nova tabela contém `integration_key`, `legacy_id`, `target_id`
e `firebase_uid`. A chave composta identifica a origem; as constraints únicas
de usuário destino e UID impedem múltiplos vínculos. A ausência deliberada de FK
para o schema de negócio mantém a preparação independente; vínculos órfãos são
detectados pelo repositório e bloqueiam recriação automática.

Depois use `python -m app.main`. Com `SYNC_DRY_RUN=false`, esse comando efetivamente
grava no Firebase e no PostgreSQL. Com `true`, é simulação. Não é necessário
reinstalar dependências adicionais nesta subtarefa.

## Resolução de identidade

`TargetUserRepository.resolve_identity` é chamado antes de Firebase. Verifica o
vínculo legado/destino e procura CPF, e-mail normalizado e, quando disponível, UID.
Confere também `admin` e registros independentes de `conta`. Em PostgreSQL utiliza
`ONLY conta`, porque consultar a tabela pai normalmente inclui seus descendentes.

Se CPF, e-mail, UID ou vínculo apontarem para pessoas diferentes, a transação é
interrompida com mensagem sem dados pessoais. Um usuário preexistente só é
adotado com CPF compatível, UID válido, relações do workspace esperado e sem
outro vínculo de origem. CPF nulo ou diferente não é preenchido por aproximação.
Um registro já vinculado cujo usuário foi apagado não é recriado silenciosamente.

Mudança de CPF ou transferência para outro workspace exige revisão e não é
automática nesta implementação. Alterações de cargo/unidade dentro do mesmo
workspace são permitidas. Mudança de e-mail segue a regra da SCRUM-183: se houver
UID conhecido, o Firebase deve ter o e-mail compatível; divergência exige reconciliação.

Depois do Firebase, `persist` repete a verificação incluindo o UID retornado para
detectar conflito com outra identidade. O retorno do Firebase sozinho nunca
autoriza substituir um UID pertencente a outro usuário.

## Operações de gravação

| Tabela | Comportamento |
| --- | --- |
| `workspaces` | UPSERT por CNPJ. Reutiliza o ID e atualiza o nome com o valor do legado. |
| `cargos` | Insere por `(workspace_id, nome)` se ausente e recupera o ID. Preserva `ativo` quando já existe. |
| `unidades` | Mesmo tratamento por `(workspace_id, nome)`, usando o workspace resolvido. |
| `usuarios` | Insere com UID Firebase e IDs reais de cargo/unidade; ou atualiza nome, e-mail, CPF e referências da identidade validada. |
| `rpa_user_links` | Registra o vínculo estável após a persistência do usuário, na mesma transação. |
| Controle de sincronização | Confirma hash e data apenas depois do processador retornar; commit acontece no final da transação externa. |

Criação aplica `FUNCIONARIO`, `PRE_CADASTRADO` e os defaults do banco para data de
criação e modalidade. Atualização preserva ID, tipo, status, modalidade e criado_em.
Não há inserção extra em `conta`, reativação automática de cargos/unidades ou
modificação de administradores. Endereços, NRs e demais tabelas fora do mapeamento
continuam fora da sincronização.

Quando o nome de cargo ou departamento muda, a chave por nome localiza/cria outra
entidade e religa o usuário. Não renomeia nem apaga a entidade anterior, que pode
ser compartilhada com outros usuários. Duas origens com mesmo CNPJ compartilham
workspace; seus nomes precisam ser consistentes, pois a atualização usa o nome
do registro processado. Nomes são comparados como definidos pelas constraints,
sem transformar automaticamente diferenças de caixa em equivalências.

## Transações, falhas e concorrência

O lock de `rpa_sync_control` serializa execuções de escrita do RPA. Usuários
existentes são bloqueados com `FOR UPDATE` enquanto se valida e persiste. UPSERT
nas relações usa as constraints de unicidade, e inserts/updates de usuários
respeitam as constraints do destino.

Todas as gravações PostgreSQL da execução, incluindo relações e histórico, usam
a mesma transação. Qualquer falha reverte tudo e mantém candidatos pendentes.
Firebase não participa do rollback: uma conta já criada permanece e é localizada
na próxima tentativa. Não há exclusão automática de conta como compensação.

Outros escritores do aplicativo não necessariamente usam o lock do RPA. As
constraints protegem unicidade dentro de cada tabela, mas a herança de `conta`
não fornece unicidade global entre tabelas. A checagem explícita de conta/admin
não substitui uma constraint global contra gravações externas concorrentes.
A aplicação e o RPA devem coordenar esse tipo de cadastro se houver concorrência
entre identidades de admin/conta/usuario. Um conflito de constraint aborta a execução.

O fingerprint agora usa versão 3, reavaliando históricos anteriores para que o
vínculo destino também seja estabelecido. Depois disso, alterações feitas apenas
no destino não são reparadas por registros cujo hash de origem não mudou; o
controle detecta mudanças do legado, não é uma auditoria contínua do destino.

## Validação realizada

Testes executam as consultas e escritas reais em SQLite com FKs e unicidade,
simulando somente Firebase: criação, repetição sem duplicatas, adoção de usuário,
alteração de cargo/unidade, preservação de perfil e status, conflitos de CPF/UID,
admin e conta, vínculos órfãos, rollback e execução pelo `main` nos dois modos.

SQLite não comprova os comportamentos exclusivos de PostgreSQL, como herança,
locks concorrentes e permissões/RLS. O ambiente PostgreSQL/Firebase real precisa
ser validado com as configurações preenchidas; testes não criam contas reais.

Referências: [herança PostgreSQL](https://www.postgresql.org/docs/current/ddl-inherit.html)
e [INSERT/ON CONFLICT](https://www.postgresql.org/docs/current/sql-insert.html).
