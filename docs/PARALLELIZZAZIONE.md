# Parallelizzazione delle ricerche

> **DOCUMENTO DERIVATO — non e' la specifica.** Questo file spiega e riassume: descrive intenzioni di progetto, non comportamento garantito.
> In caso di divergenza vince la config, poi l'orchestratore. Se trovi una differenza fra questo file e
> quelli, e' un difetto di questo file.

## I tre motivi per parallelizzare (che non sono lo stesso motivo)

**Diversita'.** Piu' istanze cieche fra loro producono candidati che un unico contesto non avrebbe
generato. Il parallelismo qui non serve alla velocita': serve a rompere la convergenza. Un solo
contesto che genera 80 pain produce 80 varianti di 5 pattern, perche' i primi token condizionano
tutti i successivi.

**Copertura.** Piu' istanze su angoli diversi coprono un ventaglio che una sola non copre, perche'
una sola si ferma quando "ha trovato abbastanza".

**Latenza.** Agenti senza dipendenze di dati eseguiti insieme invece che in fila.

Confonderli porta a parallelizzare le cose sbagliate. Il caso tipico: parallelizzare per velocita'
agenti che invece dovrebbero condividere contesto, o serializzare per prudenza agenti che non hanno
alcuna dipendenza fra loro.

---

## Dove parallelizzare in questa pipeline

### Wave 1 — 8 shard di pain-finder (motivo: diversita')

Ogni shard riceve una coppia **(settore, angolo)** diversa e **non vede gli altri**.

Gli otto angoli sono scelti per essere ortogonali fra loro, cioe' per non intercettare gli stessi
pain:

1. adempimenti normativi ricorrenti con sanzione
2. riconciliazioni manuali fra sistemi che non si parlano
3. rilavorazioni e scarti da informazione sbagliata o tardiva
4. ritardi di incasso e contestazioni documentali
5. penali e non conformita' di filiera
6. consulenze esterne ricorrenti su compiti ripetitivi
7. sovra-acquisto e sovra-stoccaggio per mancanza di visibilita'
8. ruoli assunti esplicitamente per compiti manuali (via annunci di lavoro)

L'angolo 8 merita una nota: gli annunci di lavoro sono la fonte piu' sottovalutata di questa
pipeline. Un'azienda che pubblica un annuncio per "addetto inserimento dati / controllo bolle" sta
dichiarando pubblicamente, con una cifra a bilancio, di avere un problema ricorrente. E' la prova
contabile che il budget esiste — cioe' esattamente cio' che "follow the money" richiede.

### Wave 2 — 5 canali di evidenza per pain (motivo: copertura)

`normativa`, `statistica_ufficiale`, `community`, `pricing_mercato`, `trend_adozione`.

Ogni istanza cerca **solo** nel proprio canale. La sovrapposizione e' spreco; la specializzazione e'
il motivo per cui girano insieme. Un solo agente che cerca su tutti e cinque i canali si ferma
quando il primo canale ha dato un risultato soddisfacente — ed e' quasi sempre il canale piu' facile
(community), cioe' il piu' debole.

**Pipelining, non barriera.** L'orchestratore non aspetta che tutti i pain finiscano la wave 2 prima
di aprire la wave 3. Un pain che ha completato le evidenze passa subito al gate successivo. Con 24
pain e tempi molto variabili (un pain normativo con 40 documenti da leggere costa 5 volte un pain di
community), la differenza fra pipelining e barriera e' sostanziale.

### Wave 3 — competitor + trigger + TAM insieme (motivo: latenza)

I tre agenti non hanno dipendenze di dati fra loro. Eseguirli in serie e' attesa pura.

**Ma i gate restano in ordine.** Se il competitor uccide, gli esiti di trigger e TAM vengono
archiviati e non valutati. Appena uno dei tre restituisce `KILL`, l'orchestratore manda `ABORT`
agli altri due.

**Trade-off esplicito:** l'esecuzione parallela spreca token quando il primo gate uccide. In profilo
`DEEP` conviene (`parallel_gate_execution: true`); in `LIGHT` conviene la cascata stretta.

Una regola pratica: se il tasso storico di kill al gate 3 supera il 60%, la cascata e' piu'
economica anche in DEEP. Il dato si legge da `runtime/state/seen-pains.jsonl`.

### Wave 6 — 3 lenti di red team + verifier (motivo: diversita' + copertura)

Le tre lenti sono cieche fra loro, ed e' cio' che rende significativo il voto di maggioranza: tre
istanze coordinate sarebbero un solo giudizio in tre copie.

