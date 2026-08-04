# DEEPSEARCH — Regole globali della pipeline

Questo file e' letto automaticamente da ogni agente della pipeline. Contiene gli **invarianti**:
regole che nessun agente puo' violare, reinterpretare o "ottimizzare via".

Se un'istruzione locale di un subagent contraddice questo file, **vince questo file**.

---

## 0. Le due regole d'oro (ereditate dal prompt originale, non negoziabili)

**QUALITA' SOPRA QUANTITA'.** Non si ottimizza per numero di idee. Se dopo un'analisi rigorosa
nessuna opportunita' soddisfa i criteri, lo si dichiara esplicitamente. Un output con zero idee e
una spiegazione onesta e' un run **riuscito**. Un output con quattro idee mediocri e' un run
**fallito**, anche se sembra piu' ricco.

**FOLLOW THE MONEY.** Si cercano PMI europee (primariamente italiane) che perdono denaro ogni
settimana per processi inefficienti. Non si inventano bisogni. Si preferiscono sempre problemi per
cui le aziende **spendono gia' oggi** soldi, tempo o personale — perche' quella spesa e' la prova
che il budget esiste.

---

## 1. Separazione dei poteri: chi cerca non conclude, chi conclude non cerca

E' l'invariante architetturale piu' importante dell'intera pipeline. La causa dominante delle
allucinazioni in un prompt monolitico e' il **retrieval motivato**: lo stesso modello che vuole
produrre un'idea brillante e' anche quello che decide quali fonti bastano. Qui i due ruoli sono
separati per costruzione.

| Classe | Agenti | Puo' fare ricerche | Puo' esprimere conclusioni |
|---|---|---|---|
| **Retrieval-only** | `evidence-researcher`, `competitor-analyst`, `trigger-finder`, `tam-analyst`, `source-verifier` | SI | **NO** |
| **Judgment-only** | `bootstrap-filter`, `venture-architect`, `ranking-agent` | **NO** | SI |
| **Generativo vincolato** | `pain-finder` | SI | Solo candidati, mai validazioni |
| **Avversariale** | `red-team` | SI | Solo verso la falsificazione |

Regole operative che ne discendono:

- Un agente **retrieval-only** che scrive "questa e' una buona opportunita'", "il mercato e'
  promettente", "il competitor e' battibile" ha **fallito il proprio task**, anche se i fatti
  raccolti sono corretti. Deve produrre fatti e basta.

  La garanzia non e' un elenco di parole vietate. Un elenco in italiano si aggira senza nemmeno
  volerlo ("richiede tempo significativo"), e soprattutto non intercetta il canale principale con
  cui un giudizio entra in un output di soli fatti: la **selezione**. Cinque fatti veri scelti tutti
  a favore sono gia' una conclusione, con zero aggettivi.

  La garanzia e' **strutturale**: `claim.statement` non e' testo libero ma un record con slot —
  `subject`, `observed`, `quantity`, `period`, `geo`. Non esiste un campo dove scrivere
  "significativo": la quantita' o e' un numero o e' `UNKNOWN`. L'unico testo libero superstite e'
  `verbatim_quote`, che per definizione e' copiato dalla fonte e verificabile carattere per
  carattere.

  Il controllo lessicale dell'orchestratore resta, ma come **linter di telemetria**: misura la
  sciatteria di un agente nel tempo e alimenta le note di esecuzione. Non e' una difesa e non va
  contato come tale.
- Un agente **judgment-only** che cita una fonte non presente nel `source-ledger` ha **fabbricato
  una prova**. E' il fallimento piu' grave possibile in questa pipeline. Non ha strumenti di
  ricerca: se gli manca un dato deve scrivere `UNKNOWN` e richiedere un supplemento istruttorio
  all'orchestratore.
- Il `red-team` non migliora mai l'idea. Se trova una soluzione a un problema che ha individuato,
  la tiene per se': il suo unico mandato e' produrre motivi per scartare.

---

## 2. Quattro stati, nessuna sfumatura

Ogni affermazione prodotta dalla pipeline ha esattamente uno di questi stati:

- **VERIFIED** — sostenuta da fonti che soddisfano le `fact_promotion_rules` in `config/deepsearch.config.json`.
- **ESTIMATED** — stima dichiarata, con metodo di calcolo esplicito e input tracciabili.
  Formato obbligatorio: `Stima non verificata: <numero> — ottenuta da <formula/ragionamento>, input da [S-xxx], nessuna fonte diretta trovata.`
- **HYPOTHESIS** — plausibile ma non sostenuta. E' anche lo stato in cui finisce d'ufficio ogni
  `VERIFIED` che non supera il controllo di citazione (invariante IC-1).
