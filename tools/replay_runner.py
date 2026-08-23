#!/usr/bin/env python3
"""
replay_runner.py — il runner di invocazione dei replay: task su stdin, uscita su file raw.

PERCHE' ESISTE. Due errori dell'harness d'invocazione hanno arrestato i replay, e questo
runner e' il posto UNICO in cui vanno resi impossibili:

1. (20260823-1548) un `json.dumps` mal posto aveva avvolto il percorso del file raw in
   virgolette letterali: i percorsi arrivano come elementi di argv LETTERALI e sono aperti
   cosi' come sono; questo file non importa `json`.
2. (20260823-1623) il nome nudo `claude` non veniva risolto: su PATH c'e' lo shim npm
   (claude.cmd), non claude.exe, e CreateProcess non esegue gli shim. Il runner risolve
   l'eseguibile con `shutil.which` PRIMA di avviarlo e usa il percorso reale.

Il selftest inchioda entrambi i lati: R1 riproduce il fallimento del percorso quotato, R5
riproduce il fallimento del nome nudo (rosso su HEAD), R2/R6 verificano i percorsi risolti.

Il runner NON fa rete: esegue il comando del chiamante (tipicamente il CLI con l'agente) e si
limita a trasmettere stdin, raccogliere stdout+stderr sul file raw e propagare l'exit code.

  uso:  python tools/replay_runner.py --stdin <task> --raw <percorso-uscita> -- <comando...>
  exit  = l'exit code del comando (0..N); 2 = uso errato; 3 = ambiente rotto
"""
import argparse, os, shutil, subprocess, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

VERSIONE = "v1.0.0"


class AmbienteRotto(Exception):
    """Il runner non puo' partire. Exit 3."""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--" in argv:
        i = argv.index("--")
        prima, comando = argv[:i], argv[i + 1:]
    else:
        prima, comando = argv, []
    ap = argparse.ArgumentParser(description="runner di invocazione dei replay")
    ap.add_argument("--stdin", required=True, metavar="TASK",
                    help="il file del task, passato al comando su stdin, byte per byte")
    ap.add_argument("--raw", required=True, metavar="PERCORSO",
                    help="il file in cui raccogliere stdout+stderr del comando, LETTERALE: "
                         "viene aperto cosi' come e', nessuna serializzazione")
    ap.add_argument("--timeout", type=float, default=None,
                    help="tetto in secondi per il comando (default: nessun limite)")
    a = ap.parse_args(prima)
    if not os.path.exists(a.stdin):
        raise AmbienteRotto(f"task inesistente: {a.stdin}")
    if not comando:
        ap.error("nessun comando dopo «--»")
    padre = os.path.dirname(os.path.abspath(a.raw))
    if not os.path.isdir(padre):
        raise AmbienteRotto(f"directory del raw inesistente: {padre}")
    task = open(a.stdin, "rb").read()
    risolto = shutil.which(comando[0])
    if risolto is None:
        raise AmbienteRotto(
            f"eseguibile non trovato: {comando[0]} — il replay 20260823-1623 si e' arrestato "
            "proprio qui: CreateProcess non risolve gli shim (.cmd) su PATH; il runner risolve "
            "con shutil.which e usa il percorso reale")
    comando = [risolto] + comando[1:]
    try:
        out = open(a.raw, "wb")
        try:
            r = subprocess.run(comando, input=task, stdout=out,
                               stderr=subprocess.STDOUT, timeout=a.timeout)
        except subprocess.TimeoutExpired as e:
            out.write(("\n[replay_runner: comando interrotto dopo %s secondi]\n"
                       % a.timeout).encode("utf-8"))
            out.close()
            raise AmbienteRotto(f"comando oltre il timeout: {a.timeout}s")
        finally:
            out.close()
    except (FileNotFoundError, OSError) as e:
        raise AmbienteRotto(f"comando non eseguibile o raw non scrivibile: {e}")
    return r.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AmbienteRotto as e:
        print(f"ambiente rotto: {e}", file=sys.stderr)
        sys.exit(3)
