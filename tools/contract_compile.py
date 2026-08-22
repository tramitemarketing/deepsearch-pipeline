#!/usr/bin/env python3
"""
contract_compile.py — genera dagli schemi JSON il contratto strutturale consegnato a un agente.

PERCHE' ESISTE (leggere prima di modificarlo).

I prompt di questa pipeline duplicavano a mano il contratto che gli schemi gia' dichiarano, e la
copia non convergeva. Misurato sul commit fd7ff267: 732 obblighi `required` d'istanza nei dodici
schemi d'artefatto, dei quali il 47% fuori dal payload; 406 posizioni di proprieta' su 1348
nominate dal prompt del proprio agente anche una volta sola; 75 posizioni `enum` su 194 insegnate
per intero; 0 `pattern` su 49 riprodotti verbatim. Il primo canary e il replay P-003 hanno
esercitato cinque canali ciascuno e in nessuno dei dieci casi l'evidence pack e' stato valido al
primo tentativo: 97 dei 117 errori del replay hanno `required` come parola chiave violata, e zero
sono violazioni d'`enum`. Il difetto non e' che i modelli sbaglino i vocabolari: e' che nessuno
insegna loro i campi obbligatori annidati.

Un prompt scritto a mano insegna narrando, e la narrazione porta con se' i propri controesempi:
`.claude/agents/evidence-researcher.md` contiene `1xT3` una volta sola, alla riga 191, dentro il
racconto di un difetto passato — e il replay ha emesso `1xT3` due volte. Un contratto generato non
ha quella forma. Non e' pero' privo di narrazione, e va detto: le `description` degli schemi la
contengono a loro volta, `evidence-pack.schema.json:50` compresa. Questo file non le corregge e non
le giudica: le riporta verbatim, marcate come non verificate dal validatore.

COSA NON FA, ed e' deliberato. Non ha semantica di dominio: non sa che cosa sia una promozione o un
tier, e non deve saperlo. Non valida artefatti — quello e' il livello L1, che vive altrove. Non
verifica la provenienza — quello e' provenance_check. Non integra il contratto nel prompt — quello
e' l'orchestratore. Non importa jsonschema ne' referencing, perche' su POSIX il primo e' alla 4.10.3
senza il parametro `registry=` e il secondo e' assente: cammina lo schema a mano, con json.load e
ricorsione pura, e resta eseguibile su entrambe le piattaforme.

  uso:  python tools/contract_compile.py --agente evidence-researcher
        python tools/contract_compile.py --agente evidence-researcher --out <percorso>
        python tools/contract_compile.py --agente evidence-researcher --schemi <dir>
        python tools/contract_compile.py --agente evidence-researcher --commit <sha>
        python tools/contract_compile.py --agente evidence-researcher --vista {audit,delivery}
        exit 0 = contratto generato
             1 = errore di risoluzione   («gli schemi sono rotti»)
             2 = costrutto non compilabile («il compilatore va esteso»)

I due codici sono distinti perche' confonderli fa aggiustare la cosa sbagliata.

DUE VISTE, UNA COMPILAZIONE (M1.1). La vista `audit` e' il contratto completo, persistito per-run
e NON consegnato al modello. La vista `delivery` e' la forma compatta C5d — albero rientrato, array
fusi, opzionale implicito, nessuna `description` — ed e' l'unica che entra nel contesto del
modello. Entrambe derivano dalla stessa lista di `Voce` di `cammina()`: nessuna seconda tabella
manuale. La delivery porta come prima riga dati l'`sha256` della audit generata dagli stessi input
nella stessa invocazione: e' il rinvio alla prosa, verificato da D3 e da provenance_check in M2.
La vista audit dichiara il tipo della radice in testata (D12), cosa che M1 non faceva.
"""
import argparse, hashlib, json, os, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '·' e '←' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

VERSIONE = "v1.0.0"
LIMITE_PROFONDITA = 20                     # il massimo del corpus e' 5: quattro volte il reale

SCHEMA_PER_AGENTE = {"evidence-researcher": "evidence-pack.schema.json"}

ASSERTIVE = ("type", "enum", "pattern", "const", "format", "maxLength", "minLength",
             "minimum", "maximum", "minItems", "maxItems", "additionalProperties", "uniqueItems")
METADATI = ("$schema", "$id", "title", "$comment", "$defs", "definitions", "examples", "default")
STRUTTURALI = ("$ref", "allOf", "anyOf", "oneOf", "if", "then", "else",
               "required", "properties", "items", "description")

