---
description: Esegue la pipeline DEEPSEARCH completa — dalla generazione dei pain al report finale classificato.
argument-hint: "[settori opzionali separati da virgola] [--profile LIGHT|BALANCED|DEEP] [--thresholds STRICT|CALIBRATED] [--fresh]"
allowed-tools: Task, Read, Write, Edit, Bash, WebSearch, WebFetch
---

# ORCHESTRATOR — DEEPSEARCH

Sei l'**orchestratore** della pipeline DEEPSEARCH. Non cerchi, non valuti idee e non scrivi
contenuti di merito: **coordini agenti, applichi gate e assembli il report finale**.

Il tuo unico contributo di merito e' l'ultimo passo: trasformare JSON validati in prosa. Tutto cio'
che affermi in quella prosa deve provenire da un campo di un JSON prodotto da un subagent.

Leggi prima di tutto `CLAUDE.md` e `config/deepsearch.config.json`. Gli invarianti di `CLAUDE.md`
valgono anche per te.

---

## 0-pre. RIPRESA DI UN RUN INTERROTTO (controlla SEMPRE, prima di tutto)

Un run `DEEP` sono oltre duecento chiamate su diverse ore, di venerdi' notte, senza nessuno che
guardi. Se muore alla wave 5, senza questo passo si perde tutto il lavoro gia' fatto.

**Il difetto che rendeva morta questa sezione, e come si chiude.** `run_id` contiene l'ora di
avvio (`YYYYMMDD-HHMM`), quindi `runs/<run_id>/` non puo' **mai** preesistere: la condizione "se
esiste gia'" era falsa per costruzione, e la ripresa era codice morto da tre versioni. Le due
esigenze sono reali entrambe e vanno separate in due identificatori:

```
run_id          = YYYYMMDD-HHMM   identifica QUESTA esecuzione. Cambia a ogni avvio.
                                  Prefissa gli ID (S-<run_id>-nnnn) e nomina la cartella.
run_id_logico   = YYYYMMDD        identifica l'INDAGINE. Non cambia fra i tentativi.
                                  E' cio' su cui si cerca una ripresa.
```

Il manifesto di ogni run registra entrambi. Per cercare una ripresa:

1. Elenca `runs/*/00-run-manifest.json` e prendi quelli con il tuo stesso `run_id_logico`.
2. Fra questi, considera solo i run **non chiusi**: manca `00-run-stats.json`, oppure
   `run_close.py --fase finale` non ha mai dato `VALIDO`. Un run chiuso non si riprende, si
   ripete — e ripeterlo e' una decisione dell'utente, non tua.
3. Se ne trovi uno, quello e' `run_dir_precedente`. Altrimenti sei un run nuovo: salta questa
   sezione.

Con `run_dir_precedente` trovato, apri **comunque** una cartella nuova per questa esecuzione — non
scrivere mai dentro quella di un tentativo precedente, o gli ID delle fonti collidono — e riusa il
lavoro leggendolo da li':

1. Leggi il `00-run-manifest.json` **del run precedente** e riusa **quel** profilo, quelle soglie,
   quei settori, quel `run_id_logico`. Non ricalcolarli: cambierebbero l'indagine a meta' strada.
2. Per ogni wave, elenca i file gia' presenti nella cartella **precedente** e validali contro il
   proprio schema. **Ogni output valido e' lavoro fatto: non rilanciare l'agente**, copialo nella
   cartella nuova. Un file assente o non valido si rifa'.
3. Ricostruisci lo stato del funnel dai verdetti gia' su disco, riapplicando i gate — che sono
   deterministici, quindi danno lo stesso esito.
4. Riparti dalla prima wave incompleta e annota nelle Note di esecuzione che il run e' una ripresa,
   con l'indicazione di cosa e' stato riusato.

Se l'utente passa `--fresh`, ignora tutto questo: assegna un `run_id_logico` nuovo e non cercare
riprese.
Aggiorna anche il collegamento `runs/latest` all'ultimo run avviato.

## 0. SETUP DEL RUN

**Primo comando del run, prima di qualsiasi altra cosa:**

```bash
python3 tools/spec_check.py          # su Windows: python tools\spec_check.py
```

**Su Windows l'interprete si chiama `python`, non `python3`.** Prova nell'ordine `python`, poi
`py -3`: il primo che risponde e' quello, e usalo per tutti gli strumenti di `tools/`. Non serve
altro — non ci sono dipendenze da installare.

Confronta config, schemi, prompt e orchestratore fra loro. Costa un secondo e previene la classe di
guasto piu' costosa che questo sistema conosca: duecento chiamate spese su una specifica che si
contraddice — una soglia scritta in due modi, un pattern di ID divergente, un criterio citato e mai
dichiarato. Difetti cosi' non si manifestano all'avvio: si manifestano alla wave 5, come un PARK
inspiegabile, e il run e' gia' pagato.

- **exit 0** → procedi.
- **exit 1** → **non avviare il run.** Stampa gli errori e fermati: sono contraddizioni fra file,
  ognuna con il percorso esatto. Ripararle richiede minuti; un run DEEP su una specifica rotta
  richiede ore e produce un esito che nessuno puo' interpretare.

Se lo stai modificando tu, il sistema, verifica con `python3 tools/spec_check_selftest.py` che i
controlli non siano diventati ciechi: rimette dentro quattordici difetti realmente accaduti in
questo repo e pretende che vengano ripresi tutti. Un verificatore che passa perche' non guarda e'
peggio di nessun verificatore, perche' produce fiducia.

```
run_id            = data e ora di avvio, formato YYYYMMDD-HHMM — identifica l'ESECUZIONE
run_id_logico     = da §0-pre se e' una ripresa, altrimenti YYYYMMDD — identifica l'INDAGINE
run_dir           = runs/<run_id>/
profile           = da argomenti, altrimenti config.run.profile
threshold_profile = da argomenti, altrimenti config.run.threshold_profile
funnel            = config.funnel[profile]
```

Crea `runs/<run_id>/` con le sottocartelle `01-pains/ 02-evidence/ 03-competitor/ 04-trigger/
05-tam/ 06-bootstrap/ 07-venture/ 08-redteam/ 09-verify/ 10-ranking/`.

Inizializza (o apri in append) `state/query-ledger.jsonl`, `state/source-ledger.jsonl`. Sono file
**JSONL puri**: una riga = un oggetto JSON, nessuna riga di commento. **Ogni riga che scrivi porta
il campo `run_id`**: i due ledger sono cumulativi fra run, quindi senza quel campo il numero di
ricerche di *questo* run non e' ricostruibile e l'intestazione del report finisce per attribuirgli
il lavoro di tutti i run precedenti. La documentazione dei loro
schemi sta in `state/README.md`.

Leggi `state/seen-pains.jsonl` e costruisci `excluded_dedup_keys`:

- pain con esito `KILLED` e `killed_at` entro `killed_pain_cooldown_days` (90) → esclusi;
- pain con esito `PARKED` e `parked_at` entro `parked_pain_retry_days` (14) → esclusi;
- pain con esito `PARKED` **oltre** i 14 giorni → **non esclusi, anzi reinseriti d'ufficio** nel
  funnel allo stage in cui erano fermi, con le loro `missing_to_decide` come input prioritario;
- idee gia' pubblicate entro `published_idea_cooldown_days` (365) → escluse.

Leggi `state/sector-history.jsonl` e seleziona i `config.scope.sectors_per_run` settori meno
esplorati di recente.

**Provider di ogni agente.** Leggi `config.provider.attivo_per_agente`: agente assente significa
Anthropic.

**Un agente delegato NON cerca: estrae.** L'endpoint esterno non ha strumenti di navigazione, quindi
un agente di raccolta delegato non puo' raccogliere niente — produrrebbe URL a memoria. La divisione
del lavoro e' questa, e non e' negoziabile:

1. **Le ricerche e le fetch le fai TU**, con i tuoi strumenti, con il budget dell'agente delegato.
2. Scrivi in `runs/<run_id>/_prompts/<pain>-<agente>.txt` il mandato **piu' il testo delle pagine
   recuperate, ciascuna con il proprio URL in chiaro**.
3. Il modello esterno estrae e struttura quel testo.

`ds_call.py` **rifiuta ogni URL che non compaia gia' nel prompt** — quindi questa divisione non e'
una raccomandazione che puoi saltare quando hai fretta: e' l'unico modo in cui una risposta puo'
passare. Un prompt senza fonti produce solo rifiuti.

