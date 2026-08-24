#!/usr/bin/env python3
"""
null_check_selftest.py — dimostra che null_check.py NON e' cieco.

Un controllo che dice «nessun null illecito» non prova niente finche' non si mostra che sa
rifiutare. Qui il difetto da riprendere e' REALE e misurato: il replay 20260823-1647 ha emesso
`payload/spend_evidence[0]/amount: null` su `trend_adozione`, dove il contratto dichiara
`amount o` — oggetto, senza `~` e senza `z`. La regola era gia' scritta nel prompt da due
micro-slice (`ae34883`, poi `7b8d371`) ed e' stata violata lo stesso: e' il motivo per cui il
controllo deve stare fuori dall'agente.

IL CONTRATTO NON E' SCRITTO A MANO. Ogni prova lo rigenera con `contract_compile.py --vista
delivery`: una fixture scritta a mano sarebbe una seconda fonte manuale del contratto, cioe'
esattamente cio' che questa architettura esiste per eliminare. Se il contratto cambia, queste
prove cambiano con lui o diventano rosse — che e' il comportamento voluto.

  uso:  python tools/null_check_selftest.py
        exit 0 = tutte verdi
"""
import json, os, re, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

import null_check as nc                                                    # noqa: E402

ESITI = []


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


def contratto():
    """La vista delivery rigenerata dagli schemi canonici. Mai una fixture scritta a mano."""
    r = subprocess.run([sys.executable, os.path.join(QUI, "contract_compile.py"),
                        "--agente", "evidence-researcher", "--vista", "delivery"],
                       capture_output=True, timeout=120)
    assert r.returncode == 0, f"il contratto non si genera: {r.stderr.decode('utf-8','replace')}"
    return r.stdout.decode("utf-8")


def esamina(pack, testo_contratto=None):
    """(esito, elenco) del controllo su un pack gia' deserializzato."""
    albero = nc.leggi_contratto(testo_contratto if testo_contratto is not None else contratto())
    return nc.controlla(pack, albero)


def base():
    """Un envelope minimo e valido nella forma, senza alcun `null`."""
    return {"agent": "evidence-researcher", "run_id": "20260823-1647", "status": "OK",
            "budget_used": {"searches": 1, "searches_limit": 8,
                            "fetches": 1, "fetches_limit": 16},
            "stop_reason": "COMPLETED",
            "payload": {"pain_id": "P-003", "channel": "community", "claims": [],
                        "q4_answer": {"question_id": "Q4", "answer": "x", "grounded": False,
                                      "source_ids": []},
                        "coverage": {"angles_covered": [], "gaps": []}},
            "new_sources": [], "queries_run": [], "unreachable_sources": [],
            "self_check": {"invarianti_violati": []}}


# ------------------------------------------------------------------ le prove

@prova("N1", "RIFIUTA `spend_evidence[].amount: null` — il difetto reale di trend_adozione")
def n1():
    p = base()
    p["payload"]["spend_evidence"] = [{"claim_id": "C-r22t-001", "spend_form": "software",
                                       "amount": None}]
    ok, elenco = esamina(p)
    assert not ok, ("`amount: null` e' passato. Il contratto dichiara `amount o`: oggetto, senza "
                    "`~` e senza `z`. E' l'errore misurato nel replay 20260823-1647")
    assert any(e["percorso"] == "payload/spend_evidence[]/amount" for e in elenco), \
        f"il percorso d'istanza non e' quello atteso: {[e['percorso'] for e in elenco]}"
    # Anche il MOTIVO, non solo il percorso. Senza questa riga un difetto che rompe l'aggancio
    # dentro gli array lascia N1 verde: il null viene rifiutato lo stesso, ma come «posizione non
    # nel contratto» invece che per il tipo dichiarato, e chi legge va a cercare un difetto nel
    # contratto che non c'e'. Misurato: la mutazione «l'elemento consuma un livello» non rendeva
    # rossa N1.
    e = [x for x in elenco if x["percorso"] == "payload/spend_evidence[]/amount"][0]
    assert e["tipo_dichiarato"] == "o", (
        f"l'aggancio al contratto e' sbagliato: tipo «{e['tipo_dichiarato']}» invece di «o». "
        "Scendere in un indice d'array NON deve consumare un livello di contratto")


