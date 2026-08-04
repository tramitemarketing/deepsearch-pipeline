# Strategie anti-allucinazione

> **DOCUMENTO DERIVATO — non e' la specifica.** Le regole vincolanti vivono in
> gli schemi e i prompt degli agenti. In caso di divergenza vincono quelli: una differenza fra questo file e
> loro e' un difetto di questo file. `tools/spec_check.py` verifica la presenza di questo riquadro.

> **Revisione v2.1, dopo il collaudo 20260801.** Due delle nove difese originali non facevano quello
> che promettevano e sono state riclassificate: il divieto lessicale non e' una difesa (§2) e lo
> snippet non protegge dalla fabbricazione (§4). Restano **sette difese piu' due strumenti**. La
> lezione generale: una difesa che dipende dall'autodisciplina di un agente o dal riconoscimento di
> una parola non e' una difesa, e contarla come tale produce falsa sicurezza — che e' peggio di
> nessuna difesa.

Sette difese stratificate, ordinate per costo crescente. Le prime tre non consumano budget di
ricerca: sono proprieta' strutturali del sistema. Le ultime costano, e per questo si applicano solo
alle idee che sono arrivate lontano nel funnel.

---

## Difesa 1 — Separazione dei poteri (costo zero)

Chi cerca non conclude, chi conclude non cerca.

Un agente `retrieval-only` che scrive una valutazione ha fallito il task, anche a fatti corretti.
Un agente `judgment-only` non ha strumenti di ricerca nel frontmatter.

Il punto non e' la regola: e' che `bootstrap-filter`, `venture-architect` e `ranking-agent`
**non possono materialmente fabbricare una fonte**, perche' non hanno il modo di produrne una nuova.
Ogni `source_id` che citano deve gia' esistere nel ledger, e l'orchestratore lo verifica.

Rendere impossibile un errore e' piu' affidabile che vietarlo.

## Difesa 2 — Claim strutturati, non testo libero (costo zero)

*Sostituisce il "divieto lessicale" della v2.0, che non era una difesa.*

Un elenco di aggettivi vietati in italiano si aggira senza nemmeno volerlo — "richiede tempo
significativo" al posto di "inefficiente" — e soprattutto non intercetta il canale principale con
cui un giudizio entra in un output di soli fatti: la **selezione**. Cinque fatti veri scelti tutti a
favore sono gia' una conclusione, con zero aggettivi. Nessun controllo testuale la vede.

La garanzia e' strutturale. `claim.statement` non e' una frase ma un record con slot: `subject`,
`observed`, `quantity` (un `Numeric`), `period`, `geo`. Non esiste un campo dove scrivere
"significativo": la quantita' o e' un numero o e' `UNKNOWN`. L'unico testo libero superstite e'
`verbatim_quote`, copiato dalla fonte e verificabile carattere per carattere.

Il controllo lessicale resta come **linter di telemetria**: misura la sciatteria di un agente nel
tempo e finisce nelle note di esecuzione. Non e' contato fra le difese.

## Difesa 3 — Quattro stati, con UNKNOWN e HYPOTHESIS di prima classe (costo zero)

`VERIFIED` / `ESTIMATED` / `HYPOTHESIS` / `UNKNOWN`. Non esiste un quinto stato "probabile".

`HYPOTHESIS` e' anche lo stato in cui finisce d'ufficio ogni `VERIFIED` che non supera il controllo
di citazione: e' il bersaglio del downgrade meccanico (difesa 7), quindi deve esistere. Nella v2.0
`CLAUDE.md` diceva "tre stati" mentre lo schema ne ammetteva quattro, e cinque agenti su nove hanno
segnalato la contraddizione.

`UNKNOWN` e' un output corretto e non penalizzato in se': penalizza solo il moltiplicatore di
confidenza, che e' esattamente il comportamento voluto. Sostituire un `UNKNOWN` con una
plausibilita' e' la violazione piu' comune e la piu' costosa, perche' e' invisibile a valle:
una volta scritto, un numero inventato ha lo stesso aspetto di un numero vero.

Il campo `display_string` sui numeri chiude il cerchio: e' l'agente che ha prodotto il numero a
decidere come va scritto, e per gli `ESTIMATED` la stringa **deve** iniziare con
`Stima non verificata:`. Cosi' e' strutturalmente impossibile che una stima appaia nel report con la
grafica di un dato.

## Difesa 4 — Source ledger con ID risolvibili (costo bassissimo)

Ogni fonte e' un oggetto con ID, non una stringa in prosa. Ogni claim `VERIFIED` deve avere almeno
un `source_id` risolvibile nel ledger, e ogni coppia (claim, fonte) porta il proprio snippet.

**Lo snippet non e' una difesa contro la fabbricazione, ed e' un errore contarlo come tale.** Un
modello sa produrre uno snippet plausibile da un titolo di ricerca senza aver aperto la pagina.
Blocca l'URL inventato di sana pianta e nient'altro: nel collaudo 20260801 i due claim con il
perimetro slittato avevano snippet perfettamente regolari.

La sua funzione vera e' un'altra: rende il verificatore cieco (difesa 8) **meccanico ed economico**
— confronto di stringa invece di giudizio. Percio' lo snippet vive nella coppia (claim, fonte) e non
nella fonte, e dichiara se viene dai risultati di ricerca (`SERP`) o dalla pagina (`FETCHED`), con
il verificatore che controlla per primi i SERP.

