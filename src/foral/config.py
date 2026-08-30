"""Config LOCAL do runner (v0.4.0) — zero fricção sem afrouxar o fail-closed.

`~/.foral/` guarda, por instalação: `tenant` (ALEATÓRIO por instalação — nunca um literal
compartilhado entre clientes, que era o risco vetado no red-team), `key` (Fernet, chmod 600,
gerada uma vez), `contracts/` e `sessions/`. As envs SEMPRE vencem (FORAL_TENANT,
SESSION_ENCRYPTION_KEY, CONTRATOS_DIR, SESSION_DATA_DIR) — times/CI seguem explícitos.
Erro de I/O aqui levanta claro; nunca degrada em silêncio."""
from __future__ import annotations

import os
import secrets


def home() -> str:
    return os.getenv("FORAL_HOME") or os.path.expanduser("~/.foral")


def _ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def contracts_dir() -> str:
    return _ensure(os.path.join(home(), "contracts"))


def sessions_dir() -> str:
    return _ensure(os.path.join(home(), "sessions"))


def tenant() -> str:
    """FORAL_TENANT do ambiente, ou o tenant POR INSTALAÇÃO (gerado uma vez, aleatório)."""
    env = os.getenv("FORAL_TENANT")
    if env:
        return env
    p = os.path.join(_ensure(home()), "tenant")
    if os.path.exists(p):
        t = open(p).read().strip()
        if t:
            return t
    t = "t-" + secrets.token_hex(6)
    with open(p, "w") as f:
        f.write(t + "\n")
    return t


def encryption_key() -> str:
    """SESSION_ENCRYPTION_KEY do ambiente, ou a chave por instalação (gerada 1x, chmod 600)."""
    env = os.getenv("SESSION_ENCRYPTION_KEY")
    if env:
        return env
    p = os.path.join(_ensure(home()), "key")
    if os.path.exists(p):
        k = open(p).read().strip()
        if k:
            return k
    from cryptography.fernet import Fernet
    k = Fernet.generate_key().decode()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # só o dono lê
    with os.fdopen(fd, "w") as f:
        f.write(k + "\n")
    return k


def import_contract(path: str) -> str:
    """Valida o YAML (fail-closed — inválido levanta AQUI, nada é copiado) e o instala em
    contracts_dir() como `<sistema>.yaml`. Devolve o nome do sistema (minúsculo)."""
    import shutil
    import yaml
    from foral.contract import carregar_contrato

    with open(path) as f:
        dados = yaml.safe_load(f)
    c = carregar_contrato(dados)
    dest = os.path.join(contracts_dir(), f"{c.sistema.lower()}.yaml")
    shutil.copyfile(path, dest)
    return c.sistema.lower()


def resolve_system(arg: str) -> str:
    """`foral serve/login` aceitam NOME ou CAMINHO de um .yaml baixado: caminho existente →
    importa (validado) e devolve o nome; senão trata como nome já instalado."""
    if arg.endswith((".yaml", ".yml")) or os.path.sep in arg or os.path.exists(arg):
        p = os.path.expanduser(arg)
        if os.path.exists(p):
            return import_contract(p)
        raise FileNotFoundError(f"arquivo de contrato não existe: {arg}")
    return arg
