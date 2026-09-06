# RPA de sincronização de usuários

Integração planejada entre PostgreSQL legado, Firebase Authentication e
PostgreSQL destino. O escopo inicial contempla usuários e seus dados relacionados,
com consulta incremental, validação, idempotência e recuperação de falhas parciais.

Esta entrega (SCRUM-178) prepara a estrutura Python, as dependências, a configuração
e o Dockerfile. O comando atual apenas valida a configuração e encerra: conexões,
sincronização, controle incremental e agendamento serão implementados nas próximas subtarefas.

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
As credenciais Firebase serão decodificadas e verificadas na etapa de integração;
a configuração inicial verifica apenas seu preenchimento.

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
  database/            # Futuras conexões PostgreSQL
  models/              # Futuros modelos de usuários
  repositories/        # Futuras consultas e persistência
  services/            # Futuro fluxo de sincronização e Firebase
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
