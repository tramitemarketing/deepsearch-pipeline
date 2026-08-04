#!/usr/bin/env python3
"""
_chiavi.py — l'UNICO posto che sa dove sta una chiave di provider.

PERCHE' ESISTE.

La stessa domanda — "questa chiave e' impostata?" — era scritta in tre file con tre risposte
diverse: `ds_call.py` guardava ambiente e poi `.env`, `setup.py --stato` guardava entrambi,
`provider_switch.py` guardava solo l'ambiente. Risultato: chi impostava la chiave con
`setup.py --chiave` (che scrive in `.env`) si vedeva dire da `provider_switch` che la chiave non
c'era, mentre il run sarebbe partito benissimo.

E' la classe di difetto che questo repo insegue da dodici versioni — una regola scritta in piu'
posti che diverge — comparsa in un angolo dove nessun controllo guardava, perche' `spec_check`
confronta config, schemi e prompt, non il codice degli strumenti fra loro. Qui la regola sta in
una funzione sola: gli strumenti non hanno piu' un'opinione propria su dove cercare.

Formati accettati in `.env`, perche' si incolla quello che si ha sottomano:
    DEEPSEEK_API_KEY=sk-...
    DEEPSEEK_API_KEY="sk-..."
    export DEEPSEEK_API_KEY=sk-...        <- copiato dalla documentazione
    DEEPSEEK_API_KEY = sk-...             <- spazi attorno all'uguale
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")


def _righe_env():
    if not os.path.exists(ENV):
        return {}
    d = {}
    # utf-8-sig: il Blocco note di Windows salva con il BOM, e senza questo la PRIMA riga del
    # file diventa illeggibile — cioe' proprio la chiave, se e' l'unica cosa che c'e' dentro.
    for l in open(ENV, encoding="utf-8-sig"):
        l = l.strip()
        if not l or l.startswith("#") or "=" not in l:
            continue
        if l.lower().startswith("export "):
            l = l[7:]
        k, _, v = l.partition("=")
        d[k.strip()] = v.strip().strip('"').strip("'").strip()
    return d


def trova_chiave(nome):
    """(valore, dove) — dove e' 'ambiente', '.env' oppure None. L'ambiente vince."""
    v = os.environ.get(nome)
    if v and v.strip():
        return v.strip(), "ambiente"
    v = _righe_env().get(nome)
    if v:
        return v, ".env"
    return None, None


def diagnosi(nome):
    """Righe leggibili su DOVE si e' cercato e cosa si e' trovato. Serve quando la chiave 'c'e''
    ma nessuno la vede: quasi sempre e' setx (che vale dalla PROSSIMA finestra, non da questa)
    oppure un .env nella cartella sbagliata."""
    fuori = []
    v, dove = trova_chiave(nome)
    fuori.append(f"  cerco {nome} in due posti, in quest'ordine:")
    amb = os.environ.get(nome)
    fuori.append(f"    1. variabile d'ambiente ... {'trovata' if amb else 'assente'}")
    if os.path.exists(ENV):
        chiavi = _righe_env()
        fuori.append(f"    2. {ENV}")
        fuori.append(f"       il file esiste e contiene: "
                     f"{', '.join(chiavi.keys()) if chiavi else 'nessuna chiave leggibile'}")
        if nome not in chiavi:
            simili = [k for k in chiavi if k.upper().replace("-", "_") == nome.upper()]
            if simili:
                fuori.append(f"       ATTENZIONE: c'e' '{simili[0]}' ma serve esattamente '{nome}'")
    else:
        fuori.append(f"    2. {ENV} ... IL FILE NON ESISTE")
        fuori.append("       (deve stare nella radice del repo, non in tools/ e non dove lanci "
                     "il comando)")
    if v:
        fuori.append(f"\n  TROVATA in {dove}: {v[:7]}…{v[-4:]}")
    else:
        fuori.append("\n  NON TROVATA. Le due cause quasi sempre sono:")
        fuori.append("    · hai usato `setx`: vale dalla PROSSIMA finestra del terminale, non da")
        fuori.append("      questa. Chiudi e riapri, oppure usa `python tools/setup.py --chiave`.")
        fuori.append("    · il .env sta in un'altra cartella. Deve essere esattamente quello")
        fuori.append(f"      indicato qui sopra.")
    return "\n".join(fuori)
