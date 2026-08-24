#!/usr/bin/env python3
"""
replay_report_selftest.py — dimostra che replay_report.py NON e' cieco.

QUATTRO PROPRIETA' DA PROVARE, e sono tutte proprieta' che un driver puo' avere solo per
costruzione, non per buona volonta':

  1. L'ELENCO DEI CANALI VIENE DAL MANIFESTO, non dal glob dei raw. Se venisse dal glob, un
     canale che non ha prodotto NULLA sarebbe invisibile — e quello e' precisamente il caso che
     il driver esiste per registrare. Un driver che scopre i canali dai file prodotti non puo'
     dire `NON_VALUTABILE`: puo' solo tacere.
  2. IL VERDETTO PIU' GRAVE VINCE. Un pack invalido rende il run `NON_VALIDO` anche se accanto
     c'e' un canale non valutabile. Il contrario — un canale muto che zittisce un `null` illecito
     — e' il difetto `R1` di `replay_validate.py` promosso di un livello, e sarebbe la fine della
     catena che questi strumenti compongono.
  3. NON SI SCRIVE SU CIO' CHE NON E' PROPRIO. Il divieto non e' agganciato al nome
     `03-validazione.json` — un divieto per nome protegge un nome — ma alla MARCA nel file: se
     esiste ed e' di qualcun altro, ci si ferma senza toccarlo.
  4. L'ORDINE null-check → schema E' EREDITATO. Il driver importa `replay_validate` e ne chiama
     `main`, invece di rifare i due passi: cosi' la garanzia provata da `V13` vale anche qui, e
     non e' ristabilita da capo con la speranza di non sbagliarla.

IL PACK ROTTO E' QUELLO VERO, come in `replay_validate_selftest.py`: il frammento verbatim di
`trend_adozione` del replay 20260823-1647, `amount: null`. IL CONTRATTO NON E' SCRITTO A MANO:
si rigenera con `contract_compile.py --vista delivery`. Lo si genera UNA volta per processo e lo
si riusa: resta una sola fonte, e le venti prove non pagano venti sottoprocessi.

NESSUNA PROVA LEGGE O SCRIVE `runtime/`. Ogni run di prova e' costruito in una directory
temporanea e rimosso.

  uso:  python tools/replay_report_selftest.py
        exit 0 = tutte verdi
"""
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

import replay_report as rr                                                # noqa: E402

ESITI = []
_CONTRATTO = None
USCITA = "05-validazione-rieseguibile.json"


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


def contratto():
    """La vista delivery, generata dal compilatore. Mai una fixture scritta a mano."""
    global _CONTRATTO
    if _CONTRATTO is None:
        r = subprocess.run([sys.executable, os.path.join(QUI, "contract_compile.py"),
                            "--agente", "evidence-researcher", "--vista", "delivery"],
                           capture_output=True, timeout=120)
        assert r.returncode == 0, f"il contratto non si genera: {r.stderr.decode('utf-8','replace')}"
        _CONTRATTO = r.stdout.decode("utf-8")
    return _CONTRATTO


# ------------------------------------------------------------------ i pack

def pack_valido():
    """Un evidence-pack conforme, con i soli null AMMESSI dal contratto."""
    return {"agent": "evidence-researcher", "run_id": "20260823-1647", "status": "OK",
            "budget_used": {"searches": 1, "searches_limit": 8,
                            "fetches": 1, "fetches_limit": 16},
            "stop_reason": "COMPLETED",
            "payload": {"pain_id": "P-003", "channel": "trend_adozione",
                        "claims": [{"promoted_by": "FP0", "id": "C-r22t-001",
                                    "statement": {"subject": "s", "observed": "o",
                                                  # `quantity` e' `o~`: null AMMESSO
                                                  "quantity": None, "period": None},
                                    "state": "HYPOTHESIS", "source_ids": []}],
                        "q4_answer": {"question_id": "Q4", "answer": "a", "grounded": False,
                                      "source_ids": []},
                        "coverage": {"angles_covered": [], "gaps": []}},
            "new_sources": [], "queries_run": [], "unreachable_sources": [],
            "self_check": {"invarianti_violati": []}}


