#!/usr/bin/env python3
"""
reconcile_run.py — separa il guasto amministrativo dalla fabbricazione, e ripara il primo.

PERCHE' ESISTE.

Nel run 20260802-1759 il 30% dei source_id citati negli artefatti non risolveva nel
source-ledger, e 147 ricerche su 332 non erano mai state consolidate. Applicando IC-1 alla
lettera, i claim che poggiano su quegli ID vanno declassati a HYPOTHESIS, e con loro i verdetti
che ne discendono. La reazione istintiva — cancellare `state/` e ricominciare — butta via il
lavoro buono insieme a quello cattivo, perche' confonde due cose che non sono la stessa:

  MANCATO CONSOLIDAMENTO   l'agente HA raccolto la fonte, l'ha restituita nel proprio
                           `new_sources`, e l'orchestratore non l'ha appesa al ledger. La prova
                           esiste, su disco, dentro l'artefatto. E' un guasto amministrativo ed
                           e' RIPARABILE senza rifare una sola ricerca.
  DICHIARATA IRRAGGIUNGIBILE l'agente ha bussato e la fonte ha rifiutato: paywall, blocco, 404,
                           robots, limite API. IC-4 IMPONE di dichiararla. La provenienza esiste
                           — sta in `unreachable_sources` — e va consolidata nel ledger col suo
                           `access_status`. Contarla come orfana significa marcare DEGRADATO un
                           run per aver fatto la cosa giusta, che e' come si insegna a un agente
                           a tacere le fonti che non ha potuto aprire.
  CITAZIONE SENZA ORIGINE  un source_id compare in un claim e non compare in NESSUN `new_sources`
                           ne' in NESSUN `unreachable_sources` di NESSUN artefatto del run.
                           Nessuno l'ha mai incontrato. Questa non si ripara: e' esattamente la
                           patologia che IC-1 esiste per intercettare, e i claim che vi poggiano
                           vanno declassati.

Finche' le tre restano indistinguibili, l'unica scelta possibile e' fra buttare tutto e fidarsi
di tutto, e sono entrambe sbagliate. Questo strumento le separa contandole.

E ne aggiunge una che nessuno guardava. Riconoscere le irraggiungibili come provenienza legittima
le fa entrare nel ledger, e da quel momento IC-1 le risolve: un claim VERIFIED sostenuto SOLO da
pagine che nessuno ha potuto aprire passerebbe il controllo di citazione. Sarebbe un falso
negativo comprato col falso positivo appena tolto. Una fonte irraggiungibile e' provenienza
valida per l'ESISTENZA della fonte e nulla di piu': non puo' sostenere da sola uno stato VERIFIED,
perche' non esiste lo snippet che IC-3 pretende per la coppia (claim, fonte). Quei claim sono
elencati a parte e valgono exit 1 come le citazioni senza origine.

  uso:  python3 tools/reconcile_run.py runs/<run_id>              → diagnosi, non tocca niente
        python3 tools/reconcile_run.py runs/<run_id> --consolida  → appende al ledger il
                                                                    recuperabile, e SOLO quello
        exit 0 = tutto risolvibile · 1 = restano citazioni senza origine
"""
import json, os, re, sys, glob
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RX_SRC = re.compile(r"\bS-[0-9]{8}(?:-[0-9]{4})?-[0-9]{4}\b")


def carica_ledger(p, chiave):
    visti, righe = set(), 0
    if os.path.exists(p):
        for l in open(p, encoding="utf-8"):
            if not l.strip():
                continue
            try:
                o = json.loads(l)
            except Exception:
                continue
            righe += 1
            v = o.get(chiave) if isinstance(o, dict) else None
            if v:
                visti.add(v)
    return visti, righe


def claim_di(nodo):
    """
    Ogni claim VERIFIED dell'artefatto, ovunque sia annidato.

    Non si cerca per chiave 'claims': cinque agenti diversi li appendono sotto nomi diversi
    (`claims`, `evidence`, `facts`, dentro `payload.pains[]`, dentro `payload.competitors[]`).
    Un controllo che conosce solo il percorso di oggi smette di guardare il giorno in cui un
    agente ne aggiunge uno — in silenzio, che e' il modo peggiore. Qui si riconosce la FORMA:
    un oggetto con `state: VERIFIED` e una lista `source_ids`.
    """
    if isinstance(nodo, dict):
        if nodo.get("state") == "VERIFIED" and isinstance(nodo.get("source_ids"), list):
            yield nodo
        for v in nodo.values():
            yield from claim_di(v)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from claim_di(v)


