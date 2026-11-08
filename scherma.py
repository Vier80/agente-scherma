"""Agente Scherma

Legge dal sito della FIE (fie.org) i tabelloni a eliminazione diretta delle gare
senior di Grand Prix, Coppa del Mondo, Campionati continentali e Mondiali
(fioretto, spada, sciabola - uomini e donne - individuali e a squadre)
e li scrive in un database Notion.

Le gare NON vanno elencate a mano: l'agente le trova da solo per ogni stagione
scritta nel file stagioni.txt (stagione FIE: 2027 = stagione 2026-2027).

Variabili necessarie: NOTION_TOKEN e NOTION_DATABASE_ID.
Se DRY_RUN=true fa solo una prova: stampa i dati trovati senza scrivere su Notion.
"""
import os
import re
import sys
import time
from datetime import date, timedelta

import requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
_raw_id = os.getenv("NOTION_DATABASE_ID", "").strip().replace("-", "")
_found = re.search(r"[0-9a-fA-F]{32}", _raw_id)
DATABASE_ID = _found.group(0) if _found else _raw_id
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() == "true"

# ------------------------------------------------------------ impostazioni
# Livelli di gara da importare (codici usati dal sito FIE)
LEVELS = {
    "GP": "Grand Prix",
    "A": "Coppa del Mondo",
    "CHZ": "Campionati continentali",
    "CHM": "Campionati del Mondo",
}
INCLUDE_PRELIMINARY = True   # tabellone preliminare (T256/T128) delle gare individuali
INCLUDE_PLACEMENTS = False   # incontri per i piazzamenti 5°-8° ecc. nelle gare a squadre
SKIP_FINISHED_AFTER_DAYS = 3 # gare finite da più di 3 giorni e già in Notion: non le rilegge
PAUSE_SECONDS = 1.0          # pausa tra una richiesta e l'altra al sito FIE

FIE = "https://fie.org"
WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://fie.org/",
}
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

WEAPONS = {"F": "Fioretto", "E": "Spada", "S": "Sciabola"}
GENDERS = {"M": "Men", "F": "Women"}
CONTINENTS = [("europ", "Campionati Europei"), ("asiat", "Campionati Asiatici"),
              ("panam", "Campionati Panamericani"), ("afri", "Campionati Africani"),
              ("ocean", "Campionati d'Oceania")]

# Nomi delle colonne nel database (maiuscole/minuscole non contano).
# La colonna "titolo" viene riconosciuta da sola, qualunque nome abbia.
COLUMNS = {
    "p1": ["Player 1"],
    "noc1": ["NOC 1"],
    "p2": ["Player 2"],
    "noc2": ["NOC 2"],
    "gender": ["Gender"],
    "weapon": ["Arma"],
    "score": ["Score"],
    "round": ["Turno"],
    "tournament": ["Torneo"],
    "date": ["Data"],
    "source": ["Fonte", "Fonte (link)", "Link"],
    "id": ["ID univoco", "ID"],
}
# Colonne facoltative: se nel database ci sono vengono compilate, altrimenti pazienza.
OPTIONAL_COLUMNS = {
    "kind": ["Gara", "Tipo"],        # Individuale / Squadre
    "level": ["Livello"],            # Grand Prix / Coppa del Mondo / ...
}


def die(msg):
    print(f"ERRORE: {msg}")
    sys.exit(1)


# ----------------------------------------------------------------- sito FIE
def fie_get(path, params=None):
    for attempt in range(3):
        try:
            r = requests.get(FIE + path, params=params, headers=WEB_HEADERS, timeout=40)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(3 + attempt * 3)
        finally:
            time.sleep(PAUSE_SECONDS)


def list_events(season):
    """Tutte le gare senior della stagione ai livelli scelti, già iniziate."""
    data = fie_get("/api/fie/competitions", {"season": season, "pageSize": 1000}) or {}
    today = date.today().isoformat()
    events = [e for e in data.get("items", [])
              if e.get("category") == "S"
              and e.get("competitionCategory") in LEVELS
              and e.get("weapon") in WEAPONS
              and (e.get("startDate") or "9999") <= today]
    events.sort(key=lambda e: (e.get("startDate") or "", e["competitionId"]))
    return events


def tournament_name(ev):
    cat = ev["competitionCategory"]
    year = (ev.get("startDate") or "")[:4]
    place = (ev.get("location") or "").strip()
    if cat == "CHZ":
        low = (ev.get("name") or "").lower()
        label = next((it for key, it in CONTINENTS if key in low), ev.get("name") or LEVELS[cat])
    else:
        label = LEVELS[cat]
    return re.sub(r"\s+", " ", f"{label} {place} {year}").strip()


def size_of(table_id):
    digits = re.sub(r"\D", "", table_id)
    return int(digits) if digits else 0


def main_round_label(n):
    return {2: "Finale", 4: "Semifinale", 8: "Quarti di finale"}.get(n, f"Tabellone da {n}")


def is_bye(bout):
    f1, f2 = bout.get("fencer1") or {}, bout.get("fencer2") or {}
    names = {(f1.get("name") or "").strip().upper(), (f2.get("name") or "").strip().upper()}
    return bool(bout.get("isBye")) or "BYE" in names or "" in names


