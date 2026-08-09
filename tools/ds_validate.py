#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEEPSEARCH — capacita' di validazione JSON Schema in SHADOW (ADR-M0.1-001 rev9).

Tre superfici, e una sola e' pubblica:

  esegui_shadow_produzione(istanza, contesto)      -> superficie di produzione, CHIUSA
  _esegui_shadow_core(...)                         -> nucleo privato di test, iniettabile
  _valida_sottoschema_test(...)                    -> canale privato per i 9 $defs

In M0.1 la politica di produzione ha `attivo=False` e non e' modificabile: una
invocazione di produzione si ferma al passo 3 della precedenza (NOT_EVALUATED /
SHADOW_DISABLED) oppure prima, al passo 2, con INVOCATION_ERROR. I passi da 4 in
avanti sono irraggiungibili dalla produzione e vivono solo nel nucleo privato.

Nessuna eccezione attraversa alcuna superficie. Nessuna chiamata di rete, nessun
retrieve callable: il registro degli schemi e' precaricato e CHIUSO.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ds_validate_contract import (
    ARTIFACT_ROOTS,
    CORE_SCHEMA_ID,
    DURATION_MS_MAX,
    HEX32,
    MAX_ERRORS,
    MAX_KEYWORD_BYTES,
    MAX_LOCATION_BYTES,
    MAX_MISSING_PROPERTY_BYTES,
    MAX_RECORD_BYTES,
    MODE_SHADOW,
    RECORD_VERSION,
    REDACTED_SEGMENT,
    SAFE_TOKEN,
    SCHEMA_REF,
    TIMESTAMP_UTC,
    TIMESTAMP_UTC_LEN,
    Comparison,
    FullOutcome,
    InvocationError,
    InvocationReasonCode,
    InvocationStatus,
    LegacyOutcome,
    PersistedRecord,
    ReasonCode,
    ShadowExecution,
    TestValidationTarget,
    ValidationContext,
    ValidationErrorEntry,
    ValidationResult,
    WriteReasonCode,
    WriteReceipt,
    WriteStatus,
    byte_len,
    calcola_comparison,
    canonical_json,
    checksum_record,
    contiene_controllo,
    sha256_hex,
    valida_contratto_record,
)

# --------------------------------------------------------------------------
# Politica di produzione — stato privato, non pubblico
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PoliticaProduzione:
    attivo: bool = False
    radice_output: Any = None


def _politica_produzione() -> PoliticaProduzione:
    """Factory privata. Nessun global mutabile, nessun setter, nessun rebind pubblico."""
    return PoliticaProduzione(attivo=False, radice_output=None)


@dataclass(frozen=True)
class ConfigTest:
    """Solo per il nucleo privato di test."""

    attivo: Any
    radice_output: Any


# --------------------------------------------------------------------------
# Adattatori — iniettabili SOLO sul nucleo privato
# --------------------------------------------------------------------------


class _Libreria:
    def __init__(self, jsonschema, referencing, unresolvable,
                 versione_validator, versione_referencing):
        self.jsonschema = jsonschema
        self.referencing = referencing
        self.unresolvable = unresolvable
        self.versione_validator = versione_validator
        self.versione_referencing = versione_referencing


def _importa_predefinito() -> _Libreria:
    """Import pigro e guardato: avviene al passo 5, mai all'import del modulo."""
    import jsonschema  # noqa: PLC0415
    import referencing  # noqa: PLC0415
    import referencing.exceptions  # noqa: PLC0415
    from importlib import metadata  # noqa: PLC0415

    return _Libreria(
        jsonschema=jsonschema,
        referencing=referencing,
        unresolvable=referencing.exceptions.Unresolvable,
        versione_validator=metadata.version("jsonschema"),
        versione_referencing=metadata.version("referencing"),
    )


class LettoreSchemi:
    """Legge i figli diretti *.json di una radice fidata. Nessuna ricorsione.

    Il risolutore di percorso e' iniettabile: rifiuta qualunque percorso che
    esca dalla radice fidata (contenimento).
    """

    def __init__(self, radice: str, risolutore=None):
        self._radice = radice
        self._risolutore = risolutore or risolvi_contenuto

    def elenca(self) -> list:
        nomi = []
        for n in sorted(os.listdir(self._radice)):
            if not n.endswith(".json"):
                continue
            p = os.path.join(self._radice, n)
            if os.path.isdir(p):
                continue
            nomi.append(n)
        return nomi

    def leggi(self, nome: str) -> bytes:
        percorso = self._risolutore(self._radice, nome)
        with open(percorso, "rb") as fh:
            return fh.read()


class ContenimentoViolato(Exception):
    """Il percorso richiesto esce dalla radice fidata."""


