# data-integration-rpa no Cloudflare

Worker nativo publicado em `https://data-integration-rpa.app-4str0.workers.dev`, usando os limites padrão do Workers Free. O trigger está configurado para rodar uma vez por hora, no minuto 17 (UTC), em modo somente leitura (`SYNC_DRY_RUN=true`). O GitHub Actions permanece operacional até a comparação ponta a ponta.

Consulte [Migração Workers Free](WORKERS-FREE-MIGRATION.md) para arquitetura, limites e pendências.

## Rotas

- `GET /health`: verificada com resposta `status=ok`.
- `GET /status`: cron e estado de ativação; requer `Authorization: Bearer JOBS_TOKEN`.
- `POST /jobs/sync-users`: execução autenticada; sem parâmetro, `dry_run=true`. Enquanto `SYNC_DRY_RUN=true`, qualquer pedido de gravação é recusado.

O cron registrado é `17 * * * *` (uma vez por hora, no minuto 17, UTC). `API_ENABLED=true`, `JOBS_ENABLED=true` e `SYNC_DRY_RUN=true`: a rotina agenda a leitura e comparação dos dados, sem persistir no destino ou criar contas no Firebase. O dry-run remoto iniciado pelo cliente local falhou antes da requisição por um erro de TLS; confirme os resultados no histórico de eventos do Worker. Os IDs Hyperdrive apontam para o conector de origem do RPA e o conector de destino existente.

## Custo e segredos

O desenho usa Worker nativo e dois bindings Hyperdrive, sem Containers ou override de CPU. A conta Free aplica o limite padrão de CPU; a Cloudflare recusou a publicação enquanto havia um `cpu_ms` explícito. Os Secrets `FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_BASE64` e `JOBS_TOKEN` foram configurados no Worker. As conexões PostgreSQL são fornecidas pelos bindings Hyperdrive. Os valores secretos não são armazenados neste repositório.

As execuções do Cloudflare permanecem em dry-run. Desative o cron do GitHub Actions e habilite gravações somente após comparar uma simulação bem-sucedida com a execução Python e validar as conexões reais. O Workers Free limita CPU de Cron a 10 ms; monitore erros de limite e a cota diária do Hyperdrive antes de qualquer execução de escrita.

## Exportação Grafana

O Worker envia eventos resumidos de sucesso ou falha em OTLP/HTTP para
GRAFANA_OTLP_ENDPOINT; GRAFANA_OTLP_HEADERS é um Secret do Worker. Os eventos
não incluem registros de usuários ou credenciais, e uma falha do Grafana não
interrompe a sincronização.