# La difesa e' una LISTA DI AMMISSIONE, non di negazione. Una lista di negazione — «rifiuta
# dependentRequired, prefixItems, patternProperties» — lascia passare in silenzio tutto cio' che
# chi scrive non ha previsto: `contains`, `propertyNames`, un `additionalProperties` che sia uno
# schema invece di un booleano, un `items` in forma di tupla. Nessuna di queste forme e' oggi nei
# tredici schemi, e per questo il rischio e' latente e non attuale; ma l'obbligo e' «fallire in
# modo esplicito su forme non implementate», e una lista di negazione non lo soddisfa. Aggiungere
# una parola chiave qui e' il gesto deliberato con cui si dichiara di saperla rendere.
AMMESSE = frozenset(ASSERTIVE + METADATI + STRUTTURALI)


class ErroreRisoluzione(Exception):
    """Gli schemi sono rotti: $ref irrisolvibile, puntatore rotto, ciclo, fuori radice, BOM."""


class CostruttoNonCompilabile(Exception):
    """Il compilatore va esteso: una forma di JSON Schema che questa versione non sa rendere."""


def leggi_normalizzato(percorso):
    """UTF-8 esplicito, CRLF → LF, BOM RIFIUTATO e non ignorato.

    La normalizzazione decide l'hash: senza, un checkout CRLF e uno LF darebbero due contratti
    diversi dagli stessi schemi. Qui i due file letti sono LF puri e i due digest coincidono; la
    regola esiste per M4, dove entrano i nove schemi CRLF su tredici, e per un clone configurato
    diversamente da questo (core.autocrlf=false, nessun .gitattributes).
    """
    b = open(percorso, "rb").read()
    if b[:3] == b"\xef\xbb\xbf":
        raise ErroreRisoluzione(f"BOM in {os.path.basename(percorso)}: rifiutato, non ignorato")
    return b.replace(b"\r\n", b"\n")


def ramo_non_null(rami):
    """L'unica forma disgiuntiva compilabile: [X, {"type": "null"}], in qualunque ordine."""
    if not isinstance(rami, list) or len(rami) != 2:
        return None
    a, b = rami
    if isinstance(b, dict) and b.get("type") == "null":
        return a
    if isinstance(a, dict) and a.get("type") == "null":
        return b
    return None


class Fuso:
    """Le parole chiave che valgono in UNA posizione d'istanza.

    `prop` e `items` tengono LISTE di sottoschemi applicabili, non un solo sottoschema, e ciascuno
    porta il proprio file di provenienza. Le due cose insieme sono la correttezza:

    - la LISTA, perche' due schemi possono dichiarare la stessa proprieta' e valgono entrambi.
      `Envelope.properties.payload` e' `{"type":"object"}` e `evidence-pack.properties.payload`
      porta l'intero albero: tenerne uno solo perde meta' del contratto in silenzio;
    - il FILE, perche' un `#/$defs/X` scritto dentro core.schema.json si risolve contro core e non
      contro lo schema da cui e' partita la discesa.
    """

    def __init__(self):
        self.kw, self.prop, self.items, self.req, self.nullable = {}, {}, [], [], False

    def descrizione(self):
        d = self.kw.get("description")
        return " ".join(d.split()) if isinstance(d, str) else None


