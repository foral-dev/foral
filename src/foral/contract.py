"""O CONTRATO de um sistema sem API pública — o schema e a validação.

Um contrato transforma um sistema web sem API pública em capacidades tipadas, versionadas e
auditáveis: o agente conhece a CAPACIDADE, não o navegador. O executor decide o MODO
(network_json antes de playwright_read); o contrato declara o QUE se lê, não o COMO se obtém.

Este módulo é só o SCHEMA + a validação — puro, testável, sem browser.

Princípios que moram AQUI (não em quem chama):
  · fail-closed: contrato inválido não carrega (um extrator vazio, um host fora do formato, uma
    capacidade de ESCRITA sem HITL declarado — recusa no load, não no meio da execução);
  · fingerprint de shape por capacidade — a régua que detecta mudança de forma na fonte;
  · toda capacidade de listagem declara o cursor incremental (delta-first: sem cursor, relê o
    mundo a cada ciclo).
"""
from __future__ import annotations
import re
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator

RE_HOST = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


class ModoLeitura(str, Enum):
    # Ordem de preferência: o JSON interno da SPA é mais estável que o DOM.
    NETWORK_JSON = "network_json"   # interceptar a resposta de rede que a página já busca
    PLAYWRIGHT_READ = "playwright_read"  # extrair por seletor do DOM renderizado
    PLAYWRIGHT_ACTION = "playwright_action"  # ESCRITA: preencher/clicar um fluxo mapeado (só HITL)


class Escopo(str, Enum):
    LEITURA = "leitura"     # default — livre
    ESCRITA = "escrita"     # nasce DESLIGADA, exige hitl=True declarado, e o runtime confirma


class Extrator(BaseModel):
    """Onde ler UM campo. `network_json` usa `caminho` (dot-path no JSON); `playwright_read`
    usa `seletor` (+ multi-âncora `alternativas` para o healing camada-0 tolerar renome)."""
    campo: str
    caminho: Optional[str] = None          # ex.: "data.jobs[].deadline"  (modo network_json)
    seletor: Optional[str] = None          # ex.: "[data-testid=prazo]"    (modo playwright_read)
    alternativas: list[str] = Field(default_factory=list)  # âncoras de reserva (tolerância)
    obrigatorio: bool = True               # campo que, ausente, dispara o fingerprint

    @model_validator(mode="after")
    def _um_ou_outro(self):
        if not self.caminho and not self.seletor:
            raise ValueError(f"extrator '{self.campo}' sem caminho nem seletor — vazio não lê nada")
        return self


class Capacidade(BaseModel):
    nome: str                              # ex.: "listar_pautas" — o nome que o agente chama
    modo: ModoLeitura
    rota: str                              # GET observado (network_json) ou path a navegar (dom)
    # ── LEITURA ──
    lista: bool = False                    # devolve N itens? então precisa de cursor (delta-first)
    cursor: Optional[str] = None           # o campo/param que pagina — obrigatório se lista=True
    id_na_fonte: Optional[str] = None      # o campo que É a identidade (idempotência do espelho)
    extratores: list[Extrator] = Field(default_factory=list)
    # ── ESCRITA (só com HITL) ──
    escopo: Escopo = Escopo.LEITURA
    hitl: bool = False                     # obrigatório quando escopo=ESCRITA
    parametros: list[str] = Field(default_factory=list)  # o que a ação RECEBE (ex.: job_id, nova_situacao)
    confirmacao: str = ""                  # o texto que o humano lê e aprova antes da escrita (HITL)

    @model_validator(mode="after")
    def _regras(self):
        if self.escopo == Escopo.ESCRITA:
            # ESCRITA: nasce desligada; sem HITL e sem a frase de confirmação, não carrega.
            if self.modo != ModoLeitura.PLAYWRIGHT_ACTION:
                raise ValueError(f"capacidade de ESCRITA '{self.nome}' tem de ser playwright_action")
            if not self.hitl:
                raise ValueError(
                    f"capacidade de ESCRITA '{self.nome}' sem hitl=True — escrita nasce desligada "
                    f"e só age com humano no laço (a parede estrutural, não combinado)"
                )
            if not self.confirmacao.strip():
                raise ValueError(
                    f"capacidade de ESCRITA '{self.nome}' sem `confirmacao` — o humano precisa LER "
                    f"o que vai aprovar (o botão no direto do agente mostra esta frase)"
                )
            if not self.parametros:
                raise ValueError(f"capacidade de ESCRITA '{self.nome}' sem parametros de entrada")
            return self
        # ── LEITURA ──
        if not self.extratores:
            raise ValueError(f"capacidade de LEITURA '{self.nome}' sem extratores")
        if self.lista and not self.cursor:
            raise ValueError(
                f"capacidade de listagem '{self.nome}' sem cursor — delta-first é obrigatório "
                f"(a lição do ponteiro do Drive: sem cursor relê o mundo a cada ciclo)"
            )
        if not self.id_na_fonte:
            raise ValueError(f"capacidade de LEITURA '{self.nome}' sem id_na_fonte (idempotência)")
        campos = [e.campo for e in self.extratores]
        if self.id_na_fonte not in campos:
            raise ValueError(
                f"id_na_fonte '{self.id_na_fonte}' de '{self.nome}' não está entre os extratores "
                f"— sem ele lido não há identidade na fonte, logo não há idempotência"
            )
        return self

    def fingerprint_shape(self) -> list[str]:
        """A régua que o healing observa: o conjunto ORDENADO de campos obrigatórios. Mudou o
        shape (campo sumiu/renomeou) → o fingerprint muda → alarme antes de dado torto entrar."""
        return sorted(e.campo for e in self.extratores if e.obrigatorio)


class Auth(BaseModel):
    """A cerimônia de login, separada das capacidades (o login preenche o storage_state cifrado)."""
    tipo: str = "cookie"                   # cookie | token | form — observado no gravador
    login_url: str
    renovavel: bool = True                 # o kernel renova sozinho, ou precisa de humano/2FA?


class Contrato(BaseModel):
    sistema: str                           # ex.: "Acme"
    versao_contrato: int = 1
    dominios: list[str]                    # a allowlist DESTE contrato (fail-closed)
    auth: Auth
    capacidades: list[Capacidade]
    # LGPD viaja no artefato: quem autorizou a leitura, quando.
    base_legal: str = ""                   # ex.: "contrato de operação — tenant X, 27/08/2026"

    @field_validator("dominios")
    @classmethod
    def _hosts_validos(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("contrato sem domínios — a allowlist fail-closed precisa de host")
        for h in v:
            if h == "*" or not RE_HOST.match(h):
                raise ValueError(f"domínio inválido '{h}' — sem curinga, host real")
        return v

    @model_validator(mode="after")
    def _nomes_unicos(self):
        nomes = [c.nome for c in self.capacidades]
        if len(nomes) != len(set(nomes)):
            raise ValueError("capacidades com nome repetido — o agente não saberia qual chamar")
        return self

    def capacidade(self, nome: str) -> Capacidade:
        for c in self.capacidades:
            if c.nome == nome:
                return c
        raise KeyError(f"capacidade '{nome}' não existe no contrato de {self.sistema}")


def carregar_contrato(dados: dict) -> Contrato:
    """A ÚNICA porta de entrada — fail-closed: dados inválidos levantam AQUI, no load, nunca no
    meio da execução. Pydantic acumula os erros; quem carrega o contrato vê tudo de uma vez."""
    return Contrato.model_validate(dados)
