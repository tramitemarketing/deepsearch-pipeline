#!/usr/bin/env python3
"""
gate_all_selftest.py — dimostra che gate_all.py NON e' cieco.

Mandato `56-MANDATO-AGGREGATORE-GATE.md`, redazione 5, sha256
`d6338e7a7802ad09a15e405f85526c9a911dfe001d494c4c6566776ee7546e32`, approvato con `PASS`
dall'atto `57`; sede e baseline dall'atto `58`.

QUATTRO PROPRIETA' CHE UN AGGREGATORE PUO' AVERE SOLO PER COSTRUZIONE:

  1. IL CENSIMENTO E' COMPLETO, NON EURISTICO. Ogni `.py` sotto `tools/` e' classificato, e ogni
     voce porta una ragione scritta — anche gli STRUMENTI. Un file nuovo non classificato fa
     fallire il giro invece di essere ignorato. Un `glob(*_selftest.py)` sposterebbe il difetto
     senza chiuderlo: `ds_validate_concurrency.py` e `spec_check.py` sono prove e non ci
     cascherebbero dentro.
  2. LA NON-RICORSIONE E' STRUTTURALE. `gate_all` esporta alle prove che lancia l'INSIEME delle
     radici gia' in esame e rifiuta di rientrarci. Non una casella sola: con una casella il ciclo
     di lunghezza 2 passa.
  3. «NON HA ESEGUITO NULLA» SI GUARDA, NON SI ASSERISCE. Due osservatori, perche' nessuno dei
     due basta: la TRACCIA — che ogni prova finta scrive, col percorso scritto per esteso nella
     fixture e non ricevuto dall'ambiente — e lo STUB sul punto unico di lancio. Lo stub e' cieco
     all'aggiramento; la traccia dice che e' successo ma non dove.
  4. «IN SERIE» SI OSSERVA. Le prove finte tengono una finestra dichiarata fra le due scritture:
     in serie la traccia non si interlaccia mai, in parallelo si', e non basta riordinare il
     rapporto alla fine per passare.

IL SELFTEST ESERCITA `gate_all` IN-PROCESSO, chiamandone `main(argv)`. E' l'idioma che
`replay_report.py` gia' usa per guidare `replay_validate`. UNICA ECCEZIONE, e non e' un caso ma
una FORMA: ogni caso il cui albero contiene una prova che non termina gira in SOTTOPROCESSO, con
un limite d'attesa proprio di questo selftest — altrimenti la mutazione «il timeout non e'
applicato» farebbe restare APPESO chi osserva, e una mutazione che appende non falsifica nulla.
Oggi sono `E` ed `F`.

NESSUNA PROVA LEGGE O SCRIVE `runtime/`, il ledger, lo stato o i replay. Ogni albero e' costruito
in una directory temporanea e rimosso. Il `tools/` reale lo si LEGGE soltanto, e da TRE casi —
`L`, `M` e `Q` — che chiamano `censimento()` con lo stub di lancio installato.

  uso:  python tools/gate_all_selftest.py
        exit 0 = tutte verdi
"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile, time

import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))
if QUI not in sys.path:
    sys.path.insert(0, QUI)

import gate_all as ga                                                     # noqa: E402

GATE_ALL = os.path.join(QUI, "gate_all.py")

# Il limite d'attesa PROPRIO del selftest, per i casi in sottoprocesso. Deve stare
# generosamente sopra il timeout che passiamo a gate_all: se scade, non e' che la prova finta e'
# lenta — e' che gate_all non ha applicato il proprio timeout, ed e' quello che vogliamo vedere.
LIMITE_SELFTEST = 30.0

# Il testimone del caso `P`. Vedi `main()`: la batteria si mette in bocca un valore noto PRIMA di
# neutralizzare, cosi' la situazione che la neutralizzazione deve gestire e' la stessa in TDD
# (variabile assente) e nella passata (variabile ereditata da gate_all). Senza, `P` sarebbe vero
# per costruzione in TDD e la mutazione 26 sarebbe muta.
TESTIMONE = "//testimone-del-caso-P"

ESITI = []


def prova(nome, desc):
    def _(f):
        ESITI.append((nome, desc, f))
        return f
    return _


# ------------------------------------------------------------------ la primitiva d'ambiente
#
# UNA SOLA, usata sia all'ingresso della batteria sia dal caso `P`. Se fossero due — una scritta
# in linea all'ingresso e una dentro il collaudo — `P` collauderebbe codice nato dentro il
# collaudo, e la mutazione 26 tornerebbe muta sul ramo d'ingresso.

@contextlib.contextmanager
def ambiente_neutro():
    prima = os.environ.get(ga.CHIAVE_RADICI)
    os.environ.pop(ga.CHIAVE_RADICI, None)
    try:
        yield prima
    finally:
        if prima is None:
            os.environ.pop(ga.CHIAVE_RADICI, None)
        else:
            os.environ[ga.CHIAVE_RADICI] = prima


@contextlib.contextmanager
def radici(valore):
    """Imposta l'insieme delle radici per il tempo di un caso, e lo rimette a posto."""
    prima = os.environ.get(ga.CHIAVE_RADICI)
    if valore is None:
        os.environ.pop(ga.CHIAVE_RADICI, None)
    else:
        os.environ[ga.CHIAVE_RADICI] = valore
    try:
        yield
    finally:
        if prima is None:
            os.environ.pop(ga.CHIAVE_RADICI, None)
        else:
            os.environ[ga.CHIAVE_RADICI] = prima