def risolvi_contenuto(radice: str, nome: str) -> str:
    """Risolutore predefinito: rifiuta traversal, separatori e uscite dalla radice."""
    if not isinstance(nome, str) or not nome or contiene_controllo(nome):
        raise ContenimentoViolato("nome")
    if "/" in nome or "\\" in nome or ":" in nome or ".." in nome:
        raise ContenimentoViolato("nome")
    base = os.path.realpath(radice)
    percorso = os.path.realpath(os.path.join(base, nome))
    if os.path.dirname(percorso) != base:
        raise ContenimentoViolato("uscita")
    return percorso


class Orologio:
    def ora_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def monotonico_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000


class Pubblicatore:
    """Scrittura pending + pubblicazione atomica SENZA sovrascrittura.

    Primitiva scelta: `os.link` seguito da `os.unlink` del pending.
    `os.link` fallisce con FileExistsError se il nome finale esiste gia' — su
    Windows e su POSIX — quindi non puo' mai clobberare un record esistente.
    `os.replace` e' vietato dall'ADR e non compare in questo modulo.
    """

    def prepara_radice(self, radice: str) -> None:
        os.makedirs(radice, exist_ok=True)

    def apri(self, percorso: str):
        return open(percorso, "wb")

    def scrivi(self, fh, blob: bytes) -> None:
        fh.write(blob)

    def sincronizza(self, fh) -> None:
        fh.flush()
        os.fsync(fh.fileno())

    def pubblica(self, pending: str, finale: str) -> None:
        os.link(pending, finale)
        os.unlink(pending)


def _genera_id_produzione() -> str:
    return secrets.token_hex(16)


@dataclass(frozen=True)
class Adattatori:
    importer: Any
    reader: Any
    publisher: Any
    clock: Any
    id_generator: Any


def _radice_schemi_predefinita() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schemas")


def _adattatori_predefiniti() -> Adattatori:
    return Adattatori(
        importer=_importa_predefinito,
        reader=LettoreSchemi(_radice_schemi_predefinita()),
        publisher=Pubblicatore(),
        clock=Orologio(),
        id_generator=_genera_id_produzione,
    )


# --------------------------------------------------------------------------
# Passi 1-2 — validazione stdlib-only di config e contesto
# --------------------------------------------------------------------------

_SEPARATORI = ("/", "\\")


def _token_sicuro(v: Any) -> bool:
    """§4.5 — grammatica PRIMA di ogni codifica (A1 rev4 §2.2).

    L'ordine e' normativo, non stilistico. `byte_len` codifica in UTF-8 ed e' l'unica
    operazione qui che possa sollevare: su un surrogato spaiato — quelli che
    `json.loads(r'"\\ud800"')` restituisce legittimamente — alzava `UnicodeEncodeError`
    fuori dalla superficie pubblica (P0-1). Ora e' l'ultimo controllo e lo raggiunge
    solo una stringa gia' accettata da SAFE_TOKEN, quindi di soli `[A-Za-z0-9._-]`:
    ASCII puro, dove la codifica non puo' fallire.

    Nessun controllo precedente codifica: `in`, `re.match` e `re.search` lavorano sui
    code point. Nessuno e' ridondante rispetto a SAFE_TOKEN: `".."` e' composto di soli
    caratteri ammessi, e `$` in Python accetta un newline finale, quindi la classe da
    sola non escluderebbe i caratteri di controllo.
    """
    if type(v) is not str:
        return False
    if ".." in v:
        return False
    if any(s in v for s in _SEPARATORI):
        return False
    if ":" in v:
        return False
    if contiene_controllo(v):
        return False
    if SAFE_TOKEN.match(v) is None:
        return False
    return byte_len(v) <= 64


def _valida_config(config: Any):
    """§4.5 — la config di test e' accettata per identita' di tipo (A1 rev4 §2.1 regione 2)."""
    c = InvocationReasonCode.CONFIG_INVALID
    if type(config) is not ConfigTest:
        return InvocationError(c, "attivo")
    if type(config.attivo) is not bool:
        return InvocationError(c, "attivo")
    radice = config.radice_output
    if radice is not None:
        if type(radice) is not str or not radice or contiene_controllo(radice):
            return InvocationError(c, "radice_output")
    return None


