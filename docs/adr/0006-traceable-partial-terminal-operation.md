# ADR 0006 — Operacao terminal parcial rastreavel por protocolo

Status: aceita
Data: 2026-08-09
Decisores: Solicitante e Engineering Orchestrator / Codex
SPEC relacionada: [SPEC-009](../../specs/SPEC-009-terminal-production-readiness.md)
Substitui: nenhuma
Substituida por: nenhuma

## Contexto

O pipeline altera um workbook e tambem pode copiar PDFs e persistir state/relatorios. Esses
efeitos podem ocorrer em volumes diferentes e nao compartilham uma transacao atomica.

## Problema arquitetural

Prometer rollback total exigiria apagar ou restaurar arquivos de destino, inclusive quando um
arquivo anterior ja existia, e compensar falhas da propria compensacao. O comportamento atual
restaura apenas o workbook, mas documentos anteriores descrevem o lote como integralmente
atomico.

## Forcas de decisao

Protecao do workbook, preservacao de evidencia, compatibilidade Windows/rede, idempotencia,
retomada segura, mudanca minima e ausencia de banco/transacao distribuida.

## Decisao

Adotar `OPERATION_PARTIAL_TRACEABLE_BY_PROTOCOL`.

- O workbook continua atomico por substituicao e pode ser restaurado de backup validado em
  falha sistemica.
- PDF ja arquivado nao e removido automaticamente.
- State e relatorios registram os efeitos observados e eventual acao manual.
- Retomada revalida workbook, arquivo e state antes de pular ou reaplicar.
- Sucesso so e declarado quando os efeitos exigidos e a evidencia minima foram persistidos.
- Relatorios sao separados em `PRIVATE_OPERATIONAL` e `SHAREABLE` agregado.

## Alternativas consideradas

- Transacao total do lote: rejeitada nesta etapa por exigir compensacao destrutiva entre
  volumes e backup dos destinos.
- Transacao apenas por workbook sem rastrear outros efeitos: rejeitada por produzir falso
  senso de atomicidade.
- Remover rollback do workbook: rejeitada por elevar risco de perda de dados.

## Consequencias positivas

O contrato passa a refletir os limites reais; falhas ficam retomaveis e auditaveis sem apagar
evidencia ou arquivos automaticamente.

## Consequencias negativas

Algumas falhas exigem acao manual. Operador e testes precisam interpretar estados de efeito,
nao apenas um booleano global.

## Riscos

State atrasado em relacao ao efeito real; mitigado por revalidacao de artefato/fingerprint e
falha fechada. Falha do relatorio; mitigada por regeneracao a partir do state privado.

## Seguranca

Relatorio compartilhavel nao contem linhas por protocolo, caminhos, nomes ou texto livre.
Compensacao nunca apaga arquivo automaticamente.

## Compatibilidade

Preserva Excel atomico da ADR 0002 e a arquitetura incremental da ADR 0001. Nao altera o
desktop nem remove legado.

## Plano de implementacao

Adicionar estados de efeito, validacao de restauracao, retomada idempotente, classificacao de
relatorios e testes de falha depois de cada fronteira.

## Plano de rollback

Reverter os novos campos e voltar ao resultado anterior, preservando backups e arquivos. Nao
executar compensacao destrutiva durante rollback de codigo.

## Validacao da decisao

Testes sinteticos de falha apos Excel, arquivo, state e relatorio; restauracao por hash/XLSX;
retomada sem duplicacao; allowlist compartilhavel recursiva.

## Evidencias

Registradas no relatorio final da Etapa 1.

## Referencias

- [SPEC-009](../../specs/SPEC-009-terminal-production-readiness.md)
- [ADR 0002](0002-atomic-local-persistence.md)
