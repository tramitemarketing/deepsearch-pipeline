# Red team di DEEPSEARCH v2.14.1

> **DOCUMENTO DERIVATO — non e' la specifica.** Questo file diagnostica e argomenta: le regole
> vincolanti vivono in `config/deepsearch.config.json` e in `.claude/commands/deepsearch.md`.
> In caso di divergenza vince la config, poi l'orchestratore. Le riparazioni descritte qui sono state
> applicate a quei file; questo documento resta come **verbale**, e i valori che cita sono quelli
> osservati **prima** della riparazione.

> Analisi avversariale condotta sui file della specifica **e sui tre run reali archiviati**
> (`20260801`, `20260802-1759`, `20260803-1204`), recuperati dalle copie di versione in `Downloads/`.
> `spec_check.py` su questa versione esce **0 errori, 11 avvisi, 24 controlli**: tutto ciò che segue
> passa pulito oggi.
>
> Questo documento **non ripete** `RIPENSAMENTO-8-LENTI.md`. Quel documento ha diagnosticato che il
> sistema non sa distinguere un no del mercato da un no dello strumento, ed è giusto. Qui si sostiene
> una tesi diversa e più a monte: **il sistema sa già distinguerli, e non serve a niente, perché non
> esiste nessuna azione che quella distinzione possa cambiare.**

---

## Parte I — La tesi

### 1. La funzione di perdita è invertita, ed è ereditata da un sistema che non esiste più

Ogni scelta di progetto di questa pipeline tratta il **falso positivo** — un'idea debole pubblicata —
come catastrofico, e il **falso negativo** — un'idea buona uccisa — come gratuito.

Non è un'impressione, è la struttura:

| Errore | Rilevatori nel sistema |
|---|---|
| **passata a torto** | `source-verifier` cieco · 3 lenti di red team che votano · moltiplicatore di confidenza · declassamento d'ufficio IC-1 · `precedenza_verdetto: KILL prima di UNCLASSIFIED prima di PARK prima di PASS` · soglia di pubblicazione · `min_confidence_to_publish` · 4/5 domande fontate |
| **uccisa a torto** | *nessuno* |

Il repo lo sa e lo scrive: *«nessun avversario sui KILL»* è in `MIGLIORIE.md` fra le cose aperte
**dalla v2.9**, cioè da sei versioni. L'unica misura prevista — il portafoglio ombra — richiede idee
che arrivino **oltre il gate 3 e poi muoiano**. Nei tre run reali quelle idee sono state **una**. Il
file è a zero byte.

Ora la domanda che nessuno ha posto: **quanto costa davvero un falso positivo?**

Costa che Giovanni legge una scheda da 400 parole, con sopra scritto `confidenza 0.61` <!-- spec-check: ignora -->
e `evidence_route: COMPORTAMENTO`, e decide di non costruirla. Dieci minuti. **L'ultimo gate della
pipeline è un essere umano che legge, ed è gratuito, veloce e già informato.**

Quanto costa un falso negativo? Tutto. Silenziosamente, senza traccia, e con l'aggravante che il
sistema esiste esclusivamente per evitarlo.

**L'asimmetria reale è l'opposto esatto di quella costruita nella macchina.**

E qui sta la parte interessante: **quella funzione di perdita non è sbagliata, è scaduta.** Era
corretta per il prompt monolitico. Lì un falso positivo *era* catastrofico: quattro idee allucinate,
scritte con la stessa sicurezza di quelle vere, **senza nessuna etichetta che le distinguesse**.
Contro quello, l'unica difesa possibile era non produrle.

Poi questa pipeline ha costruito le etichette. Quattro stati. `evidence_route`. I tier con le regole
di promozione. Il moltiplicatore di confidenza. Lo snippet per coppia. Il verificatore cieco.
`kill_basis`. **Il sistema di etichettatura rende obsoleta la soppressione**: oggi si può mostrare
un'idea debole scritta come debole, e il lettore non viene ingannato — che era l'unico danno che la
soppressione doveva impedire.

Nessuno ha ritirato la difesa vecchia. Il sistema ha tenuto la difesa **e** ha aggiunto la cura, e
adesso la difesa blocca la cura.

> **`QUALITÀ SOPRA QUANTITÀ` era una regola contro la sicurezza ingiustificata.
> È stata implementata come una regola contro l'output. Sono due cose diverse, e solo la prima è
> difendibile.**

