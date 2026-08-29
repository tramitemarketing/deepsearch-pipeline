#!/usr/bin/env python3
"""
reconcile_run.py — separa il guasto amministrativo dalla fabbricazione, e ripara il primo.

PERCHE' ESISTE.

Nel run 20260802-1759 il 30% dei source_id citati negli artefatti non risolveva nel
source-ledger, e 147 ricerche su 332 non erano mai state consolidate. Applicando IC-1 alla
lettera, i claim che poggiano su quegli ID vanno declassati a HYPOTHESIS, e con loro i verdetti
che ne discendono. La reazione istintiva — cancellare `state/` e ricominciare — butta via il
lavoro buono insieme a quello cattivo, perche' confonde due cose che non sono la stessa:

  MANCATO CONSOLIDAMENTO   l'agente HA raccolto la fonte, l'ha restituita nel proprio
                           `new_sources`, e l'orchestratore non l'ha appesa al ledger. La prova
                           esiste, su disco, dentro l'artefatto. E' un guasto amministrativo ed
                           e' RIPARABILE senza rifare una sola ricerca.
  DICHIARATA IRRAGGIUNGIBILE l'agente ha bussato e la fonte ha rifiutato: paywall, blocco, 404,
                           robots, limite API. IC-4 IMPONE di dichiararla. La provenienza esiste
                           — sta in `unreachable_sources` — e va consolidata nel ledger col suo
                           `access_status`. Contarla come orfana significa marcare DEGRADATO un
                           run per aver fatto la cosa giusta, che e' come si insegna a un agente
                           a tacere le fonti che non ha potuto aprire.
  CITAZIONE SENZA ORIGINE  un source_id compare in un claim e non compare in NESSUN `new_sources`
                           ne' in NESSUN `unreachable_sources` di NESSUN artefatto del run.
                           Nessuno l'ha mai incontrato. Questa non si ripara: e' esattamente la
                           patologia che IC-1 esiste per intercettare, e i claim che vi poggiano
                           vanno declassati.

Finche' le tre restano indistinguibili, l'unica scelta possibile e' fra buttare tutto e fidarsi
di tutto, e sono entrambe sbagliate. Questo strumento le separa contandole.

E ne aggiunge una che nessuno guardava. Riconoscere le irraggiungibili come provenienza legittima
le fa entrare nel ledger, e da quel momento IC-1 le risolve: un claim VERIFIED sostenuto SOLO da
pagine che nessuno ha potuto aprire passerebbe il controllo di citazione. Sarebbe un falso
negativo comprato col falso positivo appena tolto. Una fonte irraggiungibile e' provenienza
valida per l'ESISTENZA della fonte e nulla di piu': non puo' sostenere da sola uno stato VERIFIED,
perche' non esiste lo snippet che IC-3 pretende per la coppia (claim, fonte). Quei claim sono
elencati a parte e valgono exit 1 come le citazioni senza origine.

  uso:  python3 tools/reconcile_run.py runs/<run_id>              → diagnosi, non tocca niente
        python3 tools/reconcile_run.py runs/<run_id> --consolida  → appende al ledger il
                                                                    recuperabile, e SOLO quello
        exit 0 = tutto risolvibile · 1 = restano citazioni senza origine
"""
import json, os, re, sys, glob
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RX_SRC = re.compile(r"\bS-[0-9]{8}(?:-[0-9]{4})?-[0-9]{4}\b")


def radice_telemetria():
    """
    Root del ledger M0.2: due livelli sopra ROOT, fuori dal repository.

    La variabile d'ambiente e' il seam dei test, e non e' un'opzione di
    configurazione: e' l'unico modo per cui una prova puo' scrivere restando dentro
    la propria directory temporanea. Senza di essa la root sta due livelli sopra
    ROOT, e nella copia usa-e-getta dei test quei due livelli cadono FUORI da cio'
    che rmtree rimuove — la telemetria di prova sopravviverebbe fra un caso e
    l'altro, o finirebbe nella root vera.

    Il nome si legge letterale di proposito: SC-22 sorveglia le letture di
    os.environ mediate da una variabile, ed e' un controllo che va lasciato
    lavorare sul caso che gli compete (le chiavi dei provider) senza avvicinarglisi.
    """
    v = os.environ.get("DEEPSEARCH_TELEMETRY_ROOT")
    if v:
        return v
    return os.path.join(os.path.dirname(os.path.dirname(ROOT)), "runtime", "telemetry")