@prova("N2", "AMMETTE `statement.quantity: null` — il contratto lo marca `o~`")
def n2():
    p = base()
    p["payload"]["claims"] = [{"promoted_by": "FP0", "id": "C-r22c-001",
                               "statement": {"subject": "x", "observed": "y", "quantity": None},
                               "state": "HYPOTHESIS", "source_ids": []}]
    ok, elenco = esamina(p)
    assert ok, (f"un null ammesso e' stato rifiutato: {elenco}. `quantity` porta la tilde `~`, "
                "che e' precisamente il marcatore che il contratto usa per dire «qui null e' "
                "legittimo»")


@prova("N3", "AMMETTE i null sui campi marcati `|z` dentro un `Numeric`")
def n3():
    p = base()
    p["payload"]["claims"] = [{"promoted_by": "FP0", "id": "C-r22c-001",
                               "statement": {"subject": "x", "observed": "y",
                                             "quantity": {"label": "l", "value": None,
                                                          "unit": "u", "currency": None,
                                                          "year": None, "geo": None,
                                                          "state": "UNKNOWN",
                                                          "formula": None,
                                                          "value_min": None, "value_max": None},
                                             "period": None},
                               "state": "HYPOTHESIS", "source_ids": []}]
    ok, elenco = esamina(p)
    assert ok, f"un null su `|z` e' stato rifiutato: {elenco}"


@prova("N4", "RIFIUTA un null su una posizione che il contratto non conosce")
def n4():
    p = base()
    p["payload"]["campo_inventato"] = None
    ok, elenco = esamina(p)
    assert not ok, "un null su una posizione fuori contratto e' passato"
    assert elenco[0]["motivo"] == "posizione non nel contratto", \
        f"il motivo non distingue il caso: {elenco[0]}"


@prova("N5", "RIFIUTA un null come ELEMENTO di un array di stringhe")
def n5():
    p = base()
    p["payload"]["coverage"]["gaps"] = [None]
    ok, elenco = esamina(p)
    assert not ok, "`gaps: [null]` e' passato: il contratto dichiara `a<s>`, non `a<s|z>`"
    assert elenco[0]["percorso"] == "payload/coverage/gaps[]", \
        f"il percorso non nomina l'elemento: {elenco[0]['percorso']}"


@prova("N6", "RIFIUTA l'array stesso a null quando il contratto non lo ammette")
def n6():
    p = base()
    p["payload"]["coverage"]["gaps"] = None
    ok, elenco = esamina(p)
    assert not ok, "`gaps: null` e' passato"
    assert elenco[0]["percorso"] == "payload/coverage/gaps"


@prova("N7", "un pack senza alcun null passa, e l'elenco e' vuoto")
def n7():
    ok, elenco = esamina(base())
    assert ok and elenco == [], f"un pack pulito e' stato rifiutato: {elenco}"


@prova("N8", "il percorso riportato e' quello d'ISTANZA, con `[]` per gli elementi d'array")
def n8():
    p = base()
    p["new_sources"] = [{"id": "S-1", "url": None}]
    ok, elenco = esamina(p)
    assert not ok
    assert elenco[0]["percorso"] == "new_sources[]/url", (
        "il percorso deve nominare l'elemento d'array come `[]`, non con l'indice: due elementi "
        f"diversi con lo stesso difetto sono lo stesso difetto. Ottenuto: {elenco[0]['percorso']}")
    # Il caso limite: il pack STESSO e' null. Il percorso e' la stringa vuota, che stampata da
    # sola non dice niente a chi legge il rifiuto.
    ok, elenco = esamina(None)
    assert not ok and elenco[0]["percorso"] == "(radice)", \
        f"la radice non e' nominata: percorso «{elenco[0]['percorso'] if elenco else '—'}»"