@contextlib.contextmanager
def stub_di_lancio():
    """Sostituisce il punto unico di lancio con uno che solleva.

    Fallisce NEL PUNTO del difetto invece che alla fine. E' cieco a un aggiramento — per quello
    c'e' la traccia — ma e' l'unico osservatore che funzioni sul caso `L`, che gira sul `tools/`
    reale e non ha prove finte da tracciare."""
    vero = ga.esegui_prova

    def _stub(*a, **k):
        raise AssertionError("una prova e' stata lanciata dove nulla doveva esserlo")

    ga.esegui_prova = _stub
    try:
        yield
    finally:
        ga.esegui_prova = vero


# ------------------------------------------------------------------ le fixture

# Il percorso della traccia e' scritto PER ESTESO nel sorgente della prova finta: non arriva
# dall'ambiente e non e' dedotto da un argomento. Se arrivasse dalla funzione di lancio,
# aggirare quella funzione toglierebbe anche la traccia, e l'osservazione morirebbe insieme
# all'osservatore — che e' esattamente il difetto che la mutazione 20 cerca.
MODELLO = '''\
import os, sys, time
_TRACCIA = {traccia!r}
def _segna(evento):
    with open(_TRACCIA, "a", encoding="utf-8", newline="\\n") as g:
        g.write("%s\\t%s\\t%s\\n" % (evento, {nome!r}, os.environ.get({chiave!r}, "")))
_segna("inizio")
{corpo}
_segna("fine")
sys.exit({codice})
'''


def albero(nome_caso):
    d = tempfile.mkdtemp(prefix="gate-all-" + nome_caso + "-")
    os.makedirs(os.path.join(d, "tools"), exist_ok=True)
    return d


def radice(d):
    return os.path.join(d, "tools")


def traccia_di(d):
    return os.path.join(d, "traccia.txt")


def scrivi_prova(d, nome, codice=0, corpo="pass", ):
    p = os.path.join(radice(d), nome)
    with open(p, "w", encoding="utf-8", newline="\n") as g:
        g.write(MODELLO.format(traccia=traccia_di(d), nome=nome,
                               chiave=ga.CHIAVE_RADICI, corpo=corpo, codice=codice))
    return p


def scrivi_strumento(d, nome, corpo="pass"):
    p = os.path.join(radice(d), nome)
    with open(p, "w", encoding="utf-8", newline="\n") as g:
        g.write("# strumento finto\n" + corpo + "\n")
    return p


def scrivi_censimento(d, prove=None, strumenti=None, escluse=None):
    c = {"PROVE": prove or {}, "STRUMENTI": strumenti or {}, "ESCLUSE": escluse or {}}
    p = os.path.join(d, "censimento.json")
    with open(p, "w", encoding="utf-8", newline="\n") as g:
        json.dump(c, g, ensure_ascii=False, indent=2)
    return p


def letture(d):
    """La traccia, come lista di (evento, nome, radici)."""
    p = traccia_di(d)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as g:
        return [tuple(r.rstrip("\n").split("\t")) for r in g if r.strip()]


def eseguite(d):
    return [n for e, n, _ in letture(d) if e == "inizio"]


def esegui(argv, ambiente=None):
    """Chiama gate_all.main in-processo e restituisce (codice, rapporto)."""
    out, err = io.StringIO(), io.StringIO()
    with radici(ambiente):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            codice = ga.main(argv)
    return codice, out.getvalue() + err.getvalue()


