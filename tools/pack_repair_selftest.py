#!/usr/bin/env python3
"""
pack_repair_selftest.py — dimostra che pack_repair.py ripara la FORMA e non tocca i FATTI.

LA PROVA CENTRALE NON E' CHE RIPARI: E' CHE NON INVENTI. Un riparatore che aggiusta la struttura
e' utile; un riparatore che per aggiustarla si inventa un `source_id`, sceglie un valore di enum
o tronca una citazione ha fabbricato una prova, che in questa pipeline e' il fallimento piu' grave
possibile. Per questo l'invariante e' formulato in modo da essere MECCANICO e non a giudizio:

    l'insieme multiplo delle FOGLIE SCALARI del pack riparato e' IDENTICO a quello del pack di
    partenza — nessuno scalare aggiunto, nessuno perso.

Le tre riparazioni ammesse lo rispettano per costruzione: omettere una chiave che vale `null` non
toglie alcuno scalare, avvolgere un valore in un array lo sposta senza cambiarlo, scartare un
involucro cambia un annidamento. Tutto cio' che non lo rispetta e' rifiutato per definizione, e
qui si prova che lo e' davvero.

I CASI SONO QUELLI VERI. `amount: null` e' il frammento verbatim di `trend_adozione`;
`new_sources[].stale: null` e' quello di `community` nel replay 20260824-2143; il
`source_id` mancante su una fonte irraggiungibile e' la violazione di IC-4 misurata nel replay
20260823-1647. Il contratto non e' una fixture: si rigenera con `contract_compile.py`.

  uso:  python tools/pack_repair_selftest.py
        exit 0 = tutte verdi
"""
import collections, contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(QUI)
sys.path.insert(0, QUI)

import pack_repair as pr                                                  # noqa: E402
import replay_validate as _rv                                              # noqa: E402

ESITI = []
_CONTRATTO = None


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


def contratto():
    global _CONTRATTO
    if _CONTRATTO is None:
        r = subprocess.run([sys.executable, os.path.join(QUI, "contract_compile.py"),
                            "--agente", "evidence-researcher", "--vista", "delivery"],
                           capture_output=True, timeout=120)
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
        _CONTRATTO = r.stdout.decode("utf-8")
    return _CONTRATTO


# ------------------------------------------------------------------ i pack

def pack_valido():
    """Un evidence-pack conforme, coi soli null che il contratto ammette."""
    return {"agent": "evidence-researcher", "run_id": "20260824-2143", "status": "OK",
            "budget_used": {"searches": 1, "searches_limit": 8,
                            "fetches": 1, "fetches_limit": 16},
            "stop_reason": "COMPLETED",
            "payload": {"pain_id": "P-003", "channel": "trend_adozione",
                        "claims": [{"promoted_by": "FP0", "id": "C-r22t-001",
                                    "statement": {"subject": "s", "observed": "o",
                                                  "quantity": None, "period": None},
                                    "state": "HYPOTHESIS", "source_ids": []}],
                        "q4_answer": {"question_id": "Q4", "answer": "a", "grounded": False,
                                      "source_ids": []},
                        "coverage": {"angles_covered": [], "gaps": []}},
            "new_sources": [], "queries_run": [], "unreachable_sources": [],
            "self_check": {"invarianti_violati": []}}


def raw_da(pack):
    return ("Based on my research, I'll now compile the evidence pack:\n\n"
            "```json\n" + json.dumps(pack, ensure_ascii=False, indent=2) + "\n```\n")


def scrivi(p, testo):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(testo)


def esegui(pack_o_testo, extra=()):
    """Scrive il raw in una temporanea e invoca il riparatore. Ritorna (exit, rapporto, stderr)."""
    d = tempfile.mkdtemp(prefix="pr-selftest-")
    try:
        raw = os.path.join(d, "canale.raw.txt")
        scrivi(raw, pack_o_testo if isinstance(pack_o_testo, str) else raw_da(pack_o_testo))
        con = os.path.join(d, "delivery.txt")
        scrivi(con, contratto())
        r = subprocess.run([sys.executable, os.path.join(QUI, "pack_repair.py"),
                            "--raw", raw, "--contratto", con,
                            "--schemi", os.path.join(ROOT, "schemas")] + list(extra),
                           capture_output=True, timeout=300)
        out = r.stdout.decode("utf-8", "replace")
        try:
            rapporto = json.loads(out)
        except json.JSONDecodeError:
            rapporto = None
        return r.returncode, rapporto, r.stderr.decode("utf-8", "replace")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def foglie(x):
    """L'insieme multiplo degli scalari raggiungibili. `None` NON e' uno scalare: e' l'assenza
    di un dato, ed e' l'unica cosa che una riparazione puo' far sparire."""
    acc = collections.Counter()

    def giu(v):
        if isinstance(v, dict):
            for k in v:
                giu(v[k])
        elif isinstance(v, list):
            for e in v:
                giu(e)
        elif v is not None:
            acc[(type(v).__name__, json.dumps(v, ensure_ascii=False))] += 1
    giu(x)
    return acc


def invariante(rapporto, partenza):
    """Nessuno scalare aggiunto, nessuno perso: e' la prova che i fatti non sono stati toccati."""
    a, b = foglie(partenza), foglie(rapporto["pack_riparato"])
    inventati = b - a
    persi = a - b
    assert not inventati, f"scalari INVENTATI dalla riparazione: {list(inventati)[:5]}"
    assert not persi, f"scalari PERSI dalla riparazione: {list(persi)[:5]}"


# ------------------------------------------------------------------ le prove

@prova("P1", "`amount: null` (il caso reale) viene OMESSO e il pack diventa valido")
def p1():
    p = pack_valido()
    p["payload"]["spend_evidence"] = [
        {"claim_id": "C-r22t-002", "spend_form": "software", "amount": None}]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    assert rap["verdetto_prima"]["verdetto"] == "NON VALIDO", rap["verdetto_prima"]
    assert "amount" not in rap["pack_riparato"]["payload"]["spend_evidence"][0], \
        rap["pack_riparato"]["payload"]["spend_evidence"][0]
    assert rap["verdetto_dopo"]["verdetto"] == "VALIDO", rap["verdetto_dopo"]
    assert codice == 0, f"uscita {codice}"
    assert any(r["regola"] == "R-A" for r in rap["riparazioni"]), rap["riparazioni"]
    invariante(rap, p)


