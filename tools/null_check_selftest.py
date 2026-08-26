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
import ast, json, os, re, shutil, subprocess, sys, tempfile
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


# ================================================================== la camminata iterativa
#
# Mandato `63-MANDATO-CAMMINATA-ITERATIVA.md`, redazione 3, sha256
# `9983ec67c3a9c5c9c7ce9941a1285693fb4a3c08237022fbd820786130ed3313`.
#
# IL DIFETTO. `cammina` era una chiusura RICORSIVA sull'istanza, e cedeva a profondita' 996
# mentre `json.loads` accetta fino a 2998: una fascia di circa duemila livelli in cui il pack
# si parsa e la camminata cade. L'esito era exit 1 con un traceback e stdout vuoto — e `1`, in
# questa catena, significa «null illeciti trovati», cioe' una MISURA. Il guasto si travestiva
# da misura, e non era stato controllato nulla.
#
# NESSUN LIMITE INVENTATO, e non e' una rinuncia: nessuna fonte potrebbe darne uno. Il
# contratto non limita la profondita' dell'istanza — «posizione non nel contratto» e' un
# rilievo da riportare, non un motivo per fermarsi; `evidence-pack.schema.json` non porta
# alcuna occorrenza di `additionalProperties`, quindi il default permissivo lascia passare
# sottoalberi liberi; e questo controllo gira PRIMA della validazione di schema, quindi il suo
# ingresso non e' nemmeno garantito conforme. Un tetto esiste gia' a monte e non e' nostro:
# `json.loads` rifiuta oltre 2998.
#
# IL PRECEDENTE E' IN CASA: `ds_validate._cammina_schemi` e' gia' una DFS iterativa, e il suo
# docstring dice perche' — «la versione ricorsiva cedeva con `RecursionError` non catturata».
# Delle venti camminate ricorsive su JSON di `tools/`, sedici in produzione, questa e' la
# seconda a essere curata.

