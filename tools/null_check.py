#!/usr/bin/env python3
"""
null_check.py — rifiuta i `null` che il contratto non ammette, prima della validazione.

PERCHE' ESISTE (leggere prima di modificarlo).

La regola «opzionale non significa nullable: un campo opzionale si OMETTE» e' scritta nel prompt
di `evidence-researcher` dal commit `ae34883`, nomina per nome il campo colpevole
(`spend_evidence[].amount`), ed e' guardata da una prova verde (`E15`). Il replay `20260823-0211`
l'ha violata cinque volte; il replay `20260823-1647` una volta ancora, su quel campo esatto.
La micro-slice `M3.3` ha aggiunto un GESTO — «prima di emettere, cerca `null` nel tuo JSON» — e
la sua ricevuta ha registrato, senza attenuanti, che un'istruzione di auto-verifica rivolta a un
modello non e' una difesa ma una speranza: non ha un momento in cui qualcuno la esegue e nessun
altro puo' saltarla, e non produce un esito osservabile.

Questo file e' quel momento. Non chiede all'agente di controllarsi: controlla l'artefatto.

COSA FA, ed e' tutto. Cammina il JSON emesso, trova ogni `null`, e per ciascuno chiede al
CONTRATTO GIA' GENERATO se quella posizione lo ammette. Il contratto ammette `null` in due forme
sole, ed entrambe sono esplicite: la tilde `~` in coda al tipo (`quantity o~`) e la `z` fra le
alternative del tipo (`value n|z`). Tutto il resto e' rifiutato, compreso il `null` su una
posizione che il contratto non conosce affatto.

COSA NON FA, ed e' deliberato. Non valida contro lo schema: quello e' un altro passo, e questo
controllo gli sta PRIMA perche' un `null` illecito e' un difetto di forma che si diagnostica
meglio da solo che dentro un elenco di errori di schema. Non legge gli schemi: legge il
contratto, che dagli schemi e' gia' derivato — leggerli di nuovo sarebbe una seconda fonte. Non
corregge nulla: rifiuta e nomina. Non tocca la rete e non importa `jsonschema`.

  uso:  python tools/null_check.py --contratto <delivery.txt> --pack <file.json|->
        exit 0 = nessun `null` illecito
             1 = `null` illeciti, elencati per percorso d'istanza su stdout
             2 = il contratto non e' leggibile, o l'uso e' errato

I due codici 1 e 2 sono distinti perche' «il pack e' sbagliato» e «il contratto e' rotto» si
riparano in posti diversi.
"""
import argparse, json, os, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

VERSIONE = "v1.0.0"
INTESTAZIONE_ALBERO = "== ALBERO"


class ContrattoIlleggibile(Exception):
    """Il contratto non si lascia leggere. Exit 2."""


class Nodo:
    """Una posizione del contratto: se ammette `null`, e i suoi figli per nome."""

    __slots__ = ("nome", "tipo", "nullable", "obbligatorio", "figli")

    def __init__(self, nome, tipo, obbligatorio=False):
        self.nome, self.tipo = nome, tipo
        self.nullable = ammette_null(tipo)
        # Il `!` del contratto. `null_check` non lo usa: distinguere una chiave omessa da una
        # chiave a `null` non e' affar suo. Lo usa chi deve decidere se una chiave si puo'
        # TOGLIERE, e quel lettore non deve avere un secondo parser del contratto.
        self.obbligatorio = obbligatorio
        self.figli = {}


