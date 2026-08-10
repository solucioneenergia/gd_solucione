# SPEC-007 - Manutencao e limpeza segura do projeto

## Objetivo

Executar inventario tecnico, criptografico e referencial do armazenamento usado pela Automacao GD Neoenergia, produzindo um plano preliminar de manutencao em modo exclusivamente read-only.

Esta SPEC cobre a Etapa 5.1. Ela nao autoriza exclusao, movimentacao, compactacao, arquivamento, rotacao real de logs, alteracao de permissoes ou qualquer escrita fora dos relatorios da propria etapa.

## Status operacional

- Etapa 5.1: encerrada sem autorizacao de limpeza.
- Etapa 5.1A: correcao da implementacao read-only concluida.
- Etapa 5.1B: nova execucao limpa read-only concluida com plano conservador.
- Etapa 5.1C: disposicao terminal dos achados concluida.

A execucao rejeitada deve permanecer como historica. Seus arquivos indevidos nao podem ser excluidos, movidos ou renomeados pela correcao da implementacao.

Resultado terminal da Etapa 5.1C:

- source execution id: `2e42348a7221cec8c9075a0a9994e82e1fac3991b251cbc043f18444934c93ae`;
- cleanup plan hash: `bccb5e1b58c8ee892685b7b39040e58bb91fafb44cb6a872b331a88a3290c9f2`;
- disposition hash: `2cd2e2e0b88cb5db2d2724d03c10fea610e75274e5f150a818e3428dbdae5390`;
- referencias quebradas tratadas: 6/6;
- arquivos `UNKNOWN_REVIEW_REQUIRED` tratados: 52/52;
- grupos duplicados tratados: 28/28;
- total de achados reconciliados: 86/86;
- achados internos em aberto: 0;
- acoes automaticas autorizadas: 0;
- decisao 5.1C: `STAGE5_1C_COMPLETE — ALL_FINDINGS_DISPOSITIONED_READ_ONLY`;
- decisao 5.1: `STAGE5_1_COMPLETE — INVENTORY_AND_MANUAL_REVIEW_CLOSED_NO_CLEANUP_AUTHORIZED`.

Nenhuma exclusao, movimentacao, compactacao, renomeacao ou arquivamento foi autorizado pela Etapa 5.1. A Etapa 5.2 nao foi iniciada.

## Adendo 2026-08-10 - Etapa 5.2: quarentena local reversivel

Esta etapa autoriza somente a preparacao de refatoracao por auditoria e quarentena local
reversivel em `lixeira/`, sem exclusao definitiva.

Contratos adicionais:

- `lixeira/` e uma pasta local ignorada pelo Git e nao deve entrar em release, wheel, ZIP ou
  artefato compartilhavel;
- scanners de fonte podem ignorar `lixeira/` quando ela estiver na raiz do projeto, pois seu
  conteudo ja foi removido do caminho ativo e pode conter artefatos locais gerados;
- scanners de ZIP/wheel continuam obrigados a bloquear qualquer entrada `lixeira/**` que contenha
  dado operacional, credencial, caminho pessoal ou documento operacional;
- somente itens regeneraveis e ignorados pelo Git podem ser movidos automaticamente nesta etapa;
- codigo rastreado, testes, SPECs, ADRs, frontend legado, `src/`, dados operacionais, `.env`,
  PDFs, planilhas, logs operacionais e perfis de navegador permanecem protegidos;
- cada movimentacao deve ser registrada em manifesto com origem relativa, destino relativo,
  classificacao e criterio de reversao.

Itens duvidosos entram apenas como candidatos de refatoracao futura, nao como quarentena
automatica.

## Escopo

O inventario deve resolver a raiz canônica do projeto e percorrer somente o escopo interno autorizado. Em Windows, se `git rev-parse --show-toplevel` retornar um caminho com normalizacao Unicode que nao enumere o workspace real, o script operacional deve usar a raiz derivada do proprio arquivo em `scripts/..`, desde que `AGENTS.md` e `automacao_gd/` estejam presentes.

A raiz canônica deve ser validada por identidade física:

- fonte primaria: `Path(__file__).resolve().parents[1]`;
- marcadores obrigatorios: `AGENTS.md`, `automacao_gd/`, `scripts/`, `tests/`, `data/`;
- Git usado somente como verificacao;
- texto retornado pelo Git tratado com UTF-8 strict e normalizacao Unicode NFC apenas para comparacao;
- `os.path.samefile` usado quando aplicavel;
- se o texto do Git estiver corrompido, ele nao pode ser usado como destino de escrita;
- divergencia fisica entre raiz do script e raiz do Git bloqueia a execucao.

O diretorio de relatorios deve ser exatamente `<PROJECT_ROOT>/data/logs`, existir antes da execucao, nao ser symlink e resolver para dentro da raiz canônica. A execucao nao deve criar a arvore de logs automaticamente.

- `automacao_gd`;
- `apps`;
- `data`;
- `docs`;
- `scripts`;
- `specs`;
- `tests`;
- arquivos de configuracao e documentacao na raiz do repositorio.

Raizes externas configuradas devem ser registradas, mas nao percorridas recursivamente:

