"""
dashboard.py — LoL Flex Tracker

Visualiza os horários de maior atividade dos jogadores
Challenger, Grão-Mestre e Mestre na Ranqueada Flex (BR1).

Heatmap principal:
  - Resolução de 5 minutos por célula
  - Soma dos últimos 30 min (6 janelas de 5 min)
  - Deslocamento de -30 min (estima início da partida, não o fim)
"""

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# ── Configuração ──────────────────────────────────────────────────────────────
DATA_DIR           = Path(__file__).parent / "data"
PLAYER_CURRENT_CSV = DATA_DIR / "player_current.csv"
LP_CHANGES_CSV     = DATA_DIR / "lp_changes.csv"

DIAS_PT    = {0: "Segunda", 1: "Terça", 2: "Quarta", 3: "Quinta",
              4: "Sexta",   5: "Sábado", 6: "Domingo"}
ORDEM_DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

TIER_COLORS  = {"challenger": "#f59e0b", "gm": "#818cf8", "master": "#34d399"}
TIER_LABELS  = {"challenger": "Challenger", "gm": "Grão-Mestre", "master": "Mestre"}

st.set_page_config(
    page_title="LoL Flex Tracker",
    page_icon="🏆",
    layout="wide",
)
st.title("🏆 LoL Flex Tracker — Challenger / GM / Mestre | Flex BR")
st.caption(
    "Detecta jogos pela variação de LP a cada 5 min. "
    "O heatmap mostra quando os jogadores estão entrando na fila, "
    "não quando o jogo termina."
)


# ── Loaders ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_current() -> pd.DataFrame:
    if not PLAYER_CURRENT_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(PLAYER_CURRENT_CSV)
    if df.empty:
        return df
    df["winrate"] = (
        df["wins"] / (df["wins"] + df["losses"]).replace(0, pd.NA) * 100
    ).round(1)
    df["tier_label"] = df["tier"].map(TIER_LABELS).fillna(df["tier"])
    return df


@st.cache_data(ttl=60)
def load_changes() -> pd.DataFrame:
    if not LP_CHANGES_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(LP_CHANGES_CSV)
    if df.empty:
        return df
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["timestamp_br"]  = df["timestamp_utc"].dt.tz_convert("America/Sao_Paulo")
    return df


