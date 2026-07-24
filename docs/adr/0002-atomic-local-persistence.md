# ADR 0002 — Persistência local atômica

**Status:** aceito

Checkpoints, caches, relatórios e Excel são críticos para retomada e auditoria. A gravação direta foi substituída por temporário no mesmo volume, validação quando aplicável e `os.replace`.