@prova("P2", "`new_sources[].stale: null` (l'altro caso reale) viene OMESSO")
def p2():
    p = pack_valido()
    p["new_sources"] = [{"id": "S-20260824-2143-0041", "url": "https://e.it/a",
                         "publisher": "E", "title": "T", "published_date": "2025",
                         "accessed_at": "2026-08-24", "tier": "T3", "access_status": "OK",
                         "evidence_kind": "PUBBLICAZIONE", "retrieved_by": "evidence-researcher",
                         "stale": None}]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    assert "stale" not in rap["pack_riparato"]["new_sources"][0], rap["pack_riparato"]["new_sources"][0]
    assert rap["verdetto_dopo"]["verdetto"] == "VALIDO", rap["verdetto_dopo"]
    assert codice == 0
    invariante(rap, p)


@prova("P3", "`coverage.gaps` narrativo viene RIPRISTINATO ad array, verbatim")
def p3():
    p = pack_valido()
    p["payload"]["coverage"]["gaps"] = "nessuna fonte sui versamenti trimestrali"
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    g = rap["pack_riparato"]["payload"]["coverage"]["gaps"]
    assert g == ["nessuna fonte sui versamenti trimestrali"], g
    assert rap["verdetto_dopo"]["verdetto"] == "VALIDO", rap["verdetto_dopo"]
    assert codice == 0
    assert any(r["regola"] == "R-B" for r in rap["riparazioni"]), rap["riparazioni"]
    invariante(rap, p)


@prova("P4", "`source_id` obbligatorio mancante NON viene inventato: il pack resta rifiutato")
def p4():
    p = pack_valido()
    # IC-4: `why` fra i sette motivi «bussati» rende `source_id` obbligatorio. Non c'e'.
    p["unreachable_sources"] = [{"what": "portale comunale", "why": "BLOCKED",
                                 "impact": "nessun dato sulle aliquote"}]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    u = rap["pack_riparato"]["unreachable_sources"][0]
    assert "source_id" not in u, f"ha INVENTATO un source_id: {u}"
    assert rap["verdetto_dopo"]["verdetto"] == "NON VALIDO", rap["verdetto_dopo"]
    assert codice == 1, f"uscita {codice}, atteso 1 (resta non valido)"
    # il rifiuto deve NOMINARE il campo mancante: «manca qualcosa qui» non e' un verbale
    assert any("source_id" in r.get("dal_validatore", "") for r in rap["rifiuti"]), rap["rifiuti"]
    assert any(r.get("keyword") == "required" for r in rap["rifiuti"]), rap["rifiuti"]
    invariante(rap, p)


@prova("P5", "NESSUNA modifica ai dati di merito, su un pack che porta tutti i difetti insieme")
def p5():
    p = pack_valido()
    p["payload"]["claims"][0]["statement"]["subject"] = "tassa di soggiorno"
    p["payload"]["coverage"]["gaps"] = "una lacuna dichiarata"
    p["payload"]["spend_evidence"] = [
        {"claim_id": "C-r22t-002", "spend_form": "software", "amount": None}]
    p["unreachable_sources"] = [{"what": "portale", "why": "NOT_FOUND", "impact": "nessuno"}]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    invariante(rap, p)
    # e i fatti nominati sono ancora li', identici
    assert (rap["pack_riparato"]["payload"]["claims"][0]["statement"]["subject"]
            == "tassa di soggiorno")
    assert rap["pack_riparato"]["payload"]["coverage"]["gaps"] == ["una lacuna dichiarata"]
    assert rap["pack_riparato"]["unreachable_sources"][0]["why"] == "NOT_FOUND"
    assert rap["invariante_fatti"] == "verificato", rap["invariante_fatti"]


@prova("P6", "un `null` su chiave OBBLIGATORIA non si toglie: si dichiara e si resta invalidi")
def p6():
    p = pack_valido()
    p["payload"]["coverage"]["gaps"] = None          # `!gaps a<s>`: obbligatoria
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    assert rap["pack_riparato"]["payload"]["coverage"]["gaps"] is None, \
        "ha tolto una chiave obbligatoria"
    assert codice == 1, f"uscita {codice}, atteso 1"
    assert any("obbligator" in r["motivo"] for r in rap["rifiuti"]), rap["rifiuti"]


@prova("P7", "uno `snippet` oltre i 300 caratteri NON viene troncato: e' una citazione")
def p7():
    p = pack_valido()
    lungo = "x" * 420
    p["payload"]["claims"][0]["evidence"] = [
        {"source_id": "S-20260824-2143-0041", "snippet": lungo, "snippet_source": "SERP"}]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    dopo = rap["pack_riparato"]["payload"]["claims"][0]["evidence"][0]["snippet"]
    assert dopo == lungo, f"ha troncato una citazione: {len(dopo)} caratteri"
    assert codice == 1
    assert any("citazione" in r["motivo"] or "maxLength" in r["motivo"]
               for r in rap["rifiuti"]), rap["rifiuti"]


@prova("P8", "un envelope AVVOLTO viene scartato dall'involucro, senza perdere nulla")
def p8():
    p = pack_valido()
    codice, rap, err = esegui({"evidence_pack": p})
    assert rap is not None, err[:400]
    assert rap["pack_riparato"].get("agent") == "evidence-researcher", rap["pack_riparato"].keys()
    assert rap["verdetto_dopo"]["verdetto"] == "VALIDO", rap["verdetto_dopo"]
    assert codice == 0
    assert any(r["regola"] == "R-C" for r in rap["riparazioni"]), rap["riparazioni"]
    invariante(rap, {"evidence_pack": p})


@prova("P9", "un envelope ASSENTE non viene sintetizzato (il caso `statistica_ufficiale`)")
def p9():
    # il caso reale: un dizionario di claim, senza alcun envelope attorno
    codice, rap, err = esegui({"C-r22s-001": {"statement": {"subject": "s", "observed": "o"}}})
    assert rap is not None, err[:400]
    assert "agent" not in rap["pack_riparato"], "ha SINTETIZZATO un envelope"
    assert codice == 1, f"uscita {codice}, atteso 1"
    assert rap["verdetto_dopo"]["verdetto"] == "NON VALIDO"


