#!/usr/bin/env python3
"""
pack_repair.py — UN passaggio di riparazione FORMALE del pack dopo il primo fallimento di
validazione. Ripara la forma; non tocca i fatti.

PERCHE' E' DETERMINISTICO E NON UN SECONDO GIRO DEL MODELLO. Il modo ovvio di riparare un pack
sarebbe rimandarlo all'agente con l'elenco degli errori e la raccomandazione di correggere solo
la struttura. Questa pipeline ha gia' misurato tre volte che cosa vale una raccomandazione: la
regola su `spend_evidence[].amount` e' nel prompt dal commit `ae34883`, nomina il campo per nome,
`M3.3` l'ha resa inequivoca e le ha aggiunto un gesto — e nel replay `20260824-2143` ha fallito
di nuovo, su tre canali invece che su uno. Un riparatore a cui si CHIEDE di non inventare fatti
e' la stessa speranza un piano piu' su, con in piu' il potere di riscrivere le prove.

Qui «non inventa fatti» non e' un'istruzione: e' un INVARIANTE, verificato dal codice a ogni
esecuzione e provato dal selftest.

    l'insieme multiplo delle FOGLIE SCALARI del pack riparato e' IDENTICO a quello di partenza

Nessuno scalare aggiunto — quindi nessun `source_id`, nessuna quantita', nessuna citazione, nessun
valore di enum inventato. Nessuno scalare perso — quindi nessuna evidenza cancellata per far
passare la validazione. `null` non e' uno scalare: e' l'assenza di un dato, ed e' l'unica cosa che
una riparazione puo' far sparire. Se dopo la riparazione l'invariante non regge, il riparatore
ANNULLA il proprio lavoro, restituisce il pack di partenza ed esce 2.

LE TRE SOLE RIPARAZIONI AMMESSE, e perche' ciascuna non perde informazione.

  R-A  `null` illecito su chiave OPZIONALE          → si omette la chiave.
       Il contratto non ammette li' un `null`, e la chiave non e' obbligatoria: toglierla non
       toglie alcun dato, perche' `null` non ne portava. Su chiave OBBLIGATORIA non si tocca:
       ometterla sarebbe togliere un campo che il contratto pretende, e riempirla sarebbe
       inventarlo.

  R-B  valore singolo dove il contratto vuole un ARRAY → si avvolge in `[valore]`.
       Il valore resta lo stesso byte per byte; cambia solo il contenitore. E' il caso
       `coverage.gaps` scritto come nota narrativa invece che come voce d'elenco.

  R-C  envelope AVVOLTO in un involucro                → si scarta l'involucro.
       Quando la radice non e' un envelope ma contiene, sotto un'unica chiave, un oggetto che lo
       e', si prende quello. Sparisce un nome di chiave, nessuno scalare.

TUTTO IL RESTO E' RIFIUTATO, ed elencato con il motivo:

  `required`   un dato obbligatorio manca → inventarlo e' fabbricare una prova
  `maxLength`  troncare uno `snippet` modificherebbe una CITAZIONE
  `enum` `pattern` `anyOf` `format`  richiederebbero di SCEGLIERE un valore
  `type` non-array  cambiare il tipo di un dato e' cambiare il dato
  envelope assente  sintetizzarlo richiederebbe `agent`, `run_id`, `status`… che non ci sono

UN SOLO PASSAGGIO. Non c'e' un ciclo: si valida, si ripara una volta, si valida di nuovo, e il
secondo verdetto e' definitivo. Se resta invalido, resta invalido.

GLI ERRORI DEL VALIDATORE SONO L'AUTORIZZAZIONE, non un suggerimento. Il riparatore non cerca
difetti per conto proprio: agisce solo sulle posizioni che il validatore ha segnalato. Un `null`
illecito che nessuno ha segnalato resta dov'e'.

  uso:  python tools/pack_repair.py --raw <file> --contratto <delivery>
                                    [--schemi D] [--errori F] [--fascicolo DIR]
        exit 0 = dopo la riparazione il pack e' VALIDO
             1 = resta NON VALIDO (compreso: non c'era nulla di riparabile)
             2 = il riparatore non ha potuto operare, o si e' annullato
"""
import argparse, collections, contextlib, copy, hashlib, io, json, os, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import null_check                                                         # noqa: E402
import replay_validate                                                    # noqa: E402

