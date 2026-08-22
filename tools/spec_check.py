#!/usr/bin/env python3
"""
spec_check.py — verificatore di specifica. Confronta config, schemi, prompt e orchestratore
fra loro e segnala dove si contraddicono.

PERCHE' ESISTE (leggere prima di modificarlo).

Questa pipeline tiene i propri invarianti in un file di configurazione, undici schemi JSON, dieci
prompt di agente, un orchestratore e sette documenti. Nessuno di questi artefatti sapeva
dell'esistenza degli altri. Tre versioni consecutive — v2.5, v2.6, v2.7 — hanno introdotto difetti
della stessa classe: una soglia corretta in config e non nel prompt, un pattern di ID corretto in
uno schema e non nell'altro, un enum esteso nello schema e non nell'agente che deve emetterlo. Ogni
volta la correzione e' stata attenta, e ogni volta ne ha prodotta un'altra.

Il tasso non scende scrivendo prosa migliore. Scende quando qualcosa confronta i file al posto di
chi li scrive. Questo file e' quel qualcosa.

Cosa NON fa, ed e' deliberato: non ha opinioni sul merito. Non sa se 0.4 sia la soglia giusta per
unclassified_ratio, e non deve saperlo. Verifica solo che 0.4 sia scritto nello stesso modo ovunque
compaia. La differenza fra un verificatore di coerenza e un motore di regole e' esattamente questa,
ed e' il motivo per cui questo strumento puo' essere scritto ORA, mentre le soglie sono ancora
ipotesi non toccate da alcun dato: non congela nessun numero, congela solo l'accordo fra i file.

  uso:  python3 tools/spec_check.py            → tutti i controlli
        python3 tools/spec_check.py --solo ID  → un controllo (es. --solo SC-03)
        exit 0 = nessun errore · 1 = errori
"""
import json, math, os, re, sys, glob
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "deepsearch.config.json")
SCHEMI = os.path.join(ROOT, "schemas")
AGENTI = os.path.join(ROOT, ".claude", "agents")
ORCH = os.path.join(ROOT, ".claude", "commands", "deepsearch.md")
CLAUDEMD = os.path.join(ROOT, "CLAUDE.md")
DOCS = os.path.join(ROOT, "docs")

# Dimensioni minime del contratto, DICHIARATE QUI e non nel config.
# Il posto conta. La v2.8.1 metteva il contratto dentro l'artefatto che verifica, e tre controlli
# si spegnevano svuotando una lista: nessun errore, un avviso in mezzo ad altri settantadue. Non e'
# uno scenario di malafede — e' quello che succede il giorno in cui una voce da' un falso positivo
# e la si toglie "temporaneamente". Le aspettative del verificatore SUL PROPRIO contratto
# appartengono al verificatore: metterle nel file verificato ricrea la circolarita' che questo
# strumento esiste per rompere. La guardia era gia' scritta in SC-13 ("svuotare la cartella non
# puo' essere un modo per far passare i controlli") e applicata a un controllo su quattro.
CONTRATTI_MINIMI = {
    "costanti_rispecchiate": 8,
    "enum_condivisi": 4,
    "liste_vs_enum": 4,
    "clausole_orchestratore": 3,
    "id_namespacati_per_run": 1,
    "schema_per_agente": 6,
    "wave_per_agente": 10,
    "agenti/solo_giudizio": 3,
    "agenti/con_ricerca": 7,
}

errori, avvisi, RUMORE = [], [], []


def err(cid, msg, dove=""):
    errori.append((cid, msg, dove))


def avv(cid, msg, dove=""):
    avvisi.append((cid, msg, dove))


def leggi(p):
    return open(p, encoding="utf-8").read()


def carica_config():
    return json.load(open(CONFIG, encoding="utf-8"))


def tutti_gli_schemi():
    out = {}
    for p in sorted(glob.glob(os.path.join(SCHEMI, "*.json"))):
        try:
            out[os.path.basename(p)] = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            err("SC-00", f"schema JSON non valido: {e}", os.path.basename(p))
    return out


# I documenti storici — collaudi, ripensamenti, changelog — CITANO valori vecchi apposta: e' il
# loro mestiere. Lintarli contro il config di oggi produce solo rumore, e un verificatore rumoroso
# viene ignorato, che e' il modo in cui un verificatore muore. Restano fuori dai controlli che
# confrontano numeri e riferimenti; entrano solo dove il passato non puo' mentire.
STORICI = ("COLLAUDO-", "RIPENSAMENTO-", "MIGLIORIE.md", "CHANGELOG", "RED-TEAM-")

# Via d'uscita esplicita, per riga. Un esempio didattico ("unclassified_ratio = 0.66 -> PARK") e un
# valore osservato in una narrazione sono legittimi e indistinguibili da una soglia per qualunque
# regex. Senza una via d'uscita l'unico modo di scriverli e' disattivare il controllo — che e' il
# modo in cui i verificatori muoiono davvero, molto piu' spesso dei falsi negativi.
IGNORA = "<!-- spec-check: ignora -->"


def file_di_prosa(solo_normativi=False):
    """Tutti i file che possono contenere una regola scritta a mano."""
    f = [ORCH, CLAUDEMD] + sorted(glob.glob(os.path.join(AGENTI, "*.md")))
    f += sorted(glob.glob(os.path.join(DOCS, "*.md")))
    # state/README.md definisce lo schema dei file di memoria ed era fuori perimetro: ci e'
    # sopravvissuto per due versioni il vocabolario inglese (PUBLISHED) che il config dichiarava
    # eliminato. Un file normativo fuori dal perimetro e' un file che nessuno confronta.
    f += [os.path.join(ROOT, "state", "README.md")]

    out = [(os.path.relpath(p, ROOT), leggi(p)) for p in f if os.path.exists(p)]
    if solo_normativi:
        out = [(n, t) for n, t in out if not e_storico(n)]
    return out


def e_storico(nome):
    n = nome.upper()
    return any(s.upper() in n for s in STORICI)


def normativi(prosa):
    return [(n, t) for n, t in prosa if not e_storico(n)]


def pagine_pubblicate():
    """Le pagine HTML servite da Pages: descrivono il sistema di OGGI a chi non lo conosce.

    Non entrano nel perimetro generale — SC-06 leggerebbe l'etichetta di ondata "W7" come un
    criterio competitor mai dichiarato, e un controllo che spara a vuoto viene disattivato molto
    prima di uno che tace. Le legge solo chi ha una ragione precisa per farlo."""
    return [(os.path.relpath(x, ROOT), leggi(x))
            for x in sorted(glob.glob(os.path.join(ROOT, "*.html")))]


def cammina(o, prefisso=""):
    """Ogni percorso puntato del config, con il suo valore."""
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{prefisso}.{k}" if prefisso else k
            yield p, v
            yield from cammina(v, p)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield f"{prefisso}[{i}]", v
            yield from cammina(v, f"{prefisso}[{i}]")


# ---------------------------------------------------------------- SC-00
def sc00_integrita_del_contratto(cfg):
    """Il contratto che guida SC-03, SC-04, SC-07, SC-14 e SC-15 e' esso stesso un invariante."""
    vs = cfg.get("verifica_specifica")
    if not isinstance(vs, dict):
        err("SC-00", "config.verifica_specifica assente: cinque controlli sono spenti", "config")
        return
    for chiave, minimo in CONTRATTI_MINIMI.items():
        nodo, parti = vs, chiave.split("/")
        for p in parti:
            nodo = nodo.get(p) if isinstance(nodo, dict) else None
        n = len(nodo) if isinstance(nodo, (list, dict)) else 0
        if n < minimo:
            err("SC-00",
                f"verifica_specifica.{chiave.replace('/', '.')} ha {n} voci, il minimo e' {minimo}: "
                "svuotare un contratto non puo' essere un modo per spegnere un controllo",
                "config.verifica_specifica")


# ---------------------------------------------------------------- SC-01
def sc01_riferimenti_config(cfg, prosa):
    """Ogni `config.qualcosa` citato nei prompt deve esistere davvero nel config."""
    percorsi = {p for p, _ in cammina(cfg)}
    # ammette anche i prefissi con profilo variabile: funnel.DEEP.x -> funnel.*.x
    generici = {re.sub(r"\.(LIGHT|BALANCED|DEEP|STRICT|CALIBRATED)\.", ".*.", p) for p in percorsi}
    for nome, testo in prosa:
        for m in re.finditer(r"`config\.([A-Za-z0-9_.\[\]*]+?)`", testo):
            rif = m.group(1).rstrip(".")
            # i prompt scrivono segnaposto: funnel[profile].shards, budget._calcolo_teorico_*
            rif = re.sub(r"\[[A-Za-z_]+\]", "", rif).replace("..", ".")
            rif = re.sub(r"_?\*$", "", rif).rstrip("._")
            if rif in percorsi or rif in generici:
                continue
            variante = re.sub(r"\.(LIGHT|BALANCED|DEEP|STRICT|CALIBRATED|\*)\.", ".*.", rif)
            if variante in generici:
                continue
            # un riferimento a un ramo intero e' valido se qualche percorso lo estende
            if any(p.startswith(rif + ".") or p.startswith(rif + "[") for p in percorsi):
                continue
            err("SC-01", f"riferimento a config inesistente: config.{rif}", nome)


# ---------------------------------------------------------------- SC-02
def sc02_parametri_orfani(cfg, prosa):
    """
    Un parametro di config che nessun prompt legge non e' configurazione: e' decorazione.

    Ma questo controllo produce da solo il 92% degli avvisi, e un pannello con settantatre righe
    non si legge — e' il primo comando di ogni run. Il suo esito va in un canale a parte: un
    conteggio in evidenza, l'elenco solo su richiesta (--rumore). Un avviso vero sepolto sotto
    sessantasette avvisi da convenzione e' un avviso perso.
    """
    testo_tot = "\n".join(t for _, t in prosa)
    rumore = []
    foglie = [(p, v) for p, v in cammina(cfg)
              if not isinstance(v, (dict, list)) and not p.split(".")[-1].startswith("_")]
    for p, _ in foglie:
        chiave = p.split(".")[-1]
        if re.match(r"^\[?\d+\]?$", chiave) or chiave in ("id", "$schema", "version"):
            continue
        if chiave not in testo_tot:
            rumore.append(p)


    RUMORE.extend(rumore)
    if rumore:
        avv("SC-02", f"{len(rumore)} parametri mai citati da alcun prompt "
                     "(elenco completo con --rumore)", "config")


# ---------------------------------------------------------------- SC-03
def sc03_costanti_rispecchiate(cfg, prosa):
    """
    Il difetto piu' frequente della storia di questo repo: una soglia scritta a mano in un prompt
    che diverge dal config.

    LIMITE DICHIARATO, misurato e non stimato. Questo controllo legge PROSA, e la prosa italiana
    scrive un numero in molti modi: `soglia >= 6.5`, `6.5 di soglia`, `la soglia vale 40%`, "sopra
    tre quarti", o la soglia in una colonna di tabella e l'etichetta in un'altra. La v2.8 pretendeva
    l'ordine etichetta→operatore→numero sulla stessa riga, cioe' esattamente la forma della propria
    mutazione di autotest: era efficace su 3 costanti dichiarate su 12, e nessuno lo sapeva perche'
    nessuno lo aveva misurato. Ora l'ordine e' libero, le percentuali si convertono, e la COPERTURA
    E' STAMPATA: se una costante dichiarata non compare in nessun file, lo dice invece di tacere.
    Le formulazioni in lettere restano fuori portata, e restano dichiarate tali.
    """
    contratto = (cfg.get("verifica_specifica") or {}).get("costanti_rispecchiate") or []
    if not contratto:
        avv("SC-03", "nessuna costante dichiarata in config.verifica_specifica", "config")
        return
    coperte, scoperte = 0, []
    for i, voce in enumerate(contratto):
        if not isinstance(voce, dict) or "percorso" not in voce or "etichette" not in voce:
            err("SC-03", f"voce {i} malformata: servono 'percorso' e 'etichette'",
                "config.verifica_specifica"); continue
        percorso, etichette = voce["percorso"], voce["etichette"]
        atteso = cfg
        try:
            for k in percorso.split("."):
                atteso = atteso[k]
        except (KeyError, TypeError):
            err("SC-03", f"percorso inesistente nel config: {percorso}",
                "config.verifica_specifica"); continue
        if not isinstance(atteso, (int, float)) or isinstance(atteso, bool):
            err("SC-03", f"{percorso} non e' un numero ({type(atteso).__name__}): non e' "
                         "rispecchiabile", "config.verifica_specifica"); continue
        trovata = False
        for nome, testo in prosa:
            for n_riga, riga in enumerate(testo.split("\n"), 1):
                if IGNORA in riga:
                    continue
                for et in etichette:
                    if et not in riga:
                        continue
                    trovata = True
                    # SOLO i numeri ADIACENTI all'etichetta (20 caratteri per lato), in
                    # entrambi gli ordini. Senza adiacenza qualunque cifra della riga diventa
                    # candidata e il controllo spara su indici di ramo e frazioni "4/5"; con
                    # l'adiacenza in un solo verso perde l'idioma dominante del repo, che scrive
                    # l'operatore PRIMA: `weighted_sum >= config.scoring.soglia (6.5)`.
                    for m_et in re.finditer(re.escape(et), riga):
                        finestra = riga[max(0, m_et.start() - 24): m_et.end() + 40]
                        cand = []
                        for mm in re.finditer(
                                r"(?<![A-Za-z0-9_.-])(\d+(?:[.,]\d+)?)\s*(%?)(\s*/\s*\d)?", finestra):
                            if mm.group(3):          # frazione tipo 4/5: non e' una soglia scalare
                                continue
                            if re.search(r"(?:ramo|gate|passo|wave|criteri[oa]?)\s*$",
                                         finestra[:mm.start()], re.I):
                                continue
                            # indice di elenco a inizio riga ("3. >= keep_if_at_least ...")
                            assoluto = max(0, m_et.start() - 24) + mm.start()
                            if re.match(r"^\s*\d+[.)]", riga) and assoluto < riga.index(".") + 1:
                                continue
                            # NUMERO GIA' LEGATO A UN ALTRO NOME. Una riga di gate mette piu'
                            # confronti in fila: `n_direct >= 1, nessun BEATABLE, >=
                            # misalignment_min criteri M`. L'1 e' l'operando di n_direct, non di
                            # misalignment_min — e la finestra di adiacenza, che non sa leggere,
                            # lo attribuiva all'etichetta sbagliata. Il legame lo fa
                            # l'OPERATORE: nome + (>=|<=|>|<|=|:) + numero. Le forme in prosa
                            # ("la soglia vale 40%", "40% di soglia") non hanno operatore e
                            # restano candidate, che e' il punto — questo non e' un permesso di
                            # scrivere numeri sbagliati, e' il rifiuto di leggerne uno altrui.
                            prec = re.search(r"([A-Za-z_][A-Za-z0-9_.]*)\s*"
                                             r"(?:>=|<=|==|>|<|=|:)\s*$", riga[:assoluto])
                            if prec and et not in prec.group(1) and prec.group(1) not in et:
                                continue
                            g = mm.group(1).replace(",", ".")
                            try:
                                v = float(g) / (100.0 if mm.group(2) else 1.0)
                            except ValueError:
                                continue
                            cand.append((g + mm.group(2), v))
                        if not cand:
                            continue
                        if not any(abs(v - float(atteso)) < 1e-9 for _, v in cand):
                            err("SC-03",
                                f"'{et}' compare accanto a {', '.join(c for c, _ in cand)} "
                                f"ma {percorso} vale {atteso}", f"{nome}:{n_riga}")
        if trovata:
            coperte += 1
        else:
            scoperte.append(percorso)
    if scoperte:
        avv("SC-03", f"costanti dichiarate e mai nominate in prosa ({len(scoperte)}/"
                     f"{len(contratto)}): il contratto promette un controllo che non c'e' — "
                     + ", ".join(scoperte), "config.verifica_specifica")
    avv("SC-03", f"copertura reale: {coperte}/{len(contratto)} costanti effettivamente "
                 "confrontate in almeno un file", "misura, non stima")