def righe_prova(rapporto):
    """Le righe del rapporto che riguardano una prova, gia' spaccate."""
    fuori = []
    for r in rapporto.splitlines():
        m = re.match(r"^\s{2}(verde|ROSSA|NON TERMINATA|GUASTA|ESCLUSA)\s+(\S+)\s+(\S+)\s*(.*)$",
                     r)
        if m:
            fuori.append({"esito": m.group(1), "durata": m.group(2),
                          "nome": m.group(3), "coda": m.group(4)})
    return fuori


def nomi_riportati(rapporto):
    return [r["nome"] for r in righe_prova(rapporto)]


def _termina_albero(p):
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(p.pid), 9)
    except Exception:                                                     # noqa: BLE001
        try: p.kill()
        except Exception: pass
    try: p.wait(timeout=10)
    except Exception: pass


def esegui_fuori(argv, ambiente=None):
    """gate_all come SOTTOPROCESSO, col limite d'attesa proprio del selftest.

    Solo per i casi il cui albero contiene una prova che non termina: in-processo, se gate_all
    non applicasse il proprio timeout, la chiamata non tornerebbe e il caso resterebbe APPESO
    invece di diventare rosso."""
    amb = dict(os.environ)
    amb.pop(ga.CHIAVE_RADICI, None)
    if ambiente is not None:
        amb[ga.CHIAVE_RADICI] = ambiente
    amb["PYTHONIOENCODING"] = "utf-8"
    kw = {}
    if os.name != "nt":
        kw["start_new_session"] = True
    p = subprocess.Popen([sys.executable, GATE_ALL] + argv,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=amb, **kw)
    try:
        out, err = p.communicate(timeout=LIMITE_SELFTEST)
    except subprocess.TimeoutExpired:
        _termina_albero(p)
        raise AssertionError(
            f"gate_all non ha applicato il proprio timeout: non e' tornato entro "
            f"{LIMITE_SELFTEST}s") from None
    d = lambda b: (b or b"").decode("utf-8", "replace")
    return p.returncode, d(out) + d(err)


# ================================================================== i casi

@prova("A", "tutte verdi: esito 0, e il rapporto elenca anche le verdi")
def caso_a():
    d = albero("a")
    try:
        for n in ("alfa_selftest.py", "beta_selftest.py"):
            scrivi_prova(d, n, 0)
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "prova finta verde",
                                        "beta_selftest.py": "prova finta verde"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 0, f"uscita {codice}\n{rap}"
        assert sorted(nomi_riportati(rap)) == ["alfa_selftest.py", "beta_selftest.py"], rap
        assert all(r["esito"] == "verde" for r in righe_prova(rap)), rap
        assert sorted(eseguite(d)) == ["alfa_selftest.py", "beta_selftest.py"], letture(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("B", "una rossa fra le verdi: esito 1, il rapporto la nomina e riporta il suo stderr")
def caso_b():
    d = albero("b")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        scrivi_prova(d, "zeta_selftest.py", 1,
                     corpo='sys.stderr.write("il-guasto-di-zeta\\n")')
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde",
                                        "zeta_selftest.py": "rossa"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 1, f"uscita {codice}\n{rap}"
        r = [x for x in righe_prova(rap) if x["nome"] == "zeta_selftest.py"]
        assert r and r[0]["esito"] == "ROSSA", rap
        assert "il-guasto-di-zeta" in rap, "lo stderr della rossa non e' riportato\n" + rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("C", "un .py non classificato: esito 2, e nessuna prova eseguita")
def caso_c():
    d = albero("c")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        scrivi_strumento(d, "intruso.py")
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde"})
        with stub_di_lancio():
            codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 2, f"uscita {codice}\n{rap}"
        assert "intruso.py" in rap, rap
        assert eseguite(d) == [], f"traccia non vuota: {letture(d)}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D", "un nome classificato che non esiste sul disco: esito 2")
def caso_d():
    d = albero("d")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde",
                                        "fantasma_selftest.py": "cancellata mesi fa"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 2, f"uscita {codice}\n{rap}"
        assert "fantasma_selftest.py" in rap, rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("E", "una prova che non termina: esito 3, NON TERMINATA, e il nipote non sopravvive")
