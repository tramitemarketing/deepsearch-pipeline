#!/usr/bin/env python3
"""
run_close_selftest.py — dimostra che IC-3 conta i soli Claim e che la guardia grave resta larga.

Sono due proprieta' che tirano in direzioni opposte, ed e' per questo che servono entrambe
scritte.

`claim_verificati()` riconosce la FORMA `state VERIFIED + lista source_ids`, non il percorso: e'
larga apposta, perche' la post-condizione grave «nessun VERIFIED poggia solo su fonti mai lette»
deve vedere ogni oggetto che porta uno stato — Claim, Numeric, trigger, verdetti — e un controllo
che conoscesse solo i percorsi di oggi smetterebbe di guardare in silenzio il giorno in cui ne
compare uno nuovo.

`IC-3` invece misura un rapporto: quanti dei claim VERIFIED portano `evidence[]` popolato. Lo slot
`evidence[]` esiste **solo** sul Claim. Contare gli altri mette al denominatore oggetti che non
potrebbero mai stare al numeratore: sul primo canary reale il contatore diceva 19/36, e diciassette
di quei trentasei non avrebbero potuto avere `evidence[]` in nessun caso. Tre erano perfino
`statement.quantity` di claim gia' contati fra i diciannove — lo stesso oggetto contato due volte,
una come numeratore e una come denominatore che lo smentiva.

La riparazione e' nel punto di conteggio di IC-3, NON in `claim_verificati()`. Questo file esiste
per impedire che qualcuno, un domani, "semplifichi" restringendo la funzione: il caso B fallirebbe.

  uso:  python3 tools/run_close_selftest.py
        exit 0 = entrambe le proprieta' reggono
"""
import json, os, re, shutil, subprocess, sys, tempfile

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_ID = "20260101-0000"


def envelope(payload, unreachable=None):
    return {
        "agent": "pain-finder",
        "run_id": RUN_ID,
        "status": "OK",
        "budget_used": {"searches": 1, "fetches": 1},
        "stop_reason": "COMPLETED",
        "payload": payload,
        "new_sources": [],
        "queries_run": [],
        "unreachable_sources": unreachable or [],
        "self_check": {"invarianti_violati": [], "note": ""},
    }


def claim(id_, source_ids, con_evidenza=True, quantity_verificata=False):
    q = None
    if quantity_verificata:
        # Un Numeric VERIFIED con source_ids: ha la FORMA che claim_verificati riconosce, ma non
        # ha ne' puo' avere uno slot evidence[]. E' l'oggetto che gonfiava il denominatore.
        q = {"label": "ore perse", "value": 5.2, "unit": "ore/mese", "state": "VERIFIED",
             "source_ids": list(source_ids)}
    c = {
        "id": id_,
        "statement": {"subject": "imprese target", "observed": "perdono ore", "quantity": q},
        "state": "VERIFIED",
        "source_ids": list(source_ids),
        "promotion_rule_met": "1xT1",
    }
    if con_evidenza:
        c["evidence"] = [{"source_id": source_ids[0], "snippet": "passaggio citato",
                          "snippet_source": "FETCHED"}]
    return c


def albero_minimo(base, artefatto, unreachable=None):
    """tools/ + un run con un solo artefatto. ROOT di run_close e' il padre di tools/."""
    os.makedirs(os.path.join(base, "tools"))
    for f in ("run_close.py", "reconcile_run.py"):
        shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(base, "tools", f))
    d = os.path.join(base, "runs", RUN_ID, "01-stage")
    os.makedirs(d)
    with open(os.path.join(d, "artefatto.json"), "w", encoding="utf-8") as f:
        json.dump(envelope(artefatto, unreachable), f, ensure_ascii=False)
    return os.path.join(base, "runs", RUN_ID)


