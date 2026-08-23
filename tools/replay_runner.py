#!/usr/bin/env python3
"""
replay_runner.py — il runner di invocazione dei replay: task su stdin, uscita su file raw.

PERCHE' ESISTE. Il replay 20260823-1548 si e' arrestato per un errore dell'harness
d'invocazione: un `json.dumps` mal posto aveva avvolto il percorso del file raw in virgolette
letterali, e il processo e' fallito con OSError 22 PRIMA che il CLI girasse. Questo runner e'
il posto UNICO in cui il task e il percorso raw vengono passati, ed e' costruito per rendere
quel modo di fallire impossibile: i percorsi arrivano come elementi di argv LETTERALI e sono
aperti cosi' come sono; questo file non importa `json` — non c'e' alcuna serializzazione in cui
un percorso possa ritrovarsi quotato. Il selftest inchioda entrambi i lati: R1 riproduce il
fallimento del percorso quotato, R2 verifica il percorso letterale (con spazi e apostrofo).

Il runner NON fa rete: esegue il comando del chiamante (tipicamente il CLI con l'agente) e si
limita a trasmettere stdin, raccogliere stdout+stderr sul file raw e propagare l'exit code.

  uso:  python tools/replay_runner.py --stdin <task> --raw <percorso-uscita> -- <comando...>
  exit  = l'exit code del comando (0..N); 2 = uso errato; 3 = ambiente rotto
"""
import argparse, os, subprocess, sys
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