# ---------------------------------------------------------------- SC-04
def sc04_pattern_coerenti(schemi):
    """
    Un prefisso di ID = uno spazio di nomi = UN SOLO pattern. Raggruppare per nome di campo non
    basta e non avrebbe preso ne' N1 ne' C1: lo stesso ID logico si chiama `pain_id` in core e
    `id` dentro pain.schema. Il prefisso, invece, e' la cosa che identifica davvero la famiglia.
    Se `^P-` compare con due forme diverse, un agente puo' emettere un ID che supera l'envelope e
    fallisce il proprio schema — e il pain finisce in PARKED per un difetto di punteggiatura.
    """
    per_prefisso = {}

    def scendi(o, file_, percorso=""):
        if isinstance(o, dict):
            p = o.get("pattern")
            if isinstance(p, str):
                m = re.match(r"\^([A-Z]+)-", p)
                if m:
                    per_prefisso.setdefault(m.group(1) + "-", []).append((file_, percorso, p))
            for k, v in o.items():
                scendi(v, file_, f"{percorso}.{k}" if percorso else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                scendi(v, file_, f"{percorso}[{i}]")

    for f_, s in schemi.items():
        scendi(s, f_)
    for prefisso, occ in sorted(per_prefisso.items()):
        forme = {p for _, _, p in occ}
        if len(forme) > 1:
            err("SC-04",
                f"lo spazio di nomi '{prefisso}' ha {len(forme)} pattern diversi: " +
                " | ".join(f"{f_} ({perc}): {p}" for f_, perc, p in occ),
                "schemas")

    # Gli ID namespacati per run INCORPORANO il pattern di run_id. Cambiare run_id senza
    # aggiornarli e' il difetto C1 della v2.6: preso da solo, ogni pattern era coerente con se
    # stesso, quindi il confronto fra prefissi non lo vede. La relazione da verificare non e'
    # "sono uguali", e' "uno contiene l'altro".
    # LO STESSO CAMPO CON DUE VOCABOLARI. La v2.14 lo ha scoperto aggiungendo `ASSENZA` a
    # `evidence_route` in due schemi: SC-05 chiede solo che un token della prosa sia ammesso da
    # QUALCHE schema, quindi toglierlo da uno dei due lo lasciava passare. L'agente emette il
    # valore prescritto dal gate, l'artefatto fallisce la validazione a valle — DOPO aver speso
    # l'intero stage.
    #
    # SU CONTRATTO, e la prima stesura spiega perche'. Applicato a ogni nome di campo ripetuto,
    # il controllo sparava su sette campi legittimi: il `type` di un moat e il `type` di un
    # trigger sono due campi diversi che condividono un sostantivo comune, non un vocabolario. Un
    # controllo che sbaglia sette volte su otto viene spento entro la settimana, e il difetto
    # vero torna a essere invisibile — con in piu' un controllo morto a fare da alibi. Quali
    # campi siano davvero LO STESSO campo e' una decisione di progetto: si dichiara, e SC-00
    # sorveglia che la lista non venga svuotata.
    condivisi = {k: v for k, v in
                 ((json.load(open(CONFIG, encoding="utf-8")).get("verifica_specifica") or {})
                  .get("enum_condivisi") or {}).items() if isinstance(v, dict)}
    if condivisi:
        trovati = []

        def enum_scendi(o, file_, campo=None, percorso="", condizione=False):
            if isinstance(o, dict):
                # un enum sotto if/then/else/not RESTRINGE il campo per un caso: non e' il suo
                # vocabolario, ed e' precisamente la forma con cui la v2.14 ha reso source_id
                # obbligatorio solo sui motivi di rifiuto.
                if isinstance(o.get("enum"), list) and campo in condivisi and not condizione:
                    trovati.append((campo, file_, percorso,
                                    {str(v) for v in o["enum"] if v is not None}))
                for k, v in o.items():
                    strutt = k in ("properties", "items", "$defs", "anyOf", "oneOf",
                                   "allOf", "then", "else", "if", "not")
                    enum_scendi(v, file_, campo if strutt else k,
                                f"{percorso}.{k}" if percorso else k,
                                condizione or k in ("if", "then", "else", "not"))
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    enum_scendi(v, file_, campo, f"{percorso}[{i}]", condizione)

        for f_, s in schemi.items():
            enum_scendi(s, f_)
        for campo, patto in sorted(condivisi.items()):
            canonico = set(patto.get("valori") or [])
            ammesse = patto.get("restrizioni_ammesse") or {}
            occ = [t for t in trovati if t[0] == campo]
            if not canonico:
                err("SC-04", f"'{campo}' e' dichiarato enum condiviso senza i suoi valori: il "
                             "contratto promette un confronto e non dice contro cosa",
                    "config.verifica_specifica.enum_condivisi")
                continue
            if not occ:
                avv("SC-04", f"'{campo}' e' dichiarato enum condiviso ma nessuno schema lo definisce "
                             "cosi': il contratto promette un confronto che non avviene",
                    "config.verifica_specifica.enum_condivisi")
            for _, file_, perc, vals in occ:
                chiave = f"{file_}:{perc}"
                if vals == canonico:
                    continue
                # Una restrizione VOLUTA e' legittima e va dichiarata: la differenza fra una
                # scelta e una deriva e' che la scelta sta scritta da qualche parte. Ma resta un
                # sottoinsieme — dichiarare non autorizza a INVENTARE un valore.
                if chiave in ammesse:
                    if not (vals < canonico):
                        err("SC-04", f"'{campo}' in {chiave} e' dichiarato come restrizione ma "
                                     f"ammette valori estranei: {', '.join(sorted(vals - canonico))}",
                            "schemas")
                    elif not (ammesse[chiave] or "").strip():
                        err("SC-04", f"restrizione di '{campo}' in {chiave} dichiarata senza "
                                     "motivazione", "config.verifica_specifica.enum_condivisi")
                    continue
                manca = sorted(canonico - vals)
                extra = sorted(vals - canonico)
                err("SC-04", f"il campo condiviso '{campo}' diverge in {chiave}: " +
                    (f"non ammette {', '.join(manca)}" if manca else "") +
                    ("; " if manca and extra else "") +
                    (f"ammette {', '.join(extra)} che il contratto non prevede" if extra else "") +
                    " — un agente che emette il valore prescritto dal gate fallisce la validazione "
                    "a valle, dopo aver speso l'intero stage", "schemas")

    core = schemi.get("core.schema.json") or {}
    p_run = (((core.get("$defs") or {}).get("Envelope") or {})
             .get("properties") or {}).get("run_id", {}).get("pattern")
    if not p_run:
        err("SC-04", "core.schema.json non definisce il pattern di run_id", "schemas")
        return
    corpo = p_run.strip("^$")
    namespacati = ((json.load(open(CONFIG, encoding="utf-8")).get("verifica_specifica") or {})
                   .get("id_namespacati_per_run") or [])
    for prefisso in namespacati:
        occ = per_prefisso.get(prefisso)
        if not occ:
            err("SC-04", f"nessun pattern dichiara lo spazio di nomi '{prefisso}'", "schemas")
            continue
        for f_, perc, p in occ:
            if corpo not in p:
                err("SC-04",
                    f"'{prefisso}' e' namespacato per run ma il suo pattern non contiene quello "
                    f"di run_id: {p} non incorpora {corpo}", f"{f_} ({perc})")


# ---------------------------------------------------------------- SC-05
def sc05_enum_vs_prompt(schemi, prosa):
    """
    Un token che i prompt ordinano di emettere e che lo schema non ammette produce un output
    invalido a ogni run. E' il difetto 'Verdict senza FAIL' della review B.
    Si controllano solo i token in MAIUSCOLO_CON_UNDERSCORE, che nella convenzione di questo
    repo sono sempre valori di enum.
    """
    enum_per_nome = {}

    def scendi(o, campo=None):
        if isinstance(o, dict):
            if "enum" in o and campo:
                enum_per_nome.setdefault(campo, set()).update(
                    x for x in o["enum"] if isinstance(x, str))
            for k, v in o.items():
                scendi(v, k if k not in ("properties", "items", "$defs", "allOf") else campo)
        elif isinstance(o, list):
            for v in o:
                scendi(v, campo)

    for s in schemi.values():
        scendi(s)
    ammessi = set()
    for v in enum_per_nome.values():
        ammessi |= v
    # i token che compaiono nei prompt racchiusi in backtick e che assomigliano a un enum
    sospetti = {}
    for nome, testo in prosa:
        for m in re.finditer(r"`([A-Z][A-Z0-9_]{3,}(?:-[A-Z-]+)?)`", testo):
            sospetti.setdefault(m.group(1), set()).add(nome)
    noti_non_enum = {
        "CLAUDE", "JSON", "JSONL", "ISTAT", "SEARCH", "FETCH", "ABORT", "STOP", "README",
        "REPORT", "HTTP", "HTTPS", "URL", "API", "MVP", "MRR", "ARPA", "CAC", "TAM", "SAM",
        "SOM", "NACE", "ATECO", "SME", "PMI", "ICP", "PDF", "HTML", "CSV", "YAML", "IVA",
        "DEEPSEARCH", "WEBSEARCH", "WEBFETCH", "TODO", "NOTA", "BENE", "OK",
    }
    for tok, dove in sorted(sospetti.items()):
        if tok in ammessi or tok in noti_non_enum:
            continue
        if re.match(r"^(LIGHT|BALANCED|DEEP|STRICT|CALIBRATED|T[1-5]_?.*)$", tok):
            continue
        avv("SC-05", f"token in stile enum mai ammesso da uno schema: {tok}",
            ", ".join(sorted(dove))[:80])


# ---------------------------------------------------------------- SC-06
def sc06_criteri_dichiarati(cfg, prosa, schemi):
    """
    Ogni criterio K*/W*/M*/G* citato da un prompt deve essere dichiarato in config, e ogni
    criterio dichiarato deve essere usato da qualcuno. Un criterio citato e non dichiarato e'
    un gate che non si chiudera' mai; uno dichiarato e mai citato e' un gate che non si aprira'.
    """
    dichiarati = set()
    for p, v in cammina(cfg):
        if isinstance(v, str) and re.match(r"^[KWMG][0-9]{1,2}$", v):
            dichiarati.add(v)
        if isinstance(v, dict) and isinstance(v.get("id"), str) \
                and re.match(r"^[KWMG][0-9]{1,2}$", v["id"]):
            dichiarati.add(v["id"])
        if isinstance(v, dict) and isinstance(v.get("codice"), str):
            dichiarati.add(v["codice"])
    citati = {}
    for nome, testo in prosa:
        for m in re.finditer(r"\b([KWMG][0-9]{1,2})\b", testo):
            citati.setdefault(m.group(1), set()).add(nome)
    for c, dove in sorted(citati.items()):
        if c not in dichiarati:
            err("SC-06", f"criterio citato ma non dichiarato in config: {c}",
                ", ".join(sorted(dove))[:80])
    for c in sorted(dichiarati - set(citati)):
        avv("SC-06", f"criterio dichiarato in config e mai citato: {c}", "config")


# ---------------------------------------------------------------- SC-07
def sc07_separazione_dei_poteri(cfg):
    """
    Un agente di solo giudizio che dichiara WebSearch o WebFetch puo' fabbricare una fonte.
    La separazione dei poteri e' l'invariante piu' importante dell'intero sistema, ed era
    verificabile solo leggendo dieci file a mano.
    """
    contratto = (cfg.get("verifica_specifica") or {}).get("agenti") or {}
    solo_giudizio = set(contratto.get("solo_giudizio") or [])
    con_ricerca = set(contratto.get("con_ricerca") or [])
    if not solo_giudizio:
        avv("SC-07", "config.verifica_specifica.agenti non dichiara gli agenti di solo giudizio")
        return
    su_disco = {}
    for p in sorted(glob.glob(os.path.join(AGENTI, "*.md"))):
        t = leggi(p)
        base = os.path.basename(p)[:-3]
        m_nome = re.search(r"^name:\s*(\S+)\s*$", t, re.M)
        # Due forme YAML valide, e la v2.8 ne leggeva una sola: `tools: A, B` inline e
        # `tools:` seguito da `  - A`. Con la seconda, `\s*` divorava il newline e group(1)
        # diventava "- Read": un agente di solo giudizio con WebSearch passava pulito, e con
        # lui l'invariante che questo repo dichiara il piu' importante di tutti.
        m_tools = re.search(r"^tools:[ \t]*(\S.*)$", t, re.M)
        if m_tools:
            strumenti = {x.strip() for x in m_tools.group(1).split(",") if x.strip()}
        else:
            m_lista = re.search(r"^tools:[ \t]*\n((?:[ \t]*-[ \t]*\S+[ \t]*\n)+)", t, re.M)
            m_tools = m_lista
            strumenti = ({x.strip(" \t-") for x in m_lista.group(1).strip().split("\n")}
                         if m_lista else set())
        su_disco[base] = strumenti
        if not m_nome:
            err("SC-07", "frontmatter senza campo name", base + ".md")
        elif m_nome.group(1) != base:
            err("SC-07", f"name '{m_nome.group(1)}' diverso dal nome del file", base + ".md")
        if not m_tools:
            err("SC-07", "frontmatter senza campo tools: l'agente eredita tutti gli strumenti",
                base + ".md")
    for a in sorted(solo_giudizio):
        if a not in su_disco:
            err("SC-07", f"agente dichiarato di solo giudizio ma assente da .claude/agents: {a}")
            continue
        vietati = su_disco[a] & {"WebSearch", "WebFetch"}
        if vietati:
            err("SC-07",
                f"agente di SOLO GIUDIZIO con strumenti di ricerca: {', '.join(sorted(vietati))}",
                a + ".md")
    for a in sorted(con_ricerca):
        if a in su_disco and not (su_disco[a] & {"WebSearch", "WebFetch"}):
            err("SC-07", "agente di ricerca senza alcuno strumento di ricerca", a + ".md")
    non_dichiarati = set(su_disco) - solo_giudizio - con_ricerca
    for a in sorted(non_dichiarati):
        err("SC-07", f"agente presente su disco e non classificato in config: {a}")


# ---------------------------------------------------------------- SC-08
def sc08_artefatti_di_chiusura():
    """
    L'orchestratore promette un elenco di artefatti; run_close.py ne verifica un altro. Quando i
    due divergono, il run chiude NON VALIDO per un file che nessuno ha mai chiesto di scrivere —
    oppure, peggio, chiude VALIDO senza un file che serviva. E' la classe di C2.
    """
    rc = os.path.join(ROOT, "tools", "run_close.py")
    if not os.path.exists(rc):
        err("SC-08", "tools/run_close.py assente"); return
    t = leggi(rc)
    # IL TACHIMETRO NON DEVE POTER SPARIRE IN SILENZIO. Misura se il run e' MISURABILE:
    # quanti artefatti dichiarano `fetches` e quanti snippet dichiarano se la pagina sia stata
    # aperta (`snippet_source`). Serve perche' una decisione di acquisto — aggiungere un fetcher
    # con rendering — stava per essere presa su un'ipotesi che nessun dato del sistema poteva
    # confermare o smentire. E' un avviso, non blocca niente; ma un avviso che qualcuno toglie
    # perche' "tanto e' solo un avviso" riporta il sistema a discutere di retrieval per aneddoti.
    for atteso, cosa in (("fetches", "il consumo di lettura"),
                         ("snippet_source", "la provenienza degli snippet")):
        if atteso not in t:
            err("SC-08", f"run_close.py non guarda piu' '{atteso}': {cosa} torna non misurato, e "
                         "con esso ogni discussione su fetcher e crawler torna aneddotica",
                "tools/run_close.py")

    m = re.search(r"ARTEFATTI\s*=\s*\[(.*?)\]", t, re.S)
    if not m:
        err("SC-08", "ARTEFATTI non trovato in run_close.py"); return
    attesi = set(re.findall(r"\"([^\"]+)\"", m.group(1)))
    orch = leggi(ORCH)
    sezione = orch.split("## 11.")[-1] if "## 11." in orch else orch
    for a in sorted(attesi):
        if a not in sezione:
            err("SC-08", f"run_close pretende '{a}' ma §11 non dice di scriverlo",
                "deepsearch.md §11")
    for a in sorted(set(re.findall(r"`runs/<run_id>/([A-Za-z0-9._-]+)`", sezione))):
        if a not in attesi and a != "REPORT.md":
            avv("SC-08", f"§11 scrive '{a}' ma run_close non lo verifica", "run_close.py")
    # L'ORDINE E' LA SOSTANZA, ed e' il difetto C2. Tre ancore, e devono comparire in questa
    # sequenza: si scrive il report, POI lo si verifica, POI e solo poi si tocca state/.
    # Invertire le prime due rende ogni run NON VALIDO per costruzione (si verifica prima di
    # scrivere cio' che si verifica); invertire le ultime due lascia la memoria aggiornabile da
    # un run guasto, che e' il guasto che si propaga.
    # Le ancore sono MARCATORI, non frasi. Con stringhe letterali, riformulare una riga di §11 —
    # attivita' normale su un documento di prosa — bloccava il run: il falso positivo piu' facile
    # da produrre e il piu' velenoso, perche' insegna a disattivare il controllo.
    def ancora(nome_ancora, ripiego):
        i = sezione.find(f"<!-- ancora: {nome_ancora} -->")
        return i if i >= 0 else sezione.find(ripiego)

    ancore = [("scrittura del report", ancora("report", "runs/<run_id>/REPORT.md")),
              ("verifica finale", ancora("verifica-finale", "--fase finale")),
              ("autorizzazione a state/", ancora("scrittura-state", "esegui §10"))]
    if any(i < 0 for _, i in ancore):
        mancanti = [n for n, i in ancore if i < 0]
        err("SC-08", "§11 non contiene le ancore di chiusura: " + ", ".join(mancanti),
            "deepsearch.md §11")
    else:
        for (n1, i1), (n2, i2) in zip(ancore, ancore[1:]):
            if i2 < i1:
                err("SC-08", f"§11 mette '{n2}' PRIMA di '{n1}'", "deepsearch.md §11")
    i_pre = ancora("verifica-pre", "--fase pre")
    i_rep0 = ancora("report", "runs/<run_id>/REPORT.md")
    if i_pre >= 0 and i_rep0 >= 0 and i_pre > i_rep0:
        err("SC-08", "§11 esegue la verifica preliminare DOPO la scrittura del report: e' la "
                     "circolarita' C2, che questo controllo esiste per sorvegliare",
            "deepsearch.md §11")
    if "reconcile_run.py" not in sezione:
        err("SC-08", "§11 non riconcilia gli artefatti con i ledger prima di chiudere: senza quel "
                     "passo run_close confronta due numeri scritti dallo stesso attore e mai la "
                     "loro corrispondenza con il lavoro prodotto", "deepsearch.md §11")
    if "--fase pre" not in sezione:
        err("SC-08", "§11 non esegue la verifica preliminare: senza di essa il verdetto che "
                     "decide l'apertura del report non esiste ancora quando il report si scrive",
            "deepsearch.md §11")


# ---------------------------------------------------------------- SC-09
def sc09_rami_enumerati(prosa):
    """'Sette rami' seguito da otto righe numerate. Piccolo, ma e' il sintomo di una tabella
    modificata senza rileggere la frase che la introduce — cioe' della deriva che stiamo cercando."""
    numeri = {"due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8,
              "nove": 9, "dieci": 10, "undici": 11, "dodici": 12}
    for nome, testo in prosa:
        # Non piu' solo i blocchi **GATE ...**: la stessa enumerazione compare nei prompt degli
        # agenti ("cade in uno dei sette rami" sopra otto righe), che e' dove SC-09 la mancava.
        # "rami" seguito da un blocco entro poche righe = enumerazione che introduce l'elenco.
        # Una menzione narrativa a meta' paragrafo ("non attivavano nessuno degli otto rami")
        # non introduce niente e non va contata: e' la differenza fra dichiarare e citare.
        for m in re.finditer(r"([A-Za-zà-ù]+)\s+rami[^\n]{0,80}(?:\n[^\n]{0,120}){0,3}\n```", testo):
            parola = m.group(1).lower()
            if parola not in numeri:
                continue
            coda = testo[m.end() - 3:m.end() + 1600]
            blocco = re.search(r"```(.*?)```", coda, re.S)
            if not blocco:
                continue
            n = len(re.findall(r"^\s*\d+\.", blocco.group(1), re.M))
            if n and n != numeri[parola]:
                riga = testo[:m.start()].count("\n") + 1
                err("SC-09", f"dichiara '{parola} rami' ({numeri[parola]}) e ne elenca {n}",
                    f"{nome}:{riga}")


# ---------------------------------------------------------------- SC-10
def sc10_riferimenti_schemi(schemi):
    """Ogni $ref deve puntare a un file e a una definizione che esistono."""
    def scendi(o, f_):
        if isinstance(o, dict):
            r = o.get("$ref")
            if isinstance(r, str):
                file_, _, frammento = r.partition("#")
                bersaglio = file_ or f_
                if bersaglio not in schemi:
                    err("SC-10", f"$ref a schema inesistente: {r}", f_)
                elif frammento:
                    nodo = schemi[bersaglio]
                    for parte in [p for p in frammento.split("/") if p and p != "#"]:
                        if isinstance(nodo, dict) and parte in nodo:
                            nodo = nodo[parte]
                        else:
                            err("SC-10", f"$ref a definizione inesistente: {r}", f_)
                            break
            for v in o.values():
                scendi(v, f_)
        elif isinstance(o, list):
            for v in o:
                scendi(v, f_)
    for f_, s in schemi.items():
        scendi(s, f_)


# ---------------------------------------------------------------- SC-11
def sc11_aritmetica_del_funnel(cfg):
    """
    Il difetto del pain-finder: DEEP dichiarava 80 pain, l'intervallo di ID ne ammetteva 64 e il
    budget ne consentiva 48. Tre numeri in tre posti, nessuno che li confrontasse.
    """
    for prof, f in (cfg.get("funnel") or {}).items():
        if not isinstance(f, dict):
            continue
        capienza = f.get("shards", 0) * f.get("pain_ids_per_shard", 0)
        if f.get("pains_generated", 0) > capienza:
            err("SC-11",
                f"{prof}: pains_generated={f['pains_generated']} > shards x pain_ids_per_shard"
                f"={capienza}: gli ID finiscono prima dei pain", "config.funnel")
        catena = ["pains_generated", "pains_to_evidence", "pains_to_competitor",
                  "ideas_to_architect"]
        val = [f.get(k) for k in catena if f.get(k) is not None]
        if val != sorted(val, reverse=True):
            err("SC-11", f"{prof}: il funnel non e' monotono decrescente: {val}", "config.funnel")
        if f.get("max_fetches_total", 0) < f.get("max_searches_total", 0):
            avv("SC-11", f"{prof}: max_fetches_total < max_searches_total", "config.funnel")
    # Raggiungibilita'. Il confine "niente semantica" non reggeva all'ispezione: SC-11 fa gia'
    # aritmetica di dominio (pesi che sommano a 1, funnel monotono). Il confine reale e' un altro,
    # ed e' difendibile: si verifica che un ramo sia RAGGIUNGIBILE, mai che la soglia sia giusta.
    # misalignment_min = 20 su quattro criteri M non e' una soglia severa, e' un ramo spento — e
    # quel ramo, per la config stessa, e' "meta' della missione".
    ct = cfg.get("competitor_thresholds") or {}
    for prof in ("STRICT", "CALIBRATED"):
        b = ct.get(prof) or {}
        for campo, lista, etichetta in (("misalignment_min", "misalignment_criteria", "criteri M"),
                                        ("keep_if_at_least", "keep_criteria", "keep criteria")):
            soglia, disponibili = b.get(campo), b.get(lista)
            if isinstance(soglia, int) and isinstance(disponibili, list) and soglia > len(disponibili):
                err("SC-11", f"{prof}.{campo} = {soglia} ma esistono {len(disponibili)} "
                             f"{etichetta}: il ramo che dipende da questa soglia e' irraggiungibile",
                    "config.competitor_thresholds")
    # Emettibilita' del kill al gate 2. Il config dichiarava gia' il vincolo
    # (budget_minimo_perche_un_kill_sia_emettibile = 3x la finestra zero_yield) e nessuno lo
    # verificava: in tutti e tre i profili il budget era sotto la soglia, quindi la saturazione era
    # irraggiungibile, la regola del troncamento plafonava ogni verdetto a PARK, e G1/G3 non
    # potevano uccidere. Un vincolo scritto nel file che lo viola e' il caso limite di questa
    # intera sessione: la regola c'era, mancava chi la confrontasse con i numeri accanto.
    for prof, f_ in (cfg.get("funnel") or {}).items():
        if not isinstance(f_, dict) or "evidence_searches_per_channel" not in f_:
            continue
        b = f_["evidence_searches_per_channel"]
        finestra = max(2, b // 3)                      # stop_rules.zero_yield_window
        if b < 3 * finestra:
            err("SC-11", f"{prof}: evidence_searches_per_channel = {b} ma la finestra zero_yield "
                         f"e' {finestra}, quindi servono almeno {3 * finestra} ricerche perche' un "
                         "kill sia emettibile. Sotto quella soglia la saturazione non si raggiunge "
                         "mai, ogni canale chiude BUDGET_EXHAUSTED, il troncamento plafona a PARK "
                         "e il gate 2 non puo' uccidere", "config.funnel")

    ah = cfg.get("anti_hallucination") or {}
    if isinstance(ah.get("min_domande_fontate"), int) and isinstance(ah.get("domande"), list) \
            and ah["min_domande_fontate"] > len(ah["domande"]):
        err("SC-11", f"min_domande_fontate = {ah['min_domande_fontate']} ma le domande sono "
                     f"{len(ah['domande'])}: nessuna idea potra' mai essere pubblicata",
            "config.anti_hallucination")
    scal = (ct.get("valutazione_a_scalini") or {})
    obb, best = set(scal.get("criteri_obbligatori") or []), set(scal.get("criteri_best_effort") or [])
    if obb & best:
        err("SC-11", f"criteri sia obbligatori sia best-effort: {sorted(obb & best)}",
            "config.competitor_thresholds")
    dichiarati = set()
    for prof in ("STRICT", "CALIBRATED"):
        for gruppo in ("kill_if_any", "keep_criteria"):
            for voce in ((ct.get(prof) or {}).get(gruppo) or []):
                if isinstance(voce, dict) and isinstance(voce.get("id"), str):
                    dichiarati.add(voce["id"])
    if dichiarati and (obb | best) and (obb | best) != dichiarati:
        mancanti, estranei = dichiarati - (obb | best), (obb | best) - dichiarati
        err("SC-11", "la partizione obbligatori/best-effort non copre i criteri dichiarati — "
                     + (f"mai classificati: {sorted(mancanti)} " if mancanti else "")
                     + (f"inesistenti: {sorted(estranei)}" if estranei else ""),
            "config.competitor_thresholds")

    s = cfg.get("scoring") or {}
    pesi = sum(d.get("peso", 0) for d in (s.get("dimensioni") or []))
    if abs(pesi - 1.0) > 1e-6:
        err("SC-11", f"i pesi delle dimensioni sommano a {pesi}, non a 1.0", "config.scoring")
    if s.get("soglia_menzione_onorevole", 0) >= s.get("soglia_pubblicazione", 0):
        err("SC-11", "soglia_menzione_onorevole >= soglia_pubblicazione", "config.scoring")


# ---------------------------------------------------------------- SC-12
def sc12_agenti_citati(cfg):
    """Ogni agente nominato dall'orchestratore deve esistere, e ogni agente su disco deve avere
    un budget in config e almeno una chiamata nell'orchestratore."""
    orch = leggi(ORCH)
    su_disco = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(AGENTI, "*.md"))}
    budget = set((cfg.get("budget") or {}).get("max_per_agent") or {})
    citati = {a for a in su_disco if a in orch}
    for a in sorted(su_disco - citati):
        err("SC-12", f"agente su disco mai invocato dall'orchestratore: {a}")
    for a in sorted(budget - su_disco):
        err("SC-12", f"budget assegnato a un agente inesistente: {a}", "config.budget")
    contratto = (cfg.get("verifica_specifica") or {}).get("agenti") or {}
    senza_budget = su_disco - budget - set(contratto.get("solo_giudizio") or [])
    for a in sorted(senza_budget):
        err("SC-12", f"agente di ricerca senza budget in config: {a}", "config.budget")

    # PRESENZA non basta: serve il RUOLO. Verificare che il nome compaia "da qualche parte"
    # nell'orchestratore rende invisibile un agente messo nella wave sbagliata — e source-verifier
    # al posto di competitor-analyst nella wave 3 significa nessuna mappatura competitiva, quindi
    # nessun kill possibile al gate 3, su ogni pain del run.
    sezioni = {}
    for m in re.finditer(r"^## \d+\.\s*WAVE (\d+)[^\n]*$", orch, re.M):
        fine = orch.find("\n## ", m.end())
        sezioni[int(m.group(1))] = orch[m.end():fine if fine > 0 else len(orch)]
    for a, wave_ammesse in sorted(((cfg.get("verifica_specifica") or {})
                                   .get("wave_per_agente") or {}).items()):
        if a not in su_disco:
            err("SC-12", f"wave dichiarata per un agente inesistente: {a}",
                "config.verifica_specifica"); continue
        presenti = sorted(w for w, testo in sezioni.items() if a in testo)
        estranee = [w for w in presenti if w not in wave_ammesse]
        if not presenti:
            err("SC-12", f"{a} non compare in nessuna wave: dichiarato per la/e "
                         f"{wave_ammesse}", "deepsearch.md")
        if estranee:
            err("SC-12", f"{a} compare nella wave {estranee} ma e' dichiarato solo per "
                         f"{wave_ammesse}", "deepsearch.md")
        mancanti = [w for w in wave_ammesse if w not in presenti]
        if mancanti:
            err("SC-12", f"{a} e' dichiarato per la wave {mancanti} ma non vi compare: quella wave "
                         "gira senza di lui", "deepsearch.md")


