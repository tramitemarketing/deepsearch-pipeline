#!/usr/bin/env python3
"""
spec_check_selftest.py — dimostra che spec_check.py NON e' cieco.

Un verificatore che passa non dice niente. Puo' passare perche' la specifica e' coerente, oppure
perche' non guarda. La differenza non e' ispezionabile leggendo il codice: si dimostra solo
rimettendo i difetti dentro e verificando che vengano ripresi.

Questo file riapplica i DIFETTI REALMENTE ACCADUTI in questo repo — ognuno con la versione in cui
e' comparso e chi l'aveva trovato — su una copia usa-e-getta, e pretende che spec_check li segnali.
Se una mutazione passa, il controllo corrispondente e' decorativo e va riscritto.

  uso:  python3 tools/spec_check_selftest.py
        exit 0 = ogni difetto storico viene ripreso
"""
import json, os, re, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Composta a runtime, MAI scritta per intero in questo file. SC-21 scansiona tutto il repo e non
# ha eccezioni: un letterale qui bloccava il cancello d'ingresso di ogni run (§0 pretende
# spec_check exit 0) e — molto peggio — faceva risultare "ok" la mutazione SC-21 anche senza
# applicarla. Escludere questo file dalla scansione sarebbe stata la correzione sbagliata:
# un'esclusione e' un posto dove una chiave vera puo' restare per sempre.
CHIAVE_FINTA = "sk" + "-" + "a7f3c91e4b8d" + "2056fa1c3e7b90d4f2a8"


def patch_json(percorso_file, dotted, valore):
    def f(base):
        p = os.path.join(base, percorso_file)
        d = json.load(open(p, encoding="utf-8"))
        n = d
        parti = dotted.split(".")
        for k in parti[:-1]:
            n = n[int(k[1:-1])] if k.startswith("[") else n[k]
        n[parti[-1]] = valore
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return f


def patch_testo(percorso_file, prima, dopo):
    def f(base):
        p = os.path.join(base, percorso_file)
        t = open(p, encoding="utf-8").read()
        if prima not in t:
            raise AssertionError(f"testo da mutare non trovato in {percorso_file}: {prima!r}")
        open(p, "w", encoding="utf-8").write(t.replace(prima, dopo, 1))
    return f


def patch_regex(percorso_file, pattern, dopo):
    def f(base):
        p = os.path.join(base, percorso_file)
        t = open(p, encoding="utf-8").read()
        t2, n = re.subn(pattern, dopo, t, count=1)
        if not n:
            raise AssertionError(f"regex non trovata in {percorso_file}: {pattern}")
        open(p, "w", encoding="utf-8").write(t2)
    return f


def patch_ovunque(percorso_file, prima, dopo):
    """Sostituisce TUTTE le occorrenze. patch_regex ne cambia una sola, e per un rinomino di
    criterio questo lascia il vecchio nome dichiarato altrove: la mutazione si annullava da sola."""
    def f(base):
        p = os.path.join(base, percorso_file)
        t = open(p, encoding="utf-8").read()
        if prima not in t:
            raise AssertionError(f"non trovato in {percorso_file}: {prima!r}")
        open(p, "w", encoding="utf-8").write(t.replace(prima, dopo))
    return f


def scambia_passi_2_e_3(base):
    """Scambia il Passo 2 (verifica preliminare) e il Passo 3 (scrittura del report) di §11.
    E' la forma piu' fedele della circolarita' C2: nessuna stringa sparisce, cambia solo
    l'ordine — che e' precisamente cio' che la v2.8 non guardava."""
    p = os.path.join(base, ".claude", "commands", "deepsearch.md")
    t = open(p, encoding="utf-8").read()
    i2 = t.index("### Passo 2 —")
    i3 = t.index("### Passo 3 —")
    i4 = t.index("### Passo 4 —")
    t = t[:i2] + t[i3:i4] + t[i2:i3] + t[i4:]
    open(p, "w", encoding="utf-8").write(t)