def caso_e():
    d = albero("e")
    marca = os.path.join(d, "nipote.txt")
    try:
        # Il nipote sta in un file proprio: annidare il suo sorgente dentro il sorgente della
        # prova finta significherebbe annidare due livelli di quoting attorno a un percorso di
        # Windows, e la fixture si rompe prima di provare alcunche'.
        nipote = os.path.join(d, "nipote.py")
        with open(nipote, "w", encoding="utf-8", newline="\n") as g:
            g.write("import sys, time\ntime.sleep(2.0)\n"
                    "open(sys.argv[1], 'w', encoding='utf-8').write('vivo')\n")
        corpo = ("import subprocess\n"
                 "subprocess.Popen([sys.executable, %r, %r])\n"
                 "time.sleep(60)\n" % (nipote, marca))
        scrivi_prova(d, "lenta_selftest.py", 0, corpo=corpo)
        c = scrivi_censimento(d, prove={"lenta_selftest.py": "non termina"})
        avvio = time.monotonic()
        codice, rap = esegui_fuori(["--radice", radice(d), "--censimento", c, "--timeout", "0.6"])
        assert codice == 3, f"uscita {codice}\n{rap}"
        r = [x for x in righe_prova(rap) if x["nome"] == "lenta_selftest.py"]
        assert r and r[0]["esito"] == "NON TERMINATA", rap
        # finestra di grazia dichiarata: il nipote scriverebbe a 2.0s dal proprio avvio
        while time.monotonic() - avvio < 3.5:
            time.sleep(0.1)
        assert not os.path.exists(marca), \
            "il nipote e' sopravvissuto: si e' terminato il figlio, non l'albero"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F", "una rossa e una non terminata: esito 3, e il rapporto le elenca entrambe")
def caso_f():
    d = albero("f")
    try:
        scrivi_prova(d, "lenta_selftest.py", 0, corpo="time.sleep(60)")
        scrivi_prova(d, "zeta_selftest.py", 1, corpo='sys.stderr.write("rotta\\n")')
        c = scrivi_censimento(d, prove={"lenta_selftest.py": "non termina",
                                        "zeta_selftest.py": "rossa"})
        codice, rap = esegui_fuori(["--radice", radice(d), "--censimento", c, "--timeout", "0.6"])
        assert codice == 3, f"uscita {codice} (atteso 3: il guasto precede la misura)\n{rap}"
        per = {x["nome"]: x["esito"] for x in righe_prova(rap)}
        assert per.get("lenta_selftest.py") == "NON TERMINATA", rap
        assert per.get("zeta_selftest.py") == "ROSSA", \
            "il rapporto non elenca la rossa accanto alla non eseguita\n" + rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("G", "ESCLUSE non vuoto: non eseguita, ragione nel rapporto, esito 0")