def losers(rounds_list):
    out = set()
    for bout in rounds_list:
        for a, b in (("fencer1", "fencer2"), ("fencer2", "fencer1")):
            if (bout.get(b) or {}).get("isWinner") and not (bout.get(a) or {}).get("isWinner"):
                out.add((bout[a].get("name") or "").strip())
    return out


def pick_tables(tableau):
    """Decide quali parti del tabellone importare e con che nome di turno.

    Restituisce un elenco di (codice tabella, nome turno, è_preliminare, incontri).
    - tabellone principale = la prima "suite" che contiene la finale (tabella da 2)
    - le suite prima di quella = tabellone preliminare (gare individuali)
    - dopo: finale per il 3° posto (squadre) ed eventuali piazzamenti
    """
    suites = [s for s in tableau if s.get("rounds")]
    if not suites:
        return []
    main_idx = next((i for i, s in enumerate(suites)
                     if any(size_of(k) == 2 for k in s["rounds"])), None)
    if main_idx is None:  # gara ancora in corso: prendo l'ultima suite come principale
        main_idx = len(suites) - 1

    chosen = []
    for i, suite in enumerate(suites):
        rounds = suite["rounds"]
        if i < main_idx:
            if INCLUDE_PRELIMINARY:
                for k, bouts in rounds.items():
                    chosen.append((k, f"Preliminare da {size_of(k)}", True, bouts))
        elif i == main_idx:
            for k, bouts in rounds.items():
                chosen.append((k, main_round_label(size_of(k)), False, bouts))
        else:
            main_rounds = suites[main_idx]["rounds"]
            semi = next((v for k, v in main_rounds.items() if size_of(k) == 4), [])
            semi_losers = losers(semi)
            only = list(rounds.items())
            if (len(only) == 1 and size_of(only[0][0]) == 2 and len(only[0][1]) == 1 and semi_losers
                    and {(only[0][1][0]["fencer1"].get("name") or "").strip(),
                         (only[0][1][0]["fencer2"].get("name") or "").strip()} == semi_losers):
                chosen.append((only[0][0], "Finale 3° posto", False, only[0][1]))
            elif INCLUDE_PLACEMENTS:
                for k, bouts in rounds.items():
                    chosen.append((k, f"Piazzamenti ({k})", False, bouts))
    return chosen


def parse_tableau(data, ev):
    """Trasforma il tabellone FIE nell'elenco degli incontri conclusi."""
    season, comp = ev["season"], ev["competitionId"]
    is_team = ev.get("type") == "E"
    tournament = tournament_name(ev)
    matches = {}
    for table_id, round_name, prelim, bouts in pick_tables((data or {}).get("tableau") or []):
        for bout in bouts:
            if is_bye(bout):
                continue
            f1, f2 = bout["fencer1"], bout["fencer2"]
            if not (f1.get("isWinner") or f2.get("isWinner")):
                continue  # non ancora disputato
            if f2.get("isWinner") and not f1.get("isWinner"):
                f1, f2 = f2, f1  # il vincitore va sempre come Player 1
            s1, s2 = f1.get("score"), f2.get("score")
            score = f"{s1}-{s2}" if s1 is not None and s2 is not None else "Vittoria a tavolino"
            n1, n2 = (f1.get("name") or "").strip(), (f2.get("name") or "").strip()
            when = ev.get("startDate") if prelim else (ev.get("endDate") or ev.get("startDate"))
            m = {
                "id": f"{season}|{comp}|{table_id}|{n1}|{n2}",
                "p1": n1, "noc1": f1.get("nationality") or "",
                "p2": n2, "noc2": f2.get("nationality") or "",
                "gender": GENDERS.get(ev.get("gender"), ""),
                "weapon": WEAPONS.get(ev.get("weapon"), ""),
                "score": score, "round": round_name,
                "tournament": tournament, "date": when or "",
                "kind": "Squadre" if is_team else "Individuale",
                "level": LEVELS[ev["competitionCategory"]],
                "source": "",
            }
            matches[m["id"]] = m
    return list(matches.values())


def source_link(ev):
    """Link alla pagina del tabellone sul sito FIE (serve il codice del torneo)."""
    detail = fie_get(f"/api/fie/competition/{ev['season']}/{ev['competitionId']}") or {}
    tid = detail.get("tournamentId")
    if tid:
        return f"{FIE}/tournaments/{ev['season']}/{tid}/event/{ev['competitionId']}/tableau"
    return f"{FIE}/api/fie/competition/{ev['season']}/{ev['competitionId']}/results/tableau"


# ------------------------------------------------------------------- Notion
def notion(method, path, **kwargs):
    r = None
    for attempt in range(4):
        r = requests.request(method, "https://api.notion.com/v1" + path,
                             headers=NOTION_HEADERS, timeout=30, **kwargs)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 2)) + attempt)
            continue
        break
    if not r.ok:
        raise RuntimeError(f"Notion ha risposto {r.status_code}: {r.text[:400]}")
    return r.json()


