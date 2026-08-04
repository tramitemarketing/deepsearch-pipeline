# v2.9.1 — NON applicare a run in corso

Questa versione nasce dal run reale `20260802-1759`. **Non sostituire i file mentre il run gira:**
gli agenti leggono prompt, schemi e config a ogni dispatch, e cambiarli a meta' produce wave che
girano su regole diverse — un run non interpretabile, che e' precisamente il guasto che
`run_close.py` esiste per rendere visibile.

## Ordine

1. Lascia finire il run. Raccogli `REPORT.md` e l'uscita di `run_close.py --fase finale`.
2. Copia `state/` e `runs/20260802-1759/` da parte: sono i primi dati reali che questo sistema abbia
   prodotto e valgono piu' del codice.
3. Sostituisci il repo con questa versione.
4. `python tools/spec_check.py` deve uscire 0, `python tools/spec_check_selftest.py` 31/31.

## Se `python3` non esiste su Windows

`§11` chiude il run con `python3 tools/run_close.py`, e su Windows quel comando spesso non esiste:
c'e' `python`, oppure il launcher `py`, oppure niente e il sistema apre il Microsoft Store. Un run
che non riesce a chiudere non puo' scrivere `state/`, quindi **la memoria non si accende** — dopo
tutto il lavoro fatto per riaccenderla.

Prova in quest'ordine, dalla cartella `deepsearch-pipeline`:

```
python  tools\run_close.py runs\<run_id> --fase finale
py -3   tools\run_close.py runs\<run_id> --fase finale
```

Il primo che stampa il quadro delle post-condizioni e' quello giusto: usa **quello** anche per
`spec_check.py`. Se nessuno dei due funziona, Python non e' installato: `winget install Python.Python.3.12`,
poi riapri il terminale. Non saltare il passo e non scrivere `state/` a mano — uno stato scritto da
un run mai validato propaga il proprio guasto a tutti i run successivi, senza che nulla lo segnali.

I due strumenti usano solo la libreria standard: nessuna dipendenza da installare.
