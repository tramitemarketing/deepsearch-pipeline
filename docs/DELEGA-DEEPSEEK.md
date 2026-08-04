# Delega a DeepSeek per gli stage di raccolta

> **DOCUMENTO DERIVATO — non e' la specifica.** Le regole vincolanti vivono in `tools/ds_call.py`,
> in `config/deepsearch.config.json` e negli schemi. In caso di divergenza vince il codice.
> `tools/spec_check.py` verifica la presenza di questo riquadro.

## Cosa entra e cosa non entra

DeepSeek entra **solo come processo esterno via Bash**, attraverso `tools/ds_call.py`. Nessun
router, nessun proxy, nessuna variabile del provider di questa sessione viene letta o scritta:
`ANTHROPIC_BASE_URL` e `ANTHROPIC_API_KEY` non compaiono nel file e non devono comparirci mai.
Se `ds_call.py` sparisce, la pipeline gira identica — solo piu' cara.

Restano **integralmente** al modello principale, senza eccezioni: criteri di soglia, stage gate,
validazione degli schemi, red teaming, assegnazione degli ID, scrittura dei ledger, e selezione
finale delle opportunita'.

## Perche' la delega rende la separazione dei poteri piu' forte

`CLAUDE.md` §1 dice che chi cerca non conclude. Delegare la raccolta a un modello **diverso** rende
quell'invariante strutturale invece che disciplinare: il modello che giudica non puo' avere fatto la
raccolta nemmeno volendo. E' la versione forte della stessa regola, ed e' la ragione per cui questa
delega e' un miglioramento e non solo un risparmio.

## Dove diventa pericolosa, e il confine che la contiene

DeepSeek **non ha letto `CLAUDE.md`**. Non conosce IC-1..IC-4, non sa cos'e' il source-ledger, non
riceve un `source_id_range`, e non ha nessun motivo di scrivere `UNKNOWN` invece di una
plausibilita'. Il suo output non e' l'output di un agente della pipeline: e' un **input non fidato**,
e va trattato come si tratta una fonte, non come si tratta un collega.

`ds_call.py` e' quel confine, e rifiuta **prima** che l'orchestratore veda una riga:

| rifiuto | perche' |
|---|---|
| un `VERIFIED` senza `source_ids` | e' la regola "non riempirlo tu con una stima" resa impossibile da aggirare invece che raccomandata |
| campi obbligatori del payload mancanti | un solo rilancio con l'errore in input, poi `PARKED` |
| stringhe nella forma `S-...` | gli ID li assegna l'orchestratore dal cursore: se il raccoglitore li scrive, sono inventati per definizione, ed e' il modo piu' facile di corrompere il ledger |

Il terzo e' il meno ovvio e il piu' importante. Un modello esterno che "aiuta" numerando le fonti da
solo produce ID che sembrano validi, non collidono con niente, e non risolvono nel ledger: e'
esattamente la patologia che il run 20260802-1759 ha mostrato al 30%, ma introdotta di proposito.

## Uso

```bash
export DEEPSEEK_API_KEY=sk-...
python3 tools/ds_call.py \
  --prompt runs/<run_id>/_prompts/P-101-competitor.txt \
  --schema schemas/competitor-report.schema.json \
  --out    runs/<run_id>/03-competitor/P-101.json
```

Codici di uscita: `0` output valido scritto · `1` errore di configurazione · `2` rifiutato due
volte. **Su `2` l'orchestratore parcheggia l'idea e non completa mai il campo mancante a mano.**

## Cosa resta da fare all'orchestratore dopo ogni chiamata

Non e' opzionale, ed e' il motivo per cui questa delega non indebolisce nulla:

1. **Assegna gli ID.** Le fonti tornano senza identificatore: registrale nel source-ledger dal tuo
   cursore e riscrivi i riferimenti nel payload.
2. **Riapplica IC-1.** Ogni `VERIFIED` il cui `source_id` non risolve nel ledger diventa
   `HYPOTHESIS`, esattamente come per un subagent interno.
3. **Consolida i ledger nella stessa wave.** E' il difetto che ha reso non tracciabile il 30% delle
   citazioni del run 20260802-1759, e un raccoglitore esterno non lo fara' mai al posto tuo.