- **UNKNOWN** — non e' stato possibile determinarlo.

`UNKNOWN` e `HYPOTHESIS` sono valori di **prima classe**. Scriverli non e' una debolezza: sono
l'output corretto quando il dato non c'e' o non regge. Sostituire un `UNKNOWN` con una plausibilita'
non dichiarata e' la violazione piu' comune e piu' costosa.

Non esiste un quinto stato intermedio, e non esiste "probabile".

## 2-bis. Due assi, non uno: il tier dice se e' sostenuto, `evidence_kind` dice se e' pagato

Il tier di una fonte risponde a "questa affermazione e' sostenuta?". **Non** risponde a "questo
problema esiste e qualcuno ci spende sopra?". Fondere le due domande fa ottimizzare il funnel per la
copertura mediatica, che e' esattamente cio' che rende una nicchia gia' presidiata — cioe' il
contrario di quello che cerchiamo.

Ogni fonte porta quindi anche un `evidence_kind`:

- **PUBBLICAZIONE** — qualcuno ha scritto che accade. Promozione secondo i tier.
- **COMPORTAMENTO** — qualcuno ha **speso** perche' accada: un annuncio di lavoro per quel compito,
  un capitolato che lo impone, il listino di chi vende il rimedio, un bando dedicato. Promozione con
  2 tracce convergenti da attori diversi e indipendenti.
- **ARTEFATTO** — esiste un oggetto che presuppone il problema: un modulo, un portale, la FAQ di un
  ente, un corso su quel compito. Promozione con 1 artefatto + 1 traccia d'uso.

Un annuncio di lavoro e' una fonte T3 debole come pubblicazione e una prova forte come
comportamento: e' una decisione di spesa con uno stipendio attaccato. Il gate 2 passa con evidenza
di pubblicazione **oppure** di comportamento.

**Vietato ovunque:** "secondo ricerche di settore", "e' noto che", "generalmente", "studi
dimostrano", "molte aziende", "il mercato e' stimato in" senza fonte. Ogni frase di questo tipo
viene rimossa meccanicamente dall'orchestratore prima della pubblicazione.

---

## 3. Source Ledger: le fonti sono oggetti, non stringhe

Nessun agente cita una fonte "in prosa". Ogni fonte incontrata viene registrata in
`state/source-ledger.jsonl` come oggetto con ID, e da quel momento si cita **solo per ID**.

```
S-<run_id>-<progressivo>   es. S-20260807-041
```

Ogni record contiene: `url`, `publisher`, `title`, `published_date`, `accessed_at`, `tier`
(T1..T5), `access_status` (`OK` | `PAYWALL` | `BLOCKED` | `NOT_FOUND` | `ROBOTS`), `snippet`
(citazione testuale che sostiene il claim, max 300 caratteri), `retrieved_by` (nome agente).

**Invariante IC-1 (citazione).** Ogni statement `VERIFIED` deve avere almeno un `source_id`
risolvibile nel ledger. L'orchestratore verifica questo **meccanicamente**, non a giudizio: uno
statement `VERIFIED` senza `source_id` valido viene declassato d'ufficio a `HYPOTHESIS`. L'agente
non puo' opporsi.

**Invariante IC-2 (data).** Ogni fonte deve avere una data. Se la data non e' determinabile, il
campo vale `UNKNOWN` e la fonte **non puo' sostenere numeri di mercato o pricing**, solo contesto.
Fonti oltre 18 mesi vanno marcate `stale: true` e riportate come "fonte datata, verificare
aggiornamento". Oltre 36 mesi non sono utilizzabili per dimensionamenti.

**Invariante IC-3 (snippet per coppia).** Lo snippet appartiene alla coppia **(claim, fonte)**, non
alla fonte: vive in `claim.evidence[]`. Una fonte che sostiene tre claim porta tre snippet diversi.
Riusare lo stesso passaggio per claim diversi soddisfa la regola alla lettera e la tradisce nella
sostanza — succede continuamente e rende il controllo inutile.

Ogni snippet dichiara la propria provenienza: `snippet_source: SERP` (estratto dai risultati di
ricerca, senza aprire la pagina) oppure `FETCHED` (letto sulla pagina). **SERP e' legittimo e non e'
una colpa**: costa zero fetch, e il verificatore controlla per primi proprio quelli, che sono i piu'
esposti.

Attenzione a cosa questo invariante fa e non fa. Blocca l'URL inventato di sana pianta. **Non**
blocca la citazione vera ma decontestualizzata, ne' il perimetro slittato — un modello sa produrre
uno snippet plausibile da un titolo. La difesa contro quelle patologie e' il verificatore cieco
(§ agente `source-verifier`), non questo invariante. Lo snippet serve a rendere quel controllo
**meccanico ed economico**: confronto di stringa invece di giudizio.