# (id atteso, storia del difetto, mutazione)
MUTAZIONI = [
    ("SC-04",
     "N1 / v2.7 — pain_id ammesso come ^P-[0-9]{1,3}$ nell'envelope e ^P-[0-9]{3}$ nel proprio "
     "schema. Stessa classe del difetto su run_id che la v2.7 aveva appena riparato.",
     patch_regex("schemas/core.schema.json", r'\^P-\[0-9\]\{3\}\$', '^P-[0-9]{1,3}$')),

    ("SC-04",
     "C1 / v2.6 — run_id passato a YYYYMMDD-HHMM senza aggiornare il pattern degli ID di fonte: "
     "ogni output sarebbe fallito in validazione e ogni idea finita in PARKED.",
     patch_regex("schemas/core.schema.json",
                 r'\^S-\[0-9\]\{8\}\(-\[0-9\]\{4\}\)\?-\[0-9\]\{4\}\$', '^S-[0-9]{8}-[0-9]{3}$')),

    ("SC-03",
     "Review B / v2.6 — soglia scritta a mano in un prompt e divergente dal config. E' la classe "
     "di difetto piu' frequente nella storia del repo (finestre zero-yield in 4 prompt, soglia su "
     "final_score in 3 file su 5).",
     patch_testo(".claude/agents/competitor-analyst.md",
                 "unclassified_ratio", "unclassified_ratio > 0.75 —")),

    ("SC-07",
     "Separazione dei poteri — un agente di solo giudizio a cui vengono dati strumenti di ricerca. "
     "E' l'invariante piu' importante del sistema e finora era verificabile solo a occhio, su "
     "dieci file.",
     patch_testo(".claude/agents/ranking-agent.md",
                 "tools: Read, Write", "tools: WebSearch, WebFetch, Read, Write")),

    ("SC-08",
     "C2 / v2.6 e v2.7 — run_close eseguito PRIMA di scrivere gli artefatti che verifica. "
     "Conseguenza: ogni run chiudeva NON VALIDO, §11 vietava di scrivere state/, e la memoria fra "
     "run restava spenta senza che nulla lo segnalasse.",
     patch_testo(".claude/commands/deepsearch.md",
                 "python3 tools/run_close.py runs/<run_id> --fase pre",
                 "python3 tools/run_close.py runs/<run_id> --fase finale")),

    ("SC-08",
     "v2.8 — run_close pretende un artefatto che §11 non dice di scrivere. Il run chiude NON "
     "VALIDO per un file che nessuno ha mai chiesto.",
     patch_regex("tools/run_close.py", r'"dossier\.json"\]', '"dossier.json", "00-audit.json"]')),

    ("SC-09",
     "N5 / v2.7 — 'Sette rami' seguito da otto righe numerate. Innocuo da solo, ma e' il sintomo "
     "di una tabella modificata senza rileggere la frase che la introduce.",
     patch_testo(".claude/commands/deepsearch.md", "**GATE 4 — Trigger.** Sei rami",
                 "**GATE 4 — Trigger.** Cinque rami")),

    ("SC-11",
     "Collaudo 20260801 — DEEP dichiarava 80 pain, l'intervallo di ID ne ammetteva 64, il budget "
     "ne consentiva 48. Tre numeri in tre posti e nessuno che li confrontasse.",
     patch_json("config/deepsearch.config.json", "funnel.DEEP.pain_ids_per_shard", 8)),

    ("SC-11",
     "Pesi di scoring che non sommano a 1.0: il punteggio finale cambia scala senza che nessuna "
     "soglia se ne accorga.",
     patch_json("config/deepsearch.config.json", "scoring.dimensioni.[0].peso", 0.5)),

    ("SC-12",
     "Review B — budget assegnato in config a un agente che non esiste su disco.",
     patch_json("config/deepsearch.config.json", "budget.max_per_agent.agente-fantasma",
                {"searches": 5, "fetches": 5})),

    ("SC-06",
     "Un criterio citato da un prompt e mai dichiarato in config: un gate che non si chiudera' mai.",
     patch_testo(".claude/agents/competitor-analyst.md", "`K4` (AI sul core job)",
                 "`K9` (criterio inventato), `K4` (AI sul core job)")),

    ("SC-10",
     "Uno schema che punta a una definizione inesistente del core: l'output non valida piu' e "
     "l'errore compare solo a run avviato.",
     patch_testo("schemas/pain.schema.json", "core.schema.json#/$defs/Envelope",
                 "core.schema.json#/$defs/Inesistente")),

    ("SC-13",
     "N1 a monte / v2.6-v2.7 — DATA-CONTRACT.md non dichiarava di essere derivato e ha documentato "
     "per due versioni un pattern di pain_id diverso da quello degli schemi. Chi lo leggeva lo "
     "prendeva per la fonte di verita'.",
     patch_testo("docs/DATA-CONTRACT.md", "> **DOCUMENTO DERIVATO", "> **Nota")),

    ("SC-01",
     "Un prompt che legge un parametro di config che nessuno ha mai scritto: l'agente applica un "
     "default silenzioso e nessuno se ne accorge.",
     patch_testo(".claude/commands/deepsearch.md", "`config.run.output_max_ideas`",
                 "`config.run.output_max_idee_pubblicabili`")),
]


INDIPENDENTI = [
    ("SC-03",
     "soglia_pubblicazione cambiata SOLO in config: nella prosa normativa il 6.5 resta scritto a "
     "mano in piu' punti. E' testualmente la classe che il docstring dichiara come ragion d'essere.",
     patch_json("config/deepsearch.config.json", "scoring.soglia_pubblicazione", 7.0)),

    ("SC-12",
     "agente nuovo aggiunto su disco e dimenticato in config e nell'orchestratore.",
     lambda base: open(os.path.join(base, ".claude", "agents", "pricing-analyst.md"), "w",
                       encoding="utf-8").write(
         "---\nname: pricing-analyst\ntools: WebSearch, Read, Write\n---\n# PRICING\n")),

    ("SC-06",
     "criterio W5 rinominato W7 nei keep_criteria: il config diventa incoerente con se stesso e "
     "con i prompt che citano W5.",
     patch_ovunque("config/deepsearch.config.json", '"W5"', '"W7"')),

    ("SC-07",
     "frontmatter scritto come lista YAML invece che inline: forma valida, e fino alla v2.8.1 "
     "apriva un buco nell'invariante piu' importante del sistema.",
     patch_testo(".claude/agents/venture-architect.md", "tools: Read, Write",
                 "tools:\n  - Read\n  - Write\n  - WebSearch")),

    ("SC-08",
     "verifica preliminare spostata DOPO la scrittura del report: ricrea C2 in una forma che la "
     "v2.8 non sorvegliava (controllava che la stringa esistesse, non dove).",
     scambia_passi_2_e_3),

    ("SC-14",
     "un esito rinominato nel config e non nello schema che lo rispecchia: e' il difetto "
     "MENZIONE-ONESTA / MENZIONE_ONESTA, vivo nel repo mentre spec_check dichiarava 0 errori.",
     patch_regex("config/deepsearch.config.json", r'"PARKED"', '"PARCHEGGIATA"')),
]


