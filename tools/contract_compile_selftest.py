#!/usr/bin/env python3
"""
contract_compile_selftest.py — dimostra che contract_compile.py NON e' cieco.

Un compilatore che gira non dice niente. Il contratto che produce puo' essere giusto, oppure puo'
essere una pagina plausibile che ha perso meta' degli obblighi in silenzio: la differenza non e'
ispezionabile leggendo l'output, perche' un contratto incompleto ha lo stesso aspetto di uno
completo. Si dimostra solo mutando gli schemi e pretendendo che il contratto cambi.

VENTIQUATTRO PROVE, e la forma di ognuna e' vincolata. Il piano ne prevedeva ventuno: F22, F23 e
F24 sono aggiunte, e ciascuna nasce da un difetto che si e' visto passare verde. F22 dalla prosa
persa dalle posizioni d'elemento; F23 dalla guardia condizionale troncata, che faceva dichiarare al
contratto una regola FALSA; F24 dalle rese — maxLength, nullable, tipo degli elementi, sezioni —
che nessuna mutazione di schema tocca, perche' a cambiare non e' lo schema ma il renditore.

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
        exit 0 = tutte e ventiquattro verdi
"""
import hashlib, io, json, os, re, shutil, subprocess, sys, tempfile
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
