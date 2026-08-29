#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEEPSEARCH — runner deterministico dei fixture e criteri di accettazione TP-M0.1-001 §7.

E' il CHIAMANTE del nucleo privato e l'unico chiamante di `_valida_sottoschema_test`.
Non stampa mai un percorso assoluto, un valore di istanza o il testo di un'eccezione.

Uso:  python tools/ds_validate_selftest.py
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import multiprocessing
import os
import shutil
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ds_validate as V  # noqa: E402
import ds_validate_concurrency as C  # noqa: E402
import ds_validate_report as R  # noqa: E402
from ds_validate_contract import (  # noqa: E402
    ARTIFACT_ROOTS,
    CORE_SCHEMA_ID,
    MAX_ERRORS,
    MAX_KEYWORD_BYTES,
    MAX_LOCATION_BYTES,
    MAX_MISSING_PROPERTY_BYTES,
    MAX_RECORD_BYTES,
    RECORD_VERSION,
    REDACTED_SEGMENT,
    CleanupStatus,
    Comparison,
    Completeness,
    FullOutcome,
    InvocationReasonCode,
    InvocationStatus,
    LegacyOutcome,
    REASON_CODES_VALIDATOR_ERROR,
    ReasonCode,
    TestValidationTarget,
    ValidationContext,
    ValidationErrorEntry,
    WriteReasonCode,
    WriteStatus,
    canonical_json,
    checksum_record,
    risultato_to_dict,
    sha256_hex,
    valida_contratto_record,
    INVOCATION_ERROR_FIELDS,
)

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE_REPO = os.path.dirname(QUI)
FIXTURE = os.path.join(QUI, "ds_validate_fixtures")
SCHEMI = os.path.join(RADICE_REPO, "schemas")

ARTEFATTO = "a" * 64
CANARY_ID = "canary.inv-0001"

#: sentinella per il parametro `istanza` di `esegui_schema_rotto`: distingue "non
#: passata" da un'istanza legittimamente vuota `{}`, che dopo P0-3 e' il caso da
#: esercitare e non piu' quello da evitare.
_DEFAULT_ISTANZA = object()


# ==========================================================================
# Utilita' di test
# ==========================================================================


def contesto(**kw) -> ValidationContext:
    base = dict(
        invocation_id="inv-0001",
        attempt=1,
        run_id="20260808-1200",
        role="pain-finder",
        stage="pain",
        artifact_id=ARTEFATTO,
        schema_ref="pain.schema.json",
        legacy_outcome=LegacyOutcome.ACCEPTED,
    )
    base.update(kw)
    return ValidationContext(**base)


def carica(rel: str):
    with open(os.path.join(FIXTURE, rel.replace("/", os.sep)), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def byte_fixture(rel: str) -> bytes:
    with open(os.path.join(FIXTURE, rel.replace("/", os.sep)), "rb") as fh:
        return fh.read()


class ImporterStrumentato:
    def __init__(self, fallisce=False, rompi_validatore=False):
        self.chiamate = 0
        self.fallisce = fallisce
        self.rompi_validatore = rompi_validatore

    def __call__(self):
        self.chiamate += 1
        if self.fallisce:
            raise ImportError("dipendenza opzionale non importabile")
        lib = V._importa_predefinito()
        if self.rompi_validatore:
            lib.jsonschema = _JsonschemaRotto(lib.jsonschema)
        return lib


class _ValidatoreRotto:
    def __init__(self, *a, **k):
        pass

    def iter_errors(self, istanza):
        raise RuntimeError("CANARY-ECCEZIONE-FORZATA-5e6c")


class _JsonschemaRotto:
    def __init__(self, vero):
        self._vero = vero
        self.Draft202012Validator = _ValidatoreRotto
        self.Draft202012Validator.check_schema = staticmethod(
            vero.Draft202012Validator.check_schema
        )


class LettoreStrumentato(V.LettoreSchemi):
    def __init__(self, radice, risolutore=None):
        super().__init__(radice, risolutore)
        self.letture = 0
        self.elencazioni = 0

    def elenca(self):
        self.elencazioni += 1
        return super().elenca()

    def leggi(self, nome):
        self.letture += 1
        return super().leggi(nome)


class OrologioFisso:
    def __init__(self, ora="2026-08-08T12:00:00.000000Z", ms=(1000, 1005)):
        self._ora = ora
        self._ms = list(ms)
        self._i = 0

    def ora_utc(self):
        return self._ora

    def monotonico_ms(self):
        v = self._ms[min(self._i, len(self._ms) - 1)]
        self._i += 1
        return v


class OrologioRotto(OrologioFisso):
    def ora_utc(self):
        return "non-un-timestamp"


class PubblicatoreGuasto(V.Pubblicatore):
    def __init__(self, guasto):
        self.guasto = guasto

    def apri(self, percorso):
        if self.guasto == "apri":
            raise OSError("apertura negata")
        return super().apri(percorso)

    def scrivi(self, fh, blob):
        if self.guasto == "scrivi":
            raise OSError("scrittura negata")
        return super().scrivi(fh, blob)

    def sincronizza(self, fh):
        if self.guasto == "fsync":
            raise OSError("fsync negato")
        return super().sincronizza(fh)

    def pubblica(self, pending, finale):
        if self.guasto == "pubblica":
            raise OSError("pubblicazione negata")
        return super().pubblica(pending, finale)


def adattatori(radice_schemi=None, importer=None, publisher=None, clock=None, id_gen=None,
               risolutore=None):
    lettore = LettoreStrumentato(radice_schemi or SCHEMI, risolutore)
    return V.Adattatori(
        importer=importer or V._importa_predefinito,
        reader=lettore,
        publisher=publisher or V.Pubblicatore(),
        clock=clock or V.Orologio(),
        id_generator=id_gen or V._genera_id_produzione,
    )


def radice_schemi_temporanea(tmp, sostituzioni=None, aggiunte=None, ometti=None) -> str:
    """Materializza una directory di schemi fidata per i casi negativi."""
    dest = os.path.join(tmp, "schemi")
    os.makedirs(dest, exist_ok=True)
    for nome in sorted(os.listdir(SCHEMI)):
        if not nome.endswith(".json"):
            continue
        if ometti and nome in ometti:
            continue
        shutil.copyfile(os.path.join(SCHEMI, nome), os.path.join(dest, nome))
    for nome, byte in (sostituzioni or {}).items():
        with open(os.path.join(dest, nome), "wb") as fh:
            fh.write(byte)
    for nome, byte in (aggiunte or {}).items():
        with open(os.path.join(dest, nome), "wb") as fh:
            fh.write(byte)
    return dest


def id_fisso(valore):
    return lambda: valore


def deref_puntatore(documento, puntatore: str):
    """Dereferenzia SOLO RFC 6901. Un puntatore con `~R` e' un errore di programma."""
    if REDACTED_SEGMENT in puntatore:
        raise AssertionError("tentata dereferenza di una RLE redatta")
    nodo = documento
    if puntatore == "":
        return nodo
    for pezzo in puntatore.split("/")[1:]:
        chiave = pezzo.replace("~1", "/").replace("~0", "~")
        nodo = nodo[chiave] if isinstance(nodo, dict) else nodo[int(chiave)]
    return nodo


# --- errori sintetici per i test di bound / troncamento -------------------

SINT_ID = "sintetico.schema.json"
NOME_LUNGO = "L" * 600
SINT = {
    "$id": SINT_ID,
    "type": "object",
    "properties": {"campo": {"type": "string"}, NOME_LUNGO: {"type": "string"}},
}
SINT_DOCS = {SINT_ID: SINT}


class ErroreFinto:
    def __init__(self, schema_path, path, validator, message="", validator_value=None):
        self.absolute_schema_path = schema_path
        self.absolute_path = path
        self.validator = validator
        self.message = message
        self.validator_value = validator_value


class IteratoreContato:
    def __init__(self, elementi):
        self._elementi = list(elementi)
        self.prelievi = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.prelievi >= len(self._elementi):
            raise StopIteration
        v = self._elementi[self.prelievi]
        self.prelievi += 1
        return v


def voce_base(**kw) -> ValidationErrorEntry:
    base = dict(
        schema_resource_id="pain.schema.json",
        schema_pointer="/properties/payload",
        instance_pointer="/payload",
        instance_location="/payload",
        location_is_exact=True,
        keyword="type",
        missing_property=None,
    )
    base.update(kw)
    return ValidationErrorEntry(**base)


# ==========================================================================
# Guardia socket — zero chiamate di rete
# ==========================================================================

CHIAMATE_RETE = {"n": 0}


def installa_guardia_socket():
    def blocca(nome):
        def _f(*a, **k):
            CHIAMATE_RETE["n"] += 1
            raise AssertionError("chiamata di rete vietata: " + nome)

        return _f

    socket.socket.connect = blocca("connect")
    socket.socket.connect_ex = blocca("connect_ex")
    socket.socket.bind = blocca("bind")
    socket.socket.sendto = blocca("sendto")
    socket.create_connection = blocca("create_connection")


# ==========================================================================
# Worker multiprocesso (spawn) — devono stare a livello di modulo
# ==========================================================================


def _risultato_per_scrittura(radice_schemi):
    ese = V._esegui_shadow_core(
        {},
        contesto(schema_ref=CORE_SCHEMA_ID),
        config=V.ConfigTest(attivo=True, radice_output=None),
        adattatori=adattatori(radice_schemi),
    )
    return ese.validation_result


def worker_scrive(radice_output, record_id, radice_schemi):
    installa_guardia_socket()
    risultato = _risultato_per_scrittura(radice_schemi)
    ric = V.scrivi_record(
        risultato,
        contesto(schema_ref=CORE_SCHEMA_ID),
        radice_output,
        adattatori(radice_schemi, id_gen=id_fisso(record_id)),
    )
    return ric.status.value + "|" + (ric.write_reason_code.value if ric.write_reason_code else "-")


class _PubblicatoreCheMuore(V.Pubblicatore):
    def pubblica(self, pending, finale):
        os._exit(9)


def worker_guasto_prima_di_pubblicare(radice_output, record_id, radice_schemi):
    risultato = _risultato_per_scrittura(radice_schemi)
    V.scrivi_record(
        risultato,
        contesto(schema_ref=CORE_SCHEMA_ID),
        radice_output,
        adattatori(radice_schemi, publisher=_PubblicatoreCheMuore(), id_gen=id_fisso(record_id)),
    )
    os._exit(0)


# ==========================================================================
# Registro dei controlli
# ==========================================================================

RISULTATI = []

#: `DEC-14` — i criteri non ESEGUITI su questa piattaforma. Non entrano in
#: `RISULTATI`, perche' non sono ne' superati ne' falliti: sono un terzo esito, e il
#: riepilogo li conta a parte. Un criterio non eseguito e' un fatto da riportare.
NON_APPLICABILI = []

#: nome del criterio -> "passed" | "failed" | "not_applicable"
CRITERI_ESITI = {}

#: Casi che questa piattaforma non sa COSTRUIRE: dichiarati, contati e stampati,
#: mai dedotti. Non e' la guardia di `DEC-14`, che vale per `T8b` e per nient'altro
#: e conta i CRITERI registrati: qui non c'e' alcun criterio, `CRITERI_ESITI` non si
#: muove e i tre contatori dei criteri differiti restano quelli di prima. Entra
#: invece nel totale dei CONTROLLI, come `NON_APPLICABILI`: un caso non eseguito e'
#: un fatto da riportare — mai un vuoto, mai un verde.
NON_ESEGUITI = []


def check(nome, condizione, dettaglio=""):
    RISULTATI.append((nome, bool(condizione), dettaglio if not condizione else ""))


# ==========================================================================
# Il corpus schema: una capability, non una piattaforma
#
# Quasi tutta questa suite valida istanze reali contro gli schemi reali. Quel
# lavoro ha una dipendenza opzionale — `jsonschema` piu' `referencing` — e
# `ds_validate` la tratta come deve: quando manca non indovina, esce
# `VALIDATOR_ERROR / DEPENDENCY_MISSING` e non valida nulla. E' il
# comportamento GIUSTO dello strumento, ed era il banco a non saperlo leggere:
# su una radice senza quel backend CENTOTTANTA controlli su 453 registrati
# diventavano rossi, e due sezioni morivano con un `IndexError` — perche'
# chiedevano il primo elemento di un elenco di errori che non poteva esistere.
#
# La cifra e' DERIVABILE, e la derivazione e' questa: si riporta il file alla
# baseline, si esegue la sequenza di `main()` una sezione per volta contando le
# rosse attribuite a ciascuna, e si somma. Ripartizione misurata su Ubuntu
# 3.12.3 senza `referencing`:
#
#   fixture 113 · 7_5 4 · 7_6 2 · 7_7 6 · 7_8 3 · 7_9_7_10 1 · a1 5 (interrotta
#   dall'`IndexError`) · a4 3 · a5 6 · p0_3_p0_4 9 · r21_criteri 12 (interrotta
#   dall'`IndexError`) · r21_punti 13 · 7_12 2 · 7_13 1
#
# Zero rosse in 7_1, 7_2, 7_3, a2, concorrenza, 7_11, 7_14. Somma: CENTOTTANTA.
#
# La rossa di `sezione_7_13` — «rifiuto di contenimento tramite il risolutore
# iniettato», che senza backend riceve `DEPENDENCY_MISSING` invece di
# `SCHEMA_UNREADABLE` — va contata: una prima stesura diceva centosettantanove
# perche' l'aveva omessa, avendo censito le sezioni con una sonda che `7_13` non
# eseguiva. Il numero giusto e' quello che include tutte e ventuno le sezioni.
#
# LA CAPABILITY SI SONDA, NON SI DEDUCE. Non da `os.name`, non dalla versione
# di Python, non dalla semplice importabilita' di un modulo: si misura
# eseguendo una validazione VERA di un'istanza VERA attraverso il punto
# d'ingresso reale, e leggendo il verdetto che ne esce. Una radice che ha
# `referencing` importabile ma un validatore che non gira e' indistinguibile
# dalla prima per chi guarda gli import, e perfettamente distinguibile per chi
# guarda l'esito.
#
# E il corpus e' UN BLOCCO ATOMICO. Quando la capability manca, le sezioni che
# lo esercitano non si eseguono: nessuna viene sminuzzata per salvarne i
# controlli che «funzionerebbero lo stesso». Salvarli significherebbe scegliere
# a mano quali asserzioni sopravvivono, cioe' fabbricare verdi scegliendo le
# domande facili. O il corpus si esercita per intero, o si dichiara non
# eseguito per intero — e il fail-closed `DEPENDENCY_MISSING`, che non ha
# bisogno del corpus per essere dimostrato, resta sorvegliato in entrambi i
# mondi.
# ==========================================================================

#: Le sezioni che il corpus schema rende eseguibili. L'appartenenza e' MISURATA:
#: ognuna di queste, eseguita senza il backend, produce almeno un fallimento o
#: un'eccezione; nessuna delle sezioni fuori elenco ne produce.
#:
#: LA PROTEZIONE E' PARZIALE, e va detto per quello che e'. Ci sono DUE registri —
#: questa costante e i nomi letterali del ramo che salta le sezioni in `main` — e la
#: guardia del blocco confronta l'uno con l'altro. Ne segue, misurato:
#:
#:   · togliere un nome, o aggiungerne uno in UN SOLO registro -> ROSSO nominato
#:     («si saltano ESATTAMENTE le sue sezioni, tutte e nessuna di piu'»), exit 1;
#:   · aggiungerne uno COERENTEMENTE IN ENTRAMBI i registri -> nessuna rossa, exit 0,
#:     e la sezione sparisce dai controlli eseguiti. Misurato su `sezione_a2`, radice
#:     senza backend: da 118 a 91 controlli totali, VENTISETTE in meno, zero falliti.
#:
#: Cioe': la guardia coglie l'incoerenza fra i due registri, NON la tenuta dell'insieme.
#: Che l'insieme sia stretto — che nessuna sezione dichiarata dipendente giri invece
#: benissimo — non e' sorvegliato da alcun controllo, ne' qui ne' sulla piattaforma con
#: capability, dove l'elenco non viene nemmeno consultato. E' stabilito per MISURA
#: esterna, eseguendo ogni sezione senza backend e osservando quali cadono, e va
#: ristabilito allo stesso modo ogni volta che l'elenco cambia.
SEZIONI_DEL_CORPUS = (
    "sezione_fixture",
    "sezione_7_5 (oltre il fail-closed)",
    "sezione_7_6",
    "sezione_7_7",
    "sezione_7_8",
    "sezione_7_9_7_10",
    "sezione_a1",
    "sezione_a4",
    "sezione_a5",
    "sezione_p0_3_p0_4",
    "sezione_r21_criteri",
    "sezione_r21_punti",
    "sezione_7_12",
    "sezione_7_13",
)

MOTIVO_CORPUS = "corpus schema non eseguibile: dipendenza di validazione assente"

CORPUS_ESEGUIBILE = "CORPUS_ESEGUIBILE"
CORPUS_NON_ESEGUIBILE = "CORPUS_NON_ESEGUIBILE"
TERZO_STATO = "TERZO_STATO"


def classifica_sonda_corpus(r):
    """I DUE stati ammessi, per intero. Ogni altra combinazione e' `TERZO_STATO`.

    Uno stato ammesso e' una CONGIUNZIONE. Un `FULL_PASS` senza le due versioni
    non dimostra che il backend abbia lavorato; un `VALIDATOR_ERROR` con un
    `reason_code` diverso da `DEPENDENCY_MISSING` non e' un ambiente incompleto,
    e' un guasto — e con le versioni valorizzate sarebbe la prova che il backend
    c'era. Accettare la congiunzione parziale dichiarerebbe indisponibile una
    capability che invece funziona, che e' il modo piu' silenzioso di non
    eseguire una suite.
    """
    if r is None:
        return TERZO_STATO
    if (r.full_outcome is FullOutcome.FULL_PASS
            and r.reason_code is None
            and r.validator_version is not None
            and r.referencing_version is not None):
        return CORPUS_ESEGUIBILE
    if (r.full_outcome is FullOutcome.VALIDATOR_ERROR
            and r.reason_code is ReasonCode.DEPENDENCY_MISSING
            and r.validator_version is None
            and r.referencing_version is None):
        return CORPUS_NON_ESEGUIBILE
    return TERZO_STATO


def sonda_corpus():
    """(stato, risultato) dal punto d'ingresso REALE, con gli adattatori reali.

    NON cattura: puo' sollevare. Chi cattura e' `main`, e in nessun altro posto.
    La ragione: un'eccezione che salga da qui non e' un ambiente incompleto — e'
    un guasto, e convertirla in «corpus assente» sarebbe fabbricare un
    provisioning che nessuno ha misurato.

    Nessuna fixture nuova: l'istanza valida e gli schemi sono quelli che questa
    suite gia' porta, e l'importer e' quello di produzione — sondare con un
    importer iniettato misurerebbe l'iniezione, non la macchina.
    """
    esito = V._esegui_shadow_core(
        carica("valid/pain.ok.json"), contesto(),
        config=V.ConfigTest(attivo=True, radice_output=None),
        adattatori=adattatori())
    r = esito.validation_result
    return classifica_sonda_corpus(r), r


# LIMITE REGISTRATO — non esiste, in questo file, un oracle che sorvegli il ripristino dei seam.
# Qui ne esisteva uno che confrontava l'identita' di `ds_validate._importa_predefinito`,
# `ds_validate.Pubblicatore` e `ds_validate.Orologio` prima e dopo la corsa. Misurato che non
# poteva cadere: questa suite non assegna MAI ad alcun attributo di `V` — inietta per PARAMETRO,
# tramite `adattatori(importer=…, publisher=…, clock=…)`, che e' una scelta di disegno e non un
# caso. Un oracle il cui bersaglio non esiste e' un verde fabbricato: contava come controllo
# superato senza poter misurare nulla, ed e' stato tolto invece che riformulato.
#
# E' UN LIMITE, NON UN VUOTO: il file sostituisce attributi di modulo, solo non di `V`. Censiti
# per AST, a qualunque profondita' di catena: cinque superfici di `os` (`listdir`, `lstat`,
# `scandir`, `stat`, `path.isfile`), ripristinate nel loro `finally`, e cinque di `socket`
# (`create_connection`, `socket.bind`, `socket.connect`, `socket.connect_ex`, `socket.sendto`),
# che la guardia di rete sostituisce PER TUTTA la corsa e deliberatamente non ripristina.
# Nessuna delle dieci e' sorvegliata da un confronto d'identita'. Chi volesse sorvegliarle
# dovrebbe distinguere le prime — che un `finally` deve rendere — dalle seconde, che per
# disegno non tornano mai indietro; qui non e' stato fatto, ed e' questo il limite.
#
# Se un giorno una sezione sostituisse un attributo di `V`, questo controllo andrebbe riportato
# puntandolo su QUELL'attributo — come fanno `pack_repair_selftest` su `pack_repair.ripara` e
# `replay_report_selftest` su `replay_validate.valida_schema`, dove il bersaglio esiste e la
# rimozione del ripristino produce un rosso misurato.


def non_eseguito_per_corpus(nome):
    """Dichiara, conta e stampa. Mai un vuoto, mai un verde."""
    NON_ESEGUITI.append((nome, MOTIVO_CORPUS))


def criterio_fuori_corpus(nome):
    """Un criterio differito che il corpus assente rende non valutabile.

    Distinto da `criterio_non_applicabile`, che e' la guardia di PIATTAFORMA di
    `DEC-14` e vale per `T8b` e per nient'altro. Qui la ragione non e' il sistema
    operativo ma una capability misurata, e l'esito finisce fra i NON ESEGUITI —
    non fra i `NOT_APPLICABLE_WINDOWS`, che direbbero una cosa falsa.
    """
    CRITERI_ESITI[nome] = "not_applicable"


# ==========================================================================
# 7.1 — superfici di esecuzione e chiusura della produzione
# ==========================================================================


def _produzione_valida():
    return V.esegui_shadow_produzione({}, contesto())


def _produzione_contesto_malformato():
    return V.esegui_shadow_produzione({}, contesto(invocation_id="../fuori"))


def _produzione_frammento():
    return V.esegui_shadow_produzione({}, contesto(schema_ref="core.schema.json#/$defs/Source"))


def sezione_7_1():
    firma = inspect.signature(V.esegui_shadow_produzione)
    check("7.1 firma pubblica (istanza, contesto)",
          list(firma.parameters) == ["istanza", "contesto"], str(list(firma.parameters)))

    pubblici = [n for n in dir(V) if not n.startswith("_")]
    globali_mutabili = [
        n for n in pubblici
        if isinstance(getattr(V, n), (V.PoliticaProduzione, V.ConfigTest))
    ]
    check("7.1 nessun global pubblico di policy/output root", globali_mutabili == [],
          str(globali_mutabili))
    nomi_switch = [n for n in vars(V)
                   if n.lower() in ("attivo", "radice_output", "shadow_attivo", "output_root")]
    check("7.1 nessun global di modulo per switch e radice di output",
          nomi_switch == [], str(nomi_switch))
    p1, p2 = V._politica_produzione(), V._politica_produzione()
    check("7.1 factory privata, policy congelata e OFF",
          p1.attivo is False and p1.radice_output is None and p1 == p2
          and getattr(V.PoliticaProduzione, "__dataclass_params__").frozen)
    setter = [n for n in pubblici
              if n.startswith(("set_", "imposta", "configura", "attiva", "abilita"))
              or n == "politica_produzione"]
    check("7.1 nessun setter pubblico della policy", setter == [], str(setter))

    e = _produzione_valida()
    check("7.1 produzione valida -> SHADOW_DISABLED",
          e.invocation_status is InvocationStatus.OK
          and e.validation_result.full_outcome is FullOutcome.NOT_EVALUATED
          and e.validation_result.reason_code is ReasonCode.SHADOW_DISABLED
          and e.write_receipt.status is WriteStatus.NOT_ATTEMPTED
          and e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE)

    e = _produzione_contesto_malformato()
    check("7.1 produzione contesto malformato -> CONTEXT_INVALID",
          e.invocation_status is InvocationStatus.INVOCATION_ERROR
          and e.invocation_error.reason_code is InvocationReasonCode.CONTEXT_INVALID
          and e.validation_result is None
          and e.write_receipt.status is WriteStatus.NOT_ATTEMPTED
          and e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE)

    e = _produzione_frammento()
    check("7.12 la produzione rifiuta ogni rotta a frammento",
          e.invocation_status is InvocationStatus.INVOCATION_ERROR
          and e.invocation_error.field == "schema_ref")
    check("7.12 nessun parametro target sulla superficie pubblica",
          "target" not in inspect.signature(V.esegui_shadow_produzione).parameters)

    for ese in (_produzione_valida(), _produzione_contesto_malformato()):
        check("7.1 ShadowExecution sempre completa",
              ese.write_receipt is not None
              and ((ese.validation_result is None) != (ese.invocation_error is None)))

    # scansione meccanica: nessuna funzione che invoca la produzione asserisce
    # un esito raggiungibile solo dal passo 4 in poi.
    vietati = {
        "SCHEMA_NOT_MAPPED", "OUTPUT_ROOT_NOT_CONFIGURED", "DEPENDENCY_MISSING",
        "SCHEMA_UNREADABLE", "SCHEMA_META_INVALID", "SCHEMA_ID_COLLISION",
        "SCHEMA_ROOT_OUTSIDE_REGISTRY", "REF_UNRESOLVABLE", "INTERNAL_VALIDATION_ERROR",
        "FULL_PASS", "FULL_FAIL",
    }
    sorgente = open(os.path.abspath(__file__), "rb").read().decode("utf-8")
    albero = ast.parse(sorgente)
    colpevoli = []
    for nodo in ast.walk(albero):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        usa_produzione = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "esegui_shadow_produzione"
            for n in ast.walk(nodo)
        )
        if not usa_produzione:
            continue
        for n in ast.walk(nodo):
            if isinstance(n, ast.Attribute) and n.attr in vietati:
                colpevoli.append(nodo.name + ":" + n.attr)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in vietati:
                colpevoli.append(nodo.name + ":" + n.value)
    check("7.1 scansione meccanica: nessuna asserzione di produzione dal passo 4 in poi",
          colpevoli == [], str(colpevoli))