def svuota(chiave):
    """Svuota una lista del contratto di verifica. E' l'attacco che la v2.8.1 non vedeva: tre
    controlli si spegnevano senza un errore, perche' il contratto viveva dentro l'artefatto che
    verificano."""
    def f(base):
        p = os.path.join(base, "config", "deepsearch.config.json")
        d = json.load(open(p, encoding="utf-8"))
        nodo, parti = d["verifica_specifica"], chiave.split("/")
        for k in parti[:-1]:
            nodo = nodo[k]
        nodo[parti[-1]] = [] if isinstance(nodo[parti[-1]], list) else {}
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return f


def insieme(*fn):
    def f(base):
        for g in fn:
            g(base)
    return f


# Le otto mutazioni della terza revisione avversariale, riscritte nel suo formato su sua proposta.
# Sei delle otto passavano pulite sulla v2.8.1, e tre erano lo stesso buco: il contratto svuotabile.
TERZA_REVISIONE = [
    ("SC-00", "M-B — costanti_rispecchiate svuotato e soglia_pubblicazione cambiata solo in config: "
              "il controllo si spegne in silenzio e la divergenza passa.",
     insieme(svuota("costanti_rispecchiate"),
             patch_json("config/deepsearch.config.json", "scoring.soglia_pubblicazione", 8.5))),

    ("SC-00", "M-I — liste_vs_enum svuotato e un esito rinominato solo in config.",
     insieme(svuota("liste_vs_enum"),
             patch_regex("config/deepsearch.config.json", r'"PARKED"', '"PARCHEGGIATA"'))),

    ("SC-00", "M-J — id_namespacati_per_run svuotato e pattern S- staccato da run_id.",
     insieme(svuota("id_namespacati_per_run"),
             patch_regex("schemas/core.schema.json",
                         r'\^S-\[0-9\]\{8\}\(-\[0-9\]\{4\}\)\?-\[0-9\]\{4\}\$',
                         '^S-[0-9]{3}$'))),

    ("SC-00", "M-A — agenti svuotato e WebSearch dato a bootstrap-filter. Sulla v2.8.1 non lo "
              "prendeva SC-07 (che degradava ad avviso) ma SC-12, per una ragione collaterale e con "
              "la diagnosi sbagliata: chi la legge ripara il budget e lascia WebSearch.",
     insieme(svuota("agenti/solo_giudizio"),
             patch_testo(".claude/agents/bootstrap-filter.md", "tools: Read, Write",
                         "tools: WebSearch, Read, Write"))),

    ("SC-11", "M-C — misalignment_min 2 → 20 con quattro criteri M: il ramo che la config chiama "
              "'meta' della missione' diventa irraggiungibile, e nessuna soglia e' fuori range.",
     patch_json("config/deepsearch.config.json",
                "competitor_thresholds.CALIBRATED.misalignment_min", 20)),

    ("SC-15", "M-E — campo required aggiunto a uno schema e nominato da nessun prompt: la direzione "
              "schema→prompt non era coperta da alcun controllo.",
     patch_json("schemas/redteam-report.schema.json",
                "properties.payload.required", ["findings", "kill_vote", "confutazione_richiesta"])),

    ("SC-09", "M-D — ramo PASS-B cancellato dal gate 2: l'enumerazione non torna piu'.",
     patch_regex(".claude/commands/deepsearch.md",
                 r"5\. PASS-B[\s\S]*?→ evidence_route: COMPORTAMENTO, prosegue ai gate 3\.\.8\n", "")),

    ("SC-12", "M-G — competitor-analyst sostituito da source-verifier nella wave 3: senza mappatura "
              "competitiva nessun kill e' possibile al gate 3, su ogni pain del run. Passava perche' "
              "SC-12 verificava la PRESENZA del nome, non il RUOLO.",
     patch_testo(".claude/commands/deepsearch.md",
                 "**competitor-analyst**, **trigger-finder**", "**source-verifier**, **trigger-finder**")),
]