Questa è la risposta alla critica *«bravo a eliminare, non a far andare avanti»*. Non è un difetto
di taratura delle soglie. È che il sistema sta ancora combattendo il monolite del 2025, con
l'armamentario giusto per un avversario che ha già ucciso.

### 2. Non esiste nessun artefatto fra «niente» e «un'idea completa»

Il vocabolario degli esiti — `PUBBLICATA`, `VALIDAZIONE-SUL-CAMPO`, `MENZIONE-ONESTA`, `PARKED`,
`KILLED` — è definito **solo per le idee che arrivano al `ranking-agent`**.

Nei tre run reali, le idee arrivate al ranking sono **zero**. Il `ranking-agent` non è mai stato
invocato una volta. Quindi **quattro etichette su cinque non sono mai state prodotte**, e il formato
del report è stato esercitato esclusivamente nel suo ramo vuoto.

Un'idea che muore al gate 2 e un'idea che muore al gate 7 con quaranta claim `VERIFIED` producono lo
stesso identico artefatto: **una riga nella sintesi degli scarti**. Non c'è credito parziale. Non c'è
gradiente.

Un sistema che consegna solo cose finite, in un dominio dove niente è mai finito, consegna niente.

### 3. Il caso P-023 è la dimostrazione in miniatura, e va letto bene

Nel run `20260803-1204`, il pain P-023 va in `PARK` con questa motivazione — scritta dal sistema, non
da me:

> `kill_basis: ASSENZA` · `kill_attribution: STRUMENTO`
> *«Un `language_of_the_problem` vuoto dopo 3 ricerche interrotte dal budget non dice che il target
> non ha parole per il problema: dice che non le abbiamo trovate.»*

È una diagnosi **perfetta**. Il sistema ha capito esattamente cosa è andato storto, ha capito che la
colpa era sua e non del mercato, e lo ha scritto in un campo tipizzato.

Poi ha fissato `retry_after: 2026-08-17` — quattordici giorni — **e non ha cambiato niente.** Al
rientro, `config` concede allo stesso canale lo stesso budget, e il pain produce lo stesso PARK con
la stessa motivazione. `missing_to_decide` dice testualmente *«rieseguire il canale community con
budget ≥ 6 ricerche»*, e **nessun componente del sistema legge quel campo per cambiare un budget.**

> **Il sistema ha prodotto un'autodiagnosi corretta, precisa e azionabile, e non ha nessun
> meccanismo per agire su di essa.** `retry_after` è un timer, non un piano.

Questo è «bravo a eliminare, non a far avanzare» in sette righe di JSON. E nota che la proposta 1 del
documento a 8 lenti — rendere lo zero interpretabile — **qui ha già funzionato**. Lo zero *è*
interpretato. Non è servito a niente, perché l'interpretazione non ha un destinatario.

### 4. Il filtro più potente del sistema non è un gate, ed è tarato sull'asse dichiarato sbagliato

Nel run `20260803-1204`: **8 pain su 14 muoiono al taglio wave 1 → 2.** Nessun gate li ha visti. Nel
run `20260801`: 12 su 15. È il canale di perdita dominante di tutta la pipeline, con distacco.

Quel taglio ordina per (`config.funnel.ordinamento_dei_tagli`):

```
0.45 perdita economica documentata (VERIFIED > ESTIMATED > UNKNOWN)
0.35 evidenza di spesa attuale
0.20 ricorrenza
```

`evidence_kind` — cioè l'intera intuizione della v2.1, l'asse che distingue *«qualcuno ha scritto che
accade»* da *«qualcuno ha speso perché accada»* — **non compare.** Il 45% del peso sta su
`VERIFIED > ESTIMATED > UNKNOWN`, che è una scala di **documentabilità pura**.

Il repo ha capito che il gate 2 filtrava per copertura mediatica e lo ha riparato con
`evidence_kind`, PASS-B, ASSENZA. Ha lasciato intatto il filtro che uccide **cinque volte più
pain di quel gate**. E lo sapeva: `_nota_ordinamento` dice testualmente *«il gate non taglia il
funnel mentre l'ordinamento sì»* — e poi corregge solo l'ordine relativo di ricorrenza e perdita,
senza accorgersi che l'asse è quello sbagliato per intero.

