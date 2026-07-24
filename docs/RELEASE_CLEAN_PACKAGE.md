# Pacote de release limpo

Gere a release fora da árvore do projeto:

```powershell
python scripts/create_clean_release_zip.py `
  --project-root "." `
  --output "..\gd_neoenergia_release.zip"
```

O script exclui `.env`, `.venv`, autenticação, perfis do Edge, downloads, logs,
estado, cache, backups de migração, PDFs, planilhas, bytecode, temporários e cache
do pytest. Código-fonte, README, documentação, requirements, `.env.example`,
scripts e testes permanecem no ZIP.

O ZIP é validado automaticamente. Se houver caminho sensível ou faltar um item
obrigatório, a validação reprova, o ZIP é removido e o comando termina com erro.
Os relatórios são gravados fora do ZIP em:

- `data/logs/release_validation.json`;
- `data/logs/release_validation.md`.

Uma validação separada pode ser executada com:

```powershell
python scripts/validate_release_zip.py "..\gd_neoenergia_release.zip"
```

## Checklist antes da homologação

1. Execute `python -m pytest -q`.
2. Execute `python -m compileall automacao_gd`.
3. Gere o ZIP pelo script, nunca por compactação manual da pasta.
4. Confirme `Status: APROVADO` no relatório Markdown.
5. Abra o ZIP e verifique a presença de `.env.example`, README, docs e código.
6. Confirme a ausência de `.env`, PDFs, XLSX, sessão, perfis, logs e estado.
7. Entregue somente o ZIP aprovado; não entregue os relatórios operacionais.
