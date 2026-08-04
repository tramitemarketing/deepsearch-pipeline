# state/ — memoria persistente fra i run

**Non svuotare questa cartella.** E' cio' che impedisce alla pipeline di riscoprire ogni venerdi' le
stesse idee e di rifare le stesse ricerche.

I file `.jsonl` sono **JSONL puri**: una riga = un oggetto JSON, nessuna riga di commento, nessun
header. Gli schemi sono documentati qui e non dentro i file, perche' una riga di commento in un
JSONL lo rende illeggibile a chi lo parsa riga per riga.

## `seen-pains.jsonl`

Un record per pain toccato da ogni run. Governa cooldown, riesumazioni e riprese dal parcheggio.

**Il contratto e' `schemas/seen-pain.schema.json`, non questo esempio.** Fino alla v2.15 questo
record non aveva uno schema — l'unico artefatto del sistema privo di contratto era proprio quello che
deve sopravvivere al run — e l'esempio qui sotto era **sbagliato in tre punti su tre campi**: settore
non copiato da `sectors_rotation`, fascia `10-50` che non esiste (le tre sono `5-9`, `10-49`,
`50-249`), `run_id` senza l'ora. Un esempio non e' un contratto, e questo lo dimostra.

```json
{"dedup_key":"logistica e trasporti conto terzi|10-49|riconciliare ddt fatture",
 "pain_id":"P-007","run_id":"20260807-0341","outcome":"PARKED","stage":"competitor",
 "reason_code":"unclassified_ratio",
 "profondita_raggiunta":3,
 "kill_analysis":{"kill_basis":"ASSENZA","kill_attribution":"STRUMENTO","ramo_attivato":4,
                  "stop_reason_osservato":"BUDGET_EXHAUSTED","kill_emettibile":true},
 "missing_to_decide":["fatturato linea prodotto di VendorX","presenza API in VendorY"],
 "park_count":1,"park_reason_history":["unclassified_ratio"],
 "timestamp":"2026-08-07T03:41:00Z","parked_at":"2026-08-07","retry_after":"2026-08-21"}
```

`outcome`: `PUBBLICATA` | `VALIDAZIONE-SUL-CAMPO` | `MENZIONE-ONESTA` | `PARKED` | `KILLED` |
`NOT_SELECTED` — esattamente `config.memory.pain_outcomes`. **Questo file documentava `PUBLISHED`**,
un valore che nessun vocabolario del sistema ammette: `MIGLIORIE.md` v2.8.1 dichiarava il difetto
chiuso, ed e' rimasto vivo qui per sei versioni come avviso `SC-05` fra dieci.

Cooldown (da `config.memory`): 90 giorni per `KILLED`, 14 per `PARKED`, 365 per `PUBBLICATA`.
`NOT_SELECTED` non ha cooldown: sono pain validi che non sono entrati nel taglio del funnel e
possono tornare subito.

`park_count` e' il freno alle resurrezioni: al **secondo** PARK sulla stessa `missing_to_decide` il
pain esce dal funnel di scrivania. Senza, il ciclo `parcheggio -> 14 giorni -> stessa ricerca ->
stesso parcheggio` gira all'infinito e produce un report perfettamente normale ogni due settimane.

## `query-ledger.jsonl`

Ogni ricerca eseguita. Serve a deduplicare, a rendere il run auditabile e a contare le ricerche
**realmente distinte per angolo**.

```json
{"run_id":"20260807","normalized_query":"49.41 ateco imprese numero trasporto",
 "raw_query":"numero imprese ATECO 49.41 Italia","angle":"statistica_ufficiale",
 "agent":"evidence-researcher","timestamp":"2026-08-07T03:12:00Z","n_results":8,
 "source_ids":["S-20260807-1241"],"yield":"USEFUL"}
```

## `source-ledger.jsonl`

Tutte le fonti incontrate, riusabili fra run. Una fonte gia' verificata in un run precedente non va
riverificata: il costo del `source-verifier` cala nel tempo. Schema: `core.schema.json#/$defs/Source`.

## `sector-history.jsonl`

Copertura dei settori, alimenta la rotazione `least_recently_explored`.

```json
{"run_id":"20260807","sector":"logistica e trasporti conto terzi",
 "pains_generated":11,"pains_published":1,"timestamp":"2026-08-07T04:02:00Z"}
```

## Manutenzione

Se vuoi ripartire da zero su un settore (per esempio dopo un cambio normativo importante), rimuovi
le sole righe di `seen-pains.jsonl` con quel settore nel `dedup_key`. Svuotare l'intero file fa
perdere anche le idee parcheggiate, che sono la parte piu' preziosa della memoria.
