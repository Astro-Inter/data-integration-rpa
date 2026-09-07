# Automação e operação — SCRUM-185

## GitHub Actions

`ci.yml` valida pushes, pull requests e execuções manuais. Constrói a imagem de
testes, executa a suíte contra PostgreSQL 16 descartável e verifica que a imagem
final roda sem root, sem `.env` e sem testes. Não usa credenciais de produção.

`sync.yml` executa o RPA manualmente ou a cada hora, no minuto 17 (cron em UTC).
O horário agendado pode sofrer atrasos no GitHub; não é uma garantia de execução
em tempo real. Agendamentos usam a branch padrão do repositório. O acionamento
manual oferece `dry_run`, marcado por padrão; desmarcá-lo habilita gravação.

O agendamento começa desabilitado. Para configurar:

1. Publique os workflows na branch padrão após a revisão e os testes.
2. Crie o environment `rpa` no GitHub, com as proteções de branch/aprovação que
   a equipe utiliza para esse ambiente.
3. Configure os secrets abaixo no environment `rpa` ou no repositório:
   `FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_BASE64`, `LEGACY_DB_HOST`,
   `LEGACY_DB_NAME`, `LEGACY_DB_USER`, `LEGACY_DB_PASSWORD`, `TARGET_DB_HOST`,
   `TARGET_DB_NAME`, `TARGET_DB_USER` e `TARGET_DB_PASSWORD`.
   Os secrets `LEGACY_DB_PORT` e `TARGET_DB_PORT` são opcionais: padrão `5432`.
4. Garanta acesso de rede do runner aos bancos e ao Firebase. O workflow usa
   `ubuntu-latest`; bancos acessíveis apenas na rede interna exigem conectividade
   privada configurada ou mudança para um runner próprio com Docker. `localhost`
   no container representa o próprio container, não o banco da máquina local.
5. Prepare o schema de negócio e execute `python -m app.database.init_sync_control`
   uma vez no destino, com as credenciais e `SYNC_DRY_RUN=false`. O agendamento
   não cria tabelas automaticamente.
6. Execute manualmente com `dry_run=true`, confira os logs e depois valide uma
   execução de gravação no ambiente escolhido. Simulação não testa escrita,
   criação Firebase ou todos os conflitos de identidade do destino.
7. Para ligar a execução horária com gravação, crie a variável **do repositório**
   `RPA_SCHEDULE_ENABLED=true`. Remova-a ou defina `false` para desligar somente
   o agendamento. A variável opcional `SYNC_BATCH_SIZE` define o lote (padrão 100).

Nenhum secret é gravado em arquivo ou passado como valor literal na linha de
comando do Docker. O processo recebe variáveis de ambiente. A imagem não contém
as credenciais. Ambos os workflows usam permissão mínima `contents: read` e
checkout sem persistência de credenciais Git.

Há um grupo de concorrência para a sincronização, com `cancel-in-progress: false`:
uma nova execução não cancela a que já está rodando. O GitHub pode substituir uma
execução pendente por outra mais recente; não se trata de uma fila de todos os
disparos. O lock no PostgreSQL também protege contra duas gravações simultâneas
do RPA iniciadas fora do Actions.

## Limites e encerramento

O container de sincronização tem limite de 30 minutos. `timeout` envia SIGTERM
e permite até 30 segundos antes de forçar o encerramento. Há uma etapa final de
limpeza de container residual, inclusive após falha. O job inteiro tem limite
de 40 minutos, incluindo build. Ajuste esses limites conforme o volume real.

O processo Python trata SIGTERM e Ctrl+C, sai dos contextos de conexão para
reverter transações abertas e libera as integrações. Um encerramento forçado ou
perda do runner não permite garantir execução dos handlers; o PostgreSQL reverte
a transação aberta quando percebe a desconexão. Um commit já concluído permanece
válido, mesmo que o encerramento ou a publicação dos logs falhe depois.

| Código do processo | Significado |
| --- | --- |
| `0` | Sincronização ou simulação concluída. |
| `1` | Configuração, validação, integração, identidade, SQL ou falha inesperada. |
| `130` | Interrupção pelo operador (Ctrl+C). |
| `143` | Interrupção por SIGTERM. |