def caso_g():
    d = albero("g")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        scrivi_prova(d, "esclusa_selftest.py", 1)
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde"},
                              escluse={"esclusa_selftest.py": "richiede-una-piattaforma"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 0, f"uscita {codice}\n{rap}"
        r = [x for x in righe_prova(rap) if x["nome"] == "esclusa_selftest.py"]
        assert r and r[0]["esito"] == "ESCLUSA", rap
        assert "richiede-una-piattaforma" in rap, "la ragione dell'esclusione non c'e'\n" + rap
        assert "esclusa_selftest.py" not in eseguite(d), \
            f"l'esclusa e' stata eseguita: {letture(d)}"
        assert eseguite(d) == ["alfa_selftest.py"], letture(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("H", "una voce senza ragione, nei tre insiemi e di soli spazi: esito 2")
def caso_h():
    for dove, vuoto in (("PROVE", ""), ("STRUMENTI", ""), ("ESCLUSE", ""), ("PROVE", "   ")):
        d = albero("h")
        try:
            scrivi_prova(d, "alfa_selftest.py", 0)
            scrivi_strumento(d, "attrezzo.py")
            ins = {"PROVE": {"alfa_selftest.py": "verde"},
                   "STRUMENTI": {"attrezzo.py": "non e' una prova"}, "ESCLUSE": {}}
            if dove == "ESCLUSE":
                ins["PROVE"].pop("alfa_selftest.py")
                ins["ESCLUSE"]["alfa_selftest.py"] = vuoto
            else:
                chiave = "alfa_selftest.py" if dove == "PROVE" else "attrezzo.py"
                ins[dove][chiave] = vuoto
            c = scrivi_censimento(d, ins["PROVE"], ins["STRUMENTI"], ins["ESCLUSE"])
            codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
            assert codice == 2, f"{dove}/{vuoto!r}: uscita {codice}\n{rap}"
        finally:
            shutil.rmtree(d, ignore_errors=True)


@prova("I", "ordine byte-wise e deterministico, con durate opposte all'ordine alfabetico")
def caso_i():
    d = albero("i")
    try:
        # le tre frontiere del §5: '.'(0x2E) < cifra(0x30) < '_'(0x5F) < minuscola(0x61)
        nomi = ["a.py", "a0_selftest.py", "a_selftest.py", "aa_selftest.py"]
        atteso = sorted(nomi)
        durate = {"a.py": 0.30, "a0_selftest.py": 0.20, "a_selftest.py": 0.10,
                  "aa_selftest.py": 0.0}
        for n in nomi:
            scrivi_prova(d, n, 0, corpo="time.sleep(%r)" % durate[n])
        c = scrivi_censimento(d, prove={n: "prova finta" for n in nomi})
        primo = None
        for _ in range(2):
            codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
            assert codice == 0, f"uscita {codice}\n{rap}"
            visto = nomi_riportati(rap)
            assert visto == atteso, f"ordine {visto}, atteso {atteso}\n{rap}"
            if primo is None:
                primo = visto
            assert visto == primo, f"non deterministico: {primo} poi {visto}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("J", "ricorsione sulla stessa radice, in due grafie: esito 3, nulla eseguito")
def caso_j():
    grafie = [lambda p: p + os.sep]
    if os.name == "nt":
        # realpath unifica le maiuscole solo su Windows: su POSIX '.../TOOLS' e' un altro
        # percorso, la guardia farebbe bene ad ammetterlo, e questo caso pretenderebbe un
        # difetto che non c'e'.
        grafie.append(lambda p: p.upper())
    for i, grafia in enumerate(grafie):
        d = albero("j")
        try:
            scrivi_prova(d, "alfa_selftest.py", 0)
            c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde"})
            r = radice(d)
            with stub_di_lancio():
                codice, rap = esegui(["--radice", grafia(r), "--censimento", c, "--timeout", "20"],
                                     ambiente=os.path.realpath(r))
            assert codice == 3, f"grafia {i}: uscita {codice} (atteso 3)\n{rap}"
            assert eseguite(d) == [], f"grafia {i}: traccia non vuota {letture(d)}"
        finally:
            shutil.rmtree(d, ignore_errors=True)


@prova("K", "radice diversa da quella in esame: ammesso, esito normale")
def caso_k():
    d = albero("k")
    altra = albero("k-altra")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"],
                             ambiente=os.path.realpath(radice(altra)))
        assert codice == 0, f"uscita {codice} (una radice diversa va ammessa)\n{rap}"
        assert eseguite(d) == ["alfa_selftest.py"], letture(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(altra, ignore_errors=True)


@prova("L", "censimento() sul tools/ reale: coerente, 16/19/0, senza eseguire nulla")
def caso_l():
    with stub_di_lancio():
        c = ga.censimento(QUI)
    assert not c["problemi"], "censimento incoerente sul tools/ reale: " + "; ".join(c["problemi"])
    i = c["insiemi"]
    assert len(i["PROVE"]) == 16, f"PROVE {len(i['PROVE'])}, atteso 16: {sorted(i['PROVE'])}"
    assert len(i["STRUMENTI"]) == 19, f"STRUMENTI {len(i['STRUMENTI'])}, atteso 19"
    assert i["ESCLUSE"] == {}, i["ESCLUSE"]
    assert len(i["PROVE"]) + len(i["STRUMENTI"]) + len(i["ESCLUSE"]) == 35


@prova("M", "gate_all_selftest.py e spec_check.py sono fra le PROVE")
def caso_m():
    with stub_di_lancio():
        c = ga.censimento(QUI)
    for n in ("gate_all_selftest.py", "spec_check.py", "ds_validate_concurrency.py"):
        assert n in c["insiemi"]["PROVE"], f"{n} non e' fra le PROVE"
        assert c["insiemi"]["PROVE"][n].strip(), f"{n} e' fra le PROVE senza ragione"


@prova("N", "ciclo di lunghezza 2: la guardia rifiuta al terzo livello, 1 al primo, e "
            "l'insieme si accumula")
def caso_n():
    r_ = albero("n-r")
    t_ = albero("n-t")
    try:
        cr = scrivi_censimento(r_, prove={"ponte_selftest.py": "invoca gate_all su T"})
        ct = scrivi_censimento(t_, prove={"ponte_selftest.py": "invoca gate_all su R"})
        # Il ponte porta un FONDO DI SICUREZZA proprio, e non e' la proprieta' in esame: a
        # fermare il ciclo dev'essere la GUARDIA, al terzo livello. Il fondo scatta al quarto e
        # serve solo perche' una mutazione che rompe la guardia non trasformi questa fixture in
        # una fork bomb — misurato: senza, la mutazione 12 lascia vive centinaia di processi, e
        # un albero che genera mentre lo si uccide non si lascia uccidere.
        ponte = ("import subprocess\n"
                 "_p = int(os.environ.get('PONTE_PROFONDITA', '0'))\n"
                 "if _p >= 3:\n"
                 "    _segna('fondo'); sys.exit(0)\n"
                 "_a = dict(os.environ); _a['PONTE_PROFONDITA'] = str(_p + 1)\n"
                 "sys.exit(subprocess.run([sys.executable, %r, '--radice', %r,\n"
                 "    '--censimento', %r, '--timeout', '20'], env=_a).returncode)\n")
        scrivi_prova(r_, "ponte_selftest.py", 0, corpo=ponte % (GATE_ALL, radice(t_), ct))
        scrivi_prova(t_, "ponte_selftest.py", 0, corpo=ponte % (GATE_ALL, radice(r_), cr))
        codice, rap = esegui(["--radice", radice(r_), "--censimento", cr, "--timeout", "20"])
        # Il fondo si guarda PER PRIMO. Se il ciclo e' stato fermato dalla fixture invece che
        # dalla guardia, ogni altra osservazione racconta una storia falsa: il fondo esce `0`, e
        # quello `0` risale la catena facendo sembrare RIUSCITO cio' che era una ricorsione
        # illimitata. Guardarlo dopo il codice d'uscita significa diagnosticare «uscita 0»
        # quando la verita' e' «la guardia non ha tenuto».
        assert not [x for x in letture(r_) + letture(t_) if x[0] == "fondo"], \
            (f"il ciclo e' stato fermato dal fondo di sicurezza della fixture, non dalla "
             f"guardia: uscita {codice}")
        # L'exit previsto, esplicito e non «diverso da zero». Il `3` vive al TERZO livello, dove
        # la guardia rifiuta; risalendo diventa `1` a ogni livello, perche' per il padre il
        # ponte e' semplicemente una prova rossa. Osservare solo il `1` non direbbe che la
        # guardia ha parlato: la prova che ha parlato e' il suo messaggio, che affiora nel
        # rapporto annidato perche' l'uscita delle non-verdi viene riportata.
        assert codice == 1, \
            f"uscita {codice}, atteso 1: al primo livello il ponte e' una prova rossa\n{rap}"
        assert "rifiuto di rientrare" in rap, \
            "nel rapporto annidato non compare il rifiuto della guardia: il 3 del terzo " \
            "livello non e' osservato\n" + rap
        ric = [x[2] for x in letture(t_) if x[0] == "inizio"]
        assert ric, "il secondo livello non ha lasciato traccia: il ciclo non e' arrivato la'"
        parti = {os.path.realpath(p) for p in ric[0].split(os.pathsep) if p}
        assert os.path.realpath(radice(r_)) in parti and os.path.realpath(radice(t_)) in parti, \
            (f"il secondo livello ha ricevuto {ric[0]!r}: la guardia sovrascrive invece di "
             f"accumulare")
    finally:
        shutil.rmtree(r_, ignore_errors=True)
        shutil.rmtree(t_, ignore_errors=True)


@prova("O", "un file a forma di prova classificato STRUMENTO senza ragione: esito 2")
def caso_o():
    d = albero("o")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        scrivi_strumento(d, "sembra_una_prova.py", corpo="import sys\nsys.exit(0)")
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde"},
                              strumenti={"sembra_una_prova.py": ""})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 2, f"uscita {codice}\n{rap}"
        assert "sembra_una_prova.py" in rap, rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P", "la neutralizzazione d'ambiente: assente dopo, identica dopo il ripristino")
def caso_p():
    # (1) l'INGRESSO della batteria ha neutralizzato. `main()` mette il testimone prima di
    #     entrare, quindi questa asserzione e' falsa se l'ingresso non neutralizza — sia in TDD
    #     sia dentro la passata, dove la variabile arriva ereditata da gate_all.
    assert ga.CHIAVE_RADICI not in os.environ, \
        f"l'ingresso non ha neutralizzato: {os.environ.get(ga.CHIAVE_RADICI)!r}"
    # (2) la PRIMITIVA e' fedele. E' la stessa che l'ingresso usa: se ne esistessero due, questo
    #     collaudo collauderebbe codice nato dentro il collaudo.
    noto = "//alfa" + os.pathsep + "//beta"
    os.environ[ga.CHIAVE_RADICI] = noto
    try:
        with ambiente_neutro():
            assert ga.CHIAVE_RADICI not in os.environ, "la primitiva non toglie"
        assert os.environ.get(ga.CHIAVE_RADICI) == noto, \
            f"ripristino infedele: {os.environ.get(ga.CHIAVE_RADICI)!r} invece di {noto!r}"
    finally:
        os.environ.pop(ga.CHIAVE_RADICI, None)


@prova("Q", "gate_all.py e' nel censimento, fra gli STRUMENTI, con ragione non vuota")
def caso_q():
    with stub_di_lancio():
        c = ga.censimento(QUI)
    i = c["insiemi"]
    assert "gate_all.py" in i["STRUMENTI"], \
        "gate_all.py non e' censito: sarebbe sfuggito al proprio censimento"
    assert i["STRUMENTI"]["gate_all.py"].strip(), "gate_all.py e' censito senza ragione"
    assert "gate_all.py" not in i["PROVE"], \
        "gate_all.py fra le PROVE: la passata rientrerebbe in se stessa"


@prova("R", "lo stesso nome in PROVE e in STRUMENTI, con ragione in entrambi: esito 2")
def caso_r():
    d = albero("r")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "una ragione"},
                              strumenti={"alfa_selftest.py": "un'altra ragione"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 2, f"uscita {codice} (nome in due insiemi)\n{rap}"
        assert "alfa_selftest.py" in rap, rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("S", "nome, esito e durata su ogni riga; stderr solo per le non verdi")
def caso_s():
    d = albero("s")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0,
                     corpo='sys.stderr.write("chiacchiera-della-verde\\n")')
        scrivi_prova(d, "zeta_selftest.py", 1,
                     corpo='sys.stderr.write("guasto-della-rossa\\n")')
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde loquace",
                                        "zeta_selftest.py": "rossa loquace"})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 1, f"uscita {codice}\n{rap}"
        righe = righe_prova(rap)
        assert len(righe) == 2, rap
        for r in righe:
            assert r["nome"].endswith(".py"), r
            assert re.fullmatch(r"\d+\.\d\ds", r["durata"]), \
                f"la durata manca o non ha forma: {r['durata']!r}\n{rap}"
        assert "guasto-della-rossa" in rap, "lo stderr della rossa manca\n" + rap
        assert "chiacchiera-della-verde" not in rap, \
            "lo stderr di una VERDE e' riportato\n" + rap
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("T", "in serie: la traccia non si interlaccia mai")
def caso_t():
    d = albero("t")
    FINESTRA = 0.35
    try:
        nomi = ["uno_selftest.py", "due_selftest.py", "tre_selftest.py"]
        for n in nomi:
            scrivi_prova(d, n, 0, corpo="time.sleep(%r)" % FINESTRA)
        c = scrivi_censimento(d, prove={n: "prova finta con finestra" for n in nomi})
        codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        assert codice == 0, f"uscita {codice}\n{rap}"
        seq = [(e, n) for e, n, _ in letture(d)]
        assert len(seq) == 2 * len(nomi), f"traccia incompleta: {seq}"
        for i in range(0, len(seq), 2):
            a, b = seq[i], seq[i + 1]
            assert a[0] == "inizio" and b[0] == "fine" and a[1] == b[1], \
                f"traccia interlacciata: {seq} — le prove non sono in serie"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("U", "un gruppo del censimento non e' una mappa: esito 3, e nessun traceback")
