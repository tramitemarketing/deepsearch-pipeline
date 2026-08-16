#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEEPSEARCH — aggregatore deterministico dei record shadow (ADR-M0.1-001 rev9 §4.13).

Consumatore, non produttore: legge solo i file che rispettano la grammatica
finale, con lettura LIMITATA a MAX_RECORD_BYTES + 1 byte per candidato, valida
ogni record contro il contratto prima di contarlo e stampa JSON canonico.

Non persiste nulla. Non stampa mai contenuto grezzo di un record.
La quiescenza non e' MAI inferita dall'assenza di file pending.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from ds_validate_contract import (
    FINAL_NAME,
    MAX_RECORD_BYTES,
    PENDING_NAME,
    AggregateReport,
    Comparison,
    Completeness,
    FullOutcome,
    ReasonCode,
    canonical_json,
    chiavi_zero,
    valida_contratto_record,
)


def _leggi_limitato(percorso: str, apri) -> Any:
    """Legge al massimo MAX_RECORD_BYTES + 1 byte. Il resto non viene MAI caricato."""
    with apri(percorso) as fh:
        return fh.read(MAX_RECORD_BYTES + 1)


def _aggrega_con_lettore(radice: str, *, quiescent: bool, apri) -> AggregateReport:
    # `E2` rev3 §5.3 — UNA sola passata, nessuna `stat` per voce: `os.scandir` fornisce
    # `is_file(follow_symlinks=False)` senza syscall aggiuntive nel caso comune, e un
    # link simbolico non conta come file regolare. L'implementazione precedente usava
    # `os.listdir` + `os.path.isfile`, che §5.2/§5.3 non prescrivono.
    try:
        with os.scandir(radice) as it:
            voci = sorted(it, key=lambda v: v.name)
    except Exception:
        voci = []

    # `DEC-10` — «corrupt, non silenzio». Si rileva OGNI file regolare della directory;
    # un nome che non rispetta la grammatica finale o pending e' contato come
    # **corrotto senza aprirne il contenuto**. Prima un file dal nome malformato era
    # invisibile: `records_discovered` non lo vedeva e nessun segnale ne restava.
    #
    # `records_discovered` e' la partizione dei file regolari MENO i pending validi.
    # Definirlo per sottrazione e' cio' che preserva `AGG:1`
    # (`discovered = valid + corrupt`): con «ogni file regolare» a pendenti invariati
    # l'invariante si romperebbe, e `invarianti_aggregato` non si tocca.
    #
    # R2.1-5 (`A5`) — «errore di ACCESSO» e «non e' un file» sono due cose diverse, e
    # `os.path.isfile` le fondeva: inghiotte `OSError` e restituisce `False`, quindi
    # una voce il cui tipo non e' determinabile spariva — silenzio, non `corrupt` —
    # e il ramo `except Exception: continue` era morto per quel trigger. Una voce
    # indeterminabile conta ora `corrupt` e resta dentro `records_discovered`:
    # fail-closed, e `AGG:1` per costruzione.
    regolari = []
    indeterminate = 0
    for v in voci:
        try:
            regolare = v.is_file(follow_symlinks=False)
        except OSError:
            indeterminate += 1
            continue
        if regolare:
            regolari.append(v.name)

    # A2 §5 — i pending si CONTANO dal nome e non si aprono mai. La grammatica pending
    # non puo' coincidere con quella finale, quindi questo conteggio e' disgiunto da
    # `candidati` per costruzione e non entra in alcun bucket di record. E' un segnale
    # operativo sulla directory: dice che una pulizia e' rimasta indietro, non che
    # esista un artefatto in piu'. La quiescenza continua a NON essere inferita da qui.
    pendenti = len([n for n in regolari if PENDING_NAME.match(n) is not None])
    candidati = [n for n in regolari if FINAL_NAME.match(n) is not None]
    non_conformi = [n for n in regolari
                    if FINAL_NAME.match(n) is None and PENDING_NAME.match(n) is None]
    scoperti = len(candidati) + len(non_conformi) + indeterminate
    corrotti = len(non_conformi) + indeterminate
    validi = []  # (record_id, record)

    for nome in candidati:
        percorso = os.path.join(radice, nome)
        try:
            grezzo = _leggi_limitato(percorso, apri)
        except Exception:
            corrotti += 1
            continue
        if not isinstance(grezzo, (bytes, bytearray)) or len(grezzo) > MAX_RECORD_BYTES:
            corrotti += 1
            continue
        try:
            record = json.loads(bytes(grezzo).decode("utf-8"))
        except Exception:
            corrotti += 1
            continue
        if valida_contratto_record(record):
            corrotti += 1
            continue
        if nome[: -len(".json")] != record["record_id"]:
            corrotti += 1
            continue
        validi.append((record["record_id"], record))

    # deduplicazione: per ogni (invocation_id, attempt) vince il record_id
    # lessicograficamente piu' piccolo.
    gruppi = {}
    for record_id, record in validi:
        chiave = (record["invocation_id"], record["attempt"])
        gruppi.setdefault(chiave, []).append((record_id, record))

    gruppi_duplicati = 0
    extra_duplicati = 0
    contati = []
    for chiave in sorted(gruppi):
        membri = sorted(gruppi[chiave], key=lambda t: t[0])
        if len(membri) > 1:
            gruppi_duplicati += 1
            extra_duplicati += len(membri) - 1
        contati.append(membri[0][1])

    per_esito = chiavi_zero(FullOutcome)
    per_ragione = chiavi_zero(ReasonCode)
    per_confronto = chiavi_zero(Comparison)
    for record in contati:
        per_esito[record["full_outcome"]] += 1
        per_confronto[record["comparison"]] += 1
        if record["reason_code"] is not None:
            per_ragione[record["reason_code"]] += 1

    return AggregateReport(
        completeness=Completeness.COMPLETE if quiescent is True else Completeness.UNKNOWN,
        records_discovered=scoperti,
        records_valid=len(validi),
        records_corrupt=corrotti,
        duplicate_groups=gruppi_duplicati,
        duplicate_extra_records=extra_duplicati,
        records_counted=len(contati),
        pending_files=pendenti,
        by_full_outcome=per_esito,
        by_reason_code=per_ragione,
        by_comparison=per_confronto,
    )


