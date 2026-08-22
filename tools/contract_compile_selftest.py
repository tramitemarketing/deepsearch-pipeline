#!/usr/bin/env python3
"""
contract_compile_selftest.py — dimostra che contract_compile.py NON e' cieco.

Un compilatore che gira non dice niente. Il contratto che produce puo' essere giusto, oppure puo'
essere una pagina plausibile che ha perso meta' degli obblighi in silenzio: la differenza non e'
ispezionabile leggendo l'output, perche' un contratto incompleto ha lo stesso aspetto di uno
completo. Si dimostra solo mutando gli schemi e pretendendo che il contratto cambi.

CINQUANTUNO PROVE: le ventiquattro di M1 (F1-F24), le tredici della vista delivery (D1-D13) e
le quattordici della consegna M3 (E1-E13 + E7bis). Le F restano identiche: la vista audit cambia
solo per la riga di radice in testata (D12), che nessuna F legge. Delle F, F22, F23 e F24
nacquero ciascuna da un difetto visto passare verde: F22 dalla prosa persa dalle posizioni
d'elemento; F23 dalla guardia condizionale troncata, che faceva dichiarare al contratto una
regola FALSA; F24 dalle rese — maxLength, nullable, tipo degli elementi, sezioni — che nessuna
mutazione di schema tocca, perche' a cambiare non e' lo schema ma il renditore. Le D e le E
hanno la stessa forma vincolata: le proprieta' derivate dagli schemi si falsificano per
mutazione in quattro passi (riferimento contiene la voce; i byte mutati cambiano;
ricompilazione senza la voce; hash diversi); le proprieta' del renditore si asseriscono sul
testo riletto, mai sul camminatore del compilatore — il riferimento di D1 e' estratto dagli
SCHEMI con un camminatore indipendente, mai dalla vista audit.

Le prove a mutazione seguono quattro passi: si compila il riferimento e si asserisce che CONTIENE
la voce; si muta e si asserisce che i BYTE del file mutato sono cambiati (una mutazione a vuoto e'
un difetto del test, non un esito); si ricompila e si asserisce che NON contiene piu' la voce; si
asserisce che i due sha256 DIFFERISCONO. Il quarto passo esiste perche' senza di esso un
compilatore che non emetta mai nulla passa ogni prova a senso unico.

E "la voce" e' sempre una voce in una POSIZIONE STRUTTURALE nominata, mai un token nel testo. Tre
casi di questo corpus lo impongono, e ognuno ha gia' prodotto un falso rosso in fase di piano:
  - `payload` e' dichiarato anche come proprieta' propria della radice di evidence-pack, quindi
    svuotare l'allOf gli toglie l'obbligatorieta' ma non la presenza;
  - il pattern di Claim.id e' byte-identico a quello di spend_evidence[].claim_id, quindi alterare
    il primo lascia il secondo nel contratto;
  - togliere un nome da un required non toglie il campo: finisce fra gli opzionali.

  uso:  python tools/contract_compile_selftest.py
        exit 0 = tutte e cinquantuno verdi
"""
import hashlib, inspect, io, json, os, re, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '·' e '←' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMI = os.path.join(ROOT, "schemas")
QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

import contract_compile as cc                                              # noqa: E402

ESITI = []


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


# ------------------------------------------------------------------ utilita'

def copia_schemi():
    """Copia usa-e-getta degli schemi canonici. Le mutazioni non toccano MAI i canonici."""
    d = tempfile.mkdtemp(prefix="cc-selftest-")
    dst = os.path.join(d, "schemas")
    shutil.copytree(SCHEMI, dst)
    return d, dst


def compila(dir_schemi):
    """Il contratto di evidence-researcher, come stringa. Solleva le eccezioni del compilatore."""
    return cc.compila_contratto("evidence-researcher", dir_schemi)


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def muta_json(dir_schemi, nome, f):
    """Applica f al documento, riscrive, e PRETENDE che i byte siano cambiati (passo 2)."""
    p = os.path.join(dir_schemi, nome)
    prima = open(p, "rb").read()
    d = json.loads(prima.decode("utf-8"))
    f(d)
    dopo = json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8")
    open(p, "wb").write(dopo)
    if prima == dopo:
        raise AssertionError(f"mutazione a vuoto su {nome}: i byte non sono cambiati. "
                             "E' un difetto del test, non un esito.")


def scrivi_json(dir_schemi, nome, doc):
    p = os.path.join(dir_schemi, nome)
    prima = open(p, "rb").read() if os.path.exists(p) else b""
    dopo = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    open(p, "wb").write(dopo)
    if prima == dopo:
        raise AssertionError(f"mutazione a vuoto su {nome}")


def obblighi(contratto, percorso):
    """I nomi obbligatori dichiarati per una posizione d'istanza, nell'ordine del contratto.

    Legge la sezione «Struttura obbligatoria», raggruppata per posizione, e non il testo intero:
    un'asserzione per sottostringa sarebbe rossa su un'implementazione corretta ogni volta che il
    token sopravvive altrove — `payload` fra le proprieta' proprie della radice, il pattern gemello
    di spend_evidence[]/claim_id, un campo che passa da obbligatorio a opzionale.
    """
    out, attuale = [], None
    for riga in sezione(contratto, "Struttura obbligatoria").splitlines():
        if riga.strip() == "### (radice dell'artefatto)":
            attuale = ""
            continue
        m = re.match(r"^### `([^`]*)`\s*$", riga)
        if m:
            attuale = m.group(1)
            continue
        m = re.match(r"^`([^`]+)`\s+ASSERITO\s", riga)
        if m and attuale == percorso:
            out.append(m.group(1).rsplit("/", 1)[-1] if attuale else m.group(1))
    return out


def sezione(contratto, titolo):
    """Il corpo di una sezione `## <titolo>`, fino alla prossima `## ` o a fine file."""
    m = re.search(r"^## " + re.escape(titolo) + r"\s*$", contratto, re.M)
    if not m:
        return ""
    resto = contratto[m.end():]
    n = re.search(r"^## ", resto, re.M)
    return resto[:n.start()] if n else resto


def righe_campo(contratto, percorso):
    """Tutte le righe ASSERITO di una posizione d'istanza, in qualunque sezione."""
    out = []
    for m in re.finditer(r"^`([^`]+)`\s+ASSERITO\s+(.*)$", contratto, re.M):
        if m.group(1) == percorso:
            out.append(m.group(2))
    return out


def prosa_campo(contratto, percorso):
    """Il testo PROSA di una posizione d'istanza, se c'e'."""
    for m in re.finditer(r"^`([^`]+)`\s+ASSERITO\s+.*\n((?:\s+PROSA\s+.*\n(?:\s+⚠.*\n)?)?)",
                         contratto, re.M):
        if m.group(1) == percorso and m.group(2).strip():
            return m.group(2)
    return ""


def valori_enum(contratto, percorso):
    """I valori d'enum dichiarati per una posizione, come lista. Mai un confronto per sottostringa:
    `pricing` e' sottostringa di `pricing_mercato` e i due convivono in questo contratto."""
    for riga in righe_campo(contratto, percorso):
        m = re.search(r"enum \[([^\]]*)\]", riga)
        if m:
            return [json.loads(v.strip()) for v in m.group(1).split("·")]
    return []


def esegui(dir_schemi, extra=()):
    """Il compilatore come processo separato. Ritorna (codice, stdout, stderr)."""
    cmd = [sys.executable, os.path.join(QUI, "contract_compile.py"),
           "--agente", "evidence-researcher", "--schemi", dir_schemi] + list(extra)
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


# ------------------------------------------------------------------ F1 … F13

@prova("F1", "$ref locale: un puntatore rotto e' un errore, mai silenzio")
def f1():
    d, s = copia_schemi()
    try:
        compila(s)                                            # il riferimento compila
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"]["claims"]["items"].__setitem__(
                      "$ref", "core.schema.json#/$defs/ClaimInesistente"))
        try:
            compila(s)
            raise AssertionError("un puntatore rotto ha prodotto un contratto")
        except cc.ErroreRisoluzione as e:
            assert "ClaimInesistente" in str(e), f"l'errore non nomina il puntatore: {e}"
        codice, _, err = esegui(s)
        assert codice == 1, f"uscita {codice}, attesa 1"
        assert "ClaimInesistente" in err
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F2", "il fratello di $ref e' LETTO (non prova la somma: quella e' F16)")
def f2():
    d, s = copia_schemi()
    try:
        rif = compila(s)
        assert "promoted_by" in obblighi(rif, "payload/claims[]"), "passo 1"
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"]["claims"]["items"].pop("required"))
        mut = compila(s)
        assert "promoted_by" not in obblighi(mut, "payload/claims[]"), "passo 3"
        assert sha(rif) != sha(mut), "passo 4: gli hash coincidono"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F3", "fusione dei required: togliere `state` da Claim.required lo toglie dagli obblighi")
def f3():
    d, s = copia_schemi()
    try:
        rif = compila(s)
        assert "state" in obblighi(rif, "payload/claims[]"), "passo 1"
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["Claim"].__setitem__(
                      "required", [x for x in o["$defs"]["Claim"]["required"] if x != "state"]))
        mut = compila(s)
        assert "state" not in obblighi(mut, "payload/claims[]"), "passo 3"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F4", "allOf appiattito: svuotarlo toglie TUTTI E DIECI i required d'envelope")
