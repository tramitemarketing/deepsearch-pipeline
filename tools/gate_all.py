#!/usr/bin/env python3
"""
gate_all.py — esegue tutte le prove del repository, e fallisce anche quando ne trova una che non
sa di dover eseguire.

Mandato `56-MANDATO-AGGREGATORE-GATE.md`, redazione 5, sha256
`d6338e7a7802ad09a15e405f85526c9a911dfe001d494c4c6566776ee7546e32`.

IL FATTO CHE QUESTO STRUMENTO CHIUDE. Fino a oggi ogni `*_selftest.py` si invocava a mano, da un
elenco scritto a mano. I selftest sotto `tools/` erano dodici; quell'elenco ne conteneva UNDICI —
fuori restava `ds_validate_selftest.py` — e arrivava a dodici voci aggiungendo `spec_check`. Per
una dozzina di slice si e' cosi' dichiarato «dodici gate verdi», e quella frase era VERA: dodici
voci, tutte verdi. Falsa era l'altra, «TUTTI i gate», che l'atto `55` registra cosi' — «era vera
dell'elenco e falsa dei fatti». La distinzione conta, ed e' il motivo per cui questo file esiste:
il selftest mancante era verde, ma nessuno lo stava guardando. Il difetto non era una prova rossa,
era una garanzia che dipendeva dalla memoria di qualcuno.

NON UN RILEVATORE EURISTICO: UN CENSIMENTO. Un `glob(*_selftest.py)` sposterebbe il difetto senza
chiuderlo, perche' `ds_validate_concurrency.py` e `spec_check.py` sono prove a tutti gli effetti
e non ci cascherebbero dentro. La regola e' invece: ogni `.py` sotto `tools/` o e' eseguito, o e'
escluso per nome con una ragione scritta, o e' dichiarato non-prova con una ragione scritta — e
non esiste una quarta possibilita'. Un file nuovo che non ricada in nessuna fa uscire `2`.

CHE COSA IL CENSIMENTO NON FA. Non rifiuta il parcheggio: nessuna regola meccanica puo' decidere
se un file sia una prova, e il criterio (`si esegue senza argomenti` · `l'exit code dichiara
conformita'` · `l'esito dipende dal solo albero dei sorgenti`) e' applicato A MANO. La regola
verifica che una ragione CI SIA, non che sia VERA — le ragioni degli strumenti qui sotto sono
DICIOTTO, i diciassette storici piu' l'aggregatore stesso, e SETTE delle diciassette erano false
nelle prime stesure: le hanno colte due review leggendo il sorgente, non una macchina. Rende il
parcheggio DELIBERATO: chi archivia un gate deve scriverne la ragione accanto al nome, e quella
riga resta qui a essere letta.

ESITI, e la loro precedenza `2 > 3 > 1 > 0`:
    0   tutte verdi
    1   almeno una prova fallita          — una MISURA: l'artefatto e' sbagliato
    2   inventario o esclusioni incoerenti
    3   errore di esecuzione o di tooling — un GUASTO: non ho potuto eseguire
`2` non puo' mai coesistere con gli altri, perche' il censimento si verifica PRIMA di eseguire.
`3` precede `1` perche' «non ho potuto eseguire una prova» e «una prova e' rossa» si riparano in
posti diversi: e' la stessa distinzione che `replay_validate` e `pack_repair` usano fra `1` e `2`.

NON-RICORSIONE STRUTTURALE. Alle prove che lancia, `gate_all` esporta in `DEEPSEARCH_GATE_ROOTS`
l'INSIEME dei realpath delle radici gia' in esame, e rifiuta di rientrare in una di esse. Un
insieme e non una casella: con una casella sola il ciclo `R -> T -> R` passerebbe indisturbato,
perche' il secondo livello sovrascriverebbe il primo.

`main(argv)` RITORNA SEMPRE e non chiama mai `sys.exit`, nemmeno per il rifiuto della guardia:
l'idioma dei selftest di questo repository cattura `Exception` e non `SystemExit`, e un'uscita
farebbe MORIRE chi lo chiama in-processo invece di segnare rosso.

  uso:  python tools/gate_all.py [--radice DIR] [--censimento FILE.json] [--timeout SECONDI]
"""
import glob, io, json, os, subprocess, sys, time