4. **Conta le ricerche.** Le chiamate a DeepSeek non consumano il budget `searches` della pipeline —
   consumano crediti DeepSeek. Dichiara le due cose separatamente nelle Note di esecuzione, o il
   confronto fra run smette di significare qualcosa.

## Il limite onesto

Nessuno ha ancora misurato se DeepSeek raccolga peggio, uguale o meglio sul corpus italiano — che e'
la parte del mondo in cui questa pipeline lavora e in cui InfoJobs ha chiuso, i marketplace non
rispondono e meta' dei corpora sta in area riservata. La delega e' un'ipotesi economica, non un
miglioramento dimostrato. Il modo di verificarla e' un run in doppio cieco sullo stesso pain, con la
quota di `UNKNOWN` e la quota di fonti irraggiungibili come metriche — e finche' quel confronto non
esiste, il risparmio e' certo e la qualita' e' un'assunzione.

---

## Il tier del modello, che è la leva più grande delle due

La delega esterna sposta il costo di alcuni stage. Il **tier** lo abbassa su tutti. Prima della
v2.10 ogni subagent ereditava il modello della sessione — cioè il più caro — per dieci agenti su
dieci, e nessuno lo aveva mai deciso: era il default.

Il criterio **non** è "retrieval costa poco, giudizio costa caro". È l'**asimmetria dell'errore**:
si spende dove sbagliare è irrecuperabile, si risparmia dove l'output è vincolato da uno schema,
ricontrollato da un altro agente e ricalcolabile.

| agente | tier | perché |
|---|---|---|
| `pain-finder` | sonnet | Unico stadio **creativo**; tutto il resto è filtro. Nel run reale la resa era 3 pain per shard contro un tetto di 12: il collo di bottiglia è già qui. Un pain mai generato non lo trova nessuno a valle. |
| `red-team` | sonnet | L'**avversario**. Trovare il modulo TeamSystem o il seed da 12M di Pillar richiede ricerca abile, non applicazione di regole. Un red team debole non fallisce visibilmente: fa **passare**. |
| `competitor-analyst` | sonnet | Il PASSO 1 (DIRETTO vs ADIACENTE) è definito dal prompt stesso «la decisione più delicata di tutto lo stage» e si propaga a ogni gate. Il resto del suo lavoro sarebbe da haiku: è il passo 1 a decidere il tier. |
| `venture-architect` | sonnet | Progetta. Il rischio non è la fabbricazione — non ha strumenti di ricerca — è la **banalità**: un blueprint mediocre supera i gate e non lo costruirebbe nessuno. |
| `evidence-researcher` | haiku | Riempie slot di `Claim` vincolati dallo schema. La struttura a slot è precisamente ciò che rende sufficiente un modello piccolo: non c'è un campo dove scrivere bene. |
| `trigger-finder` · `tam-analyst` | haiku | Cercano date e contano popolazioni. Ogni numero porta formula e `input_source_ids`: verificabile a valle riga per riga. |
| `source-verifier` | haiku | Confronto di stringa. **Se questo agente avesse bisogno di un modello grande, sarebbe il segnale che IC-3 non sta funzionando.** Haiku sì, delegabile **no**: il secondo termine del confronto è una pagina, e il ponte esterno non ne apre. |
| `bootstrap-filter` | haiku | 13 dimensioni contro vincoli numerici. `UNKNOWN` è una risposta legittima e frequente: serve disciplina, non profondità. |
| `ranking-agent` | haiku | `CLAUDE.md` §8 gli **vieta** di modificare qualunque cosa: «si limita a contare». È il primo candidato a diventare codice Python invece di un agente. |
| orchestratore | opus | L'unico che decide, applica i gate e possiede i ledger. Non delegabile e non declassabile: è il solo punto in cui un errore non viene ricontrollato da nessuno. |

`SC-19` verifica che ogni agente abbia un tier dichiarato **con una motivazione**, e che il
frontmatter concordi col config. Un tier senza un perché è una preferenza di costo travestita da
decisione di progetto, e si degrada sempre nello stesso modo: un giorno si guarda la bolletta e si
abbassa quello che sembra il meno importante.

## Chi NON è delegabile, e perché è un controllo e non un paragrafo