def f4():
    d, s = copia_schemi()
    dieci = ["agent", "run_id", "status", "budget_used", "stop_reason", "payload",
             "new_sources", "queries_run", "unreachable_sources", "self_check"]
    try:
        rif = compila(s)
        o_rif = obblighi(rif, "")
        for n in dieci:
            assert n in o_rif, f"passo 1: {n} manca dagli obblighi di radice"
        muta_json(s, "evidence-pack.schema.json", lambda o: o.__setitem__("allOf", []))
        mut = compila(s)
        o_mut = obblighi(mut, "")
        for n in dieci:
            assert n not in o_mut, (
                f"passo 3: {n} e' ancora obbligatorio. NB: `payload` sopravvive nel CONTRATTO come "
                "proprieta' propria della radice — l'asserzione e' sugli OBBLIGHI, non sul testo")
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F5", "required annidati: togliere `gaps` da coverage.required lo toglie dagli obblighi")
def f5():
    d, s = copia_schemi()
    try:
        rif = compila(s)
        assert "gaps" in obblighi(rif, "payload/coverage"), "passo 1"
        muta_json(s, "evidence-pack.schema.json",
                  lambda o: o["properties"]["payload"]["properties"]["coverage"].__setitem__(
                      "required", ["angles_covered"]))
        mut = compila(s)
        assert "gaps" not in obblighi(mut, "payload/coverage"), "passo 3"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F6", "enum per intero: `wayback` sparisce dalla riga d'enum di queries_run[]/angle")
def f6():
    d, s = copia_schemi()
    P = "queries_run[]/angle"
    try:
        rif = compila(s)
        v = valori_enum(rif, P)
        assert "wayback" in v, f"passo 1: {v}"
        assert len(v) == 20, f"l'enum di angle ha 20 valori, ne ho letti {len(v)}"
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["Query"]["properties"]["angle"].__setitem__(
                      "enum", [x for x in o["$defs"]["Query"]["properties"]["angle"]["enum"]
                               if x != "wayback"]))
        mut = compila(s)
        assert "wayback" not in valori_enum(mut, P), "passo 3"
        # `pricing` non e' usato come bersaglio: e' sottostringa di `pricing_mercato`, che sta
        # nell'enum di payload/channel dello stesso contratto.
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F7", "pattern verbatim: il pattern nuovo compare, byte per byte, sulla riga di Claim.id")
def f7():
    d, s = copia_schemi()
    P, NUOVO = "payload/claims[]/id", "^ZZ-[0-9]{4}$"
    try:
        rif = compila(s)
        vecchio = "^C-[A-Za-z0-9]{2,20}(-contra)?-[0-9]{3}(?![\\s\\S])"
        assert any(vecchio in r for r in righe_campo(rif, P)), "passo 1"
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["Claim"]["properties"]["id"].__setitem__("pattern", NUOVO))
        mut = compila(s)
        assert not any(vecchio in r for r in righe_campo(mut, P)), (
            "passo 3: NB il pattern vecchio resta nel CONTRATTO su spend_evidence[]/claim_id, che "
            "e' byte-identico — l'asserzione e' sulla riga di questa posizione")
        assert any(NUOVO in r for r in righe_campo(mut, P)), "il pattern nuovo non e' verbatim"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


SETTE = ["PAYWALL", "BLOCKED", "NOT_FOUND", "ROBOTS", "API_LIMIT",
         "LOGIN_REQUIRED", "NO_PUBLIC_DATA"]


@prova("F8", "if/then/else/not: togliere l'else toglie il DIVIETO su source_id")
def f8():
    d, s = copia_schemi()
    try:
        rif = compila(s)
        sez = sezione(rif, "Obblighi e divieti condizionali")
        assert re.search(r"unreachable_sources\[\]/source_id.*vietato", sez), \
            f"passo 1: il divieto non e' reso.\n{sez}"
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["UnreachableSource"]["allOf"][0].pop("else"))
        mut = compila(s)
        assert not re.search(r"unreachable_sources\[\]/source_id.*vietato",
                             sezione(mut, "Obblighi e divieti condizionali")), "passo 3"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F23", "la GUARDIA condizionale porta tutti i suoi valori, o la regola resa e' falsa")
def f23():
    d, s = copia_schemi()
    try:
        def guardie(t):
            return [json.loads(m) for m in re.findall(
                r"`unreachable_sources\[\]/source_id`[^\n]*?[∈∉] (\[[^\]]*\])",
                sezione(t, "Obblighi e divieti condizionali"))]
        rif = compila(s)
        letti = guardie(rif)
        assert len(letti) == 2, f"attese due righe (obbligo + divieto), lette {len(letti)}"
        for v in letti:
            assert v == SETTE, f"guardia resa con {v} invece dei sette valori dell'if"
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["UnreachableSource"]["allOf"][0]["if"]["properties"]["why"]
                  .__setitem__("enum", SETTE[:1]))
        mut = compila(s)
        assert all(v != SETTE for v in guardie(mut)), "passo 3"
        assert sha(rif) != sha(mut), "passo 4"
        # Senza questa prova un compilatore che tronchi la guardia resta verde e il contratto
        # dichiara che con `why = BLOCKED` il `source_id` e' VIETATO, mentre lo schema e IC-4 lo
        # rendono obbligatorio: e' l'unico else/not del corpus, reso falso in silenzio.
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F9", "ordine deterministico: cinque compilazioni, un solo sha256")
def f9():
    d, s = copia_schemi()
    d2, s2 = copia_schemi()
    try:
        a, b = sha(compila(s)), sha(compila(s))                # due volte in-process
        c1, o1, e1 = esegui(s)
        c2, o2, e2 = esegui(s)                                 # due volte in processi distinti
        assert c1 == 0 and c2 == 0, f"uscite {c1}/{c2}: {e1}{e2}"
        c, dd = sha(o1), sha(o2)
        for nome in os.listdir(s2):                            # copia con terminatori CRLF
            p = os.path.join(s2, nome)
            t = open(p, "rb").read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            open(p, "wb").write(t)
        e = sha(compila(s2))
        assert a == b == c == dd == e, (
            f"cinque compilazioni, hash diversi:\n  in-process {a}\n  in-process {b}\n"
            f"  processo    {c}\n  processo    {dd}\n  CRLF        {e}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


@prova("F10", "riferimento a un file inesistente: errore di risoluzione")
def f10():
    d, s = copia_schemi()
    try:
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"]["claims"]["items"].__setitem__(
                      "$ref", "inesistente.schema.json#/$defs/X"))
        codice, _, err = esegui(s)
        assert codice == 1, f"uscita {codice}, attesa 1"
        assert "inesistente.schema.json" in err
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F11", "ciclo: errore entro un budget di tempo, con la catena stampata")
def f11():
    d, s = copia_schemi()
    try:
        def m(o):
            o["$defs"]["CicloA"] = {"$ref": "core.schema.json#/$defs/CicloB"}
            o["$defs"]["CicloB"] = {"$ref": "core.schema.json#/$defs/CicloA"}
        muta_json(s, "core.schema.json", m)
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"].__setitem__("ciclo", {"$ref": "core.schema.json#/$defs/CicloA"}))
        # il budget e' il meccanismo: senza, una guardia sbagliata fa PENDERE il test, e un test
        # che pende non e' rosso.
        codice, _, err = esegui(s)
        assert codice == 1, f"uscita {codice}, attesa 1"
        assert "ciclo" in err.lower(), f"l'errore non nomina il ciclo: {err}"
        assert "CicloA" in err and "CicloB" in err, f"la catena non e' stampata: {err}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F12", "percorso fuori radice: il file esiste, e NON viene aperto")
def f12():
    d, s = copia_schemi()
    fuori = os.path.join(d, "fuori")
    os.makedirs(fuori, exist_ok=True)
    esca = os.path.join(fuori, "x.json")
    json.dump({"$defs": {"X": {"type": "string"}}}, open(esca, "w", encoding="utf-8"))
    try:
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"]["claims"]["items"].__setitem__("$ref", "../fuori/x.json#/$defs/X"))
        aperti = []
        vero_open = io.open
        def spia(file, *a, **k):
            aperti.append(os.path.abspath(str(file)))
            return vero_open(file, *a, **k)
        import builtins
        builtins.open = spia
        try:
            try:
                compila(s)
                raise AssertionError("un percorso fuori radice ha prodotto un contratto")
            except cc.ErroreRisoluzione:
                pass
        finally:
            builtins.open = vero_open
        # il codice d'uscita e' 1 anche per un file mancante: la discriminazione e' qui.
        assert os.path.abspath(esca) not in aperti, "il file fuori radice E' STATO APERTO"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F13", "costrutto non supportato: rifiuto esplicito e nominato, distinto dall'errore")
def f13():
    for nome, mutazione, atteso in (
        ("dependentRequired",
         lambda o: o["$defs"]["Claim"].__setitem__("dependentRequired", {"id": ["state"]}),
         "dependentRequired"),
        ("oneOf a tre rami",
         lambda o: o["$defs"]["Claim"]["properties"]["statement"]["properties"].__setitem__(
             "quantity", {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "boolean"}]}),
         "oneOf"),
    ):
        d, s = copia_schemi()
        try:
            muta_json(s, "core.schema.json", mutazione)
            codice, _, err = esegui(s)
            assert codice == 2, f"{nome}: uscita {codice}, attesa 2 (rifiuto, non errore)"
            assert atteso in err, f"{nome}: il costrutto non e' nominato in «{err.strip()}»"
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ F14 … F21

@prova("F14", "asimmetria claims / contradictory_evidence sullo stesso $ref")
def f14():
    d, s = copia_schemi()
    try:
        c = compila(s)
        assert "promoted_by" in obblighi(c, "payload/claims[]")
        assert "promoted_by" not in obblighi(c, "payload/contradictory_evidence[]"), (
            "promoted_by e' comparso su contradictory_evidence[], che e' un $ref NUDO: "
            "il compilatore memoizza il risultato risolto, o deduplica per tipo")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F15", "la disgiunzione non e' una congiunzione: i required di Numeric non sono obblighi")
