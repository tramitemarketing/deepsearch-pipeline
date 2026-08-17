#!/usr/bin/env python3
"""
run_close.py — chiude un run e ne dichiara la validita'.

SOLA LETTURA sugli artefatti del run. Non modifica niente, non decide niente sul merito:
verifica che il run abbia prodotto cio' che l'orchestratore ha promesso, e ricalcola dai
ledger i numeri che il report dichiara.

Perche' esiste. Nel run del 20260801 mancavano cinque artefatti su cinque, tre file di stato
erano a zero byte, e il REPORT dichiarava 62 ricerche dove il ledger ne aveva 39 e la somma
dei budget_used ne contava 77. Nessuno se n'era accorto, e il report si apriva con "nessuna
opportunita' ha superato i criteri" — una frase che, in un run degradato, non significa niente.

DUE FASI. Il verdetto decide come si APRE il report, ma il report e' anche uno degli artefatti
verificati. Eseguire una volta sola significa scegliere quale delle due cose viene prima, e in
v2.6/v2.7 la scelta rendeva ogni run NON VALIDO per costruzione.
  --fase pre     dopo gli artefatti tipizzati, prima del report. REPORT.md non e' richiesto.
  --fase finale  dopo il report. Autorizza — solo lei — la scrittura in state/.

I FILE DI state/ SI DIVIDONO IN DUE, ed e' la correzione centrale della v2.8.1.
Fino alla v2.8 erano un elenco unico e si pretendeva che fossero tutti non vuoti. Ma meta' di
quei file viene scritta DOPO questa verifica, dal §10, che questa verifica autorizza. Il
risultato era un blocco permanente: run 1 degradato → state/ resta vuota → run 2 fallisce sui
file di stato → degradato → non scrive → run 3 identico. La memoria non si riaccendeva piu', e
l'eccezione `primo_run` mascherava il difetto sull'unico caso che funzionava.
  LEDGER   query-ledger, source-ledger — scritti DURANTE il run. Il loro contenuto e' un fatto
           su QUESTO run e va riconciliato con le statistiche dichiarate.
  MEMORIA  seen-pains, sector-history — scritti DOPO, in §10. La loro consistenza non dice nulla
           su questo run: se ne verifica solo l'integrita' (JSONL leggibile), mai la quantita'.
L'eccezione `primo_run` e' stata rimossa del tutto. Oltre a non servire piu', si sfruttava:
il verdetto dipendeva da quante cartelle ci fossero in runs/, quindi cancellare la cartella di
un run precedente faceva passare da DEGRADATO a VALIDO senza toccare nulla del run in esame.
Un verificatore che premia la cancellazione delle prove e' peggio di nessun verificatore.

  uso:  python3 tools/run_close.py runs/<run_id> --fase pre
        python3 tools/run_close.py runs/<run_id> --fase finale
        exit 0 = VALIDO · 1 = DEGRADATO · 2 = NON VALIDO
"""
import json, os, re, sys, glob, unicodedata, hashlib
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


ARTEFATTI = ["00-run-manifest.json", "00-run-stats.json", "downgrades.jsonl",
             "violations.jsonl", "integrity-incidents.jsonl", "dossier.json"]