class Compilatore:
    def __init__(self, dir_schemi, commit="non-dichiarato"):
        self.dir = os.path.normcase(os.path.realpath(dir_schemi))
        self.commit = commit
        self.doc, self.hash = {}, {}

    # -------------------------------------------------------------- lettura

    def carica(self, nome):
        """Il documento di uno schema. La cache tiene il NODO SORGENTE, mai un risultato risolto:
        una cache dell'espansione fonderebbe `claims/items` e `contradictory_evidence/items`, che
        risolvono lo stesso `Claim` ma hanno contratti diversi perche' il primo ha fratelli."""
        if nome in self.doc:
            return self.doc[nome]
        if os.path.basename(nome) != nome:
            raise ErroreRisoluzione(f"fuori radice: {nome}")
        p = os.path.realpath(os.path.join(self.dir, nome))
        try:
            comune = os.path.commonpath([self.dir, os.path.normcase(p)])
        except ValueError:                      # unita' diverse su Windows: C: contro D:
            raise ErroreRisoluzione(f"percorso su un altro volume: {nome}")
        if comune != self.dir:
            raise ErroreRisoluzione(f"fuori radice: {nome}")
        if not os.path.exists(p):
            raise ErroreRisoluzione(f"schema assente: {nome}")
        b = leggi_normalizzato(p)
        self.doc[nome] = json.loads(b.decode("utf-8"))
        self.hash[nome] = hashlib.sha256(b).hexdigest()
        return self.doc[nome]

    def risolvi(self, ref, corrente):
        if ref.startswith("#"):
            file_, frammento = corrente, ref[1:]
        else:
            f, _, frammento = ref.partition("#")
            if os.path.basename(f) != f:        # ../altrove.json: rifiutato PRIMA di aprirlo
                raise ErroreRisoluzione(f"fuori radice: {f}")
            file_ = f
        n = self.carica(file_)
        for seg in [x for x in frammento.split("/") if x]:
            seg = seg.replace("~1", "/").replace("~0", "~")
            if not isinstance(n, dict) or seg not in n:
                raise ErroreRisoluzione(f"puntatore rotto: {ref} (segmento «{seg}»)")
            n = n[seg]
        return n, file_

    # -------------------------------------------------------------- fusione

    def fondi_molti(self, applicabili, catena):
        """Fonde l'insieme degli schemi applicabili a una posizione, in ordine di documento."""
        f, cond = Fuso(), []
        for nodo, file_ in applicabili:
            g, c = self.fondi(nodo, file_, catena)
            self._somma(f, g)
            cond += c
        return f, cond

    def fondi(self, nodo, corrente, catena):
        """Un solo nodo, espandendo `$ref` e appiattendo `allOf`.

        L'ordine e' quello delle chiavi del documento, con UNA eccezione dichiarata: il `$ref` e'
        elaborato per ULTIMO, cosi' che i `required` del fratello precedano quelli del referente.
        Senza l'eccezione `claims/items` — le cui chiavi sono ["$ref","properties","required"] —
        darebbe [id, statement, state, source_ids, promoted_by] invece di
        [promoted_by, id, statement, state, source_ids].

        Nella radice di evidence-pack, invece, `allOf` precede `properties` nel documento e nessuna
        eccezione si applica: l'envelope precede `payload`, che resta il sesto dei dieci obblighi.
        """
        f, cond = Fuso(), []
        if isinstance(nodo, bool):
            raise CostruttoNonCompilabile("sottoschema booleano: forma non implementata")
        if not isinstance(nodo, dict):
            return f, cond
        for k in nodo:
            if k not in AMMESSE:
                raise CostruttoNonCompilabile(f"{k}: parola chiave non implementata")
        if "items" in nodo and isinstance(nodo["items"], list):
            raise CostruttoNonCompilabile("items in forma di tupla: forma non implementata")
        if "additionalProperties" in nodo and not isinstance(nodo["additionalProperties"], bool):
            raise CostruttoNonCompilabile(
                "additionalProperties come schema: solo il booleano e' implementato")
        for k, v in nodo.items():
            if k in ("$ref", "then", "else") or k in METADATI:
                continue                        # $ref in coda; then/else vivono nel condizionale
            if k == "allOf":
                for ramo in v:
                    g, c = self.fondi(ramo, corrente, catena)
                    self._somma(f, g)
                    cond += c
            elif k in ("anyOf", "oneOf"):
                r = ramo_non_null(v)
                if r is None:
                    raise CostruttoNonCompilabile(
                        f"{k} non riducibile a [X, null]: la disgiunzione generale non e' resa")
                f.nullable = True
                g, c = self.fondi(r, corrente, catena)
                g.req = []                      # sotto una disgiunzione i required NON sono
                self._somma(f, g)               # congiuntivi: valgono solo nel ramo non-null
                cond += c
            elif k == "if":
                cond.append(self._condizionale(nodo))
            elif k == "required":
                f.req = list(dict.fromkeys(f.req + list(v)))
            elif k == "properties":
                for nm, sub in v.items():
                    f.prop.setdefault(nm, []).append((sub, corrente))
            elif k == "items":
                f.items.append((v, corrente))
            else:
                f.kw.setdefault(k, v)
        if "$ref" in nodo:
            chiave = (corrente, nodo["$ref"])
            if chiave in catena:
                catena_ = " → ".join(f"{a}{b}" for a, b in catena + [chiave])
                raise ErroreRisoluzione(f"ciclo di $ref: {catena_}")
            t, c2 = self.risolvi(nodo["$ref"], corrente)
            base, cb = self.fondi(t, c2, catena + [chiave])
            self._somma(f, base, referente=True)
            cond += cb
        return f, cond

    @staticmethod
    def _somma(f, g, referente=False):
        f.req = list(dict.fromkeys(f.req + g.req))
        for nm, lista in g.prop.items():
            f.prop.setdefault(nm, []).extend(lista)
        f.items += g.items
        f.nullable = f.nullable or g.nullable
        for k, v in g.kw.items():
            if referente and k in ASSERTIVE and k in f.kw and f.kw[k] != v:
                # In Draft 2020-12 $ref e' un applicatore ordinario: il vincolo effettivo e'
                # l'INTERSEZIONE dei due, non la prevalenza di uno. L'intersezione non e'
                # implementata, e fingere una prevalenza produrrebbe un contratto falso in
                # silenzio invece di un rifiuto. Oggi il caso non si presenta: nel corpus le
                # posizioni con fratelli sono due, e quelle con fratelli assertivi sono zero.
                raise CostruttoNonCompilabile(
                    f"collisione sulla parola chiave assertiva «{k}» fra il fratello di $ref e il "
                    "referente: il vincolo effettivo e' l'intersezione, non implementata")
            f.kw.setdefault(k, v)

    @staticmethod
    def _condizionale(nodo):
        """Rende un if/then/else. Il `const` della guardia resta QUI e non entra mai nella riga del
        campo: dei due `const` del corpus uno solo e' un vincolo, e l'altro — questo — e' la
        guardia dell'if di Claim. Raccoglierlo insegnerebbe che `state` deve sempre valere
        VERIFIED, che e' falso, e sarebbe l'unico `const` che questo agente incontra."""
        g = nodo["if"]
        guardie = list((g.get("properties") or {}).items())
        if len(guardie) != 1:
            # Con due proprieta' nella guardia, tenerne una sola renderebbe una condizione FALSA:
            # il contratto direbbe «vietato quando why ∉ [...]» ignorando il secondo campo.
            raise CostruttoNonCompilabile(
                f"if con {len(guardie)} proprieta' nella guardia: solo una e' implementata")
        campo, sub = guardie[0]
        valori = sub.get("enum") or ([sub["const"]] if "const" in sub else None)
        if valori is None:
            raise CostruttoNonCompilabile("if con guardia non riducibile a enum o const")
        for ramo in ("then", "else"):
            r = nodo.get(ramo) or {}
            extra = set(r) - {"required", "description", "not"}
            if extra:
                raise CostruttoNonCompilabile(
                    f"{ramo} con vincoli oltre `required`: {sorted(extra)} non implementati")
            if "not" in r and set(r["not"]) - {"required"}:
                raise CostruttoNonCompilabile(f"{ramo}/not oltre `required`: non implementato")
        vietati = ((nodo.get("else") or {}).get("not") or {}).get("required") or []
        return {"campo": campo, "valori": valori,
                "allora": (nodo.get("then") or {}).get("required") or [],
                "vietati": vietati,
                "prosa": [d for d in ((nodo.get("then") or {}).get("description"),
                                      (nodo.get("else") or {}).get("description")) if d]}


