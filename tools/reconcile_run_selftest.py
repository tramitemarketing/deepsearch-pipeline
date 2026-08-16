#!/usr/bin/env python3
"""
  Macchina di prova per la telemetria M0.2 di reconcile_run.py.

  Non e' un test del riconciliatore storico, che resta senza prove come lo era: e'
  la prova di cio' che M0.2 aggiunge — una riga per run nel ledger M0.2, un sidecar
  che dice com'e' andata la scrittura, e nessun effetto su tutto il resto.

  Come i due selftest gia' presenti in tools/: script autonomo, nessun runner,
  nessuna dipendenza. exit 0 = tutto passa.

    uso:  python3 tools/reconcile_run_selftest.py

  OGNI caso gira su una COPIA usa-e-getta del repo, con la root della telemetria
  deviata dal seam dentro la stessa directory temporanea. Nessun test tocca un run
  reale, il ledger reale o i ledger legacy di questo albero: la ragione non e'
  prudenza ma il vincolo che la root M0.2 sta FUORI dal repository, dove un
  rmtree della copia non arriverebbe.
"""
import ast, hashlib, json, os, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


QUI = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(QUI)
IGNORA = shutil.ignore_patterns("runs", "*.zip", "__pycache__", ".git")
LEDGER_M02 = "m0.2-ledger.jsonl"
SIDECAR = "00-telemetry-write.json"

RUN_A = "20260803-1204"
RUN_B = "20260803-1830"

esiti = []


def segna(nome, titolo, ok, dettaglio=""):
    esiti.append((nome, titolo, bool(ok), dettaglio))


# ---------------------------------------------------------------- banco di prova

