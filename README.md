# RPA de sincronização de usuários

Integração planejada entre PostgreSQL legado, Firebase Authentication e
PostgreSQL destino. O escopo inicial contempla usuários e seus dados relacionados,
com consulta incremental, validação, idempotência e recuperação de falhas parciais.

A estrutura inicial (SCRUM-178) inclui Python, dependências, configuração e Dockerfile.
A SCRUM-179 adiciona conexões com os dois PostgreSQL e Firebase Admin SDK.
O comando atual valida configuração e acesso aos três serviços, libera os recursos
e encerra. Sincronização, controle incremental e agendamento ficam para as próximas subtarefas.

## Execução local

Use Python 3.13 (versão da imagem Docker) ou superior. No PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Apenas se ainda não existir um .env:
Copy-Item .env.example .env
# Preencha o .env antes de executar.
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
A configuração carrega `SYNC_DRY_RUN`, mas a execução de simulação depende da
implementação futura do fluxo. Nesta etapa, nenhum modo altera sistemas externos.
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
  models/              # Futuros modelos de usuários
  repositories/        # Futuras consultas e persistência
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

Os testes automatizados usam serviços simulados, sem ler credenciais reais nem
acessar a rede. Para validar o ambiente real, execute `python -m app.main` com
o `.env` preenchido e os serviços acessíveis.
