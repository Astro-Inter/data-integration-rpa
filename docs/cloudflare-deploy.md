# Deploy Cloudflare

O workflow cloudflare.yml valida o Worker em PRs, sem credenciais de produção. Após os checks, publica somente pushes na main ou execução manual na main; mantém os workflows existentes. Deploys de produção são serializados.

Diretório: cloudflare. Comando: npm run deploy. As validações incluem npm run check; npm test; npm run deploy -- --dry-run. A instalação usa o lockfile existente quando disponível.

CLOUDFLARE_API_TOKEN fica nos secrets do repositório; CLOUDFLARE_ACCOUNT_ID é uma variável pública. O token é restrito à conta Astro, com Workers Scripts: Edit, e expira em 25/10/2027. Os secrets de aplicação, Hyperdrives e demais bindings existentes permanecem no Cloudflare; este workflow não importa arquivos .env nem provisiona conectores.

A configuração entra em vigor após a revisão e o merge desta PR. Nenhum deploy ocorre ao atualizar a branch da PR.
