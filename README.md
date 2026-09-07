# RPA de sincronização de usuários

Integração planejada entre PostgreSQL legado, Firebase Authentication e
PostgreSQL destino. O escopo inicial contempla usuários e seus dados relacionados,
com consulta incremental, validação, idempotência e recuperação de falhas parciais.

A estrutura inicial (SCRUM-178) inclui Python, dependências, configuração e Dockerfile.
A SCRUM-179 adiciona conexões com os dois PostgreSQL e Firebase Admin SDK.
A SCRUM-180 consulta funcionários, empresa, departamento e e-mails no legado.
A SCRUM-181 compara hashes desses dados com o histórico confirmado no destino.
O comando atual valida acesso aos três serviços, percorre os funcionários em lotes,
informa quantidades de novos, alterados e inalterados e a última sincronização.
Transformação, persistência de usuários e agendamento ficam para as próximas subtarefas.
Enquanto essa persistência não existir, o comando funciona como prévia e não confirma
hashes nem datas de sincronização.

Os campos, critérios de seleção e decisões pendentes estão documentados em
[Campos de usuários do legado](docs/campos-usuarios-legado.md).
O protocolo de confirmação, preparação das tabelas e limites da comparação estão em
[Controle de sincronização](docs/controle-sincronizacao.md).

## Execução local

Use Python 3.13 (versão da imagem Docker) ou superior. No PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Apenas se ainda não existir um .env:
Copy-Item .env.example .env
# Preencha o .env antes de executar.
# Prepare as tabelas de controle no destino uma vez, com SYNC_DRY_RUN=false:
.\.venv\Scripts\python.exe -m app.database.init_sync_control
.\.venv\Scripts\python.exe -m app.main
```

Execute a partir da raiz do projeto. Variáveis do ambiente têm prioridade sobre
o `.env`. Campos obrigatórios ausentes, portas inválidas e lote não positivo
encerram o comando com código 1, sem imprimir os valores recebidos.

## Variáveis de ambiente

O `.env.example` contém nomes e valores padrão sem credenciais reais.

| Variável | Uso |
| --- | --- |
| `FIREBASE_PROJECT_ID` | Projeto do Firebase Authentication. |
| `FIREBASE_CREDENTIALS_BASE64` | JSON da conta de serviço codificado em Base64; segredo obrigatório. |
| `LEGACY_DB_HOST` | Servidor PostgreSQL legado. |
| `LEGACY_DB_PORT` | Porta do legado; padrão `5432`. |
| `LEGACY_DB_NAME` | Nome do banco legado. |
| `LEGACY_DB_USER` | Usuário do legado, preferencialmente com acesso somente de leitura. |
| `LEGACY_DB_PASSWORD` | Senha do legado; segredo obrigatório. |
| `TARGET_DB_HOST` | Servidor PostgreSQL destino, configurado independentemente do legado. |
| `TARGET_DB_PORT` | Porta do destino; padrão `5432`. |
| `TARGET_DB_NAME` | Nome do banco destino. |
| `TARGET_DB_USER` | Usuário do destino com as permissões necessárias para persistência. |
| `TARGET_DB_PASSWORD` | Senha do destino; segredo obrigatório. |
| `SYNC_BATCH_SIZE` | Quantidade de usuários por lote; inteiro positivo, padrão `100`. |
| `SYNC_DRY_RUN` | Solicita simulação quando `true`; padrão existente `false`. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` ou `CRITICAL`; padrão `INFO`. |

Todos os campos de Firebase e banco são obrigatórios, exceto portas com padrão.
`SYNC_DRY_RUN=true` permite detectar alterações sem chamar o processador nem
alterar o controle. O `main` atual é uma prévia em ambos os modos; o comando
separado `init_sync_control` cria as tabelas no destino e exige `false`.
As credenciais Firebase são decodificadas em memória: devem ser um JSON de conta
de serviço válido e pertencer ao `FIREBASE_PROJECT_ID` informado. Nenhum arquivo
temporário de credenciais é criado.

## Conexões e integrações

`python -m app.main` inicializa uma instância própria do Firebase Admin SDK,
executa `SELECT 1` em cada PostgreSQL e realiza uma consulta ao Firebase
Authentication limitada a um usuário. Essa consulta verifica autenticação e
permissão de leitura, não imprime dados pessoais e não cria usuários.
O serviço Firebase Authentication deve estar habilitado, e a conta de serviço
deve ter permissão para listar usuários. Inicializar o SDK isoladamente não
comprova acesso remoto; a consulta realiza essa verificação.

Os engines usam SQLAlchemy com psycopg, URLs estruturadas para preservar senhas
com caracteres especiais, verificação de conexões do pool, timeout de conexão
de 10 segundos e limite de consulta de 30 segundos. O legado é configurado com
transações somente de leitura. O destino também usa somente leitura quando
`SYNC_DRY_RUN=true`; o fluxo futuro deverá respeitar essa opção no Firebase.
Mantenha permissões de leitura no usuário do legado como proteção no próprio banco.

Falhas identificam a integração afetada sem exibir mensagens brutas dos provedores
ou credenciais e retornam código 1. Engines e instância Firebase são liberados
inclusive quando uma etapa intermediária falha. Sucesso retorna código 0 e
confirma apenas acesso, não permissões de escrita ou sincronização concluída.

Referências: [engines SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/engines.html)
e [Firebase Admin SDK](https://firebase.google.com/docs/reference/admin/python/firebase_admin).

Base64 é uma codificação, não criptografia. Não versione `.env`, senhas ou contas
de serviço. No GitHub Actions, as credenciais deverão vir de GitHub Secrets.

## Docker

```powershell
docker build -t data-integration-rpa .
docker run --rm --env-file .env data-integration-rpa
```

O container executa como usuário sem privilégios. Apenas `app/` e
`requirements.txt` entram no contexto de build; o `.env` é fornecido na execução.
As dependências usam faixas compatíveis em `requirements.txt` e ainda não possuem
um arquivo de lock com todas as versões transitivas fixadas.

## Organização

```text
app/
  config/settings.py   # Leitura e validação das variáveis
  database/connections.py # Engines PostgreSQL e teste de conexão
  database/sync_schema.py # Tabelas do histórico confirmado no destino
  database/init_sync_control.py # Preparação explícita das tabelas de controle
  models/legacy_user.py # Dados brutos do funcionário e seus e-mails
  repositories/legacy_user_repository.py # Consulta do legado por lotes
  repositories/sync_state_repository.py # Hashes e última sincronização
  services/change_tracking_service.py # Detecção e protocolo de confirmação
  services/firebase_service.py # Credenciais e acesso ao Firebase Auth
  services/integrations.py # Ciclo de vida das três integrações
  main.py              # Ponto de entrada
```

Stack: SQLAlchemy, psycopg, Pydantic, Firebase Admin SDK e python-dotenv.
O carregamento tipado do ambiente utiliza
[pydantic-settings](https://pypi.org/project/pydantic-settings/).

## Validação

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Os testes automatizados usam serviços simulados e SQLite em memória para executar
as consultas, sem ler credenciais reais nem acessar a rede.
Para validar o ambiente real, execute `python -m app.main` com
o `.env` preenchido e os serviços acessíveis.
