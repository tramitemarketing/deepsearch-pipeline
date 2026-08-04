# Contratto dati — variabili condivise e formati di scambio

> **DOCUMENTO DERIVATO — non e' la specifica.** Le forme vincolanti vivono in `schemas/*.json` e in
> `config/deepsearch.config.json`. In caso di divergenza vince lo schema. Se trovi una differenza fra
> questo file e uno schema, e' un difetto di questo file.
> Fino alla v2.7 questo documento NON era marcato come derivato, ed e' la ragione per cui la
> divergenza sul pattern di `pain_id` (N1) e' potuta restare aperta: chi lo leggeva lo prendeva per
> la fonte di verita'. `tools/spec_check.py` verifica la presenza di questo riquadro.

## 1. Variabili di run (immutabili, lette da tutti)

| Variabile | Origine | Esempio | Chi la usa |
|---|---|---|---|
| `run_id` | `YYYYMMDD` oppure `YYYYMMDD-HHMM` | `20260807`, `20260807-0320` | tutti (prefisso degli ID) |
| `profile` | argomenti o config | `DEEP` | orchestratore |
| `threshold_profile` | argomenti o config | `CALIBRATED` | competitor-analyst |
| `geo_primary` / `geo_secondary` | config | `["IT"]` | pain-finder, evidence, tam |
| `icp_constraints` | config `scope.company_size_target` | 5-250 addetti | pain-finder, tam |
| `sectors_selected` | rotazione da `sector-history` | 6 settori | pain-finder |
| `excluded_dedup_keys` | `seen-pains.jsonl` + cooldown | array | pain-finder |
| `thresholds` | config, profilo attivo | oggetto | competitor-analyst |
| `bootstrap_constraints` | config | oggetto | bootstrap-filter, venture-architect |
| `scoring_weights` | config | oggetto | ranking-agent |
| `budget` | config `budget` + `funnel` | oggetto | tutti |

Nessun agente ricalcola queste variabili e nessuno le ricorda dalla propria memoria: **arrivano
sempre dall'input**. E' cio' che rende la config l'unico punto di verita'.

## 2. Convenzioni sugli identificatori

```
S-<run_id>-<nnnn>   fonte      S-20260807-0118, S-20260807-0320-0118
C-<agente>-<nnn>    claim      C-evres3-042
P-<nnn>             pain       P-007  (SEMPRE tre cifre: P-7 non e' valido)
K1..K5             kill criteria competitor
W1..W6             keep criteria competitor
G1..G9             kill criteria globali
Q1..Q5             domande anti-allucinazione
```

Gli intervalli di ID delle fonti sono assegnati per agente dall'orchestratore
(`source_id_range`) per evitare collisioni fra istanze parallele.

## 3. Envelope comune

Ogni output di agente e' un oggetto con questa struttura (vedi `schemas/core.schema.json`):

```json
{
  "agent": "competitor-analyst",
  "run_id": "20260807",
  "pain_id": "P-003",
  "status": "OK | PARTIAL | FAILED",
  "budget_used": { "searches": 9, "limit": 10 },
  "stop_reason": "COMPLETED | BUDGET_EXHAUSTED | INSUFFICIENT_DATA | KILL_TRIGGERED | ABORTED_BY_ORCHESTRATOR | ZERO_YIELD",
  "payload": { },
  "new_sources": [ ],
  "queries_run": [ ],
  "unreachable_sources": [ ],
  "self_check": { "invarianti_violati": [], "note": "" }
}
```

`self_check.invarianti_violati` merita una nota: l'agente **dichiara** gli invarianti che sa di aver
violato. Una dichiarazione onesta costa poco; una violazione scoperta dall'orchestratore fa scartare
l'intero output. L'incentivo e' costruito per premiare la trasparenza.

## 4. I tre oggetti che circolano fra gli stage

### Source

```json
{
  "id": "S-20260807-118",
  "url": "https://...",
  "publisher": "Osservatori Digital Innovation, Politecnico di Milano",
  "title": "...",
  "published_date": "2025-11",
  "accessed_at": "2026-08-07",
  "tier": "T2",
  "access_status": "OK",
  "snippet": "citazione testuale letta, max 300 caratteri",
  "stale": false,
  "months_old": 9,
  "retrieved_by": "evidence-researcher"
}
```

### UnreachableSource

```json
{
  "source_id": "S-20260807-119",
  "what": "listino pubblico del principale fornitore del rimedio",
  "url_or_target": "https://...",
  "why": "PAYWALL",
  "impact": "avrebbe confermato o smentito C-042 sul prezzo medio"
}
```

Due famiglie di `why`, e la differenza decide se `source_id` c'e' o non c'e':

| famiglia | valori | `source_id` | perche' |
|---|---|---|---|
| **tentata e rifiutata** | `PAYWALL` `BLOCKED` `NOT_FOUND` `ROBOTS` `API_LIMIT` `LOGIN_REQUIRED` `NO_PUBLIC_DATA` | **obbligatorio** | qualcuno ha bussato: la fonte esiste, ha un URL, va nel ledger col suo `access_status` |
| **mai tentata** | `BUDGET_EXHAUSTED` `STOP_RULE` | **vietato** | nessuno ha bussato: non c'e' nessun oggetto fonte, e' una lacuna del run |