O utilitário externo `timeout` pode retornar `124` ao atingir seu prazo ou `137`
quando precisa forçar o encerramento. O Actions marca esses casos como falha.

## Logs e falhas parciais

Cada execução registra início e fim com `run_id`; o encerramento inclui
`exit_code` e `duracao_segundos`. Os demais logs informam modo de execução,
integrações, totais, novos/alterados/inalterados, validação e última sincronização.
O workflow publica o resultado no resumo do job. Mensagens brutas de drivers,
SDKs e exceções inesperadas não são impressas, pois podem conter dados pessoais
ou segredos. O Actions fixa `LOG_LEVEL=INFO`.

O [histórico Firestore](logs-firestore.md) salva etapas, contagens e resultado em
`rpa_execucoes/{run_id}`, inclusive na simulação. O workflow repassa as variáveis
`FIRESTORE_DATABASE_ID` (padrão `(default)`) e `FIRESTORE_LOGS_ENABLED` (padrão `true`).
A conta de serviço precisa de permissão para gravar no Firestore. Uma falha no
histórico é informada no terminal e não interrompe a sincronização.

O [alerta por e-mail](alertas-email.md) usa `app.4str0@gmail.com` como remetente e
destinatário. Configure o secret `SMTP_PASSWORD` no environment `rpa` ou no
repositório. O workflow repassa também as variables opcionais `EMAIL_ALERTS_ENABLED`,
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `EMAIL_FROM` e `EMAIL_TO`, com os padrões
documentados. O alerta inclui o sistema afetado, etapa, causas controladas,
campos e IDs de registros inválidos, quando disponíveis.

Todas as gravações no PostgreSQL destino de uma execução continuam na mesma
transação. Uma falha no segundo lote também reverte o primeiro, incluindo
workspace, unidade, cargo, usuário, vínculo e hashes. A última sincronização só
avança após conclusão. O lote limita a leitura em memória, não o tamanho da
transação no destino. Em modo de gravação, um candidato inválido interrompe a
execução; em simulação, os erros de validação são contados e o comando retorna 1.

Firebase não participa dessa transação. Se uma conta for criada antes de uma
falha no destino, ela permanece no Firebase. Na próxima execução, a conta é
localizada por identidade/e-mail e o UID é reaproveitado. O RPA não apaga contas
como compensação nem marca usuários como sincronizados antes da persistência.
Depois de corrigir a causa indicada nos logs, execute o workflow novamente.
Não há repetição automática imediata de toda a transação; o próximo disparo
manual ou horário retoma usando o controle confirmado.

## Testes do fluxo completo

Os testes locais padrão não precisam de serviços externos:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Para habilitar também os quatro testes PostgreSQL, use um banco **descartável**
acessível e uma conta de teste com permissão de criar schemas:

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://rpa_test:rpa_test@localhost:5432/rpa_test'
try {
    .\.venv\Scripts\python.exe -m unittest discover -s tests -v
} finally {
    Remove-Item Env:TEST_POSTGRES_URL
}
```

A suíte cria dois schemas com nomes `rpa_test_<uuid>` para cada caso e remove
somente esses schemas ao encerrar. Não usa `.env` nem acessa Firebase real.
Uma interrupção forçada pode deixar schemas de teste no banco descartável.
Os testes passam pelo `main`, pelas consultas e pela persistência reais, cobrindo:

- Simulação, criação, repetição sem duplicatas e atualização preservando perfil.
- Herança de `conta` e diferenças entre consultar a tabela pai e `ONLY conta`.
- Falha no segundo lote depois da criação Firebase, rollback total e retomada
  reaproveitando os UIDs sem criar contas duplicadas.
- Lock concorrente de sincronização e bloqueio de escrita no legado.

A suíte também testa logs sanitizados, códigos de saída, restauração do handler
de SIGTERM e rollback por interrupção. A fixture PostgreSQL reproduz as tabelas
usadas pelo RPA; não valida todas as tabelas, RLS ou permissões do aplicativo.
Os testes não comprovam credenciais e permissões de escrita no Firebase real.

Referências: [agendamentos do Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
e [concorrência](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
