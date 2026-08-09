#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEEPSEARCH — concorrenza reale fra processi sulla pubblicazione (A2 §7.10).

File a se' per una ragione: la zona era dichiarata NON ATTACCATA nel primo giro
avversariale, gate la ri-dichiarazione di `U2`, e richiede `multiprocessing spawn`
su Windows. Tenerla dentro il self-test generale ne avrebbe nascosto il requisito di
piattaforma e mescolato gli effetti sul filesystem con quelli di tutte le altre
sezioni.

Prova una cosa sola, e la prova con processi veri: piu' processi che pubblicano lo
STESSO `record_id` producono esattamente un `WRITTEN`, esattamente un file finale, e
il file non viene mai sovrascritto. `os.link` fallisce con `FileExistsError` se il
nome finale esiste gia', su Windows come su POSIX: e' la primitiva su cui poggia
l'intera garanzia, e qui la si esercita invece di assumerla.

Uso:  python tools/ds_validate_concurrency.py
Oppure importato: `esegui(schemi, tmp)` restituisce [(nome, esito, dettaglio)].
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ds_validate as V  # noqa: E402
from ds_validate_contract import (  # noqa: E402
    CleanupStatus,
    LegacyOutcome,
    ValidationContext,
    WriteStatus,
    sha256_hex,
    valida_contratto_record,
)

QUI = os.path.dirname(os.path.abspath(__file__))
SCHEMI_PREDEFINITI = os.path.join(os.path.dirname(QUI), "schemas")
ARTEFATTO = "a" * 64
PROCESSI = 4


def _contesto(invocation_id="inv-conc", attempt=1) -> ValidationContext:
    return ValidationContext(
        invocation_id=invocation_id,
        attempt=attempt,
        run_id="20260809-0000",
        role="pain-finder",
        stage="pain",
        artifact_id=ARTEFATTO,
        schema_ref="core.schema.json",
        legacy_outcome=LegacyOutcome.ACCEPTED,
    )


def _adattatori(schemi, record_id):
    return V.Adattatori(
        importer=V._importa_predefinito,
        reader=V.LettoreSchemi(schemi),
        publisher=V.Pubblicatore(),
        clock=V.Orologio(),
        id_generator=lambda: record_id,
    )


def worker(radice, record_id, schemi):
    """Deve stare a livello di modulo: con `spawn` il figlio re-importa questo file."""
    ese = V._esegui_shadow_core(
        {},
        _contesto(),
        config=V.ConfigTest(attivo=True, radice_output=None),
        adattatori=_adattatori(schemi, record_id),
    )
    ric = V.scrivi_record(ese.validation_result, _contesto(), radice,
                          _adattatori(schemi, record_id))
    return "|".join((
        ric.status.value,
        ric.write_reason_code.value if ric.write_reason_code else "-",
        ric.cleanup_status.value if ric.cleanup_status else "-",
    ))


def esegui(schemi=None, tmp=None) -> list:
    """Restituisce [(nome, esito booleano, dettaglio)]. Non stampa nulla."""
    schemi = schemi or SCHEMI_PREDEFINITI
    esiti = []

    def nota(nome, ok, dettaglio=""):
        esiti.append((nome, bool(ok), dettaglio if not ok else ""))

    proprio = tmp is None
    cartella = tempfile.mkdtemp(prefix="ds-conc-") if proprio else tmp
    try:
        ctx = multiprocessing.get_context("spawn")
        radice = os.path.join(cartella, "stesso-record-id")
        os.makedirs(radice, exist_ok=True)
        stesso = "c" * 32

        with ctx.Pool(PROCESSI) as pool:
            risposte = pool.starmap(worker, [(radice, stesso, schemi)] * PROCESSI)

        vincitori = [r for r in risposte if r.startswith(WriteStatus.WRITTEN.value + "|")]
        perdenti = [r for r in risposte
                    if r.startswith(WriteStatus.COLLISION.value + "|COLLISION_EXISTING_TARGET")]
        finali = sorted(n for n in os.listdir(radice) if n.endswith(".json"))

        nota("A2.10 spawn: un solo WRITTEN fra %d processi" % PROCESSI,
             len(vincitori) == 1, str(risposte))
        nota("A2.10 spawn: gli altri %d perdono per COLLISION, non per errore" % (PROCESSI - 1),
             len(perdenti) == PROCESSI - 1, str(risposte))
        nota("A2.10 spawn: esattamente un file finale", finali == [stesso + ".json"], str(finali))

        percorso = os.path.join(radice, stesso + ".json")
        with open(percorso, "rb") as fh:
            grezzo = fh.read()
        impronta = sha256_hex(grezzo)
        try:
            record = json.loads(grezzo.decode("utf-8"))
        except Exception:
            record = None
        nota("A2.10 spawn: il record superstite e' conforme al contratto",
             record is not None and valida_contratto_record(record) == [],
             "non deserializzabile" if record is None else str(valida_contratto_record(record)[:3]))

        # Nessuna sovrascrittura: un secondo giro sullo stesso id non tocca i byte.
        with ctx.Pool(PROCESSI) as pool:
            risposte2 = pool.starmap(worker, [(radice, stesso, schemi)] * PROCESSI)
        with open(percorso, "rb") as fh:
            impronta2 = sha256_hex(fh.read())
        nota("A2.10 spawn: un secondo giro non sovrascrive nulla",
             impronta2 == impronta and all(not r.startswith(WriteStatus.WRITTEN.value + "|")
                                           for r in risposte2),
             str(risposte2))

        residui = [n for n in os.listdir(radice) if n.endswith(".pending")]
        nota("A2.10 spawn: nessun pending sopravvive alla contesa",
             residui == [], str(residui))
        nota("A2.10 spawn: il vincitore riporta la pulizia eseguita",
             vincitori and vincitori[0].endswith("|" + CleanupStatus.CLEANED.value),
             str(vincitori))
    finally:
        if proprio:
            for n in os.listdir(os.path.join(cartella, "stesso-record-id")):
                os.unlink(os.path.join(cartella, "stesso-record-id", n))
            os.rmdir(os.path.join(cartella, "stesso-record-id"))
            os.rmdir(cartella)
    return esiti


def main() -> int:
    esiti = esegui()
    falliti = [(n, d) for n, ok, d in esiti if not ok]
    for n, d in falliti:
        sys.stdout.write("FALLITO  " + n + ("  [" + d + "]" if d else "") + "\n")
    sys.stdout.write("concorrenza: %d controlli, %d superati, %d falliti\n"
                     % (len(esiti), len(esiti) - len(falliti), len(falliti)))
    return 0 if not falliti else 1


if __name__ == "__main__":
    raise SystemExit(main())