def aggrega(radice: str, *, quiescent: bool) -> AggregateReport:
    """Firma normativa dell'ADR §4.13. Nessun parametro aggiuntivo."""
    return _aggrega_con_lettore(radice, quiescent=quiescent, apri=_apri_binario)


def _apri_binario(percorso: str):
    return open(percorso, "rb")


def invarianti_aggregato(rapporto: AggregateReport) -> list:
    """Le cinque invarianti di §4.13. Lista vuota == tutte soddisfatte."""
    v = []
    if rapporto.records_discovered != rapporto.records_valid + rapporto.records_corrupt:
        v.append("AGG:1")
    if rapporto.records_counted != rapporto.records_valid - rapporto.duplicate_extra_records:
        v.append("AGG:2")
    if sum(rapporto.by_full_outcome.values()) != rapporto.records_counted:
        v.append("AGG:3")
    if sum(rapporto.by_comparison.values()) != rapporto.records_counted:
        v.append("AGG:4")
    if sum(rapporto.by_reason_code.values()) > rapporto.records_counted:
        v.append("AGG:5")
    return v


def rendi_stdout(rapporto: AggregateReport) -> str:
    return canonical_json(rapporto.to_dict()) + "\n"


def main(argv) -> int:
    if len(argv) < 2:
        sys.stderr.write("uso: ds_validate_report.py <radice> [--quiescent]\n")
        return 2
    quiescent = "--quiescent" in argv[2:]
    rapporto = aggrega(argv[1], quiescent=quiescent)
    sys.stdout.write(rendi_stdout(rapporto))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