def esegui(base, run_dir, stato=None, con_codice=False):
    """`stato` punta DEEPSEARCH_STATE_ROOT, che e' il seam dei test dichiarato da
    `reconcile_run.radice_stato()`. Senza, la radice ripiega su `<CLAUDE CODE>/runtime/state`:
    i casi IC-5 leggerebbero il ledger reale della pipeline."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if stato:
        env["DEEPSEARCH_STATE_ROOT"] = stato
    r = subprocess.run([sys.executable, os.path.join(base, "tools", "run_close.py"),
                        "--fase", "finale", run_dir],
                       capture_output=True, text=True, cwd=base, env=env,
                       encoding="utf-8", errors="replace")
    u = r.stdout + r.stderr
    return (u, r.returncode) if con_codice else u


def riga(uscita, frammento):
    for l in uscita.splitlines():
        if frammento in l:
            return l
    return ""


# ------------------------------------------------------------------ casi
def caso_a():
    """IC-3 conta i soli Claim.

    Due claim VERIFIED con evidence[], ciascuno con un `statement.quantity` VERIFIED che porta
    `source_ids`. Gli oggetti con la forma riconosciuta sono quattro; i Claim sono due.
    Atteso: 2/2. Col conteggio non ristretto sarebbe 2/4.
    """
    base = tempfile.mkdtemp()
    try:
        payload = {"claims": [claim("C-t-001", ["S-20260101-0001"], quantity_verificata=True),
                              claim("C-t-002", ["S-20260101-0002"], quantity_verificata=True)]}
        u = esegui(base, albero_minimo(base, payload), stato_con_ledger(base, {}))
        l = riga(u, "provenienza degli snippet dichiarata (IC-3)")
        m = re.search(r"(\d+)/(\d+) claim VERIFIED", l)
        if not m:
            return False, "IC-3 non compare nell'uscita di run_close"
        if (m.group(1), m.group(2)) != ("2", "2"):
            return False, (f"IC-3 legge {m.group(1)}/{m.group(2)}, atteso 2/2 — il denominatore "
                           "conta oggetti che non hanno lo slot evidence[]")
        return True, "IC-3 legge 2/2: i due Numeric VERIFIED non gonfiano il denominatore"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_b():
    """La guardia grave resta larga.

    Un solo oggetto VERIFIED, e NON e' un Claim: e' un Numeric annidato le cui uniche fonti sono
    dichiarate irraggiungibili. La post-condizione grave deve accorgersene. Se qualcuno
    restringesse `claim_verificati()` ai soli Claim per "far tornare" IC-3, questo oggetto
    diventerebbe invisibile e la guardia passerebbe: e' precisamente il modo in cui una
    riparazione ne rompe un'altra in silenzio.
    """
    base = tempfile.mkdtemp()
    try:
        payload = {"stime": {"tam": {"label": "TAM", "value": 1000, "unit": "EUR",
                                     "state": "VERIFIED",
                                     "source_ids": ["S-20260101-0009"]}}}
        irr = [{"url": "https://esempio.invalid/x", "reason": "PAYWALL",
                "source_id": "S-20260101-0009",
                "impact": "avrebbe sostenuto il TAM"}]
        u = esegui(base, albero_minimo(base, payload, unreachable=irr),
                   stato_con_ledger(base, {}))
        l = riga(u, "nessun VERIFIED poggia solo su fonti mai lette")
        if not l:
            return False, "la post-condizione grave non compare nell'uscita di run_close"
        if l.strip().startswith("ok"):
            return False, ("la guardia grave NON vede un VERIFIED non-Claim che poggia solo su "
                           "fonti irraggiungibili: claim_verificati() e' stata ristretta")
        return True, "la guardia grave vede un VERIFIED non-Claim: la funzione e' rimasta larga"
    finally:
        shutil.rmtree(base, ignore_errors=True)



# (replay, canale, ramo, id, promoted_by, source_ids)
CLAIM_REALI = [
    ('20260823', 'community', 'claim', 'C-r22c-001', 'FP3', ['S-20260823-1647-0042', 'S-20260823-1647-0044']),
    ('20260823', 'community', 'claim', 'C-r22c-002', 'FP3', ['S-20260823-1647-0041', 'S-20260823-1647-0043']),
    ('20260823', 'community', 'claim', 'C-r22c-003', 'FP3', ['S-20260823-1647-0042', 'S-20260823-1647-0044']),
    ('20260823', 'community', 'claim', 'C-r22c-005', 'FP3', ['S-20260823-1647-0042', 'S-20260823-1647-0049']),
    ('20260823', 'pricing_mercato', 'claim', 'C-r22p-001', 'FP3', ['S-20260823-1647-0061', 'S-20260823-1647-0068']),
    ('20260823', 'pricing_mercato', 'claim', 'C-r22p-002', 'FP2', ['S-20260823-1647-0065']),
    ('20260823', 'pricing_mercato', 'claim', 'C-r22p-004', 'FP3', ['S-20260823-1647-0066', 'S-20260823-1647-0070']),
    ('20260823', 'pricing_mercato', 'contr', 'C-r22p-contra-001', None, ['S-20260823-1647-0066', 'S-20260823-1647-0070']),
    ('20260823', 'trend_adozione', 'claim', 'C-r22t-001', 'FP3', ['S-20260823-1647-0081']),
    ('20260823', 'trend_adozione', 'claim', 'C-r22t-002', 'FP3', ['S-20260823-1647-0081']),
    ('20260823', 'trend_adozione', 'claim', 'C-r22t-003', 'FP4', ['S-20260823-1647-0082']),
    ('20260823', 'trend_adozione', 'claim', 'C-r22t-004', 'FP3', ['S-20260823-1647-0081']),
    ('20260823', 'trend_adozione', 'claim', 'C-r22t-005', 'FP0', ['S-20260823-1647-0081']),
    ('20260824', 'normativa', 'claim', 'C-r22n-001', 'FP3', ['S-20260824-2143-0001', 'S-20260824-2143-0004']),
    ('20260824', 'normativa', 'claim', 'C-r22n-002', 'FP1', ['S-20260824-2143-0004']),
    ('20260824', 'normativa', 'claim', 'C-r22n-003', 'FP1', ['S-20260824-2143-0001']),
    ('20260824', 'normativa', 'claim', 'C-r22n-004', 'FP1', ['S-20260824-2143-0001']),
    ('20260824', 'normativa', 'claim', 'C-r22n-005', 'FP3', ['S-20260824-2143-0001', 'S-20260824-2143-0004']),
    ('20260824', 'normativa', 'claim', 'C-r22n-006', 'FP3', ['S-20260824-2143-0003']),
    ('20260824', 'normativa', 'contr', 'C-r22n-contra-001', 'FP3', ['S-20260824-2143-0002']),
    ('20260824', 'community', 'claim', 'C-r22c-002', 'FP3', ['S-20260824-2143-0041', 'S-20260824-2143-0045']),
    ('20260824', 'community', 'claim', 'C-r22c-003', 'FP3', ['S-20260824-2143-0041', 'S-20260824-2143-0046']),
    ('20260824', 'community', 'claim', 'C-r22c-004', 'FP3', ['S-20260824-2143-0043']),
    ('20260824', 'pricing_mercato', 'claim', 'C-r22p-001', 'FP4', ['S-20260824-2143-0061']),
    ('20260824', 'pricing_mercato', 'claim', 'C-r22p-002', 'FP4', ['S-20260824-2143-0062']),
    ('20260824', 'pricing_mercato', 'claim', 'C-r22p-003', 'FP3', ['S-20260824-2143-0063', 'S-20260824-2143-0064', 'S-20260824-2143-0065']),
    ('20260824', 'pricing_mercato', 'claim', 'C-r22p-004', 'FP0', ['S-20260824-2143-0066', 'S-20260824-2143-0067']),
    ('20260824', 'pricing_mercato', 'claim', 'C-r22p-005', 'FP0', ['S-20260824-2143-0068']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-001', 'FP3', ['S-20260824-2143-0081']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-002', 'FP1', ['S-20260824-2143-0085']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-003', 'FP1', ['S-20260824-2143-0085']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-004', 'FP2', ['S-20260824-2143-0084']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-005', 'FP2', ['S-20260824-2143-0083']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-006', 'FP4', ['S-20260824-2143-0088', 'S-20260824-2143-0089', 'S-20260824-2143-0090', 'S-20260824-2143-0087']),
    ('20260824', 'trend_adozione', 'claim', 'C-r22t-007', 'FP3', ['S-20260824-2143-0086', 'S-20260824-2143-0094']),
    ('20260824', 'trend_adozione', 'contr', 'C-r22t-contra-001', 'FP2', ['S-20260824-2143-0083', 'S-20260824-2143-0095']),
]

# id -> (tier, access_status)
FONTI_REALI = {
    'S-20260823-1647-0041': ('T3', 'OK'),
    'S-20260823-1647-0042': ('T3', 'OK'),
    'S-20260823-1647-0043': ('T3', 'OK'),
    'S-20260823-1647-0044': ('T3', 'OK'),
    'S-20260823-1647-0047': ('T2', 'OK'),
    'S-20260823-1647-0048': ('T3', 'OK'),
    'S-20260823-1647-0049': ('T3', 'OK'),
    'S-20260823-1647-0050': ('T3', 'OK'),
    'S-20260823-1647-0061': ('T3', 'OK'),
    'S-20260823-1647-0062': ('T3', 'OK'),
    'S-20260823-1647-0063': ('T3', 'OK'),
    'S-20260823-1647-0064': ('T3', 'PARTIAL'),
    'S-20260823-1647-0065': ('T2', 'PAYWALL'),
    'S-20260823-1647-0066': ('T3', 'OK'),
    'S-20260823-1647-0067': ('T3', 'OK'),
    'S-20260823-1647-0068': ('T3', 'OK'),
    'S-20260823-1647-0069': ('T3', 'OK'),
    'S-20260823-1647-0070': ('T3', 'OK'),
    'S-20260823-1647-0081': ('T3', 'OK'),
    'S-20260823-1647-0082': ('T1', 'PARTIAL'),
    'S-20260823-1647-0083': ('T3', 'BLOCKED'),
    'S-20260823-1647-0084': ('T3', 'OK'),
    'S-20260823-1647-0085': ('T3', 'OK'),
    'S-20260823-1647-0086': ('T3', 'OK'),
    'S-20260823-1647-0087': ('T2', 'OK'),
    'S-20260823-1647-0088': ('T4', 'OK'),
    'S-20260823-1647-0089': ('T4', 'OK'),
    'S-20260823-1647-0090': ('T4', 'OK'),
    'S-20260824-2143-0001': ('T2', 'OK'),
    'S-20260824-2143-0002': ('T3', 'OK'),
    'S-20260824-2143-0003': ('T3', 'OK'),
    'S-20260824-2143-0004': ('T4', 'OK'),
    'S-20260824-2143-0041': ('T3', 'OK'),
    'S-20260824-2143-0042': ('T3', 'OK'),
    'S-20260824-2143-0043': ('T3', 'OK'),
    'S-20260824-2143-0044': ('T3', 'OK'),
    'S-20260824-2143-0045': ('T3', 'OK'),
    'S-20260824-2143-0046': ('T3', 'OK'),
    'S-20260824-2143-0047': ('T3', 'OK'),
    'S-20260824-2143-0048': ('T3', 'OK'),
    'S-20260824-2143-0061': ('T3', 'OK'),
    'S-20260824-2143-0062': ('T4', 'OK'),
    'S-20260824-2143-0063': ('T4', 'OK'),
    'S-20260824-2143-0064': ('T3', 'OK'),
    'S-20260824-2143-0065': ('T4', 'OK'),
    'S-20260824-2143-0066': ('T3', 'OK'),
    'S-20260824-2143-0067': ('T4', 'OK'),
    'S-20260824-2143-0068': ('T4', 'OK'),
    'S-20260824-2143-0081': ('T2', 'OK'),
    'S-20260824-2143-0082': ('T2', 'OK'),
    'S-20260824-2143-0083': ('T2', 'OK'),
    'S-20260824-2143-0084': ('T2', 'OK'),
    'S-20260824-2143-0085': ('T1', 'OK'),
    'S-20260824-2143-0086': ('T1', 'OK'),
    'S-20260824-2143-0087': ('T4', 'OK'),
    'S-20260824-2143-0088': ('T5', 'OK'),
    'S-20260824-2143-0089': ('T5', 'OK'),
    'S-20260824-2143-0090': ('T3', 'OK'),
    'S-20260824-2143-0094': ('T1', 'OK'),
    'S-20260824-2143-0095': ('T2', 'OK'),
}

# Le attese per id, CONGELATE. Verificate a mano su tre tornate di review indipendente.
CF_ATTESI = [
    '02-evidence/20260823-pricing_mercato.json:C-r22p-002',
    '02-evidence/20260823-trend_adozione.json:C-r22t-001',
    '02-evidence/20260823-trend_adozione.json:C-r22t-002',
    '02-evidence/20260823-trend_adozione.json:C-r22t-003',
    '02-evidence/20260823-trend_adozione.json:C-r22t-004',
    '02-evidence/20260823-trend_adozione.json:C-r22t-005',
    '02-evidence/20260824-community.json:C-r22c-004',
    '02-evidence/20260824-normativa.json:C-r22n-002',
    '02-evidence/20260824-normativa.json:C-r22n-006',
    '02-evidence/20260824-normativa.json:C-r22n-contra-001',
    '02-evidence/20260824-pricing_mercato.json:C-r22p-001',
    '02-evidence/20260824-pricing_mercato.json:C-r22p-002',
    '02-evidence/20260824-pricing_mercato.json:C-r22p-003',
    '02-evidence/20260824-pricing_mercato.json:C-r22p-004',
    '02-evidence/20260824-pricing_mercato.json:C-r22p-005',
]

EE_ATTESI = [
    '02-evidence/20260824-normativa.json:C-r22n-001',
    '02-evidence/20260824-normativa.json:C-r22n-003',
    '02-evidence/20260824-normativa.json:C-r22n-004',
    '02-evidence/20260824-normativa.json:C-r22n-005',
    '02-evidence/20260824-trend_adozione.json:C-r22t-001',
    '02-evidence/20260824-trend_adozione.json:C-r22t-007',
]

# ================================================================== IC-5 (canary)
#
# QUINDICI CASI, e la ragione per cui il primo vale piu' degli altri quattordici.
#
# `IC-5` giudica se un claim VERIFIED e' sostenuto dalle fonti che qualcuno ha davvero letto.
# E' un FALSIFICATORE: sa dimostrare che una promozione e' falsa, non che e' valida —
# l'indipendenza di FP3/FP4 non e' ricostruibile dai pack, e quindi ogni misura e' un LIMITE
# INFERIORE. Il bucket che ne discende si chiama `NON_FALSIFICATA` e non `VALIDA`, e il caso P
# esiste perche' nessuno lo rinomini.
#
# Il caso C porta i TRENTASEI claim VERIFIED dei due replay reali, ridotti ai soli campi che
# `IC-5` guarda. Non e' un campione: e' il corpus intero, con gli id, e le attese sono inchiodate
# per id perche' un controllo che trovasse quindici violazioni DIVERSE sarebbe verde su un
# conteggio e sbagliato nel merito.

CARD = (("FP1", "T1", 1), ("FP2", "T2", 1), ("FP3", "T3", 2), ("FP4", "T4", 4))


def stato_con_ledger(base, fonti):
    """La radice di stato dei test, col seam documentato `DEEPSEARCH_STATE_ROOT`.

    Senza, `radice_stato()` ripiega su `<CLAUDE CODE>/runtime/state`: il selftest leggerebbe il
    ledger REALE della pipeline, che il perimetro dichiara intoccabile e che renderebbe le attese
    dipendenti da stato mutabile fuori dal repository.
    """
    d = os.path.join(base, "stato")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "source-ledger.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for sid, (tier, acc) in sorted(fonti.items()):
            f.write(json.dumps({"id": sid, "tier": tier, "access_status": acc},
                               ensure_ascii=False) + "\n")
    return d


def albero_ic5(base, artefatti):
    """tools/ + un run con N artefatti. `artefatti` e' {nome_file: payload}."""
    os.makedirs(os.path.join(base, "tools"), exist_ok=True)
    for f in ("run_close.py", "reconcile_run.py"):
        shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(base, "tools", f))
    d = os.path.join(base, "runs", RUN_ID, "02-evidence")
    os.makedirs(d, exist_ok=True)
    for nome, payload in artefatti.items():
        with open(os.path.join(d, nome), "w", encoding="utf-8") as f:
            json.dump(envelope(payload), f, ensure_ascii=False)
    return os.path.join(base, "runs", RUN_ID)


