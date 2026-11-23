"""Agente Scherma – versione 3.1 (23/11/2026, schede 54 e 55: pagina dei risultati)

Che cosa fa
-----------
Legge dal sito della FIE (fie.org) i tabelloni a eliminazione diretta delle gare senior
(fioretto, spada, sciabola - uomini e donne - individuali e a squadre) e scrive ogni incontro
nel database Notion "🤺 Agente Scherma", una riga per incontro.
NOVITÀ della versione 3: prepara anche scherma-risultati.json, con il TABELLONE PRINCIPALE
ufficiale di ogni gara (stesso ordine del sito FIE), e lo pubblica nel repository map-q-la28,
dove lo legge la pagina scherma-risultati.html.

Gare lette (scheda 26 §3):
  - Grand Prix, Coppa del Mondo, Campionati continentali, Campionati del Mondo;
  - Giochi continentali (European, Asian, African, Pan American, Pacific Games...) e
    Giochi del Mediterraneo, riconosciuti dal NOME perché la FIE li mette tra le gare "NF";
  - Olimpiadi (dal 2028), riconosciute dal nome.
  Escluse: satelliti, gare nazionali, Universiadi, giovanili.

Le gare NON vanno elencate a mano: l'agente le trova da solo per ogni stagione scritta nel
file stagioni.txt (stagione FIE: 2027 = stagione 2026-2027).

Storia delle versioni
---------------------
2   (16/11/2026) ID FIE dei due schermidori, esiti anomali nello Score (MED, DNS, DNF...),
    gare saltate solo se in Notion c'è la Finale, modalità "completa", limite di tempo.
2.1 (16/11/2026) lettura delle righe di Notion divisa per arma e genere (limite di 10.000 righe).
3.0 (23/11/2026) NOVITÀ: file scherma-risultati.json per la pagina dei risultati (scheda 54):
    - per ogni gara il tabellone principale (dal tabellone da 64, 32, 16... fino alla finale),
      la finale per il 3° posto delle gare a squadre e le gare in calendario ancora da giocare;
    - le gare sono raggruppate per tappa (codice FIE "tournamentId", es. Plovdiv 2026 =
      fioretto uomini e donne, individuale e a squadre);
    - le gare già concluse vengono riprese dal file già pubblicato, senza richieste al sito;
    - nel lancio vero il file si pubblica in map-q-la28 (serve il secret MAPPA_TOKEN) e parte
      "Aggiorna mappa LA28"; in prova il file resta solo negli Artifacts del lancio.
    La parte Notion è IDENTICA alla versione 2.1: stesse colonne, stessi ID univoci.
3.1 (23/11/2026) NOVITÀ (scheda 55): teste di serie nel json della pagina. La FIE non le scrive
    nel tabellone: si ricavano dal POSTO nel primo turno (ordine standard: 1 in cima, 2 in fondo).
    - tabellone senza preliminare (squadre, gare piccole): testa di serie = posto nel primo turno;
    - con il preliminare: chi entra direttamente nel tabellone principale (esentati e migliori
      dei gironi) ha il suo posto; chi arriva dal preliminare ha "entrati diretti + posto nel
      primo turno del preliminare". Verificato il 23/11/2026 sulla classifica dei gironi di
      Plovdiv (fioretto U): i posti 17-32 coincidono con i primi 16 dei gironi.
    Le gare salvate dalla 3.0 (senza teste di serie) si rileggono una volta dal sito.
    Il workflow (versione 5) ha in più l'input "elo": i lanci leggeri dei giorni di gara
    (cron-job.org, elo=false) aggiornano Notion e la pagina senza avviare l'ELO.

Variabili d'ambiente (arrivano dal workflow)
--------------------------------------------
NOTION_TOKEN, NOTION_DATABASE_ID   i due segreti del repository
MAPPA_TOKEN                        (novità 3.0) token GitHub con "Contents" e "Actions" in
                                   lettura e scrittura su map-q-la28; senza, Notion si aggiorna
                                   lo stesso ma la pagina web no
DRY_RUN=true                       solo prova: legge tutto, stampa, NON scrive su Notion
                                   e NON pubblica la pagina (il json resta negli Artifacts)
MODALITA                           "aggiungi" (default): scrive solo gli incontri nuovi
                                   "completa": in più compila gli ID FIE delle righe esistenti
TEMPO_MAX_MINUTI                   dopo quanti minuti smettere di scrivere (default 330)
OGGI                               solo per i collaudi: data finta AAAA-MM-GG
"""
import base64
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

import requests

VERSIONE = "3.1"

# Orologio di partenza: serve per fermarsi in ordine prima del limite di tempo di GitHub.
AVVIO = time.monotonic()

# ------------------------------------------------------------ impostazioni
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
# l'id del database si può incollare anche come link intero: ne estraggo i 32 caratteri
_raw_id = os.getenv("NOTION_DATABASE_ID", "").strip().replace("-", "")
_found = re.search(r"[0-9a-fA-F]{32}", _raw_id)
DATABASE_ID = _found.group(0) if _found else _raw_id
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() == "true"
MODALITA = os.getenv("MODALITA", "").strip().lower() or "aggiungi"
try:
    TEMPO_MAX_MINUTI = float(os.getenv("TEMPO_MAX_MINUTI", "").strip() or 330)
except ValueError:
    TEMPO_MAX_MINUTI = 330.0

# --- pagina web (novità 3.0)
MAPPA_TOKEN = os.getenv("MAPPA_TOKEN", "").strip()
MAPPA_REPO = "vier80/map-q-la28"            # repository del sito (GitHub Pages)
MAPPA_WORKFLOW = "aggiorna.yml"             # "Aggiorna mappa LA28": ripubblica il sito
FILE_WEB = "scherma-risultati.json"         # file letto da scherma-risultati.html
# una gara iniziata da più di tanti giorni, ma senza finale sul sito FIE, smette di essere
# "in corso" (es. tabellone pubblicato a metà): così la pagina non resta ferma per sempre
GIORNI_CHIUSURA = 7
# significato dei numeri di una casella del tabellone nel json (lo legge anche la pagina)
CAMPI_CASELLA = ["colonna", "posizione", "schermidore_a", "schermidore_b", "vincitore",
                 "stoccate_a", "stoccate_b", "esito", "stato"]
# formato di una gara nel json: le gare con un formato più vecchio si rileggono dal sito
# (2 = versione 3.1, con la testa di serie come quarto valore di ogni schermidore in "s")
FORMATO_GARA = 2

