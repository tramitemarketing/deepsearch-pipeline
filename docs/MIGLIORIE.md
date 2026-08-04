# Migliorie rispetto al prompt monolitico — con motivazione

Tutti i principi del prompt originale sono conservati. Nessuno e' stato rimosso: sono stati
**ridistribuiti** fra agenti specializzati e, dove erano enunciati come intenzioni, resi
meccanicamente verificabili.

Le migliorie sono ordinate per impatto atteso sulla qualita' dell'output.

---

## A. Migliorie architetturali

### A1. Separazione retrieval / judgment

**Nel monolite.** Lo stesso contesto generava l'idea, cercava le prove e decideva se bastassero.

**Perche' e' un problema.** E' retrieval motivato: il modello che ha appena scritto un'idea
brillante e' lo stesso che decide quando ha cercato abbastanza. Il criterio implicito di arresto
diventa "ho trovato abbastanza da poter scrivere la scheda", non "ho verificato". Nessuna
istruzione testuale, per quanto severa, corregge questo — perche' non e' un problema di volonta',
e' un problema di incentivi dentro un singolo contesto.

**Ora.** Cinque agenti cercano e non possono concludere (divieto lessicale esplicito, verificato
dall'orchestratore). Tre agenti concludono e **non hanno strumenti di ricerca** nel frontmatter.

**Effetto piu' importante:** `bootstrap-filter`, `venture-architect` e `ranking-agent` non possono
*materialmente* fabbricare una fonte. Non e' una regola che potrebbero infrangere: e' una proprieta'
strutturale del sistema. E' la differenza fra chiedere onesta' e renderla l'unica opzione.

### A2. Introduzione dello stato PARK

**Nel monolite.** Solo due esiti: dentro o fuori. Davanti a dati insufficienti l'istruzione diceva
"scrivilo esplicitamente invece di assumere che sia battibile", ma l'unica azione disponibile
restava includere o buttare.

**Perche' e' un problema.** "Dati insufficienti" e' l'esito piu' frequente in una ricerca reale su
PMI europee, dove i bilanci sono depositati ma non indicizzati, i vendor locali non hanno presenza
online e i dati di settore sono a pagamento. Con due soli esiti, il sistema e' costretto a
scommettere ogni volta.

**Ora.** `PARK` con `missing_to_decide` puntuale, e rientro dopo 14 giorni **allo stage in cui si
era fermato**. L'incertezza diventa una coda di lavoro invece che una scommessa.

### A3. Memoria fra run

**Nel monolite.** Nessuna. Il task del venerdi' ripartiva ogni volta da zero.

**Perche' e' un problema.** Con la stessa istruzione e lo stesso modello, run successivi convergono
sulle stesse famiglie di idee. Dopo poche settimane il valore marginale di un run tende a zero, e —
peggio — le stesse idee gia' scartate tornano riproposte con la stessa sicurezza.

**Ora.** `seen-pains.jsonl` con cooldown differenziati (90 giorni per KILLED, 14 per PARKED, 365
per PUBLISHED), `sector-history.jsonl` per la rotazione dei settori, `source-ledger.jsonl`
riusabile. Un pain ucciso rientra prima del cooldown solo se una fonte documenta un cambiamento che
invalida il motivo dello scarto.

### A4. Verificatore di fonti cieco

**Nel monolite.** L'invariante "ogni fatto ha una fonte" era autodichiarato dallo stesso agente che
produceva il fatto.

**Perche' e' un problema.** Le tre patologie che questo intercetta sono invisibili all'autore:
URL plausibili mai aperti, **perimetro slittato** (la fonte dice "manifatturiere europee oltre 250
addetti", il claim dice "PMI italiane": il numero e' vero, il claim e' falso) e date gonfiate per
aggirare la regola dei 18 mesi. La seconda e' di gran lunga la piu' comune e la piu' difficile da
notare rileggendo il proprio testo.

**Ora.** `source-verifier` riceve solo coppie `(claim, fonte)` e non sa quale idea sostengono. Il
suo `pass_rate` entra nel moltiplicatore di confidenza del ranking. Una fonte fabbricata invalida
tutti i claim dello stesso agente sulla stessa idea.

### A5. Fan-out cieco sulla generazione

**Nel monolite.** Un solo contesto generava tutte le idee, quindi i primi token condizionavano tutti
i successivi.

**Ora.** 4-8 shard di `pain-finder` (secondo il profilo) su coppie (settore, angolo) diverse, che
non si vedono. Il
parallelismo qui **non serve alla velocita'**: serve alla diversita' del pool di candidati. E'
l'unica difesa efficace contro la convergenza su un unico pattern.

### A6. Pipelining invece di barriere

**Nel monolite.** Non applicabile (esecuzione lineare).

**Ora.** Un pain che ha completato le evidenze passa subito al gate successivo, senza aspettare gli
altri. Il tempo totale e' la catena piu' lenta di un singolo pain, non la somma degli stage piu'
lenti. L'unico barrier reale e' il ranking, che e' comparativo per natura.

---

## B. Ricalibrazione delle soglie competitor

Il profilo `STRICT` conserva i valori originali alla lettera ed e' selezionabile da config. Il
profilo `CALIBRATED` e' il default e cambia quanto segue.

### B1. Qualificazione del competitor (la modifica piu' importante)

**Nel monolite.** "SCARTA l'idea se anche un solo competitor nella nicchia soddisfa..." — senza una
definizione operativa di "nella nicchia".

**Perche' e' un problema.** Senza qualificazione, qualsiasi suite generalista globale conta come
competitor di qualsiasi nicchia. In pratica ogni idea muore al gate 3, o sopravvive solo grazie a
una decisione arbitraria e non ispezionabile su cosa sia "la nicchia".

**Ora.** Un vendor e' `DIRETTO` solo se soddisfa tutte e tre: stesso ICP, stesso job-to-be-done,
stessa geografia/lingua di go-to-market. **Solo i DIRETTI attivano i kill criteria.** Il campo
`relation_justification` rende la decisione ispezionabile.

### B2. K4 — feature AI generativa

**Originale.** "ha lanciato una feature con AI generativa negli ultimi 12 mesi" → KILL.

**Perche' e' un problema nel 2026.** Praticamente ogni vendor B2B ha spedito *qualcosa* con AI
generativa: un chatbot di supporto, un generatore di descrizioni, un riassunto automatico. Il
criterio non discrimina piu' nulla: uccide tutto.

**Ora.** Il kill scatta solo se la feature AI **risolve il core job-to-be-done del pain in esame**.
Un chatbot di supporto non conta. La domanda giusta non e' "usa l'AI?" ma "l'ha usata **per il
problema che vorrei risolvere io**?".

### B3. K2 — funding

**Originale.** "ha ricevuto funding VC/PE tracciabile" → KILL, senza soglia ne' finestra temporale.

**Perche' e' un problema.** Un seed da 200k del 2016, mai seguito da altro, non e' un segnale di
forza: spesso e' il contrario. La capacita' di reazione dipende dal capitale **fresco**.

**Ora.** ≥ 3M EUR negli ultimi 36 mesi.

### B4. K1 e K3 — fatturato e dipendenti

**Originale.** Fatturato totale > 10M EUR, dipendenti > 50, riferiti all'azienda intera.

**Perche' e' un problema.** Un gruppo da 40M EUR con una linea di prodotto marginale che compete con
te non e' un concorrente da 40M EUR: e' un concorrente distratto. Il fatturato totale sovrastima
sistematicamente la minaccia dei multi-prodotto.

**Ora.** Si tenta la scomposizione per linea di prodotto e unita' organizzativa. Se non e'
scorporabile, si usa il totale ma il kill e' `severity: SOFT` e richiede conferma da un secondo
criterio.

### B5. K5 — integrazione nativa con il sistema di record (criterio nuovo)

**Motivazione.** Nel B2B per PMI europee il moat vero non e' quasi mai il capitale: e' la presenza
dentro il gestionale che il cliente usa gia'. Un competitor con un connettore nativo, documentato e
aggiornato verso l'ERP dominante del segmento e' molto piu' difficile da spostare di un competitor
finanziato. Nella pratica di questa pipeline, K5 uccide piu' idee di K2 — ed e' giusto cosi'.

### B6. W3 — volume minimo di recensioni

**Originale.** Rating medio G2/Capterra < 4.0.

**Perche' e' un problema.** Tre recensioni non fanno statistica. E i prodotti di nicchia per PMI
europee hanno tipicamente pochissime recensioni, quindi il criterio era rumore.

**Ora.** Minimo 20 recensioni perche' il criterio sia applicabile. Sotto quella soglia il criterio
e' `UNKNOWN`, non `NOT_MET`.

### B7. W5 e W6 — changelog fermo e assenza di API (criteri nuovi)

**Motivazione.** Sono i due segnali di debolezza piu' facili da verificare oggettivamente e i piu'
sfruttabili da un team piccolo. Un prodotto senza aggiornamenti visibili da 12 mesi e' in
manutenzione. Un prodotto senza API pubblica non puo' stare nel workflow del cliente, ed e' il gap
piu' aggredibile in bootstrap. Piu' verificabili di "redesign UI di 5 anni fa", che richiede
un'interpretazione visiva.

### B8. Politica per gli UNCLASSIFIED

**Originale.** "Se non trovi dati sufficienti, scrivilo esplicitamente invece di assumere che sia
battibile." Corretto come intenzione, ma senza un'azione conseguente.

**Ora.** `UNCLASSIFIED` e' uno stato di prima classe. Se supera il 40% dei diretti → `PARK`
automatico. E l'assenza *totale* di competitor e' anch'essa `PARK`, non `PASS`: in un mercato B2B
reale, se nessuno risolve il problema e' quasi sempre perche' non e' pagato.

---

## C. Migliorie sul rigore delle evidenze

### C1. Tier delle fonti con regole di promozione

**Nel monolite.** Tutte le fonti erano equivalenti purche' citate con nome e data.

**Ora.** Cinque tier con pesi. Un claim e' `VERIFIED` solo con 1×T1, o 1×T2, o 2×T3 indipendenti, o
4×T4 indipendenti. Le fonti T5 (marketing del vendor) possono sostenere solo fatti auto-descrittivi
di cui il vendor e' fonte primaria: il proprio listino, le proprie integrazioni dichiarate.

Effetto pratico: un thread Reddit non puo' piu' diventare da solo un "Fatto Verificato".

### C2. Snippet obbligatorio (IC-3)

**Motivazione.** E' il controllo piu' economico contro la citazione di URL mai aperti. Se un agente
non puo' riportare la frase letta, non ha letto la fonte. Costa zero e blocca la patologia piu'
frequente.

### C3. Ricerca obbligatoria di prove contrarie

**Nel monolite.** Assente. Il protocollo chiedeva 5 ricerche su angoli diversi, tutti confermativi.

**Perche' e' un problema.** Cinque ricerche che cercano conferme producono cinque conferme. La
diversita' degli *angoli* non corregge l'univocita' della *direzione*.

**Ora.** `evidence-researcher` deve dedicare ≥2 ricerche a dimostrare che il problema **non** esiste
o **non** e' costoso. `trigger-finder` deve dedicare ≥2 ricerche a proroghe, rinvii e moratorie —
il rinvio di una scadenza e' il modo piu' comune con cui un'analisi di questo tipo produce urgenza
inesistente. Un evidence pack senza alcuna contro-evidenza e' trattato come sospetto.

### C4. Contraddizioni conservate, non appianate

Quando due canali producono claim in conflitto, l'orchestratore li **registra entrambi** invece di
sceglierne uno. Il campo `contradicted_by` esiste apposta. Una contraddizione dichiarata e' un
segnale di qualita' della ricerca; un consenso costruito ignorando i dati contrari e' il difetto
che questa pipeline esiste per evitare.

### C5. Downgrade meccanico

**Nel monolite.** "Se non riesci a fornire un riferimento concreto, quella riga va spostata in
Ipotesi." Corretto, ma l'esecuzione dipendeva dall'autodisciplina dello stesso agente.

**Ora.** L'orchestratore scorre i claim e declassa d'ufficio ogni `VERIFIED` privo di `source_id`
risolvibile. L'agente non e' consultato. I downgrade sono loggati in `downgrades.jsonl` e diventano
una metrica di affidabilita' per agente.

### C6. Le 5 domande distribuite fra agenti diversi

**Nel monolite.** Lo stesso modello poneva e rispondeva a tutte e cinque. Era autovalutazione, non
verifica.

**Ora.** Q1→trigger, Q2→competitor, Q3→bootstrap, Q4→evidence, Q5→red team. Nessuno risponde a una
domanda che ha anche formulato. Il `ranking-agent` **conta** le risposte fondate e non puo'
modificare il campo `grounded`.

---

## D. Migliorie sul dimensionamento

### D1. Bottom-up obbligatorio

**Nel monolite.** "Ogni numero di mercato deve avere una fonte citata accanto." Ma un TAM preso da
un report di mercato *ha* una fonte, e resta comunque inutilizzabile: i report aggregati sono
definiti su perimetri diversi dall'ICP e sono il vettore piu' comune di numeri che sembrano dati e
non lo sono.

**Ora.** Il dimensionamento parte dal conteggio delle imprese per codice ATECO/NACE e classe di
addetti (T1/T2). Il top-down e' ammesso solo come controprova, e le divergenze di oltre un ordine di
grandezza vanno dichiarate.

### D2. Attrito dei filtri esplicito

Ogni filtro applicato alla popolazione registra il proprio effetto numerico. Il passaggio da
"36.500 imprese" a "12.400 target" diventa ispezionabile invece che un salto.

### D3. Il numero che conta davvero

Piu' del TAM: **quanti clienti servono per 50.000 EUR/MRR** e quanti al mese vanno acquisiti per
arrivarci. Un TAM da 800M che richiede 4.000 clienti a 12 EUR/mese e' peggiore di un TAM da 40M che
ne richiede 55 a 750 EUR/mese. Il monolite chiedeva la scalabilita' verso 50k MRR ma non imponeva
di derivarla dal prezzo e dalla popolazione.

### D4. Analisi di sensibilita'

Almeno due scenari sul SOM variando l'assunzione piu' fragile. Rende visibile quanto la conclusione
dipenda da un numero scelto dall'agente.

---

## E. Migliorie sui vincoli di bootstrap

### E1. Tre dimensioni nuove

Il monolite verificava 7 vincoli. Ne sono stati aggiunti 3, tutti causa frequente di progetti che
sembrano bootstrappabili e non lo sono:

- **`no_regulated_liability`** — se il prodotto si assume responsabilita' legale (firma,
  conservazione a norma, calcolo fiscale), servono certificazioni, assicurazioni e presidi che tre
  persone non reggono in 90 giorni;
- **`no_certification_required`** — accreditamenti o iscrizioni ad albi necessari per poter vendere;
- **`data_access_feasible`** — i dati da elaborare sono ottenibili via API pubblica o export
  standard, o solo tramite scraping fragile o accordi da negoziare con l'incumbent?

In Europa queste tre uccidono piu' micro-SaaS della concorrenza.

### E2. Q3 non accetta risposte retoriche

"Siamo piu' veloci", "gli incumbent sono lenti", "siamo piu' focalizzati" sono marcate
`grounded: false` d'ufficio. Serve una **barriera bassa verificabile** citata nei claim.

### E3. Unit economics calcolate, non asserite

CAC/ARPA e payback derivano dai numeri del market report e dai canali del blueprint. Il monolite
chiedeva "CAC stimato troppo elevato" senza fornire il denominatore.

---

## F. Migliorie sul red teaming

### F1. Tre lenti indipendenti che votano

**Nel monolite.** Una sezione "Red Teaming" scritta dallo stesso contesto che aveva appena costruito
l'idea, in fondo alla scheda.

**Perche' e' un problema.** Nessun autore demolisce sinceramente cio' che ha appena finito di
costruire, soprattutto dopo aver scritto 400 parole a sostegno.

**Ora.** Tre istanze separate, cieche fra loro, con mandato esclusivamente distruttivo e lenti
diverse: competitor nascosti, fallimenti storici, economics e distribuzione. Votano. Servono 2 KILL
su 3, oppure un solo finding `FATALE` + `VERIFIED`.

### F2. Divieto di miglioramento

Il red team non propone mitigazioni. Se trova una soluzione, se la tiene. Offrirla indebolirebbe la
funzione che svolge: un demolitore che ripara non demolisce piu'.

### F3. Ogni finding deve essere falsificabile

`how_to_falsify_this_finding` e' obbligatorio. Impedisce le obiezioni non verificabili ("il mercato
potrebbe non essere pronto"), che sembrano rigore e non lo sono.

### F4. Competitor nascosti non-software

La lente `competitor_nascosti` cerca esplicitamente moduli gia' inclusi nell'ERP del cliente,
servizi dei commercialisti, consorzi e centri servizi, gestionali verticali locali non indicizzati,
e il foglio di calcolo che funziona abbastanza bene. Nel B2B italiano il commercialista e' il
concorrente piu' sottovalutato di qualsiasi micro-SaaS amministrativo.

---

## R. Migliorie sul ranking

### R1. Formula deterministica invece di ordinamento qualitativo

**Nel monolite.** "Ordina per: 1) intensita' del dolore economico, 2) disponibilita' immediata a
pagare, ..." — sei criteri lessicografici, senza scale ne' pesi. In pratica l'ordinamento era una
sensazione.

**Ora.** 7 dimensioni con scale ancorate (cosa vale 10, cosa vale 5, cosa vale 0), pesi espliciti in
config, spareggi definiti. Due esecuzioni sullo stesso dossier producono lo stesso punteggio.

### R2. Moltiplicatore di confidenza

`0.55 + 0.15×(domande fontate/5) + 0.15×(claim VERIFIED) + 0.15×(spot check pass rate)`, applicato al
punteggio pesato. Il massimo ottenibile senza evidenze e' il 55% del nominale.

E' il meccanismo anti-retorica del sistema: **non si vince scrivendo meglio.** Un'idea brillante e
non verificata non puo' superare un'idea solida e documentata.

### R3. Punteggi senza evidenze plafonati a 5

Una dimensione senza `evidence_ids` non puo' superare 5. Impedisce di premiare cio' che non e'
documentato.

### R4. Livello di fiducia calcolato

`Alto/Medio/Basso` deriva dal moltiplicatore, non e' affermato. Il monolite chiedeva che fosse
"motivato, non affermato", ma senza un valore da cui derivarlo.

---

## S. Chiusura dei buchi logici (revisione avversariale del design)

Il design e' stato sottoposto a una revisione avversariale prima della consegna. Queste correzioni
non riguardano il prompt originale ma difetti della prima versione della pipeline stessa: le
riporto perche' sono il tipo di errore che un'architettura a stage genera piu' facilmente di un
prompt monolitico, e che va cercato apposta.

**S1. Esaustivita' dei gate.** Ogni gate ora copre tutto lo spazio dei propri input. Tre buchi
trovati e chiusi: al gate 3 il competitor **sano e ben documentato** (nessun kill criterion, ma
nemmeno abbastanza keep criteria) non ricadeva in nessuna classe — introdotta la classe `STRONG` e
il ramo `KILL (G7)`; al gate 4 il trigger `VERIFIED` completo ma con scadenza oltre i 30 giorni non
attivava nessuna regola — ora va in `PARK` con `retry_after` alla scadenza meno 60 giorni; al gate 8
l'idea con punteggio alto ma meno di 4/5 domande fontate spariva senza traccia — ora va in `PARK`
con le domande mancanti in `missing_to_decide`.

Il caso del gate 4 e' anche il piu' comune di tutti in questo dominio: quasi ogni scadenza normativa
utile e' a 6-12 mesi quando la si scopre.

**S2. Il flag `SOFT` era inerte.** La difesa contro il falso KILL (usare il fatturato totale quando
non e' scorporabile) marcava il kill come `SOFT`, ma nessuna regola consumava quel flag: un
competitor da 40M EUR non scorporabile non uccideva mai. Aggiunta `soft_kill_policy`: `SOFT` diventa
`HARD` se un secondo criterio e' `MET` + `VERIFIED`, altrimenti `PARK` con la scomposizione mancante.

**S3. Dipendenza circolare fra stage.** Il gate 6 chiedeva il rapporto CAC/ARPA, ma il CAC per
canale nasce solo con il blueprint, cioe' due wave dopo — e la regola "dimensione senza claim →
`UNKNOWN`" mandava **ogni** idea in `PARK`. Risolto spezzando la valutazione: al gate 6 CAC e ciclo
di vendita sono `non_blocking_when_unknown`, e vengono rivalutati al **gate 6-bis** dopo il
blueprint, quando i dati esistono. Aggiunto `arpa_annual` come campo obbligatorio del market report.

**S4. Loop non limitato.** Il supplemento istruttorio al gate 6 poteva ciclare all'infinito su un
dato non reperibile, consumando l'intero budget su una sola idea. Introdotto
`max_supplement_rounds`.

**S5. Budget incoerente.** I tetti di ricerca per profilo erano piu' bassi della somma dei budget
per istanza: la pipeline si sarebbe fermata a meta' funnel producendo un report vuoto
indistinguibile dall'esito legittimo "nessuna opportunita' ha superato i criteri". Ricalcolati i
tetti a partire dai budget per istanza, e aggiunta una soglia di allerta all'80% oltre la quale non
si aprono nuove idee.

**S6. Collisione degli ID.** Gli intervalli di `source_id` erano definiti per **tipo** di agente,
ma in DEEP girano oltre 120 istanze di `evidence-researcher`: gli ID collidevano e l'invariante IC-1
diventava non decidibile. Ora l'orchestratore assegna un intervallo disgiunto **per istanza**, e gli
ID delle fonti hanno 4 cifre.

**S7. Aggregazione impossibile di Q4 e Q5.** Si chiedeva a istanze cieche di stabilire se la propria
risposta fosse "la piu' forte" — informazione che per costruzione non possono avere. Ora ogni
istanza risponde per il proprio canale/lente e l'orchestratore consolida (`grounded` = OR, si
conserva la risposta con piu' fonti risolvibili).

**S8. Voto di maggioranza decorativo.** Una lente del red team poteva votare `KILL` solo su un
finding fatale verificato, che gia' da solo uccideva l'idea: il ramo "≥2 voti su 3" non produceva
mai alcun effetto. Aggiunta la seconda condizione di voto (≥2 finding `GRAVE` + `VERIFIED`), che
rende il voto di maggioranza capace di uccidere idee che nessuna singola lente avrebbe ucciso.

**S9. Il verificatore non poteva eseguire il proprio mandato.** Budget fisso di 8 fetch contro un
audit completo in profilo DEEP: il `pass_rate` sarebbe sempre stato calcolato su un campione
troncato, e quel numero pesa 0.15 nel moltiplicatore di confidenza. Ora l'orchestratore calcola le
coppie da controllare e lancia N istanze in parallelo, ciascuna con budget pari alle coppie
ricevute. Aggiunto anche il ramo `PARK` al gate 7-bis per pass rate sotto 0.6, che era definito nei
gate ma non implementato.

**S10. Assunzioni di progetto vs fatti.** Al `venture-architect` si vietava di introdurre fatti
nuovi, ma lo schema gli chiedeva tre prezzi e un CAC — che sono numeri nuovi. Distinzione ora
esplicita: i **fatti** restano vietati, le **assunzioni di progetto** sono obbligatorie e vanno
marcate `is_assumption: true` con formula e `display_string` che inizia con `Assunzione di progetto:`.

**S11. Criteri dichiarati e mai applicati.** `G4` (moda passeggera) e `G7` (gia' risolto da software
moderno) erano definiti in config ma nessun gate li usava. Ora `G4` e' un ramo del gate 4 e `G7` del
gate 3, ed entrambi compaiono nel contatore di caduta.

---

## H. Cosa NON e' cambiato (deliberatamente)

- Le due regole d'oro: qualita' sopra quantita', follow the money.
- Il massimo di 4 idee e le 400 parole per idea.
- La sintesi degli scarti in massimo 5 righe.
- La struttura della scheda di output, riprodotta identica nel report finale.
- Il vincolo delle 5 ricerche minime per idea pubblicata — anzi rafforzato: si contano solo query
  **distinte per angolo**, e in profilo DEEP un'idea pubblicata ne accumula 40+.
- La regola dei 18 mesi e il flag "fonte datata".
- La sezione "Fonti non accessibili", ora alimentata automaticamente da tutti gli agenti invece che
  compilata a memoria alla fine.
- Il divieto di "fonte: ricerche di settore" e simili, ora applicato meccanicamente
  dall'orchestratore prima della pubblicazione.
- La dichiarazione esplicita quando nessuna opportunita' supera i criteri — resa un esito di prima
  classe con un campo dedicato nello schema del ranking.

---

## S — v2.8: la specifica smette di essere solo prosa

Le versioni v2.5, v2.6 e v2.7 hanno tutte chiuso i difetti trovati dalla review precedente, e tutte
e tre ne hanno introdotti di nuovi **della stessa classe**: una soglia corretta in config e non nel
prompt, un pattern di ID corretto in uno schema e non nell'altro, un ordine di esecuzione corretto
nel codice e non nella sezione che lo invoca. Ogni correzione era attenta. Il tasso non e' sceso.

La lettura giusta non e' che le correzioni fossero superficiali: e' che l'attenzione non e' la leva.
Un sistema i cui invarianti vivono in un config, undici schemi, dieci prompt, un orchestratore e
sette documenti — senza che nulla li confronti fra loro — produce questa classe di difetto a ritmo
costante, indipendentemente da quanto si legga bene. La v2.8 non aggiunge una regola: aggiunge il
confronto.

**`tools/spec_check.py` — tredici controlli, tutti meccanici.**

| | cosa confronta | difetto storico che avrebbe preso |
|---|---|---|
| SC-01 | riferimenti `config.x` nei prompt vs config reale | parametro letto e mai scritto |
| SC-02 | parametri di config mai letti da nessuno | orfani della review B |
| SC-03 | soglie scritte a mano nei prompt vs config | finestre zero-yield in 4 prompt; soglia su `final_score` in 3 file su 5 |
| SC-04 | pattern di ID per spazio di nomi, e ID namespacati che devono incorporare `run_id` | N1 (`pain_id`), C1 (`run_id` vs `S-`) |
| SC-05 | token in stile enum nei prompt vs enum degli schemi | `Verdict` senza `FAIL`; e ha trovato da solo il vocabolario doppio degli `outcome` |
| SC-06 | criteri K/W/M/G citati vs dichiarati | gate che non si chiude, gate che non si apre |
| SC-07 | separazione dei poteri nel frontmatter degli agenti | l'invariante piu' importante del sistema, finora verificabile solo a occhio su dieci file |
| SC-08 | artefatti e **ordine delle fasi** in §11 vs `run_close.py` | **C2**, aperto in v2.6 e in v2.7 |
| SC-09 | "N rami" dichiarati vs righe elencate | N5 |
| SC-10 | `$ref` degli schemi risolvibili | rottura silenziosa della validazione |
| SC-11 | aritmetica di funnel e pesi di scoring | DEEP: 80 pain dichiarati, 64 ID disponibili, 48 di budget |
| SC-12 | inventario agenti: disco, config, orchestratore | budget a un agente inesistente |
| SC-13 | documenti esplicativi marcati come derivati | `DATA-CONTRACT.md` letto per due versioni come fonte di verita': e' da li' che e' uscito N1 |

**`tools/spec_check_selftest.py` — la parte che conta davvero.** Un verificatore che passa non dice
niente: puo' passare perche' la specifica e' coerente, o perche' non guarda, e le due cose sono
indistinguibili leggendo il codice. L'autotest riapplica **quattordici difetti realmente accaduti in
questo repo**, ognuno su una copia usa-e-getta, e pretende che vengano ripresi. Alla prima
esecuzione ne passavano 11 su 14: SC-04 non vedeva C1 (ogni pattern era coerente *con se stesso* —
la relazione da verificare non era "sono uguali" ma "uno contiene l'altro") e SC-08 non vedeva C2
per una mutazione scritta male. Entrambi riscritti. Ora 14 su 14.

**Il limite, dichiarato.** `spec_check` verifica l'**accordo** fra i file, non la loro correttezza.
Non sa se `0.4` sia la soglia giusta per `unclassified_ratio` e non deve saperlo. E' esattamente il
motivo per cui poteva essere scritto adesso, mentre ogni soglia di questa pipeline e' ancora
un'ipotesi che nessun dato ha toccato: **non congela nessun numero, congela solo l'accordo fra i
file**. Un motore dei gate in codice congelerebbe invece dei giudizi, e resta fuori finche' il
portafoglio ombra non avra' prodotto il primo tasso di falsi KILL — che e' la sola misura capace di
dire se quelle soglie sono calibrate o sono opinioni scritte con sicurezza.

**Cosa NON copre.** La coerenza semantica della prosa. Nessun programma sa se il paragrafo che
spiega il gate 3 descrive davvero il gate 3. SC-13 fa l'unica cosa onesta possibile: non tiene
allineati i documenti, ma garantisce che **nessuno li scambi per la specifica**.

### Gli altri interventi della v2.8

**C2 — chiuso davvero.** §11 e' ora una sequenza di cinque passi e `run_close.py` gira **due volte**:
`--fase pre` dopo gli artefatti tipizzati e prima del report (il verdetto decide come il report si
apre), `--fase finale` dopo il report e prima — e solo prima — della scrittura in `state/`. La
circolarita' era strutturale, non una svista: eseguendo una volta sola bisogna scegliere se il
verdetto precede il report o il report precede il verdetto, e v2.6 e v2.7 sceglievano di verificare
prima di scrivere cio' che verificano. Corretta anche l'eccezione `primo_run`, che ammetteva i file
di stato **vuoti** ma non **assenti**, cioe' non copriva l'unico caso che diceva di coprire.
Verificato su un'installazione pulita: primo run → `VALIDO`, `state/` scrivibile.

**N1** — `pain_id` unificato su `^P-[0-9]{3}$` (padding a tre cifre, non lunghezza minima).
**N2** — la retrocessione di K1/K2/K3 a best-effort e' ora dichiarata come **scambio di errori**:
meno falsi PARK, piu' falsi PASS su incumbent finanziati, con la lente `competitor_nascosti` come
copertura e il portafoglio ombra come misura per rivederla.
**N3** — la regola 3 del competitor (`STRONG`) e' diventata il ramo **terminale**: ci si arriva per
esclusione. Prima un DIRETTO con gli obbligatori decisi e i best-effort `NON_TENTATO` — il caso
*normale* sotto la valutazione a scalini — non soddisfaceva ne' `STRONG` ne' `UNCLASSIFIED` e
restava senza classe, facendo sparire il KILL di G7.
**N4** — il gate 2 e' una scala ordinata di sei rami. L'uscita `VALIDAZIONE-SUL-CAMPO` precede
`PASS-B` esplicitamente, e richiede che la via documentale sia **esaurita** (nessun canale in
`BUDGET_EXHAUSTED`), non solo incompleta: meno di 2 claim VERIFIED dopo un'interruzione non dice
"non c'e' documentazione", dice "abbiamo smesso di cercare". Il salto 2→3 tracce non e' piu' un
gradino arbitrario: uscire rinuncia alla mappatura competitiva, quindi la soglia per rinunciare e'
piu' alta, e il peso lo porta la **diversita' delle classi di attore**, non il conteggio.
**N5** — §11 rinumerata, artefatti allineati a sei, gate 3 dichiarato a otto rami, `righe()` che
restituisce `-1` non contamina piu' la riconciliazione.

**Deriva documentale.** `ARCHITETTURA.md` mostra ora l'uscita al gate 2 e otto rami al gate 3;
`STAGE-GATES.md` ha il gate 2 a due vie con l'uscita anticipata e il ramo di disallineamento al
gate 3; `DATA-CONTRACT.md` ha ID di fonte a quattro cifre, claim namespacati, `run_id` con l'ora —
ed e' finalmente marcato come derivato. Tutti e cinque i documenti esplicativi portano ora il
riquadro, e SC-13 lo verifica.

### v2.8.1 — cosa ha trovato la revisione avversariale della v2.8

La v2.8 e' stata sottoposta a due revisioni indipendenti prima della consegna. Hanno trovato
**dodici difetti confermati**, e i due piu' importanti riguardavano proprio le due cose che la v2.8
dichiarava di aver risolto. Sono elencati qui perche' il valore di questa sezione sta negli errori,
non nelle correzioni.

**Il verificatore era in parte cieco, e il numero che lo certificava era costruito all'indietro.**
L'autotest dichiarava 14/14. Su **sei difetti inventati da un revisore terzo ne riprendeva due**.
Le firme della circolarita' erano misurabili: corrispondenza 1:1 perfetta fra ogni mutazione e il
suo controllo (un difetto vero ne tocca due o nessuno), 15 rami d'errore esercitati su 31, e — la
piu' netta — **una delle quattordici mutazioni era codice morto**, scavalcata da un ramo speciale in
`main()`: eseguita davvero, sfuggiva. Il 14 la contava come ripresa. Ora l'autotest ha due sezioni
separate e stampa entrambi i numeri, con scritto quale conta: **storici 14/14 · indipendenti 6/6**.
Le mutazioni indipendenti restano nel file anche quando falliscono.

**`SC-07`, l'invariante dichiarato piu' importante del sistema, aveva un buco.** Il frontmatter
`tools:` veniva letto solo nella forma inline. Scritto come lista YAML — forma altrettanto valida —
`\s*` divorava il newline e un agente di solo giudizio con `WebSearch` passava pulito.

**`SC-03` era efficace su 3 costanti dichiarate su 12, e nessuno lo sapeva** perche' nessuno lo
aveva misurato. Pretendeva l'ordine etichetta→operatore→numero sulla stessa riga: cioe' esattamente
la forma della propria mutazione di autotest, mentre l'idioma dominante del repo scrive l'operatore
prima (`weighted_sum >= config.scoring.soglia_pubblicazione (6.5)`). Ora l'ordine e' libero, le
percentuali si convertono, e **la copertura viene stampata a ogni esecuzione**: il numero e' una
misura, non piu' una promessa.

**Sette input malformati producevano un traceback invece di un errore.** Per il primo comando di
ogni run e' il modo tipico in cui un controllo viene commentato via. Ora nessun controllo puo'
uscire con un'eccezione: un controllo che esplode viene riportato come difetto dello strumento.

**Sette modifiche legittime su diciannove venivano bocciate** — il rischio piu' probabile di morte,
piu' dei falsi negativi. Le ancore di `SC-08` erano frasi letterali, quindi riformulare una riga di
§11 bloccava il run; l'esenzione dei documenti storici era sensibile al maiuscolo. Ora le ancore
sono marcatori (`<!-- ancora: report -->`) attorno ai quali la prosa si riscrive liberamente, il
confronto sui nomi e' insensibile al maiuscolo, ed esiste una via d'uscita esplicita per riga
(`<!-- spec-check: ignora -->`) per gli esempi didattici e i valori osservati.

**`run_close.py`: i file di `state/` erano un elenco unico, ed era un errore concettuale.** Meta' di
quei file (`seen-pains`, `sector-history`) viene scritta **dopo** la verifica, dal §10 che la
verifica autorizza. Pretenderli non vuoti al momento della chiusura creava un blocco permanente:
run 1 degradato → `state/` resta vuota → run 2 fallisce sui file di stato → degradato → run 3
identico. Era C2 di nuovo, spostato dal primo al secondo run, e l'eccezione `primo_run` lo
mascherava sull'unico caso che funzionava. Peggio: quell'eccezione dipendeva da quante cartelle
esistessero in `runs/`, quindi **cancellare un run precedente faceva passare un run degradato da
DEGRADATO a VALIDO**. Ora i due gruppi sono separati per *quando vengono scritti* — i ledger sono
fatti su questo run e si riconciliano, la memoria si controlla solo per integrita' — e l'eccezione
e' sparita. Verificato su quattro run consecutivi da installazione pulita, con un run guasto in
mezzo: la memoria vive, il run guasto non la inquina, e cancellare cartelle non cambia piu' nulla.

**Difetti di specifica trovati e chiusi:** `MENZIONE_ONESTA` (schema, prompt) vs `MENZIONE-ONESTA`
(config) — la stessa classe che la v2.8 dichiarava di aver chiuso, viva mentre `spec_check` diceva
0 errori: da qui nasce `SC-14`. `state/README.md` era fuori perimetro e documentava ancora
`PUBLISHED`. Il gate 3 non era esaustivo (cinque DIRETTI, tre `STRONG` e due `UNCLASSIFIED` con
rapporto esattamente 0.4 non attivavano nessuno degli otto rami): i rami 7 e 8 ora dicono "nessun
BEATABLE" invece di "tutti STRONG", e il ramo 4 usa `>=`. Il gate 8 accettava
`VALIDAZIONE-SUL-CAMPO` solo per rotta `COMPORTAMENTO` mentre agente, schema e documentazione
ammettevano anche `DISALLINEAMENTO`. `evidence_route` aveva due scrittori e nessuna precedenza
dichiarata. `BEATABLE` era valutato prima di `UNCLASSIFIED`, contro `precedenza_verdetto`, e
promuoveva come battibile un vendor di cui non si sapeva se fosse il sistema di record del cliente.
Lo schema del competitor descriveva ancora `STRONG` come condizione, e `DATA-CONTRACT.md` stabilisce
che in caso di divergenza vince lo schema: la correzione N3 era formalmente perdente. I ledger sono
cumulativi e le loro righe non portavano `run_id`, quindi dal secondo run l'intestazione del report
avrebbe attribuito al run corrente il lavoro di tutti i precedenti — e `run_close` lo avrebbe
riconciliato con successo, confrontando lo stesso totale con se stesso.

**Quello che resta aperto, dichiarato.** `spec_check` verifica relazioni fra artefatti tipizzati e
adiacenze nella prosa. Non modella le relazioni semantiche: esaustivita' di un insieme di rami,
precedenza fra regole, chi scrive un campo quando ci sono due scrittori. Sette dei dodici difetti
qui sopra sono di quella natura e continuerebbero a passare. Il modo di ridurli non e' un'altra
regex: e' portare quei gate in codice — che resta fuori finche' il portafoglio ombra non produce il
primo tasso di falsi KILL, perche' prima di allora congelerebbe giudizi invece di accordi.

### v2.9 — il verificatore smette di essere spegnibile

Terza revisione avversariale sulla v2.8.1. Verdetto: la versione precedente era la prima a cambiare
il *processo* invece del contenuto, ma il verificatore aveva **un modo di morire che il suo autore
non aveva sorvegliato**, e sei mutazioni su otto scritte da un terzo passavano pulite.

**Il contratto autodisabilitante — la critica centrale, ed era giusta.** Cinque controlli
(`SC-03`, `SC-04` per la seconda metà, `SC-07`, `SC-14`, e ora `SC-15`) sono guidati da
`config.verifica_specifica`, cioè da un contratto che viveva **dentro l'artefatto che verificano**.
Svuotare una lista li spegneva senza un errore: al più un avviso in mezzo ad altri settantadue.
Non è uno scenario di malafede — è quello che succede il giorno in cui una voce dà un falso positivo
e la si toglie "temporaneamente". Verificato: `costanti_rispecchiate` vuoto più `soglia_pubblicazione`
6.5 → 8.5 solo in config passava pulito.

La guardia era **già scritta**, in `SC-13`, con la motivazione giusta: *"svuotare la cartella non
può essere un modo per far passare i controlli"*. Applicata a un controllo su cinque. Ora c'è
`SC-00`, che verifica le dimensioni minime di ogni contratto — e quelle soglie stanno **in
`spec_check.py`, non in config**. Il posto conta più della regola: le aspettative del verificatore
sul proprio contratto appartengono al verificatore, metterle nel file verificato ricrea la
circolarità che questo strumento esiste per rompere.

**`SC-15` — la direzione che nessuno guardava.** Tutti i controlli confrontavano prompt → schema.
Un campo `required` che nessun prompt nomina passava pulito, e l'omissione si paga a run avviato,
alla wave in cui capita. Alla prima esecuzione ha trovato **quattordici campi obbligatori** mai
nominati dai prompt che devono produrli — `pains`, `claims`, `competitors`, `verdict`, `triggers`,
`population`, `mvp`, `findings`, `kill_vote`. Ogni prompt ora elenca i propri campi obbligatori.

**`SC-11` — raggiungibilità, non severità.** Il confine dichiarato («niente semantica») non reggeva
all'ispezione: `SC-11` faceva già aritmetica di dominio. Il confine difendibile è un altro, e ora è
scritto: si verifica che un ramo sia **raggiungibile**, mai che la soglia sia giusta.
`misalignment_min = 20` su quattro criteri M non è una soglia severa, è un ramo spento — e quel
ramo, per la config stessa, è «metà della missione». Coperti anche `keep_if_at_least`,
`min_domande_fontate`, e la partizione obbligatori/best-effort.

**`SC-12` — ruolo, non presenza.** Verificava che il nome dell'agente comparisse *da qualche parte*
nell'orchestratore. Sostituire `competitor-analyst` con `source-verifier` nella wave 3 era
invisibile: nessuna mappatura competitiva, quindi nessun kill possibile al gate 3, su ogni pain del
run. Ora ogni agente dichiara le wave in cui ha diritto di comparire.

**`SC-02` in un canale a parte.** Produceva da solo 67 avvisi su 73. Un avviso vero sepolto sotto
sessantasette da convenzione è un avviso perso, e questo è il primo comando di ogni run. Ora è un
conteggio; l'elenco si chiede con `--rumore`. Il pannello è passato da 73 avvisi a 8.

**`§0-pre` — codice morto da tre versioni, chiuso.** `run_id` contiene l'ora di avvio, quindi
`runs/<run_id>/` non poteva **mai** preesistere: la condizione «se esiste già» era falsa per
costruzione e la ripresa non è mai stata raggiungibile. Erano due esigenze confuse in un
identificatore solo: `run_id` (`YYYYMMDD-HHMM`) identifica l'**esecuzione** e prefissa gli ID;
`run_id_logico` (`YYYYMMDD`) identifica l'**indagine** e non cambia fra i tentativi. La ripresa
cerca run non chiusi con lo stesso `run_id_logico`, e scrive comunque in una cartella nuova — o gli
ID delle fonti collidono.

**`dedup_key` — la criticità diventata dominante.** Ora che `run_close` sblocca la scrittura di
`state/`, una memoria che scrive senza ricordare è peggio di una memoria spenta: costa e non
trattiene. Era l'unico identificatore del sistema non tipizzato e non vincolato a un vocabolario.
I tre campi sono ora chiusi: `<settore>` è una stringa esatta di `sectors_rotation`, `<fascia>` è
una di tre, `<job>` è **verbo all'infinito + oggetto diretto**, da due a quattro parole, senza
articoli né aggettivi — e il `pain-finder` deve confrontare il proprio job con
`excluded_dedup_keys` e riusare la stringa esistente invece di coniarne una variante.

**L'autotest ha ora tre corpus e li tiene separati:** 14 storici (scritti dall'autore dei
controlli), 6 indipendenti, 8 della terza revisione — **28 su 28**. I due numeri che contano sono il
secondo e il terzo. Sulla v2.8.1 la terza revisione faceva 2 su 8.

**Cosa resta aperto e non è stato toccato in questa versione:** `grounded` mai auditato dal
`source-verifier`, nessun avversario sui KILL, cinque campi raccolti e consumati da nessuno,
ordinamenti dei tre tagli del funnel indefiniti, nessuna dimensione temporale nell'evidenza. Sono
difetti di **retrieval**, non di specifica, e nessun linter li vedrà mai: la qualità
dell'ingegneria della specifica ha ormai superato quella del retrieval che la specifica governa.
È lì che il prossimo miglioramento vale di più.

---

## T — v2.14: tre invarianti che ordinavano di scrivere qualcosa che nessuno schema accettava

Questa versione nasce da quattro difetti trovati **eseguendo** il run `20260803`, non leggendo il
repo. Tre dei quattro hanno la stessa forma, e a fine giro erano diventati sei — perché due
riparazioni ne hanno aperto uno nuovo ciascuna, che è il momento in cui un sistema di controlli
guadagna o perde credibilità.

### La forma comune: un obbligo enunciato senza lo slot per assolverlo

| dove | l'obbligo | lo slot mancante | l'effetto |
|---|---|---|---|
| gate 3 | *«trova segmenti dove non esiste soluzione software»* (`config.missione`) | nessun ramo per **zero diretti** | il gate uccideva con `G7` — «già risolto da software moderno» — su una premessa il cui contrario era vero |
| IC-4 | *«ogni fonte irraggiungibile va registrata col proprio `access_status`»* | `UnreachableSource` senza `source_id`; tre motivi su sette non scrivibili in `access_status` | `reconcile_run` la contava come orfana IC-1: il run chiudeva **DEGRADATO per aver fatto la cosa giusta** |
| IC-1 | *«uno statement `VERIFIED` senza citazione risolvibile viene declassato a `HYPOTHESIS`, l'agente non può opporsi»* | sette enum `state` su nove non ammettevano `HYPOTHESIS` | applicare l'invariante faceva **fallire la validazione dell'artefatto** |

È la stessa classe già vista tre volte — competitor `STRONG`, `fact_promotion` `FP0`, gate 3 `G7` —
ma su un asse diverso: lì mancava un **ramo** dell'enumerazione, qui manca un **valore** del
vocabolario. La difesa è la stessa: un'enumerazione deve coprire il proprio spazio, e qualcosa deve
confrontare i file al posto di chi li scrive.

### Le due riparazioni che ne hanno aperta un'altra

**Riconoscere le irraggiungibili come provenienza le fa entrare nel ledger** — e da quel momento
IC-1 le risolve. Un claim `VERIFIED` sostenuto **solo** da pagine che nessuno ha aperto sarebbe
passato: un falso negativo comprato col falso positivo appena tolto. Il contrappeso è nella
riparazione, non in una versione successiva: una fonte irraggiungibile è provenienza valida per
l'**esistenza** della fonte e per nient'altro — IC-3 pretende uno snippet per la coppia (claim,
fonte), e lì non c'è niente da citare. `reconcile_run.py` e `run_close.py` elencano quei claim e
valgono exit 1, e `SC-24` fallisce se uno dei due strumenti perde il contrappeso.

**Restringere `SC-03` poteva accecarlo.** Il falso positivo era reale — l'`1` di `n_direct >= 1`
attribuito a `misalignment_min` sulla riga accanto — ma la via facile (una riga di `IGNORA`) è un
posto dove una divergenza vera può restare per sempre. La riparazione lega i numeri **all'operatore
che li introduce**: `nome >= numero` vincola quel numero a quel nome, mentre le forme in prosa («la
soglia vale 40%») restano candidate. Non è un permesso di scrivere numeri sbagliati, è il rifiuto
di leggerne uno altrui. Una mutazione dedicata scrive un valore davvero divergente sulla stessa
riga a più confronti: se `SC-03` tacesse, la riparazione sarebbe stata una disattivazione.

### `source-verifier` esce dai delegabili — seconda volta che la linea era sull'asse sbagliato

La v2.12 spostò la delegabilità da **costo** a **quota**. La v2.14 aggiunge l'asse che mancava a
entrambe: `ds_call.py` non passa `tools`, quindi il ponte non apre pagine, e il mestiere del
verificatore **è** aprire la pagina. Delegato, confronta lo snippet con il prompt — cioè con se
stesso — e dice sempre di sì. La variante che ripara gli altri cinque (*l'orchestratore scarica, il
modello esterno estrae*) qui non è inadatta ma **contraddittoria**: l'orchestratore fornirebbe la
prova per l'audit della raccolta che ha mediato lui.

La regola diventa **delegabile ⟺ haiku ∧ chiuso sul proprio input**, e `SC-20` verifica la
congiunzione nei due sensi. `provider_switch.py` distingue ora i due rifiuti, perché non sono la
stessa cosa: «troppo importante» si può rivedere guardando la bolletta, «il ponte non può aprire
pagine» no.

Il segnale era già scritto in `DELEGA-DEEPSEEK.md` dalla v2.12.2: *«la difesa a valle avrebbe retto
— il `source-verifier` gira su Anthropic con `WebFetch`»*. Il paragrafo che spiegava perché la
delega fosse sicura **assumeva** che quell'agente non fosse delegato, mentre il config lo
autorizzava. Due affermazioni incompatibili a settanta righe di distanza, scritte lo stesso giorno
dalla stessa mano, nessuna delle due sbagliata da sola.

### `SC-04` guadagna gli enum, ma su contratto

La prima stesura confrontava **ogni** nome di campo ripetuto fra schemi e sparava su sette campi
legittimi: il `type` di un moat e il `type` di un trigger condividono un sostantivo, non un
vocabolario. Un controllo che sbaglia sette volte su otto viene spento entro la settimana, e lascia
in eredità un alibi al posto di una difesa. Quali campi siano davvero **lo stesso campo** è una
decisione di progetto: si dichiara in `config.verifica_specifica.enum_condivisi`, con i valori
canonici e le **restrizioni ammesse** — `Numeric.state` ne ha una (un numero non può essere
un'ipotesi) ed è scritta, perché la differenza fra una scelta e una deriva è che la scelta sta da
qualche parte.

### Numeri

**L'autotest ha ora cinque corpus: 14 storici · 6 indipendenti · 8 della terza revisione · 14 dal
run `20260802-1759` · 11 dal run `20260803` — 53 su 53.** Il quinto corpus contiene anche le
mutazioni che sorvegliano le riparazioni di questa versione, che sono le uniche a cui questo autore
non poteva rispondere in anticipo. Una di esse (`ASSENZA` tolta da uno solo dei due schemi) è
arrivata **cieca al primo giro**: il controllo che ora la riprende è nato per rispondere.

Corretto anche un difetto dell'autotest stesso: la sezione B stampava B+C+D e la C stampava C+D. I
totali erano giusti e le tabelle no — e il modo più rapido di far perdere fiducia a un pannello è
fargli dire due cose diverse nello stesso schermo.

### Cosa resta aperto

Immutato dalla v2.13, e cresce di importanza a ogni versione: `grounded` mai auditato, nessun
avversario sui `KILL`, cinque campi raccolti e consumati da nessuno, nessuna dimensione temporale
nell'evidenza. E una raccomandazione metodologica che nessuno strumento può imporre: **due run sulla
stessa configurazione prima di ricalibrare qualsiasi cosa.** Ogni run finora è girato su una
versione diversa, quindi del sistema non esiste ancora una misura di varianza — solo una sequenza
di aneddoti, ciascuno su un impianto diverso.

---

## U — v2.14.1: il tachimetro, ovvero come si stava per comprare uno strumento al buio

Una revisione ha proposto di aggiungere un fetcher con rendering (Crawl4AI) perché *«WebFetch
perde contenuto sui siti JS-heavy»*, e ha avuto la buona idea di chiedere di **misurarlo prima**.
La misura ha dato tre risultati, e il terzo è il più importante.

**Uno.** Su quattro fonti tipiche italiane — tre listini di gestionali PMI e una ricerca su un job
board — `WebFetch` ha restituito il fatto esatto quattro volte su quattro: prezzi con cifra e
periodicità, e da un annuncio anche la RAL, che è una prova `COMPORTAMENTO` di manuale. Quattro
URL non sono una statistica, ma non sostengono la premessa.

**Due.** Nel run archiviato `20260801`, delle 53 dichiarazioni di irraggiungibilità solo 32 sono
rifiuti veri, e di quei 32 un browser con rendering ne recupererebbe al massimo 13: `BLOCKED` e
`LOGIN_REQUIRED`. `NO_PUBLIC_DATA` e `NOT_FOUND` — 18 casi — non li recupera nessuna tecnologia,
perché il dato non esiste. **Tetto teorico ~13%**, in un run dove **12 artefatti su 18 si sono
fermati per `BUDGET_EXHAUSTED`**: il vincolo che morde è il budget di ricerche, non la lettura.

**Tre, e qui la domanda si è ribaltata.** Per rispondere davvero servivano due numeri che il
sistema non produce: quante pagine apre, e quali fonti abbia davvero letto invece di intravedere
nella SERP. Gli slot esistono — l'envelope pretende `fetches`, il §3 pretende `snippet_source` su
ogni voce di `claim.evidence[]` — ma **nessuno verificava che fossero compilati**. Non è la stessa
cosa dei tre difetti della v2.14 (lì lo slot mancava del tutto): qui lo slot c'è e nessuno guarda
se qualcuno lo riempie. Variante più insidiosa, perché la specifica *sembra* a posto.

Il `run_close` chiude ora con un **tachimetro**: quanti artefatti dichiarano `fetches`, quanti
claim hanno `evidence[]` popolato, e la quota di pagine effettivamente aperte, letta in tre bande.
Tre e non due: una soglia secca dava letture opposte a un caso di distanza — a 1 `FETCHED` su 5
diceva «quota alta» e a 1 su 6 «il collo di bottiglia non è la lettura». Un indicatore che si
ribalta su un caso in più non è una misura, e la banda di mezzo è l'unica risposta onesta quando
il dato non decide.

È un **avviso, non una post-condizione**, per una ragione di calendario: il primo run dopo
l'accensione avrà i contatori a zero per motivi che non sono sciatteria, e far chiudere DEGRADATO
un run buono per un motivo formale insegna soltanto a ignorare il verdetto. Un run con zero claim
`VERIFIED` non prende alcun avviso: il §10 dichiara **riuscito** un run che si svuota onestamente,
e punirlo qui contraddirebbe la regola d'oro.

`SC-08` sorveglia che il tachimetro non sparisca, con la sua mutazione nell'autotest — perché un
avviso che qualcuno toglie *«tanto è solo un avviso»* riporta il sistema a discutere di retrieval
per aneddoti, che è esattamente il punto di partenza.

**Verdetto su Crawl4AI: non ora.** Non perché sia un cattivo strumento, ma perché il caso a favore
poggiava su un'ipotesi non misurata, e ora c'è lo strumento per misurarla. Due run sulla v2.14.1
producono da soli il numero che decide.

**Autotest: 54 su 54** — quinto corpus a 12.

---

## V — v2.15: la funzione di perdita era invertita, ed era ereditata da un sistema che non esiste più

Red team esterno condotto sui file **e sui tre run archiviati** (`20260801`, `20260802-1759`,
`20260803-1204`). Il verbale completo è in `docs/RED-TEAM-v2.14.1.md`. `spec_check` sulla v2.14.1
usciva **0 errori su 25 controlli**: tutto ciò che segue passava pulito.

### La tesi, che nessuna delle 8 lenti aveva formulato

`RIPENSAMENTO-8-LENTI.md` aveva diagnosticato che il sistema non sa distinguere un no del mercato da
un no dello strumento. La v2.14.1 ha in parte risolto: nel run `20260803` P-023 esce con
`kill_basis: ASSENZA` e `kill_attribution: STRUMENTO`, e la motivazione — *«non dice che il target
non ha parole per il problema: dice che non le abbiamo trovate»* — è una diagnosi corretta.

Poi il sistema ha fissato `retry_after` a quattordici giorni **e non ha cambiato niente**. Al
rientro, stesso budget, stesso PARK. `missing_to_decide` chiedeva testualmente *«budget ≥ 6
ricerche»*, e nessun componente legge quel campo per cambiare un budget.

> Il sistema ha prodotto un'autodiagnosi corretta, precisa e azionabile, e non aveva **nessun
> meccanismo per agire su di essa**. Lo zero era già interpretato. Non serviva a niente, perché
> l'interpretazione non aveva un destinatario.

La causa a monte è la funzione di perdita. Otto rilevatori esistono per «idea passata a torto»;
**zero** per «idea uccisa a torto» — il repo lo scrive come aperto dalla v2.9. Ma un falso positivo
costa dieci minuti di lettura, perché **l'ultimo gate è un umano che legge, ed è gratuito**; un falso
negativo costa tutto, in silenzio. L'asimmetria reale è l'opposto di quella costruita nella macchina.

E non è sbagliata: è **scaduta**. Era corretta per il monolite, dove un'idea allucinata era
indistinguibile da una vera perché non c'erano etichette. Questa pipeline ha poi costruito le
etichette — quattro stati, `evidence_route`, moltiplicatore di confidenza, verificatore cieco — e
**le etichette rendono obsoleta la soppressione**: oggi si può mostrare un'idea debole scritta come
debole. Nessuno ha ritirato la difesa vecchia. `QUALITÀ SOPRA QUANTITÀ` era una regola contro la
sicurezza ingiustificata ed era stata implementata come una regola contro l'output.

### La riparazione strutturale: il portafoglio per profondità di sopravvivenza

Il report si apre **sempre** con le migliori cinque cose trovate, ordinate per gate massimo raggiunto
→ codice di caduta → claim `VERIFIED`. Ogni voce porta dove è morta, `kill_basis`/`kill_attribution`,
e l'**azione più economica che la sbloccherebbe**, con il costo in ricerche e il flag
`fuori_scrivania` quando l'azione è una telefonata.

Non si allenta niente: `PUBBLICATA` resta ≥ 6.5 con confidenza ≥ 0.70 e 4/5 fontate, e la frase
*«Nessuna opportunità ha superato i criteri»* resta obbligatoria. Cambia che il lavoro fatto smette
di essere buttato. In tre run reali su tre le idee arrivate al ranking sono state **zero**: quattro
etichette di esito su cinque non sono mai state emesse, e il formato della scheda è stato esercitato
solo nel suo ramo vuoto. Un sistema che consegna solo cose finite, in un dominio dove niente è mai
finito, consegna niente.

È il recupero della parte che il verificatore delle 8 lenti aveva **salvato** dalla proposta
«classifica permanente», scartandone giustamente il punteggio parziale rinormalizzato.

### I quattro difetti concreti, tutti invisibili a spec_check

**D1 — S5 riaperto, e su LIGHT.** `funnel._nota_budget` enuncia che il tetto deve stare sopra la
somma dei budget per istanza e chiama la violazione *«il guasto più insidioso possibile»*. Stato
misurato: ricerche **438/300** in LIGHT, **860/700** in BALANCED, 1624/1600 in DEEP; fetch
**798/500**, **1556/1000**, **2932/1800**. Sette violazioni su sei tetti. Causa: la v2.14 ha portato
`evidence_searches_per_channel` a 8 — +960 ricerche e +1920 fetch nel solo stage evidenze in DEEP —
e ha alzato **un tetto su sei**. E il divisore che nessuno aveva scritto: il freno all'80% smette di
aprire idee, quindi il budget reale è `0.8 × tetto`, e con tetto ≈ somma il freno scatta a metà wave
3 **su ogni run**. Tetti ricalcolati su entrambe le unità e su tutti e tre i profili.

**D2 — il gate 2 poteva uccidere e tre file dicevano di no.** Con budget 8: finestra 2, minimo 6,
`8 ≥ 6` in tutti i profili. Ma l'orchestratore §1 e due note di `stop_rules` affermavano
l'inemettibilità come un fatto permanente — vera a budget 3, mai propagata dopo la v2.14. Il run
`20260803-1204` ha applicato la frase invece del calcolo. Ora l'emettibilità si **calcola** a ogni
run, per ogni agente, e finisce nel manifesto.

**D3 — la quinta occorrenza di un obbligo senza slot.** `kill_basis` e `kill_attribution` erano
prescritti dall'orchestratore, citati da `condizione_di_riprogettazione` come base per decidere se il
difetto sia nella pipeline o nel bersaglio, e non esistevano in **nessuno** schema né erano letti da
**nessuno** strumento: finivano in file inventati caso per caso. Dopo competitor `STRONG`,
`FP0`, gate 3 `G7` e IC-4 senza `source_id`, stavolta il difetto cadeva sull'unico strumento
diagnostico che il sistema ha su sé stesso. Ora c'è `core.schema.json#/$defs/KillAnalysis`, lo
schema `gate-decision.schema.json`, e `run_close.py` stampa il **quadro dei no** — con il ripiego che
legge anche la forma piatta dei tre run storici, perché una misura disponibile solo da domani è una
misura disponibile quando non serve.

**D4 — il record di memoria era l'unico artefatto senza schema.** Undici schemi per artefatti
effimeri, zero per l'unico che deve sopravvivere al run. Aggiunto `seen-pain.schema.json`, con
`park_count` e `park_reason_history`: al secondo PARK sulla stessa `missing_to_decide` il pain esce
dal funnel di scrivania. Senza quel contatore il ciclo `parcheggio → 14 giorni → stessa ricerca →
stesso parcheggio` è possibile e **strutturalmente invisibile**.

### Il filtro che nessuno guardava

**8 pain su 14** muoiono al taglio wave 1 nel run `20260803`, **12 su 15** nel `20260801`: è il
canale di perdita dominante, uccide cinque volte più di qualunque gate, e nessun gate lo vede.
Ordinava per `0.45 perdita documentata (VERIFIED > ESTIMATED > UNKNOWN) + 0.35 spesa + 0.20
ricorrenza`: **`evidence_kind` non compariva**, cioè il 45% del peso stava sulla scala di
documentabilità che la v2.1 aveva dichiarato l'asse sbagliato. La correzione era entrata nel gate 2,
nel gate 3 e nelle regole di promozione — e non qui, mentre il config stesso scriveva due righe sopra
che *«il gate non taglia il funnel mentre l'ordinamento sì»*.

Nuovo ordinamento: `0.30 spesa + 0.30 tracce di COMPORTAMENTO da attori distinti + 0.25 perdita
quantificata (VERIFIED 1.0 · ESTIMATED 0.7) + 0.15 ricorrenza`, e l'orchestratore scrive i quattro
addendi separati per ogni pain — era l'unico punto del sistema dove un'idea moriva senza motivazione.

### Numeri

**`SC-25`** (tetti contro budget per istanza, su entrambe le unità, con il divisore del freno) e
**`SC-26`** (emettibilità dei KILL calcolata invece che asserita, più l'esistenza e la lettura di
`KillAnalysis`). **Autotest: 59 su 59**, sesto corpus a 5. Delle cinque mutazioni nuove, **tre
passavano cieche sulla v2.14.1** — sono le uniche a cui l'autore delle riparazioni non poteva
rispondere in anticipo, ed è l'unico numero che conta.

`SC-26` ha sparato al primo giro sulla citazione della frase rimossa dentro il verbale del red team:
un controllo che non distingue l'affermazione dalla citazione viene spento entro la settimana, quindi
onora l'esenzione per riga come `SC-03` e `SC-08`.

### Cosa resta aperto, e non è stato toccato

Il portafoglio rende il run leggibile. **Non alza il soffitto.** Restano intatte: la resa della wave
1 (3 pain per shard su un tetto di 12), le proposte 3, 5, 6 e 7 del documento a 8 lenti, e la critica
di completezza numero 1 — **il tasso di conversione idea → prodotto costruito non compare in nessun
file**, e se vale zero tutto il resto è taratura della mira di un cannone senza polvere.