@prova("P10", "UNA sola riparazione per pack: il rapporto lo dichiara e il codice lo rispetta")
def p10():
    p = pack_valido()
    p["payload"]["spend_evidence"] = [{"claim_id": "C-r22t-002", "spend_form": "software",
                                       "amount": None}]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    assert rap["passaggi"] == 1, rap.get("passaggi")
    # e il passaggio unico si prova STRUMENTANDO, non cercando `while` nel sorgente: una
    # seconda passata ricorsiva non contiene la parola `while` e passerebbe indenne una grep
    d = tempfile.mkdtemp(prefix="pr-uno-")
    try:
        raw = os.path.join(d, "canale.raw.txt")
        scrivi(raw, raw_da(p))
        con = os.path.join(d, "delivery.txt")
        scrivi(con, contratto())
        chiamate = []
        vero = pr.ripara

        def spia(*a, **k):
            chiamate.append(1)
            return vero(*a, **k)
        pr.ripara = spia
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                pr.main(["--raw", raw, "--contratto", con,
                         "--schemi", os.path.join(ROOT, "schemas")])
        finally:
            pr.ripara = vero
        assert len(chiamate) == 1, f"{len(chiamate)} passaggi di riparazione, atteso 1"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P11", "un pack GIA' valido non viene toccato: zero riparazioni, byte identici")
def p11():
    p = pack_valido()
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    assert codice == 0
    assert rap["verdetto_prima"]["verdetto"] == "VALIDO", rap["verdetto_prima"]
    assert rap["riparazioni"] == [], rap["riparazioni"]
    assert rap["pack_riparato"] == p, "ha modificato un pack che non aveva bisogno di nulla"


@prova("P12", "gli errori del validatore sono l'AUTORIZZAZIONE: senza, non si ripara")
def p12():
    """Un `null` illecito che il validatore non ha segnalato non viene toccato.

    E' il cardine del mandato: l'ingresso della riparazione sono gli errori ESATTI del
    validatore, non l'iniziativa del riparatore.
    """
    p = pack_valido()
    p["payload"]["spend_evidence"] = [{"claim_id": "C", "spend_form": "software",
                                       "amount": None}]
    d = tempfile.mkdtemp(prefix="pr-aut-")
    try:
        vuoto = os.path.join(d, "errori-vuoti.json")
        scrivi(vuoto, json.dumps({"null_check": {"illeciti": []}, "schema": {"errori": []}}))
        codice, rap, err = esegui(p, ["--errori", vuoto])
        assert rap is not None, err[:400]
        assert rap["riparazioni"] == [], f"ha riparato senza autorizzazione: {rap['riparazioni']}"
        assert rap["pack_riparato"]["payload"]["spend_evidence"][0]["amount"] is None
        # e con l'autorizzazione vera, la stessa posizione viene riparata: e' l'autorizzazione
        # a fare la differenza, non il difetto
        codice2, rap2, _ = esegui(p)
        assert rap2["riparazioni"], "senza il confronto la prova non direbbe nulla"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P13", "i quattro atti sono conservati separatamente, e il raw e' quello di partenza")
def p13():
    p = pack_valido()
    p["payload"]["spend_evidence"] = [{"claim_id": "C-r22t-002", "spend_form": "software",
                                       "amount": None}]
    d = tempfile.mkdtemp(prefix="pr-fasc-")
    try:
        raw = os.path.join(d, "canale.raw.txt")
        scrivi(raw, raw_da(p))
        con = os.path.join(d, "delivery.txt")
        scrivi(con, contratto())
        fasc = os.path.join(d, "fascicolo")
        r = subprocess.run([sys.executable, os.path.join(QUI, "pack_repair.py"),
                            "--raw", raw, "--contratto", con,
                            "--schemi", os.path.join(ROOT, "schemas"),
                            "--fascicolo", fasc], capture_output=True, timeout=300)
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[:400]
        attesi = ["00-raw-iniziale.txt", "01-richiesta-riparazione.json",
                  "02-pack-riparato.json", "03-verdetti.json"]
        presenti = sorted(os.listdir(fasc))
        assert presenti == attesi, presenti
        with open(os.path.join(fasc, "00-raw-iniziale.txt"), "rb") as f:
            a = f.read()
        with open(raw, "rb") as f:
            b = f.read()
        assert a == b, "il raw conservato non e' quello di partenza"
        richiesta = json.load(open(os.path.join(fasc, "01-richiesta-riparazione.json"),
                                   encoding="utf-8"))
        assert richiesta["sha256_raw"] == hashlib.sha256(b).hexdigest()
        assert richiesta["errori_del_validatore"], "la richiesta non porta gli errori"
        assert "regole_ammesse" in richiesta
        verdetti = json.load(open(os.path.join(fasc, "03-verdetti.json"), encoding="utf-8"))
        assert verdetti["prima"]["verdetto"] == "NON VALIDO"
        assert verdetti["dopo"]["verdetto"] == "VALIDO"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P14", "il riparatore si POLIZIA: se una riparazione alterasse un fatto, si annulla")
def p14():
    """L'invariante non e' solo provato qui: e' verificato a ogni esecuzione dal codice stesso.

    Si inietta una regola che cambia uno scalare e si pretende che il riparatore se ne accorga,
    restituisca il pack di partenza e dichiari il guasto.
    """
    p = pack_valido()
    p["payload"]["coverage"]["gaps"] = "una lacuna"
    d = tempfile.mkdtemp(prefix="pr-pol-")
    try:
        copia = os.path.join(d, "pack_repair.py")
        src = open(os.path.join(QUI, "pack_repair.py"), encoding="utf-8").read()
        rotto = src.replace("            padre[chiave] = [v]",
                              "            padre[chiave] = [str(v) + ' ALTERATO']", 1)
        assert rotto != src, "la mutazione non si applica: il testo e' cambiato"
        scrivi(copia, rotto)
        shutil.copyfile(os.path.join(QUI, "null_check.py"), os.path.join(d, "null_check.py"))
        shutil.copyfile(os.path.join(QUI, "replay_validate.py"),
                        os.path.join(d, "replay_validate.py"))
        raw = os.path.join(d, "canale.raw.txt")
        scrivi(raw, raw_da(p))
        con = os.path.join(d, "delivery.txt")
        scrivi(con, contratto())
        r = subprocess.run([sys.executable, copia, "--raw", raw, "--contratto", con,
                            "--schemi", os.path.join(ROOT, "schemas")],
                           capture_output=True, timeout=300)
        assert r.returncode == 2, f"uscita {r.returncode}, atteso 2 (il riparatore si annulla)"
        assert "invariante" in r.stderr.decode("utf-8", "replace").lower(), \
            r.stderr.decode("utf-8", "replace")[:300]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P15", "determinismo e codifica del rapporto")
