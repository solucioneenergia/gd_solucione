# Relatório de QA

## Resultado automatizado

Comando executado:

```text
python -m compileall -q .
python -m pytest -q
```

Resultado final:

```text
147 passed, 5 skipped
```

Os cinco testes pulados dependem de PDFs reais que não foram incluídos por segurança e privacidade.

## Cobertura funcional presente

- parsing e formatação de módulos/inversores;
- mapeamento, inserção, movimentação e reparo do Excel;
- busca fuzzy/protocolo, cache e destinos de arquivamento;
- paginação, seleção, retomada e recuperação CDP;
- construção de relatórios do pipeline;
- estado/checkpoints;
- segurança de caminhos, PDF e endpoint CDP;
- persistência atômica;
- pré-voo e controlador da apresentação.

## Testes manuais obrigatórios antes de produção

1. Portal com login manual, uma página e múltiplas páginas.
2. Solicitação concluída com PDF disponível e sem PDF disponível.
3. Retomada após encerrar o processo durante download e durante processamento.
4. Planilha aberta no Excel para validar bloqueio/erro legível.
5. Pasta de clientes em unidade de rede desconectada e reconectada.
6. Cliente com nome semelhante a outro, validando limiares fuzzy.
7. PDF com dois modelos de módulos/inversores.
8. Restauração do backup da planilha.
9. Build Tkinter em máquina Windows limpa.
10. Antivírus/EDR, permissões de rede e política de execução do Edge.

## Critérios de saída

A liberação só deve ocorrer quando:

- nenhum erro crítico estiver aberto;
- os cinco testes com fixtures privadas estiverem aprovados;
- um lote piloto real tiver conferência humana de 100%;
- restauração de backup estiver testada;
- a sessão CDP estiver restrita a loopback;
- logs e retenção tiverem aprovação do responsável por dados.

## Limitações do ambiente desta validação

- Não houve acesso ao Portal GD, a uma sessão autenticada do Edge nem às pastas e planilhas reais de produção; portanto, o fluxo navegador/CDP ainda exige homologação operacional.
- O ambiente usado para a análise não tinha o pacote `tenacity` previamente instalado. Ele está declarado em `requirements.txt` e será instalado pelo procedimento normal de setup; os testes automatizados não precisaram carregar o adaptador de navegador.
