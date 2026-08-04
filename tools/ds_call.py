#!/usr/bin/env python3
"""
ds_call.py — ponte verso DeepSeek per gli stage di sola RACCOLTA. Nessun router, nessun proxy.

COSA FA E COSA NON TOCCA.

Chiama l'endpoint OpenAI-compatibile di DeepSeek con `urllib` della libreria standard, valida la
risposta contro uno schema della pipeline, e scrive il risultato su file. Non installa niente, non
importa niente fuori dalla standard library, e **non legge ne' scrive ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY
o qualunque variabile del provider di questa sessione**. DeepSeek entra come processo esterno via
Bash e basta: se questo file sparisce, la pipeline gira esattamente come prima, solo piu' cara.

PERCHE' LA DELEGA E' ARCHITETTURALMENTE SANA — e dove diventa pericolosa.

La separazione dei poteri di `CLAUDE.md` §1 dice che chi cerca non conclude. Delegare la raccolta a
un modello DIVERSO rende quell'invariante piu' forte, non piu' debole: il modello che giudica non
puo' avere fatto la raccolta nemmeno volendo. E' la versione forte della stessa regola.

Il pericolo sta altrove, ed e' preciso. DeepSeek **non ha letto `CLAUDE.md`**. Non conosce IC-1..IC-4,
non sa cos'e' il source-ledger, non riceve un `source_id_range` da un cursore, e non ha motivo di
scrivere `UNKNOWN` invece di una plausibilita'. Quindi il suo output non e' l'output di un agente
della pipeline: e' un **input non fidato**, e va trattato come si tratta una fonte, non come si
tratta un collega. Questo file e' quel confine.

Tre rifiuti meccanici, applicati prima che l'orchestratore veda una sola riga:

  1. NUMERO SENZA FONTE. Qualunque oggetto con `state: VERIFIED` e nessun `source_ids` non
     valorizzato viene rifiutato. Non declassato: rifiutato, insieme a tutta la risposta. E' la
     regola che l'addendum chiede esplicitamente — "non riempirlo tu con una stima" — resa
     impossibile da aggirare invece che raccomandata.
  2. SCHEMA NON RISPETTATO. Campi obbligatori mancanti o di tipo sbagliato: rifiuto e un solo
     rilancio, con l'errore in input. Al secondo fallimento esce 2 e l'orchestratore parcheggia.
  4. URL NON PRESENTI NELL'INPUT. Il payload non ha il campo `tools`: questo endpoint non naviga.
     Quindi ogni URL che compare nella risposta e non era gia' nel prompt e' RICORDATO, non
     trovato, e la risposta viene rifiutata. E' il rifiuto che rende il ponte utilizzabile: forza
     l'unica architettura corretta — l'orchestratore cerca e apre le pagine, mette il testo nel
     prompt, e il modello esterno estrae e struttura invece di ricordare.
  3. ID DI FONTE INVENTATI. DeepSeek non assegna ID: li assegna l'orchestratore dal proprio
     cursore. Se la risposta contiene stringhe nella forma `S-...`, sono inventate per definizione
     e la risposta viene rifiutata. Senza questo controllo il modo piu' facile di corrompere il
     ledger sarebbe un modello esterno che "aiuta" numerando le fonti da solo.

  uso:
    export DEEPSEEK_API_KEY=sk-...
    python3 tools/ds_call.py --prompt p.txt --schema schemas/evidence-pack.schema.json \\
                            --out runs/<run_id>/02-evidence/P-101-normativa.json
    opzionali: --provider deepseek (unico oltre ad anthropic) · --model · --temperature
    exit 0 = output valido scritto · 1 = errore di configurazione · 2 = output rifiutato due volte
"""
import json, os, re, sys, urllib.request, urllib.error
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


import os, sys as _s
_s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _chiavi import trova_chiave, diagnosi   # unico posto che sa dove sta una chiave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def provider(nome):
    """Legge il provider da config, non da costanti nel codice: aggiungerne un altro domani e' una
    voce in config.provider piu' la sua variabile d'ambiente, non una modifica a questo file.
    Oggi l'unico valore ammesso oltre ad 'anthropic' e' 'deepseek'."""
    cfg = json.load(open(os.path.join(ROOT, "config", "deepsearch.config.json"), encoding="utf-8"))
    p = cfg.get("provider") or {}
    if nome not in (p.get("disponibili") or []):
        errore(f"provider '{nome}' non dichiarato in config.provider.disponibili "
               f"({', '.join(p.get('disponibili') or [])})")
    b = p.get(nome) or {}
    if not b.get("env_chiave"):
        errore(f"config.provider.{nome} non dichiara env_chiave")
    base = os.environ.get(b.get("env_base_url") or "", b.get("base_url_default", ""))
    return b["env_chiave"], base.rstrip("/") + "/chat/completions", b.get("modello_default")
