"""
Coleta um snapshot dos jogadores Challenger/Grão-Mestre/Mestre da fila Flex (BR1):
  - Registra o LP atual de cada jogador e compara com o snapshot anterior
    para detectar jogos que ocorreram no intervalo entre coletas.
  - Roda a cada 5 minutos (via GitHub Actions + cron-job.org).
  - Não usa a Spectator API — apenas a League API para leitura de LP.

Arquivos gerados/atualizados:
  data/snapshots.csv   — uma linha por ciclo (agregado)
  data/player_lp.csv   — uma linha por jogador por ciclo (histórico de LP)
"""

import csv
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

API_KEY = os.environ.get("RIOT_API_KEY", "")
if not API_KEY:
    raise SystemExit("Variável RIOT_API_KEY não definida.")

PLATFORM = "br1"
QUEUE    = "RANKED_FLEX_SR"

CALL_DELAY = 1.3  # segundos entre chamadas (respeita rate limit)

BASE_URL = f"https://{PLATFORM}.api.riotgames.com"

DATA_DIR      = Path(__file__).parent / "data"
SNAPSHOTS_CSV = DATA_DIR / "snapshots.csv"
PLAYER_LP_CSV = DATA_DIR / "player_lp.csv"

SNAPSHOTS_HEADER = [
    "timestamp_utc",
    "total_tracked",
    "challenger_count",
    "gm_count",
    "master_count",
    "games_detected_by_lp",   # jogadores cujo LP mudou desde o snapshot anterior
    "lp_wins_detected",       # subconjunto: LP subiu  (provável vitória)
    "lp_losses_detected",     # subconjunto: LP caiu   (provável derrota)
]
PLAYER_LP_HEADER = ["timestamp_utc", "puuid", "tier", "lp"]

TIER_FLOORS_CSV    = DATA_DIR / "tier_floors.csv"
TIER_FLOORS_HEADER = [
    "date_br", "challenger_floor", "gm_floor", "master_floor",
    "challenger_count", "gm_count", "master_count", "timestamp_utc",
]
BRT_OFFSET         = timedelta(hours=-3)
FLOOR_WINDOW_START = (23, 40)
FLOOR_WINDOW_END   = (23, 55)

session = requests.Session()
session.headers.update({"X-Riot-Token": API_KEY})
start_time = time.time()


def elapsed() -> str:
    s = int(time.time() - start_time)
    return f"{s // 60}m{s % 60:02d}s"


def get_with_retry(url: str, max_retries: int = 6) -> requests.Response:
    for attempt in range(max_retries):
        resp = session.get(url)
        if resp.status_code in (200, 404):
            return resp
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "10")) + 2
            print(f"  [{elapsed()}] Rate limit — aguardando {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504):
            print(f"  [{elapsed()}] HTTP {resp.status_code} transitório, tentativa {attempt+1}/{max_retries}...", flush=True)
            time.sleep(3 * (attempt + 1))
            continue
        print(f"  [{elapsed()}] HTTP {resp.status_code} inesperado: {url}", flush=True)
        return resp
    raise RuntimeError(f"Falha após {max_retries} tentativas: {url}")


def fetch_league(tier_url: str) -> list[dict]:
    resp = get_with_retry(tier_url)
    resp.raise_for_status()
    time.sleep(CALL_DELAY)
    return resp.json().get("entries", [])


def ensure_csvs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SNAPSHOTS_CSV.exists():
        with SNAPSHOTS_CSV.open("w", newline="") as f:
            csv.writer(f).writerow(SNAPSHOTS_HEADER)
    if not PLAYER_LP_CSV.exists():
        with PLAYER_LP_CSV.open("w", newline="") as f:
            csv.writer(f).writerow(PLAYER_LP_HEADER)

def floor_already_captured_today(date_br: str) -> bool:
    if not TIER_FLOORS_CSV.exists():
        return False
    with TIER_FLOORS_CSV.open(newline="") as f:
        return any(row["date_br"] == date_br for row in csv.DictReader(f))


def save_tier_floor(date_br, challengers, gm_players, masters, ts_utc):
    chall_lps  = sorted(e["leaguePoints"] for e in challengers if e.get("puuid"))
    gm_lps     = sorted(e["leaguePoints"] for e in gm_players  if e.get("puuid"))
    master_lps = sorted(e["leaguePoints"] for e in masters      if e.get("puuid"))
    row = {
        "date_br":          date_br,
        "challenger_floor": chall_lps[0]  if chall_lps  else "",
        "gm_floor":         gm_lps[0]     if gm_lps     else "",
        "master_floor":     master_lps[0] if master_lps else "",
        "challenger_count": len(challengers),
        "gm_count":         len(gm_players),
        "master_count":     len(masters),
        "timestamp_utc":    ts_utc,
    }
    is_new = not TIER_FLOORS_CSV.exists()
    with TIER_FLOORS_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TIER_FLOORS_HEADER, extrasaction="ignore")
        if is_new:
            w.writeheader()
        w.writerow(row)
    print(
        f"  [{elapsed()}] 📊 Piso capturado — "
        f"Challenger: {row['challenger_floor']} LP | "
        f"GM: {row['gm_floor']} LP",
        flush=True,
    )


