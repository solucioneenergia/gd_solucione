# SPEC-005 - Saneamento controlado da planilha

Status: aprovado para aplicacoes direcionadas com confirmacao forte.

## Objetivo

Definir o contrato operacional para saneamentos pontuais na planilha oficial quando a reconciliacao Portal GD x planilha indicar um registro ausente, divergente ou protegido para revisao.

Esta SPEC nao autoriza correcoes em massa. Cada escrita real deve ter plano, pre-voo, confirmacao forte e allowlist propria.

## Escopo

Aplicacoes direcionadas podem:

- inserir um protocolo especifico previamente aprovado;
- atualizar somente campos explicitamente allowlistados;
- copiar estilo de uma linha canonica compativel;
- gerar backup, temporario, comparacao integral, substituicao atomica, rollback e idempotencia.

Aplicacoes direcionadas nao podem:

- inferir dados tecnicos;
- excluir protocolos;
- mover linhas sem plano especifico;
- corrigir conclusoes historicas em massa;
- preencher equipamentos sem fonte documental completa;
- reaplicar backfills historicos.

## Confirmacao forte

Toda escrita real exige uma frase exata em interacao separada. Para o protocolo 2607077271, a frase autorizada foi:

```text
APLICAR SANEAMENTO DIRECIONADO DO PROTOCOLO 2607077271
```

Qualquer variacao deve bloquear a aplicacao.

## Seguranca do workbook

O fluxo obrigatorio e:

```text
pre-voo final
-> confirmacao forte
-> backup integral validado por SHA
-> temporario unico no mesmo volume
-> alteracao somente no temporario
-> comparacao integral
-> validacao da allowlist
-> os.replace unico
-> reabertura e validacao final
-> idempotencia read-only
```

Em falha apos a substituicao, o backup validado deve ser restaurado e o SHA anterior deve ser confirmado.

## Allowlist

A allowlist deve ser declarada por protocolo e por campo. Para insercao direcionada, as mudancas permitidas sao apenas:

- criacao da nova linha aprovada;
- valores e tipos dos campos autorizados;
- estilo da nova linha copiado de linha canonica compativel;
- altura da nova linha, quando necessaria;
- extensao minima de filtro apenas quando o filtro existente ja cobre a area de dados.

Mudancas em linhas existentes, formulas, cabecalhos, larguras, mesclagens, filtros historicos incompletos e configuracao de impressao sao bloqueantes.

## Proibicao de inferencia

Casos com multiplos fabricantes/modelos e quantidade total nao separada por item permanecem protegidos como `SOURCE_INCOMPLETE`. A automacao nao deve dividir quantidades por inferencia.

## Evidencia da aplicacao 2607077271

Aplicacao direcionada executada em 2026-07-28:

- plan hash: `5323c7acabc57fee3fd946d1dc176de5b07f340d79668f1c8e8d465aa7fed3c1`
- preflight hash: `ce8a359e673d593c3bd5e80da7ce6d82f30734cc9ea77e33af508d66ea79dce8`
- SHA anterior: `6b0c2dd541050526297e5ba611ee97d15e040a3bcd86ceda3cceedd44bb6f4a5`
- SHA final: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`
- protocolo inserido: `2607077271`
- aba: `2026`
- linha: `110`
- rollback: nao necessario
- idempotencia: aprovada

## Rollback

Rollback deve restaurar o backup validado e confirmar SHA igual ao estado anterior. Apos rollback, nenhuma nova escrita deve ocorrer antes de diagnostico.
