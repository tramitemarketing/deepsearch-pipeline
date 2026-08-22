#!/usr/bin/env python3
"""
provenance_check.py — il preflight di provenienza: verifica che ogni input di un run venga
dall'unica radice canonica, al commit dichiarato, e registra tutto nel manifesto.

PERCHE' ESISTE (leggere prima di modificarlo).

Un run reale ha gia' composto lo schema di una radice col prompt di un'altra: due alberi con gli
stessi nomi di file e contenuti diversi. Nessun artefatto registrava da dove venisse che cosa.
Questo preflight rende quella composizione IMPOSSIBILE: ogni componente deve essere (a) sotto la
radice canonica — verificato con os.path.commonpath, mai con un prefisso di stringa — (b) in un
repository git la cui HEAD e' il commit dichiarato, (c) byte-identico al blob di quel commit.
Tre condizioni insieme, perche' nessuna delle tre basta (design §9.2): la prima non distingue
una copia identica fuori radice, la seconda non distingue un file modificato, la terza non
distingue un'altra radice con lo stesso commit.

DUE DIGEST, MAI CONFUSI (decisione R2 del piano di M1, vincolante anche qui). `sha256_grezzo`
e' il digest dei byte grezzi: l'unico che prova `identico_al_blob`. `sha256_normalizzato` e' il
digest del contenuto CRLF→LF: la parita' Windows/POSIX, la stessa regola con cui il compilatore
rende il prodotto invariante fra piattaforme. Scambiarli fa arrestare ogni run su un checkout
CRLF, o fa passare un file manomesso su un checkout LF.

I CONTRATTI SONO UNA COLLEZIONE (vincolo M1.1): il manifesto registra una voce per vista —
`audit` e `delivery`, ciascuna con percorso, sha256 e dimensione — in una LISTA. Una terza vista
futura e' un'appendice, non una migrazione. Le due viste fornite sono confrontate byte per byte
con la ricompilazione dagli schemi VERIFICATI: la manomissione del corpo di una vista — anche a
riga dell'hash intatta — e' intercettata. La delivery deve portare esattamente una riga
`audit sha256 <64hex>` come prima riga dati, e l'hash deve coincidere con quello del file audit.

IL PROMPT CONSEGNATO (quando fornito) deve essere esattamente `norm(base) + "\\n\\n---\\n\\n" +
delivery`, con `norm(base)` = CRLF→LF e una sola newline finale. Ordine o giunzione diversi sono
rifiutati con errori nominati e distinti (design §8.5 punto 8).

NESSUNA SCRITTURA PRIMA DELLA FINE. Tutte le verifiche si completano in memoria; il manifesto —
se richiesto — si scrive in un file temporaneo nella stessa directory e si sostituisce con
os.replace: atomico, UTF-8, LF, senza BOM, mai un manifesto parziale su disco.

  uso:  python tools/provenance_check.py --radice-da <percorso> --commit <digest> [opzioni]
        --radice-da         il prompt/componente dichiarato da cui si deriva la radice canonica
        --commit            il commit dichiarato, DIGEST INTERO: 40 o 64 caratteri esadecimali,
                            mai un prefisso abbreviato
        --agente NOME       registra: .claude/agents/NOME.md, lo schema da mapping, la chiusura
                            dei $ref esterni, tools/contract_compile.py,
                            config/deepsearch.config.json
        --componente REL    componente aggiuntivo (verificato contro il blob e registrato)
        --audit PERCORSO    vista audit del contratto (registrata; ricompilata e confrontata)
        --delivery PERCORSO vista delivery (registrata; ricompilata; hash incollato confrontato)
        --prompt-consegnato PERCORSO   verifica base + giunzione + delivery
        --manifest PERCORSO scrittura atomica del manifesto (nessun file parziale)
        --config PERCORSO   default: config/deepsearch.config.json sotto la radice

  exit code:
        0 = verifiche superate (manifesto scritto se richiesto)
        1 = violazione di provenienza: fuori radice · radice vietata · fuori repository ·
            HEAD diverso · commit dichiarato non integro · assente dal commit · blob diverso ·
            mapping agente→schema mancante/differente · viste incomplete ·
            vista non riprodotta · hash incollato assente/duplicato/fuori posizione/
            non coincidente · ordine del prompt consegnato · giunzione non canonica ·
            percorso non canonico
        2 = uso errato (argomenti mancanti o malformati)
        3 = ambiente rotto: componente inesistente · git non disponibile · config illeggibile
            o senza radici vietate · compilatore non importabile

Nessuna rete, nessun jsonschema, nessun referencing: solo libreria standard e git locale.
"""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '·' e '«' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