# ==========================================================================
# 7.2 — precedenza, casi combinati e ramo di mappatura
# ==========================================================================


def sezione_7_2(tmp):
    imp = ImporterStrumentato(fallisce=True)
    lettore_conta = LettoreStrumentato(SCHEMI)
    ad = V.Adattatori(importer=imp, reader=lettore_conta, publisher=V.Pubblicatore(),
                      clock=V.Orologio(), id_generator=V._genera_id_produzione)

    e = V._esegui_shadow_core({}, contesto(role="ruolo/non valido"),
                              config=V.ConfigTest(attivo=True, radice_output=None), adattatori=ad)
    check("7.2 contesto invalido + dipendenza assente -> CONTEXT_INVALID, nessun import",
          e.invocation_status is InvocationStatus.INVOCATION_ERROR
          and e.invocation_error.reason_code is InvocationReasonCode.CONTEXT_INVALID
          and imp.chiamate == 0 and lettore_conta.letture == 0
          and e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE)

    e = V._esegui_shadow_core({}, contesto(schema_ref=None),
                              config=V.ConfigTest(attivo=True, radice_output=None), adattatori=ad)
    check("7.2 schema_ref None + dipendenza assente -> SCHEMA_NOT_MAPPED, nessun import",
          e.validation_result.reason_code is ReasonCode.SCHEMA_NOT_MAPPED and imp.chiamate == 0)

    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo=False, radice_output=None), adattatori=ad)
    check("7.2 OFF + dipendenza assente -> SHADOW_DISABLED, nessun import, nessuna scrittura",
          e.validation_result.reason_code is ReasonCode.SHADOW_DISABLED and imp.chiamate == 0
          and e.write_receipt.status is WriteStatus.NOT_ATTEMPTED
          and e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE)

    e = V._esegui_shadow_core({}, contesto(attempt=0),
                              config=V.ConfigTest(attivo=False, radice_output=None), adattatori=ad)
    check("7.2 contesto invalido + OFF -> il passo 2 precede il 3",
          e.invocation_status is InvocationStatus.INVOCATION_ERROR)

    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo="si", radice_output=None), adattatori=ad)
    check("7.2 config invalida + contesto valido -> CONFIG_INVALID",
          e.invocation_status is InvocationStatus.INVOCATION_ERROR
          and e.invocation_error.reason_code is InvocationReasonCode.CONFIG_INVALID
          and e.invocation_error.field == "attivo")

    check("7.2 validazione config/contesto senza I/O e senza import opzionale",
          lettore_conta.letture == 0 and lettore_conta.elencazioni == 0 and imp.chiamate == 0)

    # ramo di mappatura, nucleo privato attivo
    for rif, atteso_ref in (("core.schema.json", "core.schema.json"), (None, None)):
        e = V._esegui_shadow_core({}, contesto(schema_ref=rif),
                                  config=V.ConfigTest(attivo=True, radice_output=None),
                                  adattatori=adattatori())
        check("7.2 mappatura schema_ref=%s -> SCHEMA_NOT_MAPPED" % rif,
              e.validation_result.full_outcome is FullOutcome.NOT_EVALUATED
              and e.validation_result.reason_code is ReasonCode.SCHEMA_NOT_MAPPED
              and e.validation_result.schema_ref == atteso_ref
              and e.validation_result.full_outcome is not FullOutcome.FULL_PASS)
        check("7.2 mappatura senza radice legata -> OUTPUT_ROOT_NOT_CONFIGURED",
              e.write_receipt.status is WriteStatus.NOT_ATTEMPTED
              and e.write_receipt.write_reason_code is WriteReasonCode.OUTPUT_ROOT_NOT_CONFIGURED)

        radice = os.path.join(tmp, "map-" + str(rif))
        e = V._esegui_shadow_core({}, contesto(schema_ref=rif),
                                  config=V.ConfigTest(attivo=True, radice_output=radice),
                                  adattatori=adattatori())
        finali = [n for n in os.listdir(radice) if n.endswith(".json")]
        check("7.2 mappatura con radice legata -> esattamente una scrittura",
              e.write_receipt.status is WriteStatus.WRITTEN and len(finali) == 1)


# ==========================================================================
# 7.3 — errori di invocazione
# ==========================================================================

VALORI_OFFENSIVI = {
    "tipo_errato": dict(invocation_id=123),
    "campo_mancante": None,  # gestito a parte
    "enum_legacy_invalido": dict(legacy_outcome="ACCEPTED"),
    "attempt_fuori_range": dict(attempt=100),
    "grammatica": dict(role="ruolo con spazi"),
    "traversal": dict(stage="../../etc"),
    "lettera_di_unita": dict(run_id="C:cartella"),
    "unc": dict(invocation_id="\\\\server\\share"),
    "separatore": dict(role="a/b"),
    "carattere_di_controllo": dict(stage="pa\x01in"),
    "oversize": dict(invocation_id="z" * 65),
    "artifact_id_non_hex": dict(artifact_id="Z" * 64),
    "schema_ref_frammento": dict(schema_ref="pain.schema.json#/$defs/Envelope"),
}


class ContestoIncompleto:
    invocation_id = "inv-0001"
    attempt = 1
    run_id = "20260808-1200"
    role = "pain-finder"
    stage = "pain"
    # artifact_id assente


def sezione_7_3():
    ad = adattatori()
    for nome, kw in VALORI_OFFENSIVI.items():
        if kw is None:
            continue
        try:
            ctx = contesto(**kw)
        except TypeError:
            ctx = None
        if ctx is None:
            continue
        e = V._esegui_shadow_core({}, ctx,
                                  config=V.ConfigTest(attivo=True, radice_output=None),
                                  adattatori=ad)
        ok = (
            e.invocation_status is InvocationStatus.INVOCATION_ERROR
            and e.invocation_error.reason_code is InvocationReasonCode.CONTEXT_INVALID
            and e.validation_result is None
            and e.invocation_error.field in INVOCATION_ERROR_FIELDS
        )
        valore = list(kw.values())[0]
        reso = repr((e.invocation_status, e.invocation_error.reason_code,
                     e.invocation_error.field, e.validation_result, e.write_receipt))
        perde = isinstance(valore, str) and valore in reso
        check("7.3 " + nome + " -> CONTEXT_INVALID, valore offensivo mai restituito",
              ok and not perde, str(e.invocation_error.reason_code))

    # A1 rev4 §2.2 — un contesto duck-typed e' ora rifiutato sul TIPO, prima che uno
    # qualsiasi dei suoi attributi venga letto. Il campo citato non e' piu' il primo
    # mancante (`artifact_id`): non si arriva a guardarlo. Deve restare nell'insieme
    # chiuso di §4.3, che non ha un membro per "l'oggetto contesto".
    e = V._esegui_shadow_core({}, ContestoIncompleto(),
                              config=V.ConfigTest(attivo=True, radice_output=None), adattatori=ad)
    check("7.3 contesto duck-typed -> CONTEXT_INVALID sul tipo",
          e.invocation_status is InvocationStatus.INVOCATION_ERROR
          and e.invocation_error.reason_code is InvocationReasonCode.CONTEXT_INVALID
          and e.invocation_error.field in INVOCATION_ERROR_FIELDS
          and e.validation_result is None)

    for oggetto in ("non-un-contesto", 42, None, object()):
        e = V._esegui_shadow_core({}, oggetto,
                                  config=V.ConfigTest(attivo=True, radice_output=None),
                                  adattatori=ad)
        check("7.3 input Python malformato non fa uscire eccezioni",
              e.invocation_status is InvocationStatus.INVOCATION_ERROR
              and e.validation_result is None
              and e.invocation_error.field in INVOCATION_ERROR_FIELDS)

    e = V._esegui_shadow_core({}, contesto(legacy_outcome="ACCEPTED"),
                              config=V.ConfigTest(attivo=True, radice_output=None), adattatori=ad)
    check("7.3 nessuna struttura contiene un LegacyOutcome fabbricato",
          e.validation_result is None and e.invocation_error.field == "legacy_outcome")


# ==========================================================================
# 7.4 / 7.6 / 7.7 / 7.12 — corsa sui fixture
# ==========================================================================

MANIFEST = None
DOCUMENTI = None


def documenti_schemi():
    global DOCUMENTI
    if DOCUMENTI is None:
        DOCUMENTI = {}
        for n in sorted(os.listdir(SCHEMI)):
            if n.endswith(".json"):
                with open(os.path.join(SCHEMI, n), "rb") as fh:
                    DOCUMENTI[n] = json.loads(fh.read().decode("utf-8"))
    return DOCUMENTI


def controlla_voci(voci, etichetta):
    docs = documenti_schemi()
    for voce in voci:
        risorsa = docs.get(voce.schema_resource_id)
        ok = risorsa is not None
        if ok:
            try:
                deref_puntatore(risorsa, voce.schema_pointer)
            except Exception:
                ok = False
        check("7.6 " + etichetta + " puntatore dereferenziabile nella risorsa nominata", ok,
              voce.schema_resource_id + voce.schema_pointer)
        coerente = (voce.location_is_exact is (voce.instance_pointer is not None)) and (
            not voce.location_is_exact or voce.instance_pointer == voce.instance_location
        )
        if REDACTED_SEGMENT in voce.instance_location:
            coerente = coerente and voce.location_is_exact is False
        check("7.6 invariante 9 su " + etichetta, coerente)
        check("7.7 invariante 10 su " + etichetta, V.voce_conforme(voce))


def sezione_fixture(tmp):
    global MANIFEST
    MANIFEST = carica("manifest.json")
    voci_manifest = MANIFEST["entries"]

    esiti_visti = set()
    codici_visti = set()

    for voce in voci_manifest:
        percorso = voce["path"]
        kind = voce["kind"]
        if kind == "validation_instance":
            istanza = carica(percorso)
            e = V._esegui_shadow_core(
                istanza, contesto(schema_ref=voce["target_ref"]),
                config=V.ConfigTest(attivo=True, radice_output=None),
                adattatori=adattatori())
            r = e.validation_result
            atteso = FullOutcome(voce["expected_full_outcome"])
            check("7.4 " + percorso + " -> " + voce["expected_full_outcome"],
                  r.full_outcome is atteso, str(r.full_outcome) + "/" + str(r.reason_code))
            esiti_visti.add(r.full_outcome)
            if atteso is FullOutcome.FULL_PASS:
                check("7.4 " + percorso + " contatori a zero",
                      r.errors == () and r.errors_recorded == 0
                      and r.errors_dropped_oversize == 0 and r.errors_truncated is False
                      and r.reason_code is None)
            if atteso is FullOutcome.FULL_FAIL:
                check("7.4 " + percorso + " invariante 5",
                      r.reason_code is None
                      and r.errors_recorded + r.errors_dropped_oversize >= 1)
            controlla_voci(r.errors, percorso)

            # determinismo: due corse producono liste di errori byte-identiche
            e2 = V._esegui_shadow_core(
                istanza, contesto(schema_ref=voce["target_ref"]),
                config=V.ConfigTest(attivo=True, radice_output=None),
                adattatori=adattatori())
            check("7.7 " + percorso + " due corse byte-identiche",
                  canonical_json(risultato_to_dict(r)["errors"])
                  == canonical_json(risultato_to_dict(e2.validation_result)["errors"]))

        elif kind == "subschema_instance":
            istanza = carica(percorso)
            target = TestValidationTarget[voce["target_ref"]]
            e = V._valida_sottoschema_test(
                istanza, contesto(schema_ref=CORE_SCHEMA_ID), target=target,
                adattatori=adattatori())
            r = e.validation_result
            atteso = FullOutcome(voce["expected_full_outcome"])
            check("7.12 " + percorso + " -> " + voce["expected_full_outcome"],
                  r.full_outcome is atteso, str(r.full_outcome) + "/" + str(r.reason_code))
            check("7.12 " + percorso + " campi del canale $defs",
                  r.schema_ref == CORE_SCHEMA_ID
                  and r.schema_sha256 == sha256_hex(open(os.path.join(SCHEMI, CORE_SCHEMA_ID), "rb").read())
                  and all(v.schema_resource_id == CORE_SCHEMA_ID for v in r.errors)
                  and all(v.schema_pointer.startswith("/$defs/") for v in r.errors))
            check("7.9 " + percorso + " la rotta $defs non persiste mai",
                  e.write_receipt.status is WriteStatus.NOT_ATTEMPTED
                  and e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE)
            controlla_voci(r.errors, percorso)

        elif kind == "schema_fixture":
            r = esegui_schema_rotto(tmp, percorso)
            atteso = ReasonCode(voce["expected_reason_code"])
            check("7.4 " + percorso + " -> VALIDATOR_ERROR/" + voce["expected_reason_code"],
                  r.full_outcome is FullOutcome.VALIDATOR_ERROR and r.reason_code is atteso
                  and r.errors == (),
                  str(r.full_outcome) + "/" + str(r.reason_code))
            esiti_visti.add(r.full_outcome)
            codici_visti.add(r.reason_code)

    esiti_visti.add(FullOutcome.NOT_EVALUATED)
    check("7.4 tutti e quattro i FullOutcome esercitati",
          esiti_visti == set(FullOutcome), str(sorted(x.value for x in esiti_visti)))
    return codici_visti


def esegui_schema_rotto(tmp, percorso, istanza=_DEFAULT_ISTANZA):
    nome = percorso.split("/")[-1]
    byte = byte_fixture(percorso)
    sotto = os.path.join(tmp, "rotto-" + nome)
    os.makedirs(sotto, exist_ok=True)
    if nome in ("unreadable.schema.json", "meta-invalid.schema.json",
                "ref-external.schema.json", "ref-unregistered.schema.json"):
        radice = radice_schemi_temporanea(sotto, sostituzioni={"pain.schema.json": byte})
        rif = "pain.schema.json"
    elif nome in ("collide-a.schema.json", "collide-b.schema.json"):
        radice = radice_schemi_temporanea(sotto, aggiunte={
            "collide-a.schema.json": byte_fixture("schema-broken/collide-a.schema.json"),
            "collide-b.schema.json": byte_fixture("schema-broken/collide-b.schema.json")})
        rif = "pain.schema.json"
    elif nome == "id-basename-mismatch.schema.json":
        radice = radice_schemi_temporanea(sotto, aggiunte={nome: byte})
        rif = "pain.schema.json"
    else:  # root-outside.pointer.json — descrittore di selezione della radice
        descrittore = carica(percorso)
        radice = radice_schemi_temporanea(sotto, ometti={descrittore["omit_from_registry"]})
        rif = descrittore["schema_ref"]
    # L'istanza predefinita porta `payload`, perche' PRIMA di P0-3 senza di essa un
    # `$ref` sotto `properties.payload` non veniva mai seguito e il caso negativo non
    # era discriminante. Dopo il preflight della chiusura non serve piu': la
    # raggiungibilita' non dipende dall'istanza, ed e' proprio cio' che la sezione
    # P0-3 verifica passando `{}`.
    if istanza is _DEFAULT_ISTANZA:
        istanza = {"payload": {}}
    e = V._esegui_shadow_core(istanza, contesto(schema_ref=rif),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(radice))
    return e.validation_result


# ==========================================================================
# 7.5 — codici di ragione e di scrittura
# ==========================================================================


def _codice_scrittura_produzione():
    return V.esegui_shadow_produzione({}, contesto()).write_receipt.write_reason_code


def sezione_7_5(tmp, codici_visti, corpus_eseguibile=True):
    # IL FAIL-CLOSED VIENE PRIMA, E NON DIPENDE DAL CORPUS. L'importer iniettato
    # fallisce per costruzione: la prova che `ds_validate` si chiude invece di
    # indovinare vale identica sulle due piattaforme, ed e' esattamente la prova
    # che una radice senza il backend ha piu' bisogno di vedere eseguita. Sta in
    # cima perche' il resto della sezione, che il corpus lo esercita davvero, non
    # deve poterla portare via con se'.
    imp = ImporterStrumentato(fallisce=True)
    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(importer=imp))
    r = e.validation_result
    check("7.5 DEPENDENCY_MISSING via importer iniettato",
          r.full_outcome is FullOutcome.VALIDATOR_ERROR
          and r.reason_code is ReasonCode.DEPENDENCY_MISSING
          and r.validator_version is None and r.referencing_version is None)
    codici_visti.add(r.reason_code)

    if not corpus_eseguibile:
        non_eseguito_per_corpus("sezione_7_5 (oltre il fail-closed)")
        return

    e = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(importer=ImporterStrumentato(rompi_validatore=True)))
    r = e.validation_result
    check("7.5 INTERNAL_VALIDATION_ERROR senza messaggio ne' traceback",
          r.full_outcome is FullOutcome.VALIDATOR_ERROR
          and r.reason_code is ReasonCode.INTERNAL_VALIDATION_ERROR
          and "CANARY-ECCEZIONE-FORZATA-5e6c" not in canonical_json(risultato_to_dict(r)))
    codici_visti.add(r.reason_code)

    for e2 in (V._esegui_shadow_core({}, contesto(),
                                     config=V.ConfigTest(attivo=False, radice_output=None),
                                     adattatori=adattatori()),
               V._esegui_shadow_core({}, contesto(schema_ref=None),
                                     config=V.ConfigTest(attivo=True, radice_output=None),
                                     adattatori=adattatori())):
        codici_visti.add(e2.validation_result.reason_code)

    check("7.5 tutti e nove i ReasonCode di validazione esercitati",
          codici_visti == set(ReasonCode),
          str(sorted(c.value for c in set(ReasonCode) - codici_visti)))

    # --- gli 11 WriteReasonCode ------------------------------------------
    visti = set()
    risultato = _risultato_per_scrittura(SCHEMI)
    ctx = contesto(schema_ref=CORE_SCHEMA_ID)

    visti.add(_codice_scrittura_produzione())
    e = V._esegui_shadow_core({}, contesto(schema_ref=None),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori())
    visti.add(e.write_receipt.write_reason_code)

    radice = os.path.join(tmp, "wrc")
    os.makedirs(radice, exist_ok=True)

    ric = V.scrivi_record(risultato, ctx, radice, adattatori(id_gen=id_fisso("zz")))
    visti.add(ric.write_reason_code)
    check("7.5 ID_GENERATION_INVALID", ric.write_reason_code is WriteReasonCode.ID_GENERATION_INVALID)

    ric = V.scrivi_record(risultato, ctx, radice, adattatori(clock=OrologioRotto()))
    visti.add(ric.write_reason_code)
    check("7.5 CLOCK_INVALID", ric.write_reason_code is WriteReasonCode.CLOCK_INVALID)

    rec = V.costruisci_record(risultato, ctx, "b" * 32, "2026-08-08T12:00:00.000000Z")
    rec["errors"] = [voce_base(keyword="k" * 40).to_dict()]
    rec["errors_recorded"] = 1
    rec["checksum"] = checksum_record(rec)
    visti.add(V.passo_campi(rec))
    check("7.5 RECORD_FIELD_INVALID", V.passo_campi(rec) is WriteReasonCode.RECORD_FIELD_INVALID)

    blob, codice = V.passo_serializza({"x": float("nan")})
    visti.add(codice)
    check("7.5 SERIALIZATION_FAILED", codice is WriteReasonCode.SERIALIZATION_FAILED and blob is None)

    # A4 — RECORD_TOO_LARGE si raggiunge dal percorso reale, non su byte fabbricati.
    # Il test precedente chiamava `passo_dimensione(b"x" * (MAX + 1))`: non toccava ne'
    # `costruisci_record` ne' `canonical_json`, quindi non poteva testimoniare una
    # regola che parla di serializzazione. La dimostrazione completa e' in sezione_a4.
    radice_grande = os.path.join(tmp, "7-5-troppo-grande")
    ric = V.scrivi_record(_risultato_gonfio("\\"), ctx, radice_grande, adattatori())
    visti.add(ric.write_reason_code)
    check("7.5 RECORD_TOO_LARGE dal percorso reale, nessun file pubblicato",
          ric.write_reason_code is WriteReasonCode.RECORD_TOO_LARGE
          and ric.status is WriteStatus.WRITE_ERROR
          and conta_finali(radice_grande) == [] and _pending_in(radice_grande) == [],
          str(ric.write_reason_code))

    for guasto, atteso in (("apri", WriteReasonCode.TEMP_WRITE_FAILED),
                           ("scrivi", WriteReasonCode.TEMP_WRITE_FAILED),
                           ("fsync", WriteReasonCode.FLUSH_FSYNC_FAILED),
                           ("pubblica", WriteReasonCode.PUBLISH_FAILED)):
        ric = V.scrivi_record(risultato, ctx, radice,
                              adattatori(publisher=PubblicatoreGuasto(guasto)))
        visti.add(ric.write_reason_code)
        check("7.5 " + atteso.value + " (" + guasto + ")",
              ric.write_reason_code is atteso and ric.status is WriteStatus.WRITE_ERROR)

    fisso = "c" * 32
    r1 = V.scrivi_record(risultato, ctx, radice, adattatori(id_gen=id_fisso(fisso)))
    r2 = V.scrivi_record(risultato, ctx, radice, adattatori(id_gen=id_fisso(fisso)))
    visti.add(r2.write_reason_code)
    check("7.5 COLLISION_EXISTING_TARGET, mai sovrascritto",
          r1.status is WriteStatus.WRITTEN and r2.status is WriteStatus.COLLISION
          and r2.write_reason_code is WriteReasonCode.COLLISION_EXISTING_TARGET)

    visti.discard(None)
    check("7.5 tutti e 11 i WriteReasonCode esercitati", visti == set(WriteReasonCode),
          str(sorted(c.value for c in set(WriteReasonCode) - visti)))
    check("7.5 l'enumerazione WriteReasonCode ha esattamente 11 membri",
          len(WriteReasonCode) == 11, str(len(WriteReasonCode)))
    check("7.5 InvocationReasonCode ha 2 membri e ReasonCode 9",
          len(InvocationReasonCode) == 2 and len(ReasonCode) == 9)
    check("7.12 TestValidationTarget ha esattamente 9 membri", len(TestValidationTarget) == 9)


# ==========================================================================
# 7.6 — prova del $ref cross-file
# ==========================================================================


