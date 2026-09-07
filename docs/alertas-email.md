# Alertas de falha por e-mail — SCRUM-185

Ao terminar uma execução com erro, o RPA tenta enviar um único e-mail para
`app.4str0@gmail.com`, usando a mesma conta como remetente. A tentativa ocorre
após sair dos contextos de banco e encerrar o histórico Firestore. Não abre
outra conexão PostgreSQL e não depende do Firebase para enviar.

Uma falha do histórico Firestore também gera alerta, mesmo que a sincronização
tenha retornado 0; nesse caso o assunto identifica um alerta de histórico.
Sucesso completo não envia e-mail. Simulações com erros e interrupções tratadas
(SIGTERM/Ctrl+C) também alertam. Falhas do próprio SMTP só são registradas no
terminal, sem alerta recursivo e sem alterar o código de saída original.

## Configuração local

Acrescente ao `.env` (não versione a senha):

```env
EMAIL_ALERTS_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=app.4str0@gmail.com
SMTP_PASSWORD=
EMAIL_FROM=app.4str0@gmail.com
EMAIL_TO=app.4str0@gmail.com
```

Preencha `SMTP_PASSWORD` com uma **senha de app** da conta Google remetente,
sem os espaços de apresentação. Não use a senha normal da conta. Para gerar
uma senha de app, a conta precisa ter verificação em duas etapas habilitada;
a disponibilidade depende das políticas da conta. Consulte as
[instruções do Google](https://support.google.com/accounts/answer/185833?hl=pt-BR).
As credenciais Firebase não autenticam o envio de e-mail por essa conta Gmail.

O envio usa STARTTLS obrigatório com verificação de certificado, porta 587 por
padrão e timeout de 10 segundos por operação de socket. Não existe fallback
para autenticação sem TLS nem retry automático. O timeout não é um prazo total
de 10 segundos para toda a conversa SMTP. A porta 465 exige SMTP SSL desde a
conexão e não é suportada por este cliente STARTTLS.

Para testar **somente o envio**, sem consultar bancos nem executar o RPA:

```powershell
.\.venv\Scripts\python.exe -m app.services.failure_alert --test
```

O assunto informa `Teste de alerta`. Código 0 indica aceitação pelo servidor
SMTP; confira também a caixa de entrada e spam. Aceitação não comprova entrega
na caixa de entrada. Sem senha configurada, há aviso e nenhum envio.
`EMAIL_ALERTS_ENABLED=false` desativa as notificações.

## Conteúdo e diagnóstico

O e-mail inclui `run_id`, início UTC, duração, modo, código de saída, sistema,
etapa, contagens disponíveis e orientações de recuperação. Exemplos:

- PostgreSQL legado: erro ao abrir conexão ou consultar as tabelas de origem.
- Validação do legado: IDs de funcionário/empresa, campos e motivos como
  quantidade de dígitos, dígitos verificadores, e-mail ambíguo ou vínculo de
  departamento inconsistente. Não inclui os valores desses campos.
- PostgreSQL destino: identidade, persistência, controle ou commit; SQLSTATEs
  conhecidos são traduzidos para unicidade, chave estrangeira, CHECK, campo
  obrigatório, tabela inexistente, permissão ou cancelamento.
- Firebase Authentication: motivo controlado de localização/criação ou
  divergência de identidade/UID.
- Firestore: indisponibilidade do histórico operacional.
- Aplicação: campos de configuração inválidos ou etapa da falha inesperada.

Há limite de 50 diagnósticos, com contador de itens omitidos. A simulação coleta
os erros encontrados; a gravação continua interrompendo no primeiro registro
inválido, para reverter a transação. O e-mail não afirma listar erros de
registros que não chegaram a ser processados.

Não são enviados tracebacks, SQL, mensagens brutas de drivers/SDKs, nomes,
CPF/CNPJ, e-mails de usuários, UIDs ou senhas. Exceções de domínio utilizam
mensagens controladas pelo código. Os IDs internos servem para localizar os
registros. O link lógico `rpa_execucoes/{run_id}` permite consultar o histórico
Firestore, se ele tiver sido salvo.

## GitHub Actions e limites

Adicione `SMTP_PASSWORD` como **secret** no environment `rpa` ou no repositório.
O workflow `sync.yml` já repassa esse segredo ao container e aceita as demais
configurações como variables. Cada execução agendada que falhar tenta enviar
seu próprio alerta; não há deduplicação entre execuções diferentes.

Os alertas são enviados pelo processo Python. Falhas anteriores à inicialização
do processo (checkout, build, runner), SIGKILL ou perda da máquina não permitem
garantir envio. Um limite externo pode interromper o SMTP mesmo depois de o
servidor ter recebido a mensagem; não há fila persistente para reenviar alertas.
Uma falha após commit não desfaz os cadastros confirmados. Contas Firebase
criadas antes de rollback continuam disponíveis para reaproveitamento.

Referências: [SMTP do Gmail](https://developers.google.com/workspace/gmail/imap/imap-smtp)
e [cliente SMTP Python](https://docs.python.org/3/library/smtplib.html).