import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))

CHIAVE_RADICI = "DEEPSEARCH_GATE_ROOTS"

ORDINE_INSIEMI = ("PROVE", "STRUMENTI", "ESCLUSE")

# Il mandato prescrive un margine MINIMO di tre volte il massimo osservato. Le cifre, per esteso:
#   massimo storico    `spec_check_selftest`  ~112 s   ->  420 / 112   = 3,75x
#   massimo rimisurato nella passata di verifica  92,30 s  ->  420 / 92,30 = 4,55x
# `gate_all_selftest`, la cui durata era ignota perche' il file non esisteva, ne misura 7,88: non
# sposta il massimo e non impone di ridimensionare il default.
TIMEOUT_PREDEFINITO = 420.0


# ------------------------------------------------------------------ il censimento
#
# Che cosa e' una PROVA — tutte e tre le condizioni, non una:
#   (a) si esegue SENZA ARGOMENTI;
#   (b) il suo exit code DICHIARA UN ESITO DI CONFORMITA' — non uno stato, non un rapporto;
#   (c) quell'esito dipende SOLO DALL'ALBERO DEI SORGENTI: non da un run, non da un replay, non
#       da una configurazione d'ambiente, non dalla rete.
#
# La (c) non e' decorativa. `run_close.py` e `reconcile_run.py` soddisfano (a) e (b) — senza
# argomenti scelgono l'ultimo run e il loro exit code e' un verdetto — ed escono `2` oggi solo
# perche' `runs/` non esiste: una contingenza del filesystem, non una proprieta' dei file. Senza
# la (c) la selezione cambierebbe il giorno in cui quel run esiste.

PROVE = {
    "contract_compile_selftest.py":  "selftest del compilatore",
    "ds_validate_selftest.py":       "selftest del validatore; importa ed esegue anche la suite "
                                     "di ds_validate_concurrency, che percio' gira due volte",
    "ds_validate_concurrency.py":    "prova a se' per un requisito di piattaforma dichiarato "
                                     "(multiprocessing spawn su Windows): nessun argomento, "
                                     "7 controlli, exit 0/1",
    "gate_all_selftest.py":          "selftest di questo aggregatore",
    "null_check_selftest.py":        "selftest del null-check",
    "pack_repair_selftest.py":       "selftest del riparatore formale",
    "provenance_check_selftest.py":  "selftest del controllo di provenienza",
    "reconcile_run_selftest.py":     "selftest della riconciliazione",
    "replay_guard_selftest.py":      "selftest della guardia di perimetro",
    "replay_report_selftest.py":     "selftest del driver di replay",
    "replay_runner_selftest.py":     "selftest del runner",
    "replay_validate_selftest.py":   "selftest del validatore di replay",
    "run_close_selftest.py":         "selftest della chiusura di run",
    "spec_check.py":                 "gate dichiarato in cinque ricevute (32, 34, 37, 41, 45): "
                                     "nessun argomento, exit 0 = nessun errore, 1 = errori "
                                     "(anche 2 se la config e' illeggibile o mancano i file di "
                                     "prosa: qualunque uscita non nulla e' una rossa)",
    "spec_check_selftest.py":        "selftest di spec_check",
}

