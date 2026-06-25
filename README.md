# 🏆 LoL Flex Tracker — v1.1

Monitora a atividade dos jogadores **Challenger, Grão-Mestre e Mestre** na **Ranqueada Flex (BR1)**, detectando jogos pela variação de LP a cada 5 minutos.

---

## Como funciona

```
cron-job.org (a cada 5 min)
    → dispara GitHub Actions via API
        → collector.py (3 chamadas à API, ~5 segundos)
            → compara LP com estado anterior
                → registra variações em lp_changes.csv
                    → Streamlit Cloud atualiza o dashboard
```

**Detecção de jogos por LP:** a cada 5 minutos, o collector busca o LP atual de todos os jogadores rastreados. Se o LP de alguém mudou, um jogo aconteceu naquele intervalo.

**Heatmap ajustado:**
- Cada célula representa uma janela de **5 minutos**
- **Média móvel de 60 min** (12 janelas) suaviza ruído
- **Deslocamento de -30 min** estima quando o jogador entrou na fila (detectamos o fim do jogo pelo LP; subtraindo 30 min aproximamos o início)

---

## Estrutura de arquivos

```
lol-flex-tracker/
├── .github/workflows/
│   └── main.yml              ← workflow (disparo a cada 5 min)
├── data/
│   ├── player_current.csv    ← estado atual de todos os jogadores (sobrescrito a cada run)
│   └── lp_changes.csv        ← histórico de variações de LP (append somente quando LP muda)
├── collector.py              ← coleta LP dos 3 tiers (3 chamadas à API)
├── dashboard.py              ← dashboard Streamlit (4 abas)
├── requirements.txt
└── README.md
```

### Por que dois arquivos de dados?

| Arquivo | Tamanho | Estratégia |
|---------|---------|-----------|
| `player_current.csv` | Sempre ~1.500 linhas | Sobrescrito a cada run — só o estado atual |
| `lp_changes.csv` | Cresce com jogos detectados | Append — só quando LP muda (~50–200 eventos/hora) |

Numa coleta horária tradicional, guardar LP de 1.500 jogadores × 288 runs/dia × 30 dias = **12,9 milhões de linhas**. Com a nova estratégia, o `lp_changes.csv` terá apenas as mudanças reais — aproximadamente **72.000 linhas/mês**.

---

## Colunas dos CSVs

### `player_current.csv`
| Coluna | Descrição |
|--------|-----------|
| `puuid` | ID único do jogador |
| `tier` | challenger / gm / master |
| `lp` | League Points atual |
| `wins` / `losses` | Vitórias e derrotas na season |
| `last_updated_utc` | Timestamp da última atualização |

### `lp_changes.csv`
| Coluna | Descrição |
|--------|-----------|
| `timestamp_utc` | Momento da detecção da mudança |
| `puuid` | ID do jogador |
| `tier` | challenger / gm / master |
| `old_lp` | LP antes |
| `new_lp` | LP depois |
| `lp_delta` | Diferença (positivo = vitória, negativo = derrota) |

---

## Pré-requisitos

- Conta no **GitHub**
- Conta no **Streamlit Community Cloud** — https://share.streamlit.io
- Conta no **cron-job.org** — https://cron-job.org
- **Personal API Key** da Riot — https://developer.riotgames.com

> A Personal API Key não expira. A chave de desenvolvimento expira a cada 24h.

---

## Setup

### Passo 1 — Criar o repositório no GitHub

1. Acesse https://github.com/new e crie o repositório `lol-flex-tracker`
2. Faça upload de todos os arquivos mantendo a estrutura de pastas

### Passo 2 — Adicionar a API Key como secret

**Settings → Secrets and variables → Actions → New repository secret**

| Nome | Valor |
|------|-------|
| `RIOT_API_KEY` | Sua chave `RGAPI-...` |

### Passo 3 — Configurar o cron-job.org

1. Cadastre-se em https://cron-job.org
2. **Create cronjob:**

| Campo | Valor |
|-------|-------|
| Title | `LoL Flex Tracker` |
| URL | `https://api.github.com/repos/SEU_USUARIO/lol-flex-tracker/actions/workflows/main.yml/dispatches` |
| Schedule | Every 5 minutes |
| Method | POST |
| Body | `{"ref":"main"}` |

**Headers:**
| Key | Value |
|-----|-------|
| `Authorization` | `Bearer ghp_SEU_TOKEN_AQUI` |
| `Accept` | `application/vnd.github+json` |
| `Content-Type` | `application/json` |

3. Salve e clique em **Test run** — deve retornar **204 No Content**

> O token é um **Personal Access Token** do GitHub (Settings → Developer settings → Tokens → escopo `workflow`).

### Passo 4 — Hospedar o dashboard

1. Acesse https://share.streamlit.io → **New app**
2. Repository: `seu-usuario/lol-flex-tracker` | Branch: `main` | File: `dashboard.py`
3. **Deploy**

### Passo 5 — Primeira execução

Execute manualmente pelo GitHub: **Actions → Coletar LP (5 min) → Run workflow**

Na primeira run o collector salva o estado inicial mas não detecta mudanças (sem estado anterior para comparar). A partir da segunda run (5 min depois), o heatmap começa a receber dados.

---

## Dashboard — 4 abas

| Aba | Conteúdo |
|-----|----------|
| 🔥 **Heatmap** | Mapa de calor em células de 5 min com média móvel de 60 min e deslocamento de -30 min |
| 📈 **Série Temporal** | Jogos detectados por hora, vitórias/derrotas inferidas, média móvel |
| 👤 **Jogadores** | Estado atual de todos os rastreados com LP, winrate e distribuição por tier |
| 🗃️ **Dados Brutos** | Últimas mudanças de LP e estado atual completo |

---

## Manutenção

| Tarefa | Como fazer |
|--------|-----------|
| Verificar saúde | GitHub → Actions: deve haver um run a cada 5 min |
| Pausar coleta | cron-job.org → desativar o job |
| Rodar manualmente | GitHub → Actions → Run workflow |

---

## Limitações

- **Um jogo por intervalo de 5 min**: se o jogador jogou 2 partidas em 5 min, o delta de LP reflete o saldo das duas, não dois eventos separados
- **Buracos de dados**: se a API da Riot estiver fora ou a chave expirar, haverá ausência naquele período
- **Deslocamento fixo de 30 min**: partidas de Flex duram em média 28–35 min; o deslocamento é uma estimativa, não exato
