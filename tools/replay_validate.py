#!/usr/bin/env python3
"""
replay_validate.py — il passo di validazione del replay di `evidence-researcher`, con il
null-check dentro il percorso invece che accanto.

PERCHE' ESISTE. Il replay produce da sempre un `03-validazione.json`, ma nessuno strumento lo
produceva: lo componeva l'orchestratore a mano, run per run. Un passo che non esiste come codice
non ha un ordine garantito, e l'ordine e' precisamente cio' che qui va garantito: il null-check
va eseguito SUBITO PRIMA della validazione di schema, e il suo esito deve essere DECISIVO.

E' il seguito di una catena di rimedi che non avevano un momento in cui qualcuno li esegue. La
regola «opzionale non significa nullable» e' nel prompt dal commit `ae34883` e nomina per nome
`spend_evidence[].amount`; il replay `20260823-0211` l'ha violata cinque volte; `M3.3` ha
aggiunto un gesto — «prima di emettere, cerca `null` nel tuo JSON» — e la sua ricevuta ha
registrato che un'auto-verifica rivolta a un modello e' una speranza, non una difesa; il commit
`9fe3e30` ha scritto il controllo, e la sua ricevuta ha registrato che nessuno lo chiamava.
Questo file lo chiama.

DUE CONTROLLI, UN ORDINE, UN VERDETTO.

  1. NULL-CHECK contro la vista delivery gia' generata. Ogni `null` in una posizione che il
     contratto non dichiara nullable — con `~` o con `z` — rende il pack NON VALIDO.
  2. VALIDAZIONE DI SCHEMA contro `evidence-pack.schema.json`.

Il verdetto e' `VALIDO` solo se entrambi passano. Il null-check e' decisivo: un pack con un
`null` illecito e' non valido anche se lo schema lo accettasse.

E LA VALIDAZIONE DI SCHEMA GIRA COMUNQUE, anche quando il null-check ha gia' deciso. Non e' una
distrazione: il replay esiste per CONTARE le classi di difetto, e sopprimerne una perche'
un'altra ha gia' deciso il verdetto degraderebbe lo strumento di misura. Il verdetto e' un bit;
il rapporto e' la misura, e resta intera.

COSA NON FA. Non tocca `run_close.py` ne' i suoi tre verdetti: quelli restano dove sono e come
sono. Non si estende ad altri agenti — `evidence-researcher` e' l'unico che abbia un contratto
compilabile, e ogni altro nome e' rifiutato invece che indovinato. Non scrive nulla: stampa il
rapporto su stdout, e chi lo invoca decide dove metterlo. Non tocca la rete.

  uso:  python tools/replay_validate.py --raw <canale.raw.txt> --contratto <delivery.txt>
                                        [--schemi <dir>] [--agente evidence-researcher]
        exit 0 = VALIDO
             1 = NON VALIDO (null illeciti, errori di schema, o entrambi)
             2 = il raw non contiene un pack, il contratto non e' leggibile, o l'uso e' errato

I codici 1 e 2 sono distinti perche' «il pack e' sbagliato» e «l'invocazione e' rotta» si
riparano in posti diversi: il primo e' una misura, il secondo un guasto.
"""
import argparse, hashlib, json, os, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import null_check                                                          # noqa: E402

VERSIONE = "v1.0.0"
AGENTE = "evidence-researcher"
SCHEMA = "evidence-pack.schema.json"
ORDINE = ["null_check", "schema"]


class Guasto(Exception):
    """L'invocazione e' rotta, non il pack. Exit 2."""


def estrai_pack(testo):
    """Il primo oggetto JSON bilanciato del raw. Nessuna riparazione, nessuna indulgenza.

    Il CLI incornicia il pack di prosa e talvolta di un blocco ```json: si cerca prima quello,
    poi la prima graffa a inizio riga. Le graffe dentro le stringhe non contano, ed e' il motivo
    per cui questo non e' un `count("{")`.
    """
    inizio = None
    marcatore = testo.find("```json")
    if marcatore != -1:
        g = testo.find("{", marcatore)
        if g != -1:
            inizio = g
    if inizio is None:
        for i, ch in enumerate(testo):
            if ch == "{" and (i == 0 or testo[i - 1] == "\n"):
                inizio = i
                break
    if inizio is None:
        raise Guasto("nessun oggetto JSON nel raw")
    prof, in_stringa, fuga = 0, False, False
    for j in range(inizio, len(testo)):
        c = testo[j]
        if in_stringa:
            if fuga:
                fuga = False
            elif c == "\\":
                fuga = True
            elif c == '"':
                in_stringa = False
            continue
        if c == '"':
            in_stringa = True
        elif c == "{":
            prof += 1
        elif c == "}":
            prof -= 1
            if prof == 0:
                try:
                    return json.loads(testo[inizio:j + 1])
                except json.JSONDecodeError as e:
                    raise Guasto(f"oggetto JSON non deserializzabile: {e}")
    raise Guasto("graffe non bilanciate nel raw")