def load_previous_lp() -> dict[str, int]:
    """Lê o LP mais recente de cada jogador do player_lp.csv."""
    if not PLAYER_LP_CSV.exists():
        return {}
    prev: dict[str, int] = {}
    with PLAYER_LP_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            prev[row["puuid"]] = int(row["lp"])  # sobrescreve → fica o mais recente
    return prev


def save_player_lp(ts: str, players: list[tuple[str, str, int]]):
    """Acrescenta linhas (timestamp, puuid, tier, lp) ao player_lp.csv."""
    with PLAYER_LP_CSV.open("a", newline="") as f:
        w = csv.writer(f)
        for puuid, tier, lp in players:
            w.writerow([ts, puuid, tier, lp])


def save_snapshot(ts: str, total: int,
                  n_chall: int, n_gm: int, n_master: int,
                  games_lp: int, wins_lp: int, losses_lp: int):
    with SNAPSHOTS_CSV.open("a", newline="") as f:
        csv.writer(f).writerow([
            ts, total, n_chall, n_gm, n_master,
            games_lp, wins_lp, losses_lp,
        ])


def main():
    ensure_csvs()

    now_utc = datetime.now(timezone.utc)
    now_br  = now_utc + BRT_OFFSET
    ts_utc  = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
    date_br = now_br.strftime("%Y-%m-%d")
    in_floor_window = (
        FLOOR_WINDOW_START <= (now_br.hour, now_br.minute) <= FLOOR_WINDOW_END
    )

    print(f"[{elapsed()}] Buscando listas Challenger + Grão-Mestre + Mestre (Flex BR)...", flush=True)
    challengers = fetch_league(f"{BASE_URL}/lol/league/v4/challengerleagues/by-queue/{QUEUE}")
    gm_players  = fetch_league(f"{BASE_URL}/lol/league/v4/grandmasterleagues/by-queue/{QUEUE}")
    masters     = fetch_league(f"{BASE_URL}/lol/league/v4/masterleagues/by-queue/{QUEUE}")

    total = len(challengers) + len(gm_players) + len(masters)
    print(
        f"[{elapsed()}] Challenger: {len(challengers)} | GM: {len(gm_players)} | "
        f"Mestre: {len(masters)} | Total: {total}",
        flush=True,
    )

    # Captura piso de tier na janela das 23h40-23h55 (atualização diária da Riot)
    if in_floor_window and not floor_already_captured_today(date_br):
        print(f"[{elapsed()}] Janela 23h40-23h55 — capturando piso de tier...", flush=True)
        save_tier_floor(date_br, challengers, gm_players, masters, ts_utc)
    elif in_floor_window:
        print(f"[{elapsed()}] Piso de {date_br} já capturado.", flush=True)

    prev_lp = load_previous_lp()
    is_first_run = len(prev_lp) == 0
    if is_first_run:
        print(f"[{elapsed()}] Primeira execução — sem LP anterior para comparar.", flush=True)
    else:
        print(f"[{elapsed()}] LP anterior carregado para {len(prev_lp)} jogadores.", flush=True)

    all_players = (
        [(e, "challenger") for e in challengers]
        + [(e, "gm")         for e in gm_players]
        + [(e, "master")     for e in masters]
    )

    checked   = 0
    errors    = 0
    games_lp  = 0
    wins_lp   = 0
    losses_lp = 0
    current_lp_snapshot: list[tuple[str, str, int]] = []

    LOG_INTERVAL = 100

    for i, (entry, tier) in enumerate(all_players, start=1):
        puuid = entry.get("puuid")
        lp    = entry.get("leaguePoints", 0)

        if puuid is None:
            errors += 1
            continue

        current_lp_snapshot.append((puuid, tier, lp))

        if not is_first_run and puuid in prev_lp:
            delta = lp - prev_lp[puuid]
            if delta > 0:
                games_lp += 1
                wins_lp  += 1
            elif delta < 0:
                games_lp  += 1
                losses_lp += 1

        checked += 1

        if checked % LOG_INTERVAL == 0 or i == len(all_players):
            print(
                f"[{elapsed()}] {checked}/{total} ({checked/total*100:.0f}%) — "
                f"jogos detectados (LP): {games_lp} (+{wins_lp}W / -{losses_lp}L)",
                flush=True,
            )

    save_player_lp(ts_utc, current_lp_snapshot)
    save_snapshot(
        ts_utc, checked,
        len(challengers), len(gm_players), len(masters),
        games_lp, wins_lp, losses_lp,
    )

    print(f"\n[{elapsed()}] ✅ Concluído!", flush=True)
    if not is_first_run:
        print(f"[{elapsed()}] Jogos detectados por LP: {games_lp} (+{wins_lp}W / -{losses_lp}L)", flush=True)
    print(f"[{elapsed()}] Erros/sem PUUID: {errors}", flush=True)
    print(f"[{elapsed()}] Snapshot salvo.", flush=True)


if __name__ == "__main__":
    main()