Aggravante: quel taglio è applicato **prima che qualsiasi `evidence-researcher` abbia girato**. Una
«perdita economica documentata `VERIFIED`» alla wave 1 significa che il `pain-finder` è inciampato in
una cifra dentro 8 ricerche. Pesa 0.45, e misura in buona parte la fortuna dello shard.

### 5. Le due regole d'oro sono in conflitto, e vince sempre la stessa

`FOLLOW THE MONEY` dice: cerca i problemi su cui le aziende già spendono. Quelle tracce di spesa
stanno nelle nicchie **poco documentate** — è il punto.

`QUALITÀ SOPRA QUANTITÀ`, come implementata, dice: accetta solo ciò che è documentato abbastanza da
superare otto gate di scrivania.

Sono in contraddizione diretta, e la storia del repo è la storia di questa contraddizione risolta
cinque volte a favore della seconda e poi rattoppata:

| versione | rattoppo | cosa riapriva |
|---|---|---|
| v2.1 | `evidence_kind` + PASS-B | il gate 2 scartava le nicchie poco coperte |
| v2.3 | rotta `DISALLINEAMENTO` | il gate 3 uccideva i mercati con software sano |
| v2.6→2.7 | `VALIDAZIONE-SUL-CAMPO` spostata al gate 2 | era dietro sette filtri documentali, irraggiungibile |
| v2.13 | `FP0` | non esisteva l'etichetta per «non promosso» |
| v2.14 | rotta `ASSENZA`, rami 6-7 | il gate 3 uccideva il bersaglio primario della missione |

Cinque riparazioni, tutte corrette, tutte sullo **stesso** difetto: un criterio di scrivania che
esclude ciò che per definizione non sta sulla scrivania. Quando la stessa classe si ripresenta cinque
volte in tredici versioni, non è una serie di bug: **è la tesi del sistema che è in conflitto con la
sua implementazione**, e ogni rattoppo la sposta di un gate senza risolverla.

---

## Parte II — I difetti concreti

Tutti verificati eseguendo. Tutti invisibili a `spec_check` oggi.

### D1 — I tetti di budget sono sotto la somma teorica in LIGHT e BALANCED — **bloccante**

`funnel._nota_budget` enuncia l'invariante e ne descrive la conseguenza:

> *«`max_searches_total` va tenuto **SOPRA** la somma teorica dei budget per istanza. Se lo si abbassa
> sotto, la pipeline si interrompe a metà e produce un report vuoto che sembra un esito legittimo: è
> **il guasto più insidioso possibile**.»*

Somma teorica ricalcolata dai budget per istanza di questa versione:

| profilo | somma teorica | `max_searches_total` | freno all'80% |
|---|---|---|---|
| **LIGHT** | **422** | **300** ❌ | 240 |
| **BALANCED** | **828** | **700** ❌ | 560 |
| DEEP | 1576 | 1600 ✅ (+1,5%) | **1280** ❌ |

Due profili su tre violano l'invariante direttamente. Il terzo lo rispetta di un millesimo e poi lo
viola comunque attraverso il freno all'80%, che smette di aprire idee a 1280 — cioè **prima della
fine della wave 3**.

Causa: la v2.14 ha alzato `evidence_searches_per_channel` da 3-4 a **8** in tutti i profili (24 × 5 ×
8 = 960 ricerche solo di evidenze in DEEP) e ha alzato **solo** il tetto di DEEP. `_calcolo_teorico`
in config dichiara ancora *«DEEP ~1050 ricerche, BALANCED ~580, LIGHT ~265»*: sono i numeri del mondo
**prima** della v2.14.

È esattamente il difetto **S5** che `MIGLIORIE.md` dichiara chiuso (*«Budget incoerente… Ricalcolati
i tetti a partire dai budget per istanza»*). È tornato, riaperto da una riparazione, e `SC-11` —
*«aritmetica di funnel e scoring»* — passa pulito.

**LIGHT è il profilo della sentinella settimanale.** Il guasto che il sistema descrive come il più
insidioso che conosca è attivo, oggi, sul profilo che dovrebbe girare più spesso.

### D2 — Il gate 2 può uccidere dalla v2.14, e due file affermano il contrario — **bloccante**

`config.stop_rules.budget_minimo_perche_un_kill_sia_emettibile` = `3 × zero_yield_window`.
`zero_yield_window` = `max(2, floor(budget/3))`.

Con `evidence_searches_per_channel = 8`: finestra = 2, minimo = **6**, budget = **8**. **8 ≥ 6 in
tutti e tre i profili: i KILL del gate 2 sono emettibili.**

