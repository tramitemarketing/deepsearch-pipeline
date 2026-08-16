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
import urllib.parse
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
    CleanupStatus,
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
        """SOLO la pubblicazione. La rimozione del pending e' `rimuovi`, ed e' pulizia.

        Le due operazioni stavano sotto un unico `try`, quindi un `os.unlink` fallito —
        ACL che negano DELETE, un antivirus o un indicizzatore che tiene un handle:
        condizioni Windows ordinarie — faceva riportare PUBLISH_FAILED per un record
        gia' pubblicato e conforme (P0-2). Separarle e' la correzione.
        """
        os.link(pending, finale)

    def rimuovi(self, percorso: str) -> None:
        """SOLO la pulizia. Il suo fallimento non e' mai un fallimento di scrittura."""
        os.unlink(percorso)


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

#: E1 rev6 §3.3 — insieme dei dialetti supportati da `M0.1`. Ha un solo elemento, e
#: finche' ne ha uno l'insieme dei vocabolari attivi e' determinato dall'URI e non
#: richiede verifica separata. Ampliarlo e' un emendamento successivo, che dovra'
#: definire la risoluzione cross-dialetto prima di ammettere il secondo elemento.
DIALETTI_SUPPORTATI = frozenset({"https://json-schema.org/draft/2020-12/schema"})


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
    # E2 rev3 §4.3 — regex LETTERALE in linea, non una costante: e' il punto sul
    # confine pubblico, e una correzione limitata al modulo del contratto lo mancava.
    # `\Z` e non `$`: quest'ultimo accettava `"a"*64 + "\n"`, che arrivava fino al disco.
    if type(artefatto) is not str or re.match(r"^[0-9a-f]{64}\Z", artefatto) is None:
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


def _ricevuta_non_eleggibile() -> WriteReceipt:
    """A2 §4 — una NUOVA istanza a ogni chiamata, mai un singleton condiviso.

    Era un globale restituito per identita' dall'API pubblica: chiunque tenesse una
    ricevuta poteva mutare l'oggetto che ogni chiamata successiva avrebbe restituito, e
    falsificare la traccia di audit dell'intero processo. Con `cleanup_status` la
    ricevuta porta stato per-chiamata, il che rende il singleton scorretto e non solo
    imprudente. `cleanup_status` resta null qui: non e' stato creato alcun pending.
    """
    return WriteReceipt(
        status=WriteStatus.NOT_ATTEMPTED, write_reason_code=WriteReasonCode.NOT_ELIGIBLE
    )


