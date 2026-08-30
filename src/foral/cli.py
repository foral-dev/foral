"""The `foral` CLI — the RUNNER that lives on the customer's infrastructure.

`foral serve <system>` serves the system's contract as an MCP server (stdio) — the command a
dev registers with `claude mcp add foral -- foral serve <system>`. `foral verify <system>`
validates the contract (fail-closed) and exits. Reads run browserless via the contract;
writes stay human-approved. No CLI framework dependency (auditable).

This is the COMMODITY RUNNER (safe to distribute) — the discovery brain is never shipped."""
import sys

USO = """foral — charters for software without public APIs

uso:
  foral serve <sistema>                serve o contrato como MCP server (stdio)
  foral login <sistema>                loga no sistema e salva a sessão (local, ~30 dias)
  foral update <sistema> --from <URL>  adota uma nova versão do contrato (fail-closed)
  foral verify <sistema>               valida o contrato (fail-closed) e sai
  foral keygen                         gera uma SESSION_ENCRYPTION_KEY nova
  foral --help

env: FORAL_TENANT (obrigatório p/ serve/login) · CONTRATOS_DIR (onde vive o contrato) ·
     SESSION_ENCRYPTION_KEY (cifra a sessão) · SESSION_DATA_DIR · FORAL_HOSTS_MAP (dev)
"""


def _serve(sistema: str, tenant_id: str) -> int:
    from foral.executor import carregar_contrato_do_sistema
    from foral.serve import servir
    try:
        contrato = carregar_contrato_do_sistema(sistema)
    except Exception as e:
        sys.stderr.write(f"contrato inválido/ausente: {e}\n")
        return 1
    servir(contrato, tenant_id)
    return 0


def _verify(sistema: str) -> int:
    from foral.executor import carregar_contrato_do_sistema
    try:
        c = carregar_contrato_do_sistema(sistema)
    except Exception as e:
        sys.stderr.write(f"✗ {sistema}: {e}\n")
        return 1
    leituras = sum(1 for x in c.capacidades if x.escopo.value == "leitura")
    escritas = len(c.capacidades) - leituras
    sys.stderr.write(f"✓ {c.sistema} v{c.versao_contrato}: {leituras} leitura(s) + "
                     f"{escritas} escrita(s) HITL · domínio {c.dominios[0]}\n")
    return 0


def main(argv=None) -> int:
    import os
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stderr.write(USO)
        return 0
    cmd, resto = argv[0], argv[1:]
    if cmd == "keygen":
        # gera uma chave Fernet válida p/ SESSION_ENCRYPTION_KEY (sem browser, sem tenant).
        # roda no venv do foral, que já tem cryptography — o quickstart não depende do
        # python do sistema ter a lib.
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    # fail-closed (red-team 29/08): sem FORAL_TENANT explícito, NÃO assumir um tenant
    # genérico compartilhável — dois clientes cairiam no mesmo store de sessão.
    tenant_id = os.getenv("FORAL_TENANT")
    if cmd in ("serve", "login") and not tenant_id:
        sys.stderr.write("FORAL_TENANT ausente — defina o tenant explicitamente (fail-closed)\n")
        return 2
    if cmd == "serve":
        if not resto:
            sys.stderr.write("uso: foral serve <sistema>\n")
            return 2
        return _serve(resto[0], tenant_id)
    if cmd == "login":
        if not resto:
            sys.stderr.write("uso: foral login <sistema>\n")
            return 2
        from foral.login import login
        return login(resto[0], tenant_id)
    if cmd == "update":
        if not resto:
            sys.stderr.write("uso: foral update <sistema> --from <URL>\n")
            return 2
        from_url = None
        if "--from" in resto:
            i = resto.index("--from")
            from_url = resto[i + 1] if i + 1 < len(resto) else None
        from foral.update import update
        return update(resto[0], from_url)
    if cmd == "verify":
        if not resto:
            sys.stderr.write("uso: foral verify <sistema>\n")
            return 2
        return _verify(resto[0])
    sys.stderr.write(f"comando desconhecido: {cmd}\n{USO}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
