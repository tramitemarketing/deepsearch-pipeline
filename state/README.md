# Lo schema della memoria persistente fra i run

**I file di memoria non stanno piu' in questa cartella.** Vivono in `runtime/state/`, fuori dal
repository e fuori da ogni cartella sincronizzata:

```
runtime/state/seen-pains.jsonl
runtime/state/sector-history.jsonl
runtime/state/shadow-portfolio.jsonl
runtime/state/query-ledger.jsonl
runtime/state/source-ledger.jsonl
runtime/state/state-import-manifest.json
```

Il percorso lo calcola `radice_stato()` in `tools/reconcile_run.py`, ed e' l'unico posto in cui e'
deciso: `run_close.py` lo importa da li' invece di ricostruirlo. **Non si legge mai dalla
configurazione** — le cinque chiavi che finiscono in `_file`, dentro il blocco `memory` di
`config/deepsearch.config.json`, nominano ancora la vecchia collocazione: non hanno lettori, non
governano nulla, e non vanno usate come sorgente della radice.

I tre file omonimi che restano tracciati sotto `state/` sono **vuoti e vestigiali**: nessuno li
scrive piu'. Non vanno cancellati ne' riempiti.

**Non svuotare `runtime/state/`.** E' cio' che impedisce alla pipeline di riscoprire ogni venerdi'
le stesse idee e di rifare le stesse ricerche. E non e' versionato: se lo perdi, non c'e' un `git
checkout` che lo riporti indietro.

**Ogni riga scritta da un run porta `run_id`.** Vale per tutti i file di memoria, non solo per i
due ledger: e' cio' che permette di distinguere il lavoro di questo run da quello dei precedenti, e
`run_close.py` lo verifica su ogni riga scritta dopo il prefisso importato. Le righe piu' vecchie
dell'attribuzione — quelle importate dalla migrazione, dichiarate dal manifest — restano conservate
e non vengono mai attribuite a nessun run.

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

**`run_id` e' obbligatorio su ogni riga.** Questo file non ha uno schema JSON — e' l'unico dei
quattro documentati qui a non averlo — quindi la regola sta scritta qui e non altrove, ed e' qui
che `run_close.py` la trova quando qualcuno chiede da dove venga. Una riga senza `run_id` e' un
esito **grave**: rende il contributo di questo run indistinguibile da quello dei precedenti, ed e'
esattamente cio' che la rotazione dei settori deve poter distinguere.

```json
{"run_id":"20260807","sector":"logistica e trasporti conto terzi",
 "pains_generated":11,"pains_published":1,"timestamp":"2026-08-07T04:02:00Z"}
```

## `shadow-portfolio.jsonl`

Le idee arrivate almeno al gate 3 e poi uccise, per poterle rivedere quando scade
`ricontrollare_dopo`.

⚠ **Non ha alcun contratto**: nessuno schema, nessun esempio normativo, nessuno strumento che lo
legga. E' l'unico file di memoria che nessun controllo puo' verificare nel merito, ed e' registrato
come lacuna nota — non come scelta di disegno. I controlli sull'attribuzione per run **non** si
applicano a questo file, perche' non c'e' niente su cui applicarli.

## Manutenzione

Se vuoi ripartire da zero su un settore (per esempio dopo un cambio normativo importante), rimuovi
le sole righe di `runtime/state/seen-pains.jsonl` con quel settore nel `dedup_key`. Svuotare
l'intero file fa perdere anche le idee parcheggiate, che sono la parte piu' preziosa della memoria.

⚠ **Non toccare il prefisso importato.** Le prime righe dei due ledger provengono dalla migrazione
e sono congelate per `sha256` in `state-import-manifest.json`: alterarle, anche di un byte, fa
fallire `run_close.py` con un esito grave. E' voluto — quel prefisso e' l'unica ancora che questa
memoria abbia, visto che non e' versionata.
