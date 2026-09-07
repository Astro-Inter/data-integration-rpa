# Mapeamento e validação — SCRUM-182

As regras usam o script legado, o schema atual e as constraints fornecidas nesta
conversa. A consulta da SCRUM-180 já contém os campos necessários; não foi ampliada.

## Mapeamento decidido

| Origem | Destino preparado | Regra |
| --- | --- | --- |
| `empresa.nome`, `empresa.cnpj` | `workspaces.nome`, `workspaces.cnpj` | Empresa representa workspace; localização futura por CNPJ normalizado. |
| `departamento.nome` | `unidades.nome` | Departamento representa unidade organizacional no workspace da empresa. |
| `funcionario.cargo` | `cargos.nome` | Cargo pertence ao mesmo workspace da unidade. |
| `funcionario.nome` | `usuarios.nome`, Firebase `display_name` | Preservar grafia e acentos, normalizar espaços. |
| `funcionario.cpf` | `usuarios.cpf` | CPF obrigatório nesta integração e com dígitos verificadores válidos. |
| `email.email` | `usuarios.email`, Firebase `email` | Exigir um endereço válido único após normalização. |
| Sem perfil no legado | `usuarios.tipo` | Criação como `FUNCIONARIO`, sem promoção a gestor. |
| Sem situação no legado | `usuarios.status` | Criação como `PRE_CADASTRADO`, conforme default do banco. |
| Sem modalidade | `usuarios.modalidade` | Omitida; nula na criação. |
| Sem data equivalente | `usuarios.criado_em` | Omitida; banco aplica `CURRENT_TIMESTAMP` na criação. |
| Retorno futuro do Firebase | `usuarios.firebase_uid` | UID real, nunca gerado por esta preparação. |

`usuarios` herda nome, e-mail e UID de `conta`: esses campos entram na inserção de
`usuarios`, sem uma segunda inserção independente em `conta`. Não há workspace_id
direto em usuarios: o vínculo usa cargo_id e unidade_id resolvidos no destino.

Unidade e cargo serão localizados por `(workspace_id, nome)`, conforme as constraints.
Nomes têm espaços normalizados, mas caixa e acentos são preservados; não há regra
case-insensitive inventada. Departamentos com o mesmo nome normalizado no mesmo
workspace correspondem à mesma unidade por essa chave. IDs legados são referências
de origem, não IDs do destino. Cargo e unidade recebem `ativo=true` somente na
criação. Entidades já desativadas não devem ser reativadas automaticamente.

## Validação

- Nomes usam Unicode NFC, espaços aparados e espaços internos reduzidos. Pessoa,
  empresa, unidade e cargo exigem 2 a 255 caracteres. Não há truncamento nem
  conversão para título que alteraria siglas ou sobrenomes.
- CPF e CNPJ numérico removem pontuação conhecida e preservam zeros. Rejeitam
  letras, dígitos Unicode, sequências repetidas, tamanho incorreto e dígitos
  verificadores inválidos. Não inventam zeros ausentes. O CNPJ segue o formato
  numérico exigido pela constraint fornecida.
- A checagem de dígitos é mais restritiva que a constraint de formato do banco;
  não comprova existência ou situação cadastral. Apesar de o destino aceitar CPF
  nulo, esta integração exige CPF porque é a identificação obrigatória do legado.
- E-mails são aparados e convertidos para minúsculas. `email-validator` valida
  sem DNS/teste de entrega, converte domínio internacional para ASCII e rejeita
  local-part que exige SMTPUTF8. Limite de 254 caracteres; sem nome de exibição.
- E-mails nulos/brancos são ignorados; duplicatas normalizadas contam como um.
  Ausência, qualquer valor preenchido inválido ou múltiplos endereços distintos
  invalidam o usuário. Não se escolhe arbitrariamente um login principal.
- Empresa/departamento devem existir e ter os campos necessários. A empresa do
  departamento precisa coincidir com a do funcionário. IDs de origem zero ou
  negativos são preservados, já que o legado permite identidade explícita.

## Contratos produzidos

`prepare_user(LegacyUser)` retorna `PreparedUser` com modelos Pydantic imutáveis,
sem modificar os dados brutos. Métodos para as próximas etapas:

- `workspace_payload()`: nome e CNPJ, sem IDs de origem.
- `unit_insert_payload(workspace_id=...)` e `job_insert_payload(workspace_id=...)`:
  nome, ID real do workspace e ativo=true.
- `firebase_payload()`: email, display_name e email_verified=false; sem senha ou
  UID fictícios. É payload de criação, não deve redefinir verificação de conta existente.
- `target_insert_payload(firebase_uid=..., cargo_id=..., unidade_id=...)`: exige
  referências resolvidas e usa FUNCIONARIO/PRE_CADASTRADO.
- `target_update_payload(...)`: preserva tipo, status, modalidade e criado_em
  já definidos no aplicativo, enviando apenas identidade e referências do legado.

Os modelos verificam formatos, não existência de IDs nem unicidade global. A
persistência futura deve conferir que cargo e unidade pertençam ao mesmo workspace
e verificar conflitos de CPF, e-mail, UID ou CNPJ antes de vincular contas. Nunca
unir pessoas diferentes somente por terem o mesmo e-mail.

## Controle e limites

Novos/alterados são preparados antes de chamar o processador. O fingerprint passou
à versão 2 para reavaliar estados anteriores a essas regras. Na prévia, inválidos
são contados por campo, sem dados pessoais nos logs, e o comando retorna 1.
Hashes/datas não avançam. Com processador, erro de validação aborta a transação
externa e reverte também os registros anteriores. O processador da SCRUM-181
agora recebe PreparedUser, não LegacyUser.

Nenhum cadastro é criado nesta entrega. Endereços não são preparados: a relação
é opcional, mas quando existe exige campos que não podem ser resolvidos a partir
dos inteiros do legado sem suas tabelas de referência. Também ficam fora CNAE,
riscos, NRs, certificados, treinamentos, fotos e administradores.

Referências: [Pydantic](https://docs.pydantic.dev/latest/concepts/validators/),
[email-validator](https://pypi.org/project/email-validator/) e
[Firebase](https://firebase.google.com/docs/auth/admin/manage-users).
