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
import ast, hashlib, json, os, re, shutil, subprocess, sys, tempfile
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
    Copia del repo + root telemetria + root stato, tutte dentro una sola temporanea.

    Il seam serve esattamente qui: reconcile_run.py calcola le due root due livelli
    sopra il proprio ROOT, e nella copia quei due livelli cadono fuori da cio' che
    rmtree rimuove. Senza deviarle, questa macchina scriverebbe nelle root vere.

    Dal thin slice 2 le root da deviare sono DUE, e la seconda e' quella che conta di
    piu': lo stato mutabile non e' versionato, quindi un test che vi scrivesse per
    sbaglio corromperebbe la memoria vera senza lasciare traccia in alcun diff.
    """

    def __enter__(self):
        self.base = tempfile.mkdtemp(prefix="m02-")
        self.dst = os.path.join(self.base, "repo")
        shutil.copytree(ROOT, self.dst, ignore=IGNORA)
        self.tele = os.path.join(self.base, "telemetria")
        os.makedirs(self.tele, exist_ok=True)
        self.stato = os.path.join(self.base, "stato")
        os.makedirs(self.stato, exist_ok=True)
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

    def crea_legacy(self, manifest=True):
        """Due ledger legacy non vuoti, con una riga PRIVA di run_id: e' il caso
        'misto' su cui il non-assorbimento va provato, non quello comodo.

        Dal thin slice 2 vivono nella root dello stato, non piu' in dst/state, e sono
        accompagnati da un manifest che ne congela il prefisso. I ledger scritti qui
        SONO il prefisso: byte_prefisso e' la loro intera lunghezza, com'e' all'atto
        della migrazione vera."""
        with open(self.stato_p("source-ledger.jsonl"), "w", encoding="utf-8",
                  newline="\n") as g:
            g.write(json.dumps({"id": "S-20260101-0001-0001", "url": "https://vecchio.invalid",
                                "run_id": "20260101-0001"}, ensure_ascii=False) + "\n")
            g.write(json.dumps({"id": "S-20260102-0001-0001",
                                "url": "https://senza-run-id.invalid"}, ensure_ascii=False) + "\n")
        with open(self.stato_p("query-ledger.jsonl"), "w", encoding="utf-8",
                  newline="\n") as g:
            g.write(json.dumps({"normalized_query": "vecchia query",
                                "run_id": "20260101-0001"}, ensure_ascii=False) + "\n")
        for f_ in ("seen-pains.jsonl", "sector-history.jsonl", "shadow-portfolio.jsonl"):
            open(self.stato_p(f_), "w", encoding="utf-8").close()
        if manifest:
            self.scrivi_manifest()

    def scrivi_manifest(self, **override):
        """Manifest calcolato sui file di stato come stanno adesso. Gli override
        servono ai casi che devono farlo mentire: e' l'unico modo per provare che il
        controllo n.4 guarda il file e non se stesso."""
        voci = []
        for nome in ("query-ledger", "source-ledger", "seen-pains",
                     "sector-history", "shadow-portfolio"):
            p = self.stato_p(nome + ".jsonl")
            b = open(p, "rb").read() if os.path.exists(p) else b""
            r = s = 0
            for l in b.decode("utf-8").splitlines():
                if not l.strip():
                    continue
                r += 1
                try:
                    o = json.loads(l)
                except Exception:
                    o = None
                if not (isinstance(o, dict) and o.get("run_id")):
                    s += 1
            v = {"ledger": nome, "sorgente": "banco di prova",
                 "sha256_prefisso": hashlib.sha256(b).hexdigest(),
                 "byte_prefisso": len(b), "righe_importate": r,
                 "righe_legacy_senza_run_id": s, "data_importazione": "2026-08-17"}
            v.update(override.get(nome, {}))
            voci.append(v)
        doc = {"versione_formato": 1, "voci": voci}
        doc.update(override.get("_doc", {}))
        with open(self.stato_p("state-import-manifest.json"), "w", encoding="utf-8",
                  newline="\n") as g:
            json.dump(doc, g, ensure_ascii=False, indent=2)
            g.write("\n")

    # -- percorsi

    def percorso(self, *p):
        return os.path.join(self.dst, *p)

    def stato_p(self, *p):
        return os.path.join(self.stato, *p)

    def ledger(self):
        return os.path.join(self.tele, LEDGER_M02)

    def sidecar(self, run_id):
        return os.path.join(self.dst, "runs", run_id, SIDECAR)

    # -- esecuzione

    def _env(self, tele=None, stato=None):
        env = dict(os.environ)
        env["DEEPSEARCH_TELEMETRY_ROOT"] = self.tele if tele is None else tele
        env["DEEPSEARCH_STATE_ROOT"] = self.stato if stato is None else stato
        return env

    def esegui(self, run_id, *flag, tele=None, stato=None):
        arg = [sys.executable, os.path.join(self.dst, "tools", "reconcile_run.py")]
        if run_id is not None:
            arg.append("runs/" + run_id)
        arg.extend(flag)
        return subprocess.run(arg, capture_output=True, text=True, cwd=self.dst,
                              env=self._env(tele, stato), encoding="utf-8", errors="replace")

    def chiudi(self, run_id, fase="pre", stato=None):
        """run_close.py sulla copia. Serve ai controlli della guardia, che vivono li'
        e che nessun test esercitava prima del thin slice 2."""
        arg = [sys.executable, os.path.join(self.dst, "tools", "run_close.py"),
               "runs/" + run_id, "--fase", fase]
        return subprocess.run(arg, capture_output=True, text=True, cwd=self.dst,
                              env=self._env(stato=stato), encoding="utf-8", errors="replace")


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
        prima_s = leggi_byte(b.stato_p("source-ledger.jsonl"))
        prima_q = leggi_byte(b.stato_p("query-ledger.jsonl"))
        b.esegui(RUN_A, "--telemetry-m0.2")
        dopo_s = leggi_byte(b.stato_p("source-ledger.jsonl"))
        dopo_q = leggi_byte(b.stato_p("query-ledger.jsonl"))
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
        prima_s = leggi_byte(b.stato_p("source-ledger.jsonl"))
        prima_q = leggi_byte(b.stato_p("query-ledger.jsonl"))
        b.esegui(RUN_A, "--telemetry-m0.2")
        det = []
        if leggi_byte(b.stato_p("source-ledger.jsonl")) != prima_s:
            det.append("--telemetry-m0.2 ha toccato il source-ledger")
        if leggi_byte(b.stato_p("query-ledger.jsonl")) != prima_q:
            det.append("--telemetry-m0.2 ha toccato il query-ledger")
        if not os.path.exists(b.ledger()):
            det.append("il ledger M0.2 non e' stato scritto")

    with Banco() as b2:
        b2.crea_legacy()
        b2.crea_run(RUN_A)
        p_s = leggi_byte(b2.stato_p("source-ledger.jsonl"))
        p_q = leggi_byte(b2.stato_p("query-ledger.jsonl"))
        b2.esegui(RUN_A, "--consolida")
        d_s = leggi_byte(b2.stato_p("source-ledger.jsonl"))
        d_q = leggi_byte(b2.stato_p("query-ledger.jsonl"))
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

# (A) Scritture QUALIFICATE PER MODULO, confrontate dopo aver risolto gli alias.
#     Fino al thin slice 2 bastava il nome nudo dell'attributo, e la scansione non
#     sapeva distinguere `os.remove(p)` da `lista.remove(x)`, `shutil.copy(a,b)` da
#     `dizionario.copy()`, `os.replace(a,b)` da `stringa.replace("a","b")`. In un file
#     che costruisce insiemi di query e dizionari di manifest quei tre omonimi sono
#     piu' probabili delle scritture vere: il criterio sarebbe diventato rumoroso, e
#     un criterio rumoroso si disattiva.
QUALIFICATE = {
    ("os", "remove"), ("os", "unlink"), ("os", "rename"), ("os", "replace"),
    ("os", "mkdir"), ("os", "makedirs"), ("os", "rmdir"), ("os", "removedirs"),
    ("shutil", "rmtree"), ("shutil", "copy"), ("shutil", "copy2"),
    ("shutil", "copytree"), ("shutil", "move"), ("json", "dump"),
}
# (B) Scritture su OGGETTO FILE: qui il nome basta, su qualunque ricevente, perche'
#     su un file non hanno omonimi innocui.
#     `append` NON entra: su un oggetto file `.append()` non esiste — l'append su
#     file e' open(..., "a"), che prende (C) — mentre come nome d'attributo
#     colpirebbe list.append, che in run_close.py ricorre su liste di esiti.
SU_FILE = {"write", "writelines", "truncate", "flush"}

# Import ammessi in run_close.py. E' qui che il divieto su pathlib smette di essere
# una frase e diventa un controllo: introdurre pathlib — o qualunque altro modulo che
# sappia scrivere sul filesystem — fa fallire il criterio finche' (A) non viene
# esteso di proposito. Senza questo elenco, `Path(p).unlink()` sfuggirebbe a (A),
# che risolve solo riceventi legati a un modulo, e a (B), che non lo contiene.
IMPORT_AMMESSI = {"json", "os", "re", "sys", "glob", "unicodedata", "hashlib"}
#: Ampliato deliberatamente per `B-1`: `run_close` importa da `reconcile_run` anche la regola di
#: che cosa sia un evidence-pack consumabile, invece di riscriverla. Sono tre nomi, non un modulo
#: nuovo, e questo criterio e' l'unico posto dove quella decisione resta scritta.
IMPORT_DA_AMMESSI = {("reconcile_run", "radice_stato"),
                     ("reconcile_run", "CARTELLA_EVIDENZA"),
                     ("reconcile_run", "pain_con_pack"),
                     ("reconcile_run", "pains_dichiarati")}


def _legami_moduli(albero):
    """(alias -> modulo, nome nudo -> (modulo, attributo)).

    E' cio' che rende la scansione resistente agli alias invece che alla lettera del
    sorgente: `import shutil as sh` lega sh a shutil, `from shutil import copy` lega
    il nome nudo copy a shutil.copy."""
    mod, diretti = {}, {}
    for n in ast.walk(albero):
        if isinstance(n, ast.Import):
            for a in n.names:
                mod[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                diretti[a.asname or a.name] = (n.module.split(".")[0], a.name)
    return mod, diretti


def scansiona_scritture(sorgente, nome="<sorgente>"):
    """Le primitive di scrittura raggiungibili in un sorgente Python.

    Scansione AST, non ricerca testuale: un grep su 'write' trova anche i commenti."""
    albero = ast.parse(sorgente, nome)
    mod, diretti = _legami_moduli(albero)
    trovate = []
    for n in ast.walk(albero):
        # Riassegnazione di modulo: `x = shutil` renderebbe la mappa incompleta e
        # aprirebbe una via a (A). Non si indovina: si fallisce.
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            v = getattr(n, "value", None)
            if isinstance(v, ast.Name) and v.id in mod:
                trovate.append(f"riassegnazione del modulo {v.id} a riga {n.lineno}")
        if not isinstance(n, ast.Call):
            continue
        f_ = n.func
        if isinstance(f_, ast.Attribute):
            if f_.attr in SU_FILE:
                trovate.append(f"{f_.attr}() a riga {n.lineno}")
            elif isinstance(f_.value, ast.Name) and f_.value.id in mod:
                m = mod[f_.value.id]
                if (m, f_.attr) in QUALIFICATE:
                    trovate.append(f"{m}.{f_.attr}() a riga {n.lineno}")
        elif isinstance(f_, ast.Name):
            if f_.id == "open":
                modo = None
                if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                    modo = n.args[1].value
                for k in n.keywords:
                    if k.arg == "mode" and isinstance(k.value, ast.Constant):
                        modo = k.value.value
                if isinstance(modo, str) and any(c in modo for c in "wax+"):
                    trovate.append(f"open(mode={modo!r}) a riga {n.lineno}")
            elif diretti.get(f_.id) in QUALIFICATE:
                m, a = diretti[f_.id]
                trovate.append(f"{m}.{a}() importata nuda, a riga {n.lineno}")
    return trovate


def _sorgente_run_close():
    p = os.path.join(ROOT, "tools", "run_close.py")
    with open(p, encoding="utf-8") as f:
        return f.read()


def t_g():
    """run_close.py ancora read-only."""
    trovate = scansiona_scritture(_sorgente_run_close(), "run_close.py")
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

# ------------------------------------------------- T-P / T-Q / T-R / T-S / T-U / T-V
#
# I criteri del thin slice 2. Il primo e' quello che rende falsificabile tutto il
# resto: senza T-U, T-C e T-D resterebbero verdi su un percorso morto — leggerebbero
# due file che nessuno scrive piu', li troverebbero identici prima e dopo, e
# certificherebbero un non-assorbimento che nessuno sta piu' esercitando.

def _esito(out, nome):
    """True/False/None per il controllo di run_close il cui nome inizia con `nome`."""
    for l in out.splitlines():
        s = l.strip()
        for pref, val in (("ok  ", True), ("FAIL", False)):
            if s.startswith(pref) and s[len(pref):].strip().startswith(nome):
                return val
    return None


def t_p():
    """Gli import di run_close.py sono enumerati.

    E' il controllo che meccanizza il divieto su pathlib. (A) risolve solo riceventi
    legati a un modulo e (B) non contiene `unlink`: `Path(p).unlink()` sfuggirebbe a
    entrambi. Invece di inseguire ogni forma possibile si chiude la porta a monte —
    se l'insieme degli import cambia, il criterio fallisce e qualcuno deve decidere."""
    albero = ast.parse(_sorgente_run_close(), "run_close.py")
    visti, visti_da, fuori = set(), set(), []
    for n in ast.walk(albero):
        if isinstance(n, ast.Import):
            for a in n.names:
                visti.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                visti_da.add((n.module.split(".")[0], a.name))
    fuori += sorted(visti - IMPORT_AMMESSI)
    fuori += [f"{m}.{a}" for m, a in sorted(visti_da - IMPORT_DA_AMMESSI)]
    segna("T-P", "import di run_close.py enumerati", not fuori,
          ("non ammessi: " + ", ".join(fuori)) if fuori
          else f"{len(visti)} moduli, {len(visti_da)} from-import")