def _valida_contesto(contesto: Any):
    """§4.5 — validazione per identita' esatta di tipo (A1 rev4 §2.2).

    Niente duck-typing, niente sottoclassi, nessun accesso ad attributi su oggetti
    forniti dal chiamante prima che il tipo sia accettato. Il meccanismo non e' un
    `except` piu' largo: e' una superficie eseguita piu' piccola. Un `__getattr__` che
    solleva, una sottoclasse di `str` con `encode` rotto, un `__class__` che solleva
    sotto `isinstance` non arrivano mai a eseguire il proprio codice.
    """
    e = InvocationReasonCode.CONTEXT_INVALID

    # Una SOTTOCLASSE e' rifiutata: puo' ridefinire __getattribute__ e riaprirebbe
    # esattamente la famiglia che questo confine chiude.
    #
    # Il nome citato resta dentro l'insieme chiuso di §4.3. Quell'insieme non ha un
    # membro per "l'oggetto contesto", e aggiungerne uno estenderebbe una tassonomia
    # chiusa senza emendamento; `invocation_id` e' inoltre il nome che l'implementazione
    # duck-typed gia' restituiva per un oggetto del tutto sbagliato, quindi il
    # comportamento osservabile non cambia su questo punto.
    if type(contesto) is not ValidationContext:
        return InvocationError(e, "invocation_id")

    # Letture dirette di attributo su un valore gia' accertato ValidationContext.
    if not _token_sicuro(contesto.invocation_id):
        return InvocationError(e, "invocation_id")
    if not _token_sicuro(contesto.run_id):
        return InvocationError(e, "run_id")
    if not _token_sicuro(contesto.role):
        return InvocationError(e, "role")
    if not _token_sicuro(contesto.stage):
        return InvocationError(e, "stage")

    tentativo = contesto.attempt
    # `type(x) is int` rifiuta `bool` sul tipo, non per caso: `True` e' un `int`.
    if type(tentativo) is not int or not (1 <= tentativo <= 99):
        return InvocationError(e, "attempt")

    artefatto = contesto.artifact_id
    if type(artefatto) is not str or re.match(r"^[0-9a-f]{64}$", artefatto) is None:
        return InvocationError(e, "artifact_id")

    riferimento = contesto.schema_ref
    if riferimento is not None:
        # Stesso ordine di `_token_sicuro`: grammatica prima, codifica dopo.
        if type(riferimento) is not str:
            return InvocationError(e, "schema_ref")
        if SCHEMA_REF.match(riferimento) is None:
            return InvocationError(e, "schema_ref")
        if byte_len(riferimento) > 64:
            return InvocationError(e, "schema_ref")

    if type(contesto.legacy_outcome) is not LegacyOutcome:
        return InvocationError(e, "legacy_outcome")
    return None


_RICEVUTA_NON_ELEGGIBILE = WriteReceipt(
    status=WriteStatus.NOT_ATTEMPTED, write_reason_code=WriteReasonCode.NOT_ELIGIBLE
)


def _errore_invocazione(err: InvocationError) -> ShadowExecution:
    return ShadowExecution(
        invocation_status=InvocationStatus.INVOCATION_ERROR,
        invocation_error=err,
        validation_result=None,
        write_receipt=_RICEVUTA_NON_ELEGGIBILE,
    )


# --------------------------------------------------------------------------
# Superficie pubblica di produzione — CHIUSA
# --------------------------------------------------------------------------


def esegui_shadow_produzione(istanza: object, contesto: ValidationContext) -> ShadowExecution:
    """Unica API pubblica. Nessun parametro di iniezione, nessun seam.

    In M0.1 puo' restituire soltanto:
      - INVOCATION_ERROR / CONTEXT_INVALID | CONFIG_INVALID  (passo 2)
      - NOT_EVALUATED   / SHADOW_DISABLED                    (passo 3)
    """
    politica = _politica_produzione()
    return _esegui_shadow_core(
        istanza,
        contesto,
        config=ConfigTest(attivo=politica.attivo, radice_output=politica.radice_output),
        adattatori=_adattatori_predefiniti(),
    )


# --------------------------------------------------------------------------
# Nucleo privato di test — precedenza esatta §4.4
# --------------------------------------------------------------------------


def _esegui_shadow_core(
    istanza: object,
    contesto: ValidationContext,
    *,
    config: ConfigTest,
    adattatori: Adattatori,
) -> ShadowExecution:
    # passi 1-2 — stdlib only, nessun import opzionale, nessun I/O
    err = _valida_config(config)
    if err is None:
        err = _valida_contesto(contesto)
    if err is not None:
        return _errore_invocazione(err)

    legacy = contesto.legacy_outcome
    riferimento = contesto.schema_ref

    # passo 3 — OFF: nessun import, nessuna scrittura, nessun effetto
    if config.attivo is not True:
        risultato = _non_valutato(ReasonCode.SHADOW_DISABLED, legacy, riferimento)
        return ShadowExecution(InvocationStatus.OK, None, risultato, _RICEVUTA_NON_ELEGGIBILE)

    # passo 4 — mappatura: None oppure non-artifact-root
    if riferimento is None or riferimento not in ARTIFACT_ROOTS:
        risultato = _non_valutato(ReasonCode.SCHEMA_NOT_MAPPED, legacy, riferimento)
        return _con_scrittura(risultato, contesto, config, adattatori)

    # passi 5-7
    risultato = _valuta(istanza, contesto, adattatori, target=None, radice_schema=riferimento)
    return _con_scrittura(risultato, contesto, config, adattatori)