def p15():
    p = pack_valido()
    p["payload"]["coverage"]["gaps"] = "una lacuna"
    a = esegui(p)
    b = esegui(p)
    assert json.dumps(a[1], sort_keys=True) == json.dumps(b[1], sort_keys=True), \
        "due esecuzioni, due rapporti diversi"


@prova("P21", "l'invariante e' SEVERO: `foglie` distingue tipi e molteplicita'")
def p21():
    """La tesi centrale del file e' che il confronto delle foglie coglierebbe un fatto alterato.
    Se `foglie` collassasse `1` e `1.0`, o `True` e `1`, o perdesse le molteplicita', il
    confronto resterebbe verde su alterazioni vere — e nessun'altra prova se ne accorgerebbe,
    perche' `invariante()` qui e' una RE-implementazione indipendente e non tocca quella di
    produzione."""
    f = pr.foglie
    assert f([1]) != f([1.0]), "intero e float collidono"
    assert f([True]) != f([1]), "booleano e intero collidono"
    assert f(["1"]) != f([1]), "stringa e numero collidono"
    assert f(["a", "a"]) != f(["a"]), "le molteplicita' si perdono: e' un insieme, non un multiinsieme"
    assert f({"x": "a"}) == f({"y": "a"}), "la posizione non deve contare, il valore si'"
    assert f([None, None]) == f([]), "`None` non e' uno scalare"


@prova("P22", "un involucro con FRATELLI non viene sbucciato")
def p22():
    """R-C scarta l'involucro solo quando la radice ha una chiave sola. Con dei fratelli,
    sbucciare butterebbe via tutto quello che sta accanto — e se i fratelli non portassero
    scalari, l'invariante non se ne accorgerebbe."""
    p = pack_valido()
    codice, rap, err = esegui({"evidence_pack": p, "nota": {"vuoto": None}})
    assert rap is not None, err[:400]
    assert "evidence_pack" in rap["pack_riparato"], "ha sbucciato un involucro con fratelli"
    assert rap["riparazioni"] == [], rap["riparazioni"]
    assert codice == 1


