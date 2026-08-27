#!/usr/bin/env python3
"""
replay_offline.py — UN punto d'ingresso rieseguibile per la META' B del replay.

PERCHE' ESISTE. Il replay ha due meta'. **A** invoca: guard, runner, la CLI dell'agente, la rete,
un solo colpo per canale, irripetibile. **B** valida e registra a partire dai raw gia' su disco:
offline, pura, rieseguibile all'infinito. `replay_report.py` ha dato a B un punto d'esecuzione per
la sola validazione; la riparazione e la rivalidazione restavano un COMPORTAMENTO — l'orchestratore
invocava `pack_repair` a mano, canale per canale, e componeva il derivato a giudizio. Questo file
compone l'intera meta' B: validazione → riparazione → rivalidazione, in un ordine dichiarato, e
scrive quello che ha trovato.

OFFLINE, E IL REPLAY PADRE E' IN SOLA LETTURA. Nessuna rete, nessun agente, nessun run reale,
nessun tentativo consumato. Il driver non scrive un byte fuori da `<run>/_offline-repair/`, e non
copia ne' ricrea `_contracts/`.

SEI SCELTE CHE NON SONO DI COMODO.

  1. L'AVVIO E' IN DUE PASSI, e il passo di SOLA LETTURA precede la rivendicazione. Manifesto,
     contratto e schemi si leggono e si misurano PRIMA di creare qualunque cosa: un guasto li'
     vale 2 e non lascia traccia. Nessun dato del manifesto iniziale dipende da un passo
     successivo — altrimenti il manifesto non potrebbe essere scritto per primo, e senza manifesto
     scritto per primo un'interruzione lascerebbe una directory di cui nessuno rivendica la
     proprieta'.

  2. L'IDENTITA' DI ESECUZIONE E' UN INSIEME DI CONTENUTI, NON DI PERCORSI. Sei campi, tutti hash
     o valori letti dal manifesto padre. Copiare `_offline-repair/` sotto un altro run con
     manifesto o raw diversi produce exit 2, e il rifiuto poggia sui DIGEST: se manifesto, raw,
     contratto e schemi sono byte-identici, l'identita' e' la stessa e non si inventa una
     differenza fondata sulla posizione fisica del checkout.

  3. `sha256_schemi` NON E' RICORSIVO, e non e' una semplificazione: `replay_validate` legge gli
     schemi con `os.listdir`, cioe' i soli `*.json` di PRIMO LIVELLO. L'identita' sorveglia cio'
     che e' stato MISURATO, non cio' che avrebbe potuto esserlo. Uno schema annidato che nessuno
     legge non deve cambiare l'identita'.

  4. UN GUASTO DI CANALE VALE 1, NON 2. `pack_repair` puo' fallire in due modi — restituire `2`,
     oppure SOLLEVARE (`replay_validate.Guasto`, `RecursionError`: importato, non degrada a codice
     d'uscita) — e in entrambi i casi il driver registra un guasto per QUEL canale, conserva il raw
     originale byte per byte, prosegue sugli altri e conclude con 1. `2` resta riservato al guasto
     operativo del driver: un canale che non si e' potuto riparare e' un fatto sul run misurato,
     non un guasto del misuratore. La cattura e' larga di proposito e non e' muta: la CAUSA
     (`ritorno 2` contro il nome dell'eccezione) finisce nel record, ed e' cio' che tiene distinti
     i due guasti nell'apparato di prova.

  5. IL RAW SINTETICO PORTA UN'INTESTAZIONE, e l'intestazione non deve inghiottire il pack.
     `estrai_pack` applica DUE precedenze: cerca prima il marcatore ```json e prende la prima
     graffa che lo segue; solo se il marcatore non c'e' ripiega sulla prima graffa in colonna 0.
     L'intestazione rispetta entrambe: nessuna riga prima del pack porta il marcatore, e nessuna
     inizia con una graffa. Sbagliare qui fallisce in SILENZIO — `estrai_pack` non alza nulla,
     restituisce l'oggetto sbagliato, e un canale riparato con successo tornerebbe NON VALIDO
     senza che nulla dica perche'.

  6. SI RIGENERA INVOCANDO IL PROPRIETARIO, mai riscrivendo a mano. I due rapporti di validazione
     e i fascicoli restano file con la marca di CHI LI HA SCRITTI — `replay_report`, `pack_repair`
     —; il driver ne governa il PERCORSO, non il contenuto. Marcarli `replay_offline` renderebbe
     la riesecuzione impossibile: `replay_report.controlla_uscita` rifiuta un'uscita che non porti
     la propria marca.

  uso:  python tools/replay_offline.py --run <dir> --contratto <delivery> [--schemi <dir>]
        exit 0 = tutti i canali validi, originali o riparati
             1 = misura: almeno un canale invalido, non valutabile, o con guasto di canale
             2 = guasto: il driver non ha potuto leggere, comporre o scrivere
"""
import argparse, contextlib, hashlib, io, json, os, sys
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import DI MODULO e chiamata PER ATTRIBUTO, imposti dal mandato e controllati per AST dal
# selftest: `from pack_repair import main` o un binding locale catturerebbero il riferimento
# prima della sostituzione, rendendo inefficace lo spy che osserva le invocazioni — e mute le
# prove che contano le chiamate.
import pack_repair                                                        # noqa: E402
import replay_report                                                      # noqa: E402

