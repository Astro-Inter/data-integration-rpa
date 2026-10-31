# Controle de sincronização no Firestore — SCRUM-177

Python (`app.main`) e Worker usam Firestore no lugar das tabelas de controle SQL.
Os cadastros da aplicação continuam no PostgreSQL; Firebase Auth continua
responsável pelas contas. A opção de logs não desabilita o controle Firestore.

## Coleções

No projeto da conta de serviço existente, banco `FIRESTORE_DATABASE_ID`
(padrão `(default)`):

| Caminho | Conteúdo |
| --- | --- |
| `rpa_integracoes/usuarios_legado` | Última execução concluída: `last_synced_at`. |
| `rpa_integracoes/usuarios_legado/usuarios/{legacy_id}` | `data_hash`, `synced_at`, `target_id`, `firebase_uid`. |
| `rpa_integracoes/usuarios_legado/destinos/{target_id}` | `legacy_id`, para impedir reutilização de identidade. |
| `rpa_integracoes/usuarios_legado/uids/{sha256(uid)}` | `legacy_id`, para impedir reutilização de UID. |
| `rpa_locks/usuarios_legado` | Dono e início da execução exclusiva com escrita. |

Nenhum documento de controle armazena CPF, senha ou chave de descriptografia.
Não é necessário criar coleções manualmente. O banco Firestore deve existir e a
conta de serviço precisa de acesso IAM de leitura e escrita (por exemplo,
`roles/datastore.user`). Restrinja acesso dos clientes da aplicação a essas
coleções; clientes administrativos usam IAM, não as regras de clientes web.

## Confirmação e falhas

1. A simulação apenas lê controles e valida candidatos. Não adquire lock nem
   escreve controles ou cadastros. Os logs operacionais são independentes.
2. A execução com escrita cria o lock de forma exclusiva antes de processar.
3. Os vínculos e hashes ficam em memória enquanto a transação SQL está aberta.
4. Após o commit PostgreSQL, cada usuário e seus dois índices são publicados
   atomicamente no Firestore. A data da execução é publicada por último.
5. O lock é liberado ao terminar, inclusive se o processamento falhar.

Não existe transação distribuída entre PostgreSQL, Auth e Firestore. Se o SQL
falhar, os controles pendentes são descartados. Se o Firestore falhar após o
commit SQL, a execução falha e a próxima tentativa reconcilia o usuário por
CPF/e-mail/UID, em vez de criar um novo cadastro. Uma falha pode deixar apenas
parte dos usuários com hash atualizado; a data global não avança até concluir.
Contas criadas no Auth antes de um rollback SQL são reaproveitadas na tentativa
seguinte. Conflitos de identidade exigem revisão e não são resolvidos por troca
automática de vínculos.

## Execução interrompida e lock abandonado

O lock não expira automaticamente. Isso evita permitir uma segunda execução
enquanto uma primeira execução lenta ainda pode gravar. Se houver encerramento
forçado ou perda da resposta ao criar/liberar o lock:

1. Desabilite os disparos e confirme que não há execução Python ou Worker ativa.
2. Inspecione `rpa_locks/usuarios_legado` e o histórico de execução.
3. Remova somente esse documento de lock após confirmar a interrupção.
4. Execute uma simulação e depois tente novamente a sincronização.

Não apague documentos de usuários ou índices para liberar uma execução.
Nunca libere o lock apenas por sua idade.

## Transição da implementação SQL

As tabelas SQL antigas não são apagadas e não há importação automática de seus
hashes/vínculos. No ambiente diagnosticado elas estavam ausentes. Se outro
ambiente tiver histórico SQL, migre os vínculos e índices antes de habilitar
gravações ou revise cuidadosamente a reconciliação dos usuários existentes.
Desative executores antigos antes de usar a nova versão: eles não respeitam o
lock Firestore. Os adaptadores SQL continuam disponíveis para testes e código
legado, mas os pontos de entrada de produção selecionam Firestore explicitamente.

## Validação

Testes cobrem confirmação após commit, rollback, publicação incompleta,
conflitos, concorrência e leitura sem escrita. Os testes PostgreSQL opt-in
exercitam os cadastros sem criar tabelas SQL de controle; Firestore é substituído
por um double em memória nesses testes. Testes REST Worker também usam respostas
simuladas. Isso não substitui a verificação de IAM no ambiente de execução.