def f15():
    d, s = copia_schemi()
    try:
        c = compila(s)
        for P in ("payload/contradictory_evidence[]/statement/quantity",
                  "payload/claims[]/statement/quantity"):
            o = obblighi(c, P)
            for n in ("label", "value", "unit", "state"):
                assert n not in o, (
                    f"{n} e' dichiarato obbligatorio sotto {P}, che e' anyOf [Numeric, null]: "
                    "sotto una disgiunzione i required non sono congiuntivi")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F16", "il $ref e' RISOLTO, non solo rilevato: i quattro di Claim, e nell'ordine giusto")
def f16():
    d, s = copia_schemi()
    try:
        c = compila(s)
        assert obblighi(c, "payload/claims[]") == \
            ["promoted_by", "id", "statement", "state", "source_ids"], \
            f"ordine o contenuto sbagliati: {obblighi(c, 'payload/claims[]')}"
        assert obblighi(c, "payload/contradictory_evidence[]") == \
            ["id", "statement", "state", "source_ids"], \
            f"il $ref nudo non e' risolto: {obblighi(c, 'payload/contradictory_evidence[]')}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F17", "$ref annidato: evidence-pack → Claim → Numeric, tre livelli, catena reale")
def f17():
    d, s = copia_schemi()
    try:
        c = compila(s)
        for P in ("payload/claims[]/statement/quantity",
                  "payload/contradictory_evidence[]/statement/quantity"):
            for n in ("label", "value", "unit", "state"):
                assert righe_campo(c, f"{P}/{n}"), (
                    f"{P}/{n} non compare: la catena a tre livelli non e' stata risolta")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F18", "separazione ASSERITO / PROSA, e il const guardia dell'if non va raccolto")
def f18():
    d, s = copia_schemi()
    try:
        c = compila(s)
        # ogni PROSA porta la marcatura, e nessuna description finisce in ASSERITO
        for m in re.finditer(r"^\s+PROSA\s+«(.*)»\s*$", c, re.M):
            resto = c[m.end():]
            assert resto.lstrip("\n").startswith(" ") and "⚠" in resto.split("\n")[1], \
                f"PROSA senza marcatura: {m.group(1)[:60]}"
        for P in ("payload/claims[]/state", "payload/contradictory_evidence[]/state"):
            for r in righe_campo(c, P):
                assert "const" not in r, (
                    f"{P} porta un const: e' la guardia dell'if di Claim, raccolta per errore. "
                    "Insegna che state deve SEMPRE valere VERIFIED")
        assert "⚠ non verificata dal validatore" in c
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F19", "nessuna rete: zero tentativi di socket durante la compilazione")
def f19():
    import socket
    tentativi = []
    vero = socket.socket.connect
    def spia(self, *a, **k):
        tentativi.append(a)
        raise AssertionError("il compilatore ha tentato una connessione")
    socket.socket.connect = spia
    d, s = copia_schemi()
    try:
        compila(s)
        assert not tentativi
    finally:
        socket.socket.connect = vero
        shutil.rmtree(d, ignore_errors=True)
    # i sottoprocessi di F9/F11 non sono visti da un contatore in-process: si asserisce a monte
    # che il sorgente non nomina alcuna libreria di rete.
    src = open(os.path.join(QUI, "contract_compile.py"), encoding="utf-8").read()
    for lib in ("socket", "http", "urllib", "requests", "ftplib", "smtplib"):
        assert not re.search(r"^\s*(import|from)\s+" + lib + r"\b", src, re.M), \
            f"il compilatore importa {lib}"


@prova("F20", "nessun jsonschema, nessun referencing")
def f20():
    prima = set(sys.modules)
    import importlib
    importlib.reload(cc)
    delta = set(sys.modules) - prima
    for lib in ("jsonschema", "referencing"):
        assert lib not in delta, f"import di {lib} portato da contract_compile"
    src = open(os.path.join(QUI, "contract_compile.py"), encoding="utf-8").read()
    for lib in ("jsonschema", "referencing"):
        assert not re.search(r"^\s*(import|from)\s+" + lib + r"\b", src, re.M)


@prova("F22", "nessuna description raggiungibile va perduta, in nessuna sezione")
def f22():
    d, s = copia_schemi()
    try:
        c = compila(s)
        comp = cc.Compilatore(s)
        voci, _ = cc.cammina(comp, [(comp.carica("evidence-pack.schema.json"),
                                     "evidence-pack.schema.json")], "", 0, [], [], [])
        # L'insieme e' quello delle DICHIARAZIONI (file, puntatore) raggiungibili — la convenzione
        # del §6 del piano — e non quello delle posizioni d'istanza: le posizioni saltano la
        # radice e i rami then/else, che sono tre delle quarantanove e che nessun'altra prova
        # guarda. La radice e' prescritta dal §3 del piano («fra i commenti e la prima sezione»).
        dich = descrizioni_raggiungibili(s)
        assert len(dich) == 49, f"49 dichiarazioni attese, {len(dich)} raggiunte"
        manc = [p for p, t in dich if f"«{t}»" not in c and t not in c]
        assert not manc, (
            f"{len(manc)} description raggiungibili non compaiono nel contratto: {manc[:5]}. "
            "Tre classi cadono facilmente: le posizioni d'ELEMENTO — l'array e' obbligatorio, "
            "l'elemento no — la description di RADICE, che va in intestazione, e quelle dei rami "
            "THEN/ELSE, che vivono nella sezione condizionale")
        assert c.count("PROSA      «") == c.count("⚠ non verificata dal validatore"), \
            "una PROSA senza marcatura, o una marcatura senza PROSA"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def descrizioni_raggiungibili(dir_schemi):
    """Le dichiarazioni `description` (file, puntatore) raggiungibili dallo schema d'agente.

    Risolutore INDIPENDENTE da contract_compile: una prova che riusi lo strumento che misura non
    e' una verifica. Poche righe, e volutamente ingenue.
    """
    S = {n: json.load(open(os.path.join(dir_schemi, n), encoding="utf-8"))
         for n in os.listdir(dir_schemi) if n.endswith(".json")}

    def ris(ref, cur):
        if ref.startswith("#"):
            doc, ptr = S[cur], ref[1:]
        else:
            f, _, fr = ref.partition("#")
            cur, doc, ptr = os.path.basename(f), S[os.path.basename(f)], fr
        n = doc
        for p in [x for x in ptr.split("/") if x]:
            n = n[p]
        return n, cur

    out, visti = [], set()

    def go(nd, cur, ptr, pila=frozenset()):
        if not isinstance(nd, dict):
            return
        if isinstance(nd.get("description"), str) and (cur, ptr) not in visti:
            visti.add((cur, ptr))
            out.append((f"{cur}#{ptr}", " ".join(nd["description"].split())))
        if "$ref" in nd:
            ch = (cur, nd["$ref"], ptr)
            if ch not in pila:
                t, c2 = ris(nd["$ref"], cur)
                fr = nd["$ref"][1:] if nd["$ref"].startswith("#") else nd["$ref"].partition("#")[2]
                go(t, c2, fr, pila | {ch})
        for nm, sub in (nd.get("properties") or {}).items():
            go(sub, cur, f"{ptr}/properties/{nm}", pila)
        if isinstance(nd.get("items"), dict):
            go(nd["items"], cur, ptr + "/items", pila)
        for k in ("allOf", "then", "else"):
            v = nd.get(k)
            if isinstance(v, dict):
                go(v, cur, f"{ptr}/{k}", pila)
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    go(x, cur, f"{ptr}/{k}/{i}", pila)
        for k in ("anyOf", "oneOf"):
            v = nd.get(k)
            if isinstance(v, list) and len(v) == 2:
                a, b = v
                r = a if (isinstance(b, dict) and b.get("type") == "null") else (
                    b if (isinstance(a, dict) and a.get("type") == "null") else None)
                if r is not None:
                    go(r, cur, f"{ptr}/{k}/{v.index(r)}", pila)

    go(S["evidence-pack.schema.json"], "evidence-pack.schema.json", "")
    return out


@prova("F24", "le asserzioni che nessuna falsificazione strutturale tocca")
def f24():
    """maxLength, nullable, tipo degli elementi, sezioni: una resa puo' sparire senza che una
    mutazione di schema la faccia notare, perche' non e' lo SCHEMA a cambiare ma il RENDITORE."""
    d, s = copia_schemi()
    try:
        c = compila(s)
        assert c.count("maxLength 300") == 4, (
            "l'unico limite del contratto — maxLength 300 sui due `evidence[]/snippet` — non e' "
            f"reso quattro volte ma {c.count('maxLength 300')}")
        for P in ("payload/claims[]/statement/quantity",
                  "payload/contradictory_evidence[]/statement/quantity"):
            assert any("nullable" in r for r in righe_campo(c, P)), (
                f"{P} non e' marcato nullable: si perde la differenza fra `null` e un Numeric con "
                "state UNKNOWN, che lo schema dichiara «cose diverse e non intercambiabili»")
        secchi = sorted(set(re.findall(r"^`([^`]+)`   ASSERITO   [^\n]*· array(?! di)", c, re.M)))
        assert secchi == ["self_check/invarianti_violati"], (
            f"array senza il tipo degli elementi: {secchi}. Ne e' ammesso UNO SOLO, e per una "
            "ragione dello schema e non del compilatore: gli `items` di `invarianti_violati` "
            "portano un `enum` e nessun `type`, quindi il tipo dell'elemento non e' derivabile. "
            "Ogni altro array deve mostrarlo: e' la resa che il replay motiva, dove "
            "`coverage.gaps` fu scritto come stringa narrativa in tre canali su cinque")
        assert sorted(set(re.findall(r"· (array di \w+)", c))) == ["array di object",
                                                                  "array di string"], \
            "i tipi d'elemento resi non sono quelli attesi"
        for t in ("Struttura obbligatoria", "Vocabolari chiusi",
                  "Obblighi e divieti condizionali", "Limiti", "Altre posizioni"):
            assert sezione(c, t).strip(), f"la sezione «{t}» e' assente o vuota"
        assert "### (radice dell'artefatto)" in c, "la posizione di radice non ha un titolo leggibile"
        assert "ASSERITO   obbligatorio" not in sezione(c, "Altre posizioni"), (
            "una riga marcata «obbligatorio» sotto un titolo che non e' «Struttura obbligatoria»: "
            "le posizioni d'elemento vanno marcate «ogni elemento», non «obbligatorio»")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("F21", "ordine sotto allOf: le proprieta' d'envelope precedono payload")
