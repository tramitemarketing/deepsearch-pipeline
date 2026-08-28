#!/usr/bin/env python3
"""
replay_offline_selftest.py — dimostra che `replay_offline.py` NON e' cieco.

INTERAMENTE OFFLINE. Nessuna prova legge la rete, l'orologio, un agente o `runtime/`. Il pack di
base e' INCORPORATO qui sotto — e' il pack di `runtime/output/evidence-pack-20260822-2331.json`
con le due sole correzioni prescritte (`promotion_rule_met` su
`/payload/contradictory_evidence/0`, snippet di `/payload/contradictory_evidence/0/evidence/0`
troncato a 300 caratteri) — e il contratto si rigenera con `contract_compile.py`, mai a mano.

Ogni run di prova vive in una directory temporanea e viene rimosso.

  uso:  python tools/replay_offline_selftest.py
        exit 0 = tutte verdi
"""
import ast, contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):
    try: _f.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except Exception: pass

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

import pack_repair                                                        # noqa: E402
import replay_offline as ro                                               # noqa: E402
import replay_validate                                                    # noqa: E402

ESITI = []
_CONTRATTO = None
MARCATORE = "```json"


def prova(nome, descrizione):
    def deco(f):
        ESITI.append((nome, descrizione, f))
        return f
    return deco


# ------------------------------------------------------------------ il pack di base
#
# Incorporato di proposito: il selftest non deve leggere `runtime/`. E' il pack del replay
# `20260822-2331` con le due sole correzioni prescritte dal mandato.