VERSIONE = "v1.0.0"
STRUMENTO = "replay_offline"
RADICE = "_offline-repair"
MANIFESTO_DRIVER = "00-driver-manifest.json"
VALIDAZIONE_ORIGINALE = "01-validazione-originale.json"
RIPARAZIONI = "02-riparazioni"
DERIVATO = "03-replay-derivato"
ESITO = "06-esito-offline.json"
MANIFESTO_PADRE = "00-replay-manifest.json"
OUTPUT = "02-output"
USCITA_DERIVATA = "05-validazione-rieseguibile.json"
EVIDENZA = "03-verdetti.json"
PACK_RIPARATO = "02-pack-riparato.json"
NOMI_FASCICOLO = ("00-raw-iniziale.txt", "01-richiesta-riparazione.json",
                  PACK_RIPARATO, EVIDENZA)
CAMPI_IDENTITA = ("run_id_padre", "sha256_manifesto_padre", "commit_di_provenienza",
                  "digest_raw", "sha256_contratto", "sha256_schemi")
SUFFISSO = ".parziale"
INTESTAZIONE = "DERIVED_REPAIR"


class Guasto(Exception):
    """Il driver non ha potuto operare. Exit 2. Distinto da «il run e' messo male», che e' 1."""


# ---- misure -------------------------------------------------------------------------------

def sha256_file(percorso):
    with open(percorso, "rb") as f:
        b = f.read()
    return hashlib.sha256(b).hexdigest(), len(b)


def schemi_di_primo_livello(dir_schemi):
    """I soli `*.json` di PRIMO LIVELLO, ordinati per nome: cio' che `replay_validate` legge."""
    voci = []
    for nome in sorted(os.listdir(dir_schemi)):
        if not nome.endswith(".json"):
            continue
        p = os.path.join(dir_schemi, nome)
        if not os.path.isfile(p):
            continue
        sha, _ = sha256_file(p)
        voci.append({"nome": nome, "sha256": sha})
    return voci


def impronta_schemi(voci):
    """`sha256` della sequenza `<nome> NUL <sha256> LF`. Sensibile ai BYTE, di proposito."""
    seq = b"".join(v["nome"].encode("utf-8") + b"\x00" + v["sha256"].encode("ascii") + b"\n"
                   for v in voci)
    return hashlib.sha256(seq).hexdigest()


def impronta_raw(run, canali):
    """`sha256` di `<canale> NUL <byte> NUL <sha256> LF`, NELL'ORDINE DEL MANIFESTO.

    Un canale senza raw non si salta: vale `<canale> NUL - NUL - LF`. Saltarlo renderebbe due
    corpora diversi indistinguibili quando la differenza e' proprio un raw che manca.
    """
    seq = b""
    for nome in canali:
        p = os.path.join(run, OUTPUT, nome + ".raw.txt")
        if os.path.isfile(p):
            sha, byte = sha256_file(p)
            seq += f"{nome}\x00{byte}\x00{sha}\n".encode("utf-8")
        else:
            seq += f"{nome}\x00-\x00-\n".encode("utf-8")
    return hashlib.sha256(seq).hexdigest()