Nella v2.0 lo snippet era anche precondizione per citare una fonte: obbligava a una fetch per ogni
citazione e ha svuotato il budget di tutti e nove gli agenti del collaudo. Non lo e' piu'.

## Difesa 5 — Regole di promozione per tier (costo zero)

Cinque tier di fonte con pesi. Un claim diventa `VERIFIED` solo con 1×T1, o 1×T2, o 2×T3
indipendenti, o 4×T4 indipendenti. Le fonti T5 (marketing del vendor) sostengono solo fatti
auto-descrittivi di cui il vendor e' fonte primaria.

Due fonti che riprendono lo stesso comunicato stampa contano come una. L'indipendenza va verificata
prima di dichiarare la regola soddisfatta.

Effetto: un thread di forum non puo' piu' diventare da solo un "Fatto Verificato". Il monolite
trattava ogni fonte citata come equivalente, purche' avesse un nome e una data.

## Difesa 6 — Ricerca obbligatoria di prove contrarie (costo medio)

`evidence-researcher` deve dedicare almeno 2 ricerche a dimostrare che il problema **non** esiste o
**non** e' costoso. `trigger-finder` deve dedicare almeno 2 ricerche a proroghe, rinvii e moratorie.

Cinque ricerche che cercano conferme producono cinque conferme: la diversita' degli angoli non
corregge l'univocita' della direzione. Un evidence pack senza contro-evidenze e' trattato come
sospetto, non come solido.

Le contraddizioni fra canali vengono **conservate** (`contradicted_by`), non appianate. Un consenso
costruito ignorando i dati contrari e' peggio di una contraddizione dichiarata.

## Difesa 7 — Downgrade meccanico dall'orchestratore (costo zero)

Dopo ogni output, l'orchestratore scorre i claim e declassa a `HYPOTHESIS` ogni `VERIFIED` privo di
`source_id` risolvibile. L'agente non e' consultato e non puo' opporsi.

La differenza con il monolite e' chi esegue il controllo. Un'istruzione del tipo "se non hai la
fonte, sposta la riga in Ipotesi" delega l'esecuzione all'autodisciplina di chi ha appena scritto
la riga. Qui il controllo e' fuori dall'agente.

I downgrade sono loggati e diventano una metrica di affidabilita' per agente nel tempo.

## Difesa 8 — Verificatore cieco (costo alto, applicato tardi)

`source-verifier` riceve solo coppie `(claim, fonte)` e non sa quale idea sostengono, se e' la
migliore del run, ne' cosa succedera' al suo verdetto.

Cerca tre cose:

- **URL inesistente** → `SOURCE_NOT_FOUND`, segnale di fonte fabbricata;
- **perimetro slittato** → la fonte dice "manifatturiere europee oltre 250 addetti", il claim dice
  "PMI italiane". Il numero e' vero, il claim e' falso. E' il caso piu' comune e il piu' difficile
  da notare rileggendo il proprio testo;
- **data gonfiata** → una fonte del 2019 citata come 2024 per aggirare la regola dei 18 mesi.

Una sola fonte fabbricata invalida **tutti** i claim prodotti dallo stesso agente sulla stessa idea,
e l'orchestratore ricalcola i gate a monte senza quei claim. Se un gate non regge piu', l'idea muore
anche dopo essere arrivata al red team.

In profilo `DEEP` il campionamento e' totale sui claim che sostengono una risposta
anti-allucinazione o un numero pubblicato.

## Difesa 9 — Moltiplicatore di confidenza (costo zero, effetto sistemico)

```
confidence = 0.55 + 0.15×(domande fontate/5) + 0.15×(claim VERIFIED) + 0.15×(spot check pass rate)
final_score = somma_pesata × confidence
```

Il massimo ottenibile senza evidenze e' il 55% del punteggio nominale.

E' la difesa che rende razionale essere onesti. Senza di essa, un agente che gonfia le proprie
certezze ottiene un punteggio piu' alto e la sua idea viene pubblicata. Con essa, gonfiare non
conviene: il verificatore abbassa il pass rate e il punteggio scende piu' di quanto sarebbe sceso
dichiarando `UNKNOWN`.

**Non si vince scrivendo meglio.**

---

## Le tre patologie che il sistema intercetta

| Patologia | Come si manifesta | Difesa che la blocca |
|---|---|---|
| **Fonte fantasma** | URL plausibile, pagina inesistente | 4 (ID risolvibili), 8 (verifica) |
| **Perimetro slittato** | numero vero, claim falso | **8 (verifica) e solo quella** — nel collaudo ne ha trovati 2 su 10, con snippet regolari |
| **Riempimento dei buchi** | `UNKNOWN` sostituito da plausibilita' | 1, 2, 3, 7, 9 |
| **Falso negativo di nicchia** | pain reale scartato perche' poco documentato | evidenza di comportamento, esito `VALIDAZIONE-SUL-CAMPO` |

## Metrica di salute del run

Ogni run produce, nella sezione **Note di esecuzione** del report:

- numero di downgrade meccanici applicati, per agente;
- violazioni lessicali rilevate, per agente;
- `pass_rate` del verificatore e numero di fonti fabbricate;
- claim `VERIFIED` sul totale;
- domande fontate per idea pubblicata.

Un run con molti downgrade e un `pass_rate` basso e' un run **inaffidabile anche se ha prodotto
idee**. Questa sezione non si omette mai: senza di essa, un report costruito male e uno costruito
bene sono indistinguibili.