# Livelli di gara riconosciuti dal codice FIE "competitionCategory"
LEVELS = {
    "GP": "Grand Prix",
    "A": "Coppa del Mondo",
    "CHZ": "Campionati continentali",
    "CHM": "Campionati del Mondo",
}
# Valori di "Livello" per le gare riconosciute dal nome (Notion crea l'opzione alla prima scrittura)
LIVELLO_GIOCHI = "Giochi continentali"
LIVELLO_OLIMPIADI = "Olimpiadi"

# Giochi ammessi: parole cercate nel NOME della gara (in minuscolo), in inglese e in francese.
# Verificato il 16/11/2026: la FIE mette i Giochi tra le gare "NF" (es. Mediterranean Games
# Taranto 2026). Gli Asian Games 2026 NON sono nel database FIE.
# Per escludere un tipo di Giochi basta cancellarne la riga (es. i Giochi del Mediterraneo).
GIOCHI_AMMESSI = (
    "european games", "jeux européens", "jeux europeens",
    "asian games", "jeux asiatiques",
    "african games", "all africa games", "jeux africains",
    "pan american games", "panamerican games", "pan-american games", "jeux panaméricains",
    "pacific games", "jeux du pacifique",
    "central american and caribbean games", "jeux d'amérique centrale",
    "commonwealth games",
    "islamic solidarity games",
    "mediterranean games", "jeux méditerranéens", "jeux mediterraneens",
)
# Gare "Games" escluse anche se il nome contiene una parola qui sopra
GIOCHI_ESCLUSI = ("universit", "fisu", "youth", "jeunesse", "military", "militaire", "junior", "cadet")

INCLUDE_PRELIMINARY = True   # tabellone preliminare (T256/T128) delle gare individuali (solo Notion)
INCLUDE_PLACEMENTS = False   # incontri per i piazzamenti 5°-8° ecc. nelle gare a squadre
PAUSE_SECONDS = 1.0          # pausa tra una richiesta e l'altra al sito FIE
PAUSA_NOTION = 0.35          # pausa tra una scrittura e l'altra su Notion
SECONDI_PER_RIGA = 0.75      # stima del tempo reale per riga su Notion (per il preventivo)

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

# Ordine dei turni: serve a scrivere gli incontri dal primo turno alla finale.
# Così la "Finale" è sempre l'ultima riga scritta di una gara: se c'è, la gara è completa.
ORDINE_TURNI = ["Preliminare da 256", "Preliminare da 128", "Preliminare da 64",
                "Tabellone da 128", "Tabellone da 64", "Tabellone da 32", "Tabellone da 16",
                "Quarti di finale", "Semifinale", "Finale 3° posto", "Finale"]