# ---------------------------------------------------------------- SC-13
def sc13_documenti_derivati():
    """
    La prosa non si puo' tenere allineata a macchina: e' il limite onesto di questo strumento.
    Ma si puo' garantire a macchina che NESSUNO la scambi per la specifica. Ogni documento
    esplicativo deve dichiararsi derivato e dire chi vince in caso di divergenza.

    Non e' formalismo. DATA-CONTRACT.md non portava questo riquadro, e per due versioni ha
    documentato un pattern di pain_id diverso da quello degli schemi: chi lo leggeva lo prendeva
    per la fonte di verita'. Il difetto N1 e' uscito da li'.
    """
    trovati = sorted(glob.glob(os.path.join(DOCS, "*.md")))
    if len(trovati) < 5:
        err("SC-13", f"docs/ contiene {len(trovati)} documenti: sotto il minimo di 5. Svuotare la "
                     "cartella non puo' essere un modo per far passare i controlli", "docs/")
    for p in trovati:
        nome = os.path.basename(p)
        if e_storico(nome):
            continue
        t = leggi(p)
        if "DOCUMENTO DERIVATO" not in t:
            err("SC-13",
                "documento esplicativo senza il riquadro 'DOCUMENTO DERIVATO': un lettore non "
                "puo' sapere che non e' la specifica", f"docs/{nome}")