def caso_u():
    d = albero("u")
    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        p = os.path.join(d, "malformato.json")
        with open(p, "w", encoding="utf-8", newline="\n") as g:
            # `PROVE` e' una lista, non una mappa: JSON valido, censimento no.
            json.dump({"PROVE": ["alfa_selftest.py"], "STRUMENTI": {}, "ESCLUSE": {}}, g)
        codice, rap = esegui(["--radice", radice(d), "--censimento", p, "--timeout", "20"])
        assert codice == 3, \
            f"uscita {codice}: un censimento malformato e' un guasto d'invocazione, non una " \
            f"misura\n{rap}"
        assert "Traceback" not in rap, "il guasto e' uscito come traceback\n" + rap
        assert eseguite(d) == [], f"traccia non vuota: {letture(d)}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V", "il lancio stesso fallisce: GUASTA nel rapporto, uscita 3")
def caso_v():
    # Il ramo `GUASTA` non e' raggiungibile con una fixture: `Popen` solleva `OSError` solo se
    # l'INTERPRETE non e' lanciabile, e l'interprete e' quello che sta girando. Lo si esercita
    # per iniezione, che e' l'unico modo onesto di provare un ramo d'errore altrimenti muto.
    d = albero("v")
    vero = ga.subprocess.Popen

    def rotto(*a, **k):
        raise OSError(8, "Exec format error")

    try:
        scrivi_prova(d, "alfa_selftest.py", 0)
        c = scrivi_censimento(d, prove={"alfa_selftest.py": "verde"})
        ga.subprocess.Popen = rotto
        try:
            codice, rap = esegui(["--radice", radice(d), "--censimento", c, "--timeout", "20"])
        finally:
            ga.subprocess.Popen = vero
        assert codice == 3, \
            f"uscita {codice}: non aver potuto lanciare e' un GUASTO, non una misura\n{rap}"
        r = [x for x in righe_prova(rap) if x["nome"] == "alfa_selftest.py"]
        assert r and r[0]["esito"] == "GUASTA", rap
        assert "Exec format error" in rap, "la causa del guasto non e' riportata\n" + rap
        assert eseguite(d) == [], f"traccia non vuota: {letture(d)}"
    finally:
        ga.subprocess.Popen = vero
        shutil.rmtree(d, ignore_errors=True)


