#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEEPSEARCH — contratto della capacita' di validazione SHADOW (ADR-M0.1-001 rev9 §4).

Questo modulo contiene SOLO dati e funzioni deterministiche: costanti (§4.1),
tassonomie chiuse (§4.2), strutture di risultato (§4.3, §4.6, §4.11, §4.13),
helper di JSON canonico e checksum (§4.11) e il validatore di contratto del
record usato dall'aggregatore (§4.13).

Non importa nulla di opzionale, non tocca il filesystem, non apre socket.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------
# §4.1 — Costanti
# --------------------------------------------------------------------------

RECORD_VERSION = "1.0.0"
MAX_ERRORS = 50  # NON sovrascrivibile dal chiamante
MAX_LOCATION_BYTES = 512
MAX_MISSING_PROPERTY_BYTES = 128
MAX_VERSION_BYTES = 32
MAX_KEYWORD_BYTES = 32
MAX_RECORD_BYTES = 131072

#: E2 rev3 §4.1 — ancoraggio con `\Z`, MAI con `$`. In Python `$` accetta un newline
#: finale: `re.match(r"^[0-9a-f]{64}$", "a"*64 + "\n")` e' un match. Undici metadati
#: del record lo portavano fino al disco e l'aggregatore li contava; e poiche'
#: `invocation_id` e' meta' della chiave di deduplicazione, un newline sdoppiava
#: un'invocazione gia' contata, con tutte le invarianti `AGG` pulite.
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,64}\Z")
HEX8 = re.compile(r"^[0-9a-f]{8}\Z")
HEX32 = re.compile(r"^[0-9a-f]{32}\Z")
HEX64 = re.compile(r"^[0-9a-f]{64}\Z")
SCHEMA_REF = re.compile(r"^[A-Za-z0-9-]{1,48}\.schema\.json\Z")
VERSION_TOKEN = re.compile(r"^[0-9A-Za-z.+_-]{1,32}\Z")
TIMESTAMP_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")

REDACTED_SEGMENT = "~R"

#: Limite RAPPRESENTAZIONALE (int64 non negativo). NON e' un timeout: M0.1 non
#: implementa alcun timeout e nessuna validazione viene mai interrotta per durata.
DURATION_MS_MAX = 9223372036854775807

MAX_RECORD_VERSION_BYTES = 16
TIMESTAMP_UTC_LEN = 27

#: §4.1 — insieme chiuso dei 12 schemi-artefatto che possono fare da radice.
#: `core.schema.json` e' deliberatamente assente: risorsa registrata, non radice.
ARTIFACT_ROOTS = (
    "bootstrap-verdict.schema.json",
    "competitor-report.schema.json",
    "evidence-pack.schema.json",
    "gate-decision.schema.json",
    "market-report.schema.json",
    "pain.schema.json",
    "redteam-report.schema.json",
    "scorecard.schema.json",
    "seen-pain.schema.json",
    "trigger-report.schema.json",
    "venture-blueprint.schema.json",
    "verification-report.schema.json",
)

CORE_SCHEMA_ID = "core.schema.json"

MODE_SHADOW = "SHADOW"

FINAL_NAME = re.compile(r"^[0-9a-f]{32}\.json\Z")
PENDING_NAME = re.compile(r"^[0-9a-f]{32}\.[0-9a-f]{8}\.pending\Z")

#: E2 rev3 §4.2 — token persistiti, o usati per identita', deduplicazione e nomi di
#: file. Nessuno puo' contenere un carattere di controllo. Oggi la classe di ogni
#: grammatica li esclude gia' e `\Z` chiude il newline finale, quindi questo rifiuto e'
#: **ridondante**: va messo lo stesso, perche' e' l'unica difesa indipendente dalla
#: forma dell'ancoraggio, ed e' cio' che tiene se una classe si allarga domani. Senza
#: questa nota un lettore futuro lo troverebbe ridondante, avrebbe ragione, e lo
#: toglierebbe.
TOKEN_PERSISTITI = (
    "record_version", "record_id", "invocation_id", "run_id", "role", "stage",
    "artifact_id", "mode", "timestamp_utc", "schema_ref", "schema_sha256",
    "registry_fingerprint", "python_version", "validator_version",
    "referencing_version", "checksum",
)