def cl(id_, pb, ids):
    """Un claim VERIFIED ridotto ai campi che IC-5 guarda."""
    c = {"id": id_, "state": "VERIFIED", "source_ids": list(ids),
         "statement": {"subject": "s", "observed": "o"}}
    if pb is not None:
        c["promoted_by"] = pb
    return c


def ic5_esito(uscita):
    """(conteggi, {claim_id: (bucket, promoted_by, letti)}) dal blocco di dettaglio."""
    righe = uscita.splitlines()
    try:
        i = next(k for k, l in enumerate(righe) if "IC-5 (canary) — dettaglio" in l)
    except StopIteration:
        return None, None
    conte, per_id, atteso = {}, {}, None
    for l in righe[i + 1:]:
        if l.strip().startswith("run:") or l.startswith("  VERDETTO"):
            break
        m = re.findall(r"(\d+) ([A-Z_0-9]+)", l)   # la cifra di SOLO_FP5 fa parte del nome
        if m and "esaminati" in l:
            conte["VERIFIED"] = int(re.search(r"(\d+) VERIFIED esaminati", l).group(1))
        for n, k in m:
            if k != "VERIFIED":
                conte[k] = int(n)
        mm = re.match(r"\s{4}([A-Z_0-9]+)\s+(\S+)$", l)
        if mm:
            atteso = mm.group(2)                      # <artefatto>:<claim_id>, chiave PIENA:
            per_id[atteso] = [mm.group(1), None, None]  # gli id si ripetono fra i due replay
            continue
        md = re.search(r"promoted_by=(\S+)\s+letti=\{([^}]*)\}", l)
        if md and atteso:
            per_id[atteso][1] = md.group(1)
            per_id[atteso][2] = md.group(2)
            atteso = None
    return conte, per_id


def trova(per_id, id_):
    """L'unica voce il cui claim_id combacia. Nei casi piccoli l'artefatto e' uno solo."""
    v = [x for k, x in per_id.items() if k.endswith(":" + id_)]
    return v[0] if len(v) == 1 else [None, None, None]


def conta_attesa(claims, fonti):
    """La ripartizione attesa, ricalcolata QUI dall'algoritmo del mandato.

    E' una seconda implementazione, indipendente da quella di run_close.py: se le due divergono
    il caso C diventa rosso, ed e' esattamente cio' che deve succedere.
    """
    conte = {}
    for _, _, _, _, pb, ids in claims:
        letti = {}
        for x in ids:
            t, a = fonti.get(x, (None, None))
            if a == "OK" and t:
                letti[t] = letti.get(t, 0) + 1
        sodd = [fp for fp, t, n in CARD if letti.get(t, 0) >= n]
        if sodd:
            k = "NON_FALSIFICATA" if (pb is None or (pb in sodd and pb == min(sodd))) \
                else "ETICHETTA_ERRATA"
        elif letti.get("T5", 0) >= 1:
            k = "SOLO_FP5"
        elif any(x not in fonti for x in ids):
            k = "DATO_INSUFFICIENTE"
        else:
            k = "CERTAMENTE_FALSA"
        conte[k] = conte.get(k, 0) + 1
    return conte