@prova("P18", "un'autorizzazione che chiede un array dove il contratto non lo vuole e' RIFIUTATA")
def p18():
    """Gli errori arrivano da fuori (`--errori`). Il riparatore non li esegue: li confronta col
    contratto. Un'autorizzazione sbagliata non deve poter deformare un dato."""
    p = pack_valido()
    d = tempfile.mkdtemp(prefix="pr-forg-")
    try:
        falso = os.path.join(d, "errori.json")
        scrivi(falso, json.dumps({"null_check": {"illeciti": []}, "schema": {"errori": [
            {"puntatore": "/payload/pain_id", "keyword": "type",
             "messaggio": "'P-003' is not of type 'array'"}]}}))
        codice, rap, err = esegui(p, ["--errori", falso])
        assert rap is not None, err[:400]
        assert rap["pack_riparato"]["payload"]["pain_id"] == "P-003", \
            f"ha avvolto un valore che il contratto vuole scalare: {rap['pack_riparato']['payload']['pain_id']}"
        assert any("array" in r["motivo"] for r in rap["rifiuti"]), rap["rifiuti"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P19", "un `null` ELEMENTO di un array non si toglie: cambierebbe la cardinalita'")
def p19():
    p = pack_valido()
    p["payload"]["claims"][0]["source_ids"] = [None]
    codice, rap, err = esegui(p)
    assert rap is not None, err[:400]
    dopo = rap["pack_riparato"]["payload"]["claims"][0]["source_ids"]
    assert dopo == [None], f"ha tolto un elemento d'array: {dopo}"
    assert any("cardinalit" in r["motivo"] for r in rap["rifiuti"]), rap["rifiuti"]
    assert codice == 1


@prova("P20", "un'autorizzazione che non trova la sua posizione ferma tutto: exit 2")
def p20():
    """Se la convenzione dei percorsi del riparatore divergesse da quella del validatore, le
    autorizzazioni cadrebbero nel vuoto e il riparatore non riparerebbe piu' nulla in silenzio."""
    p = pack_valido()
    d = tempfile.mkdtemp(prefix="pr-perd-")
    try:
        falso = os.path.join(d, "errori.json")
        scrivi(falso, json.dumps({"null_check": {"illeciti": [
            {"percorso": "payload/posizione_inesistente", "tipo": "o"}]},
            "schema": {"errori": []}}))
        codice, rap, err = esegui(p, ["--errori", falso])
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert "autorizzazione senza posizione" in err, err[:300]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P16", "`ripara` non muta il pack in ingresso: senza copia l'invariante sarebbe vacuo")
def p16():
    """Se `ripara` lavorasse in luogo, il pack di partenza e quello riparato sarebbero lo stesso
    oggetto: il confronto delle foglie non potrebbe mai fallire, e la difesa centrale del
    riparatore diventerebbe una tautologia."""
    import null_check as nc
    p = pack_valido()
    p["payload"]["spend_evidence"] = [{"claim_id": "C", "spend_form": "software",
                                       "amount": None}]
    radice = nc.leggi_contratto(contratto())
    _, illeciti = nc.controlla(p, radice)
    assert illeciti, "premessa persa: il pack di prova non ha null illeciti"
    congelato = json.dumps(p, ensure_ascii=False, sort_keys=True)
    riparato, riparazioni, _ = pr.ripara(p, radice, illeciti, [])
    assert riparazioni, "premessa persa: nessuna riparazione applicata"
    assert json.dumps(p, ensure_ascii=False, sort_keys=True) == congelato, \
        "ha mutato il pack in ingresso"
    assert riparato is not p


@prova("P17", "il contratto ha UN solo lettore: l'obbligatorieta' viene da `null_check`")
def p17():
    import null_check as nc
    radice = nc.leggi_contratto(contratto())
    gaps = radice.figli["payload"].figli["coverage"].figli["gaps"]
    amount = radice.figli["payload"].figli["spend_evidence"].figli["amount"]
    assert gaps.obbligatorio is True, "`!gaps` non e' letta come obbligatoria"
    assert amount.obbligatorio is False, "`amount` non e' letta come opzionale"
    src = open(os.path.join(QUI, "pack_repair.py"), encoding="utf-8").read()
    assert "INTESTAZIONE_ALBERO" not in src and "== ALBERO" not in src, \
        "pack_repair ha un secondo parser del contratto"


# ------------------------------------------------------------------ esecuzione


@prova("PR-2", "profondita' 1500: la camminata arriva in fondo")
def pr_2():
    v = None
    for _ in range(1500):
        v = {"a": v}
    n = sum(1 for _ in pr.posizioni(v, None, ""))
    assert n == 1501, f"posizioni ha emesso {n} posizioni, attese 1501"


# Gli scheletri dei tre pack reali: la struttura con ogni scalare non-`null` sostituito
# da `0`. Esatto per queste prove, perche' ne' la camminata ne' `foglie` distinguono uno
# scalare da un altro se non per la sua identita'. Incorporati e non letti da `runtime/`:
# questo selftest e' una delle quindici PROVE di `gate_all`, e il criterio che le
# definisce pretende che l'esito dipenda dal solo albero dei sorgenti.
SCHELETRI_PR = {
    # 20260823-0211/P-003-trend_adozione.json
    '20260823-0211/P-003-trend_adozione.json':
    '{"agent":0,"run_id":0,"status":0,"budget_used":{"searches":0,"searches_limit":0,"fetches":0,"fetches_limit":0},"stop_reason":0,"payload":{"pain_id":0,"channel":0,"claims":[{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"value_min":0,"value_max":0,"unit":0,"currency":0,"year":0,"geo":0,"state":0,"source_ids":[0],"formula":null,"input_source_ids":[0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[0,0],"formula":null,"input_source_ids":[0,0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0,0],"promotion_rule_met":0,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null},{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[0],"formula":null,"input_source_ids":[0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[],"formula":0,"input_source_ids":[0,0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0,0],"promotion_rule_met":0,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null},{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[],"formula":0,"input_source_ids":[0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":null,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0}],"q4_answer":{"question_id":0,"answer":0,"grounded":0,"source_ids":[0,0,0],"answered_by":0},"coverage":{"angles_covered":[0,0,0,0],"gaps":[0,0,0]},"spend_evidence":[],"language_of_the_problem":[],"contradictory_evidence":[{"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[],"formula":0,"input_source_ids":[0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":null,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0},{"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"value_min":0,"value_max":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[],"formula":0,"input_source_ids":[0],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":null,"estimation_formula":null,"stale_warning":0,"contradicted_by":[],"evidence":[{"source_id":0,"snippet":0,"snippet_source":0,"verbatim_quote":null}],"evidence_kind":0}]},"new_sources":[{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0,"snippet_lang":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":null,"language":0,"data_year":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":0,"language":0,"data_year":0,"evidence_kind":0,"snippet_is_translation":0}],"queries_run":[{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0}],"unreachable_sources":[{"what":0,"why":0,"impact":0,"url_or_target":0,"source_id":0},{"what":0,"why":0,"impact":0,"url_or_target":0,"source_id":0}],"self_check":{"invarianti_violati":[],"note":0}}',
    # 20260822-2331/P-003-statistica_ufficiale.json
    '20260822-2331/P-003-statistica_ufficiale.json':
    '{"agent":0,"run_id":0,"status":0,"budget_used":{"searches":0,"searches_limit":0,"fetches":0,"fetches_limit":0},"stop_reason":0,"payload":{"pain_id":0,"channel":0,"claims":[{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[0],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"stale_warning":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[0,0],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0,0],"promotion_rule_met":0,"stale_warning":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0},{"source_id":0,"snippet":0,"snippet_source":0}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":0,"year":0,"geo":0,"state":0,"source_ids":[0],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"stale_warning":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":0,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[0,0],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0,0],"promotion_rule_met":0,"stale_warning":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0},{"source_id":0,"snippet":0,"snippet_source":0}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"unit":0,"currency":0,"year":0,"geo":0,"state":0,"source_ids":[0,0],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":0,"geo":0},"state":0,"source_ids":[0,0],"promotion_rule_met":0,"stale_warning":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0},{"source_id":0,"snippet":0,"snippet_source":0}],"evidence_kind":0},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":null,"geo":0},"state":0,"source_ids":[],"promotion_rule_met":null,"evidence":[]},{"promoted_by":0,"id":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"unit":0,"currency":null,"year":0,"geo":0,"state":0,"source_ids":[],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0},"period":null,"geo":0},"state":0,"source_ids":[],"promotion_rule_met":null,"evidence":[]}],"q4_answer":{"question_id":0,"answer":0,"grounded":0,"source_ids":[0,0,0,0]},"coverage":{"angles_covered":[0],"gaps":[0,0]},"spend_evidence":[{"claim_id":0,"spend_form":0,"amount":{"label":0,"value":0,"unit":0,"state":0,"currency":0,"year":0,"geo":0,"source_ids":[0],"formula":null,"input_source_ids":[],"is_assumption":0,"display_string":0}}],"language_of_the_problem":[],"contradictory_evidence":[{"id":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":null,"geo":0},"state":0,"source_ids":[],"promotion_rule_met":null,"evidence":[],"evidence_kind":0}]},"new_sources":[{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"months_old":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0}],"queries_run":[{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[],"yield":0}],"unreachable_sources":[],"self_check":{"invarianti_violati":[],"note":0}}',
    # 20260822-2331/P-003-pricing_mercato.json
    '20260822-2331/P-003-pricing_mercato.json':
    '{"agent":0,"run_id":0,"status":0,"budget_used":{"searches":0,"searches_limit":0,"fetches":0,"fetches_limit":0},"stop_reason":0,"payload":{"pain_id":0,"channel":0,"claims":[{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0,0],"promotion_rule_met":0,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0},{"source_id":0,"snippet":0,"snippet_source":0}]},{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}]},{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}]},{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":null,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}]},{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}]},{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":{"label":0,"value":null,"value_min":0,"value_max":0,"unit":0,"currency":0,"year":0,"geo":0,"state":0,"source_ids":[0],"formula":0,"input_source_ids":[0],"is_assumption":0,"display_string":0},"period":null,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":null,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}]}],"q4_answer":{"question_id":0,"grounded":0,"answer":0,"source_ids":[0,0,0,0],"answered_by":0},"coverage":{"angles_covered":[0],"gaps":[0,0,0,0]},"spend_evidence":[{"claim_id":0,"spend_form":0,"amount":{"label":0,"value":null,"unit":0,"state":0,"currency":0,"year":0,"geo":0,"source_ids":[],"is_assumption":0,"display_string":0}},{"claim_id":0,"spend_form":0,"amount":{"label":0,"value":null,"unit":0,"state":0,"currency":0,"year":0,"geo":0,"source_ids":[],"is_assumption":0,"display_string":0}},{"claim_id":0,"spend_form":0,"amount":{"label":0,"value":null,"value_min":0,"value_max":0,"unit":0,"state":0,"currency":0,"year":0,"geo":0,"source_ids":[0],"formula":0,"input_source_ids":[0],"is_assumption":0,"display_string":0}}],"contradictory_evidence":[{"id":0,"promoted_by":0,"statement":{"subject":0,"observed":0,"quantity":null,"period":0,"geo":0},"state":0,"source_ids":[0],"promotion_rule_met":0,"evidence_kind":0,"evidence":[{"source_id":0,"snippet":0,"snippet_source":0}]}],"language_of_the_problem":[]},"new_sources":[{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0},{"id":0,"url":0,"publisher":0,"title":0,"published_date":0,"accessed_at":0,"tier":0,"access_status":0,"retrieved_by":0,"stale":0,"evidence_kind":0}],"queries_run":[{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0,0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0},{"raw_query":0,"normalized_query":0,"angle":0,"n_results":0,"source_ids":[0],"yield":0}],"unreachable_sources":[{"what":0,"why":0,"impact":0,"url_or_target":0,"source_id":null}],"self_check":{"invarianti_violati":[],"note":0}}',
}


# ================================================================== la primitiva condivisa
#
# Mandato `65-MANDATO-PRIMITIVA-CONDIVISA.md`, redazione 2, sha256
# `5f28b9571a6da804292344743247e5886224f46447cef7bc171e43d25fe83960`.


def _riferimento_ricorsivo(v, nodo, percorso, padre=None, chiave=None):
    """La `posizioni` ricorsiva soppressa dalla slice, verbatim. Metro di `PR-3`.

    Vive nel banco di prova e non nello strumento: e' cio' contro cui la primitiva si misura,
    e tenerlo qui e' l'unico modo perche' resti un metro invece di diventare una seconda
    implementazione da mantenere."""
    yield percorso, padre, chiave, v, nodo
    if isinstance(v, dict):
        for k in list(v):
            fig = nodo.figli.get(k) if nodo is not None else None
            yield from _riferimento_ricorsivo(v[k], fig, f"{percorso}/{k}" if percorso else k, v, k)
    elif isinstance(v, list):
        for i in range(len(v)):
            yield from _riferimento_ricorsivo(v[i], nodo, f"{percorso}[]", v, i)


@prova("PR-1", "posizioni DELEGA alla primitiva: nessuna seconda camminata locale")
def pr_1():
    """La prova che la slice ha fatto cio' per cui esiste, e la fa PER AST.

    Un confronto comportamentale direbbe che qualcosa e' rotto, non che e' tornata una
    seconda camminata. Questa lo dice, e lo dice direttamente."""
    import ast
    alb = ast.parse(open(os.path.join(QUI, "pack_repair.py"), encoding="utf-8").read())
    f = [n for n in alb.body if isinstance(n, ast.FunctionDef) and n.name == "posizioni"]
    assert f, "posizioni non esiste piu': la prova va riscritta, non tolta"
    f = f[0]
    chiamate = {ast.unparse(n.func) for n in ast.walk(f) if isinstance(n, ast.Call)}
    assert "null_check.cammina_istanza" in chiamate,         f"posizioni non chiama la primitiva per nome qualificato: {sorted(chiamate)}"
    assert not [c for c in ast.walk(f) if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name) and c.func.id == "posizioni"],         "posizioni si richiama: e' tornata ricorsiva"
    tipi = {ast.unparse(a) for n in ast.walk(f) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "isinstance" for a in n.args[1:]}
    assert not ({"dict", "list"} & tipi),         f"posizioni discrimina i contenitori: cammina per conto proprio ({sorted(tipi)})"


