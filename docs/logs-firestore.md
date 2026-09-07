# Histórico de execuções no Firestore

O RPA grava um documento por execução em `rpa_execucoes/{run_id}`. O ID é o mesmo
dos logs de início e fim do terminal. No console Firebase, abra Firestore Database,
selecione o banco `(default)` e a coleção `rpa_execucoes`.

O banco solicitado já existia no projeto configurado: `(default)`, localização
`nam5`, edição Standard, modo Firestore nativo. Foi realizada uma gravação/leitura
de verificação, identificada pelo prefixo `verificacao-` e pelo evento
`VERIFICACAO_HISTORICO`. Esse registro não corresponde a uma sincronização de usuários.
O aplicativo cria os documentos e a coleção na primeira gravação; não provisiona
bancos ou altera suas configurações durante a execução.

## Conteúdo salvo

| Campo | Conteúdo |
| --- | --- |
| `run_id` | Identificador único da execução. |
| `status` | `EM_EXECUCAO`, `SUCESSO`, `FALHA` ou `INTERROMPIDA`. |
| `dry_run` | Indica simulação ou gravação de cadastros. |
| `iniciado_em`, `atualizado_em`, `encerrado_em` | Horários UTC do processo. |
| `ultima_etapa` | Código da última etapa registrada. |
| `eventos` | Lista de códigos de etapa e respectivos horários, limitada a 50 entradas. |
| `contagens` | Totais, novos, alterados, inalterados, confirmados, válidos e inválidos. |
| `exit_code`, `duracao_segundos` | Resultado e duração da execução. |

São registrados início, validação de conexões, processamento, conclusão da
transação, falhas e encerramento. Os códigos de falha distinguem integração,
consulta ao legado, validação, Firebase Auth, conflito de identidade, PostgreSQL,
erro inesperado e interrupção. O histórico recebe somente eventos conhecidos e
métricas: não recebe texto bruto de exceções, traceback, SQL, CPF, e-mail, UID
de usuário, senhas ou credenciais.

As contagens são salvas após a conclusão do processamento e saída da transação.
Se houver falha antes disso, o documento mantém as etapas, sem apresentar
contagens de usuários como confirmadas. Uma falha de limpeza posterior ao commit
pode produzir status `FALHA` com a etapa `TRANSACAO_CONCLUIDA` já registrada.

## Configuração

```env
FIRESTORE_DATABASE_ID=(default)
FIRESTORE_LOGS_ENABLED=true
```

O acesso usa o mesmo `FIREBASE_PROJECT_ID` e `FIREBASE_CREDENTIALS_BASE64` do RPA.
A conta de serviço precisa de permissão IAM de escrita no Firestore; por exemplo,
o papel `roles/datastore.user`. O Admin SDK usa IAM, independentemente das regras
de acesso dos clientes web/mobile. Nenhuma regra ou permissão é alterada pelo RPA.

O GitHub Actions aceita as duas configurações como variables do repositório ou
do environment `rpa`. Não é necessário adicionar outro segredo. As dependências
Firestore já fazem parte do Firebase Admin SDK instalado.

`SYNC_DRY_RUN=true` mantém os cadastros e o controle PostgreSQL sem alterações,
mas grava estes logs operacionais. Defina também `FIRESTORE_LOGS_ENABLED=false`
se não quiser nenhuma gravação remota. Os testes automatizados usam mocks e
desabilitam o histórico real; não carregam as credenciais do `.env`.

## Indisponibilidade e limites

O histórico é independente do PostgreSQL e permanece salvo mesmo quando ocorre
rollback de cadastros. Cada etapa grava o estado atual do documento. As chamadas
de escrita usam timeout de 3 segundos e não fazem retry automático. Na primeira
falha de inicialização ou escrita, o histórico remoto é desativado para aquela
execução, com aviso sanitizado no terminal. A próxima execução tenta novamente.
Falha de logs não muda o código de saída da sincronização.

Se o Firestore ficar indisponível no meio da execução ou o processo for encerrado
à força, o documento pode permanecer `EM_EXECUCAO`; confira o terminal/Actions.
Não há fila local para reenviar logs perdidos. Configuração inválida antes da
inicialização do histórico só aparece no terminal. Os documentos permanecem
salvos sem expiração automática; há gravações por etapa, sem log por usuário.

Referências: [Admin SDK Firestore](https://firebase.google.com/docs/reference/admin/python/firebase_admin.firestore)
e [IAM para bibliotecas de servidor](https://firebase.google.com/docs/firestore/security/iam).