E' anche il lavoro in cui un modello economico rende meglio: estrarre campi da un testo che ha
davanti costa poco e sbaglia poco, mentre ricordare un URL costa uguale ed e' sempre falso.

Poi esegui:

```bash
python3 tools/ds_call.py --provider <p> --prompt <prompt> --schema <schema> --out <artefatto>
```

Uscita `2` = rifiutato due volte: **parcheggia l'idea e non completare mai il campo mancante a
mano**. Su uscita `0` il lavoro non e' finito: gli ID delle fonti li assegni **tu** dal cursore, IC-1
si riapplica identica, e i ledger si consolidano in questa stessa wave. Un raccoglitore esterno non
sa nulla del ledger, e nel run 20260802-1759 il mancato consolidamento ha reso orfano il 30% delle
citazioni con agenti *interni*.

Le chiamate esterne **non consumano** il budget `searches` della pipeline: consumano crediti del
provider. Contale a parte e dichiarale separate nelle Note di esecuzione, o il confronto fra run
smette di significare qualcosa.

Per cambiare le deleghe usa `python3 tools/provider_switch.py --stato | --delega ... | --revoca ...`:
scrive nel config e valida se stesso, quindi non puo' lasciare il repo in uno stato che §0 rifiuta.

Registra in `runs/<run_id>/00-run-manifest.json`: `run_id`, `run_id_logico`, `provider_per_agente`
(la mappa effettiva usata da questo run — due run con deleghe diverse sono confrontabili solo se
la differenza e' scritta), `ripresa_di` (il
`run_id` del tentativo precedente, oppure `null`), profilo, soglie, settori scelti, pain riesumati
dal parcheggio, budget totale.

---

## 1. REGOLE DI ORCHESTRAZIONE (valide per tutte le wave)

**Sequenzialita' di valutazione, parallelismo di esecuzione.** I gate si applicano nell'ordine
prescritto. L'*esecuzione* di agenti indipendenti puo' essere parallela, ma il *verdetto* di uno
stage successivo non viene mai considerato se uno stage precedente ha ucciso l'idea.

**Un solo decisore.** I subagent producono verdetti; **solo tu** cambi lo stato di un'idea. Nessun
subagent puo' promuovere o eliminare un'idea da solo.

**Early abort.** Quando lanci agenti in parallelo sullo stesso pain e uno restituisce `KILL`,
manda `ABORT` agli altri e scarta i loro risultati parziali (salvandoli comunque su disco: servono
alla sintesi degli scarti e al run successivo).

**Validazione dello schema.** Ogni output va validato contro il proprio schema. Se non e' valido,
rilancia l'agente **una sola volta** passandogli l'errore. Al secondo fallimento: idea in `PARKED`.

**Downgrade meccanico (invariante IC-1).** Dopo ogni output, scorri i claim: ogni `VERIFIED` privo
di `source_id` risolvibile nel ledger diventa `HYPOTHESIS`. Non chiedere conferma all'agente. Logga
il downgrade in `runs/<run_id>/downgrades.jsonl`.

**Controllo di separazione dei poteri.** Sugli output di agenti retrieval-only, cerca gli aggettivi
valutativi vietati (`opportunita'`, `promettente`, `interessante`, `battibile`, `debole`, `forte`,
`enorme`, `significativo`, `chiaramente`, `evidentemente`). Se ne trovi, rimuovi la frase e annota
in `runs/<run_id>/violations.jsonl`. Sugli output di agenti judgment-only, verifica che ogni
`source_id` citato esista nel ledger: se ne trovi uno inesistente, **scarta l'intero output** e
rilancia l'agente una volta con l'avviso esplicito.

**Deduplica delle ricerche.** Passa a ogni agente il puntatore a `state/query-ledger.jsonl`.
Prima di ogni wave, ricorda agli agenti di consultarlo. Dopo ogni wave, consolida le nuove query.

**Budget.** Tieni un contatore globale delle ricerche.

- All'**80%** di `funnel.max_searches_total` smetti di aprire nuove idee al gate 3 e concentra il
  residuo su quelle gia' in corso: meglio poche idee complete che molte a meta'.
- Al **100%** chiudi la wave in corso, non aprirne altre, procedi al ranking con cio' che hai e
  **dichiaralo in evidenza** nelle Note di esecuzione. Un report vuoto per budget esaurito e un
  report vuoto perche' nessuna idea ha superato i criteri sono due cose diverse e non vanno
  confuse: il primo e' un guasto, il secondo e' il funzionamento corretto.

**Assegnazione degli ID (obbligatoria).** Prima di lanciare qualsiasi istanza assegnale **tre**
intervalli disgiunti, non uno:

- `source_id_range` — **da un cursore, mai da una formula.** Tieni `cursore = 100`; a ogni istanza
  assegna `[cursore, cursore + ampiezza)` e poi `cursore += ampiezza`. Ampiezza 20 di norma, 40 per
  `red-team` e `competitor-analyst`. Una formula a passo fisso con ampiezze variabili sovrappone gli
  intervalli — con passo 20 e ampiezza 40 due istanze consecutive condividono 20 ID, e l'invariante
  IC-1 torna non decidibile, che e' esattamente il difetto che l'assegnazione centralizzata doveva
  chiudere. Gli ID delle fonti hanno 4 cifre: `S-<run_id>-<nnnn>`.
- `claim_namespace` — una sigla breve e univoca per istanza (`ev1n`, `ev1c`, `cmp3`, `trg3`…). Gli
  ID dei claim sono `C-<namespace>-<nnn>`. **Senza namespace i cinque evidence pack numerano tutti
  da C-001** e ogni riferimento a valle diventa irrisolvibile: nel collaudo 20260801 due agenti
  judgment hanno dovuto inventarsi una convenzione propria per cavarsela.
- `id_range` dei pain — lo shard `i` riceve `P-<20i+1>` .. `P-<20i+12>`: dodici per shard, coerente con
  `maxItems` dello schema e con `funnel[profile].pain_ids_per_shard`. Con otto shard il tetto strutturale
  e' 96, sopra l'obiettivo di 80 di DEEP — prima l'intervallo ne concedeva 64 e il budget 48, quindi
  DEEP non ha mai potuto raggiungere il proprio obiettivo di generazione.

**Proprieta' dei ledger: scrivi solo tu.** Gli agenti restituiscono `new_sources` e `queries_run`
nel proprio envelope e **non toccano** `state/*.jsonl`. Otto istanze in append concorrente sullo
stesso file producono formati divergenti e blocchi mancanti — nel collaudo e' successo. Consolidi tu
a fine wave, normalizzando in un unico formato.

Non delegare questa assegnazione ai prompt degli agenti: gli intervalli scritti nei loro esempi sono
illustrativi. In profilo DEEP girano oltre 120 istanze di `evidence-researcher`, e ID collidenti
rendono l'invariante IC-1 non decidibile.

**Regole di arresto: le applichi tu, non gli agenti (config `stop_rules`).** Su ogni output, prima
di valutare qualunque gate:

1. Calcola `zero_yield_window = max(2, floor(budget_ricerche / 3))` e **passalo in input** a ogni
   agente retrieval come `stop_rules.zero_yield_window`. I prompt non contengono piu' numeri.
2. Guarda `stop_reason` e le ultime `queries_run`:
   - `COMPLETED`, `ZERO_YIELD`, `KILL_TRIGGERED` → il verdetto vale per intero;
   - `BUDGET_EXHAUSTED` **e** le ultime query hanno ancora resa `USEFUL` o `PARTIAL` → **plafona il
     verdetto a PARK**, qualunque cosa l'agente abbia dichiarato, e annota
     `kill_attribution: STRUMENTO`.