class Voce:
    """Una posizione d'istanza, con le sue due zone: ASSERITO derivato dalle sole parole chiave,
    PROSA con la description verbatim e sempre marcata come non verificata.

    L'inclusione e' UNIDIREZIONALE, e va detto: nulla di cio' che sta in PROSA e' applicato dal
    validatore — vero per costruzione, `description` non e' una parola chiave di validazione — ma
    ASSERITO non esaurisce cio' che il validatore applica, perche' `minimum: 0` e `minItems: 0`
    sono omessi in quanto banali.
    """

    def __init__(self, percorso, fuso, obbligatorio, elemento=False, tipo_elemento=None):
        self.percorso, self.f, self.obbligatorio = percorso, fuso, obbligatorio
        self.elemento, self.tipo_elemento = elemento, tipo_elemento

    def descrizione(self):
        return self.f.descrizione()

    def asserito(self):
        kw = self.f.kw
        # «obbligatorio» e «opzionale» parlano di campi. Una posizione d'ELEMENTO d'array non e'
        # ne' l'uno ne' l'altro: e' cio' che ogni elemento deve essere. Chiamarla «obbligatorio»
        # sotto un titolo «Campi opzionali» e' l'ambiguita' che questo contratto esiste per
        # togliere, e per giunta usa lo stesso token con due significati diversi.
        v = ["ogni elemento" if self.elemento
             else ("obbligatorio" if self.obbligatorio else "opzionale")]
        t = kw.get("type")
        if t is not None:
            tt = "|".join(t) if isinstance(t, list) else t
            if tt == "array":
                # I tipi di contenitore vanno SEMPRE mostrati col tipo degli elementi: nel replay
                # `coverage.gaps` — array di stringhe, required, senza enum ne' pattern — e' stato
                # scritto come stringa narrativa in tre canali su cinque. Il tipo si legge
                # dall'elemento FUSO e non dal nodo grezzo: quando `items` e' un `$ref` il nodo
                # grezzo non ha `type`, e la riga resterebbe «array» secco su cinque array
                # portanti su sei.
                tt = f"array di {self.tipo_elemento}" if self.tipo_elemento else "array"
            v.append(tt)
        if self.f.nullable:
            v.append("nullable")
        if "enum" in kw:
            v.append("enum [" + " · ".join(json.dumps(x, ensure_ascii=False)
                                           for x in kw["enum"]) + "]")
        if "pattern" in kw:
            v.append("pattern " + kw["pattern"])
        if "const" in kw:
            v.append("const " + json.dumps(kw["const"], ensure_ascii=False))
        if "format" in kw:
            v.append("format " + kw["format"])
        if "maxLength" in kw:
            v.append(f"maxLength {kw['maxLength']}")
        for k in ("minItems", "maxItems", "minimum", "maximum"):
            if k in kw and not (k in ("minItems", "minimum") and kw[k] == 0):
                v.append(f"{k} {kw[k]}")
        if kw.get("additionalProperties") is False:
            v.append("nessun campo extra")
        return " · ".join(v)