VERSIONE = "v1.0.0"
GIUNZIONE = "\n\n---\n\n"
# Il commit dichiarato dev'essere il DIGEST INTERO, mai un prefisso: il manifesto di un replay
# ha gia' registrato una radice vietata come `fd93cb7f` — otto caratteri, non una prova
# (design §9.2). Il repository reale produce digest SHA-1 da 40 caratteri esadecimali; un clone
# o un futuro cambio di formato ne produce 64. Entrambi sono digest interi: solo quelli passano.
REGEX_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")


class ViolazioneProvenienza(Exception):
    """Una delle condizioni di provenienza non regge. Exit 1, errore nominato nel messaggio."""


class AmbienteRotto(Exception):
    """Il controllo non puo' partire: input assenti o strumenti rotti. Exit 3."""


def git(dir_repo, *args):
    """git in sola lettura, locale. stdout e' BYTES: il blob del commit va confrontato sui
    byte grezzi, e una decodifica di mezzo lo corromperebbe su contenuti non ASCII."""
    try:
        r = subprocess.run(["git", "-C", dir_repo] + list(args), capture_output=True, timeout=120)
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        raise AmbienteRotto(f"git non disponibile: {e}")
    return r


def norm(b):
    """CRLF→LF ovunque, poi esattamente UNA newline finale (aggiunta se assente, collassata se
    doppia). E' la normalizzazione del prompt base prima della giunzione (§8.5 punto 8)."""
    b = b.replace(b"\r\n", b"\n")
    while b.endswith(b"\n\n"):
        b = b[:-1]
    if not b.endswith(b"\n"):
        b += b"\n"
    return b


def sha256_di(b):
    return hashlib.sha256(b).hexdigest()


def identico_al_blob(grezzi, normalizzati, blob):
    """L'identita' e' dei BYTE GREZZI: e' il punto che inchioda la disciplina dei due digest.
    Confrontare il digest normalizzato col blob lascerebbe passare un checkout CRLF manomesso —
    la falsificazione P10 inchioda esattamente questa riga."""
    return grezzi == blob


def refs_esterni(doc, gia_visti=None):
    """I file di schema referenziati da $ref come «nome.schema.json#/...», chiusura ricorsiva."""
    gia_visti = gia_visti if gia_visti is not None else set()
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == "$ref" and isinstance(v, str) and "#" in v:
                f = v.partition("#")[0]
                if f and f not in gia_visti:
                    gia_visti.add(f)
            else:
                refs_esterni(v, gia_visti)
    elif isinstance(doc, list):
        for x in doc:
            refs_esterni(x, gia_visti)
    return gia_visti


def leggi_manifesto(percorso):
    b = open(percorso, "rb").read()
    return b.decode("utf-8"), b