# Nomi delle colonne nel database (maiuscole/minuscole non contano).
# La colonna "titolo" (Incontro) viene riconosciuta da sola.
COLUMNS = {
    "p1": ["Player 1"],          # vincitore
    "noc1": ["NOC 1"],
    "id1": ["ID FIE 1"],         # ID FIE del vincitore (novità v2)
    "p2": ["Player 2"],          # perdente
    "noc2": ["NOC 2"],
    "id2": ["ID FIE 2"],         # ID FIE del perdente (novità v2)
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

# Codici FIE di atleti neutrali (solo per la diagnosi della sessione 2)
NEUTRALI = {"AIN", "NEU", "IFT", "EOR", "ROC", "RFE"}

LIMITE_NOTION = 10000   # una ricerca dell'API Notion restituisce al massimo 10.000 righe (verificato il 16/11/2026)


# ------------------------------------------------------------ diagnosi
# Raccoglie ciò che la "Solo prova" deve mostrare (scheda 26 §7).
DIAG = {
    "livelli": Counter(),                 # livello -> numero di gare
    "giochi": [],                         # Giochi trovati
    "anomalie": Counter(),                # codice FIE (MED, DNS, DNF...) -> numero di incontri
    "esempi_anomalie": [],
    "id_nomi": defaultdict(set),          # ID FIE -> nomi visti (individuali)
    "nome_id": defaultdict(set),          # "nome|NOC" -> ID FIE visti (individuali)
    "senza_id": 0,                        # schermidori senza ID nei dati FIE
    "noc": Counter(),                     # codici nazione degli individuali
}
AVVISI = []        # problemi della pagina web: non fermano l'agente (riepilogo in fondo)


def die(msg):
    print(f"ERRORE: {msg}")
    sys.exit(1)


def avviso(msg):
    """Problema che non deve fermare l'agente né colorare di rosso il lancio."""
    print(f"  Avviso: {msg}")
    AVVISI.append(msg)


def minuti_trascorsi():
    return (time.monotonic() - AVVIO) / 60


def tempo_scaduto():
    """Vero quando è ora di smettere di scrivere, per non essere interrotti a metà da GitHub."""
    return minuti_trascorsi() >= TEMPO_MAX_MINUTI


def oggi():
    """Data di oggi (nei collaudi si può fingere un altro giorno con la variabile OGGI)."""
    finta = os.getenv("OGGI", "").strip()
    return date.fromisoformat(finta) if finta else date.today()


def pulisci(testo):
    """Toglie spazi doppi e a capo (es. 'PAPP  Jazmin' -> 'PAPP Jazmin')."""
    return re.sub(r"\s+", " ", testo or "").strip()


# ----------------------------------------------------------------- sito FIE
def fie_get(path, params=None):
    """Chiama un'API pubblica della FIE; riprova fino a 3 volte se il sito è lento."""
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


# Memoria delle risposte del sito (novità 3.0): ogni tabellone e ogni scheda di gara si legge
# una volta sola per lancio, anche se servono sia per Notion sia per la pagina web.
_CALENDARI, _TABELLONI, _DETTAGLI = {}, {}, {}


def leggi_tabellone(ev):
    """Tabellone FIE di una gara (dalla memoria, se è già stato letto in questo lancio)."""
    chiave = f"{ev['season']}|{ev['competitionId']}"
    if chiave not in _TABELLONI:
        _TABELLONI[chiave] = fie_get(f"/api/fie/competition/{ev['season']}/{ev['competitionId']}/results/tableau")
    return _TABELLONI[chiave]


def dettaglio_gara(ev):
    """Scheda di una gara sul sito FIE: contiene il codice della tappa (tournamentId)."""
    chiave = f"{ev['season']}|{ev['competitionId']}"
    if chiave not in _DETTAGLI:
        _DETTAGLI[chiave] = fie_get(f"/api/fie/competition/{ev['season']}/{ev['competitionId']}") or {}
    return _DETTAGLI[chiave]


def livello_gara(ev):
    """Restituisce il valore di "Livello" se la gara va letta, altrimenti None."""
    cat = ev.get("competitionCategory")
    if cat in LEVELS:
        return LEVELS[cat]
    nome = (ev.get("name") or "").lower()
    if any(x in nome for x in GIOCHI_ESCLUSI):
        return None
    if "olympic games" in nome or "jeux olympiques" in nome:
        return LIVELLO_OLIMPIADI
    if any(g in nome for g in GIOCHI_AMMESSI):
        return LIVELLO_GIOCHI
    return None


def calendario(season):
    """Tutte le gare senior della stagione ai livelli scelti, ANCHE quelle future (novità 3.0:
    le gare future servono alla pagina, nel filtro «Da giocare»)."""
    if season not in _CALENDARI:
        data = fie_get("/api/fie/competitions", {"season": season, "pageSize": 1000}) or {}
        events = []
        for e in data.get("items", []):
            if e.get("category") != "S" or e.get("weapon") not in WEAPONS:
                continue
            livello = livello_gara(e)
            if not livello:
                continue
            e["_livello"] = livello          # lo ricordo per non ricalcolarlo
            e.setdefault("season", season)
            events.append(e)
        events.sort(key=lambda e: (e.get("startDate") or "", e["competitionId"]))
        _CALENDARI[season] = events
    return _CALENDARI[season]


def list_events(season):
    """Le gare della stagione già iniziate (quelle che l'agente legge per Notion)."""
    today = oggi().isoformat()
    return [e for e in calendario(season) if (e.get("startDate") or "9999") <= today]


def tournament_name(ev):
    """Nome del torneo per la colonna "Torneo", es. "Coppa del Mondo Berne 2026"."""
    cat = ev["competitionCategory"]
    year = (ev.get("startDate") or "")[:4]
    place = (ev.get("location") or "").strip()
    if cat == "CHZ":
        low = (ev.get("name") or "").lower()
        label = next((it for key, it in CONTINENTS if key in low), ev.get("name") or LEVELS[cat])
    elif cat in LEVELS:
        label = LEVELS[cat]
    else:
        # Giochi e Olimpiadi: il nome FIE senza l'eventuale anno (es. "Mediterranean Games")
        label = re.sub(r"\b(19|20)\d{2}\b", "", pulisci(ev.get("name"))).strip(" -")
    return pulisci(f"{label} {place} {year}")


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


def esito_anomalo(vinc, perd):
    """None se l'incontro è regolare (vincitore V, perdente D); altrimenti il codice FIE.

    Codici visti nella stagione 2026 (verifica del 16/11/2026): MED (decisione medica),
    DNS (forfait, non si è presentato), DNF (ritiro durante l'incontro), più alcuni casi rari
    con status E, N o X. In questi casi il punteggio non dice chi era più forte.
    """
    sv = (vinc.get("status") or "").strip().upper()
    sp = (perd.get("status") or "").strip().upper()
    if sv == "V" and sp == "D":
        return None
    for f in (perd, vinc):             # prima il codice esteso (newStatus), di solito del perdente
        codice = (f.get("newStatus") or "").strip().upper()
        if codice and codice not in ("V", "D"):
            return codice
    if sp and sp != "D":
        return sp
    return sv or "?"


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
            anomalia = esito_anomalo(f1, f2)
            score = f"{s1}-{s2}" if s1 is not None and s2 is not None else "Vittoria a tavolino"
            if anomalia:
                score += f" ({anomalia})"            # es. "0-0 (MED)"
            n1, n2 = pulisci(f1.get("name")), pulisci(f2.get("name"))
            id1 = str(f1.get("id") or "").strip()
            id2 = str(f2.get("id") or "").strip()
            when = ev.get("startDate") if prelim else (ev.get("endDate") or ev.get("startDate"))
            m = {
                "id": f"{season}|{comp}|{table_id}|{n1}|{n2}",   # stesso formato della versione 1
                "p1": n1, "noc1": f1.get("nationality") or "", "id1": id1,
                "p2": n2, "noc2": f2.get("nationality") or "", "id2": id2,
                "gender": GENDERS.get(ev.get("gender"), ""),
                "weapon": WEAPONS.get(ev.get("weapon"), ""),
                "score": score, "round": round_name,
                "tournament": tournament, "date": when or "",
                "kind": "Squadre" if is_team else "Individuale",
                "level": ev["_livello"],
                "source": "",
                "_anomalia": anomalia,
            }
            matches[m["id"]] = m

            # --- diagnosi
            if anomalia:
                DIAG["anomalie"][anomalia] += 1
                if len(DIAG["esempi_anomalie"]) < 20:
                    DIAG["esempi_anomalie"].append(
                        f"{tournament} | {m['weapon']} {m['gender']} {m['kind']} | {round_name} | "
                        f"{n1} ({m['noc1']}) vs {n2} ({m['noc2']}) | {score}")
            if not is_team:
                for nome, noc, fid in ((n1, m["noc1"], id1), (n2, m["noc2"], id2)):
                    DIAG["noc"][noc] += 1
                    if fid:
                        DIAG["id_nomi"][fid].add(nome)
                        DIAG["nome_id"][f"{nome}|{noc}"].add(fid)
                    else:
                        DIAG["senza_id"] += 1
    return list(matches.values())


def source_link(ev):
    """Link alla pagina del tabellone sul sito FIE (serve il codice del torneo)."""
    tid = dettaglio_gara(ev).get("tournamentId")
    if tid:
        return f"{FIE}/tournaments/{ev['season']}/{tid}/event/{ev['competitionId']}/tableau"
    return f"{FIE}/api/fie/competition/{ev['season']}/{ev['competitionId']}/results/tableau"


# ------------------------------------------------------------------- Notion
def notion(method, path, **kwargs):
    """Chiamata all'API Notion; aspetta e riprova se Notion è occupato (429) o in errore (5xx)."""
    r = None
    for attempt in range(5):
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
    """Legge le colonne del db e le associa ai dati. Si ferma se manca una colonna obbligatoria."""
    props = notion("GET", f"/databases/{DATABASE_ID}")["properties"]
    by_name = {n.strip().lower(): (n, p["type"]) for n, p in props.items() if p["type"] != "title"}
    mapping, missing = {}, []
    for key, options in list(COLUMNS.items()) + list(OPTIONAL_COLUMNS.items()):
        hit = next((by_name[o.lower()] for o in options if o.lower() in by_name), None)
        if hit:
            mapping[key] = hit
        elif key in COLUMNS:
            missing.append(options[0])
    titles = [(n, p["type"]) for n, p in props.items() if p["type"] == "title"]
    if missing:
        die("nel database non trovo queste colonne: " + ", ".join(missing)
            + ". Colonne presenti: " + ", ".join(props.keys()))
    mapping["title"] = titles[0]
    print("Colonne trovate in Notion: titolo '" + titles[0][0] + "' + "
          + ", ".join(f"{n} ({t})" for k, (n, t) in mapping.items() if k != "title"))
    return mapping


def testo_prop(prop):
    """Testo semplice di una proprietà Notion (titolo, testo, selezione o URL)."""
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(p.get("plain_text", "") for p in prop[t]).strip()
    if t == "select":
        return ((prop.get("select") or {}).get("name") or "").strip()
    if t == "url":
        return prop.get("url") or ""
    return ""


def leggi_righe_esistenti(mapping):
    """Tutte le righe del db: ID univoco -> {pagina, ID FIE 1, ID FIE 2, Score, Turno}.

    Versione 2.1 (16/11/2026): l'API di Notion non restituisce più di 10.000 righe per ricerca.
    Con una ricerca sola l'agente non vedrebbe le righe oltre la 10.000ª e le riscriverebbe
    (doppioni). Per questo la lettura è divisa in 6 ricerche, una per arma e genere; se una
    arrivasse al limite, lo script si ferma invece di scrivere doppioni.
    """
    col = {k: mapping[k][0] for k in ("id", "id1", "id2", "score", "round")}
    righe = {}
    for arma in WEAPONS.values():
        for genere in GENDERS.values():
            filtro = {"and": [{"property": mapping["weapon"][0], "select": {"equals": arma}},
                              {"property": mapping["gender"][0], "select": {"equals": genere}}]}
            lette, cursor = 0, None
            while True:
                body = {"page_size": 100, "filter": filtro}
                if cursor:
                    body["start_cursor"] = cursor
                data = notion("POST", f"/databases/{DATABASE_ID}/query", json=body)
                for page in data["results"]:
                    lette += 1
                    p = page["properties"]
                    uid = testo_prop(p.get(col["id"]))
                    if not uid:
                        continue
                    righe[uid] = {"page": page["id"], "id1": testo_prop(p.get(col["id1"])),
                                  "id2": testo_prop(p.get(col["id2"])), "score": testo_prop(p.get(col["score"])),
                                  "turno": testo_prop(p.get(col["round"]))}
                if not data.get("has_more"):
                    break
                cursor = data["next_cursor"]
            if lette >= LIMITE_NOTION:
                die(f"{arma} {genere}: {lette} righe, è il limite di Notion. Bisogna dividere ancora la "
                    "lettura (per esempio per stagione), altrimenti l'agente scriverebbe doppioni.")
    return righe


def notion_value(ptype, value):
    """Converte un valore nel formato che Notion vuole per quel tipo di colonna."""
    value = "" if value is None else str(value)
    if ptype == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}] if value else []}
    if ptype == "select":
        return {"select": {"name": value.replace(",", " ")[:100]} if value else None}
    if ptype == "date":
        return {"date": {"start": value} if value else None}
    if ptype == "url":
        return {"url": value or None}
    raise RuntimeError(f"Tipo di colonna non gestito: {ptype}. Usa testo, selezione, data o URL.")


