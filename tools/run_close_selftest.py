#!/usr/bin/env python3
"""
run_close_selftest.py — dimostra che IC-3 conta i soli Claim e che la guardia grave resta larga.

Sono due proprieta' che tirano in direzioni opposte, ed e' per questo che servono entrambe
scritte.

`claim_verificati()` riconosce la FORMA `state VERIFIED + lista source_ids`, non il percorso: e'
larga apposta, perche' la post-condizione grave «nessun VERIFIED poggia solo su fonti mai lette»
deve vedere ogni oggetto che porta uno stato — Claim, Numeric, trigger, verdetti — e un controllo
che conoscesse solo i percorsi di oggi smetterebbe di guardare in silenzio il giorno in cui ne
compare uno nuovo.

`IC-3` invece misura un rapporto: quanti dei claim VERIFIED portano `evidence[]` popolato. Lo slot
`evidence[]` esiste **solo** sul Claim. Contare gli altri mette al denominatore oggetti che non
potrebbero mai stare al numeratore: sul primo canary reale il contatore diceva 19/36, e diciassette
di quei trentasei non avrebbero potuto avere `evidence[]` in nessun caso. Tre erano perfino
`statement.quantity` di claim gia' contati fra i diciannove — lo stesso oggetto contato due volte,
una come numeratore e una come denominatore che lo smentiva.

La riparazione e' nel punto di conteggio di IC-3, NON in `claim_verificati()`. Questo file esiste
per impedire che qualcuno, un domani, "semplifichi" restringendo la funzione: il caso B fallirebbe.

  uso:  python3 tools/run_close_selftest.py
        exit 0 = entrambe le proprieta' reggono
"""
import json, os, re, shutil, subprocess, sys, tempfile

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_ID = "20260101-0000"


def envelope(payload, unreachable=None):
    return {
        "agent": "pain-finder",
        "run_id": RUN_ID,
        "status": "OK",
        "budget_used": {"searches": 1, "fetches": 1},
        "stop_reason": "COMPLETED",
        "payload": payload,
        "new_sources": [],
        "queries_run": [],
        "unreachable_sources": unreachable or [],
        "self_check": {"invarianti_violati": [], "note": ""},
    }


def claim(id_, source_ids, con_evidenza=True, quantity_verificata=False):
    q = None
    if quantity_verificata:
        # Un Numeric VERIFIED con source_ids: ha la FORMA che claim_verificati riconosce, ma non
        # ha ne' puo' avere uno slot evidence[]. E' l'oggetto che gonfiava il denominatore.
        q = {"label": "ore perse", "value": 5.2, "unit": "ore/mese", "state": "VERIFIED",
             "source_ids": list(source_ids)}
    c = {
        "id": id_,
        "statement": {"subject": "imprese target", "observed": "perdono ore", "quantity": q},
        "state": "VERIFIED",
        "source_ids": list(source_ids),
        "promotion_rule_met": "1xT1",
    }
    if con_evidenza:
        c["evidence"] = [{"source_id": source_ids[0], "snippet": "passaggio citato",
                          "snippet_source": "FETCHED"}]
    return c


def albero_minimo(base, artefatto, unreachable=None):
    """tools/ + un run con un solo artefatto. ROOT di run_close e' il padre di tools/."""
    os.makedirs(os.path.join(base, "tools"))
    for f in ("run_close.py", "reconcile_run.py"):
        shutil.copy(os.path.join(ROOT, "tools", f), os.path.join(base, "tools", f))
    d = os.path.join(base, "runs", RUN_ID, "01-stage")
    os.makedirs(d)
    with open(os.path.join(d, "artefatto.json"), "w", encoding="utf-8") as f:
        json.dump(envelope(artefatto, unreachable), f, ensure_ascii=False)
    return os.path.join(base, "runs", RUN_ID)


def esegui(base, run_dir):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, os.path.join(base, "tools", "run_close.py"),
                        "--fase", "finale", run_dir],
                       capture_output=True, text=True, cwd=base, env=env)
    return r.stdout + r.stderr