# ---------------------------------------------------------------- SC-14
def sc14_liste_vs_enum(cfg, schemi):
    """
    Un elenco di valori nel config e l'enum che lo rispecchia in uno schema devono coincidere.

    E' la classe di difetto che e' passata indenne attraverso tredici controlli: `MENZIONE-ONESTA`
    col trattino in config, `MENZIONE_ONESTA` con l'underscore nello schema del ranking e nel
    prompt che lo emette. L'agente emetteva il valore dello schema, l'orchestratore lo scriveva
    nella memoria, e il conteggio di `condizione_di_riprogettazione` — "5 run senza PUBBLICATA" —
    cercava un'etichetta che nel ledger non compariva mai. SC-05 vedeva token nella prosa e SC-06
    criteri: fra config e schemi non guardava nessuno.
    """
    for voce in ((cfg.get("verifica_specifica") or {}).get("liste_vs_enum") or []):
        try:
            percorso, schema_, punt = voce["percorso"], voce["schema"], voce["puntatore"]
        except (KeyError, TypeError):
            err("SC-14", "voce malformata: servono 'percorso', 'schema', 'puntatore'",
                "config.verifica_specifica"); continue
        val = cfg
        try:
            for k in percorso.split("."):
                val = val[k]
        except (KeyError, TypeError):
            err("SC-14", f"percorso inesistente nel config: {percorso}",
                "config.verifica_specifica"); continue
        if schema_ not in schemi:
            err("SC-14", f"schema inesistente: {schema_}", "config.verifica_specifica"); continue
        nodo = schemi[schema_]
        try:
            parti = [p for p in punt.split("/") if p and p != "#"]
            if parti and parti[-1] == "enum":      # il puntatore puo' finire su /enum
                parti = parti[:-1]
            for parte in parti:
                nodo = nodo[int(parte)] if parte.isdigit() else nodo[parte]
        except (KeyError, TypeError, IndexError):
            err("SC-14", f"puntatore irrisolvibile: {schema_}{punt}",
                "config.verifica_specifica"); continue
        enum = nodo.get("enum") if isinstance(nodo, dict) else None
        if enum is None:
            err("SC-14", f"{schema_}{punt} non e' un enum", "config.verifica_specifica"); continue
        solo_cfg, solo_schema = set(val) - set(enum), set(enum) - set(val)
        if solo_cfg or solo_schema:
            err("SC-14",
                f"{percorso} e {schema_}{punt} non coincidono — "
                + (f"solo nel config: {sorted(solo_cfg)} " if solo_cfg else "")
                + (f"solo nello schema: {sorted(solo_schema)}" if solo_schema else ""),
                "config vs schemi")


# ---------------------------------------------------------------- SC-15
def sc15_schema_verso_prompt(cfg, schemi):
    """
    Tutti i controlli guardavano prompt → schema. Nessuno guardava nella direzione opposta.

    Un campo `required` che nessun prompt nomina non e' una condanna certa — i prompt rinviano al
    proprio schema per percorso, e l'agente puo' leggerlo — ma e' una omissione che si paga a run
    avviato, alla wave in cui capita, come un PARK inspiegabile. Nominarlo nel prompt costa una
    riga. La direzione opposta (prompt → schema) era coperta da SC-05; questa non lo era da nessuno.
    """
    # TRE DIFETTI DELLA PRIMA STESURA, tutti misurati sul primo canary reale.
    #
    # (1) Guardava UN SOLO NODO: `payload.required` di primo livello. Non scendeva nelle
    #     proprieta' annidate e non risolveva i `$ref`. Copertura misurata sui dieci schemi che
    #     dichiara di verificare: 48 `required` su 182, il 26%. `promoted_by` vive in
    #     `payload.properties.claims.items.required` e non e' mai stato guardato — e' il campo
    #     che ha fatto scendere in PARKED tutti e cinque i canali di un run reale.
    # (2) `pain_id` era fra gli esenti, scambiato per un campo d'envelope. Non e' in
    #     `Envelope.required`: e' `required` nel payload di sette schemi, e le occorrenze nei
    #     sette prompt corrispondenti sono ZERO. L'insieme degli esenti si ricava ora
    #     dall'envelope invece di essere ricopiato a mano: una lista scritta a mano diverge, e
    #     questa era gia' divergente.
    # (3) `campo in testo` e' una ricerca di sottostringa senza confini di parola.
    #     `bootstrap-verdict` pretende `dimensions`, `bootstrap-filter.md` contiene
    #     `blocking_dimensions` — che il prompt dichiara «arrivano dall'input» — e il controllo
    #     taceva. Il confronto e' ora per TOKEN ESATTO contro un vocabolario estratto per
    #     posizione lessicale: identificatori fra backtick e chiavi dei blocchi json.
    #
    # PERCHE' SOLO I VOCABOLARI CHIUSI. Segnalare ogni `required` non nominato produrrebbe
    # centinaia di righe su campi che l'agente puo' dedurre dal nome. Il danno reale e' il campo
    # che ha un `enum` o un `pattern`: la' non basta indovinare il nome, bisogna conoscere i
    # valori ammessi, e sbagliarli costa l'intero stage. Se il prompt insegna tutti i valori
    # dell'enum senza nominare il campo, l'agente sa comunque cosa scrivere e non si segnala.
    envelope = ((schemi.get("core.schema.json") or {}).get("$defs") or {}).get("Envelope") or {}
    esenti = set(envelope.get("required") or [])
    if not esenti:
        err("SC-15", "core.schema.json#/$defs/Envelope non dichiara 'required': l'insieme degli "
                     "esenti verrebbe calcolato vuoto e ogni campo d'envelope sarebbe segnalato",
            "schemas")

    con_delivery = set((cfg.get("verifica_specifica") or {}).get("agenti_con_delivery") or [])

    def delivery_di(agente):
        """La vista delivery generata dal compilatore, per un agente dichiarato 'con delivery'.

        Una catena schema→delivery rotta e' un ERRORE, mai un silenzio: se la delivery non e'
        generabile, il prompt consegnato non puo' esistere e nessun controllo statico deve
        fingere il contrario. Il compilatore e' importato qui (stesso albero tools/, stdlib,
        deterministico): nessuna rete, nessun jsonschema."""
        try:
            import contract_compile as cc
        except Exception as e:
            err("SC-15", f"compilatore non importabile per la verifica della delivery: {e}",
                "tools")
            return None
        try:
            _, consegna = cc.compila_viste(agente, os.path.join(ROOT, "schemas"),
                                           "non-dichiarato")
        except Exception as e:
            err("SC-15", f"la delivery per {agente} non e' generabile: la catena "
                         f"schema→delivery e' rotta ({type(e).__name__}: {e})", f"{agente}.md")
            return None
        return consegna

    def risolvi(nodo, corrente, giri=0):
        """Segue i `$ref` locali e verso altri schemi. I fratelli di un `$ref` in Draft 2020-12
        SI SOMMANO al referente invece di sostituirlo: e' il meccanismo su cui poggia
        l'obbligatorieta' di `promoted_by`, e ignorarlo significa non vederla."""
        while isinstance(nodo, dict) and "$ref" in nodo and giri < 10:
            giri += 1
            ref, resto = nodo["$ref"], {k: v for k, v in nodo.items() if k != "$ref"}
            if not ref.startswith("#"):
                file_, _, frammento = ref.partition("#")
                if file_ not in schemi:
                    return nodo, corrente
                corrente, ref = file_, "#" + frammento
            t = schemi[corrente]
            for seg in ref.lstrip("#/").split("/"):
                if not seg:
                    continue
                seg = seg.replace("~1", "/").replace("~0", "~")
                if not isinstance(t, dict) or seg not in t:
                    return nodo, corrente
                t = t[seg]
            fuso = dict(t)
            for k, v in resto.items():
                if k == "required":
                    fuso["required"] = sorted(set(fuso.get("required") or []) | set(v))
                elif k == "properties":
                    q = dict(fuso.get("properties") or {}); q.update(v); fuso["properties"] = q
                else:
                    fuso[k] = v
            nodo = fuso
        return nodo, corrente

    def vocabolario_chiuso(nodo, corrente):
        """Il campo ha valori che l'agente non puo' indovinare? Ritorna il sottoschema, o None."""
        n, c2 = risolvi(nodo, corrente)
        if "enum" in n or "pattern" in n:
            return n
        if n.get("type") == "array":
            it, _ = risolvi(n.get("items") or {}, c2)
            if "enum" in it or "pattern" in it:
                return it
        return None

    def campi_chiusi(nodo, corrente, percorso="payload", fuori=None, prof=0):
        if fuori is None:
            fuori = []
        if prof > 12 or not isinstance(nodo, dict):
            return fuori
        nodo, corrente = risolvi(nodo, corrente)
        for c in (nodo.get("required") or []):
            sotto = (nodo.get("properties") or {}).get(c)
            if sotto is None:
                continue
            v = vocabolario_chiuso(sotto, corrente)
            if v is not None:
                fuori.append((f"{percorso}.{c}", c, v))
        for k, v in (nodo.get("properties") or {}).items():
            campi_chiusi(v, corrente, f"{percorso}.{k}", fuori, prof + 1)
        if "items" in nodo:
            campi_chiusi(nodo["items"], corrente, percorso + "[]", fuori, prof + 1)
        for parola in ("allOf", "anyOf", "oneOf"):
            for sub in (nodo.get(parola) or []):
                campi_chiusi(sub, corrente, percorso, fuori, prof + 1)
        return fuori

    def vocabolario_del_prompt(testo, con_delivery=False):
        """Identificatori che il prompt nomina COME CAMPI. Non `campo in testo`: la posizione
        lessicale distingue `dimensions` da `blocking_dimensions`.

        I percorsi puntati fra backtick contano per OGNI segmento: `summary.fabricated_sources`
        nomina sia `summary` sia `fabricated_sources`, e `payload.ranking[].grounded_questions`
        nomina anche `ranking`. Senza questa scomposizione il controllo sparava su campi che il
        prompt documenta per esteso - ed e' il primo modo in cui un controllo si fa disattivare.

        Con `con_delivery` (M3) il testo include la vista delivery, la cui grammatica non ha
        backtick ne' blocchi json: si estraggono i NOMI DI RIGA dell'albero e i nomi di campo
        dei percorsi della sezione COND. Nessuna esenzione per campo: la copertura passa per la
        sola grammatica della delivery, altrimenti sarebbe la whitelist vietata.
        """
        v = set()
        for span in re.findall(r"`([^`\n]+)`", testo):
            v |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", span))
        for blocco in re.findall(r"```json(.*?)```", testo, re.S):
            v |= set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', blocco))
        if con_delivery:
            for riga in testo.splitlines():
                s = riga.lstrip(" ")
                if s.startswith("!"):
                    s = s[1:]
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\b", s)
                if m and m.group(1) != "radice":
                    v.add(m.group(1))
                if s[:1] in ("+", "-"):
                    percorso = s[1:].split("|")[0]
                    for seg in percorso.split("/"):
                        v |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", seg))
        return v

    def insegna_i_valori(testo, vincolo):
        vals = vincolo.get("enum")
        if not vals:
            return False
        return all(re.search(r"\b" + re.escape(str(x)) + r"\b", testo) for x in vals)

    for voce in ((cfg.get("verifica_specifica") or {}).get("schema_per_agente") or []):
        try:
            schema_, agente = voce["schema"], voce["agente"]
        except (KeyError, TypeError):
            err("SC-15", "voce malformata: servono 'schema' e 'agente'",
                "config.verifica_specifica"); continue
        p = os.path.join(AGENTI, agente + ".md")
        if schema_ not in schemi:
            err("SC-15", f"schema inesistente: {schema_}", "config.verifica_specifica"); continue
        if not os.path.exists(p):
            err("SC-15", f"agente inesistente: {agente}", "config.verifica_specifica"); continue
        # Uno schema DICHIARATO qui e privo di properties.payload spegneva il controllo in
        # silenzio: `or {}` seguito da `or []` itera su una lista vuota e non emette nulla.
        # Un controllo che tace quando il suo oggetto non c'e' e' indistinguibile da un
        # controllo che passa.
        payload = ((schemi[schema_].get("properties") or {}).get("payload"))
        if not isinstance(payload, dict):
            err("SC-15", f"{schema_} e' dichiarato in schema_per_agente ma non ha "
                         "properties.payload: il controllo non avrebbe nulla da guardare e "
                         "tacerebbe", "config.verifica_specifica")
            continue
        testo = leggi(p)
        consegna = None
        if agente in con_delivery:
            consegna = delivery_di(agente)
            if consegna is None:
                continue
            testo = testo + "\n\n---\n\n" + consegna
        vocab = vocabolario_del_prompt(testo, con_delivery=consegna is not None)
        visti = set()

        # PASSO 1 — la garanzia originale, invariata nella portata: OGNI campo di
        # `payload.required` di primo livello deve essere nominato dal prompt. Vale anche per i
        # campi che non hanno una `properties` corrispondente, che il passo 2 non potrebbe
        # vedere. Restringere questo passo ai soli vocabolari chiusi avrebbe scambiato una
        # copertura con un'altra: l'autotest lo ha ripreso subito, sulla mutazione che aggiunge
        # `confutazione_richiesta` a redteam-report e non la nomina in nessun prompt.
        for campo in (payload.get("required") or []):
            if campo in esenti or campo in vocab:
                continue
            visti.add(campo)
            err("SC-15",
                f"'{campo}' e' required nel payload di {schema_} e il prompt non lo nomina mai: "
                "l'agente puo' ometterlo, e l'errore si manifesta a run avviato", f"{agente}.md")

        # PASSO 2 — i campi ANNIDATI, che il passo 1 non raggiunge. Qui la soglia e' il
        # vocabolario chiuso: pretendere che il prompt nomini tutti i 182 required raggiungibili
        # produrrebbe centinaia di righe su campi deducibili dal nome, e un controllo che spara a
        # vuoto viene disattivato molto prima di uno che tace. Il danno reale e' il campo di cui
        # non basta indovinare il nome perche' bisogna conoscerne i valori ammessi.
        for percorso, campo, vincolo in campi_chiusi(payload, schema_):
            if campo in esenti or campo in vocab or campo in visti:
                continue
            if insegna_i_valori(testo, vincolo):
                continue
            visti.add(campo)
            err("SC-15",
                f"'{campo}' e' required in {schema_} ({percorso}), ha un vocabolario chiuso "
                f"({'enum' if 'enum' in vincolo else 'pattern'}) e il prompt non lo nomina mai: "
                "l'agente non puo' indovinarne i valori, e l'errore si manifesta a run avviato",
                f"{agente}.md")