def create_row(mapping, m):
    values = dict(m)
    values["title"] = f"{m['p1']} vs {m['p2']}"   # colonna "Incontro"
    props = {name: notion_value(ptype, values.get(key, "")) for key, (name, ptype) in mapping.items()}
    notion("POST", "/pages", json={"parent": {"database_id": DATABASE_ID}, "properties": props})


def da_completare(riga, m):
    """Cosa manca a una riga esistente: {chiave: valore}. Vuoto se non manca niente.

    REGOLA (scheda 26): si scrivono SOLO "ID FIE 1" e "ID FIE 2" se sono vuoti, e il codice di
    un esito anomalo nello Score se manca. Nessun'altra colonna viene toccata.
    """
    nuovi = {}
    if not riga["id1"] and m["id1"]:
        nuovi["id1"] = m["id1"]
    if not riga["id2"] and m["id2"]:
        nuovi["id2"] = m["id2"]
    if m["_anomalia"] and f"({m['_anomalia']})" not in riga["score"]:
        nuovi["score"] = m["score"]
    return nuovi


def completa_riga(mapping, riga, nuovi):
    """Scrive in una riga esistente solo le colonne ammesse (ID FIE 1, ID FIE 2, Score)."""
    ammesse = {"id1", "id2", "score"}
    props = {}
    for chiave, valore in nuovi.items():
        if chiave not in ammesse:
            raise RuntimeError(f"tentativo di scrivere la colonna non ammessa '{chiave}': bloccato")
        nome, tipo = mapping[chiave]
        props[nome] = notion_value(tipo, valore)
    notion("PATCH", f"/pages/{riga['page']}", json={"properties": props})


# ======================================================================= PAGINA WEB (novità 3.0)
# scherma-risultati.json, letto da scherma-risultati.html:
#   "tornei": le tappe (es. Coppa del Mondo Plovdiv 2026), ognuna con l'elenco delle sue gare;
#   "gare":   per ogni gara (chiave "stagione|competitionId", la stessa dell'ELO) il tabellone
#             principale: "s" = schermidori o squadre [nome, NOC, ID FIE], "c" = caselle.
# "s" = [nome, NOC, ID FIE, testa di serie] (testa di serie: novità 3.1, None se non nota).
# Casella = [colonna, posizione, schermidore_a, schermidore_b, vincitore, stoccate_a,
#            stoccate_b, esito, stato]:
#   colonna 0 = primo turno del tabellone principale (es. tabellone da 64), posizione 0 = in alto;
#   schermidore_a/b = posizione nell'elenco "s" (-1 = vuoto: bye o ancora da definire);
#   vincitore = 1 (a), 2 (b), 0 (da tirare); stoccate "" se non note; esito = codice FIE di un
#   esito anomalo (MED, DNS, DNF, A, E...) oppure ""; stato "F" tirato, "N" da tirare.
SIGLA_TURNO = {2: "F", 4: "SF", 8: "QF", 16: "T16", 32: "T32", 64: "T64", 128: "T128", 256: "T256"}
ORDINE_ARMI = {"Fioretto": 0, "Spada": 1, "Sciabola": 2}


def chiave_gara(ev):
    return f"{ev['season']}|{ev['competitionId']}"


def nome_fie(f):
    """Nome pulito di uno schermidore o di una squadra (per le squadre la FIE scrive il NOC)."""
    return pulisci(f.get("name"))


def chiave_schermidore(f):
    """Chiave che identifica uno schermidore (o una squadra): l'ID FIE, oppure nome|NOC.
    None per un lato vuoto (bye o ancora da definire)."""
    nome = nome_fie(f or {})
    if not nome or nome.upper() == "BYE":
        return None
    fid = str(f.get("id") or "").strip()
    return fid or f"{nome}|{(f.get('nationality') or '').strip()}"