**Invariante IC-4 (accessibilita').** Ogni fonte che si voleva consultare ma non e' stato possibile
raggiungere va comunque registrata con il proprio `access_status` e finisce nella sezione **Fonti
non accessibili** del report. Non esiste l'omissione silenziosa.

IC-4 ha due meta' e per undici versioni non si toccavano. La dichiarazione (`unreachable_sources`)
non aveva un identificatore, la registrazione nel ledger lo pretende: la fonte dichiarata secondo
IC-4 non risolveva secondo IC-1, e il run veniva marcato DEGRADATO **per aver fatto la cosa
giusta**. Un sistema che punisce l'unico comportamento onesto disponibile insegna a tacere. Ora la
dichiarazione porta `source_id`, e la distinzione che regge tutto e' fra due famiglie di motivi:

- **tentata e rifiutata** (`PAYWALL`, `BLOCKED`, `NOT_FOUND`, `ROBOTS`, `API_LIMIT`,
  `LOGIN_REQUIRED`, `NO_PUBLIC_DATA`) — qualcuno ha bussato. La fonte esiste, ha un URL, prende un
  ID e va nel ledger col suo `access_status`. `source_id` **obbligatorio**.
- **mai tentata** (`BUDGET_EXHAUSTED`, `STOP_RULE`) — nessuno ha bussato. Non c'e' nessun oggetto
  fonte, perche' non e' stato recuperato niente: e' una lacuna dichiarata del run, non una fonte.
  `source_id` **vietato**.

E il contrappeso, senza il quale la riparazione sarebbe un peggioramento: una fonte irraggiungibile
e' provenienza valida per l'**esistenza** della fonte e per nient'altro. **Non puo' sostenere da
sola uno stato `VERIFIED`** — IC-3 pretende uno snippet per la coppia (claim, fonte), e su una
pagina che nessuno ha aperto non c'e' niente da citare. Un claim i cui `source_ids` sono *tutti*
irraggiungibili viene declassato a `HYPOTHESIS` d'ufficio, esattamente come in IC-1.

---

## 4. Query Ledger: nessuna ricerca viene fatta due volte

Prima di eseguire una ricerca, ogni agente normalizza la query (minuscolo, senza punteggiatura,
termini ordinati alfabeticamente) e controlla `state/query-ledger.jsonl`. Se la query normalizzata
esiste gia' per il run corrente, **riusa il risultato registrato** invece di rieseguirla.

Ogni ricerca eseguita viene appesa al ledger con: `normalized_query`, `raw_query`, `agent`,
`timestamp`, `n_results`, `source_ids_estratti`.

**Attenzione: riusare un DOCUMENTO non e' come riusare un RISULTATO.** Sono due cose diverse e solo
la prima e' lecita.

- **Cache di documenti** (`state/doc-cache/`, per URL canonicalizzato): un documento gia' scaricato
  in questo run puo' essere riletto senza spendere una fetch. Non rivela a nessuno cosa hanno
  cercato gli altri.
- **Riuso di risultati di ricerca**: passare a un'istanza la SERP ottenuta da un'altra. **Vietato
  fra istanze parallele della stessa wave.** Un canale che riceve i risultati di un altro canale
  puo' dichiarare saturazione per merito altrui, e la cecita' salta — proprio la cecita' da cui il
  sistema deriva la validita' del consolidamento di Q4, del voto di maggioranza del red team e della
  diversita' del fan-out. Una difesa che si annulla da sola in un'altra sezione dello stesso
  documento non e' una difesa.

Il ledger serve quindi a **non rieseguire la stessa query** (l'agente la salta e lo dichiara) e a
rendere il run auditabile — non a far circolare risultati. Fra wave diverse e fra run diversi il
riuso e' pieno: li' l'indipendenza non e' in gioco.

Questo serve a tre cose: non sprecare budget, rendere l'esecuzione **auditabile** (si puo'
ricostruire esattamente cosa e' stato cercato), e permettere all'orchestratore di verificare il
vincolo "minimo 5 ricerche distinte per idea pubblicata" contando query **realmente diverse fra
loro**, non riformulazioni della stessa.

---

## 5. Stati di un'idea

```
CANDIDATE → SCREENED → EVIDENCED → COMPETITIVE_CLEAR → TRIGGERED
          → SIZED → BOOTSTRAPPABLE → ARCHITECTED → SURVIVED_REDTEAM → RANKED
```

piu' due stati terminali/sospensivi:

- **KILLED(motivo, stage, evidenza)** — scartata. Va nel registro degli scarti con il motivo
  specifico e l'ID del claim che l'ha uccisa. Non rientra per `killed_pain_cooldown_days`.
- **PARKED(dati_mancanti, retry_after)** — non abbastanza informazioni per decidere. **Non e' un
  KILL.** Rientra nel run successivo con una lista puntuale di verifiche da fare. Questo stato non
  esisteva nel prompt monolitico, che era costretto a scegliere fra assumere "battibile" e buttare.

Il passaggio di stato e' deciso **solo dall'orchestratore**, mai da un subagent. Un subagent
produce un verdetto e le sue motivazioni; l'orchestratore applica il gate.

---

## 6. Formato di scambio: JSON, sempre

Ogni subagent riceve JSON e restituisce JSON conforme al proprio schema in `schemas/`.
Nessun subagent produce prosa destinata all'utente finale: la prosa la scrive **solo**
l'orchestratore, nell'ultimo passo, a partire dai JSON validati.

Ogni output di agente ha un envelope comune:

```json
{
  "agent": "competitor-analyst",
  "run_id": "20260807",
  "pain_id": "P-003",
  "status": "OK | PARTIAL | FAILED",
  "budget_used": { "searches": 9, "limit": 10 },
  "stop_reason": "COMPLETED | BUDGET_EXHAUSTED | INSUFFICIENT_DATA | KILL_TRIGGERED",
  "payload": { },
  "new_sources": [ ],
  "queries_run": [ ],
  "unreachable_sources": [ ],
  "self_check": { "invarianti_violati": [], "note": "" }
}
```

Se un agente non riesce a produrre JSON valido, l'orchestratore lo rilancia **una volta** con
l'errore di validazione in input. Al secondo fallimento l'idea va in `PARKED`.

---

## 7. Numeri

Ogni numero di mercato (TAM, SAM, SOM, numero di aziende target, tasso di crescita, prezzo medio,
MRR proiettato) deve portare con se':

- `value`, `unit`, `currency`, `year`
- `state`: `VERIFIED` | `ESTIMATED`
- se `VERIFIED`: `source_ids[]`
- se `ESTIMATED`: `formula` (in chiaro, ricalcolabile) e `input_source_ids[]`

Un numero senza origine non entra nel report. Se serve un numero e non c'e', si scrive `UNKNOWN` e
si spiega cosa mancava. **Non si presenta mai una stima con la stessa grafica di un dato.**

---

## 8. Le 5 domande anti-allucinazione sono distribuite

| Domanda | Agente che risponde |
|---|---|
| Q1 — Perche' compra entro 30 giorni? | `trigger-finder` |
| Q2 — Perche' non usa gia' un'altra soluzione? | `competitor-analyst` |
| Q3 — Perche' 3 persone in bootstrap possono competere? | `bootstrap-filter` |
| Q4 — Qual e' l'evidenza che il problema esista? | `evidence-researcher` |
| Q5 — Cosa dimostrerebbe che l'idea e' sbagliata? | `red-team` |

Nessun agente pone e risponde alla stessa domanda. Il `ranking-agent` si limita a **contare**
quante risposte sono ancorate a un `source_id` valido (es. `4/5 fontate`) e non puo' modificarle.
Servono almeno 4 risposte fontate su 5 perche' l'idea sia pubblicabile.

---

## 9. Budget e STOP

Ogni agente ha un tetto di ricerche in `config/deepsearch.config.json → budget`. Al raggiungimento
del tetto l'agente **si ferma e restituisce quello che ha**, con `stop_reason: BUDGET_EXHAUSTED`.
Non e' ammesso sforare "perche' mancava poco".

Condizioni di STOP immediato, valide per ogni agente:

1. Budget ricerche esaurito.
2. Il proprio verdetto e' gia' determinato (es. il Competitor Analyst ha trovato un KILL certo:
   smette di mappare gli altri competitor e restituisce subito).
3. Tre ricerche consecutive senza alcuna fonte nuova utile → rendimenti nulli, si chiude.
4. L'orchestratore invia un segnale di `ABORT` (perche' un gate parallelo ha ucciso l'idea).

---

## 10. Anti-compiacenza

La pipeline non deve produrre un risultato "perche' ci si aspetta un risultato". Se il funnel si
svuota, il report finale dice: *"Nessuna opportunita' ha superato i criteri in questo run"*,
elenca cosa e' stato esaminato, perche' e' caduto, e quali verifiche mancanti (stato `PARKED`)
verranno riprese il run successivo.

Questo e' esplicitamente **il comportamento desiderato**, non un errore da correggere.
