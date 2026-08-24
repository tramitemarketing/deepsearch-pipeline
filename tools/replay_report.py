#!/usr/bin/env python3
"""
replay_report.py — il driver rieseguibile che percorre i canali di un replay gia' su disco e
registra la validazione.

PERCHE' ESISTE. Fino a ieri il ciclo sui canali non era un artefatto: era un comportamento. Una
verifica in sola lettura (`35-EVIDENZA-PUNTO-DI-ESECUZIONE-ASSENTE.md`) ha mostrato che nessun
file del repository percorreva i canali, scriveva `03-validazione.json` o invocava il guard e il
runner — quel file lo componeva l'orchestratore a mano, run per run. `replay_validate.py` era
quindi un passo corretto che nessuno eseguiva. Questo file lo esegue, cinque volte, in un ordine
dichiarato, e scrive quello che ha trovato.

METE' B, E SOLO B. Il replay ha due meta': **A** invoca (guard → runner → CLI: rete, run reali,
un solo colpo per canale, irripetibile) e **B** valida e registra a partire dai raw gia' su disco
(offline, puro, rieseguibile all'infinito). Questo e' B. Non tocca la rete, non invoca agenti,
non consuma tentativi, e sui suoi ingressi puo' girare mille volte dando mille volte lo stesso
file.

QUATTRO SCELTE CHE NON SONO DI COMODO.

  1. L'ELENCO DEI CANALI VIENE DAL MANIFESTO. Non dal glob di `02-output/`. Se venisse dal glob,
     un canale che non ha prodotto NULLA sarebbe invisibile — e quello e' esattamente il caso che
     questo driver esiste per registrare. Un driver che scopre i canali dai file prodotti non puo'
     dire `NON_VALUTABILE`: puo' solo tacere, che e' la forma di omissione che IC-4 vieta alle
     fonti e che si era ripresentata al livello del run.

  2. `replay_validate` E' IMPORTATO, non lanciato in sottoprocesso. Cosi' l'ordine
     null-check → schema, che `V13` prova strumentando le due funzioni, e' EREDITATO invece che
     ristabilito da capo con la speranza di non sbagliarlo.

  3. IL VERDETTO PIU' GRAVE VINCE. Un pack invalido rende il run `NON_VALIDO` anche accanto a un
     canale non valutabile. Il contrario — un canale muto che zittisce un `null` illecito — e' il
     difetto `R1` di `replay_validate.py` promosso di un livello: un guasto che annulla un
     verdetto gia' preso. Li' e' stato corretto; qui non va reintrodotto — e la forma insidiosa
     non e' la precedenza fra gli esiti, che si legge, ma l'ECCEZIONE che sfugge: un raw che non
     e' UTF-8 fa morire il processo e porta via con se' i verdetti dei canali gia' fatti. Vedi
     `valuta`.

  4. NON SI SCRIVE SU CIO' CHE NON E' PROPRIO, e il divieto non e' agganciato al nome
     `03-validazione.json`. Un divieto per nome protegge un nome. Questo e' agganciato alla MARCA
     dentro il file: se l'uscita esiste e non porta `"marca": "replay_report"`, ci si ferma senza
     toccarla — che protegge anche gli artefatti fatti a mano che non esistono ancora.

ESITO DEL RUN, e la ragione per cui i codici sono tre e non quattro.

  COMPLETO   tutti i canali valutati e validi                      exit 0
  PARZIALE   almeno un NON_VALUTABILE, nessun pack invalido         exit 1
  NON_VALIDO almeno un pack invalido                                exit 1
                                                                    exit 2 = il driver non ha
                                                                    potuto operare

`PARZIALE` esce 1 e non 0 perche' 0 significa «puoi fidarti di questa misura», e una misura a cui
mancano due canali su cinque non e' una misura di cui fidarsi. `NON_VALUTABILE` non esce 2 perche'
2 significa «l'invocazione e' rotta», e un canale senza pack e' un fatto sul run misurato, non un
guasto del misuratore: promuoverlo a 2 farebbe sparire il verdetto del null-check dietro il canale
piu' debole. La distinzione fra «incompleto» e «invalido» sta dove c'e' spazio per dirla, cioe'
nel file.

  uso:  python tools/replay_report.py --run <dir-del-replay>
                                      [--contratto F] [--schemi D] [--out F]

Il default di `--out` e' `<run>/05-validazione-rieseguibile.json`, cioe' la clausola del mandato.
Puntarlo FUORI dalla directory del replay e' ammesso di proposito: e' il modo di misurare un run
senza scrivergli dentro, che serve esattamente quando `runtime/` e' fuori perimetro.
"""
import argparse, contextlib, hashlib, io, json, os, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay_validate                                                    # noqa: E402

VERSIONE = "v1.0.0"
MARCA = "replay_report"
MANIFESTO = "00-replay-manifest.json"
USCITA = "05-validazione-rieseguibile.json"
CONTRATTO = os.path.join("_contracts", "evidence-researcher.delivery.txt")
OUTPUT = "02-output"


class Guasto(Exception):
    """Il driver non puo' operare. Exit 2. Distinto da «il run e' messo male», che e' 1."""