def _valida_sottoschema_test(
    istanza: object,
    contesto: ValidationContext,
    *,
    target: TestValidationTarget,
    adattatori: Adattatori,
) -> ShadowExecution:
    """§4.14 — canale privato per i nove $defs. Non persiste MAI un record."""
    err = _valida_contesto(contesto)
    if err is not None:
        return _errore_invocazione(err)
    if not isinstance(target, TestValidationTarget):
        return _errore_invocazione(
            InvocationError(InvocationReasonCode.CONTEXT_INVALID, "schema_ref")
        )
    risultato = _valuta(
        istanza, contesto, adattatori, target=target, radice_schema=CORE_SCHEMA_ID
    )
    return ShadowExecution(InvocationStatus.OK, None, risultato, _RICEVUTA_NON_ELEGGIBILE)


def _non_valutato(codice: ReasonCode, legacy: LegacyOutcome, riferimento) -> ValidationResult:
    return ValidationResult(
        full_outcome=FullOutcome.NOT_EVALUATED,
        reason_code=codice,
        comparison=Comparison.NOT_COMPARABLE,
        legacy_outcome=legacy,
        python_version=platform.python_version(),
        validator_version=None,
        referencing_version=None,
        schema_ref=riferimento,
        schema_sha256=None,
        registry_fingerprint=None,
        duration_ms=None,
        errors=(),
        errors_recorded=0,
        errors_dropped_oversize=0,
        errors_truncated=False,
    )


def _errore_validatore(
    codice: ReasonCode,
    legacy: LegacyOutcome,
    riferimento,
    lib: Any,
    sha: Any,
    fingerprint: Any,
    durata: Any,
) -> ValidationResult:
    return ValidationResult(
        full_outcome=FullOutcome.VALIDATOR_ERROR,
        reason_code=codice,
        comparison=Comparison.NOT_COMPARABLE,
        legacy_outcome=legacy,
        python_version=platform.python_version(),
        validator_version=None if lib is None else lib.versione_validator,
        referencing_version=None if lib is None else lib.versione_referencing,
        schema_ref=riferimento,
        schema_sha256=sha,
        registry_fingerprint=fingerprint,
        duration_ms=durata,
        errors=(),
        errors_recorded=0,
        errors_dropped_oversize=0,
        errors_truncated=False,
    )


# --------------------------------------------------------------------------
# Passo 6 — registro chiuso e fingerprint (§4.9)
# --------------------------------------------------------------------------


class _Registro:
    def __init__(self, documenti, byte_grezzi, fingerprint, registry):
        self.documenti = documenti
        self.byte_grezzi = byte_grezzi
        self.fingerprint = fingerprint
        self.registry = registry