def cammina(c, applicabili, percorso, prof, catena, voci, cond, obbligatorio=True,
            elemento=False):
    """L'albero delle ISTANZE, non l'albero dei file.

    La profondita' conta i livelli d'istanza — gli array sono trasparenti — e non i nodi
    attraversati: scendere in un `allOf` o in un `$ref` non avvicina il limite.
    """
    if prof > LIMITE_PROFONDITA:
        raise ErroreRisoluzione(f"profondita' d'istanza oltre {LIMITE_PROFONDITA}: {percorso}")
    f, cc_ = c.fondi_molti(applicabili, catena)
    # Il tipo dell'elemento si legge dall'elemento FUSO, non dal nodo grezzo: quando `items` e' un
    # `$ref` il grezzo non ha `type` (vedi Voce.asserito).
    tipo_el = None
    if f.items:
        el, _ = c.fondi_molti(f.items, catena)
        te = el.kw.get("type")
        tipo_el = "|".join(te) if isinstance(te, list) else te
    voci.append(Voce(percorso, f, obbligatorio, elemento, tipo_el))
    for x in cc_:
        cond.append((percorso, x))
    for nome, lista in f.prop.items():
        p = f"{percorso}/{nome}" if percorso else nome
        cammina(c, lista, p, prof + 1, catena, voci, cond, nome in f.req)
    if f.items:
        cammina(c, f.items, percorso + "[]", prof, catena, voci, cond, True, elemento=True)
    return voci, cond


def compila_contratto(agente, dir_schemi=None, commit="non-dichiarato"):
    if agente not in SCHEMA_PER_AGENTE:
        raise CostruttoNonCompilabile(
            f"agente fuori dal perimetro di M1: {agente} (previsto solo evidence-researcher)")
    if dir_schemi is None:
        dir_schemi = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "schemas")
    nome = SCHEMA_PER_AGENTE[agente]
    c = Compilatore(dir_schemi, commit)
    voci, cond = cammina(c, [(c.carica(nome), nome)], "", 0, [], [], [])
    return rendi(agente, nome, c, voci, cond)


def compila_viste(agente, dir_schemi=None, commit="non-dichiarato"):
    """UNA sola `cammina()`, due viste: la audit completa e la delivery compatta, collegate.

    La delivery incolla l'`sha256` della audit prodotta dagli stessi input in questa stessa
    invocazione. E' il legame verificabile fra le due viste (D3): chi legge la delivery sa quale
    audit contiene la prosa, e una mutazione di schema cambia i due hash insieme.
    """
    if agente not in SCHEMA_PER_AGENTE:
        raise CostruttoNonCompilabile(
            f"agente fuori dal perimetro di M1: {agente} (previsto solo evidence-researcher)")
    if dir_schemi is None:
        dir_schemi = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "schemas")
    nome = SCHEMA_PER_AGENTE[agente]
    c = Compilatore(dir_schemi, commit)
    voci, cond = cammina(c, [(c.carica(nome), nome)], "", 0, [], [], [])
    audit = rendi(agente, nome, c, voci, cond)
    sha_audit = hashlib.sha256(audit.encode("utf-8")).hexdigest()
    return audit, rendi_delivery(agente, nome, voci, cond, sha_audit)