def sezione_7_6():
    e = V._esegui_shadow_core(carica("invalid/pain.missing-envelope.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori())
    voci = [v for v in e.validation_result.errors
            if v.schema_resource_id == CORE_SCHEMA_ID and v.schema_pointer == "/$defs/Envelope/required"]
    check("7.6 prova del $ref cross-file su pain.missing-envelope", len(voci) >= 1,
          str([(v.schema_resource_id, v.schema_pointer) for v in e.validation_result.errors][:3]))
    if voci:
        docs = documenti_schemi()
        try:
            richiesti = deref_puntatore(docs[CORE_SCHEMA_ID], voci[0].schema_pointer)
            ok = isinstance(richiesti, list) and voci[0].missing_property in richiesti
        except Exception:
            ok = False
        check("7.6 il puntatore dereferenzia contro la risorsa registrata nominata", ok)

    check("7.6 instance_pointer alla radice e' la stringa vuota",
          any(v.instance_pointer == "" for v in e.validation_result.errors))

    # RFC 6901: escaping esatto
    ptr, loc, esatta = V.costruisci_posizioni(["a~b", "c/d", 3], [True, True, True])
    check("7.6 escaping RFC 6901 ~0 e ~1", ptr == "/a~0b/c~1d/3" and loc == ptr and esatta is True)

    # RLE: segmento redatto
    ptr, loc, esatta = V.costruisci_posizioni(["payload", "chiave-dinamica"], [True, False])
    check("7.6 RLE con segmento redatto esattamente ~R",
          ptr is None and loc == "/payload/~R" and esatta is False)
    check("7.6 nessuna dereferenza di una RLE redatta",
          _rifiuta_deref(loc))


def _rifiuta_deref(loc):
    try:
        deref_puntatore({}, loc)
    except AssertionError:
        return True
    return False


# ==========================================================================
# 7.7 — sha256, ordinamento, bound, troncamento, timing
# ==========================================================================


def sezione_7_7(tmp):
    with open(os.path.join(SCHEMI, "pain.schema.json"), "rb") as fh:
        atteso = sha256_hex(fh.read())
    e = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori())
    check("7.7 schema_sha256 di una radice-artefatto",
          e.validation_result.schema_sha256 == atteso)

    with open(os.path.join(SCHEMI, CORE_SCHEMA_ID), "rb") as fh:
        atteso_core = sha256_hex(fh.read())
    e = V._valida_sottoschema_test(carica("defs/Source.ok.json"), contesto(schema_ref=CORE_SCHEMA_ID),
                                   target=TestValidationTarget.CORE_SOURCE, adattatori=adattatori())
    check("7.7 schema_sha256 di un target $defs == hash di core.schema.json",
          e.validation_result.schema_sha256 == atteso_core)

    e = V._esegui_shadow_core(carica("invalid/pain.required-multi.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori())
    richiesti = [v for v in e.validation_result.errors
                 if v.keyword == "required" and v.instance_location == "/payload/pains/0/icp"]
    check("7.7 due errori required allo stesso genitore distinguibili da missing_property",
          len(richiesti) >= 2 and len({v.missing_property for v in richiesti}) >= 2,
          str([v.missing_property for v in richiesti]))
    ordinate = sorted(e.validation_result.errors, key=lambda v: (
        v.schema_resource_id.encode(), v.schema_pointer.encode(),
        v.instance_location.encode(), v.keyword.encode(), (v.missing_property or "").encode()))
    check("7.7 ordinamento per la 5-upla", list(e.validation_result.errors) == ordinate)

    # troncamento con iteratore strumentato
    finti = [ErroreFinto(["properties", "campo", "type"], ["campo"], "type")
             for _ in range(MAX_ERRORS + 10)]
    it = IteratoreContato(finti)
    voci, scartate, troncato = V.raccogli_da_iteratore(it, SINT_ID, SINT, SINT_DOCS)
    check("7.7 troncamento: al massimo MAX_ERRORS + 1 prelievi",
          it.prelievi == MAX_ERRORS + 1, str(it.prelievi))
    check("7.7 troncamento: invarianti 6 e 7",
          troncato is True and len(voci) + scartate == MAX_ERRORS)
    check("7.7 nessun campo asserisce un totale vero",
          "errors_total" not in risultato_to_dict(
              V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                                    config=V.ConfigTest(attivo=True, radice_output=None),
                                    adattatori=adattatori()).validation_result))

    it2 = IteratoreContato(finti[:MAX_ERRORS])
    voci2, _, troncato2 = V.raccogli_da_iteratore(it2, SINT_ID, SINT, SINT_DOCS)
    check("7.7 esattamente MAX_ERRORS errori non attivano il troncamento",
          troncato2 is False and len(voci2) == MAX_ERRORS and it2.prelievi == MAX_ERRORS)

    # scarto per oversize, un caso discriminante per campo
    casi = {
        "instance_pointer": voce_base(instance_pointer="/" + "p" * MAX_LOCATION_BYTES),
        "instance_location": voce_base(instance_pointer=None, location_is_exact=False,
                                       instance_location="/" + "l" * MAX_LOCATION_BYTES),
        "schema_pointer": voce_base(schema_pointer="/" + "s" * MAX_LOCATION_BYTES),
        "schema_resource_id": voce_base(schema_resource_id="r" * 49 + ".schema.json"),
        "keyword": voce_base(keyword="k" * (MAX_KEYWORD_BYTES + 1)),
        "missing_property": voce_base(keyword="required",
                                      missing_property="m" * (MAX_MISSING_PROPERTY_BYTES + 1)),
    }
    for campo, voce in casi.items():
        check("7.7 scarto per oversize su " + campo, V.voce_conforme(voce) is False)
    check("7.7 la voce di riferimento e' conforme", V.voce_conforme(voce_base()) is True)

    lungo = ErroreFinto(["properties", NOME_LUNGO, "type"], [NOME_LUNGO], "type")
    voci3, scartate3, _ = V.raccogli_da_iteratore(
        IteratoreContato([lungo, ErroreFinto(["properties", "campo", "type"], ["campo"], "type")]),
        SINT_ID, SINT, SINT_DOCS)
    check("7.7 la voce oversize e' scartata intera, nulla e' troncato sul posto",
          scartate3 == 1 and len(voci3) == 1 and voci3[0].instance_location == "/campo")

    check("7.7 MAX_ERRORS non sovrascrivibile dal chiamante",
          all("max" not in p.lower() and "limit" not in p.lower()
              for f in (V.esegui_shadow_produzione, V._esegui_shadow_core,
                        V._valida_sottoschema_test)
              for p in inspect.signature(f).parameters))

    # timing
    for ms in ((1000, 900), (1000, 1000 + 2 ** 63), (1000, None)):
        orologio = OrologioFisso(ms=[m for m in ms if m is not None] or [1000])
        if ms[1] is None:
            orologio = _OrologioSenzaMonotonico()
        e = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                                  config=V.ConfigTest(attivo=True, radice_output=None),
                                  adattatori=adattatori(clock=orologio))
        check("7.7 durata non conforme -> duration_ms None, verdetto invariato",
              e.validation_result.duration_ms is None
              and e.validation_result.full_outcome is FullOutcome.FULL_PASS)

    radice = os.path.join(tmp, "durata-nulla")
    e = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori(clock=_OrologioSenzaMonotonico()))
    check("7.7 il writer accetta duration_ms nullo come conforme",
          e.write_receipt.status is WriteStatus.WRITTEN
          and e.validation_result.duration_ms is None)
    sorgente_capacita = open(os.path.join(QUI, "ds_validate.py"), "rb").read().decode("utf-8")
    check("7.7 nessun tetto di un'ora da nessuna parte",
          "3600000" not in sorgente_capacita and "os.replace(" not in sorgente_capacita)


class _OrologioSenzaMonotonico(V.Orologio):
    def monotonico_ms(self):
        raise RuntimeError("orologio monotonico non disponibile")


# ==========================================================================
# 7.8 — fingerprint del registro
# ==========================================================================


def sezione_7_8(tmp):
    byte = {}
    for n in sorted(os.listdir(SCHEMI)):
        if n.endswith(".json"):
            with open(os.path.join(SCHEMI, n), "rb") as fh:
                byte[n] = fh.read()
    fp = V.calcola_fingerprint_da_byte(byte)
    e = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori())
    check("7.8 fingerprint calcolato per §4.9", e.validation_result.registry_fingerprint == fp)

    invertito = dict(reversed(list(byte.items())))
    check("7.8 invarianza rispetto all'ordine di caricamento",
          V.calcola_fingerprint_da_byte(invertito) == fp)

    modificato = dict(byte)
    modificato[CORE_SCHEMA_ID] = modificato[CORE_SCHEMA_ID] + b"\n"
    check("7.8 sensibilita' a una modifica del solo core.schema.json",
          V.calcola_fingerprint_da_byte(modificato) != fp)

    sotto = os.path.join(tmp, "fp-collisione")
    os.makedirs(sotto, exist_ok=True)
    radice = radice_schemi_temporanea(sotto, aggiunte={
        "collide-a.schema.json": byte_fixture("schema-broken/collide-a.schema.json"),
        "collide-b.schema.json": byte_fixture("schema-broken/collide-b.schema.json")})
    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(radice))
    check("7.8 fail-closed su identita' duplicata",
          e.validation_result.reason_code is ReasonCode.SCHEMA_ID_COLLISION
          and e.validation_result.registry_fingerprint is None)

    sotto = os.path.join(tmp, "fp-basename")
    os.makedirs(sotto, exist_ok=True)
    radice = radice_schemi_temporanea(sotto, aggiunte={
        "id-basename-mismatch.schema.json": byte_fixture("schema-broken/id-basename-mismatch.schema.json")})
    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(radice))
    check("7.8 fail-closed su $id diverso dal basename",
          e.validation_result.reason_code is ReasonCode.SCHEMA_ID_COLLISION)


# ==========================================================================
# 7.9 / 7.10 — matrice di emissione, record, pubblicazione
# ==========================================================================


def conta_finali(radice):
    if not os.path.isdir(radice):
        return []
    return [n for n in sorted(os.listdir(radice)) if n.endswith(".json")]


