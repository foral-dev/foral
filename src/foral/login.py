"""`foral login <system>` — capture a durable session LOCALLY, on the customer's own
machine. Opens the system's login page in a real browser; the human signs in; on ENTER we
save the encrypted `storage_state` with session cookies promoted to ~30 days ("remember me").

The session NEVER leaves this machine — that is the security cornerstone. The agent never
drives a logged-in browser; the human logs in. Requires the `foral[login]` extra
(Playwright): `pip install 'foral[login]' && playwright install chromium`."""
from __future__ import annotations

import json
import os
import sys
import time


def _caminho_sistema(tenant_id: str, sistema: str) -> str:
    d = os.path.join(os.getenv("SESSION_DATA_DIR", "./sessions"), tenant_id, sistema)
    os.makedirs(d, exist_ok=True)
    return d


def login(sistema: str, tenant_id: str) -> int:
    from foral.executor import carregar_contrato_do_sistema
    from foral.crypto import encrypt_state
    try:
        contrato = carregar_contrato_do_sistema(sistema)
    except Exception as e:
        sys.stderr.write(f"contrato de '{sistema}' ausente/inválido: {e}\n")
        return 1
    login_url = getattr(getattr(contrato, "auth", None), "login_url", None) \
        or ("https://" + contrato.dominios[0])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.stderr.write("Playwright não está instalado (necessário só para o login).\n"
                         "  pip install 'foral[login]'\n  playwright install chromium\n")
        return 3

    state_dir = _caminho_sistema(tenant_id, sistema)
    sys.stderr.write(f"Abrindo {login_url}\nFaça login no seu sistema; a sessão fica SÓ nesta "
                     f"máquina.\n")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(login_url, wait_until="commit", timeout=60000)
        except Exception:
            pass
        try:
            input("\n>>> Depois de logar, pressione ENTER aqui para salvar a sessão... ")
        except (EOFError, KeyboardInterrupt):
            browser.close()
            sys.stderr.write("\ncancelado — nada salvo.\n")
            return 1
        state = ctx.storage_state()
        browser.close()

    # promove cookie de sessão a PERSISTENTE (30 dias) — o "lembrar de mim"
    agora = time.time()
    for c in state.get("cookies", []):
        if c.get("expires", -1) in (-1, None) or c.get("expires", 0) < agora:
            c["expires"] = agora + 30 * 24 * 3600
    with open(os.path.join(state_dir, "state.json"), "wb") as f:
        f.write(encrypt_state(json.dumps(state).encode()))
    n = len(state.get("cookies", []))
    sys.stderr.write(f"✓ sessão salva (cifrada, ~30 dias, {n} cookie(s)) em "
                     f"{state_dir}/state.json\n  agora: foral serve {sistema}\n")
    return 0