def f21():
    d, s = copia_schemi()
    try:
        c = compila(s)
        o = obblighi(c, "")
        atteso = ["agent", "run_id", "status", "budget_used", "stop_reason", "payload",
                  "new_sources", "queries_run", "unreachable_sources", "self_check"]
        assert o == atteso, (
            f"ordine di radice sbagliato.\n  atteso {atteso}\n  ottenuto {o}\n"
            "Nella radice di evidence-pack `allOf` precede `properties`, quindi l'envelope "
            "precede payload: payload e' il SESTO dei dieci required.")
        assert o.index("payload") == 5, "payload non e' il sesto obbligo"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ macchina di verifica D1 … D13

TIPO_INV = {"s": "string", "i": "integer", "n": "number", "b": "boolean",
            "o": "object", "a": "array", "z": "null"}

FAMIGLIE_D = ("req", "cond_obl", "cond_div", "enum_pos", "pat_pos", "lim_pos",
              "tipo_pos", "format_pos")


def inventario_schemi(dir_schemi):
    """L'inventario strutturale estratto DAGLI SCHEMI, con un camminatore indipendente.

    Stessa semantica dichiarata dal compilatore — allOf appiattito, anyOf [X,null] che sopprime i
    required e marca nullable, $ref elaborato per ultimo, if/then/else raccolto a parte, gli array
    trasparenti alla profondita' — ma riscritta qui da zero, come descrizioni_raggiungibili: una
    prova che riusasse lo strumento che misura non sarebbe una verifica.
    """
    S = {n: json.load(open(os.path.join(dir_schemi, n), encoding="utf-8"))
         for n in os.listdir(dir_schemi) if n.endswith(".json")}
    R = {k: [] for k in FAMIGLIE_D}
    R["percorsi"] = []
    R["ordine"] = []

    def ris(ref, cur):
        if ref.startswith("#"):
            doc, ptr = S[cur], ref[1:]
        else:
            f, _, fr = ref.partition("#")
            cur, doc, ptr = os.path.basename(f), S[os.path.basename(f)], fr
        n = doc
        for p in [x for x in ptr.split("/") if x]:
            n = n[p.replace("~1", "/").replace("~0", "~")]
        return n, cur

    def piu(f, g):
        kw = dict(f[0])
        kw.update(g[0])
        prop = dict(f[1])
        for nm, lista in g[1].items():
            prop.setdefault(nm, []).extend(lista)
        return (kw, prop, f[2] + g[2], list(dict.fromkeys(f[3] + g[3])),
                f[4] or g[4], f[5] + g[5])

    def fondi(nd, cur, pila):
        kw, prop, items, req, nullable, cond = {}, {}, [], [], False, []
        if not isinstance(nd, dict):
            return kw, prop, items, req, nullable, cond
        for k, v in nd.items():
            if k in ("$ref", "then", "else") or k in ("$schema", "$id", "title", "$comment",
                                                      "$defs", "definitions", "examples", "default"):
                continue
            if k == "allOf":
                for ramo in v:
                    acc = piu((kw, prop, items, req, nullable, cond), fondi(ramo, cur, pila))
                    kw, prop, items, req, nullable, cond = acc
            elif k in ("anyOf", "oneOf"):
                r = None
                if isinstance(v, list) and len(v) == 2:
                    if isinstance(v[1], dict) and v[1].get("type") == "null":
                        r = v[0]
                    elif isinstance(v[0], dict) and v[0].get("type") == "null":
                        r = v[1]
                if r is None:
                    raise AssertionError(f"disgiunzione non [X,null] in {cur}")
                a = list(fondi(r, cur, pila))
                a[3] = []                                   # i required del ramo non sono congiuntivi
                a[4] = True
                kw, prop, items, req, nullable, cond = piu(
                    (kw, prop, items, req, nullable, cond), tuple(a))
            elif k == "if":
                g = v.get("properties") or {}
                if len(g) != 1:
                    raise AssertionError(f"guardia con piu' proprieta' in {cur}")
                (campo, sub), = g.items()
                valori = sub.get("enum") or ([sub["const"]] if "const" in sub else None)
                if valori is None:
                    raise AssertionError(f"guardia non enum/const in {cur}")
                cond.append((campo, list(valori),
                             list((nd.get("then") or {}).get("required") or []),
                             list(((nd.get("else") or {}).get("not") or {}).get("required") or [])))
            elif k == "required":
                req = list(v)
            elif k == "properties":
                for nm, sub in v.items():
                    prop.setdefault(nm, []).append((sub, cur))
            elif k == "items":
                items.append((v, cur))
            else:
                kw.setdefault(k, v)
        if "$ref" in nd:
            ch = (cur, nd["$ref"])
            if ch in pila:
                raise AssertionError(f"ciclo di $ref su {nd['$ref']}")
            t, c2 = ris(nd["$ref"], cur)
            kw, prop, items, req, nullable, cond = piu(
                (kw, prop, items, req, nullable, cond), fondi(t, c2, pila | {ch}))
        return kw, prop, items, req, nullable, cond

    def fondi_molti(app, pila):
        acc = ({}, {}, [], [], False, [])
        for nd, cur in app:
            acc = piu(acc, fondi(nd, cur, pila))
        return acc

    def cammina(app, perc, obb, elemento, pila):
        kw, prop, items, req, nullable, cond = fondi_molti(app, pila)
        R["percorsi"].append(perc)
        for n in req:
            R["req"].append((perc, n))
        if "enum" in kw:
            R["enum_pos"].append((perc, tuple(kw["enum"])))
        if "pattern" in kw:
            R["pat_pos"].append((perc, kw["pattern"]))
        if "maxLength" in kw:
            R["lim_pos"].append((perc, "maxLength", kw["maxLength"]))
        if "format" in kw:
            R["format_pos"].append((perc, kw["format"]))
        t = kw.get("type")
        tt = "|".join(t) if isinstance(t, list) else t
        R["tipo_pos"].append((perc, tt, bool(nullable), bool(elemento), bool(obb)))
        for campo, valori, allora, vietati in cond:
            for n in allora:
                R["cond_obl"].append((perc, n, campo, tuple(valori)))
            for n in vietati:
                R["cond_div"].append((perc, n, campo, tuple(valori)))
        R["ordine"].append((perc, [x for x in req if x in prop]
                            + [x for x in prop if x not in req]))
        for nm, lista in prop.items():
            p = f"{perc}/{nm}" if perc else nm
            cammina(lista, p, nm in req, False, pila)
        if items:
            cammina(items, perc + "[]", True, True, pila)

    cammina([(S["evidence-pack.schema.json"], "evidence-pack.schema.json")], "", True, False, frozenset())
    return R


def split_med(s):
    """Divide su ' · ' solo a profondita' 0 di parentesi quadre (enum e regex ne hanno)."""
    out, d, cur, i = [], 0, [], 0
    while i < len(s):
        ch = s[i]
        if ch == "[":
            d += 1
        elif ch == "]":
            d -= 1
        if d == 0 and s[i:i + 3] == " · ":
            out.append("".join(cur))
            cur = []
            i += 3
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def decodi_gettoni(gett):
    d = {}
    for tok in gett:
        if tok.startswith("a<"):
            d["el"] = [x for x in tok[2:-1].split(" ") if x]
            d["type"] = "array"
        elif re.fullmatch(r"E\d+", tok):
            d["E"] = int(tok[1:])
        elif re.fullmatch(r"P\d+", tok):
            d["P"] = int(tok[1:])
        elif tok.startswith("<"):
            d["max"] = int(tok[1:])
        elif tok.startswith("@"):
            d["format"] = tok[1:]
        elif tok == "~":
            d["null"] = True
        else:
            if tok.endswith("~"):
                d["null"] = True
                tok = tok[:-1]
            d["type"] = "|".join(TIPO_INV[x] for x in tok.split("|"))
    return d