def _errore_invocazione(err: InvocationError) -> ShadowExecution:
    return ShadowExecution(
        invocation_status=InvocationStatus.INVOCATION_ERROR,
        invocation_error=err,
        validation_result=None,
        write_receipt=_ricevuta_non_eleggibile(),
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
        return ShadowExecution(InvocationStatus.OK, None, risultato, _ricevuta_non_eleggibile())

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
    return ShadowExecution(InvocationStatus.OK, None, risultato, _ricevuta_non_eleggibile())


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

    # R2.1-4 — `F3`, identita'. Da quando le risorse EMBEDDED entrano nell'indice, un
    # `$id` embedded che collide con un'identita' di primo livello darebbe due
    # bersagli per lo stesso nome: sceglierne uno in silenzio e' la classe di difetto
    # di `P0-4`. Resta un errore d'identita', fail-closed, e cade in `F3` — prima di
    # `F4` e di `F5`, come la precedenza di §6 impone.
    for identita, documento in documenti.items():
        for _ptr, identificatore, _nodo in _id_embedded(documento):
            if _identita_risolta(identificatore, identita) in documenti:
                return None, ReasonCode.SCHEMA_ID_COLLISION

    # E1 rev6 §3.3 — il dialetto dichiarato e' vincolante, e vale PRIMA della
    # meta-validazione: `check_schema` e' fissato a 2020-12 mentre
    # `Resource.from_contents` adotta la specifica dichiarata dalla risorsa, quindi
    # senza questo controllo i due passi potevano operare su dialetti diversi sullo
    # stesso documento. `M0.1` supporta un solo dialetto: un `$schema` assente o non
    # supportato e' un esito deterministico, mai un fallback silenzioso.
    # R2.1-1 (`A2`) — NESSUNA operazione di membership o di hash prima della
    # validazione di TIPO. `DIALETTI_SUPPORTATI` e' un `frozenset` e `in` chiama
    # `hash()` sul valore: con `$schema` lista o oggetto — JSON legittimo, documento
    # leggibile, `$id` conforme — usciva un `TypeError: unhashable type` NON catturato
    # da alcuna superficie pubblica, contro `DEC-5` clausola 3. Il tipo si accerta
    # prima; l'appartenenza dopo. Ogni `$schema` non stringa e ogni stringa non
    # supportata danno lo stesso esito deterministico.
    for identita, documento in documenti.items():
        dialetto = documento.get("$schema")
        if not isinstance(dialetto, str) or dialetto not in DIALETTI_SUPPORTATI:
            return None, ReasonCode.SCHEMA_META_INVALID

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
        # A5 — il fingerprint si CONSERVA: a questo punto il registro e' stato costruito
        # per intero, `_costruisci_registro` e' tornato senza codice d'errore e il valore
        # e' gia' calcolato. Buttarlo faceva perdere al record l'unica traccia forense di
        # QUALE chiusura fosse attiva quando la radice e' risultata non autorizzata —
        # esattamente l'informazione che serve a diagnosticare questo esito.
        #
        # `schema_sha256` resta null, e deve restarlo: la radice selezionata non e'
        # autorizzata e non se ne puo' pubblicare l'hash. I due campi rispondono a due
        # domande diverse — quale chiusura e' stata caricata, e quale radice e' stata
        # hashata — e l'invariante 8 e' un'implicazione a UN SOLO VERSO:
        #
        #     schema_sha256 != null  =>  registry_fingerprint != null
        #
        # Il verso opposto NON vale e non va assunto. Un fingerprint presente dice che
        # una chiusura e' stata costruita; non afferma che la radice sia stata accettata.
        # Questo e' l'unico stato che realizza quell'asimmetria: senza di esso
        # `sha is None <=> fingerprint is None` varrebbe ovunque, e l'invariante sarebbe
        # una biimplicazione travestita che nessun test puo' falsificare.
        return _errore_validatore(
            ReasonCode.SCHEMA_ROOT_OUTSIDE_REGISTRY, legacy, radice_schema, lib,
            None, registro.fingerprint,
            _durata(inizio, adattatori),
        )

    sha = sha256_hex(registro.byte_grezzi[radice_schema])
    fingerprint = registro.fingerprint

    # P0-3 — preflight della chiusura, PRIMA che un verdetto esista. Una chiusura che
    # non risolve per intero non puo' produrre ne' FULL_PASS ne' FULL_FAIL: la
    # capacita' non ha lo schema che dichiarerebbe di aver applicato. Rule H.4.
    if not chiusura_risolve(registro.documenti):
        return _errore_validatore(
            ReasonCode.REF_UNRESOLVABLE, legacy, radice_schema, lib, sha, fingerprint,
            _durata(inizio, adattatori),
        )

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
    except _PercorsoNonRisolto:
        # P0-4 — esplicito, non piu' assorbito dall'except generico qui sotto. Prima
        # un percorso non risolto degradava un FULL_FAIL legittimo a
        # INTERNAL_VALIDATION_ERROR con zero voci: l'errore c'era, la sua posizione no,
        # e il verdetto spariva insieme alla posizione. L'esito resta lo stesso, ma ora
        # e' una decisione dichiarata invece di una cattura accidentale.
        return _errore_validatore(
            ReasonCode.INTERNAL_VALIDATION_ERROR, legacy, radice_schema, lib, sha, fingerprint,
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


#: E1 rev6 §3.4 — le posizioni che il dialetto 2020-12 definisce SCHEMA. Nient'altro
#: viene attraversato: `const`, `default`, `examples` ed `enum` portano DATI, e un
#: `{"$ref": ...}` che vi compare e' un valore JSON legittimo, non un riferimento.
#: Le parole chiave ignote non si attraversano: il dialetto non le definisce schema.
#: R2.1-3 (`A3a`) — `contentSchema` e' la sola posizione-schema di 2020-12 che
#: mancava dalle tre liste, verificato per enumerazione contro i vocabolari core,
#: applicator, unevaluated e content. La sua assenza sbagliava in ENTRAMBE le
#: direzioni: un `$ref` irrisolvibile dichiarato li' dentro non veniva enumerato e la
#: closure risultava risolta (allentamento), mentre un `$anchor` legittimo dichiarato
#: li' dentro non entrava nella mappa e faceva rifiutare l'intero registro (falso
#: positivo, contro `R-FID`: `referencing` 0.37.0 lo risolve).
_KW_SCHEMA_SINGOLO = ("additionalProperties", "propertyNames", "if", "then", "else",
                      "not", "contains", "items", "unevaluatedItems",
                      "unevaluatedProperties", "contentSchema")
_KW_SCHEMA_LISTA = ("allOf", "anyOf", "oneOf", "prefixItems")
_KW_SCHEMA_MAPPA = ("properties", "patternProperties", "$defs", "dependentSchemas")
_KW_RIFERIMENTO = ("$ref", "$dynamicRef")


def _e_schema(nodo) -> bool:
    """Uno schema e' un oggetto o un booleano. Stringhe, numeri, array e null non lo sono."""
    return isinstance(nodo, dict) or isinstance(nodo, bool)


def _sottoschemi(nodo):
    """(pezzo di puntatore, sottoschema) per le sole posizioni-schema del dialetto."""
    if not isinstance(nodo, dict):
        return
    for k in _KW_SCHEMA_SINGOLO:
        if k in nodo:
            yield "/" + _esc(k), nodo[k]
    for k in _KW_SCHEMA_LISTA:
        v = nodo.get(k)
        if isinstance(v, list):
            for i, s in enumerate(v):
                yield "/" + _esc(k) + "/" + str(i), s
    for k in _KW_SCHEMA_MAPPA:
        v = nodo.get(k)
        if isinstance(v, dict):
            for nome, s in v.items():
                yield "/" + _esc(k) + "/" + _esc(str(nome)), s


def _cammina_schemi(documento):
    """Visita ITERATIVA dei nodi-schema. Nessuna ricorsione: E1 rev6 §3.7.

    `chiusura_risolve` e' pubblica e non deve mai lasciar uscire un'eccezione
    (`DEC-5`). La versione ricorsiva cedeva con `RecursionError` non catturata su un
    documento profondo, ed era schermata solo dal fatto che `check_schema` falliva
    prima — una coincidenza fra due profondita', non una difesa.
    """
    pila = [("", documento)]
    visti = set()
    while pila:
        ptr, nodo = pila.pop()
        if not isinstance(nodo, dict) or id(nodo) in visti:
            continue
        visti.add(id(nodo))
        yield ptr, nodo
        for pezzo, sotto in _sottoschemi(nodo):
            pila.append((ptr + pezzo, sotto))


def _ancore(documento, chiavi=("$anchor", "$dynamicAnchor")) -> dict:
    """`$anchor` e `$dynamicAnchor` dichiarati nelle posizioni-schema della risorsa."""
    fuori = {}
    for ptr, nodo in _cammina_schemi(documento):
        for k in chiavi:
            v = nodo.get(k)
            if isinstance(v, str) and v and v not in fuori:
                fuori[v] = (ptr, nodo)
    return fuori


def _identita_risolta(identificatore, base) -> str:
    """`$id` risolto contro la risorsa contenente, RFC 3986. Sui basename e' identita'."""
    try:
        return urllib.parse.urljoin(base, identificatore)
    except Exception:
        return identificatore


def _id_embedded(documento):
    """(puntatore, identita risolta, nodo) per ogni sottoschema con `$id` proprio."""
    for ptr, nodo in _cammina_schemi(documento):
        if ptr == "":
            continue
        identificatore = nodo.get("$id")
        if isinstance(identificatore, str) and identificatore:
            yield ptr, identificatore, nodo


def _risorse_embedded(documenti: dict) -> dict:
    """R2.1-4 (`A3b`) — indice delle risorse EMBEDDED del registro chiuso.

    In Draft 2020-12 un sottoschema con `$id` proprio **e' una risorsa**, e il resolver
    del dialetto la risolve. L'indice conosceva soltanto i documenti di primo livello,
    quindi il preflight rifiutava (`REF_UNRESOLVABLE`) un riferimento che
    `referencing` 0.37.0 risolve: violazione diretta di `R-FID`.

    Il valore e' `(identita contenente, puntatore base, nodo)`. `schema_resource_id`
    resta un documento REGISTRATO e il puntatore lo attraversa per intero: e' cio' che
    tiene valida la dereferenziazione a valle, che `ADR` §4.6 pretende risolvibile.
    Una collisione con un'identita' di primo livello non arriva qui: `F3` l'ha gia'
    respinta come errore d'identita'.
    """
    fuori = {}
    for identita, documento in documenti.items():
        for ptr, identificatore, nodo in _id_embedded(documento):
            risolta = _identita_risolta(identificatore, identita)
            if risolta in documenti or risolta in fuori:
                continue
            fuori[risolta] = (identita, ptr, nodo)
    return fuori


def _segui_ref(riferimento, risorsa_corrente, documenti, embedded=None):
    """Risoluzione INIZIALE di un riferimento dentro il registro chiuso.

    Frammento secondo RFC 3986 — con decodifica percentuale — e puntatore secondo
    RFC 6901. Un frammento non vuoto che non inizia con `/` e' un `$anchor` o un
    `$dynamicAnchor`. Il bersaglio deve essere uno SCHEMA.

    Risoluzione **iniziale**, e non di piu': il binding finale di un `$dynamicRef`
    dipende dallo scope dinamico di valutazione e non e' determinabile staticamente.
    Qui si verifica che il riferimento risolva, non a che cosa si legherà.

    Il bersaglio puo' essere una risorsa **embedded** (R2.1-4). L'indice si calcola
    solo quando la base non e' un documento di primo livello: sul registro sano —
    zero risorse embedded — non costa nulla.
    """
    if not isinstance(riferimento, str):
        raise _PercorsoNonRisolto()
    base, _, frammento = riferimento.partition("#")
    risorsa = base if base else risorsa_corrente
    if risorsa in documenti:
        identita, base_ptr, documento = risorsa, "", documenti[risorsa]
    else:
        if embedded is None:
            embedded = _risorse_embedded(documenti)
        voce = embedded.get(risorsa)
        if voce is None:
            raise _PercorsoNonRisolto()
        identita, base_ptr, documento = voce
    try:
        frammento = urllib.parse.unquote(frammento)
    except Exception:
        raise _PercorsoNonRisolto()
    if not frammento:
        return identita, base_ptr, documento
    if frammento.startswith("/"):
        nodo = documento
        for pezzo in frammento.split("/")[1:]:
            chiave = pezzo.replace("~1", "/").replace("~0", "~")
            try:
                nodo = nodo[chiave] if isinstance(nodo, dict) else nodo[int(chiave)]
            except Exception:
                raise _PercorsoNonRisolto()
        if not _e_schema(nodo):
            raise _PercorsoNonRisolto()
        return identita, base_ptr + frammento, nodo
    voce = _ancore(documento).get(frammento)
    if voce is None:
        raise _PercorsoNonRisolto()
    puntatore, nodo = voce
    if not _e_schema(nodo):
        raise _PercorsoNonRisolto()
    return identita, base_ptr + puntatore, nodo


def _riferimenti(documento, fuori: list) -> None:
    """Riferimenti nelle SOLE posizioni che il dialetto definisce schema."""
    for _ptr, nodo in _cammina_schemi(documento):
        for k in _KW_RIFERIMENTO:
            if k in nodo:
                fuori.append(nodo[k])


def chiusura_risolve(documenti: dict) -> bool:
    """P0-3 — preflight: OGNI `$ref` del registro risolve DENTRO il registro.

    Si esegue prima che un verdetto venga formato, e indipendentemente dall'istanza.
    Un `$ref` rotto sotto `properties.payload` non veniva mai seguito da un'istanza
    priva di `payload`, e la capacita' dichiarava valido un artefatto senza averne
    risolto lo schema: `FULL_PASS` con una chiusura incompleta. La raggiungibilita' di
    un riferimento rotto non puo' dipendere dalla forma dell'istanza — Rule H.4.

    La risoluzione passa da `_segui_ref`, che consulta soltanto i documenti gia'
    caricati: un `$ref` http, file:// o UNC non e' "esterno da recuperare", e' un
    riferimento che il registro chiuso non contiene. Nessuna rete, nessun retrieve.
    """
    embedded = _risorse_embedded(documenti)
    for identita, documento in documenti.items():
        trovati = []
        _riferimenti(documento, trovati)
        for riferimento in trovati:
            try:
                _segui_ref(riferimento, identita, documenti, embedded)
            except _PercorsoNonRisolto:
                return False
    return True


_ASSENTE = object()

#: Valvola di sicurezza sull'AMPIEZZA dell'enumerazione, non sulla profondita'. Non e'
#: un budget di salti — quello e' stato rimosso: era condiviso, mai ripristinato, e
#: oltre 31 `$ref` incatenati faceva degradare un `FULL_FAIL` legittimo a
#: `INTERNAL_VALIDATION_ERROR` con zero voci. La terminazione ora e' data dall'insieme
#: dei visitati. Questo tetto non deve MAI legare: se lega, l'attribuzione torna a
#: dipendere dall'ordine di enumerazione, che e' il difetto `P0-4`.
_MAX_CANDIDATI = 8

#: `K27` — strumentazione dell'AMPIEZZA osservata dall'ultimo azzeramento. Esiste per
#: rendere asseribile che `_MAX_CANDIDATI` **non lega**: se legasse, l'attribuzione
#: tornerebbe a dipendere dall'ordine di enumerazione, che e' il difetto `P0-4`.
#: Nessuna decisione della capacita' legge questo valore.
_STRUMENTAZIONE = {"ampiezza_massima": 0}


def _passo_locale(nodo, segmenti, i):
    """Un passo di cammino DENTRO il nodo corrente: (nodo, pezzo, dichiarato, consumati)."""
    s = segmenti[i]
    ultimo = i == len(segmenti) - 1
    try:
        if s == "properties" and not ultimo:
            nome = segmenti[i + 1]
            return nodo["properties"][nome], "/properties/" + _esc(str(nome)), True, 2
        if s == "items" and not ultimo:
            return nodo["items"], "/items", True, 1
        if s == "prefixItems" and not ultimo:
            idx = segmenti[i + 1]
            return nodo["prefixItems"][idx], "/prefixItems/" + str(idx), True, 2
        if s == "additionalProperties" and not ultimo:
            return nodo["additionalProperties"], "/additionalProperties", False, 1
        if s == "patternProperties" and not ultimo:
            pat = segmenti[i + 1]
            return nodo["patternProperties"][pat], "/patternProperties/" + _esc(str(pat)), False, 2
        return nodo[s], "/" + _esc(str(s)), None, 1
    except Exception:
        raise _PercorsoNonRisolto()


def _bersagli(parola, valore, risorsa, documenti, embedded):
    """(risorsa, puntatore, nodo, dinamico) per una parola chiave di riferimento.

    Per `$ref` il bersaglio e' quello statico, ed e' esatto: `dinamico = False`.

    Per `$dynamicRef` il bersaglio **non e' determinabile staticamente**. Il dialetto
    lo lega al `$dynamicAnchor` omonimo della risorsa piu' esterna dello scope
    dinamico, e lo scope dinamico non e' una proprieta' del registro (`R-PRE`). Una
    risoluzione statica **non e' prova del binding**: misurato, nella costruzione
    `lib`/`pain` con `$dynamicAnchor` omonimi e `type` divergenti produce **un solo**
    candidato ben formato, che punta alla risorsa SBAGLIATA — un'attribuzione ben
    formata e falsa, la stessa classe di `P0-4` per un'altra via.

    Si enumerano quindi **tutti** i binding possibili — ogni risorsa del registro che
    dichiara quel `$dynamicAnchor` — marcati `dinamico = True`. La scelta non si fa
    qui: `_analizza_percorso` ne accetta uno solo se il `validator_value` dell'errore
    lo prova, e altrimenti degrada per `DEC-8`. Non si indovina.
    """
    if parola != "$dynamicRef":
        try:
            yield _segui_ref(valore, risorsa, documenti, embedded) + (False,)
        except _PercorsoNonRisolto:
            return
        return

    visti = set()
    frammento = valore.partition("#")[2] if isinstance(valore, str) else ""
    if frammento and not frammento.startswith("/"):
        try:
            nome = urllib.parse.unquote(frammento)
        except Exception:
            nome = frammento
        for identita, documento in documenti.items():
            voce = _ancore(documento, ("$dynamicAnchor",)).get(nome)
            if voce is None:
                continue
            puntatore, nodo = voce
            if not _e_schema(nodo) or (identita, puntatore) in visti:
                continue
            visti.add((identita, puntatore))
            yield identita, puntatore, nodo, True
    try:
        r2, p2, n2 = _segui_ref(valore, risorsa, documenti, embedded)
    except _PercorsoNonRisolto:
        return
    if (r2, p2) not in visti:
        yield r2, p2, n2, True


def _dichiarati_sicuri(parziali) -> list:
    """`DEC-12` — la declaredness d'ISTANZA che il cammino ha comunque stabilito.

    Il degrado dell'attribuzione di SCHEMA e la redazione della posizione d'ISTANZA
    sono due assi indipendenti: un fallimento sul primo non autorizza a cancellare una
    posizione sicura come `/k`. Il prefisso di cammino che risolve determina la
    declaredness dei segmenti d'istanza corrispondenti; il residuo resta ignoto, e
    `costruisci_posizioni` lo redige.

    Voto conservativo: un segmento e' dichiarato solo se **ogni** cammino che lo ha
    raggiunto lo dichiara. Due rami che dissentono non producono una disclosure.
    """
    if not parziali:
        return []
    lunghezza = max(len(p) for p in parziali)
    return [all(p[j] for p in parziali if len(p) > j) for j in range(lunghezza)]


def _candidati(risorsa, nodo, puntatore, dichiarati, segmenti, i, documenti, fuori,
               parziali=None, embedded=None):
    """Enumera le risoluzioni possibili del percorso di schema. Visita ITERATIVA.

    `jsonschema` NON mette i riferimenti in `absolute_schema_path`: dopo un nodo che ne
    porta uno, il segmento successivo puo' appartenere al bersaglio oppure a un sibling
    locale che condivide la stessa chiave. Sono due risoluzioni entrambe ben formate, e
    la vecchia guardia ne sceglieva sempre una — quella locale — anche quando l'errore
    veniva dall'altra. E' cosi' che `evidence-pack` riportava un `required` che contiene
    `["promoted_by"]` per una `id` mancante che vive in `core.schema.json#/$defs/Claim`.

    Si seguono **tutte** le parole chiave di riferimento del dialetto, non la sola
    `$ref`: con `$dynamicRef` nessun candidato risultava ben formato, il chiamante
    sollevava, e un `FULL_FAIL` legittimo diventava `VALIDATOR_ERROR` con zero voci —
    `P0-4` ancora vivo su un'altra parola chiave.

    R2.1-2 (`A1`) — seguire la parola chiave non bastava, e il ramo aggiunto era
    **codice morto**: `jsonschema` METTE `$dynamicRef` in `absolute_schema_path` e NON
    mette `$ref`. Reimpilare il ramo con lo **stesso** indice di segmento e' corretto
    per `$ref` e sbagliato per `$dynamicRef`, che restava un segmento mai consumato.
    Misurato:

        $dynamicRef  absolute_schema_path = ['items','$dynamicRef','type']  cand. 0
        $ref         absolute_schema_path = ['items','type']                cand. 1

    Il segmento si consuma quindi **se e solo se** il percorso lo porta.

    Nessun budget globale di salti. La terminazione e' data dall'insieme dei visitati su
    `(risorsa, puntatore, indice di segmento)`: una catena lecita non degrada per la
    propria lunghezza, e un ciclo di riferimenti non fa girare a vuoto.

    `parziali`, se dato, raccoglie la declaredness di ogni stato visitato: serve a
    `DEC-12`, perche' anche un cammino che non arriva in fondo ha stabilito qualcosa
    sulla posizione d'ISTANZA, e quel qualcosa non si butta.
    """
    if embedded is None:
        embedded = _risorse_embedded(documenti)
    pila = [(risorsa, nodo, puntatore, tuple(dichiarati), i, False)]
    visti = set()
    while pila and len(fuori) < _MAX_CANDIDATI:
        ris, nd, ptr, dic, k, dinamico = pila.pop()
        chiave = (ris, ptr, k)
        if chiave in visti:
            continue
        visti.add(chiave)
        if parziali is not None:
            parziali.append(dic)
        if k >= len(segmenti):
            fuori.append((ris, ptr, list(dic), nd, dinamico))
            continue

        # Il ramo del riferimento si impila per PRIMO, quindi si estrae per ULTIMO:
        # cosi' il ramo locale resta esplorato per primo e, senza ambiguita', l'ordine
        # dei candidati riproduce esattamente il cammino storico.
        if isinstance(nd, dict):
            for parola in _KW_RIFERIMENTO:
                if parola not in nd:
                    continue
                salto = 1 if segmenti[k] == parola else 0
                for r2, p2, n2, din in _bersagli(parola, nd[parola], ris,
                                                 documenti, embedded):
                    pila.append((r2, n2, p2, dic, k + salto, dinamico or din))

        try:
            nodo2, pezzo, dichiarato, passi = _passo_locale(nd, segmenti, k)
        except _PercorsoNonRisolto:
            pass
        else:
            avanti = dic + ((dichiarato,) if dichiarato is not None else ())
            pila.append((ris, nodo2, ptr + pezzo, avanti, k + passi, dinamico))

    if len(fuori) > _STRUMENTAZIONE["ampiezza_massima"]:
        _STRUMENTAZIONE["ampiezza_massima"] = len(fuori)


def _analizza_percorso(radice_id, schema_radice, documenti, percorso_schema,
                       valore_atteso=_ASSENTE):
    """Ritorna (risorsa, puntatore, dichiarati) camminando il percorso di schema.

    `dichiarati[i]` dice se il segmento i-esimo del percorso di ISTANZA e' una
    chiave dichiarata dallo schema in quella posizione (o un indice di array).

    `valore_atteso` e' il `validator_value` dell'errore: il valore di schema che ha
    effettivamente fallito. Quando due risoluzioni sono entrambe ben formate, e' cio'
    che le distingue — il nodo terminale corretto E' quel valore. §4.6 chiede che
    `schema_resource_id` sia la risorsa che contiene davvero la parola chiave, e questa
    e' l'unica informazione disponibile che lo determina.

    Per i candidati raggiunti attraverso un `$dynamicRef` il `validator_value` non e'
    un aiuto ma la **prova**: e' l'unico dato che dice quale binding di runtime ha
    davvero prodotto l'errore. Senza quella prova si degrada per `DEC-8`.
    """
    fuori = []
    parziali = []
    _candidati(radice_id, schema_radice, "", [], list(percorso_schema), 0,
               documenti, fuori, parziali)

    # I candidati STATICI sono esatti e conservano il comportamento storico, riga per
    # riga: `$ref` non e' toccato da R2.1-2.
    statici = [c for c in fuori if not c[4]]
    if statici:
        if valore_atteso is not _ASSENTE and len(statici) > 1:
            esatti = [c for c in statici if c[3] == valore_atteso]
            if len(esatti) == 1:
                return esatti[0][0], esatti[0][1], esatti[0][2]
        return statici[0][0], statici[0][1], statici[0][2]

    # Nessun candidato statico: se ne esistono di DINAMICI, uno solo e' accettabile —
    # quello il cui nodo terminale E' il valore di schema che ha fallito. Con zero
    # prove o con piu' di un candidato compatibile il binding non e' dimostrabile, e
    # si degrada: attribuire "il primo" sarebbe ben formato e falso.
    dinamici = [c for c in fuori if c[4]]
    if dinamici and valore_atteso is not _ASSENTE:
        esatti = [c for c in dinamici if c[3] == valore_atteso]
        if len(esatti) == 1:
            return esatti[0][0], esatti[0][1], esatti[0][2]

    # `DEC-8` — un fallimento di ATTRIBUZIONE non tocca il verdetto: si degrada la sola
    # precisione dell'attribuzione di SCHEMA — risorsa = radice selezionata, puntatore
    # vuoto. Prima si sollevava, e il verdetto spariva insieme alla posizione: era
    # `P0-4`.
    #
    # `DEC-12` — e si degrada SOLO quella. La posizione d'ISTANZA vive sull'altro asse
    # e si conserva per quanto il cammino l'ha accertata: un record con
    # `schema_pointer = ""`, `instance_pointer = "/k"` e `location_is_exact = true` e'
    # conforme, e dice esattamente cio' che si sa. Il commento precedente prometteva
    # che questo ritorno facesse redigere la posizione e porre
    # `location_is_exact = false`: dopo `DEC-12` sarebbe normativamente falso.
    return radice_id, "", _dichiarati_sicuri(parziali)


def costruisci_posizioni(percorso_istanza, dichiarati):
    """(instance_pointer, instance_location, location_is_exact) — §4.8.

    `instance_pointer` e' RFC 6901 ed e' None appena un segmento e' redatto.
    `instance_location` e' RLE: come RFC 6901 ma un segmento puo' essere `~R`.
    """
    segmenti = list(percorso_istanza)
    dichiarati = list(dichiarati)
    if len(dichiarati) < len(segmenti):
        # `DEC-12` — il prefisso accertato si CONSERVA e si redige il solo residuo
        # ignoto. Il ricalcolo integrale — «solo gli indici interi sono dichiarati» —
        # buttava anche cio' che il cammino aveva stabilito: una `/k` sicura diventava
        # `/~R` per una ragione che vive sull'altro asse, ed e' l'accoppiamento che
        # `DEC-12` scioglie. Il residuo resta redatto: non accertato, non emesso.
        dichiarati += [False] * (len(segmenti) - len(dichiarati))
    elif len(dichiarati) > len(segmenti):
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
        # Il valore di schema che ha fallito e' cio' che distingue due risoluzioni
        # entrambe ben formate quando un `$ref` ha sibling locali (P0-4).
        risorsa, puntatore, dichiarati = _analizza_percorso(
            radice_id, schema_radice, documenti,
            getattr(errore, "absolute_schema_path", []),
            getattr(errore, "validator_value", _ASSENTE),
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


def _ricevuta_errore(codice: WriteReasonCode, record_id=None, cleanup=None) -> WriteReceipt:
    return WriteReceipt(
        status=WriteStatus.WRITE_ERROR, write_reason_code=codice, record_id=record_id,
        final_name=None, cleanup_status=cleanup,
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
        # Nessun pending e' stato creato: `cleanup_status` resta null.
        return _ricevuta_errore(WriteReasonCode.TEMP_WRITE_FAILED, record_id)
    percorso_pending = os.path.join(radice, nome_pending)
    percorso_finale = os.path.join(radice, nome_finale)
    try:
        fh = adattatori.publisher.apri(percorso_pending)
    except Exception:
        # L'apertura non e' riuscita: non si puo' affermare che un pending esista, e
        # tentare comunque la rimozione riporterebbe PENDING_CLEANUP per un file che
        # non c'e'. Da qui in avanti, invece, il pending esiste.
        return _ricevuta_errore(WriteReasonCode.TEMP_WRITE_FAILED, record_id)

    # La pulizia non puo' avvenire dentro il `finally`: l'espressione di `return` viene
    # valutata PRIMA che il finally chiuda l'handle, e su Windows la rimozione di un
    # file ancora aperto fallisce — riporteremmo PENDING_CLEANUP per una condizione che
    # abbiamo creato noi. Si registra il codice, si chiude, poi si pulisce.
    codice = None
    try:
        try:
            adattatori.publisher.scrivi(fh, blob)
        except Exception:
            codice = WriteReasonCode.TEMP_WRITE_FAILED
        if codice is None:
            try:
                adattatori.publisher.sincronizza(fh)
            except Exception:
                codice = WriteReasonCode.FLUSH_FSYNC_FAILED
    finally:
        try:
            fh.close()
        except Exception:
            pass
    if codice is not None:
        return _ricevuta_errore(codice, record_id, _pulisci(percorso_pending, adattatori))

    try:
        adattatori.publisher.pubblica(percorso_pending, percorso_finale)
    except FileExistsError:
        return WriteReceipt(
            status=WriteStatus.COLLISION,
            write_reason_code=WriteReasonCode.COLLISION_EXISTING_TARGET,
            record_id=record_id,
            final_name=nome_finale,
            cleanup_status=_pulisci(percorso_pending, adattatori),
        )
    except Exception:
        return _ricevuta_errore(WriteReasonCode.PUBLISH_FAILED, record_id,
                                _pulisci(percorso_pending, adattatori))

    # A2 §2 — da questa riga in poi il record E' pubblicato: `os.link` e' atomico e ha
    # gia' avuto successo. La rimozione del pending e' PULIZIA, e il suo esito non puo'
    # piu' cambiare lo stato della pubblicazione. `PUBLISH_FAILED` resta riservato al
    # caso in cui `os.link` stesso fallisce per un motivo che non sia la collisione,
    # che e' quello che §4.2 dice da sempre che significhi.
    return WriteReceipt(
        status=WriteStatus.WRITTEN,
        write_reason_code=None,
        record_id=record_id,
        final_name=nome_finale,
        cleanup_status=_pulisci(percorso_pending, adattatori),
    )


def _pulisci(percorso: str, adattatori) -> CleanupStatus:
    """Rimuove il pending e RIPORTA l'esito. Non lo ingoia piu'.

    Prima era `_rimuovi_silenzioso`, e il silenzio era il difetto: un pending poteva
    sopravvivere anche a un WRITE_ERROR senza che nulla lo dicesse.
    """
    try:
        adattatori.publisher.rimuovi(percorso)
    except Exception:
        return CleanupStatus.PENDING_CLEANUP
    return CleanupStatus.CLEANED


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
