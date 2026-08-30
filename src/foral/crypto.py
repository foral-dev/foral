"""
app/browser/crypto.py

Criptografia de sessões em repouso (storage_state).
Garante que cookies e tokens de sessão salvos no disco não fiquem em texto puro.
"""

import os
from cryptography.fernet import Fernet


def _get_encryption_key() -> bytes:
    """
    Recupera a chave de criptografia do ambiente.
    Se não estiver configurada, levanta erro fatal: não degradamos para texto puro.
    Gerar nova chave:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    key = os.getenv("SESSION_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "SESSION_ENCRYPTION_KEY não configurada. Sem ela, sessões NÃO PODEM ser "
            "salvas ou lidas — falha explícita é melhor que gravar em texto puro."
        )
    return key.encode()


def encrypt_state(raw_json_bytes: bytes) -> bytes:
    """Cifra bytes de JSON bruto."""
    f = Fernet(_get_encryption_key())
    return f.encrypt(raw_json_bytes)


def decrypt_state(encrypted_bytes: bytes) -> bytes:
    """Decifra bytes cifrados retornando JSON bruto."""
    f = Fernet(_get_encryption_key())
    return f.decrypt(encrypted_bytes)