RX_SRC = re.compile(r"\bS-[0-9]{6,}")
RX_URL = re.compile(r"https?://[^\s\"'<>)\]}]+", re.I)


def _dominio(u):
    """Confronto per (host + percorso), senza schema, www, query, ancora e slash finale: un
    modello che restituisce la stessa pagina in forma leggermente diversa non va bocciato."""
    u = re.sub(r"^https?://(www\.)?", "", u.strip().rstrip("/").lower())
    return u.split("?")[0].split("#")[0]

ISTRUZIONI = """Sei un ESTRATTORE. Non cerchi e non navighi: non ne hai gli strumenti. Ricevi
pagine gia' recuperate da qualcun altro e le trasformi in JSON conforme allo schema fornito.

Non esprimi giudizi, non valuti opportunita', non concludi. Estrai fatti dal testo che ti e' stato
dato, e basta.

Regole non negoziabili:
- USA SOLO GLI URL CHE COMPAIONO NEL PROMPT. Non scriverne altri, per nessun motivo. Un URL che
  ricordi e' un URL che non esiste, e la risposta viene rifiutata per intero.
- Ogni numero che marchi VERIFIED deve venire dal testo fornito, con l'URL della pagina da cui
  l'hai preso. Se nel testo non c'e', lo stato e' UNKNOWN. Non stimare, non completare.
- Non inventare identificatori di fonte. Non scrivere mai stringhe che iniziano con "S-".
- Se un dato non c'e' nelle pagine fornite, il campo vale UNKNOWN. UNKNOWN e' una risposta
  corretta, ed e' quasi sempre preferibile a una plausibilita'.
- Rispondi SOLO con l'oggetto JSON, senza testo prima o dopo.
"""


def errore(msg, codice=1):
    print(f"  ds_call: {msg}", file=sys.stderr)
    sys.exit(codice)