# ------------------------------------------------------------------ resa

def riga(v):
    out = [f"`{v.percorso}`   ASSERITO   {v.asserito()}"]
    if v.descrizione():
        out.append(f"            PROSA      «{v.descrizione()}»")
        out.append("                       ⚠ non verificata dal validatore")
    return out


# ------------------------------------------------------------------ vista delivery (C5d)

TIPO_SIGLA = {"string": "s", "integer": "i", "number": "n", "boolean": "b",
              "object": "o", "array": "a", "null": "z"}


def _sigla_tipo(t):
    """Il tipo nella sigla della delivery: `["integer", "null"]` → `i|z`."""
    parti = t if isinstance(t, list) else t.split("|")
    return "|".join(TIPO_SIGLA[x] for x in parti)


def _gettoni_delivery(v, tabE, tabP):
    """I gettoni di vincolo di una posizione, nell'ordine fisso: tipo, E#, P#, limite, format."""
    kw = v.f.kw
    out = []
    t = kw.get("type")
    if t is not None:
        out.append(_sigla_tipo(t) + ("~" if v.f.nullable else ""))
    elif v.f.nullable:
        out.append("~")
    if "enum" in kw:
        out.append("E%d" % tabE[tuple(kw["enum"])])
    if "pattern" in kw:
        out.append("P%d" % tabP[kw["pattern"]])
    if "maxLength" in kw:
        out.append("<%d" % kw["maxLength"])
    if "format" in kw:
        out.append("@%s" % kw["format"])
    return out


def _vincoli_extra_delivery(v):
    """Le parole chiave assertive che la delivery NON sa rendere: la loro presenza e' un rifiuto
    nominato, mai un'omissione silenziosa. I banali (`minItems`/`minimum`/`minLength` = 0) sono
    omessi anche dalla vista audit e non contano."""
    out = []
    for k in ("const", "maxItems", "minLength", "minimum", "maximum", "minItems",
              "additionalProperties"):
        if k in v.f.kw and not (k in ("minItems", "minimum", "minLength") and v.f.kw[k] == 0):
            out.append(k)
    return out