def caso_c():
    """I 36 claim reali dei due replay: 15 / 6 / 14 / 1 / 0."""
    base = tempfile.mkdtemp()
    try:
        art = {}
        for rep, ch, ramo, id_, pb, ids in CLAIM_REALI:
            nome = f"{rep}-{ch}.json"
            art.setdefault(nome, {"claims": [], "contradictory_evidence": []})
            art[nome]["claims" if ramo == "claim" else "contradictory_evidence"].append(
                cl(id_, pb, ids))
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, FONTI_REALI))
        conte, per_id = ic5_esito(u)
        if conte is None:
            return False, "il blocco di dettaglio di IC-5 non compare nell'uscita"
        atteso = {"CERTAMENTE_FALSA": 15, "ETICHETTA_ERRATA": 6, "NON_FALSIFICATA": 14,
                  "SOLO_FP5": 1, "DATO_INSUFFICIENTE": 0}
        if conte.get("VERIFIED") != 36:
            return False, f"esaminati {conte.get('VERIFIED')}, attesi 36"
        for k, n in atteso.items():
            if conte.get(k, 0) != n:
                return False, f"{k}: {conte.get(k, 0)}, atteso {n}"
        # la seconda implementazione, indipendente
        mia = conta_attesa(CLAIM_REALI, FONTI_REALI)
        for k, n in atteso.items():
            if mia.get(k, 0) != n:
                return False, f"il ricalcolo indipendente da' {k}={mia.get(k, 0)}, atteso {n}"
        # gli id, uno per uno
        cf = {i for i, v in per_id.items() if v[0] == "CERTAMENTE_FALSA"}
        if cf != set(CF_ATTESI):
            return False, (f"CERTAMENTE_FALSA su id diversi: mancano {sorted(set(CF_ATTESI) - cf)}, "
                           f"in piu' {sorted(cf - set(CF_ATTESI))}")
        ee = {i for i, v in per_id.items() if v[0] == "ETICHETTA_ERRATA"}
        if ee != set(EE_ATTESI):
            return False, f"ETICHETTA_ERRATA su id diversi: {sorted(ee)}"
        return True, "36 VERIFIED → 15 / 6 / 14 / 1 / 0, e gli id combaciano uno per uno"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_d():
    """Regola PER CLAIM: senza `promoted_by` e promuovibile → NON_FALSIFICATA.

    `C-r22p-contra-001` sta in `contradictory_evidence[]`, il cui schema — un `$ref` nudo a
    `core#/$defs/Claim` — NON richiede `promoted_by`, mentre `claims.items` lo richiede. Dentro
    lo stesso artefatto convivono claim obbligati all'etichetta e claim che non lo sono: una
    regola per ARTEFATTO non puo' decidere questo caso.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK"), "S-2": ("T3", "OK")}
        art = {"a.json": {"contradictory_evidence": [cl("C-x-contra-001", None, ["S-1", "S-2"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("NON_FALSIFICATA") != 1 or conte.get("ETICHETTA_ERRATA", 0) != 0:
            return False, (f"senza promoted_by e promuovibile: {conte} — atteso "
                           "NON_FALSIFICATA, mai ETICHETTA_ERRATA")
        return True, "senza `promoted_by` e promuovibile → NON_FALSIFICATA"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_e():
    """L'assenza dell'etichetta NON e' un'assoluzione: senza `promoted_by` e non promuovibile
    resta CERTAMENTE_FALSA."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", None, ["S-1"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("CERTAMENTE_FALSA") != 1:
            return False, f"{conte} — atteso CERTAMENTE_FALSA: 1×T3 non basta per FP3"
        return True, "senza `promoted_by` e non promuovibile → CERTAMENTE_FALSA"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_f():
    """`PARTIAL` non conta come letta (determinazione 1)."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK"), "S-2": ("T3", "PARTIAL")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1", "S-2"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("CERTAMENTE_FALSA") != 1:
            return False, f"{conte} — con PARTIAL contata sarebbe 2×T3 e soddisferebbe FP3"
        if trova(per_id, "C-x-001")[2] != "T3:1":
            return False, f"letti={per_id.get('C-x-001')} — la PARTIAL non va contata"
        return True, "1×T3 OK + 1×T3 PARTIAL → CERTAMENTE_FALSA, letti={T3:1}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_g():
    """Una fonte mai letta non conta: senza l'esclusione si ricomprerebbe il falso positivo che
    IC-4 aveva tolto."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK"), "S-2": ("T3", "BLOCKED")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1", "S-2"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("CERTAMENTE_FALSA") != 1:
            return False, f"{conte} — una BLOCKED non puo' concorrere alla cardinalita'"
        return True, "1×T3 OK + 1×T3 BLOCKED → CERTAMENTE_FALSA"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_h():
    """FP5 non e' pronunciabile (determinazione 2): un claim promuovibile solo per quella via
    esce dal perimetro del controllo, e ci esce DICHIARANDOLO."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T5", "OK"), "S-2": ("T5", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP5", ["S-1", "S-2"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("SOLO_FP5") != 1:
            return False, f"{conte} — atteso SOLO_FP5, mai CERTAMENTE_FALSA ne' NON_FALSIFICATA"
        return True, "2×T5 → SOLO_FP5: il controllo non si pronuncia"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_i():
    """MONOTONIA: una fonte ignota puo' solo AGGIUNGERE evidenza. Se le fonti note gia' bastano,
    l'ignoto non cambia nulla."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T1", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP1", ["S-1", "S-IGNOTA"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("NON_FALSIFICATA") != 1:
            return False, (f"{conte} — con una fonte non risolta ma FP1 gia' soddisfatta il "
                           "verdetto non deve cambiare: la monotonia e' saltata")
        return True, "1×T1 letta + 1 ignota → NON_FALSIFICATA: l'ignoto non toglie evidenza"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_j():
    """DATO_INSUFFICIENTE morde solo quando l'evidenza nota non basta E manca qualcosa.
    Serve anche «nessuna T5 letta», perche' il §3.3 antepone SOLO_FP5."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1", "S-IGNOTA"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("DATO_INSUFFICIENTE") != 1:
            return False, f"{conte} — atteso DATO_INSUFFICIENTE, non CERTAMENTE_FALSA"
        return True, "1×T3 letta + 1 ignota → DATO_INSUFFICIENTE: non mi pronuncio"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_k():
    """Il codice piu' basso fra i soddisfatti: FP1 e FP3 entrambi soddisfatti, dichiarato FP3."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T1", "OK"), "S-2": ("T3", "OK"), "S-3": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1", "S-2", "S-3"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("ETICHETTA_ERRATA") != 1:
            return False, f"{conte} — FP3 e' soddisfatto ma il minimo e' FP1"
        return True, "FP1+FP3 soddisfatti, dichiarato FP3 → ETICHETTA_ERRATA (minimo FP1)"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_l():
    """FP4 al limite: 4×T4 soddisfa, 3×T4 no. Il confronto e' `>=`."""
    base = tempfile.mkdtemp()
    try:
        for n, atteso in ((4, "NON_FALSIFICATA"), (3, "CERTAMENTE_FALSA")):
            f = {f"S-{i}": ("T4", "OK") for i in range(n)}
            art = {"a.json": {"claims": [cl("C-x-001", "FP4", list(f))]}}
            u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
            conte, _ = ic5_esito(u)
            if conte.get(atteso) != 1:
                return False, f"{n}×T4 → {conte}, atteso {atteso}"
        return True, "4×T4 soddisfa FP4, 3×T4 no: il confronto e' `>=`"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_m():
    """IL PREDICATO. `ok` guarda SOLO `CERTAMENTE_FALSA`.

    Un run con etichette errate, dati insufficienti e claim fuori perimetro ma zero promozioni
    false deve far PASSARE il check. `IC-5` diventera' grave un giorno, e un controllo grave deve
    scattare soltanto sulla classe falsa con certezza.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T1", "OK"), "S-2": ("T3", "OK"), "S-3": ("T3", "OK"),
             "S-4": ("T5", "OK"), "S-5": ("T3", "OK")}
        art = {"a.json": {"claims": [
            cl("C-x-001", "FP3", ["S-1", "S-2", "S-3"]),      # ETICHETTA_ERRATA
            cl("C-x-002", "FP5", ["S-4"]),                     # SOLO_FP5
            cl("C-x-003", "FP3", ["S-5", "S-IGNOTA"]),         # DATO_INSUFFICIENTE
        ]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("CERTAMENTE_FALSA", 0) != 0:
            return False, f"la premessa del caso e' persa: {conte}"
        l = riga(u, "IC-5 (canary): promozione non sostenuta")
        if not l:
            return False, "la riga di IC-5 non compare nella tabella degli esiti"
        if not l.strip().startswith("ok"):
            return False, ("il check FALLISCE con zero CERTAMENTE_FALSA: il predicato guarda "
                           "bucket che non deve guardare")
        return True, "EE + SOLO_FP5 + DI ma zero CF → il check passa"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_n():
    """`grave=False`: IC-5 non tocca `gravi`, il verdetto, ne' l'exit code.

    Non si pretende un run VALIDO — una fixture minimale non puo' esserlo — ma si confrontano
    DUE run identici in tutto tranne che per l'esito di IC-5: uno con una promozione falsa, uno
    senza. Se IC-5 fosse grave, i due divergerebbero. E' la determinazione 3, ed e' l'unica cosa
    che rende questa slice un canary invece di un cambio di gate.
    """
    base = tempfile.mkdtemp()
    try:
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1"])]}}
        run_dir = albero_ic5(base, art)
        # T3 → 1×T3 non basta per FP3 → CERTAMENTE_FALSA ;  T1 → FP1 soddisfatta, FP3 dichiarata
        # → ETICHETTA_ERRATA. Cambia l'esito di IC-5 e nient'altro.
        u_f, c_f = esegui(base, run_dir, stato_con_ledger(base, {"S-1": ("T3", "OK")}),
                          con_codice=True)
        u_o, c_o = esegui(base, run_dir, stato_con_ledger(base, {"S-1": ("T1", "OK")}),
                          con_codice=True)
        n_f, _ = ic5_esito(u_f)
        n_o, _ = ic5_esito(u_o)
        if n_f.get("CERTAMENTE_FALSA") != 1 or n_o.get("CERTAMENTE_FALSA", 0) != 0:
            return False, f"la premessa del caso e' persa: {n_f} contro {n_o}"
        if riga(u_f, "IC-5 (canary): promozione").strip().startswith("ok"):
            return False, "il check non fallisce dove deve: la premessa e' persa"
        if not riga(u_o, "IC-5 (canary): promozione").strip().startswith("ok"):
            return False, "il check fallisce dove non deve: la premessa e' persa"
        g_f = re.search(r"\((\d+) post-condizioni fallite, (\d+) avvisi\)", riga(u_f, "VERDETTO"))
        g_o = re.search(r"\((\d+) post-condizioni fallite, (\d+) avvisi\)", riga(u_o, "VERDETTO"))
        if g_f.group(1) != g_o.group(1):
            return False, (f"IC-5 ha incrementato `gravi`: {g_f.group(1)} contro {g_o.group(1)} "
                           "— e' entrato nel conteggio grave")
        if g_f.group(2) == g_o.group(2):
            return False, "gli avvisi non cambiano: IC-5 non e' contato nemmeno come avviso"
        v_f = riga(u_f, "VERDETTO DEL RUN:").split("(")[0]
        v_o = riga(u_o, "VERDETTO DEL RUN:").split("(")[0]
        if v_f != v_o:
            return False, f"IC-5 ha cambiato il verdetto: «{v_f.strip()}» contro «{v_o.strip()}»"
        if c_f != c_o:
            return False, f"IC-5 ha cambiato l'exit code: {c_f} contro {c_o}"
        return True, (f"IC-5 fallisce e non fallisce: gravi {g_f.group(1)} invariati, "
                      f"verdetto invariato, uscita {c_f} invariata; cambiano i soli avvisi")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_o():
    """`contradictory_evidence[]` e' percorso quanto `claims[]`: la `== COND` obbliga entrambi."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1"])],
                          "contradictory_evidence": [cl("C-x-contra-001", "FP3", ["S-1"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("VERIFIED") != 2 or conte.get("CERTAMENTE_FALSA") != 2:
            return False, f"{conte} — atteso 2 esaminati e 2 CERTAMENTE_FALSA"
        if trova(per_id, "C-x-contra-001")[0] is None:
            return False, "il claim di contradictory_evidence non compare nel dettaglio"
        return True, "claims[] e contradictory_evidence[] sono percorsi entrambi"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_p():
    """Il formato: `letti={…}` ordinato T1..T5, il blocco DOPO la tabella, `NON_FALSIFICATA`
    silente nel dettaglio, e il nome che non sfonda la colonna."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T5", "OK"), "S-2": ("T3", "OK"), "S-3": ("T4", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP4", ["S-1", "S-2", "S-3"]),
                                     cl("C-x-002", "FP4", ["S-1"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        v = trova(per_id, "C-x-001")[2]
        if v != "T3:1,T4:1,T5:1":
            return False, f"letti={{{v}}} — atteso ordine T1..T5, non l'ordine dei source_ids"
        righe = u.splitlines()
        i_tab = max(k for k, l in enumerate(righe) if "IC-5 (canary): promozione" in l)
        i_det = next(k for k, l in enumerate(righe) if "IC-5 (canary) — dettaglio" in l)
        if i_det < i_tab:
            return False, "il blocco di dettaglio esce PRIMA della tabella degli esiti"
        nome = riga(u, "IC-5 (canary): promozione").strip()[6:]
        if len(nome.split("  ")[0]) > 46:
            return False, "il nome del check sfonda la colonna da 46 caratteri"
        # il disclaimer non e' ornamento: e' la determinazione 4 — il bucket si chiama
        # NON_FALSIFICATA e non VALIDA, e chi legge il blocco deve sapere perche'
        if "NON_FALSIFICATA non significa valida" not in u or "limite inferiore" not in u:
            return False, ("il blocco non dichiara che NON_FALSIFICATA non significa valida "
                           "e che il controllo e' un limite inferiore")
        return True, "letti ordinato T1..T5, blocco dopo la tabella, nome entro 46 caratteri"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_q():
    """Determinismo: due esecuzioni sullo stesso ingresso danno lo stesso blocco."""
    base = tempfile.mkdtemp()
    try:
        f = {f"S-{i}": ("T3", "OK") for i in range(4)}
        art = {"a.json": {"claims": [cl(f"C-x-{i:03d}", "FP3", [f"S-{i}"]) for i in range(4)]}}
        run_dir = albero_ic5(base, art)
        stato = stato_con_ledger(base, f)
        def blocco(u):
            r = u.splitlines()
            i = next(k for k, l in enumerate(r) if "IC-5 (canary) — dettaglio" in l)
            return "\n".join(r[i:i + 20])
        a = blocco(esegui(base, run_dir, stato))
        b = blocco(esegui(base, run_dir, stato))
        if a != b:
            return False, "due esecuzioni, due blocchi diversi"
        return True, "due esecuzioni, blocco identico"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_r():
    """PER PERCORSO e non per forma: un `Numeric` annidato non e' un claim.

    Il `statement.quantity` qui sotto ha la FORMA che `claim_verificati()` riconosce — `state`
    VERIFIED piu' `source_ids` — ma e' una QUANTITA', non una promozione. Sul corpus dei due
    replay il riconoscimento per forma trova 60 oggetti contro i 36 per percorso: i 24 in piu'
    sono `statement.quantity` e `spend_evidence[].amount`. Un IC-5 che attraversasse per forma
    giudicherebbe le quantita' come se fossero claim, e questo caso lo coglie.

    Le fixture del caso C sono ridotte ai campi che IC-5 guarda e quindi NON hanno quantita'
    annidate: senza questo caso la mutazione «attraversa per forma» resterebbe muta. Misurato.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T1", "OK")}
        c = cl("C-x-001", "FP1", ["S-1"])
        c["statement"]["quantity"] = {"label": "ore perse", "value": 5.2, "unit": "ore/mese",
                                      "state": "VERIFIED", "source_ids": ["S-1"]}
        u = esegui(base, albero_ic5(base, {"a.json": {"claims": [c]}}),
                   stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("VERIFIED") != 1:
            return False, (f"esaminati {conte.get('VERIFIED')}, atteso 1: la quantita' annidata "
                           "e' stata giudicata come un claim — l'attraversamento e' per forma")
        return True, "un `Numeric` annidato non entra nel corpus: si attraversa per percorso"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def righe_tabella(uscita):
    """Le righe della tabella degli esiti: quelle che il printer condiviso emette."""
    r = uscita.splitlines()
    i = next(k for k, l in enumerate(r) if l.startswith("  ") and set(l.strip()) == {"-"})
    j = next(k for k in range(i + 1, len(r)) if r[k].startswith("  ") and set(r[k].strip()) == {"-"})
    return r[i + 1:j]


def caso_s():
    """Il `dett` del check e' di UNA riga.

    `run_close.py` stampa ogni esito con `f"  {'ok  '|'FAIL'}  {nome:<46} {dett}"`: un `dett`
    multiriga non solleva, esce — e spacca l'allineamento di ogni altro controllo, perche' la
    seconda riga non passa dal formato. Il mandato lo vieta al §2 e lo elenca fra le mutazioni
    da rendere rosse; senza questo caso passava.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        tab = righe_tabella(u)
        fuori = [l for l in tab if not re.match(r"^  (ok  |FAIL)  ", l)]
        if fuori:
            return False, (f"{len(fuori)} righe della tabella non passano dal printer condiviso: "
                           f"{fuori[0]!r} — un `dett` multiriga la sta spaccando")
        if not any("IC-5 (canary)" in l for l in tab):
            return False, "la riga di IC-5 non compare nella tabella"
        return True, f"tutte le {len(tab)} righe della tabella hanno il formato del printer"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_t():
    """AMMISSIONE: un artefatto il cui `payload` non e' un dizionario non contribuisce claim.

    E' il criterio che tiene il corpus a 36 invece che a 42: su
    `20260824-2143/statistica_ufficiale.raw.txt` l'estrazione RIESCE e restituisce un dizionario
    di claim indicizzati per id, senza `payload`. «L'estrazione non solleva» non e' ammissione.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        os.makedirs(os.path.join(base, "tools"), exist_ok=True)
        for g in ("run_close.py", "reconcile_run.py"):
            shutil.copy(os.path.join(ROOT, "tools", g), os.path.join(base, "tools", g))
        d = os.path.join(base, "runs", RUN_ID, "02-evidence")
        os.makedirs(d, exist_ok=True)
        buono = envelope({"claims": [cl("C-x-001", "FP3", ["S-1"])]})
        with open(os.path.join(d, "a.json"), "w", encoding="utf-8") as h:
            json.dump(buono, h, ensure_ascii=False)
        # payload NON dizionario, con dentro claim ben formati: e' il caso reale
        cattivo = envelope(None)
        cattivo["payload"] = [cl("C-y-001", "FP3", ["S-1"]), cl("C-y-002", "FP3", ["S-1"])]
        with open(os.path.join(d, "b.json"), "w", encoding="utf-8") as h:
            json.dump(cattivo, h, ensure_ascii=False)
        u = esegui(base, os.path.join(base, "runs", RUN_ID), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("VERIFIED") != 1:
            return False, (f"esaminati {conte.get('VERIFIED')}, atteso 1: i claim di un artefatto "
                           "senza `payload` dizionario sono entrati nel corpus")
        if trova(per_id, "C-y-001")[0] is not None:
            return False, "un claim di un artefatto non ammesso compare nel dettaglio"
        return True, "l'artefatto col `payload` non dizionario non contribuisce claim"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_u():
    """`NON_FALSIFICATA` e' il caso SILENTE: nei conteggi si', nel dettaglio mai.

    Il §4 lo prescrive, e senza questo caso la prescrizione non era verificata: le fixture del
    caso P producono zero NON_FALSIFICATA, quindi la proprieta' che quel caso dichiarava di
    provare era inosservabile dal suo stesso ingresso.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T1", "OK"), "S-2": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-nf-001", "FP1", ["S-1"]),      # NON_FALSIFICATA
                                     cl("C-cf-001", "FP3", ["S-2"])]}}   # CERTAMENTE_FALSA
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("NON_FALSIFICATA") != 1 or conte.get("CERTAMENTE_FALSA") != 1:
            return False, f"la premessa del caso e' persa: {conte}"
        if trova(per_id, "C-nf-001")[0] is not None:
            return False, "un NON_FALSIFICATA compare nel dettaglio: non e' piu' il caso silente"
        if trova(per_id, "C-cf-001")[0] != "CERTAMENTE_FALSA":
            return False, "il CERTAMENTE_FALSA non compare nel dettaglio"
        return True, "NON_FALSIFICATA nei conteggi, assente dal dettaglio"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_v():
    """Un `source_id` RIPETUTO non raggiunge la cardinalita' da solo.

    FP3 pretende «2 fonti T3 INDIPENDENTI»: la stessa fonte citata due volte non e' due fonti.
    `core#/$defs/Claim.source_ids` non pone `uniqueItems`, quindi il contratto ammette il caso, e
    senza deduplica una sola T3 letta due volte soddisferebbe FP3 — un falso negativo del
    falsificatore nella classe esatta che il controllo esiste per cogliere.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1", "S-1"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, per_id = ic5_esito(u)
        if conte.get("CERTAMENTE_FALSA") != 1:
            return False, (f"{conte} — la stessa T3 citata due volte ha soddisfatto FP3: "
                           "i source_ids non sono deduplicati")
        if trova(per_id, "C-x-001")[2] != "T3:1":
            return False, f"letti={trova(per_id, 'C-x-001')[2]}, atteso T3:1"
        return True, "1×T3 citata due volte → letti={T3:1}, CERTAMENTE_FALSA"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_w():
    """Solo i `VERIFIED` entrano nel corpus.

    Sui pack reali il corpus passerebbe da 36 a 49 senza il filtro: nove `HYPOTHESIS` e quattro
    `ESTIMATED` vivono negli stessi due rami, e giudicarne la promozione sarebbe un errore di
    categoria — uno statement non promosso DEVE restare non promosso.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T3", "OK")}
        h = cl("C-h-001", "FP0", ["S-1"]); h["state"] = "HYPOTHESIS"
        e = cl("C-e-001", "FP0", ["S-1"]); e["state"] = "ESTIMATED"
        art = {"a.json": {"claims": [cl("C-v-001", "FP3", ["S-1"]), h, e]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("VERIFIED") != 1:
            return False, (f"esaminati {conte.get('VERIFIED')}, atteso 1: HYPOTHESIS ed ESTIMATED "
                           "sono entrati nel corpus")
        return True, "HYPOTHESIS ed ESTIMATED restano fuori: si giudicano i soli VERIFIED"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_x():
    """Il dettaglio esce in ORDINE DI DOCUMENTO, non ordinato per classe.

    I due claim sono disposti perche' un ordinamento alfabetico per bucket li invertirebbe:
    `CERTAMENTE_FALSA` precede `SOLO_FP5`, ma nel documento viene dopo.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T5", "OK"), "S-2": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-a-001", "FP5", ["S-1"]),      # SOLO_FP5, primo
                                     cl("C-b-001", "FP3", ["S-2"])]}}   # CERTAMENTE_FALSA, secondo
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        _, per_id = ic5_esito(u)
        ordine = [k.split(":")[-1] for k in per_id]
        if ordine != ["C-a-001", "C-b-001"]:
            return False, (f"ordine del dettaglio {ordine}, atteso ['C-a-001', 'C-b-001'] — "
                           "il blocco e' ordinato per classe invece che per documento")
        return True, "il dettaglio segue l'ordine di documento, non quello dei bucket"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_y():
    """`SOLO_FP5` non ha la precedenza sulla promuovibilita', e guarda le T5 LETTE.

    Due sotto-casi: un claim con una T5 ma anche 2×T3 e' PROMUOVIBILE e non esce dal perimetro;
    un claim la cui unica T5 non e' letta non e' `SOLO_FP5` ma `CERTAMENTE_FALSA`.
    """
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T5", "OK"), "S-2": ("T3", "OK"), "S-3": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP3", ["S-1", "S-2", "S-3"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        conte, _ = ic5_esito(u)
        if conte.get("SOLO_FP5", 0) != 0 or conte.get("NON_FALSIFICATA") != 1:
            return False, (f"{conte} — con 2×T3 lette il claim e' promuovibile: SOLO_FP5 non ha "
                           "la precedenza")
        g = {"S-4": ("T5", "BLOCKED")}
        art2 = {"a.json": {"claims": [cl("C-y-001", "FP5", ["S-4"])]}}
        u2 = esegui(base, albero_ic5(base, art2), stato_con_ledger(base, g))
        conte2, _ = ic5_esito(u2)
        if conte2.get("CERTAMENTE_FALSA") != 1:
            return False, f"{conte2} — una T5 BLOCKED non rende il claim SOLO_FP5"
        return True, "SOLO_FP5 non precede la promuovibilita', e guarda le sole T5 lette"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_z():
    """La nota dichiara il codice MINIMO fra i soddisfatti, non uno qualsiasi."""
    base = tempfile.mkdtemp()
    try:
        f = {"S-1": ("T1", "OK"), "S-2": ("T3", "OK"), "S-3": ("T3", "OK")}
        art = {"a.json": {"claims": [cl("C-x-001", "FP4", ["S-1", "S-2", "S-3"])]}}
        u = esegui(base, albero_ic5(base, art), stato_con_ledger(base, f))
        l = [x for x in u.splitlines() if "soddisfa FP" in x]
        if not l:
            return False, "la nota `soddisfa FPn` non compare nel dettaglio"
        if "soddisfa FP1" not in l[0]:
            return False, f"la nota dice «{l[0].strip()}» — FP1 e FP3 sono soddisfatte, il minimo e' FP1"
        return True, "FP1 e FP3 soddisfatte, la nota dichiara FP1"
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------ B-1
#
# IL PACK CONSUMABILE VIVE A UN LIVELLO, e sotto `02-evidence/` non tutto e' un pack.
# Tre forme convivono in quella cartella e solo la prima e' evidenza consumabile:
#
#   02-evidence/P-<id>-<canale>.json        <- IL PACK. Un livello. Lo scrive `ds_call.py`.
#   02-evidence/<pain>/mandato-<canale>.json <- il MANDATO, scritto da `contract_compile.py`.
#   02-evidence/_giro*/…                     <- DIAGNOSTICA di un giro, non consumata da nessuno.
#
# Il difetto che questi casi chiudono non era il glob — il glob a un livello e' GIUSTO, ed e' la
# regola. Era che, non trovando alcun pack, il confronto col funnel faceva `continue`: un run che
# dichiarava evidenza e non ne aveva sul disco passava il controllo SALTANDOLO. Il conteggio
# tornava perche' nessuno lo faceva, ed e' la forma piu' silenziosa di verde.

def albero_b1(base, stats, pack=(), mandati=(), giri=()):
    """Un run con `00-run-stats.json` e le tre forme di contenuto di `02-evidence/`.

    Gli altri artefatti obbligatori mancano di proposito: una fixture minimale non puo' essere
    VALIDA, e questi casi non lo pretendono. Confrontano DUE run identici in tutto tranne la
    presenza del pack — l'idioma del caso `N` — cosi' che la differenza misurata sia attribuibile
    a questa post-condizione e a nessun'altra.
    """
    os.makedirs(os.path.join(base, "tools"), exist_ok=True)
    for f in ("run_close.py", "reconcile_run.py"):
        shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(base, "tools", f))
    run_dir = os.path.join(base, "runs", RUN_ID)
    ev = os.path.join(run_dir, "02-evidence")
    os.makedirs(ev, exist_ok=True)
    with open(os.path.join(run_dir, "00-run-stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)
    for nome in pack:                                   # un livello: il pack consumabile
        with open(os.path.join(ev, nome), "w", encoding="utf-8") as f:
            json.dump(envelope({"claims": []}), f, ensure_ascii=False)
    for pain, canale in mandati:                        # due livelli: il mandato
        d = os.path.join(ev, pain)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"mandato-{canale}.json"), "w", encoding="utf-8") as f:
            json.dump({"pain": {"id": pain}, "channel": canale}, f, ensure_ascii=False)
    for giro, nome in giri:                             # due livelli: la diagnostica
        d = os.path.join(ev, giro)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, nome), "w", encoding="utf-8") as f:
            json.dump(envelope({"claims": []}), f, ensure_ascii=False)
    return run_dir