def main(argv=None):
    ap = argparse.ArgumentParser(description="preflight di provenienza del contratto schema-driven")
    ap.add_argument("--radice-da", required=True,
                    help="il prompt/componente dichiarato da cui si deriva la radice canonica")
    ap.add_argument("--commit", required=True,
                    help="il commit dichiarato, digest intero: 40 o 64 caratteri esadecimali")
    ap.add_argument("--agente", default=None)
    ap.add_argument("--componente", nargs="*", default=[], metavar="REL")
    ap.add_argument("--audit", default=None)
    ap.add_argument("--delivery", default=None)
    ap.add_argument("--prompt-consegnato", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)

    a.commit = a.commit.lower()
    if not REGEX_COMMIT.match(a.commit):
        raise ViolazioneProvenienza(
            f"commit dichiarato non integro: {len(a.commit)} caratteri, atteso il digest "
            "intero (40 o 64 esadecimali)")
    if (a.audit is None) != (a.delivery is None):
        raise ViolazioneProvenienza("viste incomplete: audit e delivery vanno fornite insieme")
    if (a.audit is not None or a.delivery is not None) and not a.agente:
        ap.error("--agente e' richiesto quando si verificano le viste")
    if a.prompt_consegnato is not None and (not a.agente or a.delivery is None):
        ap.error("--prompt-consegnato richiede --agente e --delivery")

    # ------------------------------------------------------- radice canonica, dalla posizione
    radice_da = os.path.abspath(a.radice_da)
    if not os.path.exists(radice_da):
        raise AmbienteRotto(f"componente inesistente: {a.radice_da}")
    r = git(os.path.dirname(radice_da), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise ViolazioneProvenienza(
            f"fuori repository: {a.radice_da} non sta in un repository git")
    radice = os.path.realpath(r.stdout.decode("utf-8", "replace").strip())

    # ------------------------------------------------------- config, e la lista delle vietate
    percorso_config = a.config if a.config else os.path.join(radice, "config",
                                                            "deepsearch.config.json")
    try:
        with open(percorso_config, "rb") as f:
            cfg = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise AmbienteRotto(f"config illeggibile: {percorso_config} — {e}")
    vs = cfg.get("verifica_specifica") or {}
    vietate = vs.get("radici_vietate")
    if not isinstance(vietate, list):
        raise AmbienteRotto(
            "radici vietate non dichiarate in config.verifica_specifica.radici_vietate")
    if os.path.basename(radice) in vietate:
        raise ViolazioneProvenienza(f"radice vietata: {os.path.basename(radice)}")

    # ------------------------------------------------------- HEAD contro il commit dichiarato
    r = git(radice, "rev-parse", "HEAD")
    if r.returncode != 0:
        raise AmbienteRotto(f"git rev-parse fallito su {radice}: {r.stderr.decode('utf-8', 'replace')}")
    head = r.stdout.decode("utf-8", "replace").strip().lower()
    if head != a.commit:
        raise ViolazioneProvenienza(f"HEAD diverso: dichiarato {a.commit}, reale {head}")

    # ------------------------------------------------------- mapping agente→schema
    mapping = {}
    for voce in vs.get("schema_per_agente") or []:
        mapping[voce.get("agente")] = voce.get("schema")

    # ------------------------------------------------------- il compilatore (importato, mai riscritto)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import contract_compile as cc
    except Exception as e:
        raise AmbienteRotto(f"compilatore non importabile: {e}")

    # ------------------------------------------------------- la lista dei componenti
    componenti = list(a.componente)
    componenti.append(a.config if a.config else "config/deepsearch.config.json")
    if a.agente:
        if a.agente not in mapping:
            raise ViolazioneProvenienza(f"mapping agente→schema mancante: {a.agente}")
        schema_agente = mapping[a.agente]
        if a.agente in cc.SCHEMA_PER_AGENTE and cc.SCHEMA_PER_AGENTE[a.agente] != schema_agente:
            raise ViolazioneProvenienza(
                f"mapping agente→schema differente: il config dichiara {schema_agente}, "
                f"il compilatore {cc.SCHEMA_PER_AGENTE[a.agente]}")
        componenti.append(f".claude/agents/{a.agente}.md")
        componenti.append(f"schemas/{schema_agente}")
        percorso_schema = os.path.join(radice, "schemas", schema_agente)
        try:
            with open(percorso_schema, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            raise AmbienteRotto(f"schema dell'agente illeggibile: {percorso_schema} — {e}")
        for f in sorted(refs_esterni(doc)):
            if not re.match(r"^[\w.-]+$", f):
                raise AmbienteRotto(f"chiusura $ref non risolvibile: {f}")
            componenti.append(f"schemas/{f}")
        componenti.append("tools/contract_compile.py")
    visti = set()
    registro = []
    for rel in componenti:
        if rel in visti:
            continue
        visti.add(rel)
        segmenti = rel.replace(os.sep, "/").split("/")
        if ".." in segmenti:
            raise ViolazioneProvenienza(f"percorso non canonico: {rel}")
        p = os.path.realpath(os.path.join(radice, rel.replace("/", os.sep)))
        try:
            dentro = os.path.commonpath([radice, p]) == radice
        except ValueError:                      # unita' diverse su Windows: C: contro D:
            dentro = False
        if not dentro:
            raise ViolazioneProvenienza(f"fuori radice: {rel}")
        if not os.path.exists(p):
            raise AmbienteRotto(f"componente inesistente: {rel}")
        grezzi = open(p, "rb").read()
        blob_rel = os.path.relpath(p, radice).replace(os.sep, "/")
        r = git(radice, "cat-file", "blob", f"{a.commit}:{blob_rel}")
        if r.returncode != 0:
            raise ViolazioneProvenienza(f"assente dal commit: {rel}")
        blob = r.stdout
        normalizzati = grezzi.replace(b"\r\n", b"\n")
        if not identico_al_blob(grezzi, normalizzati, blob):
            raise ViolazioneProvenienza(
                f"blob diverso: {rel} non e' byte-identico al commit dichiarato")
        registro.append({
            "percorso": blob_rel,
            "sha256_grezzo": sha256_di(grezzi),
            "sha256_normalizzato": sha256_di(normalizzati),
            "dimensione": len(grezzi),
            "identico_al_blob": True,
        })

    # ------------------------------------------------------- le viste del contratto
    contratti = []
    if a.audit is not None and a.delivery is not None:
        audit_atteso, delivery_atteso = cc.compila_viste(a.agente, os.path.join(radice, "schemas"),
                                                         a.commit)
        testo_audit, b_audit = leggi_manifesto(a.audit)
        testo_delivery, b_delivery = leggi_manifesto(a.delivery)
        if b_audit != audit_atteso.encode("utf-8"):
            raise ViolazioneProvenienza("vista non riprodotta: audit")
        righe = testo_delivery.split("\n")
        m = re.fullmatch(r"audit sha256 ([0-9a-f]{64})", righe[1]) if len(righe) > 1 else None
        tot = len(re.findall(r"^audit sha256 [0-9a-f]{64}$", testo_delivery, re.M))
        if tot == 0:
            raise ViolazioneProvenienza("hash incollato assente: la delivery non dichiara l'audit")
        if tot != 1:
            raise ViolazioneProvenienza(
                f"hash incollato duplicato: {tot} righe, attesa una")
        if m is None:
            raise ViolazioneProvenienza(
                "hash incollato fuori posizione: la riga dev'essere la prima riga dati")
        sha_audit = sha256_di(b_audit)
        if m.group(1) != sha_audit:
            raise ViolazioneProvenienza(
                f"hash incollato non coincidente: la delivery dichiara {m.group(1)}, "
                f"l'audit e' {sha_audit}")
        if b_delivery != delivery_atteso.encode("utf-8"):
            raise ViolazioneProvenienza("vista non riprodotta: delivery")
        for vista, path in (("audit", a.audit), ("delivery", a.delivery)):
            p = os.path.realpath(os.path.abspath(path))
            b = open(p, "rb").read()
            rel = os.path.relpath(p, radice).replace(os.sep, "/")
            if rel.startswith(".."):
                rel = p
            contratti.append({"vista": vista, "percorso": rel,
                              "sha256": sha256_di(b), "dimensione": len(b)})

        if a.prompt_consegnato is not None:
            base_p = os.path.join(radice, ".claude", "agents", f"{a.agente}.md")
            base = open(base_p, "rb").read()
            consegnato = open(a.prompt_consegnato, "rb").read()
            atteso = norm(base) + GIUNZIONE.encode("utf-8") + b_delivery
            if consegnato != atteso:
                inverso = b_delivery + GIUNZIONE.encode("utf-8") + norm(base)
                if consegnato == inverso:
                    raise ViolazioneProvenienza(
                        "ordine del prompt consegnato: base e delivery invertiti")
                raise ViolazioneProvenienza("giunzione non canonica o contenuto estraneo")

    # ------------------------------------------------------- il manifesto, solo a fine verifiche
    compilatore = registro[[c["percorso"] for c in registro].index("tools/contract_compile.py")] \
        if any(c["percorso"] == "tools/contract_compile.py" for c in registro) else None
    manifesto = {
        "provenance_check_version": VERSIONE,
        "commit": a.commit,
        "radice_canonica": radice,
        "compiler_version": cc.VERSIONE,
        "compiler_sha256": compilatore["sha256_grezzo"] if compilatore else sha256_di(
            open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "contract_compile.py"), "rb").read()),
        "mapping_agente_schema": mapping,
        "componenti": registro,
        "contratti": contratti,
    }
    testo = json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n"
    if a.manifest:
        d = os.path.dirname(os.path.abspath(a.manifest)) or "."
        if not os.path.isdir(d):
            raise AmbienteRotto(f"directory del manifesto inesistente: {d}")
        fd, tmp = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(testo.encode("utf-8"))
            os.replace(tmp, a.manifest)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    sys.stdout.write(testo)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ViolazioneProvenienza as e:
        print(f"violazione di provenienza: {e}", file=sys.stderr)
        sys.exit(1)
    except AmbienteRotto as e:
        print(f"ambiente rotto: {e}", file=sys.stderr)
        sys.exit(3)