_PACK_BASE = r"""
{
 "agent": "evidence-researcher",
 "run_id": "20260822-2331",
 "status": "OK",
 "budget_used": {
  "searches": 7,
  "searches_limit": 8,
  "fetches": 3,
  "fetches_limit": 16
 },
 "stop_reason": "COMPLETED",
 "payload": {
  "pain_id": "P-003",
  "channel": "normativa",
  "claims": [
   {
    "id": "C-r22n-001",
    "promoted_by": "FP1",
    "statement": {
     "subject": "gestori di strutture ricettive extra-alberghiere",
     "observed": "riscuotono e versano la tassa di soggiorno ai comuni secondo il D.Lgs. 471/1997",
     "quantity": null,
     "period": "trimestrale",
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0001",
     "S-20260822-2331-0002"
    ],
    "promotion_rule_met": "1xT1",
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0001",
      "snippet": "Decreto legislativo del 18/12/1997 n. 471 e successive modifiche e integrazioni. Art. 13 - Sanzioni amministrative per violazioni in materia di tassa di soggiorno.",
      "snippet_source": "FETCHED"
     },
     {
      "source_id": "S-20260822-2331-0002",
      "snippet": "Il gestore della struttura ricettiva è responsabile della riscossione e del versamento della tassa di soggiorno agli enti locali secondo il regolamento comunale.",
      "snippet_source": "SERP"
     }
    ]
   },
   {
    "id": "C-r22n-002",
    "promoted_by": "FP2",
    "statement": {
     "subject": "omesso o ritardato versamento della tassa di soggiorno",
     "observed": "è soggetto a sanzione amministrativa pari al 30% dell'importo dovuto",
     "quantity": {
      "label": "Sanzione per omesso versamento",
      "value": null,
      "value_min": 30,
      "value_max": 30,
      "unit": "% dell'importo dovuto",
      "state": "VERIFIED"
     },
     "period": null,
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0001",
     "S-20260822-2331-0003"
    ],
    "promotion_rule_met": "1xT1",
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0001",
      "snippet": "Art. 13 D.Lgs. 471/1997: la sanzione per omesso, parziale o ritardato versamento della tassa di soggiorno è pari al 30% dell'importo non versato.",
      "snippet_source": "FETCHED"
     },
     {
      "source_id": "S-20260822-2331-0003",
      "snippet": "Sanzione amministrativa pecuniaria del 30% dell'importo non versato, in conformità all'articolo 13 del Decreto Legislativo 471/1997.",
      "snippet_source": "SERP"
     }
    ]
   },
   {
    "id": "C-r22n-003",
    "promoted_by": "FP2",
    "statement": {
     "subject": "omessa dichiarazione annuale della tassa di soggiorno all'Agenzia delle Entrate",
     "observed": "è soggetta a sanzione amministrativa dal 100% al 200% dell'importo dovuto",
     "quantity": {
      "label": "Sanzione per omessa dichiarazione annuale",
      "value": null,
      "value_min": 100,
      "value_max": 200,
      "unit": "% dell'importo dovuto",
      "state": "ESTIMATED",
      "formula": "Percentuale citata da fonte legale specializzata (Paratore & Partners, T2) con riferimento esplicito all'art. 13 D.Lgs. 471/1997. Intervallo su best practice di settore.",
      "input_source_ids": [
       "S-20260822-2331-0003"
      ]
     },
     "period": "annuale",
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0003"
    ],
    "promotion_rule_met": "1xT2",
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0003",
      "snippet": "Per omessa, incompleta o infedele dichiarazione, la sanzione amministrativa pecuniaria è dal 100% al 200% dell'importo dovuto secondo l'articolo 13 del D.Lgs. 471/1997.",
      "snippet_source": "SERP"
     }
    ]
   },
   {
    "id": "C-r22n-004",
    "promoted_by": "FP3",
    "statement": {
     "subject": "dichiarazione annuale della tassa di soggiorno all'Agenzia delle Entrate",
     "observed": "deve essere presentata entro il 30 giugno dell'anno successivo, senza proroghe dichiarate",
     "quantity": null,
     "period": "annuale",
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0004",
     "S-20260822-2331-0005"
    ],
    "promotion_rule_met": "2xT3-indipendenti",
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0004",
      "snippet": "La dichiarazione annuale deve essere presentata telematicamente all'Agenzia delle Entrate entro il 30 giugno dell'anno successivo. Non sono previste proroghe.",
      "snippet_source": "SERP"
     },
     {
      "source_id": "S-20260822-2331-0005",
      "snippet": "Scadenza dichiarazione: 30 giugno. La presentazione tardiva o l'omissione espone a sanzioni amministrative. La proroga è eccezione rara e dichiarata con comunicato di ente.",
      "snippet_source": "SERP"
     }
    ]
   },
   {
    "id": "C-r22n-005",
    "promoted_by": "FP2",
    "statement": {
     "subject": "il reato penale di peculato per omesso versamento della tassa di soggiorno",
     "observed": "è stato depenalizzato dal decreto rilancio (D.L. 34/2020) a partire dal 19 maggio 2020",
     "quantity": null,
     "period": "dal 2020 in avanti",
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0006"
    ],
    "promotion_rule_met": "1xT2",
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0006",
      "snippet": "La depenalizzazione è entrata in vigore il 19 maggio 2020 attraverso l'articolo 180 del Decreto Rilancio. Il gestore è ora 'responsabile del pagamento' anziché 'agente contabile', eliminando formalmente il reato di peculato.",
      "snippet_source": "FETCHED"
     }
    ]
   },
   {
    "id": "C-r22n-006",
    "promoted_by": "FP3",
    "statement": {
     "subject": "il ravvedimento operoso per omesso versamento della tassa di soggiorno",
     "observed": "permette di regolarizzare il debito con una riduzione della sanzione fino al 3.75% per versamenti oltre un anno dopo la scadenza",
     "quantity": {
      "label": "Riduzione sanzione via ravvedimento operoso",
      "value": 3.75,
      "unit": "% della sanzione ordinaria",
      "state": "VERIFIED",
      "source_ids": [
       "S-20260822-2331-0007"
      ]
     },
     "period": "oltre 1 anno dal versamento",
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0007"
    ],
    "promotion_rule_met": "1xT3",
    "evidence_kind": "ARTEFATTO",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0007",
      "snippet": "Il ravvedimento operoso ultrannuale riduce la sanzione al 3.75% (1/8 della sanzione ordinaria del 30%). Applicabile a tributi comunali tra cui la tassa di soggiorno.",
      "snippet_source": "SERP"
     }
    ]
   },
   {
    "id": "C-r22n-007",
    "promoted_by": "FP0",
    "statement": {
     "subject": "gestori di strutture ricettive extra-alberghiere",
     "observed": "non dispongono di agevolazioni o proroghe specifiche rispetto agli obblighi di versamento e dichiarazione della tassa di soggiorno",
     "quantity": null,
     "period": "dal 1998 in avanti",
     "geo": "IT"
    },
    "state": "HYPOTHESIS",
    "source_ids": [
     "S-20260822-2331-0008"
    ],
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0008",
      "snippet": "Nessuna agevolazione, proroghe o deroghe dedicate ai piccoli gestori di strutture ricettive sono documentate nella ricerca normativa ufficiale.",
      "snippet_source": "SERP"
     }
    ]
   }
  ],
  "q4_answer": {
   "question_id": "Q4",
   "grounded": true,
   "answer": "Il D.Lgs. 471/1997 art. 13, pubblicato in Gazzetta Ufficiale il 8 gennaio 1998 (entrata in vigore 1 aprile 1998), stabilisce obblighi di versamento e dichiarazione della tassa di soggiorno per gestori di strutture ricettive con sanzioni amministrative (30% per omesso versamento, 100-200% per omessa dichiarazione annuale). La norma è stata depenalizzata nel 2020 quanto alla rilevanza penale, ma le sanzioni amministrative rimangono in vigore. La dichiarazione annuale deve essere presentata entro il 30 giugno senza proroghe. Il ravvedimento operoso consente riduzioni fino al 3.75% ma non elimina l'obbligatorietà.",
   "source_ids": [
    "S-20260822-2331-0001",
    "S-20260822-2331-0003",
    "S-20260822-2331-0006"
   ]
  },
  "coverage": {
   "angles_covered": [
    "normativa",
    "fallimenti_storici"
   ],
   "gaps": [
    "Sentenze concrete: ricerca 'sentenze comuni sanzionano gestori case vacanza' ha prodotto risultati su reati di peculato ma non accesso al testo integrale di sentenze. Trovata conferma della rilevanza giudiziale ma non il dettaglio di un caso specifico con importo."
   ]
  },
  "contradictory_evidence": [
   {
    "id": "C-r22n-contra-001",
    "promoted_by": "FP2",
    "statement": {
     "subject": "il reato penale di peculato per omesso versamento della tassa di soggiorno",
     "observed": "è stato rimosso come conseguenza penale ma rimangono le sanzioni amministrative e l'incertezza giuridica sulla sua applicabilità residua",
     "quantity": null,
     "period": "dal 19 maggio 2020",
     "geo": "IT"
    },
    "state": "VERIFIED",
    "source_ids": [
     "S-20260822-2331-0006"
    ],
    "evidence_kind": "PUBBLICAZIONE",
    "evidence": [
     {
      "source_id": "S-20260822-2331-0006",
      "snippet": "Nonostante la depenalizzazione, restano diverse conseguenze: sequestro di beni, chiusura della SCIA, sospensione dell'autorizzazione ricettiva fino a 6 mesi, sanzioni pecuniarie per omesse dichiarazioni periodiche. Alcuni magistrati ritengono che il reato di peculato rimanga applicabile nonostante l",
      "snippet_source": "FETCHED"
     }
    ],
    "promotion_rule_met": "1xT1"
   }
  ],
  "spend_evidence": []
 },
 "new_sources": [
  {
   "id": "S-20260822-2331-0001",
   "url": "https://www.gazzettaufficiale.it/atto/serie_generale/caricaDettaglioAtto/originario?atto.dataPubblicazioneGazzetta=1998-01-08&atto.codiceRedazionale=097G0498&elenco30giorni=false",
   "publisher": "Gazzetta Ufficiale della Repubblica Italiana",
   "title": "D.Lgs. 18/12/1997 n. 471 - Disposizioni in materia di tributi comunali",
   "published_date": "1998-01-08",
   "accessed_at": "2026-08-22",
   "tier": "T1",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "PUBBLICAZIONE"
  },
  {
   "id": "S-20260822-2331-0002",
   "url": "https://www.indicenormativa.it/norma/urn:nir:stato:decreto.legislativo:1997-12-18;471",
   "publisher": "indicenormativa.it",
   "title": "Decreto legislativo (Stato Italiano) 18-12-1997, n. 471",
   "published_date": "1997-12-18",
   "accessed_at": "2026-08-22",
   "tier": "T2",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "PUBBLICAZIONE"
  },
  {
   "id": "S-20260822-2331-0003",
   "url": "https://www.paratore.it/tassa-di-soggiorno-strutture-ricettive-obblighi-sanzioni-e-come-tutelarsi/",
   "publisher": "Paratore & Partners - Studio Legale",
   "title": "Tassa di soggiorno strutture ricettive: obblighi, sanzioni e come tutelarsi",
   "published_date": "UNKNOWN",
   "accessed_at": "2026-08-22",
   "tier": "T2",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "PUBBLICAZIONE"
  },
  {
   "id": "S-20260822-2331-0004",
   "url": "https://www.fiscoetasse.com/new-rassegna-stampa/1749-omessa-dichiarazione-imposta-di-soggiorno-le-sanzioni.html",
   "publisher": "FiscoeTasse.com",
   "title": "Omessa dichiarazione imposta di soggiorno: sanzioni 2026",
   "published_date": "UNKNOWN",
   "accessed_at": "2026-08-22",
   "tier": "T3",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "PUBBLICAZIONE"
  },
  {
   "id": "S-20260822-2331-0005",
   "url": "https://wiisy.app/tassa-di-soggiorno-la-dichiarazione-annuale-del-30-giugno-guida-completa-per-i-gestori-di-strutture-ricettive/",
   "publisher": "Wiisy - Piattaforma di gestione strutture ricettive",
   "title": "Dichiarazione tassa di soggiorno: guida completa per gestori e host",
   "published_date": "UNKNOWN",
   "accessed_at": "2026-08-22",
   "tier": "T3",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "ARTEFATTO"
  },
  {
   "id": "S-20260822-2331-0006",
   "url": "https://www.qualitytravel.it/imposta-di-soggiorno-depenalizzato-il-mancato-versamento-ma-e-proprio-cosi/82106",
   "publisher": "Quality Travel - Magazine Turismo",
   "title": "Imposta di soggiorno: depenalizzato il mancato versamento. Ma è proprio così?",
   "published_date": "2020",
   "accessed_at": "2026-08-22",
   "tier": "T2",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "PUBBLICAZIONE"
  },
  {
   "id": "S-20260822-2331-0007",
   "url": "https://www.partitaiva.it/ravvedimento-operoso/",
   "publisher": "Partita IVA - Portale Tributi",
   "title": "Ravvedimento operoso: cos'è, come si calcola e novità sulle sanzioni",
   "published_date": "UNKNOWN",
   "accessed_at": "2026-08-22",
   "tier": "T3",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "ARTEFATTO"
  },
  {
   "id": "S-20260822-2331-0008",
   "url": "https://www.lodgify.com/blog/it/tassa-soggiorno-case-vacanze/",
   "publisher": "Lodgify - Piattaforma Gestione Proprietà Vacanziali",
   "title": "Tassa di soggiorno e affitti brevi: la guida per i proprietari 2026",
   "published_date": "2026",
   "accessed_at": "2026-08-22",
   "tier": "T3",
   "access_status": "OK",
   "retrieved_by": "evidence-researcher",
   "evidence_kind": "PUBBLICAZIONE"
  }
 ],
 "queries_run": [
  {
   "raw_query": "art. 13 D.Lgs. 471/1997 tassa soggiorno sanzioni normattiva",
   "normalized_query": "art 13 d.lgs 471 1997 normattiva sanzioni soggiorno tassa",
   "angle": "normativa",
   "n_results": 8,
   "source_ids": [
    "S-20260822-2331-0001",
    "S-20260822-2331-0002",
    "S-20260822-2331-0003"
   ],
   "yield": "USEFUL"
  },
  {
   "raw_query": "regolamento tassa di soggiorno Venezia Milano sanzioni omesso versamento omessa dichiarazione",
   "normalized_query": "dichiarazione milano omessa omesso regolamento sanzioni soggiorno tassa venezia versamento",
   "angle": "normativa",
   "n_results": 8,
   "source_ids": [
    "S-20260822-2331-0004"
   ],
   "yield": "PARTIAL"
  },
  {
   "raw_query": "agevolazioni proroghe tassa soggiorno piccoli gestori strutture ricettive Italia deroghe",
   "normalized_query": "agevolazioni deroghe gestori italia piccoli proroghe ricettive soggiorno strutture tassa",
   "angle": "normativa",
   "n_results": 6,
   "source_ids": [
    "S-20260822-2331-0008"
   ],
   "yield": "EMPTY"
  },
  {
   "raw_query": "ravvedimento operoso tassa soggiorno riduzione sanzione 3.75% regolarizzazione esecutore",
   "normalized_query": "3.75 esecutore operoso ravvedimento regolarizzazione riduzione sanzione soggiorno tassa",
   "angle": "normativa",
   "n_results": 10,
   "source_ids": [
    "S-20260822-2331-0007"
   ],
   "yield": "USEFUL"
  },
  {
   "raw_query": "sentenze comuni sanzionano gestori case vacanza omesso versamento tassa soggiorno multa",
   "normalized_query": "case gestori multa omesso sanzioni sanzionano sentenze soggiorno tassa vacanza versamento",
   "angle": "fallimenti_storici",
   "n_results": 8,
   "source_ids": [
    "S-20260822-2331-0006"
   ],
   "yield": "PARTIAL"
  },
  {
   "raw_query": "depenalizzazione mancato versamento tassa soggiorno 2020 decreto rilancio pecuniaria",
   "normalized_query": "2020 decreto depenalizzazione mancato pecuniaria rilancio soggiorno tassa versamento",
   "angle": "normativa",
   "n_results": 5,
   "source_ids": [
    "S-20260822-2331-0006"
   ],
   "yield": "USEFUL"
  },
  {
   "raw_query": "scadenza trimestrale versamento tassa soggiorno comuni comuni modalità",
   "normalized_query": "comuni comuni modalita scadenza soggiorno tassa trimestrale versamento",
   "angle": "normativa",
   "n_results": 7,
   "source_ids": [
    "S-20260822-2331-0005"
   ],
   "yield": "PARTIAL"
  }
 ],
 "unreachable_sources": [
  {
   "what": "Regolamento comunale tassa di soggiorno di Venezia (PDF)",
   "why": "PAYWALL",
   "impact": "Avrebbe fornito il testo ufficiale del regolamento locale di Venezia con sanzioni specifiche comunali",
   "url_or_target": "https://www.finanzalocale.eu/file/modulistica/soggiorno04.pdf",
   "source_id": "S-20260822-2331-0009"
  }
 ],
 "self_check": {
  "invarianti_violati": [],
  "note": "Output validato. Tutti i 7 claim hanno source_ids risolvibili e snippet conformi al maxLength. Almeno 6 claim derivano da fonti T1-T2. Le contradictory_evidence documentano la depenalizzazione parziale (rimozione del reato penale) pur mantenendo le sanzioni amministrative. Nessuna evidenza di agevolazioni per piccoli gestori trovata dopo ricerca dedicata."
 }
}
"""


SCHEMI = os.path.join(os.path.dirname(QUI), "schemas")
CONTRATTO_REL = os.path.join("_contracts", "evidence-researcher.delivery.txt")


def pack_base():
    """Una copia fresca a ogni chiamata: le fixture lo mutano."""
    return json.loads(_PACK_BASE)


def contratto():
    """La vista delivery, generata dal compilatore. Mai una fixture scritta a mano."""
    global _CONTRATTO
    if _CONTRATTO is None:
        r = subprocess.run([sys.executable, os.path.join(QUI, "contract_compile.py"),
                            "--agente", "evidence-researcher", "--vista", "delivery"],
                           capture_output=True, timeout=180)
        assert r.returncode == 0, \
            f"il contratto non si genera: {r.stderr.decode('utf-8', 'replace')}"
        _CONTRATTO = r.stdout.decode("utf-8")
    return _CONTRATTO