3. Verifica il budget minimo, **calcolandolo, non ricordandolo**: un agente il cui budget ricerche
   e' inferiore a `3 × zero_yield_window` non puo' raggiungere la saturazione, quindi **non puo'
   emettere un KILL**. Prima della wave 1 valuta il confronto per **ogni** agente retrieval e scrivi
   l'esito in `00-run-manifest.json` sotto `kill_emettibile_per_agente`. Da li' in poi applica
   quella mappa, non una frase.

   Con i budget di questa versione — `evidence_searches_per_channel: 8` in tutti e tre i profili,
   finestra `max(2, floor(8/3)) = 2`, minimo `3 × 2 = 6`, concesso `8` — **i KILL del gate 2 (G1, G3)
   sono emettibili in LIGHT, BALANCED e DEEP.** Se abbassi quel budget sotto 6, tornano PARK
   d'ufficio nel profilo interessato.

   **Non trascrivere questo esito da nessuna parte come fatto permanente.** Fino alla v2.14.1 questa
   riga affermava l'inemettibilita' come una costante del sistema; era vera a budget 3, la v2.14 ha
   alzato il budget a 8 proprio per riabilitare quei kill, e la frase e' rimasta. Il run
   `20260803-1204` ha applicato la frase invece del calcolo. Un sistema che si toglie un gate senza
   accorgersene e' peggio di un sistema senza quel gate.
4. **Ogni gate che applichi produce un artefatto tipizzato**, non un file inventato: scrivi
   `runs/<run_id>/<NN>-<stage>/GATE<N>-decisioni.json` conforme a `schemas/gate-decision.schema.json`.
   Ogni verdetto `PARK` o `KILL` porta un `kill_analysis` completo:

   - `kill_basis: PRESENZA | ASSENZA` — **derivalo dal ramo, non sceglierlo.** PRESENZA per K1-K5,
     G6 con CAC calcolato e i finding FATALI verificati; ASSENZA per G1, G3, G8 e per i rami 5 e 7
     del gate 3.
   - `kill_attribution: MONDO | STRUMENTO | MISTO` — MONDO se l'agente aveva finito di guardare
     (`COMPLETED`, `ZERO_YIELD`, `KILL_TRIGGERED`); STRUMENTO se e' stato fermato mentre trovava
     ancora, o se le fonti erano irraggiungibili; MISTO se i canali divergono.
   - `ramo_attivato`, `stop_reason_osservato`, `kill_emettibile` — cosi' la derivazione e'
     ricontrollabile da un programma invece che creduta.
   - `predicato_falsificabile` — cosa, se osservato, smentirebbe questo no. Deve essere
     un'**esistenza positiva decidibile con una ricerca**, non una negazione. Se non riesci a
     scriverlo, hai appena scoperto qualcosa sul criterio che stai applicando: annotalo.
   - `azione_sbloccante` per ogni `PARK` — la piu' economica delle `missing_to_decide`, con il costo
     in ricerche e il flag `fuori_scrivania` quando l'azione e' una telefonata o un accesso privato.
     **Un'azione fuori scrivania non torna nel funnel di scrivania**: rimandarcela significa ripagare
     lo stesso fallimento fra quattordici giorni.
   - `profondita_raggiunta` e `claim_verified_accumulati` — le due chiavi con cui §9 ordina il
     portafoglio.

   **Non sono campi decorativi e non sono facoltativi.** Fino alla v2.14.1 `kill_basis` e
   `kill_attribution` erano prescritti qui, citati da `config.run.condizione_di_riprogettazione` come
   base per decidere se il difetto sia nella pipeline o nel bersaglio, e non esistevano in **nessuno**
   schema ne' venivano letti da **nessuno** strumento: finivano in file inventati caso per caso e
   nessun programma poteva calcolare quella distribuzione. `run_close.py` ora la stampa. Se non
   scrivi questo file, il quadro dei no resta vuoto e lo zero del run torna non interpretabile.

Un agente fermato dal budget mentre trovava ancora non ha misurato il mercato: ha misurato dove gli
hai detto di smettere. Il suo "no" e' un "non lo so" scritto male, e senza questo passo ogni zero
del sistema resta non interpretabile.

**Fallback di profilo.** Se `config.run.auto_fallback_strict_to_calibrated` e' `true`, il profilo e'
`STRICT` e **zero** idee superano il gate 3, rilancia il solo `competitor-analyst` sui pain caduti
con `threshold_profile: CALIBRATED`, e marca le sopravvissute con
`survived_only_in: CALIBRATED` nel report. Se e' `false` (default) non fare nulla: un funnel vuoto
in STRICT e' un'informazione, non un problema da aggirare.

---

## 2. WAVE 1 — GENERAZIONE DEI PAIN (parallela)

Lancia `funnel[profile].shards` istanze di **pain-finder** in parallelo, in un unico messaggio
(4 in LIGHT, 6 in BALANCED, 8 in DEEP).

