# DEEPSEARCH v2 — pipeline multi-agent per opportunita' B2B bootstrap

**Su Windows l'interprete si chiama `python`, non `python3`.** Prova nell'ordine `python`, poi
`py -3`: il primo che risponde e' quello, e usalo per tutti gli strumenti di `tools/`. Non serve
altro — non ci sono dipendenze da installare.

Riprogettazione del prompt monolitico `DEEPSEARCH` in una pipeline orchestrata di 10 subagent
specializzati, con stage gate, contratti dati verificabili e memoria persistente fra i run.

Tutti i principi del prompt originale sono conservati — qualita' sopra quantita', follow the money,
verificabilita' delle fonti, soglie competitor, anti-allucinazione, red teaming, bootstrap first —
ridistribuiti fra agenti con responsabilita' separate e resi meccanicamente verificabili.

---

## Installazione

Copia il contenuto di questa cartella dentro `C:\Users\gioff\Desktop\CLAUDE CODE\DEEPSEARCH`,
mantenendo la struttura:

```
DEEPSEARCH/
├── CLAUDE.md                       ← regole globali, lette da tutti gli agenti
├── README.md
├── config/
│   └── deepsearch.config.json      ← UNICO punto in cui si toccano soglie e parametri
├── .claude/
│   ├── agents/                     ← 10 subagent
│   │   ├── pain-finder.md
│   │   ├── evidence-researcher.md
│   │   ├── competitor-analyst.md
│   │   ├── trigger-finder.md
│   │   ├── tam-analyst.md
│   │   ├── bootstrap-filter.md
│   │   ├── venture-architect.md
│   │   ├── red-team.md
│   │   ├── source-verifier.md
│   │   └── ranking-agent.md
│   └── commands/
│       └── deepsearch.md           ← ORCHESTRATOR (comando /deepsearch)
├── schemas/                        ← contratti JSON di input/output
├── docs/
│   ├── ARCHITETTURA.md             ← architettura + diagrammi
│   ├── STAGE-GATES.md              ← regole di passaggio fra stage
│   ├── DATA-CONTRACT.md            ← variabili condivise e formati
│   ├── ANTI-HALLUCINATION.md       ← le 9 difese
│   ├── PARALLELIZZAZIONE.md        ← strategie di fan-out e deduplica
│   └── MIGLIORIE.md                ← cosa e' cambiato e perche'
├── state/                          ← memoria persistente fra run (non svuotare; vedi state/README.md)
└── runs/                           ← un sottodirectory per run, con tutti i JSON e il REPORT.md
```

Verifica che i subagent siano caricati con `/agents` in Claude Code. Se non compaiono, controlla che
il frontmatter YAML di ogni file `.claude/agents/*.md` sia intatto (delimitatori `---`).

## Uso

```
/deepsearch
/deepsearch --profile BALANCED
/deepsearch logistica,edilizia --profile DEEP --thresholds STRICT
```

Senza argomenti usa i default di `config/deepsearch.config.json`: profilo `DEEP`, soglie
`CALIBRATED`, 6 settori scelti per rotazione fra i meno esplorati di recente.

Output: `runs/<YYYYMMDD>/REPORT.md`, piu' tutti i JSON intermedi per la tracciabilita'.

## Migrazione del task del venerdi'

Il task pianificato attualmente contiene l'intero prompt monolitico. Sostituiscilo con:

```
Esegui la pipeline DEEPSEARCH come definita in questa cartella.
Leggi CLAUDE.md e config/deepsearch.config.json, poi segui .claude/commands/deepsearch.md
dall'inizio alla fine. Profilo DEEP, soglie CALIBRATED.
Sei in esecuzione non presidiata: non fare domande, applica i default di config e dichiara
ogni assunzione nella sezione "Note di esecuzione" del report.
Al termine consegna runs/<run_id>/REPORT.md.
```

Il resto — settori, soglie, budget, funnel — vive in config e si cambia senza toccare il task.

## Le tre manopole che userai davvero

**`run.profile`** — `LIGHT` / `BALANCED` / `DEEP`. Governa shard, ampiezza del funnel e tetto di
ricerche. Nel caso peggiore `DEEP` = ~200 chiamate ad agente e fino a 1200 ricerche; il consumo
reale e' tipicamente il 40-60%, perche' i gate potano il funnel presto. Il dettaglio del calcolo e'
in `config.budget._calcolo_teorico_*`.

**`run.threshold_profile`** — `CALIBRATED` (default) o `STRICT`. `STRICT` applica alla lettera le
soglie competitor del prompt originale: massima fedelta', alta probabilita' di output vuoto.
Le differenze sono documentate una per una in `docs/MIGLIORIE.md`, sezione B.

**`scope.sectors_rotation`** — i settori fra cui ruotare. Aggiungi o togli voci liberamente: la
rotazione `least_recently_explored` si adatta da sola.

## Come leggere un report

Guarda **prima** le Note di esecuzione, in fondo. Un run con molti downgrade meccanici e un
`pass_rate` del verificatore basso e' inaffidabile **anche se ha prodotto quattro idee**. Le
metriche di salute contano piu' del numero di idee.

Poi la Sintesi degli scarti: se cadono quasi tutte allo stesso gate, il problema e' la
calibrazione di quel gate, non il mercato.

Un report che dice **"nessuna opportunita' ha superato i criteri"** e' un run riuscito, non un run
fallito. E' scritto negli invarianti (`CLAUDE.md`, sezione 0) proprio perche' la tentazione opposta
e' forte.

## Estendere la pipeline

**Aggiungere un agente:** crea `.claude/agents/<nome>.md` con missione, input, output, criteri di
successo e fallimento, condizioni di STOP; crea `schemas/<nome>.schema.json` che estenda
`core.schema.json#/$defs/Envelope`; aggiungi il gate corrispondente in `docs/STAGE-GATES.md` e la
wave nell'orchestratore.

**Cambiare una soglia:** solo in `config/deepsearch.config.json`. Nessun agente deve avere soglie
hardcoded — se ne trovi una in un prompt, e' un bug.

**Aggiungere una geografia:** `scope.geo_secondary` piu' le fonti statistiche nazionali
corrispondenti nei tier T1/T2 di `sources.tiers`.

## Portabilita'

I file `.claude/agents/*.md` sono prompt di sistema autosufficienti: funzionano come system prompt
di sessioni separate su Gemini CLI, Codex o via API. Gli schemi sono JSON Schema standard, usabili
direttamente come structured output. I gate sono condizioni booleane su campi, quindi si traducono
in codice senza ambiguita'. Vedi `docs/ARCHITETTURA.md`, sezione 9.
