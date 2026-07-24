# Especificações orientadas a comportamento

## SPEC-001 — Configuração segura

**Dado** um endpoint CDP remoto, **quando** `ALLOW_REMOTE_CDP=false`, **então** a configuração/pipeline deve bloquear a execução.

Critérios:

- `PORTAL_GD_URL` deve ser HTTPS;
- limiares fuzzy devem estar entre 0 e 100;
- o limiar de revisão não pode superar o automático;
- em produção real, backup do Excel é obrigatório;
- caminhos relativos são resolvidos a partir da raiz do projeto.

## SPEC-002 — Persistência recuperável

**Dado** um checkpoint existente, **quando** um novo estado é salvo, **então** o arquivo oficial só deve ser substituído após a gravação completa do temporário.

Critérios:

- JSONs e relatórios usam temporário no mesmo diretório e `os.replace`;
- arquivos privados recebem permissão restrita quando suportado;
- workbook é salvo temporariamente, validado como XLSX e só então substituído;
- falhas removem o temporário e preservam o arquivo oficial.

## SPEC-003 — Entrada PDF confiável

Critérios:

- arquivo deve existir, ser regular, ter extensão `.pdf`, tamanho não nulo e abaixo do limite;
- os cinco primeiros bytes devem ser `%PDF-`;
- extração usa PyMuPDF e fallback pdfplumber;
- campos técnicos ausentes geram warnings de revisão.

## SPEC-004 — Arquivamento confinado

Critérios:

- protocolo deve ser um componente de caminho seguro;
- destino final deve estar dentro de `CLIENTES_ROOT`;
- cópia usa arquivo temporário e substituição atômica;
- cache fora da raiz atual é ignorado;
- entradas expiradas são ignoradas;
- fallback padrão envia para `_PENDENTES_CONFERENCIA_GD`.

## SPEC-005 — Execução offline

Critérios:

- simulação não altera Excel nem pastas;
- execução real faz simulação prévia;
- erros críticos bloqueiam a fase real;
- backup ocorre antes da primeira alteração do Excel;
- relatório consolida sucesso, erro, pendência, cache, Excel e arquivamento.

## SPEC-006 — Pipeline CDP

Critérios:

- login permanece manual;
- somente solicitações concluídas e elegíveis são selecionadas;
- limite é aplicado depois de remover duplicados e protocolos concluídos;
- estado permite retomada e reprocessamento forçado;
- paginação respeita flags e limites;
- falha de navegação produz relatório e não deve causar atualização parcial silenciosa.

## SPEC-007 — Aplicativo desktop

Critérios:

- UI não contém regra de negócio;
- tarefas longas não bloqueiam o loop do Tkinter;
- alterações reais exigem confirmação explícita;
- login manual é confirmado por caixa de diálogo;
- erros são convertidos em `OperationResult` e exibidos sem encerrar a aplicação.

## SPEC-008 — Qualidade e entrega

Critérios:

- `compileall` e pytest devem passar em Python 3.12/3.13;
- verificações fatais do Ruff devem passar;
- dependências são auditadas no CI;
- build não inclui `.env`, estado de autenticação ou dados reais;
- artefatos de produção são gerados por script reproduzível.
