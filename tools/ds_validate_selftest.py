#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEEPSEARCH — runner deterministico dei fixture e criteri di accettazione TP-M0.1-001 §7.

E' il CHIAMANTE del nucleo privato e l'unico chiamante di `_valida_sottoschema_test`.
Non stampa mai un percorso assoluto, un valore di istanza o il testo di un'eccezione.

Uso:  python tools/ds_validate_selftest.py
"""

from __future__ import annotations

import ast
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


def check(nome, condizione, dettaglio=""):
    RISULTATI.append((nome, bool(condizione), dettaglio if not condizione else ""))


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


def esegui_schema_rotto(tmp, percorso):
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
    # istanza con `payload` presente: senza di essa un $ref sotto `properties.payload`
    # non verrebbe mai seguito e il caso negativo non sarebbe discriminante.
    e = V._esegui_shadow_core({"payload": {}}, contesto(schema_ref=rif),
                              config=V.ConfigTest(attivo=True, radice_output=None),
                              adattatori=adattatori(radice))
    return e.validation_result


# ==========================================================================
# 7.5 — codici di ragione e di scrittura
# ==========================================================================


def _codice_scrittura_produzione():
    return V.esegui_shadow_produzione({}, contesto()).write_receipt.write_reason_code


def sezione_7_5(tmp, codici_visti):
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

    codice = V.passo_dimensione(b"x" * (MAX_RECORD_BYTES + 1))
    visti.add(codice)
    check("7.5 RECORD_TOO_LARGE (fail-closed di riserva)",
          codice is WriteReasonCode.RECORD_TOO_LARGE
          and V.passo_dimensione(b"x" * MAX_RECORD_BYTES) is None)

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
    check("7.14 il manifest contiene esattamente i 50 percorsi allowlistati",
          len(percorsi) == 50 and percorsi == sorted(su_disco),
          str(set(percorsi) ^ set(su_disco)))
    check("7.14 contratto del manifest rispettato", valida_manifest(manifesto) == [],
          str(valida_manifest(manifesto)))
    from collections import Counter
    conteggi = Counter(v["kind"] for v in manifesto["entries"])
    check("7.14 totali per kind 22/19/8/1",
          conteggi == {"validation_instance": 22, "subschema_instance": 19,
                       "schema_fixture": 8, "test_data": 1}, str(dict(conteggi)))
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
# main
# ==========================================================================


def main() -> int:
    installa_guardia_socket()
    testi = []
    with tempfile.TemporaryDirectory(prefix="ds-shadow-") as tmp:
        sezione_7_1()
        sezione_7_2(tmp)
        sezione_7_3()
        codici = sezione_fixture(tmp)
        sezione_7_5(tmp, codici)
        sezione_7_6()
        sezione_7_7(tmp)
        sezione_7_8(tmp)
        sezione_7_9_7_10(tmp)
        sezione_a1(tmp)
        sezione_a2(tmp)
        sezione_concorrenza(tmp)
        uscita_aggregatore = sezione_7_11(tmp)
        testi.append(uscita_aggregatore)
        sezione_7_12()
        sezione_7_14()
        sezione_7_13(tmp, testi)
        rimosso = tmp
    digest = sezione_7_15()
    check("7.15 la TemporaryDirectory e' rimossa dopo la corsa", not os.path.exists(rimosso))

    falliti = [(n, d) for n, ok, d in RISULTATI if not ok]
    for n, d in falliti:
        sys.stdout.write("FALLITO  " + n + ("  [" + d + "]" if d else "") + "\n")
    sys.stdout.write(
        "controlli: %d totali, %d superati, %d falliti\n"
        % (len(RISULTATI), len(RISULTATI) - len(falliti), len(falliti))
    )
    sys.stdout.write("tools/ds_call.py sha256: " + digest + "\n")
    sys.stdout.write("chiamate di rete: %d\n" % CHIAMATE_RETE["n"])
    return 0 if not falliti else 1


if __name__ == "__main__":
    raise SystemExit(main())