def pack_reale_rotto():
    """Il pack valido piu' il frammento VERBATIM emesso da trend_adozione nel replay."""
    p = pack_valido()
    p["payload"]["spend_evidence"] = [
        {"claim_id": "C-r22t-002", "spend_form": "software", "amount": None}]
    return p


def raw_da(pack):
    """Un raw come lo produce il CLI: prosa attorno, JSON dentro un blocco ```json."""
    return ("Based on my research, I'll now compile the evidence pack:\n\n"
            "```json\n" + json.dumps(pack, ensure_ascii=False, indent=2) + "\n```\n")


RAW_VALIDO = None
RAW_ROTTO = None


# ------------------------------------------------------------------ il run di prova

def scrivi(percorso, testo):
    with open(percorso, "w", encoding="utf-8", newline="\n") as f:
        f.write(testo)


def crea_run(canali, contenuti, con_contratto=True, con_manifesto=True, manifesto=None):
    """Un replay finto ma completo.

    `canali`   e' l'elenco ORDINATO che finisce nel manifesto.
    `contenuti` mappa nome-canale -> testo del raw. Un canale ASSENTE dalla mappa non ha file:
                e' il caso «canale mancante», che il glob non vedrebbe mai.
    """
    d = tempfile.mkdtemp(prefix="rr-selftest-")
    os.makedirs(os.path.join(d, "02-output"))
    os.makedirs(os.path.join(d, "_contracts"))
    if con_manifesto:
        man = manifesto if manifesto is not None else {
            "run_id": "20260823-1647",
            "commit_di_provenienza": "8894c3b54fe530e7126cf311e51a337b8bef94bf",
            "canali": [{"channel": c} for c in canali],
            "validazione": "03-validazione.json"}
        scrivi(os.path.join(d, "00-replay-manifest.json"),
               json.dumps(man, ensure_ascii=False, indent=2) + "\n")
    for nome, testo in contenuti.items():
        scrivi(os.path.join(d, "02-output", nome + ".raw.txt"), testo)
    if con_contratto:
        scrivi(os.path.join(d, "_contracts", "evidence-researcher.delivery.txt"), contratto())
    # due artefatti preesistenti, che il driver non deve toccare mai
    scrivi(os.path.join(d, "03-validazione.json"),
           json.dumps({"run_id": "20260823-1647", "canali_validati": 3,
                       "totale_errori": 4, "canali": []}, ensure_ascii=False, indent=2) + "\n")
    scrivi(os.path.join(d, "04-rapporto.md"), "# rapporto composto a mano\n")
    return d


def esegui(d, extra=()):
    r = subprocess.run([sys.executable, os.path.join(QUI, "replay_report.py"), "--run", d]
                       + list(extra), capture_output=True, timeout=600)
    return (r.returncode, r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))


def leggi_uscita(d):
    with open(os.path.join(d, USCITA), "rb") as f:
        b = f.read()
    return b, json.loads(b.decode("utf-8"))


