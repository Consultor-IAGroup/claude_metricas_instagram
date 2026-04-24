# Claude Code Usage Dashboard

Dashboard local para visualizar métricas de uso do [Claude Code](https://claude.com/claude-code) com gerador de cards para Instagram.

Baseado no projeto [claude-usage](https://github.com/phuryn/claude-usage) com extensões para:
- Painel **Visão Geral** com dados reais do `stats-cache.json` (sessões, tokens, sequência, heatmap)
- **Cards Instagram** (1:1) com seus números reais, prontos para download em PNG
- Frase motivacional comparando seus tokens com Harry Potter e a Pedra Filosofal

---

## Requisitos

- Python 3.9+
- Claude Code instalado (para ter o `~/.claude/` com dados reais)

## Instalação

```bash
git clone https://github.com/Consultor-IAGroup/claude_metricas_instagram.git
cd claude_metricas_instagram
pip install sqlite3  # já incluso no Python padrão
```

## Uso

```bash
# 1. Escanear sessões (cria ~/.claude/usage.db)
python cli.py scan

# 2. Abrir o dashboard (porta padrão: 8080)
python cli.py dashboard

# Porta customizada
PORT=8081 python cli.py dashboard
```

Acesse: `http://localhost:8080`

## Logo personalizada (opcional)

Coloque um arquivo `logo.png` ou `logo-dark.png` na mesma pasta do `dashboard.py`.
Ele aparecerá nos cards Instagram e no rodapé do dashboard.
Recomendado: PNG com fundo transparente ou escuro, ~500×500px.

## Abas do dashboard

| Aba | Conteúdo |
|---|---|
| **Visão Geral** | Sessões, mensagens, tokens, dias ativos, sequência, heatmap de calendário |
| **Cards Instagram** | 7 cards 1:1 com seus dados reais para postar nas redes sociais |
| **Modelos** | Breakdown por modelo: input/output/cache, custo estimado, projetos |

## Cards Instagram gerados

1. **Capa** — resumo geral (sessões + instruções + tokens)
2. **Sessões** — número de sessões de agente
3. **Mensagens** — total de instruções enviadas
4. **Tokens** — tokens de raciocínio vs Harry Potter
5. **Commits + Linhas** — contribuições de código no período
6. **Insight** — frase de impacto sobre o período
7. **Horas** — tempo total de agente rodando

Para baixar: botão **⬇ PNG** em cada card ou **⬇ Baixar todos** para todos de uma vez.

## Fonte dos dados

| Dado | Fonte |
|---|---|
| Sessões, mensagens, tokens, heatmap | `~/.claude/stats-cache.json` (nativo Claude Code) |
| Commits e linhas de código | `git log` nos repositórios encontrados nas sessões |
| Tabela de sessões detalhada | `~/.claude/usage.db` (construído pelo scanner) |

## Personalização dos cards

Edite a função `buildIgCards()` em `dashboard.py` para ajustar os textos dos cards ao seu contexto.

---

Licença: MIT