_B1_NOME = "evidence-pack consumabile per ogni pain dichiarato"


def _b1_riga(uscita):
    return riga(uscita, "evidence-pack consumabile")


def _b1_dett(uscita):
    """Il `dett` della riga, separato dal nome del check.

    Si guarda la FRASE INTERA e non il solo `ok`: il `dett` viene stampato anche quando il check
    passa, e una riga `ok` seguita da «ma nessun pack» sarebbe falsa nel report di chiusura di
    ogni run buono. Un caso che si ferma a `startswith("ok")` non se ne accorge — misurato.
    """
    l = _b1_riga(uscita)
    return l.split(_B1_NOME, 1)[1].strip() if _B1_NOME in l else ""


def _b1_gravi(uscita):
    m = re.search(r"\((\d+) post-condizioni fallite, (\d+) avvisi\)", riga(uscita, "VERDETTO"))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def caso_aa():
    """`pains_to_evidence=1` con soli mandati e diagnostica: post-condizione GRAVE.

    E' il rosso di partenza di `B-1`. Sotto `02-evidence/` c'e' del contenuto — un mandato
    annidato e un file di `_giro1` — ma nessun pack consumabile. Prima del rimedio il confronto
    col funnel non falliva: usciva dal ciclo con `continue`.
    """
    base = tempfile.mkdtemp()
    try:
        run_dir = albero_b1(base, {"pains_to_evidence": 1},
                            mandati=[("P-003", "community")],
                            giri=[("_giro1", "P-003-community.json")])
        u, c = esegui(base, run_dir, con_codice=True)
        l = _b1_riga(u)
        if not l:
            return False, ("nessuna post-condizione nomina l'assenza di evidence-pack "
                           "consumabili: il confronto col funnel e' stato SALTATO")
        if l.strip().startswith("ok"):
            return False, f"la post-condizione esiste ma passa: «{l.strip()}»"
        if c == 0:
            return False, f"uscita {c}: il run chiude validamente senza evidenza sul disco"
        dett = _b1_dett(u)
        for atteso in ("stats.pains_to_evidence=1", "ma nessun",
                       "02-evidence/P-<id>-<canale>.json", "`_giro*`"):
            if atteso not in dett:
                return False, f"il dettaglio non dice «{atteso}»: «{dett}»"
        if len(dett.splitlines()) != 1:
            return False, "il dettaglio occupa piu' di una riga"
        return True, f"post-condizione fallita, uscita {c}, dettaglio vero: «{dett}»"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_ab():
    """Il pack canonico a UN livello e' visto, e la post-condizione passa."""
    base = tempfile.mkdtemp()
    try:
        run_dir = albero_b1(base, {"pains_to_evidence": 1}, pack=["P-003-community.json"])
        u = esegui(base, run_dir)
        l = _b1_riga(u)
        if not l:
            return False, "la post-condizione non compare nemmeno col pack presente"
        if not l.strip().startswith("ok"):
            return False, f"il pack canonico non e' stato visto: «{l.strip()}»"
        # LA FRASE INTERA, non il solo `ok`. Il dettaglio del ramo che passa deve descrivere il
        # disco com'e': dire «ma nessun pack» accanto a un `ok` e' una frase falsa, e prima di
        # questa clausola nessun caso la guardava.
        dett = _b1_dett(u)
        if "ma nessun" in dett or "non sono evidenza consumata" in dett:
            return False, (f"il check passa ma il dettaglio nega il pack che c'e': «{dett}»")
        if "P-003" not in dett:
            return False, f"il dettaglio non nomina il pain con pack: «{dett}»"
        if len(dett.splitlines()) != 1:
            return False, "il dettaglio occupa piu' di una riga"
        return True, f"il pack e' visto e il dettaglio lo dice: «{dett}»"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_ac():
    """Mandati e `_giro*` NON contano: accanto a un pack, il conteggio resta uno.

    Senza questa clausola la riparazione piu' comoda — guardare se `02-evidence/` e' vuota —
    avrebbe dichiarato presente l'evidenza di un run che ha soltanto mandati.
    """
    base = tempfile.mkdtemp()
    try:
        run_dir = albero_b1(base, {"pains_to_evidence": 1}, pack=["P-003-community.json"],
                            mandati=[("P-003", "community"), ("P-004", "normativa")],
                            giri=[("_giro1", "P-003-community.json"),
                                  ("_giro2", "P-004-normativa.json")])
        u = esegui(base, run_dir)
        l = _b1_riga(u)
        if not l.strip().startswith("ok"):
            return False, f"«{l.strip()}» — con un pack e stats=1 la post-condizione deve passare"
        confronto = riga(u, "stats.pains_to_evidence")
        if confronto and not confronto.strip().startswith("ok"):
            return False, (f"«{confronto.strip()}» — mandati o `_giro*` sono entrati nel "
                           "conteggio dei pain con evidenza")
        # La frase deve nominare UN solo pain: se i mandati o i `_giro*` fossero contati,
        # comparirebbe anche `P-004`, che ha soltanto un mandato.
        dett = _b1_dett(u)
        if "P-004" in dett:
            return False, f"il dettaglio conta un pain che ha solo un mandato: «{dett}»"
        if "P-003" not in dett or "ma nessun" in dett:
            return False, f"il dettaglio non descrive il disco: «{dett}»"
        return True, (f"quattro file annidati accanto a un pack: contano zero — «{dett}»")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_ad():
    """`pains_to_evidence == 0` e nessun pack: l'assenza e' corretta, nessuna post-condizione."""
    base = tempfile.mkdtemp()
    try:
        run_dir = albero_b1(base, {"pains_to_evidence": 0},
                            mandati=[("P-003", "community")])
        u = esegui(base, run_dir)
        l = _b1_riga(u)
        if l and not l.strip().startswith("ok"):
            return False, f"«{l.strip()}» — con zero pain dichiarati l'assenza di pack e' corretta"
        return True, "stats a zero e nessun pack: nessuna post-condizione fallisce per B-1"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_ae():
    """La post-condizione e' GRAVE, e pesa esattamente uno.

    I due run hanno lo STESSO disco — stessi mandati, stessa diagnostica, nessun pack — e
    differiscono per la sola cifra dichiarata in `00-run-stats.json`: `1` contro `0`. Variare il
    disco invece della dichiarazione confronterebbe due alberi diversi, e il delta misurato
    porterebbe con se' gli effetti collaterali del file aggiunto: misurato `-1` invece di `+1`,
    perche' il pack in piu' accende altre post-condizioni che non c'entrano con questa.

    La differenza sui `gravi` dev'essere `1`: zero significherebbe un avviso che non ferma la
    chiusura, piu' di uno un controllo non attribuibile.
    """
    base = tempfile.mkdtemp()
    try:
        uno = albero_b1(os.path.join(base, "u"), {"pains_to_evidence": 1},
                        mandati=[("P-003", "community")],
                        giri=[("_giro1", "P-003-community.json")])
        zero = albero_b1(os.path.join(base, "z"), {"pains_to_evidence": 0},
                         mandati=[("P-003", "community")],
                         giri=[("_giro1", "P-003-community.json")])
        u_1, c_1 = esegui(os.path.join(base, "u"), uno, con_codice=True)
        u_0, c_0 = esegui(os.path.join(base, "z"), zero, con_codice=True)
        g_1, a_1 = _b1_gravi(u_1)
        g_0, a_0 = _b1_gravi(u_0)
        if g_1 is None or g_0 is None:
            return False, "la riga VERDETTO non e' leggibile in una delle due corse"
        if g_1 - g_0 != 1:
            return False, (f"gravi {g_1} con stats=1 contro {g_0} con stats=0: differenza "
                           f"{g_1 - g_0}, attesa 1")
        if a_1 != a_0:
            return False, f"gli avvisi cambiano ({a_1} contro {a_0}): il controllo non e' grave"
        if c_1 == 0:
            return False, f"uscita {c_1} con stats=1 e nessun pack: il run chiuderebbe validamente"
        return True, (f"stesso disco, sola cifra dichiarata: gravi {g_0} -> {g_1}, avvisi {a_0} "
                      f"invariati, uscite {c_0} e {c_1}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_ag():
    """`00-run-stats.json` valido come JSON ma NON dizionario: uscita 2 pulita, nessun traceback.

    E' la regressione che la prima stesura del cancello aveva introdotto. Il predicato sulla
    dichiarazione era ripetuto in linea — `d.get(...)` piu' due `isinstance` — e su una lista,
    una stringa o un numero `d.get` solleva `AttributeError`: misurato il passaggio da `exit 2`
    pulito a `exit 1` con traceback, cioe' un run con statistiche corrotte declassato da NON
    VALIDO a DEGRADATO. Il rimedio e' una lettura sola, `pains_dichiarati`, che rende `None` su
    qualunque forma non intera.
    """
    for etichetta, contenuto in (("lista", "[1, 2, 3]"), ("stringa", '"ciao"'),
                                 ("numero", "7"), ("null", "null")):
        base = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(base, "tools"), exist_ok=True)
            for f in ("run_close.py", "reconcile_run.py"):
                shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(base, "tools", f))
            run_dir = os.path.join(base, "runs", RUN_ID)
            os.makedirs(os.path.join(run_dir, "02-evidence"), exist_ok=True)
            with open(os.path.join(run_dir, "00-run-stats.json"), "w", encoding="utf-8") as g:
                g.write(contenuto)
            u, c = esegui(base, run_dir, con_codice=True)
            if "Traceback" in u:
                return False, (f"statistiche `{etichetta}`: traceback invece di un verdetto\n"
                               + u[-200:].replace("\n", " "))
            if c != 2:
                return False, (f"statistiche `{etichetta}`: uscita {c}, atteso 2 — un run con "
                               "statistiche corrotte non e' DEGRADATO, e' NON VALIDO")
        finally:
            shutil.rmtree(base, ignore_errors=True)
    return True, "lista, stringa, numero e null: uscita 2 pulita, nessun traceback"