STRUMENTI = {
    # (a) — pretendono argomenti obbligatori
    "contract_compile.py":      "pretende argomenti obbligatori (--agente): fallisce (a)",
    "ds_call.py":               "pretende argomenti obbligatori (--prompt, --schema, --out): "
                                "fallisce (a)",
    "ds_validate_report.py":    "pretende un argomento obbligatorio (<radice>): fallisce (a)",
    "null_check.py":            "pretende argomenti obbligatori (--contratto, --pack): "
                                "fallisce (a)",
    "pack_repair.py":           "pretende argomenti obbligatori (--raw, --contratto): "
                                "fallisce (a)",
    "provenance_check.py":      "pretende argomenti obbligatori (--radice-da, --commit): "
                                "fallisce (a)",
    "replay_guard.py":          "pretende argomenti obbligatori (--perimetro e un comando): "
                                "fallisce (a)",
    "replay_report.py":         "pretende un argomento obbligatorio (--run): fallisce (a)",
    "replay_runner.py":         "pretende argomenti obbligatori (--stdin, --raw e un comando): "
                                "fallisce (a)",
    "replay_validate.py":       "pretende argomenti obbligatori (--raw, --contratto): "
                                "fallisce (a)",
    # (c) — l'esito riguarda un run, non il repository
    "run_close.py":             "senza argomenti chiude l'ULTIMO RUN e il suo exit code e' il "
                                "verdetto di quel run, non del repository: fallisce (c)",
    "reconcile_run.py":         "senza argomenti riconcilia l'ULTIMO RUN: il suo esito riguarda "
                                "quel run, non il repository: fallisce (c)",
    # (b) — non dichiara conformita'
    "provider_switch.py":       "senza argomenti stampa lo stato e ritorna sempre 0: non "
                                "dichiara conformita', fallisce (b)",
    "_chiavi.py":               "modulo senza main: si esegue, non fa nulla ed esce 0 — non "
                                "dichiara conformita', fallisce (b)",
    "ds_validate.py":           "modulo senza main: si esegue, non fa nulla ed esce 0 — "
                                "fallisce (b)",
    "ds_validate_contract.py":  "modulo senza main: si esegue, non fa nulla ed esce 0 — "
                                "fallisce (b)",
    # interattivo e scrittore
    "setup.py":                 "senza argomenti entra nella configurazione guidata, legge da "
                                "input() e SCRIVE config con backup: non e' eseguibile senza "
                                "sorveglianza e non e' inerte",
    # se stesso
    "gate_all.py":              "e' l'aggregatore stesso: soddisfa tutte e tre le condizioni del "
                                "criterio, ed e' il caso piu' netto in cui il criterio "
                                "dichiarato non e' una macchina. Comparire nel proprio "
                                "censimento sarebbe la ricorsione che la guardia vieta, e la "
                                "passata uscirebbe 3 alla prima riga",
}

# Vuoto per determinazione dell'autorita': `ds_validate_concurrency.py` e' INCLUSO fra le prove.
# Vuoto e' uno stato legittimo, non l'assenza del meccanismo — che esiste, e che il caso `G` del
# selftest prova con una fixture.
ESCLUSE = {}


def censimento(radice, elenco=None):
    """I tre insiemi e i problemi di coerenza. NON ESEGUE NULLA.

    Quattro regole, tutte con esito `2`:
      1. un `.py` presente sotto la radice e non classificato;
      2. un nome classificato che non esiste sul disco;
      3. una voce senza ragione, in qualunque dei tre insiemi;
      4. un nome classificato in piu' di un insieme."""
    if elenco is None:
        elenco = {"PROVE": PROVE, "STRUMENTI": STRUMENTI, "ESCLUSE": ESCLUSE}
    insiemi = {g: dict(elenco.get(g) or {}) for g in ORDINE_INSIEMI}
    problemi = []

    sul_disco = {os.path.basename(p) for p in glob.glob(os.path.join(radice, "*.py"))}

    dove = {}
    for g in ORDINE_INSIEMI:
        for n in insiemi[g]:
            dove.setdefault(n, []).append(g)

    for n in sorted(dove):
        if len(dove[n]) > 1:
            problemi.append(f"{n}: classificato in piu' insiemi ({', '.join(dove[n])})")

    for g in ORDINE_INSIEMI:
        for n in sorted(insiemi[g]):
            r = insiemi[g][n]
            if not isinstance(r, str) or not r.strip():
                problemi.append(f"{n}: voce in {g} senza ragione scritta")

    for n in sorted(dove):
        if n not in sul_disco:
            problemi.append(f"{n}: classificato ma non esiste sotto {radice}")

    for n in sorted(sul_disco - set(dove)):
        problemi.append(f"{n}: presente sotto la radice e NON CLASSIFICATO")

    return {"insiemi": insiemi, "problemi": problemi}