@prova("N9", "riporta TUTTI i null illeciti, non solo il primo")
def n9():
    p = base()
    p["payload"]["spend_evidence"] = [{"claim_id": "C-r22t-001", "spend_form": "software",
                                       "amount": None}]
    p["payload"]["coverage"]["gaps"] = None
    ok, elenco = esamina(p)
    assert not ok
    assert len(elenco) == 2, (
        f"attesi 2 difetti, riportati {len(elenco)}: un controllo che si ferma al primo obbliga "
        "a un secondo giro per vedere il secondo")


@prova("N10", "l'esito e' deterministico e l'ordine e' quello di documento")
def n10():
    p = base()
    p["payload"]["coverage"]["gaps"] = None
    p["payload"]["spend_evidence"] = [{"claim_id": "C-x-001", "spend_form": "software",
                                       "amount": None}]
    a = esamina(p)
    b = esamina(p)
    assert a == b, "due esecuzioni danno esiti diversi"
    # L'ordine atteso e' quello delle CHIAVI DEL PACK, non quello in cui il test le assegna:
    # `coverage` esiste gia' in `base()` e viene mutata sul posto, `spend_evidence` e' una chiave
    # nuova e finisce in coda. Chi legge l'elenco deve ritrovare i difetti nell'ordine in cui li
    # incontrerebbe leggendo il JSON.
    atteso = [k for k in ("payload/coverage/gaps", "payload/spend_evidence[]/amount")]
    assert [e["percorso"] for e in a[1]] == atteso, \
        f"l'ordine non e' quello del documento: {[e['percorso'] for e in a[1]]}"
    assert list(p["payload"]).index("coverage") < list(p["payload"]).index("spend_evidence"), \
        "la premessa dell'ordine atteso non regge piu': base() e' cambiata"


@prova("N11", "dalla riga di comando: exit 1 sul difetto reale, exit 0 sul pack pulito")
def n11():
    d = tempfile.mkdtemp(prefix="nullcheck-")
    try:
        c = os.path.join(d, "contratto.txt")
        with open(c, "w", encoding="utf-8", newline="\n") as f:
            f.write(contratto())
        p = base()
        p["payload"]["spend_evidence"] = [{"claim_id": "C-r22t-001", "spend_form": "software",
                                           "amount": None}]
        rotto = os.path.join(d, "rotto.json")
        json.dump(p, open(rotto, "w", encoding="utf-8"))
        pulito = os.path.join(d, "pulito.json")
        json.dump(base(), open(pulito, "w", encoding="utf-8"))
        cmd = [sys.executable, os.path.join(QUI, "null_check.py"), "--contratto", c, "--pack"]
        r = subprocess.run(cmd + [rotto], capture_output=True, timeout=120)
        assert r.returncode == 1, f"uscita {r.returncode}, attesa 1"
        assert b"spend_evidence[]/amount" in r.stdout + r.stderr, "il percorso non e' stampato"
        r = subprocess.run(cmd + [pulito], capture_output=True, timeout=120)
        assert r.returncode == 0, f"uscita {r.returncode} sul pack pulito, attesa 0"
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)


@prova("N12", "contratto assente o senza albero: uscita 2, distinta dal rifiuto")
def n12():
    d = tempfile.mkdtemp(prefix="nullcheck-")
    try:
        pulito = os.path.join(d, "pulito.json")
        json.dump(base(), open(pulito, "w", encoding="utf-8"))
        vuoto = os.path.join(d, "vuoto.txt")
        open(vuoto, "w", encoding="utf-8").write("non sono un contratto\n")
        r = subprocess.run([sys.executable, os.path.join(QUI, "null_check.py"),
                            "--contratto", vuoto, "--pack", pulito],
                           capture_output=True, timeout=120)
        assert r.returncode == 2, (
            f"uscita {r.returncode}, attesa 2. «Il contratto e' rotto» e «il pack ha null "
            "illeciti» sono due cose diverse e confonderle fa aggiustare la cosa sbagliata")
        r = subprocess.run([sys.executable, os.path.join(QUI, "null_check.py"),
                            "--contratto", os.path.join(d, "inesistente.txt"),
                            "--pack", pulito], capture_output=True, timeout=120)
        assert r.returncode == 2, f"uscita {r.returncode} su contratto assente, attesa 2"
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)