# ---- scrittura deterministica --------------------------------------------------------------

def serializza(oggetto):
    """UTF-8, LF, nessun BOM, newline finale, indentazione e ordine di chiavi fissi."""
    return json.dumps(oggetto, ensure_ascii=False, indent=2) + "\n"


def sostituisci(parziale, percorso):
    """L'unica sostituzione. E' una funzione perche' e' il punto che si interrompe."""
    os.replace(parziale, percorso)


def scrivi_atomico_byte(percorso, dati):
    """Prima interamente accanto, poi un solo `os.replace`. Mai un mezzo file al posto giusto."""
    parziale = percorso + SUFFISSO
    cartella = os.path.dirname(percorso)
    if cartella:
        os.makedirs(cartella, exist_ok=True)
    with open(parziale, "wb") as f:
        f.write(dati)
    sostituisci(parziale, percorso)


def scrivi_atomico(percorso, testo):
    scrivi_atomico_byte(percorso, testo.encode("utf-8"))


# ---- la matrice dei percorsi ---------------------------------------------------------------

def percorsi_gestiti(canali):
    """La MATRICE: non i file che esistono, ma quelli che possono esistere.

    Quali fascicoli esisteranno dipende da quali canali risultino non validi, noto solo dopo la
    prima validazione — ma l'insieme dei percorsi possibili e' finito e derivabile al passo 0,
    perche' i canali vengono dal manifesto padre e i nomi del fascicolo sono i quattro che
    `pack_repair` scrive davvero. Non ce ne sono altri: anche l'evidenza del raw sintetico e' uno
    di quei quattro.
    """
    return {
        "driver_owned_statici": [MANIFESTO_DRIVER, f"{DERIVATO}/{MANIFESTO_PADRE}", ESITO],
        "canali_autorizzati": list(canali),
        "raw_derivati_attesi": [f"{DERIVATO}/{OUTPUT}/{c}.raw.txt" for c in canali],
        "delegated_outputs": [VALIDAZIONE_ORIGINALE, f"{DERIVATO}/{USCITA_DERIVATA}"],
        "matrice_fascicoli": [f"{RIPARAZIONI}/{c}/{n}" for c in canali for n in NOMI_FASCICOLO],
    }


def destinazioni(gestiti):
    """Ogni destinazione gestita, nell'ordine in cui il manifesto la dichiara."""
    return (list(gestiti["driver_owned_statici"]) + list(gestiti["delegated_outputs"])
            + list(gestiti["raw_derivati_attesi"]) + list(gestiti["matrice_fascicoli"]))


def cartelle_ammesse(dest):
    amm = set()
    for p in dest:
        pezzi = p.split("/")[:-1]
        for i in range(1, len(pezzi) + 1):
            amm.add("/".join(pezzi[:i]))
    return amm


def scandisci(radice):
    """Ogni percorso relativo dentro la radice, file e cartelle, con barra."""
    file, cartelle = [], []
    for base, _sotto, nomi in os.walk(radice):
        rel = os.path.relpath(base, radice).replace("\\", "/")
        if rel != ".":
            cartelle.append(rel)
        for n in nomi:
            file.append(n if rel == "." else f"{rel}/{n}")
    return sorted(file), sorted(cartelle)


def residuo_posseduto(percorso, dest):
    """E' residuo POSSEDUTO ogni percorso esattamente della forma `<destinazione gestita>.parziale`.

    Una regola sola, senza eccezioni: vale per il manifesto del driver, per gli output statici,
    per i raw derivati, per gli artefatti dei fascicoli e per gli output delegati. Le tre regole
    incompatibili di una redazione precedente facevano nascere uno stato inuscibile — un
    `00-driver-manifest.json.parziale` accanto a un manifesto valido non era «da solo», non era un
    delegated output, e cadeva in exit 2 definitivo.
    """
    return percorso.endswith(SUFFISSO) and percorso[:-len(SUFFISSO)] in dest


# ---- passo 0: SOLA LETTURA ------------------------------------------------------------------