def leggi_delivery(t):
    """Rilegge la vista DELIVERY dal suo testo e ricostruisce l'inventario, come farebbe un
    lettore che ha solo quel documento: albero a rientri, tabelle E#/P#, sezione COND."""
    sez, cur = {}, "(testa)"
    sez[cur] = []
    for L in t.split("\n"):
        if L.startswith("== "):
            cur = L[3:].split()[0]
            sez.setdefault(cur, [])
            continue
        sez[cur].append(L)
    E, P = {}, {}
    for L in sez.get("VOCABOLARI", []):
        m = re.match(r"^E(\d+) (.*)$", L)
        if m:
            E[int(m.group(1))] = tuple(m.group(2).split("|"))
            continue
        m = re.match(r"^P(\d+) (.*)$", L)
        if m:
            P[int(m.group(1))] = m.group(2)
    R = {k: [] for k in FAMIGLIE_D}
    R["percorsi"] = []
    R["ordine"] = []

    def emetti(path, gett, obb, elem):
        d = decodi_gettoni(gett)
        R["percorsi"].append(path)
        if "E" in d:
            R["enum_pos"].append((path, E[d["E"]]))
        if "P" in d:
            R["pat_pos"].append((path, P[d["P"]]))
        if "max" in d:
            R["lim_pos"].append((path, "maxLength", d["max"]))
        if "format" in d:
            R["format_pos"].append((path, d["format"]))
        R["tipo_pos"].append((path, d.get("type"), bool(d.get("null")), elem, obb))
        return d

    stack = {}
    for L in sez["ALBERO"]:
        if not L.strip():
            continue
        prof = len(L) - len(L.lstrip(" "))
        riga = L.lstrip(" ")
        if riga.startswith("!"):
            marca, resto = "!", riga[1:]
        else:
            marca, resto = "?", riga
        parti = resto.split(" ")
        nome, gett = parti[0], parti[1:]
        gen = stack.get(prof - 1) if prof > 0 else None
        if nome == "(radice)" and prof == 0:
            path = ""
        else:
            path = ("%s/%s" % (gen, nome)) if gen else nome
        d = emetti(path, gett, marca == "!", False)
        if prof > 0:
            R["ordine"].append((gen if gen is not None else "", nome))
        if marca == "!" and path:
            R["req"].append((gen if gen is not None else "", nome))
        if "el" in d:
            emetti(path + "[]", d["el"], True, True)
            stack[prof] = path + "[]"
        else:
            stack[prof] = path
    for L in sez.get("COND", []):
        if not L or L[0] not in "+-":
            continue
        p, campo, vals = L[1:].split("|")
        perc, nome = p.rsplit("/", 1)
        v = tuple(vals.split(","))
        (R["cond_obl"] if L[0] == "+" else R["cond_div"]).append((perc, nome, campo, v))
    return R


def leggi_audit_inventario(t):
    """Rilegge la vista AUDIT dal suo testo e ricostruisce lo stesso inventario: cinque sezioni
    piu' la riga di radice in testata. Serve a D2 e D12."""
    R = {k: [] for k in FAMIGLIE_D}
    R["percorsi"] = []
    testa = t.split("## ", 1)[0]
    m = re.search(r"^`\(radice dell'artefatto\)`   ASSERITO   (.*)$", testa, re.M)
    assert m, "la vista audit non dichiara la riga di radice in testata"
    corpi = {}

    def corpo_radice(corpo):
        tipo, null, obb = None, False, True
        for tok in split_med(corpo):
            if tok == "obbligatorio":
                obb = True
            elif tok == "opzionale":
                obb = False
            elif tok == "nullable":
                null = True
            elif tok.startswith("enum [") or tok.startswith("pattern ") or \
                    tok.startswith(("maxLength ", "maxItems ", "minimum ", "maximum ")) or \
                    tok.startswith("format ") or tok.startswith("const "):
                pass
            elif tok == "ogni elemento" or tok == "nessun campo extra":
                pass
            else:
                tipo = tok.replace("array di ", "").replace("array", "array") or tipo
        R["tipo_pos"].append(("", tipo, null, False, obb))

    corpo_radice(m.group(1))
    sez, attuale = "", ""
    for L in t.split("\n"):
        if L.startswith("## "):
            sez = L[3:]
            continue
        if not sez:
            continue                            # la testata (riga di radice compresa) e' gia' resa
        if sez == "Struttura obbligatoria" and re.match(r"^### ", L):
            attuale = "" if "radice dell'artefatto" in L else re.match(r"^### `([^`]*)`", L).group(1)
            continue
        m = re.match(r"^`([^`]+)`   ASSERITO   (.*)$", L)
        if m:
            p, corpo = m.group(1), m.group(2)
            if p in corpi:
                assert corpi[p] == corpo, (p, corpi[p], corpo)
            corpi[p] = corpo
            if sez == "Struttura obbligatoria" and attuale is not None and p.startswith(
                    attuale + "/" if attuale else ""):
                nome = p[len(attuale) + 1:] if attuale else p
                R["req"].append((attuale, nome))
            continue
        m = re.match(r"^- `([^`]+)` è \*\*(obbligatorio|vietato)\*\* quando `([^`]+)` "
                     r"[∈∉] (\[.*\])$", L)
        if m:
            perc, nome = m.group(1).rsplit("/", 1)
            v = tuple(json.loads(m.group(4)))
            (R["cond_obl"] if m.group(2) == "obbligatorio" else R["cond_div"]).append(
                (perc, nome, m.group(3), v))
    for p, corpo in corpi.items():
        R["percorsi"].append(p)
        tipo, null, elem, obb = None, False, False, False
        for tok in split_med(corpo):
            if tok == "obbligatorio":
                obb = True
            elif tok == "opzionale":
                pass
            elif tok == "ogni elemento":
                elem, obb = True, True
            elif tok == "nullable":
                null = True
            elif tok.startswith("enum ["):
                R["enum_pos"].append((p, tuple(json.loads(x) for x in split_med(tok[6:-1]))))
            elif tok.startswith("pattern "):
                R["pat_pos"].append((p, tok[8:]))
            elif tok.startswith("maxLength "):
                R["lim_pos"].append((p, "maxLength", int(tok[10:])))
            elif tok.startswith(("maxItems ", "minimum ", "maximum ")):
                pass
            elif tok.startswith("format "):
                R["format_pos"].append((p, tok[7:]))
            elif tok.startswith(("minItems ", "const ")):
                pass
            elif tok == "nessun campo extra":
                pass
            else:
                tipo = tok[len("array di "):] and "array" if tok.startswith("array di ") else tok
        R["tipo_pos"].append((p, tipo, null, elem, obb))
    return R


def budget_delivery(testo):
    """Il messaggio informativo di D13: quanto si e' sforato e su quale voce. I quattro blocchi
    (testa, ALBERO, VOCABOLARI E PATTERN, COND) partizionano il testo: sommano al byte."""
    n = len(testo.encode("utf-8"))
    if n <= 4784:
        return None
    i_albero = testo.index("== ALBERO")
    i_voc = testo.index("== VOCABOLARI")
    i_cond = testo.index("== COND")
    testa = len(testo[:i_albero].encode("utf-8"))
    albero = len(testo[i_albero:i_voc].encode("utf-8"))
    voc = len(testo[i_voc:i_cond].encode("utf-8"))
    cond = len(testo[i_cond:].encode("utf-8"))
    return ("delivery %d byte: budget 4 784 sforato di %d (testa %d; ALBERO %d; "
            "VOCABOLARI E PATTERN %d; COND %d)" % (n, n - 4784, testa, albero, voc, cond))


# ------------------------------------------------------------------ D1 … D13

@prova("D1", "la delivery, riletta dal testo, ha l'inventario completo degli schemi (mai dell'audit)")
def d1():
    d, s = copia_schemi()
    try:
        _, consegna = cc.compila_viste("evidence-researcher", s)
        A = inventario_schemi(s)
        B = leggi_delivery(consegna)
        for k in FAMIGLIE_D:
            a, b = set(map(repr, A[k])), set(map(repr, B[k]))
            assert not (a - b), f"{k}: manca nella delivery {sorted(a - b)[:6]}"
            assert not (b - a), f"{k}: in piu' nella delivery {sorted(b - a)[:6]}"

        def raggruppa(ord_):
            out = {}
            for p, n in ord_:
                if isinstance(n, list):
                    out.setdefault(p, []).extend(n)
                else:
                    out.setdefault(p, []).append(n)
            return {p: tuple(ns) for p, ns in out.items()}

        ordA, ordB = raggruppa(A["ordine"]), raggruppa(B["ordine"])
        div = [k for k in set(ordA) | set(ordB) if ordA.get(k, ()) != ordB.get(k, ())]
        assert not div, (
            "ordine dei figli diverso dagli schemi (required prima, poi properties): "
            f"{div[:8]}")
        assert len(A["req"]) == 70 and len(A["cond_obl"]) == 3 and len(A["cond_div"]) == 1, \
            (len(A["req"]), len(A["cond_obl"]), len(A["cond_div"]))
        assert len(A["enum_pos"]) == 22 and sum(len(v) for _, v in A["enum_pos"]) == 114
        assert len(A["pat_pos"]) == 7 and len({r for _, r in A["pat_pos"]}) == 5
        assert len(A["lim_pos"]) == 2 and len(A["tipo_pos"]) == 174 and len(A["format_pos"]) == 1
        rif = consegna
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"]["coverage"].__setitem__("required", ["angles_covered"]))
        _, mut = cc.compila_viste("evidence-researcher", s)
        assert ("payload/coverage", "gaps") not in leggi_delivery(mut)["req"], "passo 3"
        assert sha(rif) != sha(mut), "passo 4: gli hash coincidono"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D2", "le due viste vengono dalla stessa compilazione: inventari identici, radice inclusa")
def d2():
    d, s = copia_schemi()
    try:
        audit, consegna = cc.compila_viste("evidence-researcher", s)
        A = leggi_audit_inventario(audit)
        B = leggi_delivery(consegna)
        for k in FAMIGLIE_D:
            a, b = set(map(repr, A[k])), set(map(repr, B[k]))
            assert a == b, (
                f"{k}: le due viste differiscono. soloA={sorted(a - b)[:4]} soloB={sorted(b - a)[:4]}")
        assert ("", "object", False, False, True) in A["tipo_pos"], \
            "la radice non e' object/obbligatoria nell'inventario della audit"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D3", "la delivery porta l'hash della audit generata nella stessa invocazione")