def valida_schema(pack, dir_schemi):
    """Gli errori di schema, ciascuno con puntatore e messaggio. Lista vuota = nessun errore.

    L'import di `jsonschema` sta QUI e non in testa: su POSIX `referencing` e' assente e
    `jsonschema` e' alla 4.10.3 senza il parametro `registry=`, e importarlo in testa renderebbe
    non eseguibile anche il null-check, che invece e' Python puro e gira ovunque. Se la libreria
    manca, il guasto e' dichiarato e non silenzioso.
    """
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012
    except ImportError as e:
        raise Guasto(f"validazione di schema non disponibile su questa piattaforma: {e}")
    schemi, reg = {}, Registry()
    for nome in sorted(os.listdir(dir_schemi)):
        if not nome.endswith(".json"):
            continue
        with open(os.path.join(dir_schemi, nome), "rb") as f:
            schemi[nome] = json.loads(f.read().replace(b"\r\n", b"\n").decode("utf-8"))
        reg = reg.with_resource(nome, Resource.from_contents(
            schemi[nome], default_specification=DRAFT202012))
    if SCHEMA not in schemi:
        raise Guasto(f"schema assente: {SCHEMA}")
    v = Draft202012Validator(schemi[SCHEMA], registry=reg)
    return [{"puntatore": "/" + "/".join(map(str, e.absolute_path)),
             "keyword": e.validator, "messaggio": e.message}
            for e in sorted(v.iter_errors(pack), key=lambda e: list(e.absolute_path))]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="valida un pack di replay: null-check, poi schema")
    ap.add_argument("--raw", required=True, metavar="FILE",
                    help="l'uscita grezza del canale, prosa compresa")
    ap.add_argument("--contratto", required=True, metavar="DELIVERY",
                    help="la vista delivery gia' generata da contract_compile.py")
    ap.add_argument("--schemi", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas"))
    ap.add_argument("--agente", default=AGENTE)
    a = ap.parse_args(argv)
    try:
        if a.agente != AGENTE:
            raise Guasto(f"agente fuori perimetro: {a.agente}. Questo passo vale per il solo "
                         f"{AGENTE}, che e' l'unico con un contratto compilabile")
        with open(a.raw, "rb") as f:
            grezzo = f.read()
        pack = estrai_pack(grezzo.replace(b"\r\n", b"\n").decode("utf-8"))
        with open(a.contratto, "rb") as f:
            testo = f.read().replace(b"\r\n", b"\n").decode("utf-8")
        try:
            albero = null_check.leggi_contratto(testo)
        except null_check.ContrattoIlleggibile as e:
            raise Guasto(f"contratto non leggibile: {e}")

        # (1) NULL-CHECK — prima, e decisivo.
        try:
            ok_null, illeciti = null_check.controlla(pack, albero)
        except null_check.ContrattoIlleggibile as e:
            raise Guasto(f"contratto non leggibile: {e}")

        # (2) SCHEMA — dopo, e comunque: il verdetto e' gia' deciso ma la misura no.
        #
        # E IL SUO GUASTO NON ANNULLA IL VERDETTO GIA' PRESO. Una prima stesura racchiudeva
        # questa chiamata nello stesso `try` del null-check: bastava che `jsonschema` mancasse
        # — cioe' POSIX, la piattaforma che il docstring di `valida_schema` nomina — o che
        # `--schemi` puntasse alla directory sbagliata, e un pack con un `null` illecito usciva
        # con 2 invece di 1. Cioe': «l'invocazione e' rotta» al posto di «il pack e' sbagliato»,
        # che manda a riparare la cosa sbagliata, e soprattutto contraddice il mandato — il pack
        # va rifiutato come non valido, e la decisione era gia' in memoria quando veniva buttata.
        # Se il null-check ha potuto girare, il suo esito sopravvive alla misura mancante.
        schema_ok, errori, motivo = True, [], None
        try:
            errori = valida_schema(pack, a.schemi)
        except Guasto as e:
            schema_ok, motivo = False, str(e)
    except Guasto as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2

    valido = ok_null and schema_ok and not errori
    rapporto = {
        "strumento": f"replay_validate.py {VERSIONE}",
        "agente": a.agente,
        "ordine": ORDINE,
        "sha256_raw": hashlib.sha256(grezzo).hexdigest(),
        "sha256_contratto": hashlib.sha256(testo.encode("utf-8")).hexdigest(),
        "verdetto": "VALIDO" if valido else "NON VALIDO",
        "null_check": {
            "esito": "AMMESSO" if ok_null else "RIFIUTATO",
            "decisivo": True,
            "illeciti": illeciti,
        },
        "schema": {
            "schema": SCHEMA,
            "disponibile": schema_ok,
            "motivo_indisponibilita": motivo,
            "n_errori": len(errori),
            "errori": errori,
        },
    }
    sys.stdout.write(json.dumps(rapporto, ensure_ascii=False, indent=2) + "\n")
    return 0 if valido else 1


if __name__ == "__main__":
    sys.exit(main())
