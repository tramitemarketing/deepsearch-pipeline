#!/usr/bin/env python3
"""
replay_validate_selftest.py — dimostra che replay_validate.py NON e' cieco, e soprattutto che
l'ORDINE fra i due controlli e' quello dichiarato.

La cosa da provare qui non e' che il null-check funzioni: quello lo prova gia'
`null_check_selftest.py`, quindici volte. Qui si prova che il null-check e' DAVVERO NEL PERCORSO,
che gira PRIMA della validazione di schema, e che il suo esito e' DECISIVO per il verdetto. Un
controllo giusto invocato dopo, o invocato e ignorato, non e' integrato: e' decorativo.

IL PACK ROTTO E' QUELLO VERO. `spend_evidence` porta il frammento verbatim emesso dal canale
`trend_adozione` del replay 20260823-1647 — `claim_id: C-r22t-002`, `spend_form: software`,
`amount: null` — che e' l'unico errore che questa integrazione esiste per intercettare. Non e'
una riproduzione inventata: e' copiato dal raw.

IL CONTRATTO NON E' SCRITTO A MANO. Ogni prova lo rigenera con `contract_compile.py --vista
delivery`: una fixture sarebbe una seconda fonte manuale del contratto.

  uso:  python tools/replay_validate_selftest.py
        exit 0 = tutte verdi
"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

import replay_validate as rv                                              # noqa: E402
import null_check as _nc                                                   # noqa: E402

ESITI = []


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


def contratto():
    r = subprocess.run([sys.executable, os.path.join(QUI, "contract_compile.py"),
                        "--agente", "evidence-researcher", "--vista", "delivery"],
                       capture_output=True, timeout=120)
    assert r.returncode == 0, f"il contratto non si genera: {r.stderr.decode('utf-8','replace')}"
    return r.stdout.decode("utf-8")


def pack_valido():
    """Un evidence-pack conforme, con i null AMMESSI dal contratto e nessun altro."""
    return {"agent": "evidence-researcher", "run_id": "20260823-1647", "status": "OK",
            "budget_used": {"searches": 1, "searches_limit": 8,
                            "fetches": 1, "fetches_limit": 16},
            "stop_reason": "COMPLETED",
            "payload": {"pain_id": "P-003", "channel": "trend_adozione",
                        "claims": [{"promoted_by": "FP0", "id": "C-r22t-001",
                                    "statement": {"subject": "s", "observed": "o",
                                                  # `quantity` e' `o~`: null AMMESSO
                                                  "quantity": None, "period": None},
                                    "state": "HYPOTHESIS", "source_ids": []}],
                        "q4_answer": {"question_id": "Q4", "answer": "a", "grounded": False,
                                      "source_ids": []},
                        "coverage": {"angles_covered": [], "gaps": []}},
            "new_sources": [], "queries_run": [], "unreachable_sources": [],
            "self_check": {"invarianti_violati": []}}


def pack_reale_rotto():
    """Il pack valido, piu' il frammento VERBATIM emesso da trend_adozione nel replay."""
    p = pack_valido()
    p["payload"]["spend_evidence"] = [
        {"claim_id": "C-r22t-002", "spend_form": "software", "amount": None}]
    return p


def raw_da(pack, prosa=True):
    """Un raw come lo produce il CLI: prosa attorno, JSON dentro un blocco ```json."""
    corpo = json.dumps(pack, ensure_ascii=False, indent=2)
    if not prosa:
        return corpo
    return ("Based on my research, I'll now compile the evidence pack:\n\n"
            "```json\n" + corpo + "\n```\n")


def in_temp(pack, prosa=True):
    d = tempfile.mkdtemp(prefix="rv-selftest-")
    raw = os.path.join(d, "canale.raw.txt")
    with open(raw, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw_da(pack, prosa))
    con = os.path.join(d, "delivery.txt")
    with open(con, "w", encoding="utf-8", newline="\n") as f:
        f.write(contratto())
    return d, raw, con


def esegui(raw, con, extra=()):
    r = subprocess.run([sys.executable, os.path.join(QUI, "replay_validate.py"),
                        "--raw", raw, "--contratto", con,
                        "--schemi", os.path.join(ROOT, "schemas")] + list(extra),
                       capture_output=True, timeout=300)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


# ------------------------------------------------------------------ le prove

