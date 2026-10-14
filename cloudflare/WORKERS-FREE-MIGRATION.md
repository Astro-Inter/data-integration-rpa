# Migração do data-integration-rpa para Workers Free

## Estado

O serviço Python atual usa SQLAlchemy/psycopg, Firebase Admin SDK, Firestore e SMTP. O cron do GitHub Actions executa às 17 minutos de cada hora e continua ativo até que o substituto passe validação ponta a ponta. O Worker também está configurado para executar uma vez por hora, no minuto 17 (UTC), em dry-run.

A proposta anterior executava a imagem Python por Cloudflare Containers, que exige plano Workers pago. Esse caminho foi removido do pacote; a configuração atual não usa Containers.

O port nativo agora inclui `domain.ts`, `repositories.ts`, `target-user-repository.ts`, `sync-runner.ts`, `firebase-auth.ts`, `hyperdrive.ts` e `worker.ts`. O Worker oferece `/health`, `/status` autenticado e `POST /jobs/sync-users`; o cron registrado é `17 * * * *`. A execução agendada opera em dry-run, sem escrita no destino ou criação de usuários Firebase. A API manual também recusa pedidos de escrita enquanto `SYNC_DRY_RUN=true`. Firebase segue o endpoint de criação do Admin SDK oficial, com OAuth de conta de serviço e sem senha ([implementação oficial](https://github.com/firebase/firebase-admin-python/blob/main/firebase_admin/_user_mgt.py#L634-L652)).

O `wrangler.jsonc` declara o Hyperdrive de origem `data-integration-rpa-legacy` e reutiliza o destino `astro-email-db`. Ambos estão vinculados ao Worker publicado em `https://data-integration-rpa.app-4str0.workers.dev`. A primeira tentativa de deploy foi recusada porque Workers Free não aceita `limits.cpu_ms` explícito; a configuração agora usa o limite padrão do plano, sem Containers nem override de CPU. `API_ENABLED=true`, `JOBS_ENABLED=true`, `SYNC_DRY_RUN=true` e cron `17 * * * *`. A rotina agendada consulta e compara os registros, mas não escreve no destino nem cria usuários no Firebase. Os Secrets de Firebase e autenticação da API estão configurados diretamente no Cloudflare. `/health` respondeu corretamente. O teste remoto de `POST /jobs/sync-users` não foi concluído porque o ambiente local falhou na negociação TLS; confirme os eventos de cron no dashboard antes de ativar gravações. A rotina e o schedule do GitHub Actions seguem intactos.

## Arquitetura e limites

- O legado é consultado em sessão somente leitura; o destino tem uma única transação pela execução, com identidade, vínculos, persistência e confirmação de hashes.
- O port preserva o hash versão 3, validação CPF/CNPJ, resolução de conflito de identidade e recuperação após resposta ambígua do Firebase.
- Firebase Authentication usa REST e assinatura OAuth Web Crypto. Logs não registram emails, identificadores pessoais, respostas Firebase nem strings de conexão.
- Firestore e SMTP ainda não foram portados. Não devem ser habilitados até existir implementação HTTP compatível e testes.
- Uma fila por usuário mudaria a semântica para commits parciais e incrementais. Ela não será usada sem decisão explícita para aceitar essa alteração.
- A varredura inteira ainda não foi medida no limite padrão de 10 ms de CPU por execução do Workers Free. Se exceder o limite, não há migração equivalente pronta dentro do Free mantendo rollback global.

## Validação e troca

1. Validar a execução ponta a ponta com PostgreSQL descartável e Firebase Emulator ou mocks, usando dados sintéticos.
2. Medir o volume e o CPU real do Cron Free sem credenciais de produção.
3. Configurar IDs Hyperdrive para os bancos existentes de origem e destino; não criar banco nem mudar plano.
4. Os Secrets de Firebase e autenticação já foram configurados; concluir dry-run autenticado sem escrita quando o acesso TLS do ambiente permitir.
5. Comparar a saída do dry-run com a execução Python e revisar os eventos do cron; observar o limite de CPU de 10 ms e a cota diária do Hyperdrive no Free.
6. Só após uma execução comprovada desativar o schedule do GitHub Actions e, em implantação separada, habilitar gravações mudando `SYNC_DRY_RUN`. Não executar os dois agendadores com escrita em paralelo.

## Verificação local

Em 2026-10-01, a suíte Python existente passou: 109 testes OK; quatro testes PostgreSQL foram pulados por falta de `TEST_POSTGRES_URL`. A suíte Worker passou: 29 testes, incluindo política, domínio, Firebase REST simulado, persistência, rollback, endpoints e bloqueio de escrita durante dry-run. `npm run check` também passou. O Worker e o trigger foram publicados; `/health` respondeu corretamente. O teste remoto de simulação e de CPU do Free ainda depende de uma execução Cron observada.
