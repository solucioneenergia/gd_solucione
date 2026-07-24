# Migração operacional da V1 para a V2

O script `scripts/migrate_v1_operational_data.py` migra somente o estado necessário
para continuidade operacional:

- `data/state/pipeline_cdp_state.json`;
- `data/cache/client_folder_cache.json`;
- arquivos válidos dentro de `data/downloads/`.

Nunca são migrados `.env`, ambientes virtuais, autenticação, cookies, sessões,
perfis do navegador, logs antigos, planilhas, arquivos temporários, bytecode ou
cache do pytest. PDFs fora de `data/downloads/` também não são migrados.

## Dry-run obrigatório antes do apply

O modo padrão não grava nada na V2:

```powershell
python scripts/migrate_v1_operational_data.py `
  --v1-root "C:\caminho\v1" `
  --v2-root "C:\caminho\v2"
```

`--dry-run` pode ser informado explicitamente e produz o mesmo comportamento.
Revise a lista de arquivos planejados antes de continuar.

## Aplicar a migração

```powershell
python scripts/migrate_v1_operational_data.py `
  --v1-root "C:\caminho\v1" `
  --v2-root "C:\caminho\v2" `
  --apply
```

Antes de substituir dados existentes, a V2 cria um backup com timestamp em
`data/migration_backups/<timestamp>/`. O apply gera:

- `data/logs/migracao_v1_v2.json`;
- `data/logs/migracao_v1_v2.md`.

Os relatórios registram itens planejados, migrados, ignorados, backups e erros.

## Verificação pós-migração

1. Confirme que `.env` e `data/auth/` da V2 não mudaram.
2. Confirme os backups em `data/migration_backups/`.
3. Revise os dois relatórios.
4. Execute `python -m pytest -q`.
5. Inicie com `DRY_RUN=true` e `MAX_COMPLETED_TO_PROCESS=1`.
