# Regole di passaggio fra gli stage

> **DOCUMENTO DERIVATO — non e' la specifica.** Questo file spiega e riassume: le regole vincolanti vivono in `config/deepsearch.config.json` e in `.claude/commands/deepsearch.md`.
> In caso di divergenza vince la config, poi l'orchestratore. Se trovi una differenza fra questo file e
> quelli, e' un difetto di questo file.

Un gate e' una funzione da (output di uno stage) a `PASS | KILL | PARK`. Le condizioni sono
**deterministiche**: nessun gate dipende da un giudizio soggettivo, tutti si applicano leggendo
campi di JSON validati. Solo l'orchestratore esegue i gate.

## Tabella completa

| # | Gate | Input | KILL se | PARK se | PASS se | Chi produce l'evidenza |
|---|---|---|---|---|---|---|
| 1 | **Pain** | `pain.schema.json` × 8 shard | `self_kill_flags` contiene G1 (nessuna spesa attuale) o G2 (problema occasionale o una tantum, senza cadenza prevedibile); oppure `dedup_key` in cooldown senza `revival_reason` | — | il pain ha statement, ICP, ricorrenza, meccanismo di perdita e almeno una `evidence_seed` concreta | `pain-finder` |
| 2 | **Evidence** | 5 × `evidence-pack.schema.json` | **G1** nessuna `spend_evidence` **e** nessuna traccia di COMPORTAMENTO su alcun canale · **G3** `language_of_the_problem` vuoto su tutti i canali | meno di 2 claim VERIFIED **e** meno di 2 tracce di comportamento | **PASS-A** ≥2 claim VERIFIED **e** ≥1 `spend_evidence` (route PUBBLICAZIONE) · **PASS-B** ≥2 tracce di COMPORTAMENTO convergenti da attori indipendenti, anche con zero claim VERIFIED (route COMPORTAMENTO) · **uscita VALIDAZIONE-SUL-CAMPO** se la via documentale e' esaurita (nessun canale in `BUDGET_EXHAUSTED`) e ≥3 tracce da ≥2 classi di attore: esce qui, senza i gate 3..8 | `evidence-researcher` ×5 |
| 3 | **Competitor** | `competitor-report.schema.json` | DIRETTO `KILL` con `kill_severity: HARD` · DIRETTO `KILL` `SOFT` confermato da un secondo criterio · **G7** tutti `STRONG` **e nessun disallineamento** (≥2 criteri M) | DIRETTO `KILL` `SOFT` non confermato · `unclassified_ratio` > 0.40 · nessun DIRETTO e nessun SOSTITUTO | ≥1 DIRETTO `BEATABLE` con ≥`keep_if_at_least` criteri verificati · oppure tutti `STRONG` **ma** `misalignment.disallineamento_dichiarato: true` con ≥`misalignment_min` criteri M verificati (route DISALLINEAMENTO): questo ramo **precede** G7 | `competitor-analyst` |
| 4 | **Trigger** | `trigger-report.schema.json` | **G8** nessun trigger · tutti disinnescati da `counter_evidence` · **G4** unico driver = moda passeggera | solo trigger `ESTIMATED` / privi di `forcing_function` / perimetro indeterminato · trigger `VERIFIED` completo ma `buy_within_30_days: false` | ≥1 trigger `VERIFIED` con `buy_within_30_days: true`, data, perimetro e conseguenza | `trigger-finder` |
| 5 | **TAM** | `market-report.schema.json` | **G9** `som.value` < 600.000 EUR/anno a 24 mesi | `population` = `UNKNOWN` dopo il tentativo · oppure `price_point` non ancorabile | SOM ≥ soglia con formula ricalcolabile, `arpa_annual` calcolato e ≥2 scenari di sensibilita' | `tam-analyst` |
| 6 | **Bootstrap** | `bootstrap-verdict.schema.json` | dimensione `FAIL` fra `constraints.blocking_dimensions` (include **G5** capitali esterni) | `UNKNOWN` su una dimensione bloccante · >3 `UNKNOWN` totali | 13 dimensioni valutate, nessun FAIL bloccante, ≤3 UNKNOWN. `cac_to_arpa_ratio` e `sales_cycle_days` sono `non_blocking_when_unknown` | `bootstrap-filter` (`pass: 1`) |
| 6b | **Unit economics** | `bootstrap-verdict.schema.json` + `venture-blueprint.schema.json` | **G6** CAC/ARPA > 3.0 · ciclo di vendita > 90 giorni | CAC ancora `UNKNOWN` con i canali disponibili | CAC/ARPA entro soglia | `bootstrap-filter` (`pass: 2`) |
| 7 | **Red Team** | 3 × `redteam-report.schema.json` | ≥2 voti `KILL` su 3 · **oppure** un solo finding `FATALE` + `VERIFIED` + `would_kill_if_true` | — (il red team non parcheggia: o uccide o lascia passare) | altrimenti; i finding non fatali restano e finiscono nel report | `red-team` ×3 |
| 7b | **Integrita' fonti** | `verification-report.schema.json` | dopo l'invalidazione dei claim dell'agente responsabile, un gate a monte non regge piu' | `pass_rate` < 0.6 con campione ≥5 | `fabricated_sources` vuoto e gate a monte intatti | `source-verifier` |
| 8 | **Ranking** | `scorecard.schema.json` | **somma pesata** < 5.0 · KILL del red team | somma pesata ≥ 6.5 ma confidenza < 0.70 o fontate < 4/5 (→ VALIDAZIONE-SUL-CAMPO se `evidence_route` e' COMPORTAMENTO o DISALLINEAMENTO) | somma pesata ≥ 6.5 **e** confidenza ≥ 0.70 **e** ≥4/5 fontate **e** nessun KILL. Fra 5.0 e 6.5 → menzione onesta | `ranking-agent` |

## Ordine e parallelismo

I gate si valutano **sempre** in ordine 1 → 2 → 3 → 4 → 5 → 6 → 6b → 7 → 7b → 8, anche quando gli
agenti che li alimentano girano in parallelo. La regola:

> **Parallelismo di esecuzione, sequenzialita' di valutazione.**

Se il gate 3 uccide, gli output di trigger e TAM vengono archiviati su disco ma **non valutati**.
Servono comunque: alimentano il registro degli scarti e possono essere riusati se il pain viene
riesumato in un run futuro.

## Semantica dei tre esiti

**KILL** — c'e' una ragione documentata per escludere. Il pain esce dal run e va in cooldown per 90
giorni. Rientra prima solo se una fonte documenta un cambiamento che invalida il motivo dello scarto
(tipicamente: nuova normativa che crea il trigger che mancava, o un competitor che chiude).

**PARK** — non ci sono abbastanza informazioni per decidere. **Non e' un fallimento dell'idea, e'
un fallimento della raccolta.** Il pain esce dal run con la lista puntuale di `missing_to_decide` e
rientra dopo 14 giorni **allo stage in cui si era fermato**, non dall'inizio.

Questo stato e' l'aggiunta piu' importante rispetto al prompt monolitico, che aveva solo due esiti e
quindi, davanti a dati insufficienti, doveva scegliere fra assumere "battibile" (allucinazione) e
buttare (perdita di segnale). Nella pratica sceglieva quasi sempre il primo.

**PASS** — l'idea prosegue. Nessun PASS e' definitivo: gate successivi possono ancora ucciderla, e
il gate 7b puo' invalidarne retroattivamente le basi.

## Precedenza fra gli esiti dentro un gate

Dentro ogni gate le condizioni si valutano **in ordine** e la prima che si attiva decide. L'ordine e'
sempre **KILL → PARK → PASS**. Serve a evitare esiti ambigui quando piu' condizioni si attivano
insieme: un trigger `VERIFIED` con `buy_within_30_days: true` che pero' e' disinnescato da
`counter_evidence` viene ucciso, non promosso.

Ogni gate copre esaustivamente il proprio spazio di input: non esistono combinazioni per cui nessuna
condizione si applica. Il ramo che chiude lo spazio e' esplicitato in ogni gate (per il gate 3 e' il
ramo `G7`, per il gate 4 e' il ramo `PARK` sulla scadenza lontana).

## Supplemento istruttorio

Solo il gate 6 puo' chiedere un supplemento. Quando `bootstrap-filter` produce
`supplement_requests` con `blocks_verdict: true`, l'orchestratore lancia una singola istanza
dell'agente retrieval indicato, con budget ridotto (3 ricerche) e mandato circoscritto alla sola
domanda mancante, e poi rilancia il gate.

**Massimo `config.parallelism.max_supplement_rounds` round (default 1).** Esaurito il round senza
aver ottenuto il dato, l'idea va in `PARK` d'ufficio. Senza questo limite, un dato non reperibile
farebbe ciclare `bootstrap-filter ↔ agente retrieval` fino a consumare l'intero budget del run su
una sola idea.

E' l'unica eccezione al flusso in avanti, ed e' consentita solo qui perche' il bootstrap-filter e'
l'unico agente judgment che puo' bloccarsi su un dato fattuale semplice (per esempio: un competitor
espone API pubbliche?) che nessuno ha ancora cercato.

## Contatore di caduta

L'orchestratore mantiene per ogni run il conteggio delle cadute per codice:

```
G1 spesa attuale non documentata ..........  n
G2 problema non ricorrente ................  n
G3 richiede educazione del mercato ........  n
G4 moda passeggera senza driver ...........  n
G5 richiede capitali esterni ..............  n
G6 CAC eccessivo o vendita enterprise .....  n
G7 gia' risolto da competitor sani ........  n
G8 nessun trigger .........................  n
G9 SOM sotto soglia .......................  n
K1..K5 competitor sopra soglia ............  n
bootstrap FAIL (altre dimensioni) .........  n
red team KILL .............................  n
integrita' fonti ..........................  n
PARKED (per stage) ........................  n
```

Tutti e nove i codici `G` sono applicati da un gate: nessuno resta dichiarato in config e mai usato.

E' questo contatore, non un'impressione, che genera la **Sintesi degli scarti** (massimo 5 righe)
richiesta in apertura del report.