def ordine_tabellone(n):
    """Teste di serie dall'alto in basso in un tabellone da n (ordine standard FIE).

    Esempio: n = 8 -> [1, 8, 5, 4, 3, 6, 7, 2]: in cima 1 contro 8, in fondo 7 contro 2.
    Si costruisce raddoppiando: ogni testa di serie s incontra (2 × quante) + 1 - s.
    """
    o = [1, 2]
    if n < 2:
        return [1]
    while len(o) < n:
        m = len(o) * 2
        nuovo = []
        for i, s in enumerate(o):
            # nelle coppie pari il più forte sta sopra, in quelle dispari sotto
            nuovo += [s, m + 1 - s] if i % 2 == 0 else [m + 1 - s, s]
        o = nuovo
    return o


def posti_primo_turno(k_turno, bouts):
    """Chiave dello schermidore -> testa di serie del posto che occupa nel primo turno.
    Nei bye la FIE scrive lo schermidore sempre sopra: il passaggio diretto spetta alla testa
    di serie migliore della coppia, quindi gli tocca il numero più piccolo."""
    o = ordine_tabellone(size_of(k_turno))
    posti = {}
    for i, b in enumerate(bouts):
        if 2 * i + 1 >= len(o):
            break
        s1, s2 = o[2 * i], o[2 * i + 1]
        a, c = chiave_schermidore(b.get("fencer1")), chiave_schermidore(b.get("fencer2"))
        if a and c:
            posti[a], posti[c] = s1, s2
        elif a or c:
            posti[a or c] = min(s1, s2)
    return posti


def teste_di_serie(primo_principale, primo_preliminare):
    """Chiave -> testa di serie (scheda 55).

    primo_principale / primo_preliminare = (codice turno, incontri) del primo turno del
    tabellone principale e del preliminare (None se non c'è preliminare).
    - chi entra direttamente nel principale: il suo posto (es. esentati 1-16, migliori dei gironi 17-32);
    - chi arriva dal preliminare: entrati diretti + posto nel preliminare (es. 32 + 1 = 33).
    """
    principale = posti_primo_turno(*primo_principale)
    if not primo_preliminare:
        return principale
    preliminare = posti_primo_turno(*primo_preliminare)
    diretti = [k for k in principale if k not in preliminare]
    teste = {k: principale[k] for k in diretti}
    for k, s in preliminare.items():
        teste[k] = len(diretti) + s
    return teste


def casella_web(bout, col, pos, persone, indice):
    """Una casella del tabellone principale nel formato del json (vedi CAMPI_CASELLA)."""
    f1, f2 = bout.get("fencer1") or {}, bout.get("fencer2") or {}

    def chi(f):
        k = chiave_schermidore(f)
        if not k:
            return -1                                  # lato vuoto: bye o ancora da definire
        if k not in indice:
            indice[k] = len(persone)
            persone.append([nome_fie(f), (f.get("nationality") or "").strip(), str(f.get("id") or "").strip()])
        return indice[k]

    a, b = chi(f1), chi(f2)
    vincitore = 1 if f1.get("isWinner") else 2 if f2.get("isWinner") else 0
    bye = bool(bout.get("isBye")) or "BYE" in (nome_fie(f1).upper(), nome_fie(f2).upper())
    if bye:
        # passaggio diretto: vince chi c'è (a volte la FIE non lo segna come vincitore)
        vincitore = 1 if a >= 0 else 2 if b >= 0 else 0
        return [col, pos, a, b, vincitore, "", "", "", "F" if vincitore else "N"]
    s1 = f1.get("score") if vincitore else None
    s2 = f2.get("score") if vincitore else None
    esito = ""
    if vincitore:
        w, l = (f1, f2) if vincitore == 1 else (f2, f1)
        esito = esito_anomalo(w, l) or ""
    return [col, pos, a, b, vincitore, "" if s1 is None else s1, "" if s2 is None else s2,
            esito, "F" if vincitore else "N"]


def gara_web(ev, data, tid, today):
    """Tabellone principale di una gara per la pagina. None se il tabellone è vuoto."""
    tabelle = pick_tables((data or {}).get("tableau") or [])
    principali = [(k, b) for k, nome, prelim, b in tabelle
                  if not prelim and nome != "Finale 3° posto" and not nome.startswith("Piazzamenti")]
    bronzo = next((b for k, nome, prelim, b in tabelle if nome == "Finale 3° posto"), None)
    info = {"formato": FORMATO_GARA, "torneo": f"{ev['season']}|{tid}", "nome": tournament_name(ev), "livello": ev["_livello"],
            "arma": WEAPONS[ev["weapon"]], "genere": "U" if ev.get("gender") == "M" else "D",
            "tipo": "S" if ev.get("type") == "E" else "I",
            "inizio": ev.get("startDate") or "", "fine": ev.get("endDate") or ev.get("startDate") or "",
            "luogo": pulisci(ev.get("location")),
            "url": f"{FIE}/tournaments/{ev['season']}/{tid}/event/{ev['competitionId']}/tableau" if tid else FIE}
    if (ev.get("startDate") or "9999") > today.isoformat():
        return {**info, "stato": "da giocare", "misura": 0, "turni": [], "s": [], "c": [], "bronzo": None,
                "giocati": 0, "attesi": 0}
    if not principali:
        return None                       # tabellone non pubblicato (es. Giochi del Mediterraneo 2026)
    # colonne: dal turno più grande (tabellone da 64) alla finale
    principali.sort(key=lambda x: -size_of(x[0]))
    persone, indice, caselle = [], {}, []
    for col, (k, bouts) in enumerate(principali):
        for pos, bout in enumerate(bouts):
            caselle.append(casella_web(bout, col, pos, persone, indice))
    cas_bronzo = casella_web(bronzo[0], 0, 0, persone, indice) if bronzo else None
    # teste di serie (novità 3.1): quarto valore di ogni schermidore in "s" (None se non nota)
    preliminari = [(k, b) for k, nome, prelim, b in tabelle if prelim]
    primo_prelim = max(preliminari, key=lambda x: size_of(x[0])) if preliminari else None
    teste = teste_di_serie(principali[0], primo_prelim)
    for k, i in indice.items():
        persone[i].append(teste.get(k))
    misura = size_of(principali[0][0])
    finale = next((c for c in caselle if c[0] == len(principali) - 1), None)
    # bye = passaggio diretto (casella con un lato vuoto e un vincitore): non è un assalto
    bye = sum(1 for c in caselle if c[8] == "F" and (c[2] < 0 or c[3] < 0))
    tirati = [c for c in caselle + ([cas_bronzo] if cas_bronzo else [])
              if c[8] == "F" and c[2] >= 0 and c[3] >= 0]
    # assalti attesi: un tabellone da 64 ne ha 63, meno i bye, più l'eventuale finale per il 3° posto
    attesi = misura - 1 - bye + (1 if cas_bronzo else 0)
    conclusa = bool(finale and finale[8] == "F" and size_of(principali[-1][0]) == 2)
    vecchia = (ev.get("startDate") or "") < (today - timedelta(days=GIORNI_CHIUSURA)).isoformat()
    stato = "concluso" if conclusa or vecchia else "in corso"
    return {**info, "stato": stato, "misura": misura,
            "turni": [SIGLA_TURNO.get(size_of(k), k) for k, _ in principali],
            "s": persone, "c": caselle, "bronzo": cas_bronzo,
            "giocati": len(tirati), "attesi": max(attesi, len(tirati))}