def d3():
    d, s = copia_schemi()
    try:
        audit, consegna = cc.compila_viste("evidence-researcher", s)
        assert consegna.splitlines()[1] == "audit sha256 " + sha(audit), \
            "l'hash incollato non e' quello della audit della stessa invocazione"
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["Claim"].__setitem__(
                      "required", [x for x in o["$defs"]["Claim"]["required"] if x != "state"]))
        audit2, consegna2 = cc.compila_viste("evidence-researcher", s)
        assert consegna2.splitlines()[1] == "audit sha256 " + sha(audit2), "passo 3"
        assert sha(audit) != sha(audit2) and \
            consegna.splitlines()[1] != consegna2.splitlines()[1], \
            "passo 4: una mutazione di schema non ha cambiato gli hash"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D4", "determinismo della delivery: in-process, processi distinti, copia CRLF")
def d4():
    d, s = copia_schemi()
    d2, s2 = copia_schemi()
    try:
        a = sha(cc.compila_viste("evidence-researcher", s)[1])
        b = sha(cc.compila_viste("evidence-researcher", s)[1])
        c1, o1, e1 = esegui(s, ("--vista", "delivery"))
        c2, o2, e2 = esegui(s, ("--vista", "delivery"))
        assert c1 == 0 and c2 == 0, f"uscite {c1}/{c2}: {e1}{e2}"
        per, da = sha(o1), sha(o2)
        for nome in os.listdir(s2):
            p = os.path.join(s2, nome)
            t = open(p, "rb").read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            open(p, "wb").write(t)
        e = sha(cc.compila_viste("evidence-researcher", s2)[1])
        assert a == b == per == da == e, (
            f"cinque compilazioni, hash diversi:\n  in-process {a}\n  in-process {b}\n"
            f"  processo    {per}\n  processo    {da}\n  CRLF        {e}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


@prova("D5", "ordine derivato dal documento: required prima, tabelle per prima apparizione, "
             "nessun set/sorted")
def d5():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        src = "\n".join(inspect.getsource(f) for f in (
            cc.rendi_delivery, cc._gettoni_delivery, cc._vincoli_extra_delivery, cc._sigla_tipo))
        assert not re.search(r"\bset\s*\(", src), "set() nel percorso di resa della delivery"
        assert not re.search(r"\bsorted\s*\(", src), "sorted() nel percorso di resa della delivery"
        albero = c.split("== VOCABOLARI E PATTERN", 1)[0].split("== ALBERO", 1)[1]
        figli = []
        for L in albero.splitlines()[1:]:       # la prima riga e' il resto dell'intestazione
            if len(L) - len(L.lstrip(" ")) == 1:
                riga = L.strip()
                figli.append((riga[1:] if riga.startswith("!") else riga).split(" ")[0])
        assert figli == ["agent", "run_id", "status", "budget_used", "stop_reason", "payload",
                         "new_sources", "queries_run", "unreachable_sources", "self_check",
                         "pain_id"], f"ordine di radice: {figli}"
        assert figli.index("payload") == 5, "payload non e' il sesto dei dieci obblighi"
        tabelle = c.split("== COND", 1)[0].split("== VOCABOLARI E PATTERN", 1)[1]
        seqE = [tuple(L.split(" ", 1)[1].split("|")) for L in tabelle.splitlines()
                if re.match(r"^E\d+ ", L)]
        attesiE = [
            ("OK", "PARTIAL", "FAILED"),
            ("COMPLETED", "BUDGET_EXHAUSTED", "INSUFFICIENT_DATA", "KILL_TRIGGERED",
             "ABORTED_BY_ORCHESTRATOR", "ZERO_YIELD"),
            ("normativa", "statistica_ufficiale", "community", "pricing_mercato",
             "trend_adozione"),
            ("FP0", "FP1", "FP2", "FP3", "FP4", "FP5"),
            ("VERIFIED", "ESTIMATED", "UNKNOWN"),
            ("VERIFIED", "ESTIMATED", "HYPOTHESIS", "UNKNOWN"),
            ("SERP", "FETCHED"),
            ("PUBBLICAZIONE", "COMPORTAMENTO", "ARTEFATTO"),
            ("Q1", "Q2", "Q3", "Q4", "Q5"),
            ("software", "personale_interno", "consulenza_esterna", "penali_o_sanzioni",
             "perdite_operative"),
            ("T1", "T2", "T3", "T4", "T5"),
            ("OK", "PAYWALL", "BLOCKED", "NOT_FOUND", "ROBOTS", "PARTIAL", "API_LIMIT",
             "LOGIN_REQUIRED", "NO_PUBLIC_DATA"),
            ("normativa", "statistica_ufficiale", "competitor", "pricing", "community",
             "trend_adozione", "fallimenti_storici", "job_posting", "bilanci", "recensioni",
             "wayback", "integrazioni", "falsificazione", "stagionalita", "capitolati_e_bandi",
             "mietitura_annunci", "mietitura_marketplace_integrazioni",
             "mietitura_forum_assistenza", "mietitura_capitolati", "mietitura_circolari_categoria"),
            ("USEFUL", "PARTIAL", "EMPTY"),
            ("PAYWALL", "BLOCKED", "NOT_FOUND", "ROBOTS", "API_LIMIT", "LOGIN_REQUIRED",
             "NO_PUBLIC_DATA", "BUDGET_EXHAUSTED", "STOP_RULE"),
            ("IC-1", "IC-2", "IC-3", "IC-4", "SEP-RETRIEVAL", "SEP-JUDGMENT", "TRE-STATI",
             "NUMERI")]
        assert seqE == attesiE, f"sequenza E# sbagliata: {seqE[:4]} …"
        seqP = [L.split(" ", 1)[1] for L in tabelle.splitlines() if re.match(r"^P\d+ ", L)]
        assert seqP == ["^[0-9]{8}(-[0-9]{4})?$",
                        "^C-[A-Za-z0-9]{2,20}(-contra)?-[0-9]{3}(?![\\s\\S])",
                        "^S-[0-9]{8}(-[0-9]{4})?-[0-9]{4}$",
                        "^([0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?|UNKNOWN)$",
                        "^P-[0-9]{3}$"], f"sequenza P# sbagliata: {seqP}"
        conds = [L for L in c.split("== COND", 1)[1].splitlines() if L.startswith(("+", "-"))]
        assert conds == [
            "+payload/claims[]/promotion_rule_met|state|VERIFIED",
            "+payload/contradictory_evidence[]/promotion_rule_met|state|VERIFIED",
            "+unreachable_sources[]/source_id|why|PAYWALL,BLOCKED,NOT_FOUND,ROBOTS,API_LIMIT,"
            "LOGIN_REQUIRED,NO_PUBLIC_DATA",
            "-unreachable_sources[]/source_id|why|PAYWALL,BLOCKED,NOT_FOUND,ROBOTS,API_LIMIT,"
            "LOGIN_REQUIRED,NO_PUBLIC_DATA"], f"ordine dei condizionali: {conds}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D6", "nessun letterale di campo, enum o regex che non provenga dagli schemi")
def d6():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        A = inventario_schemi(s)
        B = leggi_delivery(c)
        assert set(B["percorsi"]) == set(A["percorsi"]), (
            "percorsi diversi dagli schemi:\n  in piu' "
            f"{sorted(set(B['percorsi']) - set(A['percorsi']))[:5]}\n  mancanti "
            f"{sorted(set(A['percorsi']) - set(B['percorsi']))[:5]}")
        valori = {v for _, t in A["enum_pos"] for v in t}
        regex = {r for _, r in A["pat_pos"]}
        assert all(v in valori for _, t in B["enum_pos"] for v in t), "un valore d'enum inventato"
        assert all(r in regex for _, r in B["pat_pos"]), "una regex inventata"
        # controprova: un valore fantasma incollato in una tabella E# deve far scattare il
        # rilevatore. La riga da gonfiare si trova nel testo, non con un letterale: la prova
        # deve sopravvivere a una rinumerazione legittima delle tabelle.
        m = re.search(r"^E\d+ .+$", c, re.M)
        assert m, "nessuna tabella E# nel testo della delivery"
        gonfio = c.replace(m.group(0), m.group(0) + "|FANTASMA", 1)
        B2 = leggi_delivery(gonfio)
        assert any(v not in valori for _, t in B2["enum_pos"] for v in t), \
            "il rilevatore non vede un valore d'enum inventato"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D7", "la delivery non porta prosa, e lo dichiara")
def d7():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        assert c.startswith("CONTRATTO evidence-researcher <- evidence-pack.schema.json\n"
                            "audit sha256 "), "manca la riga che rinvia alla vista audit"
        for p, t in descrizioni_raggiungibili(s):
            assert t not in c, f"la description di {p} compare nella delivery"
        # controprova: una description incollata deve far scattare il rilevatore
        testo = "ignoto  " + descrizioni_raggiungibili(s)[0][1] + "  ignoto"
        assert any(t in testo for _, t in descrizioni_raggiungibili(s)), \
            "la controprova non rileva una description incollata"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D8", "la trappola dei vocabolari omonimi: pricing mai nella tabella di pricing_mercato")
def d8():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        B = leggi_delivery(c)
        canale = [v for p, v in B["enum_pos"] if p.endswith("/channel")]
        angolo = [v for p, v in B["enum_pos"] if p.endswith("queries_run[]/angle")]
        assert len(canale) == 1 and len(angolo) == 1, (canale, angolo)
        assert "pricing_mercato" in canale[0] and "pricing" not in canale[0], \
            "pricing_mercato non sta nella sua tabella, o pricing ci e' finito dentro"
        assert "pricing" in angolo[0] and "pricing_mercato" not in angolo[0], \
            "pricing non sta nella sua tabella, o pricing_mercato ci e' finito dentro"
        # la controprova che la trappola esiste: per SOTTOSTRINGA pricing e' in entrambi
        assert any("pricing" in x for x in canale[0]), \
            "confrontare per sottostringa darebbe l'esito sbagliato, e questa prova lo dichiara"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D9", "l'asimmetria di Claim sopravvive: promoted_by solo sotto claims[]")