def t_q():
    """La guardia sullo stato migrato: sette guasti, sette gravi.

    Ogni alterazione avviene sulla copia del banco. La radice vera non viene mai
    aperta in scrittura da questa prova: e' l'unico modo per collaudare una guardia
    su dati che, fuori di qui, non sono versionati e non si ripristinano."""
    casi, det = [], []

    def caso(nome, prepara, controllo, atteso=False):
        with Banco() as b:
            b.crea_legacy()
            b.crea_run(RUN_A)
            prepara(b)
            r = b.chiudi(RUN_A)
            e = _esito(r.stdout, controllo)
            casi.append((nome, e is atteso))
            if e is not atteso:
                det.append(f"{nome}: atteso {atteso}, ottenuto {e}")

    # (a) manifest assente
    caso("manifest assente",
         lambda b: os.remove(b.stato_p("state-import-manifest.json")),
         "manifest dello stato presente")
    # (b) manifest illeggibile
    caso("manifest illeggibile",
         lambda b: open(b.stato_p("state-import-manifest.json"), "w").write("{ rotto"),
         "manifest dello stato leggibile")
    # (c) voce mancante
    caso("voce mancante",
         lambda b: _togli_voce(b, "source-ledger"),
         "manifest con una voce per file")
    # (d) manifest internamente incoerente: dichiara una riga in meno di quante ce ne
    #     siano. E' il caso che distingue un manifest coerente con se stesso da uno
    #     coerente col file: l'hash del prefisso combacia, il conteggio no.
    caso("39 dichiarate su 40 reali",
         lambda b: b.scrivi_manifest(**{"source-ledger": {"righe_importate": 1}}),
         "source-ledger.jsonl: manifest coerente col file")
    # (e) prefisso manomesso, a parita' di lunghezza
    caso("prefisso manomesso", _manometti_prefisso,
         "source-ledger.jsonl: prefisso storico intatto")
    # (f) troncamento sotto byte_prefisso
    caso("ledger troncato", _tronca,
         "source-ledger.jsonl: non troncato")
    # (g) riga nuova senza run_id, su un ledger e su un file di memoria
    caso("riga nuova senza run_id (ledger)",
         lambda b: _appendi(b, "query-ledger.jsonl", {"normalized_query": "orfana"}),
         "query-ledger.jsonl: ogni riga nuova porta run_id")
    caso("riga nuova senza run_id (memoria)",
         lambda b: _appendi(b, "sector-history.jsonl", {"sector": "senza attribuzione"}),
         "sector-history.jsonl: ogni riga nuova porta run_id")

    # E la controprova, senza la quale i sette sopra sarebbero soddisfatti anche da
    # una guardia che dice sempre di no: su stato integro tutto passa.
    with Banco() as b:
        b.crea_legacy()
        b.crea_run(RUN_A)
        r = b.chiudi(RUN_A)
        for c in ("manifest completo",
                  "source-ledger.jsonl: prefisso storico intatto",
                  "source-ledger.jsonl: manifest coerente col file",
                  "query-ledger.jsonl: ogni riga nuova porta run_id"):
            if _esito(r.stdout, c) is not True:
                det.append(f"CONTROPROVA: '{c}' non passa su stato integro")

    segna("T-Q", "guardia sullo stato: manifest, prefisso, attribuzione",
          not det, "; ".join(det[:4]))