def ammette_null(tipo):
    """Le due sole forme con cui il contratto dichiara che `null` e' legittimo.

    `o~`     — la tilde in coda: l'intero valore puo' essere null (es. `quantity o~`)
    `n|z`    — la `z` fra le alternative del tipo (es. `value n|z`, `currency s|z`)

    Tutto il resto e' no. In particolare `a<s>` NON ammette null ne' per l'array ne' per i suoi
    elementi.

    La forma `a<s|z>` — array i cui ELEMENTI possono essere null — non esiste oggi nel corpus, e
    questo controllo la RIFIUTA invece di indovinarla. Il motivo e' che array ed elemento
    condividono una sola riga di contratto, quindi un solo bit di nullabilita': trattare quel bit
    come se valesse per entrambi renderebbe nullable anche l'array, trattarlo come se valesse per
    il solo elemento renderebbe silenziosamente ammesso `campo: null`. Non c'e' una lettura giusta
    da scegliere, e sceglierne una a caso e' il modo di sbagliare senza accorgersene. Se la forma
    comparira', qui si fermera' tutto — che e' l'esito voluto.
    """
    if not tipo:
        return False
    # L'INTERNO DELLE PARENTESI SI GUARDA PER PRIMO. La nullabilita' si scrive in DUE grafie,
    # perche' due sono le forme di schema che la producono: `type: ["string","null"]` diventa
    # `s|z`, e `anyOf: [{X}, {"type":"null"}]` diventa `X~`. Una prima stesura di questo file
    # controllava la tilde PRIMA di guardare dentro `a<...>`, e cosi' fermava `a<s|z>` ma
    # lasciava passare `a<o~>` in silenzio, come se l'array non fosse nullable — rifiutando a
    # torto i null dei suoi elementi e stampando come motivo «il contratto non dichiara `~`»
    # accanto a un tipo che la tilde ce l'ha. Le due grafie vanno intercettate insieme o il
    # fermo dichiarato qui sotto non e' un fermo.
    dentro = tipo[tipo.index("<") + 1:tipo.rindex(">")] if "<" in tipo and ">" in tipo else None
    if dentro is not None:
        if "z" in dentro.split("|") or dentro.endswith("~"):
            raise ContrattoIlleggibile(
                f"tipo «{tipo}»: un array con elementi nullable non e' implementato. Array ed "
                "elemento condividono una riga sola, e un solo bit di nullabilita' non basta a "
                "dire quale dei due ammette null")
        return False                      # la nullabilita' dell'elemento non e' quella dell'array
    if tipo.endswith("~"):
        return True
    return "z" in tipo.split("|")


def leggi_contratto(testo):
    """L'albero delle posizioni, dalla sezione ALBERO della vista delivery.

    Il rientro e' l'annidamento, uno spazio per livello. Un array e il suo elemento sono la
    STESSA riga (`a<o>`), quindi i figli di un elemento d'array sono figli della riga dell'array:
    e' la ragione per cui scendere in un indice non consuma un livello di contratto.
    """
    righe = []
    dentro = False
    for riga in testo.split("\n"):
        if riga.startswith(INTESTAZIONE_ALBERO):
            dentro = True
            continue
        if dentro:
            if riga.startswith("== "):
                break
            if riga.strip():
                righe.append(riga)
    if not righe:
        raise ContrattoIlleggibile("nessuna sezione ALBERO nel contratto")
    radice, pila = None, []
    for riga in righe:
        prof = len(riga) - len(riga.lstrip(" "))
        pezzi = riga.strip().split()
        nome = pezzi[0].lstrip("!")
        tipo = pezzi[1] if len(pezzi) > 1 else ""
        n = Nodo(nome, tipo, obbligatorio=pezzi[0].startswith("!"))
        if prof == 0:
            if radice is not None:
                raise ContrattoIlleggibile("due radici nell'albero")
            radice, pila = n, [n]
            continue
        if radice is None:
            raise ContrattoIlleggibile("l'albero non comincia dalla radice")
        if prof > len(pila):
            raise ContrattoIlleggibile(f"salto di rientro alla riga «{riga.strip()}»")
        del pila[prof:]
        pila[-1].figli[nome] = n
        pila.append(n)
    return radice