def leggi_manifesto(percorso):
    """`run_id`, baseline e l'elenco ORDINATO dei canali. Nessun default, nessuna invenzione."""
    try:
        with open(percorso, "rb") as f:
            m = json.loads(f.read().replace(b"\r\n", b"\n").decode("utf-8"))
    except OSError as e:
        raise Guasto(f"manifesto non leggibile: {e}")
    except json.JSONDecodeError as e:
        raise Guasto(f"manifesto non deserializzabile: {e}")
    if not isinstance(m, dict):
        raise Guasto("il manifesto non e' un oggetto")
    canali = m.get("canali")
    if not isinstance(canali, list) or not canali:
        raise Guasto(f"il manifesto non dichiara un elenco di canali: {MANIFESTO} → `canali`")
    nomi = []
    for c in canali:
        nome = c.get("channel") if isinstance(c, dict) else c
        if not isinstance(nome, str) or not nome:
            raise Guasto(f"canale senza nome nel manifesto: {c!r}")
        # Il nome di canale diventa un percorso. Un nome che contiene un separatore farebbe
        # leggere fuori dalla directory del replay e scriverebbe nel record un `raw` che non
        # descrive piu' nulla: si rifiuta invece di normalizzarlo in silenzio.
        if "/" in nome or "\\" in nome or nome in (".", ".."):
            raise Guasto(f"nome di canale non ammesso, esce dalla directory del replay: {nome!r}")
        nomi.append(nome)
    return m.get("run_id"), m.get("commit_di_provenienza"), nomi


def rel_al_run(run, percorso):
    """Il percorso relativo al run, se ci sta dentro; altrimenti il solo nome del file.

    Mai un percorso assoluto: renderebbe il record dipendente dalla macchina che l'ha prodotto,
    e questi file si confrontano fra macchine.
    """
    p, r = os.path.abspath(percorso), os.path.abspath(run)
    try:
        if os.path.commonpath([p, r]) == r:
            return os.path.relpath(p, r).replace("\\", "/")
    except ValueError:                        # volumi diversi: non c'e' un relativo
        pass
    return os.path.basename(p)


def impronta(percorso):
    with open(percorso, "rb") as f:
        b = f.read()
    return hashlib.sha256(b).hexdigest(), len(b)