@prova("PR-3", "equivalenza di sequenza col riferimento ricorsivo soppresso")
def pr_3():
    import null_check as nc
    albero = nc.leggi_contratto(contratto())
    for nome, testo in SCHELETRI_PR.items():
        pack = json.loads(testo)
        a = [(p, c) for p, _, c, _, _ in _riferimento_ricorsivo(pack, albero, "")]
        b = [(p, c) for p, _, c, _, _ in pr.posizioni(pack, albero, "")]
        assert a == b, f"{nome}: la sequenza diverge dal riferimento"


# L'ORACOLO DELLE FOGLIE. Non e' calcolato: e' scritto. La redazione 1 di questa prova
# confrontava `foglie(pack)` con `foglie(json.loads(json.dumps(pack)))`, cioe' un pack con la
# propria copia — un'uguaglianza vera per costruzione per qualunque funzione deterministica del
# valore. Svuotare `foglie` la lasciava verde. Uno specchio dove serviva un METRO.
#
# I valori qui sotto sono stati misurati sulla baseline `993c771` per due vie indipendenti che
# hanno concordato: `foglie()` di quel commit, e un contatore scritto apposta che non passa da
# `foglie()`. Gli scheletri sostituiscono ogni scalare non nullo con `0`, quindi il multiinsieme
# atteso ha una sola chiave: la prova sorveglia il CONTEGGIO, che e' cio' che `foglie` promette.
FOGLIE_ATTESE = {
    "20260823-0211/P-003-trend_adozione.json":       {("int", "0"): 383},
    "20260822-2331/P-003-statistica_ufficiale.json": {("int", "0"): 333},
    "20260822-2331/P-003-pricing_mercato.json":      {("int", "0"): 289},
}


@prova("PR-4", "l'invariante sugli scalari e' invariato, misurato contro un oracolo fisso")
def pr_4():
    assert set(FOGLIE_ATTESE) == set(SCHELETRI_PR), \
        "l'oracolo e gli scheletri non coprono gli stessi pack"
    for nome, testo in SCHELETRI_PR.items():
        ottenuto = dict(pr.foglie(json.loads(testo)))
        assert ottenuto == FOGLIE_ATTESE[nome], \
            f"{nome}: foglie() ha prodotto {ottenuto}, atteso {FOGLIE_ATTESE[nome]}"