@st.cache_data(ttl=60)
def compute_heatmap(changes_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Processa lp_changes.csv e retorna:
      - pivot: heatmap (dia × slot de 5 min) com média móvel + deslocamento
      - series: série temporal para o gráfico de linha
    """
    if changes_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = changes_df.copy()

    # Agrupa por janela de 5 min
    df["window"] = df["timestamp_br"].dt.floor("5min")
    counts = df.groupby("window").size().reset_index(name="n")

    # Preenche janelas sem eventos com 0 para a média móvel ser contínua
    if len(counts) > 1:
        full_range = pd.date_range(
            start=counts["window"].min(),
            end=counts["window"].max(),
            freq="5min",
            tz="America/Sao_Paulo",
        )
        counts = (
            counts.set_index("window")
            .reindex(full_range, fill_value=0)
            .rename_axis("window")
            .reset_index()
        )

    counts = counts.sort_values("window")

    # Soma dos últimos 30 min (6 janelas × 5 min)
    counts["rolling"] = (
        counts["n"].rolling(window=6, min_periods=1).sum().astype(int)
    )

    # Deslocamento de -30 min: detectamos o fim do jogo, queremos o início
    counts["window_inicio"] = counts["window"] - pd.Timedelta(minutes=30)

    # Extrai dia da semana e slot de 5 min dentro do dia (0–287)
    counts["dia_semana"] = counts["window_inicio"].dt.weekday.map(DIAS_PT)
    counts["slot_5min"]  = (
        counts["window_inicio"].dt.hour * 12
        + counts["window_inicio"].dt.minute // 5
    )

    # Pivot: média do rolling por dia × slot
    pivot = (
        counts.groupby(["dia_semana", "slot_5min"])["rolling"]
        .mean()
        .reset_index()
        .pivot(index="dia_semana", columns="slot_5min", values="rolling")
        .reindex(ORDEM_DIAS)
    )

    # Garante todas as 288 colunas (preenche com 0 onde não há dados)
    all_slots = list(range(288))
    pivot = pivot.reindex(columns=all_slots, fill_value=0)

    return pivot, counts


# ── Carrega dados ─────────────────────────────────────────────────────────────
current_df = load_current()
changes_df = load_changes()

# ── Métricas rápidas ──────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)

if not current_df.empty:
    c1.metric("Total rastreados", len(current_df))
    c2.metric("Challengers",      int((current_df["tier"] == "challenger").sum()))
    c3.metric("Grão-Mestres",     int((current_df["tier"] == "gm").sum()))
    c4.metric("Mestres",          int((current_df["tier"] == "master").sum()))
else:
    for col in [c1, c2, c3, c4]:
        col.metric("—", "—")

if not changes_df.empty:
    ultima = changes_df["timestamp_br"].max()
    c5.metric("Última coleta (BR)", ultima.strftime("%d/%m %H:%M"))
else:
    c5.metric("Última coleta", "—")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_heat, tab_serie, tab_jogadores, tab_raw = st.tabs([
    "🔥 Heatmap",
    "📈 Série Temporal",
    "👤 Jogadores",
    "🗃️ Dados Brutos",
])


# ── Tab 1: Heatmap ────────────────────────────────────────────────────────────
with tab_heat:
    st.subheader("Heatmap de atividade — início estimado das partidas")
    st.caption(
        "**Cada célula = janela de 5 minutos.** "
        "Valor exibido = soma de jogos detectados por LP nos **30 min anteriores**, "
        "deslocada 30 min para trás (estima quando o jogador entrou na fila, "
        "não quando o jogo terminou)."
    )

    # Filtro de tier — aplicado ANTES de computar o heatmap
    tier_sel = st.multiselect(
        "Filtrar por tier:",
        options=["challenger", "gm", "master"],
        default=["challenger", "gm", "master"],
        format_func=lambda t: TIER_LABELS[t],
        key="tier_heat",
    )

    changes_heat = changes_df[changes_df["tier"].isin(tier_sel)] if not changes_df.empty else changes_df
    pivot_df, series_df = compute_heatmap(changes_heat)

    if pivot_df.empty:
        st.info(
            "Aguardando dados. São necessários ao menos 2 ciclos de coleta "
            "(10 minutos após a primeira execução)."
        )
    else:
        # Converte colunas de slot (0–287) para strings de horário ("19:20")
        # assim o tooltip exibe o horário legível em vez do número do slot
        def slot_to_timestr(slot: int) -> str:
            h = slot // 12
            m = (slot % 12) * 5
            return f"{h:02d}:{m:02d}"

        pivot_plot = pivot_df.copy()
        pivot_plot.columns = [slot_to_timestr(int(c)) for c in pivot_plot.columns]

        # Ticks: só mostra o rótulo a cada hora cheia (ex: "06:00", "07:00"...)
        tick_positions = [slot_to_timestr(s) for s in range(0, 288, 12)]
        tick_labels    = [f"{h:02d}h" for h in range(24)]

        fig_heat = px.imshow(
            pivot_plot,
            labels=dict(
                x="Horário (Brasília)",
                y="",
                color="Jogos nos últimos 30 min",
            ),
            color_continuous_scale="Reds",
            aspect="auto",
            zmin=0,
        )
        fig_heat.update_xaxes(
            tickvals=tick_positions,
            ticktext=tick_labels,
            showgrid=True,
            gridcolor="rgba(255,255,255,0.1)",
        )
        fig_heat.update_layout(
            height=320,
            margin=dict(l=10, r=10, t=10, b=10),
            coloraxis_colorbar=dict(title="Jogos / 30 min", thickness=15),
        )
        st.plotly_chart(fig_heat, use_container_width=True)

        # Top 10 slots mais perigosos
        st.subheader("⚠️ Top 10 momentos mais movimentados")
        st.caption("Horários com mais partidas de elite em andamento (após deslocamento de -30 min).")

        # pivot_plot já tem colunas como strings de horário
        top_slots = (
            pivot_plot.stack()
            .reset_index()
            .rename(columns={"dia_semana": "dia", "slot_5min": "horário", 0: "média"})
            .sort_values("média", ascending=False)
            .head(10)
            .reset_index(drop=True)
        )
        top_slots.index += 1
        top_slots["média"] = top_slots["média"].round(2)
        st.dataframe(
            top_slots[["dia", "horário", "média"]],
            use_container_width=True,
        )


# ── Tab 2: Série Temporal ─────────────────────────────────────────────────────
with tab_serie:
    if changes_df.empty:
        st.info("Aguardando dados de LP...")
    else:
        tier_sel2 = st.multiselect(
            "Tiers:",
            options=["challenger", "gm", "master"],
            default=["challenger", "gm", "master"],
            format_func=lambda t: TIER_LABELS[t],
            key="tier_serie",
        )

        filtered = changes_df[changes_df["tier"].isin(tier_sel2)] if not changes_df.empty else changes_df

        # Série temporal de jogos detectados por hora
        st.subheader("Jogos detectados por hora (delta de LP)")
        hourly = (
            filtered.groupby(filtered["timestamp_br"].dt.floor("h"))
            .size()
            .reset_index(name="jogos")
        )
        fig_hora = px.bar(
            hourly, x="timestamp_br", y="jogos",
            labels={"timestamp_br": "Hora (Brasília)", "jogos": "Jogos detectados"},
            color_discrete_sequence=["#ef4444"],
        )
        st.plotly_chart(fig_hora, use_container_width=True)

        # Distribuição por direção (LP subiu vs caiu)
        st.subheader("Vitórias e derrotas inferidas")
        filtered2 = filtered.copy()
        filtered2["resultado"] = filtered2["lp_delta"].apply(
            lambda d: "Vitória (LP↑)" if d > 0 else "Derrota (LP↓)"
        )
        hourly2 = (
            filtered2.groupby([filtered2["timestamp_br"].dt.floor("h"), "resultado"])
            .size()
            .reset_index(name="n")
        )
        fig_vd = px.bar(
            hourly2, x="timestamp_br", y="n", color="resultado",
            labels={"timestamp_br": "Hora (Brasília)", "n": "Partidas"},
            color_discrete_map={
                "Vitória (LP↑)": "#4ade80",
                "Derrota (LP↓)": "#f87171",
            },
            barmode="stack",
        )
        st.plotly_chart(fig_vd, use_container_width=True)

        # Série da média móvel (o mesmo que alimenta o heatmap)
        _, series_df2 = compute_heatmap(filtered)
        if not series_df2.empty:
            st.subheader("Soma de jogos detectados — janela de 30 min (com deslocamento de -30 min)")
            fig_roll = px.line(
                series_df2.dropna(subset=["rolling"]),
                x="window_inicio",
                y="rolling",
                labels={
                    "window_inicio": "Início estimado (Brasília)",
                    "rolling":       "Jogos detectados (soma 30 min)",
                },
                color_discrete_sequence=["#3b82f6"],
            )
            fig_roll.update_traces(line_width=1.5)
            st.plotly_chart(fig_roll, use_container_width=True)


# ── Tab 3: Jogadores ──────────────────────────────────────────────────────────
with tab_jogadores:
    if current_df.empty:
        st.info("Aguardando primeira coleta...")
    else:
        st.subheader("Estado atual dos jogadores rastreados")

        col_tier, col_sort = st.columns(2)
        with col_tier:
            tier_sel3 = st.multiselect(
                "Tier:",
                options=["challenger", "gm", "master"],
                default=["challenger", "gm"],
                format_func=lambda t: TIER_LABELS[t],
                key="tier_jogadores",
            )
        with col_sort:
            sort_by = st.selectbox("Ordenar por:", ["LP (maior)", "Winrate (maior)", "Jogos (maior)"])

        sort_map = {
            "LP (maior)":       ("lp",      False),
            "Winrate (maior)":  ("winrate", False),
            "Jogos (maior)":    ("wins",    False),
        }
        sort_col, sort_asc = sort_map[sort_by]

        displayed = (
            current_df[current_df["tier"].isin(tier_sel3)]
            .sort_values(sort_col, ascending=sort_asc)
            .reset_index(drop=True)
        )
        displayed.index += 1

        cols_show = ["tier_label", "lp", "wins", "losses", "winrate"]
        rename_map = {
            "tier_label": "Tier",
            "lp":         "LP",
            "wins":       "Vitórias",
            "losses":     "Derrotas",
            "winrate":    "Winrate (%)",
        }

        st.metric("Jogadores exibidos", len(displayed))
        st.dataframe(
            displayed[cols_show].rename(columns=rename_map),
            use_container_width=True,
        )

        # Distribuição de LP por tier
        st.subheader("Distribuição de LP por tier")
        fig_lp_dist = px.box(
            current_df[current_df["tier"].isin(tier_sel3)],
            x="tier_label", y="lp",
            color="tier_label",
            color_discrete_map={v: TIER_COLORS[k] for k, v in TIER_LABELS.items()},
            labels={"tier_label": "Tier", "lp": "LP"},
        )
        fig_lp_dist.update_layout(showlegend=False)
        st.plotly_chart(fig_lp_dist, use_container_width=True)


# ── Tab 4: Dados Brutos ───────────────────────────────────────────────────────
with tab_raw:
    sub1, sub2 = st.tabs(["Mudanças de LP (recentes)", "Estado atual dos jogadores"])

    with sub1:
        if changes_df.empty:
            st.info("Sem dados ainda.")
        else:
            n = st.slider("Últimas N mudanças:", 50, 500, 100)
            show = (
                changes_df.sort_values("timestamp_br", ascending=False)
                .head(n)
                .copy()
            )
            show["tier"] = show["tier"].map(TIER_LABELS).fillna(show["tier"])
            show = show.rename(columns={
                "timestamp_br": "Data/Hora (BR)",
                "tier":         "Tier",
                "old_lp":       "LP Antes",
                "new_lp":       "LP Depois",
                "lp_delta":     "Delta",
            })
            cols = ["Data/Hora (BR)", "Tier", "LP Antes", "LP Depois", "Delta"]
            st.dataframe(show[cols].reset_index(drop=True), use_container_width=True)

    with sub2:
        if current_df.empty:
            st.info("Sem dados ainda.")
        else:
            st.dataframe(
                current_df.sort_values(["tier", "lp"], ascending=[True, False])
                .reset_index(drop=True),
                use_container_width=True,
            )