LEDGER = ["query-ledger.jsonl", "source-ledger.jsonl"]
MEMORIA = ["seen-pains.jsonl", "sector-history.jsonl"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# La radice dello stato mutabile si decide in UN SOLO posto, e non e' questo: la si
# importa invece di ricostruirla. Tre join letterali che risolvano ognuno per conto
# proprio sono tre occasioni di divergere, e una rilocazione parziale resterebbe
# verde — ogni lettore troverebbe il file che si aspetta, in posti diversi.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile_run import radice_stato          # noqa: E402  (dopo sys.path, per forza)

MANIFEST_STATO = "state-import-manifest.json"

APERTURA_DEGRADATA = "run degradato: l'esito vuoto non e' interpretabile"
APERTURA_VUOTA = "nessuna opportunita' ha superato i criteri"
# L'apertura si cerca solo in testa al documento: la stessa frase compare legittimamente piu'
# sotto, nella Sintesi degli scarti prescritta da §9. Cercarla su tutto il file faceva scattare
# una violazione su un report scritto esattamente come l'orchestratore lo prescrive.
TESTA = 1200


def piatto(s):
    """Confronto insensibile ad accenti e apostrofi: 'e'' e 'è' sono la stessa parola, e un
    report bocciato per un accento e' un ciclo che non si chiude ('correggi e riesegui')."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"['`´’]", "'", s)


def righe(path, run_id=None):
    """(n_totali, n_di_questo_run, n_senza_run_id). (None,None,None) = assente ·
    -1 ovunque = JSONL corrotto.

    Fino al thin slice 2 c'era un ripiego: se NESSUNA riga portava run_id la funzione
    rendeva `mio = None` e atteso() ricadeva sul TOTALE. Serviva ai ledger scritti
    prima che il campo esistesse. Con una memoria storica migrata quel ripiego e' il
    contrario di cio' che si vuole: i contatori del REPORT misurano il lavoro di
    QUESTO run, e attribuirgli righe che nessuno gli ha attribuito e' la lineage
    dedotta che la disciplina vieta. `mio` e' ora un intero, zero compreso — e lo
    zero e' una misura reale, non un'assenza.

    Il prezzo della rimozione va detto: l'errore "riga nuova senza run_id" non e'
    piu' catturato da qui. Lo catturano i controlli n.5 e n.6 di guardia_stato(), e
    per questo le due modifiche non sono separabili.
    """
    if not os.path.exists(path):
        return None, None, None
    tot, mio, senza = 0, 0, 0
    for l in open(path, encoding="utf-8"):
        if not l.strip():
            continue
        try:
            o = json.loads(l)
        except Exception:
            return -1, -1, -1
        tot += 1
        if isinstance(o, dict) and o.get("run_id"):
            if o["run_id"] == run_id:
                mio += 1
        else:
            senza += 1
    return tot, mio, senza


def guardia_stato(check):
    """I sei controlli d'integrita' sullo stato migrato (n.1-6 della guardia).

    Lo stato mutabile vive fuori dal repository e non e' versionato: nessuno lo
    ripristina, nessun diff lo mostra, e un'alterazione silenziosa del prefisso
    storico non lascerebbe traccia da nessuna parte. Il manifest e' l'unica ancora
    che abbiamo, e questi controlli sono l'unico posto in cui viene interrogata.

    SOLE LETTURE, ed e' un vincolo di questo file, non una preferenza: open in "rb",
    read, seek, hashlib, json.load. Nessuna primitiva di scrittura — T-G lo verifica
    per AST, ed e' la ragione per cui la guardia sta qui e la creazione della radice
    sta nello scrittore.

    Perche' il prefisso e non tutto il file: dopo il primo run la coda cresce, e
    congelarne l'hash renderebbe il controllo inutile al secondo giro. Il prefisso
    invece non cambia mai piu' — e' precisamente cio' che lo rende un'ancora.
    """
    ATTRIBUIBILI = {"query-ledger", "source-ledger", "seen-pains", "sector-history"}
    # shadow-portfolio resta fuori dai controlli n.5 e n.6: non ha schema, non e'
    # documentato in state/README.md, non e' in MEMORIA e nessuno strumento lo legge.
    # E' l'unico dei cinque senza alcun contratto sul contenuto — lacuna nota, non
    # proprieta' del file, e non va scambiata per una scelta di disegno.

    radice = radice_stato()
    mp = os.path.join(radice, MANIFEST_STATO)

    # --- n.1  manifest presente, leggibile, completo
    if not os.path.exists(mp):
        check("manifest dello stato presente", False,
              f"{MANIFEST_STATO} assente in {radice}: il prefisso storico non e' verificabile")
        return
    try:
        with open(mp, encoding="utf-8") as g:
            man = json.load(g)
    except Exception as e:
        check("manifest dello stato leggibile", False, f"JSON non valido: {type(e).__name__}")
        return
    if not isinstance(man, dict) or "versione_formato" not in man:
        check("manifest con versione_formato", False, "campo assente")
        return
    voci = {v.get("ledger"): v for v in (man.get("voci") or []) if isinstance(v, dict)}
    manca = [n for n in ("query-ledger", "source-ledger", "seen-pains",
                         "sector-history", "shadow-portfolio") if n not in voci]
    if manca:
        check("manifest con una voce per file", False, "voci mancanti: " + ", ".join(manca))
        return
    incomplete = [n for n, v in voci.items()
                  if not str(v.get("sorgente") or "").strip()
                  or not str(v.get("data_importazione") or "").strip()]
    check("manifest completo", not incomplete,
          ("voci senza sorgente o data: " + ", ".join(sorted(incomplete))) if incomplete
          else f"{len(voci)} voci, versione {man.get('versione_formato')}")
    if incomplete:
        return

    for nome, v in sorted(voci.items()):
        f_ = nome + ".jsonl"
        p = os.path.join(radice, f_)
        n = v.get("byte_prefisso")
        if not isinstance(n, int) or n < 0:
            check(f"{f_}: byte_prefisso valido", False, f"valore {n!r}")
            continue
        if not os.path.exists(p):
            # Un file di stato assente non e' materia di questa guardia: lo dicono gia'
            # i controlli sui ledger e sulla memoria, con le loro parole.
            continue
        with open(p, "rb") as g:
            grezzo = g.read()

        # --- n.3  assenza di troncamento
        if len(grezzo) < n:
            check(f"{f_}: non troncato", False,
                  f"{len(grezzo)} byte, meno dei {n} del prefisso importato")
            continue
        check(f"{f_}: non troncato", True, f"{len(grezzo)} byte, prefisso {n}")

        pre = grezzo[:n]

        # --- n.2  prefisso byte-identico
        h = hashlib.sha256(pre).hexdigest()
        check(f"{f_}: prefisso storico intatto", h == v.get("sha256_prefisso"),
              f"atteso {str(v.get('sha256_prefisso'))[:16]}..., trovato {h[:16]}...")

        # --- n.4  coerenza interna del manifest col file vivo
        #     E' il controllo che impedisce a un manifest coerente con se' stesso di
        #     mentire sul file: un prefisso di 40 righe dichiarato di 39 passerebbe
        #     l'hash solo se l'hash fosse calcolato sul manifest, e infatti non lo e'.
        confine = (n == 0) or (grezzo[n - 1:n] == b"\n")
        r_pre, s_pre = _conta_righe(pre)
        problemi = []
        if not confine:
            problemi.append("byte_prefisso non cade su confine di riga")
        if r_pre != v.get("righe_importate"):
            problemi.append(f"righe {r_pre} vs {v.get('righe_importate')} dichiarate")
        if s_pre != v.get("righe_legacy_senza_run_id"):
            problemi.append(f"senza run_id {s_pre} vs {v.get('righe_legacy_senza_run_id')}")
        check(f"{f_}: manifest coerente col file", not problemi,
              "; ".join(problemi) if problemi else f"{r_pre} righe, {s_pre} senza run_id")

        if nome not in ATTRIBUIBILI:
            continue

        coda = grezzo[n:]
        r_coda, s_coda = _conta_righe(coda)

        # --- n.5  tetto alle righe legacy, sull'INTERO file
        tetto = v.get("righe_legacy_senza_run_id")
        tot_senza = s_pre + s_coda
        check(f"{f_}: nessuna riga senza run_id oltre le legacy",
              isinstance(tetto, int) and tot_senza <= tetto,
              f"{tot_senza} righe senza run_id, dichiarate {tetto}")

        # --- n.6  run_id presente e non vuoto su ogni riga dopo il prefisso
        #     Presenza e non vuotezza, NON conformita' a un pattern: le 39 righe
        #     migrate portano run_id "20260801", otto cifre nude, che non soddisfa
        #     ^[0-9]{8}-[0-9]{4}$ di seen-pain.schema.json. Imporre il pattern qui
        #     farebbe fallire la migrazione all'istante, e la conformita' fra i
        #     cinque file e' questione aperta, non materia di questa guardia.
        check(f"{f_}: ogni riga nuova porta run_id", s_coda == 0,
              f"{s_coda} righe su {r_coda} scritte dopo il prefisso non lo portano"
              if s_coda else f"{r_coda} righe nuove, tutte attribuite")


def _conta_righe(b):
    """(righe non vuote, righe senza run_id) su un blocco di byte JSONL.
    Una riga non parsabile conta come priva di run_id: e' l'esito prudente, e il
    caso vero e' comunque intercettato dall'esito 'JSONL corrotto' di righe()."""
    r = s = 0
    for l in b.decode("utf-8", "replace").splitlines():
        if not l.strip():
            continue
        r += 1
        try:
            o = json.loads(l)
        except Exception:
            s += 1
            continue
        if not (isinstance(o, dict) and o.get("run_id")):
            s += 1
    return r, s


def claim_verificati(nodo):
    """Ogni claim VERIFIED, ovunque sia annidato. Si riconosce la FORMA (`state: VERIFIED` +
    lista `source_ids`), non il percorso: cinque agenti li appendono sotto chiavi diverse, e un
    controllo che conosce solo i percorsi di oggi smette di guardare in silenzio il giorno in cui
    ne compare uno nuovo."""
    if isinstance(nodo, dict):
        if nodo.get("state") == "VERIFIED" and isinstance(nodo.get("source_ids"), list):
            yield nodo
        for v in nodo.values():
            yield from claim_verificati(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from claim_verificati(v)


def chiudi(run_dir, fase="finale"):
    esiti, gravi, avvisi = [], 0, 0
    run_id = os.path.basename(os.path.normpath(run_dir))

    def check(nome, ok, dett="", grave=True):
        nonlocal gravi, avvisi
        esiti.append((nome, ok, dett))
        if not ok:
            if grave: gravi += 1
            else: avvisi += 1

    for a in ARTEFATTI:
        check(f"artefatto {a}", os.path.exists(os.path.join(run_dir, a)))

    rep = os.path.join(run_dir, "REPORT.md")
    if fase == "finale":
        check("artefatto REPORT.md", os.path.exists(rep))

    # --- ledger: scritti DURANTE il run, quindi sono fatti su questo run
    conteggi = {}
    for f_ in LEDGER:
        tot, mio, senza = righe(os.path.join(radice_stato(), f_), run_id)
        conteggi[f_] = (tot, mio)
        if tot == -1:
            check(f"ledger {f_}", False, "JSONL corrotto")
        elif tot is None:
            check(f"ledger {f_}", False, "assente: il run non ha registrato nulla")
        else:
            # I due numeri restano distinti in ogni riga d'esito, ed e' la coppia che rende
            # leggibile uno zero legittimo accanto a un ledger di novanta righe: "quante ne ha
            # aggiunte questo run" e "quante ne aveva il file" sono due domande diverse.
            d = f"{mio} righe di questo run su {tot} totali"
            if senza:
                d += (f" — {senza} precedono l'attribuzione per run e restano fuori"
                      f" dai contatori")
            check(f"ledger {f_}", True, d)

    # --- memoria: scritta DOPO da §10. Solo integrita', mai quantita'.
    for f_ in MEMORIA:
        tot, _, _ = righe(os.path.join(radice_stato(), f_), run_id)
        check(f"memoria {f_} integra", tot != -1,
              "JSONL corrotto" if tot == -1 else
              ("assente (la scrive §10, dopo questa verifica)" if tot is None else f"{tot} righe"))

    # --- guardia sullo stato migrato: il prefisso storico e l'attribuzione delle righe nuove
    guardia_stato(check)

    # --- riconciliazione: i numeri dichiarati contro i ledger, sempre
    def atteso(f_):
        # Il numero che il REPORT deve dichiarare e' il lavoro di QUESTO run, sempre. Non
        # esiste piu' il ripiego sul totale: su un ledger con memoria storica attribuirebbe
        # al run corrente il lavoro di quelli precedenti.
        tot, mio = conteggi.get(f_, (None, None))
        if tot in (None, -1):
            return None
        return mio

    q, f = atteso("query-ledger.jsonl"), atteso("source-ledger.jsonl")
    stats = os.path.join(run_dir, "00-run-stats.json")
    d = None
    if os.path.exists(stats):
        try:
            d = json.load(open(stats, encoding="utf-8"))
        except Exception as e:
            check("00-run-stats.json leggibile", False, f"JSON non valido: {e}")
    if d is not None and q is not None and f is not None:
        check("statistiche ricerche == ledger", d.get("searches") == q,
              f"stats {d.get('searches')} vs ledger {q}")
        check("statistiche fonti == ledger", d.get("sources") == f,
              f"stats {d.get('sources')} vs ledger {f}")
    elif d is None:
        check("riconciliazione impossibile", False,
              "00-run-stats.json assente o illeggibile: nessun numero da confrontare")
    else:
        check("riconciliazione impossibile", False, "ledger assenti o corrotti")

    # L'intestazione del report si riconcilia SEMPRE, anche quando le statistiche tipizzate ci
    # sono. §11 lo promette, e fino alla v2.8 il ramo elif la saltava ogni volta che
    # 00-run-stats.json esisteva — cioe' sempre, dato che §11 obbliga a scriverlo. Il numero
    # tipizzato e quello che un essere umano legge possono divergere: e' il difetto originario.
    if os.path.exists(rep) and q is not None and f is not None:
        t = open(rep, encoding="utf-8").read()
        m = re.search(r"Ricerche(?:\s+totali|\s+nel ledger)?:\s*(\d+).*?Fonti nel ledger:\s*(\d+)",
                      t, re.S)
        if m:
            check("intestazione ricerche == ledger", int(m.group(1)) == q,
                  f"report {m.group(1)} vs ledger {q}")
            check("intestazione fonti == ledger", int(m.group(2)) == f,
                  f"report {m.group(2)} vs ledger {f}")
        elif fase == "finale":
            check("intestazione del report leggibile", False,
                  "nessuna delle varianti note combacia: impossibile riconciliare la prosa")

    # ================= ANCORAGGIO AL LAVORO =================
    # Il difetto che questa sezione chiude, trovato dal run 20260802-1759.
    # run_close confrontava 00-run-stats.json con i ledger — due numeri scritti dallo STESSO
    # attore alla fine dello stesso run — e mai nessuno dei due con il lavoro effettivamente
    # prodotto. Quel run ha chiuso VALIDO con zero post-condizioni fallite mentre il 30% dei
    # source_id citati non risolveva nel ledger, 147 ricerche su 332 non erano mai state
    # consolidate, e tre contatori su quattro raddoppiavano il funnel (12 dichiarati, 6 su disco).
    # E' la stessa classe del difetto originario — "l'intestazione deve venire dai ledger, non
    # dalla memoria" — spostata di un livello: ora i ledger devono venire dal LAVORO.
    artefatti_json = []
    for p in sorted(glob.glob(os.path.join(run_dir, "*", "*.json"))):
        try:
            artefatti_json.append((p, json.load(open(p, encoding="utf-8"))))
        except Exception:
            check(f"artefatto illeggibile {os.path.basename(p)}", False, "JSON non valido")

    if artefatti_json:
        nel_ledger = set()
        lp = os.path.join(radice_stato(), "source-ledger.jsonl")
        if os.path.exists(lp):
            for l in open(lp, encoding="utf-8"):
                try:
                    o = json.loads(l)
                    if isinstance(o, dict) and o.get("id"):
                        nel_ledger.add(o["id"])
                except Exception:
                    pass

        # Le fonti che qualcuno ha TENTATO e che hanno rifiutato. IC-4 obbliga a dichiararle;
        # senza questo insieme IC-1 le contava come orfane, e un run risultava DEGRADATO per
        # aver dichiarato cio' che non e' riuscito a leggere — cioe' il sistema puniva l'unico
        # comportamento onesto disponibile. Due invarianti che si contraddicono non sono due
        # regole severe: sono una regola sola, applicata a caso.
        dichiarate_irr = set()
        for _, o in artefatti_json:
            if isinstance(o, dict):
                for u in (o.get("unreachable_sources") or []):
                    if isinstance(u, dict) and u.get("source_id"):
                        dichiarate_irr.add(u["source_id"])
        con_provenienza = nel_ledger | dichiarate_irr

        # IC-1 reso meccanico. E' l'unico invariante del sistema formulato come verificabile a
        # macchina, ed era l'unico mai verificato: una differenza fra insiemi, due righe.
        citati, orfani_per_file = set(), {}
        for p, o in artefatti_json:
            trovati = set(re.findall(r"\bS-[0-9]{8}(?:-[0-9]{4})?-[0-9]{4}\b",
                                     json.dumps(o, ensure_ascii=False)))
            citati |= trovati
            orf = trovati - con_provenienza
            if orf:
                orfani_per_file[os.path.relpath(p, run_dir)] = len(orf)
        orfani = citati - con_provenienza
        quota = (len(orfani) / len(citati)) if citati else 0.0
        peggiori = sorted(orfani_per_file.items(), key=lambda x: -x[1])[:3]
        check("IC-1: ogni source_id citato risolve nel ledger", not orfani,
              f"{len(orfani)}/{len(citati)} orfani ({quota:.0%})" +
              (f" — {len(dichiarate_irr)} dichiarate irraggiungibili (IC-4), non orfane"
               if dichiarate_irr else "") +
              (" — peggiori: " + ", ".join(f"{a} ({n})" for a, n in peggiori) if peggiori else ""))

        # IL CONTRAPPESO. Riconoscere le irraggiungibili come provenienza le fa risolvere in
        # IC-1: da sole non devono poter sostenere un VERIFIED, o si e' comprato un falso
        # negativo col falso positivo appena tolto. IC-3 pretende uno snippet per la coppia
        # (claim, fonte), e su una pagina che nessuno ha aperto non c'e' niente da citare.
        solo_irr = []
        if dichiarate_irr:
            for p, o in artefatti_json:
                for c in claim_verificati(o):
                    ids = [x for x in c.get("source_ids") if isinstance(x, str)]
                    if ids and all(x in dichiarate_irr for x in ids):
                        solo_irr.append(f"{os.path.relpath(p, run_dir)}:{c.get('id', '?')}")
        check("nessun VERIFIED poggia solo su fonti mai lette", not solo_irr,
              f"{len(solo_irr)} claim da declassare a HYPOTHESIS: {', '.join(solo_irr[:3])}"
              if solo_irr else "")

        # ------------------------------------------------------------------ TACHIMETRO
        # Non misura la qualita' del run: misura se il run e' MISURABILE.
        #
        # Serve perche' una decisione di acquisto e' stata quasi presa al buio. Alla domanda
        # "conviene aggiungere un fetcher con rendering, visto che WebFetch perde contenuto sui
        # siti JS-heavy?" la risposta corretta era: non lo sai, perche' non registri quante
        # pagine apri ne' quali fonti hai davvero letto. Il §3 impone snippet_source (SERP =
        # visto nei risultati, FETCHED = pagina aperta) e l'envelope pretende fetches: sono i
        # due contatori che rendono quella domanda rispondibile. Con i contatori spenti ogni
        # discussione sul retrieval e' aneddotica, e si compra uno strumento per fiducia.
        #
        # AVVISO e non post-condizione, deliberatamente. Il primo run dopo l'accensione avra' i
        # contatori a zero per ragioni di calendario, non di sciatteria, e far chiudere
        # DEGRADATO un run buono per un motivo formale insegna solo a ignorare il verdetto.
        con_fetches = sum(1 for _, o in artefatti_json
                          if isinstance((o.get("budget_used") or {}).get("fetches"), int))
        prov = {"SERP": 0, "FETCHED": 0}
        claim_tot = claim_con_ev = 0
        for _, o in artefatti_json:
            for c in claim_verificati(o):
                claim_tot += 1
                ev = c.get("evidence")
                if isinstance(ev, list) and ev:
                    claim_con_ev += 1
                    for e in ev:
                        if isinstance(e, dict) and e.get("snippet_source") in prov:
                            prov[e["snippet_source"]] += 1
        n_art = len(artefatti_json)
        letto = prov["SERP"] + prov["FETCHED"]
        check("contatore fetches acceso", con_fetches == n_art,
              f"{con_fetches}/{n_art} artefatti dichiarano budget_used.fetches"
              + ("" if con_fetches else " — il consumo di lettura di questo run e' ignoto"),
              grave=False)
        # claim_tot == 0 non e' un difetto: il §10 dichiara RIUSCITO un run che si svuota
        # onestamente. Un avviso li' punirebbe il comportamento che la specifica premia.
        if claim_tot:
            check("provenienza degli snippet dichiarata (IC-3)", claim_con_ev == claim_tot,
                  f"{claim_con_ev}/{claim_tot} claim VERIFIED con evidence[] popolato"
                  + (f" · {prov['FETCHED']} FETCHED / {prov['SERP']} SERP" if letto else
                     " — nessuno snippet dichiara se la pagina sia stata aperta"),
                  grave=False)
        if letto:
            quota = prov["FETCHED"] / letto
            # Tre bande e non due. Una soglia secca produce letture opposte a un punto di
            # distanza — a 1 FETCHED su 5 diceva "quota alta" e a 1 su 6 "il collo di bottiglia
            # non e' la lettura. Un indicatore che si ribalta su un caso in piu' non e' una
            # misura, e la banda di mezzo e' l'unica risposta onesta quando il dato non decide.
            if quota < 0.20:
                lettura = ("quasi tutto viene dalla SERP: un fetcher migliore non sposta niente, "
                           "il collo di bottiglia non e' la lettura")
            elif quota < 0.60:
                lettura = ("banda intermedia: il dato non decide da solo. Guarda il tasso di "
                           "rifiuto sulle fonti tentate — e' quello il numero")
            else:
                lettura = ("si apre molto: qui un fetch che fallisce costa davvero, e il tasso "
                           "di rifiuto diventa il numero che decide sul fetcher")
            esiti.append((f"   ↳ pagine effettivamente aperte: {quota:.0%} "
                          f"({prov['FETCHED']} su {letto})", True, lettura))
        elif claim_tot:
            esiti.append(("   ↳ questo run NON puo' essere usato per decidere sul retrieval",
                          False, "i contatori erano spenti: qualunque conclusione su "
                                 "fetcher, crawler o qualita' delle fonti sarebbe un aneddoto"))
            avvisi += 1

        # Le ricerche dichiarate dagli agenti contro il ledger.
        #
        # Fino al thin slice 2 qui stava `dich_s <= q`: un confronto fra due numeri, quante
        # ricerche gli agenti dicono di aver fatto contro quante ne risultano consolidate per
        # QUESTO run. Con una memoria storica migrata quella relazione non e' piu' ben formata.
        # reconcile_run carica l'INTERO query-ledger (:302) e appende solo cio' che non c'e'
        # gia' (:358): una ricerca del run corrente che ripeta una query gia' presente nel
        # prefisso storico e' legittimamente non consolidata e non entra in q. Il controllo
        # avrebbe prodotto un grave per la sola ragione di avere memoria.
        #
        # Cio' che voleva dire e' la COPERTURA: nessuna ricerca deve restare non registrata.
        # Si verifica per appartenenza a un insieme, non per confronto fra numeri — e' indiffe-
        # rente se la query stia fra le righe di questo run o fra quelle del prefisso storico,
        # purche' qualcuno l'abbia registrata. Una query dichiarata e assente da entrambi e'
        # una ricerca di cui non resta traccia, ed e' l'unico caso che merita un grave.
        dich_f = sum((o.get("budget_used") or {}).get("fetches") or 0 for _, o in artefatti_json)
        dichiarate = set()
        for _, o in artefatti_json:
            for qq in (o.get("queries_run") or []):
                if isinstance(qq, dict) and qq.get("normalized_query"):
                    dichiarate.add(qq["normalized_query"])
        if dichiarate:
            nel_ql = set()
            qp = os.path.join(radice_stato(), "query-ledger.jsonl")
            if os.path.exists(qp):
                for l in open(qp, encoding="utf-8"):
                    try:
                        oq = json.loads(l)
                        if isinstance(oq, dict) and oq.get("normalized_query"):
                            nel_ql.add(oq["normalized_query"])
                    except Exception:
                        pass
            scoperte = sorted(dichiarate - nel_ql)
            dett = (f"{len(scoperte)} mai registrate: "
                    + "; ".join(x[:40] for x in scoperte[:3])) if scoperte else \
                   f"{len(dichiarate)} dichiarate, tutte nel ledger"
            check("ogni ricerca dichiarata e' nel query-ledger", not scoperte, dett)
        if d is not None and dich_f:
            check("fetches nelle statistiche == fetches dichiarate", d.get("fetches") == dich_f,
                  f"stats {d.get('fetches')} vs agenti {dich_f}")

        # I contatori del funnel contro i file su disco. Una statistica che raddoppia il funnel
        # non e' un errore di conteggio: e' un report che descrive un run che non e' avvenuto.
        cartelle = {"pains_to_evidence": "02-evidence", "pains_to_competitor": "03-competitor",
                    "ideas_architected": "07-venture"}
        for campo, cart in cartelle.items():
            if d is None or d.get(campo) is None:
                continue
            pain_su_disco = {re.search(r"P-[0-9]{3}", os.path.basename(x)).group(0)
                             for x in glob.glob(os.path.join(run_dir, cart, "*.json"))
                             if re.search(r"P-[0-9]{3}", os.path.basename(x))}
            if not pain_su_disco:
                continue
            check(f"stats.{campo} == pain in {cart}/", d[campo] == len(pain_su_disco),
                  f"stats {d[campo]} vs disco {len(pain_su_disco)}")

    manca = []
    for p in glob.glob(os.path.join(run_dir, "*", "*.json")):
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(o, dict) and "fetches" not in (o.get("budget_used") or {"fetches": 1}):
            manca.append(os.path.basename(p))
    check("budget_used in due unita' (searches + fetches)", not manca,
          f"{len(manca)} output senza 'fetches'", grave=False)

    # ---- QUADRO DEI NO -------------------------------------------------------
    # I due campi che dovrebbero rendere interpretabile ogni zero del sistema —
    # kill_basis e kill_attribution — erano prescritti dall'orchestratore, citati da
    # config.run.condizione_di_riprogettazione ("si legge la distribuzione di
    # kill_attribution sui 5 run") e non esistevano in nessuno schema ne' venivano letti
    # da nessuno strumento. Finivano in file inventati caso per caso. Qui vengono letti.
    #
    # E' un AVVISO, non una post-condizione, per la stessa ragione del tachimetro: il
    # primo run dopo l'accensione avra' i contatori a zero per motivi che non sono
    # sciatteria, e far chiudere DEGRADATO un run buono per un motivo formale insegna
    # soltanto a ignorare il verdetto.
    decisioni, senza_analisi = [], []
    for p in sorted(glob.glob(os.path.join(run_dir, "*", "GATE*-decisioni.json"))):
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for v in (o.get("verdetti") or []):
            if not isinstance(v, dict):
                continue
            v = dict(v, _gate=o.get("gate"))
            # Ripiego sui run storici: fino alla v2.14.1 kill_basis e kill_attribution
            # non avevano uno schema, e l'orchestratore li scriveva PIATTI accanto al
            # verdetto. Leggerli anche li' non e' indulgenza — sono gli unici tre run
            # che questo sistema abbia, e rifiutarli renderebbe la misura disponibile
            # solo da domani, cioe' esattamente quando non serve.
            ka = v.get("kill_analysis")
            if not isinstance(ka, dict):
                ka = {k: v[k] for k in ("kill_basis", "kill_attribution", "ramo_attivato")
                      if v.get(k) is not None}
                if ka:
                    ka["_forma"] = "piatta (pre-v2.15)"
                    v["kill_analysis"] = ka
            decisioni.append(v)
            if v.get("verdetto") in ("PARK", "KILL") and not v.get("kill_analysis"):
                senza_analisi.append(f"gate {o.get('gate')} {v.get('pain_id')}")

    if decisioni:
        check("ogni PARK/KILL porta kill_analysis", not senza_analisi,
              f"{len(senza_analisi)} senza: {', '.join(senza_analisi[:5])}", grave=False)

    verdetto = "VALIDO" if gravi == 0 else ("DEGRADATO" if gravi <= 3 else "NON VALIDO")

    incoerenza = None
    if fase == "finale" and os.path.exists(rep):
        testa = piatto(open(rep, encoding="utf-8").read()[:TESTA])
        vuoto = piatto(APERTURA_VUOTA) in testa
        degradata = piatto(APERTURA_DEGRADATA) in testa
        if verdetto != "VALIDO" and not degradata:
            incoerenza = ("il run e' degradato e il report non si apre dichiarandolo"
                          + (" — dichiara invece l'esito vuoto" if vuoto else ""))
        elif verdetto == "VALIDO" and degradata:
            incoerenza = "il report si dichiara degradato ma il run e' valido"

    print(f"\n  run: {run_dir}   fase: {fase}")
    print(f"  {'-'*68}")
    for nome, ok, dett in esiti:
        print(f"  {'ok  ' if ok else 'FAIL'}  {nome:<46} {dett}")
    print(f"  {'-'*68}")
    print(f"  VERDETTO DEL RUN: {verdetto}   ({gravi} post-condizioni fallite, {avvisi} avvisi)\n")

    if decisioni:
        neg = [v for v in decisioni if v.get("verdetto") in ("PARK", "KILL")]
        print(f"  QUADRO DEI NO — {len(neg)} verdetti negativi su {len(decisioni)} decisioni")
        print(f"  {'-'*68}")
        for campo in ("kill_basis", "kill_attribution"):
            conte = {}
            for v in neg:
                k = (v.get("kill_analysis") or {}).get(campo) or "NON_DICHIARATO"
                conte[k] = conte.get(k, 0) + 1
            riga = " · ".join(f"{k} {n}" for k, n in sorted(conte.items(), key=lambda x: -x[1]))
            print(f"  {campo:<18} {riga or 'nessun dato'}")
        stru = sum(1 for v in neg
                   if (v.get("kill_analysis") or {}).get("kill_attribution") == "STRUMENTO")
        if neg:
            print(f"\n  {stru}/{len(neg)} dei no sono attribuiti allo STRUMENTO, non al mercato."
                  + ("  <-- il funnel sta misurando se stesso" if stru * 2 > len(neg) else ""))

        # Portafoglio per profondita' di sopravvivenza. E' l'unica grandezza confrontabile
        # fra run senza rinormalizzazione: non dipende da soglie, pesi o dimensioni.
        prof = sorted(decisioni,
                      key=lambda v: (-(v.get("profondita_raggiunta") or 0),
                                     -(v.get("claim_verified_accumulati") or 0)))
        print(f"\n  PORTAFOGLIO PER PROFONDITA' (le prime {min(5, len(prof))})")
        for v in prof[:5]:
            az = (v.get("azione_sbloccante") or {}).get("descrizione") or "-"
            print(f"    gate {v.get('profondita_raggiunta', '?')}  {v.get('pain_id'):<8}"
                  f" {str(v.get('verdetto')):<5} {str(v.get('reason_code') or '-'):<8} {az[:44]}")
        print()

    if verdetto != "VALIDO":
        print(f"  Il report deve aprirsi con: \"Run degradato: l'esito vuoto non e' interpretabile.\"")
        print("  seguito dall'elenco delle post-condizioni fallite qui sopra.")
    if incoerenza:
        print(f"  >> VIOLAZIONE: {incoerenza}.")
    print("  Scrivere in state/ SOLO se VALIDO e senza violazioni.\n" if fase == "finale"
          else "  Fase pre: scegli con questo verdetto l'apertura del report, poi --fase finale.\n")

    if incoerenza:
        return 2
    return 0 if verdetto == "VALIDO" else (1 if verdetto == "DEGRADATO" else 2)


if __name__ == "__main__":
    a = list(sys.argv[1:])
    fase = "finale"
    if "--fase" in a:
        i = a.index("--fase")
        fase = a[i + 1] if i + 1 < len(a) else "finale"
        del a[i:i + 2]
    if fase not in ("pre", "finale"):
        print("--fase accetta solo: pre | finale"); sys.exit(2)
    a = [x for x in a if x != "--latest"]
    if not a:
        r = sorted(glob.glob(os.path.join(ROOT, "runs", "2*")))
        if not r:
            print("nessun run trovato"); sys.exit(2)
        sys.exit(chiudi(r[-1], fase))
    sys.exit(chiudi(a[0] if os.path.isabs(a[0]) else os.path.join(ROOT, a[0]), fase))