@prova("PR-5", "il percorso NON dipende dal nodo di contratto")
def pr_5():
    import null_check as nc
    albero = nc.leggi_contratto(contratto())
    for testo in SCHELETRI_PR.values():
        pack = json.loads(testo)
        a = [p for p, _, _, _, _ in pr.posizioni(pack, albero, "")]
        b = [p for p, _, _, _, _ in pr.posizioni(pack, None, "")]
        assert a == b, "il percorso dipende dal contratto: non dovrebbe"


# ------------------------------------------------------------------ il provisioning
#
# Questo banco gira in due mondi e ne fingeva uno solo. `replay_validate` dichiara che senza
# `referencing` la validazione di schema non e' disponibile, e in quel caso rifiuta di dire
# `VALIDO`: ogni pack altrimenti valido diventa conservativamente `NON VALIDO`. Le prove qui
# elencate asseriscono un verdetto positivo, o un esito che ne dipende, e su una radice priva di
# quel backend diventavano rosse senza che nulla dicesse che l'ambiente e' incompleto invece che
# l'artefatto sbagliato.
#
# LA CAPABILITY SI RILEVA, NON SI INDOVINA. Non da `os.name`, non dalla distribuzione, non dalla
# versione di Python, non dalla semplice importabilita' di un modulo: si misura interrogando il
# VERO confine di `replay_validate` con un pack valido. Un ambiente che ha `referencing`
# importabile ma un validatore che non gira e' indistinguibile dal primo per chi guarda gli
# import, e perfettamente distinguibile per chi guarda il confine.

#: L'insieme e' MISURATO: senza il backend, queste dieci cadono e nessun'altra. Ma LA
#: PROTEZIONE E' PARZIALE, e va detto per quello che e': un nome TOLTO da qui fa girare la
#: prova e produce una rossa; un nome AGGIUNTO che in realta' girerebbe benissimo viene
#: dichiarato non applicabile e nessun controllo se ne accorge — misurato, exit 0 su
#: entrambe le piattaforme. La tenuta dell'insieme e' stabilita per misura ESTERNA
#: (svuotarlo e osservare quali prove cadono davvero), non da una guardia interna.
DIPENDENTI_DALLO_SCHEMA = ('P1', 'P2', 'P3', 'P4', 'P5', 'P7', 'P8', 'P11', 'P13', 'P14')
MOTIVO_NA = "provisioning schema indisponibile"
ETICHETTA_NA = "[n/a]"
ETICHETTA_VERDE = "[verde]"
ETICHETTA_ROSSA = "[ROSSO]"

#: Il ruolo d'appello, scritto a mano e INDIPENDENTE da `ESITI`. Serve perche' un registro
#: che si conta da solo non puo' accorgersi di essere dimagrito: se una prova perde il
#: decoratore, `ESITI` si accorcia e ogni conteggio derivato da `ESITI` torna. Confrontare
#: `ESITI` con una lista letterale e' l'unico modo perche' la sparizione produca un rosso —
#: ed e' anche il motivo per cui questa lista va aggiornata a mano quando una prova nasce o
#: muore: l'attrito e' voluto.
PROVE_ATTESE = (
    'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10', 'P11', 'P12', 'P13', 'P14',
    'P15', 'P16', 'P17', 'P18', 'P19', 'P20', 'P21', 'P22',
    'PR-1', 'PR-2', 'PR-3', 'PR-4', 'PR-5',
)


def classifica_sonda(codice, rapporto):
    """I DUE stati ammessi, per intero. Ogni altra combinazione e' `TERZO_STATO`.

    Uno stato ammesso e' una CONGIUNZIONE: un `NON VALIDO` con errori di schema diversi da zero
    non e' provisioning assente, e' un pack sbagliato; un null-check `RIFIUTATO` non e' ne' l'uno
    ne' l'altro. Accettare la congiunzione parziale dichiarerebbe indisponibile una capability
    che invece funziona.
    """
    if not isinstance(rapporto, dict):
        return "TERZO_STATO"
    nc = rapporto.get("null_check") or {}
    sc = rapporto.get("schema") or {}
    if (codice == 0 and rapporto.get("verdetto") == "VALIDO"
            and nc.get("esito") == "AMMESSO" and sc.get("disponibile") is True
            and sc.get("n_errori") == 0):
        return "SCHEMA_DISPONIBILE"
    if (codice == 1 and rapporto.get("verdetto") == "NON VALIDO"
            and nc.get("esito") == "AMMESSO" and sc.get("disponibile") is False
            and sc.get("n_errori") == 0
            and (sc.get("motivo_indisponibilita") or "").strip()):
        return "SCHEMA_INDISPONIBILE"
    return "TERZO_STATO"