class Banco:
    """
    Copia del repo + root telemetria deviata, entrambe dentro una sola temporanea.

    Il seam serve esattamente qui: reconcile_run.py calcola la root M0.2 due livelli
    sopra il proprio ROOT, e nella copia quei due livelli cadono fuori da cio' che
    rmtree rimuove. Senza deviarla, questa macchina scriverebbe nella root vera.
    """

    def __enter__(self):
        self.base = tempfile.mkdtemp(prefix="m02-")
        self.dst = os.path.join(self.base, "repo")
        shutil.copytree(ROOT, self.dst, ignore=IGNORA)
        self.tele = os.path.join(self.base, "telemetria")
        os.makedirs(self.tele, exist_ok=True)
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.base, ignore_errors=True)
        return False

    # -- fixture

    def crea_run(self, run_id, senza_origine=False, irraggiungibile=False):
        d = os.path.join(self.dst, "runs", run_id, "wave1")
        os.makedirs(d, exist_ok=True)
        sid = f"S-{run_id}-0001"
        art = {
            "agent": "evidence-researcher", "run_id": run_id, "status": "OK",
            "new_sources": [{"id": sid, "url": "https://esempio.invalid/a",
                             "publisher": "Esempio", "tier": "T2",
                             "access_status": "OK"}],
            "queries_run": [{"normalized_query": "alfa beta", "raw_query": "Alfa Beta",
                             "agent": "evidence-researcher", "n_results": 3},
                            {"normalized_query": "gamma delta", "raw_query": "Gamma Delta",
                             "agent": "evidence-researcher", "n_results": 1}],
            "unreachable_sources": [],
            "payload": {"claims": [{"id": "C-1", "state": "VERIFIED",
                                    "source_ids": [sid]}]},
        }
        if senza_origine:
            art["payload"]["claims"].append(
                {"id": "C-2", "state": "VERIFIED", "source_ids": [f"S-{run_id}-9999"]})
        if irraggiungibile:
            art["unreachable_sources"].append(
                {"source_id": f"S-{run_id}-0002", "url_or_target": "https://esempio.invalid/b",
                 "what": "pagina chiusa", "why": "PAYWALL"})
            art["payload"]["claims"].append(
                {"id": "C-3", "state": "VERIFIED", "source_ids": [f"S-{run_id}-0002"]})
        with open(os.path.join(d, "01-evidence.json"), "w", encoding="utf-8") as g:
            json.dump(art, g, ensure_ascii=False)

    def crea_legacy(self):
        """Due ledger legacy non vuoti, con una riga PRIVA di run_id: e' il caso
        'misto' su cui il non-assorbimento va provato, non quello comodo."""
        os.makedirs(os.path.join(self.dst, "state"), exist_ok=True)
        with open(self.percorso("state", "source-ledger.jsonl"), "w", encoding="utf-8") as g:
            g.write(json.dumps({"id": "S-20260101-0001-0001", "url": "https://vecchio.invalid",
                                "run_id": "20260101-0001"}, ensure_ascii=False) + "\n")
            g.write(json.dumps({"id": "S-20260102-0001-0001",
                                "url": "https://senza-run-id.invalid"}, ensure_ascii=False) + "\n")
        with open(self.percorso("state", "query-ledger.jsonl"), "w", encoding="utf-8") as g:
            g.write(json.dumps({"normalized_query": "vecchia query",
                                "run_id": "20260101-0001"}, ensure_ascii=False) + "\n")

    # -- percorsi

    def percorso(self, *p):
        return os.path.join(self.dst, *p)

    def ledger(self):
        return os.path.join(self.tele, LEDGER_M02)

    def sidecar(self, run_id):
        return os.path.join(self.dst, "runs", run_id, SIDECAR)

    # -- esecuzione

    def esegui(self, run_id, *flag, tele=None):
        env = dict(os.environ)
        env["DEEPSEARCH_TELEMETRY_ROOT"] = self.tele if tele is None else tele
        arg = [sys.executable, os.path.join(self.dst, "tools", "reconcile_run.py")]
        if run_id is not None:
            arg.append("runs/" + run_id)
        arg.extend(flag)
        return subprocess.run(arg, capture_output=True, text=True, cwd=self.dst,
                              env=env, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- utilita'

def leggi_byte(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


def righe_ledger(p):
    if not os.path.exists(p):
        return []
    fuori = []
    with open(p, encoding="utf-8") as f:
        for l in f:
            if l.strip():
                try:
                    fuori.append(json.loads(l))
                except Exception:
                    fuori.append(None)
    return fuori


def leggi_sidecar(p):
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------- T-A

def t_a():
    """Idempotenza: doppia esecuzione -> ledger byte-identico, e il sidecar DICE
    che e' restato identico. I due artefatti si comportano diversamente di
    proposito: rec2.10 vincola il ledger, non il sidecar."""
    with Banco() as b:
        b.crea_run(RUN_A)
        r1 = b.esegui(RUN_A, "--telemetry-m0.2")
        dopo1 = leggi_byte(b.ledger())
        side1 = leggi_sidecar(b.sidecar(RUN_A))
        r2 = b.esegui(RUN_A, "--telemetry-m0.2")
        dopo2 = leggi_byte(b.ledger())
        side2 = leggi_sidecar(b.sidecar(RUN_A))

        if dopo1 is None:
            return segna("T-A", "idempotenza + terzo esito", False,
                         "il ledger M0.2 non e' stato creato alla prima esecuzione")
        ok_ledger = dopo1 == dopo2
        ok_1 = bool(side1) and side1.get("esito_scrittura") == "riuscita"
        ok_2 = bool(side2) and side2.get("esito_scrittura") == "gia_presente"
        det = []
        if not ok_ledger:
            det.append("ledger NON byte-identico fra 1a e 2a esecuzione")
        if not ok_1:
            det.append(f"sidecar 1a = {side1 and side1.get('esito_scrittura')!r}, atteso 'riuscita'")
        if not ok_2:
            det.append(f"sidecar 2a = {side2 and side2.get('esito_scrittura')!r}, atteso 'gia_presente'")
        if r1.returncode != r2.returncode:
            det.append(f"exit diverso fra le due esecuzioni: {r1.returncode} vs {r2.returncode}")
        segna("T-A", "idempotenza + terzo esito", ok_ledger and ok_1 and ok_2, "; ".join(det))


# ---------------------------------------------------------------- T-B

def t_b():
    """Assenza di duplicazioni ALLA GRANA DEL CONTRATTO, che l'autorita' ha fissato
    in una riga per run: la cardinalita' per run_id vale esattamente 1, e due run
    distinti fanno due righe."""
    with Banco() as b:
        b.crea_run(RUN_A)
        b.crea_run(RUN_B)
        b.esegui(RUN_A, "--telemetry-m0.2")
        b.esegui(RUN_A, "--telemetry-m0.2")
        b.esegui(RUN_A, "--telemetry-m0.2")
        b.esegui(RUN_B, "--telemetry-m0.2")
        righe = righe_ledger(b.ledger())
        malformate = [r for r in righe if r is None]
        ids = [r.get("run_id") for r in righe if isinstance(r, dict)]
        det = []
        if malformate:
            det.append(f"{len(malformate)} righe non parsabili")
        if ids.count(RUN_A) != 1:
            det.append(f"run A presente {ids.count(RUN_A)} volte dopo 3 esecuzioni, atteso 1")
        if ids.count(RUN_B) != 1:
            det.append(f"run B presente {ids.count(RUN_B)} volte, atteso 1")
        if len(righe) != 2:
            det.append(f"{len(righe)} righe totali, attese 2")
        segna("T-B", "nessuna duplicazione, grana = una riga per run", not det, "; ".join(det))


# ---------------------------------------------------------------- T-C

def t_c():
    """Non-assorbimento: le righe legacy preesistenti restano identiche, anche sul
    ledger misto in cui una riga non ha run_id."""
    with Banco() as b:
        b.crea_legacy()
        b.crea_run(RUN_A)
        prima_s = leggi_byte(b.percorso("state", "source-ledger.jsonl"))
        prima_q = leggi_byte(b.percorso("state", "query-ledger.jsonl"))
        b.esegui(RUN_A, "--telemetry-m0.2")
        dopo_s = leggi_byte(b.percorso("state", "source-ledger.jsonl"))
        dopo_q = leggi_byte(b.percorso("state", "query-ledger.jsonl"))
        det = []
        if prima_s != dopo_s:
            det.append("source-ledger legacy modificato")
        if prima_q != dopo_q:
            det.append("query-ledger legacy modificato")
        segna("T-C", "non-assorbimento dei ledger legacy", not det, "; ".join(det))


# ---------------------------------------------------------------- T-D

def t_d():
    """Esclusivita': la nuova modalita' scrive SOLO il ledger M0.2. E la controprova
    che serve davvero — che --consolida continui a consolidare: senza di essa
    'i legacy non cambiano' sarebbe soddisfatto anche da un programma rotto."""
    with Banco() as b:
        b.crea_legacy()
        b.crea_run(RUN_A)
        prima_s = leggi_byte(b.percorso("state", "source-ledger.jsonl"))
        prima_q = leggi_byte(b.percorso("state", "query-ledger.jsonl"))
        b.esegui(RUN_A, "--telemetry-m0.2")
        det = []
        if leggi_byte(b.percorso("state", "source-ledger.jsonl")) != prima_s:
            det.append("--telemetry-m0.2 ha toccato il source-ledger")
        if leggi_byte(b.percorso("state", "query-ledger.jsonl")) != prima_q:
            det.append("--telemetry-m0.2 ha toccato il query-ledger")
        if not os.path.exists(b.ledger()):
            det.append("il ledger M0.2 non e' stato scritto")

    with Banco() as b2:
        b2.crea_legacy()
        b2.crea_run(RUN_A)
        p_s = leggi_byte(b2.percorso("state", "source-ledger.jsonl"))
        p_q = leggi_byte(b2.percorso("state", "query-ledger.jsonl"))
        b2.esegui(RUN_A, "--consolida")
        d_s = leggi_byte(b2.percorso("state", "source-ledger.jsonl"))
        d_q = leggi_byte(b2.percorso("state", "query-ledger.jsonl"))
        if d_s == p_s:
            det.append("CONTROPROVA: --consolida non consolida piu' le fonti")
        if d_q == p_q:
            det.append("CONTROPROVA: --consolida non consolida piu' le query")
        if os.path.exists(b2.ledger()):
            det.append("--consolida da solo ha scritto il ledger M0.2")
    segna("T-D", "esclusivita' M0.2 + controprova su --consolida", not det, "; ".join(det))


# ---------------------------------------------------------------- T-E / T-F

def _sezione_11():
    p = os.path.join(ROOT, ".claude", "commands", "deepsearch.md")
    with open(p, encoding="utf-8") as f:
        t = f.read()
    return t, t.split("## 11.")[-1]


def _righe_eseguibili(testo):
    """Le righe dentro una recinzione ```...```: e' li' che sta un comando."""
    dentro, fuori = False, []
    for l in testo.splitlines():
        if l.strip().startswith("```"):
            dentro = not dentro
            continue
        if dentro:
            fuori.append(l)
    return fuori


LETTERALE_R1 = "python3 tools/reconcile_run.py runs/<run_id>"


def t_e():
    """Una sola invocazione nominale — e con essa R1, R2 e l'ordine dei token, che
    sono le condizioni da cui dipende la NON-CECITA' della mutazione SC-08 di
    spec_check_selftest.py. patch_testo sostituisce un letterale CONTIGUO: se ne
    esistesse piu' d'uno, o se il token reconcile_run.py sopravvivesse altrove in
    §11, la mutazione tornerebbe cieca e nulla lo segnalerebbe qui."""
    testo, sez = _sezione_11()
    esegui = _righe_eseguibili(sez)
    n_tele = sum(l.count("--telemetry-m0.2") for l in esegui)
    n_cons = sum(l.count("--consolida") for l in esegui)
    det = []
    if n_tele != 1:
        det.append(f"--telemetry-m0.2 in posizione eseguibile {n_tele} volte, attesa 1")
    if n_cons != 0:
        det.append(f"--consolida in posizione eseguibile {n_cons} volte, attese 0")
    n_r1 = testo.count(LETTERALE_R1)
    if n_r1 != 1:
        det.append(f"R1: letterale contiguo {n_r1} volte nell'intero file, attesa 1")
    n_r2 = sez.count("reconcile_run.py")
    if n_r2 != 1:
        det.append(f"R2: reconcile_run.py {n_r2} volte in §11, attesa 1 (nessun'altra)")
    if LETTERALE_R1 + " --telemetry-m0.2" not in sez:
        det.append("il flag non segue il percorso del run")
    segna("T-E", "una sola invocazione nominale, R1/R2, flag dopo il percorso",
          not det, "; ".join(det))


def t_f():
    """Nessuna scrittura prima del cancello. Si accerta sul TESTO, ed e' quanto si
    puo' accertare: il percorso nominale e' prosa eseguita da un attore LLM."""
    testo, sez = _sezione_11()
    i_ancora = sez.find("<!-- ancora: scrittura-state -->")
    i_tele = sez.find("--telemetry-m0.2")
    det = []
    if i_ancora < 0:
        det.append("ancora scrittura-state non trovata in §11")
    if i_tele < 0:
        det.append("--telemetry-m0.2 non compare in §11")
    if i_ancora >= 0 and i_tele >= 0 and i_tele < i_ancora:
        det.append("l'invocazione precede l'ancora scrittura-state")
    if "reconcile_run.py" in sez[:i_ancora if i_ancora >= 0 else 0]:
        det.append("reconcile_run.py compare in §11 PRIMA del cancello")
    segna("T-F", "nessuna invocazione prima del cancello", not det, "; ".join(det))


# ---------------------------------------------------------------- T-G

SCRITTURA = {"write", "writelines", "truncate", "remove", "unlink", "rename",
             "replace", "mkdir", "makedirs", "rmtree", "copy", "copy2", "copytree",
             "dump", "flush"}


def t_g():
    """run_close.py ancora read-only. Scansione AST, non ricerca testuale: un
    grep su 'write' trova anche i commenti."""
    p = os.path.join(ROOT, "tools", "run_close.py")
    with open(p, encoding="utf-8") as f:
        albero = ast.parse(f.read(), p)
    trovate = []
    for n in ast.walk(albero):
        if not isinstance(n, ast.Call):
            continue
        f_ = n.func
        if isinstance(f_, ast.Attribute) and f_.attr in SCRITTURA:
            trovate.append(f"{f_.attr}() a riga {n.lineno}")
        if isinstance(f_, ast.Name) and f_.id == "open":
            modo = None
            if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                modo = n.args[1].value
            for k in n.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    modo = k.value.value
            if isinstance(modo, str) and any(c in modo for c in "wax+"):
                trovate.append(f"open(mode={modo!r}) a riga {n.lineno}")
    segna("T-G", "run_close.py ancora read-only", not trovate,
          "; ".join(trovate[:6]))


# ---------------------------------------------------------------- T-H

def t_h():
    """Exit code invariati, in OGNI combinazione di guasto. I tre ritorni misurati
    sono 0, 1 e 2, e nessun guasto della telemetria puo' spostarli."""
    casi = []
    with Banco() as b:
        b.crea_run(RUN_A)
        casi.append(("run pulito", b.esegui(RUN_A).returncode,
                     b.esegui(RUN_A, "--telemetry-m0.2").returncode, 0))
    with Banco() as b:
        b.crea_run(RUN_A, senza_origine=True)
        casi.append(("citazioni senza origine", b.esegui(RUN_A).returncode,
                     b.esegui(RUN_A, "--telemetry-m0.2").returncode, 1))
    with Banco() as b:
        casi.append(("nessun run", b.esegui(None).returncode,
                     b.esegui(None, "--telemetry-m0.2").returncode, 2))
    with Banco() as b:
        b.crea_run(RUN_A)
        rotta = os.path.join(b.base, "blocco", "sotto")
        os.makedirs(os.path.dirname(rotta), exist_ok=True)
        with open(os.path.join(b.base, "blocco", "sotto"), "w") as g:
            g.write("non sono una directory")
        casi.append(("ledger non scrivibile", b.esegui(RUN_A).returncode,
                     b.esegui(RUN_A, "--telemetry-m0.2",
                              tele=os.path.join(rotta, "tele")).returncode, 0))
    det = []
    for nome, senza, con, atteso in casi:
        if senza != atteso:
            det.append(f"{nome}: baseline {senza}, attesa {atteso}")
        if con != senza:
            det.append(f"{nome}: con telemetria {con}, senza {senza}")
    segna("T-H", "exit code invariati sotto ogni guasto", not det, "; ".join(det))


# ---------------------------------------------------------------- T-J

def t_j():
    """Nessun sidecar SENZA FLAG, ed e' tutto cio' che questa prova accerta.

    Il gate non esiste dentro il programma: nel percorso nominale i run che non
    arrivano al gate valido semplicemente non invocano la modalita'. Qui si prova
    dunque che senza invocazione non nasce nulla, e che nessun record artificiale
    dichiara un tentativo non avvenuto — non che un gate lo impedisca. E' il limite
    che il packet dichiara al §6.4 (il percorso nominale e' prosa eseguita da un
    attore LLM), non un difetto della prova: il titolo lo dice per non promettere
    di piu' di quanto accerti."""
    with Banco() as b:
        b.crea_run(RUN_A, senza_origine=True)
        b.esegui(RUN_A)                     # degradato/invalido: nessun flag
        b.esegui(RUN_A, "--consolida")      # interrotto prima del gate: solo storico
        det = []
        if os.path.exists(b.sidecar(RUN_A)):
            det.append("sidecar creato senza invocazione della modalita'")
        if os.path.exists(b.ledger()):
            det.append("ledger M0.2 creato senza invocazione della modalita'")
        for base, _, files in os.walk(os.path.join(b.dst, "runs")):
            for n in files:
                try:
                    with open(os.path.join(base, n), encoding="utf-8") as f:
                        if "attempted" in f.read():
                            det.append(f"record 'attempted' trovato in {n}")
                except Exception:
                    pass
        segna("T-J", "nessun sidecar senza flag, nessun 'attempted: no'",
              not det, "; ".join(det))


# ---------------------------------------------------------------- T-K

def t_k():
    """Fallimento del ledger, run per il resto riuscito. La destinazione si rende
    invalida per FORMA del percorso, non per permessi: su NTFS il proprietario
    conserva WRITE_DAC e un diniego non e' affidabile."""
    with Banco() as b:
        b.crea_run(RUN_A)
        senza = b.esegui(RUN_A)
        blocco = os.path.join(b.base, "blocco")
        with open(blocco, "w") as g:
            g.write("sono un file, non una directory")
        r = b.esegui(RUN_A, "--telemetry-m0.2", tele=os.path.join(blocco, "tele"))
        side = leggi_sidecar(b.sidecar(RUN_A))
        det = []
        if r.returncode != senza.returncode:
            det.append(f"exit {r.returncode} != {senza.returncode} senza guasto")
        if not (r.stderr or "").strip():
            det.append("stderr vuoto")
        if not os.path.isfile(b.sidecar(RUN_A)):
            det.append("sidecar assente")
        elif not side:
            det.append("sidecar non leggibile come JSON")
        else:
            if side.get("esito_scrittura") != "fallita":
                det.append(f"esito_scrittura = {side.get('esito_scrittura')!r}, atteso 'fallita'")
            cod = side.get("errore_codice")
            if cod not in ("destinazione_non_scrivibile", "errore_interno"):
                det.append(f"errore_codice = {cod!r}, fuori dai due ammessi")
            msg = side.get("errore_messaggio") or ""
            if not msg:
                det.append("errore_messaggio assente")
            if "/" in msg or "\\" in msg:
                det.append("errore_messaggio contiene un separatore di percorso")
            if b.base in msg or "Errno" in msg or ":" in msg:
                det.append("errore_messaggio contiene il testo di un'eccezione")
        segna("T-K", "fallimento del ledger, run riuscito, sidecar sanitizzato",
              not det, "; ".join(det))


# ---------------------------------------------------------------- T-L

def t_l():
    """Fallimento ANCHE del sidecar: resta stderr, e il run non si blocca."""
    with Banco() as b:
        b.crea_run(RUN_A)
        senza = b.esegui(RUN_A)
        blocco = os.path.join(b.base, "blocco")
        with open(blocco, "w") as g:
            g.write("sono un file, non una directory")
        os.makedirs(b.sidecar(RUN_A), exist_ok=True)   # al posto del file, una directory
        r = b.esegui(RUN_A, "--telemetry-m0.2", tele=os.path.join(blocco, "tele"))
        det = []
        if r.returncode != senza.returncode:
            det.append(f"exit {r.returncode} != {senza.returncode} senza guasto")
        err = (r.stderr or "")
        if not err.strip():
            det.append("stderr vuoto")
        if "sidecar" not in err.lower():
            det.append("stderr non dichiara il fallimento del sidecar")
        if not (r.stdout or "").strip():
            det.append("il run si e' bloccato: nessuna diagnosi su stdout")
        segna("T-L", "fallimento anche del sidecar: resta stderr", not det, "; ".join(det))


# ---------------------------------------------------------------- T-O

# L'esca del caso dell'oggetto-esca. Deliberatamente NON in forma di chiave API e
# senza il nome di alcuna variabile di credenziale: dev'essere una stringa che nessun
# output puo' contenere, non un segreto vero, e scriverne uno verosimile qui
# violerebbe SC-21 e SC-22 per provare qualcosa che non ha bisogno di essere provato
# con un segreto autentico.
SEGRETO = "ESCA-M02-NON-DEVE-USCIRE-4b7d1e"

# Il letterale congelato del caso 9: l'output di config_m0_2() sul config vigente.
# 452 byte, sha256 5185878809...b96a8, misurato sul config 674bb8272b...de7e9. Non e'
# una copia di comodo: e' la custodia della regola 'output sul config vigente
# byte-identico'. Se un giorno differisce anche di un byte, o e' cambiato il config o
# e' cambiata la funzione, e in nessuno dei due casi la costante va allineata prima di
# aver guardato quale dei due.
CFG_VIGENTE = (
    '{"modelli_per_agente": {"bootstrap-filter": "haiku", "competitor-analyst": "sonnet", '
    '"evidence-researcher": "haiku", "pain-finder": "sonnet", "ranking-agent": "haiku", '
    '"red-team": "sonnet", "source-verifier": "haiku", "tam-analyst": "haiku", '
    '"trigger-finder": "haiku", "venture-architect": "sonnet"}, '
    '"provider_delegabili": ["bootstrap-filter", "evidence-researcher", "ranking-agent", '
    '"tam-analyst", "trigger-finder"], "provider_attivo_per_agente": {}}'
)
CFG_VIGENTE_SHA = "518587880971555b2b0588236b9ce97e656ad0887e1e1bf2bb28eb74b26b96a8"


def _muta_config(b, muta):
    """Riscrive il config DENTRO la copia usa-e-getta, mai quello vero: config/ non e'
    fra i percorsi che questo slice tocca, e una prova non fa eccezione."""
    p = b.percorso("config", "deepsearch.config.json")
    with open(p, encoding="utf-8") as f:
        c = json.load(f)
    muta(c)
    with open(p, "w", encoding="utf-8") as g:
        json.dump(c, g, ensure_ascii=False)


def _caso_config(muta=None):
    """Un caso: copia del repo, config eventualmente malformato in un punto, un run,
    la modalita' telemetria. Si accerta cio' che arriva nel LEDGER, che e' l'unico
    luogo in cui il valore conta.

    Ritorna anche, accanto, tutto cio' che il processo ha emesso o lasciato su disco —
    ledger, sidecar, stdout, stderr — riunito in un solo testo: e' li' dentro che il
    caso dell'esca cerca il segreto, e cercarlo in un posto solo non proverebbe nulla.
    """
    with Banco() as b:
        b.crea_run(RUN_A)
        if muta is not None:
            _muta_config(b, muta)
        r = b.esegui(RUN_A, "--telemetry-m0.2")
        cfg = None
        for x in righe_ledger(b.ledger()):
            if isinstance(x, dict) and x.get("run_id") == RUN_A:
                cfg = x.get("configurazione")
        pezzi = [(leggi_byte(b.ledger()) or b"").decode("utf-8", "replace"),
                 (leggi_byte(b.sidecar(RUN_A)) or b"").decode("utf-8", "replace"),
                 r.stdout or "", r.stderr or ""]
        return cfg, "\n".join(pezzi)


def t_o():
    """I nove casi dell'irrobustimento della lettura di configurazione.

    Assenza e malformazione portano etichette diverse, e la differenza e' il merito
    della prova: NOT_INSTRUMENTED dice 'il campo non c'e'', UNKNOWN dice 'c'e' ma non
    e' interpretabile'. Confonderle le renderebbe entrambe illeggibili a valle. Un
    solo caso per volta e' malformato: cosi' cio' che si legge nel ledger e'
    attribuibile, e non l'esito combinato di piu' guasti.
    """
    def m1(c):
        c["modelli"]["per_agente"]["pain-finder"] = "sonnet"

    def m2(c):
        c["modelli"]["per_agente"]["pain-finder"] = {"perche": "voce senza modello"}

    def m3(c):
        c["modelli"]["per_agente"]["pain-finder"] = {"modello": None, "perche": "nullo"}

    def m4(c):
        del c["provider"]["delegabili"]

    def m5(c):
        c["provider"]["delegabili"] = "tutti"

    def m6(c):
        del c["provider"]["attivo_per_agente"]

    def m7(c):
        c["provider"]["attivo_per_agente"] = ["pain-finder"]

    def m8(c):
        """L'esca in OGNI ramo non enumerato, non in uno solo: voce non-dict, voce
        dict con proprieta' extra, `modello` a sua volta oggetto, e provider.deepseek
        — che nessun ramo deve leggere."""
        pa = c["modelli"]["per_agente"]
        pa["pain-finder"] = SEGRETO
        pa["red-team"] = {"modello": "sonnet", "perche": SEGRETO, "nota": SEGRETO}
        pa["ranking-agent"] = {"modello": {"nome": "haiku", "riservato": SEGRETO}}
        c["provider"]["deepseek"] = dict(c["provider"].get("deepseek") or {},
                                         riservato=SEGRETO)

    def voce(cfg, k):
        mp = (cfg or {}).get("modelli_per_agente")
        return mp.get(k) if isinstance(mp, dict) else mp

    def campo(cfg, k):
        return (cfg or {}).get(k)

    def esito(nome, titolo, letto, atteso):
        segna(nome, titolo, letto == atteso,
              "" if letto == atteso else f"letto {letto!r}, atteso {atteso!r}")

    esito("T-O1", "voce agente scalare -> UNKNOWN",
          voce(_caso_config(m1)[0], "pain-finder"), "UNKNOWN")
    esito("T-O2", "voce dict senza modello -> NOT_INSTRUMENTED",
          voce(_caso_config(m2)[0], "pain-finder"), "NOT_INSTRUMENTED")
    esito("T-O3", "modello null -> UNKNOWN",
          voce(_caso_config(m3)[0], "pain-finder"), "UNKNOWN")
    esito("T-O4", "delegabili assente -> NOT_INSTRUMENTED",
          campo(_caso_config(m4)[0], "provider_delegabili"), "NOT_INSTRUMENTED")
    esito("T-O5", "delegabili di tipo errato -> UNKNOWN",
          campo(_caso_config(m5)[0], "provider_delegabili"), "UNKNOWN")
    esito("T-O6", "attivo_per_agente assente -> NOT_INSTRUMENTED",
          campo(_caso_config(m6)[0], "provider_attivo_per_agente"), "NOT_INSTRUMENTED")
    esito("T-O7", "attivo_per_agente di tipo errato -> UNKNOWN",
          campo(_caso_config(m7)[0], "provider_attivo_per_agente"), "UNKNOWN")

    cfg, uscita = _caso_config(m8)
    det = []
    if SEGRETO in uscita:
        det.append("l'esca compare in un output: ledger, sidecar, stdout o stderr")
    if voce(cfg, "pain-finder") != "UNKNOWN":
        det.append(f"voce non-dict -> {voce(cfg, 'pain-finder')!r}, atteso UNKNOWN")
    if voce(cfg, "red-team") != "sonnet":
        det.append(f"voce con proprieta' extra -> {voce(cfg, 'red-team')!r}, atteso 'sonnet'")
    if voce(cfg, "ranking-agent") != "UNKNOWN":
        det.append(f"modello-oggetto -> {voce(cfg, 'ranking-agent')!r}, atteso UNKNOWN")
    segna("T-O8", "esca in ogni ramo non enumerato: non esce da nessuna parte",
          not det, "; ".join(det))

    cfg, _ = _caso_config()
    reso = json.dumps(cfg, ensure_ascii=False)
    byte = reso.encode("utf-8")
    sha = hashlib.sha256(byte).hexdigest()
    det = []
    if reso != CFG_VIGENTE:
        det.append("output diverso dal letterale congelato")
    if sha != CFG_VIGENTE_SHA or len(byte) != 452:
        det.append(f"{len(byte)} byte sha {sha[:10]}, attesi 452 byte sha {CFG_VIGENTE_SHA[:10]}")
    segna("T-O9", "config vigente: output invariato byte per byte", not det, "; ".join(det))


# ---------------------------------------------------------------- consegna

def main():
    for t in (t_a, t_b, t_c, t_d, t_e, t_f, t_g, t_h, t_j, t_k, t_l, t_o):
        try:
            t()
        except Exception as e:
            segna(t.__name__.upper().replace("_", "-"), "eccezione nella prova",
                  False, f"{type(e).__name__}: {e}")

    print()
    largo = max(len(t) for _, t, _, _ in esiti)
    for nome, titolo, ok, det in esiti:
        print(f"  {'ok   ' if ok else 'FALL '} {nome:5s} {titolo:{largo}s}"
              + (f"   {det}" if det else ""))
    print("  " + "-" * 78)
    falliti = [n for n, _, ok, _ in esiti if not ok]
    print(f"  {len(esiti) - len(falliti)}/{len(esiti)} passano"
          + (f"   ·   falliti: {', '.join(falliti)}" if falliti else ""))
    print("\n  T-I (regola Git) e' verifica manuale su file reale e non sta qui.")
    print("  T-M e T-N si accertano eseguendo spec_check.py e spec_check_selftest.py.\n")
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