def _costruisci_registro(adattatori: Adattatori, lib: _Libreria):
    """Ritorna (_Registro, None) oppure (None, ReasonCode). Fail-closed."""
    try:
        nomi = adattatori.reader.elenca()
    except Exception:
        return None, ReasonCode.SCHEMA_UNREADABLE

    documenti = {}
    byte_grezzi = {}
    identita_viste = {}
    for nome in nomi:
        try:
            grezzo = adattatori.reader.leggi(nome)
        except Exception:
            return None, ReasonCode.SCHEMA_UNREADABLE
        try:
            documento = json.loads(grezzo.decode("utf-8"))
        except Exception:
            return None, ReasonCode.SCHEMA_UNREADABLE
        if not isinstance(documento, dict):
            return None, ReasonCode.SCHEMA_UNREADABLE
        identita = documento.get("$id")
        if not isinstance(identita, str) or not identita:
            return None, ReasonCode.SCHEMA_ID_COLLISION
        if identita in identita_viste:
            return None, ReasonCode.SCHEMA_ID_COLLISION  # identita' duplicata
        if identita != nome:
            return None, ReasonCode.SCHEMA_ID_COLLISION  # $id != basename
        identita_viste[identita] = nome
        documenti[identita] = documento
        byte_grezzi[identita] = grezzo

    for identita, documento in documenti.items():
        try:
            lib.jsonschema.Draft202012Validator.check_schema(documento)
        except Exception:
            return None, ReasonCode.SCHEMA_META_INVALID

    manifesto = [[i, sha256_hex(byte_grezzi[i])] for i in sorted(documenti, key=lambda s: s.encode("utf-8"))]
    serializzato = json.dumps(
        manifesto, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    fingerprint = sha256_hex(serializzato)

    try:
        from referencing import Resource  # noqa: PLC0415
        from referencing.jsonschema import DRAFT202012  # noqa: PLC0415

        registry = lib.referencing.Registry().with_resources(
            [
                (i, Resource.from_contents(d, default_specification=DRAFT202012))
                for i, d in documenti.items()
            ]
        )
    except Exception:
        return None, ReasonCode.SCHEMA_META_INVALID

    return _Registro(documenti, byte_grezzi, fingerprint, registry), None


def calcola_fingerprint_da_byte(mappa_identita_byte: dict) -> str:
    """Espone il calcolo di §4.9 per i test di invarianza/sensibilita'."""
    manifesto = [
        [i, sha256_hex(mappa_identita_byte[i])]
        for i in sorted(mappa_identita_byte, key=lambda s: s.encode("utf-8"))
    ]
    return sha256_hex(
        json.dumps(manifesto, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


# --------------------------------------------------------------------------
# Passi 5-7 — valutazione
# --------------------------------------------------------------------------


class _PercorsoNonRisolto(Exception):
    pass


def _valuta(istanza, contesto, adattatori, *, target, radice_schema) -> ValidationResult:
    legacy = contesto.legacy_outcome
    inizio = _istante(adattatori)

    # passo 5 — import opzionale, pigro e guardato
    try:
        lib = adattatori.importer()
    except Exception:
        return _errore_validatore(
            ReasonCode.DEPENDENCY_MISSING, legacy, radice_schema, None, None, None,
            _durata(inizio, adattatori),
        )

    # passo 6 — registro chiuso
    registro, codice = _costruisci_registro(adattatori, lib)
    if codice is not None:
        return _errore_validatore(
            codice, legacy, radice_schema, lib, None, None, _durata(inizio, adattatori)
        )
    if radice_schema not in registro.documenti:
        return _errore_validatore(
            ReasonCode.SCHEMA_ROOT_OUTSIDE_REGISTRY, legacy, radice_schema, lib, None, None,
            _durata(inizio, adattatori),
        )

    sha = sha256_hex(registro.byte_grezzi[radice_schema])
    fingerprint = registro.fingerprint

    if target is None:
        schema_radice = registro.documenti[radice_schema]
    else:
        schema_radice = {"$ref": radice_schema + "#/$defs/" + target.value}

    # passo 7 — validazione
    try:
        validatore = lib.jsonschema.Draft202012Validator(
            schema_radice, registry=registro.registry
        )
        voci, scartate, troncato = _raccogli(
            validatore.iter_errors(istanza), radice_schema, schema_radice, registro.documenti
        )
    except lib.unresolvable:
        return _errore_validatore(
            ReasonCode.REF_UNRESOLVABLE, legacy, radice_schema, lib, sha, fingerprint,
            _durata(inizio, adattatori),
        )
    except Exception:
        return _errore_validatore(
            ReasonCode.INTERNAL_VALIDATION_ERROR, legacy, radice_schema, lib, sha, fingerprint,
            _durata(inizio, adattatori),
        )

    esito = FullOutcome.FULL_PASS if (len(voci) == 0 and scartate == 0) else FullOutcome.FULL_FAIL
    return ValidationResult(
        full_outcome=esito,
        reason_code=None,
        comparison=calcola_comparison(legacy, esito),
        legacy_outcome=legacy,
        python_version=platform.python_version(),
        validator_version=lib.versione_validator,
        referencing_version=lib.versione_referencing,
        schema_ref=radice_schema,
        schema_sha256=sha,
        registry_fingerprint=fingerprint,
        duration_ms=_durata(inizio, adattatori),
        errors=tuple(voci),
        errors_recorded=len(voci),
        errors_dropped_oversize=scartate,
        errors_truncated=troncato,
    )


def _istante(adattatori):
    try:
        v = adattatori.clock.monotonico_ms()
    except Exception:
        return None
    return v


def _durata(inizio, adattatori):
    """Nessun clamp, nessun arrotondamento, nessun valore inventato: o e' conforme o e' None."""
    if inizio is None:
        return None
    fine = _istante(adattatori)
    if fine is None:
        return None
    try:
        d = fine - inizio
    except Exception:
        return None
    if isinstance(d, bool) or not isinstance(d, int):
        return None
    if d < 0 or d > DURATION_MS_MAX:
        return None
    return d


# --------------------------------------------------------------------------
# §4.8 — posizioni, redazione, bound, ordinamento
# --------------------------------------------------------------------------

_RX_REQUIRED = re.compile(r"^'(?P<p>.+)' is a required property$")


def _esc(seg: str) -> str:
    return seg.replace("~", "~0").replace("/", "~1")


def _ha_chiave(nodo, s) -> bool:
    return isinstance(nodo, dict) and isinstance(s, str) and s in nodo


def _segui_ref(riferimento, risorsa_corrente, documenti):
    if not isinstance(riferimento, str):
        raise _PercorsoNonRisolto()
    base, _, frammento = riferimento.partition("#")
    risorsa = base if base else risorsa_corrente
    if risorsa not in documenti:
        raise _PercorsoNonRisolto()
    nodo = documenti[risorsa]
    puntatore = ""
    if frammento:
        if not frammento.startswith("/"):
            raise _PercorsoNonRisolto()
        for pezzo in frammento.split("/")[1:]:
            chiave = pezzo.replace("~1", "/").replace("~0", "~")
            try:
                nodo = nodo[chiave] if isinstance(nodo, dict) else nodo[int(chiave)]
            except Exception:
                raise _PercorsoNonRisolto()
        puntatore = frammento
    return risorsa, puntatore, nodo


def _analizza_percorso(radice_id, schema_radice, documenti, percorso_schema):
    """Ritorna (risorsa, puntatore, dichiarati) camminando il percorso di schema.

    `dichiarati[i]` dice se il segmento i-esimo del percorso di ISTANZA e' una
    chiave dichiarata dallo schema in quella posizione (o un indice di array).
    """
    risorsa = radice_id
    nodo = schema_radice
    puntatore = ""
    dichiarati = []
    segmenti = list(percorso_schema)
    i = 0
    while i < len(segmenti):
        s = segmenti[i]
        while isinstance(nodo, dict) and "$ref" in nodo and not _ha_chiave(nodo, s):
            risorsa, puntatore, nodo = _segui_ref(nodo["$ref"], risorsa, documenti)
        ultimo = i == len(segmenti) - 1
        try:
            if s == "properties" and not ultimo:
                nome = segmenti[i + 1]
                nodo = nodo["properties"][nome]
                puntatore += "/properties/" + _esc(str(nome))
                dichiarati.append(True)
                i += 2
                continue
            if s == "items" and not ultimo:
                nodo = nodo["items"]
                puntatore += "/items"
                dichiarati.append(True)
                i += 1
                continue
            if s == "prefixItems" and not ultimo:
                idx = segmenti[i + 1]
                nodo = nodo["prefixItems"][idx]
                puntatore += "/prefixItems/" + str(idx)
                dichiarati.append(True)
                i += 2
                continue
            if s == "additionalProperties" and not ultimo:
                nodo = nodo["additionalProperties"]
                puntatore += "/additionalProperties"
                dichiarati.append(False)
                i += 1
                continue
            if s == "patternProperties" and not ultimo:
                pat = segmenti[i + 1]
                nodo = nodo["patternProperties"][pat]
                puntatore += "/patternProperties/" + _esc(str(pat))
                dichiarati.append(False)
                i += 2
                continue
            nodo = nodo[s]
        except _PercorsoNonRisolto:
            raise
        except Exception:
            raise _PercorsoNonRisolto()
        puntatore += "/" + _esc(str(s))
        i += 1
    return risorsa, puntatore, dichiarati


def costruisci_posizioni(percorso_istanza, dichiarati):
    """(instance_pointer, instance_location, location_is_exact) — §4.8.

    `instance_pointer` e' RFC 6901 ed e' None appena un segmento e' redatto.
    `instance_location` e' RLE: come RFC 6901 ma un segmento puo' essere `~R`.
    """
    segmenti = list(percorso_istanza)
    if len(dichiarati) != len(segmenti):
        dichiarati = [isinstance(s, int) and not isinstance(s, bool) for s in segmenti]
    pezzi_rle = []
    redatto = False
    for seg, ok in zip(segmenti, dichiarati):
        if isinstance(seg, int) and not isinstance(seg, bool):
            pezzi_rle.append(str(seg))
            continue
        # A1 rev4 §2.3 — un segmento e' redatto anche quando NON e' rappresentabile,
        # cioe' porta un carattere di controllo, oltre che quando non e' dichiarato
        # dallo schema. Il nome originale non finisce mai in un record.
        #
        # La redazione NON e' uno scarto: `errors_dropped_oversize` continua a contare
        # una cosa sola, la voce scartata perche' una stringa vincolata ha superato il
        # proprio limite in byte. Gli indici di array sono interi e non passano di qui.
        if ok and contiene_controllo(str(seg)):
            ok = False
        if ok:
            pezzi_rle.append(_esc(str(seg)))
        else:
            pezzi_rle.append(REDACTED_SEGMENT)
            redatto = True
    posizione = "".join("/" + p for p in pezzi_rle)
    if redatto:
        return None, posizione, False
    return posizione, posizione, True


def _estrai_missing_property(errore) -> Any:
    """Nome di proprieta' DICHIARATO dallo schema, verificato contro `required`."""
    messaggio = getattr(errore, "message", None)
    if not isinstance(messaggio, str):
        return None
    m = _RX_REQUIRED.match(messaggio)
    if m is None:
        return None
    nome = m.group("p")
    richiesti = getattr(errore, "validator_value", None)
    if isinstance(richiesti, (list, tuple)) and nome in richiesti:
        # A1 rev4 §2.3 — `missing_property` e' un nome di proprieta', non un segmento di
        # percorso: `~R` non le si applica, perche' non e' una posizione. Un nome con un
        # carattere di controllo si riporta come `null`. La voce resta, e il suo
        # `keyword` resta `required`.
        if contiene_controllo(nome):
            return None
        return nome
    return None


def _voce_conforme(voce: ValidationErrorEntry) -> bool:
    if byte_len(voce.schema_resource_id) > 64 or SCHEMA_REF.match(voce.schema_resource_id) is None:
        return False
    if byte_len(voce.schema_pointer) > MAX_LOCATION_BYTES:
        return False
    if voce.instance_pointer is not None and byte_len(voce.instance_pointer) > MAX_LOCATION_BYTES:
        return False
    if byte_len(voce.instance_location) > MAX_LOCATION_BYTES:
        return False
    if byte_len(voce.keyword) > MAX_KEYWORD_BYTES:
        return False
    if voce.missing_property is not None and byte_len(voce.missing_property) > MAX_MISSING_PROPERTY_BYTES:
        return False
    return True


def _chiave_ordinamento(voce: ValidationErrorEntry):
    return (
        voce.schema_resource_id.encode("utf-8"),
        voce.schema_pointer.encode("utf-8"),
        voce.instance_location.encode("utf-8"),
        voce.keyword.encode("utf-8"),
        (voce.missing_property or "").encode("utf-8"),
    )


def _raccogli(iteratore, radice_id, schema_radice, documenti):
    """Enumerazione limitata: al massimo MAX_ERRORS + 1 prelievi dall'iteratore."""
    considerati = []
    troncato = False
    prelievi = 0
    it = iter(iteratore)
    while prelievi < MAX_ERRORS + 1:
        try:
            errore = next(it)
        except StopIteration:
            break
        prelievi += 1
        if prelievi == MAX_ERRORS + 1:
            troncato = True
            break
        considerati.append(errore)

    voci = []
    scartate = 0
    for errore in considerati:
        risorsa, puntatore, dichiarati = _analizza_percorso(
            radice_id, schema_radice, documenti, getattr(errore, "absolute_schema_path", [])
        )
        ptr_ist, posizione, esatta = costruisci_posizioni(
            getattr(errore, "absolute_path", []), dichiarati
        )
        parola = str(getattr(errore, "validator", ""))
        mancante = _estrai_missing_property(errore) if parola == "required" else None
        voce = ValidationErrorEntry(
            schema_resource_id=risorsa,
            schema_pointer=puntatore,
            instance_pointer=ptr_ist,
            instance_location=posizione,
            location_is_exact=esatta,
            keyword=parola,
            missing_property=mancante,
        )
        if _voce_conforme(voce):
            voci.append(voce)
        else:
            scartate += 1
    voci.sort(key=_chiave_ordinamento)
    return voci, scartate, troncato


def raccogli_da_iteratore(iteratore, radice_id, schema_radice, documenti):
    """Superficie di test per l'enumerazione limitata e lo scarto per oversize."""
    return _raccogli(iteratore, radice_id, schema_radice, documenti)


def voce_conforme(voce) -> bool:
    return _voce_conforme(voce)


# --------------------------------------------------------------------------
# §4.10 / §4.11 — matrice di emissione e writer
# --------------------------------------------------------------------------


def _con_scrittura(risultato, contesto, config, adattatori) -> ShadowExecution:
    if config.radice_output is None:
        ricevuta = WriteReceipt(
            status=WriteStatus.NOT_ATTEMPTED,
            write_reason_code=WriteReasonCode.OUTPUT_ROOT_NOT_CONFIGURED,
        )
    else:
        ricevuta = _scrivi(risultato, contesto, config.radice_output, adattatori)
    return ShadowExecution(InvocationStatus.OK, None, risultato, ricevuta)


def _ricevuta_errore(codice: WriteReasonCode, record_id=None) -> WriteReceipt:
    return WriteReceipt(
        status=WriteStatus.WRITE_ERROR, write_reason_code=codice, record_id=record_id,
        final_name=None,
    )


def passo_id(adattatori):
    try:
        valore = adattatori.id_generator()
    except Exception:
        return None, WriteReasonCode.ID_GENERATION_INVALID
    if not isinstance(valore, str) or HEX32.match(valore) is None:
        return None, WriteReasonCode.ID_GENERATION_INVALID
    return valore, None


def passo_orologio(adattatori):
    try:
        valore = adattatori.clock.ora_utc()
    except Exception:
        return None, WriteReasonCode.CLOCK_INVALID
    if (
        not isinstance(valore, str)
        or len(valore) != TIMESTAMP_UTC_LEN
        or TIMESTAMP_UTC.match(valore) is None
    ):
        return None, WriteReasonCode.CLOCK_INVALID
    return valore, None


def passo_campi(record: dict):
    violazioni = valida_contratto_record(record)
    return WriteReasonCode.RECORD_FIELD_INVALID if violazioni else None


def passo_serializza(record: dict):
    try:
        return canonical_json(record).encode("utf-8"), None
    except Exception:
        return None, WriteReasonCode.SERIALIZATION_FAILED


def passo_dimensione(blob: bytes):
    return WriteReasonCode.RECORD_TOO_LARGE if len(blob) > MAX_RECORD_BYTES else None


def costruisci_record(risultato, contesto, record_id: str, timestamp: str) -> dict:
    record = PersistedRecord(
        record_version=RECORD_VERSION,
        record_id=record_id,
        invocation_id=contesto.invocation_id,
        attempt=contesto.attempt,
        mode=MODE_SHADOW,
        run_id=contesto.run_id,
        role=contesto.role,
        stage=contesto.stage,
        artifact_id=contesto.artifact_id,
        timestamp_utc=timestamp,
        risultato=risultato,
    ).to_dict()
    record["checksum"] = checksum_record(record)
    return record


def passo_pubblica(radice: str, record_id: str, blob: bytes, adattatori) -> WriteReceipt:
    nome_finale = record_id + ".json"
    nome_pending = record_id + "." + secrets.token_hex(4) + ".pending"
    try:
        adattatori.publisher.prepara_radice(radice)
    except Exception:
        return _ricevuta_errore(WriteReasonCode.TEMP_WRITE_FAILED, record_id)
    percorso_pending = os.path.join(radice, nome_pending)
    percorso_finale = os.path.join(radice, nome_finale)
    try:
        fh = adattatori.publisher.apri(percorso_pending)
    except Exception:
        return _ricevuta_errore(WriteReasonCode.TEMP_WRITE_FAILED, record_id)
    try:
        try:
            adattatori.publisher.scrivi(fh, blob)
        except Exception:
            return _ricevuta_errore(WriteReasonCode.TEMP_WRITE_FAILED, record_id)
        try:
            adattatori.publisher.sincronizza(fh)
        except Exception:
            return _ricevuta_errore(WriteReasonCode.FLUSH_FSYNC_FAILED, record_id)
    finally:
        try:
            fh.close()
        except Exception:
            pass
    try:
        adattatori.publisher.pubblica(percorso_pending, percorso_finale)
    except FileExistsError:
        _rimuovi_silenzioso(percorso_pending)
        return WriteReceipt(
            status=WriteStatus.COLLISION,
            write_reason_code=WriteReasonCode.COLLISION_EXISTING_TARGET,
            record_id=record_id,
            final_name=nome_finale,
        )
    except Exception:
        _rimuovi_silenzioso(percorso_pending)
        return _ricevuta_errore(WriteReasonCode.PUBLISH_FAILED, record_id)
    return WriteReceipt(
        status=WriteStatus.WRITTEN,
        write_reason_code=None,
        record_id=record_id,
        final_name=nome_finale,
    )


def _rimuovi_silenzioso(percorso: str) -> None:
    try:
        os.unlink(percorso)
    except Exception:
        pass


def _scrivi(risultato, contesto, radice: str, adattatori) -> WriteReceipt:
    record_id, codice = passo_id(adattatori)
    if codice is not None:
        return _ricevuta_errore(codice, None)
    timestamp, codice = passo_orologio(adattatori)
    if codice is not None:
        return _ricevuta_errore(codice, record_id)
    try:
        record = costruisci_record(risultato, contesto, record_id, timestamp)
    except Exception:
        return _ricevuta_errore(WriteReasonCode.RECORD_FIELD_INVALID, record_id)
    codice = passo_campi(record)
    if codice is not None:
        return _ricevuta_errore(codice, record_id)
    blob, codice = passo_serializza(record)
    if codice is not None:
        return _ricevuta_errore(codice, record_id)
    codice = passo_dimensione(blob)
    if codice is not None:
        return _ricevuta_errore(codice, record_id)
    return passo_pubblica(radice, record_id, blob, adattatori)


def scrivi_record(risultato, contesto, radice: str, adattatori) -> WriteReceipt:
    """Superficie di test del writer completo (mai raggiungibile dalla produzione)."""
    return _scrivi(risultato, contesto, radice, adattatori)