def sonda_capability():
    """Il confine vero di `replay_validate`, su un pack VALIDO. NON cattura: puo' sollevare.

    Chi cattura e' il gate, e in nessun altro posto. La ragione: `replay_validate.main` propaga
    al chiamante un'eccezione non-`Guasto` che venga da `valida_schema`, e quell'eccezione non e'
    un ambiente incompleto — e' un guasto. Convertirla in «schema indisponibile» sarebbe
    fabbricare provisioning assente.

    Nessuna fixture nuova: il contratto e il pack valido sono quelli che questo banco gia' porta.
    """
    d = tempfile.mkdtemp(prefix="sonda-capability-")
    try:
        con = os.path.join(d, "contratto.txt")
        with open(con, "w", encoding="utf-8", newline="\n") as f:
            f.write(contratto())
        raw = os.path.join(d, "sonda.raw.txt")
        with open(raw, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw_da(pack_valido()))
        uscita = io.StringIO()
        with contextlib.redirect_stdout(uscita), contextlib.redirect_stderr(io.StringIO()):
            codice = _rv.main(["--raw", raw, "--contratto", con])
        testo = uscita.getvalue().strip()
        return classifica_sonda(codice, json.loads(testo) if testo else None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def oracle_ripristino():
    """L'ORACLE del ripristino: identita' del seam che questo banco sostituisce DAVVERO.

    Il seam e' `pack_repair.ripara`, e lo sostituisce `P10` per contare i passaggi di
    riparazione. Sorvegliare qualcos'altro — per esempio un attributo di
    `replay_validate`, che questo file importa ma non tocca mai — darebbe un controllo che non
    puo' fallire: un oracle senza bersaglio e' un verde fabbricato, e conta come tale.

    Non chiama la sonda e non la influenza: legge soltanto un'identita' di attributo sul
    riferimento al modulo GIA' IMPORTATO.
    """
    return {"pack_repair.ripara": pr.ripara}


def main():
    """UNA invocazione, UNA sonda, UNA valutazione decisionale.

    QUI, E SOLTANTO QUI, si cattura l'eccezione che la sonda ha lasciato salire. Non esiste un
    `try` attorno a `main()`: una rete esterna farebbe morire il processo senza riepilogo, e
    l'osservatore del terzo stato — che pretende una riga rossa NOMINATA — sparirebbe.
    """
    try:
        stato = sonda_capability()
    except BaseException as e:                                            # noqa: BLE001
        stato, diagnostica = "TERZO_STATO", f"{type(e).__name__}: {e}"
    else:
        diagnostica = None

    istantanea = oracle_ripristino()
    return _corpo(stato, diagnostica, istantanea)


def _corpo(stato, diagnostica, istantanea):
    print(f"pack_repair_selftest — {len(ESITI)} prove — provisioning: {stato}\n")

    if stato == "TERZO_STATO":
        # Il gate si arresta PRIMA delle prove: nessuna viene avviata, e non si dichiara non
        # applicabile nulla, perche' non si e' accertato nulla. Un terzo stato non e' un ambiente
        # incompleto: e' un guasto, e si presenta nominato invece che come traceback.
        print(f"  {ETICHETTA_ROSSA} provisioning  TERZO_STATO — "
              f"{diagnostica or 'la sonda non ha prodotto uno stato ammesso'}")
        print("\n  gate di provisioning: ROSSO")
        print("  nessuna prova avviata")
        return 1

    non_applicabili = (set(DIPENDENTI_DALLO_SCHEMA)
                       if stato == "SCHEMA_INDISPONIBILE" else set())
    # `verdi` e' un ELENCO DI NOMI, non un contatore. La differenza non e' di stile: i tre
    # registri — verdi, falliti, saltate — sono la sola prova che una prova abbia ricevuto
    # un esito, e un contatore non sa dire QUALE nome ha contato.
    falliti, verdi, saltate = [], [], []
    for nome, desc, f in ESITI:
        if nome in non_applicabili:
            # non si esegue fingendo aspettative diverse: o intera, o per niente
            saltate.append(nome)
            print(f"  {ETICHETTA_NA} {nome:<4} {desc} — {MOTIVO_NA}")
            continue
        try:
            f()
            verdi.append(nome)
            print(f"  {ETICHETTA_VERDE} {nome:<4} {desc}")
        except Exception as e:                                            # noqa: BLE001
            falliti.append((nome, e))
            print(f"  {ETICHETTA_ROSSA} {nome:<4} {desc}\n          {type(e).__name__}: {e}")

    guasti = []
    if stato == "SCHEMA_DISPONIBILE" and saltate:
        guasti.append(f"non applicabili con capability presente: {sorted(saltate)}")
    # LE DUE CLAUSOLE DEL RUOLO D'APPELLO. Sorvegliano questo, e nient'altro:
    #
    #   (a) i nomi REGISTRATI in `ESITI` sono esattamente quelli ATTESI — se una prova perde
    #       il decoratore, o ne compare una di troppo, o un nome e' ripetuto, cade qui;
    #   (b) ogni nome atteso ha ricevuto UNO DEI TRE ESITI — verde, rossa, o non applicabile.
    #       I tre registri sono l'unica testimonianza dell'esito, quindi un nome che non
    #       compare in nessuno dei tre non e' stato deciso: e' stato saltato, comunque lo si
    #       sia saltato, e in qualunque punto del ciclo sia stato inserito il salto.
    #
    # E' (b) a cogliere lo skip silenzioso: prima, `saltate` veniva confrontata con
    # `DIPENDENTI_DALLO_SCHEMA`, cioe' due derivati della STESSA costante, e un `continue`
    # introdotto altrove nel ciclo passava indisturbato — misurato: exit 0 su entrambe le
    # piattaforme, con la prova sparita da ogni riga.
    registrate = [n for n, _d, _f in ESITI]
    if sorted(registrate) != sorted(PROVE_ATTESE):
        guasti.append(f"registrate e attese non coincidono: mancano "
                      f"{sorted(set(PROVE_ATTESE) - set(registrate))}, in piu' "
                      f"{sorted(set(registrate) - set(PROVE_ATTESE))}")
    decise = set(verdi) | {n for n, _e in falliti} | set(saltate)
    if decise != set(PROVE_ATTESE):
        guasti.append(f"prove senza alcun esito: {sorted(set(PROVE_ATTESE) - decise)}")
    # E la clausola sull'insieme dipendente, che sorveglia una proprieta' DIVERSA: che le
    # prove dichiarate non applicabili siano esattamente quelle saltate. Coglie un nome
    # dichiarato che non corrisponde ad alcuna prova, non uno skip introdotto altrove.
    if stato == "SCHEMA_INDISPONIBILE" and sorted(saltate) != sorted(DIPENDENTI_DALLO_SCHEMA):
        guasti.append("saltate e dichiarate non coincidono: "
                      f"saltate={sorted(saltate)} dichiarate={sorted(DIPENDENTI_DALLO_SCHEMA)}")
    finale = oracle_ripristino()
    for chiave, atteso in istantanea.items():
        if finale.get(chiave) is not atteso:
            guasti.append(f"ripristino: `{chiave}` non e' stato ripristinato")

    print(f"\n  gate di provisioning: {'verde' if not guasti else 'ROSSO'}")
    for g in guasti:
        print(f"    · {g}")
    if saltate:
        print(f"  NON APPLICABILI ({len(saltate)}): {', '.join(saltate)} — {MOTIVO_NA}")
        print(f"  {len(verdi)}/{len(verdi) + len(falliti)} applicabili verdi"
              + (f" — ROSSE: {', '.join(n for n, _e in falliti)}" if falliti else ""))
    else:
        print(f"  {len(verdi)}/{len(verdi) + len(falliti)} verdi"
              + (f" — ROSSE: {', '.join(n for n, _e in falliti)}" if falliti else ""))
    return 1 if (falliti or guasti) else 0


if __name__ == "__main__":
    sys.exit(main())