def caso_af():
    """Il prompt e i lettori dichiarano la STESSA regola.

    Una regola scritta in prosa e implementata nel codice sono due copie destinate a separarsi:
    e' la classe di difetto che questo repository insegue da dodici versioni, e qui i due lati
    sono a due alberi di distanza — `.claude/commands/deepsearch.md` e `tools/`. Questo caso li
    tiene legati per COSTANTE: non cerca frasi, cerca i simboli che i lettori usano davvero.

    Se qualcuno spostasse la cartella dell'evidenza o allargasse il glob, la prosa resterebbe
    vera in apparenza e falsa nei fatti; qui invece cade.
    """
    prompt = os.path.join(ROOT, ".claude", "commands", "deepsearch.md")
    if not os.path.exists(prompt):
        return False, "il prompt dell'orchestratore non e' al suo posto"
    testo = open(prompt, encoding="utf-8").read()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import reconcile_run as rr                                        # noqa: PLC0415
    manca = []
    # la cartella e la forma del pack, prese dalle costanti dei lettori
    if f"{rr.CARTELLA_EVIDENZA}/P-<id>-<canale>.json" not in testo:
        manca.append(f"la forma del pack `{rr.CARTELLA_EVIDENZA}/P-<id>-<canale>.json`")
    # le due forme che NON sono evidenza, entrambe nominate
    if "mandato-<canale>.json" not in testo:
        manca.append("il mandato annidato")
    if "_giro" not in testo:
        manca.append("la diagnostica `_giro*`")
    # il fail-closed, nei termini in cui i due lettori lo applicano
    if "pains_to_evidence > 0" not in testo:
        manca.append("la condizione `pains_to_evidence > 0`")
    if "pains_to_evidence == 0" not in testo:
        manca.append("il caso legittimo `pains_to_evidence == 0`")
    if manca:
        return False, "il prompt non dichiara: " + "; ".join(manca)
    # e il comportamento: la regola scritta e' quella che il lettore applica
    base = tempfile.mkdtemp()
    try:
        run_dir = albero_b1(base, {"pains_to_evidence": 1}, mandati=[("P-003", "community")])
        if rr.pain_con_pack(run_dir):
            return False, "il lettore conta come pack un mandato annidato: prosa e codice divergono"
        run_dir2 = albero_b1(os.path.join(base, "2"), {"pains_to_evidence": 1},
                             pack=["P-003-community.json"])
        if rr.pain_con_pack(run_dir2) != {"P-003"}:
            return False, "il lettore non riconosce il pack che la prosa prescrive"
        return True, "prompt e lettori dichiarano la stessa regola, e il lettore la applica"
    finally:
        shutil.rmtree(base, ignore_errors=True)