def passo_zero(run, contratto, schemi):
    """Legge e misura. Non scrive. Un guasto qui vale 2 e non lascia traccia."""
    if not os.path.isdir(run):
        raise Guasto(f"la directory del replay non esiste: {run}")
    if not os.path.isfile(contratto):
        raise Guasto(f"contratto non trovato: {contratto}")
    if not os.path.isdir(schemi):
        raise Guasto(f"directory degli schemi non trovata: {schemi}")
    percorso_manifesto = os.path.join(run, MANIFESTO_PADRE)
    if not os.path.isfile(percorso_manifesto):
        raise Guasto(f"manifesto del replay padre assente: {MANIFESTO_PADRE}")
    try:
        # la lettura del manifesto e' quella di `replay_report`: il predicato che rifiuta i nomi
        # di canale che escono dalla directory si eredita invece di riscriverlo qui
        run_id, commit, canali = replay_report.leggi_manifesto(percorso_manifesto)
    except replay_report.Guasto as e:
        raise Guasto(str(e))
    sha_man, _ = sha256_file(percorso_manifesto)
    sha_con, _ = sha256_file(contratto)
    voci = schemi_di_primo_livello(schemi)
    identita = {
        "run_id_padre": run_id,
        "sha256_manifesto_padre": sha_man,
        "commit_di_provenienza": commit,
        "digest_raw": impronta_raw(run, canali),
        "sha256_contratto": sha_con,
        "sha256_schemi": impronta_schemi(voci),
    }
    return run_id, commit, canali, identita, voci


# ---- l'ordine decisionale della riesecuzione (§4.6) -----------------------------------------

def identita_diversa(attesa, trovata):
    """Il primo campo che differisce, o `None`. Campo per campo: e' cio' che si falsifica."""
    if not isinstance(trovata, dict):
        return CAMPI_IDENTITA[0]
    for campo in CAMPI_IDENTITA:
        if trovata.get(campo) != attesa.get(campo):
            return campo
    return None


def ricognizione(radice, identita, dest):
    """I punti 1-4 del §4.6, in quest'ordine e senza sovrapposizioni.

    Restituisce i soli residui RICONOSCIUTI da rimuovere: e' l'unica cancellazione autorizzata
    dell'intero design, e si raggiunge solo dopo che 1-4 hanno lasciato passare.
    """
    if not os.path.isdir(radice):
        return []
    file, cartelle = scandisci(radice)
    if not file and not cartelle:
        return []

    # 1. il manifesto, se esiste. Senza manifesto passa il SOLO residuo di bootstrap.
    if MANIFESTO_DRIVER not in file:
        if file == [MANIFESTO_DRIVER + SUFFISSO] and not cartelle:
            return [MANIFESTO_DRIVER + SUFFISSO]
        raise Guasto(f"{RADICE}/ contiene qualcosa e non ha un manifesto di questo strumento: "
                     f"non tocco nulla")

    # 2. illeggibile, malformato, o con stato ignoto: le tre condizioni stanno insieme perche'
    #    hanno la stessa risposta — il driver non sa che cosa ha davanti e non tocca nulla.
    try:
        with open(os.path.join(radice, MANIFESTO_DRIVER), "rb") as f:
            m = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Guasto(f"manifesto del driver non leggibile: {e}")
    if not isinstance(m, dict) or m.get("strumento") != STRUMENTO:
        raise Guasto("il manifesto non dichiara questo strumento: non tocco nulla")
    if m.get("stato") not in ("IN_CORSO", "COMPLETO"):
        raise Guasto(f"stato del manifesto ignoto: {m.get('stato')!r}")

    # 3. identita' di esecuzione
    campo = identita_diversa(identita, m.get("identita"))
    if campo is not None:
        raise Guasto(f"identita' di esecuzione diversa da quella dichiarata: {campo}")

    # 4. percorso sconosciuto
    ammesse = cartelle_ammesse(dest)
    for c in cartelle:
        if c not in ammesse:
            raise Guasto(f"percorso sconosciuto in {RADICE}/: {c}")
    residui = []
    for p in file:
        if p in dest:
            continue
        if residuo_posseduto(p, dest):
            residui.append(p)
            continue
        raise Guasto(f"percorso sconosciuto in {RADICE}/: {p}")
    return residui