# --------------------------------------------------------------------------
# §4.2 — Tassonomie chiuse
# --------------------------------------------------------------------------


class InvocationStatus(Enum):
    OK = "OK"
    INVOCATION_ERROR = "INVOCATION_ERROR"


class LegacyOutcome(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


class FullOutcome(Enum):
    FULL_PASS = "FULL_PASS"
    FULL_FAIL = "FULL_FAIL"
    VALIDATOR_ERROR = "VALIDATOR_ERROR"
    NOT_EVALUATED = "NOT_EVALUATED"


class Comparison(Enum):
    AGREE_PASS = "AGREE_PASS"
    AGREE_FAIL = "AGREE_FAIL"
    LEGACY_ACCEPT_FULL_FAIL = "LEGACY_ACCEPT_FULL_FAIL"
    LEGACY_REJECT_FULL_PASS = "LEGACY_REJECT_FULL_PASS"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class WriteStatus(Enum):
    WRITTEN = "WRITTEN"
    WRITE_ERROR = "WRITE_ERROR"
    COLLISION = "COLLISION"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class CleanupStatus(Enum):
    """Chiuso, ESATTAMENTE DUE valori (A2 §3).

    Vive SOLO nella ricevuta, mai nel record. Una volta che `os.link` e' riuscito il
    record e' immutabile: scrivergli dentro l'esito della pulizia imporrebbe di
    ricalcolarne il checksum, distruggendo l'atomicita' che il percorso di scrittura
    esiste per fornire. Pubblicazione e pulizia sono due assi, e confonderli e'
    esattamente il difetto P0-2.
    """

    CLEANED = "CLEANED"
    PENDING_CLEANUP = "PENDING_CLEANUP"


class Completeness(Enum):
    COMPLETE = "COMPLETE"
    UNKNOWN = "UNKNOWN"


class InvocationReasonCode(Enum):
    """Chiuso, 2 valori."""

    CONTEXT_INVALID = "CONTEXT_INVALID"
    CONFIG_INVALID = "CONFIG_INVALID"


class ReasonCode(Enum):
    """Chiuso, 9 valori. I primi 7 implicano VALIDATOR_ERROR, gli ultimi 2 NOT_EVALUATED."""

    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    SCHEMA_UNREADABLE = "SCHEMA_UNREADABLE"
    SCHEMA_META_INVALID = "SCHEMA_META_INVALID"
    SCHEMA_ID_COLLISION = "SCHEMA_ID_COLLISION"
    SCHEMA_ROOT_OUTSIDE_REGISTRY = "SCHEMA_ROOT_OUTSIDE_REGISTRY"
    REF_UNRESOLVABLE = "REF_UNRESOLVABLE"
    INTERNAL_VALIDATION_ERROR = "INTERNAL_VALIDATION_ERROR"
    SHADOW_DISABLED = "SHADOW_DISABLED"
    SCHEMA_NOT_MAPPED = "SCHEMA_NOT_MAPPED"


REASON_CODES_VALIDATOR_ERROR = frozenset(
    {
        ReasonCode.DEPENDENCY_MISSING,
        ReasonCode.SCHEMA_UNREADABLE,
        ReasonCode.SCHEMA_META_INVALID,
        ReasonCode.SCHEMA_ID_COLLISION,
        ReasonCode.SCHEMA_ROOT_OUTSIDE_REGISTRY,
        ReasonCode.REF_UNRESOLVABLE,
        ReasonCode.INTERNAL_VALIDATION_ERROR,
    }
)
REASON_CODES_NOT_EVALUATED = frozenset(
    {ReasonCode.SHADOW_DISABLED, ReasonCode.SCHEMA_NOT_MAPPED}
)


class WriteReasonCode(Enum):
    """Chiuso, ESATTAMENTE UNDICI valori (§4.2). Ridurli e' un difetto."""

    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    OUTPUT_ROOT_NOT_CONFIGURED = "OUTPUT_ROOT_NOT_CONFIGURED"
    SERIALIZATION_FAILED = "SERIALIZATION_FAILED"
    RECORD_TOO_LARGE = "RECORD_TOO_LARGE"
    RECORD_FIELD_INVALID = "RECORD_FIELD_INVALID"
    ID_GENERATION_INVALID = "ID_GENERATION_INVALID"
    CLOCK_INVALID = "CLOCK_INVALID"
    TEMP_WRITE_FAILED = "TEMP_WRITE_FAILED"
    FLUSH_FSYNC_FAILED = "FLUSH_FSYNC_FAILED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    COLLISION_EXISTING_TARGET = "COLLISION_EXISTING_TARGET"


WRITE_REASON_TO_STATUS = {
    WriteReasonCode.NOT_ELIGIBLE: WriteStatus.NOT_ATTEMPTED,
    WriteReasonCode.OUTPUT_ROOT_NOT_CONFIGURED: WriteStatus.NOT_ATTEMPTED,
    WriteReasonCode.SERIALIZATION_FAILED: WriteStatus.WRITE_ERROR,
    WriteReasonCode.RECORD_TOO_LARGE: WriteStatus.WRITE_ERROR,
    WriteReasonCode.RECORD_FIELD_INVALID: WriteStatus.WRITE_ERROR,
    WriteReasonCode.ID_GENERATION_INVALID: WriteStatus.WRITE_ERROR,
    WriteReasonCode.CLOCK_INVALID: WriteStatus.WRITE_ERROR,
    WriteReasonCode.TEMP_WRITE_FAILED: WriteStatus.WRITE_ERROR,
    WriteReasonCode.FLUSH_FSYNC_FAILED: WriteStatus.WRITE_ERROR,
    WriteReasonCode.PUBLISH_FAILED: WriteStatus.WRITE_ERROR,
    WriteReasonCode.COLLISION_EXISTING_TARGET: WriteStatus.COLLISION,
}


class TestValidationTarget(Enum):
    """§4.14 — canale privato di test: ESATTAMENTE NOVE frammenti allowlistati."""

    CORE_KILL_ANALYSIS = "KillAnalysis"
    CORE_ENVELOPE = "Envelope"
    CORE_SOURCE = "Source"
    CORE_UNREACHABLE_SOURCE = "UnreachableSource"
    CORE_QUERY = "Query"
    CORE_CLAIM = "Claim"
    CORE_NUMERIC = "Numeric"
    CORE_ANTI_HALLUCINATION_ANSWER = "AntiHallucinationAnswer"
    CORE_VERDICT = "Verdict"


#: §4.3 — insieme chiuso dei NOMI di campo citabili in InvocationError.field.
INVOCATION_ERROR_FIELDS = frozenset(
    {
        "invocation_id",
        "attempt",
        "run_id",
        "role",
        "stage",
        "artifact_id",
        "schema_ref",
        "legacy_outcome",
        "attivo",
        "radice_output",
    }
)


# --------------------------------------------------------------------------
# §4.5 / §4.6 / §4.3 — strutture
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationContext:
    invocation_id: str
    attempt: int
    run_id: str
    role: str
    stage: str
    artifact_id: str
    schema_ref: Any  # str | None
    legacy_outcome: Any  # LegacyOutcome


@dataclass(frozen=True)
class InvocationError:
    reason_code: InvocationReasonCode
    field: Any = None  # solo un NOME di campo allowlistato, mai il valore


@dataclass(frozen=True)
class ValidationErrorEntry:
    schema_resource_id: str
    schema_pointer: str
    instance_pointer: Any  # str | None
    instance_location: str
    location_is_exact: bool
    keyword: str
    missing_property: Any = None  # str | None

    def to_dict(self) -> dict:
        return {
            "schema_resource_id": self.schema_resource_id,
            "schema_pointer": self.schema_pointer,
            "instance_pointer": self.instance_pointer,
            "instance_location": self.instance_location,
            "location_is_exact": self.location_is_exact,
            "keyword": self.keyword,
            "missing_property": self.missing_property,
        }


ENTRY_KEYS = (
    "schema_resource_id",
    "schema_pointer",
    "instance_pointer",
    "instance_location",
    "location_is_exact",
    "keyword",
    "missing_property",
)


@dataclass(frozen=True)
class ValidationResult:
    full_outcome: FullOutcome
    reason_code: Any
    comparison: Comparison
    legacy_outcome: LegacyOutcome
    python_version: str
    validator_version: Any
    referencing_version: Any
    schema_ref: Any
    schema_sha256: Any
    registry_fingerprint: Any
    duration_ms: Any
    errors: tuple = ()
    errors_recorded: int = 0
    errors_dropped_oversize: int = 0
    errors_truncated: bool = False


@dataclass(frozen=True)
class WriteReceipt:
    status: WriteStatus
    write_reason_code: Any = None
    record_id: Any = None
    final_name: Any = None
    #: A2 §3 — CleanupStatus | None. Non-null ogni volta che un file pending e' stato
    #: creato, null altrimenti (quindi null per NOT_ATTEMPTED / NOT_ELIGIBLE, dove non
    #: si e' mai scritto nulla). NON finisce mai nel record persistito.
    cleanup_status: Any = None


@dataclass(frozen=True)
class ShadowExecution:
    invocation_status: InvocationStatus
    invocation_error: Any
    validation_result: Any
    write_receipt: WriteReceipt


RECORD_METADATA_KEYS = (
    "record_version",
    "record_id",
    "invocation_id",
    "attempt",
    "mode",
    "run_id",
    "role",
    "stage",
    "artifact_id",
    "timestamp_utc",
)

RECORD_RESULT_KEYS = (
    "full_outcome",
    "reason_code",
    "comparison",
    "legacy_outcome",
    "python_version",
    "validator_version",
    "referencing_version",
    "schema_ref",
    "schema_sha256",
    "registry_fingerprint",
    "duration_ms",
    "errors",
    "errors_recorded",
    "errors_dropped_oversize",
    "errors_truncated",
)

RECORD_KEYS = RECORD_METADATA_KEYS + RECORD_RESULT_KEYS + ("checksum",)


@dataclass(frozen=True)
class PersistedRecord:
    record_version: str
    record_id: str
    invocation_id: str
    attempt: int
    mode: str
    run_id: str
    role: str
    stage: str
    artifact_id: str
    timestamp_utc: str
    risultato: ValidationResult
    checksum: Any = None

    def to_dict(self) -> dict:
        d = {
            "record_version": self.record_version,
            "record_id": self.record_id,
            "invocation_id": self.invocation_id,
            "attempt": self.attempt,
            "mode": self.mode,
            "run_id": self.run_id,
            "role": self.role,
            "stage": self.stage,
            "artifact_id": self.artifact_id,
            "timestamp_utc": self.timestamp_utc,
        }
        d.update(risultato_to_dict(self.risultato))
        if self.checksum is not None:
            d["checksum"] = self.checksum
        return d


@dataclass
class AggregateReport:
    completeness: Completeness
    records_discovered: int
    records_valid: int
    records_corrupt: int
    duplicate_groups: int
    duplicate_extra_records: int
    records_counted: int
    #: A2 §5 — quanti file rispettano la grammatica pending nella radice. E' un segnale
    #: OPERATIVO sulla directory, non evidenza su alcun artefatto: i pending non vengono
    #: mai aperti, mai analizzati, e non entrano in nessun bucket di record.
    pending_files: int = 0
    by_full_outcome: dict = field(default_factory=dict)
    by_reason_code: dict = field(default_factory=dict)
    by_comparison: dict = field(default_factory=dict)
    denominator_label: str = "observed record set"

    def to_dict(self) -> dict:
        return {
            "completeness": self.completeness.value,
            "records_discovered": self.records_discovered,
            "records_valid": self.records_valid,
            "records_corrupt": self.records_corrupt,
            "duplicate_groups": self.duplicate_groups,
            "duplicate_extra_records": self.duplicate_extra_records,
            "records_counted": self.records_counted,
            "pending_files": self.pending_files,
            "by_full_outcome": dict(self.by_full_outcome),
            "by_reason_code": dict(self.by_reason_code),
            "by_comparison": dict(self.by_comparison),
            "denominator_label": self.denominator_label,
        }


def risultato_to_dict(r: ValidationResult) -> dict:
    return {
        "full_outcome": r.full_outcome.value,
        "reason_code": None if r.reason_code is None else r.reason_code.value,
        "comparison": r.comparison.value,
        "legacy_outcome": r.legacy_outcome.value,
        "python_version": r.python_version,
        "validator_version": r.validator_version,
        "referencing_version": r.referencing_version,
        "schema_ref": r.schema_ref,
        "schema_sha256": r.schema_sha256,
        "registry_fingerprint": r.registry_fingerprint,
        "duration_ms": r.duration_ms,
        "errors": [e.to_dict() for e in r.errors],
        "errors_recorded": r.errors_recorded,
        "errors_dropped_oversize": r.errors_dropped_oversize,
        "errors_truncated": r.errors_truncated,
    }


# --------------------------------------------------------------------------
# §4.7 — matrice di confronto
# --------------------------------------------------------------------------


def calcola_comparison(legacy: LegacyOutcome, full: FullOutcome) -> Comparison:
    if full in (FullOutcome.VALIDATOR_ERROR, FullOutcome.NOT_EVALUATED):
        return Comparison.NOT_COMPARABLE
    if legacy in (LegacyOutcome.ERROR, LegacyOutcome.NOT_RUN):
        return Comparison.NOT_COMPARABLE
    if legacy is LegacyOutcome.ACCEPTED:
        return (
            Comparison.AGREE_PASS
            if full is FullOutcome.FULL_PASS
            else Comparison.LEGACY_ACCEPT_FULL_FAIL
        )
    return (
        Comparison.LEGACY_REJECT_FULL_PASS
        if full is FullOutcome.FULL_PASS
        else Comparison.AGREE_FAIL
    )


# --------------------------------------------------------------------------
# §4.11 — JSON canonico, checksum, hashing
# --------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_hex(dati: bytes) -> str:
    return hashlib.sha256(dati).hexdigest()


def checksum_record(record_senza_checksum: dict) -> str:
    """checksum = sha256( UTF-8( canonical_json( record SENZA la chiave checksum ) ) )."""
    d = {k: v for k, v in record_senza_checksum.items() if k != "checksum"}
    return sha256_hex(canonical_json(d).encode("utf-8"))


# --------------------------------------------------------------------------
# Helper di grammatica
# --------------------------------------------------------------------------


def byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _e_stringa_entro(v: Any, limite: int) -> bool:
    return isinstance(v, str) and byte_len(v) <= limite


def _match(rx: "re.Pattern[str]", v: Any, limite: int | None = None) -> bool:
    if not isinstance(v, str):
        return False
    if limite is not None and byte_len(v) > limite:
        return False
    return rx.match(v) is not None


CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def contiene_controllo(s: str) -> bool:
    return CONTROL_CHARS.search(s) is not None


# --------------------------------------------------------------------------
# §4.13 — validatore di contratto del record (usato dall'aggregatore)
# --------------------------------------------------------------------------

VERSIONI_SUPPORTATE = frozenset({RECORD_VERSION})


def valida_contratto_record(rec: Any) -> list:
    """Ritorna la lista dei codici di violazione. Lista vuota == record conforme.

    I codici citano solo NOMI di campo e numeri di invariante: mai il contenuto.
    """
    v: list = []
    if not isinstance(rec, dict):
        return ["TIPO_RECORD"]
    if set(rec.keys()) != set(RECORD_KEYS):
        return ["INSIEME_CHIAVI"]
    if rec["record_version"] not in VERSIONI_SUPPORTATE:
        return ["VERSIONE_NON_SUPPORTATA"]
    if not _e_stringa_entro(rec["record_version"], MAX_RECORD_VERSION_BYTES):
        v.append("BOUND:record_version")

    if not _match(HEX32, rec["record_id"]):
        v.append("GRAMMATICA:record_id")
    if not _match(HEX64, rec["artifact_id"]):
        v.append("GRAMMATICA:artifact_id")
    for k in ("invocation_id", "run_id", "role", "stage"):
        if not _match(SAFE_TOKEN, rec[k], 64):
            v.append("GRAMMATICA:" + k)
    if not (isinstance(rec["attempt"], int) and not isinstance(rec["attempt"], bool)
            and 1 <= rec["attempt"] <= 99):
        v.append("GRAMMATICA:attempt")
    if rec["mode"] != MODE_SHADOW:
        v.append("GRAMMATICA:mode")
    ts = rec["timestamp_utc"]
    if not (_match(TIMESTAMP_UTC, ts) and len(ts) == TIMESTAMP_UTC_LEN):
        v.append("GRAMMATICA:timestamp_utc")

    # enum
    try:
        full = FullOutcome(rec["full_outcome"])
    except ValueError:
        return v + ["ENUM:full_outcome"]
    try:
        comparison = Comparison(rec["comparison"])
    except ValueError:
        return v + ["ENUM:comparison"]
    try:
        legacy = LegacyOutcome(rec["legacy_outcome"])
    except ValueError:
        return v + ["ENUM:legacy_outcome"]
    reason = rec["reason_code"]
    if reason is not None:
        try:
            reason_enum = ReasonCode(reason)
        except ValueError:
            return v + ["ENUM:reason_code"]
    else:
        reason_enum = None

    # versioni
    if not _match(VERSION_TOKEN, rec["python_version"], MAX_VERSION_BYTES):
        v.append("GRAMMATICA:python_version")
    for k in ("validator_version", "referencing_version"):
        if rec[k] is not None and not _match(VERSION_TOKEN, rec[k], MAX_VERSION_BYTES):
            v.append("GRAMMATICA:" + k)
    if rec["schema_ref"] is not None and not _match(SCHEMA_REF, rec["schema_ref"], 64):
        v.append("GRAMMATICA:schema_ref")
    for k in ("schema_sha256", "registry_fingerprint"):
        if rec[k] is not None and not _match(HEX64, rec[k]):
            v.append("GRAMMATICA:" + k)
    dm = rec["duration_ms"]
    if dm is not None and not (
        isinstance(dm, int) and not isinstance(dm, bool) and 0 <= dm <= DURATION_MS_MAX
    ):
        v.append("GRAMMATICA:duration_ms")

    # contatori
    for k in ("errors_recorded", "errors_dropped_oversize"):
        if not (isinstance(rec[k], int) and not isinstance(rec[k], bool) and rec[k] >= 0):
            v.append("TIPO:" + k)
    if not isinstance(rec["errors_truncated"], bool):
        v.append("TIPO:errors_truncated")
    if not isinstance(rec["errors"], list):
        return v + ["TIPO:errors"]

    # voci di errore
    for e in rec["errors"]:
        if not isinstance(e, dict) or set(e.keys()) != set(ENTRY_KEYS):
            v.append("INSIEME_CHIAVI:errors")
            break
        if not _match(SCHEMA_REF, e["schema_resource_id"], 64):
            v.append("GRAMMATICA:schema_resource_id")
        if not _e_stringa_entro(e["schema_pointer"], MAX_LOCATION_BYTES):
            v.append("BOUND:schema_pointer")
        if not _e_stringa_entro(e["instance_location"], MAX_LOCATION_BYTES):
            v.append("BOUND:instance_location")
        if e["instance_pointer"] is not None and not _e_stringa_entro(
            e["instance_pointer"], MAX_LOCATION_BYTES
        ):
            v.append("BOUND:instance_pointer")
        if not _e_stringa_entro(e["keyword"], MAX_KEYWORD_BYTES):
            v.append("BOUND:keyword")
        if e["missing_property"] is not None and not _e_stringa_entro(
            e["missing_property"], MAX_MISSING_PROPERTY_BYTES
        ):
            v.append("BOUND:missing_property")
        # A1 rev4 §2.4 — i caratteri di controllo grezzi sono RIFIUTATI nei campi di
        # posizione persistiti. Il backslash NON e' vietato: e' legittimo nei dati, e
        # sotto A4 il tetto si misura sulla serializzazione canonica e su nient'altro,
        # quindi vietarlo non comprerebbe nulla e rifiuterebbe dati conformi.
        #
        # E' un backstop. Dopo §2.3 il produttore di questa capacita' non puo' piu'
        # costruire un record simile da dati di istanza; il rifiuto serve ai record che
        # l'aggregatore §4.13 legge da una directory che non ha scritto lui, e al caso
        # di §4.2 in cui il carattere viene dallo SCHEMA e non e' redigibile.
        # E2 rev3 — `schema_resource_id` era l'UNICO campo di `ENTRY_KEYS` escluso da
        # questo ciclo: token persistito che identifica una risorsa, accettava il
        # newline finale, e non compariva ne' nell'ambito ne' fra le esclusioni.
        for k in (
            "schema_resource_id",
            "schema_pointer",
            "instance_pointer",
            "instance_location",
            "keyword",
            "missing_property",
        ):
            if isinstance(e[k], str) and contiene_controllo(e[k]):
                v.append("CARATTERI:" + k)
        if not isinstance(e["location_is_exact"], bool):
            v.append("TIPO:location_is_exact")
        else:
            # invariante 9
            if e["location_is_exact"] != (e["instance_pointer"] is not None):
                v.append("INVARIANTE:9")
            elif e["location_is_exact"] and e["instance_pointer"] != e["instance_location"]:
                v.append("INVARIANTE:9")
            if REDACTED_SEGMENT in e["instance_location"] and e["location_is_exact"]:
                v.append("INVARIANTE:9")

    # E2 rev3 §4.2 — nessun carattere di controllo nei token persistiti. `D-4` copriva
    # i cinque campi di posizione delle voci e non i metadati: la difesa esisteva sul
    # percorso del produttore e mancava su quello dell'aggregatore, che e' proprio il
    # percorso che legge record scritti da altri.
    for k in TOKEN_PERSISTITI:
        val = rec.get(k)
        if isinstance(val, str) and contiene_controllo(val):
            v.append("CARATTERI:" + k)

    # nullability §4.7
    v.extend(_controlla_nullability(rec, full, reason_enum, comparison))

    # invarianti incrociate 1..8, 10
    v.extend(_controlla_invarianti(rec, full, comparison, legacy, reason_enum))

    # checksum
    if not _match(HEX64, rec["checksum"]):
        v.append("GRAMMATICA:checksum")
    elif checksum_record(rec) != rec["checksum"]:
        v.append("CHECKSUM")
    return v


def _controlla_nullability(rec, full, reason_enum, comparison) -> list:
    v = []
    if full in (FullOutcome.FULL_PASS, FullOutcome.FULL_FAIL):
        if reason_enum is not None:
            v.append("NULL:reason_code")
        for k in ("validator_version", "referencing_version", "schema_ref",
                  "schema_sha256", "registry_fingerprint"):
            if rec[k] is None:
                v.append("NULL:" + k)
    elif full is FullOutcome.VALIDATOR_ERROR:
        if reason_enum is None or reason_enum not in REASON_CODES_VALIDATOR_ERROR:
            v.append("NULL:reason_code")
        if reason_enum is ReasonCode.DEPENDENCY_MISSING:
            for k in ("validator_version", "referencing_version"):
                if rec[k] is not None:
                    v.append("NULL:" + k)
        else:
            for k in ("validator_version", "referencing_version"):
                if rec[k] is None:
                    v.append("NULL:" + k)
        if rec["schema_ref"] is None:
            v.append("NULL:schema_ref")
        # C3: schema_sha256 e registry_fingerprint sono condizionali; solo il
        # legame dell'invariante 8 e' obbligatorio (verificato altrove).
    else:  # NOT_EVALUATED
        if reason_enum is None or reason_enum not in REASON_CODES_NOT_EVALUATED:
            v.append("NULL:reason_code")
        for k in ("validator_version", "referencing_version", "schema_sha256",
                  "registry_fingerprint", "duration_ms"):
            if rec[k] is not None:
                v.append("NULL:" + k)
        if reason_enum is ReasonCode.SCHEMA_NOT_MAPPED:
            if rec["schema_ref"] is not None and rec["schema_ref"] in ARTIFACT_ROOTS:
                v.append("C2:schema_ref")
        elif reason_enum is ReasonCode.SHADOW_DISABLED:
            pass
    if full in (FullOutcome.FULL_PASS, FullOutcome.VALIDATOR_ERROR,
                FullOutcome.NOT_EVALUATED):
        if rec["errors"] != []:
            v.append("VUOTO:errors")
        if rec["errors_recorded"] != 0:
            v.append("ZERO:errors_recorded")
        if rec["errors_dropped_oversize"] != 0:
            v.append("ZERO:errors_dropped_oversize")
        if rec["errors_truncated"] is not False:
            v.append("FALSO:errors_truncated")
    return v


def _controlla_invarianti(rec, full, comparison, legacy, reason_enum) -> list:
    v = []
    # 1
    if (reason_enum is None) != (full in (FullOutcome.FULL_PASS, FullOutcome.FULL_FAIL)):
        v.append("INVARIANTE:1")
    # 2
    if full in (FullOutcome.VALIDATOR_ERROR, FullOutcome.NOT_EVALUATED) and (
        comparison is not Comparison.NOT_COMPARABLE
    ):
        v.append("INVARIANTE:2")
    # 3
    if rec["errors_recorded"] != len(rec["errors"]):
        v.append("INVARIANTE:3")
    # 4
    if rec["errors_recorded"] + rec["errors_dropped_oversize"] > MAX_ERRORS:
        v.append("INVARIANTE:4")
    # 5
    if full is FullOutcome.FULL_FAIL and (
        rec["errors_recorded"] + rec["errors_dropped_oversize"] < 1
    ):
        v.append("INVARIANTE:5")
    # 6
    if rec["errors_truncated"] is True and (
        rec["errors_recorded"] + rec["errors_dropped_oversize"] != MAX_ERRORS
    ):
        v.append("INVARIANTE:6")
    # 7 — nessun campo asserisce un totale vero: verificato strutturalmente
    #     dall'insieme chiuso delle chiavi (nessuna chiave di totale esiste).
    # 8
    if rec["schema_sha256"] is not None and rec["registry_fingerprint"] is None:
        v.append("INVARIANTE:8")
    # matrice di confronto
    if calcola_comparison(legacy, full) is not comparison:
        v.append("MATRICE:comparison")
    # 10 — i bound delle voci sono gia' stati controllati sopra.
    return v


def chiavi_zero(enum_cls) -> dict:
    return {m.value: 0 for m in enum_cls}