@prova("W", "i due nomi del replay offline sono classificati, e toglierne uno rende "
            "INCOERENTE il censimento")
def caso_w():
    """La registrazione dev'essere SORVEGLIATA, non solo scritta.

    Il caso `L` conta e verifica la coerenza, ma un conteggio giusto non dice QUALI nomi ci sono:
    due aggiunte sbagliate che si compensano lo lascerebbero verde. Qui si toglie un nome per
    volta e si pretende che il censimento se ne accorga, nominandolo.
    """
    with stub_di_lancio():
        base = ga.censimento(QUI)
    assert not base["problemi"], "; ".join(base["problemi"])
    for nome, gruppo in (("replay_offline.py", "STRUMENTI"),
                         ("replay_offline_selftest.py", "PROVE")):
        assert nome in base["insiemi"][gruppo], f"{nome} non e' fra le {gruppo}"
        assert base["insiemi"][gruppo][nome].strip(), f"{nome} e' fra le {gruppo} senza ragione"
        elenco = {g: dict(getattr(ga, g)) for g in ("PROVE", "STRUMENTI", "ESCLUSE")}
        del elenco[gruppo][nome]
        with stub_di_lancio():
            c = ga.censimento(QUI, elenco)
        assert c["problemi"], f"tolto {nome} da {gruppo}, il censimento resta coerente"
        assert len(c["problemi"]) == 1, f"attesa una sola incoerenza: {c['problemi']}"
        assert nome in c["problemi"][0], \
            f"l'incoerenza non nomina {nome}: {c['problemi'][0]}"