# I difetti che solo l'ESECUZIONE ha trovato. Il run 20260802-1759 (BALANCED, reale) li ha prodotti
# con sei istanze cieche fra loro che convergevano sullo stesso rilievo — la convergenza e' cio' che
# li rende attendibili. Nessuna revisione a tavolino li aveva visti, incluse le tre avversariali.
DAL_CAMPO = [
    ("SC-11", "run 20260803 — evidence_searches_per_channel sotto 3x la finestra zero_yield: la "
              "saturazione non si raggiunge mai, il gate 2 non puo' uccidere, e VALIDAZIONE-SUL-CAMPO "
              "e' irraggiungibile. Il vincolo era DICHIARATO nel config e nessuno lo verificava.",
     patch_json("config/deepsearch.config.json", "funnel.LIGHT.evidence_searches_per_channel", 3)),

    ("SC-14", "il ramo terminale FP0 tolto dai codici di promozione: gli agenti tornano a coniare "
              "etichette proprie per dire 'non promosso', ed e' la terza occorrenza della stessa "
              "classe (enumerazione senza uscita per esclusione).",
     patch_json("config/deepsearch.config.json", "sources.fact_promotion_rules._codici",
                ["FP1", "FP2", "FP3", "FP4", "FP5"])),

    ("SC-23", "il rifiuto sugli URL non presenti nel prompt tolto da ds_call: un agente di "
              "retrieval delegato a un endpoint senza strumenti produce URL a memoria, e gli altri "
              "rifiuti non lo vedono (RX_SRC blocca gli ID di ledger, non gli URL).",
     patch_ovunque("tools/ds_call.py", "non presenti nel prompt", "non verificati")),

    ("SC-23", "§0 non prescrive piu' che sia l'orchestratore a cercare per gli agenti delegati: "
              "senza quel passo ogni chiamata esterna viene rifiutata e la delega e' inservibile.",
     patch_testo(".claude/commands/deepsearch.md", "**Un agente delegato NON cerca: estrae.**",
                 "**Nota sulla delega.**")),

    ("SC-22", "uno strumento che torna a leggere la chiave da os.environ per conto proprio: e' il "
              "difetto per cui provider_switch diceva 'chiave assente' su una chiave presente.",
     patch_testo("tools/provider_switch.py", "v, dove = trova_chiave(chiave)",
                 "v, dove = (os.environ.get(chiave), 'ambiente')")),

    ("SC-20", "un agente haiku tolto dai delegabili: la lista si stacca in silenzio dalla regola "
              "che dichiara di seguire, e nessuno se ne accorge finche' non serve.",
     lambda base: __import__("json").dump(
         (lambda d: (d["provider"].__setitem__(
             "delegabili", [x for x in d["provider"]["delegabili"] if x != "ranking-agent"]), d)[1])(
             __import__("json").load(open(os.path.join(base, "config",
                                                       "deepsearch.config.json"), encoding="utf-8"))),
         open(os.path.join(base, "config", "deepsearch.config.json"), "w", encoding="utf-8"),
         ensure_ascii=False, indent=2)),

    ("SC-21", "chiave API incollata nel config invece che in .env: config finisce in ogni zip e in "
              "ogni revisione esterna, quindi e' una chiave pubblicata.",
     patch_json("config/deepsearch.config.json", "provider.deepseek.api_key",
                CHIAVE_FINTA)),

    ("SC-20", "la mappa ATTIVA scavalca il permesso: red-team assegnato a deepseek senza essere "
              "fra i delegabili. E' il buco che un interruttore frettoloso aprirebbe.",
     lambda base: __import__("json").dump(
         (lambda d: (d["provider"].setdefault("attivo_per_agente", {}).update(
             {"red-team": "deepseek"}), d)[1])(
             __import__("json").load(open(os.path.join(base, "config",
                                                       "deepsearch.config.json"), encoding="utf-8"))),
         open(os.path.join(base, "config", "deepsearch.config.json"), "w", encoding="utf-8"),
         ensure_ascii=False, indent=2)),

    ("SC-20", "il red-team marcato delegabile al provider esterno: e' il risparmio piu' dannoso "
              "possibile, e non produce un errore visibile ma un PASS.",
     patch_json("config/deepseek_placeholder", "x", 0) if False else
     (lambda base: __import__("json").dump(
         (lambda d: (d["provider"]["delegabili"].append("red-team"), d)[1])(
             __import__("json").load(open(os.path.join(base, "config",
                                                       "deepsearch.config.json"), encoding="utf-8"))),
         open(os.path.join(base, "config", "deepsearch.config.json"), "w", encoding="utf-8"),
         ensure_ascii=False, indent=2))),

    ("SC-19", "tier abbassato nel frontmatter e non in config: il pain-finder passa a haiku e "
              "nessuno se ne accorge finche' il funnel non si svuota.",
     patch_testo(".claude/agents/pain-finder.md", "model: sonnet", "model: haiku")),

    ("SC-08", "run_close certifica VALIDO un run con il 30% delle citazioni orfane e le statistiche "
              "che raddoppiano il funnel: confrontava due numeri scritti dallo STESSO attore alla "
              "fine, e mai la loro corrispondenza col lavoro. §11 ora chiama reconcile_run: se "
              "quel passo sparisce, la catena torna scollegata dal lavoro.",
     patch_testo(".claude/commands/deepsearch.md",
                 "python3 tools/reconcile_run.py runs/<run_id>", "(passo rimosso)")),

    ("SC-18", "Le MIETITURE non erano dichiarabili in budget_used: quattro shard, quattro convenzioni "
              "private, consumi non confrontabili. Su uno shard la lettura del conteggio ha deciso "
              "fra tre pain e zero.",
     patch_regex("schemas/core.schema.json", r'"harvests": \{', '"harvests_RIMOSSO": {')),

    ("SC-16", "G2 scritto come 'frequenza < mensile' nel prompt e come 'occasionale o una tantum' in "
              "config: la soglia rimossa in v2.1 perche' uccideva gli adempimenti trimestrali, "
              "rimasta nel prompt per otto versioni. SC-03 non la vede: e' una regola in parole.",
     patch_testo(".claude/agents/pain-finder.md", "## CRITERI DI SUCCESSO",
                 "Se la ricorrenza e' inferiore a mensile applica `G2` e scarta.\n\n## CRITERI DI SUCCESSO")),

    ("SC-17", "Il prompt legge `employees_min`/`employees_max`, l'orchestratore passa `addetti_*`. "
              "Tre shard ciechi fra loro l'hanno segnalato. SC-01 non lo vede: guarda i percorsi "
              "`config.x`, non gli identificatori nudi.",
     patch_testo(".claude/agents/pain-finder.md", "addetti_min", "employees_min")),
]


