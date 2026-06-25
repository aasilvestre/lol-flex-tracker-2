"""
collector.py — Coleta de LP a cada 5 minutos

Busca as listas completas de Challenger, Grão-Mestre e Mestre
na Ranqueada Flex (BR1) com apenas 3 chamadas à API.

Detecta variações de LP entre execuções consecutivas e registra
somente as mudanças — não o estado completo de todos os jogadores
a cada run, evitando arquivos gigantes.

Arquivos gerados:
  data/player_current.csv  — estado atual de todos os jogadores (sobrescrito a cada run)
  data/lp_changes.csv      — histórico de variações de LP (append somente quando LP muda)
"""

import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = os.environ.get("RIOT_API_KEY", "")
if not API_KEY:
    raise SystemExit("RIOT_API_KEY não definida.")

QUEUE         = "RANKED_FLEX_SR"
PLATFORM_BASE = "https://br1.api.riotgames.com"

DATA_DIR            = Path(__file__).parent / "data"
PLAYER_CURRENT_CSV  = DATA_DIR / "player_current.csv"
LP_CHANGES_CSV      = DATA_DIR / "lp_changes.csv"

PLAYER_CURRENT_HEADER = [
    "puuid", "tier", "lp", "wins", "losses", "last_updated_utc",
]
LP_CHANGES_HEADER = [
    "timestamp_utc", "puuid", "tier", "old_lp", "new_lp", "lp_delta",
]

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
        elif resp.status_code in (500, 502, 503, 504):
            time.sleep(3 * (attempt + 1))
        else:
            print(f"  [{elapsed()}] HTTP {resp.status_code}: {url}", flush=True)
            return resp
    raise RuntimeError(f"Falha após {max_retries} tentativas: {url}")


def fetch_league(endpoint: str) -> list[dict]:
    url = f"{PLATFORM_BASE}/lol/league/v4/{endpoint}leagues/by-queue/{QUEUE}"
    resp = get_with_retry(url)
    resp.raise_for_status()
    return resp.json().get("entries", [])


def load_current() -> dict[str, dict]:
    """Carrega o estado atual dos jogadores como dict puuid → row."""
    if not PLAYER_CURRENT_CSV.exists():
        return {}
    with PLAYER_CURRENT_CSV.open(newline="") as f:
        return {row["puuid"]: row for row in csv.DictReader(f)}


def save_current(rows: list[dict]):
    """Sobrescreve player_current.csv com o estado mais recente."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PLAYER_CURRENT_CSV.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PLAYER_CURRENT_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(PLAYER_CURRENT_CSV)  # substituição atômica


def append_changes(changes: list[dict]):
    """Adiciona linhas ao lp_changes.csv somente quando há mudanças."""
    if not changes:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not LP_CHANGES_CSV.exists()
    with LP_CHANGES_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LP_CHANGES_HEADER, extrasaction="ignore")
        if is_new:
            w.writeheader()
        w.writerows(changes)


def ensure_lp_changes_header():
    if not LP_CHANGES_CSV.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LP_CHANGES_CSV.open("w", newline="") as f:
            csv.writer(f).writerow(LP_CHANGES_HEADER)


def main():
    ensure_lp_changes_header()

    print(f"[{elapsed()}] Buscando Challenger + GM + Mestre (Flex BR)...", flush=True)
    challengers = fetch_league("challenger")
    gm_players  = fetch_league("grandmaster")
    masters     = fetch_league("master")

    total = len(challengers) + len(gm_players) + len(masters)
    print(
        f"[{elapsed()}] Challenger: {len(challengers)} | "
        f"GM: {len(gm_players)} | "
        f"Mestre: {len(masters)} | "
        f"Total: {total}",
        flush=True,
    )

    prev = load_current()
    is_first_run = not bool(prev)
    if is_first_run:
        print(f"[{elapsed()}] Primeira execução — sem estado anterior. Próxima run já detecta deltas.", flush=True)
    else:
        print(f"[{elapsed()}] Estado anterior carregado: {len(prev)} jogadores.", flush=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    all_players = (
        [(e, "challenger") for e in challengers]
        + [(e, "gm")       for e in gm_players]
        + [(e, "master")   for e in masters]
    )

    new_rows: list[dict] = []
    changes:  list[dict] = []
    lp_ups   = 0
    lp_downs = 0

    for entry, tier in all_players:
        puuid  = entry.get("puuid")
        lp     = entry.get("leaguePoints", 0)
        wins   = entry.get("wins", 0)
        losses = entry.get("losses", 0)

        if not puuid:
            continue

        new_rows.append({
            "puuid":            puuid,
            "tier":             tier,
            "lp":               lp,
            "wins":             wins,
            "losses":           losses,
            "last_updated_utc": ts,
        })

        if not is_first_run and puuid in prev:
            old_lp = int(prev[puuid]["lp"])
            delta  = lp - old_lp
            if delta != 0:
                changes.append({
                    "timestamp_utc": ts,
                    "puuid":         puuid,
                    "tier":          tier,
                    "old_lp":        old_lp,
                    "new_lp":        lp,
                    "lp_delta":      delta,
                })
                if delta > 0:
                    lp_ups += 1
                else:
                    lp_downs += 1

    save_current(new_rows)
    append_changes(changes)

    print(f"\n[{elapsed()}] ✅ Concluído!", flush=True)
    print(f"[{elapsed()}] Jogadores rastreados: {len(new_rows)}", flush=True)
    if not is_first_run:
        print(f"[{elapsed()}] LP mudou:           {len(changes)} (+{lp_ups} / -{lp_downs})", flush=True)
    print(f"[{elapsed()}] Arquivos salvos.", flush=True)


if __name__ == "__main__":
    main()