def riga(uscita, frammento):
    for l in uscita.splitlines():
        if frammento in l:
            return l
    return ""


# ------------------------------------------------------------------ casi
def caso_a():
    """IC-3 conta i soli Claim.

    Due claim VERIFIED con evidence[], ciascuno con un `statement.quantity` VERIFIED che porta
    `source_ids`. Gli oggetti con la forma riconosciuta sono quattro; i Claim sono due.
    Atteso: 2/2. Col conteggio non ristretto sarebbe 2/4.
    """
    base = tempfile.mkdtemp()
    try:
        payload = {"claims": [claim("C-t-001", ["S-20260101-0001"], quantity_verificata=True),
                              claim("C-t-002", ["S-20260101-0002"], quantity_verificata=True)]}
        u = esegui(base, albero_minimo(base, payload))
        l = riga(u, "provenienza degli snippet dichiarata (IC-3)")
        m = re.search(r"(\d+)/(\d+) claim VERIFIED", l)
        if not m:
            return False, "IC-3 non compare nell'uscita di run_close"
        if (m.group(1), m.group(2)) != ("2", "2"):
            return False, (f"IC-3 legge {m.group(1)}/{m.group(2)}, atteso 2/2 — il denominatore "
                           "conta oggetti che non hanno lo slot evidence[]")
        return True, "IC-3 legge 2/2: i due Numeric VERIFIED non gonfiano il denominatore"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def caso_b():
    """La guardia grave resta larga.

    Un solo oggetto VERIFIED, e NON e' un Claim: e' un Numeric annidato le cui uniche fonti sono
    dichiarate irraggiungibili. La post-condizione grave deve accorgersene. Se qualcuno
    restringesse `claim_verificati()` ai soli Claim per "far tornare" IC-3, questo oggetto
    diventerebbe invisibile e la guardia passerebbe: e' precisamente il modo in cui una
    riparazione ne rompe un'altra in silenzio.
    """
    base = tempfile.mkdtemp()
    try:
        payload = {"stime": {"tam": {"label": "TAM", "value": 1000, "unit": "EUR",
                                     "state": "VERIFIED",
                                     "source_ids": ["S-20260101-0009"]}}}
        irr = [{"url": "https://esempio.invalid/x", "reason": "PAYWALL",
                "source_id": "S-20260101-0009",
                "impact": "avrebbe sostenuto il TAM"}]
        u = esegui(base, albero_minimo(base, payload, unreachable=irr))
        l = riga(u, "nessun VERIFIED poggia solo su fonti mai lette")
        if not l:
            return False, "la post-condizione grave non compare nell'uscita di run_close"
        if l.strip().startswith("ok"):
            return False, ("la guardia grave NON vede un VERIFIED non-Claim che poggia solo su "
                           "fonti irraggiungibili: claim_verificati() e' stata ristretta")
        return True, "la guardia grave vede un VERIFIED non-Claim: la funzione e' rimasta larga"
    finally:
        shutil.rmtree(base, ignore_errors=True)


CASI = [
    ("A", "IC-3 conta i soli oggetti Claim", caso_a),
    ("B", "la guardia grave resta larga e vede anche i non-Claim", caso_b),
]


def main():
    print("\n  run_close — autotest")
    print(f"  {'-' * 78}")
    falliti = []
    for lettera, titolo, fn in CASI:
        try:
            ok, dettaglio = fn()
        except Exception as e:                                   # noqa: BLE001
            ok, dettaglio = False, f"{type(e).__name__}: {e}"
        print(f"    {'ok   ' if ok else 'FAIL '} {lettera}. {titolo}")
        print(f"           {dettaglio}")
        if not ok:
            falliti.append(lettera)
    print(f"  {'-' * 78}")
    print(f"  totale: {len(CASI) - len(falliti)}/{len(CASI)}\n")
    if falliti:
        print("  >> le due proprieta' tirano in direzioni opposte: se una cade, verificare che")
        print("     la riparazione dell'altra non l'abbia mangiata.\n")
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
