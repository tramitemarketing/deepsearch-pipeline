---
description: Esegue tutte le prove di verifica del repository tramite tools/gate_all.py e riporta esito e rapporto integrali.
allowed-tools: Bash
---

# GATE — le verifiche del repository

**Riguarda le verifiche del REPOSITORY — selftest e controlli di conformita' del codice — non i
gate di stadio della pipeline, che vivono in `docs/STAGE-GATES.md` e giudicano i claim.**

Esegui `tools/gate_all.py` **dalla radice del repository**, cioe' la directory che contiene
`tools/`, `config/` e `schemas/`.

## L'interprete

Su Windows si chiama `python`, non `python3`. Prova nell'ordine e usa **il primo che risponde**:

```
python  tools/gate_all.py
py -3   tools/gate_all.py
python3 tools/gate_all.py
```

Se nessuno risponde, Python non e' installato: fermati e dillo. Non cercare altre vie.

## Che cosa NON devi fare

- **Non elencare le prove.** L'elenco vive nel censimento di `tools/gate_all.py` e in nessun altro
  posto: e' l'intera ragione per cui quello strumento esiste. Un elenco scritto qui sarebbe una
  seconda copia da tenere aggiornata a mano, cioe' il difetto che l'aggregatore chiude.
- **Non riassumere il rapporto.** Riportalo **integralmente e verbatim**, riga per riga, comprese
  le verdi e le durate. Un rapporto riassunto non e' verificabile, e questa catena ha misurato
  piu' volte che il riassunto e' il punto in cui i fatti si perdono.
- **Non riparare nulla** in questa invocazione. Se una prova e' rossa, riporta il suo `stderr`
  cosi' come `gate_all` lo stampa e fermati: la riparazione e' un'altra cosa e va decisa a parte.

## L'esito

Riporta il codice d'uscita **esatto**, con la lettura che `gate_all` gli da':

- **0** — tutte verdi.
- **1** — almeno una prova fallita. E' una **misura**: un artefatto e' sbagliato.
- **2** — inventario o esclusioni incoerenti; nulla e' stato eseguito. Tipicamente un `.py` nuovo
  sotto `tools/` che nessuno ha classificato: e' il caso per cui il censimento esiste.
- **3** — errore di esecuzione o di tooling. E' un **guasto**: non si e' potuto eseguire. Si
  ripara in un posto diverso da `1`, ed e' per questo che i due codici sono distinti.

## Perimetro

Il comando **legge ed esegue le prove, e nient'altro**. Non e' innestato nei run, non e' invocato
da `spec_check.py`, da `run_close.py` o da alcun hook, e non deve diventarlo senza una decisione
esplicita: un fallimento di verifica del repository non deve bloccare un run di business, e tre
minuti davanti a ogni run o a ogni commit sarebbero un costo che nessuno ha autorizzato.

Il giro costa circa tre minuti. Non lanciarlo in parallelo ad altro lavoro pesante: alcune prove
generano processi veri, e le durate del rapporto diventerebbero illeggibili.