def radice_stato():
    """
    Root dello stato mutabile della pipeline: accanto alla telemetria, fuori dal
    repository e fuori da ogni cartella sincronizzata.

    Prima del thin slice 2 i cinque file di stato vivevano in ROOT/state/, cioe'
    dentro GOVERNANCE-SYNC/, che e' una cartella Syncthing: un run reale li avrebbe
    replicati su altre macchine senza che alcun atto lo avesse autorizzato. E' il
    motivo per cui questa funzione esiste.

    E' l'UNICO punto in cui la radice dello stato si decide. run_close.py la importa
    da qui invece di ricostruirla: tre join letterali che risolvano ognuno per conto
    proprio sono tre occasioni di divergere, e una rilocazione parziale resterebbe
    verde perche' ogni lettore troverebbe il file che si aspetta.

    La variabile d'ambiente ha lo stesso statuto di quella della telemetria — seam
    dei test, non opzione di configurazione — e per la stessa ragione si legge
    letterale.

    La radice non si legge MAI da config/: le chiavi memory.*_file sono
    configurazione inattiva che nessuno strumento legge, e nominano ancora la
    vecchia collocazione. Usarle qui riporterebbe lo stato dentro il repository.
    """
    v = os.environ.get("DEEPSEARCH_STATE_ROOT")
    if v:
        return v
    return os.path.join(os.path.dirname(os.path.dirname(ROOT)), "runtime", "state")


NON_STRUM = "NOT_INSTRUMENTED"
IGNOTO = "UNKNOWN"
LEDGER_M02 = "m0.2-ledger.jsonl"
SIDECAR_M02 = "00-telemetry-write.json"