Il `source-verifier` gira **insieme** al red team, non dopo: non ha dipendenze dal loro output.

### Dove NON parallelizzare

**Il ranking.** E' comparativo per natura: serve un solo agente che veda tutte le idee insieme. E'
l'unico vero barrier della pipeline.

**Il bootstrap-filter su una singola idea.** E' veloce (nessuna ricerca) e ha bisogno del dossier
intero. Parallelizzarne le dimensioni frammenterebbe il giudizio senza guadagno.

---

## Strategie di deduplica

Con 8 shard + 5 canali × 24 pain + 3 lenti, la stessa ricerca verrebbe eseguita decine di volte
senza controllo.

**Query ledger.** Prima di ogni ricerca l'agente normalizza la query (minuscolo, senza
punteggiatura, termini ordinati alfabeticamente) e controlla `runtime/state/query-ledger.jsonl`. Se esiste
gia' per il run corrente, riusa il risultato.

**Source ledger persistente fra run.** Una fonte gia' registrata e verificata in un run precedente
non va riverificata. Il costo del `source-verifier` cala nel tempo.

**Intervalli di ID assegnati.** L'orchestratore assegna a ogni istanza un `source_id_range`, cosi'
istanze parallele non collidono e il merge e' banale.

**Conteggio per angolo, non per stringa.** Il vincolo "minimo 5 ricerche distinte" si misura su
`angle` diversi, non su riformulazioni della stessa domanda. Cinque modi di chiedere la stessa cosa
sono una ricerca sola.

---

## Limiti di concorrenza

`config.parallelism.max_concurrent_subagents` = 8 di default.

Alzarlo non aumenta linearmente la resa: oltre una certa soglia si satura il rate limit dei motori
di ricerca e le istanze cominciano a ricevere gli stessi risultati. Otto e' un buon punto di
equilibrio per un run settimanale non presidiato.

Il `funnel.max_searches_total` (250 in DEEP) e' un tetto **duro**. Al superamento l'orchestratore
chiude la wave in corso, non ne apre altre e procede al ranking dichiarandolo nelle Note di
esecuzione. Meglio un run dichiaratamente incompleto che un run che sfora silenziosamente.

---

## Ordine di lancio consigliato in Claude Code

Le chiamate parallele vanno emesse **in un unico messaggio** con piu' invocazioni del tool `Task`.
Emetterle in messaggi successivi le serializza e annulla il guadagno.

```
# Wave 1 — un solo messaggio, 8 Task
Task(pain-finder, shard 1) … Task(pain-finder, shard 8)

# Wave 2 — un messaggio per lotto di pain, 5 Task ciascuno
Task(evidence-researcher, pain P-001, canale normativa)
… × 5 canali

# Wave 3 — un messaggio per pain, 3 Task
Task(competitor-analyst, P-001)
Task(trigger-finder, P-001)
Task(tam-analyst, P-001)

# Wave 6 — un messaggio per idea, 4 Task
Task(red-team, lente competitor_nascosti)
Task(red-team, lente fallimenti_storici)
Task(red-team, lente economics_e_distribuzione)
Task(source-verifier, campione claim)
```

---

## Stima dei costi per profilo

Le chiamate sono contate nel caso peggiore (nessun kill anticipato). I gate potano il funnel, quindi
il consumo reale e' tipicamente il 40-60% di questi valori.

| Profilo | Shard | Chiamate ad agente (caso peggiore) | Ricerche (tetto) | Uso indicativo |
|---|---|---|---|---|
| `LIGHT` | 4 | ~55 | 300 | test della pipeline, run frequenti |
| `BALANCED` | 6 | ~110 | 700 | run settimanale standard |
| `DEEP` | 8 | ~200 | 1200 | run settimanale non presidiato, massima copertura |

Il dettaglio del calcolo per profilo e' in `config.budget._calcolo_teorico_*`. La regola da non
violare mai: **`max_searches_total` deve restare sopra la somma teorica dei budget per istanza.**
Se lo si abbassa sotto, la pipeline si ferma a meta' funnel e produce un report vuoto che sembra
l'esito legittimo previsto da `allow_empty_output` — il guasto piu' insidioso possibile, perche' si
traveste da funzionamento corretto.

In `DEEP`, un'idea che arriva alla pubblicazione ha accumulato **oltre 40 ricerche distinte per
angolo** fra tutti gli agenti, contro le 5 minime richieste dal prompt originale. Il vincolo
originale resta come pavimento, non come obiettivo.