# ================================================================== esecuzione

def main():
    print(f"gate_all_selftest — {len(ESITI)} prove\n")
    ereditato = os.environ.get(ga.CHIAVE_RADICI)
    falliti = []
    try:
        # Il testimone rende deterministica la situazione che la neutralizzazione deve gestire:
        # in TDD la variabile e' assente, dentro la passata arriva ereditata da gate_all. Senza,
        # il caso `P` sarebbe vero per costruzione in un contesto e falso nell'altro — che e'
        # esattamente il difetto per cui la terza redazione e' stata respinta.
        os.environ[ga.CHIAVE_RADICI] = TESTIMONE
        with ambiente_neutro():
            for nome, desc, f in ESITI:
                try:
                    f()
                    print(f"  [verde] {nome:<3} {desc}")
                except Exception as e:                                    # noqa: BLE001
                    falliti.append((nome, e))
                    print(f"  [ROSSO] {nome:<3} {desc}\n          {type(e).__name__}: {e}")
    finally:
        if ereditato is None:
            os.environ.pop(ga.CHIAVE_RADICI, None)
        else:
            os.environ[ga.CHIAVE_RADICI] = ereditato
    print()
    if falliti:
        print(f"  {len(ESITI) - len(falliti)}/{len(ESITI)} verdi — ROSSE: "
              + ", ".join(n for n, _ in falliti))
        return 1
    print(f"  {len(ESITI)}/{len(ESITI)} verdi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