def config_m0_2():
    """
    La configurazione necessaria a interpretare le grandezze di lavoro.

    Le grandezze contano cio' che gli agenti hanno prodotto. A interpretarle serve
    sapere con quale modello ciascun agente ha girato, chi era delegabile a un
    provider esterno e chi vi girava davvero — la mappa attiva, che e' quella che il
    run legge. Il resto di config/ descrive il metodo, non il lavoro di questo run.

    Si LEGGE soltanto: config/ non e' fra i percorsi che questo slice tocca.

    Due assenze diverse, due etichette diverse, e la distinzione non e' pedanteria:
    se il file non si legge il valore non e' determinabile in modo affidabile
    (UNKNOWN); se il file si legge ma la chiave non c'e', manca il campo
    (NOT_INSTRUMENTED). Nessuno dei due caso e' zero, e nessuno e' una mappa vuota:
    una mappa vuota qui e' un dato: significa 'nessun agente delegato'.

    La conformita' all'allowlist e' STRUTTURALE, non misurata sull'input: cio' che
    esce e' o il valore di una delle tre sorgenti ammesse, o una delle due etichette.
    Mai altro, quale che sia la forma del file. Per ciascuna sorgente assenza e
    malformazione si separano — chiave che manca -> NOT_INSTRUMENTED, chiave presente
    ma nulla o del tipo sbagliato -> UNKNOWN — e il tipo atteso e' UNO per chiave: str
    per `modello`, list per `delegabili`, dict per `attivo_per_agente`.

    Non si coerce: un `modello` numerico, booleano o a sua volta oggetto e' UNKNOWN e
    non la propria serializzazione, perche' fabbricare '3' da 3 scriverebbe nel ledger
    un valore semanticamente falso al posto di dichiarare che non e' interpretabile.
    La stringa vuota resta invece un dato, per la stessa ragione per cui lo sono la
    lista vuota e la mappa vuota: vuoto non e' assenza.

    Di ogni voce di per_agente si legge la sola proprieta' `modello`: nessun ramo
    restituisce la voce, ne' una sua altra parte. Sotto `provider` si leggono le due
    sole chiavi ammesse: `deepseek`, gli endpoint e i nomi delle variabili di
    credenziale non sono toccati ne' per .get ne' per iterazione.
    """
    p = os.path.join(ROOT, "config", "deepsearch.config.json")
    try:
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return {"modelli_per_agente": IGNOTO, "provider_delegabili": IGNOTO,
                "provider_attivo_per_agente": IGNOTO}, "configurazione illeggibile"

    def modello_di(voce):
        # solo il modello: il resto della voce e' la prosa che spiega perche', e
        # nel ledger sarebbe rumore che non aiuta a interpretare alcun conteggio.
        # I tre soli ritorni possibili sono una stringa `modello`, IGNOTO, NON_STRUM.
        if not isinstance(voce, dict):
            return IGNOTO
        if "modello" not in voce:
            return NON_STRUM
        m = voce["modello"]
        return m if isinstance(m, str) else IGNOTO

    if not isinstance(c, dict):
        c = {}
    mod = c.get("modelli")
    per_ag = mod.get("per_agente") if isinstance(mod, dict) else None
    if isinstance(per_ag, dict):
        modelli = {k: modello_di(v) for k, v in sorted(per_ag.items())}
    else:
        modelli = NON_STRUM

    prov = c.get("provider")
    if not isinstance(prov, dict):
        prov = {}
    if "delegabili" in prov:
        d = prov["delegabili"]
        deleg = sorted(d) if isinstance(d, list) else IGNOTO
    else:
        deleg = NON_STRUM
    if "attivo_per_agente" in prov:
        a = prov["attivo_per_agente"]
        attivo = dict(sorted(a.items())) if isinstance(a, dict) else IGNOTO
    else:
        attivo = NON_STRUM
    return {
        "modelli_per_agente": modelli,
        "provider_delegabili": deleg,
        "provider_attivo_per_agente": attivo,
    }, None


def riga_m0_2(run_id, lavoro):
    """
    Una riga per run. La chiave di identita' e di idempotenza e' run_id.

    Nulla qui puo' variare fra due esecuzioni sullo stesso run: nessun istante di
    scrittura, nessun contatore, nessun identificatore generato. E' il requisito
    'doppia esecuzione -> ledger byte-identico', ed e' un vincolo sulla FORMA prima
    che sul meccanismo: un timestamp lo renderebbe impossibile da soddisfare.
    """
    cfg, guasto = config_m0_2()
    return {
        "run_id": run_id,
        "lavoro": lavoro,
        "configurazione": cfg,
        "non_strumentato": {
            "coverage_consumo": NON_STRUM,
            "denominatore_coverage": NON_STRUM,
            "total_count": NON_STRUM,
            "costo_monetario": NON_STRUM,
            "identificatori_query": NON_STRUM,
        },
    }, guasto


def run_gia_nel_ledger(percorso, run_id):
    """Rilettura con chiave run_id: e' l'idempotenza, e con questa grana e' tutta
    qui. Una riga illeggibile non conta come presenza — non si puo' affermare che
    contenga questo run."""
    if not os.path.exists(percorso):
        return False
    with open(percorso, encoding="utf-8") as f:
        for l in f:
            if not l.strip():
                continue
            try:
                o = json.loads(l)
            except Exception:
                continue
            if isinstance(o, dict) and o.get("run_id") == run_id:
                return True
    return False


def scrivi_ledger_m0_2(run_id, lavoro):
    """Ritorna (esito, guasto_configurazione). Non intercetta le eccezioni: chi
    chiama e' la guardia, e tenerle separate e' cio' che rende la guardia
    verificabile."""
    radice = radice_telemetria()
    percorso = os.path.join(radice, LEDGER_M02)
    riga, guasto = riga_m0_2(run_id, lavoro)
    if run_gia_nel_ledger(percorso, run_id):
        return "gia_presente", guasto
    os.makedirs(radice, exist_ok=True)
    with open(percorso, "a", encoding="utf-8") as g:
        g.write(json.dumps(riga, ensure_ascii=False) + "\n")
    return "riuscita", guasto