Ma:

- **orchestratore §1**, regola di arresto 3: *«Con i budget attuali questo vale per
  l'`evidence-researcher`, e ne segue che i KILL del gate 2 (G1, G3) diventano PARK.»* — **falso**;
- **config `stop_rules._conseguenza_gate_2`**: *«l'`evidence-researcher` ha 3-4 ricerche per canale e una finestra di 2, quindi 3x la finestra fa 6 e NESSUN profilo glielo concede»* <!-- spec-check: ignora -->
  — **falso, e contraddice `funnel._nota_gate2` nello stesso file**, che spiega perché il valore è
  stato portato a 8. *(Citazione del testo rimosso. È la riga su cui `SC-26` ha sparato al primo
  giro, prima ancora che fosse cancellata dal config: il controllo non sa che sta leggendo una
  citazione, e questo è il motivo per cui l'esenzione per riga esiste.)*
- **config `stop_rules._come_riabilitare_gate_2`**: *«la strada… è alzare
  `evidence_searches_per_channel` a 6»* — **già fatto, a 8**.

Tre note nello stesso repo descrivono tre stati del mondo diversi, e quella che l'orchestratore legge
come istruzione operativa è quella sbagliata. L'orchestratore del run `20260803` ha infatti applicato
la regola stantia e mandato P-023 in PARK dichiarando *«il KILL non era emettibile»* — su un run
LIGHT che allora aveva budget 3, quindi era corretto **allora**. Oggi non lo è più, e nessuno lo ha
propagato.

Effetto: il sistema si è tolto da solo l'unico gate capace di uccidere per assenza di spesa, e non lo
sa.

### D3 — `kill_basis` e `kill_attribution` non esistono in nessuno schema e nessuno strumento li legge

I due campi che dovrebbero rendere interpretabile ogni no del sistema:

- sono **prescritti** dall'orchestratore §1, regola di arresto 4 (*«Registra su ogni transizione di
  stato…»*);
- sono **citati** in `config.run.condizione_di_riprogettazione` come base per decidere se il difetto
  è nella pipeline o nel bersaglio;
- **non compaiono in nessuno degli 11 schemi** (`grep` su `schemas/*.json`: zero risultati);
- **non sono letti da `run_close.py`, `reconcile_run.py` né da alcun altro strumento** (`grep` su
  `tools/*.py`: zero risultati).

Nel run reale finiscono in `02-evidence/GATE2-decisioni.json` — un file che l'orchestratore si è
inventato, che nessuno schema valida e che nessun programma rilegge.

È la **quinta occorrenza** della classe di difetto che questo repo ha già trovato quattro volte —
competitor `STRONG`, `FP0`, gate 3 `G7`, IC-4 senza `source_id`, `HYPOTHESIS` fuori dagli enum: *un
obbligo enunciato senza lo slot per assolverlo*. Stavolta cade sul campo progettato per misurare se i
no del sistema siano affidabili, cioè **sull'unico strumento diagnostico che il sistema possiede su
se stesso**.

`condizione_di_riprogettazione` promette: *«si legge la distribuzione di `kill_attribution` sui 5
run»*. Quella distribuzione non è calcolabile da nessun programma.

### D4 — Il record di memoria, da cui dipende tutto, è l'unico artefatto senza schema

Il record di `seen-pains.jsonl` è definito **solo come esempio JSON nell'orchestratore §10**.
`reason_code` compare in un solo schema (`pain.schema.json`), e non in quel contesto. Non esiste
`seen-pain.schema.json`.

Da quel record dipendono: cooldown dei `KILLED` (90 giorni), retry dei `PARKED` (14),
esclusione dei pubblicati (365), riesumazione allo stage corretto, `missing_to_decide` come input
prioritario. Cioè **tutta la memoria fra run** — la miglioria A3, e il prerequisito dichiarato della
proposta 4 del documento a 8 lenti.

Il sistema ha undici schemi per gli artefatti effimeri di un run e zero per l'unico che deve
sopravvivergli.

### D5 — Difetti minori, verificati

- **`_calcolo_teorico`** in config è stantio (descrive i budget pre-v2.14). Alimenta D1.
- **`state/README.md` documenta ancora `PUBLISHED`**, esito che nessun vocabolario ammette.
  `MIGLIORIE.md` v2.8.1 dichiara questo difetto chiuso. È vivo, e compare come avviso `SC-05`
  **numero 4 su 11**: il canale degli avvisi è già abbastanza rumoroso da nascondere un difetto vero,
  che è la patologia che `SC-02` è stato spostato in un canale a parte per evitare.