# ------------------------------------------------------------------ il punto unico di lancio

def _termina_albero(p):
    """Termina il figlio E I SUOI DISCENDENTI.

    Non il solo figlio diretto: `ds_validate_concurrency` genera processi veri con `spawn`, e su
    Windows uccidere il figlio lascia vivi i nipoti."""
    if p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(p.pid), 9)
    except Exception:                                                     # noqa: BLE001
        try: p.kill()
        except Exception: pass
    try: p.wait(timeout=15)
    except Exception: pass


def _decodifica(b):
    return (b or b"").decode("utf-8", "replace")


def esegui_prova(percorso, timeout, ambiente):
    """L'UNICO punto da cui nascono sottoprocessi. Ritorna (esito, durata, uscita).

    Che sia uno solo non e' stile: e' cio' che rende osservabile «non ha eseguito nulla». Il
    selftest lo sostituisce con uno stub che solleva, e cosi' il difetto fallisce NEL PUNTO
    invece che alla fine. Lo stub e' pero' cieco a un aggiramento, ed e' per questo che le prove
    finte scrivono anche una traccia per conto proprio.

    Le prove girano col cwd sulla directory che contiene la radice — per il `tools/` reale, la
    radice del repository: e' il modo in cui questi gate sono sempre stati invocati a mano."""
    avvio = time.monotonic()
    lavoro = os.path.dirname(os.path.abspath(os.path.dirname(percorso)))
    kw = {}
    if os.name != "nt":
        kw["start_new_session"] = True
    try:
        p = subprocess.Popen([sys.executable, percorso],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=lavoro or None, env=ambiente, **kw)
    except OSError as e:
        return "GUASTA", time.monotonic() - avvio, f"{type(e).__name__}: {e}"
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _termina_albero(p)
        return "NON TERMINATA", time.monotonic() - avvio, ""
    durata = time.monotonic() - avvio
    uscita = _decodifica(out) + _decodifica(err)
    # Qualunque uscita non nulla di una prova ESEGUITA e' una rossa: il `3` e' riservato a «non
    # ho potuto eseguire». Vale anche per il `2` che `spec_check` puo' restituire.
    return ("verde" if p.returncode == 0 else "ROSSA"), durata, uscita


# ------------------------------------------------------------------ la guardia

def _insieme_ambientale(ambiente):
    grezzo = (ambiente or {}).get(CHIAVE_RADICI, "")
    return {os.path.realpath(x) for x in grezzo.split(os.pathsep) if x.strip()}


# ------------------------------------------------------------------ il rapporto

def _riga(esito, durata, nome, coda=""):
    d = "—" if durata is None else f"{durata:.2f}s"
    return f"  {esito:<14}  {d:>8}  {nome}" + (f"  ({coda})" if coda else "")


def _blocco(testo):
    fuori = []
    for r in (testo or "").rstrip("\n").splitlines():
        fuori.append("      | " + r)
    return "\n".join(fuori)


# ------------------------------------------------------------------ main