Fino alla v2.13 questo oggetto non aveva `source_id`. IC-4 obbligava a dichiarare la fonte
irraggiungibile, IC-1 pretendeva che ogni citazione risolvesse nel ledger, e la dichiarazione
imposta dal primo era irricevibile per il secondo: `reconcile_run.py` la contava come orfana e il
run chiudeva **DEGRADATO per aver fatto la cosa giusta**. Il contrappeso, arrivato con la
riparazione: una fonte irraggiungibile e' provenienza valida per l'*esistenza* della fonte e per
nient'altro, e **non puo' sostenere da sola uno stato `VERIFIED`** — IC-3 pretende uno snippet per
la coppia (claim, fonte), e su una pagina che nessuno ha aperto non c'e' niente da citare.

### Claim

```json
{
  "id": "C-042",
  "statement": "affermazione atomica, verificabile, senza aggettivi valutativi",
  "state": "VERIFIED",
  "source_ids": ["S-20260807-118"],
  "promotion_rule_met": "1xT2",
  "estimation_formula": null,
  "stale_warning": false,
  "contradicted_by": []
}
```

### Numeric

```json
{
  "label": "imprese target in IT",
  "value": 12400,
  "unit": "imprese",
  "currency": null,
  "year": 2025,
  "geo": "IT",
  "state": "ESTIMATED",
  "source_ids": [],
  "formula": "(imprese NACE 49.41 in IT, 36.500 [S-501]) x (quota con flotta propria >5 mezzi, 34% [S-503])",
  "input_source_ids": ["S-20260807-501", "S-20260807-503"],
  "display_string": "Stima non verificata: 12.400 imprese — ottenuta da 36.500 x 34%, nessuna fonte diretta per il dato combinato."
}
```

Il campo `display_string` esiste perche' la formattazione di un numero nel report non sia lasciata
all'orchestratore: **e' l'agente che lo ha prodotto a decidere come va scritto**, e per gli
`ESTIMATED` la stringa deve iniziare con `Stima non verificata:`. Cosi' e' impossibile che una
stima venga presentata con la stessa grafica di un dato.

## 5. Matrice input/output

| Agente | Riceve | Produce | Schema |
|---|---|---|---|
| `pain-finder` | shard (settore, angolo), ICP, dedup esclusi | pain candidati + scarti | `pain.schema.json` |
| `evidence-researcher` | pain, canale, evidence_seeds | claim, spend_evidence, linguaggio, contro-evidenze, Q4 | `evidence-pack.schema.json` |
| `competitor-analyst` | pain, claim, soglie del profilo | competitor classificati criterio per criterio, verdetto, Q2 | `competitor-report.schema.json` |
| `trigger-finder` | pain, claim, geo | trigger con data/perimetro/conseguenza, contro-evidenze, verdetto, Q1 | `trigger-report.schema.json` |
| `tam-analyst` | pain, claim, ICP | popolazione, prezzo, TAM/SAM/SOM, sensibilita', verdetto | `market-report.schema.json` |
| `bootstrap-filter` | dossier completo, vincoli | 13 dimensioni, unit economics, verdetto, Q3, supplementi | `bootstrap-verdict.schema.json` |
| `venture-architect` | dossier + tutti i verdetti PASS | MVP, pricing, GTM, AI, integrazioni, roadmap, moat | `venture-blueprint.schema.json` |
| `red-team` | dossier + blueprint, lente | finding falsificabili, voto, Q5 | `redteam-report.schema.json` |
| `source-verifier` | solo coppie (claim, fonte) | esiti di verifica, pass rate, fonti fabbricate | `verification-report.schema.json` |
| `ranking-agent` | dossier + blueprint + red team + verifica | 7 dimensioni, confidenza, punteggio, pubblicabilita' | `scorecard.schema.json` |

Il `source-verifier` e' l'unico che **non** riceve il dossier. E' deliberato: la cecita' e' la
condizione della sua utilita'.

## 6. File di stato persistenti

**`state/seen-pains.jsonl`** — un record per pain toccato da ogni run.

```json
{"dedup_key":"logistica|10-50|riconciliazione ddt fatture","pain_id":"P-007","run_id":"20260807",
 "outcome":"PARKED","stage":"competitor","reason_code":"unclassified_ratio",
 "missing_to_decide":["fatturato linea prodotto di VendorX","presenza API in VendorY"],
 "timestamp":"2026-08-07T03:41:00Z","retry_after":"2026-08-21"}
```

**`state/query-ledger.jsonl`** — ogni ricerca eseguita, con query normalizzata, angolo, resa e
fonti estratte. Serve a deduplicare, a rendere il run auditabile e a contare le ricerche
**realmente distinte** (per angolo, non per riformulazione).

**`state/source-ledger.jsonl`** — tutte le fonti, riusabili fra run. Una fonte gia' verificata in un
run precedente non va riverificata: riduce il costo del `source-verifier` nel tempo.

**`state/sector-history.jsonl`** — settori esplorati per run, con pain prodotti e pubblicati.
Alimenta la rotazione `least_recently_explored`.

## 7. Regole di serializzazione

- Tutti i JSON in UTF-8, chiavi in inglese, valori in italiano.
- Le date in ISO 8601. `UNKNOWN` come stringa quando la data non e' determinabile — mai `null`,
  mai una data inventata.
- I numeri come `number`, mai come stringhe formattate. La formattazione vive in `display_string`.
- Nessun campo omesso: se un valore non c'e', il campo esiste con `null` o `"UNKNOWN"` esplicito.
  Un campo assente e' ambiguo fra "non applicabile" e "dimenticato"; un campo esplicito no.