def _togli_voce(b, nome):
    p = b.stato_p("state-import-manifest.json")
    with open(p, encoding="utf-8") as g:
        d = json.load(g)
    d["voci"] = [v for v in d["voci"] if v.get("ledger") != nome]
    with open(p, "w", encoding="utf-8", newline="\n") as g:
        json.dump(d, g, ensure_ascii=False, indent=2)


def _manometti_prefisso(b):
    p = b.stato_p("source-ledger.jsonl")
    d = open(p, "rb").read().replace(b"vecchio.invalid", b"vecchiX.invalid")
    open(p, "wb").write(d)


def _tronca(b):
    p = b.stato_p("source-ledger.jsonl")
    d = open(p, "rb").read()
    open(p, "wb").write(d[:len(d) // 2])


def _appendi(b, f_, riga):
    with open(b.stato_p(f_), "a", encoding="utf-8", newline="\n") as g:
        g.write(json.dumps(riga, ensure_ascii=False) + "\n")


def t_r():
    """Radici d'ambiente enumerate.

    SC-22 non copre questo seam e non si finge che lo faccia: salta ogni file di
    tools/ che non nomini le credenziali dei provider, e reconcile_run.py non le
    nomina — di proposito, per lasciarlo lavorare sul caso che gli compete senza
    avvicinarglisi. La contenzione la prova questo criterio.

    (Questa spiegazione evita di scrivere per esteso i due marcatori su cui SC-22
    filtra: nominarli qui farebbe entrare il banco di prova nel suo perimetro, e il
    controllo si metterebbe a cercare in questo file una gestione di chiavi che non
    c'e'. E' successo, ed e' il motivo della perifrasi.)"""
    ammesse = {"DEEPSEARCH_TELEMETRY_ROOT", "DEEPSEARCH_STATE_ROOT"}
    lette, mediate, det = {}, [], []
    for f_ in ("reconcile_run.py", "run_close.py"):
        p = os.path.join(ROOT, "tools", f_)
        with open(p, encoding="utf-8") as g:
            albero = ast.parse(g.read(), p)
        lette[f_] = set()
        for n in ast.walk(albero):
            nomi = None
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("getenv", "get"):
                v = n.func.value
                ok = (isinstance(v, ast.Name) and v.id == "os") or \
                     (isinstance(v, ast.Attribute) and v.attr == "environ")
                if ok and n.args:
                    nomi = n.args[0]
            elif isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) \
                    and n.value.attr == "environ":
                nomi = n.slice
            if nomi is None:
                continue
            if isinstance(nomi, ast.Constant) and isinstance(nomi.value, str):
                lette[f_].add(nomi.value)
            else:
                mediate.append(f"{f_}:{n.lineno}")
    if mediate:
        det.append("lette mediante variabile: " + ", ".join(mediate))
    tutte = lette["reconcile_run.py"] | lette["run_close.py"]
    if tutte != ammesse:
        det.append(f"insieme letto {sorted(tutte)} != {sorted(ammesse)}")
    if lette["run_close.py"]:
        det.append(f"run_close.py ne legge {sorted(lette['run_close.py'])}")
    segna("T-R", "radici d'ambiente enumerate e lette per nome letterale",
          not det, "; ".join(det))


def t_s():
    """T-G esteso: vede gli alias, e non spara sui contenitori.

    Due lati, e servono entrambi. Senza il primo il criterio sarebbe aggirabile con
    un `import shutil as sh`; senza il secondo sarebbe rumoroso, e un criterio
    rumoroso viene disattivato dal primo che ha fretta."""
    base = _sorgente_run_close()
    det = []

    def sonda(nome, codice, deve_fallire):
        trovate = scansiona_scritture(base + "\n" + codice, "sonda")
        nuove = len(trovate) > 0
        if nuove is not deve_fallire:
            det.append(f"{nome}: atteso {'rilevata' if deve_fallire else 'ignorata'}")

    # DEVONO essere rilevate
    sonda("os.remove diretto", "import os\ndef _s1(p):\n    os.remove(p)\n", True)
    sonda("alias di modulo", "import shutil as sh\ndef _s2(a, b):\n    sh.copy(a, b)\n", True)
    sonda("import nudo", "from shutil import copy\ndef _s3(a, b):\n    copy(a, b)\n", True)
    sonda("alias di os", "import os as _o\ndef _s4(p):\n    _o.makedirs(p)\n", True)
    sonda("json.dump", "import json\ndef _s5(o, g):\n    json.dump(o, g)\n", True)
    sonda("write su file", "def _s6(g):\n    g.write('x')\n", True)
    sonda("open in append", "def _s7(p):\n    open(p, 'a')\n", True)
    sonda("riassegnazione di modulo", "import shutil\n_x = shutil\n", True)

    # NON devono essere rilevate: sono metodi su contenitori e su stringhe
    sonda("dict.copy", "def _n1(d):\n    return d.copy()\n", False)
    sonda("set.remove", "def _n2(s, x):\n    s.remove(x)\n", False)
    sonda("str.replace", "def _n3(t):\n    return t.replace('a', 'b')\n", False)
    sonda("list.append", "def _n4(l, x):\n    l.append(x)\n", False)
    sonda("open in lettura", "def _n5(p):\n    open(p, 'rb')\n", False)

    segna("T-S", "T-G vede gli alias e non spara sui contenitori", not det,
          "; ".join(det[:4]))


def t_u():
    """Il ri-puntamento e' avvenuto davvero, e la radice vergine non fa esplodere
    niente.

    La prima meta' e' il criterio che manca a T-C e T-D: se qualcuno rilocasse a
    meta', loro resterebbero verdi su un percorso morto e nessuno se ne accorgerebbe.
    Qui il percorso morto viene guardato di proposito."""
    det = []
    with Banco() as b:
        b.crea_legacy()
        b.crea_run(RUN_A)
        b.esegui(RUN_A, "--consolida")
        for f_ in ("query-ledger.jsonl", "source-ledger.jsonl"):
            if os.path.exists(b.percorso("state", f_)):
                det.append(f"scritto ancora sotto state/: {f_}")
            if not os.path.exists(b.stato_p(f_)):
                det.append(f"non scritto nella radice dello stato: {f_}")

    # Radice vergine: non esiste, e --consolida deve crearla invece di morire con
    # FileNotFoundError. E' il caso della macchina appena preparata, cioe' il primo
    # che si incontra e l'unico che nessuno prova.
    with Banco() as b:
        vergine = os.path.join(b.base, "mai-creata", "state")
        b.crea_run(RUN_A)
        r = b.esegui(RUN_A, "--consolida", stato=vergine)
        if "FileNotFoundError" in (r.stderr or ""):
            det.append("radice vergine: FileNotFoundError")
        if not os.path.exists(os.path.join(vergine, "source-ledger.jsonl")):
            det.append("radice vergine: il ledger non e' stato creato")

    segna("T-U", "stato rilocato davvero, radice vergine creata dallo scrittore",
          not det, "; ".join(det[:4]))


def t_v():
    """Attribuzione per run, zero legittimo, copertura delle query.

    L'ultimo caso e' quello per cui il vecchio `dich_s <= q` e' stato ritirato: un
    run che ripete query gia' presenti nel prefisso storico non le riconsolida, e
    con la vecchia aritmetica sarebbe stato grave per aver avuto memoria."""
    det = []
    with Banco() as b:
        b.crea_legacy()
        b.crea_run(RUN_A)
        b.esegui(RUN_A, "--consolida")
        r = b.chiudi(RUN_A)
        # le righe nuove sono attribuite a questo run, le legacy no
        if _esito(r.stdout, "query-ledger.jsonl: ogni riga nuova porta run_id") is not True:
            det.append("le righe scritte dal run non risultano attribuite")
        if _esito(r.stdout, "ogni ricerca dichiarata e' nel query-ledger") is not True:
            det.append("copertura: query consolidate non riconosciute")
        # il prefisso storico non si e' mosso
        b2 = leggi_byte(b.stato_p("source-ledger.jsonl"))
        if b2 is None or b"senza-run-id.invalid" not in b2:
            det.append("le righe storiche non attribuibili sono sparite")

        # Il caso decisivo: un SECONDO run che dichiara le stesse query. Non ne
        # consolida nessuna — ci sono gia' — quindi ne attribuisce zero a se stesso.
        # La copertura deve passare lo stesso: le query SONO nel ledger.
        b.crea_run(RUN_B)
        b.esegui(RUN_B, "--consolida")
        r2 = b.chiudi(RUN_B)
        if _esito(r2.stdout, "ogni ricerca dichiarata e' nel query-ledger") is not True:
            det.append("copertura: query gia' nel prefisso trattate come mai registrate")

    # Zero legittimo: un run che non dichiara nulla non produce gravi di copertura.
    with Banco() as b:
        b.crea_legacy()
        os.makedirs(b.percorso("runs", RUN_A, "wave1"), exist_ok=True)
        r = b.chiudi(RUN_A)
        if _esito(r.stdout, "ogni ricerca dichiarata e' nel query-ledger") is False:
            det.append("zero query dichiarate trattato come guasto")

    segna("T-V", "attribuzione per run, zero legittimo, copertura delle query",
          not det, "; ".join(det[:4]))


FILE_STATO = {"seen-pains.jsonl", "sector-history.jsonl", "shadow-portfolio.jsonl",
              "query-ledger.jsonl", "source-ledger.jsonl"}


def t_w():
    """Un solo punto di configurazione: tutti i percorsi di stato nascono da
    radice_stato(), e nessuno li ricostruisce per conto proprio.

    Serve perche' la rilocazione PARZIALE e' il guasto piu' insidioso di tutti: se
    uno solo dei cinque join tornasse a ROOT/state, ogni lettore troverebbe un file
    plausibile — vuoto invece che pieno, o pieno invece che vuoto — e nessun altro
    criterio se ne accorgerebbe. run_close.py:207 in particolare ripeteva il join
    invece di riusare quello di :124, ed e' il punto in cui una rilocazione a meta'
    passerebbe inosservata.

    La prova e' strutturale, non testuale: si guarda l'albero sintattico, non si
    cerca una stringa. Un commento che nomini ROOT/state non e' un difetto; una
    chiamata che lo costruisca lo e'."""
    det = []
    for f_ in ("reconcile_run.py", "run_close.py"):
        p = os.path.join(ROOT, "tools", f_)
        with open(p, encoding="utf-8") as g:
            albero = ast.parse(g.read(), p)

        # nomi assegnati da radice_stato(): `_stato = radice_stato()`
        da_radice = set()
        for n in ast.walk(albero):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                    and isinstance(n.value.func, ast.Name) \
                    and n.value.func.id == "radice_stato":
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        da_radice.add(t.id)

        # La definizione di radice_stato() e' l'unico posto in cui il segmento "state"
        # DEVE essere costruito: e' il punto di configurazione, ed escluderlo non e'
        # un'eccezione di comodo — e' la definizione stessa di "un solo punto".
        dentro = set()
        for n in ast.walk(albero):
            if isinstance(n, ast.FunctionDef) and n.name == "radice_stato":
                dentro = set(range(n.lineno, (n.end_lineno or n.lineno) + 1))

        for n in ast.walk(albero):
            if getattr(n, "lineno", None) in dentro:
                continue
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "join"):
                continue
            cost = [a.value for a in n.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            # (a) il segmento "state" non ha piu' alcuna ragione di essere costruito
            if "state" in cost:
                det.append(f"{f_}:{n.lineno} costruisce ancora il segmento 'state'")
                continue
            # (b) chi nomina un file di stato deve partire da radice_stato()
            if not (FILE_STATO & set(cost)):
                continue
            primo = n.args[0] if n.args else None
            ok = (isinstance(primo, ast.Call) and isinstance(primo.func, ast.Name)
                  and primo.func.id == "radice_stato") or \
                 (isinstance(primo, ast.Name) and primo.id in da_radice)
            if not ok:
                det.append(f"{f_}:{n.lineno} nomina un file di stato senza radice_stato()")

    segna("T-W", "un solo punto di configurazione della radice dello stato",
          not det, "; ".join(det[:4]))


def _radice_stato_di(albero, con_variabile=None):
    """radice_stato() come la calcola l'albero indicato, in un processo separato.

    Serve un processo separato perche' il valore dipende dalla posizione del file
    sorgente e dall'ambiente, e importarlo qui darebbe la risposta di QUESTO albero
    con QUESTO ambiente — cioe' non la domanda che si vuole porre."""
    env = dict(os.environ)
    env.pop("DEEPSEARCH_STATE_ROOT", None)
    if con_variabile is not None:
        env["DEEPSEARCH_STATE_ROOT"] = con_variabile
    codice = ("import sys; sys.path.insert(0, sys.argv[1]);"
              " import reconcile_run; print(reconcile_run.radice_stato())")
    r = subprocess.run([sys.executable, "-c", codice, os.path.join(albero, "tools")],
                       capture_output=True, text=True, env=env,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip()


def _antenati(p):
    p = os.path.abspath(p)
    while True:
        yield p
        su = os.path.dirname(p)
        if su == p:
            return
        p = su


def t_x():
    """La radice di PRODUZIONE: quella che si ottiene senza il seam d'ambiente.

    Il banco fissa sempre DEEPSEARCH_STATE_ROOT, quindi non esercita mai il ramo di
    ripiego di radice_stato() — che e' precisamente il ramo che vale in produzione,
    dove nessuna variabile e' impostata. T-W non lo copre, e non per dimenticanza:
    esclude il corpo di radice_stato() perche' e' li' che il segmento va composto,
    una volta sola. Restava scoperto il caso peggiore — riportare il SOLO ripiego a
    ROOT/state — che passava tutti gli altri criteri lasciando la memoria dentro il
    repository e dentro Syncthing su ogni macchina reale.

    Le tre proprieta' si verificano sul valore calcolato, non sul testo del sorgente:
    un ripiego riportato a ROOT/state fallisce la seconda per costruzione, perche' il
    percorso che produce sta dentro l'albero."""
    det = []
    reale = _radice_stato_di(ROOT)
    atteso = os.path.join(os.path.dirname(os.path.dirname(ROOT)), "runtime", "state")

    # 1. senza variabile, il valore e' esattamente quello previsto
    if os.path.normpath(reale) != os.path.normpath(atteso):
        det.append(f"senza variabile: {reale!r} invece di {atteso!r}")

    # 2. fuori dal repository. E' il controllo che intercetta il ripiego riportato a
    #    ROOT/state: quel percorso e' dentro l'albero, e qui si misura.
    r_abs, root_abs = os.path.abspath(reale), os.path.abspath(ROOT)
    if r_abs == root_abs or r_abs.startswith(root_abs + os.sep):
        det.append(f"la radice cade DENTRO il repository: {reale}")

    # 3. fuori da Syncthing: nessun .stfolder in alcun antenato
    sync = [a for a in _antenati(reale) if os.path.isdir(os.path.join(a, ".stfolder"))]
    if sync:
        det.append(f"antenato sincronizzato: {sync[0]}")

    # 4. con la variabile impostata, il seam continua a valere
    finto = os.path.join(tempfile.gettempdir(), "radice-di-prova-ts21")
    con = _radice_stato_di(ROOT, con_variabile=finto)
    if os.path.normpath(con) != os.path.normpath(finto):
        det.append(f"con variabile: {con!r} invece di {finto!r}")

    segna("T-X", "radice di produzione: fuori dal repository e da Syncthing",
          not det, "; ".join(det) if det else reale)


def t_t():
    """La radice dello stato non viene dalla configurazione.

    Le chiavi memory.*_file nominano ancora la vecchia collocazione e nessuno le
    legge. Restano li' perche' correggerle allargherebbe il perimetro; il rischio e'
    che qualcuno le prenda per la sorgente della radice, ed e' questo a doverlo
    escludere. L'alterazione avviene sulla COPIA: il file di configurazione del
    repository non viene toccato in alcun caso."""
    det = []
    with Banco() as b:
        p = b.percorso("config", "deepsearch.config.json")
        with open(p, encoding="utf-8") as g:
            cfg = json.load(g)
        mem = cfg.get("memory")
        if not isinstance(mem, dict):
            det.append("config.memory assente: il caso non e' piu' quello descritto")
        else:
            for k in list(mem):
                if k.endswith("_file"):
                    mem[k] = "percorso/inventato/" + os.path.basename(str(mem[k]))
            with open(p, "w", encoding="utf-8", newline="\n") as g:
                json.dump(cfg, g, ensure_ascii=False, indent=2)
            b.crea_legacy()
            b.crea_run(RUN_A)
            b.esegui(RUN_A, "--consolida")
            if not os.path.exists(b.stato_p("source-ledger.jsonl")):
                det.append("la radice e' cambiata con la configurazione")
            if os.path.exists(b.percorso("percorso", "inventato")):
                det.append("una chiave di configurazione ha prodotto un percorso reale")
    segna("T-T", "la radice dello stato non si legge dalla configurazione",
          not det, "; ".join(det))


# ---------------------------------------------------------------- censimento dei seam

SEAM = re.compile(r"DEEPSEARCH_[A-Z0-9_]+")

# L'INVENTARIO. Per ogni seam: il file di PRODUZIONE che lo possiede e usa, e i file di
# TEST O POLICY autorizzati soltanto a NOMINARLO.
#
# QUESTO FILE COMPARE PER TUTTI E TRE I NOMI, ed e' dichiarato, non nascosto: e' il file che
# porta l'inventario, e i nomi devono starci in chiaro. Spezzarli in concatenazioni, costruirli
# a runtime o escludere questo file dalla camminata renderebbe il criterio cieco a se stesso —
# e un criterio che non si vede e' la premessa di ogni difetto che questa catena ha inseguito.
#
# Con l'inventario ai soli due seam storici il criterio e' ROSSO su `gate_all.py`: quel rosso e'
# la voce aperta della ricevuta 59 §8.6, ed e' il motivo per cui questa prova esiste.
SEAM_INVENTARIO = {
    "DEEPSEARCH_STATE_ROOT":
        ("reconcile_run.py", ("reconcile_run_selftest.py", "run_close_selftest.py")),
    "DEEPSEARCH_TELEMETRY_ROOT":
        ("reconcile_run.py", ("reconcile_run_selftest.py",)),
    "DEEPSEARCH_GATE_ROOTS":
        ("gate_all.py", ("reconcile_run_selftest.py",)),
}


def _seam_trovati(radice):
    """Per ogni seam trovato, l'insieme dei file di `radice` che lo NOMINANO, e l'elenco
    dei file che NON si sono potuti leggere.

    UN FILE ILLEGGIBILE NON E' UN FILE SENZA SEAM. Saltarlo — com'e' stato fino al
    commit precedente, con un `except SyntaxError: continue` — ne fa sparire i seam dal
    censimento e lascia il criterio VERDE: un errore nella direzione insicura. Ora ogni
    errore di lettura o di parsing e' un rosso che nomina il file e la causa.

    Non e' pero' l'UNICO modo di far tacere questo controllo senza toccarlo, e dirlo
    sarebbe sovradetto: un seam scritto in un file che non finisce in `.py`, o annidato
    sotto tools/, o raggiunto da un symlink penzolante — `os.path.isfile` falso — viene
    saltato altrettanto in silenzio. Sono i limiti gia' dichiarati al §9 del mandato 60,
    e questa slice non li tocca.

    Si cattura `Exception` e non il solo `SyntaxError`: un sorgente puo' essere
    irraggiungibile anche per codifica invalida, byte nulli o profondita' di ricorsione, e
    per il censimento sono tutti lo stesso fatto — di quel file non si sa nulla."""
    fuori, illeggibili = {}, []
    for nome in sorted(os.listdir(radice)):
        p = os.path.join(radice, nome)
        if not nome.endswith(".py") or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as g:
                sorgente = g.read()
            albero = ast.parse(sorgente, p)
        except Exception as e:                                            # noqa: BLE001
            illeggibili.append((nome, f"{type(e).__name__}: {e}"))
            continue
        for n in ast.walk(albero):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                for s in SEAM.findall(n.value):
                    fuori.setdefault(s, set()).add(nome)
    return fuori, illeggibili


def _seam_problemi(radice, inventario):
    trovati, illeggibili = _seam_trovati(radice)
    det = [f"{nome}: non parsabile — {msg}" for nome, msg in illeggibili]
    for s in sorted(set(trovati) - set(inventario)):
        det.append(f"seam non dichiarato {s} in {sorted(trovati[s])}")
    for s in sorted(set(inventario) - set(trovati)):
        det.append(f"seam dichiarato e non trovato: {s}")
    for s in sorted(set(inventario) & set(trovati)):
        proprietario, menzioni = inventario[s]
        atteso = {proprietario} | set(menzioni)
        piu_ = sorted(trovati[s] - atteso)
        meno = sorted(atteso - trovati[s])
        if piu_:
            det.append(f"{s}: file in piu' {piu_}")
        if meno:
            det.append(f"{s}: file dichiarato che non lo nomina {meno}")
    return det


# DEROGA DICHIARATA, e vale SOLO da qui in giu' e SOLO per le fixture sintetiche.
#
# La prova della chiusura pretende, nella copia, un nome che nell'inventario non c'e'. Ma questo
# file finisce nel censimento che esso stesso esegue: scrivere qui il nome per esteso lo farebbe
# trovare QUI, e il caso `a` diventerebbe rosso. Dichiararlo nell'inventario sarebbe peggio —
# metterebbe nel registro un nome che NON e' un seam, cioe' una falsita' nel documento che il
# criterio esiste per tenere vero, e la chiusura perderebbe l'unica prova che la osserva.
#
# Quindi il nome sintetico si costruisce a runtime. La deroga **non** si estende ai tre seam
# reali, all'inventario, al criterio `t_y` ne' ad alcun codice di produzione: quelli restano in
# chiaro, ed e' il §5 del mandato 60 a pretenderlo.
#
# NELLA COPIA il nome arriva come COSTANTE LETTERALE COMPLETA — `_Q = "DEEPSEARCH_QUARTO"` — per
# cui la chiusura vi si esercita davvero, su un albero indistinguibile da uno in cui qualcuno
# avesse introdotto un quarto seam per davvero.
_SEAM_FINTO = "DEEPSEARCH_" + "QUARTO"


def _seam_copia(muta):
    """Una copia usa-e-getta di tools/, mutata. Il tools/ reale non si tocca mai."""
    d = tempfile.mkdtemp(prefix="seam-")
    t = os.path.join(d, "tools")
    shutil.copytree(QUI, t, ignore=shutil.ignore_patterns("__pycache__"))
    muta(t)
    return d, t


def _seam_appendi(t, nome, testo):
    with open(os.path.join(t, nome), "a", encoding="utf-8", newline="\n") as g:
        g.write(testo)


def _seam_togli(t, nome, quale):
    p = os.path.join(t, nome)
    with open(p, encoding="utf-8") as g:
        s = g.read()
    if quale not in s:
        raise AssertionError(f"ancoraggio assente: {quale} in {nome}")
    with open(p, "w", encoding="utf-8", newline="\n") as g:
        g.write(s.replace(quale, "XX_" + quale[len("DEEPSEARCH_"):]))


def t_y():
    """Il censimento dei seam d'ambiente. Mandato 60, redazione 2 (`ba358ddb...`).

    UNA REGOLA SOLA: per ogni seam dichiarato, l'insieme dei file che lo NOMINANO e'
    esattamente {proprietario} unito alle menzioni dichiarate. Piu' una chiusura
    sull'insieme dei nomi.

    Il ruolo e' DICHIARATO, NON DEDOTTO: per un file non dichiarato il criterio non
    puo' sapere in quale ruolo cadrebbe, quindi non lo assegna a nessuno dei due — lo
    rifiuta e basta. E' il caso `d` a inchiodarlo, con una fixture che si chiama
    `*_selftest.py` e cade lo stesso.

    La seconda meta' della chiusura — «un nome dichiarato e sparito» — e' STRUTTURALMENTE
    INERTE su un albero che contiene questo file: l'inventario tiene vivo ogni nome che
    dichiara. Resta nel codice perche' su un albero che NON contiene questo file (mutazione
    9) e' l'unica cosa che parla. La sparizione dal proprietario la coglie la regola unica.

    Gli otto casi sintetici girano su COPIE usa-e-getta di tools/. Il tools/ reale lo si
    LEGGE soltanto, nel caso `a`."""
    det = []

    def prova(etichetta, radice, deve_fallire, atteso=None):
        p = _seam_problemi(radice, SEAM_INVENTARIO)
        if bool(p) is not deve_fallire:
            det.append(f"{etichetta}: atteso {'rosso' if deve_fallire else 'verde'}, "
                       f"ottenuto {'; '.join(p) if p else 'verde'}")
        elif atteso and not any(atteso in x for x in p):
            det.append(f"{etichetta}: rosso ma senza «{atteso}» — {'; '.join(p)}")

    prova("a", QUI, False)

    casi = (
        # (etichetta, mutazione, deve_fallire, frammento atteso nel messaggio)
        ("b", lambda t: _seam_appendi(t, "null_check.py", '\n_Q = "%s"\n' % _SEAM_FINTO),
         True, "seam non dichiarato " + _SEAM_FINTO),
        ("c", lambda t: _seam_appendi(t, "null_check.py", '\n_X = "DEEPSEARCH_STATE_ROOT"\n'),
         True, "file in piu' ['null_check.py']"),
        ("d", lambda t: _seam_appendi(t, "pack_repair_selftest.py",
                                      '\n_X = "DEEPSEARCH_STATE_ROOT"\n'),
         True, "file in piu' ['pack_repair_selftest.py']"),
        ("e", lambda t: _seam_togli(t, "reconcile_run.py", "DEEPSEARCH_TELEMETRY_ROOT"),
         True, "che non lo nomina ['reconcile_run.py']"),
        ("f", lambda t: _seam_togli(t, "run_close_selftest.py", "DEEPSEARCH_STATE_ROOT"),
         True, "che non lo nomina ['run_close_selftest.py']"),
        ("g", lambda t: _seam_appendi(t, "null_check.py", "\n# %s\n" % _SEAM_FINTO),
         False, None),
        ("h", lambda t: _seam_appendi(
            t, "null_check.py",
            '\n\ndef _prosa():\n    """Una nota che cita %s dentro la prosa."""\n' % _SEAM_FINTO),
         True, "seam non dichiarato " + _SEAM_FINTO),
        # Un `.py` che non si parsa non e' un file senza seam: e' un file di cui non si sa
        # nulla. Saltarlo in silenzio farebbe sparire i suoi seam dal censimento e lascerebbe
        # il criterio VERDE — un errore in direzione insicura.
        ("i", lambda t: _seam_appendi(t, "zz_rotto.py", "def (\n"),
         True, "zz_rotto.py: non parsabile"),
    )
    for etichetta, muta, deve_fallire, atteso in casi:
        d = None
        try:
            d, t = _seam_copia(muta)
            prova(etichetta, t, deve_fallire, atteso)
        except Exception as e:                                            # noqa: BLE001
            det.append(f"{etichetta}: {type(e).__name__}: {e}")
        finally:
            if d:
                shutil.rmtree(d, ignore_errors=True)

    segna("T-Y", "seam d'ambiente censiti, e ciascuno solo dove e' dichiarato",
          not det, " | ".join(det))


# ---------------------------------------------------------------- T-Z, T-AA (B-1)
#
# IL PACK CONSUMABILE VIVE A UN LIVELLO: `02-evidence/P-<id>-<canale>.json`. Sotto la stessa
# cartella stanno anche i MANDATI — `02-evidence/<pain>/mandato-<canale>.json`, scritti da
# `contract_compile.py` — e la DIAGNOSTICA dei giri, `_giro*/`. Nessuno dei due e' evidenza
# consumata da chi riconcilia, e il glob a un livello e' percio' la regola, non un difetto.
#
# Cio' che mancava e' il fail-closed: un run che DICHIARA evidenza e non ne ha sul disco veniva
# riconciliato lo stesso, e la telemetria scriveva la sua riga. Qui `riconcilia` deve fermarsi
# PRIMA — prima del consolidamento, prima del sidecar, prima del ledger `M0.2` — perche' una riga
# di telemetria su un run senza evidenza e' un dato che sembra buono e non lo e'.

def _fixture_b1(b, run_id, pains_to_evidence, pack=(), mandati=(), giri=()):
    """Le tre forme di contenuto di `02-evidence/`, piu' `00-run-stats.json`."""
    run_dir = os.path.join(b.dst, "runs", run_id)
    ev = os.path.join(run_dir, "02-evidence")
    os.makedirs(ev, exist_ok=True)
    with open(os.path.join(run_dir, "00-run-stats.json"), "w", encoding="utf-8") as g:
        json.dump({"pains_to_evidence": pains_to_evidence}, g, ensure_ascii=False)
    for nome in pack:
        with open(os.path.join(ev, nome), "w", encoding="utf-8") as g:
            json.dump({"agent": "evidence-researcher", "run_id": run_id, "status": "OK",
                       "payload": {"claims": []}}, g, ensure_ascii=False)
    for pain, canale in mandati:
        d = os.path.join(ev, pain)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"mandato-{canale}.json"), "w", encoding="utf-8") as g:
            json.dump({"pain": {"id": pain}, "channel": canale}, g, ensure_ascii=False)
    for giro, nome in giri:
        d = os.path.join(ev, giro)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, nome), "w", encoding="utf-8") as g:
            json.dump({"agent": "evidence-researcher", "run_id": run_id, "status": "OK",
                       "payload": {"claims": []}}, g, ensure_ascii=False)


