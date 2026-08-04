#!/usr/bin/env python3
"""
provider_switch.py — sposta uno stage di raccolta da Anthropic a DeepSeek, e viceversa.

PERCHE' PASSA DAL CONFIG INVECE DI ESSERE UN FLAG A RUNTIME.

Un interruttore che cambia il comportamento senza lasciare traccia nel config sarebbe l'unica cosa
in questa pipeline che sfugge a `spec_check.py`. Qui invece l'interruttore fa una sola cosa: scrive
`config.provider.attivo_per_agente`. Da quel momento valgono tutte le guardie gia' esistenti —
`SC-20` rifiuta la delega degli agenti che non si delegano, il manifesto del run registra chi ha
girato dove, e due run con deleghe diverse restano confrontabili perche' la differenza e' scritta.

E fa una seconda cosa che nessun altro strumento di questo repo faceva: **valida la propria
modifica e la annulla se rompe qualcosa**. Scrive, esegue `spec_check.py`, e se esce diverso da zero
rimette il file com'era e stampa l'errore. Un editor di configurazione che puo' lasciare il repo in
uno stato che il proprio cancello rifiuta e' esattamente il difetto che questa sessione ha inseguito
per otto versioni, in una forma nuova.

COSA RIFIUTA, E PERCHE' NON C'E' UN --force.

Gli agenti di solo giudizio non hanno strumenti di ricerca: delegarli non risparmia niente e rompe
la separazione dei poteri. Il `pain-finder` e' l'unico stadio creativo e il collo di bottiglia
misurato del funnel. Il `competitor-analyst` decide la qualificazione DIRETTO/ADIACENTE, che si
propaga a ogni gate. E il `red-team` e' l'avversario: delegarlo al modello piu' economico non
produce un errore visibile, produce un PASS — e te ne accorgi costruendo l'idea e fallendo.

Il rifiuto e' un dosso, non un muro: se vuoi farlo davvero, modifica `config.provider.delegabili`
a mano e `spec_check.py` ti dira' cosa stai spegnendo. Deve costare un gesto deliberato, non un
flag battuto di fretta alle undici di sera guardando la bolletta.

  uso:  python3 tools/provider_switch.py --stato
        python3 tools/provider_switch.py --delega evidence-researcher,tam-analyst
        python3 tools/provider_switch.py --delega haiku         # tutti e sei i delegabili
        python3 tools/provider_switch.py --revoca haiku         # tutti indietro su Anthropic
        python3 tools/provider_switch.py --revoca tutti
        exit 0 = fatto · 1 = rifiutato · 2 = annullato perche' spec_check falliva
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
AGENTI = os.path.join(ROOT, ".claude", "agents")


def carica():
    return json.load(open(CONFIG, encoding="utf-8"), object_pairs_hook=collections.OrderedDict)


def agenti_su_disco():
    return sorted(f[:-3] for f in os.listdir(AGENTI) if f.endswith(".md"))


def stato(cfg):
    prov = cfg.get("provider") or {}
    attivo = prov.get("attivo_per_agente") or {}
    delegabili = set(prov.get("delegabili") or [])
    modelli = ((cfg.get("modelli") or {}).get("per_agente") or {})
    print(f"\n  provider disponibili: {', '.join(prov.get('disponibili') or [])}")
    print(f"  {'-'*74}")
    print(f"  {'agente':<22} {'tier':<8} {'gira su':<12} {'delegabile'}")
    print(f"  {'-'*74}")
    non_chiusi = {a for a, v in (prov.get("chiuso_sul_proprio_input") or {}).items()
                  if isinstance(v, dict) and v.get("chiuso") is False}
    for a in agenti_su_disco():
        p = attivo.get(a, "anthropic")
        # Due NO diversi. "Troppo importante" e' una scelta che si puo' rivedere guardando la
        # bolletta; "il ponte non naviga" e' un'impossibilita', e presentarli con la stessa
        # etichetta invita a rivedere anche il secondo — cioe' a riaprire il buco.
        if a in delegabili:
            eti = "si"
        elif a in non_chiusi:
            eti = "NO — il ponte non puo' aprire pagine, e il suo lavoro e' aprirle"
        else:
            eti = "NO — vedi il perche in config.provider"
        print(f"  {a:<22} {(modelli.get(a) or {}).get('modello', '?'):<8} {p:<12} {eti}")
    print(f"  {'-'*74}")
    n = sum(1 for a in agenti_su_disco() if attivo.get(a, "anthropic") != "anthropic")
    print(f"  {n} agenti su {len(agenti_su_disco())} girano fuori da Anthropic.\n")


def applica(cfg, bersagli, provider_nuovo):
    prov = cfg.setdefault("provider", collections.OrderedDict())
    delegabili = set(prov.get("delegabili") or [])
    su_disco = set(agenti_su_disco())
    attivo = prov.setdefault("attivo_per_agente", collections.OrderedDict())

    if bersagli in (["haiku"], ["raccolta"]):
        bersagli = sorted(delegabili)
    elif bersagli in (["tutti"], ["haiku"]) and provider_nuovo == "anthropic":
        bersagli = sorted(su_disco)
    elif bersagli == ["tutti"]:
        bersagli = sorted(delegabili)

    ignoti = [b for b in bersagli if b not in su_disco]
    if ignoti:
        print(f"  agenti inesistenti: {', '.join(ignoti)}", file=sys.stderr)
        return 1
    if provider_nuovo != "anthropic":
        vietati = [b for b in bersagli if b not in delegabili]
        if vietati:
            print(f"\n  RIFIUTATO. Non sono delegabili: {', '.join(vietati)}\n", file=sys.stderr)
            chius = prov.get("chiuso_sul_proprio_input") or {}
            impossibili = [b for b in vietati
                           if isinstance(chius.get(b), dict) and chius[b].get("chiuso") is False]
            if impossibili:
                print(f"  Per {', '.join(impossibili)} non e' una scelta prudente: e' impossibile.\n",
                      file=sys.stderr)
                for b in impossibili:
                    print(f"  {chius[b].get('perche', '')}\n", file=sys.stderr)
                print("  Aggiungerlo a mano ai delegabili non lo rende capace di aprire una\n"
                      "  pagina: lo rende un verificatore che dice sempre di si'.\n",
                      file=sys.stderr)
            print(f"  {prov.get('_delegabili_regola', '')}\n", file=sys.stderr)
            print("  Se vuoi farlo davvero, aggiungili a mano a config.provider.delegabili:\n"
                  "  spec_check.py ti dira' cosa stai spegnendo. Deve costare un gesto\n"
                  "  deliberato, non un flag.\n", file=sys.stderr)
            return 1
        if provider_nuovo not in (prov.get("disponibili") or []):
            print(f"  provider non dichiarato: {provider_nuovo}", file=sys.stderr)
            return 1
        chiave = (prov.get(provider_nuovo) or {}).get("env_chiave")
        if chiave:
            v, dove = trova_chiave(chiave)
            if v:
                print(f"\n  {chiave}: trovata in {dove}.")
            else:
                print(f"\n  ATTENZIONE: {chiave} non trovata. La delega viene scritta lo stesso,\n"
                      f"  ma il run fallirebbe alla prima chiamata.\n")
                print(diagnosi(chiave))

    for b in bersagli:
        if provider_nuovo == "anthropic":
            attivo.pop(b, None)
        else:
            attivo[b] = provider_nuovo
    return 0


def main():
    a = sys.argv[1:]
    if not a or "--stato" in a:
        stato(carica()); return 0

    def arg(n):
        return a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None

    if arg("--delega"):
        bersagli, nuovo = arg("--delega").split(","), arg("--provider") or "deepseek"
    elif arg("--revoca"):
        bersagli, nuovo = arg("--revoca").split(","), "anthropic"
    else:
        print(__doc__.split("  uso:")[-1]); return 1

    cfg = carica()
    backup = CONFIG + ".prima-dello-switch"
    shutil.copy2(CONFIG, backup)
    esito = applica(cfg, [b.strip() for b in bersagli], nuovo)
    if esito:
        os.remove(backup); return esito
    json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # La parte che rende questo strumento diverso da un'edit a mano: valida se stesso.
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "spec_check.py")],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        shutil.move(backup, CONFIG)
        print("\n  ANNULLATO: la modifica faceva fallire spec_check, e il config e' stato\n"
              "  rimesso com'era. Nessun run puo' partire da uno stato che il proprio\n"
              "  cancello rifiuta.\n", file=sys.stderr)
        print(r.stdout[r.stdout.find("ERRORI"):][:800], file=sys.stderr)
        return 2
    os.remove(backup)
    stato(carica())
    print("  Scritto in config.provider.attivo_per_agente e validato.")
    print("  Il manifesto del prossimo run registrera' chi ha girato dove: due run con\n"
          "  deleghe diverse restano confrontabili solo perche' la differenza e' scritta.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