@prova("V1", "ROSSA sul pack REALE con `amount: null`: verdetto NON VALIDO")
def v1():
    import shutil
    d, raw, con = in_temp(pack_reale_rotto())
    try:
        codice, out, err = esegui(raw, con)
        assert codice == 1, f"uscita {codice}, atteso 1 (non valido). stderr: {err}"
        rap = json.loads(out)
        assert rap["verdetto"] == "NON VALIDO", rap["verdetto"]
        assert rap["null_check"]["esito"] == "RIFIUTATO"
        assert any(e["percorso"] == "payload/spend_evidence[]/amount"
                   for e in rap["null_check"]["illeciti"]), rap["null_check"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V2", "VERDE sui null AMMESSI (`quantity` e' `o~`): verdetto VALIDO")
def v2():
    import shutil
    d, raw, con = in_temp(pack_valido())
    try:
        codice, out, err = esegui(raw, con)
        assert codice == 0, f"uscita {codice}, atteso 0. stdout: {out[:400]} stderr: {err[:400]}"
        rap = json.loads(out)
        assert rap["verdetto"] == "VALIDO", rap
        assert rap["null_check"]["esito"] == "AMMESSO"
        assert rap["null_check"]["illeciti"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V3", "il null-check gira PRIMA: il rapporto lo dichiara e l'ordine e' quello")
def v3():
    import shutil
    d, raw, con = in_temp(pack_reale_rotto())
    try:
        _, out, _ = esegui(raw, con)
        rap = json.loads(out)
        assert rap["ordine"] == ["null_check", "schema"], (
            f"l'ordine dichiarato non e' quello prescritto: {rap.get('ordine')}. Il null-check "
            "va eseguito SUBITO PRIMA della validazione di schema")
        assert list(rap).index("null_check") < list(rap).index("schema"), \
            "nel rapporto la sezione dello schema precede quella del null-check"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V4", "l'esito del null-check e' DECISIVO: da solo rende NON VALIDO uno schema pulito")
def v4():
    import shutil
    # IL CASO CHE SEPARA I DUE CONTROLLI. Il pack reale con `amount: null` non serve a questa
    # prova: fallisce ANCHE lo schema, quindi resterebbe non valido pure se il null-check fosse
    # consultivo — e infatti una prima stesura di V4 usava quello ed era muta alla mutazione
    # «il null-check non e' decisivo».
    # Serve un null che il contratto rifiuti e lo schema ammetta. Esiste: gli schemi non pongono
    # `additionalProperties: false`, quindi una chiave sconosciuta passa la validazione; il
    # contratto invece non la conosce e rifiuta il null.
    p = pack_valido()
    p["payload"]["campo_non_nel_contratto"] = None
    d, raw, con = in_temp(p)
    try:
        codice, out, _ = esegui(raw, con)
        rap = json.loads(out)
        assert rap["schema"]["n_errori"] == 0, (
            f"la premessa non regge: questo pack doveva essere valido per lo schema. "
            f"Errori: {rap['schema']['errori']}")
        assert rap["null_check"]["esito"] == "RIFIUTATO"
        assert rap["verdetto"] == "NON VALIDO" and codice == 1, (
            "il verdetto non e' governato dal null-check: con schema pulito e un solo null "
            f"illecito il pack risulta {rap['verdetto']}. Rapporto: {rap}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V13", "il null-check e' ESEGUITO prima, non solo dichiarato prima")
def v13():
    # V3 guarda l'etichetta `ordine` e la posizione delle chiavi nel rapporto: due cose che si
    # possono scrivere giuste avendo invertito le chiamate. Qui si guarda la sequenza reale,
    # strumentando le due funzioni e registrando chi viene chiamato per primo.
    import shutil
    import null_check as nc
    d, raw, con = in_temp(pack_reale_rotto())
    sequenza = []
    vero_null, vero_schema = nc.controlla, rv.valida_schema
    def spia_null(*a, **k):
        sequenza.append("null_check")
        return vero_null(*a, **k)
    def spia_schema(*a, **k):
        sequenza.append("schema")
        return vero_schema(*a, **k)
    nc.controlla = spia_null
    rv.valida_schema = spia_schema
    try:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):   # il rapporto non sporca l'elenco
            rv.main(["--raw", raw, "--contratto", con,
                     "--schemi", os.path.join(ROOT, "schemas")])
    finally:
        nc.controlla, rv.valida_schema = vero_null, vero_schema
        shutil.rmtree(d, ignore_errors=True)
    assert sequenza == ["null_check", "schema"], (
        f"l'ordine di ESECUZIONE e' {sequenza}, non ['null_check', 'schema']. Il mandato "
        "prescrive il null-check SUBITO PRIMA della validazione, e un ordine dichiarato in una "
        "costante non e' l'ordine eseguito")


@prova("V14", "l'estrazione non si fa ingannare dalle graffe dentro le stringhe")
def v14():
    import shutil
    # Un pack il cui testo contiene `}` e `{` dentro un valore di stringa. Un estrattore che
    # conti le graffe senza saltare le stringhe chiude il pack in anticipo e deserializza un
    # frammento — oppure fallisce del tutto. Gli snippet reali sono citazioni: le graffe ci
    # finiscono senza fatica.
    p = pack_reale_rotto()
    p["payload"]["coverage"]["gaps"] = ["la fonte cita un frammento { \"chiave\": 1 } e poi }}"]
    d, raw, con = in_temp(p)
    try:
        codice, out, err = esegui(raw, con)
        assert codice == 1, f"uscita {codice}: l'estrazione si e' rotta. stderr: {err[:200]}"
        rap = json.loads(out)
        assert any(e["percorso"] == "payload/spend_evidence[]/amount"
                   for e in rap["null_check"]["illeciti"]), (
            "il pack e' stato troncato all'altezza della graffa dentro la stringa: "
            f"{rap['null_check']}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V5", "la validazione di schema resta e non e' persa: i suoi errori sono riportati")
def v5():
    import shutil
    p = pack_valido()
    p["payload"]["coverage"]["gaps"] = "una stringa narrativa invece dell'array"
    d, raw, con = in_temp(p)
    try:
        codice, out, _ = esegui(raw, con)
        rap = json.loads(out)
        assert codice == 1 and rap["verdetto"] == "NON VALIDO"
        assert rap["null_check"]["esito"] == "AMMESSO", "questo pack non ha null illeciti"
        assert rap["schema"]["n_errori"] >= 1, (
            "l'errore di schema e' sparito: integrare il null-check non deve TOGLIERE la "
            f"validazione che gia' c'era. Rapporto: {rap['schema']}")
        assert any(e["puntatore"] == "/payload/coverage/gaps" for e in rap["schema"]["errori"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V6", "un null illecito NON nasconde gli errori di schema dello stesso pack")
def v6():
    import shutil
    p = pack_reale_rotto()
    p["payload"]["coverage"]["gaps"] = "stringa"
    d, raw, con = in_temp(p)
    try:
        _, out, _ = esegui(raw, con)
        rap = json.loads(out)
        assert rap["null_check"]["esito"] == "RIFIUTATO"
        assert rap["schema"]["n_errori"] >= 1, (
            "il rifiuto del null-check ha soppresso la misura degli altri errori. Il replay "
            "esiste per CONTARE le classi di difetto: nasconderne una perche' un'altra ha gia' "
            "deciso il verdetto degrada lo strumento di misura")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V7", "solo `evidence-researcher`: ogni altro agente e' rifiutato, non indovinato")
def v7():
    import shutil
    d, raw, con = in_temp(pack_valido())
    try:
        codice, _, err = esegui(raw, con, ["--agente", "competitor-analyst"])
        assert codice == 2, f"uscita {codice}, atteso 2 (uso errato)"
        assert "evidence-researcher" in err, err
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V8", "raw senza JSON estraibile: uscita 2, distinta dal pack non valido")
def v8():
    import shutil
    d, raw, con = in_temp(pack_valido())
    try:
        with open(raw, "w", encoding="utf-8", newline="\n") as f:
            f.write("You've hit your monthly spend limit\n")
        codice, _, err = esegui(raw, con)
        assert codice == 2, (
            f"uscita {codice}, atteso 2. «Il raw non contiene un pack» e «il pack non e' valido» "
            "sono due cose diverse: il primo e' un guasto dell'invocazione, il secondo una misura")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V9", "il JSON si estrae anche senza blocco ```json, e il verdetto non cambia")
def v9():
    import shutil
    d, raw, con = in_temp(pack_reale_rotto(), prosa=False)
    try:
        codice, out, _ = esegui(raw, con)
        assert codice == 1 and json.loads(out)["verdetto"] == "NON VALIDO"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V10", "il rapporto e' deterministico byte per byte")
def v10():
    import shutil
    d, raw, con = in_temp(pack_reale_rotto())
    try:
        a = esegui(raw, con)[1]
        b = esegui(raw, con)[1]
        assert a == b, "due esecuzioni danno rapporti diversi"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V11", "il null-check e' quello committato, non una copia")
def v11():
    src = open(os.path.join(QUI, "replay_validate.py"), encoding="utf-8").read()
    assert re.search(r"^\s*import null_check\b", src, re.M), \
        "replay_validate non importa null_check: se lo ha riscritto, sono due fonti"
    for vietato in ("def ammette_null", "def leggi_contratto", "def controlla"):
        assert vietato not in src, \
            f"«{vietato}» e' ridefinito qui invece di essere importato da null_check"


@prova("V15", "un guasto del passo di schema NON annulla il verdetto gia' preso dal null-check")
def v15():
    import shutil
    # Il caso che il mandato non ammette: il pack ha un `null` illecito, quindi va rifiutato come
    # non valido — anche se la validazione di schema non puo' girare. Su POSIX non puo' girare
    # per davvero (`referencing` assente, `jsonschema` 4.10.3 senza `registry=`); qui lo si
    # riproduce senza toccare la piattaforma, puntando `--schemi` a una directory priva dello
    # schema. Una prima stesura racchiudeva i due passi nello stesso `try` e usciva 2: cioe'
    # «l'invocazione e' rotta» invece di «il pack e' sbagliato», con il verdetto del null-check
    # gia' calcolato e buttato.
    d, raw, con = in_temp(pack_reale_rotto())
    vuota = tempfile.mkdtemp(prefix="rv-senza-schemi-")
    try:
        codice, out, err = esegui(raw, con, ["--schemi", vuota])
        assert codice == 1, (
            f"uscita {codice}, atteso 1. Con il null-check gia' concluso, un guasto del passo di "
            f"schema non puo' declassare il verdetto a «invocazione rotta». stderr: {err[:200]}")
        rap = json.loads(out)
        assert rap["verdetto"] == "NON VALIDO"
        assert rap["null_check"]["esito"] == "RIFIUTATO"
        assert any(e["percorso"] == "payload/spend_evidence[]/amount"
                   for e in rap["null_check"]["illeciti"])
        assert rap["schema"]["disponibile"] is False and rap["schema"]["motivo_indisponibilita"], \
            "la misura mancante non e' dichiarata: sembrerebbe uno schema pulito"
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(vuota, ignore_errors=True)


@prova("V16", "contratto illeggibile: uscita 2, il terzo modo di guastarsi")
def v16():
    import shutil
    d, raw, con = in_temp(pack_valido())
    try:
        with open(con, "w", encoding="utf-8", newline="\n") as f:
            f.write("non sono un contratto: manca la sezione ALBERO\n")
        codice, _, err = esegui(raw, con)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert "contratto" in err.lower(), err
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("V12", "il contratto reale ammette `quantity` e rifiuta `amount`: la premessa di V1 e V2")
def v12():
    import null_check as nc
    alb = nc.leggi_contratto(contratto())
    q = alb.figli["payload"].figli["claims"].figli["statement"].figli["quantity"]
    a = alb.figli["payload"].figli["spend_evidence"].figli["amount"]
    assert q.nullable and q.tipo == "o~", f"`quantity` non e' piu' `o~`: {q.tipo}"
    assert not a.nullable and a.tipo == "o", f"`amount` non e' piu' `o` non-nullable: {a.tipo}"


# ------------------------------------------------------------------ esecuzione

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

#: L'insieme e' MISURATO: senza il backend, queste tre cadono e nessun'altra. Ma LA
#: PROTEZIONE E' PARZIALE, e va detto per quello che e': un nome TOLTO da qui fa girare la
#: prova e produce una rossa; un nome AGGIUNTO che in realta' girerebbe benissimo viene
#: dichiarato non applicabile e nessun controllo se ne accorge — misurato, exit 0 su
#: entrambe le piattaforme. La tenuta dell'insieme e' stabilita per misura ESTERNA
#: (svuotarlo e osservare quali prove cadono davvero), non da una guardia interna.
DIPENDENTI_DALLO_SCHEMA = ('V2', 'V5', 'V6')
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
    'V1', 'V2', 'V3', 'V4', 'V5', 'V6', 'V7', 'V8', 'V9', 'V10', 'V11', 'V12', 'V13', 'V14',
    'V15', 'V16',
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
            codice = rv.main(["--raw", raw, "--contratto", con])
        testo = uscita.getvalue().strip()
        return classifica_sonda(codice, json.loads(testo) if testo else None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def oracle_ripristino():
    """L'ORACLE del ripristino: identita' di TUTTI i seam che questo banco sostituisce.

    Sono DUE, non uno, e il censimento non e' stato fatto a occhio: viene dall'ALBERO
    SINTATTICO, cercando ogni `Attribute` in posizione di Store, a QUALUNQUE profondita' di
    catena. La forma che una lettura per riga non vede e' l'assegnazione a TUPLA —
    `nc.controlla, rv.valida_schema = spia_null, spia_schema` — che sostituisce due
    riferimenti in una riga sola e che una regex ingenua conta per zero.

      · `replay_validate.valida_schema` — sostituito da `V13`
      · `null_check.controlla`          — sostituito da `V13`

    Sorvegliarne uno solo lasciava l'altro scoperto: misurato che, rimosso il ripristino
    del solo `null_check.controlla`, il gate restava VERDE ed exit 0 su entrambe le
    piattaforme. Un oracle parziale non e' un oracle a meta': e' un oracle che dichiara una
    copertura che non ha.

    Non chiama la sonda e non la influenza: legge soltanto identita' di attributo, sui
    riferimenti ai moduli GIA' IMPORTATI. Un controllo che risondasse sarebbe una seconda
    valutazione per invocazione.
    """
    return {"replay_validate.valida_schema": rv.valida_schema,
            "null_check.controlla": _nc.controlla}


def guasti_di_ripristino(istantanea):
    """Confronto per IDENTITA' (`is`), non per uguaglianza.

    Una funzione sostituita da una che si comporta allo stesso modo passerebbe qualunque
    confronto per valore: qui interessa che sia TORNATO L'OGGETTO DI PRIMA, e la sola
    domanda che lo accerta e' `is`.
    """
    finale = oracle_ripristino()
    return [f"ripristino: `{chiave}` non e' stato ripristinato"
            for chiave, atteso in istantanea.items() if finale.get(chiave) is not atteso]


def main():
    """UNA invocazione, UNA sonda, UNA valutazione decisionale.

    QUI, E SOLTANTO QUI, si cattura l'eccezione che la sonda ha lasciato salire. Non esiste un
    `try` attorno a `main()`: una rete esterna farebbe morire il processo senza riepilogo, e
    l'osservatore del terzo stato — che pretende una riga rossa NOMINATA — sparirebbe.

    Il ripristino si verifica su OGNI cammino. Sul cammino normale e su quello del terzo stato
    lo fa `_corpo`, dove un seam non ripristinato tinge di rosso il gate. Sul cammino
    D'ERRORE — un'eccezione che sfugge a `_corpo` — quel confronto non verrebbe mai
    raggiunto: qui non si puo' cambiare un esito, perche' il processo sta gia' morendo, ma si
    puo' NOMINARE il seam rimasto sostituito invece di lasciarlo sparire dentro il traceback.
    """
    try:
        stato = sonda_capability()
    except BaseException as e:                                            # noqa: BLE001
        stato, diagnostica = "TERZO_STATO", f"{type(e).__name__}: {e}"
    else:
        diagnostica = None

    istantanea = oracle_ripristino()
    try:
        return _corpo(stato, diagnostica, istantanea)
    except BaseException:                                                 # noqa: BLE001
        for g in guasti_di_ripristino(istantanea):
            print(f"  {ETICHETTA_ROSSA} {g} (rilevato sul cammino d'errore)")
        raise


def _corpo(stato, diagnostica, istantanea):
    print(f"replay_validate_selftest — {len(ESITI)} prove — provisioning: {stato}\n")

    if stato == "TERZO_STATO":
        # Il gate si arresta PRIMA delle prove: nessuna viene avviata, e non si dichiara non
        # applicabile nulla, perche' non si e' accertato nulla. Un terzo stato non e' un ambiente
        # incompleto: e' un guasto, e si presenta nominato invece che come traceback.
        print(f"  {ETICHETTA_ROSSA} provisioning  TERZO_STATO — "
              f"{diagnostica or 'la sonda non ha prodotto uno stato ammesso'}")
        print("\n  gate di provisioning: ROSSO")
        # Anche qui il ripristino si verifica: nessuna prova e' partita, quindi nessun seam
        # DOVREBBE risultare sostituito, ed e' precisamente per questo che il confronto ha
        # valore — se ne trovasse uno, la sostituzione verrebbe da fuori questo banco.
        for g in guasti_di_ripristino(istantanea):
            print(f"  {ETICHETTA_ROSSA} {g}")
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
    guasti += guasti_di_ripristino(istantanea)

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