def leggi_pubblicato():
    """Il scherma-risultati.json già pubblicato (serve a non rileggere le gare concluse)."""
    try:
        if MAPPA_TOKEN:
            url = f"https://api.github.com/repos/{MAPPA_REPO}/contents/{FILE_WEB}"
            r = requests.get(url, headers=gh_headers("application/vnd.github.raw+json"), timeout=90)
        else:   # senza token (es. in prova) lo leggo dal repository pubblico
            r = requests.get(f"https://raw.githubusercontent.com/{MAPPA_REPO}/main/{FILE_WEB}", timeout=90)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return json.loads(r.content.decode("utf-8"))
    except (requests.RequestException, ValueError) as e:
        avviso(f"non riesco a leggere il {FILE_WEB} già pubblicato ({e}): rileggo tutte le gare")
        return {}


def costruisci_web(seasons, vecchio):
    """Prepara il contenuto di scherma-risultati.json.

    Rilegge dal sito FIE solo le gare non ancora concluse o non ancora nel file pubblicato;
    le altre le riprende dal file pubblicato, senza richieste.
    """
    today = oggi()
    vecchie = (vecchio or {}).get("gare") or {}
    gare, letti, ripresi, vuoti = {}, 0, 0, []
    for season in seasons:
        try:
            eventi = calendario(season)
        except requests.RequestException as e:
            avviso(f"calendario FIE {season} non leggibile ({e}): tengo le gare già pubblicate di quella stagione")
            for k, g in vecchie.items():
                if k.startswith(f"{season}|"):
                    gare[k] = g
            continue
        for ev in eventi:
            k = chiave_gara(ev)
            prima = vecchie.get(k)
            if prima and prima.get("stato") == "concluso" and prima.get("formato") == FORMATO_GARA:
                gare[k] = prima                       # gara conclusa: nessuna richiesta al sito
                ripresi += 1
                continue
            try:
                if prima and prima.get("torneo"):
                    tid = prima["torneo"].split("|")[1]
                else:
                    tid = dettaglio_gara(ev).get("tournamentId")
                futura = (ev.get("startDate") or "9999") > today.isoformat()
                data = None if futura else leggi_tabellone(ev)
                g = gara_web(ev, data, tid, today)
                letti += 0 if futura else 1
            except requests.RequestException as e:
                avviso(f"{tournament_name(ev)} {WEAPONS[ev['weapon']]} {GENDERS.get(ev.get('gender'), '')}: "
                       f"tabellone non letto ({e})" + ("; tengo quello già pubblicato" if prima else ""))
                if prima:
                    gare[k] = prima
                continue
            if g is None:
                vuoti.append(f"{tournament_name(ev)} - {WEAPONS[ev['weapon']]} {GENDERS.get(ev.get('gender'), '')}")
                continue
            gare[k] = g

    # tappe: gare con lo stesso codice FIE di tappa (stagione|tournamentId)
    tornei = {}
    for k, g in gare.items():
        t = tornei.setdefault(g["torneo"], {"id": g["torneo"], "nome": g["nome"], "categoria": g["livello"],
                                            "stagione": int(g["torneo"].split("|")[0]), "inizio": g["inizio"],
                                            "fine": g["fine"], "luogo": g["luogo"], "gare": [],
                                            "url": f"{FIE}/tournaments/{g['torneo'].replace('|', '/')}"})
        t["gare"].append(k)
        t["inizio"] = min(t["inizio"], g["inizio"]) if t["inizio"] else g["inizio"]
        t["fine"] = max(t["fine"], g["fine"])
        # nome della tappa: quello della gara individuale più importante (i Mondiali restano Mondiali)
        if g["tipo"] == "I" and g["nome"] != t["nome"] and g["livello"] == "Campionati del Mondo":
            t["nome"], t["categoria"] = g["nome"], g["livello"]
    for t in tornei.values():
        t["gare"].sort(key=lambda k: (gare[k]["tipo"] != "I", ORDINE_ARMI.get(gare[k]["arma"], 9),
                                      gare[k]["genere"] != "U", gare[k]["inizio"]))
        stati = {gare[k]["stato"] for k in t["gare"]}
        t["stato"] = ("da giocare" if stati == {"da giocare"} else
                      "concluso" if stati == {"concluso"} else "in corso")
        t["giocati"] = sum(gare[k]["giocati"] for k in t["gare"])
        t["attesi"] = sum(gare[k]["attesi"] for k in t["gare"])
    elenco = sorted(tornei.values(), key=lambda t: (t["inizio"], t["nome"]))
    print(f"\nPagina web: {len(gare)} gare in {len(elenco)} tappe · lette dal sito {letti}, "
          f"riprese dal file pubblicato {ripresi}"
          + (f" · senza tabellone (non mostrate): {', '.join(vuoti)}" if vuoti else ""))
    for t in elenco:
        if t["stato"] != "da giocare":
            print(f"  {t['nome']} ({t['stato']}): " + ", ".join(
                f"{gare[k]['arma'][:3]} {gare[k]['genere']}{' sq.' if gare[k]['tipo'] == 'S' else ''} "
                f"{gare[k]['misura']}" for k in t["gare"]))
    return {"aggiornato": today.isoformat(), "versione": VERSIONE,
            "stagioni": sorted(seasons), "campi_casella": CAMPI_CASELLA,
            "tornei": elenco, "gare": gare}


def gh_headers(accept="application/vnd.github+json"):
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if MAPPA_TOKEN:
        h["Authorization"] = f"Bearer {MAPPA_TOKEN}"
    return h