def d9():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        B = leggi_delivery(c)
        assert ("payload/claims[]", "promoted_by") in B["req"], "promoted_by manca sotto claims[]"
        assert ("payload/contradictory_evidence[]", "promoted_by") not in B["req"], \
            "promoted_by e' comparso sotto contradictory_evidence[]"
        assert "payload/claims[]/promoted_by" in B["percorsi"]
        assert "payload/contradictory_evidence[]/promoted_by" not in B["percorsi"]
        rif = c
        muta_json(s, "evidence-pack.schema.json", lambda o: o["properties"]["payload"]
                  ["properties"]["claims"]["items"].pop("required"))
        _, mut = cc.compila_viste("evidence-researcher", s)
        assert ("payload/claims[]", "promoted_by") not in leggi_delivery(mut)["req"], "passo 3"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D10", "la disgiunzione: zero obblighi sotto statement/quantity, 16 figlie, amount ne ha 4")
def d10():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        A = inventario_schemi(s)
        B = leggi_delivery(c)
        Q = "payload/claims[]/statement/quantity"
        assert all(p != Q for p, _ in B["req"]), "required sotto la disgiunzione anyOf"
        figli = [p for p in A["percorsi"] if p.startswith(Q + "/")]
        assert len(figli) == 16, \
            f"attese 16 posizioni figlie (14 proprieta' + 2 elementi), lette {len(figli)}"
        assert any(p == Q and null for p, _, null, _, _ in A["tipo_pos"]), \
            "quantity non e' nullable"
        req_amount = {n for p, n in A["req"] if p == "payload/spend_evidence[]/amount"}
        assert req_amount == {"label", "value", "unit", "state"}, \
            f"amount ($ref diretto) non ha i suoi quattro: {req_amount}"
        # falsificazione: rimuovere la disgiunzione (anyOf → $ref nudo) fa tornare i quattro
        # required di Numeric congiuntivi, e la delivery deve dichiararli obblighi
        rif = c
        muta_json(s, "core.schema.json",
                  lambda o: o["$defs"]["Claim"]["properties"]["statement"]["properties"]
                  .__setitem__("quantity", {"$ref": "core.schema.json#/$defs/Numeric"}))
        _, mut = cc.compila_viste("evidence-researcher", s)
        assert ("payload/claims[]/statement/quantity", "label") in leggi_delivery(mut)["req"], \
            "passo 3: senza disgiunzione i required di Numeric devono tornare obblighi"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D11", "la guardia condizionale per intero, e il const mai come vincolo di state")
def d11():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        B = leggi_delivery(c)
        guardie = [v for p, n, campo, v in B["cond_obl"] + B["cond_div"]
                   if n == "source_id" and campo == "why"]
        assert len(guardie) == 2 and all(v == tuple(SETTE) for v in guardie), \
            f"guardia non resa per intero: {guardie}"
        stati = [v for p, v in B["enum_pos"] if p.endswith("[]/state")]
        assert all(set(v) == {"VERIFIED", "ESTIMATED", "HYPOTHESIS", "UNKNOWN"} for v in stati), \
            f"state non porta i quattro stati: {stati}"
        assert "const" not in c, "un const e' comparso nella delivery"
        rif = c
        muta_json(s, "core.schema.json", lambda o: o["$defs"]["UnreachableSource"]["allOf"][0]
                  ["if"]["properties"]["why"].__setitem__("enum", SETTE[:1]))
        _, mut = cc.compila_viste("evidence-researcher", s)
        g2 = [v for p, n, campo, v in leggi_delivery(mut)["cond_obl"]
              + leggi_delivery(mut)["cond_div"] if n == "source_id"]
        assert all(v != tuple(SETTE) for v in g2), "passo 3: la guardia troncata non e' scomparsa"
        assert sha(rif) != sha(mut), "passo 4"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D12", "il tipo di radice, nella vista audit")
def d12():
    d, s = copia_schemi()
    try:
        audit, _ = cc.compila_viste("evidence-researcher", s)
        riga = "`(radice dell'artefatto)`   ASSERITO   obbligatorio · object"
        testa = audit.split("## ", 1)[0]
        assert riga in testa, "la radice non e' dichiarata object, o non sta in testata"
        assert riga not in sezione(audit, "Struttura obbligatoria"), (
            "la riga di radice sta in «Struttura obbligatoria»: il parser obblighi() la "
            "leggerebbe come un figlio fittizio")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("D13", "budget di crescita: la delivery non supera 4 784 byte senza dichiararlo")
def d13():
    d, s = copia_schemi()
    try:
        _, c = cc.compila_viste("evidence-researcher", s)
        assert budget_delivery(c) is None, budget_delivery(c)
        # controprova: gonfiata, il messaggio di sforamento nomina lo scarto e le voci.
        # Lo scarto atteso e' calcolato dalla misura corrente, non da un letterale: la prova
        # deve sopravvivere a una crescita legittima entro il budget.
        n0 = len(c.encode("utf-8"))
        msg = budget_delivery(c + "x" * 500)
        assert msg and ("sforato di %d" % (n0 + 500 - 4784)) in msg \
            and "testa" in msg and "COND" in msg, msg
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ E1 … E13 (consegna, M3)

def fixture_consegna(marcatore=None):
    """Un repository usa-e-getta col layout minimo per la consegna: schemi veri, prompt base
    REALE (il file redatto del repo), copie del compilatore e di provenance_check, git locale.
    La directory del run e' una temp dir separata: nessun `runs/` reale viene mai toccato.
    `marcatore` rende il commit diverso da quello di un fixture gemello."""
    d = tempfile.mkdtemp(prefix="cc-consegna-")
    root = os.path.join(d, "repo")
    os.makedirs(os.path.join(root, "schemas"))
    os.makedirs(os.path.join(root, ".claude", "agents"))
    os.makedirs(os.path.join(root, "config"))
    os.makedirs(os.path.join(root, "tools"))
    for nome in ("evidence-pack.schema.json", "core.schema.json"):
        shutil.copy(os.path.join(SCHEMI, nome), os.path.join(root, "schemas", nome))
    shutil.copy(os.path.join(ROOT, ".claude", "agents", "evidence-researcher.md"),
                os.path.join(root, ".claude", "agents", "evidence-researcher.md"))
    with open(os.path.join(root, "config", "deepsearch.config.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({"verifica_specifica": {
            "radici_vietate": [],
            "schema_per_agente": [
                {"schema": "evidence-pack.schema.json", "agente": "evidence-researcher"}]}},
            f, ensure_ascii=False, indent=2)
        f.write("\n")
    if marcatore is not None:
        open(os.path.join(root, "marcatore.txt"), "w", encoding="utf-8").write(marcatore + "\n")
    for nome in ("contract_compile.py", "provenance_check.py"):
        shutil.copy(os.path.join(QUI, nome), os.path.join(root, "tools", nome))

    def g(*args):
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=120)
        assert r.returncode == 0, f"git {args}: {r.stderr.decode('utf-8', 'replace')}"
        return r
    g("init")
    for k, v in (("core.autocrlf", "false"), ("core.safecrlf", "false")):
        g("config", k, v)
    g("add", "-A")
    g("-c", "user.name=selftest", "-c", "user.email=selftest@locale", "commit", "-m", "fixture")
    commit = g("rev-parse", "HEAD").stdout.decode().strip()
    return d, root, commit


def consegna(root, commit, dir_run, mandati=()):
    r = subprocess.run(
        [sys.executable, os.path.join(root, "tools", "contract_compile.py"),
         "--agente", "evidence-researcher", "--schemi", os.path.join(root, "schemas"),
         "--commit", commit, "--consegna", dir_run]
        + (["--mandato"] + list(mandati) if mandati else []),
        capture_output=True, timeout=180)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def provenance(root, commit, *args):
    r = subprocess.run(
        [sys.executable, os.path.join(root, "tools", "provenance_check.py"),
         "--radice-da", os.path.join(root, ".claude", "agents", "evidence-researcher.md"),
         "--commit", commit] + list(args),
        capture_output=True, timeout=180)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def mandato(root, pain_id="P-003", channel="normativa"):
    p = os.path.join(root, f"mandato-{channel}.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"run_id": "20260822-0000", "pain": {"id": pain_id}, "channel": channel,
                   "evidence_seeds": [], "budget": {"searches": 8, "fetches": 16}}, f,
                  ensure_ascii=False, indent=2)
        f.write("\n")
    return p


@prova("E1", "il prompt finale e' norm(base) + giunzione + delivery, nell'ordine esatto")
def e1():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        base = open(os.path.join(root, ".claude", "agents", "evidence-researcher.md"),
                    "rb").read()
        consegnato = open(os.path.join(out, "_prompts", "evidence-researcher.md"), "rb").read()
        delivery = open(os.path.join(out, "_contracts", "evidence-researcher.delivery.txt"),
                        "rb").read()
        assert consegnato == base.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n" \
            + b"\n\n---\n\n" + delivery, "ordine o contenuto del prompt finale sbagliati"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E2", "la giunzione e' byte-esatta e la delivery e' l'ultimo blocco")
def e2():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        consegnato = open(os.path.join(out, "_prompts", "evidence-researcher.md"), "rb").read()
        delivery = open(os.path.join(out, "_contracts", "evidence-researcher.delivery.txt"),
                        "rb").read()
        base = open(os.path.join(root, ".claude", "agents", "evidence-researcher.md"),
                    "rb").read().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
        assert consegnato == base + b"\n\n---\n\n" + delivery, \
            "la giunzione non e' byte-esatta (o la composizione non e' quella prescritta)"
        assert consegnato.endswith(delivery), "la delivery non e' l'ultimo blocco del documento"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E3", "il prompt finale e' ricostruibile dai suoi componenti")