def read_schema():
    props = notion("GET", f"/databases/{DATABASE_ID}")["properties"]
    by_name = {n.strip().lower(): (n, p["type"]) for n, p in props.items()}
    mapping, missing = {}, []
    for key, options in list(COLUMNS.items()) + list(OPTIONAL_COLUMNS.items()):
        hit = next((by_name[o.lower()] for o in options if o.lower() in by_name), None)
        if hit:
            mapping[key] = hit
        elif key in COLUMNS:
            missing.append(options[0])
    titles = [(n, p["type"]) for n, p in props.items() if p["type"] == "title"]
    mapping["title"] = titles[0]
    if missing:
        die("nel database non trovo queste colonne: " + ", ".join(missing)
            + ". Colonne presenti: " + ", ".join(props.keys()))
    return mapping


def existing_ids(id_column):
    ids, cursor = set(), None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = notion("POST", f"/databases/{DATABASE_ID}/query", json=body)
        for page in data["results"]:
            prop = page["properties"].get(id_column, {})
            parts = prop.get("rich_text") or prop.get("title") or []
            text = "".join(p.get("plain_text", "") for p in parts)
            if text:
                ids.add(text)
        if not data.get("has_more"):
            return ids
        cursor = data["next_cursor"]


def notion_value(ptype, value):
    value = "" if value is None else str(value)
    if ptype == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}]}
    if ptype == "select":
        return {"select": {"name": value.replace(",", " ")[:100]} if value else None}
    if ptype == "date":
        return {"date": {"start": value} if value else None}
    if ptype == "url":
        return {"url": value or None}
    raise RuntimeError(f"Tipo di colonna non gestito: {ptype}. Usa testo, selezione, data o URL.")


def create_row(mapping, m):
    values = dict(m)
    values["title"] = f"{m['p1']} vs {m['p2']}"
    props = {name: notion_value(ptype, values.get(key, "")) for key, (name, ptype) in mapping.items()}
    notion("POST", "/pages", json={"parent": {"database_id": DATABASE_ID}, "properties": props})


# --------------------------------------------------------------------- main
def read_seasons():
    with open("stagioni.txt", encoding="utf-8") as f:
        rows = [r.strip() for r in f if r.strip() and not r.strip().startswith("#")]
    seasons = []
    for r in rows:
        if r.isdigit():
            seasons.append(int(r))
        else:
            print(f"Riga ignorata in stagioni.txt (non è un anno): {r}")
    return seasons


def main():
    if not DRY_RUN and (not NOTION_TOKEN or not DATABASE_ID):
        die("mancano NOTION_TOKEN o NOTION_DATABASE_ID (controlla i Secrets su GitHub).")
    mapping, done = None, set()
    if not DRY_RUN:
        mapping = read_schema()
        done = existing_ids(mapping["id"][0])
        print(f"Righe già presenti in Notion: {len(done)}")
    else:
        print("MODALITÀ PROVA: non scrivo nulla su Notion.")
    done_events = {"|".join(i.split("|")[:2]) for i in done}
    old_limit = (date.today() - timedelta(days=SKIP_FINISHED_AFTER_DAYS)).isoformat()

    errors = added = shown = 0
    for season in read_seasons():
        print(f"\n===== Stagione {season - 1}-{season} =====")
        try:
            events = list_events(season)
        except requests.RequestException as e:
            print(f"  Sito FIE non raggiungibile: {e}")
            errors += 1
            continue
        print(f"Gare trovate: {len(events)}")
        for ev in events:
            key = f"{season}|{ev['competitionId']}"
            label = (f"{tournament_name(ev)} - {WEAPONS[ev['weapon']]} "
                     f"{GENDERS.get(ev.get('gender'), '')} "
                     f"{'squadre' if ev.get('type') == 'E' else 'individuale'}")
            if key in done_events and (ev.get("endDate") or "") < old_limit:
                continue  # gara finita e già importata
            try:
                data = fie_get(f"/api/fie/competition/{season}/{ev['competitionId']}/results/tableau")
            except requests.RequestException as e:
                print(f"  {label}: tabellone non leggibile ({e})")
                errors += 1
                continue
            matches = parse_tableau(data, ev)
            new = [m for m in matches if m["id"] not in done]
            print(f"  {label}: incontri {len(matches)}, nuovi {len(new)}")
            if not new:
                continue
            try:
                link = source_link(ev)
            except requests.RequestException:
                link = FIE
            for m in new:
                m["source"] = link
                if DRY_RUN:
                    if shown < 15:
                        print(f"     [{m['round']}] {m['p1']} ({m['noc1']}) vs "
                              f"{m['p2']} ({m['noc2']}) {m['score']} | {m['date']}")
                        shown += 1
                    continue
                try:
                    create_row(mapping, m)
                    done.add(m["id"])
                    added += 1
                    time.sleep(0.35)
                except RuntimeError as e:
                    print(f"     Errore su {m['p1']} vs {m['p2']}: {e}")
                    errors += 1
    if DRY_RUN:
        print("\nProva terminata (mostrati solo i primi 15 incontri come esempio).")
    else:
        print(f"\nFatto. Righe aggiunte: {added}. Errori: {errors}.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