def pubblica(payload, vecchio):
    """Scrive scherma-risultati.json su map-q-la28 (solo se è cambiato qualcosa oltre la data)
    e fa ripartire «Aggiorna mappa LA28», che ripubblica il sito."""
    if vecchio and {**vecchio, "aggiornato": ""} == {**payload, "aggiornato": ""}:
        print("Pagina web: nessuna novità, non la ripubblico.")
        return
    url = f"https://api.github.com/repos/{MAPPA_REPO}/contents/{FILE_WEB}"
    sha = None
    r = requests.get(url, headers=gh_headers(), timeout=30)
    if r.status_code == 200:
        sha = r.json().get("sha")             # serve a GitHub per sostituire il file esistente
    elif r.status_code != 404:
        raise RuntimeError(f"GitHub ha risposto {r.status_code}: {r.text[:300]}")
    testo = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    corpo = {"message": "Aggiorna i risultati della scherma",
             "content": base64.b64encode(testo.encode("utf-8")).decode("ascii")}
    if sha:
        corpo["sha"] = sha
    r = requests.put(url, headers=gh_headers(), json=corpo, timeout=120)
    if not r.ok:
        raise RuntimeError(f"GitHub non accetta {FILE_WEB}: {r.status_code} {r.text[:300]}")
    r = requests.post(f"https://api.github.com/repos/{MAPPA_REPO}/actions/workflows/{MAPPA_WORKFLOW}/dispatches",
                      headers=gh_headers(), json={"ref": "main"}, timeout=30)
    if r.status_code != 204:
        avviso(f"{FILE_WEB} salvato, ma la ripubblicazione non è partita ({r.status_code}): "
               "si aggiornerà al prossimo giro di «Aggiorna mappa LA28»")
    else:
        print(f"Pagina web: {FILE_WEB} aggiornato ({len(testo) // 1024} KB), ripubblicazione avviata.")


def fase_web(seasons):
    """Costruisce scherma-risultati.json, lo salva nella cartella (per gli Artifacts) e,
    nel lancio vero con MAPPA_TOKEN, lo pubblica. Gli errori diventano avvisi."""
    print("\n" + "=" * 78)
    print("PAGINA WEB (scherma-risultati.json)")
    print("=" * 78)
    if not DRY_RUN and not MAPPA_TOKEN:
        avviso("manca il secret MAPPA_TOKEN: Notion è aggiornato, la pagina web no (scheda 54)")
    vecchio = leggi_pubblicato()
    try:
        payload = costruisci_web(seasons, vecchio)
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as e:
        avviso(f"pagina web non preparata: {e}")
        return
    testo = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(FILE_WEB, "w", encoding="utf-8") as f:
        f.write(testo)
    print(f"{FILE_WEB}: {len(testo) // 1024} KB salvato nella cartella del lancio (Artifacts).")
    if DRY_RUN:
        print("Solo prova: la pagina web non viene pubblicata.")
        return
    if MAPPA_TOKEN:
        try:
            pubblica(payload, vecchio)
        except (requests.RequestException, RuntimeError) as e:
            avviso(f"pubblicazione non riuscita: {e}. Controlla che MAPPA_TOKEN abbia «Contents: "
                   "Read and write» su map-q-la28 e non sia scaduto")