def rendi_delivery(agente, nome, voci, cond, sha_audit):
    """La vista delivery: albero rientrato, array fusi, opzionale implicito, zero prosa.

    Deriva dalle STESSE `voci`/`cond` della vista audit (D2): nessuna seconda tabella manuale.
    L'ordine e' derivato dal documento e da nient'altro (D5): figli prima nell'ordine della lista
    `required`, poi nell'ordine delle `properties`; tabelle `E#`/`P#` in ordine di prima
    apparizione nell'albero reso; condizionali in ordine di raccolta. Nessun `set`, nessun
    `sorted`. Il rinvio alla prosa e' la riga `audit sha256 …` (D7).
    """
    per = {v.percorso: v for v in voci}
    figli = {}
    for v in voci:
        p = v.percorso
        if not p or p.endswith("[]"):
            continue                            # la radice e gli elementi fusi non sono figli
        gen = p.rpartition("/")[0]
        if gen.endswith("[]"):
            gen = gen[:-2]                      # i figli di un elemento stanno sotto l'array
        figli.setdefault(gen, []).append(v)

    def ordine(gen):
        """I percorsi dei figli, prima nell'ordine della lista `required`, poi nell'ordine delle
        `properties`. Percorsi VERI: i figli di un elemento fuso vivono sotto `gen + "[]"`, e la
        lista `required` che li governa e' quella dell'ELEMENTO, non dell'array."""
        genitore = gen + "[]" if per.get(gen + "[]") is not None else gen
        req = per[genitore].f.req
        vs = {v.percorso.rsplit("/", 1)[-1]: v.percorso for v in figli.get(gen, [])}
        return [vs[x] for x in req if x in vs] + [vs[x] for x in vs if x not in req]

    tabE, tabP = {}, {}

    def numera(gen):
        v = per[gen]
        if "enum" in v.f.kw:
            t = tuple(v.f.kw["enum"])
            if t not in tabE:
                tabE[t] = len(tabE) + 1
        if "pattern" in v.f.kw:
            if v.f.kw["pattern"] not in tabP:
                tabP[v.f.kw["pattern"]] = len(tabP) + 1
        elem = per.get(gen + "[]")
        if elem is not None:
            if "enum" in elem.f.kw:
                t = tuple(elem.f.kw["enum"])
                if t not in tabE:
                    tabE[t] = len(tabE) + 1
            if "pattern" in elem.f.kw:
                if elem.f.kw["pattern"] not in tabP:
                    tabP[elem.f.kw["pattern"]] = len(tabP) + 1
        for p in ordine(gen):
            numera(p)

    numera("")
    r = ["CONTRATTO %s <- %s" % (agente, nome),
         "audit sha256 %s" % sha_audit,
         "! obbligatorio  (senza ! = opzionale)  a<X> = array i cui elementi sono X  ~ nullable",
         "tipi s=string i=integer n=number b=boolean o=object a=array z=null",
         "E# vocabolario chiuso  P# pattern  <n maxLength  @f format",
         "== ALBERO (rientro = annidamento)"]

    def emetti(gen, prof):
        v = per[gen]
        elem = per.get(gen + "[]")
        nome = "(radice)" if gen == "" else gen.rsplit("/", 1)[-1]
        marca = "!" if v.obbligatorio else ""
        t = v.f.kw.get("type")
        e_array = t == "array" or t == ["array"]
        if e_array:
            if elem is None:
                raise CostruttoNonCompilabile(
                    f"array {v.percorso} senza elementi: la fusione a<X> non sa cosa rendere")
            extra = _vincoli_extra_delivery(v) + [
                k for k in ("enum", "pattern", "maxLength", "format") if k in v.f.kw]
            if extra or v.f.nullable:
                raise CostruttoNonCompilabile(
                    f"array {v.percorso} con vincoli propri: la fusione a<X> non li rende "
                    f"({' '.join(extra)})")
            r.append(" " * prof + marca + nome + " a<"
                     + " ".join(_gettoni_delivery(elem, tabE, tabP)) + ">")
        else:
            extra = _vincoli_extra_delivery(v)
            if extra:
                raise CostruttoNonCompilabile(
                    f"vincolo non reso nella delivery su {v.percorso}: {' '.join(extra)}")
            gett = _gettoni_delivery(v, tabE, tabP)
            if not gett:
                raise CostruttoNonCompilabile(
                    f"posizione {v.percorso} senza nulla da mostrare nella delivery")
            r.append(" " * prof + marca + nome + " " + " ".join(gett))
        for p in ordine(gen):
            emetti(p, prof + 1)

    emetti("", 0)
    r.append("== VOCABOLARI E PATTERN")
    for t in tabE:
        r.append("E%d %s" % (tabE[t], "|".join(str(x) for x in t)))
    for x in tabP:
        r.append("P%d %s" % (tabP[x], x))
    r.append("== COND  +obbligatorio-se  -vietato-se-non   percorso|campo|valori")
    for perc, x in cond:
        base = "%s/%%s|%s|%s" % (perc, x["campo"], ",".join(x["valori"]))
        for n in x["allora"]:
            r.append("+" + base % n)
        for n in x["vietati"]:
            r.append("-" + base % n)
    return "\n".join(r).rstrip("\n") + "\n"


