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
import json, os, re, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

import replay_validate as rv                                              # noqa: E402

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

def main():
    print(f"replay_validate_selftest — {len(ESITI)} prove\n")
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