def e3():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        base = open(os.path.join(root, ".claude", "agents", "evidence-researcher.md"),
                    "rb").read().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
        delivery = open(os.path.join(out, "_contracts", "evidence-researcher.delivery.txt"),
                        "rb").read()
        consegnato = open(os.path.join(out, "_prompts", "evidence-researcher.md"), "rb").read()
        assert consegnato == base + b"\n\n---\n\n" + delivery
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E4", "le scritture sono atomiche: mai file parziali con nome definitivo")
def e4():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        for p in ("_contracts/evidence-researcher.audit.md",
                  "_contracts/evidence-researcher.delivery.txt",
                  "_prompts/evidence-researcher.md"):
            f = os.path.join(out, *p.split("/"))
            b = open(f, "rb").read()
            assert b.endswith(b"\n"), f"{p} troncato o senza newline finale"
            residui = [x for x in os.listdir(os.path.dirname(f))
                       if x.startswith(".consegna-") and x.endswith(".tmp")]
            assert not residui, f"residui temporanei accanto a {p}: {residui}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E5", "i mandati finiscono nella sottodirectory del pain, atomici")
def e5():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        m1 = mandato(root, pain_id="P-003", channel="normativa")
        m2 = mandato(root, pain_id="P-003", channel="community")
        codice, _, err = consegna(root, commit, out, mandati=(m1, m2))
        assert codice == 0, f"uscita {codice}: {err}"
        p = os.path.join(out, "02-evidence", "P-003", "mandato-normativa.json")
        assert os.path.exists(p), "mandato normativa assente"
        assert json.load(open(p, encoding="utf-8"))["channel"] == "normativa"
        p2 = os.path.join(out, "02-evidence", "P-003", "mandato-community.json")
        assert os.path.exists(p2), "mandato community assente"
        assert json.load(open(p2, encoding="utf-8"))["channel"] == "community"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E6", "la doppia compilazione in memoria deve coincidere (determinismo della consegna)")
def e6():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        for _ in range(2):
            codice, _, err = consegna(root, commit, out)
            assert codice == 0, f"uscita {codice}: {err}"
        a = open(os.path.join(out, "_contracts", "evidence-researcher.audit.md"), "rb").read()
        c = open(os.path.join(out, "_prompts", "evidence-researcher.md"), "rb").read()
        codice, _, err = consegna(root, commit, out)
        assert codice == 0
        assert a == open(os.path.join(out, "_contracts", "evidence-researcher.audit.md"),
                         "rb").read(), "due consegne, audit diversi"
        assert c == open(os.path.join(out, "_prompts", "evidence-researcher.md"),
                         "rb").read(), "due consegne, prompt diversi"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E7", "il base non contiene i due marker della sezione manuale rimossa")
def e7():
    base = open(os.path.join(ROOT, ".claude", "agents", "evidence-researcher.md"),
                encoding="utf-8").read()
    assert "| campo | dove | valori ammessi |" not in base, "la tabella manuale e' ricomparsa"
    assert "mietitura_circolari_categoria" not in base, "l'enumerazione degli angoli e' ricomparsa"


@prova("E7bis", "il base committato coincide con la redazione esatta: 12 958 byte, digest atteso")
def e7bis():
    b = open(os.path.join(ROOT, ".claude", "agents", "evidence-researcher.md"), "rb").read()
    b = b.replace(b"\r\n", b"\n")
    assert len(b) == 12958, f"base {len(b)} byte, attesi 12 958: la redazione non e' quella esatta"
    assert hashlib.sha256(b).hexdigest() == \
        "1f7d8ba9fb3b19c3d16b5adbca9c66798932be54282455c3d0fff607e868f544", \
        "il base e' cambiato rispetto alla redazione inchiodata: E7bis va riaperto deliberatamente"


@prova("E8", "consegna senza la vista delivery: il preflight la pretende")
def e8():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        codice, _, err = provenance(root, commit, "--agente", "evidence-researcher",
                                    "--audit", os.path.join(out, "_contracts",
                                                           "evidence-researcher.audit.md"))
        assert codice == 1 and "viste incomplete" in err, (codice, err)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E9", "una delivery di un altro schema/agente non passa")
def e9():
    d, root, commit = fixture_consegna(marcatore="primo")
    d2, root2, commit2 = fixture_consegna(marcatore="secondo")
    out = tempfile.mkdtemp(prefix="cc-run-")
    out2 = tempfile.mkdtemp(prefix="cc-run2-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        # la delivery di UN ALTRO albero (altro commit, altra audit): il legame hash non regge
        # e la ricompilazione dagli schemi verificati non la riproduce
        codice, _, err = consegna(root2, commit2, out2)
        assert codice == 0, f"uscita {codice}: {err}"
        assert commit != commit2, "i due fixture hanno lo stesso commit: prova rotta"
        codice, _, err = provenance(root, commit, "--agente", "evidence-researcher",
                                    "--audit", os.path.join(out, "_contracts",
                                                           "evidence-researcher.audit.md"),
                                    "--delivery", os.path.join(
                                        out2, "_contracts", "evidence-researcher.delivery.txt"))
        assert codice == 1 and ("non coincidente" in err or "vista non riprodotta" in err), \
            (codice, err)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(out2, ignore_errors=True)


@prova("E10", "audit e delivery non collegati non passano")
def e10():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        audit_p = os.path.join(out, "_contracts", "evidence-researcher.audit.md")
        delivery_p = os.path.join(out, "_contracts", "evidence-researcher.delivery.txt")
        # si scollegano: la delivery dichiara un audit che non e' quello su disco
        t = open(delivery_p, encoding="utf-8").read()
        open(delivery_p, "w", encoding="utf-8", newline="\n").write(
            t.replace(t.splitlines()[1], "audit sha256 " + "0" * 64, 1))
        codice, _, err = provenance(root, commit, "--agente", "evidence-researcher",
                                    "--audit", audit_p, "--delivery", delivery_p)
        assert codice == 1 and "non coincidente" in err, (codice, err)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E11", "un prompt scritto parzialmente blocca la consegna: nessuna invocazione")
def e11():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        prompt_p = os.path.join(out, "_prompts", "evidence-researcher.md")
        b = open(prompt_p, "rb").read()
        open(prompt_p, "wb").write(b[:len(b) // 2])          # troncato a meta'
        codice, _, err = provenance(root, commit, "--agente", "evidence-researcher",
                                    "--audit", os.path.join(out, "_contracts",
                                                           "evidence-researcher.audit.md"),
                                    "--delivery", os.path.join(
                                        out, "_contracts", "evidence-researcher.delivery.txt"),
                                    "--prompt-consegnato", prompt_p)
        assert codice == 1, (codice, err)
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


@prova("E12", "la consegna non tenta rete: nessun import e nessun socket")
def e12():
    import socket
    tentativi = []
    vero = socket.socket.connect

    def spia(self, *a, **k):
        tentativi.append(a)
        raise AssertionError("la consegna ha tentato una connessione")
    socket.socket.connect = spia
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        assert not tentativi
    finally:
        socket.socket.connect = vero
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
    for src in (os.path.join(QUI, "contract_compile.py"),
                os.path.join(QUI, "provenance_check.py")):
        testo = open(src, encoding="utf-8").read()
        for lib in ("socket", "http", "urllib", "requests", "ftplib", "smtplib"):
            assert not re.search(r"^\s*(import|from)\s+" + lib + r"\b", testo, re.M), \
                f"{os.path.basename(src)} importa {lib}"


@prova("E13", "il preflight verifica il prompt finale e lo registra nel manifesto")
def e13():
    d, root, commit = fixture_consegna()
    out = tempfile.mkdtemp(prefix="cc-run-")
    try:
        codice, _, err = consegna(root, commit, out)
        assert codice == 0, f"uscita {codice}: {err}"
        audit_p = os.path.join(out, "_contracts", "evidence-researcher.audit.md")
        delivery_p = os.path.join(out, "_contracts", "evidence-researcher.delivery.txt")
        prompt_p = os.path.join(out, "_prompts", "evidence-researcher.md")
        m1 = mandato(root)
        manifesto = os.path.join(out, "manifest.json")
        codice, _, err = provenance(root, commit, "--agente", "evidence-researcher",
                                    "--audit", audit_p, "--delivery", delivery_p,
                                    "--prompt-consegnato", prompt_p, "--mandato", m1,
                                    "--manifest", manifesto)
        assert codice == 0, f"uscita {codice}: {err}"
        m = json.load(open(manifesto, encoding="utf-8"))
        assert m["prompt_finale"]["sha256"] == hashlib.sha256(
            open(prompt_p, "rb").read()).hexdigest()
        assert m["mandati"][0]["pain_id"] == "P-003" and m["mandati"][0]["channel"] == "normativa"
        assert all("ruolo" in c for c in m["componenti"])
        assert m["prompt_base"]["ruolo"] == "prompt_base"
        assert m["compilatore"]["ruolo"] == "compilatore" and m["compilatore"]["dimensione"] > 0
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------------------------------ esecuzione

def main():
    print(f"contract_compile_selftest — {len(ESITI)} prove\n")
    falliti = []
    for nome, desc, f in ESITI:
        try:
            f()
            print(f"  [verde] {nome:<5} {desc}")
        except Exception as e:                                            # noqa: BLE001
            falliti.append((nome, e))
            print(f"  [ROSSO] {nome:<5} {desc}\n          {type(e).__name__}: {e}")
    print()
    if falliti:
        print(f"  {len(ESITI) - len(falliti)}/{len(ESITI)} verdi — ROSSE: "
              + ", ".join(n for n, _ in falliti))
        return 1
    print(f"  {len(ESITI)}/{len(ESITI)} verdi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