`config.provider.delegabili` contiene `bootstrap-filter`, `evidence-researcher`, `ranking-agent`,
`tam-analyst`, `trigger-finder`. `SC-20` fallisce se qualcuno aggiunge il `red-team`, il
`pain-finder`, il `competitor-analyst` o il `venture-architect` — e fallisce anche se `ds_call.py`
nomina `ANTHROPIC_API_KEY` o `ANTHROPIC_BASE_URL`.

Il caso che il controllo esiste per fermare è uno solo: **delegare l'avversario al modello più
economico**. Non produce un errore visibile, produce un `PASS`. L'unico modo di accorgersene è
costruire l'idea e fallire — fuori dalla pipeline, con i propri soldi. Fra sei mesi, guardando la
bolletta, sembrerà una buona idea: per questo sta in un controllo.

## Scegliere il provider

```bash
python3 tools/ds_call.py --provider deepseek --prompt ... --schema ... --out ...
```

Il provider si legge da `config.provider`, non da costanti nel codice: aggiungerne uno domani è una
voce in `disponibili` più il suo blocco con `env_chiave`, senza toccare `ds_call.py`. Oggi l'unico
valore ammesso oltre ad `anthropic` è `deepseek`, e un `--provider` non dichiarato viene rifiutato
con l'elenco di quelli validi.

---

## v2.12 — delegabile **se e solo se** il tier è haiku

La v2.11 aveva una lista di tre delegabili scritta a mano, e la giustificazione per escludere gli
altri tre haiku era: «delegare un judgment-only non risparmia nulla». Era un ragionamento sul costo
in **token**. Quando il vincolo è la **quota Anthropic** — che è il caso reale, perché la quota
serve anche per altro lavoro — quel risparmio è esattamente quello che serve.

E la separazione dei poteri non si rompe: un agente judgment-only che gira su DeepSeek **resta
senza strumenti di ricerca**, quindi continua a non poter cercare. L'invariante di `CLAUDE.md` §1 è
sugli **strumenti**, non sul modello che li usa. La linea era tracciata sull'asse sbagliato.

L'asse giusto è lo stesso del tier, e non per comodità: **il tier e la delegabilità rispondono alla
stessa domanda** — l'errore di questo agente è recuperabile? Se l'output è vincolato da uno schema,
ricontrollato a valle e ricalcolabile, allora basta un modello piccolo **e** va bene un provider
esterno, per la medesima ragione. Se sbagliare qui è irrecuperabile, non basta né l'uno né l'altro.
Due liste disegnate su criteri diversi divergono al primo cambiamento; una derivata dall'altra no —
e `SC-20` verifica l'equivalenza **nei due sensi**, quindi non si può né delegare un sonnet né
sfilare un haiku dalla lista senza che il cancello se ne accorga.

```
python tools\provider_switch.py --delega haiku    # i cinque su DeepSeek
python tools\provider_switch.py --revoca haiku    # tutti indietro su Anthropic
```

### v2.14 — `source-verifier` esce dai delegabili, e la regola guadagna il suo secondo asse

Fino alla v2.13 erano sei, e il sesto era `source-verifier`. La motivazione scritta qui diceva: *«è
delegabile — il suo lavoro è confronto di stringa, per questo è haiku»*. Vera e irrilevante: **il
confronto ha due termini, e il secondo è la pagina.** `ds_call.py` non passa `tools`, l'endpoint non
naviga. Delegato, il verificatore confronta lo snippet con il prompt — cioè con se stesso — e dice
sempre di sì.

E la variante che ripara gli altri cinque, *l'orchestratore scarica e il modello esterno estrae*,
qui non è inadatta: è **contraddittoria**. L'orchestratore fornirebbe la prova per l'audit della
raccolta che ha mediato lui, e la cecità da cui il verificatore prende tutto il suo valore
sparisce. Quel che resta è «questo testo contiene questa sottostringa»: tre righe di Python, senza
modello.

Il segnale era già in questo documento, due sezioni più sotto, dalla v2.12.2: *«la difesa a valle
avrebbe retto — il `source-verifier` gira su Anthropic con `WebFetch`»*. Il paragrafo che spiegava
perché la delega è sicura **assumeva** che quell'agente non fosse delegato, mentre il config lo
autorizzava. Due affermazioni incompatibili a settanta righe di distanza, entrambe scritte da me,
nessuna delle due sbagliata da sola.