# Gli SCHELETRI dei tre pack reali: la struttura con ogni scalare non-`null` sostituito da `0`.
# E' esatto per questa proprieta', e la ragione e' verificabile: l'unico test scalare della
# camminata e' `v is None`. I tre sono scelti per NULL ILLECITI — non per nodi, non per null
# totali: sono esattamente i tre che ne portano piu' d'uno, e ne portano tre ciascuno. Un pack
# con zero illeciti renderebbe l'asserzione del caso `a` un `[] == []`, vacuo.
SCHELETRI = {
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


def _riferimento(valore, radice):
    """L'algoritmo RICORSIVO di prima, verbatim. E' il metro di paragone del caso `a`.

    Vive qui e non in `null_check`: e' cio' contro cui la nuova camminata si misura, e
    tenerlo nel banco di prova invece che nello strumento e' l'unico modo perche' resti un
    metro e non diventi una seconda implementazione da mantenere."""
    fuori = []

    def cammina(v, nodo, percorso):
        if v is None:
            dove = percorso or "(radice)"
            if nodo is None:
                fuori.append({"percorso": dove, "motivo": "posizione non nel contratto",
                              "tipo_dichiarato": None})
            elif not nodo.nullable:
                fuori.append({"percorso": dove,
                              "motivo": "il contratto non dichiara `~` ne' `z`",
                              "tipo_dichiarato": nodo.tipo})
            return
        if isinstance(v, dict):
            for k, sub in v.items():
                p = f"{percorso}/{k}" if percorso else k
                cammina(sub, nodo.figli.get(k) if nodo else None, p)
        elif isinstance(v, list):
            for sub in v:
                cammina(sub, nodo, f"{percorso}[]")

    cammina(valore, radice, "")
    return (not fuori), fuori


def _percorsi(elenco):
    return [e["percorso"] for e in elenco]


def _profondo(n):
    """Un JSON di profondita' `n` con un `null` in fondo, fuori contratto."""
    return '{"a":' * n + "null" + "}" * n


def _da_riga(testo_pack, testo_contratto):
    """(exit, stdout, stderr) di null_check.py dalla riga di comando."""
    d = tempfile.mkdtemp(prefix="nc-")
    try:
        c = os.path.join(d, "delivery.txt")
        p = os.path.join(d, "pack.json")
        with open(c, "w", encoding="utf-8", newline="\n") as g:
            g.write(testo_contratto)
        with open(p, "w", encoding="utf-8", newline="\n") as g:
            g.write(testo_pack)
        r = subprocess.run([sys.executable, os.path.join(QUI, "null_check.py"),
                            "--contratto", c, "--pack", p],
                           capture_output=True, timeout=180)
        return (r.returncode, r.stdout.decode("utf-8", "replace"),
                r.stderr.decode("utf-8", "replace"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("I-a", "equivalenza elemento per elemento sui tre scheletri reali")
def i_a():
    albero = nc.leggi_contratto(contratto())
    for nome, testo in SCHELETRI.items():
        pack = json.loads(testo)
        _, atteso = _riferimento(pack, albero)
        _, ottenuto = nc.controlla(pack, albero)
        assert ottenuto == atteso, (
            f"{nome}: la camminata diverge dal riferimento\n"
            f"  riferimento: {_percorsi(atteso)}\n"
            f"  ottenuto   : {_percorsi(ottenuto)}")
        assert len(atteso) == 3, f"{nome}: {len(atteso)} illeciti, attesi 3 — scheletro sbagliato"


@prova("I-b", "il difetto reale resta ripreso, con lo stesso percorso")
def i_b():
    pack = base()
    pack["payload"]["spend_evidence"] = [{"actor": "x", "amount": None}]
    ok, el = esamina(pack)
    assert not ok, "il difetto reale non e' piu' ripreso"
    assert "payload/spend_evidence[]/amount" in _percorsi(el), _percorsi(el)


@prova("I-c", "profondita' 1500, dentro la fascia: un VERDETTO, non un'eccezione")
def i_c():
    codice, uscita, err = _da_riga(_profondo(1500), contratto())
    assert "RecursionError" not in err, "la camminata cede ancora:\n" + err[-400:]
    assert codice == 1, f"uscita {codice}, atteso 1 (un null illecito trovato)\n{err[-300:]}"
    assert "null illeciti: 1" in uscita, uscita[:200]
    assert uscita.count("/a") >= 1400, "il percorso completo non e' riportato"


@prova("I-d", "profondita' 3500, oltre il parser: GUASTO nominato, exit 2, nessun traceback")
def i_d():
    codice, uscita, err = _da_riga(_profondo(3500), contratto())
    assert codice == 2, f"uscita {codice}, atteso 2: non aver potuto leggere e' un guasto"
    assert "Traceback" not in err, "il guasto e' uscito come traceback:\n" + err[-400:]
    assert "profondita" in err.lower(), f"la diagnostica non nomina la causa: {err!r}"


@prova("I-e", "ordine DFS su una fixture ANNIDATA con fratelli a profondita' diverse")
def i_e():
    pack = {"a": {"b": None}, "c": [None], "d": [[None]], "payload": {"ignoto": None}}
    _, el = esamina(pack)
    assert _percorsi(el) == ["a/b", "c[]", "d[][]", "payload/ignoto"], _percorsi(el)


@prova("I-f", "convenzione dei percorsi, con un DIZIONARIO NON VUOTO fuori contratto")
def i_f():
    # Il dizionario deve PORTARE un `null`: senza, si esercita la discesa fuori contratto ma
    # non si produce il rilievo che questo caso pretende.
    pack = {"a": {"b": None}, "c": [None], "d": [[None]],
            "payload": {"fuori_contratto": {"z": None}}}
    _, el = esamina(pack)
    assert _percorsi(el) == ["a/b", "c[]", "d[][]", "payload/fuori_contratto/z"], _percorsi(el)
    fuori = [e for e in el if e["motivo"] == "posizione non nel contratto"]
    assert len(fuori) == 4, f"attesi 4 rilievi fuori contratto, {len(fuori)}"


@prova("I-g", "contenitori vuoti e `null` alla radice")
def i_g():
    for vuoto in ({}, []):
        ok, el = esamina(vuoto)
        assert ok and el == [], f"{vuoto!r} produce rilievi: {_percorsi(el)}"
    ok, el = esamina(None)
    assert not ok and _percorsi(el) == ["(radice)"], _percorsi(el)


@prova("I-h", "nessuna soglia arbitraria: ne' nella funzione, ne' come costante di modulo")
def i_h():
    sorgente = open(os.path.join(QUI, "null_check.py"), encoding="utf-8").read()
    albero = ast.parse(sorgente)
    assert "setrecursionlimit" not in sorgente, "una soglia e' stata imposta all'interprete"
    for n in albero.body:
        if isinstance(n, ast.FunctionDef) and n.name == "controlla":
            num = [x.value for x in ast.walk(n)
                   if isinstance(x, ast.Constant)
                   and isinstance(x.value, (int, float)) and not isinstance(x.value, bool)]
            assert num == [], f"costanti numeriche dentro controlla(): {num}"
    # E una soglia non si scrive come numero dentro la funzione: si scrive come COSTANTE DI
    # MODULO con un nome — `contract_compile.LIMITE_PROFONDITA = 20` lo fa gia'. Dentro
    # `controlla` resterebbe un `Name`, invisibile al controllo qui sopra.
    modulo = []
    for n in albero.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            v = n.value
            if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) \
                    and not isinstance(v.value, bool):
                modulo.append(ast.unparse(n))
    assert modulo == [], f"costanti numeriche di modulo, nuove rispetto alla linea di base: {modulo}"
    # LA PRIMITIVA e' dove la camminata vive ora. Senza queste due clausole `I-h` guarderebbe
    # un `controlla` che non cammina piu' e passerebbe senza esaminare nulla — il caso si
    # svuoterebbe SENZA DIRLO, che e' esattamente cio' che il mandato 63 §6.2 aveva previsto.
    prim = [n for n in albero.body
            if isinstance(n, ast.FunctionDef) and n.name == "cammina_istanza"]
    assert prim, "la primitiva non esiste piu': `I-h` guarderebbe il vuoto"
    num = [x.value for x in ast.walk(prim[0])
           if isinstance(x, ast.Constant)
           and isinstance(x.value, (int, float)) and not isinstance(x.value, bool)]
    assert num == [], f"costanti numeriche dentro la primitiva: {num}"
    # UNA SOLA CAMMINATA in questo file: nessuna funzione si richiama per nome. La clausola
    # vale qui e non su `pack_repair.py`, dove `giu` continua legittimamente a farlo.
    #
    # `ast.walk` E NON `albero.body`: la redazione 1 guardava il solo livello di modulo, e una
    # chiusura ricorsiva ANNIDATA dentro `cammina_istanza` le passava sotto. Non e' un caso di
    # scuola: e' esattamente la forma del difetto che il commit `993c771` ha sciolto, cioe' la
    # `cammina` interna. A coglierla restavano `I-c` e `PR-2`, comportamentali, che dicono che
    # qualcosa e' rotto ma non PERCHE'. La clausola strutturale esiste per dire il perche', e
    # deve percorrere l'albero intero.
    for n in ast.walk(albero):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            auto = [c for c in ast.walk(n) if isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name) and c.func.id == n.name]
            assert not auto, f"{n.name}() si richiama per nome: e' tornata una camminata ricorsiva"


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