def valuta(raw, contratto, schemi):
    """Un canale, delegando a `replay_validate.main`.

    Lo stdout e lo stderr del passo vengono catturati: il primo porta il rapporto, il secondo il
    motivo di un eventuale guasto. Chiamare `main` invece dei due controlli separati e' cio' che
    fa EREDITARE l'ordine null-check → schema, provato la' e non riprovato qui.

    NESSUNA ECCEZIONE DI CANALE PUO' ANNULLARE IL RUN. `replay_validate.main` cattura `Guasto` e
    `OSError`, non tutto: un raw che non e' UTF-8 alza `UnicodeDecodeError`, un JSON abissale alza
    `RecursionError`, ed entrambi sono cose che un CLI puo' produrre davvero. Lasciarle passare
    farebbe morire il processo con un traceback, **buttando i verdetti gia' presi sui canali
    precedenti e non scrivendo alcun file** — cioe' il difetto `R1` promosso di un livello, quello
    che il punto 3 di questo docstring dichiara di non voler reintrodurre. Un canale che non si
    lascia leggere e' un fatto sul run misurato, esattamente come il canale senza pack: si
    registra `NON_VALUTABILE` e si va avanti. La cattura e' larga di proposito, e non e' muta: il
    tipo e il messaggio dell'eccezione finiscono nel `motivo`, quindi nulla sparisce in silenzio.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            codice = replay_validate.main(["--raw", raw, "--contratto", contratto,
                                           "--schemi", schemi])
    except Exception as e:                                                # noqa: BLE001
        return 2, None, f"{type(e).__name__}: {e}"
    if codice == 2:
        motivo = err.getvalue().strip() or "guasto non dichiarato"
        if motivo.startswith("guasto: "):
            motivo = motivo[len("guasto: "):]
        return 2, None, motivo
    try:
        return codice, json.loads(out.getvalue()), None
    except json.JSONDecodeError as e:
        return 2, None, f"rapporto di canale non deserializzabile: {e}"


def controlla_uscita(percorso):
    """Si scrive solo su ciò che e' proprio. La prova e' la marca, non il nome."""
    if not os.path.exists(percorso):
        return
    try:
        with open(percorso, "rb") as f:
            d = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Guasto(f"l'uscita esiste e non e' un rapporto di questo strumento, "
                     f"quindi non la tocco: {os.path.basename(percorso)}")
    if not isinstance(d, dict) or d.get("marca") != MARCA:
        raise Guasto(f"l'uscita esiste e non porta la marca `{MARCA}`, "
                     f"quindi non la tocco: {os.path.basename(percorso)}")


def scrivi_atomico(percorso, testo):
    """Prima interamente accanto, poi un solo `os.replace`.

    Una scrittura interrotta non deve lasciare un mezzo file che somiglia a un record; e se la
    sostituzione fallisce, la destinazione precedente resta quella che era e il parziale sparisce.
    """
    parziale = percorso + ".parziale"
    # Anche il parziale e' un file dentro la directory del replay, e la regola «non si scrive su
    # cio' che non e' proprio» non ha eccezioni per i file di servizio. La stessa guardia: se
    # esiste e porta la marca e' un nostro residuo e si puo' riusare, altrimenti ci si ferma.
    controlla_uscita(parziale)
    try:
        with open(parziale, "w", encoding="utf-8", newline="\n") as f:
            f.write(testo)
        os.replace(parziale, percorso)
    except OSError as e:
        with contextlib.suppress(OSError):
            os.remove(parziale)
        raise Guasto(f"scrittura non riuscita, destinazione lasciata intatta: {e}")


def componi(run, contratto, schemi, canali, run_id, baseline):
    """Percorre i canali NELL'ORDINE DEL MANIFESTO e restituisce il rapporto di run."""
    sha_con, _ = impronta(contratto)
    righe, non_valutabili, errori = [], 0, 0
    for nome in canali:
        raw = os.path.join(run, OUTPUT, nome + ".raw.txt")
        rel = f"{OUTPUT}/{nome}.raw.txt"
        if not os.path.isfile(raw):
            righe.append({"canale": nome, "raw": rel, "esito": "NON_VALUTABILE",
                          "exit": 2, "motivo": "il raw non esiste"})
            non_valutabili += 1
            continue
        try:
            sha, byte = impronta(raw)
        except OSError as e:
            righe.append({"canale": nome, "raw": rel, "esito": "NON_VALUTABILE", "exit": 2,
                          "motivo": f"raw non leggibile: {e}"})
            non_valutabili += 1
            continue
        codice, rap, motivo = valuta(raw, contratto, schemi)
        if codice == 2:
            righe.append({"canale": nome, "raw": rel, "esito": "NON_VALUTABILE", "exit": 2,
                          "motivo": motivo, "sha256_raw": sha, "byte_raw": byte})
            non_valutabili += 1
            continue
        errori += rap["schema"]["n_errori"]
        righe.append({"canale": nome, "raw": rel, "esito": rap["verdetto"], "exit": codice,
                      "sha256_raw": sha, "byte_raw": byte,
                      "null_check": rap["null_check"], "schema": rap["schema"]})

    invalidi = sum(1 for r in righe if r["esito"] == "NON VALIDO")
    if invalidi:
        esito = "NON_VALIDO"
    elif non_valutabili:
        esito = "PARZIALE"
    else:
        esito = "COMPLETO"
    return {
        "strumento": f"replay_report.py {VERSIONE}",
        "marca": MARCA,
        "run_id": run_id,
        "baseline": baseline,
        "manifesto": MANIFESTO,
        # il contratto REALMENTE usato, non la costante: `--contratto` e' un'opzione offerta
        # dall'interfaccia, e un record che dichiara un ingresso che non ha usato mente
        "contratto": rel_al_run(run, contratto),
        "sha256_contratto": sha_con,
        "schema": replay_validate.SCHEMA,
        "ordine": replay_validate.ORDINE,
        "esito_run": esito,
        "canali_dichiarati": len(canali),
        "canali_valutati": len(canali) - non_valutabili,
        "canali_non_valutabili": non_valutabili,
        "canali_non_validi": invalidi,
        "totale_errori": errori,
        "canali": righe,
    }, esito


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="valida i canali di un replay gia' su disco e registra l'esito")
    ap.add_argument("--run", required=True, metavar="DIR", help="la directory del replay")
    ap.add_argument("--contratto", metavar="FILE",
                    help=f"default: <run>/{CONTRATTO.replace(os.sep, '/')}")
    ap.add_argument("--schemi", metavar="DIR", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas"))
    ap.add_argument("--out", metavar="FILE", help=f"default: <run>/{USCITA}")
    a = ap.parse_args(argv)

    run = a.run
    contratto = a.contratto or os.path.join(run, CONTRATTO)
    uscita = a.out or os.path.join(run, USCITA)
    try:
        if not os.path.isdir(run):
            raise Guasto(f"la directory del replay non esiste: {run}")
        if not os.path.isfile(contratto):
            raise Guasto(f"contratto non trovato: {contratto}")
        run_id, baseline, canali = leggi_manifesto(os.path.join(run, MANIFESTO))
        controlla_uscita(uscita)
        rapporto, esito = componi(run, contratto, a.schemi, canali, run_id, baseline)
        scrivi_atomico(uscita, json.dumps(rapporto, ensure_ascii=False, indent=2) + "\n")
    except Guasto as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2

    print(f"{esito} — {rapporto['canali_valutati']}/{rapporto['canali_dichiarati']} valutati, "
          f"{rapporto['canali_non_valutabili']} non valutabili, "
          f"{rapporto['canali_non_validi']} non validi, "
          f"{rapporto['totale_errori']} errori di schema → {uscita}", file=sys.stderr)
    return 0 if esito == "COMPLETO" else 1


if __name__ == "__main__":
    sys.exit(main())