def togli_da_lista_config(percorso, valore):
    """Rimuove un elemento da una lista del config, lasciando tutto il resto intatto."""
    def f(base):
        p = os.path.join(base, "config", "deepsearch.config.json")
        d = json.load(open(p, encoding="utf-8"))
        n = d
        for k in percorso.split(".")[:-1]:
            n = n[k]
        n[percorso.split(".")[-1]] = [x for x in n[percorso.split(".")[-1]] if x != valore]
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return f


def patch_schema(percorso_file, muta_dict):
    def f(base):
        p = os.path.join(base, percorso_file)
        d = json.load(open(p, encoding="utf-8"))
        muta_dict(d)
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return f


# Il quinto corpus. Difetti trovati ESEGUENDO il run 20260803, piu' quelli emersi riparandoli —
# perche' due delle riparazioni ne hanno aperto uno nuovo, ed e' il momento in cui un sistema
# di controlli guadagna o perde credibilita'.
QUINTO_RUN = [
    ("SC-03",
     "La riparazione del falso positivo di SC-03 (l'1 di 'n_direct >= 1' attribuito a "
     "misalignment_min sulla riga accanto) poteva accecare il controllo invece di restringerlo. "
     "Questa mutazione scrive un valore DAVVERO divergente accanto alla stessa etichetta, sulla "
     "stessa riga a piu' confronti: se ora tace, la riparazione era una disattivazione.",
     patch_testo(".claude/commands/deepsearch.md",
                 ">= misalignment_min criteri M", ">= misalignment_min 3 criteri M")),

    ("SC-09",
     "Un ramo del gate 3 rimosso dall'elenco mentre la prosa continua a dire 'Dieci rami'. E' la "
     "forma esatta con cui il gate era arrivato a non partizionare il proprio spazio: "
     "un'enumerazione che promette una copertura che non ha piu'.",
     patch_regex(".claude/commands/deepsearch.md",
                 r"7\. nessun DIRETTO, >=1 SOSTITUTO senza spesa documentata.*\n", "")),

    ("SC-04",
     "La rotta ASSENZA tolta da UNO dei due schemi che la ricevono. SC-05 non la vede — chiede "
     "solo che il token sia ammesso da QUALCHE schema, e nell'altro c'e' ancora. L'orchestratore "
     "marcherebbe il valore prescritto dal gate 3 e l'artefatto verrebbe rifiutato in validazione "
     "DOPO aver speso l'intero stage competitor. Questa mutazione e' arrivata cieca al primo "
     "giro: il controllo che la riprende e' nato per rispondere.",
     patch_ovunque("schemas/competitor-report.schema.json", '"ASSENZA",', "")),

    ("SC-04",
     "HYPOTHESIS tolto dall'enum 'state' di uno schema di valle: e' lo stato in cui il repo era "
     "fino alla v2.13 in sette posti su nove. IC-1 ordina di declassare a HYPOTHESIS uno statement "
     "VERIFIED senza citazione risolvibile — 'l'agente non puo' opporsi' — e in quei sette posti "
     "quel valore non era scrivibile: applicare l'invariante faceva fallire la validazione "
     "dell'artefatto. Un obbligo enunciato senza lo slot per assolverlo, per la terza volta.",
     patch_schema("schemas/trigger-report.schema.json",
                  lambda d: d["properties"]["payload"]["properties"]["triggers"]["items"]
                  ["properties"]["state"].__setitem__(
                      "enum", ["VERIFIED", "ESTIMATED", "UNKNOWN"]))),

    ("SC-04",
     "la restrizione di Numeric.state tolta dalle eccezioni dichiarate: resta legittima nel "
     "merito (un numero non e' un'ipotesi) e diventa indistinguibile da una deriva. La differenza "
     "fra una scelta e una svista e' che la scelta sta scritta da qualche parte.",
     patch_schema("config/deepsearch.config.json",
                  lambda d: d["verifica_specifica"]["enum_condivisi"]["state"]
                  .__setitem__("restrizioni_ammesse", {}))),

    ("SC-24",
     "source_id tolto da UnreachableSource: e' lo stato in cui il repo e' stato per undici "
     "versioni. IC-4 impone di dichiarare la fonte irraggiungibile, IC-1 pretende che ogni "
     "citazione risolva, e la dichiarazione imposta dal primo era irricevibile per il secondo. "
     "Il run 20260803 chiuse DEGRADATO per aver fatto la cosa giusta.",
     patch_schema("schemas/core.schema.json",
                  lambda d: d["$defs"]["UnreachableSource"]["properties"].pop("source_id", None))),

    ("SC-24",
     "access_status riportato ai sei valori vecchi: tre dei sette motivi di rifiuto tornano non "
     "scrivibili. IC-4 chiede di registrare la fonte 'col proprio access_status' e per API_LIMIT, "
     "LOGIN_REQUIRED e NO_PUBLIC_DATA quel valore non esisterebbe — un obbligo enunciato e non "
     "assolvibile, che e' il difetto e non la sua conseguenza.",
     patch_schema("schemas/core.schema.json",
                  lambda d: d["$defs"]["Source"]["properties"]["access_status"].__setitem__(
                      "enum", ["OK", "PAYWALL", "BLOCKED", "NOT_FOUND", "ROBOTS", "PARTIAL"]))),

    ("SC-24",
     "reconcile_run smette di leggere unreachable_sources: torna a contare come orfana IC-1 una "
     "fonte che IC-4 obbliga a dichiarare, e il run torna DEGRADATO per onesta'.",
     patch_ovunque("tools/reconcile_run.py", "unreachable_sources", "unreachable_RIMOSSO")),

    ("SC-24",
     "run_close riconosce le irraggiungibili come provenienza ma perde il contrappeso: un claim "
     "VERIFIED sostenuto SOLO da pagine che nessuno ha aperto passerebbe IC-1, perche' ora quelle "
     "fonti risolvono nel ledger. E' il falso negativo comprato col falso positivo, e senza questa "
     "mutazione nessuno lo sorveglia.",
     patch_ovunque("tools/run_close.py", "VERIFIED", "VERIFICATO_")),

    ("SC-20",
     "source-verifier rimesso fra i delegabili. E' haiku a pieno titolo, quindi sotto la regola "
     "della v2.13 il controllo lo APPROVAVA: il suo lavoro e' confronto di stringa. Ma il secondo "
     "termine del confronto e' una pagina, e ds_call.py non passa 'tools'. Delegato, confronta lo "
     "snippet con se stesso e dice sempre di si'.",
     lambda base: __import__("json").dump(
         (lambda d: (d["provider"]["delegabili"].append("source-verifier"), d)[1])(
             __import__("json").load(open(os.path.join(base, "config",
                                                       "deepsearch.config.json"), encoding="utf-8"))),
         open(os.path.join(base, "config", "deepsearch.config.json"), "w", encoding="utf-8"),
         ensure_ascii=False, indent=2)),

    ("SC-08",
     "il tachimetro tolto da run_close: sparisce la misura di quante pagine il run abbia "
     "davvero aperto. E' il difetto che ha quasi fatto comprare un fetcher con rendering su "
     "un'ipotesi — 'WebFetch perde contenuto sui siti JS-heavy' — che nessun dato del sistema "
     "poteva confermare o smentire, perche' il sistema non contava i fetch. Un avviso che "
     "qualcuno toglie 'tanto e' solo un avviso' riporta tutto agli aneddoti.",
     patch_ovunque("tools/run_close.py", "snippet_source", "snippet_RIMOSSO")),

    ("SC-20",
     "la non-chiusura dichiarata senza il perche'. Una riga di divieto senza la sua ragione e' la "
     "riga che il prossimo turno cancella, perche' nessuno ricorda piu' cosa proteggeva — e' "
     "successo con l'avvertenza sul source-verifier, che diceva 'delegarlo e' legittimo'.",
     patch_schema("config/deepsearch.config.json",
                  lambda d: d["provider"]["chiuso_sul_proprio_input"]["source-verifier"]
                  .__setitem__("perche", ""))),

    # ---- corpus F: il red team della v2.14.1 -------------------------------------------
    # Cinque mutazioni scritte contro le riparazioni di QUESTA versione. Sono le uniche a cui
    # l'autore delle riparazioni non poteva rispondere in anticipo, ed e' l'unico numero che
    # conta: sulla v2.14.1 intatta, tre di queste cinque passavano cieche.

    ("SC-25",
     "S5 riaperto / v2.14 — il tetto di un profilo scende sotto la somma dei budget per istanza. "
     "E' lo stato REALE della v2.14.1: LIGHT 438 ricerche su un tetto di 300, BALANCED 860 su "
     "700, fetch fuori scala in tutti e tre. funnel._nota_budget lo chiama 'il guasto piu' "
     "insidioso possibile' — la pipeline si ferma a meta' e il report vuoto e' indistinguibile "
     "dall'esito legittimo — e nessun controllo lo guardava. Aggravante: LIGHT e' il profilo "
     "della sentinella settimanale.",
     patch_schema("config/deepsearch.config.json",
                  lambda d: d["funnel"]["LIGHT"].__setitem__("max_searches_total", 300))),

    ("SC-25",
     "il tetto sta sopra la somma ma sotto somma/warn_at_budget_pct: il freno all'80% scatta "
     "prima della fine del funnel dichiarato, su OGNI run. E' il modo in cui DEEP restava rotto "
     "pur avendo il tetto formalmente sopra la somma — 1600 contro 1576, verificato conforme da "
     "chiunque guardasse solo la prima disuguaglianza.",
     patch_schema("config/deepsearch.config.json",
                  lambda d: d["funnel"]["DEEP"].__setitem__("max_searches_total", 1650))),

    ("SC-26",
     "l'affermazione stantia sull'emettibilita' dei KILL. La v2.14 ha alzato "
     "evidence_searches_per_channel a 8 PROPRIO per riabilitare G1 e G3, e ha lasciato in piedi "
     "tre frasi che dichiaravano l'inemettibilita' come un fatto permanente. Il run "
     "20260803-1204 ha applicato la frase invece del calcolo: il sistema si era tolto da solo "
     "l'unico gate capace di uccidere per assenza di spesa, e non lo sapeva.",
     patch_schema("config/deepsearch.config.json",
                  lambda d: d["stop_rules"].__setitem__(
                      "_conseguenza_gate_2",
                      "l'evidence-researcher ha 3-4 ricerche per canale e una finestra di 2, "
                      "quindi 3x la finestra fa 6 e NESSUN profilo glielo concede: i KILL del "
                      "gate 2 diventano PARK."))),

    ("SC-26",
     "KillAnalysis tolto dal core: kill_basis e kill_attribution tornano campi senza uno slot. "
     "Quinta occorrenza in questo repo di un obbligo enunciato senza il posto dove assolverlo "
     "(dopo competitor STRONG, fact_promotion FP0, gate 3 G7, IC-4 senza source_id). Stavolta "
     "cade sull'unico strumento diagnostico che il sistema ha su SE STESSO: "
     "config.run.condizione_di_riprogettazione promette di decidere sulla distribuzione di "
     "kill_attribution su 5 run, e senza lo slot nessun programma puo' calcolarla.",
     patch_schema("schemas/core.schema.json",
                  lambda d: d["$defs"].pop("KillAnalysis", None))),

    ("SC-26",
     "run_close smette di leggere kill_attribution: gli slot restano, il lettore sparisce, e il "
     "quadro dei no torna vuoto. E' la variante piu' insidiosa dei tre difetti della v2.14 — li' "
     "lo slot mancava del tutto, qui c'e' e nessuno guarda se qualcuno lo riempie, quindi la "
     "specifica SEMBRA a posto.",
     patch_ovunque("tools/run_close.py", "kill_attribution", "kill_RIMOSSO")),

    # ---- corpus G: il controllo positivo CP-001 ----------------------------------------
    # Difetti trovati facendo attraversare i gate a un pain per cui esiste OGGI un mercato
    # pagante. Nessuno dei due era visibile leggendo il repo.

    ("SC-27",
     "la deroga di severity su K5 tolta dal config. Senza, K5 e' 'automaticamente MET' ogni volta "
     "che il competitor E' il sistema di record — e config dichiara sistema di record anche 'il "
     "portale pubblico obbligatorio quando esiste'. Per OGNI pain della forma 'adempimento "
     "telematico obbligatorio', il portale c'e' sempre, e' sempre un DIRETTO e uccide sempre al "
     "ramo 1, precludendo per costruzione il ramo 9 (DISALLINEAMENTO) che config.missione chiama "
     "meta' della missione. E 'adempimenti normativi ricorrenti con sanzione' e' l'angolo NUMERO 1 "
     "degli otto del pain-finder: il gate uccideva il terreno di caccia prescritto dalla pipeline. "
     "Gia' accaduto sul run 20260801 (P-013, quadratura fatture SdI) e registrato nel verbale fra "
     "le cose che hanno FUNZIONATO.",
     patch_schema("config/deepsearch.config.json",
                  lambda d: [k.pop("_severity", None)
                             for k in d["competitor_thresholds"]["CALIBRATED"]["kill_if_any"]
                             if k.get("id") == "K5"])),

    ("SC-28",
     "il numero di controlli dichiarato nella pagina pubblica diverge dal registro. E' la tesi "
     "dell'intero progetto applicata al verificatore stesso, ed e' successa davvero: tre documenti "
     "e due immagini dicevano '27 controlli' mentre il registro ne conteneva 28 — l'errore nasce "
     "sommando i nuovi a una base contata male (SC-01..SC-24 invece di SC-00..SC-24). Nessun "
     "controllo poteva vederlo, perche' le pagine HTML erano fuori perimetro: erano l'unico "
     "artefatto pubblico che nessuno confrontava.",
     patch_ovunque("index.html", "29 controlli", "27 controlli")),

    ("SC-27",
     "la deroga resta in config e l'agente non ha lo slot per dichiararla: E_IL_SISTEMA_DI_RECORD "
     "tolto dall'enum severity_reason di competitor-report.schema.json. E' la sesta forma della "
     "classe 'obbligo senza slot' e la piu' insidiosa, perche' la specifica in prosa resta "
     "perfetta. La PRIMA stesura di SC-27 non la vedeva: cercava 'K5' vicino a 'SOFT' nella "
     "prosa, e quelle due parole convivono in mezza pagina per altri motivi. Il controllo "
     "passava pulito con la deroga cancellata, cioe' era decorativo — trovato dall'autotest, "
     "non da una rilettura.",
     patch_schema("schemas/competitor-report.schema.json",
                  lambda d: d["properties"]["payload"]["properties"]["competitors"]["items"]
                             ["properties"]["criteria_evaluated"]["items"]["properties"]
                             ["severity_reason"]["enum"].remove("E_IL_SISTEMA_DI_RECORD"))),
]