- **`sectors_per_run: 6` ma LIGHT ha 4 shard**, e ogni shard riceve una sola coppia (settore,
  angolo): i settori esplorabili in LIGHT sono **4**. Rilevato dall'orchestratore *durante* il run
  `20260803` e scritto nelle sue Note di esecuzione. Mai riportato in config.
- **`min_competitors_to_map` e `max_competitors_to_map` sono dichiarate in
  `costanti_rispecchiate` e mai nominate in prosa** (avviso `SC-03`): il contratto promette un
  controllo che non esiste. 4 costanti su 12 sono in questo stato.

---

## Parte III — Cosa farne

### La riparazione strutturale: il report non può essere vuoto

Non si tocca nessuna soglia. Non si abbassa niente. Si cambia **cosa il sistema è autorizzato a
consegnare**.

Oggi il report ha un solo modo di essere non vuoto: un'idea che supera otto gate, arriva al ranking e
fa ≥ 6.5. Mai successo. Il resto — le idee morte al gate 2, quelle morte al gate 3, quelle troncate
dal taglio wave 1 — non ha nessuna rappresentazione, se non un conteggio aggregato in cinque righe.

**Proposta: il portafoglio per profondità di sopravvivenza.**

Il report si apre **sempre** con le migliori N cose trovate in questo run, ordinate per:

1. gate massimo raggiunto,
2. a parità, il codice di caduta,
3. a parità, claim `VERIFIED` accumulati.

Ogni voce porta tre cose e nessuna in più: **dove è morta**, **perché** (con `kill_basis` e
`kill_attribution`, che finalmente hanno un lettore), e **l'azione più economica che la
sbloccherebbe** — che è `missing_to_decide`, un campo che il sistema già scrive e nessuno legge.

Cosa **non** cambia, ed è il punto:

- `PUBBLICATA` resta ciò che è: ≥ 6.5, confidenza ≥ 0.70, 4/5 fontate, nessun KILL del red team.
  Nessuna idea diventa un'opportunità senza averlo meritato.
- La frase *«Nessuna opportunità ha superato i criteri in questo run»* **resta dov'è**, e resta
  obbligatoria. Non si pubblica la meno peggio: si dichiara cosa si è trovato e quanto poco se ne sa.
- Nessun gate viene allentato. Nessuna soglia viene toccata. Il funnel resta severo esattamente
  com'è.

L'unica cosa che cambia è che **il lavoro fatto smette di essere buttato**. Un run che genera 14
pain, ne evidenzia 2 e ne porta 1 al gate 3 ha prodotto informazione reale; oggi la consegna come
cinque righe di conteggio e la butta.

Questo è il recupero della parte valida della proposta *«classifica permanente»* scartata dal
verificatore del documento a 8 lenti — che l'aveva scartata per il **punteggio parziale
rinormalizzato**, giustamente, e aveva salvato testualmente proprio questo: *«ordinare il portafoglio
per PROFONDITÀ DI SOPRAVVIVENZA (gate massimo raggiunto, a parità il codice di caduta, a parità i
claim VERIFIED accumulati), che è confrontabile fra run senza nessuna rinormalizzazione»*.

E risponde alla critica nella sua forma esatta: **lo zero smette di esserci.** Non perché le soglie
si abbassano, ma perché il sistema smette di confondere *«nessuna idea ha superato i criteri»* con
*«non ho niente da dirti»*.

### Cosa questo NON risolve, dichiarato

Il portafoglio rende il run leggibile. **Non alza il soffitto.** Il soffitto sta nella wave 1 — 3
pain per shard su un tetto di 12 — e nel fatto che una ricerca web trova solo opportunità
ricercabili sul web. Per quello servono la proposta 7 (lessico + tassonomia degli errori) e, sopra
tutto, un'ora di telefono che nessuna versione di questo software può fare al posto tuo.

E resta intatta la critica di completezza numero 1 del documento a 8 lenti, che nessuna delle
riparazioni qui proposte tocca: **il tasso di conversione idea → prodotto costruito non compare in
nessun file**, e se vale zero, tutto il resto è taratura della mira di un cannone senza polvere.
