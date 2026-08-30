# -*- coding: utf-8 -*-
"""Runner do Foral — as GARANTIAS públicas, testadas (o que um dev vê ao abrir o repo):
contrato fail-closed, escrita nunca vira tool nem executa por MCP, dispatch do CLI fail-closed,
keygen. Puro: sem browser, sem sessão, sem rede."""
import pytest

from foral.contract import carregar_contrato, Escopo
from foral.serve import tools_do_contrato, tratar_requisicao
from foral import cli


CONTRATO_OK = {
    "sistema": "Acme",
    "versao_contrato": 1,
    "dominios": ["acme.example.com"],
    "auth": {"login_url": "https://acme.example.com/login"},
    "capacidades": [
        {"nome": "list_orders", "modo": "network_json", "rota": "/api/orders",
         "lista": True, "cursor": "page", "id_na_fonte": "id",
         "extratores": [{"campo": "id", "caminho": "orders[].id"},
                        {"campo": "total", "caminho": "orders[].total"}]},
        {"nome": "update_order", "modo": "playwright_action", "rota": "/orders/edit",
         "escopo": "escrita", "hitl": True, "parametros": ["order_id", "status"],
         "confirmacao": "Confirm changing order {order_id} to {status}?"},
    ],
}


# ── Contrato: fail-closed ────────────────────────────────────────────────────
def test_contrato_valido_carrega():
    c = carregar_contrato(CONTRATO_OK)
    assert c.sistema == "Acme" and len(c.capacidades) == 2
    assert c.capacidade("update_order").escopo == Escopo.ESCRITA


def test_escrita_sem_hitl_recusa():
    dados = {**CONTRATO_OK, "capacidades": [
        {"nome": "w", "modo": "playwright_action", "rota": "/x", "escopo": "escrita",
         "parametros": ["a"], "confirmacao": "ok?"}]}    # hitl ausente
    with pytest.raises(Exception, match="hitl"):
        carregar_contrato(dados)


def test_escrita_sem_confirmacao_recusa():
    dados = {**CONTRATO_OK, "capacidades": [
        {"nome": "w", "modo": "playwright_action", "rota": "/x", "escopo": "escrita",
         "hitl": True, "parametros": ["a"]}]}            # confirmacao ausente
    with pytest.raises(Exception, match="confirmacao"):
        carregar_contrato(dados)


def test_leitura_lista_sem_cursor_recusa():
    dados = {**CONTRATO_OK, "capacidades": [
        {"nome": "r", "modo": "network_json", "rota": "/x", "lista": True, "id_na_fonte": "id",
         "extratores": [{"campo": "id", "caminho": "x[].id"}]}]}   # lista sem cursor
    with pytest.raises(Exception, match="cursor"):
        carregar_contrato(dados)


def test_dominio_curinga_recusa():
    dados = {**CONTRATO_OK, "dominios": ["*"]}
    with pytest.raises(Exception, match="curinga|domínio"):
        carregar_contrato(dados)


# ── Serve (MCP): a escrita é uma parede ──────────────────────────────────────
def test_tools_list_so_leituras():
    c = carregar_contrato(CONTRATO_OK)
    tools = tools_do_contrato(c)
    nomes = {t["name"] for t in tools}
    assert "list_orders" in nomes
    assert "update_order" not in nomes                   # escrita NUNCA vira tool


def test_tools_call_escrita_recusa_com_frase():
    c = carregar_contrato(CONTRATO_OK)
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "update_order", "arguments": {}}}
    resp = tratar_requisicao(c, "tenant-x", req)
    r = resp["result"]
    assert r.get("isError") is True
    txt = r["content"][0]["text"]
    assert "HITL" in txt and "Confirm changing order" in txt   # devolve a cerimônia, não o efeito


def test_tools_call_desconhecida_e_erro():
    c = carregar_contrato(CONTRATO_OK)
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
           "params": {"name": "nao_existe", "arguments": {}}}
    r = tratar_requisicao(c, "t", req)["result"]
    assert r.get("isError") is True


def test_initialize_e_ping():
    c = carregar_contrato(CONTRATO_OK)
    ini = tratar_requisicao(c, "t", {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert ini["result"]["serverInfo"]["name"] == "foral-acme"
    assert "protocolVersion" in ini["result"]
    png = tratar_requisicao(c, "t", {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert png["result"] == {}


def test_notificacao_nao_responde():
    c = carregar_contrato(CONTRATO_OK)
    assert tratar_requisicao(c, "t", {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


# ── CLI: dispatch fail-closed + keygen ───────────────────────────────────────
def test_help_ok():
    assert cli.main(["--help"]) == 0
    assert cli.main([]) == 0


def test_comando_desconhecido_recusa():
    assert cli.main(["frobnicate"]) == 2


def test_serve_sem_tenant_recusa(monkeypatch):
    monkeypatch.delenv("FORAL_TENANT", raising=False)
    assert cli.main(["serve", "acme"]) == 2               # fail-closed: sem tenant, não assume


def test_login_sem_tenant_recusa(monkeypatch):
    monkeypatch.delenv("FORAL_TENANT", raising=False)
    assert cli.main(["login", "acme"]) == 2


def test_keygen_gera_chave_fernet_valida(capsys):
    assert cli.main(["keygen"]) == 0
    chave = capsys.readouterr().out.strip()
    from cryptography.fernet import Fernet
    f = Fernet(chave.encode())                            # aceita = chave válida
    assert f.decrypt(f.encrypt(b"x")) == b"x"