def impronte(d):
    """sha256 di ogni file del run, per percorso relativo. Serve a provare cosa NON e' cambiato."""
    out = {}
    for radice, _, files in os.walk(d):
        for n in files:
            p = os.path.join(radice, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, d).replace("\\", "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


def canale(rap, nome):
    for c in rap["canali"]:
        if c["canale"] == nome:
            return c
    raise AssertionError(f"canale {nome} assente dal rapporto: "
                         f"{[c['canale'] for c in rap['canali']]}")


CINQUE = ["normativa", "statistica_ufficiale", "community", "pricing_mercato", "trend_adozione"]


def _init():
    global RAW_VALIDO, RAW_ROTTO
    RAW_VALIDO = raw_da(pack_valido())
    RAW_ROTTO = raw_da(pack_reale_rotto())


# ------------------------------------------------------------------ le prove

@prova("R1", "manifesto assente: il driver non puo' operare, esce 2 e non scrive nulla")
def r1():
    d = crea_run(["community"], {"community": RAW_VALIDO}, con_manifesto=False)
    try:
        codice, _, err = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2. stderr: {err[:300]}"
        assert not os.path.exists(os.path.join(d, USCITA)), "ha scritto pur non potendo operare"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R2", "manifesto senza `canali`: esce 2, non inventa un elenco")
def r2():
    d = crea_run([], {}, manifesto={"run_id": "x", "commit_di_provenienza": "y"})
    try:
        codice, _, err = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2. stderr: {err[:300]}"
        assert not os.path.exists(os.path.join(d, USCITA))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R3", "contratto assente: esce 2 invece di validare senza contratto")
def r3():
    d = crea_run(["community"], {"community": RAW_VALIDO}, con_contratto=False)
    try:
        codice, _, err = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2. stderr: {err[:300]}"
        assert not os.path.exists(os.path.join(d, USCITA))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R4", "l'ORDINE dei canali e' quello del manifesto, non alfabetico e non di scoperta")
def r4():
    # ordine deliberatamente non alfabetico e non uguale all'ordine di creazione dei file
    ordine = ["trend_adozione", "community", "normativa", "pricing_mercato"]
    d = crea_run(ordine, {"community": RAW_VALIDO, "normativa": "",
                          "pricing_mercato": RAW_VALIDO, "trend_adozione": RAW_VALIDO})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        letti = [c["canale"] for c in rap["canali"]]
        assert letti == ordine, f"{letti} != {ordine}"
        assert letti != sorted(letti), "premessa della prova persa: l'ordine e' alfabetico"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R5", "canale MANCANTE (nessun file): NON_VALUTABILE, con motivo ed exit 2 di canale")
def r5():
    d = crea_run(["community", "normativa"], {"community": RAW_VALIDO})   # normativa non esiste
    try:
        codice, _, _ = esegui(d)
        _, rap = leggi_uscita(d)
        c = canale(rap, "normativa")
        assert c["esito"] == "NON_VALUTABILE", c
        assert c["exit"] == 2, c
        assert c.get("motivo"), "un NON_VALUTABILE senza motivo e' un'omissione con un'etichetta"
        assert "amount" not in json.dumps(c)
        assert codice == 1, f"uscita {codice}, atteso 1 (PARZIALE)"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R6", "canale con raw VUOTO (il caso reale `normativa`): NON_VALUTABILE, non zero errori")
def r6():
    d = crea_run(["community", "normativa"], {"community": RAW_VALIDO, "normativa": ""})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        c = canale(rap, "normativa")
        assert c["esito"] == "NON_VALUTABILE", c
        assert c["exit"] == 2, c
        assert c.get("motivo"), c
        assert "n_errori" not in c, "un canale non valutato non ha un conteggio di errori"
        assert c.get("byte_raw") == 0, c        # il file c'e' ed e' vuoto: distinto dall'assente
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R7", "COMPLETO / exit 0: tutti i canali valutati e validi")
def r7():
    d = crea_run(["community", "pricing_mercato"],
                 {"community": RAW_VALIDO, "pricing_mercato": RAW_VALIDO})
    try:
        codice, _, err = esegui(d)
        _, rap = leggi_uscita(d)
        assert rap["esito_run"] == "COMPLETO", (rap["esito_run"], err[:300])
        assert codice == 0, f"uscita {codice}, atteso 0"
        assert rap["canali_non_valutabili"] == 0 and rap["totale_errori"] == 0, rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R8", "PARZIALE / exit 1: un NON_VALUTABILE e nessun pack invalido")
def r8():
    d = crea_run(["community", "normativa"], {"community": RAW_VALIDO, "normativa": ""})
    try:
        codice, _, _ = esegui(d)
        _, rap = leggi_uscita(d)
        assert rap["esito_run"] == "PARZIALE", rap["esito_run"]
        assert codice == 1, f"uscita {codice}, atteso 1"
        assert rap["totale_errori"] == 0, rap        # nessun errore, ma non e' COMPLETO
        assert rap["canali_non_valutabili"] == 1, rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R9", "NON_VALIDO / exit 1: il pack invalido VINCE sul canale non valutabile")
def r9():
    d = crea_run(["normativa", "trend_adozione"],
                 {"normativa": "", "trend_adozione": RAW_ROTTO})
    try:
        codice, _, _ = esegui(d)
        _, rap = leggi_uscita(d)
        assert rap["esito_run"] == "NON_VALIDO", rap["esito_run"]
        assert codice == 1, f"uscita {codice}, atteso 1"
        assert rap["canali_non_valutabili"] == 1, rap
        c = canale(rap, "trend_adozione")
        assert c["null_check"]["esito"] == "RIFIUTATO", c["null_check"]
        assert any(i["percorso"] == "payload/spend_evidence[]/amount"
                   for i in c["null_check"]["illeciti"]), c["null_check"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R10", "`canali_non_valutabili` e `totale_errori` sono separati e non si coprono")
def r10():
    d = crea_run(["normativa", "statistica_ufficiale", "community", "trend_adozione"],
                 {"normativa": "", "statistica_ufficiale": "Errore del CLI, nessun output.\n",
                  "community": RAW_VALIDO, "trend_adozione": RAW_ROTTO})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        assert rap["canali_dichiarati"] == 4, rap
        assert rap["canali_valutati"] == 2, rap
        assert rap["canali_non_valutabili"] == 2, rap
        somma = sum(c["schema"]["n_errori"] for c in rap["canali"] if "schema" in c)
        assert rap["totale_errori"] == somma, (rap["totale_errori"], somma)
        assert rap["canali_valutati"] + rap["canali_non_valutabili"] == rap["canali_dichiarati"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R11", "uscita PREESISTENTE e ALTRUI (senza marca): esce 2 e non la tocca")
def r11():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        estraneo = json.dumps({"run_id": "20260823-1647", "canali_validati": 3,
                               "totale_errori": 4}, ensure_ascii=False, indent=2) + "\n"
        scrivi(os.path.join(d, USCITA), estraneo)
        prima = impronte(d)
        codice, _, err = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2. stderr: {err[:300]}"
        assert impronte(d) == prima, "ha toccato un file che non e' suo"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R12", "il divieto e' sulla MARCA, non sul nome: anche `03-validazione.json` e' protetto")
def r12():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        prima = impronte(d)
        codice, _, err = esegui(d, ["--out", os.path.join(d, "03-validazione.json")])
        assert codice == 2, f"uscita {codice}, atteso 2. stderr: {err[:300]}"
        assert impronte(d) == prima, "ha riscritto 03-validazione.json"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R13", "uscita preesistente NON-JSON: esce 2 e non la tocca")
def r13():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        scrivi(os.path.join(d, USCITA), "questo non e' JSON\n")
        prima = impronte(d)
        codice, _, _ = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert impronte(d) == prima, "ha toccato un file che non e' suo"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R14", "uscita PROPRIA (con marca): rigenerata davvero, non lasciata com'era")
def r14():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        codice, _, err = esegui(d)
        assert codice == 0, f"prima esecuzione: uscita {codice}. stderr: {err[:300]}"
        b1, rap1 = leggi_uscita(d)
        assert rap1.get("marca") == "replay_report", "l'uscita non porta la propria marca"
        # la si guasta CONSERVANDO la marca: solo una vera riscrittura la rimette a posto
        rap1["esito_run"] = "GUASTO"
        scrivi(os.path.join(d, USCITA), json.dumps(rap1, ensure_ascii=False, indent=2) + "\n")
        codice, _, _ = esegui(d)
        assert codice == 0, f"seconda esecuzione: uscita {codice}"
        b2, rap2 = leggi_uscita(d)
        assert rap2["esito_run"] == "COMPLETO", "non ha rigenerato: ha lasciato il guasto"
        assert b2 == b1, "rigenerato ma con byte diversi a parita' d'ingresso"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R15", "scrittura ATOMICA: se `os.replace` fallisce, la destinazione resta intatta")
def r15():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        assert esegui(d)[0] == 0
        prima = impronte(d)
        originale = prima[USCITA]

        vero = rr.os.replace
        visti = []

        def replace_rotto(a, b):
            visti.append((a, b))
            raise OSError("disco pieno (iniettato)")

        rr.os.replace = replace_rotto
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                codice = rr.main(["--run", d])
        finally:
            rr.os.replace = vero

        assert codice == 2, f"uscita {codice}, atteso 2 (il driver non ha potuto operare)"
        # un solo `replace`, e la sorgente sta ACCANTO alla destinazione: un temporaneo su un
        # altro volume renderebbe il rename non atomico, e nessuna prova se ne accorgerebbe
        assert len(visti) == 1, f"{len(visti)} chiamate a os.replace, attesa una"
        assert visti[0] == (os.path.join(d, USCITA) + ".parziale", os.path.join(d, USCITA)), visti
        dopo = impronte(d)
        assert dopo[USCITA] == originale, "la destinazione e' stata toccata da una scrittura fallita"
        residui = [k for k in dopo if k not in prima]
        assert not residui, f"residui di scrittura lasciati in giro: {residui}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R16", "l'ordine null_check → schema e' EREDITATO da replay_validate, non rifatto")
def r16():
    import null_check as nc
    import replay_validate as rv
    d = crea_run(["community", "pricing_mercato"],
                 {"community": RAW_VALIDO, "pricing_mercato": RAW_VALIDO})
    sequenza = []
    vero_null, vero_schema = nc.controlla, rv.valida_schema

    def spia_null(*a, **k):
        sequenza.append("null_check")
        return vero_null(*a, **k)

    def spia_schema(*a, **k):
        sequenza.append("schema")
        return vero_schema(*a, **k)

    try:
        nc.controlla, rv.valida_schema = spia_null, spia_schema
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            codice = rr.main(["--run", d])
        assert codice == 0, f"uscita {codice}"
        assert sequenza == ["null_check", "schema", "null_check", "schema"], sequenza
    finally:
        nc.controlla, rv.valida_schema = vero_null, vero_schema
        shutil.rmtree(d, ignore_errors=True)


@prova("R17", "la baseline viene dal MANIFESTO, non da un argomento e non da un default")
def r17():
    d = crea_run(["community"], {"community": RAW_VALIDO},
                 manifesto={"run_id": "20990101-0000",
                            "commit_di_provenienza": "cafebabe" * 5,
                            "canali": [{"channel": "community"}]})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        assert rap["baseline"] == "cafebabe" * 5, rap["baseline"]
        assert rap["run_id"] == "20990101-0000", rap["run_id"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R18", "un raw NON dichiarato nel manifesto e' ignorato: il glob non e' la fonte")
def r18():
    d = crea_run(["community"], {"community": RAW_VALIDO, "canale_fantasma": RAW_ROTTO})
    try:
        codice, _, _ = esegui(d)
        _, rap = leggi_uscita(d)
        nomi = [c["canale"] for c in rap["canali"]]
        assert nomi == ["community"], nomi
        assert rap["esito_run"] == "COMPLETO" and codice == 0, rap["esito_run"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R19", "non tocca NESSUN artefatto preesistente: solo il proprio file compare")
def r19():
    d = crea_run(CINQUE, {"normativa": "", "statistica_ufficiale": "errore\n",
                          "community": RAW_VALIDO, "pricing_mercato": RAW_VALIDO,
                          "trend_adozione": RAW_ROTTO})
    try:
        prima = impronte(d)
        esegui(d)
        dopo = impronte(d)
        nuovi = set(dopo) - set(prima)
        assert nuovi == {USCITA}, f"file nuovi inattesi: {nuovi}"
        for k in prima:
            assert dopo[k] == prima[k], f"artefatto preesistente modificato: {k}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R20", "determinismo e codifica: due esecuzioni, stessi byte; UTF-8, LF, un solo newline")
def r20():
    d = crea_run(["community", "normativa"], {"community": RAW_VALIDO, "normativa": ""})
    try:
        esegui(d)
        b1, _ = leggi_uscita(d)
        esegui(d)
        b2, _ = leggi_uscita(d)
        assert b1 == b2, "due esecuzioni sullo stesso ingresso danno byte diversi"
        assert b1[:3] != b"\xef\xbb\xbf", "BOM"
        assert b"\r\n" not in b1, "CRLF"
        assert b1.endswith(b"\n") and not b1.endswith(b"\n\n"), "newline finale"
        b1.decode("utf-8")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------- il RECORD deve dire la verita', non solo esistere
#
# Le prove fin qui verificano il COMPORTAMENTO: verdetti, uscite, quali file vengono toccati.
# Ma il file prodotto e' l'unica cosa che sopravvive al run, e in una catena in cui gli atti si
# citano per hash un record che puo' dichiarare digest inventati non e' un atto di evidenza. Le
# prove che seguono verificano il CONTENUTO.

@prova("R21", "un canale ILLEGGIBILE non annulla il run: e' NON_VALUTABILE, e il file si scrive")
def r21():
    d = crea_run(["trend_adozione", "sporco"], {"trend_adozione": RAW_ROTTO})
    try:
        # byte che non sono UTF-8: e' cio' che un CLI puo' emettere, e non e' un guasto del driver
        with open(os.path.join(d, "02-output", "sporco.raw.txt"), "wb") as f:
            f.write(b'```json\n{"agent": "\xff\xfe"}\n```\n')
        codice, _, err = esegui(d)
        assert os.path.exists(os.path.join(d, USCITA)), \
            f"il canale illeggibile ha annullato il run e non ha scritto nulla. stderr: {err[-400:]}"
        _, rap = leggi_uscita(d)
        c = canale(rap, "sporco")
        assert c["esito"] == "NON_VALUTABILE" and c["exit"] == 2, c
        assert "UnicodeDecodeError" in c["motivo"], c["motivo"]
        # e soprattutto: il verdetto gia' preso sull'altro canale NON e' stato buttato
        assert rap["esito_run"] == "NON_VALIDO" and codice == 1, (rap["esito_run"], codice)
        t = canale(rap, "trend_adozione")
        assert any(i["percorso"] == "payload/spend_evidence[]/amount"
                   for i in t["null_check"]["illeciti"]), t["null_check"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R22", "`sha256_raw`, `byte_raw` e `raw` sono quelli VERI, non valori fabbricati")
def r22():
    d = crea_run(["community", "normativa"], {"community": RAW_VALIDO, "normativa": ""})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        for c in rap["canali"]:
            p = os.path.join(d, *c["raw"].split("/"))
            assert os.path.isfile(p), f"il campo `raw` non punta a un file: {c['raw']}"
            with open(p, "rb") as f:
                b = f.read()
            assert c["sha256_raw"] == hashlib.sha256(b).hexdigest(), \
                f"sha256_raw fabbricato su {c['canale']}"
            assert c["byte_raw"] == len(b), f"byte_raw fabbricato su {c['canale']}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R23", "`sha256_contratto` e `contratto` descrivono il contratto REALMENTE usato")
def r23():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        altro = os.path.join(d, "contratto-altro.txt")
        scrivi(altro, contratto())
        esegui(d, ["--contratto", altro])
        _, rap = leggi_uscita(d)
        with open(altro, "rb") as f:
            atteso = hashlib.sha256(f.read()).hexdigest()
        assert rap["sha256_contratto"] == atteso, "sha256_contratto fabbricato"
        assert rap["contratto"] == "contratto-altro.txt", \
            f"il record dichiara un contratto che non ha usato: {rap['contratto']}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R24", "il campo `ordine` non mente: e' la sequenza davvero eseguita")
def r24():
    import null_check as nc
    import replay_validate as rv
    d = crea_run(["community"], {"community": RAW_VALIDO})
    sequenza = []
    vero_null, vero_schema = nc.controlla, rv.valida_schema
    try:
        nc.controlla = lambda *a, **k: (sequenza.append("null_check"), vero_null(*a, **k))[1]
        rv.valida_schema = lambda *a, **k: (sequenza.append("schema"), vero_schema(*a, **k))[1]
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            assert rr.main(["--run", d]) == 0
    finally:
        nc.controlla, rv.valida_schema = vero_null, vero_schema
    try:
        _, rap = leggi_uscita(d)
        assert rap["ordine"] == sequenza, f"dichiara {rap['ordine']}, ha eseguito {sequenza}"
        assert rap["ordine"] == rv.ORDINE, rap["ordine"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R25", "ogni canale valutato porta ENTRAMBI i blocchi, e `canali_non_validi` conta davvero")
def r25():
    d = crea_run(["community", "trend_adozione", "normativa"],
                 {"community": RAW_VALIDO, "trend_adozione": RAW_ROTTO, "normativa": ""})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        for c in rap["canali"]:
            if c["esito"] == "NON_VALUTABILE":
                continue
            assert "null_check" in c and "schema" in c, \
                f"{c['canale']}: la misura di schema sparisce quando il verdetto e' gia' deciso"
            assert "esito" in c["null_check"] and "n_errori" in c["schema"], c
            assert c["null_check"]["decisivo"] is True, \
                f"{c['canale']}: il record dichiara il null-check non decisivo"
        atteso = sum(1 for c in rap["canali"] if c["esito"] == "NON VALIDO")
        assert rap["canali_non_validi"] == atteso == 1, (rap["canali_non_validi"], atteso)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R26", "il `motivo` e' propagato e SPECIFICO del canale, non una costante educata")
def r26():
    import replay_validate as rv
    d = crea_run(["assente", "vuoto"], {"vuoto": ""})
    try:
        esegui(d)
        _, rap = leggi_uscita(d)
        m_assente = canale(rap, "assente")["motivo"]
        m_vuoto = canale(rap, "vuoto")["motivo"]
        assert m_assente != m_vuoto, \
            f"due cause diverse, un solo motivo: {m_assente!r}"
        # e quello del raw vuoto e' PROPRIO la frase di replay_validate, non una parafrasi
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rv.main(["--raw", os.path.join(d, "02-output", "vuoto.raw.txt"),
                     "--contratto", os.path.join(d, "_contracts",
                                                 "evidence-researcher.delivery.txt")])
        atteso = err.getvalue().strip()[len("guasto: "):]
        assert m_vuoto == atteso, f"{m_vuoto!r} != {atteso!r}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R27", "un `.parziale` preesistente e ALTRUI non viene distrutto di soppiatto")
def r27():
    d = crea_run(["community"], {"community": RAW_VALIDO})
    try:
        scrivi(os.path.join(d, USCITA + ".parziale"), "roba di qualcun altro\n")
        prima = impronte(d)
        codice, _, _ = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert impronte(d) == prima, "ha distrutto un file che non e' suo"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R28", "un nome di canale che esce dalla directory del replay e' rifiutato")
def r28():
    d = crea_run(["../../fuori"], {})
    try:
        codice, _, err = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2. stderr: {err[:200]}"
        assert not os.path.exists(os.path.join(d, USCITA))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R29", "schema indisponibile (il caso POSIX): il canale resta registrato, niente crash")
def r29():
    import replay_validate as rv
    d = crea_run(["community"], {"community": RAW_VALIDO})
    vero = rv.valida_schema
    try:
        def rotto(*a, **k):
            raise rv.Guasto("validazione di schema non disponibile su questa piattaforma")
        rv.valida_schema = rotto
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            codice = rr.main(["--run", d])
        assert codice == 1, f"uscita {codice}, atteso 1"
        _, rap = leggi_uscita(d)
        c = canale(rap, "community")
        assert c["esito"] == "NON VALIDO", c["esito"]
        assert c["schema"]["disponibile"] is False, c["schema"]
        assert c["null_check"]["esito"] == "AMMESSO", c["null_check"]
    finally:
        rv.valida_schema = vero
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ esecuzione

def main():
    _init()
    print(f"replay_report_selftest — {len(ESITI)} prove\n")
    falliti = []
    for nome, desc, f in ESITI:
        try:
            f()
            print(f"  [verde] {nome:<4} {desc}")
        except Exception as e:                                            # noqa: BLE001
            falliti.append((nome, e))
            print(f"  [ROSSO] {nome:<4} {desc}\n          {type(e).__name__}: {e}")
    print()
    if falliti:
        print(f"  {len(ESITI) - len(falliti)}/{len(ESITI)} verdi — ROSSE: "
              + ", ".join(n for n, _ in falliti))
        return 1
    print(f"  {len(ESITI)}/{len(ESITI)} verdi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
