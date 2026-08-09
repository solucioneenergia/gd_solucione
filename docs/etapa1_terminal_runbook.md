# Runbook da Etapa 1 — terminal seguro

Status: preparado para homologação sintética; não autoriza produção
SPEC: [SPEC-009](../specs/SPEC-009-terminal-production-readiness.md)

## Regras de parada

Interrompa a operação se qualquer gate estiver reprovado, se o limite for maior que 5, se a
árvore Git estiver suja, se o lote mudar depois do congelamento ou se o workbook não puder ser
substituído atomicamente. Não improvise fallback por cópia sobre o workbook oficial.

Portal, Edge/CDP, workbook e pastas reais exigem autorização humana explícita separada. Este
documento não concede essa autorização.

## Homologação offline

1. Use somente fixtures sintéticas e diretórios temporários.
2. Execute a suíte e os gates:

   ```powershell
   python -m pytest -q
   python -m ruff check automacao_gd apps src scripts tests
   python -m mypy automacao_gd
   python scripts/validate_engineering_foundation.py
   ```

3. Execute o scanner da árvore e dos artefatos sintéticos conforme a ajuda do comando:

   ```powershell
   python scripts/privacy_scan.py --help
   ```

4. Valide o dry-run integrado e o ensaio de restauração/retomada na suíte da SPEC-009.

### Equivalente Windows e versões Python

Em `windows-latest`, execute os mesmos comandos PowerShell acima após instalar
`requirements-dev.txt`. A matriz canônica de CI cobre Python 3.12 e 3.13; localmente, registre
separadamente a versão exibida por `python --version`, a contagem da suíte e qualquer teste
ignorado por falta de privilégio de symlink. A ausência de um interpretador local não autoriza
declarar seu gate aprovado: nesse caso, o resultado permanece pendente até a execução da CI.

## Opção 4 — processamento offline

- Dry-run pode avaliar os PDFs locais sem escrita real.
- Execução real exige limite entre 1 e 5, lista explícita, protocolo pelo nome, SHA-256 de
  cada arquivo, digest do lote e lock global.
- Antes da confirmação, confira quantidade, digest abreviado e flags de escrita.
- A frase exata é exibida pelo terminal:

  ```text
  APLICAR OPCAO 4 EM <N> PROTOCOLOS
  ```

- `SIM`, frase parcial, limite ausente e mudança de arquivo bloqueiam a operação.

## Opção 5 — pipeline CDP

- O limite solicitado deve estar entre 1 e 5.
- A confirmação forte é obrigatória também em `DRY_RUN=true`, pois essa rota acessa
  Portal/CDP e pode baixar PDFs; o dry-run integrado offline é uma rota distinta.
- A opção usa o mesmo mutex global da opção 4.
- A frase forte é derivada do limite e deve ser copiada exatamente da tela.
- Scripts diretos de download/processamento real ficam bloqueados; use o CLI canônico.

## Ensaio de restauração e retomada

Somente com workbook e arquivos sintéticos:

1. calcule o SHA-256 e valide o XLSX inicial;
2. crie o backup e confirme que ele é reabrível;
3. aplique o lote sintético;
4. simule falha depois de um efeito;
5. restaure o backup por cópia atômica;
6. reabra o workbook restaurado e compare seu SHA-256 ao backup;
7. retome o mesmo lote;
8. confirme uma única linha, um único arquivo de destino e uma única entrada de state;
9. confirme `excel_effect`, `archive_effect`, `state_effect`, `report_effect`,
   `manual_action_required` e `rollback_possible`;
10. gere apenas o relatório agregado `SHAREABLE` para auditoria externa.

## Piloto/canário

O piloto está preparado, mas não deve ser executado nesta etapa sem nova autorização.

- máximo absoluto de 5 protocolos;
- lote congelado e digest conferido;
- confirmação forte da operação e do limite;
- lock global adquirido;
- workbook fechado, backup validado e restauração ensaiada;
- relatório antes/depois minimizado;
- parada imediata em protocolo fora do lote ou divergência entre nome e extração.

## Release

Release só pode ser criada de commit limpo e identificável. O manifesto deve registrar HEAD,
branch/detached state, tags no HEAD, `git describe`, versões e scanner aprovado. O validador
deve aprovar o conteúdo e o SHA. Nenhum arquivo de `outputs`, log, cache ou dado privado entra
por exceção implícita.