CASI = [
    ("A", "IC-3 conta i soli oggetti Claim", caso_a),
    ("B", "la guardia grave resta larga e vede anche i non-Claim", caso_b),
    ("C", "IC-5: i 36 claim reali → 15 / 6 / 14 / 1 / 0, per id", caso_c),
    ("D", "IC-5: senza `promoted_by` e promuovibile → NON_FALSIFICATA", caso_d),
    ("E", "IC-5: senza `promoted_by` e non promuovibile → CERTAMENTE_FALSA", caso_e),
    ("F", "IC-5: `PARTIAL` non conta come letta", caso_f),
    ("G", "IC-5: una fonte mai letta non concorre alla cardinalita'", caso_g),
    ("H", "IC-5: FP5 non e' pronunciabile → SOLO_FP5", caso_h),
    ("I", "IC-5: monotonia — l'ignoto non toglie evidenza", caso_i),
    ("J", "IC-5: DATO_INSUFFICIENTE morde solo se l'evidenza nota non basta", caso_j),
    ("K", "IC-5: il codice piu' basso fra i soddisfatti", caso_k),
    ("L", "IC-5: FP4 al limite, il confronto e' `>=`", caso_l),
    ("M", "IC-5: il predicato guarda SOLO CERTAMENTE_FALSA", caso_m),
    ("N", "IC-5: `grave=False` — verdetto, gravi ed exit code invariati", caso_n),
    ("O", "IC-5: `contradictory_evidence[]` e' percorso quanto `claims[]`", caso_o),
    ("P", "IC-5: formato — letti T1..T5, blocco dopo la tabella", caso_p),
    ("Q", "IC-5: determinismo del blocco", caso_q),
    ("R", "IC-5: per percorso, non per forma — i Numeric non sono claim", caso_r),
    ("S", "IC-5: il `dett` del check e' di una riga sola", caso_s),
    ("T", "IC-5: ammissione — `payload` non dizionario non contribuisce claim", caso_t),
    ("U", "IC-5: NON_FALSIFICATA e' il caso silente nel dettaglio", caso_u),
    ("V", "IC-5: un `source_id` ripetuto non raggiunge la cardinalita'", caso_v),
    ("W", "IC-5: si giudicano i soli VERIFIED", caso_w),
    ("X", "IC-5: il dettaglio segue l'ordine di documento", caso_x),
    ("Y", "IC-5: SOLO_FP5 non precede la promuovibilita' e guarda le T5 lette", caso_y),
    ("Z", "IC-5: la nota dichiara il codice MINIMO", caso_z),
    ("AA", "B-1: stats positive e soli mandati/diagnostica -> post-condizione grave", caso_aa),
    ("AB", "B-1: il pack canonico a un livello e' visto", caso_ab),
    ("AC", "B-1: `mandato-*` e `_giro*` non contano come evidenza", caso_ac),
    ("AD", "B-1: stats a zero senza pack -> l'assenza e' corretta", caso_ad),
    ("AE", "B-1: la post-condizione e' grave e pesa esattamente uno", caso_ae),
    ("AF", "B-1: la regola del prompt e il comportamento dei lettori coincidono", caso_af),
    ("AG", "B-1: statistiche non-dizionario -> uscita 2 pulita, nessun traceback", caso_ag),
]


def main():
    print("\n  run_close — autotest")
    print(f"  {'-' * 78}")
    falliti = []
    for lettera, titolo, fn in CASI:
        try:
            ok, dettaglio = fn()
        except Exception as e:                                   # noqa: BLE001
            ok, dettaglio = False, f"{type(e).__name__}: {e}"
        print(f"    {'ok   ' if ok else 'FAIL '} {lettera}. {titolo}")
        print(f"           {dettaglio}")
        if not ok:
            falliti.append(lettera)
    print(f"  {'-' * 78}")
    print(f"  totale: {len(CASI) - len(falliti)}/{len(CASI)}\n")
    if falliti:
        print("  >> le due proprieta' tirano in direzioni opposte: se una cade, verificare che")
        print("     la riparazione dell'altra non l'abbia mangiata.\n")
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