# --------------------------------------------------------------------- stampe
def stampa_diagnosi():
    print("\n" + "=" * 78)
    print("DIAGNOSI (scheda 26 §7)")
    print("=" * 78)
    print("\nGare lette per livello:")
    for liv, n in DIAG["livelli"].most_common():
        print(f"  {liv:<26} {n:>4}")
    print("\nGiochi e Olimpiadi trovati:")
    for g in DIAG["giochi"] or ["nessuno"]:
        print(f"  {g}")

    print("\nEsiti anomali (lo script ELO li escluderà):",
          ", ".join(f"{k} {v}" for k, v in DIAG["anomalie"].most_common()) or "nessuno")
    for e in DIAG["esempi_anomalie"]:
        print(f"    · {e}")

    print("\nControllo degli ID FIE (solo gare individuali):")
    print(f"  ID diversi: {len(DIAG['id_nomi'])} · schermidori senza ID nei dati FIE: {DIAG['senza_id']}")
    piu_nomi = [(i, s) for i, s in DIAG["id_nomi"].items() if len(s) > 1]
    print(f"  ID con più nomi (stessa persona scritta in modo diverso): {len(piu_nomi)}")
    for i, s in piu_nomi[:15]:
        print(f"    · {i}: {' / '.join(sorted(s))}")
    piu_id = [(n, s) for n, s in DIAG["nome_id"].items() if len(s) > 1]
    print(f"  Stesso nome e NOC con più ID (omonimi, persone diverse): {len(piu_id)}")
    for n, s in piu_id[:15]:
        print(f"    · {n}: {', '.join(sorted(s))}")

    neutri = {k: v for k, v in DIAG["noc"].items() if k in NEUTRALI}
    print("\nCodici nazione di atleti neutrali:", ", ".join(f"{k} {v}" for k, v in neutri.items()) or "nessuno",
          f"· RUS {DIAG['noc'].get('RUS', 0)} · BLR {DIAG['noc'].get('BLR', 0)} (presenze negli incontri)")


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
    if MODALITA not in ("aggiungi", "completa"):
        die(f"MODALITA deve essere 'aggiungi' o 'completa' (hai scritto '{MODALITA}').")
    if not DRY_RUN and (not NOTION_TOKEN or not DATABASE_ID):
        die("mancano NOTION_TOKEN o NOTION_DATABASE_ID (controlla i Secrets su GitHub).")
    print(f"Agente Scherma {VERSIONE} · "
          f"{'SOLO PROVA: non scrivo nulla su Notion.' if DRY_RUN else 'SCRITTURA su Notion.'} "
          f"Modalità: {MODALITA}. Limite di tempo: {TEMPO_MAX_MINUTI:.0f} minuti.")

    # 1. righe già presenti in Notion (anche in prova, se il token c'è: è solo lettura)
    mapping, esistenti = None, {}
    if NOTION_TOKEN and DATABASE_ID:
        try:
            mapping = read_schema()
            esistenti = leggi_righe_esistenti(mapping)
        except SystemExit:
            if not DRY_RUN:
                raise
            print("In prova continuo comunque, ma prima del lancio vero le colonne vanno sistemate.")
        except RuntimeError as e:
            if not DRY_RUN:
                raise
            print(f"Non riesco a leggere il db Notion ({e}). In prova continuo senza.")
    else:
        print("Token Notion assente: in prova non leggo il db.")

    # gare complete = gare che in Notion hanno già la "Finale"
    gare_complete = {"|".join(u.split("|")[:2]) for u, r in esistenti.items() if r["turno"] == "Finale"}
    # gare con righe a cui mancano gli ID FIE (servono alla modalità completa)
    gare_senza_id = {"|".join(u.split("|")[:2]) for u, r in esistenti.items() if not (r["id1"] and r["id2"])}
    print(f"Righe già presenti in Notion: {len(esistenti)} · gare complete: {len(gare_complete)} · "
          f"gare con righe senza ID FIE: {len(gare_senza_id)}")
    if gare_senza_id and MODALITA == "aggiungi":
        print("  (per compilare gli ID FIE delle righe esistenti usa la modalità 'completa')")

    errors = added = completate = shown = 0
    tot_nuovi = tot_completare = 0
    interrotto = False
    seasons = read_seasons()
    for season in seasons:
        if interrotto:
            break
        print(f"\n===== Stagione {season - 1}-{season} =====")
        try:
            events = list_events(season)
        except requests.RequestException as e:
            print(f"  Sito FIE non raggiungibile: {e}")
            errors += 1
            continue
        print(f"Gare trovate: {len(events)} ("
              + ", ".join(f"{k} {v}" for k, v in Counter(e['_livello'] for e in events).most_common()) + ")")
        for ev in events:
            key = f"{season}|{ev['competitionId']}"
            DIAG["livelli"][ev["_livello"]] += 1
            label = (f"{tournament_name(ev)} - {WEAPONS[ev['weapon']]} "
                     f"{GENDERS.get(ev.get('gender'), '')} "
                     f"{'squadre' if ev.get('type') == 'E' else 'individuale'}")
            if ev["_livello"] in (LIVELLO_GIOCHI, LIVELLO_OLIMPIADI):
                DIAG["giochi"].append(f"{label} (FIE: '{pulisci(ev.get('name'))}', id {ev['competitionId']})")

            # una gara si salta solo se è completa (c'è la Finale) e non ha righe da completare
            serve_completare = MODALITA == "completa" and key in gare_senza_id
            if key in gare_complete and not serve_completare:
                continue
            if not DRY_RUN and tempo_scaduto():
                interrotto = True
                break
            try:
                data = leggi_tabellone(ev)
            except requests.RequestException as e:
                print(f"  {label}: tabellone non leggibile ({e})")
                errors += 1
                continue
            matches = parse_tableau(data, ev)
            nuovi = [m for m in matches if m["id"] not in esistenti]
            # dal primo turno alla finale: la Finale è sempre l'ultima riga scritta
            nuovi.sort(key=lambda m: ORDINE_TURNI.index(m["round"]) if m["round"] in ORDINE_TURNI else 5)
            completare = []
            if MODALITA == "completa":
                for m in matches:
                    if m["id"] in esistenti:
                        mancano = da_completare(esistenti[m["id"]], m)
                        if mancano:
                            completare.append((m, mancano))
            tot_nuovi += len(nuovi)
            tot_completare += len(completare)
            print(f"  {label}: incontri {len(matches)}, nuovi {len(nuovi)}"
                  + (f", da completare {len(completare)}" if MODALITA == "completa" else ""))
            if DRY_RUN:
                for m in nuovi:
                    if shown < 15:
                        print(f"     [{m['round']}] {m['p1']} ({m['noc1']}, id {m['id1']}) vs "
                              f"{m['p2']} ({m['noc2']}, id {m['id2']}) {m['score']} | {m['date']}")
                        shown += 1
                continue
            if not nuovi and not completare:
                continue

            # righe esistenti: solo ID FIE (e codice anomalo nello Score) se mancano
            for m, mancano in completare:
                if tempo_scaduto():
                    interrotto = True
                    break
                try:
                    completa_riga(mapping, esistenti[m["id"]], mancano)
                    esistenti[m["id"]].update({k: v for k, v in mancano.items() if k in ("id1", "id2", "score")})
                    completate += 1
                    if completate % 100 == 0:
                        print(f"     ... completate {completate} righe ({minuti_trascorsi():.0f} minuti)", flush=True)
                    time.sleep(PAUSA_NOTION)
                except RuntimeError as e:
                    print(f"     Errore nel completare {m['p1']} vs {m['p2']}: {e}")
                    errors += 1
            if interrotto:
                break

            # incontri nuovi
            if nuovi:
                try:
                    link = source_link(ev)
                except requests.RequestException:
                    link = FIE
            for m in nuovi:
                if tempo_scaduto():
                    interrotto = True
                    break
                m["source"] = link
                try:
                    create_row(mapping, m)
                    esistenti[m["id"]] = {"page": "", "id1": m["id1"], "id2": m["id2"],
                                          "score": m["score"], "turno": m["round"]}
                    added += 1
                    if added % 100 == 0:
                        print(f"     ... scritte {added} righe ({minuti_trascorsi():.0f} minuti)", flush=True)
                    time.sleep(PAUSA_NOTION)
                except RuntimeError as e:
                    print(f"     Errore su {m['p1']} vs {m['p2']}: {e}")
                    errors += 1
            if interrotto:
                break

    stampa_diagnosi()

    # 2. pagina web (novità 3.0): si salta solo se l'agente si è fermato per il limite di tempo,
    #    perché ci vorrebbero altri minuti; il lancio successivo la prepara.
    if interrotto:
        avviso("pagina web non aggiornata in questo lancio (limite di tempo raggiunto)")
    else:
        fase_web(seasons)

    print("\n" + "=" * 78)
    print("RIEPILOGO")
    print("=" * 78)
    if DRY_RUN:
        minuti = (tot_nuovi + tot_completare) * SECONDI_PER_RIGA / 60
        print(f"Righe nuove da scrivere: {tot_nuovi}"
              + (f" · righe esistenti da completare: {tot_completare}" if MODALITA == "completa" else "")
              + f" · tempo stimato in Notion: circa {minuti:.0f} minuti (limite per lancio: {TEMPO_MAX_MINUTI:.0f}).")
        if minuti > TEMPO_MAX_MINUTI:
            print("  Servono più lanci: lo script si ferma da solo e il lancio successivo riparte da lì.")
        if NOTION_TOKEN and not mapping:
            print("ATTENZIONE: lo schema Notion non è a posto (vedi sopra).")
        print("\nProva terminata (mostrati solo i primi 15 incontri come esempio).")
    else:
        print(f"Righe aggiunte: {added} · righe completate: {completate} · errori: {errors} · "
              f"minuti: {minuti_trascorsi():.0f}")
    if AVVISI:
        print(f"\nAvvisi ({len(AVVISI)}), non bloccano l'agente:")
        for a in AVVISI:
            print(f"  · {a}")
    if interrotto:
        print(f"\nATTENZIONE: mi sono fermato dopo {TEMPO_MAX_MINUTI:.0f} minuti per non superare il limite "
              "di GitHub. Rilancia con le stesse impostazioni: le righe già scritte vengono saltate.")
        sys.exit(1)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