@prova("N13", "nessuna rete e nessuna dipendenza da jsonschema")
def n13():
    src = open(os.path.join(QUI, "null_check.py"), encoding="utf-8").read()
    for lib in ("socket", "http", "urllib", "requests", "jsonschema", "referencing"):
        assert not re.search(r"^\s*(import|from)\s+" + lib + r"\b", src, re.M), \
            f"null_check importa {lib}"


@prova("N15", "la grammatica dei tipi, decisa una forma per volta")
def n15():
    for tipo, atteso in (("o", False), ("s", False), ("i", False), ("b", False), ("n", False),
                         ("E1", False), ("a<s>", False), ("a<o>", False), ("a<E16>", False),
                         ("o~", True), ("s|z", True), ("n|z", True), ("i|z", True), ("", False)):
        assert nc.ammette_null(tipo) is atteso, \
            f"«{tipo}»: atteso {atteso}, ottenuto {nc.ammette_null(tipo)}"
    # `a<s|z>` non esiste nel corpus e il controllo la RIFIUTA invece di indovinarla: array ed
    # elemento condividono una riga sola, quindi un solo bit di nullabilita', e non c'e' una
    # lettura giusta da scegliere. Senza questo rifiuto la forma passerebbe in silenzio come
    # «array non nullable», e i suoi elementi null sarebbero rifiutati a torto — oppure, con la
    # lettura opposta, `campo: null` sarebbe ammesso a torto. E' una mutazione VACUA sul corpus
    # di oggi: senza questa prova nulla la vedrebbe.
    # DUE grafie, perche' due sono le forme di schema che le producono: `type: ["string","null"]`
    # diventa `s|z`, e `anyOf: [{X}, {"type":"null"}]` diventa `X~`. La seconda e' viva nel corpus
    # — `quantity o~` nasce cosi' — quindi `a<o~>` e' una riga di schema di distanza. Una prima
    # stesura fermava solo la prima grafia e lasciava passare la seconda in silenzio.
    for tipo in ("a<s|z>", "a<n|z>", "a<o~>", "a<s~>"):
        try:
            nc.ammette_null(tipo)
        except nc.ContrattoIlleggibile:
            continue
        raise AssertionError(f"«{tipo}» non e' stata rifiutata: il controllo sta indovinando")


@prova("N14", "il contratto reale porta i due marcatori che il controllo usa")
def n14():
    t = contratto()
    assert re.search(r"^\s*\S+ o~\s*$", t, re.M), (
        "nessuna riga `o~` nel contratto: il marcatore tilde non esiste piu' e N2 sta provando "
        "una regola che il contratto non esprime")
    assert re.search(r"\|z\b", t), "nessun tipo `|z` nel contratto: N3 sta provando il nulla"


# ------------------------------------------------------------------ esecuzione

def main():
    print(f"null_check_selftest — {len(ESITI)} prove\n")
    falliti = []
    for nome, desc, f in ESITI:
        try:
            f()
            print(f"  [verde] {nome:<4} {desc}")
        except Exception as e:                                            # noqa: BLE001
            falliti.append((nome, e))
            print(f"  [ROSSO] {nome:<4} {desc}\n          {type(e).__name__}: {e}")
    print()
    if falliti:
        print(f"  {len(ESITI) - len(falliti)}/{len(ESITI)} verdi — ROSSE: "
              + ", ".join(n for n, _ in falliti))
        return 1
    print(f"  {len(ESITI)}/{len(ESITI)} verdi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