def sezione_7_9_7_10(tmp):
    ctx = contesto(schema_ref=CORE_SCHEMA_ID)

    radice = os.path.join(tmp, "emis-invocazione")
    e = V._esegui_shadow_core({}, contesto(role="ruolo/invalido"),
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori())
    check("7.9 INVOCATION_ERROR non scrive nulla",
          e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE
          and conta_finali(radice) == [] and not os.path.isdir(radice))

    radice = os.path.join(tmp, "emis-off")
    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo=False, radice_output=radice),
                              adattatori=adattatori())
    check("7.9 OFF: nessun record, nessuna directory, nessun import",
          e.write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE
          and not os.path.isdir(radice))

    casi = {
        "SCHEMA_NOT_MAPPED": (carica("valid/pain.ok.json"), contesto(schema_ref=CORE_SCHEMA_ID), SCHEMI),
        "FULL_PASS": (carica("valid/pain.ok.json"), contesto(), SCHEMI),
        "FULL_FAIL": (carica("invalid/pain.enum.json"), contesto(), SCHEMI),
        "VALIDATOR_ERROR": (None, None, None),
    }
    for nome, (istanza, ctx_caso, schemi) in casi.items():
        if nome == "VALIDATOR_ERROR":
            sotto = os.path.join(tmp, "emis-ve")
            os.makedirs(sotto, exist_ok=True)
            schemi = radice_schemi_temporanea(
                sotto, sostituzioni={"pain.schema.json": byte_fixture("schema-broken/ref-external.schema.json")})
            istanza, ctx_caso = {}, contesto()
        radice = os.path.join(tmp, "emis-" + nome)
        e = V._esegui_shadow_core(istanza, ctx_caso,
                                  config=V.ConfigTest(attivo=True, radice_output=radice),
                                  adattatori=adattatori(schemi))
        check("7.9 " + nome + " con radice legata: esattamente una scrittura",
              e.write_receipt.status is WriteStatus.WRITTEN and len(conta_finali(radice)) == 1)
        e2 = V._esegui_shadow_core(istanza, ctx_caso,
                                   config=V.ConfigTest(attivo=True, radice_output=None),
                                   adattatori=adattatori(schemi))
        check("7.9 " + nome + " senza radice: OUTPUT_ROOT_NOT_CONFIGURED, nessun record",
              e2.write_receipt.status is WriteStatus.NOT_ATTEMPTED
              and e2.write_receipt.write_reason_code is WriteReasonCode.OUTPUT_ROOT_NOT_CONFIGURED
              and e2.write_receipt.record_id is None and e2.write_receipt.final_name is None)

    radice = os.path.join(tmp, "emis-guasto")
    e = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori(publisher=PubblicatoreGuasto("pubblica")))
    check("7.9 un guasto in scrittura non cambia full_outcome e non solleva nulla",
          e.validation_result.full_outcome is FullOutcome.FULL_PASS
          and e.write_receipt.status is WriteStatus.WRITE_ERROR)

    # record: grammatica, checksum, identita'
    radice = os.path.join(tmp, "record")
    e = V._esegui_shadow_core(carica("invalid/pain.enum.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori())
    nome = e.write_receipt.final_name
    with open(os.path.join(radice, nome), "rb") as fh:
        rec = json.loads(fh.read().decode("utf-8"))
    check("7.10 record conforme al contratto", valida_contratto_record(rec) == [],
          str(valida_contratto_record(rec)))
    check("7.10 timestamp_utc di esattamente 27 caratteri", len(rec["timestamp_utc"]) == 27)
    check("7.10 checksum calcolato senza la chiave checksum e riverificabile",
          checksum_record(rec) == rec["checksum"])
    mutato = dict(rec)
    mutato["role"] = "altro"
    check("7.10 un byte mutato viene rilevato", checksum_record(mutato) != mutato["checksum"])
    import re as _re
    check("7.10 grammatica dei nomi finali",
          _re.match(r"^[0-9a-f]{32}\.json$", nome) is not None
          and rec["run_id"] not in nome and rec["role"] not in nome and rec["stage"] not in nome)
    check("7.10 id di produzione da secrets.token_hex(16)",
          _re.match(r"^[0-9a-f]{32}$", V._genera_id_produzione()) is not None)
    check("7.10 ricevuta WRITTEN conforme alla tabella §4.11",
          e.write_receipt.write_reason_code is None
          and e.write_receipt.record_id == rec["record_id"]
          and e.write_receipt.final_name == nome)
    check("7.10 grammatica dei nomi pending distinta da quella finale",
          _re.match(r"^[0-9a-f]{32}\.[0-9a-f]{8}\.pending$", "d" * 32 + "." + "e" * 8 + ".pending")
          is not None
          and _re.match(r"^[0-9a-f]{32}\.json$", "d" * 32 + "." + "e" * 8 + ".pending") is None)


# ==========================================================================
# 7.10 — prova multiprocesso spawn
# ==========================================================================


def sezione_concorrenza(tmp):
    ctx = multiprocessing.get_context("spawn")
    radice = os.path.join(tmp, "conc-distinti")
    os.makedirs(radice, exist_ok=True)
    ids = ["%032x" % (0x1000 + i) for i in range(6)]
    with ctx.Pool(3) as pool:
        esiti = pool.starmap(worker_scrives_wrapper, [(radice, i, SCHEMI) for i in ids])
    finali = conta_finali(radice)
    integri = True
    for n in finali:
        with open(os.path.join(radice, n), "rb") as fh:
            try:
                rec = json.loads(fh.read().decode("utf-8"))
            except Exception:
                integri = False
                continue
        if valida_contratto_record(rec):
            integri = False
    check("7.10 spawn: id distinti, tutti i record integri",
          len(finali) == len(ids) and integri and all(x.startswith("WRITTEN") for x in esiti),
          str(esiti))
    check("7.10 spawn: nessun JSON parziale mai visibile sotto la grammatica finale", integri)

    radice = os.path.join(tmp, "conc-collisione")
    os.makedirs(radice, exist_ok=True)
    stesso = "f" * 32
    with ctx.Pool(4) as pool:
        esiti = pool.starmap(worker_scrives_wrapper, [(radice, stesso, SCHEMI)] * 4)
    vincitori = [x for x in esiti if x.startswith("WRITTEN")]
    perdenti = [x for x in esiti if x.startswith("COLLISION|COLLISION_EXISTING_TARGET")]
    check("7.10 spawn: collisione su stesso id -> esattamente un vincitore",
          len(vincitori) == 1 and len(perdenti) == 3 and conta_finali(radice) == [stesso + ".json"],
          str(esiti))

    radice = os.path.join(tmp, "conc-guasto")
    os.makedirs(radice, exist_ok=True)
    guasto_id = "e" * 32
    p = ctx.Process(target=worker_guasto_prima_di_pubblicare, args=(radice, guasto_id, SCHEMI))
    p.start()
    p.join()
    pending = [n for n in os.listdir(radice) if n.endswith(".pending")]
    check("7.10 spawn: guasto prima della pubblicazione -> nessun record finale",
          conta_finali(radice) == [] and len(pending) == 1)
    check("7.10 primitiva atomica: os.link senza sovrascrittura, os.replace assente",
          "os.link(" in inspect.getsource(V.Pubblicatore.pubblica)
          and "os.replace(" not in inspect.getsource(V))


def worker_scrives_wrapper(radice, record_id, schemi):
    return worker_scrive(radice, record_id, schemi)


# ==========================================================================
# 7.11 — aggregatore
# ==========================================================================


class ApriContato:
    def __init__(self):
        self.byte_letti = 0
        self.aperture = 0  # A2 §5: quante volte l'aggregatore apre davvero un file

    def __call__(self, percorso):
        contatore = self
        contatore.aperture += 1

        class _F:
            def __init__(self):
                self._fh = open(percorso, "rb")

            def read(self, n=-1):
                dati = self._fh.read(n)
                contatore.byte_letti += len(dati)
                return dati

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self._fh.close()
                return False

        return _F()


def sezione_7_11(tmp):
    radice = os.path.join(tmp, "agg")
    os.makedirs(radice, exist_ok=True)
    ctx = contesto(schema_ref=CORE_SCHEMA_ID)
    risultato = _risultato_per_scrittura(SCHEMI)

    for i in range(3):
        V.scrivi_record(risultato, contesto(invocation_id="inv-%04d" % i, schema_ref=CORE_SCHEMA_ID),
                        radice, adattatori(id_gen=id_fisso("%032x" % (0xA00 + i))))
    # duplicato della stessa (invocation_id, attempt)
    V.scrivi_record(risultato, contesto(invocation_id="inv-0000", schema_ref=CORE_SCHEMA_ID),
                    radice, adattatori(id_gen=id_fisso("%032x" % 0xB00)))
    # file pending: non deve mai essere aperto
    with open(os.path.join(radice, "0" * 32 + "." + "1" * 8 + ".pending"), "wb") as fh:
        fh.write(b"{ non json")

    rapporto = R.aggrega(radice, quiescent=True)
    check("7.11 quiescenza dichiarata dal chiamante",
          rapporto.completeness is Completeness.COMPLETE
          and R.aggrega(radice, quiescent=False).completeness is Completeness.UNKNOWN)
    check("7.11 i file pending non sono mai candidati", rapporto.records_discovered == 4)
    check("7.11 deduplicazione: vince il record_id lessicograficamente minore",
          rapporto.duplicate_groups == 1 and rapporto.duplicate_extra_records == 1
          and rapporto.records_counted == 3)
    check("7.11 le cinque invarianti dell'aggregato", R.invarianti_aggregato(rapporto) == [],
          str(R.invarianti_aggregato(rapporto)))
    check("7.11 stdout canonico con tutte le chiavi enum azzerate",
          len(rapporto.by_full_outcome) == len(FullOutcome)
          and len(rapporto.by_reason_code) == len(ReasonCode)
          and len(rapporto.by_comparison) == len(Comparison)
          and rapporto.denominator_label == "observed record set")
    check("7.11 due aggregazioni sullo stesso insieme sono byte-identiche",
          R.rendi_stdout(rapporto) == R.rendi_stdout(R.aggrega(radice, quiescent=True)))

    # file finale sovradimensionato -> corrotto, il resto non viene mai caricato
    radice2 = os.path.join(tmp, "agg-oversize")
    os.makedirs(radice2, exist_ok=True)
    grande = "9" * 32 + ".json"
    with open(os.path.join(radice2, grande), "wb") as fh:
        fh.write(b"[" + b"0," * (MAX_RECORD_BYTES) + b"0]")
    apri = ApriContato()
    rapporto2 = R._aggrega_con_lettore(radice2, quiescent=True, apri=apri)
    check("7.11 file finale sovradimensionato classificato corrotto",
          rapporto2.records_corrupt == 1 and rapporto2.records_counted == 0)
    check("7.11 lettura limitata a MAX_RECORD_BYTES + 1 byte",
          apri.byte_letti <= MAX_RECORD_BYTES + 1, str(apri.byte_letti))

    # record valido rinominato -> corrotto
    radice3 = os.path.join(tmp, "agg-rinominato")
    os.makedirs(radice3, exist_ok=True)
    V.scrivi_record(risultato, ctx, radice3, adattatori(id_gen=id_fisso("%032x" % 0xC00)))
    vecchio = conta_finali(radice3)[0]
    os.rename(os.path.join(radice3, vecchio), os.path.join(radice3, "d" * 32 + ".json"))
    rapporto3 = R.aggrega(radice3, quiescent=True)
    check("7.11 record valido rinominato -> corrotto, non contribuisce a nessun bucket",
          rapporto3.records_corrupt == 1 and rapporto3.records_counted == 0
          and sum(rapporto3.by_full_outcome.values()) == 0)

    # versione non supportata -> corrotto, non errore
    radice4 = os.path.join(tmp, "agg-versione")
    os.makedirs(radice4, exist_ok=True)
    rec = V.costruisci_record(risultato, ctx, "a" * 32, "2026-08-08T12:00:00.000000Z")
    rec["record_version"] = "9.9.9"
    rec["checksum"] = checksum_record(rec)
    with open(os.path.join(radice4, "a" * 32 + ".json"), "wb") as fh:
        fh.write(canonical_json(rec).encode("utf-8"))
    rapporto4 = R.aggrega(radice4, quiescent=True)
    check("7.11 record_version non supportata -> corrotto, non errore",
          rapporto4.records_corrupt == 1 and R.invarianti_aggregato(rapporto4) == [])

    uscita = R.rendi_stdout(rapporto)
    check("7.11 nessun contenuto grezzo di record nello stdout dell'aggregatore",
          "inv-0000" not in uscita and "pain-finder" not in uscita
          and "pipeline" not in uscita.lower())
    return uscita


# ==========================================================================
# 7.13 — privacy, canary, contenimento
# ==========================================================================


def sezione_7_13(tmp, testi_da_scansionare):
    ambiente = carica("canary/environment.canary.json")
    autorizzati = ambiente["metadati_autorizzati"]
    segreti = list(ambiente["segreti"].values())
    rifiutato = ambiente["metadati_rifiutati"]["invocation_id"]

    radice = os.path.join(tmp, "canary")
    ctx = ValidationContext(
        invocation_id=autorizzati["invocation_id"], attempt=1,
        run_id=autorizzati["run_id"], role=autorizzati["role"], stage=autorizzati["stage"],
        artifact_id=ARTEFATTO, schema_ref="pain.schema.json",
        legacy_outcome=LegacyOutcome.ACCEPTED)
    e = V._esegui_shadow_core(carica("canary/instance.canary.json"), ctx,
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori())
    with open(os.path.join(radice, e.write_receipt.final_name), "rb") as fh:
        testo_record = fh.read().decode("utf-8")
    rec = json.loads(testo_record)
    check("7.13 i metadati autorizzati fanno round-trip byte per byte",
          rec["invocation_id"] == autorizzati["invocation_id"]
          and rec["run_id"] == autorizzati["run_id"]
          and rec["role"] == autorizzati["role"] and rec["stage"] == autorizzati["stage"])
    import re as _re2
    check("7.13 artifact_id opaco HEX64, nessun percorso persistito",
          rec["artifact_id"] == ARTEFATTO
          and _re2.search(r"[A-Za-z]:[\\\\/]", testo_record) is None
          and "\\\\\\\\" not in testo_record
          and tmp not in testo_record and RADICE_REPO not in testo_record)

    radice_rif = os.path.join(tmp, "canary-rifiutato")
    e2 = V._esegui_shadow_core({}, contesto(invocation_id=rifiutato),
                               config=V.ConfigTest(attivo=True, radice_output=radice_rif),
                               adattatori=adattatori())
    check("7.13 i metadati rifiutati non producono alcun record",
          e2.invocation_status is InvocationStatus.INVOCATION_ERROR
          and not os.path.isdir(radice_rif))

    tutti = "\n".join(testi_da_scansionare + [testo_record])
    perdite = [s for s in segreti + [rifiutato] if s in tutti]
    check("7.13 nessun canary di segretezza compare in record, report o stdout",
          perdite == [], str(len(perdite)))
    check("7.13 nessun percorso assoluto compare nei record",
          tmp not in tutti and RADICE_REPO not in tutti)

    # contenimento tramite il risolutore di percorso iniettato
    def risolutore_che_esce(radice_fidata, nome):
        raise V.ContenimentoViolato("uscita simulata dalla radice fidata")

    e3 = V._esegui_shadow_core({}, contesto(),
                               config=V.ConfigTest(attivo=True, radice_output=None),
                               adattatori=adattatori(risolutore=risolutore_che_esce))
    check("7.13 rifiuto di contenimento tramite il risolutore iniettato",
          e3.validation_result.full_outcome is FullOutcome.VALIDATOR_ERROR
          and e3.validation_result.reason_code is ReasonCode.SCHEMA_UNREADABLE)
    for cattivo in ("../fuori.json", "sub/dir.json", "C:altro.json", "\\\\srv\\s.json"):
        try:
            V.risolvi_contenuto(SCHEMI, cattivo)
            respinto = False
        except V.ContenimentoViolato:
            respinto = True
        check("7.13 il risolutore predefinito respinge " + cattivo.replace("\\", "|"), respinto)
    return testo_record


# ==========================================================================
# 7.14 — contratto del manifest
# ==========================================================================

CAMPI_PER_KIND = {
    "validation_instance": ({"path", "kind", "target_ref", "expected_full_outcome"},
                            {"expected_reason_code"}),
    "subschema_instance": ({"path", "kind", "target_ref", "expected_full_outcome"},
                           {"expected_reason_code"}),
    "schema_fixture": ({"path", "kind", "expected_full_outcome", "expected_reason_code"}, set()),
    "test_data": ({"path", "kind", "usage"}, set()),
}


def valida_manifest(manifesto) -> list:
    problemi = []
    percorsi = [v["path"] for v in manifesto["entries"]]
    if len(percorsi) != len(set(percorsi)):
        problemi.append("percorsi duplicati")
    for voce in manifesto["entries"]:
        kind = voce.get("kind")
        if kind not in CAMPI_PER_KIND:
            problemi.append("kind fuori dall'insieme chiuso: " + voce["path"])
            continue
        obbligatori, opzionali = CAMPI_PER_KIND[kind]
        chiavi = set(voce)
        if not obbligatori <= chiavi:
            problemi.append("campi obbligatori mancanti: " + voce["path"])
        vietati = chiavi - obbligatori - opzionali
        if vietati:
            problemi.append("campi vietati per il kind: " + voce["path"])
        if kind == "validation_instance" and voce["target_ref"] not in ARTIFACT_ROOTS:
            problemi.append("target_ref non e' una radice-artefatto: " + voce["path"])
        if kind == "subschema_instance" and voce["target_ref"] not in TestValidationTarget.__members__:
            problemi.append("target_ref non e' un TestValidationTarget: " + voce["path"])
        if kind == "schema_fixture" and voce["expected_full_outcome"] != "VALIDATOR_ERROR":
            problemi.append("schema_fixture senza VALIDATOR_ERROR: " + voce["path"])
        if kind == "test_data" and (not isinstance(voce.get("usage"), str)
                                    or "\n" in voce["usage"] or len(voce["usage"]) > 300):
            problemi.append("usage non e' una riga limitata: " + voce["path"])
        esito = voce.get("expected_full_outcome")
        if kind in ("validation_instance", "subschema_instance"):
            serve = esito in ("VALIDATOR_ERROR", "NOT_EVALUATED")
            if serve and "expected_reason_code" not in voce:
                problemi.append("expected_reason_code mancante: " + voce["path"])
            if not serve and "expected_reason_code" in voce:
                problemi.append("expected_reason_code non ammesso: " + voce["path"])
    return problemi


def sezione_7_14():
    manifesto = carica("manifest.json")
    percorsi = sorted(v["path"] for v in manifesto["entries"])
    su_disco = []
    for cartella in ("valid", "invalid", "defs", "schema-broken", "canary"):
        for n in sorted(os.listdir(os.path.join(FIXTURE, cartella))):
            su_disco.append(cartella + "/" + n)
    # A3 — l'allowlist emendata: 52 fixture, non piu' 50, e poi 61 con le nove aggiunte
    # dalla micro-slice di coerenza dei contratti M0.2. Le due aggiunte del 50->52 sono i
    # negativi di `evidence-pack`, senza i quali P0-4 non era distinguibile: uno schema
    # con la sola fixture VALIDA che passa non sa dire se e' stato "validato bene"
    # oppure "non validato affatto".
    check("7.14 il manifest contiene esattamente i 61 percorsi allowlistati",
          len(percorsi) == 61 and percorsi == sorted(su_disco),
          str(set(percorsi) ^ set(su_disco)))
    check("7.14 contratto del manifest rispettato", valida_manifest(manifesto) == [],
          str(valida_manifest(manifesto)))
    from collections import Counter
    conteggi = Counter(v["kind"] for v in manifesto["entries"])
    check("7.14 totali per kind 26/26/8/1",
          conteggi == {"validation_instance": 26, "subschema_instance": 26,
                       "schema_fixture": 8, "test_data": 1}, str(dict(conteggi)))
    per_cartella = Counter(v["path"].split("/")[0] for v in manifesto["entries"])
    check("7.14 conteggi per directory: valid 13, invalid 12, defs 26, schema-broken 8, canary 2",
          per_cartella == {"valid": 13, "invalid": 12, "defs": 26,
                           "schema-broken": 8, "canary": 2}, str(dict(per_cartella)))

    # A3 — il totale autorizzato e' un LETTERALE, non una deduzione: 5 file di codice
    # + 1 manifest + 61 fixture = 67. Ogni differenza va spiegata, non assorbita.
    codice = sorted(n for n in os.listdir(QUI)
                    if n.startswith("ds_validate") and n.endswith(".py"))
    atteso_codice = ["ds_validate.py", "ds_validate_concurrency.py", "ds_validate_contract.py",
                     "ds_validate_report.py", "ds_validate_selftest.py"]
    check("7.14 i file di codice autorizzati sono esattamente 5", codice == atteso_codice,
          str(set(codice) ^ set(atteso_codice)))
    check("7.14 l'allowlist totale e' esattamente 67 file",
          len(codice) + 1 + len(percorsi) == 67,
          str(len(codice)) + "+1+" + str(len(percorsi)))
    ambiente = [v for v in manifesto["entries"]
                if v["path"] == "canary/environment.canary.json"][0]
    check("7.14 environment.canary.json e' test_data senza target ne' esiti",
          ambiente["kind"] == "test_data" and "target_ref" not in ambiente
          and "expected_full_outcome" not in ambiente
          and "expected_reason_code" not in ambiente)
    guasto = dict(ambiente)
    guasto["target_ref"] = "pain.schema.json"
    check("7.14 il controllo rifiuta un campo vietato per il kind",
          valida_manifest({"entries": [guasto]}) != [])


# ==========================================================================
# 7.12 — libreria di definizioni
# ==========================================================================


def sezione_7_12():
    docs = documenti_schemi()
    core = docs[CORE_SCHEMA_ID]
    check("7.12 core.schema.json e' una libreria di definizioni senza vincoli di radice",
          set(core) == {"$schema", "$id", "title", "description", "$defs"}
          and len(core["$defs"]) == 9)
    import jsonschema
    try:
        jsonschema.Draft202012Validator.check_schema(core)
        meta = True
    except Exception:
        meta = False
    check("7.12 core.schema.json meta-validato contro Draft 2020-12", meta)
    check("7.12 core non e' una radice-artefatto: nessun FULL_PASS vacuo possibile",
          CORE_SCHEMA_ID not in ARTIFACT_ROOTS)
    e = V._esegui_shadow_core({}, contesto(schema_ref=CORE_SCHEMA_ID),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori())
    check("7.12 un {} contro la radice di core non e' mai contato come copertura",
          e.validation_result.full_outcome is FullOutcome.NOT_EVALUATED
          and e.validation_result.reason_code is ReasonCode.SCHEMA_NOT_MAPPED)
    coperti = {TestValidationTarget[v["target_ref"]] for v in carica("manifest.json")["entries"]
               if v["kind"] == "subschema_instance"}
    check("7.12 tutti e nove i $defs ricevono test discriminanti diretti",
          coperti == set(TestValidationTarget))
    e = V._valida_sottoschema_test(carica("defs/UnreachableSource.bad.json"),
                                   contesto(schema_ref=CORE_SCHEMA_ID),
                                   target=TestValidationTarget.CORE_UNREACHABLE_SOURCE,
                                   adattatori=adattatori())
    check("7.12 IC-4: il ramo if/then/else sbagliato fallisce",
          e.validation_result.full_outcome is FullOutcome.FULL_FAIL
          and any("/allOf/0/else" in v.schema_pointer for v in e.validation_result.errors),
          str([v.schema_pointer for v in e.validation_result.errors]))
    e = V._valida_sottoschema_test(carica("defs/Source.format-invalid.ok.json"),
                                   contesto(schema_ref=CORE_SCHEMA_ID),
                                   target=TestValidationTarget.CORE_SOURCE,
                                   adattatori=adattatori())
    check("7.12 format e' solo annotazione: nessun FormatChecker installato",
          e.validation_result.full_outcome is FullOutcome.FULL_PASS)
    sorgente = open(os.path.join(QUI, "ds_validate.py"), "rb").read().decode("utf-8")
    check("7.12 nessun FormatChecker nel codice della capacita'",
          "FormatChecker" not in sorgente and "format_checker" not in sorgente)


# ==========================================================================
# 7.15 — non-regressione del repository
# ==========================================================================


def sezione_7_15():
    with open(os.path.join(QUI, "ds_call.py"), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    check("7.15 zero chiamate di rete registrate dalla guardia", CHIAMATE_RETE["n"] == 0,
          str(CHIAMATE_RETE["n"]))
    return digest


# ==========================================================================
# A1 rev4 — confine delle eccezioni, tipi esatti, igiene dei caratteri
#
# Criteri di accettazione A1 rev4 §6. Ogni item dichiara dove si applica, perche' le
# tre superfici di §2.0 non raggiungono la stessa macchina: `_valida_sottoschema_test`
# non persiste alcun record, e la produzione si ferma al passo 3 con SHADOW OFF.
# ==========================================================================

# Cio' che `json.loads(r'"\ud800"')` restituisce: un surrogato spaiato dentro un `str`
# perfettamente ordinario. Faceva alzare UnicodeEncodeError dalla superficie pubblica.
SURROGATO = "\ud800"
CTRL = "\x01"
NOME_CTRL = "pa" + CTRL + "in"
NOME_BACKSLASH = "a\\b"
BV = "bootstrap-verdict.schema.json"


class _GetattrOstile:
    def __getattr__(self, nome):
        raise RuntimeError("attributo ostile")


class _ProprietaOstile:
    @property
    def invocation_id(self):
        raise RuntimeError("proprieta' ostile")


class _ClasseOstile:
    @property
    def __class__(self):
        raise RuntimeError("__class__ ostile")


class _StrRotta(str):
    def encode(self, *a, **k):
        raise RuntimeError("encode rotto")


class _IntRotto(int):
    def __ge__(self, altro):
        raise RuntimeError("__ge__ rotto")


class _SottoContesto(ValidationContext):
    """Una sottoclasse puo' ridefinire __getattribute__: per questo e' rifiutata."""


def _superfici(istanza, ctx, ad):
    """Le tre superfici callable di A1 rev4 §2.0, invocate senza far uscire eccezioni.

    Restituisce [(nome, esecuzione | None, eccezione | None)].
    """
    fatti = []
    for nome, chiamata in (
        ("pubblica", lambda: V.esegui_shadow_produzione(istanza, ctx)),
        ("core", lambda: V._esegui_shadow_core(
            istanza, ctx, config=V.ConfigTest(attivo=True, radice_output=None), adattatori=ad)),
        ("sottoschema", lambda: V._valida_sottoschema_test(
            istanza, ctx, target=TestValidationTarget.CORE_SOURCE, adattatori=ad)),
    ):
        try:
            fatti.append((nome, chiamata(), None))
        except BaseException as exc:  # noqa: BLE001 - e' il punto del test
            fatti.append((nome, None, type(exc).__name__))
    return fatti


def _tutte_context_invalid(fatti, campo=None):
    for _nome, e, exc in fatti:
        if exc is not None or e is None:
            return False
        if e.invocation_status is not InvocationStatus.INVOCATION_ERROR:
            return False
        if e.invocation_error.reason_code is not InvocationReasonCode.CONTEXT_INVALID:
            return False
        if e.invocation_error.field not in INVOCATION_ERROR_FIELDS:
            return False
        if campo is not None and e.invocation_error.field != campo:
            return False
        if e.validation_result is not None:
            return False
    return True


def _schema_caratteri(required_ctrl=False) -> bytes:
    """Uno schema fidato che DICHIARA nomi non rappresentabili.

    Serve per raggiungere §2.3 e §4.2: con i 13 schemi reali nessun nome dichiarato
    porta un carattere di controllo, quindi il ramo non e' esercitabile dal corpus.
    `bootstrap-verdict.schema.json` e' scelto perche' nessun altro schema lo referenzia:
    sostituirlo non incrina la chiusura del registro.
    """
    doc = {
        "$id": BV,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            NOME_CTRL: {"type": "string"},
            NOME_BACKSLASH: {"type": "string"},
        },
    }
    if required_ctrl:
        doc["required"] = [NOME_CTRL]
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


class _PubblicatoreInterrotto(V.Pubblicatore):
    def pubblica(self, pending, finale):
        raise KeyboardInterrupt()


def sezione_a1(tmp):
    ad = adattatori()

    # --- item 1: un surrogato spaiato in ciascuno degli 8 campi -------------
    for campo in ("invocation_id", "run_id", "role", "stage", "artifact_id",
                  "schema_ref", "attempt", "legacy_outcome"):
        fatti = _superfici({}, contesto(**{campo: SURROGATO}), ad)
        senza_eccezioni = all(exc is None for _n, _e, exc in fatti)
        check("A1.1 surrogato in " + campo + " -> CONTEXT_INVALID sulle tre superfici",
              senza_eccezioni and _tutte_context_invalid(fatti, campo),
              ";".join(n + ":" + str(x) for n, _e, x in fatti if x))

    # --- item 2: la famiglia dei contesti ostili ---------------------------
    ostili = {
        "getattr_che_solleva": _GetattrOstile(),
        "proprieta_che_solleva": _ProprietaOstile(),
        "classe_che_solleva": _ClasseOstile(),
    }
    for nome, oggetto in ostili.items():
        fatti = _superfici({}, oggetto, ad)
        check("A1.2 " + nome + " -> CONTEXT_INVALID, zero eccezioni",
              all(exc is None for _n, _e, exc in fatti) and _tutte_context_invalid(fatti),
              ";".join(n + ":" + str(x) for n, _e, x in fatti if x))

    fatti = _superfici({}, contesto(invocation_id=_StrRotta("inv-0001")), ad)
    check("A1.2 sottoclasse di str con encode rotto -> CONTEXT_INVALID sul tipo",
          all(exc is None for _n, _e, exc in fatti)
          and _tutte_context_invalid(fatti, "invocation_id"))

    fatti = _superfici({}, contesto(attempt=_IntRotto(1)), ad)
    check("A1.2 sottoclasse di int con __ge__ rotto -> CONTEXT_INVALID sul tipo",
          all(exc is None for _n, _e, exc in fatti)
          and _tutte_context_invalid(fatti, "attempt"))

    # --- item 3: la sottoclasse e' rifiutata, l'identico valore no ---------
    valori = dict(invocation_id="inv-0001", attempt=1, run_id="20260808-1200",
                  role="pain-finder", stage="pain", artifact_id=ARTEFATTO,
                  schema_ref="pain.schema.json", legacy_outcome=LegacyOutcome.ACCEPTED)
    fatti = _superfici({}, _SottoContesto(**valori), ad)
    check("A1.3 sottoclasse di ValidationContext -> CONTEXT_INVALID",
          all(exc is None for _n, _e, exc in fatti) and _tutte_context_invalid(fatti))

    fatti = _superfici({}, ValidationContext(**valori), ad)
    check("A1.3 ValidationContext con gli stessi valori -> accettato",
          all(exc is None for _n, _e, exc in fatti)
          and all(e.invocation_status is InvocationStatus.OK for _n, e, _x in fatti),
          "il rifiuto e' per tipo, non per valore")

    # --- item 4: attempt=True rifiutato sul tipo ---------------------------
    fatti = _superfici({}, contesto(attempt=True), ad)
    check("A1.4 attempt=True -> CONTEXT_INVALID sul tipo (bool e' un int)",
          all(exc is None for _n, _e, exc in fatti)
          and _tutte_context_invalid(fatti, "attempt"))

    # --- item 5: KeyboardInterrupt propaga; il pending non e' contato ------
    # Solo sulle superfici che attraversano la finestra di scrittura: la produzione
    # si ferma al passo 3 con SHADOW OFF, e §4.14 non persiste mai.
    radice = os.path.join(tmp, "a1-interrupt")
    risultato = _risultato_per_scrittura(SCHEMI)
    propagata = None
    try:
        V.scrivi_record(risultato, contesto(schema_ref=CORE_SCHEMA_ID), radice,
                        adattatori(publisher=_PubblicatoreInterrotto()))
    except KeyboardInterrupt:
        propagata = True
    except BaseException as exc:  # noqa: BLE001
        propagata = type(exc).__name__
    check("A1.5 KeyboardInterrupt nella finestra di scrittura PROPAGA", propagata is True,
          str(propagata))
    pending = [n for n in os.listdir(radice)] if os.path.isdir(radice) else []
    rapporto = R.aggrega(radice, quiescent=True)
    check("A1.5 il pending orfano resta, e l'aggregatore non lo conta",
          len(pending) == 1 and pending[0].endswith(".pending")
          and rapporto.records_discovered == 0 and rapporto.records_counted == 0,
          str(pending) + " / " + str(rapporto.records_discovered))

    check("A1.5 la superficie §4.14 non attraversa alcuna finestra di scrittura",
          V._valida_sottoschema_test(
              {}, contesto(), target=TestValidationTarget.CORE_SOURCE, adattatori=ad
          ).write_receipt.write_reason_code is WriteReasonCode.NOT_ELIGIBLE)

    # --- item 6/7/9: redazione dei segmenti non rappresentabili ------------
    sotto = os.path.join(tmp, "a1-caratteri")
    os.makedirs(sotto, exist_ok=True)
    schemi = radice_schemi_temporanea(sotto, sostituzioni={BV: _schema_caratteri()})
    ctx_bv = contesto(schema_ref=BV)

    radice = os.path.join(tmp, "a1-ctrl")
    e = V._esegui_shadow_core({NOME_CTRL: 123, NOME_BACKSLASH: "ok"}, ctx_bv,
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori(schemi))
    voci = e.validation_result.errors
    v = voci[0] if len(voci) == 1 else None
    check("A1.6 chiave con carattere di controllo -> ~R, puntatore null, non esatta",
          v is not None and v.instance_location == "/" + REDACTED_SEGMENT
          and v.instance_pointer is None and v.location_is_exact is False,
          repr(None if v is None else (v.instance_location, v.instance_pointer)))
    check("A1.6 il nome originale non compare da nessuna parte nel risultato",
          NOME_CTRL not in canonical_json(risultato_to_dict(e.validation_result)))
    check("A1.7 la redazione non e' uno scarto: errors_dropped_oversize resta 0",
          e.validation_result.errors_dropped_oversize == 0
          and e.validation_result.errors_recorded == 1)
    check("A1.7 lo schema_pointer porta il carattere dallo SCHEMA: record rifiutato",
          e.write_receipt.status is WriteStatus.WRITE_ERROR
          and e.write_receipt.write_reason_code is WriteReasonCode.RECORD_FIELD_INVALID
          and conta_finali(radice) == [],
          str(e.write_receipt.write_reason_code))

    radice = os.path.join(tmp, "a1-backslash")
    e = V._esegui_shadow_core({NOME_CTRL: "ok", NOME_BACKSLASH: 123}, ctx_bv,
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori(schemi))
    voci = e.validation_result.errors
    v = voci[0] if len(voci) == 1 else None
    check("A1.9 il backslash e' conservato, non redatto",
          v is not None and v.instance_pointer == "/" + NOME_BACKSLASH
          and v.location_is_exact is True
          and e.write_receipt.status is WriteStatus.WRITTEN
          and len(conta_finali(radice)) == 1,
          repr(None if v is None else v.instance_pointer))

    # --- item 8: missing_property non rappresentabile -> null --------------
    sotto2 = os.path.join(tmp, "a1-required")
    os.makedirs(sotto2, exist_ok=True)
    schemi2 = radice_schemi_temporanea(
        sotto2, sostituzioni={BV: _schema_caratteri(required_ctrl=True)})
    radice = os.path.join(tmp, "a1-missing")
    e = V._esegui_shadow_core({}, ctx_bv,
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori(schemi2))
    voci = [x for x in e.validation_result.errors if x.keyword == "required"]
    v = voci[0] if len(voci) == 1 else None
    check("A1.8 missing_property con carattere di controllo -> null, voce conservata",
          v is not None and v.missing_property is None and v.keyword == "required"
          and e.validation_result.errors_dropped_oversize == 0,
          repr(None if v is None else v.missing_property))
    check("A1.8 il nome non rappresentabile non finisce nel record",
          NOME_CTRL not in canonical_json(risultato_to_dict(e.validation_result)))

    # --- item 10: il backstop rifiuta i cinque campi, uno per volta --------
    # Si parte da un record REALE e conforme, e si inietta il carattere in un campo per
    # volta. Il checksum smette di tornare — atteso e irrilevante: qui si verifica che
    # il rifiuto per carattere ci sia, non che sia l'unica violazione.
    fail = V._esegui_shadow_core(carica("invalid/pain.enum.json"), contesto(),
                                 config=V.ConfigTest(attivo=True, radice_output=None),
                                 adattatori=ad).validation_result
    modello = V.costruisci_record(fail, contesto(), "0" * 32, "2026-08-09T00:00:00.000000Z")
    check("A1.10 il record di partenza e' conforme", valida_contratto_record(modello) == [],
          str(valida_contratto_record(modello)[:4]))

    mutazioni = {
        "schema_pointer": {"schema_pointer": "/properties/x" + CTRL},
        "instance_pointer": {"instance_pointer": "/x" + CTRL,
                             "instance_location": "/x" + CTRL,
                             "location_is_exact": True},
        "instance_location": {"instance_pointer": None,
                              "instance_location": "/x" + CTRL,
                              "location_is_exact": False},
        "keyword": {"keyword": "ty" + CTRL + "pe"},
        "missing_property": {"keyword": "required", "missing_property": "i" + CTRL + "d"},
    }
    for campo, patch in mutazioni.items():
        record = json.loads(canonical_json(modello))
        record["errors"] = [dict(record["errors"][0])]
        record["errors"][0].update(patch)
        violazioni = valida_contratto_record(record)
        check("A1.10 carattere di controllo in " + campo + " -> record non conforme",
              ("CARATTERI:" + campo) in violazioni, str(violazioni[:4]))

    # --- item 11: la restrizione di §2.1 non sconfina nel verdetto ---------
    e = V._esegui_shadow_core(carica("invalid/pain.enum.json"), contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=ad)
    check("A1.11 una violazione di schema resta FULL_FAIL, non CONTEXT_INVALID",
          e.invocation_status is InvocationStatus.OK
          and e.validation_result.full_outcome is FullOutcome.FULL_FAIL)

    sotto3 = os.path.join(tmp, "a1-ref")
    os.makedirs(sotto3, exist_ok=True)
    schemi3 = radice_schemi_temporanea(
        sotto3, sostituzioni={"pain.schema.json": byte_fixture("schema-broken/ref-external.schema.json")})
    e = V._esegui_shadow_core({"payload": {}}, contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(schemi3))
    check("A1.11 un $ref irrisolvibile resta VALIDATOR_ERROR, non CONTEXT_INVALID",
          e.invocation_status is InvocationStatus.OK
          and e.validation_result.reason_code is ReasonCode.REF_UNRESOLVABLE,
          str(e.validation_result.reason_code))

    # --- item 12: la regione 3 tiene la tassonomia del writer -------------
    blob, codice = V.passo_serializza({"non": object()})
    check("A1.12 un fallimento di serializzazione resta SERIALIZATION_FAILED",
          blob is None and codice is WriteReasonCode.SERIALIZATION_FAILED)
    check("A1.12 len(WriteReasonCode) == 11, invariato", len(WriteReasonCode) == 11)

    # --- §2.1: nessun except nudo, nessun except BaseException ------------
    for modulo in ("ds_validate.py", "ds_validate_contract.py", "ds_validate_report.py"):
        with open(os.path.join(QUI, modulo), "rb") as fh:
            albero = ast.parse(fh.read().decode("utf-8"))
        larghi = [
            n for n in ast.walk(albero)
            if isinstance(n, ast.ExceptHandler)
            and (n.type is None or (isinstance(n.type, ast.Name) and n.type.id == "BaseException"))
        ]
        check("A1 §2.1 " + modulo + ": nessun except nudo ne' BaseException",
              not larghi, str(len(larghi)))


# ==========================================================================
# A2 rev — la pubblicazione batte la pulizia (P0-2, U2)
#
# Criteri A2 §7. La concorrenza reale fra processi vive in ds_validate_concurrency.py
# e viene ripiegata qui sotto, cosi' che esista una sola sorgente di verita'.
# ==========================================================================


class _PubblicatorePuliziaGuasta(V.Pubblicatore):
    """`os.link` riesce, `os.unlink` no. ACL che negano DELETE, antivirus, indicizzatori:
    condizioni Windows ordinarie, ed e' esattamente la coppia che produceva P0-2."""

    def rimuovi(self, percorso):
        raise OSError("rimozione negata")


class _PubblicatoreScritturaEPuliziaGuaste(_PubblicatorePuliziaGuasta):
    def scrivi(self, fh, blob):
        raise OSError("scrittura negata")


def _pending_in(radice):
    if not os.path.isdir(radice):
        return []
    return sorted(n for n in os.listdir(radice) if n.endswith(".pending"))


def sezione_a2(tmp):
    risultato = _risultato_per_scrittura(SCHEMI)
    ctx = contesto(schema_ref=CORE_SCHEMA_ID)

    # --- A2.0 la separazione strutturale che rende P0-2 irripetibile -------
    # I test comportamentali qui sotto usano doppi scritti per la forma NUOVA
    # dell'adattatore, e per costruzione non possono esercitare il metodo conflato di
    # prima. Questa guardia sorveglia proprio la fusione: se qualcuno rimettesse
    # `os.unlink` dentro `pubblica`, un `unlink` fallito tornerebbe a essere catturato
    # come fallimento di pubblicazione e P0-2 rinascerebbe, silenziosamente e con tutti
    # i test comportamentali ancora verdi.
    def _sorgente(nome):
        metodo = getattr(V.Pubblicatore, nome, None)
        return inspect.getsource(metodo) if metodo is not None else ""

    sorgente_pubblica = _sorgente("pubblica")
    sorgente_rimuovi = _sorgente("rimuovi")
    check("A2.0 `pubblica` fa SOLO os.link: nessun unlink nella stessa operazione",
          "os.link(" in sorgente_pubblica and "os.unlink(" not in sorgente_pubblica)
    check("A2.0 la pulizia e' un'operazione distinta dell'adattatore",
          "os.unlink(" in sorgente_rimuovi and "os.link(" not in sorgente_rimuovi)
    check("A2.0 os.replace resta assente dal modulo", "os.replace(" not in inspect.getsource(V))

    # --- A2.1 link OK + pulizia KO -> WRITTEN, mai PUBLISH_FAILED ----------
    radice = os.path.join(tmp, "a2-pending-cleanup")
    ric = V.scrivi_record(risultato, ctx, radice,
                          adattatori(publisher=_PubblicatorePuliziaGuasta(),
                                     id_gen=id_fisso("a" * 32)))
    check("A2.1 link riuscito + pulizia fallita -> WRITTEN, non PUBLISH_FAILED",
          ric.status is WriteStatus.WRITTEN and ric.write_reason_code is None
          and ric.cleanup_status is CleanupStatus.PENDING_CLEANUP,
          str((ric.status, ric.write_reason_code, ric.cleanup_status)))
    finali = conta_finali(radice)
    conforme = False
    if finali:
        with open(os.path.join(radice, finali[0]), "rb") as fh:
            rec = json.loads(fh.read().decode("utf-8"))
        conforme = valida_contratto_record(rec) == []
    check("A2.1 il record finale esiste, e' conforme e il suo checksum torna",
          finali == ["a" * 32 + ".json"] and conforme, str(finali))
    check("A2.1 il pending sopravvive, ed e' il solo segno della pulizia mancata",
          len(_pending_in(radice)) == 1)
    check("A2.1 il record persistito ha ancora ESATTAMENTE 26 chiavi, senza cleanup",
          len(rec) == 26 and "cleanup_status" not in rec, str(len(rec)))

    # --- A2.2 il duplicato non e' piu' INDOTTO dalla ricevuta -------------
    # Con la ricevuta che diceva PUBLISH_FAILED, un chiamante conforme riprovava e
    # produceva un secondo record per lo stesso tentativo. Ora la ricevuta dice
    # WRITTEN, quindi non riprova: e' li' che "esattamente una volta" si regge.
    # Se un chiamante riprovasse comunque, la deduplicazione di §4.13 tiene.
    ric2 = V.scrivi_record(risultato, ctx, radice,
                           adattatori(publisher=_PubblicatorePuliziaGuasta(),
                                      id_gen=id_fisso("b" * 32)))
    rapporto = R.aggrega(radice, quiescent=True)
    check("A2.2 un ritentativo dello stesso (invocation_id, attempt) resta UN record contato",
          ric2.status is WriteStatus.WRITTEN and len(conta_finali(radice)) == 2
          and rapporto.records_counted == 1 and rapporto.duplicate_groups == 1
          and rapporto.duplicate_extra_records == 1,
          str((rapporto.records_counted, rapporto.duplicate_groups)))

    # --- A2.9 l'aggregatore conta i pending dal nome, senza aprirli --------
    contatore = ApriContato()
    rapporto = R._aggrega_con_lettore(radice, quiescent=True, apri=contatore)
    check("A2.9 i pending sono contati", rapporto.pending_files == 2,
          str(rapporto.pending_files))
    check("A2.9 i pending non sono mai aperti: aperture == record finali",
          contatore.aperture == len(conta_finali(radice)), str(contatore.aperture))
    check("A2.9 i pending non entrano in alcun bucket di record",
          rapporto.records_discovered == 2 and rapporto.records_valid == 2
          and sum(rapporto.by_full_outcome.values()) == rapporto.records_counted
          and R.invarianti_aggregato(rapporto) == [],
          str(R.invarianti_aggregato(rapporto)))
    check("A2.9 pending_files compare nel JSON dell'aggregatore",
          '"pending_files":2' in canonical_json(rapporto.to_dict()))

    # --- A2.3 successo normale -> CLEANED, nessun pending residuo ---------
    radice = os.path.join(tmp, "a2-pulito")
    ric = V.scrivi_record(risultato, ctx, radice, adattatori(id_gen=id_fisso("c" * 32)))
    check("A2.3 successo normale -> WRITTEN + CLEANED, nessun pending",
          ric.status is WriteStatus.WRITTEN and ric.cleanup_status is CleanupStatus.CLEANED
          and _pending_in(radice) == [] and len(conta_finali(radice)) == 1,
          str((ric.status, ric.cleanup_status, _pending_in(radice))))

    # --- A2.4 link fallito per motivo diverso dalla collisione ------------
    radice = os.path.join(tmp, "a2-publish-ko")
    ric = V.scrivi_record(risultato, ctx, radice,
                          adattatori(publisher=PubblicatoreGuasto("pubblica"),
                                     id_gen=id_fisso("d" * 32)))
    check("A2.4 link fallito -> WRITE_ERROR / PUBLISH_FAILED, nessun record finale",
          ric.status is WriteStatus.WRITE_ERROR
          and ric.write_reason_code is WriteReasonCode.PUBLISH_FAILED
          and conta_finali(radice) == [],
          str((ric.status, ric.write_reason_code)))
    check("A2.4 e la ricevuta dice comunque se il pending e' stato rimosso",
          ric.cleanup_status is CleanupStatus.CLEANED and _pending_in(radice) == [],
          str(ric.cleanup_status))

    # --- A2.4-bis un WRITE_ERROR puo' lasciare un pending, e ora lo dice --
    radice = os.path.join(tmp, "a2-write-ko")
    ric = V.scrivi_record(risultato, ctx, radice,
                          adattatori(publisher=_PubblicatoreScritturaEPuliziaGuaste(),
                                     id_gen=id_fisso("e" * 32)))
    check("A2.4 TEMP_WRITE_FAILED con pulizia fallita -> PENDING_CLEANUP, non silenzio",
          ric.status is WriteStatus.WRITE_ERROR
          and ric.write_reason_code is WriteReasonCode.TEMP_WRITE_FAILED
          and ric.cleanup_status is CleanupStatus.PENDING_CLEANUP
          and len(_pending_in(radice)) == 1,
          str((ric.write_reason_code, ric.cleanup_status)))

    # --- A2.5 collisione: il file preesistente non e' toccato -------------
    radice = os.path.join(tmp, "a2-collisione")
    V.scrivi_record(risultato, ctx, radice, adattatori(id_gen=id_fisso("f" * 32)))
    percorso = os.path.join(radice, "f" * 32 + ".json")
    with open(percorso, "rb") as fh:
        prima = sha256_hex(fh.read())
    ric = V.scrivi_record(risultato, ctx, radice, adattatori(id_gen=id_fisso("f" * 32)))
    with open(percorso, "rb") as fh:
        dopo = sha256_hex(fh.read())
    check("A2.5 collisione -> COLLISION_EXISTING_TARGET, preesistente byte-identico",
          ric.status is WriteStatus.COLLISION
          and ric.write_reason_code is WriteReasonCode.COLLISION_EXISTING_TARGET
          and prima == dopo and _pending_in(radice) == [],
          str((ric.status, ric.write_reason_code, prima == dopo)))

    # --- A2.6 nessuna ricevuta e' condivisa fra chiamate ------------------
    a = V.esegui_shadow_produzione({}, contesto())
    b = V.esegui_shadow_produzione({}, contesto())
    c = V._esegui_shadow_core({}, contesto(), config=V.ConfigTest(attivo=False, radice_output=None),
                              adattatori=adattatori())
    ricevute = [a.write_receipt, b.write_receipt, c.write_receipt]
    check("A2.6 NOT_ELIGIBLE -> cleanup_status None",
          all(r.write_reason_code is WriteReasonCode.NOT_ELIGIBLE for r in ricevute)
          and all(r.cleanup_status is None for r in ricevute))
    check("A2.6 ogni ricevuta e' una NUOVA istanza, mai un singleton condiviso",
          len({id(r) for r in ricevute}) == 3
          and not hasattr(V, "_RICEVUTA_NON_ELEGGIBILE"),
          str(len({id(r) for r in ricevute})))

    # --- A2.8 le tassonomie non si sono allargate -------------------------
    check("A2.8 len(WriteReasonCode) == 11, invariato", len(WriteReasonCode) == 11)
    check("A2.8 CleanupStatus e' chiuso a due valori", len(CleanupStatus) == 2)

    # --- A2.10 concorrenza reale fra processi (file dedicato) -------------
    for nome, ok, dettaglio in C.esegui(SCHEMI, tmp):
        check(nome, ok, dettaglio)


# ==========================================================================
# A4 — il tetto in byte si MISURA, non si calcola
#
# Nessuna logica nuova: il codice gia' serializzava e misurava il blob reale. A
# cambiare e' l'evidenza. §4.11 sommava budget di campo — 512+512+512+64+32+128+~120
# ~= 1.880 byte per voce, x50 ~= 94.000, piu' metadati ~= 94.600 < 131.072 — e da quel
# conto dichiarava RECORD_TOO_LARGE irraggiungibile. La somma conta i byte dei CAMPI e
# assume implicitamente che la serializzazione JSON sia gratis. Non lo e'.
# ==========================================================================

_BASE_FULL_FAIL = None


def _risultato_gonfio(riempimento: str, voci=None):
    """Un ValidationResult con MAX_ERRORS voci, ogni stringa ENTRO il proprio bound.

    Nessun campo supera il proprio limite: e' esattamente il punto. Il record e'
    conforme a `valida_contratto_record` con zero violazioni, e solo la
    serializzazione canonica rivela se sta sotto il tetto.
    """
    global _BASE_FULL_FAIL
    if _BASE_FULL_FAIL is None:
        _BASE_FULL_FAIL = V._esegui_shadow_core(
            carica("invalid/pain.enum.json"), contesto(),
            config=V.ConfigTest(attivo=True, radice_output=None),
            adattatori=adattatori()).validation_result
    n = MAX_ERRORS if voci is None else voci
    posizione = riempimento * MAX_LOCATION_BYTES
    voce = ValidationErrorEntry(
        schema_resource_id="pain.schema.json",
        schema_pointer=posizione,
        instance_pointer=posizione,
        instance_location=posizione,
        location_is_exact=True,
        keyword=riempimento * MAX_KEYWORD_BYTES,
        missing_property=None,
    )
    return dataclasses.replace(_BASE_FULL_FAIL, errors=tuple([voce] * n), errors_recorded=n)


def _byte_serializzati(risultato) -> int:
    record = V.costruisci_record(risultato, contesto(), "0" * 32, "2026-08-09T00:00:00.000000Z")
    return len(canonical_json(record).encode("utf-8"))


def _somma_byte_campi(risultato) -> int:
    totale = 0
    for v in risultato.errors:
        for s in (v.schema_resource_id, v.schema_pointer, v.instance_pointer,
                  v.instance_location, v.keyword, v.missing_property or ""):
            totale += len(s.encode("utf-8"))
    return totale


def sezione_a4(tmp):
    ascii_ = _risultato_gonfio("a")
    backslash = _risultato_gonfio("\\")

    # --- A4.3 la prova che la regola e' misurata e non calcolata ----------
    # Stessi bound di campo, stesso numero di voci, stessa forma. Cambia solo un
    # carattere del riempimento, e la somma dei campi non se ne accorge.
    somma_a = _somma_byte_campi(ascii_)
    somma_b = _somma_byte_campi(backslash)
    ser_a = _byte_serializzati(ascii_)
    ser_b = _byte_serializzati(backslash)
    check("A4.3 la somma dei byte di campo e' identica nei due casi",
          somma_a == somma_b and somma_a < MAX_RECORD_BYTES, str((somma_a, somma_b)))
    check("A4.3 riempito ASCII: sotto il tetto — e' lo scenario che l'ADR calcolava",
          ser_a < MAX_RECORD_BYTES, str(ser_a))
    check("A4.3 riempito con backslash: OLTRE il tetto, a parita' di byte di campo",
          ser_b > MAX_RECORD_BYTES, str(ser_b))
    check("A4.3 entrambi i record sono conformi con ZERO violazioni",
          valida_contratto_record(V.costruisci_record(
              ascii_, contesto(), "0" * 32, "2026-08-09T00:00:00.000000Z")) == []
          and valida_contratto_record(V.costruisci_record(
              backslash, contesto(), "0" * 32, "2026-08-09T00:00:00.000000Z")) == [],
          "un record conforme puo' superare il tetto: non e' un backstop teorico")

    # --- A4.1 / A4.2 il percorso reale, non byte fabbricati ---------------
    radice = os.path.join(tmp, "a4-oltre")
    ric = V.scrivi_record(backslash, contesto(), radice, adattatori())
    check("A4.1 oltre il tetto -> WRITE_ERROR / RECORD_TOO_LARGE",
          ric.status is WriteStatus.WRITE_ERROR
          and ric.write_reason_code is WriteReasonCode.RECORD_TOO_LARGE,
          str((ric.status, ric.write_reason_code)))
    check("A4.1 nessun file pubblicato, verificato elencando la radice",
          conta_finali(radice) == [] and _pending_in(radice) == [],
          str(os.listdir(radice) if os.path.isdir(radice) else "radice assente"))
    check("A4.1 e nessun pending resta indietro: cleanup_status non e' PENDING_CLEANUP",
          ric.cleanup_status is None, str(ric.cleanup_status))

    radice = os.path.join(tmp, "a4-sotto")
    ric = V.scrivi_record(ascii_, contesto(), radice, adattatori(id_gen=id_fisso("7" * 32)))
    check("A4.2 sotto il tetto, stesso percorso reale -> pubblicato",
          ric.status is WriteStatus.WRITTEN and conta_finali(radice) == ["7" * 32 + ".json"],
          str((ric.status, ric.write_reason_code)))

    # --- A4.4 la misura e' sul blob, non su una stima --------------------
    # `passo_dimensione` resta il controllo, ma cio' che gli arriva viene da
    # `canonical_json(record).encode("utf-8")` e da nient'altro.
    record = V.costruisci_record(backslash, contesto(), "0" * 32, "2026-08-09T00:00:00.000000Z")
    blob, codice = V.passo_serializza(record)
    check("A4.4 il blob misurato e' esattamente la serializzazione canonica UTF-8",
          codice is None and blob == canonical_json(record).encode("utf-8")
          and V.passo_dimensione(blob) is WriteReasonCode.RECORD_TOO_LARGE
          and len(blob) == ser_b)
    check("A4.4 il tetto resta 131072, invariato", MAX_RECORD_BYTES == 131072)


# ==========================================================================
# A5 — il fingerprint si conserva quando il registro e' stato costruito
#
# L'implementazione calcolava il fingerprint e poi lo buttava sul ramo
# SCHEMA_ROOT_OUTSIDE_REGISTRY, leggendo C3 come una condizione congiunta sui due
# campi. Ma li' il registro E' stato costruito per intero. Il record perdeva l'unica
# traccia di quale chiusura fosse attiva, e l'invariante 8 — un'implicazione a un solo
# verso — diventava una biimplicazione travestita che nessun test poteva falsificare.
# ==========================================================================


def _e_hex64(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _byte_della_radice(radice) -> dict:
    byte = {}
    for n in sorted(os.listdir(radice)):
        if n.endswith(".json"):
            with open(os.path.join(radice, n), "rb") as fh:
                byte[n] = fh.read()
    return byte


def sezione_a5(tmp):
    # Una chiusura RIDOTTA che si costruisce senza errori: bootstrap-verdict e' omesso
    # ma resta un artifact root, quindi la richiesta supera il passo 4 e cade al passo 6
    # sulla appartenenza al registro. Nessun altro schema lo referenzia, quindi togliergli
    # il file non incrina la chiusura.
    sotto = os.path.join(tmp, "a5-registro-ridotto")
    os.makedirs(sotto, exist_ok=True)
    schemi = radice_schemi_temporanea(sotto, ometti={BV})
    atteso = V.calcola_fingerprint_da_byte(_byte_della_radice(schemi))
    ctx_fuori = contesto(schema_ref=BV)

    # --- A5.1 registro costruito + radice fuori registro ------------------
    e = V._esegui_shadow_core({}, ctx_fuori,
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(schemi))
    r = e.validation_result
    check("A5.1 radice fuori dal registro -> VALIDATOR_ERROR con il codice atteso",
          r.full_outcome is FullOutcome.VALIDATOR_ERROR
          and r.reason_code is ReasonCode.SCHEMA_ROOT_OUTSIDE_REGISTRY,
          str(r.reason_code))
    check("A5.1 schema_sha256 e' null: la radice selezionata non e' autorizzata",
          r.schema_sha256 is None, str(r.schema_sha256))
    check("A5.1 registry_fingerprint e' un HEX64 valido, non null",
          _e_hex64(r.registry_fingerprint), str(r.registry_fingerprint))
    check("A5.1 ed e' il fingerprint della chiusura EFFETTIVAMENTE costruita",
          r.registry_fingerprint == atteso, str(r.registry_fingerprint))

    # --- A5.2 stessa chiusura, radice valida -> stesso fingerprint --------
    e2 = V._esegui_shadow_core(carica("valid/pain.ok.json"), contesto(),
                               config=V.ConfigTest(attivo=True, radice_output=None),
                               adattatori=adattatori(schemi))
    r2 = e2.validation_result
    check("A5.2 una validazione riuscita sulla stessa chiusura da' lo STESSO fingerprint",
          r2.registry_fingerprint == r.registry_fingerprint,
          str((r.registry_fingerprint, r2.registry_fingerprint)))
    check("A5.2 e li' schema_sha256 non e' null, perche' la radice e' autorizzata",
          _e_hex64(r2.schema_sha256), str(r2.schema_sha256))

    # --- A5.3 dipendenza mancante -> entrambi null ------------------------
    e3 = V._esegui_shadow_core({}, contesto(),
                               config=V.ConfigTest(attivo=True, radice_output=None),
                               adattatori=adattatori(importer=ImporterStrumentato(fallisce=True)))
    r3 = e3.validation_result
    check("A5.3 dipendenza mancante -> entrambi null: li' il registro non e' mai nato",
          r3.reason_code is ReasonCode.DEPENDENCY_MISSING
          and r3.schema_sha256 is None and r3.registry_fingerprint is None,
          str((r3.schema_sha256, r3.registry_fingerprint)))

    # --- A5.4 l'invariante 8 non e' piu' una tautologia -------------------
    # Il verso dichiarato regge su tutti gli stati; il verso opposto e' FALSIFICATO
    # da A5.1. Senza quello stato i due campi sarebbero stati null insieme ovunque, e
    # un'invariante che nessuno stato puo' violare non e' un'invariante testata.
    stati = [("fuori-registro", r), ("validazione-riuscita", r2), ("dipendenza-mancante", r3)]
    check("A5.4 invariante 8 (sha != null => fingerprint != null) regge su tutti gli stati",
          all(not (s.schema_sha256 is not None and s.registry_fingerprint is None)
              for _n, s in stati))
    check("A5.4 il verso opposto e' falsificato: esiste fingerprint != null con sha null",
          any(s.schema_sha256 is None and s.registry_fingerprint is not None
              for _n, s in stati),
          "senza questo stato l'implicazione sarebbe una biimplicazione travestita")

    # Lo stato discriminante deve essere PERSISTIBILE, non solo rappresentabile.
    radice = os.path.join(tmp, "a5-record")
    ric = V.scrivi_record(r, ctx_fuori, radice, adattatori(schemi, id_gen=id_fisso("5" * 32)))
    finali = conta_finali(radice)
    su_disco = None
    if finali:
        with open(os.path.join(radice, finali[0]), "rb") as fh:
            su_disco = json.loads(fh.read().decode("utf-8"))
    check("A5.4 il record che porta lo stato asimmetrico e' conforme e viene pubblicato",
          ric.status is WriteStatus.WRITTEN and su_disco is not None
          and valida_contratto_record(su_disco) == [],
          str(ric.write_reason_code))
    check("A5.4 e sul disco i due campi restano com'erano: null e HEX64",
          su_disco is not None and su_disco["schema_sha256"] is None
          and _e_hex64(su_disco["registry_fingerprint"]))


# ==========================================================================
# P0-3 / P0-4 — preflight della chiusura e attribuzione del percorso
# ==========================================================================


def _ultimo_segmento(puntatore: str) -> str:
    if not puntatore:
        return ""
    pezzo = puntatore.split("/")[-1]
    return pezzo.replace("~1", "/").replace("~0", "~")


def _coerenza_voce(voce, documenti) -> str:
    """§7.16 — la voce descrive un nodo che ESISTE e che contiene davvero cio' che dice.

    E' il controllo che avrebbe preso P0-4 il giorno in cui e' stato scritto: un
    puntatore che indirizza `["promoted_by"]` mentre riporta `missing_property: id`
    non e' un dettaglio cosmetico, e' una prova sbagliata.
    """
    documento = documenti.get(voce.schema_resource_id)
    if documento is None:
        return "risorsa non registrata: " + voce.schema_resource_id
    try:
        nodo = deref_puntatore(documento, voce.schema_pointer)
    except Exception:
        return "puntatore non dereferenziabile in " + voce.schema_resource_id
    if _ultimo_segmento(voce.schema_pointer) != voce.keyword:
        return "il puntatore non termina sulla parola chiave riportata: " + voce.keyword
    if voce.missing_property is not None:
        if voce.keyword != "required":
            return "missing_property senza keyword=required"
        if not isinstance(nodo, list) or voce.missing_property not in nodo:
            return "missing_property non e' membro del required indirizzato"
    return ""


def sezione_p0_3_p0_4(tmp):
    documenti = documenti_schemi()

    # --- P0-3: ogni schema rotto -> VALIDATOR_ERROR con QUALSIASI istanza ---
    manifesto = carica("manifest.json")
    rotti = [v["path"] for v in manifesto["entries"] if v["kind"] == "schema_fixture"]
    for percorso in rotti:
        for etichetta, istanza in (("istanza vuota {}", {}),
                                   ("istanza che attraversa il ramo", {"payload": {}})):
            r = esegui_schema_rotto(tmp, percorso, istanza)
            check("P0-3 " + percorso + " con " + etichetta + " -> VALIDATOR_ERROR",
                  r.full_outcome is FullOutcome.VALIDATOR_ERROR
                  and r.reason_code in REASON_CODES_VALIDATOR_ERROR
                  and r.errors == (),
                  str(r.full_outcome) + "/" + str(r.reason_code))

    # Il caso che produceva P0-3: `$ref` irrisolvibile sotto `properties.payload`, e
    # un'istanza priva di `payload` che non lo attraversa mai. Prima: FULL_PASS.
    for fixture in ("schema-broken/ref-external.schema.json",
                    "schema-broken/ref-unregistered.schema.json"):
        r = esegui_schema_rotto(tmp, fixture, {})
        check("P0-3 " + fixture + " con {} -> REF_UNRESOLVABLE, mai FULL_PASS",
              r.reason_code is ReasonCode.REF_UNRESOLVABLE
              and r.full_outcome is not FullOutcome.FULL_PASS,
              str(r.full_outcome) + "/" + str(r.reason_code))

    # Il preflight non dipende nemmeno dalla radice selezionata: una chiusura rotta
    # altrove fa fallire una validazione radicata su uno schema integro.
    sotto = os.path.join(tmp, "p03-altra-radice")
    os.makedirs(sotto, exist_ok=True)
    schemi = radice_schemi_temporanea(
        sotto, sostituzioni={"pain.schema.json": byte_fixture(
            "schema-broken/ref-unregistered.schema.json")})
    e = V._esegui_shadow_core(carica("valid/gate-decision.ok.json"),
                              contesto(schema_ref="gate-decision.schema.json"),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(schemi))
    check("P0-3 la chiusura si verifica per intero, non solo dalla radice selezionata",
          e.validation_result.reason_code is ReasonCode.REF_UNRESOLVABLE,
          str(e.validation_result.reason_code))
    check("P0-3 nessuna chiamata di rete durante il preflight", CHIAMATE_RETE["n"] == 0)

    # --- §4.10: la riga VALIDATOR_ERROR della matrice, finalmente coperta ---
    radice = os.path.join(tmp, "p03-emissione")
    sotto2 = os.path.join(tmp, "p03-emissione-schemi")
    os.makedirs(sotto2, exist_ok=True)
    schemi2 = radice_schemi_temporanea(
        sotto2, sostituzioni={"pain.schema.json": byte_fixture(
            "schema-broken/ref-external.schema.json")})
    e = V._esegui_shadow_core({}, contesto(),
                              config=V.ConfigTest(attivo=True, radice_output=radice),
                              adattatori=adattatori(schemi2))
    check("P0-3 la riga VALIDATOR_ERROR di §4.10 e' esercitata con l'esito giusto",
          e.validation_result.full_outcome is FullOutcome.VALIDATOR_ERROR
          and e.write_receipt.status is WriteStatus.WRITTEN
          and len(conta_finali(radice)) == 1,
          str(e.validation_result.full_outcome))

    # --- P0-4: le due fixture negative di evidence-pack --------------------
    ctx_ep = contesto(schema_ref="evidence-pack.schema.json")
    r = V._esegui_shadow_core(carica("invalid/evidence-pack.claim-id-type.json"), ctx_ep,
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori()).validation_result
    check("P0-4 violazione di tipo sotto claims[].id -> FULL_FAIL con una voce",
          r.full_outcome is FullOutcome.FULL_FAIL and len(r.errors) == 1,
          str(r.full_outcome) + "/" + str(r.reason_code) + " voci=" + str(len(r.errors)))

    r = V._esegui_shadow_core(carica("invalid/evidence-pack.claim-id-missing.json"), ctx_ep,
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori()).validation_result
    voci = [v for v in r.errors if v.keyword == "required"]
    v = voci[0] if len(voci) == 1 else None
    check("P0-4 `id` mancante -> FULL_FAIL", r.full_outcome is FullOutcome.FULL_FAIL)
    check("P0-4 schema_resource_id e' la risorsa che contiene davvero la parola chiave",
          v is not None and v.schema_resource_id == CORE_SCHEMA_ID,
          repr(None if v is None else v.schema_resource_id))
    check("P0-4 schema_pointer indirizza /$defs/Claim/required, non il sibling locale",
          v is not None and v.schema_pointer == "/$defs/Claim/required",
          repr(None if v is None else v.schema_pointer))
    # Il deref va tentato, non assunto: su un puntatore mal attribuito solleva, e un
    # controllo che fa cadere la suite invece di fallire non e' un controllo.
    contenuto = None
    if v is not None:
        try:
            contenuto = deref_puntatore(documenti.get(v.schema_resource_id, {}),
                                        v.schema_pointer)
        except Exception:
            contenuto = None
    check("P0-4 e quel required contiene davvero `id`",
          v is not None and v.missing_property == "id"
          and isinstance(contenuto, list) and "id" in contenuto,
          repr(None if v is None else (v.schema_resource_id, v.missing_property, contenuto)))

    # --- §7.16: la proprieta' su TUTTO il corpus --------------------------
    entrate = [x for x in manifesto["entries"]
               if x["kind"] in ("validation_instance", "subschema_instance")]
    voci_totali = 0
    incoerenti = []
    for voce_manifest in entrate:
        percorso = voce_manifest["path"]
        istanza = carica(percorso)
        if voce_manifest["kind"] == "validation_instance":
            r = V._esegui_shadow_core(
                istanza, contesto(schema_ref=voce_manifest["target_ref"]),
                config=V.ConfigTest(attivo=True, radice_output=None),
                adattatori=adattatori()).validation_result
        else:
            r = V._valida_sottoschema_test(
                istanza, contesto(schema_ref=CORE_SCHEMA_ID),
                target=TestValidationTarget[voce_manifest["target_ref"]],
                adattatori=adattatori()).validation_result
        for v in r.errors:
            voci_totali += 1
            problema = _coerenza_voce(v, documenti)
            if problema:
                incoerenti.append(percorso + ": " + problema)
    check("§7.16 ogni voce del corpus indirizza un nodo che contiene cio' che riporta",
          incoerenti == [], str(incoerenti[:3]))
    check("§7.16 la proprieta' e' stata esercitata su un numero non banale di voci",
          voci_totali >= 20, str(voci_totali))


# ==========================================================================
# R2.1-6 — i 24 criteri differiti dentro la suite (`DEC-13`), con la
# contabilita' a tre contatori di `DEC-14`
#
# Il commit `e508b0b` non toccava questo file: i 562 controlli erano la suite
# PREESISTENTE, e nessuno dei 24 criteri `[AD]`/`[CI]` di `E1` rev6 §9 e di `E2` rev3
# §7 era asserito. E' la causa comune dei cinque difetti trovati dalla review: sono
# sopravvissuti a una suite verde perche' la suite non poteva vederli. Da `R2.1` in
# poi un criterio `[AD]` non asserito qui **non esiste come copertura** (`DEC-13`).
#
# `DEC-14`: ogni criterio registrato finisce in ESATTAMENTE uno dei tre contatori —
# `passed`, `failed`, `not_applicable` — e i tre sommano ai registrati. Un criterio
# non eseguito e' un fatto da riportare, non un vuoto.
# ==========================================================================

#: I 24: 12 `[AD]` piu' 1 `[CI]` di `E1` rev6 §9.7, e 11 `[AD]` di `E2` rev3 §7.
CRITERI_REGISTRATI = (
    "K5", "K6", "K7", "K8a", "K9", "K10", "K10b", "K10e", "K13", "K14", "K15",
    "K25", "K27",
    "T1", "T2", "T3", "T4", "T5", "T6", "T8a", "T8b", "T15", "T16", "T18",
)

DIALETTO = "https://json-schema.org/draft/2020-12/schema"

#: `E2` rev3 §2 — i 17 campi persistiti in ambito: 16 a livello record piu'
#: `errors[].schema_resource_id`. I quattro enum (`full_outcome`, `comparison`,
#: `legacy_outcome`, `reason_code`) restano fuori perche' governati
#: dall'appartenenza, e le cinque posizioni di `ENTRY_KEYS` per `A1`/`D-4`.
CAMPI_RECORD_IN_AMBITO = (
    "record_version", "record_id", "invocation_id", "mode", "run_id", "role",
    "stage", "artifact_id", "timestamp_utc", "python_version", "validator_version",
    "referencing_version", "schema_ref", "schema_sha256", "registry_fingerprint",
    "checksum",
)


def criterio(nome, condizione, dettaglio=""):
    """Registra l'esito di un criterio differito, ed e' anche un controllo della suite."""
    CRITERI_ESITI[nome] = "passed" if condizione else "failed"
    check("R2.1-6 criterio " + nome, condizione, dettaglio)


def criterio_non_applicabile(nome, ragione):
    """`DEC-14` — guardia di PIATTAFORMA esplicita, con la ragione scritta.

    Non e' un `try/except` che assorbe, e non e' un verde implicito: l'esito
    `NOT_APPLICABLE_WINDOWS` e' dichiarato e contato a parte. Vale per `T8b` e per
    nient'altro: se un secondo criterio risultasse non applicabile su questa
    piattaforma, ci si ferma e lo si segnala (`14-…` §5.3), non si allarga la guardia.
    """
    CRITERI_ESITI[nome] = "not_applicable"
    NON_APPLICABILI.append((nome, ragione))


# --- utilita' comuni ai criteri -------------------------------------------


def _registro(tmp, nome, sostituzioni=None, aggiunte=None, ometti=None) -> str:
    sotto = os.path.join(tmp, "r21-" + nome)
    os.makedirs(sotto, exist_ok=True)
    return radice_schemi_temporanea(sotto, sostituzioni=sostituzioni,
                                    aggiunte=aggiunte, ometti=ometti)


def _esito(schemi, istanza, schema_ref="pain.schema.json"):
    return V._esegui_shadow_core(
        istanza, contesto(schema_ref=schema_ref),
        config=V.ConfigTest(attivo=True, radice_output=None),
        adattatori=adattatori(schemi)).validation_result


def _doc(extra, ident="pain.schema.json") -> bytes:
    documento = {"$id": ident, "$schema": DIALETTO}
    documento.update(extra)
    return json.dumps(documento).encode("utf-8")


def _firma(r):
    """Esito osservabile, confrontabile fra due registri."""
    return (r.full_outcome, r.reason_code, len(r.errors),
            tuple((v.schema_resource_id, v.schema_pointer, v.instance_location,
                   v.keyword) for v in r.errors))


def _documenti_di(schemi) -> dict:
    fuori = {}
    for n in sorted(os.listdir(schemi)):
        if n.endswith(".json"):
            with open(os.path.join(schemi, n), "rb") as fh:
                fuori[n] = json.loads(fh.read().decode("utf-8"))
    return fuori


def _schema_registro(nome):
    with open(os.path.join(SCHEMI, nome), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _record_conforme(record_id="0" * 32, **kw):
    """Un record `FULL_FAIL` reale, conforme e con voci: la base delle sonde di `E2`."""
    risultato = V._esegui_shadow_core(
        carica("invalid/pain.enum.json"), contesto(),
        config=V.ConfigTest(attivo=True, radice_output=None),
        adattatori=adattatori()).validation_result
    return V.costruisci_record(risultato, contesto(**kw), record_id,
                               "2026-08-09T00:00:00.000000Z")


def _ricalcola_checksum(record):
    r = json.loads(json.dumps(record))
    r.pop("checksum", None)
    r["checksum"] = checksum_record(r)
    return r


def _scrivi_record_grezzo(radice, record):
    """Scrive un record SENZA passare dal writer: serve a sondare l'aggregatore."""
    os.makedirs(radice, exist_ok=True)
    percorso = os.path.join(radice, str(record["record_id"]) + ".json")
    with open(percorso, "wb") as fh:
        fh.write(canonical_json(record).encode("utf-8"))
    return percorso


# --- E1 rev6 §9 — 12 [AD] + 1 [CI] ----------------------------------------


def _criterio_K5(tmp):
    schemi = _registro(tmp, "K5", {"pain.schema.json": _doc(
        {"type": "array", "items": {"$dynamicRef": "#NONESISTE"}})})
    esiti = [_esito(schemi, ist) for ist in ([], [1])]
    criterio("K5", all(r.reason_code is ReasonCode.REF_UNRESOLVABLE for r in esiti),
             str([str(r.reason_code) for r in esiti]))


def _criterio_K6(tmp):
    """`[CI]` — falsifica un preflight che pretenda di risolvere il BINDING.

    `core` dichiara il `$dynamicAnchor T` di fallback, `pain` lo ridichiara con
    `type: string` e `scorecard` con `type: integer`. La closure risolve staticamente
    per entrambe le radici; l'esito dipende dalla radice e dall'istanza, cioe' da cio'
    che il preflight non puo' conoscere. Un preflight che affermasse il binding
    dovrebbe emettere un esito qui, e non lo fa.
    """
    core = _schema_registro(CORE_SCHEMA_ID)
    core["$defs"]["ListaDinamica"] = {"type": "array", "items": {"$dynamicRef": "#T"}}
    core["$defs"]["AncoraDinamica"] = {"$dynamicAnchor": "T"}
    rif = {"$ref": CORE_SCHEMA_ID + "#/$defs/ListaDinamica"}
    schemi = _registro(tmp, "K6", {
        CORE_SCHEMA_ID: json.dumps(core).encode("utf-8"),
        "pain.schema.json": _doc(
            dict(rif, **{"$defs": {"T": {"$dynamicAnchor": "T", "type": "string"}}})),
        "scorecard.schema.json": _doc(
            dict(rif, **{"$defs": {"T": {"$dynamicAnchor": "T", "type": "integer"}}}),
            ident="scorecard.schema.json"),
    })
    passa = [_esito(schemi, ["s"], "pain.schema.json"),
             _esito(schemi, [1], "scorecard.schema.json")]
    fallisce = [_esito(schemi, [1], "pain.schema.json"),
                _esito(schemi, ["s"], "scorecard.schema.json")]
    criterio("K6",
             V.chiusura_risolve(_documenti_di(schemi)) is True
             and all(r.full_outcome is FullOutcome.FULL_PASS for r in passa)
             and all(r.full_outcome is not FullOutcome.FULL_PASS for r in fallisce),
             str([str(r.full_outcome) for r in passa + fallisce]))


def _catena(lunghezza):
    defs = {}
    for j in range(lunghezza):
        defs["r%d" % j] = ({"$ref": "#/$defs/r%d" % (j + 1)}
                           if j + 1 < lunghezza else {"type": "string"})
    return _doc({"$ref": "#/$defs/r0", "$defs": defs})


def _criterio_K7(tmp):
    esiti = {}
    for lunghezza in (1, 32, 64, 200):
        schemi = _registro(tmp, "K7-%d" % lunghezza,
                           {"pain.schema.json": _catena(lunghezza)})
        r = _esito(schemi, 123)
        esiti[lunghezza] = (r.full_outcome, r.reason_code, len(r.errors),
                            r.errors[0].schema_pointer if r.errors else None)
    atteso = {lunghezza: (FullOutcome.FULL_FAIL, None, 1,
                          "/$defs/r%d/type" % (lunghezza - 1)) for lunghezza in esiti}
    criterio("K7", esiti == atteso, str(esiti))


def _criterio_K8a(tmp):
    schemi = _registro(tmp, "K8a", {"pain.schema.json": _doc(
        {"type": "array",
         "items": {"$ref": CORE_SCHEMA_ID + "#/$defs/Claim/required/0"}})})
    esiti = [_esito(schemi, ist) for ist in ([], [1], {})]
    criterio("K8a", all(r.reason_code is ReasonCode.REF_UNRESOLVABLE for r in esiti),
             str([str(r.reason_code) for r in esiti]))


#: `K9` — gli otto costrutti che `M7` misura, tutti in `market-report.schema.json`,
#: che la radice `pain.schema.json` non raggiunge. Sono le otto cause di spegnimento
#: globale che `E1` rev6 §3.4 toglie: occorrenze in posizione DATI, un nome di
#: proprieta', una parola chiave ignota, un frammento percent-encoded e una risorsa
#: embedded. Nessuna e' un riferimento, o e' un riferimento che risolve.
COSTRUTTI_K9 = {
    "const": {"Z": {"const": {"$ref": "#/$defs/NONESISTE"}}},
    "default": {"Z": {"default": {"$ref": "#/$defs/NONESISTE"}}},
    "examples": {"Z": {"examples": [{"$ref": "#/$defs/NONESISTE"}]}},
    "enum": {"Z": {"enum": [{"$ref": "#/$defs/NONESISTE"}]}},
    "nome-di-proprieta": {"Z": {"type": "object",
                                "properties": {"$ref": {"type": "string"}}}},
    "parola-chiave-ignota": {"Z": {"xIgnota": {"$ref": "#/$defs/NONESISTE"}}},
    "frammento-percent": {"Z": {"$ref": "#/%24defs/Y"}, "Y": {"type": "string"}},
    "risorsa-embedded": {"Z": {"$ref": "emb.schema.json"},
                         "W": {"$id": "emb.schema.json", "type": "string"}},
}


def _criterio_K9(tmp):
    intatto = _firma(_esito(_registro(tmp, "K9-intatto"), {}))
    divergenti = []
    for nome, aggiunte in sorted(COSTRUTTI_K9.items()):
        documento = _schema_registro("market-report.schema.json")
        documento.setdefault("$defs", {}).update(json.loads(json.dumps(aggiunte)))
        schemi = _registro(tmp, "K9-" + nome, {
            "market-report.schema.json": json.dumps(documento).encode("utf-8")})
        if _firma(_esito(schemi, {})) != intatto:
            divergenti.append(nome)
    criterio("K9", len(COSTRUTTI_K9) == 8 and divergenti == [], str(divergenti))


SIGMA1 = {"type": "array", "items": {"$dynamicRef": "#T"},
          "$defs": {"T": {"$dynamicAnchor": "T", "type": "string"}}}
SIGMA2 = {"type": "array", "items": {"$dynamicRef": "#tt"},
          "$defs": {"T": {"$dynamicAnchor": "T", "type": "string"}}}
SIGMA3 = {"type": "array", "items": {"$ref": "#/$defs/T"},
          "$defs": {"T": {"type": "string"}}}


def _criterio_K10(tmp):
    r1 = _esito(_registro(tmp, "K10-s1", {"pain.schema.json": _doc(SIGMA1)}), [1])
    r3 = _esito(_registro(tmp, "K10-s3", {"pain.schema.json": _doc(SIGMA3)}), [1])
    criterio("K10",
             r1.full_outcome is FullOutcome.FULL_FAIL and len(r1.errors) == 1
             and r3.full_outcome is FullOutcome.FULL_FAIL and len(r3.errors) == 1
             and r1.errors[0].instance_location == r3.errors[0].instance_location
             and r1.errors[0].instance_pointer == r3.errors[0].instance_pointer
             and r1.errors[0].location_is_exact is r3.errors[0].location_is_exact,
             str((str(r1.full_outcome), len(r1.errors),
                  None if not r1.errors else r1.errors[0].instance_location)))


def _criterio_K10b(tmp):
    r = _esito(_registro(tmp, "K10b", {"pain.schema.json": _doc(SIGMA1)}), [1, 2])
    criterio("K10b",
             r.full_outcome is FullOutcome.FULL_FAIL and len(r.errors) == 2,
             str((str(r.full_outcome), len(r.errors))))


def _criterio_K10e(tmp):
    schemi = _registro(tmp, "K10e", {"pain.schema.json": _doc(SIGMA2)})
    esiti = [_esito(schemi, ist) for ist in ([], ["s"], [1])]
    criterio("K10e", all(r.reason_code is ReasonCode.REF_UNRESOLVABLE for r in esiti),
             str([str(r.reason_code) for r in esiti]))


#: `K13`/`K14` — le otto combinazioni di `M2`: due risorse per quattro dialetti.
DIALETTI_K13 = ("https://json-schema.org/draft/2019-09/schema",
                "http://json-schema.org/draft-07/schema#",
                None,
                "https://esempio.invalido/dialetto-inventato")


def _registro_dialetto(tmp, etichetta, risorsa, dialetto, indice):
    documento = _schema_registro(risorsa)
    if dialetto is None:
        documento.pop("$schema", None)
    else:
        documento["$schema"] = dialetto
    return _registro(tmp, "%s-%s-%d" % (etichetta, risorsa.split(".")[0], indice),
                     {risorsa: json.dumps(documento).encode("utf-8")})


def _criteri_K13_K14(tmp):
    for etichetta, istanza in (("K13", carica("valid/pain.ok.json")),
                               ("K14", carica("invalid/pain.enum.json"))):
        esiti = []
        for risorsa in (CORE_SCHEMA_ID, "pain.schema.json"):
            for indice, dialetto in enumerate(DIALETTI_K13):
                schemi = _registro_dialetto(tmp, etichetta, risorsa, dialetto, indice)
                esiti.append(_esito(schemi, istanza).reason_code)
        criterio(etichetta,
                 len(esiti) == 8
                 and all(c is ReasonCode.SCHEMA_META_INVALID for c in esiti),
                 str([str(c) for c in esiti]))


def _criterio_K15(tmp):
    """Risorsa embedded con `$id` proprio e SENZA `$schema`: eredita il dialetto."""
    schemi = _registro(tmp, "K15", {"pain.schema.json": _doc(
        {"type": "object", "properties": {"k": {"$ref": "emb.schema.json"}},
         "$defs": {"E": {"$id": "emb.schema.json", "type": "string"}}})})
    r = _esito(schemi, {})
    criterio("K15",
             r.full_outcome is FullOutcome.FULL_PASS and r.reason_code is None,
             str((str(r.full_outcome), str(r.reason_code))))


def _criterio_K25(tmp):
    """`chiusura_risolve` NON solleva mai: visita iterativa, non la pila di chiamate.

    Non promette alcuna profondita': afferma una proprieta' dell'implementazione. Dal
    percorso pubblico l'esito resta `SCHEMA_META_INVALID`, perche' `F5` cade prima.
    """
    nodo = {"type": "object"}
    for _ in range(900):
        nodo = {"type": "object", "properties": {"p": nodo}}
    schemi = _registro(tmp, "K25", {"pain.schema.json": _doc(nodo)})
    pubblico = _esito(schemi, {}).reason_code
    try:
        diretta = V.chiusura_risolve(_documenti_di(schemi))
        sollevata = None
    except Exception as exc:  # noqa: BLE001 — e' esattamente cio' che si nega
        diretta, sollevata = None, type(exc).__name__
    criterio("K25",
             sollevata is None and diretta is True
             and pubblico is ReasonCode.SCHEMA_META_INVALID,
             str((sollevata, diretta, str(pubblico))))


def _criterio_K27():
    """`_MAX_CANDIDATI` non lega sul corpus `valid/` + `invalid/`.

    Se legasse, l'attribuzione tornerebbe a dipendere dall'ordine di enumerazione: e'
    il difetto `P0-4`. La seconda meta' di `K27` — il tetto come CAUSA di degrado —
    resta a `R3`.
    """
    manifesto = carica("manifest.json")
    entrate = [v for v in manifesto["entries"]
               if v["kind"] == "validation_instance"
               and v["path"].split("/")[0] in ("valid", "invalid")]
    V._STRUMENTAZIONE["ampiezza_massima"] = 0
    for voce in entrate:
        V._esegui_shadow_core(
            carica(voce["path"]), contesto(schema_ref=voce["target_ref"]),
            config=V.ConfigTest(attivo=True, radice_output=None),
            adattatori=adattatori())
    osservato = V._STRUMENTAZIONE["ampiezza_massima"]
    criterio("K27",
             len(entrate) == 25 and 0 < osservato < V._MAX_CANDIDATI,
             "fixture=%d ampiezza_massima=%d tetto=%d"
             % (len(entrate), osservato, V._MAX_CANDIDATI))


# --- E2 rev3 §7 — 11 [AD] -------------------------------------------------


def _criterio_T1():
    accettati = []
    for campo in CAMPI_RECORD_IN_AMBITO:
        record = _record_conforme()
        record[campo] = str(record[campo]) + "\n"
        if campo != "checksum":
            record = _ricalcola_checksum(record)
        if valida_contratto_record(record) == []:
            accettati.append(campo)
    record = _record_conforme()
    record["errors"][0]["schema_resource_id"] += "\n"
    if valida_contratto_record(_ricalcola_checksum(record)) == []:
        accettati.append("errors[].schema_resource_id")
    criterio("T1", len(CAMPI_RECORD_IN_AMBITO) + 1 == 17 and accettati == [],
             str(accettati))


def _criterio_T2(tmp):
    record = _record_conforme()
    record["errors"][0]["schema_resource_id"] += "\n"
    record = _ricalcola_checksum(record)
    radice = os.path.join(tmp, "r21-T2")
    _scrivi_record_grezzo(radice, record)
    rapporto = R.aggrega(radice, quiescent=True)
    criterio("T2",
             rapporto.records_discovered == 1 and rapporto.records_valid == 0
             and rapporto.records_corrupt == 1 and rapporto.records_counted == 0
             and R.invarianti_aggregato(rapporto) == [],
             str((rapporto.records_discovered, rapporto.records_valid,
                  rapporto.records_corrupt, rapporto.records_counted)))


def _criterio_T3(tmp):
    radice = os.path.join(tmp, "r21-T3")
    for indice, invocazione in enumerate(("inv-x", "inv-x", "inv-x\n")):
        record = _record_conforme(record_id=str(indice) * 32,
                                  invocation_id=invocazione)
        _scrivi_record_grezzo(radice, _ricalcola_checksum(record))
    rapporto = R.aggrega(radice, quiescent=True)
    criterio("T3",
             rapporto.records_discovered == 3 and rapporto.records_valid == 2
             and rapporto.records_corrupt == 1 and rapporto.records_counted == 1
             and rapporto.duplicate_groups == 1
             and R.invarianti_aggregato(rapporto) == [],
             str((rapporto.records_discovered, rapporto.records_valid,
                  rapporto.records_corrupt, rapporto.records_counted)))


def _criterio_T4(tmp):
    record = _record_conforme()
    record["record_id"] = "0" * 32 + "\n"
    record = _ricalcola_checksum(record)
    respinto = valida_contratto_record(record) != []
    radice = os.path.join(tmp, "r21-T4")
    os.makedirs(radice, exist_ok=True)
    with open(os.path.join(radice, "0" * 32 + ".json"), "wb") as fh:
        fh.write(canonical_json(record).encode("utf-8"))
    rapporto = R.aggrega(radice, quiescent=True)
    criterio("T4",
             respinto and rapporto.records_discovered == 1
             and rapporto.records_valid == 0 and rapporto.records_corrupt == 1,
             str((respinto, rapporto.records_discovered, rapporto.records_valid,
                  rapporto.records_corrupt)))


def _criterio_T5():
    """Il confine PUBBLICO e il nucleo di test devono respingere allo stesso modo."""
    sporco = contesto(artifact_id=ARTEFATTO + "\n")
    pubblico = V.esegui_shadow_produzione({}, sporco)
    nucleo = V._esegui_shadow_core({}, sporco,
                                   config=V.ConfigTest(attivo=True, radice_output=None),
                                   adattatori=adattatori())
    def _respinto(ese):
        return (ese.invocation_status is InvocationStatus.INVOCATION_ERROR
                and ese.invocation_error.reason_code
                is InvocationReasonCode.CONTEXT_INVALID
                and ese.invocation_error.field == "artifact_id")
    criterio("T5", _respinto(pubblico) and _respinto(nucleo),
             str((str(pubblico.invocation_status), str(nucleo.invocation_status))))


def _criterio_T6(tmp):
    radice = os.path.join(tmp, "r21-T6")
    ese = V._esegui_shadow_core({}, contesto(schema_ref="pain.schema.json\n"),
                                config=V.ConfigTest(attivo=True, radice_output=radice),
                                adattatori=adattatori())
    scritti = os.listdir(radice) if os.path.isdir(radice) else []
    criterio("T6",
             ese.invocation_status is InvocationStatus.INVOCATION_ERROR
             and ese.invocation_error.reason_code
             is InvocationReasonCode.CONTEXT_INVALID
             and ese.invocation_error.field == "schema_ref"
             and scritti == [],
             str((str(ese.invocation_status), scritti)))


def _criterio_T8a():
    finale = "0" * 32 + ".json"
    pendente = "0" * 32 + "." + "0" * 8 + ".pending"
    from ds_validate_contract import FINAL_NAME, PENDING_NAME  # noqa: PLC0415
    criterio("T8a",
             FINAL_NAME.match(finale) is not None
             and PENDING_NAME.match(pendente) is not None
             and FINAL_NAME.match(finale + "\n") is None
             and PENDING_NAME.match(pendente + "\n") is None,
             "le grammatiche di nome file devono distinguere il newline finale")


def _criterio_T8b(tmp):
    """`DEC-14` — su Windows un nome con newline non e' creabile (`OSError`).

    Non e' un verde implicito e non e' un'omissione: l'esito e' dichiarato
    `NOT_APPLICABLE_WINDOWS` e contato a parte. Su POSIX il criterio ESEGUE.
    """
    if os.name == "nt":
        criterio_non_applicabile(
            "T8b", "nome di file con newline finale non creabile su Windows (OSError)")
        return
    radice = os.path.join(tmp, "r21-T8b")
    os.makedirs(radice, exist_ok=True)
    with open(os.path.join(radice, "0" * 32 + ".json\n"), "wb") as fh:
        fh.write(b"{}")
    rapporto = R.aggrega(radice, quiescent=True)
    criterio("T8b",
             rapporto.records_discovered == 1 and rapporto.records_corrupt == 1
             and rapporto.records_valid == 0
             and R.invarianti_aggregato(rapporto) == [],
             str((rapporto.records_discovered, rapporto.records_corrupt)))


#: `E2` rev3 §3.4 — le nove grammatiche COSTANTI in ambito, con un token valido per
#: ciascuna. Il decimo call site e' il letterale in linea di `ds_validate.py`, che
#: solo `D2(b)` trova. La mappa e' indicizzata per TESTO del pattern: una grammatica
#: nuova non vi compare, e `T15` fallisce — che e' il punto del criterio.
TOKEN_VALIDI = {
    r"^[A-Za-z0-9._-]{1,64}\Z": "token-valido",
    r"^[0-9a-f]{8}\Z": "0" * 8,
    r"^[0-9a-f]{32}\Z": "0" * 32,
    r"^[0-9a-f]{64}\Z": "0" * 64,
    r"^[A-Za-z0-9-]{1,48}\.schema\.json\Z": "pain.schema.json",
    r"^[0-9A-Za-z.+_-]{1,32}\Z": "1.0.0",
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z": "2026-08-09T00:00:00.000000Z",
    r"^[0-9a-f]{32}\.json\Z": "0" * 32 + ".json",
    r"^[0-9a-f]{32}\.[0-9a-f]{8}\.pending\Z": "0" * 32 + "." + "0" * 8 + ".pending",
}

#: Fuori ambito per `E2` rev3 §4.1: `CONTROL_CHARS` e' una RICERCA, non un'ancora, e
#: `_RX_REQUIRED` analizza un messaggio di `jsonschema`, non un token persistito.
PATTERN_FUORI_AMBITO = (r"[\x00-\x1f\x7f]",
                        r"^'(?P<p>.+)' is a required property$")

MODULI_D2 = ("ds_validate.py", "ds_validate_contract.py", "ds_validate_report.py")


def _pattern_da_vars():
    """`D2(a)` — ogni `re.Pattern` raggiungibile da `vars(modulo)`, dedup per `id()`."""
    import re as _re  # noqa: PLC0415
    visti, fuori = set(), []
    for modulo in (V, __import__("ds_validate_contract"), R):
        for valore in vars(modulo).values():
            if isinstance(valore, _re.Pattern) and id(valore) not in visti:
                visti.add(id(valore))
                fuori.append(valore)
    return fuori


def _letterali_da_ast():
    """`D2(b)` — ogni `re.<f>(<letterale>, …)` nell'`ast` dei sorgenti.

    Cattura le regex mai legate a un nome: e' l'estrattore che trova il letterale in
    linea sul confine pubblico, che `D2(a)` da solo perderebbe.
    """
    fuori = []
    for nome in MODULI_D2:
        with open(os.path.join(QUI, nome), "rb") as fh:
            albero = ast.parse(fh.read().decode("utf-8"))
        for nodo in ast.walk(albero):
            if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
                    and isinstance(nodo.func.value, ast.Name)
                    and nodo.func.value.id == "re" and nodo.args
                    and isinstance(nodo.args[0], ast.Constant)
                    and isinstance(nodo.args[0].value, str)):
                fuori.append((nome, nodo.func.attr, nodo.args[0].value))
    return fuori


def _criterio_T15():
    import re as _re  # noqa: PLC0415
    oggetti = _pattern_da_vars()
    testi_a = [p.pattern for p in oggetti
               if p.pattern not in PATTERN_FUORI_AMBITO]
    letterali = _letterali_da_ast()
    in_linea = [(f, fn, t) for f, fn, t in letterali
                if fn != "compile" and t not in PATTERN_FUORI_AMBITO]

    problemi = []
    if len(oggetti) != 11:
        problemi.append("D2(a) ha restituito %d oggetti Pattern, attesi 11" % len(oggetti))
    if len(testi_a) != 9:
        problemi.append("grammatiche costanti in ambito: %d, attese 9" % len(testi_a))
    if len(in_linea) != 1:
        problemi.append("letterali in linea in ambito: %d, atteso 1" % len(in_linea))

    # La PROPRIETA', non la forma: `fullmatch(...)` e `\Z` la soddisfano identicamente.
    for testo in testi_a + [t for _f, _fn, t in in_linea]:
        campione = TOKEN_VALIDI.get(testo)
        if campione is None:
            problemi.append("grammatica in ambito senza campione: " + testo)
            continue
        compilata = _re.compile(testo)
        if compilata.match(campione) is None:
            problemi.append("il token valido non e' accettato: " + testo)
        if compilata.match(campione + "\n") is not None:
            problemi.append("token e token+newline classificati UGUALMENTE: " + testo)
    criterio("T15", problemi == [], str(problemi[:3]))


def _criterio_T16(tmp):
    """La directory di `E2` rev3 §5.1: 6 voci, e la partizione che preserva `AGG:1`."""
    radice = os.path.join(tmp, "r21-T16")
    _scrivi_record_grezzo(radice, _record_conforme(record_id="1" * 32))
    for nome, contenuto in (("1" * 32 + "." + "2" * 8 + ".pending", b"{}"),
                            ("note.txt", b"x"), ("README", b"x"),
                            ("aaaa.json", b"x")):
        with open(os.path.join(radice, nome), "wb") as fh:
            fh.write(contenuto)
    os.makedirs(os.path.join(radice, "sottodirectory"), exist_ok=True)
    rapporto = R.aggrega(radice, quiescent=True)
    criterio("T16",
             rapporto.records_discovered == 4 and rapporto.records_valid == 1
             and rapporto.records_corrupt == 3 and rapporto.records_counted == 1
             and rapporto.pending_files == 1
             and R.invarianti_aggregato(rapporto) == [],
             str((rapporto.records_discovered, rapporto.records_valid,
                  rapporto.records_corrupt, rapporto.records_counted,
                  rapporto.pending_files)))


class _VoceIndeterminabile:
    """Una voce di directory il cui TIPO non e' determinabile: `is_file` solleva."""

    def __init__(self, voce, bersaglio):
        self._voce = voce
        self._bersaglio = bersaglio
        self.name = voce.name
        self.path = voce.path

    def is_file(self, *a, **kw):
        if self.name == self._bersaglio:
            raise OSError("tipo della voce non determinabile")
        return self._voce.is_file(*a, **kw)

    def is_dir(self, *a, **kw):
        return self._voce.is_dir(*a, **kw)


def _criterio_T18(tmp):
    """LIVELLO DI ACCESSO: **monkeypatching** di `os.scandir`, dichiarato.

    Il caso non e' raggiungibile dal contenuto di una directory: la domanda sul TIPO
    della voce deve sollevare `OSError`. `os.path.isfile` inghiottiva quell'errore e
    restituiva `False`, quindi la voce spariva — silenzio invece di `corrupt`.

    Si sondano ENTRAMBE le superfici con cui il tipo puo' essere chiesto — `os.scandir`
    e `os.path.isfile` — perche' il criterio riguarda la voce indeterminabile, non
    l'API scelta per interrogarla: sondarne una sola lo renderebbe verde per il solo
    fatto che l'implementazione usa l'altra.
    """
    # La voce indeterminabile porta un record VALIDO. E' cio' che rende il criterio
    # falsificabile: se la domanda sul tipo viene ignorata, il file viene aperto e
    # contato `valid`, e l'asserzione cade. Con un contenuto gia' corrotto il criterio
    # sarebbe verde per la ragione sbagliata.
    radice = os.path.join(tmp, "r21-T18")
    _scrivi_record_grezzo(radice, _record_conforme(record_id="3" * 32))
    rotto = "4" * 32 + ".json"
    _scrivi_record_grezzo(radice, _record_conforme(record_id="4" * 32))

    vero_scandir = os.scandir
    vero_isfile = os.path.isfile

    def _isfile(percorso):
        if os.path.basename(percorso) == rotto:
            raise OSError("tipo della voce non determinabile")
        return vero_isfile(percorso)

    class _Scandir:
        def __init__(self, it):
            self._it = it

        def __enter__(self):
            return self

        def __exit__(self, *a):
            try:
                self._it.__exit__(*a)
            except Exception:  # noqa: BLE001
                pass
            return False

        def __iter__(self):
            for voce in self._it:
                yield _VoceIndeterminabile(voce, rotto)

    os.scandir = lambda percorso: _Scandir(vero_scandir(percorso))
    os.path.isfile = _isfile
    try:
        rapporto = R.aggrega(radice, quiescent=True)
    finally:
        os.scandir = vero_scandir
        os.path.isfile = vero_isfile
    criterio("T18",
             rapporto.records_discovered == 2 and rapporto.records_valid == 1
             and rapporto.records_corrupt == 1 and rapporto.records_counted == 1
             and R.invarianti_aggregato(rapporto) == [],
             str((rapporto.records_discovered, rapporto.records_valid,
                  rapporto.records_corrupt, rapporto.records_counted)))


def sezione_r21_criteri(tmp):
    _criterio_K5(tmp)
    _criterio_K6(tmp)
    _criterio_K7(tmp)
    _criterio_K8a(tmp)
    _criterio_K9(tmp)
    _criterio_K10(tmp)
    _criterio_K10b(tmp)
    _criterio_K10e(tmp)
    _criteri_K13_K14(tmp)
    _criterio_K15(tmp)
    _criterio_K25(tmp)
    _criterio_K27()
    _criterio_T1()
    _criterio_T2(tmp)
    _criterio_T3(tmp)
    _criterio_T4(tmp)
    _criterio_T5()
    _criterio_T6(tmp)
    _criterio_T8a()
    _criterio_T8b(tmp)
    _criterio_T15()
    _criterio_T16(tmp)
    _criterio_T18(tmp)


def sezione_contabilita_criteri(corpus_eseguibile=True):
    """L'asserzione META di `DEC-14`: i tre contatori SOMMANO ai registrati.

    Senza questa riga `DEC-14` sarebbe una convenzione di stampa; con essa e' un
    vincolo che la suite impone a se stessa, e fallisce se qualcuno lascia cadere un
    criterio registrato in silenzio.
    """
    registrati = list(CRITERI_REGISTRATI)
    check("R2.1-6 i criteri registrati sono 24, senza ripetizioni",
          len(registrati) == 24 and len(set(registrati)) == 24, str(len(registrati)))
    mancanti = [n for n in registrati if n not in CRITERI_ESITI]
    estranei = [n for n in CRITERI_ESITI if n not in registrati]
    check("R2.1-6 ogni criterio registrato ha un esito, e nessun esito e' estraneo",
          mancanti == [] and estranei == [], str((mancanti, estranei)))
    conteggi = {stato: sum(1 for v in CRITERI_ESITI.values() if v == stato)
                for stato in ("passed", "failed", "not_applicable")}
    check("R2.1-6 DEC-14: passed + failed + not_applicable == registrati",
          sum(conteggi.values()) == len(registrati),
          str(conteggi) + " != " + str(len(registrati)))
    na = sorted(n for n, v in CRITERI_ESITI.items() if v == "not_applicable")
    if corpus_eseguibile:
        check("R2.1-6 DEC-14: la guardia di piattaforma vale per T8b e per nient'altro",
              na == (["T8b"] if os.name == "nt" else []), str(na))
    else:
        # Il blocco e' ATOMICO anche qui. Senza corpus i ventiquattro criteri non
        # sono stati valutati, e devono risultare non applicabili TUTTI: un
        # sottoinsieme significherebbe che qualcuno e' stato valutato lo stesso,
        # o che qualcuno e' caduto in silenzio senza esito. La guardia di
        # piattaforma non si sovrappone: `T8b` e' dentro i ventiquattro.
        check("R2.1-6 DEC-14: senza corpus i criteri sono non applicabili IN BLOCCO, "
              "tutti e nessuno di meno",
              na == sorted(CRITERI_REGISTRATI), str(na))
    return conteggi


# ==========================================================================
# R2.1-1 … R2.1-5 — il test proprio di ciascun punto
#
# Sono i casi che il packet nomina uno per uno, con l'esito misurato sul commit
# contro cui devono fallire. Non sono i 24 criteri differiti: quelli stanno sopra e
# hanno la loro contabilita'. Qui c'e' cio' che i cinque difetti hanno rotto.
# ==========================================================================


def _punto_R21_1(tmp):
    """`A2` — `$schema` di tipo non stringa. Nessun hash prima del tipo.

    Su `e508b0b` un `$schema` lista o oggetto faceva uscire un `TypeError` non
    catturato da alcuna superficie pubblica: `in` su un `frozenset` chiama `hash()`.
    Il padre classificava gli stessi input `SCHEMA_META_INVALID`, quindi il commit
    peggiorava un caso mentre ne correggeva quattro.
    """
    non_stringa = [("lista", ["x"]), ("oggetto", {"a": 1}), ("numero", 7),
                   ("nullo", None), ("booleano", True),
                   ("annidato", {"$ref": "core.schema.json"})]
    stringhe_non_supportate = [
        ("uri-inventato", "https://esempio.invalido/dialetto"),
        ("2019-09", "https://json-schema.org/draft/2019-09/schema"),
        ("draft-07", "http://json-schema.org/draft-07/schema#"),
    ]
    def _codici(gruppo, prefisso):
        fuori = {}
        for etichetta, valore in gruppo:
            documento = {"$id": "pain.schema.json", "$schema": valore,
                         "type": "object"}
            schemi = _registro(tmp, prefisso + etichetta,
                               {"pain.schema.json": json.dumps(documento).encode("utf-8")})
            try:
                fuori[etichetta] = _esito(schemi, {}).reason_code
            except Exception as exc:  # noqa: BLE001 — e' esattamente cio' che si nega
                fuori[etichetta] = "ECCEZIONE:" + type(exc).__name__
        return fuori

    # I due gruppi restano separati perche' discriminano contro commit diversi: il
    # primo e' la regressione del commit, il secondo e' cio' che il commit ha corretto
    # e che non deve tornare indietro.
    esiti = _codici(non_stringa, "p1-t-")
    check("R2.1-1 nessuna operazione di hash prima del tipo: ogni `$schema` non "
          "stringa -> SCHEMA_META_INVALID, nessuna eccezione in uscita",
          all(c is ReasonCode.SCHEMA_META_INVALID for c in esiti.values()),
          str({k: str(v) for k, v in esiti.items()}))
    esiti = _codici(stringhe_non_supportate, "p1-s-")
    check("R2.1-1 e ogni dialetto dichiarato ma non supportato resta "
          "SCHEMA_META_INVALID",
          all(c is ReasonCode.SCHEMA_META_INVALID for c in esiti.values()),
          str({k: str(v) for k, v in esiti.items()}))

    # E l'assenza di `$schema` resta l'esito di sempre: `F4` prima di `F5`.
    documento = {"$id": "pain.schema.json", "type": "object"}
    schemi = _registro(tmp, "p1-assente",
                       {"pain.schema.json": json.dumps(documento).encode("utf-8")})
    check("R2.1-1 `$schema` assente su una risorsa registrata -> SCHEMA_META_INVALID",
          _esito(schemi, {}).reason_code is ReasonCode.SCHEMA_META_INVALID)


#: `R2.1-2` — la costruzione della clausola sul binding: due risorse con
#: `$dynamicAnchor` OMONIMO. Il binding di runtime usa quello di `pain`; la
#: risoluzione STATICA di `#T` da `lib` porterebbe a `lib`. Un test che accettasse
#: `lib.schema.json` certificherebbe il difetto.
def _lib_pain(tipo_lib, sotto_chiave=True):
    interno = {"$dynamicRef": "#T"}
    corpo = ({"type": "object", "properties": {"k": interno}} if sotto_chiave
             else {"type": "array", "items": interno})
    lib = json.dumps({"$id": "lib.schema.json", "$schema": DIALETTO,
                      "$defs": {"L": corpo,
                                "T": {"$dynamicAnchor": "T", "type": tipo_lib}}}
                     ).encode("utf-8")
    pain = _doc({"$ref": "lib.schema.json#/$defs/L",
                 "$defs": {"T": {"$dynamicAnchor": "T", "type": "string"}}})
    return lib, pain


def _punto_R21_2(tmp):
    """`A1` — provenance di `$dynamicRef`, e `DEC-12` sui due assi indipendenti."""
    # (a) `S10`: la posizione d'ISTANZA non e' toccata dal degrado di schema, e
    # l'attribuzione ora esiste. La controprova `S11` con `$ref` da le stesse
    # posizioni: e' cio' che dimostra che la differenza era la parola chiave.
    s10 = _doc({"type": "object", "properties": {"k": {"$dynamicRef": "#T"}},
                "$defs": {"T": {"$dynamicAnchor": "T", "type": "string"}}})
    s11 = _doc({"type": "object", "properties": {"k": {"$ref": "#/$defs/T"}},
                "$defs": {"T": {"type": "string"}}})
    schemi_s10 = _registro(tmp, "p2-s10", {"pain.schema.json": s10})
    documenti = _documenti_di(schemi_s10)
    r10 = _esito(schemi_s10, {"k": 1})
    r11 = _esito(_registro(tmp, "p2-s11", {"pain.schema.json": s11}), {"k": 1})
    posizioni = [(v.instance_pointer, v.instance_location, v.location_is_exact)
                 for v in (r10.errors + r11.errors)]
    check("R2.1-2 `S10` con `$dynamicRef`: FULL_FAIL, e la posizione d'istanza e' "
          "quella della controprova `S11` con `$ref` — DEC-12",
          r10.full_outcome is FullOutcome.FULL_FAIL and len(r10.errors) == 1
          and len(r11.errors) == 1 and posizioni == [("/k", "/k", True)] * 2,
          str((str(r10.full_outcome), posizioni)))
    check("R2.1-2 `S10` attribuisce la parola chiave alla risorsa che la contiene",
          len(r10.errors) == 1 and _coerenza_voce(r10.errors[0], documenti) == "",
          "" if not r10.errors else _coerenza_voce(r10.errors[0], documenti))

    # (b) `Sigma1`: risorsa e puntatore devono indirizzare il nodo reale.
    schemi_s1 = _registro(tmp, "p2-sigma1", {"pain.schema.json": _doc(SIGMA1)})
    documenti_s1 = _documenti_di(schemi_s1)
    r1 = _esito(schemi_s1, [1])
    check("R2.1-2 `Sigma1`: schema_resource_id e schema_pointer indirizzano davvero "
          "la parola chiave che ha fallito",
          len(r1.errors) == 1 and _coerenza_voce(r1.errors[0], documenti_s1) == "",
          "" if not r1.errors else _coerenza_voce(r1.errors[0], documenti_s1))

    # (c) cross-risorsa DISAMBIGUABILE: il binding di runtime vive in `pain`, e la
    # risoluzione statica porterebbe a `lib`. L'atteso non e' «un'attribuzione»: e'
    # che la capacita' NON attribuisca a `lib.schema.json`.
    lib, pain = _lib_pain("number", sotto_chiave=False)
    r = _esito(_registro(tmp, "p2-cross-diverso", {"pain.schema.json": pain},
                         aggiunte={"lib.schema.json": lib}), [1])
    attribuite = [v.schema_resource_id for v in r.errors]
    check("R2.1-2 cross-risorsa: mai attribuito a `lib.schema.json`, la risorsa che "
          "solo la risoluzione STATICA indicherebbe",
          r.full_outcome is FullOutcome.FULL_FAIL and len(r.errors) == 1
          and "lib.schema.json" not in attribuite,
          str(attribuite))
    check("R2.1-2 cross-risorsa: attribuito alla risorsa del binding di RUNTIME, con "
          "il puntatore che vi indirizza",
          len(r.errors) == 1 and r.errors[0].schema_resource_id == "pain.schema.json"
          and r.errors[0].schema_pointer == "/$defs/T/type",
          str([(v.schema_resource_id, v.schema_pointer) for v in r.errors]))

    # (d) cross-risorsa NON dimostrabile: due binding compatibili col
    # `validator_value`. Si degrada per `DEC-8` — e per `DEC-12` la posizione
    # d'istanza resta esatta. E' il residuo che il packet nomina.
    lib, pain = _lib_pain("string", sotto_chiave=True)
    r = _esito(_registro(tmp, "p2-cross-ambiguo", {"pain.schema.json": pain},
                         aggiunte={"lib.schema.json": lib}), {"k": 1})
    v = r.errors[0] if len(r.errors) == 1 else None
    check("R2.1-2 binding non dimostrabile -> si degrada, non si indovina: "
          "schema_pointer vuoto, instance_pointer `/k`, location_is_exact vera",
          r.full_outcome is FullOutcome.FULL_FAIL and v is not None
          and v.schema_resource_id != "lib.schema.json" and v.schema_pointer == ""
          and v.instance_pointer == "/k" and v.instance_location == "/k"
          and v.location_is_exact is True,
          str(None if v is None else (v.schema_resource_id, v.schema_pointer,
                                      v.instance_pointer, v.location_is_exact)))
    check("R2.1-2 e il record degradato resta CONFORME al contratto",
          v is not None and V.voce_conforme(v))


def _punto_R21_3(tmp):
    """`A3a` — `contentSchema`, e sbagliava in ENTRAMBE le direzioni."""
    c20 = _doc({"type": "object",
                "properties": {"b": {"type": "string",
                                     "contentSchema": {"$ref": "#/$defs/NONESISTE"}}}})
    schemi = _registro(tmp, "p3-c20", {"pain.schema.json": c20})
    codici = [_esito(schemi, ist).reason_code for ist in ({}, {"b": 1}, {"b": "x"})]
    check("R2.1-3 `C20` un `$ref` irrisolvibile sotto `contentSchema` non si nasconde "
          "piu': REF_UNRESOLVABLE, indipendente dall'istanza",
          all(c is ReasonCode.REF_UNRESOLVABLE for c in codici),
          str([str(c) for c in codici]))

    c21 = _doc({"type": "object",
                "properties": {"b": {"type": "string",
                                     "contentSchema": {"$anchor": "CS",
                                                       "type": "object"}}},
                "items": {"$ref": "#CS"}})
    r = _esito(_registro(tmp, "p3-c21", {"pain.schema.json": c21}), {})
    check("R2.1-3 `C21` un `$anchor` dichiarato dentro `contentSchema` risolve: il "
          "preflight non rifiuta cio' che il resolver del dialetto risolve (R-FID)",
          r.reason_code is not ReasonCode.REF_UNRESOLVABLE, str(r.reason_code))


def _punto_R21_4(tmp):
    """`A3b` — risorse embedded con `$id`: per puntatore e per anchor interno."""
    c22 = _doc({"type": "object", "properties": {"k": {"$ref": "sub.schema.json"}},
                "$defs": {"E": {"$id": "sub.schema.json", "type": "string"}}})
    c23 = _doc({"type": "object", "properties": {"k": {"$ref": "sub.schema.json#B"}},
                "$defs": {"E": {"$id": "sub.schema.json",
                                "$defs": {"B": {"$anchor": "B", "type": "string"}}}}})
    codici = []
    for etichetta, documento in (("c22", c22), ("c23", c23)):
        codici.append(_esito(_registro(tmp, "p4-" + etichetta,
                                       {"pain.schema.json": documento}), {}).reason_code)
    check("R2.1-4 `C22`/`C23` una risorsa embedded risolve, per puntatore e per anchor",
          all(c is not ReasonCode.REF_UNRESOLVABLE for c in codici),
          str([str(c) for c in codici]))

    # Il registro resta CHIUSO: un `$id` embedded che non esiste non si recupera.
    fuori = _doc({"type": "object",
                  "properties": {"k": {"$ref": "mai-registrata.schema.json"}}})
    check("R2.1-4 il registro resta chiuso: un `$id` mai registrato non si recupera",
          _esito(_registro(tmp, "p4-fuori", {"pain.schema.json": fuori}), {}).reason_code
          is ReasonCode.REF_UNRESOLVABLE)

    # Un `$id` embedded che collide con un'identita' registrata resta un errore di
    # identita': due bersagli per lo stesso nome, e nessuno dei due si sceglie in
    # silenzio.
    collide = _doc({"type": "object",
                    "$defs": {"E": {"$id": CORE_SCHEMA_ID, "type": "string"}}})
    check("R2.1-4 un `$id` embedded che collide con un'identita' registrata resta un "
          "errore di identita'",
          _esito(_registro(tmp, "p4-collisione",
                           {"pain.schema.json": collide}), {}).reason_code
          is ReasonCode.SCHEMA_ID_COLLISION)


def _ragione_link_non_creabile(exc) -> str:
    """Ragione DICHIARATA quando la piattaforma non sa creare un link simbolico.

    Isolata dal registro globale perche' sia esercitabile senza sporcarlo: su
    Windows con Developer Mode e su POSIX il link E' creabile, quindi il ramo che
    la usa non viene mai preso e senza questa funzione la sua correttezza non
    sarebbe provata da nulla.

    Non si stampa `str(exc)`: il testo di sistema di `os.symlink` incorpora i due
    percorsi ASSOLUTI, e la riga 6 di questo modulo vieta tanto il percorso
    assoluto quanto il testo di un'eccezione. Il codice numerico non e' ne' l'uno
    ne' l'altro, ma da solo sarebbe ambiguo: `winerror` ed `errno` sono due spazi
    di nomi DISTINTI che collidono — `1` vale `EPERM` come `errno` e non e' un
    `winerror` — quindi la numerazione va NOMINATA. `winerror` ha la precedenza
    perche' sulla stessa eccezione Windows valgono `1314` e `22`, e solo il primo
    dice la causa; il confronto e' con `None` e non per verita', cosi' un codice
    `0` viene riportato invece di scivolare sull'altra numerazione.
    """
    win = getattr(exc, "winerror", None)
    err = getattr(exc, "errno", None)
    if win is not None:
        codice = " winerror " + str(win)
    elif err is not None:
        codice = " errno " + str(err)
    else:
        codice = ""
    return ("link simbolico non creabile su questa piattaforma ("
            + type(exc).__name__ + codice
            + "): il caso resta da eseguire su POSIX")


def _punto_R21_5(tmp, *, symlink=None):
    """`A5`, seconda meta': un link simbolico non e' un file regolare.

    LIVELLO DI ACCESSO: **filesystem reale**, nessun monkeypatching. `T18` copre
    l'altra meta' del punto — la voce il cui TIPO non e' determinabile, che conta
    `corrupt` — e per arrivarci deve sostituire `os.scandir`, perche' quel caso non
    e' costruibile con una directory vera. Questo lo e'.

    La regola applicata e' quella di `E2` rev3 **§5.2**: «le voci che non sono file
    regolari — directory, e link simbolici che non risolvono a un file regolare
    sotto `follow_symlinks=False` — non sono ne' scoperte ne' pending». §5.3 e'
    citata per il solo MECCANISMO (`os.scandir` che fornisce
    `is_file(follow_symlinks=False)` senza `stat` per voce); la sua prosa sui costi
    dice invece che i link simbolici finirebbero `corrupt`, e su questo punto §5.2 e
    §5.3 di `E2` rev3 non concordano. Il codice di produzione implementa §5.2, e
    questo caso asserisce §5.2. La discordanza e' segnalata, non risolta qui.

    Il bersaglio vive FUORI dalla radice aggregata: dentro sarebbe un file regolare
    a pieno titolo, e nessun contatore saprebbe piu' distinguere il link dal proprio
    bersaglio. Resta comunque dentro `tmp`, cosi' la pulizia di `7.15` non ha nulla
    da inseguire fuori dalla directory temporanea. Il nome del link rispetta
    `FINAL_NAME` e il bersaglio e' un record VALIDO: se il link fosse contato come
    file regolare verrebbe aperto, letto e contato `valid`, e l'asserzione cade. Con
    un bersaglio gia' corrotto il caso sarebbe verde per la ragione sbagliata.

    Il record regolare accanto al link e' il TESTIMONE: senza di lui una radice
    illeggibile — `voci = []` — darebbe gli stessi zeri, e il caso passerebbe senza
    aver visto niente. I due record portano `invocation_id` diversi per tenere
    discriminante anche la clausola `records_counted == 1`: a `invocation_id` uguali
    la deduplicazione riassorbe il link nel gruppo del testimone e quel solo numero
    tornerebbe `1` anche con il link seguito (misurato: `2, 2, 0, 1`). Le altre
    clausole cadrebbero comunque, ma un'asserzione le cui parti non discriminano
    tutte e' un'asserzione che si sta gia' sfaldando.

    `symlink` esiste per un solo motivo: permettere a
    `_punto_R21_5_ramo_dichiarato` di esercitare il ramo di rifiuto SENZA
    sostituire `os.symlink` nel modulo `os`. Nella corsa normale resta `None` e
    il punto usa `os.symlink`, quindi la strada di produzione del caso e'
    esattamente quella di prima.
    """
    base = os.path.join(tmp, "r21-5-symlink")
    radice = os.path.join(base, "records")
    deposito = os.path.join(base, "bersaglio")
    _scrivi_record_grezzo(radice, _record_conforme(record_id="6" * 32,
                                                   invocation_id="inv-r215-reg"))
    bersaglio = _scrivi_record_grezzo(
        deposito, _record_conforme(record_id="5" * 32, invocation_id="inv-r215-lnk"))
    collegamento = os.path.join(radice, "5" * 32 + ".json")

    try:
        (symlink if symlink is not None else os.symlink)(bersaglio, collegamento)
    except (OSError, NotImplementedError, AttributeError) as exc:
        # Il `try` avvolge la SOLA creazione del link, e nient'altro: non puo'
        # assorbire il fallimento dell'asserzione, che non e' ancora stata scritta.
        # La discriminante e' la CAPACITA', non la piattaforma: su Windows con
        # Developer Mode attivo il link e' creabile, e una guardia su `os.name`
        # salterebbe un caso perfettamente eseguibile — cioe' mentirebbe.
        NON_ESEGUITI.append(
            ("R2.1-5 link simbolico non contato come file regolare",
             _ragione_link_non_creabile(exc)))
        return

    rapporto = R.aggrega(radice, quiescent=True)
    check("R2.1-5 un link simbolico con nome conforme, che punta a un record "
          "VALIDO fuori radice, non conta come file regolare",
          rapporto.records_discovered == 1 and rapporto.records_valid == 1
          and rapporto.records_corrupt == 0 and rapporto.records_counted == 1
          and rapporto.pending_files == 0
          and R.invarianti_aggregato(rapporto) == [],
          str((rapporto.records_discovered, rapporto.records_valid,
               rapporto.records_corrupt, rapporto.records_counted,
               rapporto.pending_files, R.invarianti_aggregato(rapporto))))


def _punto_R21_5_ramo_dichiarato(tmp):
    """Il ramo «link non creabile» di `_punto_R21_5`, esercitato davvero.

    LIVELLO DI ACCESSO: **iniezione della sola `os.symlink` nel punto**, piu'
    istanze di eccezione sintetiche per l'etichetta. Nessun monkeypatching
    globale — `os.symlink` non viene mai sostituita nel modulo `os` — e nessuna
    alterazione PERSISTENTE dei registri: `NON_ESEGUITI` viene riportato alla
    lunghezza di partenza, e si verifica che `RISULTATI` e `CRITERI_ESITI` non si
    siano mossi.

    Serve perche' su entrambe le piattaforme di gate il link e' creabile: il ramo
    non viene mai preso, il quinto contatore vale `0` in tutti e due i verbali, e
    la sua correttezza resterebbe altrimenti asserita da nulla.

    Le eccezioni sintetiche portano `winerror` per ASSEGNAZIONE su una
    sottoclasse, non per la forma a cinque argomenti di `OSError`: quella e'
    speciale solo su Windows — misurato, su POSIX lascia `winerror` a `None` —
    e renderebbe questo caso divergente fra le due piattaforme.
    """
    class _ConWinerror(OSError):
        pass

    con_win = _ConWinerror(22, "messaggio di sistema")
    con_win.winerror = 1314
    con_win.filename = os.path.join(tmp, "assoluto-a.json")
    con_win.filename2 = os.path.join(tmp, "assoluto-b.json")

    forme = (con_win, OSError(1, "operazione non permessa"), OSError(),
             NotImplementedError(), AttributeError())
    righe = [_ragione_link_non_creabile(e) for e in forme]
    attese = ("_ConWinerror winerror 1314", "PermissionError errno 1", "(OSError)",
              "(NotImplementedError)", "(AttributeError)")
    check("R2.1-5 la ragione del ramo NOMINA la numerazione del codice e degrada "
          "al solo tipo quando non ce n'e' alcuna",
          all(a in r for a, r in zip(attese, righe))
          # nessun percorso assoluto, nessun testo di sistema, in nessuna forma
          and not any(tmp in r or "messaggio di sistema" in r
                      or "operazione non permessa" in r for r in righe),
          str(righe))

    prima_ne, prima_ris = len(NON_ESEGUITI), len(RISULTATI)
    prima_cri = dict(CRITERI_ESITI)

    def _rifiuta(_sorgente, _destinazione):
        raise con_win

    _punto_R21_5(os.path.join(tmp, "ramo"), symlink=_rifiuta)
    aggiunte = NON_ESEGUITI[prima_ne:]
    del NON_ESEGUITI[prima_ne:]          # registro globale RIPRISTINATO

    check("R2.1-5 sul ramo dichiarato non nasce alcun `check`, i criteri non si "
          "muovono, e resta esattamente una voce dichiarata",
          len(aggiunte) == 1
          and aggiunte[0][0] == "R2.1-5 link simbolico non contato come file regolare"
          and "winerror 1314" in aggiunte[0][1]
          and len(RISULTATI) == prima_ris
          and CRITERI_ESITI == prima_cri
          and len(NON_ESEGUITI) == prima_ne,
          str((len(aggiunte), len(RISULTATI) - prima_ris,
               CRITERI_ESITI == prima_cri, len(NON_ESEGUITI) - prima_ne)))


def _punto_E2_5_3_costo(tmp):
    """`E2` rev3 §5.3, blockquote: «una sola passata, nessuna `stat` per voce».

    LIVELLO DI ACCESSO: **contatori sulle superfici `os` di livello Python**,
    installati per la sola durata di UNA aggregazione e rimossi in `finally`.

    PERIMETRO DICHIARATO, e va letto per quello che e'. Si conta cio' che transita
    dal MODULO `os`, **non le syscall**: `DirEntry.is_file()` e' implementata in C
    e non passa da `os.stat`, quindi nessun contatore Python puo' vederla. Il
    conteggio effettivo richiederebbe `strace` — strumento esterno, solo POSIX,
    fuori dal perimetro di `DEC-13`. Questo caso asserisce dunque la SCELTA
    IMPLEMENTATIVA — una enumerazione, nessuna interrogazione per voce dal livello
    Python — e non il costo reale. La meta' di §5.3 sul costo resta aperta.

    La superficie e' pero' interamente enumerabile, ed e' cio' che rende
    l'asserzione non vuota: `_aggrega_con_lettore` usa `os.scandir` una volta,
    `os.path.join` (stringhe) e `open` per i candidati, e nient'altro. La forma
    pre-`R2.1` — `os.listdir` piu' `os.path.isfile` per voce — darebbe
    `scandir 0`, `listdir 1`, `per_voce N`: tutte e tre le clausole cadono.
    """
    radice = os.path.join(tmp, "e2-5-3-costo")
    for cifra in ("1", "2", "3"):
        _scrivi_record_grezzo(radice, _record_conforme(
            record_id=cifra * 32, invocation_id="inv-costo-" + cifra))

    conta = {"scandir": 0, "listdir": 0, "per_voce": 0}
    veri = {"scandir": os.scandir, "listdir": os.listdir, "stat": os.stat,
            "lstat": os.lstat, "isfile": os.path.isfile}

    def _conta(chiave, nome):
        def _avvolta(*a, **k):
            conta[chiave] += 1
            return veri[nome](*a, **k)
        return _avvolta

    os.scandir = _conta("scandir", "scandir")
    os.listdir = _conta("listdir", "listdir")
    os.stat = _conta("per_voce", "stat")
    os.lstat = _conta("per_voce", "lstat")
    os.path.isfile = _conta("per_voce", "isfile")
    try:
        rapporto = R.aggrega(radice, quiescent=True)
    finally:
        # Il ripristino sta in `finally` e non dopo il `try`: se `R.aggrega`
        # sollevasse, le cinque superfici resterebbero avvolte per tutto il resto
        # della corsa — cioe' proprio quell'alterazione PERSISTENTE dei registri
        # globali che questo caso doveva evitare. Il residuo non farebbe rumore:
        # i contatori DELEGANO alle funzioni vere, quindi `shutil.rmtree` e il
        # controllo `7.15` continuerebbero a passare (misurato, col ripristino
        # rimosso, su entrambe le piattaforme di gate). Un residuo che nessun
        # controllo vede e' la ragione per cui il ripristino non puo' essere
        # affidato al cammino felice.
        os.scandir, os.listdir = veri["scandir"], veri["listdir"]
        os.stat, os.lstat = veri["stat"], veri["lstat"]
        os.path.isfile = veri["isfile"]

    check("R2.1-5 `E2` §5.3: una sola enumerazione per aggregazione, e nessuna "
          "interrogazione per voce dal livello Python",
          conta["scandir"] == 1 and conta["listdir"] == 0 and conta["per_voce"] == 0
          and rapporto.records_discovered == 3 and rapporto.records_valid == 3
          and rapporto.records_corrupt == 0
          and R.invarianti_aggregato(rapporto) == [],
          str(conta) + " " + str((rapporto.records_discovered,
                                  rapporto.records_valid, rapporto.records_corrupt)))


def sezione_r21_punti(tmp):
    _punto_R21_1(tmp)
    _punto_R21_2(tmp)
    _punto_R21_3(tmp)
    _punto_R21_4(tmp)
    # `R2.1-5` ha DUE meta', e ciascuna ha la propria prova, separatamente
    # identificabile: il criterio `T18` copre la voce il cui TIPO non e'
    # determinabile -> `corrupt` (monkeypatching di `os.scandir`, perche' con una
    # directory vera quel caso non si costruisce); `_punto_R21_5` copre il link
    # simbolico, che non e' un file regolare (filesystem reale, nessun patch).
    _punto_R21_5(tmp)
    # Il ramo di `_punto_R21_5` che nessuna delle due piattaforme di gate prende,
    # e la meta' di `E2` §5.3 asseribile dal livello Python. Entrambi sono
    # `check` del punto, non criteri: `CRITERI_REGISTRATI` resta a 24.
    _punto_R21_5_ramo_dichiarato(tmp)
    _punto_E2_5_3_costo(tmp)


# ==========================================================================
# main
# ==========================================================================


def main() -> int:
    installa_guardia_socket()

    # UNA invocazione, UNO stato del corpus, UNA valutazione decisionale. QUI, E
    # SOLTANTO QUI, si cattura cio' che la sonda ha lasciato salire: non esiste un
    # `try` attorno alle sezioni, perche' una rete larga trasformerebbe un guasto
    # in un ambiente incompleto e la suite direbbe «non applicabile» dove invece
    # qualcosa si e' rotto.
    try:
        stato, sonda = sonda_corpus()
    except BaseException as exc:                                     # noqa: BLE001
        stato, sonda = TERZO_STATO, None
        diagnostica = type(exc).__name__ + ": " + str(exc)
    else:
        diagnostica = None

    sys.stdout.write("corpus schema: " + stato + "\n")
    if stato == TERZO_STATO:
        # Ci si ferma PRIMA delle sezioni: nessuna viene avviata, e non si
        # dichiara non eseguibile niente, perche' non si e' accertato niente. Un
        # terzo stato non e' un ambiente incompleto: e' un guasto, e si presenta
        # nominato invece che come traceback.
        sys.stdout.write(
            "FALLITO  sonda del corpus: terzo stato  ["
            + (diagnostica or "la sonda non ha dato uno dei due stati ammessi")
            + "]\nnessuna sezione avviata\n")
        return 1

    corpus = stato == CORPUS_ESEGUIBILE
    if not corpus:
        # UNA riga sola, e ha un osservabile MISURATO: un classificatore che assorbe un
        # terzo stato dentro «corpus assente» la fa cadere. Non e' ridondanza con il
        # `7.5 DEPENDENCY_MISSING via importer iniettato`, che dimostra il fail-closed con
        # un'iniezione: qui il fail-closed si legge sull'AMBIENTE REALE, ed e' proprio il
        # verso che l'iniezione non puo' testimoniare.
        #
        # Il verso opposto — «corpus presente» — NON ha una riga corrispondente, e non ne ha
        # una perche' non ha un osservabile: misurato che un classificatore il quale dichiari
        # eseguibile cio' che non lo e' fa morire la corsa con un'eccezione PRIMA del
        # riepilogo, e nessun `check` arriva mai a essere stampato. Il segnale resta l'exit
        # diverso da zero, senza rossa nominata. Il limite e' registrato e non e' colmato da
        # un'asserzione che ripeta il predicato del ramo: quella sarebbe un verde fabbricato.
        check("corpus assente: fail-closed DEPENDENCY_MISSING dall'ambiente reale, "
              "nessuna versione dichiarata",
              sonda.full_outcome is FullOutcome.VALIDATOR_ERROR
              and sonda.reason_code is ReasonCode.DEPENDENCY_MISSING
              and sonda.validator_version is None
              and sonda.referencing_version is None)

    testi = []
    with tempfile.TemporaryDirectory(prefix="ds-shadow-") as tmp:
        sezione_7_1()
        sezione_7_2(tmp)
        sezione_7_3()
        if corpus:
            codici = sezione_fixture(tmp)
        else:
            codici = set()
            non_eseguito_per_corpus("sezione_fixture")
        sezione_7_5(tmp, codici, corpus)
        if corpus:
            sezione_7_6()
            sezione_7_7(tmp)
            sezione_7_8(tmp)
            sezione_7_9_7_10(tmp)
            sezione_a1(tmp)
        else:
            for nome in ("sezione_7_6", "sezione_7_7", "sezione_7_8",
                         "sezione_7_9_7_10", "sezione_a1"):
                non_eseguito_per_corpus(nome)
        sezione_a2(tmp)
        if corpus:
            sezione_a4(tmp)
            sezione_a5(tmp)
            sezione_p0_3_p0_4(tmp)
            sezione_r21_criteri(tmp)
            sezione_r21_punti(tmp)
        else:
            for nome in ("sezione_a4", "sezione_a5", "sezione_p0_3_p0_4",
                         "sezione_r21_criteri", "sezione_r21_punti"):
                non_eseguito_per_corpus(nome)
            for nome in CRITERI_REGISTRATI:
                criterio_fuori_corpus(nome)
        sezione_concorrenza(tmp)
        uscita_aggregatore = sezione_7_11(tmp)
        testi.append(uscita_aggregatore)
        if corpus:
            sezione_7_12()
        else:
            non_eseguito_per_corpus("sezione_7_12")
        sezione_7_14()
        # `7.13` scrive un record VERO e poi lo perquisisce: senza corpus il record
        # nasce da un `VALIDATOR_ERROR`, e il rifiuto di contenimento che la sezione
        # pretende non puo' nemmeno essere raggiunto — la dipendenza si dichiara
        # prima della lettura degli schemi. E' corpus, e va nel blocco con gli altri.
        if corpus:
            sezione_7_13(tmp, testi)
        else:
            non_eseguito_per_corpus("sezione_7_13")
        rimosso = tmp
    digest = sezione_7_15()
    check("7.15 la TemporaryDirectory e' rimossa dopo la corsa", not os.path.exists(rimosso))
    conteggi = sezione_contabilita_criteri(corpus)

    # Le due guardie del blocco, entrambe misurate su questa corsa.
    saltate = sorted(n for n, r in NON_ESEGUITI if r == MOTIVO_CORPUS)
    check("il corpus e' un blocco: con capability presente non si salta nulla per esso",
          not (corpus and saltate), str(saltate))
    check("il corpus e' un blocco: con capability assente si saltano ESATTAMENTE le sue sezioni, "
          "tutte e nessuna di piu'",
          corpus or saltate == sorted(SEZIONI_DEL_CORPUS), str(saltate))
    falliti = [(n, d) for n, ok, d in RISULTATI if not ok]
    for n, d in falliti:
        sys.stdout.write("FALLITO  " + n + ("  [" + d + "]" if d else "") + "\n")
    for n, ragione in NON_APPLICABILI:
        sys.stdout.write("NOT_APPLICABLE_WINDOWS  " + n + "  [" + ragione + "]\n")
    for n, ragione in NON_ESEGUITI:
        sys.stdout.write("NON ESEGUITO  " + n + "  [" + ragione + "]\n")
    # `DEC-14` — i contatori SOMMANO al totale: una riga con `superati` e
    # `falliti` soltanto e' ambigua per costruzione appena esiste un esito non
    # applicabile, ed e' precisamente cio' che `DEC-14` vieta. Per la stessa
    # ragione un caso non ESEGUITO entra nel totale invece di sparirne: stamparlo
    # senza contarlo lascerebbe la somma piu' corta del vero, che e' il vuoto che
    # `DEC-14` vieta — nella forma piu' insidiosa, perche' la riga sembra tornare.
    totale = len(RISULTATI) + len(NON_APPLICABILI) + len(NON_ESEGUITI)
    sys.stdout.write(
        "controlli: %d totali, %d superati, %d falliti, %d non applicabili, "
        "%d non eseguiti\n"
        % (totale, len(RISULTATI) - len(falliti), len(falliti),
           len(NON_APPLICABILI), len(NON_ESEGUITI))
    )
    sys.stdout.write(
        "criteri differiti: %d registrati, %d passati, %d falliti, %d not_applicable\n"
        % (len(CRITERI_REGISTRATI), conteggi["passed"], conteggi["failed"],
           conteggi["not_applicable"])
    )
    sys.stdout.write("tools/ds_call.py sha256: " + digest + "\n")
    sys.stdout.write("chiamate di rete: %d\n" % CHIAMATE_RETE["n"])
    return 0 if not falliti else 1


if __name__ == "__main__":
    raise SystemExit(main())