def carica_ledger(p, chiave):
    visti, righe = set(), 0
    if os.path.exists(p):
        for l in open(p, encoding="utf-8"):
            if not l.strip():
                continue
            try:
                o = json.loads(l)
            except Exception:
                continue
            righe += 1
            v = o.get(chiave) if isinstance(o, dict) else None
            if v:
                visti.add(v)
    return visti, righe


def claim_di(nodo):
    """
    Ogni claim VERIFIED dell'artefatto, ovunque sia annidato.

    Non si cerca per chiave 'claims': cinque agenti diversi li appendono sotto nomi diversi
    (`claims`, `evidence`, `facts`, dentro `payload.pains[]`, dentro `payload.competitors[]`).
    Un controllo che conosce solo il percorso di oggi smette di guardare il giorno in cui un
    agente ne aggiunge uno — in silenzio, che e' il modo peggiore. Qui si riconosce la FORMA:
    un oggetto con `state: VERIFIED` e una lista `source_ids`.
    """
    if isinstance(nodo, dict):
        if nodo.get("state") == "VERIFIED" and isinstance(nodo.get("source_ids"), list):
            yield nodo
        for v in nodo.values():
            yield from claim_di(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from claim_di(v)


FRASE_ERRORE = {
    "destinazione_non_scrivibile": "destinazione non scrivibile",
    "errore_interno": "errore interno del writer",
}


def codice_errore(e):
    """Due codici, non tre. Due sono le classi che si distinguono senza indovinare:
    la destinazione non si apre, oppure e' successo altro. Coniarne di piu' per casi
    mai osservati sarebbe inventare una tassonomia."""
    return "destinazione_non_scrivibile" if isinstance(e, OSError) else "errore_interno"


def messaggio_sanitizzato(cod, e):
    """Frase fissa per codice, piu' al massimo il NOME della classe d'eccezione.

    Mai str(e), mai un percorso, mai un frammento di dato: cosi' 'eccezione non
    sanitizzata' e' escluso per costruzione e non per diligenza di chi scrive.
    """
    return FRASE_ERRORE[cod] + " - " + type(e).__name__


def scrivi_sidecar_m0_2(run_dir, dati):
    """Scrive SOLO 00-telemetry-write.json, e solo dentro un run che esiste gia'.

    Non crea la directory del run: se non c'e', non c'e' nulla di cui questo file
    sia il sidecar, e crearla sarebbe fabbricare un run. Nessun altro artefatto
    gia' presente sotto runs/<run_id>/ viene letto, toccato o riscritto.
    """
    if not os.path.isdir(run_dir):
        raise NotADirectoryError("run assente")
    with open(os.path.join(run_dir, SIDECAR_M02), "w", encoding="utf-8") as g:
        json.dump(dati, g, ensure_ascii=False)
        g.write("\n")


#: La cartella dei pack, e il modo di riconoscerne uno. Un pack consumabile porta il pain nel
#: NOME e sta a UN livello: `02-evidence/P-<id>-<canale>.json`.
CARTELLA_EVIDENZA = "02-evidence"
RX_PAIN = re.compile(r"P-[0-9]{3}")


def pain_con_pack(run_dir):
    """I pain che hanno almeno un evidence-pack CONSUMABILE sul disco.

    LA REGOLA STA QUI E IN NESSUN ALTRO POSTO, e `run_close.py` la importa invece di
    riscriverla. Due join letterali che rispondano ognuno per conto proprio alla domanda «che
    cos'e' un pack» sono due occasioni di divergere, ed e' esattamente la classe di difetto che
    questo repository insegue: una regola scritta in due posti che si separano in silenzio.

    Sotto `02-evidence/` convivono tre forme, e solo la prima e' consumata:

      · `02-evidence/P-<id>-<canale>.json`         il PACK. Un livello, il pain nel nome.
      · `02-evidence/<pain>/mandato-<canale>.json` il MANDATO, scritto da `contract_compile`.
      · `02-evidence/_giro*/…`                     DIAGNOSTICA di un giro, non consumata.

    Il glob resta deliberatamente a UN livello, e non e' una svista da correggere con `**`: e' la
    regola. Un glob ricorsivo raccoglierebbe i mandati e la diagnostica, cioe' dichiarerebbe
    evidenza dove c'e' soltanto l'istruzione di andarla a cercare — e un run che non ha raccolto
    niente sembrerebbe averlo fatto.
    """
    fuori = set()
    for p in glob.glob(os.path.join(run_dir, CARTELLA_EVIDENZA, "*.json")):
        m = RX_PAIN.search(os.path.basename(p))
        if m:
            fuori.add(m.group(0))
    return fuori


def pains_dichiarati(run_dir):
    """`00-run-stats.json → pains_to_evidence`, o `None` se non c'e' o non si legge.

    `None` non e' zero: «il run non lo dichiara» e «il run dichiara zero» sono due fatti diversi,
    e solo il secondo autorizza a concludere che l'assenza di pack e' corretta.
    """
    try:
        with open(os.path.join(run_dir, "00-run-stats.json"), encoding="utf-8") as f:
            v = json.load(f).get("pains_to_evidence")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def riconcilia(run_dir, consolida=False, telemetria=False):
    run_id = os.path.basename(os.path.normpath(run_dir))

    # CANCELLO DI EVIDENZA — prima di ogni altra cosa, e non e' una scelta di stile.
    #
    # Piu' in basso questa funzione appende ai ledger di stato, scrive il sidecar e scrive la
    # riga di telemetria M0.2. Un run che DICHIARA di aver raccolto evidenza e non ne ha sul
    # disco non e' un run degradato: e' un run di cui non si sa nulla, e riconciliarlo
    # significherebbe consolidare fonti che nessun artefatto sostiene e scrivere una riga di
    # telemetria che ha la forma di un dato buono. Il ledger M0.2 e' irreversibile per atto: una
    # riga sbagliata non si cancella.
    #
    # Fail-CLOSED, quindi, e distinto dal fail-OPEN della telemetria piu' sotto: li' un guasto
    # dello STRUMENTO non deve declassare un run buono; qui e' l'ARTEFATTO a mancare, e non c'e'
    # niente da salvare proseguendo. Uscita `2`, che in questo strumento e in `run_close` e' gia'
    # il codice del guasto d'invocazione, non della misura.
    dichiarati = pains_dichiarati(run_dir)
    if dichiarati is not None and dichiarati > 0 and not pain_con_pack(run_dir):
        print(f"  ARRESTO: 00-run-stats.json dichiara pains_to_evidence={dichiarati}, e sotto "
              f"{CARTELLA_EVIDENZA}/ non esiste alcun evidence-pack consumabile", file=sys.stderr)
        print("  Un pack consumabile sta a un livello e porta il pain nel nome: "
              f"{CARTELLA_EVIDENZA}/P-<id>-<canale>.json.", file=sys.stderr)
        print("  I mandati annidati e le cartelle `_giro*` non sono evidenza consumata.",
              file=sys.stderr)
        print("  Nessun consolidamento, nessun sidecar, nessuna telemetria.", file=sys.stderr)
        return 2

    _stato = radice_stato()
    sl = os.path.join(_stato, "source-ledger.jsonl")
    ql = os.path.join(_stato, "query-ledger.jsonl")
    id_nel_ledger, n_src = carica_ledger(sl, "id")
    q_nel_ledger, n_q = carica_ledger(ql, "normalized_query")

    citati, raccolte, query_dichiarate = set(), {}, {}
    irraggiungibili, verified_solo_irr, claim_verified = {}, [], []
    n_art = 0
    for p in sorted(glob.glob(os.path.join(run_dir, "*", "*.json"))):
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        n_art += 1
        citati |= set(RX_SRC.findall(json.dumps(o, ensure_ascii=False)))
        if not isinstance(o, dict):
            continue
        for s in (o.get("new_sources") or []):
            if isinstance(s, dict) and s.get("id"):
                s.setdefault("run_id", run_id)
                s.setdefault("retrieved_by", o.get("agent", "sconosciuto"))
                raccolte.setdefault(s["id"], s)
        for u in (o.get("unreachable_sources") or []):
            if not isinstance(u, dict) or not u.get("source_id"):
                continue
            # Il record di ledger si ricostruisce dalla dichiarazione: e' meno di una fonte
            # aperta, ed e' giusto che lo sia. access_status porta il motivo del rifiuto, cosi'
            # chi legge il ledger vede subito che questa pagina non e' stata letta da nessuno.
            irraggiungibili.setdefault(u["source_id"], {
                "id": u["source_id"], "run_id": run_id,
                "url": u.get("url_or_target", "UNKNOWN"),
                "publisher": "UNKNOWN", "title": u.get("what", "UNKNOWN"),
                "published_date": "UNKNOWN", "accessed_at": "UNKNOWN", "tier": "T5",
                "access_status": u.get("why", "BLOCKED"),
                "retrieved_by": o.get("agent", "sconosciuto"),
                "_nota": "registrata per IC-4: tentata e rifiutata, mai letta"})
        for qq in (o.get("queries_run") or []):
            if isinstance(qq, dict) and qq.get("normalized_query"):
                qq.setdefault("run_id", run_id)
                qq.setdefault("agent", o.get("agent", "sconosciuto"))
                query_dichiarate.setdefault(qq["normalized_query"], qq)
        for c in claim_di(o):
            ids = [x for x in (c.get("source_ids") or []) if isinstance(x, str)]
            if ids:
                claim_verified.append((os.path.basename(p), c.get("id", "?"), ids))

    # SECONDA PASSATA, e il fatto che sia seconda non e' uno stile. Il claim che sta
    # nell'artefatto 1 puo' citare una fonte dichiarata irraggiungibile nell'artefatto 9: dentro
    # il ciclo l'insieme e' ancora mezzo vuoto e il controllo tace su meta' dei casi, in modo
    # dipendente dall'ordine alfabetico dei file. E' la stessa forma dell'errore di ordine che
    # aveva reso muta la mutazione SC-21: verificare prima che il fatto sia completo.
    for f, cid, ids in claim_verified:
        if all(x in irraggiungibili for x in ids):
            verified_solo_irr.append((f, cid, ids))

    orfani = citati - id_nel_ledger
    recuperabili = {i for i in orfani if i in raccolte}
    dichiarate_irr = {i for i in orfani - recuperabili if i in irraggiungibili}
    senza_origine = orfani - recuperabili - dichiarate_irr
    q_mancanti = {k: v for k, v in query_dichiarate.items() if k not in q_nel_ledger}

    print(f"\n  run: {run_dir}   artefatti letti: {n_art}")
    print(f"  {'-'*72}")
    print(f"  source_id citati negli artefatti .................. {len(citati)}")
    print(f"  gia' nel source-ledger ........................... {len(citati & id_nel_ledger)}")
    print(f"  ORFANI ........................................... {len(orfani)}"
          + (f"  ({len(orfani)/len(citati):.0%})" if citati else ""))
    print(f"    di cui RECUPERABILI (stanno in un new_sources) . {len(recuperabili)}")
    print(f"    di cui IRRAGGIUNGIBILI dichiarate (IC-4) ....... {len(dichiarate_irr)}")
    print(f"    di cui SENZA ORIGINE (nessuno li ha visti) ..... {len(senza_origine)}")
    print(f"  query dichiarate dagli agenti .................... {len(query_dichiarate)}")
    print(f"    mai consolidate nel query-ledger .............. {len(q_mancanti)}")
    print(f"  claim VERIFIED sostenuti SOLO da irraggiungibili .. {len(verified_solo_irr)}")
    print(f"  {'-'*72}")

    if verified_solo_irr:
        print("\n  VERIFIED SU PAGINE MAI LETTE — ogni source_id di questi claim e' una fonte")
        print("  che ha rifiutato. IC-3 pretende uno snippet per la coppia (claim, fonte) e li'")
        print("  non c'e' niente da citare: vanno declassati a HYPOTHESIS. Dichiarare una fonte")
        print("  irraggiungibile e' corretto; farle sostenere un fatto no.\n")
        for f, cid, ids in verified_solo_irr[:15]:
            print(f"    {f}  {cid}  <- {', '.join(ids)}")
        if len(verified_solo_irr) > 15:
            print(f"    ... e altri {len(verified_solo_irr)-15}")

    if senza_origine:
        print("\n  CITAZIONI SENZA ORIGINE — non riparabili, i claim che vi poggiano vanno")
        print("  declassati a HYPOTHESIS insieme ai verdetti che ne discendono:\n")
        for i in sorted(senza_origine)[:25]:
            print(f"    {i}")
        if len(senza_origine) > 25:
            print(f"    ... e altri {len(senza_origine)-25}")

    if telemetria:
        # Ramo proprio della modalita' M0.2, e sta QUI per due ragioni: le grandezze
        # sono complete alla riga sopra, e da qui il ramo e' raggiungibile senza
        # passare per i due append di --consolida, che restano dove sono e come sono.
        #
        # Due scostamenti dall'enumerazione delle undici grandezze eps-1, entrambi
        # voluti, e dichiarati qui perche' nessun altro documento li registra:
        #
        #   - degli «orfani E PERCENTUALE» si scrive il solo conteggio. La
        #     percentuale e' ri-derivabile da orfani/source_id_citati da chiunque
        #     legga la riga; aggiungerla come campo cambierebbe la forma della riga
        #     senza aggiungere un'informazione che non ci sia gia'.
        #   - delle «righe dei due ledger PRIMA E DOPO» si scrive il solo PRIMA.
        #     In questa modalita' il «dopo» NON ESISTE: M0.2 non appende ai ledger
        #     di state/, quindi non c'e' alcun secondo conteggio da osservare, e
        #     scriverne uno — fosse pure ripetendo il primo — significherebbe
        #     fabbricare un dato mai misurato. E' esattamente cio' che G-3 vieta.
        lavoro = {
            "artefatti_letti": n_art,
            "source_id_citati": len(citati),
            "gia_nel_source_ledger": len(citati & id_nel_ledger),
            "orfani": len(orfani),
            "orfani_recuperabili": len(recuperabili),
            "orfani_irraggiungibili_dichiarate": len(dichiarate_irr),
            "orfani_senza_origine": len(senza_origine),
            "query_dichiarate": len(query_dichiarate),
            "query_mai_consolidate": len(q_mancanti),
            "claim_verified_solo_irraggiungibili": len(verified_solo_irr),
            "righe_source_ledger": n_src,
            "righe_query_ledger": n_q,
        }
        # Guardia fail-open. Il fallimento della telemetria non converte un run
        # riuscito in fallito, non riusa gli exit code del verdetto, e non e' reso
        # come zero: e' uno stato della scrittura, e ha il proprio canale.
        esito, cod, msg = "riuscita", None, None
        try:
            esito, guasto_cfg = scrivi_ledger_m0_2(run_id, lavoro)
            if guasto_cfg:
                print(f"  telemetria m0.2:{guasto_cfg}", file=sys.stderr)
        except Exception as e:
            esito = "fallita"
            cod = codice_errore(e)
            msg = messaggio_sanitizzato(cod, e)
            print(f"  telemetria m0.2:ledger non scritto - {msg}", file=sys.stderr)

        dati = {"run_id": run_id, "esito_scrittura": esito}
        if esito == "fallita":
            dati["errore_codice"] = cod
            dati["errore_messaggio"] = msg
        try:
            scrivi_sidecar_m0_2(run_dir, dati)
        except Exception as e:
            # Ultimo canale: se anche il sidecar non si scrive, resta stderr, e il
            # run non si ferma. E' il ripiego che copre il caso residuo.
            print(f"  telemetria m0.2:sidecar non scritto - "
                  f"{messaggio_sanitizzato(codice_errore(e), e)}. Il run prosegue.",
                  file=sys.stderr)
        print(f"\n  telemetria m0.2:esito scrittura = {esito}")

    if not consolida:
        # La guardia resta quella di sempre — «senza --consolida non si appende ai
        # ledger di state/» — e il ritorno con essa. Cambia la SOLA prima riga, e
        # solo quando la modalita' M0.2 e' attiva: in quel caso il ledger M0.2 e il
        # sidecar sono appena stati scritti, e «non ho scritto niente» sarebbe falso
        # proprio nelle ultime parole che l'orchestratore legge. Le tre righe che
        # seguono valgono invariate in entrambe le modalita'.
        if telemetria:
            print("\n  Diagnosi, piu' la sola telemetria M0.2 (esito qui sopra): ai ledger di")
            print("  state/ non ho scritto niente.")
        else:
            print("\n  Diagnosi soltanto: non ho scritto niente.")
        print("  Con --consolida appendo ai ledger SOLO cio' che un agente ha davvero incontrato:")
        print("  quello che ha raccolto, e quello che ha tentato e non ha potuto aprire.")
        print("  Le citazioni senza origine non vengono mai inventate: restano scoperte.\n")
        return 1 if (senza_origine or verified_solo_irr) else 0

    # La radice dello stato e' fuori dal repository e alla prima esecuzione puo' non
    # esistere: senza questa riga l'append la solleverebbe come FileNotFoundError non
    # intercettata, e il consolidamento morirebbe sul caso piu' comune di tutti — la
    # macchina appena preparata. La telemetria ha sempre avuto il proprio makedirs;
    # lo stato no, perche' fino al thin slice 2 viveva in una directory tracciata che
    # esisteva per costruzione.
    os.makedirs(_stato, exist_ok=True)
    with open(sl, "a", encoding="utf-8") as g:
        for i in sorted(recuperabili):
            g.write(json.dumps(raccolte[i], ensure_ascii=False) + "\n")
        for i in sorted(dichiarate_irr):
            g.write(json.dumps(irraggiungibili[i], ensure_ascii=False) + "\n")
    with open(ql, "a", encoding="utf-8") as g:
        for k in sorted(q_mancanti):
            g.write(json.dumps(q_mancanti[k], ensure_ascii=False) + "\n")
    n_scritte = len(recuperabili) + len(dichiarate_irr)
    print(f"\n  Consolidate {len(recuperabili)} fonti raccolte, {len(dichiarate_irr)} dichiarate"
          f" irraggiungibili e {len(q_mancanti)} query.")
    print(f"  source-ledger: {n_src} -> {n_src + n_scritte} righe")
    print(f"  query-ledger:  {n_q} -> {n_q + len(q_mancanti)} righe")
    if senza_origine or verified_solo_irr:
        print(f"\n  RESTANO {len(senza_origine)} citazioni senza origine e {len(verified_solo_irr)}"
              " claim VERIFIED su pagine mai lette.")
        print("  Il run NON diventa valido per questo: ricontrolla con run_close.py --fase finale")
        print("  e declassa i claim elencati sopra. Consolidare non e' assolvere.\n")
    else:
        print("\n  Nessuna citazione senza origine: il run e' ora integralmente tracciabile.")
        print("  Riesegui run_close.py --fase finale.\n")
    return 1 if (senza_origine or verified_solo_irr) else 0


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not a:
        r = sorted(glob.glob(os.path.join(ROOT, "runs", "2*")))
        if not r:
            print("nessun run trovato"); sys.exit(2)
        a = [r[-1]]
    d = a[0] if os.path.isabs(a[0]) else os.path.join(ROOT, a[0])
    sys.exit(riconcilia(d, "--consolida" in sys.argv, "--telemetry-m0.2" in sys.argv))