def linea_di_base():
    """
    Quali controlli falliscono GIA' sul repo intatto.

    Senza questo passo l'autotest e' cieco su se stesso: se un controllo e' gia' rosso, la sua
    mutazione risulta "ripresa" anche non applicando niente, e il numero finale conta un successo
    che non e' avvenuto. E' successo davvero, con SC-21, nel turno in cui SC-21 e' stato scritto —
    perche' spec_check era stato eseguito PRIMA di aggiungere la mutazione e mai piu' dopo.
    L'ordine di verifica era sbagliato, e nessun controllo sorvegliava l'ordine.
    """
    base = tempfile.mkdtemp(prefix="baseline-")
    dst = os.path.join(base, "repo")
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "*.zip", "__pycache__"))
    try:
        r = subprocess.run([sys.executable, os.path.join(dst, "tools", "spec_check.py")],
                           capture_output=True, text=True, cwd=dst)
        return set(re.findall(r"FAIL\s+(SC-\d+)", r.stdout)), r.stdout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    gia_rossi, out_base = linea_di_base()
    if gia_rossi:
        print(f"\n  LINEA DI BASE SPORCA: {', '.join(sorted(gia_rossi))} falliscono gia' sul repo")
        print("  intatto. Ogni mutazione su quei controlli risulterebbe 'ripresa' senza fare")
        print("  niente, e il totale conterebbe successi mai avvenuti.")
        print("  Ripara il repo prima di fidarti di qualunque numero qui sotto.\n")
        print(out_base[out_base.find("ERRORI"):][:600])
    # (lettera, titolo, corpus). Un elenco invece di quattro blocchi copiati: la versione a
    # blocchi stampava la sezione B con esiti[n_st:], cioe' B piu' C piu' D, e cosi' le sezioni
    # successive. I totali erano giusti e le tabelle no — il modo piu' efficace di far perdere
    # fiducia a un pannello e' fargli dire due cose diverse nello stesso schermo.
    CORPORA = [
        ("A", "DIFETTI STORICI (scritti dallo stesso autore dei controlli)", MUTAZIONI),
        ("B", "DIFETTI INDIPENDENTI (proposti da una revisione avversariale)", INDIPENDENTI),
        ("C", "DIFETTI DELLA TERZA REVISIONE (6 su 8 passavano sulla v2.8.1)", TERZA_REVISIONE),
        ("D", "DIFETTI TROVATI DAL RUN REALE 20260802-1759", DAL_CAMPO),
        ("E", "DIFETTI DEL RUN 20260803 E DELLE LORO RIPARAZIONI", QUINTO_RUN),
    ]
    esiti = []
    for atteso, storia, muta in [m for _, _, c in CORPORA for m in c]:
        base = tempfile.mkdtemp(prefix="specselftest-")
        dst = os.path.join(base, "repo")
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "*.zip", "__pycache__"))
        try:
            muta(dst)
            r = subprocess.run([sys.executable, os.path.join(dst, "tools", "spec_check.py")],
                               capture_output=True, text=True, cwd=dst)
            preso = atteso in r.stdout and re.search(
                rf"FAIL\s+{re.escape(atteso)}\b", r.stdout) is not None
            # Un controllo gia' rosso sulla linea di base non puo' dimostrare niente qui:
            # l'esito non e' "preso", e' INDETERMINATO.
            esiti.append((atteso, storia, None if atteso in gia_rossi else preso, r.stdout))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    print(f"\n  spec_check — autotest")
    print(f"  {'-'*78}")
    i, riassunto = 0, []
    for lettera, titolo, corpus in CORPORA:
        fetta = esiti[i:i + len(corpus)]
        i += len(corpus)
        print(f"\n  {lettera}. {len(corpus)} {titolo}")
        for atteso, storia, preso, _ in fetta:
            print(f"    {'ok   ' if preso else ('CIECO' if preso is False else 'INDET')}  "
                  f"{atteso}  {storia.splitlines()[0][:60]}")
        riassunto.append(f"{lettera}: {sum(1 for e in fetta if e[2])}/{len(corpus)}")
    indeterminati = [e for e in esiti if e[2] is None]
    falliti = [e for e in esiti if e[2] is False]
    print(f"  {'-'*78}")
    print("  " + "   ·   ".join(riassunto) + f"   ·   totale: "
          f"{sum(1 for e in esiti if e[2])}/{len(esiti)}")
    print("  Il primo numero e' il meno interessante: misura la memoria, non la vista — un corpus")
    print("  di mutazioni scritte a partire dai controlli passa per costruzione. Contano B ed E:")
    print("  difetti proposti da altri, e difetti aperti dalle riparazioni di questa versione.\n")
    for atteso, storia, _, out in falliti:
        print(f"  >> {atteso} NON riprende questo difetto. Il controllo e' decorativo.")
        print(f"     {storia}\n")
    # I buchi NOTI e documentati non fanno fallire l'autotest: fanno fallire il silenzio.
    # Farlo uscire in rosso per sempre lo trasformerebbe in rumore, e il primo effetto sarebbe
    # togliere la mutazione invece di chiudere il buco.
    for atteso, storia, _, _ in indeterminati:
        print(f"  >> {atteso} INDETERMINATO: il controllo falliva gia' senza mutazione.")
    return 1 if (falliti or indeterminati) else 0


if __name__ == "__main__":
    sys.exit(main())