# ---------------------------------------------------------------- SC-16
def sc16_regole_dei_criteri(cfg, prosa):
    """
    Un criterio la cui REGOLA e' scritta diversamente nel config e in un prompt.

    SC-03 confronta numeri accanto a etichette. `G2` non e' un numero: il config lo definisce
    "occasionale o una tantum", e `pain-finder.md` ha continuato per otto versioni a scrivere
    "frequenza < mensile" — la soglia rimossa in v2.1 proprio perche' uccideva gli adempimenti
    trimestrali, "il caso piu' PAGATO di tutti". Gli agenti del run 20260802-1759 hanno applicato
    il config, correttamente, ma nessun controllo vedeva la divergenza.

    Segnale meccanico: una parola di FREQUENZA accanto al codice di un criterio, assente dalla
    regola che il config gli assegna.
    """
    # Ogni frequenza con le sue varianti: il config puo' scrivere "EUR/anno" dove il prompt scrive
    # "annuale", e sono la stessa cosa. Senza le varianti il controllo spara sui sinonimi, che e' il
    # modo piu' rapido per farsi disattivare.
    frequenze = {"giornalier": ("giornalier", "al giorno", "quotidian"),
                 "quotidian": ("quotidian", "giornalier", "al giorno"),
                 "settimanal": ("settimanal", "a settimana"),
                 "mensil": ("mensil", "al mese", "ogni mese"),
                 "bimestral": ("bimestral", "due mesi"),
                 "trimestral": ("trimestral", "trimestre"),
                 "semestral": ("semestral", "semestre"),
                 "annual": ("annual", "anno", "annuo")}
    regole = {}
    for _, v in cammina(cfg):
        if isinstance(v, dict) and isinstance(v.get("id"), str) \
                and re.match(r"^[KWMG][0-9]{1,2}$", v["id"]) and isinstance(v.get("regola"), str):
            regole[v["id"]] = v["regola"].lower()
    for nome, testo in prosa:
        for n_riga, riga in enumerate(testo.split("\n"), 1):
            if IGNORA in riga:
                continue
            for cod in re.findall(r"\b([KWMG][0-9]{1,2})\b", riga):
                if cod not in regole:
                    continue
                for fr, varianti in frequenze.items():
                    if fr in riga.lower() and not any(v in regole[cod] for v in varianti):
                        err("SC-16",
                            f"{cod} e' descritto con '{fr}...' ma la regola in config non la "
                            f"contiene: \"{regole[cod][:70]}...\"", f"{nome}:{n_riga}")
                        break


# ---------------------------------------------------------------- SC-17
def sc17_nomi_di_campo_inventati(cfg, prosa, schemi=None):
    """
    Un prompt che nomina un campo di input con un nome che il config non usa.

    L'orchestratore passa `icp_constraints` da `scope.company_size_target`, che dichiara
    `addetti_min`/`addetti_max` — e il prompt del pain-finder leggeva `employees_min`/`employees_max`,
    piu' un `revenue_min_eur` che in config non esiste. Tre shard ciechi fra loro l'hanno segnalato
    nel run 20260802-1759. SC-01 non lo vedeva perche' guarda i percorsi `config.x`, non gli
    identificatori nudi.
    """
    # Un identificatore e' legittimo se e' una chiave di config OPPURE una proprieta' di uno
    # schema: i prompt nominano sia i campi che RICEVONO sia quelli che PRODUCONO, e confonderli
    # fa sparare il controllo su meta' del vocabolario del repo. Sono inventati solo quelli che
    # non stanno ne' di qua ne' di la'.
    # Chiavi del config, VALORI delle sue liste (le dimensioni si dichiarano come stringhe in
    # blocking_dimensions, non come chiavi), proprieta' e valori di enum degli schemi.
    chiavi = {p.split(".")[-1] for p, _ in cammina(cfg)}
    for _, v in cammina(cfg):
        if isinstance(v, list):
            chiavi.update(x for x in v if isinstance(x, str))
        elif isinstance(v, str):
            chiavi.add(v)

    def proprieta(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "properties" and isinstance(v, dict):
                    chiavi.update(v.keys())
                if k == "enum" and isinstance(v, list):
                    chiavi.update(x for x in v if isinstance(x, str))
                proprieta(v)
        elif isinstance(o, list):
            for v in o:
                proprieta(v)

    for s in (schemi or {}).values():
        proprieta(s)
    sospetto = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)*_(?:min|max|eur|days|months|limit|"
                          r"total|ratio|rate|window))`")
    for nome, testo in prosa:
        if not nome.startswith(".claude"):
            continue
        for n_riga, riga in enumerate(testo.split("\n"), 1):
            if IGNORA in riga:
                continue
            candidati = set(sospetto.findall(riga))
            candidati |= {m for m in re.findall(
                r'"([a-z][a-z0-9]*(?:_[a-z0-9]+)*_(?:min|max|eur|days|months|limit|total|'
                r"ratio|rate|window))\"\s*:", riga)}
            for tok in candidati:
                if tok in chiavi:
                    continue
                err("SC-17",
                    f"`{tok}` ha la forma di un campo di configurazione e non esiste in config: "
                    "l'agente leggera' un campo che nessuno gli passa", f"{nome}:{n_riga}")


# ---------------------------------------------------------------- SC-18
def sc18_unita_di_budget(cfg, schemi):
    """
    Ogni unita' di consumo che il config assegna a un agente deve essere dichiarabile nell'envelope.

    La v2.5 ha introdotto le MIETITURE come terza unita' e non le ha messe in `budget_used`. Nel run
    20260802-1759 quattro shard ciechi fra loro hanno inventato quattro convenzioni private per
    dichiararle, i loro consumi non erano confrontabili, e su uno shard la lettura del conteggio ha
    deciso fra tre pain e zero. E' il difetto P0-1 del collaudo 20260801 — budget contato sull'unita'
    sbagliata — reintrodotto su una terza unita' dalla correzione che doveva chiuderlo.
    """
    bu = (((schemi.get("core.schema.json") or {}).get("$defs") or {}).get("Envelope") or {}) \
        .get("properties", {}).get("budget_used", {})
    dichiarabili = set(bu.get("properties") or {}) | set(bu.get("required") or [])
    for agente, b in ((cfg.get("budget") or {}).get("max_per_agent") or {}).items():
        if not isinstance(b, dict):
            continue
        for unita in b:
            if unita.startswith("_") or unita in dichiarabili:
                continue
            err("SC-18",
                f"config assegna a {agente} un budget di '{unita}' ma l'envelope non ha un campo "
                f"dove dichiararne il consumo: ogni agente ne inventera' uno suo",
                "core.schema.json budget_used")


# ---------------------------------------------------------------- SC-19
def sc19_modelli_dichiarati(cfg):
    """
    Ogni agente dichiara il proprio tier in config, e il frontmatter deve concordare.

    Il tier non e' una preferenza di costo: e' una decisione di progetto sull'asimmetria
    dell'errore. Lasciarlo solo nel frontmatter significa che cambia con un'edit di una riga e
    nessuno se ne accorge — e il modo in cui questa scelta si degrada e' sempre lo stesso: un
    giorno si guarda la bolletta e si abbassa quello che sembra il meno importante.
    """
    dichiarati = ((cfg.get("modelli") or {}).get("per_agente") or {})
    su_disco = {os.path.basename(p)[:-3]: leggi(p)
                for p in sorted(glob.glob(os.path.join(AGENTI, "*.md")))}
    ammessi = {"haiku", "sonnet", "opus", "inherit"}
    for a in sorted(su_disco):
        if a not in dichiarati:
            err("SC-19", f"agente senza tier dichiarato in config.modelli.per_agente: {a}",
                "config.modelli"); continue
        atteso = (dichiarati[a] or {}).get("modello")
        if atteso not in ammessi:
            err("SC-19", f"{a}: tier '{atteso}' non ammesso ({', '.join(sorted(ammessi))})",
                "config.modelli"); continue
        if not (dichiarati[a] or {}).get("perche"):
            err("SC-19", f"{a}: tier dichiarato senza motivazione. Un tier senza un perche' "
                         "e' una preferenza di costo travestita da decisione di progetto",
                "config.modelli")
        m = re.search(r"^model:\s*(\S+)\s*$", su_disco[a], re.M)
        if not m:
            err("SC-19", f"{a}.md non dichiara 'model:' nel frontmatter: eredita quello della "
                         "sessione, cioe' il piu' caro", a + ".md")
        elif m.group(1) != atteso:
            err("SC-19", f"{a}: frontmatter dice '{m.group(1)}', config dice '{atteso}'",
                a + ".md")
    for a in sorted(set(dichiarati) - set(su_disco)):
        err("SC-19", f"tier dichiarato per un agente inesistente: {a}", "config.modelli")