# ------------------------------------------------------------------ i raw di canale

def raw_valido():
    return json.dumps(pack_base(), ensure_ascii=False, indent=2)


def raw_rip_null():
    """Il difetto riparabile: un `null` illecito su chiave OPZIONALE."""
    p = pack_base()
    p["payload"]["claims"][0]["stale_warning"] = None
    return json.dumps(p, ensure_ascii=False, indent=2)


def raw_rip_involucro():
    """L'envelope avvolto in un involucro a chiave unica: R-C."""
    return json.dumps({"result": pack_base()}, ensure_ascii=False, indent=2)


def raw_ostile():
    """Non riparabile: tipo errato su chiave OBBLIGATORIA, con CRLF e indent=1."""
    p = pack_base()
    p["budget_used"]["searches"] = "molte"
    corpo = json.dumps(p, ensure_ascii=False, indent=1).replace("\n", "\r\n")
    return ("Ho completato le ricerche sul canale.\r\nEcco il pack.\r\n\r\n"
            + MARCATORE + "\r\n" + corpo + "\r\n" + "```\r\n")


def raw_fixture_a():
    """Fixture A — guasto per ECCEZIONE. La foglia `null` la rende non valida."""
    v = None
    for _ in range(1200):
        v = {"_p": v}
    p = pack_base()
    p["_p"] = v
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"))


def raw_fixture_a_negativa():
    """Il controllo negativo di A: stessa catena, foglia `{}`. E' VALIDA, e la prova e' muta."""
    v = {}
    for _ in range(1200):
        v = {"_p": v}
    p = pack_base()
    p["_p"] = v
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"))


def raw_fixture_b():
    """Fixture B — guasto per `return 2`. Ingresso naturale, nessuna iniezione."""
    p = pack_base()
    p["payload"]["claims"][0]["stale_warning"] = None
    p["payload"]["claims"][0]["evidence"][0]["snippet"] = (
        "La guida riporta l'esempio " + MARCATORE + " {\"campo\":1} ``` e prosegue.")
    return ("Ho completato le ricerche sul canale.\n"
            + MARCATORE + "\n"
            + json.dumps(p, ensure_ascii=False, indent=2) + "\n"
            + "```\n")


CANALI_SINTETICI = ["c_nonval", "c_valido", "c_rip_null", "c_rip_involucro", "c_nonrip"]


def contenuti_sintetici():
    return {"c_nonval": "nessun pack qui",
            "c_valido": raw_valido(),
            "c_rip_null": raw_rip_null(),
            "c_rip_involucro": raw_rip_involucro(),
            "c_nonrip": raw_ostile()}


# ------------------------------------------------------------------ il replay padre finto

def scrivi_byte(percorso, dati):
    cartella = os.path.dirname(percorso)
    if cartella:
        os.makedirs(cartella, exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati)


def leggi_byte(percorso):
    with open(percorso, "rb") as f:
        return f.read()


def leggi_json(percorso):
    return json.loads(leggi_byte(percorso).decode("utf-8"))


