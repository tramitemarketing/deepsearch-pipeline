#!/usr/bin/env python3
"""
setup.py — configurazione guidata. Si esegue UNA VOLTA, non a ogni run.

PERCHE' NON SI CHIEDE A OGNI RUN.

Questa pipeline e' nata per girare come task pianificato, di notte, senza nessuno davanti: §11 dice
esplicitamente che in modalita' non interattiva non si pongono domande e si applicano i default.
Un run che chiede qualcosa alle 3:20 del mattino non parte, e nessuno se ne accorge fino al venerdi'
dopo.

La forma corretta non e' "chiedere invece di usare i flag": e' chiedere **una volta**, scrivere le
risposte nel config, e da quel momento non chiedere piu'. I flag restano come scavalco per il caso
singolo (`--profile LIGHT` per un run veloce), e il run pianificato non vede mai una domanda.

Come provider_switch, questo strumento **valida la propria modifica**: scrive, esegue spec_check, e
se fallisce rimette il config com'era.

  uso:  python3 tools/setup.py            → configurazione guidata
        python3 tools/setup.py --chiave   → solo la chiave del provider esterno
        python3 tools/setup.py --stato    → cosa e' configurato adesso
        python3 tools/setup.py --diagnosi → dove cerco la chiave e cosa trovo
"""
import json, os, shutil, subprocess, sys, collections
import sys as _sys
for _f in (_sys.stdout, _sys.stderr):      # console Windows in cp1252: '→' e '·' fanno crashare
    try: _f.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


import os, sys as _s
_s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _chiavi import trova_chiave, diagnosi   # unico posto che sa dove sta una chiave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "deepsearch.config.json")
ENV = os.path.join(ROOT, ".env")


def carica():
    return json.load(open(CONFIG, encoding="utf-8"), object_pairs_hook=collections.OrderedDict)


def chiedi(domanda, opzioni, default):
    """Una domanda con opzioni numerate. Invio = default."""
    print(f"\n  {domanda}")
    for i, (val, desc) in enumerate(opzioni, 1):
        marca = " (default)" if val == default else ""
        print(f"    {i}. {val}{marca} — {desc}")
    r = input(f"  scelta [1-{len(opzioni)}, invio = default]: ").strip()
    if not r:
        return default
    try:
        return opzioni[int(r) - 1][0]
    except (ValueError, IndexError):
        print("  scelta non valida, uso il default.")
        return default


def leggi_env():
    if not os.path.exists(ENV):
        return {}
    d = {}
    for l in open(ENV, encoding="utf-8"):
        l = l.strip()
        if l and not l.startswith("#") and "=" in l:
            k, _, v = l.partition("=")
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def scrivi_env(chiave, valore):
    """
    La chiave va in un file, MAI nel config.

    config/deepsearch.config.json finisce in ogni zip, in ogni copia, in ogni allegato mandato a
    qualcuno per farsi revisionare la pipeline — e' successo dieci volte in questo progetto. Una
    chiave li' dentro e' una chiave pubblicata. Il file .env sta nel .gitignore e SC-21 fallisce se
    una stringa che sembra una chiave compare in qualunque altro file del repo.
    """
    d = leggi_env()
    d[chiave] = valore
    with open(ENV, "w", encoding="utf-8") as g:
        g.write("# Chiavi dei provider esterni. NON committare, NON allegare, NON mettere in "
                "config.\n# Letto da tools/ds_call.py quando la variabile d'ambiente non e' gia' "
                "impostata.\n")
        for k, v in d.items():
            g.write(f"{k}={v}\n")
    try:
        os.chmod(ENV, 0o600)
    except Exception:
        pass
    gi = os.path.join(ROOT, ".gitignore")
    righe = open(gi, encoding="utf-8").read().splitlines() if os.path.exists(gi) else []
    if ".env" not in righe:
        with open(gi, "a", encoding="utf-8") as g:
            g.write("\n# chiavi dei provider esterni — mai nel repo\n.env\n")


def imposta_chiave(cfg):
    prov = cfg.get("provider") or {}
    esterni = [p for p in (prov.get("disponibili") or []) if p != "anthropic"]
    if not esterni:
        print("  nessun provider esterno dichiarato in config."); return
    nome = esterni[0] if len(esterni) == 1 else chiedi(
        "Per quale provider?", [(p, "") for p in esterni], esterni[0])
    var = (prov.get(nome) or {}).get("env_chiave")
    gia, _d = trova_chiave(var)
    if gia:
        print(f"\n  {var} e' gia' impostata ({gia[:7]}…{gia[-4:]}).")
        if input("  sostituirla? [s/N]: ").strip().lower() not in ("s", "si", "y"):
            return
    print(f"\n  Incolla la chiave {nome} (resta solo in .env, mai nel config):")
    v = input(f"  {var}= ").strip()
    if not v:
        print("  niente da scrivere."); return
    scrivi_env(var, v)
    print(f"\n  Scritta in .env (permessi 600) e aggiunta al .gitignore.")
    print(f"  Su Windows `setx {var} \"...\"` la rende visibile anche fuori — ma solo dalla\n"
          f"  PROSSIMA finestra del terminale, non da questa. Il .env vale subito.")
    print("  ds_call.py legge prima la variabile d'ambiente, poi .env: se la imposti in\n"
          "  entrambi vince l'ambiente.")


