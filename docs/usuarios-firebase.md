# Criação e localização de contas Firebase — SCRUM-183

`FirebaseUserService` recebe a instância explícita do Firebase Admin SDK e
`dry_run` obrigatório. Opera somente sobre `PreparedUser`, produzido pela validação
da SCRUM-182. Não consulta o legado nem grava tabelas PostgreSQL.

## Fluxo

1. Se houver `known_uid` de um vínculo validado no destino, consultar esse UID e
   conferir se o e-mail é compatível. UID ausente ou e-mail divergente interrompem
   a operação; não se cria outra conta nem se troca o vínculo silenciosamente.
2. Sem UID conhecido, consultar o e-mail normalizado com `get_user_by_email`.
   Uma conta encontrada retorna seu UID sem atualizar dados.
3. Apenas `UserNotFoundError` significa que se pode tentar criar. Erro de rede,
   permissão ou credencial não é tratado como ausência de usuário.
4. Se ausente e `dry_run=true`, retornar `would_create` e `uid=None`.
5. Se ausente e escrita habilitada, chamar `create_user` com e-mail, display_name
   e email_verified=false. Não gerar senha ou UID artificiais.
6. Se houver conflito de e-mail durante a criação, consultar novamente para
   recuperar a conta que pode ter sido criada por outra execução.
7. Em timeout ou falha transitória de resultado incerto, consultar novamente
   antes de concluir a tentativa. Se a conta não puder ser confirmada, falhar e
   deixar uma próxima execução tentar novamente. Não repetir criação em loop.

Cada chamada do SDK recebe `app=...`, evitando usar acidentalmente outro projeto
ou a instância padrão do processo.

## Resultado

`FirebaseUserResult` retorna `uid`, `outcome` e `disabled`:

| outcome | Significado |
| --- | --- |
| `existing` | Conta localizada por UID ou e-mail. |
| `created` | Criação confirmada pelo retorno do SDK. |
| `recovered` | Conta localizada após conflito ou resposta incerta da criação. Não afirma qual processo a criou. |
| `would_create` | Simulação de uma conta ausente; UID e disabled são `None`. |

UID retornado deve ser uma string não vazia de até 128 caracteres, e o e-mail da
conta deve corresponder ao preparado. Contas já existentes não sofrem alteração
de nome, senha, e-mail, verificação, custom claims ou disabled. Uma conta bloqueada
permanece bloqueada; sua condição é informada no resultado.

Mudança de e-mail em um usuário que já tem UID no destino exige uma regra futura
de reconciliação explícita. O serviço atual acusa conflito para evitar abandonar
a conta original ou vincular outra identidade pelo novo endereço.

## Ligação com a persistência

`UserSyncProcessor` é um callback compatível com `ChangeTrackingService.run`.
Sua construção exige dois callbacks da próxima etapa:

- `resolve_identity(prepared, connection) -> str | None`: verifica conflitos de
  CPF/e-mail/UID e propriedade da identidade no destino; retorna o UID já vinculado
  ou `None`. Conflitos devem lançar exceção antes de acessar Firebase.
- `persist_user(prepared, uid, connection) -> None`: resolve workspace, cargo e
  unidade e persiste o usuário usando o UID confirmado e a mesma transação.

O processador chama resolução de identidade → Firebase → persistência, e só então
retorna. O controle da SCRUM-181 confirma hashes e datas depois desse retorno.
Callbacks não podem fazer commit próprio nem engolir falhas; qualquer exceção deve
sair do bloco `target.begin()` para provocar rollback.

Não use `ensure_user` isoladamente como callback de sincronização: Firebase OK
não equivale a usuário salvo no banco. O adaptador exige persistência justamente
para impedir essa confirmação prematura.

Uma busca por e-mail no Firebase não comprova CPF ou propriedade de uma conta.
Esses conflitos precisam ser resolvidos no destino antes de efetivar vínculos.
O serviço não modifica schemas, não decide fusão de pessoas nem concede perfis.

Firebase não participa da transação PostgreSQL. Se o Firebase criar uma conta e
o destino falhar, a conta permanece no Firebase e o controle não avança. A próxima
tentativa busca o e-mail e reutiliza seu UID, desde que a identidade siga compatível.
Não há exclusão automática de contas como compensação de rollback.

## Execução atual e simulação

O `main` continua como prévia enquanto os callbacks de resolução e persistência
do destino não forem implementados. Esta entrega disponibiliza o serviço e o
adaptador, mas não ativa criação em massa separada da persistência.

Para simular o serviço isoladamente, use
`FirebaseUserService(app, dry_run=True).ensure_user(prepared)`: pode consultar,
mas nunca cria. No fluxo integrado, `ChangeTrackingService.run(dry_run=True)` nem
chama o processador. O próprio processador também recusa executar em modo de
simulação caso seja chamado diretamente, sem confirmar UID fictício.

Erros de SDK, transporte e credenciais são convertidos em `FirebaseUserError`
com mensagem genérica e etapa, sem resposta bruta, token, e-mail ou UID nos logs.
Sem recuperação imediata possível, a falha permanece visível para nova tentativa.

## Validação

Testes simulam as chamadas do SDK e usam SQLite para verificar a integração
transacional: conta existente, criação, corrida por e-mail, timeout, erros de
permissão, UID divergente, simulação e retomada depois de falha no PostgreSQL.
Nenhuma conta real é criada pelos testes. Permissões e conectividade reais ainda
precisam ser validadas no ambiente de integração.

Referências: [Firebase Admin Auth](https://firebase.google.com/docs/reference/admin/python/firebase_admin.auth)
e [gerenciamento de usuários](https://firebase.google.com/docs/auth/admin/manage-users).