VERSIONE = "v1.0.0"
MARCA = "pack_repair"
REGOLE = {
    "R-A": "`null` illecito su chiave opzionale: la chiave si omette",
    "R-B": "valore singolo dove il contratto vuole un array: si avvolge",
    "R-C": "envelope avvolto in un involucro: si scarta l'involucro",
}


class Guasto(Exception):
    """Il riparatore non puo' operare, o si e' annullato. Exit 2."""


def foglie(v):
    """L'insieme multiplo degli scalari raggiungibili. `None` non e' uno scalare."""
    acc = collections.Counter()

    def giu(x):
        if isinstance(x, dict):
            for k in x:
                giu(x[k])
        elif isinstance(x, list):
            for e in x:
                giu(e)
        elif x is not None:
            acc[(type(x).__name__, json.dumps(x, ensure_ascii=False))] += 1
    giu(v)
    return acc


def posizioni(v, nodo, percorso, padre=None, chiave=None):
    """Ogni posizione dell'istanza con il suo nodo di contratto, il padre e la chiave.

    La convenzione dei percorsi e' quella di `null_check.cammina`, e deve restarlo: un elemento
    d'array NON consuma un livello di contratto e aggiunge `[]` al percorso. Se le due
    convenzioni divergessero, le autorizzazioni del validatore non troverebbero piu' la loro
    posizione — ed e' esattamente il caso che `main` rifiuta invece di indovinare.
    """
    yield percorso, padre, chiave, v, nodo
    if isinstance(v, dict):
        for k in list(v):
            fig = nodo.figli.get(k) if nodo is not None else None
            yield from posizioni(v[k], fig, f"{percorso}/{k}" if percorso else k, v, k)
    elif isinstance(v, list):
        for i in range(len(v)):
            yield from posizioni(v[i], nodo, f"{percorso}[]", v, i)


def e_envelope(x):
    return isinstance(x, dict) and "agent" in x and "payload" in x


def puntatore_a_posizione(pack, puntatore):
    """Da JSON-pointer a (padre, chiave, valore). `None` se non risolve."""
    cur, padre, chiave = pack, None, None
    for pezzo in [p for p in puntatore.split("/") if p != ""]:
        padre = cur
        if isinstance(cur, list):
            try:
                chiave = int(pezzo)
                cur = cur[chiave]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if pezzo not in cur:
                return None
            chiave = pezzo
            cur = cur[pezzo]
        else:
            return None
    return padre, chiave, cur


def nodo_di(pack, radice, puntatore):
    """Il nodo di contratto che governa la posizione indicata dal JSON-pointer."""
    n = radice
    for pezzo in [p for p in puntatore.split("/") if p != ""]:
        if pezzo.isdigit():
            continue                       # un indice non consuma un livello di contratto
        if n is None:
            return None
        n = n.figli.get(pezzo)
    return n


# ---- riparazione ------------------------------------------------------------------------