def guidata():
    cfg = carica()
    prima = json.dumps(cfg, ensure_ascii=False, indent=2)
    print("\n  CONFIGURAZIONE GUIDATA — si esegue una volta, non a ogni run.")
    print("  Invio accetta il default; i flag della riga di comando scavalcano sempre.")

    cfg["run"]["profile"] = chiedi(
        "Profilo predefinito dei run:",
        [("LIGHT", "4 shard, ~20 pain, il piu' economico — buono per provare"),
         ("BALANCED", "6 shard, ~40 pain, ~2-4 ore, ~110 agenti"),
         ("DEEP", "8 shard, ~80 pain, diverse ore, 200+ agenti")],
        cfg["run"]["profile"])

    cfg["run"]["threshold_profile"] = chiedi(
        "Severita' delle soglie sui competitor:",
        [("CALIBRATED", "tarate sul mercato italiano di nicchia — consigliata"),
         ("STRICT", "piu' severe: piu' KILL, piu' rischio di falsi negativi")],
        cfg["run"]["threshold_profile"])

    cfg["run"]["cadenza"]["modo"] = chiedi(
        "Cadenza:",
        [("SENTINELLA_PIU_INDAGINE", "vigilanza settimanale quasi gratis, indagine solo se serve"),
         ("SOLO_SU_RICHIESTA", "gira solo quando lo lanci tu"),
         ("SETTIMANALE_FISSO", "un run completo ogni settimana, sempre")],
        cfg["run"]["cadenza"]["modo"])

    prov = cfg.setdefault("provider", collections.OrderedDict())
    delegabili = prov.get("delegabili") or []
    if delegabili:
        scelta = chiedi(
            f"Delegare gli stage di sola raccolta a un provider esterno?\n"
            f"     (sono {', '.join(delegabili)} — gli altri non sono delegabili per invariante)",
            [("no", "tutto su Anthropic"),
             ("si", "i sei agenti haiku su DeepSeek — i quattro sonnet restano su Anthropic")],
            "no")
        attivo = prov.setdefault("attivo_per_agente", collections.OrderedDict())
        if scelta == "si":
            for a in delegabili:
                attivo[a] = "deepseek"
            imposta_chiave(cfg)
        else:
            for a in delegabili:
                attivo.pop(a, None)

    backup = CONFIG + ".prima-del-setup"
    open(backup, "w", encoding="utf-8").write(prima)
    json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "spec_check.py")],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        shutil.move(backup, CONFIG)
        print("\n  ANNULLATO: la configurazione faceva fallire spec_check ed e' stata rimessa\n"
              "  com'era. Nessun run puo' partire da uno stato che il proprio cancello rifiuta.\n")
        print(r.stdout[r.stdout.find("ERRORI"):][:600])
        return 2
    os.remove(backup)
    stato()
    print("  Scritto in config e validato. Da adesso i run non fanno domande: usa i flag solo\n"
          "  per il caso singolo (--profile LIGHT), e il run pianificato non ne vedra' mai una.\n")
    return 0


def stato():
    cfg = carica()
    prov = cfg.get("provider") or {}
    attivo = prov.get("attivo_per_agente") or {}
    env = leggi_env()
    print(f"\n  profilo ............ {cfg['run']['profile']}")
    print(f"  soglie ............. {cfg['run']['threshold_profile']}")
    print(f"  cadenza ............ {cfg['run']['cadenza']['modo']}")
    n = len([a for a, p in attivo.items() if p != "anthropic"])
    print(f"  delega esterna ..... {(str(n) + ' agenti: ' + ', '.join(sorted(a for a, p in attivo.items() if p != 'anthropic'))) if n else 'nessuna, tutto su Anthropic'}")
    for p_ in (prov.get("disponibili") or []):
        if p_ == "anthropic":
            continue
        var = (prov.get(p_) or {}).get("env_chiave")
        _v, dove = trova_chiave(var)
        print(f"  chiave {p_:<12} {'impostata (' + dove + ')' if dove else 'ASSENTE — il run fallirebbe alla prima chiamata'}")
    print()


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--stato" in a:
        stato(); sys.exit(0)
    if "--diagnosi" in a:
        cfg = carica(); prov = cfg.get("provider") or {}
        for p_ in (prov.get("disponibili") or []):
            if p_ == "anthropic": continue
            print(f"\n  === {p_} ===\n" + diagnosi((prov.get(p_) or {}).get("env_chiave")))
        print(); sys.exit(0)
    if "--chiave" in a:
        imposta_chiave(carica()); sys.exit(0)
    try:
        sys.exit(guidata())
    except (KeyboardInterrupt, EOFError):
        print("\n  interrotto: niente e' stato scritto.\n"); sys.exit(1)