def chiama(prompt, schema_txt, modello, temperatura, env_chiave, endpoint):
    chiave, _dove = trova_chiave(env_chiave)
    if not chiave:
        errore(f"{env_chiave} non trovata.\n\n{diagnosi(env_chiave)}\n\n"
               f"  Impostala con: python3 tools/setup.py --chiave\n"
               f"  Non tocco nessun'altra variabile d'ambiente.")
    corpo = json.dumps({
        "model": modello,
        "messages": [
            {"role": "system", "content": ISTRUZIONI + "\n\nSCHEMA:\n" + schema_txt},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperatura,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=corpo, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {chiave}",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        errore(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:
        errore(f"chiamata fallita: {type(e).__name__}: {e}")
    uso = d.get("usage") or {}
    print(f"  ds_call: {modello} · in {uso.get('prompt_tokens', '?')} · "
          f"out {uso.get('completion_tokens', '?')} token")
    return d["choices"][0]["message"]["content"], uso


def valida(testo, schema, testo_prompt=""):
    """Rifiuti meccanici. Ritorna (oggetto, None) oppure (None, motivo)."""
    testo = re.sub(r"^```(?:json)?|```$", "", testo.strip(), flags=re.M).strip()
    try:
        o = json.loads(testo)
    except Exception:
        # Un modello che premette "Ecco il risultato:" non sta sbagliando il JSON, sta parlando.
        # Rifiutarlo per "non e' JSON valido" manda il rilancio a correggere la cosa sbagliata:
        # si estrae l'oggetto e si giudica QUELLO, che e' il contenuto su cui il verdetto conta.
        i, j = testo.find("{"), testo.rfind("}")
        if i < 0 or j <= i:
            return None, "la risposta non contiene alcun oggetto JSON"
        try:
            o = json.loads(testo[i:j + 1])
        except Exception as e:
            return None, f"non e' JSON valido: {e}"

    # 3. ID di fonte inventati — si controlla per primo perche' e' il piu' insidioso
    inventati = sorted(set(RX_SRC.findall(json.dumps(o, ensure_ascii=False))))
    if inventati:
        return None, ("contiene identificatori di fonte inventati (li assegna l'orchestratore, "
                      f"non il raccoglitore): {', '.join(inventati[:5])}")

    # 4. NESSUN URL CHE NON FOSSE GIA' NELL'INPUT — il rifiuto che mancava, ed e' il piu'
    #    importante di tutti.
    #
    #    Il payload che questo file costruisce NON ha il campo `tools`: l'endpoint chat di DeepSeek
    #    non naviga e non apre pagine. Quindi un agente di retrieval delegato cosi' com'era non
    #    cercava — ricordava, e produceva URL dalla memoria parametrica. Gli altri tre rifiuti non
    #    lo intercettavano: RX_SRC blocca gli ID di ledger inventati (`S-...`), non gli URL, e un
    #    claim VERIFIED con una fonte dall'URL fabbricato supera il controllo "numero senza fonte"
    #    perche' la fonte c'e' — semplicemente non esiste.
    #
    #    La difesa non e' istruire il modello a non inventare: e' rendere strutturalmente
    #    impossibile passare senza che qualcuno gli abbia dato fonti vere. Da qui discende
    #    l'architettura corretta, senza bisogno di scriverla come disciplina: l'orchestratore
    #    cerca e apre le pagine con i propri strumenti, mette il testo recuperato e gli URL nel
    #    file di prompt, e DeepSeek ESTRAE e STRUTTURA invece di ricordare. E' anche il lavoro in
    #    cui un modello economico rende meglio.
    ammessi = {_dominio(u) for u in RX_URL.findall(testo_prompt or "")}
    usati = {u: _dominio(u) for u in RX_URL.findall(json.dumps(o, ensure_ascii=False))}
    fabbricati = sorted({u for u, d in usati.items() if d not in ammessi})
    if fabbricati:
        if not ammessi:
            return None, ("la risposta contiene URL ma il prompt non ne conteneva nessuno: questo "
                          "modello non naviga, quindi ogni URL qui e' ricordato, non trovato. "
                          "L'orchestratore deve mettere nel prompt le pagine gia' recuperate.")
        return None, (f"{len(fabbricati)} URL non presenti nel prompt (il modello non naviga: "
                      f"sono ricordati, non trovati): {', '.join(fabbricati[:3])}")

    # 2. schema: campi obbligatori del payload, senza dipendenze esterne
    pay_schema = ((schema.get("properties") or {}).get("payload") or {})
    payload = o.get("payload") if isinstance(o.get("payload"), dict) else o
    mancanti = [c for c in (pay_schema.get("required") or []) if c not in payload]
    if mancanti:
        return None, f"campi obbligatori del payload mancanti: {', '.join(mancanti)}"

    # 1. numeri senza fonte
    senza = []

    def scendi(n, percorso="payload"):
        if isinstance(n, dict):
            if n.get("state") == "VERIFIED" and not n.get("source_ids") \
                    and not n.get("source_id") and not n.get("supporting_claim_ids"):
                senza.append(percorso)
            for k, v in n.items():
                scendi(v, f"{percorso}.{k}")
        elif isinstance(n, list):
            for i, v in enumerate(n):
                scendi(v, f"{percorso}[{i}]")

    scendi(payload)
    if senza:
        return None, ("VERIFIED senza fonte in " + ", ".join(senza[:5]) +
                      f"{' e altri ' + str(len(senza)-5) if len(senza) > 5 else ''}"
                      " — un numero senza origine non entra nella pipeline, e non lo si stima")
    return o, None


def main():
    a = sys.argv[1:]

    def arg(nome, default=None):
        return a[a.index(nome) + 1] if nome in a and a.index(nome) + 1 < len(a) else default

    p_prompt, p_schema, p_out = arg("--prompt"), arg("--schema"), arg("--out")
    nome_provider = arg("--provider", "deepseek")
    env_chiave, endpoint, modello_default = provider(nome_provider)
    modello = arg("--model", modello_default)
    temperatura = float(arg("--temperature", "0"))
    if not (p_prompt and p_schema and p_out):
        errore("servono --prompt, --schema, --out")
    for p in (p_prompt, p_schema):
        if not os.path.exists(p):
            errore(f"file non trovato: {p}")

    prompt = open(p_prompt, encoding="utf-8").read()
    schema_txt = open(p_schema, encoding="utf-8").read()
    schema = json.loads(schema_txt)

    motivo = None
    for tentativo in (1, 2):
        testo, _ = chiama(prompt if tentativo == 1 else
                          prompt + f"\n\nIL TUO OUTPUT PRECEDENTE E' STATO RIFIUTATO: {motivo}\n"
                                   "Correggi ESATTAMENTE questo e non altro.",
                          schema_txt, modello, temperatura, env_chiave, endpoint)
        o, motivo = valida(testo, schema, prompt)
        if o is not None:
            os.makedirs(os.path.dirname(os.path.abspath(p_out)), exist_ok=True)
            json.dump(o, open(p_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"  ds_call: output valido scritto in {p_out}"
                  + (f" (al tentativo {tentativo})" if tentativo > 1 else ""))
            return 0
        print(f"  ds_call: tentativo {tentativo} RIFIUTATO — {motivo}", file=sys.stderr)

    print("  ds_call: rifiutato due volte. L'orchestratore deve parcheggiare l'idea, MAI\n"
          "           completare a mano il campo mancante: e' il difetto che questa\n"
          "           pipeline esiste per impedire.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