def t_z():
    """Statistiche positive senza pack consumabile: uscita 2, causa nominata, nulla scritto."""
    det = []
    with Banco() as b:
        b.crea_run(RUN_A)
        _fixture_b1(b, RUN_A, 1, mandati=[("P-003", "community")],
                    giri=[("_giro1", "P-003-community.json")])
        r = b.esegui(RUN_A, "--consolida", "--telemetry-m0.2")
        testo = r.stdout + r.stderr
        if r.returncode != 2:
            det.append(f"uscita {r.returncode}, atteso 2")
        if "evidence-pack consumabile" not in testo:
            det.append("la causa non e' nominata nell'uscita")
        if os.path.exists(b.ledger()):
            det.append("il ledger M0.2 e' stato scritto nonostante l'arresto")
        if os.path.exists(b.sidecar(RUN_A)):
            det.append("il sidecar e' stato scritto nonostante l'arresto")
        # e nemmeno il consolidamento: i ledger di stato non devono essere toccati
        for f_ in ("source-ledger.jsonl", "query-ledger.jsonl"):
            if os.path.exists(os.path.join(b.stato, f_)):
                det.append(f"{f_} scritto: il consolidamento non si e' fermato prima")
    segna("T-Z", "stats positive senza pack: exit 2 prima di tutto", not det, "; ".join(det))