# ---------------------------------------------------------------- SC-20
def sc20_delega_esterna(cfg):
    """
    Nessun agente di giudizio, avversariale o generativo puo' essere delegato a un provider esterno.

    E' l'invariante che protegge la delega da se stessa. Delegare l'AVVERSARIO al modello piu'
    economico e' il risparmio piu' dannoso possibile in questo sistema, e non produce un errore
    visibile: produce un PASS. L'unico modo di accorgersene e' costruire l'idea e fallire — cioe'
    fuori dalla pipeline, con i propri soldi. Fra sei mesi, guardando la bolletta, sembrera' una
    buona idea: per questo il divieto sta in un controllo e non in un paragrafo.
    """
    prov = cfg.get("provider") or {}
    contratto = (cfg.get("verifica_specifica") or {}).get("agenti") or {}
    delegabili = set(prov.get("delegabili") or [])
    if not prov:
        return
    # L'equivalenza delegabile <=> (tier haiku E chiuso sul proprio input), nei due sensi. Una
    # lista mantenuta a mano diverge dalla regola che dice di seguire al primo cambiamento; una
    # derivata non puo'.
    #
    # La seconda condizione e' arrivata dopo, e la sua storia e' il motivo per cui esiste questo
    # controllo. Fino alla v2.13 la regola era la sola prima, e sotto quella regola il
    # source-verifier risultava delegabile: haiku a pieno titolo, il suo lavoro E' confronto di
    # stringa. Ma il confronto ha due termini e il secondo — la pagina — non entra dal prompt, e
    # il ponte esterno non naviga. Delegato, confrontava lo snippet con se stesso e diceva sempre
    # di si'. Nessun controllo lo vedeva perche' tutti guardavano l'asse del COSTO dell'errore, e
    # questo e' un difetto sull'asse della RAGGIUNGIBILITA' dell'oggetto da verificare.
    tier = ((cfg.get("modelli") or {}).get("per_agente") or {})
    haiku = {a for a, v in tier.items() if (v or {}).get("modello") == "haiku"}
    chiusura = prov.get("chiuso_sul_proprio_input") or {}
    non_chiusi = {a for a, v in chiusura.items()
                  if isinstance(v, dict) and v.get("chiuso") is False}
    for a in sorted(non_chiusi):
        if not (chiusura[a].get("perche") or "").strip():
            err("SC-20", f"{a} e' dichiarato non chiuso sul proprio input senza motivazione: una "
                         "riga senza il perche' e' una riga che il prossimo turno cancella",
                "config.provider.chiuso_sul_proprio_input")
    if tier:
        ammessi = haiku - non_chiusi
        for a in sorted(delegabili - haiku):
            err("SC-20", f"{a} e' delegabile ma il suo tier e' "
                         f"'{(tier.get(a) or {}).get('modello')}': la delegabilita' segue il tier, "
                         "perche' rispondono alla stessa domanda (l'errore e' recuperabile?)",
                "config.provider.delegabili")
        for a in sorted(delegabili & non_chiusi):
            err("SC-20", f"{a} e' delegabile ma non e' chiuso sul proprio input: il ponte esterno "
                         "non naviga, quindi confronterebbe cio' che ha ricevuto con se stesso e "
                         "direbbe sempre di si'", "config.provider.delegabili")
        for a in sorted(ammessi - delegabili):
            err("SC-20", f"{a} ha tier haiku ed e' chiuso sul proprio input ma non e' delegabile: "
                         "la lista si e' staccata dalla regola che dichiara di seguire",
                "config.provider.delegabili")
    vietati = set()
    for a in sorted(delegabili & vietati):
        motivo = ("e' di solo giudizio: non ha strumenti di ricerca, quindi delegarlo non "
                  "risparmia nulla e rompe la separazione dei poteri"
                  if a in set(contratto.get("solo_giudizio") or []) else
                  "e' l'avversario o il generatore: delegarlo sposta il costo a valle e lo "
                  "moltiplica, senza produrre un errore visibile")
        err("SC-20", f"{a} e' marcato delegabile a un provider esterno, ma {motivo}",
            "config.provider.delegabili")
    su_disco = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(AGENTI, "*.md"))}
    for a in sorted(delegabili - su_disco):
        err("SC-20", f"delegabile inesistente: {a}", "config.provider.delegabili")

    # La mappa ATTIVA e' cio' che il run legge davvero. L'elenco dei delegabili e' un permesso;
    # questa e' l'assegnazione, ed e' quella che puo' divergere: e' il punto in cui un interruttore
    # frettoloso metterebbe l'avversario sul modello piu' economico.
    for a, p_ in sorted((prov.get("attivo_per_agente") or {}).items()):
        if a not in su_disco:
            err("SC-20", f"provider assegnato a un agente inesistente: {a}",
                "config.provider.attivo_per_agente")
        elif p_ != "anthropic" and a not in delegabili:
            err("SC-20", f"{a} gira su '{p_}' ma non e' fra i delegabili: la mappa attiva ha "
                         "scavalcato il permesso", "config.provider.attivo_per_agente")
        elif p_ not in (prov.get("disponibili") or []):
            err("SC-20", f"{a} assegnato a un provider non dichiarato: {p_}",
                "config.provider.attivo_per_agente")
    for nome in (prov.get("disponibili") or []):
        if nome == "anthropic":
            continue
        b = prov.get(nome)
        if not isinstance(b, dict) or not b.get("env_chiave"):
            err("SC-20", f"provider '{nome}' dichiarato senza blocco di configurazione "
                         "(serve almeno env_chiave)", "config.provider")
    # il ponte non deve mai nominare le variabili del provider di sessione
    ponte = os.path.join(ROOT, "tools", "ds_call.py")
    if os.path.exists(ponte):
        tp = leggi(ponte)
        for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            if re.search(rf"environ.*{v}|{v}.*environ", tp):
                err("SC-20", f"tools/ds_call.py accede a {v}: il ponte esterno non deve toccare "
                             "il provider di questa sessione", "ds_call.py")