La regola diventa: **delegabile se e solo se il tier è haiku E l'agente è chiuso sul proprio
input.** Il tier chiede *«l'errore di questo agente è recuperabile?»*, la chiusura chiede *«il suo
compito si esaurisce nel testo che riceve?»*. `source-verifier` è haiku a pieno titolo e non è
chiuso: resta in casa. Non è un'eccezione al criterio — è il criterio applicato a un caso che il
criterio vecchio non sapeva vedere. `SC-20` verifica ora la congiunzione nei due sensi, e
`provider_switch.py` distingue i due rifiuti: *«troppo importante»* si può rivedere guardando la
bolletta, *«il ponte non può aprire pagine»* no.

Costa: è il più caro dei sei per la quota, ed è anche l'unico il cui fallimento è **silenzioso** —
nel collaudo 20260801 intercettò 2 claim con il perimetro slittato su 10, e uno aveva già inquinato
il TAM che produsse il KILL su G9. Se la quota stringe, la leva resta il profilo, non spegnere
l'unico controllo indipendente sulla raccolta.

**Seconda volta che questa linea era tracciata sull'asse sbagliato.** La v2.12 la spostò da *costo*
a *quota*; la v2.14 aggiunge l'asse che mancava a entrambe. Nessuno dei due difetti è stato trovato
da un controllo: il primo da un'obiezione, il secondo leggendo il payload di `ds_call.py` e notando
l'assenza di `tools`.

**I quattro sonnet restano dove sono.** Se la quota continua a essere il vincolo, la leva onesta è
il **profilo**: un LIGHT costa circa un quinto di un DEEP e usa gli stessi gate. Abbassare il
profilo riduce quanto guardi; spostare l'avversario sul modello più economico riduce quanto vedi, e
te ne accorgi solo costruendo l'idea sbagliata.

---

## v2.12.2 — il difetto che rendeva la delega inservibile, e la riga che la ripara

Una revisione ha trovato il buco peggiore introdotto in questa sessione, ed era mio: **il payload di
`ds_call.py` non ha il campo `tools`.** L'endpoint chat non naviga e non apre pagine. Quindi un
agente di **raccolta** delegato non raccoglieva niente — produceva URL dalla memoria parametrica. E
tutti i delegabili sono agenti di raccolta: avevo spedito un ponte che non poteva servire nessuno
degli agenti che era autorizzato a servire.

Peggio: **nessuno dei tre rifiuti lo intercettava.** `RX_SRC` blocca gli ID di ledger inventati
(`S-...`), non gli URL. E un claim `VERIFIED` con una fonte dall'URL fabbricato supera il controllo
«numero senza fonte», perché la fonte c'è — semplicemente non esiste.

La difesa a valle avrebbe retto (il `source-verifier` gira su Anthropic con `WebFetch`, e il gate
7-bis invalida i claim di un agente con `fabricated_sources` non vuoto) — ma avrebbe retto **dopo**
aver speso le wave 2, 3, 4, 5 e 6 su claim fabbricati, per restituire uno zero non interpretabile.

**La riparazione non è documentare una disciplina, è un quarto rifiuto meccanico:** ogni URL che
compare nella risposta e **non era già nel prompt** fa rifiutare l'intera risposta. Il confronto è
per host+percorso, quindi `www`, `http/https`, slash finale e query non producono falsi rifiuti.

Da questa riga discende l'architettura corretta senza bisogno di prescriverla come regola da
ricordare: **l'orchestratore cerca e apre le pagine con i propri strumenti, mette il testo
recuperato e gli URL nel file di prompt, e il modello esterno estrae e struttura invece di
ricordare.** Un prompt senza fonti produce solo rifiuti — non è una raccomandazione saltabile quando
si ha fretta, è l'unico modo in cui una risposta può passare.

Ed è anche il lavoro in cui un modello economico rende meglio: estrarre campi da un testo che ha
davanti costa poco e sbaglia poco, mentre ricordare un URL costa uguale ed è sempre falso.

`SC-23` fallisce se il rifiuto sparisce da `ds_call.py` o se §0 smette di prescrivere che sia
l'orchestratore a cercare — perché senza quel passo ogni chiamata esterna verrebbe rifiutata e la
delega tornerebbe inservibile, stavolta in modo rumoroso invece che silenzioso.
