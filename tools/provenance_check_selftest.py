#!/usr/bin/env python3
"""
provenance_check_selftest.py — dimostra che provenance_check.py NON e' cieco.

Un preflight di provenienza che gira non dice niente: la differenza fra un componente letto
dalla radice giusta e lo stesso file letto da una copia fuori radice non e' ispezionabile
guardando il manifesto, perche' i due manifesti hanno lo stesso aspetto. Si dimostra solo
costruendo repository ISOLATI in directory temporanee e pretendendo l'arresto nominato: nessun
repository reale viene mai toccato, nessuna directory di run viene mai creata.

VENTIDUE PROVE (P0 cammino felice + P1..P21). Le falsificazioni seguono il registro del
progetto: ogni violazione ha un errore NOMINATO e un exit code preciso (1 = violazione di
provenienza), e la prova asserisce entrambi. Le mutazioni che devono far cadere il rivelatore
girano su COPIE del tool in temp, mai sul file del repository.

  uso:  python tools/provenance_check_selftest.py
        exit 0 = tutte e ventidue verdi
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '·' e '«' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMI = os.path.join(ROOT, "schemas")
QUI = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(QUI, "provenance_check.py")

ESITI = []


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


# ------------------------------------------------------------------ utilita'

def git(dir_repo, *args):
    r = subprocess.run(["git", "-C", dir_repo] + list(args),
                       capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def repo_fixture(crlf_schema=False, radici_vietate=None, mapping=None, senza_config=False,
                 nome_root="repo", marcatore=None, fine_prompt="singola"):
    """Un repository usa-e-getta col layout minimo: schemi VERI (evidence-pack + core, cosi' la
    ricompilazione delle viste e' possibile), prompt d'agente sintetico, copie del compilatore e
    del tool. L'identita' git e' passata per flag: nessun `git config` globale. `marcatore`
    rende il commit diverso da quello di un fixture gemello (i commit nello stesso secondo
    altrimenti collidono)."""
    d = tempfile.mkdtemp(prefix="pc-selftest-")
    root = os.path.join(d, nome_root)
    os.makedirs(os.path.join(root, "schemas"))
    os.makedirs(os.path.join(root, ".claude", "agents"))
    os.makedirs(os.path.join(root, ".claude", "commands"))
    os.makedirs(os.path.join(root, "tools"))
    os.makedirs(os.path.join(root, "config"))
    for nome in ("evidence-pack.schema.json", "core.schema.json"):
        b = open(os.path.join(SCHEMI, nome), "rb").read()
        if crlf_schema and nome == "core.schema.json":
            b = b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        open(os.path.join(root, "schemas", nome), "wb").write(b)
    finale = {"singola": b"\n", "zero": b"", "doppia": b"\n\n"}[fine_prompt]
    open(os.path.join(root, ".claude", "agents", "evidence-researcher.md"), "wb").write(
        b"prompt base di prova per evidence-researcher" + finale)
    open(os.path.join(root, ".claude", "commands", "deepsearch.md"), "wb").write(
        b"orchestratore di prova\n")
    if marcatore is not None:
        open(os.path.join(root, "marcatore.txt"), "w", encoding="utf-8").write(marcatore + "\n")
    for nome in ("contract_compile.py", "provenance_check.py"):
        shutil.copy(os.path.join(QUI, nome), os.path.join(root, "tools", nome))
    if not senza_config:
        cfg = {"verifica_specifica": {
            "radici_vietate": radici_vietate if radici_vietate is not None else [],
            "schema_per_agente": mapping if mapping is not None else [
                {"schema": "evidence-pack.schema.json", "agente": "evidence-researcher"}]}}
        with open(os.path.join(root, "config", "deepsearch.config.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    c, _, e = git(root, "init")
    assert c == 0, f"git init fallito: {e}"
    # il fixture dev'essere ERMETICO: niente autocrlf ereditato dal sistema, i byte committati
    # sono i byte scritti, altrimenti le prove CRLF non significano nulla
    for k, v in (("core.autocrlf", "false"), ("core.safecrlf", "false")):
        c, _, e = git(root, "config", k, v)
        assert c == 0, f"git config {k} fallito: {e}"
    c, _, e = git(root, "add", "-A")
    assert c == 0, f"git add fallito: {e}"
    c, _, e = git(root, "-c", "user.name=selftest", "-c", "user.email=selftest@locale",
                  "commit", "-m", "fixture")
    assert c == 0, f"git commit fallito: {e}"
    c, out, _ = git(root, "rev-parse", "HEAD")
    assert c == 0
    return root, out.strip()


def viste(root, commit, dir_out):
    """Genera le due viste col compilatore del fixture (stessi input che il tool ricompila)."""
    out = {}
    for vista in ("audit", "delivery"):
        p = os.path.join(dir_out, f"{vista}.txt")
        r = subprocess.run(
            [sys.executable, os.path.join(root, "tools", "contract_compile.py"),
             "--agente", "evidence-researcher", "--schemi", os.path.join(root, "schemas"),
             "--commit", commit, "--vista", vista, "--out", p],
            capture_output=True, timeout=120)
        assert r.returncode == 0, f"compilazione {vista} fallita: {r.stderr.decode('utf-8', 'replace')}"
        out[vista] = p
    return out


def esegui(root, *args):
    """Il tool sotto test, come processo separato. Ritorna (codice, stdout, stderr)."""
    r = subprocess.run([sys.executable, os.path.join(root, "tools", "provenance_check.py")]
                       + list(args), capture_output=True, timeout=180)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def radice_da(root):
    return os.path.join(root, ".claude", "agents", "evidence-researcher.md")


def componenti_std(root):
    return ["schemas/evidence-pack.schema.json", "schemas/core.schema.json"]


def norm_bytes(b):
    """La normalizzazione del prompt base: CRLF->LF ovunque, esattamente UNA newline finale."""
    b = b.replace(b"\r\n", b"\n")
    while b.endswith(b"\n\n"):
        b = b[:-1]
    if not b.endswith(b"\n"):
        b += b"\n"
    return b


def consegnato(root, commit, dir_out, base=None, giunzione=b"\n\n---\n\n",
               ordine="base-giunzione-delivery"):
    """Costruisce un prompt consegnato (corretto o mutato) dai pezzi del fixture."""
    v = viste(root, commit, dir_out)
    base_b = base if base is not None else open(os.path.join(
        root, ".claude", "agents", "evidence-researcher.md"), "rb").read()
    delivery = open(v["delivery"], "rb").read()
    if ordine == "base-giunzione-delivery":
        return norm_bytes(base_b) + giunzione + delivery
    return delivery + giunzione + norm_bytes(base_b)


# ------------------------------------------------------------------ P0 … P20

@prova("P0", "cammino felice: manifesto completo, atomico, LF senza BOM, due viste")
def p0():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        pc = os.path.join(out, "consegnato.txt")
        open(pc, "wb").write(consegnato(root, commit, out))
        manifesto = os.path.join(out, "manifest.json")
        codice, stdout, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                     "--agente", "evidence-researcher", "--componente",
                                     ".claude/commands/deepsearch.md", "--audit", v["audit"],
                                     "--delivery", v["delivery"],
                                     "--prompt-consegnato", pc, "--manifest", manifesto)
        assert codice == 0, f"uscita {codice}: {err}"
        b = open(manifesto, "rb").read()
        assert not b.startswith(b"\xef\xbb\xbf"), "il manifesto ha un BOM"
        assert b"\r" not in b, "il manifesto non e' LF"
        assert b.endswith(b"\n"), "il manifesto non ha l'unica newline finale"
        m = json.loads(b.decode("utf-8"))
        assert m["commit"] == commit and m["radice_canonica"] == os.path.realpath(root)
        assert re.fullmatch(r"[0-9a-f]{64}", m["compiler_sha256"])
        assert m["compiler_version"] and m["mapping_agente_schema"] == \
            {"evidence-researcher": "evidence-pack.schema.json"}
        percorsi = [c["percorso"] for c in m["componenti"]]
        assert "schemas/core.schema.json" in percorsi and \
            "config/deepsearch.config.json" in percorsi and \
            "tools/contract_compile.py" in percorsi, f"componenti incompleti: {percorsi}"
        for c in m["componenti"]:
            assert re.fullmatch(r"[0-9a-f]{64}", c["sha256_grezzo"])
            assert re.fullmatch(r"[0-9a-f]{64}", c["sha256_normalizzato"])
            assert c["identico_al_blob"] is True and c["dimensione"] > 0
            assert "\\" not in c["percorso"], "percorso non canonico (separatori di piattaforma)"
        assert isinstance(m["contratti"], list) and len(m["contratti"]) == 2, \
            "i contratti non sono una collezione di due viste"
        viste_let = {c["vista"] for c in m["contratti"]}
        assert viste_let == {"audit", "delivery"}, viste_let
        for c in m["contratti"]:
            assert re.fullmatch(r"[0-9a-f]{64}", c["sha256"]) and c["dimensione"] > 0
        residui = [f for f in os.listdir(out) if f != "manifest.json" and "tmp" in f]
        assert not residui, f"residui temporanei accanto al manifesto: {residui}"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P1", "componente fuori radice: arresto nominato, mai silenzio")
def p1():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        esterno = os.path.join(out, "esterno.json")
        open(esterno, "w", encoding="utf-8").write("{}")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente", esterno,
                                "--audit", v["audit"], "--delivery", v["delivery"])
        assert codice == 1, f"uscita {codice}"
        assert "fuori radice" in err, err
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P2", "prefisso simile ma non contenuto: commonpath rifiuta, startswith accetterebbe")
def p2():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        gemello = root + "-gemello"
        os.makedirs(gemello)
        finto = os.path.join(gemello, "x.json")
        open(finto, "w", encoding="utf-8").write("{}")
        assert finto.startswith(root), "il fixture del prefisso simile non fa il suo lavoro"
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente", finto,
                                "--audit", v["audit"], "--delivery", v["delivery"])
        assert codice == 1 and "fuori radice" in err, (codice, err)
        assert os.path.commonpath([os.path.realpath(root), os.path.realpath(finto)]) \
            != os.path.realpath(root), "commonpath non distingue il gemello: prova rotta"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P3", "lo stesso file da un altro repository: HEAD diverso, arresto")
def p3():
    root, commit = repo_fixture(marcatore="primo")
    radice_b, commit_b = repo_fixture(marcatore="secondo")
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        # stessi contenuti, commit dichiarato del PRIMO repo, radice nel SECONDO
        codice, _, err = esegui(radice_b, "--radice-da", radice_da(radice_b), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/core.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "HEAD diverso" in err, (codice, err)
        assert commit != commit_b, "i due fixture hanno lo stesso commit: prova rotta"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(radice_b, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P4", "HEAD differente dal commit dichiarato: arresto")
def p4():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        open(os.path.join(root, "secondo.txt"), "w", encoding="utf-8").write("secondo\n")
        git(root, "add", "-A")
        git(root, "-c", "user.name=selftest", "-c", "user.email=selftest@locale",
            "commit", "-m", "secondo")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/core.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "HEAD diverso" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P5", "blob diverso dal file su disco: arresto, con l'errore nominato")
def p5():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        p = os.path.join(root, "schemas", "core.schema.json")
        b = open(p, "rb").read()
        open(p, "wb").write(b + b"\n// manomesso\n")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/core.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "blob diverso" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P6", "componente di contratto non tracciato: assente dal commit dichiarato")
def p6():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        nuovo = os.path.join(root, "schemas", "nuovo.schema.json")
        open(nuovo, "w", encoding="utf-8").write("{}")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/nuovo.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "assente dal commit" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P7", "radice vietata dichiarata nel config: arresto prima di qualunque lettura")
def p7():
    nome = "radice-vietata-di-prova"
    root, commit = repo_fixture(radici_vietate=[nome], nome_root=nome)
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "radice vietata" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P8", "hash abbreviato: rifiutato prima di parlare con git")
def p8():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit[:8],
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "commit dichiarato non integro" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P9", "CRLF contro LF: digest grezzi diversi, normalizzati coerenti, ruoli distinti")
def p9():
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        # (a) file COMMITTATO in CRLF: passa, e il manifesto registra grezzo != normalizzato
        root, commit = repo_fixture(crlf_schema=True)
        v = viste(root, commit, out)
        manifesto = os.path.join(out, "manifest.json")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"], "--manifest", manifesto)
        assert codice == 0, f"uscita {codice}: {err}"
        m = json.load(open(manifesto, encoding="utf-8"))
        crlf = [c for c in m["componenti"] if c["percorso"] == "schemas/core.schema.json"][0]
        lf = [c for c in m["componenti"] if c["percorso"] == "schemas/evidence-pack.schema.json"][0]
        assert crlf["sha256_grezzo"] != crlf["sha256_normalizzato"], \
            "un file CRLF registra digest uguali: i due ruoli sono confusi"
        assert crlf["identico_al_blob"] is True
        assert lf["sha256_grezzo"] == lf["sha256_normalizzato"]
        shutil.rmtree(root, ignore_errors=True)
        # (b) file su disco in CRLF contro blob LF: identico_al_blob FALSO, arresto
        root2, commit2 = repo_fixture()
        v2 = viste(root2, commit2, out)
        p = os.path.join(root2, "schemas", "core.schema.json")
        b = open(p, "rb").read()
        open(p, "wb").write(b.replace(b"\n", b"\r\n"))
        codice, _, err = esegui(root2, "--radice-da", radice_da(root2), "--commit", commit2,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/core.schema.json", "--audit", v2["audit"],
                                "--delivery", v2["delivery"])
        assert codice == 1 and "blob diverso" in err, (codice, err)
        shutil.rmtree(root2, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)


@prova("P10", "confusione fra digest grezzo e normalizzato: il rivelatore mutato lascia passare")
def p10():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        p = os.path.join(root, "schemas", "core.schema.json")
        b = open(p, "rb").read()
        open(p, "wb").write(b.replace(b"\n", b"\r\n"))
        # il tool VERO deve arrestarsi (identita' sui byte grezzi)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/core.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "blob diverso" in err, (codice, err)
        # una COPIA mutata che confronta il digest NORMALIZZATO lascerebbe passare: la
        # differenza e' esattamente la disciplina grezzo/normalizzato che questa prova inchioda
        mut = os.path.join(out, "mut")
        os.makedirs(mut)
        src = open(TOOL, encoding="utf-8").read()
        assert "return grezzi == blob" in src, "la linea di identita' non ha la forma attesa"
        aperto = open(os.path.join(mut, "provenance_check.py"), "w",
                      encoding="utf-8", newline="\n")
        aperto.write(src.replace("return grezzi == blob", "return normalizzati == blob"))
        aperto.close()
        shutil.copy(os.path.join(QUI, "contract_compile.py"),
                    os.path.join(mut, "contract_compile.py"))
        r = subprocess.run([sys.executable, os.path.join(mut, "provenance_check.py"),
                            "--radice-da", radice_da(root), "--commit", commit,
                            "--agente", "evidence-researcher", "--componente",
                            "schemas/core.schema.json", "--audit", v["audit"],
                            "--delivery", v["delivery"]], capture_output=True, timeout=180)
        assert r.returncode == 0, (
            f"la copia confusa non passa ({r.returncode}): {r.stderr.decode('utf-8', 'replace')}"
            " — la disciplina grezzo/normalizzato non e' la linea che ferma il caso CRLF")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P11", "sorgente del compilatore modificato a versione ferma: arresto")
def p11():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        p = os.path.join(root, "tools", "contract_compile.py")
        b = open(p, "rb").read()
        open(p, "wb").write(b + b"\n# manomesso\n")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "blob diverso" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P12", "mapping agente→schema mancante o differente: arresto nominato")
def p12():
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        root, commit = repo_fixture(mapping=[{"schema": "evidence-pack.schema.json",
                                              "agente": "altro"}])
        v = viste(root, commit, out)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "mapping agente" in err and "mancante" in err, (codice, err)
        shutil.rmtree(root, ignore_errors=True)
        root2, commit2 = repo_fixture(mapping=[{"schema": "agente.schema.json",
                                                "agente": "evidence-researcher"}])
        codice, _, err = esegui(root2, "--radice-da", radice_da(root2), "--commit", commit2,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "mapping agente" in err and "differente" in err, (codice, err)
        shutil.rmtree(root2, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)


@prova("P13", "manifesto con una sola vista: le viste sono due, sempre")
def p13():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"])
        assert codice == 1 and "viste incomplete" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P14", "modifica di una sola vista: sempre intercettata")
def p14():
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        def tenta(nome, manomissione, atteso):
            root, commit = repo_fixture()
            v = viste(root, commit, out)
            p = os.path.join(out, nome + ".txt")
            originale = open(v[nome], "rb").read()      # PRIMA di troncare: p == v[nome]
            open(p, "wb").write(manomissione(originale))
            codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                    "--agente", "evidence-researcher",
                                    "--audit" if nome == "audit" else "--delivery", p,
                                    "--delivery" if nome == "audit" else "--audit",
                                    v["delivery"] if nome == "audit" else v["audit"])
            shutil.rmtree(root, ignore_errors=True)
            assert codice == 1, f"{nome}: uscita {codice}"
            assert atteso in err, f"{nome}: atteso «{atteso}», ottenuto «{err.strip()}»"

        tenta("audit", lambda b: b + b"\n// manomesso\n", "vista non riprodotta")
        tenta("delivery", lambda b: b + b"\n// corpo manomesso, riga hash intatta\n",
              "vista non riprodotta")
        tenta("delivery", lambda b: b"\n".join(b.split(b"\n")[:1] + b.split(b"\n")[2:]),
              "hash incollato assente")
        tenta("delivery", lambda b: b.replace(b"\n", b"\naudit sha256 " + b"0" * 64 + b"\n", 1),
              "hash incollato duplicato")
        tenta("delivery",
              lambda b: b"\n".join([b.split(b"\n")[1], b.split(b"\n")[0]] + b.split(b"\n")[2:]),
              "hash incollato fuori posizione")
        tenta("delivery",
              lambda b: b.replace(b.split(b"\n")[1],
                                  b"audit sha256 " + b"0" * 64, 1),
              "hash incollato non coincidente")
        tenta("audit", lambda b: b + b"\n", "vista non riprodotta")
    finally:
        shutil.rmtree(out, ignore_errors=True)


@prova("P15", "ordine base/giunzione/delivery invertito: rifiutato")
def p15():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        pc = os.path.join(out, "consegnato.txt")
        open(pc, "wb").write(consegnato(root, commit, out, ordine="invertito"))
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"], "--prompt-consegnato", pc)
        assert codice == 1 and "ordine del prompt consegnato" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P16", "giunzione alterata: rifiutata")
def p16():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        pc = os.path.join(out, "consegnato.txt")
        open(pc, "wb").write(consegnato(root, commit, out, giunzione=b"\n\n--\n\n"))
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"], "--prompt-consegnato", pc)
        assert codice == 1 and "giunzione non canonica" in err, (codice, err)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P17", "nessun output parziale dopo un errore: manifesto e residui assenti")
def p17():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        manifesto = os.path.join(out, "manifest.json")
        p = os.path.join(root, "schemas", "core.schema.json")
        b = open(p, "rb").read()
        open(p, "wb").write(b + b"\n// manomesso\n")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "schemas/core.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"], "--manifest", manifesto)
        assert codice == 1, f"uscita {codice}: {err}"
        assert not os.path.exists(manifesto), "un manifesto parziale e' rimasto sul disco"
        residui = [f for f in os.listdir(out)
                   if f not in ("audit.txt", "delivery.txt", "consegnato.txt")
                   and (f.startswith(".manifest-") or f.endswith(".tmp"))]
        assert not residui, f"residui temporanei dopo l'errore: {residui}"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P18", "fuga dalla radice: «..» rifiutato, symlink fuori radice rifiutato")
def p18():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--componente",
                                "../esterno.schema.json", "--audit", v["audit"],
                                "--delivery", v["delivery"])
        assert codice == 1 and "percorso non canonico" in err, (codice, err)
        esterno = os.path.join(out, "fuori.json")
        open(esterno, "w", encoding="utf-8").write("{}")
        ponte = os.path.join(root, "schemas", "ponte.schema.json")
        try:
            os.symlink(esterno, ponte)
        except (OSError, NotImplementedError):
            print("          (sotto-caso symlink saltato: la piattaforma non concede i symlink; "
                  "su POSIX la prova gira per intero)")
            return
        try:
            codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                    "--agente", "evidence-researcher", "--componente",
                                    "schemas/ponte.schema.json", "--audit", v["audit"],
                                    "--delivery", v["delivery"])
            assert codice == 1 and "fuori radice" in err, (codice, err)
        finally:
            os.unlink(ponte)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P19", "parita' Windows/POSIX: il manifesto non dipende dalla piattaforma")
def p19():
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        manifesto = os.path.join(out, "manifest.json")
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"], "--manifest", manifesto)
        assert codice == 0, f"uscita {codice}: {err}"
        m = json.load(open(manifesto, encoding="utf-8"))
        # l'unico dato dipendente dalla macchina e' la radice canonica, dichiarata come tale:
        # tutto il resto deve essere identico su entrambe le piattaforme
        for c in m["componenti"]:
            assert c["percorso"].startswith("schemas/") or c["percorso"].startswith(
                "tools/") or c["percorso"].startswith(".claude/") or c["percorso"].startswith(
                "config/"), f"percorso non canonico: {c['percorso']}"
        assert all("\\" not in c["percorso"] for c in m["componenti"])
        sha_audit = {c["vista"]: c["sha256"] for c in m["contratti"]}["audit"]
        consegna = open(v["delivery"], encoding="utf-8").read()
        assert consegna.splitlines()[1] == "audit sha256 " + sha_audit, \
            "la parita' non regge il legame hash fra le viste"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("P20", "zero rete: nessun import di rete e nessun tentativo di socket")
def p20():
    import socket
    tentativi = []
    vero = socket.socket.connect

    def spia(self, *a, **k):
        tentativi.append(a)
        raise AssertionError("provenance_check ha tentato una connessione")
    socket.socket.connect = spia
    root, commit = repo_fixture()
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        v = viste(root, commit, out)
        sys.path.insert(0, QUI)
        import importlib
        import provenance_check as pc
        importlib.reload(pc)
        codice = pc.main(["--radice-da", radice_da(root), "--commit", commit,
                          "--agente", "evidence-researcher", "--audit", v["audit"],
                          "--delivery", v["delivery"]])
        assert codice == 0, "il cammino in-process non passa"
        assert not tentativi, "tentativi di rete durante la verifica"
    finally:
        socket.socket.connect = vero
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
    src = open(TOOL, encoding="utf-8").read()
    for lib in ("socket", "http", "urllib", "requests", "ftplib", "smtplib"):
        assert not re.search(r"^\s*(import|from)\s+" + lib + r"\b", src, re.M), \
            f"provenance_check importa {lib}"


@prova("P21", "i bordi di norm() — zero o due newline finali — e l'errore «fuori repository»")
def p21():
    out = tempfile.mkdtemp(prefix="pc-out-")
    try:
        for fine in ("zero", "doppia"):
            root, commit = repo_fixture(fine_prompt=fine)
            v = viste(root, commit, out)
            pc = os.path.join(out, f"consegnato-{fine}.txt")
            open(pc, "wb").write(consegnato(root, commit, out))
            codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                    "--agente", "evidence-researcher", "--audit", v["audit"],
                                    "--delivery", v["delivery"], "--prompt-consegnato", pc)
            assert codice == 0, f"base con finale «{fine}»: uscita {codice}: {err}"
            shutil.rmtree(root, ignore_errors=True)
        # composizione fatta SENZA normalizzare: al base senza newline manca quella di norm()
        root, commit = repo_fixture(fine_prompt="zero")
        v = viste(root, commit, out)
        base = open(os.path.join(root, ".claude", "agents", "evidence-researcher.md"),
                    "rb").read()
        delivery = open(v["delivery"], "rb").read()
        pc = os.path.join(out, "sbagliato.txt")
        open(pc, "wb").write(base + b"\n\n---\n\n" + delivery)
        codice, _, err = esegui(root, "--radice-da", radice_da(root), "--commit", commit,
                                "--agente", "evidence-researcher", "--audit", v["audit"],
                                "--delivery", v["delivery"], "--prompt-consegnato", pc)
        assert codice == 1 and "giunzione non canonica" in err, (codice, err)
        shutil.rmtree(root, ignore_errors=True)
        # --radice-da fuori da qualunque repository: errore nominato, mai un traceback
        root, commit = repo_fixture()
        sciolto = os.path.join(out, "sciolto.md")
        open(sciolto, "wb").write(b"sciolto\n")
        codice, _, err = esegui(root, "--radice-da", sciolto, "--commit", commit,
                                "--agente", "evidence-researcher")
        assert codice == 1 and "fuori repository" in err, (codice, err)
        shutil.rmtree(root, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------------------------------ esecuzione

def main():
    print(f"provenance_check_selftest — {len(ESITI)} prove\n")
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