- planilha oficial;
- raiz de clientes;
- perfis de navegador;
- diretorios externos de autenticacao;
- qualquer drive de rede referenciado por configuracao.

## Fora do escopo

- remover arquivos;
- mover arquivos;
- compactar arquivos;
- alterar timestamps, atributos ou permissoes;
- abrir PDFs para extracao de conteudo;
- acessar Portal GD;
- reprocessar planilha;
- executar limpeza real;
- criar release, tag, commit ou arquivo `.zip`.
- criar snapshots fisicos.
- gerar inventario real durante etapas de correcao da implementacao.

## Protecoes absolutas

Devem ser classificados como `PROTECTED_ABSOLUTE` ou protecao superior equivalente:

- planilha oficial e qualquer workbook oficial;
- backups de workbook;
- PDFs de orcamento, parecer ou cliente;
- arquivos de autenticacao, cookies, storage state e sessoes;
- estado de retomada do pipeline;
- codigo-fonte, testes, scripts operacionais;
- specs, runbook, checklist, ledger, README, CHANGELOG, `pyproject.toml`, `.gitignore`;
- planos, pre-voos, relatorios de aplicacao, certificacoes, auditorias e artefatos citados;
- arquivos referenciados por hash, nome ou caminho relativo em outro artefato.

Os backups visuais abaixo permanecem protegidos enquanto forem citados pela certificacao:

- `planilha_pre_visual_standardization_20260729T125050Z.xlsx`;
- `planilha_pre_visual_standardization_style_completion_20260729T125346Z.xlsx`;
- `planilha_pre_visual_standardization_style_completion_20260729T125229Z.xlsx`.

## Classificacoes

Cada arquivo inventariado deve possuir exatamente uma classificacao primaria entre:

```text
PROTECTED_ABSOLUTE
PROTECTED_REFERENCED
ACTIVE_RUNTIME
ACTIVE_PIPELINE_STATE
ACTIVE_AUTHENTICATION
ACTIVE_DOWNLOAD
HISTORICAL_AUDIT
HISTORICAL_BACKUP
HISTORICAL_REPORT
REGENERABLE_CACHE
REGENERABLE_BUILD
TEMPORARY_ACTIVE
TEMPORARY_ORPHAN_CANDIDATE
DUPLICATE_IDENTICAL_REVIEW
DUPLICATE_DERIVED_REVIEW
LOG_RETENTION_REVIEW
ARCHIVE_CANDIDATE
DELETE_CANDIDATE_SAFE
UNKNOWN_REVIEW_REQUIRED
EXTERNAL_PROTECTED_ROOT
BROKEN_SYMLINK_REVIEW
UNREADABLE_REVIEW
HASH_UNSTABLE_FILE
```

A hierarquia de decisao e:

1. protecao absoluta;
2. referencia documental;
3. estado operacional ativo;
4. autenticacao ou segredo;
5. auditoria e rollback;
6. dependencia entre artefatos;
7. historico;
8. regeneravel;
9. temporario;
10. duplicidade;
11. idade e retencao;
12. candidato a limpeza;
13. revisao obrigatoria.

Classificacao de protecao prevalece sobre qualquer evidencia de limpeza.

## Grafo de referencias

O servico deve analisar, quando seguro, arquivos textuais como Markdown, JSON, YAML, TOML, TXT, Python e configuracoes, identificando:

- caminhos relativos;
- nomes de arquivos;
- SHA-256;
- `plan_hash`;
- `preflight_hash`;
- `certification_hash`;
- nomes de backup;
- nomes de relatorio.

Arquivos com `referenced_by_count > 0` nao podem ser classificados como descartaveis nesta etapa.

## Snapshot em memoria

A protecao de read-only deve usar snapshots exclusivamente em memoria:

```text
capture_filesystem_snapshot(root, exclusions)
compare_filesystem_snapshots(before, after)
```

Cada entrada registra somente `relative_path`, `file_type`, `size`, `mtime_ns` e `is_symlink`. E proibido gravar snapshot em JSON, TXT, arquivo oculto, `.tmp` ou qualquer destino dentro do projeto.

Na Etapa 5.1B, os arquivos indevidos ja existentes da execucao rejeitada entram no snapshot inicial e nao constituem nova violacao. Qualquer novo arquivo fora dos oito relatorios finais e violacao.

## Bundle e escrita dos artefatos

Os oito relatorios finais devem derivar de um unico bundle imutavel com `execution_id`, `created_at`, `timestamp`, payloads finais, `cleanup_plan_hash` e manifesto de artefatos.

O escritor da Etapa 5.1 deve:

- renderizar JSON e Markdown em memoria;
- validar UTF-8 strict antes de escrever;
- validar consistencia JSON x Markdown;
- validar `cleanup_plan_hash` deterministico;
- exigir que nenhum destino exista;
- gravar apenas os oito destinos finais com criacao exclusiva (`open(..., "xb")`);
- nao usar `atomic_write_json`, `atomic_write_text`, `NamedTemporaryFile`, `mkstemp`, `os.replace`, `Path.replace`, `shutil.copy`, `shutil.move` ou nomes `.tmp`.