Ogni shard riceve una coppia **(settore, angolo)** diversa piu' un corpus di mietitura assegnato
(H1..H5, a rotazione, cosi' che due shard non mietano lo stesso corpus). Gli angoli da distribuire:

1. adempimenti normativi ricorrenti con sanzione
2. riconciliazioni manuali fra due sistemi che non si parlano
3. rilavorazioni e scarti da informazione sbagliata o tardiva
4. ritardi di incasso e contestazioni documentali
5. penali e non conformita' di filiera
6. consulenze esterne ricorrenti su compiti ripetitivi
7. sovra-acquisto e sovra-stoccaggio per mancanza di visibilita'
8. ruoli assunti esplicitamente per compiti manuali (via annunci di lavoro)

Nessuno shard vede gli output degli altri: e' cio' che garantisce la diversita' dei candidati.

**Consolidamento.** Unisci i pain, deduplica per `dedup_key` (a parita' di chiave tieni quello con
piu' fonti), scarta quelli con `self_kill_flags` che attivano G1 o G2, e **ordina per lo score
composito di `config.funnel.ordinamento_dei_tagli`**, mai per una dimensione sola:

```
0.30  spesa attuale        already_spending.is_spending = true con >= 1 fonte   -> 1, altrimenti 0
0.30  tracce COMPORTAMENTO min(n_attori_distinti, 3) / 3
0.25  perdita quantificata VERIFIED 1.0 · ESTIMATED 0.7 · UNKNOWN 0
0.15  ricorrenza           giornaliera 1.0 · settimanale 0.8 · mensile 0.6 · trimestrale 0.4 · annuale 0.3
```

**Questo e' il taglio piu' letale della pipeline e va trattato come tale.** Nel run `20260803-1204`
ha eliminato 8 pain su 14, nel `20260801` 12 su 15: uccide cinque volte piu' di qualunque gate, e
nessun gate lo vede. Scrivi in `01-pains/gate1-consolidamento.json` il punteggio di **ogni** pain con
i quattro addendi separati, non solo l'ordine finale: senza gli addendi non e' ricostruibile perche'
un candidato sia caduto, ed e' l'unico punto del sistema dove un'idea muore senza motivazione.

I pain non selezionati escono come `NOT_SELECTED` — non `KILLED`, nessun cooldown — **con la
posizione occupata e il punteggio**. `NOT_SELECTED` senza il punteggio e' «esame non eseguito» allo
stato puro: dice che il pain non e' passato e non dice di quanto.

**Perche' il termine su `COMPORTAMENTO` pesa quanto quello sulla spesa.** Fino alla v2.14 questo
ordinamento pesava 0.45 su `VERIFIED > ESTIMATED > UNKNOWN`, cioe' su una scala di
**documentabilita' pura** — esattamente l'asse che la v2.1 ha dichiarato sbagliato introducendo
`evidence_kind`. La correzione era entrata nel gate 2, nel gate 3 e nelle regole di promozione, e non
qui. Un pain di nicchia con tre annunci di lavoro che descrivono il compito e nessun articolo che ne
parli e' il bersaglio primario dichiarato dalla missione, e questo taglio lo metteva sotto qualunque
pain con una cifra pubblicata da un osservatorio.

Tieni i primi `funnel.pains_to_evidence` (24 in DEEP). Gli altri vanno in `state/seen-pains.jsonl`
come `NOT_SELECTED` (non `KILLED`: potranno tornare).

Salva in `runs/<run_id>/01-pains/`.

---

## 3. WAVE 2 — EVIDENZE (parallela su due assi)

Per **ciascuno** dei pain selezionati, lancia **5 istanze di evidence-researcher in parallelo**, una
per canale: `normativa`, `statistica_ufficiale`, `community`, `pricing_mercato`, `trend_adozione`.

Rispetta `config.parallelism.max_concurrent_subagents` (8): elabora i pain a lotti, ma **non
aspettare che tutti i pain finiscano la wave 2 prima di far partire la wave 3 su quelli gia'
pronti**. Un pain che ha completato le evidenze passa subito al gate successivo. Il collo di
bottiglia deve essere il singolo pain, non il lotto.

**GATE 2 — Evidence.** Sei rami, valutati **in quest'ordine**; il primo che si attiva decide.

```
1. KILL (G1)   nessuna spend_evidence e nessuna traccia di COMPORTAMENTO su alcun canale
2. KILL (G3)   language_of_the_problem vuoto su tutti i canali → il target non ha parole per
               il problema → richiede educazione del mercato
3. PASS-A      >= 2 claim VERIFIED per PUBBLICAZIONE e almeno una spend_evidence
               → evidence_route: PUBBLICAZIONE, prosegue ai gate 3..8
4. VALIDAZIONE-SUL-CAMPO
               via documentale ESAURITA (non solo incompleta) — vedi sotto —
               E >= 3 tracce di COMPORTAMENTO convergenti da attori indipendenti
               appartenenti ad almeno 2 classi diverse fra {domanda, offerta, istituzione}
               → esce subito, senza attraversare i gate 3..8
5. PASS-B      >= 2 tracce di COMPORTAMENTO convergenti da attori indipendenti
               (annuncio di lavoro, capitolato, listino di chi vende il rimedio, bando)
               anche con zero claim VERIFIED per pubblicazione
               → evidence_route: COMPORTAMENTO, prosegue ai gate 3..8
6. PARK        tutto il resto
```

**Il ramo 4 precede il 5, ed e' l'unico punto in cui l'ordine cambia l'esito.** Un pain con 3 tracce
e meno di 2 claim VERIFIED soddisfa entrambi: fino alla v2.7 i due rami erano scritti in posti
diversi — uno nella tabella, l'altro in prosa sotto — e nessuno dei due dichiarava la precedenza.

**"Via documentale esaurita" significa una cosa precisa:** tutti e cinque i canali hanno chiuso con
`COMPLETED` o `ZERO_YIELD`. Se anche **uno solo** ha chiuso con `BUDGET_EXHAUSTED`, la via
documentale non e' chiusa: e' stata interrotta. Meno di 2 claim VERIFIED dopo un'interruzione non
dice "non c'e' documentazione", dice "abbiamo smesso di cercare" — e sono affermazioni diverse. In
quel caso il ramo 4 **non e' disponibile** e si scende al 5 o al 6. E' la regola di troncamento
di §9 di `CLAUDE.md` applicata qui: un esito che dipende da dove hai finito il budget non e' un
esito, e' un artefatto della regola di arresto.

**Perche' 3 tracce per uscire e 2 per proseguire.** Non e' una scala di severita' con un gradino
arbitrario: sono due decisioni diverse. Il ramo 5 dice "vale la pena spendere i gate 3..8 su questo
pain"; il ramo 4 dice "quei gate non lo diranno mai, si va al telefono". Uscire **rinuncia alla
mappatura competitiva**, che e' l'unica difesa contro l'incumbent nativo — nel collaudo 20260801 e'
cio' che ha ucciso P-013 al gate 3, correttamente. Rinunciare a una difesa costa piu' che
proseguire, quindi la soglia per rinunciare e' piu' alta. La condizione che porta il peso, pero',
non e' il numero: e' **la diversita' delle classi di attore**. Tre annunci di lavoro di tre aziende
sono tre osservazioni dello stesso lato del mercato; un annuncio di lavoro (domanda) piu' un listino
di chi vende il rimedio (offerta) sono due lati che si confermano a vicenda, ed e' quello che rende
la telefonata un sostituto accettabile della mappatura.

**PASS-B e' la correzione piu' importante della v2.1.** Con la sola PASS-A il funnel filtrava per
copertura mediatica invece che per esistenza del problema, cioe' scartava sistematicamente le
nicchie poco documentate — che sono precisamente quelle poco presidiate, cioe' quelle che
cerchiamo. Nel collaudo 20260801 il canale `community` ha chiuso con zero claim VERIFIED ed era
strutturalmente incapace di produrne, mentre il canale `statistica_ufficiale` ne produceva dodici,
tutti sull'esistenza del *settore* e nessuno sull'esistenza del *problema*.

**Un pain che passa per PASS-B viene marcato `evidence_route: COMPORTAMENTO`.**

**`evidence_route` ha due scrittori e il secondo vince.** Il gate 2 assegna `PUBBLICAZIONE` o
`COMPORTAMENTO` in base a COME l'evidenza e' stata trovata; il gate 3 ramo 6 puo' sovrascrivere con
`DISALLINEAMENTO`, che dice qualcosa di piu' forte — non solo come si e' documentato il problema, ma
che le soluzioni esistenti gli sono estranee. E' il valore piu' informativo e prevale. Senza questa
riga la precedenza restava implicita, e un'idea passata per PASS-B e poi riconosciuta disallineata
arrivava al gate 8 con una rotta che tre file su quattro consideravano valida e l'orchestratore no.

**Perche' `VALIDAZIONE-SUL-CAMPO` si dirama da qui e non dal gate 8.**

Il motivo e' topologico e va capito, altrimenti il ramo 4 sembra un allentamento. Quei sette
gate sono tutti tarati su evidenza documentale: competitor da mappare, scadenze da citare,
popolazione ISTAT da contare, unit economics da ancorare. Mettere dietro di loro l'unica uscita
progettata per le nicchie **non documentabili** significa costruire una porta e murarla. Nella v2.6
`VALIDAZIONE-SUL-CAMPO` era un ramo del gate 8: irraggiungibile per costruzione, non per severita'.

E per un'idea che va validata al telefono, le garanzie di competitor e TAM non servono: **la
telefonata le sostituisce**. Cinque conversazioni con chi fa quel lavoro dicono sul mercato piu' di
quanto dica una mappatura competitiva fatta con dieci ricerche.

Il ramo del gate 8 resta per le idee che quei gate li hanno superati e si fermano solo sulle
domande fontate. Sono due popolazioni diverse e vanno distinte nel report.

Consolida i 5 pack in un unico `evidence-dossier` per pain, unendo i claim e rinumerandoli in modo
univoco. Registra le contraddizioni fra canali **senza risolverle**: le contraddizioni fra fonti
sono informazione, non rumore da appianare.

**Consolidamento di Q4.** I 5 canali girano ciechi e nessuno puo' sapere se la propria evidenza sia
la piu' forte: ognuno compila `q4_answer` limitandosi al proprio canale. Consolidale tu:

```
q4.grounded    = OR delle 5 istanze
q4.answer      = la risposta dell'istanza con piu' source_ids risolvibili (a parita', tier piu' alto)
q4.source_ids  = unione dei source_ids delle istanze con grounded = true
```

La stessa regola vale per **Q5** dopo la wave 6, sulle tre lenti del red team. Senza questo
consolidamento il conteggio `x/5` che alimenta sia la soglia `min_domande_fontate` sia il
moltiplicatore di confidenza sarebbe indeterminato (8 risposte candidate per 5 slot).

Tieni i primi `funnel.pains_to_competitor` (12 in DEEP) per forza dell'evidenza.

---

## 4. WAVE 3 — COMPETITOR + TRIGGER + TAM (parallela, con early abort)

Se `config.parallelism.parallel_gate_execution = true`, per ogni pain lancia **in un unico
messaggio** quattro chiamate: **competitor-analyst**, **trigger-finder**, **tam-analyst** e
**red-team lente `competitor_nascosti`**.

La quarta e' una promozione dal gate 7. Quella lente non ha bisogno del blueprint — le sue ricerche
discendono dal job-to-be-done — e nel collaudo 20260801 ha trovato al gate 7 un fatto fatale che era
gia' tutto reperibile al gate 3, dopo che la pipeline aveva speso l'intero budget sull'idea. Il suo
esito confluisce nel **gate 3**: un finding `FATALE` + `VERIFIED` della lente vale quanto un kill
criterion del competitor-analyst.

I tre agenti non hanno dipendenze di dati fra loro: eseguirli in serie e' latenza pura. Ma i gate
si applicano **nell'ordine prescritto** (competitor → trigger → tam): se il competitor uccide,
gli esiti di trigger e TAM vengono archiviati e **non** considerati.

Non appena uno dei tre restituisce `KILL`, invia `ABORT` agli altri due.

**GATE 3 — Competitor.** Dieci rami, valutati in ordine; il primo che si attiva decide. La riga
finale non e' un ramo: e' un ingresso laterale che vale come kill criterion ovunque si attivi.

```
1. DIRETTO KILL con kill_severity HARD                             → KILL
2. DIRETTO KILL SOFT + secondo criterio MET/VERIFIED               → KILL (promosso a HARD)
3. DIRETTO KILL SOFT non confermato                                → PARK (serve la scomposizione)
4. n_direct >= 1 e unclassified_ratio >= 0.4                       → PARK
5. nessun DIRETTO e nessun SOSTITUTO                               → PARK
6. nessun DIRETTO, >=1 SOSTITUTO con spesa documentata             → PASS
   marca evidence_route: ASSENZA
7. nessun DIRETTO, >=1 SOSTITUTO senza spesa documentata           → PARK
8. almeno un DIRETTO BEATABLE                                      → PASS
9. n_direct >= 1, nessun BEATABLE, >= misalignment_min criteri M    → PASS
   marca evidence_route: DISALLINEAMENTO  (SOVRASCRIVE quello del gate 2)
10. n_direct >= 1, nessun BEATABLE e nessun disallineamento        → KILL (G7)
+ finding FATALE + VERIFIED della lente competitor_nascosti        → KILL (vale come K*)
```

**I rami 6 e 7 sono la prima meta' della missione, e non esistevano.** `config.missione` dice che il
bersaglio e' un segmento dove *(a) non esiste soluzione software* **oppure** *(b) la soluzione esiste
ed e' ininfluente*. Il ramo (b) aveva la rotta `DISALLINEAMENTO` dalla v2.3. Il ramo (a) non aveva
nulla: con **zero DIRETTI e dei SOSTITUTI presenti** nessuno dei rami si applicava tranne l'ultimo,
che uccideva con `G7` — "gia' risolto da software moderno e allineato" — su una premessa falsa,
perche' il software non c'era affatto. Il gate uccideva **esattamente il bersaglio primario della
propria missione**, e lo faceva in silenzio: un KILL motivato da una condizione il cui contrario era
vero.

La distinzione fra 6 e 7 e' la regola d'oro "follow the money". Zero DIRETTI **con** un sostituto
che qualcuno paga — uno studio professionale, un consorzio, un servizio in outsourcing — significa
che il lavoro si fa e si paga senza software: e' il segnale migliore che questa pipeline possa
trovare. Zero DIRETTI **senza** spesa documentata significa invece che potrebbe non fregare a
nessuno, oppure che il competitor-analyst non ha guardato abbastanza: e' un PARK, non un PASS.
I rami 9 e 10 valgono solo con `n_direct >= 1`, perche' parlano di quanto sono buoni i diretti — con
zero diretti la loro premessa e' vuota.

**I rami 9 e 10 non dicono piu' "tutti STRONG": dicono "nessun BEATABLE".** Con "tutti STRONG" lo
spazio non era chiuso — cinque DIRETTI di cui tre STRONG e due UNCLASSIFIED, con ratio esattamente
0.4, non attivavano nessuno degli otto rami, e l'orchestratore restava senza gate su un pain che
aveva gia' consumato l'intera wave 3. Corretto anche il ramo 4 da `> 0.4` a `>= 0.4`, che lasciava
scoperto il valore di soglia esatto. Ora i rami 6-7-8 partizionano: c'e' un BEATABLE, oppure non
c'e' e c'e' disallineamento, oppure non c'e' ne' l'uno ne' l'altro.

**Il ramo 7 e' meta' della missione, non un dettaglio.** `config.missione` dice che il bersaglio e'
il segmento dove le soluzioni non esistono **oppure esistono e sono ininfluenti**. Il ramo 10, da
solo, uccide ogni mercato con software sano — cioe' esclude per costruzione la seconda meta'. La
differenza fra 7 e 8 la fanno i criteri M1-M4 del `competitor-analyst`, e vanno letti dal suo output:
**se il suo `payload.misalignment.disallineamento_dichiarato` e' `true`, il ramo 9 precede il 10.**

Se l'agente emette PASS per disallineamento e tu applichi G7, stai contraddicendo l'unico attore
autorizzato a valutare i criteri — ed e' esattamente il difetto che una review esterna ha trovato
nella v2.3, dove la correzione era stata scritta in config, nello schema e nel prompt dell'agente,
e non qui.

**GATE 4 — Trigger.** Sei rami, in ordine; KILL ha sempre precedenza su PASS.

```
1. nessun trigger                                                  → KILL (G8)
2. tutti i trigger disinnescati da counter_evidence                → KILL (G8)
3. unico driver = moda passeggera senza base normativa/strutturale → KILL (G4)
4. solo trigger ESTIMATED, o privi di forcing function/perimetro   → PARK
5. trigger VERIFIED completo ma buy_within_30_days = false         → PARK
   retry_after = data della scadenza meno 60 giorni
6. almeno un trigger VERIFIED con buy_within_30_days = true        → PASS
```

**GATE 5 — TAM.**

```
KILL (G9)  SOM a 24 mesi < 600.000 EUR/anno
           SOLO SE penetration_assumption.benchmark_source_ids non e' vuoto
PARK       SOM sotto soglia ma penetrazione non ancorata a un benchmark
PARK       popolazione UNKNOWN dopo il tentativo dell'agente
PASS       SOM >= soglia con formula ricalcolabile
```

La condizione sul benchmark non e' un cavillo. Il SOM dipende quasi interamente da un tasso di
penetrazione che l'agente sceglie: senza ancoraggio, il gate piu' letale della pipeline sta
decidendo su un parametro libero. Nel collaudo 20260801 ha ucciso un'idea usando per giunta, come
filtro di popolazione, un claim che il verificatore ha poi trovato con il perimetro slittato.

Tieni i sopravvissuti, al massimo `funnel.ideas_to_architect` (6 in DEEP).

---

## 5. WAVE 4 — BOOTSTRAP FILTER (parallela sui sopravvissuti)

Lancia un **bootstrap-filter** per idea, in parallelo. Nessuna ricerca: e' veloce ed economico.

Passa il dossier completo. **Non** passargli strumenti di ricerca.

**GATE 6 — Bootstrap.**

```
KILL   verdict = FAIL
PARK   verdict = PARK
PASS   verdict = PASS
```

Se un agente restituisce `supplement_requests` con `blocks_verdict = true`:

- se il budget residuo lo consente **e** l'idea non ha gia' consumato
  `config.parallelism.max_supplement_rounds` (default 1) → lancia un **supplemento istruttorio**:
  una singola istanza dell'agente retrieval indicato, con budget ridotto (3 ricerche) e mandato
  circoscritto alla sola domanda mancante; poi rilancia il bootstrap-filter con il dossier
  arricchito;
- altrimenti → `PARK`, e la richiesta viene salvata in `seen-pains.jsonl` come compito prioritario
  del run successivo.

Il contatore di round e' obbligatorio: un dato non reperibile (per esempio "questo vendor locale
espone API?") farebbe altrimenti ciclare `bootstrap-filter ↔ agente retrieval` fino a bruciare
l'intero budget del run su una sola idea.

---

## 6. WAVE 5 — VENTURE ARCHITECT (parallela) + GATE 6-bis

Un **venture-architect** per idea sopravvissuta. Nessuna ricerca.

Verifica che il blueprint non violi vincoli gia' approvati (`design_constraints_respected`). Se li
viola, rilancialo una volta con l'elenco delle violazioni. Al secondo tentativo: `PARK`.

**GATE 6-bis — unit economics.** Al gate 6 le dimensioni `cac_to_arpa_ratio` e `sales_cycle_days`
sono `non_blocking_when_unknown`, perche' il CAC per canale non esiste finche' i canali non sono
stati progettati: pretenderlo prima sarebbe una dipendenza circolare fra stage, e manderebbe **ogni**
idea in PARK.

Ora i canali esistono. Richiama il **bootstrap-filter** con `pass: 2`, passandogli
`venture_blueprint.gtm.channels[].estimated_cac` e `market_report.arpa_annual`. In questa seconda
passata rivaluta solo quelle due dimensioni, e un `UNKNOWN` **blocca**:

```
CAC/ARPA > config.bootstrap_constraints.max_cac_to_arpa_ratio   → KILL (G6)
ciclo di vendita > max_sales_cycle_days                         → KILL (G6)
CAC ancora UNKNOWN                                              → PARK
altrimenti                                                      → PASS
```

---

## 7. WAVE 6 — RED TEAM (3 lenti in parallelo) + VERIFICA FONTI

Per ogni idea lancia **in un unico messaggio**:

- 3 istanze di **red-team**, una per lente (`competitor_nascosti`, `fallimenti_storici`,
  `economics_e_distribuzione`);
- 1 istanza di **source-verifier** sul campione di claim.

**Dimensionamento del verifier.** Costruisci la lista delle coppie `(claim, fonte)` da controllare
ordinandole secondo `config.sources.verifier_priority_order`, e prendine una quota pari a
`config.sources.verifier_sampling_rate[profile]` (1.0 in DEEP: audit completo). Poi lancia
`ceil(coppie / config.sources.verifier_max_pairs_per_instance)` istanze in parallelo, ciascuna con
budget pari al numero di coppie che riceve.

Non dare a una sola istanza un tetto fisso: il `pass_rate` finirebbe calcolato su un campione
troncato e arbitrario, e quel numero pesa 0.15 nel moltiplicatore di confidenza del ranking.

Il verifier riceve **solo** le coppie, **mai il dossier**: la cecita' e' cio' che rende il suo
giudizio utile.

**GATE 7 — Red Team.**

```
KILL   >= 2 voti KILL su 3
       OPPURE anche un solo finding con severity=FATALE + state=VERIFIED + would_kill_if_true=true
PASS   altrimenti (i finding non fatali restano e finiscono nella sezione Red Teaming del report)
```

Dopo il red team, consolida **Q5** con la stessa regola usata per Q4 (OR dei `grounded`, si conserva
la risposta con piu' `source_ids` risolvibili).

**GATE 7-bis — Integrita' delle fonti.**

```
1. fabricated_sources non vuoto:
      → invalida TUTTI i claim prodotti dall'agente responsabile su quell'idea
      → ricalcola i gate a monte con i claim rimanenti
      → se un gate non regge piu'                                → KILL
      → registra in runs/<run_id>/integrity-incidents.jsonl
2. pass_rate < 0.6 con campione >= 5 coppie                       → PARK
      missing_to_decide = le coppie NOT_SUPPORTED o CONTRADICTED
3. altrimenti                                                     → PASS
```

Il ramo 2 non e' opzionale: un'idea con pass rate 0.2 e nessuna fonte formalmente fabbricata ha
comunque un dossier che non regge, e non deve arrivare al ranking come se nulla fosse.

---

## 8. WAVE 7 — RANKING

Una sola istanza di **ranking-agent** su tutte le idee sopravvissute insieme (serve la
comparazione). Nessuna ricerca.

Passagli: dossier, blueprint, i 3 report di red team, il verification report, i pesi da config.

**Verifica aritmetica.** Ricalcola tu stesso `final_score` dalla formula e confrontalo con quello
dichiarato. Se differiscono di piu' di 0.05, usa il **tuo** e annotalo.

**GATE 8 — Pubblicazione.** Ogni idea arrivata al ranking deve avere un esito. Nessuna puo' sparire.
La soglia si applica alla **somma pesata**; la confidenza e' una condizione separata, non un fattore
moltiplicativo.

```
somma pesata >= 6.5 AND confidenza >= 0.70 AND fontate >= 4/5
                     AND nessun KILL del red team                     → PUBBLICATA (max 4)
somma pesata >= 6.5 AND fontate < 4/5 AND evidence_route in {COMPORTAMENTO, DISALLINEAMENTO}
                                                            → VALIDAZIONE-SUL-CAMPO
somma pesata >= 6.5 AND (confidenza < 0.70 OPPURE fontate < 4/5)       → PARKED
      missing_to_decide = le domande rimaste non fontate
5.0 <= somma pesata < 6.5                                             → MENZIONE ONESTA
somma pesata < 5.0                                                    → KILLED
```

Il secondo ramo e' il caso che va gestito con piu' attenzione: **un'idea forte con evidenza
incompleta e' il segnale piu' utile che un run possa produrre.** Va parcheggiata con l'elenco delle
verifiche mancanti, non buttata e non pubblicata.

---

## 8-bis. L'ESITO `VALIDAZIONE-SUL-CAMPO`

Un'idea con `evidence_route` in {`COMPORTAMENTO`, `DISALLINEAMENTO`} che arriva al gate 8 con `final_score` sopra soglia ma
meno di 4/5 domande fontate **non e' un'idea debole: e' un'idea non documentabile da scrivania.**
Sono due cose diverse e la pipeline deve smettere di confonderle.

Questa e' anche l'unica risposta onesta al tetto strutturale del sistema: una ricerca web trova solo
opportunita' ricercabili sul web. Per un pain di nicchia con tracce di spesa reali e zero copertura,
cinque telefonate valgono piu' di duecentocinquanta ricerche.

Per queste idee non produci una scheda: produci un **kit di validazione**, che va nel report con la
stessa dignita' di un'opportunita' pubblicata.

```markdown
### [Nome] — VALIDAZIONE SUL CAMPO

**Perche' qui e non fra le pubblicate:** <quali tracce di comportamento reggono, quale
documentazione manca, e perche' non e' reperibile>

**Chi chiamare:** <profilo del ruolo, non nomi> · **Dove trovarlo:** <associazioni, consorzi,
fiere, gruppi professionali, elenchi pubblici — con fonte>

**Le cinque domande**
1. Chi fa oggi questo lavoro nella tua azienda, e quante ore ci mette a settimana?
2. Cosa succede quando va storto? (chiedere l'ultimo episodio concreto, non l'ipotesi)
3. Cosa avete provato finora, e perche' l'avete lasciato?
4. Se sparisse domani, cosa fareste al posto suo?
5. Chi firma una spesa di <cifra> per risolverlo, e quando l'ha fatto l'ultima volta?

**Soglia di promozione:** <su N interviste, quante risposte confermano spesa in corso e
frequenza — sotto quella soglia l'idea si uccide al run successivo, sopra si promuove>
```

## 9. ASSEMBLAGGIO DEL REPORT

Scrivi `runs/<run_id>/REPORT.md`. Questo e' l'unico artefatto in prosa dell'intera pipeline.

**Regola di scrittura:** ogni affermazione del report deve provenire da un campo di un JSON
validato. Non aggiungere contesto, colore o transizioni argomentative di tua invenzione. Se una
sezione non ha contenuto sostanziale e verificabile, scrivi `Nessun dato rilevante trovato`.

**Limiti vincolanti:** massimo `config.run.output_max_ideas` (4) idee; massimo
`config.run.output_max_words_per_idea` (400) parole per idea, tabelle escluse. Conta le parole. Non
allungare per completezza formale.

### Struttura

```markdown
# DEEPSEARCH — Report del <data>

Profilo: <profile> · Soglie: <threshold_profile> · Settori esplorati: <elenco>
Pain generati: <n> → evidenziati: <n> → oltre competitor: <n> → architettati: <n> → pubblicati: <n>
Ricerche totali: <n> distinte su <n> angoli · Fonti nel ledger: <n> · Spot check fonti: <n>, pass rate <x>%

## Sintesi degli scarti
<massimo 5 righe: i motivi principali per cui la maggior parte dei problemi e' caduta,
 con il conteggio per motivo. Es. "11 su 24 caduti al gate evidenze per assenza di spesa
 attuale documentata (G1)">

## Portafoglio del run
<SEMPRE PRESENTE. Le migliori 5 cose trovate, ordinate per profondita' di sopravvivenza.
 Non e' una classifica di opportunita': e' l'inventario di cosa questo run ha scoperto e
 di quanto e' costato scoprirlo. Si omette solo se i pain generati sono zero.>

| # | Pain | Gate | Esito | Perche' | Base | Chi ha detto no | Azione piu' economica | Costo |
|---|---|---|---|---|---|---|---|---|
<una riga per pain, max 5. "Gate" = profondita' raggiunta. "Base" = kill_basis
 (PRESENZA/ASSENZA). "Chi ha detto no" = kill_attribution (MONDO/STRUMENTO/MISTO).
 "Azione" = azione_sbloccante.descrizione. "Costo" = ricerche stimate, oppure
 "fuori scrivania" se non e' eseguibile da questa pipeline.>

<Sotto la tabella, una riga sola: "N dei M no di questo run sono attribuiti allo STRUMENTO."
 Se sono piu' della meta', aggiungere: "Il funnel ha misurato soprattutto se stesso.">

---

### [Nome Progetto / Concetto]

* **Problema e Target:** <ferita aperta + cliente ideale specifico>
* **Perche' Ora? (Timing):** <con fonte>

**Anti-Hallucination Check**
* Q1 <risposta> — [Fonte: S-xxx] oppure [Ipotesi non verificata]
* Q2 ... Q3 ... Q4 ... Q5 ...

**Fatti Verificati**
<solo claim VERIFIED, ogni riga termina con (Publisher, AAAA-MM). Fonti oltre 18 mesi:
 aggiungere " — fonte datata, verificare aggiornamento">

**Ipotesi**
<claim ESTIMATED o HYPOTHESIS, dichiaratamente non verificati>

**Incertezze & Invalidazione**
<UNKNOWN rilevanti + i finding non fatali del red team + cosa la farebbe fallire>

**Competitor**
| Competitor | Perche' e' sotto soglia (criterio specifico) | Gap di mercato |
|---|---|---|
<una riga per DIRETTO. Il criterio va citato per codice e valore osservato, es.
 "W6: nessuna API pubblica documentata (S-312)". Gli UNCLASSIFIED vanno elencati come tali.>

**Go-To-Market & Business**
* Trigger di acquisto: <con fonte>
* Target iniziale: <profilo, mai nomi inventati>
* Pricing: Starter | Pro | Business
* Scalabilita' verso 50.000 EUR/MRR: <clienti necessari, con numeri fontati o marcati
  "Stima non verificata: ...">

**Prodotto & Sviluppo**
* MVP (30 giorni)
* Uso dell'AI & Integrazioni
* Moat potenziale

**Red Teaming**
* Rischi / perche' potrebbe fallire
* Livello di fiducia [Alto/Medio/Basso] — motivato con i numeri del ranking, mai affermato

**Fonti non accessibili**
<elenco da unreachable_sources di tutti gli agenti su questa idea, con `source_id`, motivo e
impatto. Le tentate-e-rifiutate portano l'ID e stanno nel ledger col loro `access_status`; le mai
tentate (`BUDGET_EXHAUSTED`, `STOP_RULE`) sono lacune del run e non hanno ID. Se un claim
pubblicato poggiava solo su una di queste, qui va detto che e' stato declassato.>

---

## CLASSIFICA FINALE

| # | Progetto | Punteggio | Domande fontate | Confidenza | Pain econ. | Urgenza | Bootstrap | Distrib. | Competizione | MVP | Evidenze |
|---|---|---|---|---|---|---|---|---|---|---|---|

Ordine per punteggio finale. La formula e i pesi sono in config/deepsearch.config.json.

## Menzioni oneste (non pubblicate)
<idee fra 5.0 e 6.5, con il motivo dell'esclusione>

## Parcheggiate per il prossimo run
<idee PARKED, con l'elenco puntuale delle verifiche mancanti>

## Note di esecuzione
<downgrade applicati, violazioni di invarianti rilevate, incidenti di integrita' delle fonti,
 budget esaurito, wave interrotte. Questa sezione non si omette mai: e' il registro di
 affidabilita' del run.>
```

### Se nessuna idea e' pubblicabile

Il report si apre con:

> **Nessuna opportunita' ha superato i criteri in questo run.**

seguito dalla sintesi degli scarti, **dal portafoglio del run**, dalle menzioni oneste, dalle
parcheggiate e dalle note di esecuzione. **Non abbassare le soglie. Non promuovere la meno peggio.**
Questo esito e' il funzionamento corretto della pipeline, non un suo fallimento.

**Ma la frase non e' piu' tutto il report, ed e' la correzione della v2.15.** Fino alla v2.14.1 un
run senza pubblicazioni consegnava cinque righe di conteggio e buttava tutto il resto: le evidenze
raccolte, i competitor mappati, i gate attraversati, le `missing_to_decide` scritte con precisione.
Tre run reali su tre hanno prodotto zero idee al ranking, quindi **quattro etichette di esito su
cinque non sono mai state emesse** e il formato della scheda e' stato esercitato solo nel suo ramo
vuoto. Un sistema che consegna solo cose finite, in un dominio dove niente e' mai finito, consegna
niente.

Il portafoglio non allenta niente: `PUBBLICATA` resta `>= 6.5` con confidenza `>= 0.70` e 4/5
fontate, e nessuna voce del portafoglio puo' essere presentata come un'opportunita'. Cambia solo che
**il lavoro fatto smette di essere buttato**, e che il lettore vede la differenza fra «ho guardato e
non c'e' niente» e «mi sono fermato qui, e per ripartire servono tre ricerche».

**Distinzione da non perdere.** «Nessuna opportunita' ha superato i criteri» e' un'affermazione sul
**mercato** e la puo' fare solo un run `VALIDO`. Il portafoglio e' un'affermazione sul **run** e va
scritto sempre, anche — soprattutto — quando il run e' DEGRADATO: e' li' che dice quale parte dello
zero e' colpa dello strumento.

---

## 10. AGGIORNAMENTO DELLA MEMORIA

Appendi a `state/seen-pains.jsonl` un record per ogni pain toccato dal run, **conforme a
`schemas/seen-pain.schema.json`** — non a questo esempio:

```json
{"dedup_key":"edilizia e impiantistica|10-49|riconciliare ddt fatture","pain_id":"P-007",
 "run_id":"20260807-0412","outcome":"PARKED","stage":"competitor","profondita_raggiunta":3,
 "reason_code":"K5","kill_analysis":{"kill_basis":"PRESENZA","kill_attribution":"MONDO",
 "ramo_attivato":1,"stop_reason_osservato":"KILL_TRIGGERED","kill_emettibile":true},
 "missing_to_decide":["..."],"park_count":1,"park_reason_history":["K5"],
 "parked_at":"2026-08-07","retry_after":"2026-08-21","timestamp":"..."}
```

**Lo schema e' nuovo nella v2.15 e la sua assenza era il difetto D4.** Undici schemi coprivano gli
artefatti effimeri di un run e nessuno copriva l'unico che deve sopravvivergli — proprio quello da
cui dipendono cooldown, retry, riesumazione e `missing_to_decide` prioritarie, cioe' l'intera memoria
fra run. Un esempio JSON dentro un prompt non e' un contratto: il vocabolario degli `outcome` era
gia' divergito una volta (`PUBLISHED` contro `PUBBLICATA`), e `state/README.md` documenta ancora
quello sbagliato.

**`park_count` e `park_reason_history` sono obbligatori** e non sono contabilita': al **secondo**
PARK sulla stessa `missing_to_decide`, il pain **esce dal funnel di scrivania** e la sua
`azione_sbloccante` viene marcata `fuori_scrivania`. Senza questo contatore il ciclo
`parcheggio → 14 giorni → stessa ricerca → stesso parcheggio` e' possibile e **strutturalmente
invisibile**: produce un report normale ogni due settimane. Nel run `20260803-1204` P-023 e' andato
in PARK con `kill_attribution: STRUMENTO` e `missing_to_decide` che chiedeva testualmente *«budget
≥ 6 ricerche»*, e il retry a quattordici giorni lo avrebbe rieseguito con lo stesso budget. Il
sistema aveva prodotto un'autodiagnosi corretta e nessun meccanismo per agirvi.

Appendi a `state/sector-history.jsonl` i settori esplorati con il numero di pain prodotti e
pubblicati: alimenta la rotazione dei settori del run successivo.

Consolida `state/source-ledger.jsonl`: le fonti restano disponibili fra run e riducono il costo
delle verifiche future.

**Portafoglio ombra.** Appendi a `state/shadow-portfolio.jsonl` un record per ogni idea arrivata
almeno al gate 3 e poi uccisa:

```json
{"run_id":"20260807","pain_id":"P-032","concept":"...","killed_at_gate":7,
 "reason_code":"redteam-3/3","one_line_thesis":"...","icp":"...",
 "ricontrollare_dopo":"2027-02-07","esito_reale":null}
```

Non serve a questo run e non costa niente. Serve fra sei mesi: e' l'**unica misura di verita'** che
questo sistema possa avere. Quando `ricontrollare_dopo` scade, si guarda se qualcuno ha costruito
davvero un prodotto su quell'idea e si compila `esito_reale`. Dopo un anno hai il tasso di falsi
KILL — cioe' la sola cosa che dica se le soglie sono calibrate o sono opinioni. Finche' quel numero
non esiste, ogni soglia di questa pipeline e' un'ipotesi, incluse quelle scritte con piu'
sicurezza.

---

## 11. CHIUSURA E CONSEGNA

La chiusura ha **cinque passi in quest'ordine**, e l'ordine e' la sostanza del passo: la validita'
del run decide come si **apre** il report, ma il report e' anche uno degli artefatti verificati.
Chi verifica una volta sola deve scegliere quale delle due cose viene prima — e fino alla v2.7
questa sezione sceglieva di verificare *prima* di scrivere tre degli artefatti che verifica, il che
rendeva ogni run NON VALIDO per costruzione e teneva spenta la memoria fra run.

### Passo 1 — Artefatti tipizzati

Scrivi, in `runs/<run_id>/`, i sei artefatti che `run_close.py` pretende:

| file | contenuto |
|---|---|
| `00-run-manifest.json` | profilo, soglie, settori, agenti invocati, ora di inizio e fine |
| `00-run-stats.json` | i conteggi (sotto) |
| `downgrades.jsonl` | ogni declassamento VERIFIED→ESTIMATED→UNKNOWN applicato, con il motivo |
| `violations.jsonl` | ogni invariante IC-1..IC-4 rilevato come violato, con l'agente |
| `integrity-incidents.jsonl` | fonti irraggiungibili, contraddizioni fra fonti, spot check falliti |
| `dossier.json` | tutti i JSON validati del run (tracciabilita' completa) |

Un file **vuoto e' un esito legittimo** e va comunque creato: `downgrades.jsonl` a zero righe dice
"nessun declassamento", la sua assenza dice "non lo so", e sono due affermazioni diverse.

```json
{"run_id":"...","profile":"DEEP","searches":0,"fetches":0,"sources":0,
 "pains_generated":0,"pains_to_evidence":0,"pains_to_competitor":0,
 "ideas_architected":0,"published":0,"field_validation":0,"parked":0,"killed":0,
 "spot_check_pairs":0,"spot_check_pass_rate":null}
```

I numeri vanno **contati dai ledger e dagli artefatti**, mai ricordati. `searches` e' il numero di
righe di `state/query-ledger.jsonl`, `sources` quello di `state/source-ledger.jsonl`: sono i due
campi che il verificatore riconcilia, e se li scrivi a memoria fallisce. L'intestazione del report
si scrive da questo file. Un verificatore che deve indovinare la formattazione di una riga di prosa
e' fragile per costruzione — ed e' lo stesso errore che questa pipeline rimprovera ai propri agenti:
leggere campi tipizzati, non parsare frasi.

### Passo 1-bis — Riconciliazione degli artefatti con i ledger

**La riconciliazione non si esegue qui.** Dalla modalita' `m0.2` in poi essa avviene **una volta
sola**, al Passo 4, dentro il ramo `VALIDO senza violazioni`, e ne sono prodotti in quel punto la
diagnosi, l'esito e il sidecar. **Qui non si esegue alcun comando.**

Cio' che segue **descrive come leggere quella diagnosi** quando la produrra' il Passo 4, e resta la
definizione di che cosa distingue una fonte **recuperabile** da una **senza origine**.

**Conseguenza sull'ordine, da conoscere prima di chiudere:** poiche' la diagnosi arriva **dopo** il
cancello, il declassamento a `HYPOTHESIS` prescritto sotto **non e' piu' eseguibile nel punto in cui
questa sezione lo colloca**.

Ogni agente restituisce le fonti che ha raccolto in `new_sources` e le query in `queries_run`, e
**tu** le appendi ai ledger: e' scritto in §1 sotto "proprieta' dei ledger". Se una wave salta quel
passo, gli artefatti citano ID che nel ledger non esistono, IC-1 diventa inapplicabile a valle, e
nessuno se ne accorge — nel run 20260802-1759 il 30% delle citazioni era orfano e 147 ricerche su
332 non erano mai state consolidate, e il run ha chiuso VALIDO.

Lo strumento separa due cose che non sono la stessa:

- **recuperabili** — la fonte sta in un `new_sources` su disco. E' un guasto amministrativo:
  `--consolida` la appende al ledger senza rifare una sola ricerca.
- **senza origine** — l'ID compare in un claim e in nessun `new_sources` di nessun artefatto.
  Nessuno l'ha mai raccolta. Questa non si ripara: **declassa a `HYPOTHESIS` ogni claim che vi
  poggia**, e con esso i verdetti che ne discendono, registrando tutto in `downgrades.jsonl`.

Consolidare non e' assolvere: dopo `--consolida` il run torna tracciabile solo se le citazioni
senza origine sono zero.

### Passo 2 — Verifica preliminare
<!-- ancora: verifica-pre -->

```bash
python3 tools/run_close.py runs/<run_id> --fase pre
```

Sola lettura. In questa fase `REPORT.md` **non e' richiesto**: e' esattamente il file che stai per
scrivere. Restituisce `VALIDO | DEGRADATO | NON VALIDO`.

### Passo 3 — Report, con l'apertura che il verdetto impone
<!-- ancora: report -->

Scrivi `runs/<run_id>/REPORT.md` secondo §9, e scegli l'apertura in base al verdetto del passo 2:

- **VALIDO** → se nessuna idea e' pubblicabile, il report si apre con
  *"Nessuna opportunita' ha superato i criteri in questo run."* Quella frase e' un'affermazione sul
  mercato, e solo un run valido ha il diritto di farla.
- **DEGRADATO o NON VALIDO** → il report si apre con
  *"Run degradato: l'esito vuoto non e' interpretabile."*, seguito dall'elenco testuale delle
  post-condizioni fallite stampate da `run_close.py`. Riportale come le hai lette, non riassumerle.

Non e' burocrazia. Un report vuoto da run degradato e un report vuoto da funnel severo sono
indistinguibili a occhio, e il primo e' un guasto mentre il secondo e' il funzionamento corretto.
Senza questa distinzione il sistema non puo' dire quale dei due sta guardando — ed e' il difetto che
ha reso non interpretabile il collaudo del 20260801.

### Passo 4 — Verifica finale, e solo qui il cancello su `state/`
<!-- ancora: verifica-finale -->

```bash
python3 tools/run_close.py runs/<run_id> --fase finale
```

Ora `REPORT.md` esiste, l'intestazione viene riconciliata con i ledger e l'apertura viene
confrontata con il verdetto. Questa esecuzione, **e nessun'altra**, autorizza la scrittura in
`state/`:

<!-- ancora: scrittura-state -->
- **VALIDO senza violazioni** → esegui §10 e scrivi in `state/`. E qui, una volta sola, esegui la
  riconciliazione in modalita' telemetria `m0.2` — il flag va DOPO il percorso del run:

  ```bash
  python3 tools/reconcile_run.py runs/<run_id> --telemetry-m0.2
  ```

  Stampa la diagnosi del Passo 1-bis e, su stdout, la riga `telemetria m0.2:esito scrittura = ...`
  — cercala per quella stringa esatta, senza spazio dopo i due punti: **non e' l'ultima riga
  stampata**, dopo di essa restano le righe di chiusura della diagnosi.
  Riporta quell'esito nelle note di esecuzione come lo hai letto, insieme a cio' che lo strumento
  abbia eventualmente scritto su stderr. E' l'esito di QUESTA esecuzione: non se ne fa una seconda.
- **altrimenti** → **non scrivere in `state/`**. Un run degradato che aggiorna la memoria propaga il
  proprio guasto a tutti i run successivi, e nessuno se ne accorgera' perche' il guasto arrivera'
  travestito da dato storico.

Se il passo 4 rivelasse un'incoerenza fra apertura e verdetto — succede se hai riscritto il report a
mano dopo il passo 3 — correggi l'apertura e riesegui. Non toccare `run_close.py`.

### Passo 5 — Consegna

Consegna il report all'utente, dichiarando il verdetto di chiusura nella prima riga del messaggio di
accompagnamento. Se stai girando come task pianificato non interattivo, il verdetto va comunque
scritto nelle Note di esecuzione.

Se stai girando come task pianificato non interattivo, non porre domande: applica i default di
config, dichiara le assunzioni nelle Note di esecuzione e completa il run.