def ripara(pack, radice, illeciti, errori_schema):
    """UN passaggio. Ritorna (pack_nuovo, riparazioni, rifiuti). Non modifica l'originale."""
    nuovo = copy.deepcopy(pack)
    riparazioni, rifiuti = [], []

    # R-C prima di tutto: cambia gli annidamenti, quindi i percorsi delle altre autorizzazioni.
    # Si applica solo quando la radice NON e' un envelope, e in quel caso gli errori sono tutti
    # `required` di radice: non c'e' nulla che le altre regole potrebbero riparare.
    if not e_envelope(nuovo):
        if isinstance(nuovo, dict) and len(nuovo) == 1:
            unico = next(iter(nuovo.values()))
            if e_envelope(unico):
                riparazioni.append({"regola": "R-C", "percorso": "(radice)",
                                    "azione": f"scartato l'involucro «{next(iter(nuovo))}»"})
                return unico, riparazioni, rifiuti
        rifiuti.append({"regola": None, "percorso": "(radice)",
                        "motivo": "envelope assente: sintetizzarlo richiederebbe dati "
                                  "obbligatori che il pack non contiene"})
        return nuovo, riparazioni, rifiuti

    # R-A — solo sulle posizioni che il validatore ha segnalato
    autorizzati = collections.Counter(i["percorso"] for i in illeciti)
    azioni, visti = [], collections.Counter()
    for perc, padre, chiave, v, nodo in posizioni(nuovo, radice, ""):
        etichetta = perc or "(radice)"
        if v is not None or not autorizzati.get(etichetta):
            continue
        visti[etichetta] += 1
        if nodo is None:
            rifiuti.append({"regola": None, "percorso": etichetta,
                            "motivo": "posizione sconosciuta al contratto: non decido"})
        elif nodo.nullable:
            # l'autorizzazione arriva da fuori (`--errori`) e puo' non concordare col contratto:
            # come R-B, anche R-A la confronta invece di eseguirla
            rifiuti.append({"regola": None, "percorso": etichetta,
                            "motivo": "il contratto ammette `null` qui: l'autorizzazione non "
                                      "concorda col contratto, non tocco"})
        elif padre is None or isinstance(padre, list):
            # prima dell'obbligatorieta': per un ELEMENTO la ragione non e' che la sua chiave
            # sia pretesa, ma che toglierlo accorcerebbe l'elenco — e la lunghezza di un elenco
            # e' un dato di merito quanto i suoi valori
            rifiuti.append({"regola": None, "percorso": etichetta,
                            "motivo": "elemento d'array a null: toglierlo cambierebbe la "
                                      "cardinalita' dichiarata"})
        elif nodo.obbligatorio:
            rifiuti.append({"regola": None, "percorso": etichetta,
                            "motivo": "chiave obbligatoria: ometterla toglierebbe un campo "
                                      "che il contratto pretende, riempirla sarebbe inventarlo"})
        else:
            azioni.append(("R-A", padre, chiave, etichetta, None))

    mancate = [p for p, n in autorizzati.items() if visti.get(p, 0) < n]
    if mancate:
        raise Guasto(f"autorizzazione senza posizione: {mancate[0]} — la convenzione dei "
                     f"percorsi del riparatore e quella del validatore non coincidono")

    # R-B — un valore singolo dove il contratto vuole un array
    for e in errori_schema:
        if e.get("keyword") != "type" or "of type 'array'" not in e.get("messaggio", ""):
            continue
        pos = puntatore_a_posizione(nuovo, e["puntatore"])
        nodo = nodo_di(nuovo, radice, e["puntatore"])
        pezzi = [p for p in e["puntatore"].split("/") if p != ""]
        if pos is None or nodo is None or not nodo.tipo.startswith("a<"):
            rifiuti.append({"regola": None, "percorso": e["puntatore"],
                            "motivo": "il contratto non dichiara un array in questa posizione"})
            continue
        if pezzi and pezzi[-1].isdigit():
            # `nodo_di` salta gli indici, quindi un puntatore a ELEMENTO eredita il nodo
            # dell'array: senza questa guardia si avvolgerebbe l'elemento invece dell'elenco
            rifiuti.append({"regola": None, "percorso": e["puntatore"],
                            "motivo": "il puntatore indica un elemento, non l'elenco"})
            continue
        padre, chiave, v = pos
        if isinstance(v, list):
            rifiuti.append({"regola": None, "percorso": e["puntatore"],
                            "motivo": "e' gia' un array: avvolgerlo lo anniderebbe"})
            continue
        if v is None or padre is None:
            continue
        azioni.append(("R-B", padre, chiave, e["puntatore"], v))

    for regola, padre, chiave, etichetta, v in azioni:
        if regola == "R-A":
            del padre[chiave]
            riparazioni.append({"regola": "R-A", "percorso": etichetta,
                                "azione": f"omessa la chiave `{chiave}`"})
        if regola == "R-B":
            padre[chiave] = [v]
            riparazioni.append({"regola": "R-B", "percorso": etichetta,
                                "azione": "valore avvolto in array, verbatim"})

    # cio' che resta e non e' riparabile, dichiarato con il motivo
    for e in errori_schema:
        k = e.get("keyword")
        if k == "type" and "of type 'array'" in e.get("messaggio", ""):
            continue
        motivo = {
            "required": "dato obbligatorio mancante: inventarlo sarebbe fabbricare una prova",
            "maxLength": "e' una citazione: troncarla la modificherebbe",
            "enum": "richiederebbe di scegliere un valore: sarebbe inventarlo",
            "pattern": "richiederebbe di scegliere un valore: sarebbe inventarlo",
            "anyOf": "richiederebbe di scegliere una forma fra piu' ammesse: sarebbe inventarla",
            "oneOf": "richiederebbe di scegliere una forma fra piu' ammesse: sarebbe inventarla",
            "format": "richiederebbe di riscrivere un valore: sarebbe modificarlo",
            "type": "cambiare il tipo di un dato e' cambiare il dato",
        }.get(k, f"nessuna riparazione formale definita per `{k}`")
        # il messaggio del validatore viaggia con il rifiuto: «manca un dato obbligatorio in
        # /unreachable_sources/0» non dice quale, e chi legge il fascicolo ha bisogno del nome
        rifiuti.append({"regola": None, "percorso": e["puntatore"] or "(radice)",
                        "motivo": motivo, "keyword": k,
                        "dal_validatore": e.get("messaggio", "")})
    return nuovo, riparazioni, rifiuti