# ---------------------------------------------------------------- SC-21
def sc21_nessun_segreto_nel_repo():
    """
    Nessuna stringa che sembra una chiave API deve stare in un file del repo che non sia `.env`.

    Non e' paranoia astratta: `config/deepsearch.config.json` e' finito in dieci zip nel corso di
    questo progetto, ed e' allegato a ogni revisione esterna. Una chiave li' dentro e' una chiave
    pubblicata, e ce ne si accorge dall'estratto conto. Il controllo guarda anche gli artefatti dei
    run, perche' un prompt salvato su disco puo' portarsi dietro tutto quello che gli e' stato dato
    in pasto.
    """
    rx = re.compile(r"\b(?:sk|api[_-]?key|token)[-_][A-Za-z0-9_\-]{20,}", re.I)
    esclusi = {".env"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for nome in files:
            if nome in esclusi or nome.endswith((".zip", ".png", ".svg", ".pdf")):
                continue
            p = os.path.join(base, nome)
            try:
                testo = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for m in rx.finditer(testo):
                riga = testo[:m.start()].count("\n") + 1
                err("SC-21", f"stringa che sembra una chiave API: {m.group(0)[:14]}… — le chiavi "
                             "stanno solo in .env, che e' nel .gitignore",
                    f"{os.path.relpath(p, ROOT)}:{riga}")
    gi = os.path.join(ROOT, ".gitignore")
    if not os.path.exists(gi) or ".env" not in leggi(gi):
        err("SC-21", ".env non e' nel .gitignore: la prima chiave impostata verrebbe committata",
            ".gitignore")


# ---------------------------------------------------------------- SC-22
def sc22_una_sola_ricerca_della_chiave():
    """
    La domanda "questa chiave e' impostata?" deve avere UNA risposta sola.

    Era scritta in tre strumenti con tre risposte diverse: ds_call guardava ambiente e .env,
    setup guardava entrambi, provider_switch solo l'ambiente. Chi impostava la chiave con
    `setup.py --chiave` — che scrive in .env — si sentiva dire da provider_switch che non c'era.
    E' la stessa classe di difetto che spec_check insegue fra config, schemi e prompt, comparsa
    fra gli STRUMENTI, dove nessun controllo guardava.
    """
    helper = os.path.join(ROOT, "tools", "_chiavi.py")
    if not os.path.exists(helper):
        err("SC-22", "tools/_chiavi.py assente: la ricerca della chiave torna duplicata",
            "tools/"); return
    for p in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py"))):
        nome = os.path.basename(p)
        if nome == "_chiavi.py":
            continue
        t_ = leggi(p)
        if "env_chiave" not in t_ and "API_KEY" not in t_:
            continue
        if "from _chiavi import" not in t_:
            err("SC-22", f"{nome} tratta chiavi ma non usa _chiavi.trova_chiave: avra' una "
                         "propria opinione su dove cercarle, e divergera'", f"tools/{nome}")
        for m in re.finditer(r"environ\.get\(\s*(env_chiave|var|chiave|nome)\b", t_):
            err("SC-22", f"{nome} legge una chiave direttamente da os.environ invece che da "
                         "trova_chiave: e' il ramo che diverge",
                f"tools/{nome}:{t_[:m.start()].count(chr(10)) + 1}")


# ---------------------------------------------------------------- SC-23
def sc23_ponte_senza_navigazione():
    """
    Il ponte esterno non naviga: deve rifiutare gli URL che non erano nel proprio input.

    `ds_call.py` costruisce un payload senza campo `tools`, quindi l'endpoint non apre pagine.
    Tutti gli agenti delegabili sono di RACCOLTA: delegarli senza questo rifiuto significa
    sostituire la ricerca con la memoria parametrica, e nessuno degli altri controlli lo vede —
    un URL fabbricato supera sia il controllo sugli ID di ledger sia quello sul numero senza
    fonte, perche' la fonte c'e', semplicemente non esiste.
    """
    p = os.path.join(ROOT, "tools", "ds_call.py")
    if not os.path.exists(p):
        return
    t_ = leggi(p)
    if '"tools"' in t_ and "tools_non_supportati" not in t_:
        avv("SC-23", "ds_call.py sembra passare 'tools' all'endpoint: se ora naviga davvero, il "
                     "rifiuto sulla provenienza degli URL va ripensato", "tools/ds_call.py")
    if "RX_URL" not in t_ or "non presenti nel prompt" not in t_:
        err("SC-23", "ds_call.py non rifiuta gli URL assenti dal prompt: un agente di raccolta "
                     "delegato produrrebbe fonti ricordate invece che trovate, e nessun altro "
                     "controllo se ne accorgerebbe", "tools/ds_call.py")
    orch = leggi(ORCH)
    if "Un agente delegato NON cerca" not in orch:
        err("SC-23", "§0 non prescrive che sia l'orchestratore a cercare per gli agenti delegati: "
                     "senza quel passo ogni chiamata esterna verrebbe rifiutata e la delega "
                     "sarebbe inservibile", "deepsearch.md §0")


# ---------------------------------------------------------------- SC-24
# I motivi per cui una fonte e' irraggiungibile SI DIVIDONO IN DUE, e la divisione non e'
# cosmetica: sette dicono che qualcuno ha bussato e la porta era chiusa (la fonte esiste, ha un
# URL, si identifica), due dicono che nessuno ha bussato (non c'e' nessuna fonte da identificare).
RIFIUTI = ("PAYWALL", "BLOCKED", "NOT_FOUND", "ROBOTS", "API_LIMIT",
           "LOGIN_REQUIRED", "NO_PUBLIC_DATA")


def sc24_le_due_meta_di_ic4(schemi):
    """
    Un obbligo enunciato in un invariante deve avere, da qualche parte, lo slot per assolverlo.

    IC-4 ordina di registrare ogni fonte irraggiungibile "con il proprio access_status"; IC-1
    pretende che ogni citazione risolva nel ledger. Per undici versioni `UnreachableSource` non
    ha avuto un identificatore e tre dei sette motivi di rifiuto non erano scrivibili in
    `access_status`: la dichiarazione imposta da un invariante era irricevibile per l'altro, e il
    run veniva marcato DEGRADATO per aver fatto la cosa giusta. Nessun controllo poteva vederlo,
    perche' ciascuno dei due schemi era internamente coerente — si contraddicevano solo NELLA
    PROSA che li lega, ed e' esattamente il perimetro di questo strumento.

    Terza forma della stessa classe gia' vista in competitor STRONG, gate 3 G7 e fact_promotion
    FP0: un'enumerazione che non copre il proprio spazio. Qui lo spazio non e' quello dei rami ma
    quello dei valori: il campo c'era, i valori ammessi no.
    """
    core = schemi.get("core.schema.json") or {}
    d = core.get("$defs") or {}
    U, S = d.get("UnreachableSource") or {}, d.get("Source") or {}
    if not U or not S:
        return
    if "source_id" not in (U.get("properties") or {}):
        err("SC-24", "UnreachableSource non ha source_id: IC-4 impone di dichiarare una fonte che "
                     "IC-1 non puo' risolvere, e il run risulta degradato per averla dichiarata",
            "schemas/core.schema.json")
    ammessi = set((S.get("properties", {}).get("access_status") or {}).get("enum") or [])
    perche = set((U.get("properties", {}).get("why") or {}).get("enum") or [])
    mancanti = sorted((perche & set(RIFIUTI)) - ammessi)
    if mancanti:
        err("SC-24", f"motivi di rifiuto non scrivibili in Source.access_status: {', '.join(mancanti)}"
                     " — IC-4 chiede di registrare la fonte col proprio access_status e per questi "
                     "motivi quel valore non esiste", "schemas/core.schema.json")
    cond = json.dumps(U.get("allOf") or [], ensure_ascii=False)
    if "source_id" not in cond or not all(r in cond for r in RIFIUTI):
        err("SC-24", "source_id non e' richiesto condizionatamente sui motivi di rifiuto: o e' "
                     "obbligatorio anche quando nessuno ha tentato (e allora si inventa un ID per "
                     "una fonte mai vista) o e' facoltativo sempre (e allora IC-1 torna a non "
                     "risolvere)", "schemas/core.schema.json")

    # Le due meta' devono toccarsi anche negli STRUMENTI, o la riparazione vale un run solo.
    for nome, obbligo in (("reconcile_run.py", "unreachable_sources"),
                          ("run_close.py", "unreachable_sources")):
        p = os.path.join(ROOT, "tools", nome)
        if not os.path.exists(p):
            continue
        t_ = leggi(p)
        if obbligo not in t_:
            err("SC-24", f"{nome} non legge unreachable_sources: conta come orfana IC-1 una fonte "
                         "che IC-4 obbliga a dichiarare", f"tools/{nome}")
        # Il contrappeso. Senza, la riparazione compra un falso negativo col falso positivo:
        # le irraggiungibili entrano nel ledger e da sole sosterrebbero un VERIFIED su una
        # pagina che nessuno ha aperto — dove lo snippet preteso da IC-3 non puo' esistere.
        if "VERIFIED" not in t_:
            err("SC-24", f"{nome} riconosce le irraggiungibili come provenienza ma non verifica "
                         "che nessun VERIFIED poggi solo su di esse: e' il falso negativo comprato "
                         "col falso positivo", f"tools/{nome}")


def _somma_teorica(cfg, prof):
    """
    La somma dei budget per istanza lungo il funnel dichiarato, nelle due unita'.
    La formula sta anche in config.funnel._formula_dei_tetti: se cambia la topologia
    delle wave va cambiata in entrambi i posti, ed e' voluto — sono la stessa
    affermazione scritta per due lettori diversi, e SC-25 esiste perche' finora
    l'unica scritta era quella che nessuno rileggeva.
    """
    f = (cfg.get("funnel") or {}).get(prof) or {}
    b = ((cfg.get("budget") or {}).get("max_per_agent")) or {}
    if not f or not b:
        return None, None

    def g(agente, unita):
        v = (b.get(agente) or {}).get(unita)
        return v if isinstance(v, int) else 0

    ev = f.get("evidence_searches_per_channel") or 0
    pairs = (cfg.get("sources") or {}).get("verifier_max_pairs_per_instance") or 0
    quattro_s = sum(g(a, "searches") for a in
                    ("competitor-analyst", "trigger-finder", "tam-analyst", "red-team"))
    quattro_f = sum(g(a, "fetches") for a in
                    ("competitor-analyst", "trigger-finder", "tam-analyst", "red-team"))
    s = (f.get("shards", 0) * g("pain-finder", "searches")
         + f.get("pains_to_evidence", 0) * 5 * ev
         + f.get("pains_to_competitor", 0) * quattro_s
         + f.get("ideas_to_architect", 0) * 3 * g("red-team", "searches")
         + f.get("ideas_to_architect", 0) * 2 * g("source-verifier", "searches"))
    ft = (f.get("shards", 0) * g("pain-finder", "fetches")
          + f.get("pains_to_evidence", 0) * 5 * ev * 2
          + f.get("pains_to_competitor", 0) * quattro_f
          + f.get("ideas_to_architect", 0) * 3 * g("red-team", "fetches")
          + f.get("ideas_to_architect", 0) * 2 * pairs)
    return s, ft


def sc25_tetti_contro_budget(cfg):
    """
    Il tetto di un profilo deve stare sopra la somma dei budget per istanza DIVISA per
    la soglia del freno, non sopra la somma.

    `funnel._nota_budget` enuncia l'invariante e ne descrive la conseguenza — "la pipeline
    si interrompe a meta' e produce un report vuoto che sembra un esito legittimo: e' il
    guasto piu' insidioso possibile" — e nessun controllo lo verificava. `SC-11` fa
    aritmetica di funnel ma confronta gli obiettivi di generazione con gli intervalli di ID,
    mai i tetti con i budget che li consumano.

    Il difetto S5, dichiarato chiuso in v2.1, e' stato riaperto dalla v2.14: portare
    `evidence_searches_per_channel` da 3-4 a 8 ha aggiunto 960 ricerche e 1920 fetch al solo
    stage evidenze in DEEP, ed e' stato alzato un tetto su sei. Stato al momento della
    scoperta: ricerche 438/300, 860/700, 1624/1600; fetch 798/500, 1556/1000, 2932/1800.
    Sette violazioni su sei tetti, e spec_check usciva 0 errori.

    Il divisore e' la parte che non si vede a occhio: il freno all'80% smette di aprire
    nuove idee al gate 3, quindi il budget disponibile per percorrere il funnel dichiarato
    e' 0.8 x tetto. Con tetto == somma il freno scatta a meta' wave 3, su OGNI run, per
    costruzione — ed e' il modo in cui DEEP restava rotto pur avendo il tetto formalmente
    sopra la somma.
    """
    warn = (cfg.get("budget") or {}).get("warn_at_budget_pct")
    if not isinstance(warn, (int, float)) or not 0 < warn <= 1:
        err("SC-25", "budget.warn_at_budget_pct assente o fuori (0,1]: il margine dei tetti non "
                     "e' calcolabile", "config.budget")
        return
    for prof in ("LIGHT", "BALANCED", "DEEP"):
        f = (cfg.get("funnel") or {}).get(prof) or {}
        s, ft = _somma_teorica(cfg, prof)
        if s is None:
            continue
        for unita, somma, campo in (("ricerche", s, "max_searches_total"),
                                    ("fetch", ft, "max_fetches_total")):
            tetto = f.get(campo)
            if not isinstance(tetto, int):
                err("SC-25", f"{prof}.{campo} assente: un funnel senza tetto non ha una regola "
                             "di arresto, ha una bolletta", "config.funnel")
                continue
            minimo = int(math.ceil(somma / warn))
            if tetto < somma:
                err("SC-25", f"{prof}: il tetto di {unita} ({tetto}) e' SOTTO la somma dei budget "
                             f"per istanza ({somma}). La pipeline si ferma a meta' funnel e il "
                             "report vuoto e' indistinguibile dall'esito legittimo — il guasto che "
                             "funnel._nota_budget chiama il piu' insidioso possibile",
                    f"config.funnel.{prof}.{campo}")
            elif tetto < minimo:
                err("SC-25", f"{prof}: il tetto di {unita} ({tetto}) sta sopra la somma ({somma}) "
                             f"ma sotto somma/warn_at_budget_pct ({minimo}): il freno al "
                             f"{int(warn*100)}% scatta prima della fine del funnel dichiarato, su "
                             "ogni run", f"config.funnel.{prof}.{campo}")
    # La riga che descrive le somme a parole deve dire gli stessi numeri, o torna a scollarsi
    # in silenzio: e' proprio cosi' che i tetti sono finiti sotto la somma.
    dichiarato = (cfg.get("budget") or {}).get("_calcolo_teorico") or ""
    for prof in ("LIGHT", "BALANCED", "DEEP"):
        s, ft = _somma_teorica(cfg, prof)
        if s and str(s) not in dichiarato:
            avv("SC-25", f"budget._calcolo_teorico non riporta la somma reale di {prof} ({s} "
                         "ricerche): la nota descrive una configurazione passata",
                "config.budget._calcolo_teorico")


def sc26_emettibilita_dei_kill(cfg, prosa, schemi):
    """
    Quale agente possa uccidere e' il confronto fra due numeri del config. Ogni frase che lo
    dichiari a memoria e' una fotografia, e si scolla alla prima modifica di un budget.

    Successo fra la v2.14 e la v2.14.1: `evidence_searches_per_channel` e' stato portato a 8
    PROPRIO per riabilitare i KILL del gate 2 (funnel._nota_gate2 lo dice), e sono rimaste in
    piedi tre frasi che dichiaravano l'inemettibilita' come un fatto permanente — in
    stop_rules._conseguenza_gate_2, in stop_rules._come_riabilitare_gate_2 e nella regola di
    arresto 3 dell'orchestratore. Il run 20260803-1204 ha applicato la frase invece del calcolo.
    Un sistema che si toglie un gate senza accorgersene e' peggio di un sistema senza quel gate.

    Il controllo ha due meta'. La prima ricalcola l'emettibilita'. La seconda cerca nei file
    ogni numero dichiarato come "N ricerche per canale" e lo confronta con il config: e' la
    forma in cui l'affermazione stantia sopravvive, perche' cita un numero che era vero.
    """
    veri = {}
    for prof in ("LIGHT", "BALANCED", "DEEP"):
        ev = ((cfg.get("funnel") or {}).get(prof) or {}).get("evidence_searches_per_channel")
        if not isinstance(ev, int):
            continue
        finestra = max(2, ev // 3)
        veri[prof] = (ev >= 3 * finestra, ev, finestra)

    if veri and all(v[0] for v in veri.values()):
        # I kill sono emettibili ovunque: nessun file puo' dichiarare il contrario citando
        # un budget diverso da quello reale.
        reali = {v[1] for v in veri.values()}
        testi = dict(prosa)
        testi["config/deepsearch.config.json"] = leggi(os.path.join(ROOT, "config",
                                                                    "deepsearch.config.json"))
        for nome, t_ in testi.items():
            for m in re.finditer(r"(\d+)(?:\s*-\s*(\d+))?\s+ricerche per canale", t_ or ""):
                citati = {int(x) for x in m.groups() if x}
                riga = (t_[:m.start()].count("\n")) + 1
                # L'esenzione per riga vale anche qui. Senza, il primo verbale che CITA la frase
                # rimossa fa fallire il run: un controllo che non distingue l'affermazione dalla
                # citazione viene spento entro la settimana, ed e' il modo in cui SC-03 era
                # quasi morto nella v2.8.1.
                if IGNORA in (t_.splitlines()[riga - 1] if riga <= len(t_.splitlines()) else ""):
                    continue
                if citati and not (citati & reali):
                    err("SC-26", f"dichiara «{m.group(0)}» mentre config concede "
                                 f"{sorted(reali)}: e' la frase da cui discende "
                                 "l'inemettibilita' dei KILL del gate 2, e con il budget reale "
                                 "quei kill SONO emettibili", f"{nome}:{riga}")

    for prof, (ok, ev, w) in veri.items():
        if not ok:
            avv("SC-26", f"{prof}: evidence-researcher ha {ev} ricerche contro un minimo di "
                         f"{3*w} (3 x finestra {w}): in questo profilo G1 e G3 non sono "
                         "emettibili e diventano PARK d'ufficio. Legittimo, ma va scritto nel "
                         "manifesto del run, non ricordato", f"config.funnel.{prof}")

    # Gli slot che rendono leggibile un no devono esistere ed essere referenziati e letti.
    core = (schemi.get("core.schema.json") or {}).get("$defs") or {}
    if "KillAnalysis" not in core:
        err("SC-26", "core.schema.json non definisce KillAnalysis: kill_basis e kill_attribution "
                     "sono prescritti dall'orchestratore e citati da "
                     "config.run.condizione_di_riprogettazione, e senza uno slot tipizzato "
                     "finiscono in file inventati che nessun programma rilegge",
            "schemas/core.schema.json")
    else:
        riferito = [n for n, s_ in schemi.items()
                    if "KillAnalysis" in json.dumps(s_, ensure_ascii=False) and n != "core.schema.json"]
        if not riferito:
            err("SC-26", "KillAnalysis e' definito e nessuno schema lo referenzia: un tipo che "
                         "nessun artefatto porta non e' un contratto", "schemas/")
    rc = leggi(os.path.join(ROOT, "tools", "run_close.py"))
    if "kill_attribution" not in rc:
        err("SC-26", "run_close.py non legge kill_attribution: config.run."
                     "condizione_di_riprogettazione promette di decidere sulla sua distribuzione "
                     "su 5 run, e nessun programma la calcola", "tools/run_close.py")


def sc27_kill_automatici_hanno_una_severity(cfg, prosa, schemi):
    """
    Un kill criterion che si dichiara 'automaticamente MET' e non dichiara una severity
    condizionale e' un KILL HARD automatico, cioe' un ramo del gate che si attiva da solo.

    K5 dichiara: 'Se il competitor E' il sistema di record, K5 e' automaticamente MET', e
    definisce sistema di record anche 'il portale pubblico obbligatorio quando esiste'. Senza
    una regola di severity, per QUALUNQUE pain della forma 'adempimento telematico obbligatorio
    su portale pubblico' quel portale e' sempre presente, offre sempre uno strumento gratuito,
    e' sempre un DIRETTO e ha sempre K5 MET: il gate 3 uccide al ramo 1, sempre, per costruzione.
    E il ramo 9 — DISALLINEAMENTO, che config.missione chiama meta' della missione — non viene
    mai raggiunto, perche' il ramo 1 lo precede.

    Ma 'adempimenti normativi ricorrenti con sanzione' e' l'angolo NUMERO 1 degli otto assegnati
    al pain-finder e la firma numero 1 del metodo follow-the-money: il gate uccideva
    strutturalmente il terreno di caccia che la pipeline si prescrive.

    Trovato dal controllo positivo CP-001 e gia' accaduto su dati reali: nel collaudo 20260801
    P-013 e' morto cosi', e il verbale lo registra fra le cose che hanno FUNZIONATO. Un falso
    negativo di questa classe si presenta come un successo, quindi serve un controllo: non lo
    trovera' mai nessuno rileggendo.
    """
    testi = " ".join(t for _, t in (prosa or []))
    for prof in ("STRICT", "CALIBRATED"):
        for cr in ((cfg.get("competitor_thresholds") or {}).get(prof) or {}).get("kill_if_any") or []:
            blob = json.dumps(cr, ensure_ascii=False)
            if "automaticamente MET" not in blob:
                continue
            cid = cr.get("id", "?")
            sev = " ".join(str(v) for k, v in cr.items() if "severity" in k.lower())
            if "SOFT" not in sev:
                err("SC-27", f"{cid} ({prof}) si dichiara 'automaticamente MET' senza una regola "
                             "di severity che ammetta SOFT: e' un KILL HARD che si attiva da solo, "
                             "e con esso il ramo 1 del gate 3 preclude per sempre il ramo 9 "
                             "(DISALLINEAMENTO), che config.missione chiama meta' della missione",
                    f"config.competitor_thresholds.{prof}.kill_if_any[{cid}]")
                continue
            # La regola deve vivere anche dove viene APPLICATA, o vale un run solo — e deve
            # viverci come SLOT TIPIZZATO, non come frase. La prima stesura di questo controllo
            # cercava 'K5' vicino a 'SOFT' nella prosa: passava pulita anche cancellando la
            # deroga, perche' quelle due parole convivono in mezza pagina per altri motivi.
            # Un controllo che sbaglia cosi' e' decorativo, e lo ha dimostrato l'autotest.
            enum_atteso = "E_IL_SISTEMA_DI_RECORD"
            comp = (schemi or {}).get("competitor-report.schema.json") or {}

            def enum_severity_reason(nodo):
                """Cerca l'enum vero, non la stringa nel blob serializzato.

                La prima stesura faceva `enum_atteso in json.dumps(schema)`: toglierlo
                dall'enum lasciandolo nella description faceva passare il controllo, perche'
                avevo scritto il nome del valore dentro la sua stessa spiegazione. Un
                verificatore che si lascia soddisfare dalla documentazione di cio' che
                verifica e' la forma piu' pura del controllo decorativo.
                """
                if isinstance(nodo, dict):
                    sr = nodo.get("severity_reason")
                    if isinstance(sr, dict) and isinstance(sr.get("enum"), list):
                        yield sr["enum"]
                    for v in nodo.values():
                        yield from enum_severity_reason(v)
                elif isinstance(nodo, list):
                    for v in nodo:
                        yield from enum_severity_reason(v)

            enums = list(enum_severity_reason(comp))
            if not enums or not any(enum_atteso in e for e in enums):
                err("SC-27", f"{cid} e' derogabile in config e competitor-report.schema.json non "
                             f"ammette '{enum_atteso}' fra le severity_reason: l'agente non ha uno "
                             "slot per dichiarare PERCHE' il kill e' SOFT, quindi la deroga non e' "
                             "scrivibile in un artefatto e vale solo nella prosa",
                    "schemas/competitor-report.schema.json")
            if not re.search(rf"{re.escape(cid)}[^.]{{0,400}}SOFT|SOFT[^.]{{0,400}}{re.escape(cid)}",
                             testi, re.S):
                err("SC-27", f"{cid} ha una regola di severity condizionale in config e nessun "
                             "prompt la nomina: l'agente che classifica i competitor non la "
                             "applicherebbe, e la deroga varrebbe solo sulla carta",
                    ".claude/agents/")


def sc28_quanti_controlli_dice_di_avere(prosa):
    """
    Il verificatore verifica tutti tranne il numero che lo descrive.

    E' la stessa forma di SC-09 — "N rami dichiarati contro le righe elencate" — applicata a
    questo strumento. Un numero scritto a mano in prosa diverge dal file che dovrebbe contare, e
    nessuno se ne accorge perche' ogni file preso da solo e' coerente: e' la tesi dell'intero
    progetto, e valeva anche qui.

    Trovato pubblicando la pagina di riferimento: tre documenti e due immagini dichiaravano
    "27 controlli" mentre il registro ne conteneva 28. L'errore nasce sommando i nuovi a una
    base contata male (SC-01..SC-24 invece di SC-00..SC-24) — cioe' esattamente il modo in cui
    un'enumerazione che parte da zero viene contata da chi la legge invece che da chi la esegue.

    I documenti storici sono gia' fuori perimetro (normativi() li esclude), quindi qui il
    numero deve COINCIDERE. La prima stesura ammetteva qualunque valore piu' basso come
    "storico": con quella lettura una pagina pubblica ferma a una versione vecchia sarebbe
    passata per sempre, ed e' il difetto che questo controllo esiste per impedire.
    """
    reali = len(CONTROLLI)
    for nome, t_ in (prosa or []):
        for m in re.finditer(r"\b(\d{1,3})\s+controlli\b", t_ or ""):
            n = int(m.group(1))
            riga = t_[:m.start()].count("\n") + 1
            linea = t_.splitlines()[riga - 1] if riga <= len(t_.splitlines()) else ""
            if IGNORA in linea:
                continue
            if n != reali:
                err("SC-28", f"dichiara «{m.group(0)}» e il registro ne contiene {reali}. "
                             "I file storici sono gia' esclusi da normativi(): qui il numero "
                             "descrive il sistema di oggi e deve coincidere",
                    f"{nome}:{riga}")


# ---------------------------------------------------------------- SC-29
def sc29_clausole_dell_orchestratore(cfg):
    """
    Una determinazione che vive solo in prosa non e' falsificabile da nessuno.

    Il primo canary reale ha potuto comporre lo schema di una radice col prompt di un'altra. La
    riparazione — dichiarare la radice canonica, registrare percorso e sha256 di cio' che viene
    caricato, fermarsi prima di scrivere su un mismatch — e' per sua natura una frase nel prompt
    dell'orchestratore. Se nessun controllo la legge, cancellarla non produce alcun segnale, e
    fra sei mesi nessuno sapra' se sia mai stata scritta.

    Questo controllo verifica che la clausola ESISTA DOVE DEVE. Non verifica che un run la
    rispetti: quello nessun controllo statico puo' farlo, e dirlo qui e' preferibile a lasciarlo
    intendere. Il confronto normalizza gli spazi, cosi' che riformattare un paragrafo non
    spenga il controllo — che e' il modo in cui i controlli su prosa muoiono davvero.
    """
    def norma(s):
        return re.sub(r"\s+", " ", s).strip()

    for voce in ((cfg.get("verifica_specifica") or {}).get("clausole_orchestratore") or []):
        try:
            file_, clausola = voce["file"], voce["clausola"]
        except (KeyError, TypeError):
            err("SC-29", "voce malformata: servono 'file' e 'clausola'",
                "config.verifica_specifica"); continue
        p = os.path.join(ROOT, *file_.split("/"))
        if not os.path.exists(p):
            err("SC-29", f"file inesistente: {file_}", "config.verifica_specifica"); continue
        if norma(clausola) not in norma(leggi(p)):
            err("SC-29",
                f"la clausola «{clausola}» non compare in {file_}: la determinazione che la "
                "impone resterebbe senza alcun luogo in cui e' scritta", file_)


CONTROLLI = {
    "SC-00": ("integrita' del contratto di verifica", lambda c, s, p: sc00_integrita_del_contratto(c)),
    "SC-01": ("riferimenti a config esistenti", lambda c, s, p: sc01_riferimenti_config(c, normativi(p))),
    "SC-02": ("parametri di config effettivamente letti", lambda c, s, p: sc02_parametri_orfani(c, p)),
    "SC-03": ("costanti rispecchiate nei prompt", lambda c, s, p: sc03_costanti_rispecchiate(c, normativi(p))),
    "SC-04": ("pattern di ID coerenti fra schemi", lambda c, s, p: sc04_pattern_coerenti(s)),
    "SC-05": ("enum degli schemi vs token dei prompt", lambda c, s, p: sc05_enum_vs_prompt(s, normativi(p))),
    "SC-06": ("criteri dichiarati e criteri citati", lambda c, s, p: sc06_criteri_dichiarati(c, normativi(p), s)),
    "SC-07": ("separazione dei poteri negli agenti", lambda c, s, p: sc07_separazione_dei_poteri(c)),
    "SC-08": ("artefatti di chiusura e ordine delle fasi", lambda c, s, p: sc08_artefatti_di_chiusura()),
    "SC-09": ("rami dichiarati == rami elencati", lambda c, s, p: sc09_rami_enumerati(normativi(p))),
    "SC-10": ("$ref degli schemi risolvibili", lambda c, s, p: sc10_riferimenti_schemi(s)),
    "SC-11": ("aritmetica di funnel e scoring", lambda c, s, p: sc11_aritmetica_del_funnel(c)),
    "SC-12": ("inventario degli agenti", lambda c, s, p: sc12_agenti_citati(c)),
    "SC-13": ("documenti esplicativi marcati come derivati", lambda c, s, p: sc13_documenti_derivati()),
    "SC-14": ("liste del config vs enum degli schemi", lambda c, s, p: sc14_liste_vs_enum(c, s)),
    "SC-15": ("campi required degli schemi vs prompt", lambda c, s, p: sc15_schema_verso_prompt(c, s)),
    "SC-16": ("regole dei criteri: config vs prompt", lambda c, s, p: sc16_regole_dei_criteri(c, normativi(p))),
    "SC-17": ("nomi di campo che nessuno definisce", lambda c, s, p: sc17_nomi_di_campo_inventati(c, normativi(p), s)),
    "SC-18": ("unita' di budget dichiarabili nell'envelope", lambda c, s, p: sc18_unita_di_budget(c, s)),
    "SC-19": ("tier del modello dichiarato e coerente", lambda c, s, p: sc19_modelli_dichiarati(c)),
    "SC-20": ("delega esterna solo a stage di raccolta", lambda c, s, p: sc20_delega_esterna(c)),
    "SC-21": ("nessun segreto fuori da .env", lambda c, s, p: sc21_nessun_segreto_nel_repo()),
    "SC-22": ("una sola ricerca della chiave", lambda c, s, p: sc22_una_sola_ricerca_della_chiave()),
    "SC-23": ("il ponte esterno non finge di navigare", lambda c, s, p: sc23_ponte_senza_navigazione()),
    "SC-24": ("le due meta' di IC-4 si toccano", lambda c, s, p: sc24_le_due_meta_di_ic4(s)),
    "SC-25": ("tetti di budget contro budget per istanza", lambda c, s, p: sc25_tetti_contro_budget(c)),
    "SC-26": ("emettibilita' dei KILL: calcolata, non asserita", lambda c, s, p: sc26_emettibilita_dei_kill(c, normativi(p), s)),
    "SC-27": ("nessun kill automatico senza severity", lambda c, s, p: sc27_kill_automatici_hanno_una_severity(c, normativi(p), s)),
    "SC-28": ("quanti controlli dice di avere", lambda c, s, p: sc28_quanti_controlli_dice_di_avere(normativi(p) + pagine_pubblicate())),
    "SC-29": ("clausole di provenienza nell'orchestratore", lambda c, s, p: sc29_clausole_dell_orchestratore(c)),
}


# ---------------------------------------------------------------- SC-30
def sc30_nessuna_duplicazione_manuale(cfg):
    """Per gli agenti con delivery, il base non deve riportare i listati strutturali rimossi in
    M3: sarebbero una SECONDA fonte manuale che puo' divergere dalla delivery, cioe' il difetto
    che l'architettura esiste per eliminare. Gate NEGATIVO (assenza di due marker), con la via
    d'uscita per riga IGNORA: un futuro testo di metodo legittimo che nomini quei token non deve
    produrre un falso rosso. Una reintroduzione con marker alterati sfugge ai due gate: e' un
    residuo dichiarato, non risolvibile senza una whitelist."""
    con_delivery = set((cfg.get("verifica_specifica") or {}).get("agenti_con_delivery") or [])
    for agente in sorted(con_delivery):
        p = os.path.join(AGENTI, agente + ".md")
        if not os.path.exists(p):
            err("SC-30", f"agente con delivery inesistente: {agente}",
                "config.verifica_specifica")
            continue
        for riga in leggi(p).splitlines():
            if IGNORA in riga:
                continue
            for marker in ("| campo | dove | valori ammessi |", "mietitura_circolari_categoria"):
                if marker in riga:
                    err("SC-30", f"il base di {agente} reintroduce il listato strutturale "
                                 f"rimosso in M3: «{marker}». E' la seconda fonte manuale che "
                                 "la delivery ha sostituito", f"{agente}.md")
                    break


CONTROLLI["SC-30"] = ("nessuna duplicazione strutturale manuale con delivery",
                      lambda c, s, p: sc30_nessuna_duplicazione_manuale(c))


def main():
    solo = None
    if "--solo" in sys.argv:
        solo = sys.argv[sys.argv.index("--solo") + 1]
    try:
        cfg = carica_config()
    except Exception as e:
        print(f"\n  spec_check NON PUO' PARTIRE: config illeggibile — {type(e).__name__}: {e}")
        print("  Non avviare il run.\n")
        return 2
    schemi, prosa = tutti_gli_schemi(), file_di_prosa()
    if not prosa:
        print("\n  spec_check NON PUO' PARTIRE: nessun file di prosa trovato.\n")
        return 2
    print(f"\n  spec_check — config v{cfg.get('version')} · {len(schemi)} schemi · "
          f"{len(prosa)} file di prosa")
    print(f"  {'-'*76}")
    for cid, (titolo, fn) in CONTROLLI.items():
        if solo and cid != solo:
            continue
        prima_e, prima_a = len(errori), len(avvisi)
        try:
            fn(cfg, schemi, prosa)
        except Exception as e:
            # Un verificatore che esce con un traceback invece dell'elenco degli errori viene
            # commentato via dopo due volte, ed e' il PRIMO comando di ogni run. Un controllo che
            # esplode e' esso stesso un difetto della specifica, e va riportato come tale.
            err(cid, f"il controllo e' esploso su questo repo: {type(e).__name__}: {e}",
                "spec_check.py — difetto dello strumento, non della specifica")
        de, da = len(errori) - prima_e, len(avvisi) - prima_a
        stato = "FAIL" if de else ("avv " if da else "ok  ")
        print(f"  {stato}  {cid}  {titolo:<44} {de} errori, {da} avvisi")
    print(f"  {'-'*76}")
    if errori:
        print("\n  ERRORI (la specifica si contraddice: vanno chiusi)\n")
        for cid, msg, dove in errori:
            print(f"    [{cid}] {msg}")
            if dove:
                print(f"           in {dove}")
    if avvisi:
        print(f"\n  AVVISI ({len(avvisi)}) — non bloccanti, spesso rumore da convenzione\n")
        for cid, msg, dove in avvisi[:40]:
            print(f"    [{cid}] {msg}" + (f"  ({dove})" if dove else ""))
        if len(avvisi) > 40:
            print(f"    ... e altri {len(avvisi)-40}")
    if RUMORE and "--rumore" in sys.argv:
        print(f"\n  RUMORE — {len(RUMORE)} parametri di config mai citati\n")
        for p_ in RUMORE:
            print(f"    {p_}")
    print(f"\n  TOTALE: {len(errori)} errori, {len(avvisi)} avvisi\n")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