def _uso(messaggio):
    print(f"gate_all: {messaggio}")
    print("uso: gate_all.py [--radice DIR] [--censimento FILE.json] [--timeout SECONDI]")
    return 3


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    radice, percorso_censimento, timeout = QUI, None, TIMEOUT_PREDEFINITO
    while a:
        v = a.pop(0)
        if v in ("--radice", "--censimento", "--timeout"):
            if not a:
                return _uso(f"{v} vuole un valore")
            w = a.pop(0)
            if v == "--radice":
                radice = w
            elif v == "--censimento":
                percorso_censimento = w
            else:
                try:
                    timeout = float(w)
                except ValueError:
                    return _uso(f"--timeout vuole un numero, non {w!r}")
        else:
            return _uso(f"argomento sconosciuto: {v!r}")

    if not os.path.isdir(radice):
        return _uso(f"la radice non e' una directory: {radice!r}")

    # ---- la guardia, prima di ogni altra cosa: e' un guasto d'invocazione, non un censimento
    presenti = _insieme_ambientale(os.environ)
    mia = os.path.realpath(radice)
    if mia in presenti:
        print(f"gate_all: rifiuto di rientrare in una radice gia' in esame: {mia}")
        print(f"          radici in esame: {os.pathsep.join(sorted(presenti))}")
        return 3

    elenco = None
    if percorso_censimento is not None:
        try:
            with open(percorso_censimento, encoding="utf-8") as g:
                elenco = json.load(g)
        except Exception as e:                                            # noqa: BLE001
            return _uso(f"censimento illeggibile: {type(e).__name__}: {e}")
        if not isinstance(elenco, dict):
            return _uso("il censimento non e' un oggetto JSON")
        # Un JSON valido puo' comunque non essere un censimento: se un gruppo e' una lista invece
        # di una mappa nome->ragione, `dict(...)` piu' avanti solleverebbe un `ValueError` che
        # uscirebbe come traceback ed exit 1 — cioe' «una prova e' fallita», che e' falso. Un
        # censimento malformato e' un GUASTO D'INVOCAZIONE, e vale 3.
        for g in ORDINE_INSIEMI:
            v = elenco.get(g)
            if v is not None and not isinstance(v, dict):
                return _uso(f"il gruppo {g} del censimento non e' una mappa nome->ragione, "
                            f"ma {type(v).__name__}")

    print(f"gate_all — radice {mia}")

    c = censimento(radice, elenco)
    if c["problemi"]:
        print(f"\n  censimento INCOERENTE — {len(c['problemi'])} problemi, nulla e' stato "
              f"eseguito:")
        for p in c["problemi"]:
            print(f"    · {p}")
        return 2

    insiemi = c["insiemi"]
    ambiente = dict(os.environ)
    ambiente[CHIAVE_RADICI] = os.pathsep.join(sorted(presenti | {mia}))

    # In serie, in ordine byte-wise del nome completo del file, estensione compresa: `sorted()`
    # sulla stringa, che non dipende dal locale. Sotto `tools/` i nomi hanno solo minuscole,
    # cifre e `_`, e nella tabella ASCII '.'(0x2E) < cifre(0x30) < '_'(0x5F) < minuscole(0x61) —
    # quindi `ds_validate.py` viene prima di `ds_validate_concurrency.py`.
    da_fare = sorted(list(insiemi["PROVE"]) + list(insiemi["ESCLUSE"]))

    print()
    rosse, guaste, righe = [], [], []
    for nome in da_fare:
        if nome in insiemi["ESCLUSE"]:
            righe.append(_riga("ESCLUSA", None, nome, insiemi["ESCLUSE"][nome]))
            continue
        esito, durata, uscita = esegui_prova(os.path.join(radice, nome), timeout, ambiente)
        righe.append(_riga(esito, durata, nome))
        if esito == "ROSSA":
            rosse.append(nome)
        elif esito != "verde":
            guaste.append(nome)
        # Lo `stderr` — e piu' in generale l'uscita — si riporta SOLO per le non verdi.
        if esito != "verde" and uscita.strip():
            righe.append(_blocco(uscita))
    for r in righe:
        print(r)

    n = len(insiemi["PROVE"])
    print()
    if guaste:
        print(f"  {len(guaste)} su {n} NON ESEGUITE: " + ", ".join(guaste)
              + (f"  ·  {len(rosse)} rosse: " + ", ".join(rosse) if rosse else ""))
        return 3
    if rosse:
        print(f"  {n - len(rosse)}/{n} verdi — ROSSE: " + ", ".join(rosse))
        return 1
    print(f"  {n}/{n} verdi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
