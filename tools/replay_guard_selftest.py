#!/usr/bin/env python3
"""
replay_guard_selftest.py — dimostra che replay_guard.py NON e' cieco.

Due difetti dell'harness dei replay precedenti: gli agenti hanno scritto FUORI dal perimetro
(C:\\tmp, runtime\\output del worktree) e il monitoraggio per-canale sotto-contava i diff a file
singolo. Queste prove simulano le scritture con processi usa-e-getta in directory temporanee —
mai claude, mai rete, mai C:\\tmp reale, mai il worktree, mai i ledger — e pretendono l'esito
nominato.

  uso:  python tools/replay_guard_selftest.py
        exit 0 = tutte le otto prove verdi
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(QUI, "replay_guard.py")

ESITI = []


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


def fixture():
    """Perimetro + una zona 'esterna' (che simula C:\\tmp) in una directory temporanea."""
    d = tempfile.mkdtemp(prefix="rg-selftest-")
    perimetro = os.path.join(d, "replay")
    zona = os.path.join(d, "tmp-esterno")
    os.makedirs(perimetro)
    os.makedirs(zona)
    return d, perimetro, zona


def script_scrive(percorso, testo="x\n"):
    return ("import pathlib; p = pathlib.Path(%s); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            "p.write_text(%s, encoding='utf-8')"
            % (json.dumps(percorso), json.dumps(testo)))


def esegui(perimetro, zone, comando):
    r = subprocess.run([sys.executable, GUARD, "--perimetro", perimetro]
                       + [x for z in zone for x in ("--zona", z)]
                       + ["--", sys.executable, "-c", comando],
                       capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def rapporto(perimetro):
    return json.load(open(os.path.join(perimetro, "00-guard-report.json"), encoding="utf-8"))


@prova("G1", "scrittura DENTRO il perimetro: legittima, exit 0")
def g1():
    d, perimetro, zona = fixture()
    try:
        destinazione = os.path.join(perimetro, "02-output", "pack.json")
        codice, _, err = esegui(perimetro, [zona], script_scrive(destinazione))
        assert codice == 0, f"uscita {codice}: {err}"
        assert os.path.exists(destinazione)
        assert rapporto(perimetro)["violazioni"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("G2", "UN solo file nuovo fuori perimetro: rilevato (il caso che eluse il monitoraggio)")
def g2():
    d, perimetro, zona = fixture()
    try:
        esterno = os.path.join(zona, "evidence-pack-r22x.json")
        codice, _, err = esegui(perimetro, [zona], script_scrive(esterno))
        assert codice == 1, f"uscita {codice}: {err}"
        v = rapporto(perimetro)["violazioni"]
        assert len(v) == 1, f"attesa UNA violazione, trovate {len(v)}: {v}"
        assert os.path.realpath(v[0]["percorso"]) == os.path.realpath(esterno)
        assert v[0]["stato"] == "nuovo"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("G3", "modifica di un file esistente fuori perimetro: rilevata come modificato")
def g3():
    d, perimetro, zona = fixture()
    try:
        esterno = os.path.join(zona, "esistente.json")
        open(esterno, "w", encoding="utf-8").write("prima\n")
        codice, _, err = esegui(perimetro, [zona], script_scrive(esterno, "dopo\n"))
        assert codice == 1, f"uscita {codice}: {err}"
        v = rapporto(perimetro)["violazioni"]
        assert len(v) == 1 and v[0]["stato"] == "modificato", v
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("G4", "nessuna scrittura: exit 0, rapporto vuoto")
def g4():
    d, perimetro, zona = fixture()
    try:
        codice, _, err = esegui(perimetro, [zona], "print('niente')")
        assert codice == 0, f"uscita {codice}: {err}"
        assert rapporto(perimetro)["violazioni"] == []
        assert rapporto(perimetro)["exit_comando"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("G5", "il settings generato porta le regole deny con i glob delle zone")
def g5():
    import replay_guard as G
    perimetro = os.path.join(tempfile.gettempdir(), "rg-p")
    zona = os.path.join(tempfile.gettempdir(), "rg-z")
    t = G.regole_settings(perimetro, [zona])
    d = json.loads(t)
    assert "permissions" in d and "deny" in d["permissions"], t
    posix = zona.replace(os.sep, "/")
    assert any(posix in r for r in d["permissions"]["deny"]), t


@prova("G6", "determinismo: due esecuzioni identiche danno lo stesso rapporto (modulo mtime)")
def g6():
    d, perimetro, zona = fixture()
    try:
        esterno = os.path.join(zona, "x.json")
        c = script_scrive(esterno)

        def esito():
            codice, _, _ = esegui(perimetro, [zona], c)
            r = json.load(open(os.path.join(perimetro, "00-guard-report.json"), encoding="utf-8"))
            return codice, [(v["percorso"], v["dimensione"], v["stato"]) for v in r["violazioni"]]
        c1, v1 = esito()
        os.unlink(os.path.join(perimetro, "00-guard-report.json"))
        os.unlink(esterno)
        c2, v2 = esito()
        assert c1 == c2 == 1
        assert v1 == v2, f"due esecuzioni, inventari diversi: {v1} vs {v2}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("G7", "zero rete: nessun import di rete, nessun tentativo di socket")
def g7():
    import socket
    tentativi = []
    vero = socket.socket.connect

    def spia(self, *a, **k):
        tentativi.append(a)
        raise AssertionError("replay_guard ha tentato una connessione")
    socket.socket.connect = spia
    try:
        import replay_guard as G
        d, perimetro, zona = fixture()
        prima = G.fotografia(perimetro)
        G.fotografia(zona)
        G.violazioni(prima, {}, perimetro, [zona])
        assert not tentativi
        shutil.rmtree(d, ignore_errors=True)
    finally:
        socket.socket.connect = vero
    src = open(GUARD, encoding="utf-8").read()
    for lib in ("socket", "http", "urllib", "requests", "ftplib", "smtplib"):
        assert not __import__("re").search(r"^\s*(import|from)\s+" + lib + r"\b", src,
                                           __import__("re").M), \
            f"replay_guard importa {lib}"


@prova("G8", "una zona dichiarata ma annidata dentro il perimetro non produce false violazioni")
def g8():
    d, perimetro, zona = fixture()
    try:
        figlia = os.path.join(perimetro, "sottozona")
        os.makedirs(figlia)
        destinazione = os.path.join(figlia, "pack.json")
        codice, _, err = esegui(perimetro, [zona, figlia], script_scrive(destinazione))
        assert codice == 0, f"uscita {codice}: {err}"
        assert os.path.exists(destinazione)
        assert rapporto(perimetro)["violazioni"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print(f"replay_guard_selftest — {len(ESITI)} prove\n")
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