def t_aa():
    """Le due controprove: il pack canonico passa, e le stats a zero senza pack pure.

    Senza di esse la riparazione potrebbe fermare qualunque run, che e' il modo piu' facile di
    rendere verde un fail-closed: basta chiuderlo su tutti.
    """
    det = []
    with Banco() as b:
        b.crea_run(RUN_A)
        _fixture_b1(b, RUN_A, 1, pack=["P-003-community.json"],
                    mandati=[("P-003", "community")],
                    giri=[("_giro1", "P-003-community.json")])
        r = b.esegui(RUN_A, "--telemetry-m0.2")
        if r.returncode == 2:
            det.append("il pack canonico a un livello non e' stato visto: uscita 2")
        if not os.path.exists(b.ledger()):
            det.append("col pack presente la telemetria non ha scritto")
    with Banco() as b:
        b.crea_run(RUN_B)
        _fixture_b1(b, RUN_B, 0, mandati=[("P-003", "community")])
        r = b.esegui(RUN_B, "--telemetry-m0.2")
        if r.returncode == 2:
            det.append("stats a zero senza pack: uscita 2, ma l'assenza e' corretta")
        if not os.path.exists(b.ledger()):
            det.append("con stats a zero la telemetria non ha scritto")
    segna("T-AA", "pack canonico e stats a zero: nessun arresto", not det, "; ".join(det))


def main():
    for t in (t_a, t_b, t_c, t_d, t_e, t_f, t_g, t_h, t_j, t_k, t_l, t_o,
              t_p, t_q, t_r, t_s, t_t, t_u, t_v, t_w, t_x, t_y, t_z, t_aa):
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