O `cleanup_plan_hash` e calculado por SHA-256 sobre JSON canonico, com `ensure_ascii=false`, `sort_keys=true`, `separators=(",", ":")` e exclusao do proprio campo. O mesmo valor deve aparecer no JSON e no Markdown.

## UTF-8 e mojibake

Textos institucionais controlados devem permanecer em UTF-8 valido, normalizados em NFC e sem substituicao de caracteres. A decisao deve preservar o travessao canonico `—`; qualquer formato que use ponto de interrogacao no lugar do travessao deve bloquear a escrita.

## Modelo tipado de referencias

Referencias devem ser tipadas:

```text
AUTHORITATIVE_FILE_REFERENCE
AUTHORITATIVE_HASH_REFERENCE
CODE_RESOURCE_REFERENCE
TEST_FIXTURE_REFERENCE
DOCUMENTATION_EXAMPLE
DIRECTORY_REFERENCE
PLACEHOLDER_REFERENCE
MALFORMED_REFERENCE
EXTERNAL_REFERENCE
```

E devem registrar confianca `HIGH`, `MEDIUM` ou `LOW`.

Somente referencias autoritativas concretas e existentes podem proteger arquivos. Fixtures, exemplos, placeholders, diretorios e caminhos malformados nao protegem alvos e nao entram como referencias quebradas autoritativas. Caches citados por testes, SPEC ou pelo proprio servico permanecem `REGENERABLE_CACHE`, salvo evidencia operacional concreta em contrario.

Referencias quebradas devem ser registradas como revisao, sem correcao automatica.

## Duplicidades

Duplicidades devem ser agrupadas por `sha256 + size_bytes`.

Todos os grupos duplicados permanecem em revisao:

```text
DUPLICATE_IDENTICAL_REVIEW
delete_now = false
```

O espaco recuperavel de um grupo e no maximo `total_do_grupo - uma_copia_preservada`, sem contar arquivos protegidos como recuperaveis.

## Temporarios, caches e builds

Padroes temporarios reconhecidos incluem:

- `*.tmp`;
- `*.temp`;
- `*.partial`;
- `*.part`;
- `*.crdownload`;
- `*.download`;
- `~$*.xlsx`;
- `*.lock`;
- `*.lck`;
- `*.pid`;
- `*.retry`;
- `*.staging`;
- `*.old.tmp`.

Caches e builds regeneraveis incluem:

- `__pycache__`;
- `*.pyc`;
- `.pytest_cache`;
- `.mypy_cache`;
- `.ruff_cache`;
- `.coverage`;
- `htmlcov`;
- `build`;
- `dist`;
- `*.egg-info`.

Nenhum item sera excluido nesta etapa.

## Privacidade

Os relatorios nao devem conter:

- nomes completos de clientes;
- CPF;
- CNPJ;
- e-mails;
- telefones;
- enderecos;
- tokens;
- cookies;
- senhas;
- conteudo de PDFs;
- conteudo de autenticacao;
- caminho absoluto do perfil do usuario.

Caminhos internos devem ser relativos ao repositorio. Raizes externas devem ser registradas por alias.

## Contrato read-only

Todos os itens do plano devem conter:

```text
delete_now = false
move_now = false
archive_now = false
compress_now = false
```

Contadores obrigatorios:

```text
files_deleted = 0
files_moved = 0
files_archived = 0
files_compressed = 0
directories_deleted = 0
permissions_changed = 0
timestamps_changed = 0
workbook_save = 0
os_replace = 0
portal_access = 0
pdf_downloads = 0
```

## Artefatos

A Etapa 5.1 deve gerar:

- `project_storage_inventory_stage5_1_<timestamp>.json`;
- `project_storage_inventory_stage5_1_<timestamp>.md`;
- `project_cleanup_plan_stage5_1_<timestamp>.json`;
- `project_cleanup_plan_stage5_1_<timestamp>.md`;
- `project_artifact_reference_graph_stage5_1_<timestamp>.json`;
- `project_artifact_reference_graph_stage5_1_<timestamp>.md`;
- `project_duplicate_and_temporary_analysis_stage5_1_<timestamp>.json`;
- `project_duplicate_and_temporary_analysis_stage5_1_<timestamp>.md`.

A disposicao terminal 5.1C deve gerar somente:

- `stage5_1_terminal_disposition_<timestamp>.json`;
- `stage5_1_terminal_disposition_<timestamp>.md`.

## Criterios de aceite

- [ ] raiz do repositorio resolvida dinamicamente;
- [ ] planilha oficial verificada por existencia, tamanho e SHA;
- [ ] raizes externas registradas sem varredura recursiva;
- [ ] inventario interno concluido;
- [ ] cada arquivo possui uma classificacao primaria;
- [ ] arquivos protegidos permanecem protegidos;
- [ ] grafo de referencias gerado;
- [ ] duplicados agrupados por SHA-256;
- [ ] temporarios, caches e builds classificados;
- [ ] plano preliminar gerado com todas as acoes desativadas;
- [ ] privacidade validada;
- [ ] read-only violations = 0.