# ---- gli strumenti delegati ------------------------------------------------------------------

def invoca_report(run, contratto, schemi, uscita):
    """`replay_report` con `--out`: il replay che misura non si scrive addosso."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        codice = replay_report.main(["--run", run, "--contratto", contratto,
                                     "--schemi", schemi, "--out", uscita])
    return codice, err.getvalue().strip()


def ripara_canale(raw, contratto, schemi, fascicolo):
    """UNA invocazione, senza retry, dentro la cattura larga del §4.8.

    Restituisce `(codice, guasto)`. Il guasto porta la CAUSA: `ritorno 2` quando lo strumento
    dichiara il proprio guasto, il NOME dell'eccezione quando l'import non lo degrada a codice
    d'uscita. Senza quella distinzione i due guasti darebbero lo stesso verdetto pubblico e una
    prova potrebbe restare verde per la ragione dell'altra.
    """
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            codice = pack_repair.main(["--raw", raw, "--contratto", contratto,
                                       "--schemi", schemi, "--fascicolo", fascicolo])
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:                                            # noqa: BLE001
        return None, {"causa": type(e).__name__, "dettaglio": f"{e}"}
    if codice == 2:
        motivo = err.getvalue().strip()
        if motivo.startswith("guasto: "):
            motivo = motivo[len("guasto: "):]
        return None, {"causa": "ritorno 2", "dettaglio": motivo}
    return codice, None


# ---- il raw sintetico ------------------------------------------------------------------------

def raw_sintetico(canale, sha_origine, sha_evidenza, pack):
    """Quattro righe testuali esatte, poi il pack con la prima graffa in colonna 0.

    Nessuna riga precedente al pack inizia con `{`, e nessuna contiene il marcatore ```json: le
    due condizioni non sono ridondanti, perche' `estrai_pack` ha due precedenze e il marcatore
    vince sulla colonna 0.
    """
    return (f"{INTESTAZIONE}\n"
            f"source_raw_sha256={sha_origine}\n"
            f"repair_evidence_sha256={sha_evidenza}\n"
            f"repair_evidence_path={RIPARAZIONI}/{canale}/{EVIDENZA}\n"
            + pack)


def componi_derivato(run, radice, canali, esiti, run_id, commit):
    """Il passo 4: TUTTI i canali, ciascuno col raw che il §4.3 gli assegna.

    Un invalido non riparabile e un guasto di riparazione NON diventano mai `NON_VALUTABILE`:
    togliere il raw a un canale `NON VALIDO` lo farebbe, e sarebbe un verdetto cambiato dal
    misuratore.
    """
    provenienze = {}
    for nome in canali:
        origine = os.path.join(run, OUTPUT, nome + ".raw.txt")
        destinazione = os.path.join(radice, DERIVATO, OUTPUT, nome + ".raw.txt")
        fascicolo = os.path.join(radice, RIPARAZIONI, nome)
        evidenza = os.path.join(fascicolo, EVIDENZA)
        riparato = os.path.join(fascicolo, PACK_RIPARATO)
        if esiti.get(nome) == 0 and os.path.isfile(evidenza) and os.path.isfile(riparato):
            sha_origine, _ = sha256_file(origine)
            sha_evidenza, _ = sha256_file(evidenza)
            with open(riparato, "rb") as f:
                pack = f.read().decode("utf-8")
            scrivi_atomico(destinazione, raw_sintetico(nome, sha_origine, sha_evidenza, pack))
            provenienze[nome] = "sintetico"
            continue
        if os.path.isfile(origine):
            with open(origine, "rb") as f:
                scrivi_atomico_byte(destinazione, f.read())
        provenienze[nome] = "originale"
    manifesto = {"strumento": STRUMENTO, "run_id": run_id,
                 "commit_di_provenienza": commit,
                 "canali": [{"channel": c} for c in canali]}
    scrivi_atomico(os.path.join(radice, DERIVATO, MANIFESTO_PADRE), serializza(manifesto))
    return provenienze


# ---- l'esito -----------------------------------------------------------------------------------

def inventario(radice, dest):
    """L'elenco EFFETTIVO dei file prodotti, con dimensione e `sha256`.

    `00-driver-manifest.json` compare SENZA dimensione ne' `sha256`: un file non puo' contenere il
    proprio hash. E' una necessita' aritmetica, non una scelta.
    """
    voci = []
    for p in dest:
        assoluto = os.path.join(radice, p.replace("/", os.sep))
        if p == MANIFESTO_DRIVER:
            voci.append({"percorso": p})
            continue
        if not os.path.isfile(assoluto):
            continue
        sha, byte = sha256_file(assoluto)
        voci.append({"percorso": p, "byte": byte, "sha256": sha})
    return voci


def componi_esito(run, radice, canali, ident, contratto_nome, originale, derivato,
                  esiti, guasti, provenienze):
    """Il confronto e il lineage. Nessun percorso: ogni provenienza e' espressa per contenuto."""
    stati_o = {r["canale"]: r["esito"] for r in originale["canali"]}
    stati_d = {r["canale"]: r["esito"] for r in derivato["canali"]}
    righe = []
    for nome in canali:
        fascicolo = os.path.join(radice, RIPARAZIONI, nome)
        evidenza = os.path.join(fascicolo, EVIDENZA)
        if nome in guasti:
            azione = "GUASTO_DI_RIPARAZIONE"
        elif nome not in esiti:
            azione = "NON_INVOCATO"
        elif esiti[nome] == 0:
            azione = "RIPARATO"
        else:
            azione = "NON_RIPARATO"
        origine = os.path.join(run, OUTPUT, nome + ".raw.txt")
        derivata = os.path.join(radice, DERIVATO, OUTPUT, nome + ".raw.txt")
        righe.append({
            "canale": nome,
            "stato_originale": stati_o.get(nome),
            "stato_derivato": stati_d.get(nome),
            "azione": azione,
            "provenienza_raw": provenienze.get(nome),
            "sha256_raw_originale": sha256_file(origine)[0] if os.path.isfile(origine) else None,
            "sha256_raw_derivato": sha256_file(derivata)[0] if os.path.isfile(derivata) else None,
            "sha256_evidenza_riparazione":
                sha256_file(evidenza)[0] if os.path.isfile(evidenza) else None,
            "guasto_di_riparazione": guasti.get(nome),
        })
    return {
        "strumento": f"replay_offline.py {VERSIONE}",
        "identita": ident,
        "contratto_nome": contratto_nome,
        "esito_run_originale": originale["esito_run"],
        "esito_run_derivato": derivato["esito_run"],
        "canali": righe,
        "confronto": {
            "canali_dichiarati": len(canali),
            "riparati": sum(1 for r in righe if r["azione"] == "RIPARATO"),
            "non_riparati": sum(1 for r in righe if r["azione"] == "NON_RIPARATO"),
            "non_invocati": sum(1 for r in righe if r["azione"] == "NON_INVOCATO"),
            "guasti_di_riparazione": len(guasti),
            "non_validi_prima": sum(1 for r in righe if r["stato_originale"] == "NON VALIDO"),
            "non_validi_dopo": sum(1 for r in righe if r["stato_derivato"] == "NON VALIDO"),
            "non_valutabili_prima":
                sum(1 for r in righe if r["stato_originale"] == "NON_VALUTABILE"),
            "non_valutabili_dopo":
                sum(1 for r in righe if r["stato_derivato"] == "NON_VALUTABILE"),
        },
        "lineage": {c: ident[c] for c in CAMPI_IDENTITA},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="compone offline la meta' B del replay: valida, ripara, rivalida")
    ap.add_argument("--run", required=True, metavar="DIR", help="la directory del replay padre")
    ap.add_argument("--contratto", required=True, metavar="DELIVERY",
                    help="la vista delivery gia' generata da contract_compile.py")
    ap.add_argument("--schemi", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas"))
    a = ap.parse_args(argv)
    radice = os.path.join(a.run, RADICE)
    try:
        # 0. SOLA LETTURA
        run_id, commit, canali, ident, voci = passo_zero(a.run, a.contratto, a.schemi)
        gestiti = percorsi_gestiti(canali)
        dest = destinazioni(gestiti)
        residui = ricognizione(radice, ident, dest)

        # 1. RIVENDICA, con i soli dati del passo 0
        os.makedirs(radice, exist_ok=True)
        for r in residui:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(radice, r.replace("/", os.sep)))
        manifesto = {"strumento": STRUMENTO, "versione": VERSIONE, "stato": "IN_CORSO",
                     "identita": ident, "schemi_considerati": voci,
                     "contratto_nome": os.path.basename(a.contratto),
                     "percorsi_gestiti": gestiti}
        scrivi_atomico(os.path.join(radice, MANIFESTO_DRIVER), serializza(manifesto))

        # 2. la validazione del replay PADRE, scritta dentro `_offline-repair/`
        uscita = os.path.join(radice, VALIDAZIONE_ORIGINALE)
        codice, motivo = invoca_report(a.run, a.contratto, a.schemi, uscita)
        if codice == 2:
            raise Guasto(f"la validazione del replay padre non ha potuto girare: {motivo}")
        with open(uscita, "rb") as f:
            originale = json.loads(f.read().decode("utf-8"))

        # 3. riparazione, UNA VOLTA e SOLTANTO sui canali con pack presente e non valido
        stati = {r["canale"]: r["esito"] for r in originale["canali"]}
        esiti, guasti = {}, {}
        for nome in canali:
            if stati.get(nome) != "NON VALIDO":
                continue
            esito, guasto = ripara_canale(
                os.path.join(a.run, OUTPUT, nome + ".raw.txt"), a.contratto, a.schemi,
                os.path.join(radice, RIPARAZIONI, nome))
            esiti[nome] = esito
            if guasto is not None:
                guasti[nome] = guasto

        # 4. il replay derivato, con TUTTI i canali
        provenienze = componi_derivato(a.run, radice, canali, esiti, run_id, commit)

        # 5. la seconda validazione, sul derivato
        uscita2 = os.path.join(radice, DERIVATO, USCITA_DERIVATA)
        codice2, motivo2 = invoca_report(os.path.join(radice, DERIVATO), a.contratto,
                                         a.schemi, uscita2)
        if codice2 == 2:
            raise Guasto(f"la validazione del replay derivato non ha potuto girare: {motivo2}")
        with open(uscita2, "rb") as f:
            derivato = json.loads(f.read().decode("utf-8"))

        # 6. l'esito, col confronto e il lineage
        esito = componi_esito(a.run, radice, canali, ident, manifesto["contratto_nome"],
                              originale, derivato, esiti, guasti, provenienze)
        scrivi_atomico(os.path.join(radice, ESITO), serializza(esito))

        # 7. il manifesto COMPLETO sostituisce l'`IN_CORSO`, con l'inventario effettivo
        chiuso = dict(manifesto)
        chiuso["stato"] = "COMPLETO"
        chiuso["inventario"] = inventario(radice, dest)
        scrivi_atomico(os.path.join(radice, MANIFESTO_DRIVER), serializza(chiuso))
    except Guasto as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"guasto: {e}", file=sys.stderr)
        return 2
    except Exception as e:                                                # noqa: BLE001
        # Il contenimento del §4.8 vale per il singolo canale e NON si estende ai passi 0, 1, 2,
        # 4, 5, 6 e 7. Ma un'eccezione che arriva fin qui non deve uscire come traceback: `2`
        # significa «il driver non ha potuto operare», ed e' esattamente quello che e' successo.
        # E' anche cio' che rende osservabile la rimozione della cattura di canale: il guasto di
        # `pack_repair` smette di restare confinato e SALE, emergendo come 2 invece che come 1.
        print(f"guasto: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    c = esito["confronto"]
    print(f"{derivato['esito_run']} — {c['riparati']} riparati, {c['non_riparati']} non "
          f"riparabili, {c['guasti_di_riparazione']} guasti di riparazione, "
          f"{c['non_validi_dopo']} non validi, {c['non_valutabili_dopo']} non valutabili "
          f"→ {RADICE}/{ESITO}", file=sys.stderr)
    return 0 if derivato["esito_run"] == "COMPLETO" and not guasti else 1


if __name__ == "__main__":
    sys.exit(main())