def riconcilia(run_dir, consolida=False):
    run_id = os.path.basename(os.path.normpath(run_dir))
    sl = os.path.join(ROOT, "state", "source-ledger.jsonl")
    ql = os.path.join(ROOT, "state", "query-ledger.jsonl")
    id_nel_ledger, n_src = carica_ledger(sl, "id")
    q_nel_ledger, n_q = carica_ledger(ql, "normalized_query")

    citati, raccolte, query_dichiarate = set(), {}, {}
    irraggiungibili, verified_solo_irr, claim_verified = {}, [], []
    n_art = 0
    for p in sorted(glob.glob(os.path.join(run_dir, "*", "*.json"))):
        try:
            o = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        n_art += 1
        citati |= set(RX_SRC.findall(json.dumps(o, ensure_ascii=False)))
        if not isinstance(o, dict):
            continue
        for s in (o.get("new_sources") or []):
            if isinstance(s, dict) and s.get("id"):
                s.setdefault("run_id", run_id)
                s.setdefault("retrieved_by", o.get("agent", "sconosciuto"))
                raccolte.setdefault(s["id"], s)
        for u in (o.get("unreachable_sources") or []):
            if not isinstance(u, dict) or not u.get("source_id"):
                continue
            # Il record di ledger si ricostruisce dalla dichiarazione: e' meno di una fonte
            # aperta, ed e' giusto che lo sia. access_status porta il motivo del rifiuto, cosi'
            # chi legge il ledger vede subito che questa pagina non e' stata letta da nessuno.
            irraggiungibili.setdefault(u["source_id"], {
                "id": u["source_id"], "run_id": run_id,
                "url": u.get("url_or_target", "UNKNOWN"),
                "publisher": "UNKNOWN", "title": u.get("what", "UNKNOWN"),
                "published_date": "UNKNOWN", "accessed_at": "UNKNOWN", "tier": "T5",
                "access_status": u.get("why", "BLOCKED"),
                "retrieved_by": o.get("agent", "sconosciuto"),
                "_nota": "registrata per IC-4: tentata e rifiutata, mai letta"})
        for qq in (o.get("queries_run") or []):
            if isinstance(qq, dict) and qq.get("normalized_query"):
                qq.setdefault("run_id", run_id)
                qq.setdefault("agent", o.get("agent", "sconosciuto"))
                query_dichiarate.setdefault(qq["normalized_query"], qq)
        for c in claim_di(o):
            ids = [x for x in (c.get("source_ids") or []) if isinstance(x, str)]
            if ids:
                claim_verified.append((os.path.basename(p), c.get("id", "?"), ids))

    # SECONDA PASSATA, e il fatto che sia seconda non e' uno stile. Il claim che sta
    # nell'artefatto 1 puo' citare una fonte dichiarata irraggiungibile nell'artefatto 9: dentro
    # il ciclo l'insieme e' ancora mezzo vuoto e il controllo tace su meta' dei casi, in modo
    # dipendente dall'ordine alfabetico dei file. E' la stessa forma dell'errore di ordine che
    # aveva reso muta la mutazione SC-21: verificare prima che il fatto sia completo.
    for f, cid, ids in claim_verified:
        if all(x in irraggiungibili for x in ids):
            verified_solo_irr.append((f, cid, ids))

    orfani = citati - id_nel_ledger
    recuperabili = {i for i in orfani if i in raccolte}
    dichiarate_irr = {i for i in orfani - recuperabili if i in irraggiungibili}
    senza_origine = orfani - recuperabili - dichiarate_irr
    q_mancanti = {k: v for k, v in query_dichiarate.items() if k not in q_nel_ledger}

    print(f"\n  run: {run_dir}   artefatti letti: {n_art}")
    print(f"  {'-'*72}")
    print(f"  source_id citati negli artefatti .................. {len(citati)}")
    print(f"  gia' nel source-ledger ........................... {len(citati & id_nel_ledger)}")
    print(f"  ORFANI ........................................... {len(orfani)}"
          + (f"  ({len(orfani)/len(citati):.0%})" if citati else ""))
    print(f"    di cui RECUPERABILI (stanno in un new_sources) . {len(recuperabili)}")
    print(f"    di cui IRRAGGIUNGIBILI dichiarate (IC-4) ....... {len(dichiarate_irr)}")
    print(f"    di cui SENZA ORIGINE (nessuno li ha visti) ..... {len(senza_origine)}")
    print(f"  query dichiarate dagli agenti .................... {len(query_dichiarate)}")
    print(f"    mai consolidate nel query-ledger .............. {len(q_mancanti)}")
    print(f"  claim VERIFIED sostenuti SOLO da irraggiungibili .. {len(verified_solo_irr)}")
    print(f"  {'-'*72}")

    if verified_solo_irr:
        print("\n  VERIFIED SU PAGINE MAI LETTE — ogni source_id di questi claim e' una fonte")
        print("  che ha rifiutato. IC-3 pretende uno snippet per la coppia (claim, fonte) e li'")
        print("  non c'e' niente da citare: vanno declassati a HYPOTHESIS. Dichiarare una fonte")
        print("  irraggiungibile e' corretto; farle sostenere un fatto no.\n")
        for f, cid, ids in verified_solo_irr[:15]:
            print(f"    {f}  {cid}  <- {', '.join(ids)}")
        if len(verified_solo_irr) > 15:
            print(f"    ... e altri {len(verified_solo_irr)-15}")

    if senza_origine:
        print("\n  CITAZIONI SENZA ORIGINE — non riparabili, i claim che vi poggiano vanno")
        print("  declassati a HYPOTHESIS insieme ai verdetti che ne discendono:\n")
        for i in sorted(senza_origine)[:25]:
            print(f"    {i}")
        if len(senza_origine) > 25:
            print(f"    ... e altri {len(senza_origine)-25}")

    if not consolida:
        print("\n  Diagnosi soltanto: non ho scritto niente.")
        print("  Con --consolida appendo ai ledger SOLO cio' che un agente ha davvero incontrato:")
        print("  quello che ha raccolto, e quello che ha tentato e non ha potuto aprire.")
        print("  Le citazioni senza origine non vengono mai inventate: restano scoperte.\n")
        return 1 if (senza_origine or verified_solo_irr) else 0

    with open(sl, "a", encoding="utf-8") as g:
        for i in sorted(recuperabili):
            g.write(json.dumps(raccolte[i], ensure_ascii=False) + "\n")
        for i in sorted(dichiarate_irr):
            g.write(json.dumps(irraggiungibili[i], ensure_ascii=False) + "\n")
    with open(ql, "a", encoding="utf-8") as g:
        for k in sorted(q_mancanti):
            g.write(json.dumps(q_mancanti[k], ensure_ascii=False) + "\n")
    n_scritte = len(recuperabili) + len(dichiarate_irr)
    print(f"\n  Consolidate {len(recuperabili)} fonti raccolte, {len(dichiarate_irr)} dichiarate"
          f" irraggiungibili e {len(q_mancanti)} query.")
    print(f"  source-ledger: {n_src} -> {n_src + n_scritte} righe")
    print(f"  query-ledger:  {n_q} -> {n_q + len(q_mancanti)} righe")
    if senza_origine or verified_solo_irr:
        print(f"\n  RESTANO {len(senza_origine)} citazioni senza origine e {len(verified_solo_irr)}"
              " claim VERIFIED su pagine mai lette.")
        print("  Il run NON diventa valido per questo: ricontrolla con run_close.py --fase finale")
        print("  e declassa i claim elencati sopra. Consolidare non e' assolvere.\n")
    else:
        print("\n  Nessuna citazione senza origine: il run e' ora integralmente tracciabile.")
        print("  Riesegui run_close.py --fase finale.\n")
    return 1 if (senza_origine or verified_solo_irr) else 0


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not a:
        r = sorted(glob.glob(os.path.join(ROOT, "runs", "2*")))
        if not r:
            print("nessun run trovato"); sys.exit(2)
        a = [r[-1]]
    d = a[0] if os.path.isabs(a[0]) else os.path.join(ROOT, a[0])
    sys.exit(riconcilia(d, "--consolida" in sys.argv))