def controlla(valore, radice):
    """(esito, elenco). L'elenco e' in ordine di documento e riporta OGNI null illecito.

    Fermarsi al primo obbligherebbe a un secondo giro per vedere il secondo, e un pack ne porta
    piu' d'uno per volta: il replay 20260823-1647 ne aveva due, su due canali diversi.

    ITERATIVA, e non e' una preferenza di stile.

    La versione ricorsiva cedeva con `RecursionError` a profondita' 996, mentre `json.loads`
    accetta fino a 2998: c'era una fascia di circa duemila livelli in cui il pack si parsava e
    la camminata cadeva, uscendo `1` — che in questa catena significa «null illeciti trovati»,
    cioe' una MISURA — senza aver controllato nulla. Il guasto si travestiva da misura.

    NESSUNA SOGLIA DI PROFONDITA', e nessuna fonte potrebbe darne una: il contratto non limita
    l'istanza, perche' «posizione non nel contratto» e' un rilievo da riportare e non un motivo
    per fermarsi; `evidence-pack.schema.json` non porta alcuna occorrenza di
    `additionalProperties`, quindi il default permissivo lascia passare sottoalberi liberi; e
    questo controllo gira PRIMA della validazione di schema, quindi il suo ingresso non e'
    nemmeno garantito conforme. Un tetto esiste gia' a monte e non e' nostro: `json.loads`
    rifiuta oltre 2998, e di li' non si passa.

    Il precedente e' in casa: `ds_validate._cammina_schemi` e' gia' una DFS iterativa, nata
    dallo stesso difetto.
    """
    fuori = []

    # In profondita', pre-ordine: i figli si impilano IN ORDINE INVERSO perche' la pila li
    # restituisca in ordine di documento. L'ordine di `fuori` dipende solo dall'ordine di
    # visita, quindi cosi' e' esattamente quello di prima.
    pila = [(valore, radice, "")]
    while pila:
        v, nodo, percorso = pila.pop()
        if v is None:
            dove = percorso or "(radice)"
            if nodo is None:
                fuori.append({"percorso": dove, "motivo": "posizione non nel contratto",
                              "tipo_dichiarato": None})
            elif not nodo.nullable:
                fuori.append({"percorso": dove,
                              "motivo": "il contratto non dichiara `~` ne' `z`",
                              "tipo_dichiarato": nodo.tipo})
            continue
        if isinstance(v, dict):
            for k in reversed(list(v)):
                p = f"{percorso}/{k}" if percorso else k
                pila.append((v[k], nodo.figli.get(k) if nodo else None, p))
        elif isinstance(v, list):
            # L'elemento NON consuma un livello di contratto: `a<o>` fonde array ed elemento.
            for sub in reversed(v):
                pila.append((sub, nodo, f"{percorso}[]"))

    return (not fuori), fuori


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="rifiuta i null che il contratto non ammette, prima della validazione")
    ap.add_argument("--contratto", required=True, metavar="DELIVERY",
                    help="la vista delivery gia' generata da contract_compile.py")
    ap.add_argument("--pack", required=True, metavar="JSON",
                    help="l'artefatto da controllare, oppure «-» per lo standard input")
    a = ap.parse_args(argv)
    try:
        with open(a.contratto, "rb") as f:
            testo = f.read().replace(b"\r\n", b"\n").decode("utf-8")
        radice = leggi_contratto(testo)
    except (OSError, UnicodeDecodeError) as e:
        print(f"contratto non leggibile: {e}", file=sys.stderr)
        return 2
    except ContrattoIlleggibile as e:
        print(f"contratto non leggibile: {e}", file=sys.stderr)
        return 2
    try:
        if a.pack == "-":
            grezzo = sys.stdin.read()
        else:
            with open(a.pack, "rb") as f:
                grezzo = f.read().decode("utf-8")
        pack = json.loads(grezzo)
    except RecursionError:
        # CIRCOSCRITTA AL PARSING, e deliberatamente non piu' larga. Oltre 2998 livelli
        # `json.loads` rinuncia, e non aver potuto LEGGERE il pack e' un guasto — vale `2`,
        # non `1`, che qui significherebbe «null illeciti trovati» su un pack mai controllato.
        # Non si avvolge `controlla`: da li' un `RecursionError` non nasce piu', e catturarlo
        # anche li' maschererebbe un difetto invece di riportarlo.
        print("pack non leggibile: profondita' oltre il limite del parser JSON — "
              "l'annidamento supera cio' che l'interprete accetta di deserializzare",
              file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"pack non leggibile: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    ok, elenco = controlla(pack, radice)
    if ok:
        return 0
    print(f"null illeciti: {len(elenco)}")
    for e in elenco:
        t = f" (contratto: {e['tipo_dichiarato']})" if e["tipo_dichiarato"] else ""
        print(f"  {e['percorso']}  — {e['motivo']}{t}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