def rendi(agente, nome, c, voci, cond):
    sha_c = hashlib.sha256(leggi_normalizzato(os.path.abspath(__file__))).hexdigest()
    r = [f"# CONTRATTO — {agente} ← {nome}",
         f"<!-- generato da contract_compile.py {VERSIONE} sha256 {sha_c} -->",
         f"<!-- schema {nome} sha256 {c.hash[nome]} · commit {c.commit} -->", ""]
    if voci[0].descrizione():
        r += [voci[0].descrizione(), ""]

    # Il tipo della radice, in testata e NON in «Struttura obbligatoria»: quella sezione elenca i
    # FIGLI obbligatori, e un lettore che cerchi «che cos'e' l'artefatto» non trova la risposta in
    # nessuna sezione — il difetto che M1.1 ha trovato (D12). Qui e non altrove, perche' il
    # parser di «Struttura obbligatoria» del selftest la leggerebbe come un figlio fittizio.
    r += ["`(radice dell'artefatto)`   ASSERITO   " + voci[0].asserito(), ""]

    # Gli obblighi sono resi nell'ordine della LISTA `required`, non in quello delle `properties`.
    # E' la decisione 2 del §8.5 del design — «Envelope.required elenca agent, run_id, status,
    # budget_used, stop_reason, payload…, che e' l'ordine in cui un agente compila l'envelope» — e
    # senza di essa la decisione 4, `dict.fromkeys` invece di `sorted(set(...))`, non sarebbe
    # osservabile in alcun punto del prodotto: il contratto uscirebbe identico con l'una e con
    # l'altra, e la prova che la sorveglia sarebbe cieca.
    per_percorso = {v.percorso: v for v in voci}
    resi = set()
    r.append("## Struttura obbligatoria")
    for v in voci:
        if not v.f.req:
            continue
        r += ["", f"### `{v.percorso}`" if v.percorso else "### (radice dell'artefatto)"]
        for n in v.f.req:
            p = f"{v.percorso}/{n}" if v.percorso else n
            resi.add(p)
            figlio = per_percorso.get(p)
            # La riga intera, non il solo nome: un campo obbligatorio che ha una `description` ma
            # nessun enum, nessun pattern e nessun limite non comparirebbe in nessun'altra sezione,
            # e la sua prosa andrebbe perduta in silenzio. E' la perdita d'informazione che il §7.1
            # del design chiude imponendo che nessuna sezione sia «senza dettaglio».
            r += riga(figlio) if figlio else [f"`{p}`   ASSERITO   obbligatorio"]

    r += ["", "## Vocabolari chiusi", ""]
    for v in voci[1:]:
        if any(k in v.f.kw for k in ("enum", "pattern", "const")):
            resi.add(v.percorso)
            r += riga(v)

    r += ["", "## Obblighi e divieti condizionali", ""]
    for perc, x in cond:
        vals = json.dumps(x["valori"], ensure_ascii=False)
        for n in x["allora"]:
            r.append(f"- `{perc}/{n}` è **obbligatorio** quando `{x['campo']}` ∈ {vals}")
        for n in x["vietati"]:
            r.append(f"- `{perc}/{n}` è **vietato** quando `{x['campo']}` ∉ {vals}")
        for d in x["prosa"]:
            r.append(f"            PROSA      «{' '.join(d.split())}»")
            r.append("                       ⚠ non verificata dal validatore")

    r += ["", "## Limiti", ""]
    for v in voci[1:]:
        if any(k in v.f.kw for k in ("maxLength", "maxItems", "maximum")):
            resi.add(v.percorso)
            r += riga(v)

    # Chiusura: ogni posizione che porta qualcosa e non e' gia' comparsa. Senza questa rete due
    # posizioni d'elemento — `payload/claims[]` e `payload/contradictory_evidence[]` — perderebbero
    # la propria `description`, perche' non stanno nel `required` di nessuno (l'array e'
    # obbligatorio, l'elemento no) e non hanno enum, pattern o limiti che le facciano comparire
    # altrove. La regola e' che nessuna sezione sia «senza dettaglio», e vale anche per il fondo.
    r += ["", "## Altre posizioni", ""]
    for v in voci[1:]:
        if v.percorso in resi:
            continue
        if v.descrizione() or v.f.kw or v.f.nullable:
            resi.add(v.percorso)
            r += riga(v)
    return "\n".join(r).rstrip("\n") + "\n"


def main():
    ap = argparse.ArgumentParser(description="genera il contratto strutturale di un agente")
    ap.add_argument("--agente", required=True)
    ap.add_argument("--schemi", default=None)
    ap.add_argument("--out")
    ap.add_argument("--vista", choices=("audit", "delivery"), default="audit",
                    help="audit = contratto completo, persistito per-run e non consegnato al "
                         "modello (default); delivery = forma compatta C5d per il contesto del "
                         "modello, con l'hash della audit")
    ap.add_argument("--commit", default="non-dichiarato",
                    help="registrato nell'intestazione. Il compilatore non lo LEGGE da git: "
                         "renderebbe l'uscita dipendente dallo stato del repository invece che "
                         "dai byte d'ingresso. Verificarlo e' compito di provenance_check.")
    a = ap.parse_args()
    try:
        if a.vista == "delivery":
            t = compila_viste(a.agente, a.schemi, a.commit)[1]
        else:
            t = compila_contratto(a.agente, a.schemi, a.commit)
    except ErroreRisoluzione as e:
        print(f"errore di risoluzione: {e}", file=sys.stderr)
        return 1
    except CostruttoNonCompilabile as e:
        print(f"costrutto non compilabile: {e}", file=sys.stderr)
        return 2
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    else:
        sys.stdout.write(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