# ---- validazione ------------------------------------------------------------------------

def valida(percorso_raw, contratto, schemi):
    """Il verdetto, prodotto dallo STESSO strumento che ha emesso il primo: `replay_validate`."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            codice = replay_validate.main(["--raw", percorso_raw, "--contratto", contratto,
                                           "--schemi", schemi])
    except Exception as e:                                                # noqa: BLE001
        raise Guasto(f"il validatore non ha potuto girare: {type(e).__name__}: {e}")
    if codice == 2:
        motivo = err.getvalue().strip()
        raise Guasto(motivo[len("guasto: "):] if motivo.startswith("guasto: ") else motivo)
    try:
        return json.loads(out.getvalue())
    except json.JSONDecodeError as e:
        raise Guasto(f"rapporto del validatore non deserializzabile: {e}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="un passaggio di riparazione formale del pack")
    ap.add_argument("--raw", required=True, metavar="FILE")
    ap.add_argument("--contratto", required=True, metavar="DELIVERY")
    ap.add_argument("--schemi", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas"))
    ap.add_argument("--errori", metavar="FILE",
                    help="il rapporto del validatore da usare come AUTORIZZAZIONE, invece di "
                         "quello calcolato qui: gli errori esatti, presi da fuori")
    ap.add_argument("--fascicolo", metavar="DIR",
                    help="conserva separatamente raw iniziale, richiesta, pack riparato e "
                         "i due verdetti")
    a = ap.parse_args(argv)
    try:
        with open(a.raw, "rb") as f:
            grezzo = f.read()
        with open(a.contratto, "rb") as f:
            testo_con = f.read().replace(b"\r\n", b"\n").decode("utf-8")
        try:
            radice = null_check.leggi_contratto(testo_con)
        except null_check.ContrattoIlleggibile as e:
            raise Guasto(f"contratto non leggibile: {e}")
        pack = replay_validate.estrai_pack(grezzo.replace(b"\r\n", b"\n").decode("utf-8"))

        prima = valida(a.raw, a.contratto, a.schemi)
        if a.errori:
            with open(a.errori, "rb") as f:
                autorizzazione = json.loads(f.read().decode("utf-8"))
        else:
            autorizzazione = prima

        # L'impronta si prende PRIMA. Se un giorno `ripara` mutasse in luogo invece di lavorare
        # su una copia, `pack` e `riparato` sarebbero lo stesso oggetto e il confronto sotto
        # sarebbe vacuo: un controllo che non puo' fallire non e' un controllo.
        impronta_prima = foglie(pack)
        riparato, riparazioni, rifiuti = ripara(
            pack, radice,
            autorizzazione.get("null_check", {}).get("illeciti", []),
            autorizzazione.get("schema", {}).get("errori", []))

        # IL RIPARATORE SI POLIZIA. Se una riparazione avesse aggiunto o tolto uno scalare, il
        # lavoro si annulla: meglio un pack invalido di un pack alterato.
        a_dopo = foglie(riparato)
        if impronta_prima != a_dopo:
            inventati = list(a_dopo - impronta_prima)[:3]
            persi = list(impronta_prima - a_dopo)[:3]
            raise Guasto(f"invariante dei fatti violato: la riparazione si annulla. "
                         f"inventati={inventati} persi={persi}")

        dest = os.path.join(a.fascicolo, "02-pack-riparato.json") if a.fascicolo else None
        if a.fascicolo:
            os.makedirs(a.fascicolo, exist_ok=True)
            with open(os.path.join(a.fascicolo, "00-raw-iniziale.txt"), "wb") as f:
                f.write(grezzo)
        import tempfile
        temporanea = None
        if dest is None:
            temporanea = tempfile.mkdtemp(prefix="pack-repair-")
            dest = os.path.join(temporanea, "02-pack-riparato.json")
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(riparato, ensure_ascii=False, indent=2) + "\n")
        try:
            dopo = valida(dest, a.contratto, a.schemi)
        finally:
            if temporanea:
                import shutil
                shutil.rmtree(temporanea, ignore_errors=True)
    except Guasto as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2

    rapporto = {
        "strumento": f"pack_repair.py {VERSIONE}",
        "marca": MARCA,
        "passaggi": 1,
        "sha256_raw": hashlib.sha256(grezzo).hexdigest(),
        # NORMALIZZATO nel nome perche' lo e' nel calcolo: il raw si hasha sui byte, il contratto
        # sul testo con i CRLF gia' ridotti. Chiamarli entrambi «sha256_…» inviterebbe a
        # ricalcolarli con lo stesso comando e a trovarli diversi su un checkout CRLF.
        "sha256_contratto_normalizzato": hashlib.sha256(testo_con.encode("utf-8")).hexdigest(),
        "verdetto_prima": prima,
        "riparazioni": riparazioni,
        "rifiuti": rifiuti,
        "invariante_fatti": "verificato",
        "verdetto_dopo": dopo,
        "pack_riparato": riparato,
    }
    if a.fascicolo:
        with open(os.path.join(a.fascicolo, "01-richiesta-riparazione.json"),
                  "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
                "strumento": f"pack_repair.py {VERSIONE}",
                "sha256_raw": rapporto["sha256_raw"],
                "sha256_contratto_normalizzato":
                    rapporto["sha256_contratto_normalizzato"],
                "errori_del_validatore": {
                    "null_check": autorizzazione.get("null_check", {}).get("illeciti", []),
                    "schema": autorizzazione.get("schema", {}).get("errori", [])},
                "regole_ammesse": REGOLE,
                "vietato": "inventare o modificare fatti, fonti, claim_id, source_id, "
                           "quantita', citazioni o valutazioni",
            }, ensure_ascii=False, indent=2) + "\n")
        with open(os.path.join(a.fascicolo, "03-verdetti.json"),
                  "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"prima": prima, "dopo": dopo,
                                "riparazioni": riparazioni, "rifiuti": rifiuti},
                               ensure_ascii=False, indent=2) + "\n")
    sys.stdout.write(json.dumps(rapporto, ensure_ascii=False, indent=2) + "\n")
    return 0 if dopo["verdetto"] == "VALIDO" else 1


if __name__ == "__main__":
    sys.exit(main())
