#!/usr/bin/env python3
"""
replay_runner_selftest.py — dimostra che il runner di replay passa i percorsi LETTERALMENTE e
risolve l'eseguibile prima di avviarlo.

Due errori dell'harness hanno arrestato i replay, e queste prove li inchiodano entrambi: il
20260823-1548 (un `json.dumps` mal posto aveva avvolto il percorso del file raw in virgolette
letterali -> OSError 22) e il 20260823-1623 (il nome nudo `claude` non risolto: su PATH c'e'
lo shim .cmd, non l'eseguibile -> WinError 2). R1 riproduce il modo di fallire del percorso
quotato, R5 quello del nome nudo; R2/R6 verificano i percorsi risolti. Processi usa-e-getta,
nessuna rete, nessun CLI reale, nessun artefatto reale toccato.

  uso:  python tools/replay_runner_selftest.py
        exit 0 = tutte le prove verdi
"""
import os, re, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(QUI, "replay_runner.py")

ESITI = []


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


@prova("R1", "il modo di fallire del 20260823-1548: il percorso quotato non produce MAI il file previsto")
def r1():
    d = tempfile.mkdtemp(prefix="rr-selftest-")
    try:
        sotto = os.path.join(d, "sotto")
        os.makedirs(sotto)
        percorso = os.path.join(sotto, "raw con spazio.txt")
        codice = ("import os, json; open(json.dumps(r'%s'), 'wb').close()" % percorso)
        r = subprocess.run([sys.executable, "-c", codice], capture_output=True, timeout=60,
                           cwd=d)
        # su Windows il percorso quotato fallisce (OSError 22, come nel replay); su POSIX
        # crea un file col NOME sbagliato (virgolette incluse). In NESSUN caso esiste il file
        # previsto: e' questa la proprieta' che R2 rovescia.
        assert not os.path.exists(percorso), \
            f"il percorso quotato ha prodotto il file previsto: uscita {r.returncode}"
        if r.returncode != 0 and os.name == "nt":
            assert "Invalid argument" in r.stderr.decode("utf-8", "replace"), \
                "il fallimento non e' l'OSError 22 misurato nel replay"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R2", "il runner passa il percorso letterale: output scritto, exit trasmesso, stdin trasmesso")
def r2():
    d = tempfile.mkdtemp(prefix="rr-selftest-")
    try:
        task = os.path.join(d, "task di prova.txt")
        open(task, "wb").write("contenuto del task\n".encode("utf-8"))
        raw = os.path.join(d, "sotto dir", "raw con apostrofo' e spazio.txt")
        os.makedirs(os.path.dirname(raw))
        comando_finto = ("import sys; "
                         "sys.stdout.buffer.write(b'ECHO:' + sys.stdin.buffer.read()); "
                         "sys.stderr.buffer.write(b'nota'); sys.exit(7)")
        r = subprocess.run([sys.executable, RUNNER, "--stdin", task, "--raw", raw,
                            "--", sys.executable, "-c", comando_finto],
                           capture_output=True, timeout=120)
        assert r.returncode == 7, f"exit non trasmesso: {r.returncode} / {r.stderr.decode()}"
        assert not r.stdout and not r.stderr, "il runner non deve scrivere su stdout/stderr"
        b = open(raw, "rb").read()
        assert b == "ECHO:contenuto del task\nnota".encode("utf-8"), \
            f"il file raw non e' l'uscita esatta: {b!r}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R3", "il runner non importa json: nessun percorso quotato possibile nel codice")
def r3():
    src = open(RUNNER, encoding="utf-8").read()
    assert not re.search(r"^\s*import\s+json\b", src, re.M), \
        "il runner importa json: il modo di fallire del 20260823-1548 resta possibile"
    assert src.count("json.dumps") == 1, \
        "json.dumps compare nel CODICE: e' ammesso solo nel docstring, come lezione"
    for lib in ("socket", "http", "urllib", "requests", "ftplib", "smtplib"):
        assert not re.search(r"^\s*(import|from)\s+" + lib + r"\b", src, re.M), \
            f"replay_runner importa {lib}"


@prova("R4", "timeout: il comando appeso viene interrotto, exit 3, nota nel raw")
def r4():
    d = tempfile.mkdtemp(prefix="rr-selftest-")
    try:
        task = os.path.join(d, "task.txt")
        open(task, "wb").write(b"t\n")
        raw = os.path.join(d, "raw.txt")
        r = subprocess.run([sys.executable, RUNNER, "--stdin", task, "--raw", raw,
                            "--timeout", "1",
                            "--", sys.executable, "-c", "import time; time.sleep(30)"],
                           capture_output=True, timeout=120)
        assert r.returncode == 3, f"uscita {r.returncode}: {r.stderr.decode()}"
        assert "oltre il timeout" in r.stderr.decode("utf-8", "replace")
        assert "comando interrotto" in open(raw, "rb").read().decode("utf-8", "replace")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R5", "nome nudo risolto via shim: il runner risolve con shutil.which e lo esegue")
def r5():
    """La prova ROSSA del difetto 20260823-1623: con il runner di HEAD, su Windows, un nome nudo
    che esiste solo come shim .cmd su PATH fallisce con WinError 2 (CreateProcess non risolve
    gli shim). Dopo la correzione il runner risolve il percorso reale e lo esegue. Su POSIX il
    nome nudo era gia' risolto da execvp: la prova resta come guardia di non regressione."""
    d = tempfile.mkdtemp(prefix="rr-selftest-")
    try:
        bin_dir = os.path.join(d, "bin")
        os.makedirs(bin_dir)
        if os.name == "nt":
            shim = os.path.join(bin_dir, "agente-echo.cmd")
            open(shim, "w", encoding="utf-8", newline="\n").write("@echo off\necho SHIM-OK\n")
        else:
            shim = os.path.join(bin_dir, "agente-echo")
            open(shim, "w", encoding="utf-8").write("#!/bin/sh\necho SHIM-OK\n")
            os.chmod(shim, 0o755)
        task = os.path.join(d, "task.txt")
        open(task, "wb").write(b"t\n")
        raw = os.path.join(d, "raw.txt")
        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        r = subprocess.run([sys.executable, RUNNER, "--stdin", task, "--raw", raw,
                            "--", "agente-echo"],
                           capture_output=True, timeout=120, env=env)
        assert r.returncode == 0, (
            f"il nome nudo non e' stato risolto: {r.returncode} / {r.stderr.decode()}")
        contenuto = open(raw, "rb").read().replace(b"\r\n", b"\n")
        assert contenuto == "SHIM-OK\n".encode("utf-8"), \
            f"lo shim non e' stato eseguito: {open(raw, 'rb').read()!r}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("R6", "percorso risolto esplicito: passa e continua a passare")
def r6():
    d = tempfile.mkdtemp(prefix="rr-selftest-")
    try:
        task = os.path.join(d, "task.txt")
        open(task, "wb").write(b"t\n")
        raw = os.path.join(d, "raw.txt")
        comando_finto = ("import sys; sys.stdout.buffer.write(b'OK'); sys.exit(0)")
        r = subprocess.run([sys.executable, RUNNER, "--stdin", task, "--raw", raw,
                            "--", sys.executable, "-c", comando_finto],
                           capture_output=True, timeout=120)
        assert r.returncode == 0, r.stderr.decode()
        assert open(raw, "rb").read() == b"OK"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print(f"replay_runner_selftest — {len(ESITI)} prove\n")
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