def crea_replay(canali, contenuti, run_id="T-RED4", commit="de" * 20, manifesto=None):
    """Un replay padre finto ma completo, col proprio contratto dentro `_contracts/`."""
    d = tempfile.mkdtemp(prefix="replay-offline-")
    m = manifesto if manifesto is not None else {
        "run_id": run_id, "commit_di_provenienza": commit,
        "canali": [{"channel": c} for c in canali]}
    scrivi_byte(os.path.join(d, "00-replay-manifest.json"),
                (json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    os.makedirs(os.path.join(d, "02-output"), exist_ok=True)
    for nome, testo in contenuti.items():
        scrivi_byte(os.path.join(d, "02-output", nome + ".raw.txt"),
                    testo.encode("utf-8") if isinstance(testo, str) else testo)
    scrivi_byte(os.path.join(d, CONTRATTO_REL), contratto().encode("utf-8"))
    return d


def replay_sintetico():
    """I cinque canali del §6.2, nell'ordine deliberatamente NON alfabetico del manifesto."""
    return crea_replay(CANALI_SINTETICI, contenuti_sintetici())


def esegui(run, contratto_path=None, schemi=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        codice = ro.main(["--run", run,
                          "--contratto", contratto_path or os.path.join(run, CONTRATTO_REL),
                          "--schemi", schemi or SCHEMI])
    return codice, out.getvalue(), err.getvalue()


def impronte(base, salta=None):
    """`sha256` di ogni file sotto `base`, per percorso relativo con barra."""
    acc = {}
    for cartella, _s, nomi in os.walk(base):
        rel = os.path.relpath(cartella, base).replace("\\", "/")
        if salta is not None and (rel == salta or rel.startswith(salta + "/")):
            continue
        for n in nomi:
            acc[n if rel == "." else f"{rel}/{n}"] = hashlib.sha256(
                leggi_byte(os.path.join(cartella, n))).hexdigest()
    return acc


def canale(rapporto, nome):
    for r in rapporto["canali"]:
        if r["canale"] == nome:
            return r
    raise AssertionError(f"canale assente dal rapporto: {nome}")


def derivato_di(run):
    return os.path.join(run, ro.RADICE, ro.DERIVATO)


# ------------------------------------------------------------------ le prove


@prova("P01", "l'invalido NON riparabile e' nel derivato col raw byte-identico e lo stesso stato")
def p01():
    """LA PRIMA PROVA SCRITTA, §6.1. Contro un driver che non compone il derivato e' rossa."""
    d = replay_sintetico()
    try:
        codice, _, err = esegui(d)
        originale = leggi_byte(os.path.join(d, "02-output", "c_nonrip.raw.txt"))
        derivato = derivato_di(d)
        atteso = os.path.join(derivato, "02-output", "c_nonrip.raw.txt")
        assert os.path.isfile(atteso), \
            f"il derivato non porta il raw del non riparabile. stderr: {err.strip()[:200]}"
        assert leggi_byte(atteso) == originale, "il raw conservato non e' byte-identico"
        rapporto = leggi_json(os.path.join(derivato, ro.USCITA_DERIVATA))
        assert canale(rapporto, "c_nonrip")["esito"] == "NON VALIDO", \
            canale(rapporto, "c_nonrip")["esito"]
        assert codice == 1, f"uscita {codice}, atteso 1"
    finally:
        shutil.rmtree(d, ignore_errors=True)




SORGENTE_DRIVER = os.path.join(QUI, "replay_offline.py")


class Spy:
    """Un CONTATORE, non un sostituto: delega sempre alla `pack_repair.main` vera.

    Riassegna l'attributo del modulo. E' per questo che il driver deve importare il MODULO e
    chiamare per attributo: `from pack_repair import main` catturerebbe il riferimento prima
    della sostituzione e renderebbe mute tutte le prove che contano le invocazioni.
    """

    def __init__(self):
        self.chiamate = []
        self._vero = None

    def __enter__(self):
        self._vero = pack_repair.main

        def spia(argv=None):
            self.chiamate.append(list(argv or []))
            return self._vero(argv)
        pack_repair.main = spia
        return self

    def __exit__(self, *_e):
        pack_repair.main = self._vero
        return False

    def conteggio(self, canale):
        marca = canale + ".raw.txt"
        return sum(1 for a in self.chiamate if any(marca in str(p) for p in a))


def valida_raw(testo, contratto_path, schemi=None):
    """`replay_validate` su un raw, per confermare una precondizione. Restituisce il codice."""
    d = tempfile.mkdtemp(prefix="valida-")
    try:
        p = os.path.join(d, "x.raw.txt")
        scrivi_byte(p, testo.encode("utf-8") if isinstance(testo, str) else testo)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return replay_validate.main(["--raw", p, "--contratto", contratto_path,
                                         "--schemi", schemi or SCHEMI])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def forma_di_chiamata_conforme(sorgente):
    """Il controllo STRUTTURALE del §4.11: import di modulo, chiamata per attributo.

    Rifiuta `from pack_repair import main` e il binding locale `_m = pack_repair.main`: entrambi
    catturano il riferimento prima che lo spy possa sostituirlo.
    """
    albero = ast.parse(sorgente)
    importato, chiamato = False, False
    for n in ast.walk(albero):
        if isinstance(n, ast.ImportFrom) and n.module == "pack_repair":
            return False
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name == "pack_repair":
                    if alias.asname is not None:
                        return False
                    importato = True
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            v = n.value
            if (isinstance(v, ast.Attribute) and v.attr == "main"
                    and isinstance(v.value, ast.Name) and v.value.id == "pack_repair"):
                return False
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "main" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "pack_repair"):
            chiamato = True
    return importato and chiamato


def radice_di(run):
    return os.path.join(run, ro.RADICE)


def esito_di(run):
    return leggi_json(os.path.join(radice_di(run), ro.ESITO))


def manifesto_di(run):
    return leggi_json(os.path.join(radice_di(run), ro.MANIFESTO_DRIVER))


def derivato_rapporto(run):
    return leggi_json(os.path.join(derivato_di(run), ro.USCITA_DERIVATA))


def copia_schemi(modifica=None, annidato=None):
    """Una radice di schemi in un'altra posizione fisica, byte-identica salvo l'alterazione."""
    d = tempfile.mkdtemp(prefix="schemi-")
    radice = os.path.join(d, "schemas")
    shutil.copytree(SCHEMI, radice)
    if modifica:
        p = os.path.join(radice, modifica)
        scrivi_byte(p, leggi_byte(p) + b"\n")
    if annidato:
        scrivi_byte(os.path.join(radice, "annidati", annidato), b'{"title": "annidato"}\n')
    return d, radice


@prova("P02", "TUTTI i canali sono nel derivato, i `NON_VALUTABILE` compresi")
def p02():
    d = replay_sintetico()
    try:
        codice, _, _ = esegui(d)
        rapporto = derivato_rapporto(d)
        assert rapporto["canali_dichiarati"] == 5, rapporto["canali_dichiarati"]
        assert [r["canale"] for r in rapporto["canali"]] == CANALI_SINTETICI
        assert canale(rapporto, "c_nonval")["esito"] == "NON_VALUTABILE"
        assert canale(rapporto, "c_valido")["esito"] == "VALIDO"
        assert canale(rapporto, "c_rip_null")["esito"] == "VALIDO"
        assert canale(rapporto, "c_rip_involucro")["esito"] == "VALIDO"
        assert canale(rapporto, "c_nonrip")["esito"] == "NON VALIDO"
        assert rapporto["esito_run"] == "NON_VALIDO", rapporto["esito_run"]
        assert codice == 1, f"uscita {codice}, atteso 1"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P03", "il raw conservato e' byte-identico anche quando NON e' in forma canonica")
def p03():
    """La fixture ostile porta `CRLF` e `indent=1`: un driver che «normalizza» cambia i byte."""
    ostile = raw_ostile().encode("utf-8")
    assert len(ostile) == 17574, len(ostile)
    d = crea_replay(["c_nonrip"], {"c_nonrip": raw_ostile()})
    try:
        esegui(d)
        derivato = leggi_byte(os.path.join(derivato_di(d), "02-output", "c_nonrip.raw.txt"))
        assert derivato == ostile, \
            f"il raw e' stato riscritto: {len(derivato)} byte contro {len(ostile)}"
        e = esito_di(d)
        assert e["canali"][0]["sha256_raw_originale"] == e["canali"][0]["sha256_raw_derivato"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P04", "fixture A: attraversa il filtro e fa SOLLEVARE; col controllo negativo e' muta")
def p04():
    """Non-degenerazione della fixture A, misurata senza spy, sullo strumento vero."""
    d = crea_replay(["c_prof"], {"c_prof": raw_fixture_a()})
    con = os.path.join(d, CONTRATTO_REL)
    try:
        raw = raw_fixture_a().encode("utf-8")
        assert len(raw) == 22960, len(raw)
        assert hashlib.sha256(raw).hexdigest() == \
            "dfc2ae0af7bdc4f201986902e280d69a2c402d86b093b5ad8ef10527dff23534"
        assert valida_raw(raw, con) == 1, "precondizione: la fixture A deve essere NON VALIDA"
        p = os.path.join(d, "02-output", "c_prof.raw.txt")
        sollevata = None
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                pack_repair.main(["--raw", p, "--contratto", con, "--schemi", SCHEMI,
                                  "--fascicolo", os.path.join(d, "_f")])
        except BaseException as e:                                        # noqa: BLE001
            sollevata = type(e).__name__
        assert sollevata == "RecursionError", f"non ha sollevato RecursionError: {sollevata}"

        # il controllo negativo: la stessa catena con foglia `{}` e' VALIDA e non passa il filtro
        neg = raw_fixture_a_negativa().encode("utf-8")
        assert len(neg) == 22958, len(neg)
        assert hashlib.sha256(neg).hexdigest() == \
            "60d83a2fa1f3c7a9d39f7ef842bf525249be13dae08b216ab38c2040894ac036"
        assert valida_raw(neg, con) == 0, \
            "il controllo negativo deve essere VALIDO: e' la foglia `null` a decidere"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P05", "fixture B: attraversa il filtro e fa restituire `2`, senza alcuna iniezione")
def p05():
    d = crea_replay(["c_ret2"], {"c_ret2": raw_fixture_b()})
    con = os.path.join(d, CONTRATTO_REL)
    try:
        raw = raw_fixture_b().encode("utf-8")
        assert len(raw) == 18737, len(raw)
        assert hashlib.sha256(raw).hexdigest() == \
            "94dbe47b4fac6549a93ff193cd93de488502d8c48afd719ab9f891d9a151bc7f"
        assert valida_raw(raw, con) == 1, "precondizione: la fixture B deve essere NON VALIDA"
        p = os.path.join(d, "02-output", "c_ret2.raw.txt")
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            codice = pack_repair.main(["--raw", p, "--contratto", con, "--schemi", SCHEMI,
                                       "--fascicolo", os.path.join(d, "_f")])
        assert codice == 2, f"`pack_repair.main` ha restituito {codice}, atteso 2"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P06", "l'ordine dei canali viene dal manifesto padre, non dal glob")
def p06():
    d = replay_sintetico()
    try:
        esegui(d)
        emesso = [r["canale"] for r in esito_di(d)["canali"]]
        assert emesso == CANALI_SINTETICI, emesso
        assert emesso != sorted(emesso), "l'ordine coincide con `sorted()`: la prova sarebbe muta"
        assert [r["canale"] for r in derivato_rapporto(d)["canali"]] == CANALI_SINTETICI
        assert leggi_json(os.path.join(radice_di(d), ro.VALIDAZIONE_ORIGINALE)) \
            ["canali"][0]["canale"] == CANALI_SINTETICI[0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P07", "il replay padre non viene scritto: ogni suo file e' invariato per `sha256`")
def p07():
    d = replay_sintetico()
    try:
        prima = impronte(d, salta=ro.RADICE)
        esegui(d)
        dopo = impronte(d, salta=ro.RADICE)
        assert prima == dopo, \
            f"il padre e' cambiato: {sorted(set(dopo) - set(prima))}"
        assert ro.USCITA_DERIVATA not in prima, "la fixture non e' pulita"
        assert not os.path.exists(os.path.join(d, ro.USCITA_DERIVATA)), \
            "il 1o report ha usato il default invece di `--out`"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P08", "UNA sola riparazione per canale, contata dallo spy")
def p08():
    d = crea_replay(["c_rip_null"], {"c_rip_null": raw_rip_null()})
    try:
        with Spy() as spia:
            codice, _, _ = esegui(d)
        assert spia.conteggio("c_rip_null") == 1, \
            f"invocazioni: {spia.conteggio('c_rip_null')}, attesa 1"
        assert codice == 0, f"uscita {codice}, atteso 0"

        # perche' l'osservatore e' lo spy e NON i file del fascicolo: due invocazioni sullo
        # stesso raw nello stesso fascicolo danno quattro file byte-identici
        fasc = os.path.join(d, "_doppio")
        p = os.path.join(d, "02-output", "c_rip_null.raw.txt")
        con = os.path.join(d, CONTRATTO_REL)
        impronte_per_giro = []
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                pack_repair.main(["--raw", p, "--contratto", con, "--schemi", SCHEMI,
                                  "--fascicolo", fasc])
            impronte_per_giro.append(impronte(fasc))
        assert impronte_per_giro[0] == impronte_per_giro[1] and len(impronte_per_giro[0]) == 4, \
            "il fascicolo distinguerebbe due invocazioni: allora l'osservatore potrebbe essere lui"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P09", "i canali senza pack non arrivano MAI a `pack_repair`: zero chiamate")
def p09():
    contenuti = {"c_graffe": '{"a": 1',
                 "c_illeggibile": '{"a": }',
                 "c_senza_pack": "nessun pack qui",
                 "c_rip_null": raw_rip_null()}
    canali = ["c_graffe", "c_illeggibile", "c_senza_pack", "c_rip_null"]
    d = crea_replay(canali, contenuti)
    con = os.path.join(d, CONTRATTO_REL)
    try:
        muti = ["c_graffe", "c_illeggibile", "c_senza_pack"]
        for nome in muti:
            assert valida_raw(contenuti[nome], con) == 2, \
                f"precondizione: {nome} deve dare exit 2 al validatore"
        prima = {n: leggi_byte(os.path.join(d, "02-output", n + ".raw.txt")) for n in muti}
        with Spy() as spia:
            esegui(d)
        rapporto = derivato_rapporto(d)
        for nome in muti:
            assert spia.conteggio(nome) == 0, f"{nome}: {spia.conteggio(nome)} chiamate, attese 0"
            assert canale(rapporto, nome)["esito"] == "NON_VALUTABILE", \
                canale(rapporto, nome)["esito"]
            assert leggi_byte(os.path.join(derivato_di(d), "02-output", nome + ".raw.txt")) \
                == prima[nome], f"{nome}: il raw non e' byte-identico"
        # la prova non e' vacua: lo spy scatta per il canale che il filtro lascia passare
        assert spia.conteggio("c_rip_null") == 1, "lo spy non ha osservato nulla"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _guasto_contenuto(nome_canale, raw, causa_attesa):
    """Le quattro asserzioni comuni alle righe 10 e 11, piu' la CAUSA che le separa."""
    canali = ["c_valido", nome_canale, "c_rip_null"]
    d = crea_replay(canali, {"c_valido": raw_valido(), nome_canale: raw,
                             "c_rip_null": raw_rip_null()})
    con = os.path.join(d, CONTRATTO_REL)
    try:
        assert valida_raw(raw, con) == 1, "precondizione: il canale deve essere NON VALIDO"
        originale = leggi_byte(os.path.join(d, "02-output", nome_canale + ".raw.txt"))
        with Spy() as spia:
            codice, _, _ = esegui(d)
        assert spia.conteggio(nome_canale) == 1, \
            f"invocazioni: {spia.conteggio(nome_canale)}, attesa 1"
        riga = [r for r in esito_di(d)["canali"] if r["canale"] == nome_canale][0]
        g = riga["guasto_di_riparazione"]
        assert g is not None, "nessun guasto registrato per il canale"
        assert g["causa"] == causa_attesa, f"causa {g['causa']!r}, attesa {causa_attesa!r}"
        assert leggi_byte(os.path.join(derivato_di(d), "02-output", nome_canale + ".raw.txt")) \
            == originale, "il raw originale non e' stato conservato byte per byte"
        rapporto = derivato_rapporto(d)
        assert canale(rapporto, "c_valido")["esito"] == "VALIDO", "gli altri canali non misurati"
        assert canale(rapporto, "c_rip_null")["esito"] == "VALIDO", "gli altri canali non misurati"
        assert codice == 1, f"uscita {codice}, atteso 1: un guasto di CANALE non vale 2"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P10", "il guasto di canale per ECCEZIONE e' contenuto, e la causa e' l'eccezione")
def p10():
    _guasto_contenuto("c_prof", raw_fixture_a(), "RecursionError")


@prova("P11", "il guasto di canale per `return 2` e' contenuto, e la causa e' `ritorno 2`")
def p11():
    _guasto_contenuto("c_ret2", raw_fixture_b(), "ritorno 2")


@prova("P12", "il raw sintetico e' riconoscibile e non inghiotte il pack")
def p12():
    d = crea_replay(["c_rip_null"], {"c_rip_null": raw_rip_null()})
    try:
        esegui(d)
        testo = leggi_byte(os.path.join(derivato_di(d), "02-output",
                                        "c_rip_null.raw.txt")).decode("utf-8")
        righe = testo.split("\n")
        assert righe[0] == "DERIVED_REPAIR", righe[0]
        assert righe[1].startswith("source_raw_sha256="), righe[1]
        assert righe[2].startswith("repair_evidence_sha256="), righe[2]
        assert righe[3] == "repair_evidence_path=02-riparazioni/c_rip_null/03-verdetti.json", \
            righe[3]
        assert righe[4].startswith("{"), "il pack non comincia in colonna 0"
        for r in righe[:4]:
            assert not r.startswith("{"), f"una riga d'intestazione inizia con una graffa: {r}"
            assert MARCATORE not in r, f"una riga d'intestazione porta il marcatore: {r}"
        riparato = leggi_json(os.path.join(radice_di(d), ro.RIPARAZIONI,
                                           "c_rip_null", ro.PACK_RIPARATO))
        assert replay_validate.estrai_pack(testo) == riparato, \
            "`estrai_pack` non restituisce esattamente il pack riparato"
        assert canale(derivato_rapporto(d), "c_rip_null")["esito"] == "VALIDO"
        # e i due hash dell'intestazione sono quelli veri
        assert righe[1].split("=")[1] == hashlib.sha256(
            leggi_byte(os.path.join(d, "02-output", "c_rip_null.raw.txt"))).hexdigest()
        assert righe[2].split("=")[1] == hashlib.sha256(leggi_byte(os.path.join(
            radice_di(d), ro.RIPARAZIONI, "c_rip_null", ro.EVIDENZA))).hexdigest()
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P13", "le DUE precedenze di `estrai_pack`, provate entrambe")
def p13():
    esca = '{"esca": 1}'
    corpo = json.dumps(pack_base(), ensure_ascii=False, indent=2)
    # il marcatore VINCE sulla colonna 0: l'esca lo segue, il pack sta piu' sotto in colonna 0
    assert replay_validate.estrai_pack(MARCATORE + " " + esca + "\n" + corpo) == {"esca": 1}
    # tolto il solo marcatore, lo stesso testo ripiega sulla colonna 0 e trova il pack
    assert replay_validate.estrai_pack(" " + esca + "\n" + corpo) == pack_base()
    # un oggetto JSON a inizio riga prima del pack vince il ripiego
    assert replay_validate.estrai_pack(esca + "\n" + corpo) == {"esca": 1}
    # la forma del §4.4 non cade in nessuna delle due
    prescritta = ro.raw_sintetico("c", "a" * 64, "b" * 64, corpo)
    assert replay_validate.estrai_pack(prescritta) == pack_base()


@prova("P14", "non si scrive su cio' che non e' proprio: niente manifesto, niente da fare")
def p14():
    d = replay_sintetico()
    try:
        scrivi_byte(os.path.join(radice_di(d), "roba-di-qualcun-altro.json"), b"{}\n")
        prima = impronte(d)
        codice, _, _ = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert impronte(d) == prima, "ha toccato una directory che non ha rivendicato"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P15", "atomicita': la destinazione e' o intatta o completa, mai a meta'")
def p15():
    d = replay_sintetico()
    vero = ro.sostituisci
    try:
        def interrotta(parziale, percorso):
            raise OSError("interruzione simulata fra la scrittura e la sostituzione")
        ro.sostituisci = interrotta
        codice, _, _ = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2"
        manifesto = os.path.join(radice_di(d), ro.MANIFESTO_DRIVER)
        if os.path.exists(manifesto):
            leggi_json(manifesto)                       # se c'e', dev'essere COMPLETO
        assert os.path.exists(manifesto + ro.SUFFISSO), "il parziale non e' nemmeno stato scritto"
        ro.sostituisci = vero
        # e il residuo di bootstrap e' recuperabile: la passata successiva chiude
        codice, _, _ = esegui(d)
        assert codice == 1, f"uscita {codice}, atteso 1"
        assert manifesto_di(d)["stato"] == "COMPLETO"
        assert not [p for p in impronte(radice_di(d)) if p.endswith(ro.SUFFISSO)]
    finally:
        ro.sostituisci = vero
        shutil.rmtree(d, ignore_errors=True)


@prova("P16", "l'identita' comprende gli schemi: uno schema di PRIMO livello modificato ferma")
def p16():
    d = replay_sintetico()
    fuori, radice = copia_schemi(modifica="core.schema.json")
    try:
        assert ro.impronta_schemi(ro.schemi_di_primo_livello(radice)) != \
            ro.impronta_schemi(ro.schemi_di_primo_livello(SCHEMI)), \
            "precondizione: l'impronta deve cambiare"
        esegui(d)
        assert manifesto_di(d)["stato"] == "COMPLETO"
        prima = impronte(d)
        codice, _, _ = esegui(d, schemi=radice)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert impronte(d) == prima, "ha toccato qualcosa nonostante l'identita' diversa"
    finally:
        shutil.rmtree(fuori, ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)


@prova("P17", "il controllo strutturale del §4.11 distingue le TRE forme di chiamata")
def p17():
    conforme = leggi_byte(SORGENTE_DRIVER).decode("utf-8")
    assert forma_di_chiamata_conforme(conforme), \
        "il driver vero non passa il proprio controllo strutturale"
    da_modulo = conforme.replace("import pack_repair  ", "from pack_repair import main  ", 1) \
                        .replace("pack_repair.main(", "main(")
    assert not forma_di_chiamata_conforme(da_modulo), "l'import da modulo non e' stato rifiutato"
    binding = conforme.replace("import pack_repair  ",
                               "import pack_repair\n_m = pack_repair.main  ", 1) \
                      .replace("pack_repair.main(", "_m(")
    assert not forma_di_chiamata_conforme(binding), "il binding locale non e' stato rifiutato"
    # e le tre forme sono davvero diverse fra loro
    assert len({conforme, da_modulo, binding}) == 3

    # ---- le NOVE sotto-asserzioni del §4.7, sul sorgente di QUESTO file.
    #
    # Le asserzioni ereditate restano sopra, prime, e nessuna delle nove le precede o le assorbe:
    # `P17` deve poter cadere per la forma di chiamata del driver E per la forma del sorgente del
    # selftest, e dire quale delle due. Ogni sotto-asserzione porta la propria diagnostica.
    mio = leggi_byte(os.path.join(QUI, "replay_offline_selftest.py")).decode("utf-8")
    esiti = controllo_strutturale(mio)
    # Si ACCUMULA e si solleva una volta sola, con l'insieme COMPLETO e ordinato: fermarsi alla
    # prima in ordine alfabetico nasconderebbe le altre, e la tabella delle mutazioni pretende di
    # sapere QUALI sotto-asserzioni cadono — `find_spec`, per dirne una, ne fa cadere due.
    cadute = [n for n in sorted(esiti) if esiti[n]]
    assert not cadute, (
        f"sotto-asserzioni cadute {cadute}: "
        + " | ".join(f"{n} → " + " ; ".join(esiti[n][:3]) for n in cadute))


@prova("P18", "l'identita' comprende il manifesto padre: un canale in meno ferma tutto")
def p18():
    a = replay_sintetico()
    b = crea_replay(CANALI_SINTETICI[:-1],
                    {k: v for k, v in contenuti_sintetici().items() if k != "c_nonrip"})
    try:
        esegui(a)
        ident_a = manifesto_di(a)["identita"]
        shutil.copytree(radice_di(a), radice_di(b))
        _, _, _, ident_b, _ = ro.passo_zero(b, os.path.join(b, CONTRATTO_REL), SCHEMI)
        assert ident_a["sha256_manifesto_padre"] != ident_b["sha256_manifesto_padre"]
        assert ident_a["digest_raw"] != ident_b["digest_raw"]
        prima = impronte(b)
        codice, _, err = esegui(b)
        assert codice == 2, f"uscita {codice}, atteso 2"
        # su questa fixture DUE campi differiscono, e il solo `exit 2` non separerebbe il
        # controllo sul manifesto da quello sui raw: si osserva quindi il campo ATTRIBUITO
        assert "sha256_manifesto_padre" in err, \
            f"il rifiuto non e' attribuito al manifesto padre: {err.strip()[:200]}"
        assert impronte(b) == prima, "ha scritto sotto un padre che non e' il suo"
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


@prova("P19", "l'identita' comprende i raw: un solo byte alterato ferma tutto")
def p19():
    d = replay_sintetico()
    try:
        esegui(d)
        prima_ident = manifesto_di(d)["identita"]
        p = os.path.join(d, "02-output", "c_valido.raw.txt")
        scrivi_byte(p, leggi_byte(p) + b" ")
        _, _, _, dopo_ident, _ = ro.passo_zero(d, os.path.join(d, CONTRATTO_REL), SCHEMI)
        diversi = [c for c in ro.CAMPI_IDENTITA if prima_ident[c] != dopo_ident[c]]
        assert diversi == ["digest_raw"], f"campi diversi: {diversi}"
        prima = impronte(d, salta=None)
        codice, _, _ = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert impronte(d) == prima, "ha scritto nonostante i raw diversi"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _con_manifesto_in_corso(d):
    """Interrompe una esecuzione VERA subito dopo il passo 1: lascia un `IN_CORSO` autentico."""
    vero = ro.invoca_report

    def rotta(*_a, **_k):
        raise OSError("interruzione dopo la rivendicazione")
    ro.invoca_report = rotta
    try:
        codice, _, _ = esegui(d)
    finally:
        ro.invoca_report = vero
    assert codice == 2
    assert manifesto_di(d)["stato"] == "IN_CORSO"


@prova("P20", "un residuo POSSEDUTO e' recuperabile, accanto a `IN_CORSO` come a `COMPLETO`")
def p20():
    for stato in ("IN_CORSO", "COMPLETO"):
        d = replay_sintetico()
        try:
            padre_prima = impronte(d, salta=ro.RADICE)
            if stato == "IN_CORSO":
                _con_manifesto_in_corso(d)
            else:
                esegui(d)
                assert manifesto_di(d)["stato"] == "COMPLETO"
            residuo = os.path.join(radice_di(d), ro.MANIFESTO_DRIVER + ro.SUFFISSO)
            scrivi_byte(residuo, b"mezzo manifesto, interrotto\n")
            codice, _, _ = esegui(d)
            assert not os.path.exists(residuo), f"[{stato}] il residuo non e' stato rimosso"
            assert manifesto_di(d)["stato"] == "COMPLETO", f"[{stato}] non ha chiuso"
            estranei = [p for p in impronte(radice_di(d))
                        if p not in ro.destinazioni(manifesto_di(d)["percorsi_gestiti"])]
            assert not estranei, f"[{stato}] estranei rimasti: {estranei}"
            assert impronte(d, salta=ro.RADICE) == padre_prima, f"[{stato}] padre alterato"
            uno = impronte(radice_di(d))
            esegui(d)
            assert impronte(radice_di(d)) == uno, f"[{stato}] la riesecuzione non e' identica"
            assert codice == 1, f"[{stato}] uscita {codice}, atteso 1"
        finally:
            shutil.rmtree(d, ignore_errors=True)


@prova("P21", "un residuo NON riconosciuto resta intatto e ferma tutto")
def p21():
    d = replay_sintetico()
    try:
        esegui(d)
        ignoto = os.path.join(radice_di(d), "qualcosa-di-ignoto.parziale")
        scrivi_byte(ignoto, b"non e' di nessuno\n")
        prima = impronte(d)
        codice, _, _ = esegui(d)
        assert codice == 2, f"uscita {codice}, atteso 2"
        assert impronte(d) == prima, "ha cancellato o alterato un residuo che non e' suo"
        assert os.path.isfile(ignoto)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P22", "un manifesto che non si sa leggere ferma tutto, nelle tre varianti")
def p22():
    for variante in ("illeggibile", "senza campi", "stato ignoto"):
        d = replay_sintetico()
        try:
            esegui(d)
            m = os.path.join(radice_di(d), ro.MANIFESTO_DRIVER)
            if variante == "illeggibile":
                scrivi_byte(m, b"\xff\xfe non e' JSON")
            elif variante == "senza campi":
                scrivi_byte(m, b'{"a": 1}\n')
            else:
                d2 = leggi_json(m)
                d2["stato"] = "BOH"
                scrivi_byte(m, (json.dumps(d2, ensure_ascii=False, indent=2)
                                + "\n").encode("utf-8"))
            prima = impronte(d)
            codice, _, _ = esegui(d)
            assert codice == 2, f"[{variante}] uscita {codice}, atteso 2"
            assert impronte(d) == prima, f"[{variante}] ha modificato la directory"
        finally:
            shutil.rmtree(d, ignore_errors=True)


@prova("P23", "il manifesto non contiene il proprio hash")
def p23():
    d = replay_sintetico()
    try:
        esegui(d)
        m = manifesto_di(d)
        voci = [v for v in m["inventario"] if v["percorso"] == ro.MANIFESTO_DRIVER]
        assert len(voci) == 1, f"voci per se' stesso: {len(voci)}"
        assert set(voci[0]) == {"percorso"}, f"la voce porta anche {set(voci[0]) - {'percorso'}}"
        altre = [v for v in m["inventario"] if v["percorso"] != ro.MANIFESTO_DRIVER]
        assert altre and all("byte" in v and "sha256" in v for v in altre), \
            "le altre voci devono portare dimensione e sha256"
        for v in altre:
            p = os.path.join(radice_di(d), v["percorso"].replace("/", os.sep))
            assert hashlib.sha256(leggi_byte(p)).hexdigest() == v["sha256"], v["percorso"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("P24", "gli artefatti propri sono deterministici: due passate, byte identici")
def p24():
    d = replay_sintetico()
    try:
        esegui(d)
        propri = [ro.ESITO, ro.MANIFESTO_DRIVER, f"{ro.DERIVATO}/{ro.MANIFESTO_PADRE}"]
        uno = {p: leggi_byte(os.path.join(radice_di(d), p.replace("/", os.sep)))
               for p in propri}
        esegui(d)
        due = {p: leggi_byte(os.path.join(radice_di(d), p.replace("/", os.sep)))
               for p in propri}
        for p in propri:
            assert uno[p] == due[p], f"{p} differisce fra due passate identiche"
            assert not uno[p].startswith(b"\xef\xbb\xbf"), f"{p} porta un BOM"
            assert b"\r\n" not in uno[p], f"{p} porta terminatori CRLF"
            assert uno[p].endswith(b"\n"), f"{p} non finisce con un newline"
            uno[p].decode("utf-8")
        assert [r["canale"] for r in json.loads(uno[ro.ESITO].decode("utf-8"))["canali"]] \
            == CANALI_SINTETICI
        assert impronte(d, salta=ro.RADICE) == impronte(d, salta=ro.RADICE)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@prova("C1", "l'identita' e' per CONTENUTO: stesso corpus, percorsi diversi, stessa identita'")
def c1():
    d = replay_sintetico()
    altrove = tempfile.mkdtemp(prefix="altrove-")
    fuori, schemi_copia = copia_schemi()
    try:
        esegui(d)
        ident = manifesto_di(d)["identita"]
        copia = os.path.join(altrove, "run")
        shutil.copytree(d, copia)                       # `_offline-repair/` si sposta con esso
        _, _, _, ident2, _ = ro.passo_zero(copia, os.path.join(copia, CONTRATTO_REL),
                                           schemi_copia)
        diversi = [c for c in ro.CAMPI_IDENTITA if ident[c] != ident2[c]]
        assert not diversi, f"campi diversi pur essendo i byte gli stessi: {diversi}"
        codice, _, err = esegui(copia, schemi=schemi_copia)
        assert codice == 1, f"la riesecuzione altrove e' stata rifiutata: {err.strip()[:200]}"
        assert manifesto_di(copia)["stato"] == "COMPLETO"
    finally:
        shutil.rmtree(fuori, ignore_errors=True)
        shutil.rmtree(altrove, ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)


@prova("C2", "uno schema ANNIDATO non cambia l'impronta, perche' il validatore non lo legge")
def c2():
    d = replay_sintetico()
    fuori, radice = copia_schemi(annidato="extra.schema.json")
    try:
        assert os.path.isfile(os.path.join(radice, "annidati", "extra.schema.json"))
        assert ro.impronta_schemi(ro.schemi_di_primo_livello(radice)) == \
            ro.impronta_schemi(ro.schemi_di_primo_livello(SCHEMI)), \
            "l'impronta segue una sottodirectory che il validatore non legge"
        esegui(d)
        codice, _, err = esegui(d, schemi=radice)
        assert codice == 1, f"la riesecuzione e' stata rifiutata: {err.strip()[:200]}"
        assert manifesto_di(d)["stato"] == "COMPLETO"
        assert len(manifesto_di(d)["schemi_considerati"]) == 13
    finally:
        shutil.rmtree(fuori, ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ i divieti strutturali
#
# La decisione di provisioning si RILEVA. Perche' resti tale servono nove divieti, e servono
# verificabili: una prosa che dice «non guardare `os.name`» e' una raccomandazione, un `ast` che
# la rifiuta e' un invariante. Le nove sotto-asserzioni sono NOMINATE e portano ciascuna la
# propria diagnostica, perche' `P17` cade ormai per cause eterogenee — la forma di chiamata del
# driver e la forma del sorgente di questo file — e un rosso che dicesse soltanto «P17» sarebbe
# illeggibile.

SYS_AMMESSI = ("executable", "exit", "path", "stdout", "stderr")
OS_AMMESSI = ("makedirs", "path", "sep", "walk")


def alias_di_modulo(albero, modulo):
    """Ogni nome legato a `modulo`, alias risolti.

    Non e' facoltativo: questo file lega `sys` a DUE nomi — `sys` e `_sys` — e un controllo che
    guardasse il solo nome letterale lascerebbe aperta la porta che deve chiudere.
    """
    nomi = set()
    for n in ast.walk(albero):
        if isinstance(n, ast.Import):
            for al in n.names:
                if al.name == modulo:
                    nomi.add(al.asname or al.name)
    return nomi


def controllo_strutturale(sorgente):
    """Le NOVE sotto-asserzioni del §4.7. Restituisce {nome: [diagnostiche]}; lista vuota = verde."""
    albero = ast.parse(sorgente)
    g = {f"S{i}": [] for i in range(1, 10)}
    a_sys, a_os = alias_di_modulo(albero, "sys"), alias_di_modulo(albero, "os")
    moduli = a_sys | a_os
    segnalati = []                      # (riga, base) di cio' che S1 rifiuta: e' l'oggetto di S2

    for n in ast.walk(albero):
        if isinstance(n, ast.Attribute):
            base = n.value.id if isinstance(n.value, ast.Name) else None
            if base in a_sys and n.attr not in SYS_AMMESSI:
                g["S1"].append(f"riga {n.lineno}: `{base}.{n.attr}` fuori dall'insieme di `sys`")
                segnalati.append((n.lineno, base))
            if base in a_os and n.attr not in OS_AMMESSI:
                g["S1"].append(f"riga {n.lineno}: `{base}.{n.attr}` fuori dall'insieme di `os`")
                segnalati.append((n.lineno, base))
            if n.attr == "find_spec":
                g["S8"].append(f"riga {n.lineno}: riferimento a `find_spec`")
        if isinstance(n, ast.Name) and n.id == "find_spec":
            g["S8"].append(f"riga {n.lineno}: riferimento a `find_spec`")
        if isinstance(n, ast.Import):
            for al in n.names:
                if al.name.split(".")[0] in ("platform", "importlib"):
                    g["S7"].append(f"riga {n.lineno}: `import {al.name}`")
        if isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] in ("platform", "importlib"):
                g["S7"].append(f"riga {n.lineno}: `from {n.module} import …`")
            for al in n.names:
                if al.name == "*" and n.module in ("sys", "os"):
                    g["S4"].append(f"riga {n.lineno}: `from {n.module} import *`")
                elif n.module == "sys" and al.name not in SYS_AMMESSI:
                    g["S3"].append(f"riga {n.lineno}: `from sys import {al.name}`")
                elif n.module == "os" and al.name not in OS_AMMESSI:
                    g["S3"].append(f"riga {n.lineno}: `from os import {al.name}`")
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id in moduli):
            g["S5"].append(f"riga {n.lineno}: `getattr({n.args[0].id}, …)`")
        if isinstance(n, ast.Try):
            for m in ast.walk(n):
                if isinstance(m, ast.Import) and any(
                        al.name.split(".")[0] == "referencing" for al in m.names):
                    g["S6"].append(f"riga {m.lineno}: `import referencing` dentro un `try`")
                if isinstance(m, ast.ImportFrom) and \
                        (m.module or "").split(".")[0] == "referencing":
                    g["S6"].append(f"riga {m.lineno}: `from referencing import …` in un `try`")

    # S2 — il confronto e' qualificato SULLA BASE, non sul nome dell'attributo. Sette sedi di
    # questo file portano un attributo omonimo a uno di `os`/`sys` con base ESTRANEA — `f.write`,
    # `f.read`, `alias.name`, quattro `.replace` — e devono restare AMMESSE. Un controllo scritto
    # sul solo nome le rifiuterebbe tutte e sette, cioe' sarebbe rosso sul candidato conforme.
    for riga, base in segnalati:
        if base not in moduli:
            g["S2"].append(f"riga {riga}: rifiutato un attributo con base estranea `{base}`")

    # S9 — le sei dipendenti dalla capacita' non la consultano: o si eseguono intere, o non si
    # eseguono affatto. Una prova che si adatta misura due cose diverse a seconda di dove gira.
    for n in ast.walk(albero):
        if not isinstance(n, ast.FunctionDef):
            continue
        etichetta = None
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
                etichetta = d.args[0].value
        if etichetta not in SEI_DIPENDENTI:
            continue
        for m in ast.walk(n):
            if isinstance(m, ast.Name) and m.id in ("_CTX", "sonda_capability",
                                                    "classifica_sonda", "SEI_DIPENDENTI"):
                g["S9"].append(f"riga {m.lineno}: `{etichetta}` consulta `{m.id}`")
    return g


def sotto_asserzioni_cadute(sorgente):
    return sorted(k for k, v in controllo_strutturale(sorgente).items() if v)


# ------------------------------------------------------------------ il provisioning
#
# Il selftest gira in due mondi, e finge di girarne uno solo. `replay_validate` dichiara
# (`replay_validate.py:115` e `:177`) che senza `referencing` la validazione di schema non e'
# disponibile, e in quel caso rifiuta di dire `VALIDO`: ogni pack altrimenti valido diventa
# conservativamente `NON VALIDO`. Sei prove di questa suite asseriscono un verdetto positivo, o un
# `exit 0` che ne dipende, e su una radice priva di `referencing` diventano rosse senza che nulla
# dica che l'ambiente e' incompleto invece che l'artefatto sbagliato.
#
# LA CAPABILITY SI RILEVA, NON SI INDOVINA. Non da `os.name`, non dalla distribuzione, non dalla
# versione di Python, non dalla presenza dei moduli: si misura interrogando il VERO confine di
# `replay_validate` con un pack VALIDO. Un ambiente che ha `referencing` importabile ma un
# validatore che non gira e' indistinguibile dal primo per chiunque guardi gli import, e
# perfettamente distinguibile per chi guarda il confine.

SEI_DIPENDENTI = ("P02", "P04", "P08", "P10", "P11", "P12")
MOTIVO_NA = "provisioning schema indisponibile"
ETICHETTA_NA = "[n/a]"
ETICHETTA_VERDE = "[verde]"
ETICHETTA_ROSSA = "[ROSSO]"

# Il contesto dell'invocazione corrente. UNA valutazione decisionale per invocazione; le prove la
# riusano e non risondano; una chiamata annidata a `main()` e' una nuova invocazione e rivaluta.
# Nessuna cache globale o persistente fra invocazioni dello stesso processo.
_CTX = {"stato": None, "annidato": False}


def classifica_sonda(codice, rapporto):
    """I DUE stati ammessi, per intero. Ogni altra combinazione e' `TERZO_STATO`.

    Non basta guardare il verdetto: uno stato ammesso e' una CONGIUNZIONE. Un `NON VALIDO` con
    errori di schema diversi da zero non e' provisioning assente, e' un pack sbagliato; un
    null-check `RIFIUTATO` non e' ne' l'uno ne' l'altro. Accettare la congiunzione parziale
    significherebbe dichiarare indisponibile una capability che invece funziona.
    """
    if not isinstance(rapporto, dict):
        return "TERZO_STATO"
    nc = rapporto.get("null_check") or {}
    sc = rapporto.get("schema") or {}
    if (codice == 0 and rapporto.get("verdetto") == "VALIDO"
            and nc.get("esito") == "AMMESSO" and sc.get("disponibile") is True
            and sc.get("n_errori") == 0):
        return "SCHEMA_DISPONIBILE"
    if (codice == 1 and rapporto.get("verdetto") == "NON VALIDO"
            and nc.get("esito") == "AMMESSO" and sc.get("disponibile") is False
            and sc.get("n_errori") == 0
            and (sc.get("motivo_indisponibilita") or "").strip()):
        return "SCHEMA_INDISPONIBILE"
    return "TERZO_STATO"


def sonda_capability():
    """Il confine vero di `replay_validate`, su un pack VALIDO. NON cattura: puo' sollevare.

    Chi cattura e' il gate, e in nessun altro posto (§4.4 punto 1). La ragione e' misurata:
    `replay_validate.main` propaga al chiamante un'eccezione non-`Guasto` che venga da
    `valida_schema`, e quell'eccezione non e' un ambiente incompleto — e' un guasto. Convertirla in
    «schema indisponibile» sarebbe fabbricare provisioning assente.

    Nessuna fixture nuova: il contratto e il pack valido sono quelli che la suite gia' porta.
    """
    d = tempfile.mkdtemp(prefix="sonda-capability-")
    try:
        con = os.path.join(d, "contratto.txt")
        scrivi_byte(con, contratto().encode("utf-8"))
        raw = os.path.join(d, "sonda.raw.txt")
        scrivi_byte(raw, raw_valido().encode("utf-8"))
        uscita = io.StringIO()
        with contextlib.redirect_stdout(uscita), contextlib.redirect_stderr(io.StringIO()):
            codice = replay_validate.main(["--raw", raw, "--contratto", con, "--schemi", SCHEMI])
        testo = uscita.getvalue().strip()
        return classifica_sonda(codice, json.loads(testo) if testo else None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def oracle_ripristino():
    """L'ORACLE del ripristino: identita' degli attributi che l'armatura puo' sostituire.

    Non chiama la sonda e non la influenza: legge soltanto un'identita' di attributo, sul
    riferimento al modulo GIA' IMPORTATO — mai attraverso `sys.modules`, che sta fuori
    dall'insieme chiuso di `sys`. Un controllo che risondasse sarebbe una seconda valutazione per
    invocazione; uno che leggesse lo stato gia' deciso confronterebbe un dato con se' stesso.
    """
    return {"replay_validate.valida_schema": replay_validate.valida_schema}


def etichette_di(testo):
    """(nome, etichetta) per ogni riga di prova dell'uscita catturata.

    Si leggono le ETICHETTE PER PROVA, non il riepilogo: misurato che una corsa in cui i non
    applicabili sono stampati `[verde]` ha riepilogo ed exit IDENTICI a una corsa corretta, e
    differisce nelle sole etichette. Un osservatore sul riepilogo sarebbe muto.
    """
    acc = []
    for riga in testo.split("\n"):
        s = riga.strip()
        for e in (ETICHETTA_NA, ETICHETTA_VERDE, ETICHETTA_ROSSA):
            if s.startswith(e):
                resto = s[len(e):].split()
                if resto:
                    acc.append((resto[0], e))
                break
    return acc


# I sei nomi attesi e il numero delle applicabili sono LETTERALI, scritti qui, e INDIPENDENTI da
# `SEI_DIPENDENTI`: ricavarli dalla costante che devono sorvegliare significherebbe confrontare
# una cosa con se' stessa, e le falsificazioni sull'insieme chiuso diventerebbero mute.
SEI_ATTESI = ("P02", "P04", "P08", "P10", "P11", "P12")
# Il TERZO letterale: i venti nomi delle originarie che NON dipendono dalla capacita', scritti
# per esteso, uno per uno. NON ricavati dal registro delle prove, non da `SEI_ATTESI`, non da un
# conteggio, non dalle etichette osservate. Senza di esso l'osservazione e' per CARDINALITA', e
# un nome sconosciuto passa indisturbato: misurato che rinominando una prova indipendente la
# suite resta verde su entrambe le piattaforme.
VENTI_ATTESE = ("P01", "P03", "P05", "P06", "P07", "P09", "P13", "P14", "P15", "P16",
                "P17", "P18", "P19", "P20", "P21", "P22", "P23", "P24", "C1", "C2")
APPLICABILI_ATTESE = 21


@prova("P27", "il contratto degradato: le sei etichettate, le altre ventuno verdi, exit 0")
def p27():
    """LA SEDE PERMANENTE DEL ROSSO, e l'equivalente di `R29` per la meta' B.

    Due parti sotto un'unica finestra di degrado. La A misura il driver: sotto degrado il canale
    resta registrato, non diventa falsamente `VALIDO`, conserva il raw byte per byte, dichiara
    l'indisponibilita' ed esce `1`. La B misura il CONTRATTO: guida una corsa annidata e ne legge
    le etichette.

    La A non e' ridondante con la B: sotto degrado le sei prove che asserirebbero «`c_valido` non
    e' falsamente VALIDO» sono proprio quelle non applicabili. Senza la parte A nessuna riga della
    suite sorveglierebbe piu' il driver sotto degrado.
    """
    if _CTX["annidato"]:
        return                       # guardia di rientranza: la corsa annidata non si ripete
    vero = replay_validate.valida_schema
    # RIUSA il contesto dell'invocazione: non risonda. Se la capability manca, la finestra di
    # degrado e' la realta' e non si forza nulla; se c'e', la si forza — e il §2.7 misura che i
    # due percorsi danno gli stessi valori.
    forzare = _CTX["stato"] == "SCHEMA_DISPONIBILE"
    try:
        if forzare:
            def indisponibile(*_a, **_k):
                raise replay_validate.Guasto(
                    "validazione di schema non disponibile su questa piattaforma: "
                    "No module named 'referencing'")
            replay_validate.valida_schema = indisponibile

        # ---- parte A: il driver sotto degrado
        d = replay_sintetico()
        try:
            codice, _, _ = esegui(d)
            rapporto = derivato_rapporto(d)
            riga = canale(rapporto, "c_valido")
            originale = leggi_byte(os.path.join(d, "02-output", "c_rip_null.raw.txt"))
            derivato = leggi_byte(os.path.join(derivato_di(d), "02-output",
                                               "c_rip_null.raw.txt"))
            assert rapporto["canali_dichiarati"] == 5, "un canale e' sparito dal derivato"
            assert riga["esito"] == "NON VALIDO", f"falso VALIDO: {riga['esito']}"
            assert riga["schema"]["disponibile"] is False, riga["schema"]
            assert riga["null_check"]["esito"] == "AMMESSO", riga["null_check"]
            assert derivato == originale, "il raw originale non e' stato conservato"
            assert codice == 1, f"exit del driver {codice}, atteso 1"
        finally:
            shutil.rmtree(d, ignore_errors=True)

        # ---- parte B: il contratto degradato, letto dalle ETICHETTE
        import replay_offline_selftest as modulo
        modulo._CTX["annidato"] = True
        try:
            uscita = io.StringIO()
            with contextlib.redirect_stdout(uscita):
                codice_annidato = modulo.main()
            testo = uscita.getvalue()
        finally:
            modulo._CTX["annidato"] = False

        et = etichette_di(testo)
        nomi = [n for n, _e in et]
        assert len(nomi) == len(set(nomi)), f"nomi duplicati fra le etichette: {nomi}"
        na = [n for n, e in et if e == ETICHETTA_NA]
        verdi = [n for n, e in et if e == ETICHETTA_VERDE]
        rosse = [n for n, e in et if e == ETICHETTA_ROSSA]
        # (1) i NON APPLICABILI sono esattamente i sei letterali, una volta ciascuno
        for nome in SEI_ATTESI:
            assert na.count(nome) == 1, \
                f"{nome}: {na.count(nome)} righe {ETICHETTA_NA}, attesa 1"
        assert set(na) == set(SEI_ATTESI), \
            f"non applicabili {sorted(na)}, attesi {sorted(SEI_ATTESI)}"

        # (2) i VERDI sono esattamente i venti letterali piu' `P27`, una volta ciascuno. E'
        #     un'identita' d'insieme, non una cardinalita': un nome sconosciuto al posto di uno
        #     atteso lascia il conteggio intatto e cambia l'insieme.
        verdi_attesi = set(VENTI_ATTESE) | {"P27"}
        for nome in sorted(verdi_attesi):
            assert verdi.count(nome) == 1, \
                f"{nome}: {verdi.count(nome)} righe {ETICHETTA_VERDE}, attesa 1"
        assert set(verdi) == verdi_attesi, \
            f"verdi inattesi {sorted(set(verdi) - verdi_attesi)}, " \
            f"attesi e assenti {sorted(verdi_attesi - set(verdi))}"

        # (3) nessun nome compare in piu' di una classe
        for a, b, ea, eb in ((na, verdi, "non applicabile", "verde"),
                             (na, rosse, "non applicabile", "rossa"),
                             (verdi, rosse, "verde", "rossa")):
            comuni = set(a) & set(b)
            assert not comuni, f"{sorted(comuni)} compare come {ea} e come {eb}"

        # (4) nessun nome SCONOSCIUTO fra tutte le etichette
        noti = set(SEI_ATTESI) | verdi_attesi
        sconosciuti = set(nomi) - noti
        assert not sconosciuti, f"nomi sconosciuti fra le etichette: {sorted(sconosciuti)}"

        # (5) nessuna prova rossa
        assert not rosse, f"rosse nella corsa annidata: {rosse}"

        # (6) e il conteggio indipendente degli applicabili, accanto all'identita'
        assert len(verdi) == APPLICABILI_ATTESE, \
            f"verdi {len(verdi)}, attesi {APPLICABILI_ATTESE}"
        assert f"{APPLICABILI_ATTESE}/{APPLICABILI_ATTESE} applicabili verdi" in testo, \
            "il riepilogo non concorda con le etichette"
        assert "gate di provisioning: verde" in testo, "gate non verde nella corsa annidata"
        assert codice_annidato == 0, f"exit della corsa annidata {codice_annidato}, atteso 0"
    finally:
        replay_validate.valida_schema = vero


# ------------------------------------------------------------------ esecuzione

def main():
    """UNA invocazione, UN contesto di provisioning, UNA valutazione decisionale.

    Il contesto e' dell'invocazione, non del processo: una chiamata annidata a `main()` e' una
    nuova invocazione e rivaluta la sonda. Una cache globale renderebbe la corsa annidata cieca —
    leggerebbe lo stato dell'invocazione esterna e classificherebbe zero non applicabili dove ce
    ne sono sei.

    QUI, E SOLTANTO QUI, si cattura l'eccezione che la sonda ha lasciato salire. Non esiste un
    `try` attorno a `main()`: una rete esterna farebbe morire il processo senza riepilogo, e
    l'osservatore del terzo stato — che pretende una riga rossa NOMINATA — diventerebbe invisibile.
    """
    try:
        stato = sonda_capability()
    except BaseException as e:                                            # noqa: BLE001
        stato, diagnostica = "TERZO_STATO", f"{type(e).__name__}: {e}"
    else:
        diagnostica = None

    precedente = _CTX["stato"]
    _CTX["stato"] = stato
    istantanea = oracle_ripristino()
    try:
        return _corpo(stato, diagnostica, istantanea)
    finally:
        _CTX["stato"] = precedente


def _corpo(stato, diagnostica, istantanea):
    print(f"replay_offline_selftest — {len(ESITI)} prove — provisioning: {stato}\n")

    if stato == "TERZO_STATO":
        # Il gate si arresta PRIMA delle prove: nessuna viene avviata, e non si dichiara non
        # applicabile nulla, perche' non si e' accertato nulla. Un terzo stato non e' un ambiente
        # incompleto: e' un guasto, e si presenta nominato invece che come traceback.
        print(f"  {ETICHETTA_ROSSA} provisioning  TERZO_STATO — "
              f"{diagnostica or 'la sonda non ha prodotto uno stato ammesso'}")
        print("\n  gate di provisioning: ROSSO")
        print("  nessuna prova avviata")
        return 1

    non_applicabili = set(SEI_DIPENDENTI) if stato == "SCHEMA_INDISPONIBILE" else set()
    falliti, verdi, saltate = [], 0, []
    for nome, desc, f in ESITI:
        if nome in non_applicabili:
            # non si esegue fingendo aspettative diverse: o intera, o per niente
            saltate.append(nome)
            print(f"  {ETICHETTA_NA} {nome:<4} {desc} — {MOTIVO_NA}")
            continue
        try:
            f()
            verdi += 1
            print(f"  {ETICHETTA_VERDE} {nome:<4} {desc}")
        except Exception as e:                                            # noqa: BLE001
            falliti.append((nome, e))
            print(f"  {ETICHETTA_ROSSA} {nome:<4} {desc}\n          {type(e).__name__}: {e}")

    # ---- il gate: tre controlli, e ognuno violato vale una riga rossa e `exit 1`
    guasti = []
    if stato == "SCHEMA_DISPONIBILE" and saltate:
        guasti.append(f"non applicabili con capability presente: {sorted(saltate)}")
    finale = oracle_ripristino()
    for chiave, atteso in istantanea.items():
        if finale.get(chiave) is not atteso:
            guasti.append(f"ripristino: `{chiave}` non e' stato ripristinato")

    print(f"\n  gate di provisioning: {'verde' if not guasti else 'ROSSO'}")
    for g in guasti:
        print(f"    · {g}")
    if saltate:
        # per NOME, non per conteggio: «sei non applicabili» e' la frase con cui si perde di
        # vista quali sei
        print(f"  NON APPLICABILI ({len(saltate)}): {', '.join(saltate)} — {MOTIVO_NA}")
        print(f"  {verdi}/{verdi + len(falliti)} applicabili verdi"
              + (f" — ROSSE: {', '.join(n for n, _e in falliti)}" if falliti else ""))
    else:
        print(f"  {verdi}/{verdi + len(falliti)} verdi"
              + (f" — ROSSE: {', '.join(n for n, _e in falliti)}" if falliti else ""))
    return 1 if (falliti or guasti) else 0


if __name__ == "__main__":
    sys.exit(main())
