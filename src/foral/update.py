"""`foral update <system> --from <URL>` — adopt a NEW version of the contract.

The healing brain (contract re-discovery) runs on Foral's infrastructure, never here; the
runner only ADOPTS the new version. On each read the runner already fingerprints the shape
and alarms when the system changed — that alarm is your cue to update. Fail-closed: a
downloaded contract that doesn't validate is NOT applied."""
from __future__ import annotations

import os
import sys
import urllib.request


def update(sistema: str, from_url: str | None) -> int:
    dst_dir = os.getenv("CONTRATOS_DIR") or "."
    dst = os.path.join(dst_dir, f"{sistema.lower()}.yaml")
    if not from_url:
        sys.stderr.write("uso: foral update <sistema> --from <URL do contrato>\n"
                         "(a nova versão é publicada pelo cérebro do Foral; aponte a URL dela)\n")
        return 2
    try:
        with urllib.request.urlopen(from_url, timeout=30) as r:
            data = r.read(5_000_000)
    except Exception as e:
        sys.stderr.write(f"falha ao baixar {from_url}: {e}\n")
        return 1
    # valida ANTES de gravar — nunca troca por um contrato quebrado (fail-closed)
    from foral.contract import carregar_contrato
    import yaml
    try:
        c = carregar_contrato(yaml.safe_load(data))
    except Exception as e:
        sys.stderr.write(f"contrato baixado inválido — NÃO aplicado: {e}\n")
        return 1
    os.makedirs(dst_dir, exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    sys.stderr.write(f"✓ {c.sistema} v{c.versao_contrato} aplicado em {dst}\n")
    return 0
