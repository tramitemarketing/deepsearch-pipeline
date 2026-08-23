#!/usr/bin/env python3
"""
replay_guard.py — contenimento e monitoraggio delle scritture di un comando in un perimetro
isolato.

PERCHE' ESISTE. I due replay del contratto consegnato hanno mostrato due difetti
dell'HARNESS, non del contratto: (1) gli agenti hanno scritto fuori dal perimetro del replay
(C:\\tmp, runtime\\output del worktree) invece di restituire il pack come risposta; (2) il
monitoraggio per-canale sotto-contava i diff a file singolo, cosi' la condizione d'arresto
veniva accertata a fine corsa invece che alla prima scrittura.

TRE LIVELLI, e il rilevatore e' quello che conta. (a) PREVENTIVO: `regole_settings()` genera un
file di settings per il CLI (`--settings`) con regole `permissions.deny` per `Write` fuori dal
perimetro, e l'invocazione prescritta porta `--disallowedTools Write` — difesa in profondita',
la cui onoranza dipende dalla versione del CLI e NON e' assunta. (b) RILEVATORE: istantanee
prima/dopo delle zone monitorate, confronto su insiemi di tuple (percorso, dimensione,
mtime_ns) in Python — il diff a file singolo che ha eluso il monitoraggio precedente e' coperto
da una prova dedicata. (c) REGISTRO: il rapporto `00-guard-report.json` e' scritto DENTRO il
perimetro, in modo atomico.

Nessuna rete in questo file: il comando eseguito e' responsabilita' del chiamante.

  uso:  python tools/replay_guard.py --perimetro <dir> [--zona <dir>]... -- <comando...>
  exit  0 = comando completato, nessuna scrittura fuori perimetro
        1 = violazioni rilevate (inventario in 00-guard-report.json e su stdout)
        2 = uso errato
        3 = ambiente rotto (perimetro o zone inesistenti)
"""
import argparse, json, os, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

VERSIONE = "v1.0.0"


class AmbienteRotto(Exception):
    """Il guard non puo' partire. Exit 3."""


def fotografia(zona):
    """I file di una zona come {percorso-relativo: (dimensione, mtime_ns)}. Nessuna decodifica,
    nessun confronto testuale: tuple e basta."""
    out = {}
    if not os.path.isdir(zona):
        return out
    for radice, _, files in os.walk(zona):
        for f in files:
            p = os.path.join(radice, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, zona).replace(os.sep, "/")] = (st.st_size, st.st_mtime_ns)
    return out


def violazioni(prima, dopo, perimetro, zone_extra):
    """I file NUOVI o MODIFICATI nelle zone FUORI dal perimetro. Il perimetro e' il confine:
    le scritture dentro sono legittime e non compaiono mai qui."""
    out = []
    per_r = os.path.realpath(perimetro)
    for zona in zone_extra:
        zr = os.path.realpath(zona)
        if zr == per_r or zr.startswith(per_r + os.sep) or not os.path.isdir(zr):
            continue                      # dentro il perimetro = legittimo, mai violazione
        a = prima.get(zr, {})
        b = dopo.get(zr, {})
        for rel, info in b.items():
            if a.get(rel) != info:
                out.append({"percorso": os.path.join(zr, rel.replace("/", os.sep)),
                            "dimensione": info[0],
                            "mtime_ns": info[1],
                            "stato": "nuovo" if rel not in a else "modificato"})
    return out


def regole_settings(perimetro, zone_extra):
    """Le regole di negazione per il CLI: Write vietato fuori dal perimetro. Best effort: la
    semantica delle regole dipende dalla versione del CLI; il rilevatore non dipende da loro."""
    deny = []
    for zona in zone_extra:
        if os.path.realpath(zona) != os.path.realpath(perimetro):
            deny.append("Write(%s/**)" % zona.replace(os.sep, "/"))
    doc = {
        "permissions": {"deny": deny},
        "_nota": ("Regole generate da replay_guard.py %s: Write vietato fuori dal perimetro. "
                  "L'onoranza dipende dalla versione del CLI; il rilevatore del guard resta la "
                  "fonte di verita'.") % VERSIONE,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--" in argv:
        i = argv.index("--")
        prima, comando = argv[:i], argv[i + 1:]
    else:
        prima, comando = argv, []
    ap = argparse.ArgumentParser(description="contenimento e monitoraggio delle scritture")
    ap.add_argument("--perimetro", required=True)
    ap.add_argument("--zona", nargs="*", default=[], metavar="DIR")
    a = ap.parse_args(prima)
    perimetro = os.path.realpath(a.perimetro)
    if not os.path.isdir(perimetro):
        raise AmbienteRotto(f"perimetro inesistente: {a.perimetro}")
    zone = [perimetro] + [os.path.realpath(z) for z in a.zona]
    inesistenti = [z for z in a.zona if not os.path.isdir(os.path.realpath(z))]
    if inesistenti:
        raise AmbienteRotto(f"zona inesistente: {inesistenti[0]}")
    if not comando:
        ap.error("nessun comando dopo «--»")
    prima_foto = {z: fotografia(z) for z in zone}
    try:
        r = subprocess.run(comando, cwd=perimetro)
    except (FileNotFoundError, OSError) as e:
        raise AmbienteRotto(f"comando non eseguibile: {e}")
    dopo_foto = {z: fotografia(z) for z in zone}
    vio = violazioni(prima_foto, dopo_foto, perimetro, zone[1:])
    report = {"guard_version": VERSIONE, "perimetro": perimetro,
              "comando": comando, "exit_comando": r.returncode,
              "violazioni": vio}
    testo = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    destinazione = os.path.join(perimetro, "00-guard-report.json")
    fd, tmp = tempfile.mkstemp(prefix=".guard-", suffix=".tmp", dir=perimetro)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(testo.encode("utf-8"))
        os.replace(tmp, destinazione)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    sys.stdout.write(testo)
    return 1 if vio else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AmbienteRotto as e:
        print(f"ambiente rotto: {e}", file=sys.stderr)
        sys.exit(3)
